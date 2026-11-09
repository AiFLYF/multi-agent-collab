"""Record explicit caller activity without changing task state or file ownership."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

MAX_SESSION_BYTES = 1024 * 1024
_AGENT = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z")
_FIELD = re.compile(r"^([ \t]*[-*][ \t]*\*{0,2}([A-Za-z_][\w -]*?)\*{0,2}[ \t]*[:：][ \t]*)(.*?)$")
ACTIVITY_COMMANDS = frozenset(
    {"agents_status", "agents_status.py", "agents_messages", "agents_messages.py",
     "agents_session", "agents_session.py", "agents_update", "agents_update.py", "install.py"}
    | {"agents_status." + name for name in ("context", "brief", "json", "status", "html")}
    | {"agents_messages." + name for name in ("list", "read")}
    | {"agents_session." + name for name in
       ("check", "start", "heartbeat", "handoff", "resume", "finish", "standby", "activate", "run",
        "takeover-inspect", "takeover-plan", "takeover-apply", "takeover-cancel")}
    | {"agents_update." + name for name in ("update", "check", "dry-run")}
    | {"install." + name for name in
       ("install", "update", "upgrade", "sync", "mirror", "doctor", "clients", "uninstall")}
)


def _modules():
    # A CLI calls us outside its coordination lock. Lazy imports avoid the
    # status -> activity -> session -> status initialization cycle.
    import agents_session
    return agents_session, agents_session.board


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _unique_location(roots, session_id, path):
    locations = [candidate for workspace in roots for candidate in [_session_path(workspace, session_id)] if candidate.is_file()]
    if locations != [path]:
        raise ValueError("多个工作区存在相同会话 ID；未记录任何一方活动")


def _session_path(root, session_id):
    sessions, board = _modules()
    return board._checked_state_path(root, sessions.state_path(root, "sessions") / (session_id + ".md"))


def _read_record(root, session_id, expected_path=None):
    _sessions, board = _modules()
    path = _session_path(root, session_id)
    if expected_path is not None and path != expected_path:
        raise ValueError("协作目录在活动记录期间变化；未更新会话")
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise ValueError("当前工作区不存在该会话；未记录活动") from None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("活动会话记录不是普通文件；未更新会话")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("读取活动会话时文件已被替换；未更新会话")
        original = stream.read(MAX_SESSION_BYTES + 1)
    board._checked_state_path(root, path)
    after = path.lstat()
    if len(original) > MAX_SESSION_BYTES or _signature(before) != _signature(after):
        raise ValueError("活动会话记录过大或读取期间发生变化；未更新会话")
    text = original.decode("utf-8-sig")
    header_size, started = 0, False
    for line in text.splitlines(keepends=True):
        if _FIELD.match(line.rstrip("\r\n")):
            started = True
        elif line.strip() and (started or not line.lstrip("\ufeff \t").startswith(("# ", "<!--"))):
            break
        header_size += len(line)
        if header_size > 65536:
            raise ValueError("活动会话元数据超过读取上限；未更新会话")
    record = board.parse_kv_text(text)
    if not record or record.get("_read_error"):
        raise ValueError("活动会话元数据无效；未更新会话")
    return path, original, after, text, record


def _validate(root, record, agent, current_branch):
    sessions, board = _modules()
    if record.get("agent") != agent:
        raise ValueError("平台与会话所有者不一致；未记录其它会话的活动")
    if record.get("workspace"):
        recorded_root = Path(record["workspace"]).expanduser().resolve()
        if recorded_root != root:
            raise ValueError("会话登记工作区不一致；未更新会话")
    state = board.validate_session_status(record)
    if state in board.CLOSED_WORDS:
        return "closed"
    if state not in ("active", "blocked", "standby"):
        raise ValueError("会话状态无法确认；活动记录不会自行恢复任务")
    if state == "standby":
        try:
            claimed = json.loads(record.get("files", ""))
        except (ValueError, TypeError):
            claimed = None
        if not isinstance(claimed, list) or claimed:
            raise ValueError("会话文件范围与状态不一致；未更新活动或释放占用")
        if not sessions.valid_standby(record):
            raise ValueError("待机会话仍有运行记录或无效占用；未更新活动")
    else:
        claimed, invalid = board.parse_claimed_files(record.get("files", ""))
        if invalid or not claimed:
            raise ValueError("会话文件范围与状态不一致；未更新活动或释放占用")
    if not record.get("branch"):
        raise ValueError("会话缺少登记分支；未更新活动")
    if not current_branch:
        raise ValueError("当前没有有效分支；未记录活动")
    if state != "standby" and record["branch"] != current_branch:
        raise ValueError("当前分支与会话登记分支不一致；未更新活动")
    if state != "standby":
        sessions.normalize_files(root, claimed)
    return state


def _recent(record, now, min_interval):
    _sessions, board = _modules()
    times = [board.valid_heartbeat(record.get(key, ""), now) for key in ("last_activity", "heartbeat")]
    last = max((value for value in times if value is not None), default=None)
    elapsed = (now - last).total_seconds() if last is not None else None
    return elapsed is not None and 0 <= elapsed < min_interval


def read_session_record(root, session_id):
    """Return a stable, bounded record snapshot; callers own coordination locks."""
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise ValueError("会话 ID 无效")
    return _read_record(Path(root).expanduser().resolve(), session_id)


def replace_session_record(root, session_id, snapshot, updated):
    """Replace only the exact original snapshot, preserving BOM when given text."""
    path, original, info, _text, _record = snapshot
    if isinstance(updated, str):
        updated = updated.encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf") and not updated.startswith(b"\xef\xbb\xbf"):
            updated = b"\xef\xbb\xbf" + updated
    if not isinstance(updated, bytes) or len(updated) > MAX_SESSION_BYTES:
        raise ValueError("会话写入必须是有界 UTF-8 文本")
    _replace_record(Path(root).expanduser().resolve(), session_id, path, original, info, updated)


def update_session_fields(text, updates):
    """Keep original header/newline style while updating single-line fields."""
    if not isinstance(text, str) or not isinstance(updates, dict):
        raise ValueError("会话字段更新需要文本与字段字典")
    for key, value in updates.items():
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key)
                or not isinstance(value, str) or "\x00" in value or (value and value.splitlines() != [value])):
            raise ValueError("会话更新只能包含合法的单行头部字段")
    return _updated_text(text, updates)


def _updated_text(text, updates):
    # Keep BOM and every untouched line's exact newline/style. Equal legacy
    # duplicates are updated together; conflicting activity values are rejected.
    lines = text.splitlines(keepends=True)
    ending = "\r\n" if "\r\n" in text else ("\r" if "\r" in text and "\n" not in text else "\n")
    started, last_field, seen = False, None, {}
    for index, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        match = _FIELD.match(raw)
        if match:
            started, last_field = True, index
            key, value = match.group(2).strip().lower(), match.group(3).strip()
            if key in updates:
                if key in seen and seen[key] != value:
                    raise ValueError("顶部活动字段重复且冲突；未更新会话")
                seen[key] = value
                newline = line[len(raw):]
                lines[index] = match.group(1) + updates[key] + newline
        elif raw.strip() and (started or not raw.lstrip("\ufeff \t").startswith(("# ", "<!--"))):
            break
    if last_field is None:
        raise ValueError("活动会话缺少可更新的元数据；未更新会话")
    additions = ["- " + key + ": " + value + ending for key, value in updates.items() if key not in seen]
    if additions:
        if not lines[last_field].endswith(("\n", "\r")):
            lines[last_field] += ending
        lines[last_field + 1:last_field + 1] = additions
    return "".join(lines)


def _assert_original(root, session_id, path, expected, info):
    current_path, content, current_info, _text, _record = _read_record(root, session_id, path)
    if current_path != path or content != expected or _signature(current_info) != _signature(info):
        raise ValueError("活动写入前会话被外部修改；保留原现场")


def _replace_record(root, session_id, path, expected, info, content):
    _sessions, board = _modules()
    _assert_original(root, session_id, path, expected, info)
    temporary, temp_info, finished_info = None, None, None
    try:
        fd, name = tempfile.mkstemp(prefix="." + path.name + ".activity-", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            temp_info = os.fstat(stream.fileno())
            stream.write(content)
            stream.flush()
            finished_info = os.fstat(stream.fileno())
        board._checked_state_path(root, temporary)
        actual_temp = temporary.lstat()
        if not stat.S_ISREG(actual_temp.st_mode) or (actual_temp.st_dev, actual_temp.st_ino) != (temp_info.st_dev, temp_info.st_ino):
            raise ValueError("活动临时文件被重定向；保留外部文件")
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        _assert_original(root, session_id, path, expected, info)
        board._checked_state_path(root, temporary)
        current_temp = temporary.lstat()
        if (_signature(current_temp) != _signature(finished_info)
                or temporary.read_bytes() != content):
            raise ValueError("活动替换前临时文件被修改；保留外部文件")
        os.replace(str(temporary), str(path))
        temporary = None
    finally:
        if temporary is not None and temp_info is not None:
            # A redirected parent or reused temporary name belongs to somebody
            # else. Never delete it as cleanup for our failed transaction.
            try:
                board._checked_state_path(root, temporary)
                current = temporary.lstat()
                if ((current.st_dev, current.st_ino) == (temp_info.st_dev, temp_info.st_ino)
                        and (finished_info is None or _signature(current) == _signature(finished_info))):
                    temporary.unlink()
            except (ValueError, OSError, RuntimeError):
                pass


def touch_activity(root, agent, session_id, command, *, now=None, force=False, min_interval=60, branch=None):
    """Record a validated explicit caller; anonymous observers do no I/O.

    Call at a CLI boundary, never while holding ``coordination_lock`` already.
    A recent record is left untouched. Activity changes no task/control state.
    """
    if not agent or not session_id:
        return {"updated": False, "reason": "identity-not-explicit"}
    if not isinstance(agent, str) or not _AGENT.fullmatch(agent):
        raise ValueError("活动记录需要有效的明确平台")
    if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
        raise ValueError("活动记录需要有效的明确会话 ID")
    if not isinstance(command, str) or command not in ACTIVITY_COMMANDS:
        raise ValueError("活动记录只接受固定工具命令名，不保存参数或命令正文")
    if isinstance(min_interval, bool) or not isinstance(min_interval, (int, float)) or not 0 <= min_interval <= 900:
        raise ValueError("活动节流间隔应为 0 至 900 秒")
    if not isinstance(force, bool) or (branch is not None and not isinstance(branch, str)):
        raise ValueError("活动强制标志或缓存分支类型无效")
    moment = datetime.now() if now is None else now
    if not isinstance(moment, datetime):
        raise ValueError("活动时间需要 datetime")
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    root = Path(root).expanduser().resolve()
    sessions, board = _modules()
    path = _session_path(root, session_id)
    if not force:
        # Ordinary calls keep their cheap, write-free throttling fast path.
        path, original, info, text, record = _read_record(root, session_id)
        current_branch = branch if branch is not None else sessions.git(root, "branch", "--show-current")
        state = _validate(root, record, agent, current_branch)
        if state == "closed":
            return {"updated": False, "reason": "closed"}
        if _recent(record, moment, min_interval):
            return {"updated": False, "reason": "throttled"}
    roots = sessions.worktree_roots(root)
    if root not in roots:
        raise ValueError("活动工作区与 Git 清单不一致；未更新会话")
    _unique_location(roots, session_id, path)
    primary = roots[0]
    lock_path = board._checked_state_path(primary, sessions.state_path(primary, "coordination.lock"))
    if lock_path.exists() and not stat.S_ISREG(lock_path.lstat().st_mode):
        raise ValueError("活动协调锁不是普通文件；未更新会话")
    with sessions.coordination_lock(primary):
        board._checked_state_path(primary, lock_path)
        roots = sessions.refresh_worktree_roots(root, primary)
        _unique_location(roots, session_id, path)
        current_branch = sessions.git(root, "branch", "--show-current")
        path, original, info, text, record = _read_record(root, session_id, path)
        state = _validate(root, record, agent, current_branch)
        if state == "closed":
            return {"updated": False, "reason": "closed"}
        if not force and _recent(record, moment, min_interval):
            return {"updated": False, "reason": "throttled"}
        at = moment.isoformat(timespec="seconds")
        updates = {"heartbeat": moment.strftime("%Y-%m-%d %H:%M"), "last_activity": at, "last_command": command}
        updated = _updated_text(text, updates).encode("utf-8")
        if original.startswith(b"\xef\xbb\xbf"):
            updated = b"\xef\xbb\xbf" + updated
        if updated == original:
            return {"updated": False, "reason": "unchanged"}
        _replace_record(root, session_id, path, original, info, updated)
        return {"updated": True, "session": session_id, "at": at, "command": command}


def safe_touch_activity(root, agent, session_id, command, *, now=None, force=False, min_interval=60, branch=None):
    """Return an independent warning; callers keep their original business code."""
    try:
        return touch_activity(root, agent, session_id, command, now=now, force=force, min_interval=min_interval, branch=branch)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, TypeError) as exc:
        return {"updated": False, "warning": str(exc)[:500]}
