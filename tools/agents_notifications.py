"""Small, read-only message discovery and explicit, isolated reading receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
from agents_layout import state_path  # noqa: E402

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_RECORDS = 4096
MAX_RECEIPT_BYTES = 1024 * 1024
_TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,100}\Z")
_FIELD = re.compile(r"^[ \t]*[-*][ \t]*\*{0,2}[A-Za-z_][\w -]*?\*{0,2}[ \t]*[:：]")
_REPLY = re.compile(r"^##[ \t]+(?:回复|replies|reply|response)(?:[ \t：:].*)?$", re.I)
_ACTIONS = {"message", "pause", "cancel", "avoid"}


def _board():
    # Imports stay lazy: status itself uses this module at its output boundary.
    import agents_status
    return agents_status


def _canonical(path: Path) -> str:
    value = str(path.resolve())
    return value.casefold() if os.name == "nt" else value


def _target(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or not _TARGET.fullmatch(value):
        raise ValueError(label + " 使用小写英文、数字和连字符")
    return value


def notification_roots(root):
    """Verify Git topology once, or use only the explicitly named non-Git project."""
    root = Path(root).expanduser().resolve()
    marker = root / ".git"
    if not marker.exists() and not marker.is_symlink():
        return [root]
    board = _board()
    rows = board.worktrees(root)
    try:
        workspaces = [_canonical(Path(row["path"])) for row in rows]
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise ValueError("无法可靠读取 Git 工作区消息清单，请修复 Git 后重新查看") from exc
    if not rows or _canonical(root) not in workspaces:
        raise ValueError("无法核验 Git 工作区消息清单，不能据此认定没有新消息；请修复 Git 后重新查看")
    return [workspace for workspace, _is_main in board.agent_roots(root, rows)]


def _roots(root: Path, roots=None):
    root = Path(root).expanduser().resolve()
    candidates = roots if roots is not None else notification_roots(root)
    found = []
    seen = set()
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        key = _canonical(path)
        if key not in seen:
            found.append(path)
            seen.add(key)
    if _canonical(root) not in seen:
        found.insert(0, root)
    return root, found


def _identity(root: Path, agent=None, session_id=None):
    agent = _target(agent, "平台名")
    session_id = _target(session_id, "会话 ID")
    if session_id is not None:
        path = _board()._checked_state_path(root, state_path(root, "sessions") / (session_id + ".md"))
        if path.exists():
            record = _board().parse_kv(path)
            owner = record.get("agent")
            if not isinstance(owner, str) or not _TARGET.fullmatch(owner):
                raise ValueError("无法核验会话平台，先查看原记录：" + str(path))
            if agent is not None and owner != agent:
                raise ValueError("平台与会话所有者不一致，不能共用其它会话的阅读回执")
            agent = owner
    else:
        candidates = [item for item in _board().load_sessions([(root, True)], datetime.now(), 15)
                      if not item["closed"]]
        if len(candidates) == 1:
            candidate = candidates[0]
            owner, identifier = candidate.get("agent"), candidate.get("id")
            if (not candidate.get("claims_error") and isinstance(owner, str) and _TARGET.fullmatch(owner)
                    and isinstance(identifier, str) and _TARGET.fullmatch(identifier)
                    and (agent is None or agent == owner)):
                agent, session_id = owner, identifier
    return {"root": _canonical(root), "agent": agent, "session": session_id}


def notification_identity(root, agent=None, session_id=None):
    """Resolve the same conservative identity used by message discovery."""
    return _identity(Path(root).expanduser().resolve(), agent, session_id)


def _metadata(path: Path):
    fields = _board().parse_kv(path)
    if (not fields or fields.get("_read_error") or not fields.get("from")
            or not fields.get("to") or not _TARGET.fullmatch(fields["to"])):
        raise ValueError("inbox 无法可靠分类，先查看原记录：" + str(path))
    if fields.get("action", "message") not in _ACTIONS:
        raise ValueError("inbox action 无效，先查看原记录：" + str(path))
    return fields


def _pending(status, action, sender):
    helper = getattr(_board(), "inbox_is_pending", None)
    if helper is None:
        raise ValueError("看板核心版本不匹配，缺少控制生命周期支持；请完整更新套件")
    return helper(status, action, sender.casefold().startswith("human"))


def _full_bytes(root: Path, path: Path):
    board = _board()
    board._checked_state_path(root, path)
    before = path.stat()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("读取期间 inbox 文件发生替换：" + str(path))
        raw = stream.read(MAX_MESSAGE_BYTES + 1)
    board._checked_state_path(root, path)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("读取期间 inbox 内容发生变化，请重读：" + str(path))
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("inbox 超过单条 1 MiB 阅读上限，请先拆分原记录：" + str(path))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("inbox 不是可可靠读取的 UTF-8：" + str(path)) from exc
    return raw, text


def _section_offsets(text: str):
    """Find request/reply boundaries without altering original bytes or fences."""
    lines = text.splitlines(keepends=True)
    start, offset, started = 0, 0, False
    for index, line in enumerate(lines):
        if _FIELD.match(line):
            started = True
        elif line.strip() and (started or not line.lstrip("\ufeff \t").startswith(("# ", "<!--"))):
            start = index
            break
        offset += len(line)
    else:
        start = len(lines)
    request_start = offset
    fence = None
    for line in lines[start:]:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$", line.rstrip("\r\n"))
        if marker:
            token = marker.group(1)
            suffix = marker.group(2)
            if fence is None:
                if token[0] != "`" or "`" not in suffix:
                    fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip(" \t"):
                fence = None
        elif fence is None and _REPLY.fullmatch(line.rstrip("\r\n")):
            return request_start, offset, offset + len(line)
        offset += len(line)
    return request_start, None, None


def reply_boundary(text: str):
    """Return the end of a real reply heading; fenced examples never match."""
    return _section_offsets(text)[2]


def is_reply_placeholder(line: str):
    value = line.strip()
    return value.startswith(("（待回复", "(待回复")) or value == "待回复"


def _sections(text: str):
    """Preserve every request section; only a real top-level reply starts replies."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start, heading, reply = _section_offsets(text)
    if reply is not None:
        return text[start:heading].strip(), text[reply:].strip()
    return text[start:].strip(), ""


def _has_reply(reply: str):
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    return any(not is_reply_placeholder(line) for line in lines)


def _request_preamble(text: str):
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines(keepends=True):
        if _FIELD.match(line):
            break
        lines.append(line)
    return "".join(lines).strip()


def _records(roots):
    paths = [(root, path) for root in roots
             for path in _board()._state_record_files(root, "inbox", "Q-*.md")]
    if len(paths) > MAX_RECORDS:
        raise ValueError("inbox 超过 4096 条记录，请先归档；未忽略超出的消息")
    counts = {}
    for _root, path in paths:
        counts[path.name] = counts.get(path.name, 0) + 1
    for root, path in paths:
        fields = _metadata(path)
        yield root, path, fields, path.name if counts[path.name] == 1 else str(path.resolve())


def _item(root, path, fields, identifier):
    raw, text = _full_bytes(root, path)
    checked = _board().parse_kv_text(text)
    if checked != fields:
        raise ValueError("读取期间 inbox 头部发生变化，请重读：" + str(path))
    body, reply = _sections(text)
    request = {"path": _canonical(path), "from": fields["from"], "to": fields["to"],
               "action": fields.get("action", "message"), "preamble": _request_preamble(text), "body": body}
    request_version = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode("utf-8")).hexdigest()
    status = _board().status_word(fields.get("status", "open"))
    return {"id": identifier, "path": str(path.resolve()), "workspace": str(root.resolve()),
            "from": fields["from"], "to": fields["to"], "status": status,
            "action": fields.get("action", "message"),
            "open": _pending(status, fields.get("action", "message"), fields["from"]),
            "human": fields["from"].casefold().startswith("human"),
            "body": body, "reply": reply, "text": text,
            "version": hashlib.sha256(raw).hexdigest(), "request_version": request_version}


def human_requests(roots, agent, session_id=None):
    agent, session_id = _target(agent, "平台名"), _target(session_id, "会话 ID")
    targets = {"any", agent, session_id}
    result = {}
    for root, path, fields, identifier in _records([Path(root).resolve() for root in roots]):
        status = _board().status_word(fields.get("status", "open"))
        if (_pending(status, fields.get("action", "message"), fields["from"]) and fields["from"].casefold().startswith("human")
                and fields["to"] in targets):
            result[identifier] = _item(root, path, fields, identifier)["request_version"]
    return result


def human_controls(roots, agent, session_id=None):
    """Typed, pending controls cannot be cleared by a delivery or reading receipt."""
    agent, session_id = _target(agent, "平台名"), _target(session_id, "会话 ID")
    targets = {"any", agent, session_id}
    result = {}
    for root, path, fields, identifier in _records([Path(root).resolve() for root in roots]):
        status = _board().status_word(fields.get("status", "open"))
        if (_pending(status, fields.get("action", "message"), fields["from"]) and fields["from"].casefold().startswith("human")
                and fields["to"] in targets and fields.get("action", "message") != "message"):
            item = _item(root, path, fields, identifier)
            result[identifier] = item["action"]
    return result


def _receipt_path(root: Path, identity):
    seed = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    name = "reader-" + hashlib.sha256(seed.encode("utf-8")).hexdigest() + ".json"
    return _board()._checked_state_path(root, state_path(root, "notifications") / name)


def _load_receipt(root: Path, identity):
    path = _receipt_path(root, identity)
    if not path.exists():
        return path, {}, None
    if not path.is_file():
        raise ValueError("阅读回执不是普通文件：" + str(path))
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("阅读回执过大，先核对原记录：" + str(path))
    with path.open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    _board()._checked_state_path(root, path)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("阅读回执过大，先核对原记录：" + str(path))
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("阅读回执损坏，不能据此隐藏消息：" + str(path)) from exc
    if (not isinstance(record, dict) or type(record.get("schema_version")) is not int or record.get("schema_version") != 1
            or record.get("identity") != identity or not isinstance(record.get("read"), dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in record["read"].items())):
        raise ValueError("阅读回执字段无效，不能据此隐藏消息：" + str(path))
    return path, record["read"], raw


def scan_messages(root, agent=None, session_id=None, roots=None, include_closed=False, include_content=True):
    root, roots = _roots(root, roots)
    identity = _identity(root, agent, session_id)
    targets = {"any", identity["agent"], identity["session"]}
    senders = {value for value in (identity["agent"], identity["session"]) if value}
    receipts = _load_receipt(root, identity)[1] if identity["session"] else {}
    items = []
    for workspace, path, fields, identifier in _records(roots):
        opened = _pending(fields.get("status", "open"), fields.get("action", "message"), fields["from"])
        own = identity["session"] is not None and fields["from"] == identity["session"]
        sender_reply = fields["from"] in senders
        incoming = fields["to"] in targets and not own
        if not sender_reply and not (incoming and (opened or include_closed)):
            continue
        item = _item(workspace, path, fields, identifier)
        reply = sender_reply and _has_reply(item["reply"])
        if not reply and not (incoming and (opened or include_closed)):
            continue
        item["kind"] = "reply" if reply else "incoming"
        item["notification_version"] = _notification_version(item)
        item["unread"] = receipts.get(_canonical(path)) != item["notification_version"]
        if not include_content:
            # Keep full-content fingerprints, without retaining every body in memory.
            for key in ("body", "reply", "text"):
                item.pop(key)
        items.append(item)
    items.sort(key=lambda item: (not (item["human"] and item["open"]), item["path"]))
    return identity, items


def _notification_version(item):
    if item["kind"] == "incoming":
        return item["request_version"]
    reply = {"path": _canonical(Path(item["path"])), "from": item["from"], "to": item["to"],
             "status": item["status"], "reply": item["reply"]}
    return hashlib.sha256(json.dumps(reply, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def read_command(root, identity):
    command = ["python", str(Path(__file__).with_name("agents_messages.py").resolve()), "--root", str(Path(root).resolve())]
    if identity["agent"]:
        command.extend(["--agent", identity["agent"]])
    if identity["session"]:
        command.extend(["--session", identity["session"]])
    command.append("read")
    if identity["session"]:
        command.append("--mark-read")
    return command


def _notification_summary(root, identity, items):
    unread = sum(item["unread"] for item in items)
    human = sum(item["human"] and item["open"] and item["kind"] == "incoming" for item in items)
    if not unread and not human:
        return None
    return {"schema_version": 1, "unread_count": unread, "human_pending": human,
            "reply_count": sum(item["unread"] and item["kind"] == "reply" for item in items),
            "read_command": read_command(root, identity) if unread else None}


def collect_notifications(root, agent=None, session_id=None, roots=None):
    identity, items = scan_messages(root, agent, session_id, roots, include_content=False)
    return _notification_summary(root, identity, items)


def notification_snapshot(root, agent=None, session_id=None, roots=None):
    """A compact in-memory cursor lets one running command avoid repeated hints."""
    identity, items = scan_messages(root, agent, session_id, roots, include_content=False)
    summary = _notification_summary(root, identity, items)
    if summary is None:
        return None, None
    digest = hashlib.sha256()
    for item in items:
        if item["unread"] or (item["human"] and item["open"] and item["kind"] == "incoming"):
            data = [item["path"], item["notification_version"], item["kind"], item["unread"], item["open"]]
            digest.update(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    return summary, digest.hexdigest()


def format_notification(summary):
    if not summary:
        return ""
    if not summary["unread_count"] and summary["human_pending"]:
        return "提醒：已读人类事项 " + str(summary["human_pending"]) + " 条仍待处理，请回原 inbox 更新结果。"
    def quote(part):
        if os.name != "nt":
            return shlex.quote(part)
        # Windows documentation uses PowerShell; single quotes also preserve $ and `.
        return "'" + part.replace("'", "''") + "'"
    command = ("& " if os.name == "nt" else "") + " ".join(quote(part) for part in summary["read_command"])
    return ("提醒：inbox 消息待查看 " + str(summary["unread_count"]) + "，人类未处理 "
            + str(summary["human_pending"]) + "，新回复 " + str(summary["reply_count"])
            + ("；PowerShell 读取：" if os.name == "nt" else "；读取：") + command)


@contextmanager
def _receipt_lock(root, path):
    directory = _board()._checked_state_directory(root, "notifications")
    directory.mkdir(parents=True, exist_ok=True)
    _board()._checked_state_directory(root, "notifications")
    lock = _board()._checked_state_path(root, path.with_suffix(".lock"))
    with lock.open("a+b") as stream:
        opened = os.fstat(stream.fileno())
        _board()._checked_state_path(root, lock)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (lock.stat().st_dev, lock.stat().st_ino):
            raise ValueError("阅读回执锁发生替换，停止写入")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("阅读回执正在更新，请稍后重试；未抢占锁") from None
                time.sleep(0.05)
        try:
            _board()._checked_state_path(root, lock)
            current = lock.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("等待期间阅读回执锁被替换，停止写入")
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _save_receipt(root, path, identity, values, expected):
    payload = json.dumps({"schema_version": 1, "identity": identity, "read": values}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("阅读回执超过 1 MiB，请先归档；未丢弃原回执")
    _replace_receipt(root, path, payload, expected)
    return payload


def _replace_receipt(root, path, payload, expected):
    _board()._checked_state_path(root, path)
    if payload is None:
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise ValueError("阅读回执被其它操作修改，未覆盖原记录")
        if path.exists():
            path.unlink()
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".reader-", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _board()._checked_state_path(root, temporary)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _board()._checked_state_path(root, temporary)
        _board()._checked_state_path(root, path)
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise ValueError("阅读回执被其它操作修改，保留原记录并重试")
        os.replace(str(temporary), str(path))
    finally:
        # Only remove the temporary file this call created, after rechecking its path.
        if temporary.exists():
            _board()._checked_state_path(root, temporary)
            temporary.unlink()


def mark_read(root, items, agent=None, session_id=None, roots=None, emit=None):
    root, roots = _roots(root, roots)
    identity = _identity(root, agent, session_id)
    if not identity["session"]:
        raise ValueError("标读需要 --session 或当前工作区唯一未结束会话；不能共用其它会话的游标")
    session = _board()._checked_state_path(root, state_path(root, "sessions") / (identity["session"] + ".md"))
    if not session.is_file():
        raise ValueError("标读需要当前工作区存在的真实会话记录，先确认 --session")
    allowed = {_canonical(workspace): workspace for workspace in roots}
    expected = []
    for item in items:
        workspace = Path(item["workspace"]).resolve()
        path = Path(item["path"])
        if (_canonical(workspace) not in allowed or path.parent.resolve() != state_path(workspace, "inbox").resolve()
                or not path.name.startswith("Q-") or not path.name.endswith(".md")):
            raise ValueError("不能标读本仓库 inbox 以外的内容：" + str(path))
        if not isinstance(item.get("version"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["version"]):
            raise ValueError("消息版本无效，未登记阅读回执")
        if (not isinstance(item.get("notification_version"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["notification_version"])):
            raise ValueError("通知版本无效，请用新版工具完整重读后标记")
        _board()._checked_state_path(workspace, path)
        expected.append((workspace, path, item["version"], item["notification_version"]))
    if not expected:
        return []
    # Check before creating even a lock directory; failed reads remain zero-write.
    for workspace, path, version, _notification in expected:
        if hashlib.sha256(_full_bytes(workspace, path)[0]).hexdigest() != version:
            raise ValueError("读取期间消息已变化，未标读；请重新读取：" + str(path))
    receipt = _receipt_path(root, identity)
    with _receipt_lock(root, receipt):
        receipt, values, previous = _load_receipt(root, identity)
        original_values = values
        values = dict(values)
        for workspace, path, version, notification in expected:
            if hashlib.sha256(_full_bytes(workspace, path)[0]).hexdigest() != version:
                raise ValueError("读取期间消息已变化，未标读；请重新读取：" + str(path))
            values[_canonical(path)] = notification
        written = previous if values == original_values else _save_receipt(root, receipt, identity, values, previous)
        try:
            if emit is not None:
                emit()
                # A consumer can trigger edits while processing the output.
                for workspace, path, version, _notification in expected:
                    if hashlib.sha256(_full_bytes(workspace, path)[0]).hexdigest() != version:
                        raise ValueError("输出期间消息已变化，未标读；请重新读取：" + str(path))
        except BaseException as exc:
            try:
                if written != previous:
                    _replace_receipt(root, receipt, previous, written)
            except (ValueError, OSError, RuntimeError) as rollback:
                raise ValueError("消息输出失败，阅读回执回滚未完成：" + str(rollback)) from exc
            raise
    return [item["id"] for item in items]
