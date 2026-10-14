#!/usr/bin/env python3
"""管理本机会话与文件占用：check / start / heartbeat / handoff / resume / finish。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# 此脚本会安装进任意技术栈的项目，不能在 tools/ 下留下未被忽略的 Python 缓存。
sys.dont_write_bytecode = True
import agents_status as board


class ConflictError(ValueError):
    pass


def git(root: Path, *args: str) -> str:
    executable = board.resolve_git()
    if not executable:
        raise ValueError("Git 不可用，无法核对工作区")
    result = subprocess.run([executable, *args], cwd=str(root), capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=20)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git 检查失败")
    return result.stdout.strip()


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"路径指向工作区外部：{relative}") from None
    return path


def one_line(value: str, label: str) -> str:
    value = value.strip()
    if not value or "\n" in value or "\r" in value or len(value) > 1000:
        raise ValueError(f"{label} 必须是 1–1000 字符的单行文本")
    return value


def normalize_files(root: Path, raw: list[str]) -> list[str]:
    files = []
    for value in raw:
        value = one_line(value, "文件范围").replace("\\", "/")
        if value.startswith("/") or ":" in value or ".." in value.split("/") or any(c in value for c in "*?"):
            raise ValueError(f"请使用项目相对文件或目录路径，不使用通配符：{value}")
        path = safe_path(root, value)
        relative = path.relative_to(root).as_posix().rstrip("/")
        if relative in ("", ".") or relative.startswith(".git/") or relative == ".git":
            raise ValueError("请声明具体项目文件或目录，不能占用整个工作区或 .git")
        if path.is_dir() or value.endswith("/"):
            relative += "/"
        if relative not in files:
            files.append(relative)
    return files


def worktree_roots(root: Path) -> list[Path]:
    raw = git(root, "worktree", "list", "--porcelain")
    roots = [Path(line[9:]).resolve() for line in raw.splitlines() if line.startswith("worktree ")]
    if not roots:
        raise ValueError("无法确定共享仓库的工作区，停止写入")
    return roots


@contextmanager
def coordination_lock(primary: Path):
    """进程退出自动释放系统锁；不删除锁文件，避免替换 inode 形成两把锁。"""
    lock_path = safe_path(primary, ".agents/coordination.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
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
                    raise ConflictError("另一个会话正在登记或更新状态，请稍后重试；没有抢占锁") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sessions_in(roots: list[Path]) -> list[dict]:
    return board.load_sessions([(root, False) for root in roots], datetime.now(), 15)


def pending_human_inbox(roots: list[Path], agent: str | None, session_id: str | None = None) -> list[str]:
    return [q["file"] for q in board.load_inbox([(root, False) for root in roots])
            if q["open"] and q["from_human"] and q["to"] in ("any", agent, session_id)]


def acknowledge_for_call(pending: list[str], requested: list[str] | None) -> list[str]:
    requested = requested or []
    unknown = set(requested) - set(pending)
    if unknown:
        raise ValueError("确认条目不是当前待处理的人类指令：" + ", ".join(sorted(unknown)))
    unseen = set(pending) - set(requested)
    if unseen:
        raise ConflictError("先读取并回复人类指令；仍在执行的事项可显式 --ack-inbox 确认已纳入任务：" + ", ".join(sorted(unseen)))
    return sorted(set(requested))


def check_conflicts(root: Path, branch: str, files: list[str], sessions: list[dict],
                    own_id: str | None = None) -> list[str]:
    conflicts = []
    for session in sessions:
        if session["closed"] or session["id"] == own_id:
            continue
        if session.get("claims_error"):
            conflicts.append(f"{session['id']} 的 files 字段无效，先核对占用范围")
            continue
        same_workspace = Path(session["workspace"]).resolve() == root
        overlaps = board.overlapping_files(files, session.get("files", []))
        if same_workspace or session["branch"] == branch or overlaps:
            reason = "同一工作区已有未结束会话" if same_workspace else (
                "分支已被占用" if session["branch"] == branch else "文件范围重叠：" + ", ".join(overlaps))
            stale = "；心跳失活仍保留占用，按接管流程处理" if session["stale"] else ""
            conflicts.append(f"{session['id']}：{reason}{stale}")
    return conflicts


def put_fields(text: str, fields: dict[str, str]) -> str:
    for key, value in fields.items():
        pattern = rf"(?m)^- {re.escape(key)}:.*$"
        replacement = f"- {key}: {value}"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda _match: replacement, text, count=1)
        else:
            metadata = list(re.finditer(r"(?m)^- [a-z_]+:.*$", text.split("\n## ", 1)[0]))
            if metadata:
                end = metadata[-1].end()
                text = text[:end] + "\n" + replacement + text[end:]
            else:
                head, separator, body = text.partition("\n\n")
                text = head + "\n\n" + replacement + "\n" + separator + body
    return text


def append_worklog(root: Path, session_id: str, branch: str, summary: str, tests: str = "") -> None:
    now = datetime.now()
    path = safe_path(root, f".agents/worklog/{now:%Y-%m-%d}.md")
    entry = f"\n## {now:%H:%M} · {session_id}\n\n- 分支：{branch}\n- {summary}\n"
    if tests:
        entry += f"- 测试：{tests}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if path.exists() else f"# {now:%Y-%m-%d}\n"
    # 真正只追加；不重写历史，避免覆盖其它仍用手工追加方式的会话。
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(prefix + entry)


def handoff_note(root: Path, session_id: str, summary: str) -> str:
    now = datetime.now()
    inbox = safe_path(root, ".agents/inbox")
    inbox.mkdir(parents=True, exist_ok=True)
    # 独立文件名避免与网页指令的递增序号竞争；from 使用 agent，绝不伪装成人类。
    path = inbox / f"Q-{now:%Y-%m-%d}-handoff-{uuid.uuid4().hex[:8]}.md"
    content = (f"# 交接：{session_id}\n\n- from: {session_id}\n- to: any\n- status: open\n"
               f"- created: {now:%Y-%m-%d %H:%M}\n\n## 问题\n\n{summary}\n\n"
               "原会话保持 blocked，文件占用保留；确认交接后再由原会话收尾释放。\n\n## 回复\n")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return path.relative_to(root).as_posix()


def close_handoffs(root: Path, record: dict, session_id: str, reply: str) -> list[str]:
    """只回执本会话生成的交接条；普通问题、他人问题及人类指令不自动作答。"""
    relative = record.get("handoff_inbox", "")
    if relative:
        candidate = safe_path(root, relative.replace("\\", "/"))
        if candidate.parent != root / ".agents/inbox" or "-handoff-" not in candidate.name:
            raise ValueError("关联交接路径无效，未修改会话")
        candidates = [candidate] if candidate.is_file() else []
    else:
        # 兼容 1.5.0：那时没有保存关联路径，仅匹配本会话生成的交接条。
        candidates = [p for p in safe_path(root, ".agents/inbox").glob("Q-*-handoff-*.md")
                      if board.parse_kv(p).get("from") == session_id]
    pending = []
    for path in candidates:
        safe_path(root, path.relative_to(root).as_posix())
        content = path.read_text(encoding="utf-8")
        fields = board.parse_kv(path)
        if fields.get("from") != session_id or not content.startswith(f"# 交接：{session_id}\n"):
            raise ValueError("关联交接不属于本会话，拒绝自动回执")
        if board.status_word(fields.get("status", "")) == "open":
            content = put_fields(content, {"status": "answered", "answered": datetime.now().strftime("%Y-%m-%d %H:%M")})
            pending.append((path, content.rstrip() + "\n\n" + reply + "\n"))
    for path, content in pending:
        board._write_lf_atomic(path, content)
    return [path.relative_to(root).as_posix() for path, _content in pending]


def execute(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    root = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if not safe_path(root, ".agents/sessions").is_dir():
        raise ValueError("项目尚未安装会话目录，请先运行安装器")
    roots = worktree_roots(root)
    branch = git(root, "branch", "--show-current")
    files = normalize_files(root, args.files or []) if hasattr(args, "files") else []
    if args.cmd == "check":
        conflicts = check_conflicts(root, branch, files, sessions_in(roots), args.session)
        pending = pending_human_inbox(roots, args.agent, args.session)
        return {"ok": not conflicts and not pending, "branch": branch, "files": files,
                "conflicts": conflicts, "human_inbox": pending}
    agent = one_line(args.agent, "平台名").lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", agent):
        raise ValueError("平台名使用小写英文、数字或连字符")
    if not branch or branch in ("main", "master", "develop"):
        raise ValueError("请先切到任务分支；会话工具不会创建分支或在共享主干登记写任务")
    with coordination_lock(roots[0]):
        # 等待锁期间可能有人切分支，写入前再次核对。
        if git(root, "branch", "--show-current") != branch:
            raise ConflictError("等待期间工作区分支发生变化，停止写入")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if args.cmd == "start":
            task = one_line(args.task, "任务")
            if not files:
                raise ValueError("开始写任务前请用 --files 声明具体文件或目录")
            pending = pending_human_inbox(roots, agent)
            acknowledged = acknowledge_for_call(pending, getattr(args, "ack_inbox", None))
            current_sessions = sessions_in(roots)
            conflicts = check_conflicts(root, branch, files, current_sessions)
            if conflicts:
                raise ConflictError("\n".join(conflicts))
            existing_ids = {session["id"] for session in current_sessions}
            while True:
                session_id = f"{agent}-{datetime.now():%y%m%d-%H%M}-{uuid.uuid4().hex[:4]}"
                path = safe_path(root, f".agents/sessions/{session_id}.md")
                if session_id not in existing_ids and not path.exists():
                    break
            text = (f"# Session: {session_id}\n\n- agent: {agent}\n- branch: {branch}\n- task: {task}\n"
                    f"- files: {json.dumps(files, ensure_ascii=False)}\n- status: active\n"
                    f"- acknowledged_inbox: {json.dumps(acknowledged, ensure_ascii=False)}\n"
                    f"- started: {now}\n- heartbeat: {now}\n- pr: \n\n"
                    "## 当前进度\n\n- 已登记任务与文件范围。\n\n## 下一步\n\n- 按项目测试门禁完成任务。\n\n"
                    "## 阻塞点 / handoff\n\n- 无\n")
            board._write_lf_atomic(path, text)
            return {"ok": True, "id": session_id, "branch": branch, "files": files, "file": str(path)}
        session_id = args.session
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", session_id):
            raise ValueError("无效的会话 ID")
        path = safe_path(root, f".agents/sessions/{session_id}.md")
        text = path.read_text(encoding="utf-8")
        record = board.parse_kv(path)
        if record.get("agent") != agent:
            raise ValueError("平台与会话所有者不一致，不能更新其它 agent 的会话")
        if record.get("branch") != branch:
            raise ConflictError("当前分支与会话登记分支不一致，先排查工作区变化")
        if board.status_word(record.get("status", "")) in board.CLOSED_WORDS:
            raise ValueError("会话已经结束，请创建新会话，不要重新激活历史记录")
        updates = {"heartbeat": now}
        result = {"ok": True, "id": session_id, "branch": branch}
        if args.cmd == "resume":
            pending = pending_human_inbox(roots, agent, session_id)
            acknowledged = acknowledge_for_call(pending, getattr(args, "ack_inbox", None))
            claimed, invalid = board.parse_claimed_files(record.get("files", ""))
            if args.files is None:
                if invalid:
                    raise ValueError("原文件范围无效，请用 --files 明确声明恢复范围")
                files = normalize_files(root, claimed)
            if not files:
                raise ValueError("恢复写任务需要具体文件范围，请使用 --files")
            conflicts = check_conflicts(root, branch, files, sessions_in(roots), session_id)
            if conflicts:
                raise ConflictError("\n".join(conflicts))
            note = one_line(args.note or "继续原任务", "恢复说明")
            result["answered_handoffs"] = close_handoffs(root, record, session_id, "原会话恢复工作：" + note)
            updates.update(status="active", files=json.dumps(files, ensure_ascii=False), progress=note, handoff="", handoff_inbox="")
            updates["acknowledged_inbox"] = json.dumps(acknowledged, ensure_ascii=False)
            if board.status_word(record.get("status", "")) == "blocked":
                append_worklog(root, session_id, branch, "恢复原任务：" + note)
        elif args.cmd == "heartbeat":
            if args.files is not None:
                conflicts = check_conflicts(root, branch, files, sessions_in(roots), session_id)
                if conflicts:
                    raise ConflictError("\n".join(conflicts))
                updates["files"] = json.dumps(files, ensure_ascii=False)
            if args.note:
                updates["progress"] = one_line(args.note, "进度")
        else:
            summary = one_line(args.summary, "总结")
            tests = one_line(args.tests, "测试结果") if args.cmd == "finish" else ""
            if args.cmd == "handoff":
                result["answered_handoffs"] = close_handoffs(root, record, session_id, "已由本会话的新交接条替代，详见最新会话记录。")
                result["inbox"] = handoff_note(root, session_id, summary)
                updates["status"] = "blocked"
                updates["handoff"] = summary
                updates["handoff_inbox"] = result["inbox"]
            else:
                result["answered_handoffs"] = close_handoffs(root, record, session_id, "原会话已收尾：" + summary + "；测试记录：" + tests)
                updates["status"] = "done"
                updates["handoff"] = ""
                updates["handoff_inbox"] = ""
            append_worklog(root, session_id, branch, summary, tests)
        board._write_lf_atomic(path, put_fields(text, updates))
        result["status"] = updates.get("status", record.get("status"))
        return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="工作区路径，默认当前目录")
    commands = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "start", "heartbeat", "handoff", "resume", "finish"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true", help="输出结构化结果")
        if name != "check":
            sub.add_argument("--agent", required=True, help="当前平台，必须与会话所有者一致")
        if name in ("heartbeat", "finish", "handoff", "resume"):
            sub.add_argument("session", help="自己的会话 ID")
        if name == "check":
            sub.add_argument("--session", default=None, help="只读检查时排除自己的会话")
            sub.add_argument("--agent", default=None, help="过滤发给当前平台的人类指令")
        if name in ("check", "start", "heartbeat", "resume"):
            sub.add_argument("--files", nargs="+", help="准备修改的相对文件或目录（目录以 / 结尾）")
        if name == "start":
            sub.add_argument("--task", required=True)
        if name in ("start", "resume"):
            sub.add_argument("--ack-inbox", nargs="+", help="已实际读取并纳入本任务的人类指令文件名；不将其标记完成")
        if name in ("heartbeat", "resume"):
            sub.add_argument("--note", default=None, help="简短进度，不重写已有正文")
        if name in ("finish", "handoff"):
            sub.add_argument("--summary", required=True)
        if name == "finish":
            sub.add_argument("--tests", required=True, help="实际测试结果；未测也要如实说明")
    args = parser.parse_args()
    rc = 0
    try:
        result = execute(args)
        if not result["ok"]:
            rc = 1
    except (ConflictError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        rc = 1 if isinstance(exc, ConflictError) else 2
        result = {"ok": False, "error": str(exc)}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result["ok"] else "[FAIL] ") + json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
