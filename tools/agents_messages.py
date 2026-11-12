#!/usr/bin/env python3
"""List/read messages, and send/reply as the current verified session."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
import agents_notifications as notifications  # noqa: E402

DEFAULT_MAX_CHARS = 6000
_MAX_REPLY_LEN = 4000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _inbox_paths(root):
    directory = notifications._board()._checked_state_directory(root, "inbox")
    if not directory.exists():
        return []
    result = []
    for path in directory.iterdir():
        if path.name.startswith("Q-") and path.name.endswith(".md"):
            result.append(path)
            if len(result) > 65536:
                raise ValueError("归档扫描超过 65536 条，请先按工作区拆分历史")
    return sorted(result)


def _silence_failed_stream(stream):
    """Prevent interpreter shutdown from flushing a failed real CLI pipe again."""
    descriptor = 1 if stream is sys.__stdout__ else 2 if stream is sys.__stderr__ else None
    if descriptor is None or not isinstance(stream, io.TextIOWrapper):
        return
    try:
        if stream.fileno() != descriptor:
            return
        sink = os.open(os.devnull, os.O_WRONLY)
        try:
            if sink != descriptor:
                os.dup2(sink, descriptor)
        finally:
            # os.open can reuse an already closed standard descriptor.
            if sink != descriptor:
                os.close(sink)
        stream.flush()
    except (OSError, ValueError):
        pass


def _write_output(text, stream):
    writer = getattr(stream, "write", None)
    flusher = getattr(stream, "flush", None)
    if not callable(writer) or not callable(flusher):
        raise OSError("输出流不可用，未登记阅读回执")
    text += "\n"
    try:
        if writer(text) != len(text):
            raise OSError("消息输出不完整，未登记阅读回执")
        flusher()
    except (OSError, ValueError):
        _silence_failed_stream(stream)
        raise


def _report_error(exc, as_json, after_output=False):
    error = {"ok": False, "error": str(exc)[:500]}
    text = json.dumps(error, ensure_ascii=False) if as_json else "[FAIL] " + error["error"]
    # A partial or failed stdout must never receive a second JSON document.
    streams = [sys.stderr] if after_output else [sys.stdout, sys.stderr]
    for stream in streams:
        try:
            _write_output(text, stream)
            break
        except (OSError, ValueError):
            continue
    return 2


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _common(parser, suppress=False):
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--root", default=argparse.SUPPRESS if suppress else ".", help="项目根或子目录")
    parser.add_argument("--agent", default=default, help="当前平台")
    parser.add_argument("--session", default=default, help="当前会话 ID，隔离阅读回执")
    parser.add_argument("--no-activity", action="store_true", default=argparse.SUPPRESS if suppress else False,
                        help="只读审计，不更新自己的最近活动")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False)
    parser.add_argument("--max-chars", type=int, default=argparse.SUPPRESS if suppress else DEFAULT_MAX_CHARS,
                        help="完整输出字符预算，1024–100000，默认6000")


def _metadata(item):
    return {key: item[key] for key in ("id", "path", "workspace", "from", "to", "status", "action", "human", "kind",
                                     "unread", "version", "request_version", "notification_version")}


def format_messages(payload, as_json=False):
    if as_json:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = ["消息 " + str(len(payload["items"])) + " 条 | 未读 " + str(payload["unread_count"])
             + " | 人类未处理 " + str(payload["human_pending"])]
    for item in payload["items"]:
        lines.append(item["id"] + " | from=" + item["from"] + " to=" + item["to"] + " | " + item["path"])
        if "text" in item:
            lines.append(item["text"])
    if payload["truncated"]:
        lines.append("输出已截断，未标读；提高 --max-chars 或指定单条消息读取。")
    if payload.get("marked_read"):
        lines.append("已登记完整阅读回执；任务完成与人类指令纳入仍须分别处理。")
    if payload.get("activity_warning"):
        lines.append("活动记录失败 " + payload["activity_warning"])
    return "\n".join(lines)


def _fit(payload, maximum, as_json):
    """Trim only read data, clearly exposing incomplete output and preserving valid JSON."""
    if len(format_messages(payload, as_json)) + 1 <= maximum:
        return
    payload["truncated"] = True
    payload["marked_read"] = []
    for item in reversed(payload["items"]):
        if "text" in item:
            item["text"] = ""
            item["truncated"] = True
            if len(format_messages(payload, as_json)) + 1 <= maximum:
                return
    omitted = 0
    while payload["items"] and len(format_messages(payload, as_json)) + 1 > maximum:
        payload["items"].pop()
        omitted += 1
        payload["omitted_count"] = omitted
    if len(format_messages(payload, as_json)) + 1 > maximum:
        raise ValueError("输出元数据超过预算，请提高 --max-chars")


def _select_records(records, identifiers):
    selected = []
    for identifier in identifiers:
        absolute = notifications._canonical(Path(identifier)) if Path(identifier).is_absolute() else None
        matches = [item for item in records if item["id"] == identifier or item["path"] == identifier
                   or (absolute is not None and notifications._canonical(Path(item["path"])) == absolute)
                   or (item["id"] == item["path"] and Path(identifier).name == identifier
                       and Path(item["path"]).name == identifier)]
        if len(matches) != 1:
            raise ValueError("消息标识不存在、无关或同名有歧义，请先 list 并使用绝对路径：" + identifier)
        if matches[0] not in selected:
            selected.append(matches[0])
    return selected


def _require_sender(root, args, roots=None):
    if not args.agent or not args.session:
        raise ValueError("发送或回复必须提供 --agent 和 --session，不能匿名写入协作记录")
    identity = notifications.notification_identity(root, args.agent, args.session)
    session_id = identity["session"]
    if not session_id or identity["agent"] != args.agent:
        raise ValueError("当前工作区没有可核验的会话身份，先登记或检查 --agent/--session")
    board = notifications._board()
    path = board._checked_state_path(root, board.state_path(root, "sessions") / (session_id + ".md"))
    if not path.is_file():
        raise ValueError("当前工作区不存在该会话，不能以它的身份发送消息")
    import agents_activity as activity
    snapshot = activity.read_session_record(root, session_id)
    record = snapshot[4]
    if record.get("agent") != identity["agent"]:
        raise ValueError("平台与会话所有者不一致，不能共用其它会话的身份")
    state = board.validate_session_status(record)
    if state in board.CLOSED_WORDS:
        raise ValueError("已结束会话不能发送消息；新任务请先登记")
    # A non-Git project may still exchange messages, but cannot claim a live
    # Git branch. Explicitly reject mismatches whenever a Git repo is present.
    branch = board._git_at(root, "branch", "--show-current") if (root / ".git").exists() else record.get("branch")
    activity._validate(root, record, args.agent, (branch or "").strip())
    roots = roots if roots is not None else notifications.notification_roots(root)
    locations = [path for workspace in roots
                 for path in [board._checked_state_path(Path(workspace),
                              board.state_path(Path(workspace), "sessions") / (session_id + ".md"))]
                 if path.is_file()]
    if locations != [path]:
        raise ValueError("多个工作区存在相同会话 ID，不能以歧义身份写消息")
    return identity


def _active_sessions(root, roots):
    board = notifications._board()
    pairs = [(Path(item), index == 0) for index, item in enumerate(roots)]
    return board.load_sessions(pairs, datetime.now(), 15)


def _resolve_target(value, sessions):
    target = (value or "").strip()
    if not target:
        raise ValueError("请用 --to 指定会话 ID 或 any")
    if target == "any":
        return target
    matches = [item for item in sessions if item["id"] == target]
    if len(matches) > 1:
        raise ValueError("目标会话 ID 在多个工作区重复，先协调原记录：" + target)
    if not matches:
        platforms = [item for item in sessions if item.get("agent") == target and not item["closed"]]
        if platforms:
            return target
        raise ValueError("目标会话或平台不存在：" + target)
    if matches[0]["closed"]:
        raise ValueError("目标会话已结束，不能发送消息：" + target)
    if matches[0].get("status_error") or matches[0].get("claims_error"):
        raise ValueError("目标会话记录无效，先核对原记录：" + target)
    return target


def _append_reply(text: str, session_id: str, body: str, stamp: str) -> str:
    ending = "\r\n" if "\r\n" in text else ("\r" if "\r" in text and "\n" not in text else "\n")
    # Use the same fenced-code-aware boundary as notification fingerprints.
    boundary = notifications.reply_boundary(text)
    if boundary is not None:
        head, tail = text[:boundary], text[boundary:]
    else:
        head = text + (ending if not text.endswith(("\r", "\n")) else "") + ending + "## 回复" + ending
        tail = ""
    if not notifications._has_reply(tail):
        tail = ""
    entry = f"回复（{session_id}，{stamp}）：{ending}" + ending.join(body.splitlines()) + ending
    return head + tail + (ending if tail and not tail.endswith(("\n", "\r")) else "") + ending + entry


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _message_snapshot(root, path):
    board = notifications._board()
    board._checked_state_path(root, path)
    if path.parent != board.state_path(root, "inbox") or not path.name.startswith("Q-"):
        raise ValueError("消息写入只用于该工作区的 inbox 原文件")
    before = path.lstat()
    raw, text = notifications._full_bytes(root, path)
    after = path.lstat()
    if not stat.S_ISREG(after.st_mode) or _signature(before) != _signature(after) or "\x00" in text:
        raise ValueError("消息记录损坏或读取期间发生变化，请核对原文件：" + str(path))
    fields = board.parse_kv_text(text)
    if fields != notifications._metadata(path):
        raise ValueError("消息头部在读取后发生变化，请重新读取：" + str(path))
    if board.status_word(fields.get("status", "open")) not in (board.INBOX_CLOSED_WORDS | {"open"}):
        raise ValueError("消息状态无效，先核对原记录：" + str(path))
    return root, path, raw, text, after, fields


def _assert_message(snapshot):
    root, path, raw, _text, info, _fields = snapshot
    fresh = _message_snapshot(root, path)
    if fresh[2] != raw or _signature(fresh[4]) != _signature(info):
        raise ValueError("消息在写入前被修改，保留原文件并重新读取：" + str(path))


def _replace_message(snapshot, updated, guard):
    root, path, raw, _text, info, _fields = snapshot
    content = updated.encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        content = b"\xef\xbb\xbf" + content
    if len(content) > notifications.MAX_MESSAGE_BYTES:
        raise ValueError("回复后消息超过 1 MiB，先拆分原记录")
    _assert_message(snapshot)
    temporary = None
    own = None
    try:
        fd, name = tempfile.mkstemp(prefix="." + path.name + ".reply-", suffix=".tmp", dir=str(path.parent))
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            own = os.fstat(stream.fileno())
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        board = notifications._board()
        board._checked_state_path(root, temporary)
        current = temporary.lstat()
        if (current.st_dev, current.st_ino) != (own.st_dev, own.st_ino):
            raise ValueError("回复临时文件被替换，保留外部文件")
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        guard()
        _assert_message(snapshot)
        board._checked_state_path(root, temporary)
        current = temporary.lstat()
        if ((current.st_dev, current.st_ino) != (own.st_dev, own.st_ino)
                or temporary.read_bytes() != content):
            raise ValueError("回复临时文件被修改，停止替换")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and own is not None:
            try:
                notifications._board()._checked_state_path(root, temporary)
                current = temporary.lstat()
                if (current.st_dev, current.st_ino) == (own.st_dev, own.st_ino):
                    if os.name == "nt":
                        os.chmod(temporary, stat.S_IWRITE | stat.S_IREAD)
                    temporary.unlink()
            except (ValueError, OSError, RuntimeError):
                pass


def _run_action(args):
    board = notifications._board()
    root = board.detect_root(args.root)
    roots = notifications.notification_roots(root)
    identity = _require_sender(root, args, roots)
    import agents_session as sessions

    with sessions.coordination_lock(Path(roots[0])):
        fresh_roots = notifications.notification_roots(root)
        if fresh_roots != roots:
            raise ValueError("等待期间工作区清单发生变化，未写入消息")
        identity = _require_sender(root, args, roots)
        if args.cmd == "send":
            if args.action != "message":
                raise ValueError("agent 不能发送 pause/cancel/avoid 控制指令；控制只能由人类看板发出")
            target = _resolve_target(args.to, _active_sessions(root, roots))
            dest = board.create_directive(board._checked_state_directory(root, "inbox"),
                                          target, args.body, sender=identity["session"], label="协作消息")
            payload = {"ok": True, "schema_version": 1, "command": "send", "root": str(root),
                       "agent": identity["agent"], "session": identity["session"],
                       "to": target, "file": dest.name, "path": str(dest), "action": "message"}
        else:
            records = notifications.scan_messages(root, identity["agent"], identity["session"],
                                                  roots=roots, include_closed=True, include_content=False)[1]
            item = _select_records(records, [args.identifier])[0]
            if item.get("kind") != "incoming":
                raise ValueError("只能回复发给当前平台、当前会话或 any 的消息")
            targets = {"any", identity["agent"], identity["session"]}
            if item.get("to") not in targets:
                raise ValueError("该消息不是发给当前平台或会话的，不能代答")
            path = Path(item["path"])
            if path.is_symlink() or not path.is_file():
                raise ValueError("消息文件不是普通文件，停止回复：" + str(path))
            fresh = board.parse_kv(path)
            if fresh.get("to") not in targets:
                raise ValueError("消息在回复前已被改动，目标不是当前平台或会话；请重新读取")
            body = "\n".join(_CONTROL.sub("", args.body or "").splitlines()).strip()
            if not body:
                raise ValueError("回复正文为空")
            if len(body) > _MAX_REPLY_LEN:
                raise ValueError(f"回复超过 {_MAX_REPLY_LEN} 字")
            body = board._neutralize_body(body)
            stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
            workspace = Path(item["workspace"])
            snapshot = _message_snapshot(workspace, path)
            if hashlib.sha256(snapshot[2]).hexdigest() != item["version"]:
                raise ValueError("消息在读取后已变化，请重新读取原要求")
            if args.request_version is not None and args.request_version != item["request_version"]:
                raise ValueError("原要求版本已变化，未写回复；请重新读取并处理新要求")
            if (snapshot[5]["from"].casefold().startswith("human")
                    and snapshot[5].get("action", "message") != "message"
                    and board.status_word(snapshot[5].get("status", "")) in board.CONTROL_RELEASE_WORDS):
                raise ValueError("控制已经由人类解除，不能通过回复重新激活它")
            text = snapshot[3]
            updated = _append_reply(text, identity["session"], body, stamp)
            if notifications._sections(updated)[0] != notifications._sections(text)[0]:
                raise ValueError("无法可靠分隔原要求与回复，请先修正原记录的代码围栏或回复标题")
            from agents_activity import update_session_fields
            updated = update_session_fields(updated, {
                "status": args.status,
                "answered": stamp if args.status == "answered" else "",
            })
            _replace_message(snapshot, updated, lambda: _require_sender(root, args, roots))
            payload = {"ok": True, "schema_version": 1, "command": "reply", "root": str(root),
                       "agent": identity["agent"], "session": identity["session"],
                       "file": path.name, "path": str(path), "status": args.status}
    if args.agent and args.session and not args.no_activity:
        from agents_activity import safe_touch_activity
        warning = safe_touch_activity(root, args.agent, args.session,
                                      "agents_messages." + args.cmd).get("warning")
        if warning:
            payload["activity_warning"] = warning
    try:
        notice = notifications.collect_notifications(root, args.agent, args.session, roots=roots)
        if notice:
            payload["notifications"] = notice
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        payload["notification_warning"] = str(exc)[:200]
    return payload


def _format_action(payload):
    if payload["command"] == "send":
        text = "已发送 " + payload["file"] + " → " + payload["to"]
    else:
        text = "已回复 " + payload["file"] + "（status=" + payload["status"] + "）"
    if payload.get("activity_warning"):
        text += "\n活动记录失败 " + payload["activity_warning"]
    if payload.get("notifications"):
        text += "\n" + notifications.format_notification(payload["notifications"])
    if payload.get("notification_warning"):
        text += "\n提醒检查失败 " + payload["notification_warning"]
    return text


def _archive_references(roots):
    """Keep files needed to resume or finish any still-open session."""
    board = notifications._board()
    protected = set()
    owners = []
    for workspace in roots:
        for path in board._state_record_files(Path(workspace), "sessions", "*.md"):
            if path.name.startswith("_"):
                continue
            fields = board.parse_kv(path)
            state = board.validate_session_status(fields)
            if state in board.CLOSED_WORDS:
                from agents_jobs import ensure_no_running_jobs
                try:
                    ensure_no_running_jobs(fields)
                except ValueError:
                    pass
                else:
                    continue
            owners.append((Path(workspace), path.stem, fields.get("agent")))
            if state == "standby":
                import agents_session as sessions
                if sessions.valid_standby(fields):
                    continue
            references = [fields["handoff_inbox"]] if fields.get("handoff_inbox") else []
            for key in ("acknowledged_inbox", "acknowledged_inbox_versions"):
                if key not in fields:
                    continue
                value = json.loads(fields[key])
                if not isinstance(value, list if key == "acknowledged_inbox" else dict):
                    raise ValueError("未结束会话的消息关联无效，停止归档：" + str(path))
                references.extend(value)
            for value in references:
                if not isinstance(value, str) or not value:
                    raise ValueError("未结束会话的消息关联无效，停止归档：" + str(path))
                reference = Path(value)
                if reference.is_absolute():
                    protected.add(notifications._canonical(reference))
                elif len(reference.parts) == 1:
                    protected.update(notifications._canonical(board.state_path(Path(root), "inbox") / value)
                                     for root in roots)
                else:
                    protected.add(notifications._canonical(Path(workspace) / reference))
    return protected, owners


def _archive_plan(args, board, roots, root):
    """Scan local history independently of the live 4096-message queue limit."""
    cutoff = datetime.now() - timedelta(days=args.older_than)
    candidates, skipped = [], []
    protected, owners = _archive_references(roots)
    receipts = {}
    paths = _inbox_paths(root)
    for path in paths:
        name = path.name
        reason = ""
        try:
            snapshot = _message_snapshot(root, path)
            fields = snapshot[5]
            created = board.parse_time(fields.get("created", ""))
            answered = board.parse_time(fields.get("answered", ""))
            if fields.get("action", "message") != "message":
                reason = "控制指令保留原路径"
            elif board.status_word(fields.get("status", "")) not in board.INBOX_CLOSED_WORDS:
                reason = "事项未关闭"
            elif created is None or (fields.get("answered") and answered is None):
                reason = "created/answered 时间缺失或无法解析，不猜测日期"
            elif max(created, answered or created, datetime.fromtimestamp(snapshot[4].st_mtime)) > cutoff:
                reason = "创建、回复或最后修改仍在保留期内"
            elif notifications._canonical(path) in protected:
                reason = "未结束会话仍引用此消息"
            else:
                reply = notifications._sections(snapshot[3])[1]
                if notifications._has_reply(reply):
                    for workspace, identifier, agent in owners:
                        if fields["from"] not in (identifier, agent):
                            continue
                        identity = {"root": notifications._canonical(workspace), "agent": agent, "session": identifier}
                        key = (str(workspace), identifier)
                        if key not in receipts:
                            receipts[key] = notifications._load_receipt(workspace, identity)[1]
                        version = notifications._notification_version({"kind": "reply", "path": str(path),
                            "from": fields["from"], "to": fields["to"], "status": board.status_word(fields["status"]),
                            "reply": reply})
                        if receipts[key].get(notifications._canonical(path)) != version:
                            reason = "已登记发送者仍有未读回复"
                            break
        except (ValueError, OSError, RuntimeError) as exc:
            reason = "无法可靠读取原记录：" + str(exc)[:200]
        if reason:
            skipped.append({"file": name, "path": str(path), "reason": reason})
            continue
        archive_dir = board.state_path(root, "inbox/archive/" + created.strftime("%Y-%m"))
        destination = board._checked_state_path(root, archive_dir / name)
        candidates.append({"file": name, "path": str(path), "workspace": str(root),
                           "destination": str(destination), "version": hashlib.sha256(snapshot[2]).hexdigest(),
                           "_signature": _signature(snapshot[4])})
    return candidates, skipped


def _assert_archive_source(root, item):
    snapshot = _message_snapshot(root, Path(item["path"]))
    if (hashlib.sha256(snapshot[2]).hexdigest() != item["version"]
            or _signature(snapshot[4]) != item["_signature"]):
        raise ValueError("归档源在预览后发生变化，保留原消息：" + item["file"])
    return snapshot


def _move_no_overwrite(root, source, destination, expected):
    """Keep bytes/mode/mtime and refuse collisions on every supported platform."""
    board = notifications._board()
    board._checked_state_path(root, source)
    board._checked_state_path(root, destination)
    if destination.exists():
        raise ValueError("归档目标已存在，拒绝覆盖：" + str(destination))
    if os.name == "nt":
        # Windows rename refuses existing targets and supports moving readonly
        # files on FAT/network volumes without changing their mode attributes.
        if _signature(source.lstat()) != _signature(expected[1]) or source.read_bytes() != expected[0]:
            raise ValueError("归档原文件在移动前发生变化")
        os.rename(source, destination)
        return
    created = None
    try:
        try:
            os.link(source, destination)
            linked = destination.lstat()
            if (linked.st_dev, linked.st_ino) != (expected[1].st_dev, expected[1].st_ino):
                raise ValueError("归档目标在创建后被替换，保留外部文件")
            created = linked
        except FileExistsError:
            raise ValueError("归档目标已存在，拒绝覆盖：" + str(destination)) from None
        except OSError:
            # FAT/network volumes may lack hard links; an exclusive copy gives
            # the same no-overwrite guarantee and keeps the source until checked.
            fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                created = os.fstat(stream.fileno())
                stream.write(expected[0])
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(destination, stat.S_IMODE(expected[1].st_mode))
            os.utime(destination, ns=(expected[1].st_atime_ns, expected[1].st_mtime_ns))
        board._checked_state_path(root, source)
        board._checked_state_path(root, destination)
        current = source.lstat()
        current_destination = destination.lstat()
        if (_signature(current) != _signature(expected[1]) or source.read_bytes() != expected[0]
                or (current_destination.st_dev, current_destination.st_ino) != (created.st_dev, created.st_ino)
                or destination.read_bytes() != expected[0]):
            raise ValueError("归档移动期间原文件发生变化，保留现场：" + str(source))
        source.unlink()
        created = None
    finally:
        if created is not None:
            try:
                board._checked_state_path(root, destination)
                info = destination.lstat()
                if (info.st_dev, info.st_ino) == (created.st_dev, created.st_ino):
                    destination.unlink()
            except (OSError, ValueError, RuntimeError):
                pass


def _run_archive(args):
    board = notifications._board()
    root = board.detect_root(args.root)
    roots = notifications.notification_roots(root)
    if not 1 <= args.older_than <= 365000:
        raise ValueError("--older-than 必须是正整数天数")
    if not 1 <= args.limit <= 1000:
        raise ValueError("--limit 应为 1 至 1000")
    if args.apply:
        _require_sender(root, args, roots)
    candidates, skipped = _archive_plan(args, board, roots, root)
    selected = candidates[:args.limit]
    payload = {"ok": True, "schema_version": 1, "command": "archive", "root": str(root),
               "applied": bool(args.apply), "older_than_days": args.older_than,
               "candidates": [{k: v for k, v in item.items() if not k.startswith("_")} for item in selected],
               "candidates_count": len(candidates), "moved": [], "moved_count": 0, "skipped": skipped,
               "skipped_count": len(skipped), "remaining_count": len(candidates) - len(selected),
               "truncated": False, "omitted_count": 0}
    if not args.apply or not selected:
        return payload
    import agents_session as sessions

    with sessions.coordination_lock(Path(roots[0])):
        if notifications.notification_roots(root) != roots:
            raise ValueError("等待期间工作区清单发生变化，未归档消息")
        _require_sender(root, args, roots)
        protected, _owners = _archive_references(roots)
        for item in selected:
            _assert_archive_source(root, item)
            if notifications._canonical(Path(item["path"])) in protected:
                raise ValueError("归档源新关联了未结束会话，停止归档：" + item["file"])
            destination = board._checked_state_path(root, Path(item["destination"]))
            if destination.exists():
                raise ValueError("归档目标已存在，拒绝覆盖：" + str(destination))
        moved = []
        try:
            for item in selected:
                snapshot = _assert_archive_source(root, item)
                source, destination = Path(item["path"]), Path(item["destination"])
                board._checked_state_path(root, destination.parent)
                destination.parent.mkdir(parents=True, exist_ok=True)
                board._checked_state_path(root, destination.parent)
                _move_no_overwrite(root, source, destination, (snapshot[2], snapshot[4]))
                moved.append((item, snapshot[2]))
                payload["moved"].append({"file": item["file"], "path": str(source), "destination": str(destination)})
        except BaseException as exc:
            incomplete = []
            for item, raw in reversed(moved):
                destination = Path(item["destination"])
                try:
                    board._checked_state_path(root, destination)
                    if destination.read_bytes() != raw:
                        raise ValueError("归档内容被外部修改")
                    _move_no_overwrite(root, destination, Path(item["path"]), (raw, destination.lstat()))
                except (ValueError, OSError, RuntimeError):
                    incomplete.append(str(destination))
            if incomplete:
                raise ValueError("本批归档失败且回滚未完成，保留现场：" + ", ".join(incomplete)) from exc
            raise
    payload["moved_count"] = len(payload["moved"])
    if args.agent and args.session and not args.no_activity:
        from agents_activity import safe_touch_activity
        warning = safe_touch_activity(root, args.agent, args.session, "agents_messages.archive").get("warning")
        if warning:
            payload["activity_warning"] = warning
    return payload


def _format_archive(payload):
    mode = "已移动" if payload["applied"] else "dry-run 预览（未移动任何文件）"
    lines = [mode + "；阈值 " + str(payload["older_than_days"]) + " 天",
             "候选 " + str(payload["candidates_count"]) + " 条，已移动 "
             + str(payload["moved_count"]) + " 条，跳过 " + str(payload["skipped_count"]) + " 条"]
    for item in payload["moved"] or payload["candidates"]:
        lines.append(item["file"] + " → " + item["destination"])
    for item in payload["skipped"]:
        lines.append("跳过 " + item["file"] + "：" + item["reason"])
    if payload["remaining_count"]:
        lines.append("本批外仍有 " + str(payload["remaining_count"]) + " 条候选；可重复归档或调整 --limit。")
    if payload["truncated"]:
        lines.append("明细已截断；提高 --max-chars 查看。总数未截断。")
    return "\n".join(lines)


def _fit_archive(payload, maximum, as_json):
    def render():
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) if as_json else _format_archive(payload)
    for key in ("skipped", "candidates", "moved"):
        while payload[key] and len(render()) + 1 > maximum:
            # Geometric trimming keeps large history previews linear instead
            # of serializing the whole list once for every removed record.
            removed = max(1, len(payload[key]) // 2)
            del payload[key][-removed:]
            payload["truncated"] = True
            payload["omitted_count"] += removed
    if len(render()) + 1 > maximum:
        raise ValueError("归档输出元数据超过预算，请提高 --max-chars")


def _prepare(args):
    root = notifications._board().detect_root(args.root)
    roots = notifications.notification_roots(root)
    identity, records = notifications.scan_messages(root, args.agent, args.session, roots=roots,
                                                     include_closed=args.cmd == "read" and bool(args.identifiers),
                                                     include_content=args.cmd == "read")
    if args.cmd == "read":
        selected = _select_records(records, args.identifiers) if args.identifiers else [item for item in records if item["unread"]]
    else:
        selected = records
    payload = {"ok": True, "schema_version": 1, "root": str(root), "agent": identity["agent"], "session": identity["session"],
               "unread_count": sum(item["unread"] for item in records),
               "human_pending": sum(item["human"] and item["open"] and item["kind"] == "incoming" for item in records),
               "items": [_metadata(item) for item in selected], "truncated": False, "omitted_count": 0, "marked_read": []}
    if args.cmd == "read":
        for output, original in zip(payload["items"], selected):
            output["text"] = original["text"]
    _fit(payload, args.max_chars, args.json)
    return root, identity, selected, payload, roots


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                # An empty flush can succeed with a closed fd on POSIX. Reject
                # it before message/lock files can reuse that descriptor.
                if isinstance(stream, io.TextIOWrapper):
                    os.fstat(stream.fileno())
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError) as exc:
                _silence_failed_stream(stream)
                return _report_error(exc, "--json" in arguments, after_output=stream is sys.stdout)
    if sys.stdout is None:
        return _report_error(OSError("标准输出不可用，未登记阅读回执"), "--json" in arguments, after_output=True)
    parser = _ArgumentParser(description=__doc__)
    _common(parser)
    commands = parser.add_subparsers(dest="cmd", required=True)
    listing = commands.add_parser("list", help="只查看相关消息元数据")
    _common(listing, True)
    reading = commands.add_parser("read", help="按需读取相关消息正文")
    _common(reading, True)
    reading.add_argument("identifiers", nargs="*", help="唯一文件名或消息绝对路径；省略时读取相关未读消息")
    reading.add_argument("--mark-read", action="store_true", help="仅对完整且版本未变的内容登记阅读回执")
    sending = commands.add_parser("send", help="以当前会话身份发送一条协作消息")
    _common(sending, True)
    sending.add_argument("--to", required=True, help="目标会话 ID、已登记平台或 any")
    sending.add_argument("--body", required=True, help="消息正文")
    sending.add_argument("--action", default="message", choices=sorted(notifications._ACTIONS),
                         help="agent 只能发送 message；控制指令由人类看板发出")
    replying = commands.add_parser("reply", help="回复一条发给当前平台或会话的消息")
    _common(replying, True)
    replying.add_argument("identifier", help="唯一文件名或消息绝对路径")
    replying.add_argument("--body", required=True, help="回复正文")
    replying.add_argument("--status", choices=("answered", "open"), default="open",
                          help="默认保持 open；实际完成后显式 --status answered")
    replying.add_argument("--request-version", help="read 返回的 request_version；有变化时拒绝旧回复")
    archiving = commands.add_parser("archive", help="归档已关闭且超过保留期的普通消息；默认只预览")
    _common(archiving, True)
    archiving.add_argument("--older-than", dest="older_than", type=int, default=30,
                           help="仅归档 created 早于该天数的已关闭消息（默认 30）")
    archiving.add_argument("--apply", action="store_true",
                           help="实际移动文件；省略时只输出候选清单，不写任何文件")
    archiving.add_argument("--limit", type=int, default=100, help="本批最多归档条数，1–1000，默认 100")
    try:
        args = parser.parse_args(arguments)
    except ValueError as exc:
        return _report_error(exc, "--json" in arguments)
    if not 1024 <= args.max_chars <= 100000:
        return _report_error(ValueError("--max-chars 应为1024至100000"), args.json)
    if (args.cmd == "reply" and args.request_version is not None
            and not re.fullmatch(r"[0-9a-f]{64}", args.request_version)):
        return _report_error(ValueError("--request-version 应为原要求的 64 位小写 SHA256"), args.json)
    delivered = False
    output_started = False
    if args.cmd in ("send", "reply"):
        payload = None
        try:
            payload = _run_action(args)
            output_started = True
            _write_output(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                          if args.json else _format_action(payload), sys.stdout)
            return 0
        except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            if payload is not None and output_started:
                exc = ValueError("消息已写入 " + payload["path"] + "，输出失败；先 list/read 核验结果再重试：" + str(exc))
            return _report_error(exc, args.json, after_output=output_started)
    if args.cmd == "archive":
        payload = None
        try:
            payload = _run_archive(args)
            _fit_archive(payload, args.max_chars, args.json)
            output_started = True
            _write_output(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                          if args.json else _format_archive(payload), sys.stdout)
            return 1 if payload["truncated"] and not args.apply else 0
        except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            if payload is not None and args.apply and payload["moved_count"]:
                exc = ValueError("本批已归档 " + str(payload["moved_count"]) + " 条，输出失败；到 inbox/archive 核验结果：" + str(exc))
            return _report_error(exc, args.json, after_output=output_started)
    try:
        root, identity, selected, payload, roots = _prepare(args)
        if args.agent and args.session and not args.no_activity:
            from agents_activity import safe_touch_activity
            warning = safe_touch_activity(root, args.agent, args.session, "agents_messages." + args.cmd).get("warning")
            if warning:
                payload["activity_warning"] = warning
                _fit(payload, args.max_chars, args.json)

        def emit():
            nonlocal delivered, output_started
            output_started = True
            _write_output(format_messages(payload, args.json), sys.stdout)
            delivered = True

        if args.cmd == "read" and args.mark_read and not payload["truncated"]:
            # Reserve receipt-result space before any write, so the final output also fits.
            payload["marked_read"] = [item["id"] for item in selected]
            _fit(payload, args.max_chars, args.json)
            if not payload["truncated"]:
                payload["marked_read"] = notifications.mark_read(root, selected, identity["agent"], identity["session"],
                                                                roots=roots, emit=emit)
        if not delivered:
            emit()
        return 1 if payload["truncated"] else 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        return _report_error(exc, args.json, after_output=output_started)


if __name__ == "__main__":
    sys.exit(main())
