#!/usr/bin/env python3
"""管理本机会话与文件占用：check / start / activate / run / heartbeat / handoff / resume / finish。"""

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
if __name__ == "__main__":
    # Lazy activity/run imports must use the same CLI owner checks and
    # ConflictError class, rather than loading a second session module.
    sys.modules.setdefault("agents_session", sys.modules[__name__])
import agents_status as board
from agents_layout import state_path, state_relative
from agents_policy import effective_policy, check_task_branch, validate_policy
from agents_notifications import collect_notifications, format_notification, human_requests, human_controls, notification_identity


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
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法安全核对路径 {relative}：{exc}") from None
    return board._checked_state_path(root, path)


def one_line(value: str, label: str) -> str:
    value = value.strip()
    # parse_kv uses splitlines(), which also recognizes Unicode and control
    # separators. Accepting those here could inject metadata before status/files.
    if not value or len(value.splitlines()) != 1 or "\x00" in value or len(value) > 1000:
        raise ValueError(f"{label} 必须是 1–1000 字符的单行文本")
    return value


def normalize_files(root: Path, raw: list[str]) -> list[str]:
    files = []
    for value in raw:
        value = one_line(value, "文件范围").replace("\\", "/")
        if value.startswith("/") or ":" in value or ".." in value.split("/") or any(c in value for c in "*?"):
            raise ValueError(f"请使用项目相对文件或目录路径，不使用通配符：{value}")
        path = safe_path(root, value)
        if path.resolve() != path:
            raise ValueError(f"文件范围含符号链接、junction 或路径别名：{value}；请声明真实项目路径")
        relative = path.relative_to(root).as_posix().rstrip("/")
        if relative in ("", ".") or relative.startswith(".git/") or relative == ".git":
            raise ValueError("请声明具体项目文件或目录，不能占用整个工作区或 .git")
        if path.is_dir() or value.endswith("/"):
            relative += "/"
        if relative not in files:
            files.append(relative)
    return files


def worktree_roots(root: Path) -> list[Path]:
    raw = git(root, "worktree", "list", "--porcelain", "-z", raw=True)
    roots = [Path(row["path"]).resolve() for row in board.parse_worktree_porcelain(raw)]
    if not roots:
        raise ValueError("无法确定共享仓库的工作区，停止写入")
    return roots


def refresh_worktree_roots(root: Path, primary: Path) -> list[Path]:
    """Reload under the shared lock; an owner may register while we wait for it."""
    roots = worktree_roots(root)
    if roots[0] != primary or root not in roots:
        raise ConflictError("等待期间仓库主工作区发生变化，请重新检查；未更新会话")
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
            if safe_path(primary, state_relative(primary, ".agents/coordination.lock")) != lock_path:
                raise ConflictError("等待期间协作数据目录发生变化，请重新检查；未使用旧锁继续写入")
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def valid_standby(record: dict) -> bool:
    """Only an explicit, valid empty claim releases a waiting conversation."""
    if board.session_status(record.get("status", "")) != "standby" or record.get("_read_error"):
        return False
    try:
        claimed = json.loads(record.get("files", ""))
    except (ValueError, TypeError):
        return False
    from agents_jobs import has_job_markers
    return isinstance(claimed, list) and not claimed and not has_job_markers(record)


def sessions_in(roots: list[Path]) -> list[dict]:
    sessions = board.load_sessions([(root, False) for root in roots], datetime.now(), 15)
    for session in sessions:
        if session.get("status_error"):
            raise ValueError(f"{session['path']}：会话状态无效；先核对原记录，保留占用")
        if session["closed"]:
            continue
        if board.status_word(session.get("status", "")) == "standby":
            record = board.parse_kv(Path(session["path"]))
            released = valid_standby(record)
            session.update(standby=released, occupies=not released,
                           claims_error=not released)
            if released:
                session["files"] = []
            continue
        try:
            session["files"] = normalize_files(Path(session["workspace"]).resolve(), session.get("files", []))
        except (ValueError, OSError, RuntimeError):
            session["claims_error"] = True
        # Legacy or partially written active records still own unknown files.
        # An empty claim must never mean that another worktree can safely write.
        if not session.get("files"):
            session["claims_error"] = True
    return sessions


def pending_human_inbox(roots: list[Path], agent: str | None, session_id: str | None = None) -> list[str]:
    return list(human_requests(roots, agent, session_id))


def acknowledge_for_call(pending: list[str], requested: list[str] | None,
                         roots: list[Path] | None = None) -> list[str]:
    requested = requested or []
    if roots:
        aliases = {}
        for identifier in pending:
            if Path(identifier).name == identifier:
                for root in roots:
                    candidate = state_path(root, "inbox") / identifier
                    if candidate.is_file():
                        aliases[os.path.normcase(str(candidate.resolve()))] = identifier
        requested = [aliases.get(os.path.normcase(str(Path(item).expanduser().resolve())), item)
                     if Path(item).is_absolute() else item for item in requested]
    unknown = set(requested) - set(pending)
    if unknown:
        raise ValueError("确认条目不是当前待处理的人类指令：" + ", ".join(sorted(unknown)))
    unseen = set(pending) - set(requested)
    if unseen:
        raise ConflictError("先读取并回复人类指令；仍在执行的事项可显式 --ack-inbox 确认已纳入任务：" + ", ".join(sorted(unseen)))
    return sorted(set(requested))


def acknowledged_for_range_change(record: dict, pending: dict[str, str]) -> list[str]:
    """A receipt applies to the same path and original request, never just its name."""
    try:
        acknowledged = json.loads(record.get("acknowledged_inbox", "[]"))
        versions = json.loads(record.get("acknowledged_inbox_versions", "{}"))
    except (ValueError, TypeError):
        raise ValueError("acknowledged_inbox 字段无效，请用 resume --ack-inbox 明确纳入当前指令") from None
    if not isinstance(acknowledged, list) or any(not isinstance(item, str) or not item for item in acknowledged):
        raise ValueError("acknowledged_inbox 字段必须是指令标识字符串数组，请用 resume 修复")
    if (not isinstance(versions, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for key, value in versions.items())):
        raise ValueError("acknowledged_inbox_versions 无效，请用 resume --ack-inbox 重新确认原指令")
    try:
        return acknowledge_for_call(list(pending), [item for item in acknowledged
                                                    if item in pending and versions.get(item) == pending[item]])
    except ConflictError as exc:
        raise ConflictError("范围变更前须纳入新的人类指令；原 active 会话也可用 resume --ack-inbox ... --files ... 原子确认：" + str(exc)) from None


def ensure_no_controls(roots: list[Path], agent: str | None, session_id: str | None = None) -> None:
    controls = human_controls(roots, agent, session_id)
    if controls:
        raise ConflictError("先处理暂停/取消/避让指令；阅读或 --ack-inbox 不能解除控制：" +
                            ", ".join(f"{identifier} ({action})" for identifier, action in controls.items()))


def verify_requests_unchanged(roots: list[Path], agent: str | None, session_id: str | None,
                              expected: dict[str, str]) -> None:
    if human_requests(roots, agent, session_id) != expected:
        raise ConflictError("核对期间人类指令发生变化，请重新读取；未使用旧确认继续写入")


def check_conflicts(root: Path, branch: str, files: list[str], sessions: list[dict],
                    own_id: str | None = None) -> list[str]:
    conflicts = []
    for session in sessions:
        same_workspace = Path(session["workspace"]).resolve() == root
        if session["closed"] or (session["id"] == own_id and same_workspace):
            continue
        if session["id"] == own_id:
            conflicts.append(f"{session['id']} 在其它工作区有同名会话，先核对全部记录：{session['workspace']}")
            continue
        if session.get("claims_error"):
            conflicts.append(f"{session['id']} 的 files 字段缺失、为空或无效，先核对占用范围")
            continue
        if session.get("standby"):
            if session.get("duplicate_id"):
                conflicts.append(f"{session['id']} 在多个工作区有同名待机会话，先核对全部记录")
            continue
        overlaps = board.overlapping_files(files, session.get("files", []))
        if same_workspace or session["branch"] == branch or overlaps:
            reason = "同一工作区已有未结束会话" if same_workspace else (
                "分支已被占用" if session["branch"] == branch else "文件范围重叠：" + ", ".join(overlaps))
            stale = "；心跳失活仍保留占用，按接管流程处理" if session["stale"] else ""
            conflicts.append(f"{session['id']}：{reason}{stale}")
    return conflicts


def own_session(root: Path, session_id: str, agent: str | None, branch: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", session_id):
        raise ValueError("无效的会话 ID")
    path = safe_path(root, state_relative(root, f".agents/sessions/{session_id}.md"))
    if not path.is_file():
        raise ValueError("当前工作区不存在该会话，不能排除其它工作区的占用")
    record = board.parse_kv(path)
    state = board.validate_session_status(record)
    if agent is not None and record.get("agent") != agent:
        raise ValueError("平台与会话所有者不一致，不能更新或排除其它 agent 的会话")
    standby = state == "standby"
    if standby and not valid_standby(record):
        raise ValueError("待机会话必须有合法的空 files 数组；先核对原记录，不能用无效状态释放占用")
    if not standby and record.get("branch") != branch:
        raise ConflictError("当前分支与会话登记分支不一致，先排查工作区变化")
    return path, record


def put_fields(text: str, fields: dict[str, str]) -> str:
    # Keep the same boundary as parse_kv: only the initial metadata block is
    # writable. Body notes may legitimately contain examples of these fields.
    end, started = 0, False
    metadata_pattern = r"^[ \t]*[-*][ \t]*\*{0,2}[A-Za-z_][\w -]*?\*{0,2}[ \t]*[:：]"
    for line in text.splitlines(keepends=True):
        if re.match(metadata_pattern, line):
            started = True
        elif line.strip() and (started or not line.lstrip("\ufeff \t").startswith(("# ", "<!--"))):
            break
        end += len(line)
    header, body = text[:end], text[end:]
    for key, value in fields.items():
        pattern = rf"(?mi)^[ \t]*[-*][ \t]*\*{{0,2}}{re.escape(key)}[ \t]*\*{{0,2}}[ \t]*[:：][^\n]*$"
        replacement = f"- {key}: {value}"
        if re.search(pattern, header):
            # Equal duplicates are accepted legacy metadata. Update every copy
            # together so this write cannot introduce a critical conflict.
            header = re.sub(pattern, lambda _match: replacement, header)
        else:
            metadata = list(re.finditer("(?m)" + metadata_pattern + r"[^\n]*$", header))
            if metadata:
                insertion = metadata[-1].end()
                header = header[:insertion] + "\n" + replacement + header[insertion:]
            else:
                header = header.rstrip("\r\n") + "\n\n" + replacement + "\n\n"
    return header + body


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
    board.validate_session_status(record)
    if any(not record.get(key, "").strip() for key in ("agent", "branch", "status")):
        raise ValueError("原会话缺少所有者、分支或状态，先人工核对")
    index_lock = Path(git(root, "rev-parse", "--git-path", "index.lock"))
    if not index_lock.is_absolute():
        index_lock = root / index_lock
    if index_lock.exists():
        raise ConflictError("原工作区有 Git index.lock，先确认 Git 进程已经停止")
    claimed, invalid = board.parse_claimed_files(record.get("files", ""))
    from agents_jobs import jobs_for_record
    jobs_for_record(record)
    if valid_standby(record):
        claimed, invalid = [], False
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
            "running_jobs": record.get("running_jobs", "{}"),
            "record_sha256": digest(path.read_bytes()), "mtime_ns": str(stat.st_mtime_ns),
            "mtime": stat.st_mtime, "last_file_activity": last_file_activity,
            "git_status": git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", raw=True),
            "index_sha256": digest(git(root, "ls-files", "--stage", "-z", raw=True)),
            "working_files": fingerprints}


def takeover_blockers(snapshot: dict) -> list[str]:
    blockers = []
    from agents_jobs import ensure_no_running_jobs
    try:
        ensure_no_running_jobs({"running_jobs": snapshot.get("running_jobs", "{}")})
    except ConflictError as exc:
        blockers.append(str(exc))
    if board.status_word(snapshot["status"]) in board.CLOSED_WORDS:
        blockers.append("原会话已经结束，无需收回")
    if board.status_word(snapshot["status"]) == "standby" and not snapshot["files"]:
        blockers.append("原会话待机且无文件占用，无需收回")
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
        roots = refresh_worktree_roots(root, primary)
        actor_session = notification_identity(root, actor, getattr(args, "actor_session", None))["session"]
        if effective_policy(root, primary) != policy:
            raise ConflictError("等待期间项目策略发生变化，请重新检查")
        if args.cmd == "takeover-plan":
            snapshot = takeover_snapshot(roots, args.session)
            pending = set(pending_human_inbox(roots, actor, actor_session))
            pending.update(pending_human_inbox(roots, snapshot["owner"], args.session))
            ensure_no_controls(roots, actor, actor_session)
            ensure_no_controls(roots, snapshot["owner"], args.session)
            acknowledge_for_call(sorted(pending), getattr(args, "ack_inbox", None), roots)
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
            pending = set(pending_human_inbox(roots, actor, actor_session))
            pending.update(pending_human_inbox(roots, plan["snapshot"]["owner"], target_id))
            ensure_no_controls(roots, actor, actor_session)
            ensure_no_controls(roots, plan["snapshot"]["owner"], target_id)
            acknowledge_for_call(sorted(pending), getattr(args, "ack_inbox", None), roots)
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
    args._notification_root = root
    if not safe_path(root, state_relative(root, ".agents/sessions")).is_dir():
        raise ValueError("项目尚未安装会话目录，请先运行安装器")
    roots = worktree_roots(root)
    args._notification_roots = roots
    branch = git(root, "branch", "--show-current")
    policy = effective_policy(root, roots[0])
    if args.cmd.startswith("takeover-"):
        return execute_takeover(args, root, roots, policy)
    files = normalize_files(root, args.files or []) if hasattr(args, "files") else []
    if args.cmd == "check":
        actor = one_line(args.agent, "平台名").lower() if args.agent is not None else None
        if actor is not None and not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", actor):
            raise ValueError("平台名使用小写英文、数字或连字符")
        if args.session is not None:
            own_session(root, args.session, actor, branch)
        conflicts = check_conflicts(root, branch, files, sessions_in(roots), args.session)
        pending = pending_human_inbox(roots, actor, args.session)
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
    if args.cmd in ("start", "resume", "activate") or (args.cmd == "heartbeat" and args.files is not None):
        check_task_branch(branch, policy)
    elif not branch:
        raise ValueError("当前没有有效分支，先排查工作区状态")
    with coordination_lock(roots[0]):
        roots = refresh_worktree_roots(root, roots[0])
        args._notification_roots = roots
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
            requests = human_requests(roots, agent)
            ensure_no_controls(roots, agent)
            acknowledged = acknowledge_for_call(list(requests), getattr(args, "ack_inbox", None), roots)
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
                    f"- acknowledged_inbox_versions: {json.dumps({item: requests[item] for item in acknowledged}, ensure_ascii=False)}\n"
                    f"- started: {now}\n- heartbeat: {now}\n- pr: \n\n"
                    "## 当前进度\n\n- 已登记任务与文件范围。\n\n## 下一步\n\n- 按项目测试门禁完成任务。\n\n"
                    "## 阻塞点 / handoff\n\n- 无\n")
            verify_requests_unchanged(roots, agent, None, requests)
            board._write_lf_atomic(path, text)
            return {"ok": True, "id": session_id, "branch": branch, "files": files, "file": str(path)}
        session_id = args.session
        path, record = own_session(root, session_id, agent, branch)
        from agents_activity import read_session_record, replace_session_record, update_session_fields
        record_snapshot = read_session_record(root, session_id)
        if record_snapshot[0] != path or record_snapshot[4] != record:
            raise ConflictError("会话在核对期间发生变化，未更新记录")
        text = record_snapshot[3]
        if board.status_word(record.get("status", "")) in board.CLOSED_WORDS:
            raise ValueError("会话已经结束，请创建新会话，不要重新激活历史记录")
        standby = valid_standby(record)
        if args.cmd == "activate" and not standby:
            raise ValueError("activate 只用于待机会话接收新任务；未完成任务请用 resume")
        if standby and args.cmd in ("resume", "handoff"):
            raise ValueError("待机会话没有未完成任务；收到新任务后用 activate --task --files 重新登记")
        if standby and args.cmd == "heartbeat" and args.files is not None:
            raise ValueError("heartbeat 不能给待机会话添加占用；新任务请用 activate --task --files")
        if standby and args.cmd == "finish" and getattr(args, "standby", False):
            raise ValueError("会话已经待机，不能重复完成原任务；结束对话使用不带 --standby 的 finish")
        completed_claims = None
        if args.cmd == "finish" and getattr(args, "standby", False):
            completed_claims, invalid = board.parse_claimed_files(record.get("files", ""))
            if invalid:
                raise ValueError("完成后待机前须核对合法的原文件范围，不能用待机隐藏无效占用")
        updates = {"heartbeat": now}
        if args.cmd in ("finish", "resume", "activate") or (args.cmd == "heartbeat" and args.files is not None):
            from agents_jobs import ensure_no_running_jobs, clear_dead_jobs
            ensure_no_running_jobs(record)
            if "running_jobs" in record:
                updates["running_jobs"] = json.dumps(clear_dead_jobs(record), ensure_ascii=False, separators=(",", ":"))
        result = {"ok": True, "id": session_id, "branch": branch}
        verified_requests = None
        if args.cmd in ("resume", "activate"):
            requests = human_requests(roots, agent, session_id)
            ensure_no_controls(roots, agent, session_id)
            acknowledged = acknowledge_for_call(list(requests), getattr(args, "ack_inbox", None), roots)
            claimed, invalid = board.parse_claimed_files(record.get("files", ""))
            if args.cmd == "activate":
                task = one_line(args.task, "任务")
                if args.files is None or not files:
                    raise ValueError("新任务需要具体文件范围，请使用 --files")
            elif args.files is None:
                if invalid:
                    raise ValueError("原文件范围无效，请用 --files 明确声明恢复范围")
                files = normalize_files(root, claimed)
            if not files:
                raise ValueError("恢复写任务需要具体文件范围，请使用 --files")
            conflicts = check_conflicts(root, branch, files, sessions_in(roots), session_id)
            if conflicts:
                raise ConflictError("\n".join(conflicts))
            verify_requests_unchanged(roots, agent, session_id, requests)
            note = one_line(getattr(args, "note", None) or "继续原任务", "恢复说明") if args.cmd == "resume" else task
            result["answered_handoffs"] = close_handoffs(root, record, session_id, "原会话恢复工作：" + note)
            updates.update(status="active", files=json.dumps(files, ensure_ascii=False), progress=note, handoff="", handoff_inbox="")
            if args.cmd == "activate":
                updates.update(task=task, branch=branch, task_started_at=datetime.now().isoformat(timespec="seconds"),
                               standby_since="")
                result.update(task=task, files=files)
            updates["acknowledged_inbox"] = json.dumps(acknowledged, ensure_ascii=False)
            updates["acknowledged_inbox_versions"] = json.dumps(
                {item: requests[item] for item in acknowledged}, ensure_ascii=False)
            verified_requests = requests
            if args.cmd == "activate":
                append_worklog(root, session_id, branch, "开始新任务：" + note)
            elif board.status_word(record.get("status", "")) == "blocked":
                append_worklog(root, session_id, branch, "恢复原任务：" + note)
        elif args.cmd == "heartbeat":
            if args.files is not None:
                if not files:
                    raise ValueError("写任务必须保留具体文件范围，不能用空列表释放占用")
                claimed, invalid = board.parse_claimed_files(record.get("files", ""))
                if invalid:
                    raise ValueError("原文件范围无效，请用 resume --files 明确修复后再变更范围")
                original_files = normalize_files(root, claimed)
                changed = set(files) != set(original_files)
                if changed:
                    if board.status_word(record.get("status", "")) == "blocked":
                        raise ConflictError("会话仍是 blocked；先处理指令并用 resume 恢复，再变更文件范围")
                    requests = human_requests(roots, agent, session_id)
                    ensure_no_controls(roots, agent, session_id)
                    if requests:
                        acknowledged_for_range_change(record, requests)
                    verified_requests = requests
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
                # Invalid associations are data errors, even when another human task is pending.
                # This preparation is read-only; close_handoffs validates again before writing.
                handoff_updates(root, record, session_id, "")
                requests = human_requests(roots, agent, session_id)
                ensure_no_controls(roots, agent, session_id)
                if requests:
                    raise ConflictError("仍有未处理的人类指令；先在原 inbox 回复结果并更新状态，再 finish：" +
                                        ", ".join(requests))
                verify_requests_unchanged(roots, agent, session_id, requests)
                result["answered_handoffs"] = close_handoffs(root, record, session_id, "原会话已收尾：" + summary + "；测试记录：" + tests)
                if getattr(args, "standby", False):
                    completed_at = datetime.now().isoformat(timespec="seconds")
                    updates.update(status="standby", files="[]", task="", progress="任务已完成，待机等待新指令",
                                   last_task=record.get("task", ""), last_task_files=json.dumps(completed_claims, ensure_ascii=False),
                                   last_task_branch=record.get("branch", branch), last_task_summary=summary,
                                   last_task_tests=tests, task_completed_at=completed_at, standby_since=completed_at)
                    result.update(files=[], task_completed=True)
                else:
                    updates["status"] = "done"
                    if standby:
                        updates.update(branch=branch, progress="待机结束：" + summary,
                                       closed_at=datetime.now().isoformat(timespec="seconds"))
                updates["handoff"] = ""
                updates["handoff_inbox"] = ""
            log_summary = "结束待机：" + summary if standby else summary
            if args.cmd == "finish" and getattr(args, "standby", False):
                log_summary = ("完成任务：" + record.get("task", "") + "；文件范围："
                               + json.dumps(completed_claims, ensure_ascii=False) + "；" + summary)
            append_worklog(root, session_id, branch, log_summary, "" if standby else tests)
        if verified_requests is not None:
            verify_requests_unchanged(roots, agent, session_id, verified_requests)
        replace_session_record(root, session_id, record_snapshot, update_session_fields(text, updates))
        result["status"] = updates.get("status", record.get("status"))
        return result


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="工作区路径，默认当前目录")
    commands = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "start", "activate", "heartbeat", "handoff", "resume", "finish"):
        details = ({"help": "继续原 active/blocked 会话，可原子纳入新指令与完整文件范围",
                    "description": "继续自己的原会话。blocked 用此恢复；active 收到新的合法人类任务时，也可用 --ack-inbox 和 --files 原子重新确认。确认已读不能代替真实授权或绕过暂停。"}
                   if name == "resume" else
                   {"help": "待机会话接收新任务，重新登记完整范围",
                    "description": "只将自己的合法 standby 会话用于新的任务。重新核对当前分支、项目策略、文件占用和人类指令；不会复活已结束任务。"}
                   if name == "activate" else {})
        sub = commands.add_parser(name, **details)
        sub.add_argument("--json", action="store_true", help="输出结构化结果")
        sub.add_argument("--no-activity", action="store_true", help="不附带更新活动时间，仅执行所选命令")
        if name != "check":
            sub.add_argument("--agent", required=True, help="当前平台，必须与会话所有者一致")
        if name in ("heartbeat", "finish", "handoff", "resume", "activate"):
            sub.add_argument("session", help="自己的会话 ID")
        if name == "check":
            sub.add_argument("--session", default=None, help="只读检查时排除自己的会话")
            sub.add_argument("--agent", default=None, help="过滤发给当前平台的人类指令")
        if name in ("check", "start", "heartbeat", "resume", "activate"):
            sub.add_argument("--files", nargs="+", required=name == "activate",
                             help="准备修改的相对文件或目录（目录以 / 结尾）")
        if name in ("start", "activate"):
            sub.add_argument("--task", required=True)
        if name in ("start", "resume", "activate"):
            sub.add_argument("--ack-inbox", nargs="+", help="已实际读取并纳入本任务的人类指令文件名；不将其标记完成")
        if name in ("heartbeat", "resume"):
            sub.add_argument("--note", default=None, help="简短进度，不重写已有正文")
        if name in ("finish", "handoff"):
            sub.add_argument("--summary", required=True)
        if name == "finish":
            sub.add_argument("--tests", required=True, help="实际测试结果；未测也要如实说明")
            sub.add_argument("--standby", action="store_true", help="完成当前任务后待机，释放文件占用并保留会话身份")
    for name in ("takeover-inspect", "takeover-plan", "takeover-apply", "takeover-cancel"):
        sub = commands.add_parser(name, help="人工接管：先检查与声明，再确认执行；不操作 Git 分支")
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--no-activity", action="store_true", help="不附带更新活动时间")
        sub.add_argument("--actor-session", help="执行接管操作的自己的会话 ID；位置参数是被查看的目标")
        if name in ("takeover-inspect", "takeover-plan"):
            sub.add_argument("session", help="原会话 ID")
        else:
            sub.add_argument("plan", help="本机接管计划 ID")
        if name != "takeover-inspect":
            sub.add_argument("--agent", required=True)
        else:
            sub.add_argument("--agent", help="当前平台，配合 --actor-session 记录自己的活动")
        if name in ("takeover-plan", "takeover-cancel"):
            sub.add_argument("--reason", required=True)
        if name in ("takeover-plan", "takeover-apply"):
            sub.add_argument("--ack-inbox", nargs="+")
        if name == "takeover-apply":
            sub.add_argument("--confirm-session", required=True, help="再次输入原会话 ID")
            sub.add_argument("--approval-note", required=True, help="记录实际取得的用户确认；此参数本身不是授权")
    run_parser = commands.add_parser("run", help="执行已登记任务的命令，自动记录活动并保留原输出与退出码",
                                     usage="%(prog)s <会话ID> --agent <平台> -- <命令> [参数...]")
    run_parser.add_argument("session", help="自己的 active 会话 ID")
    run_parser.add_argument("--agent", required=True, help="当前平台，必须与会话所有者一致")
    run_parser.set_defaults(json=False, no_activity=True)
    arguments = list(sys.argv[1:] if argv is None else argv)
    separator = arguments.index("--") if "--" in arguments else None
    if separator is not None and "run" in arguments[:separator]:
        args = parser.parse_args(arguments[:separator])
        if args.cmd != "run":
            parser.error("命令参数分隔仅用于 run")
        args.command = arguments[separator + 1:]
    else:
        args = parser.parse_args(arguments)
    if args.cmd == "run" and (separator is None or not getattr(args, "command", None)):
        parser.error("run 需要 -- 后的实际命令及参数")
    rc = 0
    if args.cmd == "run":
        try:
            from agents_run import execute_tracked
            return execute_tracked(args)
        except (ConflictError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            print("[FAIL] " + str(exc), file=sys.stderr)
            return 1 if isinstance(exc, ConflictError) else 2
    try:
        result = execute(args)
        if not result["ok"]:
            rc = 1
    except (ConflictError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        rc = 1 if isinstance(exc, ConflictError) else 2
        result = {"ok": False, "error": str(exc)}
    notice = None
    activity_warning = None
    if hasattr(args, "_notification_root"):
        actor_session = (getattr(args, "actor_session", None) if args.cmd.startswith("takeover-")
                         else getattr(args, "session", None) or result.get("id"))
        if getattr(args, "agent", None) and actor_session and not args.no_activity:
            from agents_activity import safe_touch_activity
            activity_warning = safe_touch_activity(args._notification_root, args.agent, actor_session,
                                                   "agents_session." + args.cmd).get("warning")
        try:
            notice_session = actor_session
            notice = collect_notifications(args._notification_root, getattr(args, "agent", None),
                                           notice_session,
                                           getattr(args, "_notification_roots", None))
        except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            notice = {"error": str(exc)[:500]}
    if args.json:
        if notice:
            result["notifications"] = notice
        if activity_warning:
            result["activity_warning"] = activity_warning
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result["ok"] else "[FAIL] ") + json.dumps(result, ensure_ascii=False))
        if notice:
            print(format_notification(notice) if "error" not in notice
                  else "[提醒检查失败] " + notice["error"])
        if activity_warning:
            print("[活动记录失败] " + activity_warning, file=sys.stderr)
    return rc


if __name__ == "__main__":
    status = main()
    # SystemExit uses a signed C long on Windows; retain native DWORD statuses.
    if os.name == "nt" and isinstance(status, int) and 0x80000000 <= status <= 0xFFFFFFFF:
        status -= 0x100000000
    sys.exit(status)
