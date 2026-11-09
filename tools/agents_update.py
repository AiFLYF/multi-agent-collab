#!/usr/bin/env python3
"""Update an installed project from its recorded local kit source, without fetching."""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from agents_layout import state_dir  # noqa: E402


def project_root(raw: str) -> Path:
    requested = Path(raw).expanduser().resolve()
    for candidate in (requested, *requested.parents):
        if (candidate / ".git").exists():
            if ".git" in requested.relative_to(candidate).parts:
                raise ValueError("请指定实际项目工作区，不能从 .git 元数据目录更新")
            return candidate
    return requested


def local_source(root: Path, explicit: str | None) -> Path:
    metadata = root / state_dir(root) / "install.json"
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError("没有可核验的安装记录；首次安装请使用套件源的 install.py install")
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("安装记录不可读取或损坏，停止更新") from exc
    if not isinstance(record, dict) or record.get("owner") not in (None, "multi-agent-collab"):
        raise ValueError("安装记录归属不明，停止更新")
    raw = explicit if explicit is not None else record.get("source_dir")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("本地套件源路径缺失，请用 --source <套件目录> 明确指定")
    source = Path(raw).expanduser().resolve()
    script = source / "install.py"
    if script.is_symlink() or not script.is_file():
        raise ValueError(f"本地套件源不存在或不是普通文件：{script}；请用 --source 指定现有套件目录")
    try:
        tree = ast.parse(script.read_text(encoding="utf-8-sig"))
    except (OSError, SyntaxError) as exc:
        raise ValueError("本地套件源无法解析，停止更新") from exc
    if not any(isinstance(node, ast.FunctionDef) and node.name == "cmd_update" for node in tree.body):
        raise ValueError("本地套件源尚不支持 update；请先准备新版套件源，再用 --source 指定")
    return source


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="已安装项目或其子目录，默认当前目录")
    parser.add_argument("--source", help="改用另一份本地套件源；成功更新后记住该路径")
    parser.add_argument("--check", action="store_true", help="只检查：0已最新，1可更新，2错误")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不修改文件")
    parser.add_argument("--diff", action="store_true", help="显示详细文本差异")
    parser.add_argument("--json", action="store_true", help="输出结构化结果")
    parser.add_argument("--allow-downgrade", action="store_true", help="明确允许较低版本的本地源")
    parser.add_argument("--agent", help="当前平台，用于定向消息提示")
    parser.add_argument("--session", help="当前会话 ID，用于消息阅读回执隔离")
    parser.add_argument("--no-activity", action="store_true", help="只读审计，不更新自己的最近活动")
    args = parser.parse_args(argv)
    try:
        root = project_root(args.root)
        source = local_source(root, args.source)
        from agents_notifications import (
            collect_notifications, format_notification, human_controls, notification_identity, notification_roots,
        )
        roots = notification_roots(root)
        identity = notification_identity(root, args.agent, args.session)
        if not args.check and not args.dry_run:
            controls = human_controls(roots, identity["agent"], identity["session"])
            if controls:
                raise ValueError("先处理暂停/取消/避让指令；阅读不能解除控制：" +
                                 ", ".join(f"{key} ({value})" for key, value in controls.items()))
        activity_warning = None
        if args.agent and args.session and not args.no_activity and not args.check and not args.dry_run:
            from agents_activity import safe_touch_activity
            activity_warning = safe_touch_activity(root, args.agent, args.session, "agents_update").get("warning")
        command = [sys.executable, "-B", str(source / "install.py"), "update", str(root)]
        for name in ("check", "dry_run", "diff", "json", "allow_downgrade"):
            if getattr(args, name):
                command.append("--" + name.replace("_", "-"))
        # Context flags stay in this installed wrapper so an older local source
        # remains usable, including when --allow-downgrade was explicitly chosen.
        child = subprocess.run(command, cwd=str(root), shell=False, capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        output = child.stdout.decode("utf-8", errors="replace")
        errors = child.stderr.decode("utf-8", errors="replace")
        if errors:
            print(errors, file=sys.stderr, end="")
        try:
            notice = collect_notifications(root, identity["agent"], identity["session"], roots)
        except (ValueError, OSError, RuntimeError) as exc:
            notice = {"error": str(exc)[:500]}
        if args.json:
            try:
                payload = json.loads(output)
                if not isinstance(payload, dict):
                    raise ValueError
            except ValueError:
                payload = {"ok": False, "error": (errors or output or "本地更新源未返回合法 JSON")[:1000]}
                print(json.dumps(payload, ensure_ascii=False))
                return child.returncode or 2
            payload.pop("notifications", None)
            if notice:
                payload["notifications"] = notice
            if activity_warning:
                payload["activity_warning"] = activity_warning
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            # The newer source can already emit a notice; keep one final notice
            # using the caller's explicit identity, rather than duplicating it.
            lines = output.splitlines(keepends=True)
            print("".join(line for line in lines if not line.startswith(
                ("提醒：inbox", "提醒：已读人类事项", "[提醒检查失败]"))), end="")
            if notice:
                print("[提醒检查失败] " + notice["error"] if "error" in notice else format_notification(notice))
            if activity_warning:
                print("[活动记录失败] " + activity_warning, file=sys.stderr)
        return child.returncode
    except (ValueError, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print("[FAIL] " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
