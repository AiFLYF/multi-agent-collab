#!/usr/bin/env python3
"""List message metadata, or read messages and explicitly record complete reads."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import agents_notifications as notifications  # noqa: E402

DEFAULT_MAX_CHARS = 6000


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


def _prepare(args):
    root = notifications._board().detect_root(args.root)
    roots = notifications.notification_roots(root)
    identity, records = notifications.scan_messages(root, args.agent, args.session, roots=roots,
                                                     include_closed=args.cmd == "read" and bool(args.identifiers),
                                                     include_content=args.cmd == "read")
    if args.cmd == "read":
        selected = []
        for identifier in args.identifiers:
            absolute = notifications._canonical(Path(identifier)) if Path(identifier).is_absolute() else None
            matches = [item for item in records if item["id"] == identifier or item["path"] == identifier
                       or (absolute is not None and notifications._canonical(Path(item["path"])) == absolute)
                       or (item["id"] == item["path"] and Path(identifier).name == identifier and Path(item["path"]).name == identifier)]
            if len(matches) != 1:
                raise ValueError("消息标识不存在、无关或同名有歧义，请先 list 并使用绝对路径：" + identifier)
            if matches[0] not in selected:
                selected.append(matches[0])
        if not args.identifiers:
            selected = [item for item in records if item["unread"]]
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
    try:
        args = parser.parse_args(arguments)
    except ValueError as exc:
        return _report_error(exc, "--json" in arguments)
    if not 1024 <= args.max_chars <= 100000:
        return _report_error(ValueError("--max-chars 应为1024至100000"), args.json)
    delivered = False
    output_started = False
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
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return _report_error(exc, args.json, after_output=output_started)


if __name__ == "__main__":
    sys.exit(main())
