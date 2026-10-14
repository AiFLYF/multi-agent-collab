#!/usr/bin/env python3
"""管理本机会话与文件占用：check / start / heartbeat / handoff / resume / finish。"""

from __future__ import annotations

import argparse
import hashlib
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
from agents_layout import state_path, state_relative
from agents_policy import effective_policy, check_task_branch, validate_policy


class ConflictError(ValueError):
    pass


def git(root: Path, *args: str, raw: bool = False) -> str:
    executable = board.resolve_git()
    if not executable:
        raise ValueError("Git 不可用，无法核对工作区")
    result = subprocess.run([executable, *args], cwd=str(root), capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "Git 检查失败")
    output = result.stdout.decode("utf-8", errors="strict" if raw else "replace")
    return output if raw else output.strip()


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
    lock_path = safe_path(primary, state_relative(primary, ".agents/coordination.lock"))
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
    path = safe_path(root, state_relative(root, f".agents/worklog/{now:%Y-%m-%d}.md"))
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
    inbox = safe_path(root, state_relative(root, ".agents/inbox"))
    inbox.mkdir(parents=True, exist_ok=True)
    # 独立文件名避免与网页指令的递增序号竞争；from 使用 agent，绝不伪装成人类。
    path = inbox / f"Q-{now:%Y-%m-%d}-handoff-{uuid.uuid4().hex[:8]}.md"
    content = (f"# 交接：{session_id}\n\n- from: {session_id}\n- to: any\n- status: open\n"
               f"- created: {now:%Y-%m-%d %H:%M}\n\n## 问题\n\n{summary}\n\n"
               "原会话保持 blocked，文件占用保留；确认交接后再由原会话收尾释放。\n\n## 回复\n")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    return path.relative_to(root).as_posix()


def handoff_updates(root: Path, record: dict, session_id: str, reply: str) -> list[tuple[Path, str]]:
    """只准备本会话的交接回执，完成校验后由调用方写入。"""
    relative = record.get("handoff_inbox", "")
    if relative:
        candidate = safe_path(root, relative.replace("\\", "/"))
        if candidate.parent != state_path(root, "inbox") or "-handoff-" not in candidate.name:
            raise ValueError("关联交接路径无效，未修改会话")
        candidates = [candidate] if candidate.is_file() else []
    else:
        # 兼容 1.5.0：那时没有保存关联路径，仅匹配本会话生成的交接条。
        candidates = [p for p in safe_path(root, state_relative(root, ".agents/inbox")).glob("Q-*-handoff-*.md")
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
    return pending


def close_handoffs(root: Path, record: dict, session_id: str, reply: str) -> list[str]:
    """普通问题、他人问题及人类指令不自动作答。"""
    pending = handoff_updates(root, record, session_id, reply)
    for path, content in pending:
        board._write_lf_atomic(path, content)
    return [path.relative_to(root).as_posix() for path, _content in pending]


def digest(value) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


PLAN_ID = r"T-\d{8}-\d{4}-[a-f0-9]{8}"
SESSION_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}"
SHA256 = r"[a-f0-9]{64}"
TAKEOVER_LIMIT = 8 * 1024 * 1024


def takeover_path(root: Path, relative: str) -> Path:
    path = safe_path(root, relative)
    if path.is_symlink() or path.resolve() != path:
        raise ValueError(f"接管涉及链接路径 {relative}，先人工核对")
    return path


def takeover_snapshot(roots: list[Path], session_id: str) -> dict:
    if not re.fullmatch(SESSION_ID, session_id):
        raise ValueError("无效的会话 ID")
    locations = [takeover_path(root, state_relative(root, f".agents/sessions/{session_id}.md")) for root in roots]
    locations = [path for path in locations if path.is_file()]
    if len(locations) != 1:
        raise ValueError("会话不存在或多个工作区存在相同 ID，先人工核对")
    path = locations[0]
    root = path.parents[2]
    record = board.parse_kv(path)
    if any(not record.get(key, "").strip() for key in ("agent", "branch", "status")):
        raise ValueError("原会话缺少所有者、分支或状态，先人工核对")
    index_lock = Path(git(root, "rev-parse", "--git-path", "index.lock"))
    if not index_lock.is_absolute():
        index_lock = root / index_lock
    if index_lock.exists():
        raise ConflictError("原工作区有 Git index.lock，先确认 Git 进程已经停止")
    claimed, invalid = board.parse_claimed_files(record.get("files", ""))
    if invalid:
        raise ValueError("原会话文件范围无效，不能生成可执行接管计划")
    changed = git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "HEAD", "--", raw=True)
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", raw=True)
    names = sorted(set(filter(None, (changed + untracked).split("\0"))))
    if len(names) > 100:
        raise ValueError("改动文件超过 100 个，先人工核对，不自动收回")
    fingerprints, total, last_file_activity = {}, 0, 0.0
    for name in names:
        item = takeover_path(root, name)
        if not item.exists():
            fingerprints[name] = "deleted"
        elif not item.is_file():
            raise ValueError(f"改动含目录或子模块 {name}，先人工核对")
        else:
            before = item.stat()
            if total + before.st_size > TAKEOVER_LIMIT:
                raise ValueError("改动内容超过 8 MiB，先人工核对")
            with item.open("rb") as stream:
                content = stream.read(TAKEOVER_LIMIT - total + 1)
            after = item.stat()
            total += len(content)
            if total > TAKEOVER_LIMIT or before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
                raise ValueError("检查时文件继续增长，停止生成计划")
            fingerprints[name] = {"sha256": digest(content), "mtime_ns": str(after.st_mtime_ns)}
            last_file_activity = max(last_file_activity, after.st_mtime)
    stat = path.stat()
    return {"session_id": session_id, "workspace": str(root), "owner": record.get("agent"),
            "recorded_branch": record.get("branch"), "branch": git(root, "branch", "--show-current"),
            "head": git(root, "rev-parse", "HEAD"), "status": record.get("status"),
            "heartbeat": record.get("heartbeat", ""), "files": claimed,
            "record_sha256": digest(path.read_bytes()), "mtime_ns": str(stat.st_mtime_ns),
            "mtime": stat.st_mtime, "last_file_activity": last_file_activity,
            "git_status": git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", raw=True),
            "index_sha256": digest(git(root, "ls-files", "--stage", "-z", raw=True)),
            "working_files": fingerprints}


def takeover_blockers(snapshot: dict) -> list[str]:
    blockers = []
    if board.status_word(snapshot["status"]) in board.CLOSED_WORDS:
        blockers.append("原会话已经结束，无需收回")
    if snapshot["branch"] != snapshot["recorded_branch"]:
        blockers.append("原工作区分支已改变，先调查")
    heartbeat = board.valid_heartbeat(snapshot["heartbeat"], datetime.now())
    if heartbeat is None:
        blockers.append("原会话心跳无效，不能自动认定失联")
    last_activity = max(snapshot["mtime"], snapshot["last_file_activity"],
                        heartbeat.timestamp() if heartbeat else snapshot["mtime"])
    if time.time() - last_activity <= 15 * 60:
        blockers.append("心跳、会话或改动文件在 15 分钟内更新，不能认定原会话失联")
    return blockers


def declaration_name(primary: Path, plan_id: str) -> str:
    return state_relative(primary, ".agents/inbox/Q-" + plan_id[2:] + "-takeover.md")


def valid_timestamp(value) -> bool:
    return type(value) in (int, float) and 0 <= value <= 253402300799


def read_takeover_plan(primary: Path, plan_id: str) -> tuple[Path, dict]:
    if not re.fullmatch(PLAN_ID, plan_id):
        raise ValueError("无效的接管计划 ID")
    path = takeover_path(primary, state_relative(primary, f".agents/takeovers/{plan_id}.json"))
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("接管计划过大，先人工核对")
    plan = json.loads(data.decode("utf-8"))
    required = {"schema_version", "id", "status", "requester", "reason", "created_at", "snapshot",
                "policy", "declaration", "declaration_sha256"}
    if (not isinstance(plan, dict) or not required.issubset(plan)
            or type(plan["schema_version"]) is not int or plan["schema_version"] != 1
            or plan["id"] != plan_id or plan["status"] not in ("pending", "applying", "applied", "cancelled")
            or not isinstance(plan["requester"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", plan["requester"])
            or not isinstance(plan["reason"], str)
            or not valid_timestamp(plan["created_at"])
            or plan["created_at"] <= 0 or plan["declaration"] != declaration_name(primary, plan_id)
            or not isinstance(plan["declaration_sha256"], str) or not re.fullmatch(SHA256, plan["declaration_sha256"])):
        raise ValueError("接管计划结构无效，未修改原会话")
    one_line(plan["reason"], "接管理由")
    validate_policy(plan["policy"])
    snapshot = plan["snapshot"]
    strings = ("session_id", "workspace", "owner", "recorded_branch", "branch", "head", "status",
               "heartbeat", "record_sha256", "mtime_ns", "git_status", "index_sha256")
    if (not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), str) for k in strings)
            or not re.fullmatch(SESSION_ID, snapshot["session_id"])
            or not isinstance(snapshot.get("files"), list) or not all(isinstance(p, str) for p in snapshot["files"])
            or not isinstance(snapshot.get("working_files"), dict)
            or any(not valid_timestamp(snapshot.get(k)) for k in ("mtime", "last_file_activity"))):
        raise ValueError("接管快照结构无效，未修改原会话")
    return path, plan


def read_declaration(primary: Path, plan: dict) -> tuple[Path, str]:
    path = takeover_path(primary, plan["declaration"])
    notice = path.read_text(encoding="utf-8")
    fields = board.parse_kv(path)
    if (not notice.startswith(f"# 接管声明：{plan['snapshot']['session_id']}\n")
            or fields.get("from") != plan["requester"] + "-takeover"
            or fields.get("takeover_plan") != plan["id"] or fields.get("to") != "any"):
        raise ValueError("声明不属于此接管计划，拒绝修改")
    return path, notice


def execute_takeover(args, root: Path, roots: list[Path], policy) -> dict:
    if args.cmd == "takeover-inspect":
        snapshot = takeover_snapshot(roots, args.session)
        return {"ok": True, "snapshot": snapshot, "blockers": takeover_blockers(snapshot),
                "notice": "这里只读核对，不代表获准接管。"}
    actor = one_line(args.agent, "执行者")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", actor):
        raise ValueError("执行者使用小写平台名")
    primary = roots[0]
    with coordination_lock(primary):
        if effective_policy(root, primary) != policy:
            raise ConflictError("等待期间项目策略发生变化，请重新检查")
        if args.cmd == "takeover-plan":
            snapshot = takeover_snapshot(roots, args.session)
            pending = set(pending_human_inbox(roots, actor, args.session))
            pending.update(pending_human_inbox(roots, snapshot["owner"], args.session))
            acknowledge_for_call(sorted(pending), getattr(args, "ack_inbox", None))
            blockers = takeover_blockers(snapshot)
            if blockers:
                raise ConflictError("；".join(blockers))
            reason = one_line(args.reason, "接管理由")
            directory = takeover_path(primary, state_relative(primary, ".agents/takeovers"))
            for old_file in directory.glob("T-*.json"):
                _path, old = read_takeover_plan(primary, old_file.stem)
                if old["status"] in ("pending", "applying") and old["snapshot"]["session_id"] == args.session:
                    raise ConflictError(f"已有待处理计划 {old_file.stem}，先检查或取消，不重复发布声明")
            plan_id = "T-" + datetime.now().strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:8]
            relative = declaration_name(primary, plan_id)
            declaration = takeover_path(primary, relative)
            if declaration.exists() or (directory / (plan_id + ".json")).exists():
                raise ConflictError("计划编号碰撞，请重新生成；未覆盖已有文件")
            if takeover_snapshot(roots, args.session) != snapshot:
                raise ConflictError("检查期间原会话或文件正在变化，停止生成计划")
            body = (f"# 接管声明：{args.session}\n\n- from: {actor}-takeover\n- to: any\n- status: open\n"
                    f"- takeover_plan: {plan_id}\n"
                    f"- created: {datetime.now():%Y-%m-%d %H:%M}\n\n## 说明\n\n{reason}\n\n"
                    f"原会话：{args.session}；分支：{snapshot['branch']}；提交：{snapshot['head']}。\n"
                    "仅申请收回协作占用。至少等待 5 分钟、无人回复、用户明确确认且快照仍一致后才能执行。\n\n## 回复\n")
            board._write_lf_atomic(declaration, body)
            plan = {"schema_version": 1, "id": plan_id, "status": "pending", "requester": actor,
                    "reason": reason, "created_at": time.time(), "snapshot": snapshot, "policy": policy,
                    "declaration": relative, "declaration_sha256": digest(declaration.read_bytes())}
            board._write_lf_atomic(directory / (plan_id + ".json"), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
            return {"ok": True, "plan": plan_id, "snapshot": snapshot, "declaration": relative,
                    "earliest_apply": datetime.fromtimestamp(plan["created_at"] + 300).isoformat(timespec="seconds"),
                    "notice": "计划未执行。先核对进程、改动和声明回复，并取得用户对本计划的明确确认。"}
        plan_path, plan = read_takeover_plan(primary, args.plan)
        if plan["requester"] != actor:
            raise ValueError("计划结构或执行者不匹配")
        if plan["status"] != "pending":
            raise ValueError("计划已执行、执行中或已取消，不可重复使用；执行中断时先人工核对审计记录")
        declaration, notice = read_declaration(primary, plan)
        if args.cmd == "takeover-cancel":
            reason = one_line(args.reason, "取消原因")
            plan.update(status="cancelled", cancelled_reason=reason)
            board._write_lf_atomic(declaration, put_fields(notice, {"status": "cancelled"}) + "\n计划已取消：" + reason + "\n")
        else:
            approval = one_line(args.approval_note, "实际用户确认的记录")
            target_id = plan["snapshot"]["session_id"]
            if args.confirm_session != target_id:
                raise ValueError("再次输入的原会话 ID 与计划不一致")
            if time.time() < plan["created_at"] + 300:
                raise ConflictError("声明后尚未满 5 分钟，不能执行")
            pending = set(pending_human_inbox(roots, actor, target_id))
            pending.update(pending_human_inbox(roots, plan["snapshot"]["owner"], target_id))
            acknowledge_for_call(sorted(pending), getattr(args, "ack_inbox", None))
            if digest(declaration.read_bytes()) != plan["declaration_sha256"]:
                raise ConflictError("接管声明出现回复或被修改，先人工协调，不忽略异议")
            snapshot = takeover_snapshot(roots, target_id)
            if snapshot != plan["snapshot"] or effective_policy(root, primary) != plan["policy"]:
                raise ConflictError("原会话、Git 状态或项目策略已变化，旧计划失效")
            blockers = takeover_blockers(snapshot)
            if blockers:
                raise ConflictError("；".join(blockers))
            source = Path(snapshot["workspace"])
            path = takeover_path(source, state_relative(source, f".agents/sessions/{target_id}.md"))
            record = board.parse_kv(path)
            handoffs = handoff_updates(source, record, target_id, "按人工确认收回协作占用，计划 " + args.plan)
            for candidate, _text in handoffs:
                takeover_path(source, candidate.relative_to(source).as_posix())
            takeover_path(source, state_relative(source, f".agents/worklog/{datetime.now():%Y-%m-%d}.md"))
            updated = put_fields(path.read_text(encoding="utf-8"), {"status": "closed", "closed_by": actor,
                                 "closed_at": datetime.now().isoformat(timespec="seconds"),
                                 "closed_reason": "按人工确认收回占用，未声称原任务或测试完成", "takeover_plan": args.plan})
            if takeover_snapshot(roots, target_id) != snapshot or digest(declaration.read_bytes()) != plan["declaration_sha256"]:
                raise ConflictError("最终检查发现会话、Git 或声明变化，未收回占用")
            # 多文件更新无法整体原子提交；先落审计状态，中断后必须人工核对，不能盲目重复执行。
            plan.update(status="applying", approval_note=approval, applying_at=time.time())
            board._write_lf_atomic(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
            for candidate, content in handoffs:
                board._write_lf_atomic(candidate, content)
            board._write_lf_atomic(path, updated)
            notice = put_fields(notice, {"status": "answered"})
            board._write_lf_atomic(declaration, notice + "\n人工确认后已收回占用；原提交和工作文件均保留。\n")
            plan.update(status="applied", applied_at=time.time(), approval_note=approval)
            append_worklog(source, actor, snapshot["branch"], f"人工确认收回 {target_id} 的占用，计划 {args.plan}；保留提交 {snapshot['head']} 与全部工作文件。")
        board._write_lf_atomic(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
        return {"ok": True, "plan": args.plan, "status": plan["status"], "base_commit": plan["snapshot"]["head"],
                "notice": "没有切换、删除或重建 Git 分支。后续从原提交建立自己的任务分支，并保留未提交文件后重新登记。"}


def execute(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    root = Path(git(root, "rev-parse", "--show-toplevel")).resolve()
    if not safe_path(root, state_relative(root, ".agents/sessions")).is_dir():
        raise ValueError("项目尚未安装会话目录，请先运行安装器")
    roots = worktree_roots(root)
    branch = git(root, "branch", "--show-current")
    policy = effective_policy(root, roots[0])
    if args.cmd.startswith("takeover-"):
        return execute_takeover(args, root, roots, policy)
    files = normalize_files(root, args.files or []) if hasattr(args, "files") else []
    if args.cmd == "check":
        conflicts = check_conflicts(root, branch, files, sessions_in(roots), args.session)
        pending = pending_human_inbox(roots, args.agent, args.session)
        try:
            check_task_branch(branch, policy)
            branch_ok = True
        except ValueError as exc:
            branch_ok = False
            if files:
                conflicts.append(str(exc))
        return {"ok": not conflicts and not pending, "branch": branch, "files": files,
                "conflicts": conflicts, "human_inbox": pending, "policy": policy,
                "task_branch_allowed": branch_ok}
    agent = one_line(args.agent, "平台名").lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", agent):
        raise ValueError("平台名使用小写英文、数字或连字符")
    if args.cmd in ("start", "resume") or (args.cmd == "heartbeat" and args.files is not None):
        check_task_branch(branch, policy)
    elif not branch:
        raise ValueError("当前没有有效分支，先排查工作区状态")
    with coordination_lock(roots[0]):
        # 等待锁期间可能有人切分支，写入前再次核对。
        if git(root, "branch", "--show-current") != branch:
            raise ConflictError("等待期间工作区分支发生变化，停止写入")
        if effective_policy(root, roots[0]) != policy:
            raise ConflictError("等待期间项目策略发生变化，请重新检查")
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
                path = safe_path(root, state_relative(root, f".agents/sessions/{session_id}.md"))
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
        path = safe_path(root, state_relative(root, f".agents/sessions/{session_id}.md"))
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
    for name in ("takeover-inspect", "takeover-plan", "takeover-apply", "takeover-cancel"):
        sub = commands.add_parser(name, help="人工接管：先检查与声明，再确认执行；不操作 Git 分支")
        sub.add_argument("--json", action="store_true")
        if name in ("takeover-inspect", "takeover-plan"):
            sub.add_argument("session", help="原会话 ID")
        else:
            sub.add_argument("plan", help="本机接管计划 ID")
        if name != "takeover-inspect":
            sub.add_argument("--agent", required=True)
        if name in ("takeover-plan", "takeover-cancel"):
            sub.add_argument("--reason", required=True)
        if name in ("takeover-plan", "takeover-apply"):
            sub.add_argument("--ack-inbox", nargs="+")
        if name == "takeover-apply":
            sub.add_argument("--confirm-session", required=True, help="再次输入原会话 ID")
            sub.add_argument("--approval-note", required=True, help="记录实际取得的用户确认；此参数本身不是授权")
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
