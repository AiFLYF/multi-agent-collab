"""Keep registered command ownership until its wrapper and child are finished."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
MAX_JOBS = 16
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_AGENT = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z")
_JOB_FIELDS = {"owner_pid", "child_pid", "started"}
_GROUP_JOB_FIELDS = _JOB_FIELDS | {"group_id"}


def _modules():
    import agents_activity
    import agents_session
    return agents_activity, agents_session


def _valid_pid(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0x7FFFFFFF


def _windows_process_alive(pid):
    # os.kill(pid, 0) terminates processes on Windows. Query a native process
    # handle instead; inaccessible process state remains unknown, never dead.
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000 | 0x00001000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result == 0:
                return False
            if result == 258:
                return True
            return None
        finally:
            kernel.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None


def process_is_alive(pid):
    """Return True/False/None; None means ownership cannot safely be released."""
    if not _valid_pid(pid):
        raise ValueError("运行记录的 PID 无效")
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError as exc:
        return False if exc.errno == errno.ESRCH else None
    return True


def jobs_for_record(record):
    """Validate optional job metadata without treating fields as authentication."""
    if not isinstance(record, dict):
        raise ValueError("会话运行记录必须是字典")
    if record.get("_read_error"):
        raise ValueError("运行记录元数据不可读取")
    if "running_jobs" not in record:
        return {}
    raw = record["running_jobs"]
    if not isinstance(raw, str):
        raise ValueError("running_jobs 必须是 JSON 字典")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("运行记录 JSON 含重复字段，不能据此释放占用")
            result[key] = value
        return result
    try:
        jobs = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        raise ValueError("running_jobs JSON 损坏，不能据此释放占用") from None
    if not isinstance(jobs, dict) or len(jobs) > MAX_JOBS:
        raise ValueError("running_jobs 必须是最多 16 项的字典")
    result = {}
    for token, value in jobs.items():
        if (not isinstance(token, str) or not _TOKEN.fullmatch(token) or not isinstance(value, dict)
                or set(value) not in (_JOB_FIELDS, _GROUP_JOB_FIELDS) or not _valid_pid(value.get("owner_pid"))
                or (value.get("child_pid") is not None and not _valid_pid(value.get("child_pid")))):
            raise ValueError("运行记录含未知或损坏字段，不能据此释放占用")
        if "group_id" in value and (not _valid_pid(value["group_id"]) or value["group_id"] != value["child_pid"]):
            raise ValueError("运行记录的隔离进程组无效，不能据此释放占用")
        started = value["started"]
        if not isinstance(started, str) or not 1 <= len(started) <= 40:
            raise ValueError("运行记录 started 时间无效")
        try:
            datetime.fromisoformat(started)
        except ValueError:
            raise ValueError("运行记录 started 时间无效") from None
        result[token] = dict(value)
    return result


def has_job_markers(record):
    """Malformed and nonempty markers must never make standby unclaimed."""
    try:
        return bool(jobs_for_record(record))
    except (ValueError, TypeError, AttributeError):
        return True


def _group_state(value):
    if "group_id" not in value:
        return False
    if os.name == "nt":
        # A record created on POSIX cannot be declared dead by a Windows probe.
        return None
    try:
        from agents_processes import group_is_alive
        state = group_is_alive(value["group_id"])
        return state if state is True or state is False else None
    except (ImportError, ValueError, OSError, RuntimeError):
        return None


def _job_state(value):
    states = [process_is_alive(value["owner_pid"])]
    if value["child_pid"] is not None:
        states.append(process_is_alive(value["child_pid"]))
    if any(state is True for state in states):
        return True
    states.append(_group_state(value))
    if any(state is True for state in states):
        return True
    return None if any(state is None for state in states) else False


def ensure_no_running_jobs(record):
    """A completed task cannot release files while command state is uncertain."""
    _activity, sessions = _modules()
    for value in jobs_for_record(record).values():
        if _job_state(value) is not False:
            raise sessions.ConflictError("仍有运行中命令或进程状态无法确认；先等待并核对命令结束，未释放文件占用")


def clear_dead_jobs(record):
    """Return retained live/unknown markers for an owner's locked record update."""
    return {token: value for token, value in jobs_for_record(record).items() if _job_state(value) is not False}


def _check_identity(root, record, agent):
    if not record or record.get("_read_error") or record.get("agent") != agent:
        raise ValueError("运行命令的平台与会话所有者不一致")
    if record.get("workspace") and Path(record["workspace"]).expanduser().resolve() != root:
        raise ValueError("运行命令与会话登记工作区不一致")


def _unique_record(roots, session_id, expected):
    _activity, sessions = _modules()
    paths = []
    for workspace in roots:
        path = sessions.board._checked_state_path(workspace, sessions.state_path(workspace, "sessions") / (session_id + ".md"))
        if path.is_file():
            paths.append(path)
    if paths != [expected]:
        raise ValueError("多个工作区存在相同会话 ID，不能更新运行命令")


def _change_job(root, agent, session_id, job_id, action, *, branch=None, child_pid=None, group_id=None):
    if (not isinstance(agent, str) or not _AGENT.fullmatch(agent)
            or not isinstance(session_id, str) or not _SESSION.fullmatch(session_id)):
        raise ValueError("运行命令需要明确的有效平台和自己的会话 ID")
    if not isinstance(job_id, str) or not _TOKEN.fullmatch(job_id):
        raise ValueError("运行命令 token 必须是 32 位小写随机 hex")
    if action == "attach" and (not _valid_pid(child_pid) or child_pid == os.getpid()):
        raise ValueError("child_pid 必须是实际子进程的有效 PID")
    if group_id is not None and (action != "attach" or os.name == "nt" or
                                 not _valid_pid(group_id) or group_id != child_pid):
        raise ValueError("group_id 仅用于本次 POSIX 隔离监督器的实际 PID")
    root = Path(root).expanduser().resolve()
    activity, sessions = _modules()
    # Locate the record before locking, but read its first stable snapshot
    # inside the lock: another legitimate command may atomically replace it.
    path = sessions.board._checked_state_path(root, sessions.state_path(root, "sessions") / (session_id + ".md"))
    roots = sessions.worktree_roots(root)
    if root not in roots:
        raise ValueError("运行命令工作区与 Git 清单不一致")
    _unique_record(roots, session_id, path)
    primary = roots[0]
    lock = sessions.board._checked_state_path(primary, sessions.state_path(primary, "coordination.lock"))
    if lock.exists() and not stat.S_ISREG(lock.lstat().st_mode):
        raise ValueError("运行命令协调锁不是普通文件")
    with sessions.coordination_lock(primary):
        sessions.board._checked_state_path(primary, lock)
        roots = sessions.refresh_worktree_roots(root, primary)
        _unique_record(roots, session_id, path)
        snapshot = activity.read_session_record(root, session_id)
        if snapshot[0] != path:
            raise ValueError("等待时协作目录发生变化，未更新运行命令")
        record = snapshot[4]
        _check_identity(root, record, agent)
        jobs = jobs_for_record(record)
        requests = None
        if action in ("register", "attach"):
            actual_branch = sessions.git(root, "branch", "--show-current")
            if branch is not None and branch != actual_branch:
                raise sessions.ConflictError("当前分支已变化，未登记运行命令")
            if activity._validate(root, record, agent, actual_branch) != "active":
                raise sessions.ConflictError("运行命令登记只用于实际 active 任务")
            policy = sessions.effective_policy(root, primary)
            sessions.check_task_branch(actual_branch, policy)
            conflicts = sessions.check_conflicts(root, actual_branch,
                                                 sessions.normalize_files(root, sessions.board.parse_claimed_files(record.get("files", ""))[0]),
                                                 sessions.sessions_in(roots), session_id)
            if conflicts:
                raise sessions.ConflictError("；".join(conflicts))
            sessions.ensure_no_controls(roots, agent, session_id)
            if action == "register":
                requests = sessions.human_requests(roots, agent, session_id)
                sessions.acknowledged_for_range_change(record, requests)
        if action == "register":
            if job_id in jobs:
                raise ValueError("运行命令 token 已登记，不能覆盖或复用")
            jobs = clear_dead_jobs(record)
            if len(jobs) >= MAX_JOBS:
                raise sessions.ConflictError("此会话已有 16 条未结束命令；先等待结束")
            jobs[job_id] = {"owner_pid": os.getpid(), "child_pid": None, "started": datetime.now().isoformat(timespec="seconds")}
        else:
            value = jobs.get(job_id)
            if value is None:
                if action == "unregister":
                    return False
                raise ValueError("运行命令 token 尚未登记，不能附加子进程")
            if value["owner_pid"] != os.getpid():
                raise ValueError("该 token 不属于当前 wrapper 进程，不能更新其它命令")
            if action == "attach":
                if value["child_pid"] not in (None, child_pid):
                    raise ValueError("运行命令已附加其它子进程，不能覆盖 PID")
                if value["child_pid"] == child_pid and (group_id is None or value.get("group_id") == group_id):
                    return False
                value["child_pid"] = child_pid
                if group_id is not None:
                    value["group_id"] = group_id
            else:
                if value["child_pid"] is not None and process_is_alive(value["child_pid"]) is not False:
                    raise sessions.ConflictError("子进程仍在运行或状态无法确认；先结束子进程，未清除运行标志")
                if _group_state(value) is not False:
                    raise sessions.ConflictError("隔离组仍有后台命令或状态无法确认；未清除运行标志")
                del jobs[job_id]
        if requests is not None:
            sessions.verify_requests_unchanged(roots, agent, session_id, requests)
        text = activity.update_session_fields(snapshot[3], {"running_jobs": json.dumps(jobs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))})
        activity.replace_session_record(root, session_id, snapshot, text)
        return job_id if action == "register" else True


def register_job(root, agent, session_id, job_id, *, branch=None):
    return _change_job(root, agent, session_id, job_id, "register", branch=branch)


def attach_child(root, agent, session_id, job_id, child_pid, *, group_id=None):
    return _change_job(root, agent, session_id, job_id, "attach", child_pid=child_pid, group_id=group_id)


def unregister_job(root, agent, session_id, job_id):
    return _change_job(root, agent, session_id, job_id, "unregister")
