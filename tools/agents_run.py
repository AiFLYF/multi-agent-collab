"""Supervise an explicitly owned command without guessing task completion."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.dont_write_bytecode = True
ACTIVITY_INTERVAL = 60
WAIT_SLICE = 1


def _session_tools():
    import agents_session
    return agents_session


def _validate_owner(root, agent, session_id, *, check_claims=False, enforce_ack=True):
    sessions = _session_tools()
    branch = sessions.git(root, "branch", "--show-current")
    roots = sessions.worktree_roots(root)
    with sessions.coordination_lock(roots[0]):
        roots = sessions.refresh_worktree_roots(root, roots[0])
        if sessions.git(root, "branch", "--show-current") != branch:
            raise sessions.ConflictError("工作区分支发生变化，未继续执行命令")
        path, record = sessions.own_session(root, session_id, agent, branch)
        state = sessions.board.status_word(record.get("status", ""))
        if state in sessions.board.CLOSED_WORDS:
            raise ValueError("已结束会话不能执行命令或复活；新任务须另行登记")
        if state != "active":
            raise sessions.ConflictError("run 只执行已登记的 active 任务；待机先 activate，受阻先 resume")
        claimed, invalid = sessions.board.parse_claimed_files(record.get("files", ""))
        files = sessions.normalize_files(root, claimed)
        if invalid or not files:
            raise ValueError("执行命令前需要有效的文件范围")
        policy = sessions.effective_policy(root, roots[0])
        sessions.check_task_branch(branch, policy)
        requests = sessions.human_requests(roots, agent, session_id) if enforce_ack else None
        sessions.ensure_no_controls(roots, agent, session_id)
        if requests is not None:
            sessions.acknowledged_for_range_change(record, requests)
        if check_claims:
            conflicts = sessions.check_conflicts(root, branch, files, sessions.sessions_in(roots), session_id)
            if conflicts:
                raise sessions.ConflictError("；".join(conflicts))
        if requests is not None:
            sessions.verify_requests_unchanged(roots, agent, session_id, requests)
        return branch, roots


def _notice(root, agent, session_id, roots, previous=None):
    from agents_notifications import notification_snapshot, format_notification
    try:
        notice, cursor = notification_snapshot(root, agent, session_id, roots)
        if notice and cursor != previous:
            print(format_notification(notice), file=sys.stderr, flush=True)
        return cursor
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        cursor = "error:" + str(exc)[:500]
        if cursor != previous:
            print("[提醒检查失败] " + str(exc)[:500], file=sys.stderr, flush=True)
        return cursor


def _stop_child(child):
    if child.poll() is not None:
        return
    if os.name == "nt":
        # Only terminate the process tree launched by this wrapper. No shell,
        # process-name matching or unrelated background task is involved.
        taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
        try:
            subprocess.run([str(taskkill), "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            if child.poll() is None:
                child.terminate()
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            child.kill()
        child.wait(timeout=5)


def _launch(command, **options):
    return subprocess.Popen(command, **options)


def _validate_output_streams():
    for stream, label in ((sys.stdout, "标准输出"), (sys.stderr, "标准错误")):
        try:
            if stream is None:
                raise OSError(label + "不可用")
            descriptor = stream.fileno()
            if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
                raise OSError(label + "没有有效的进程句柄")
            os.fstat(descriptor)
        except (OSError, ValueError, AttributeError) as exc:
            raise OSError(label + "无法传递给子进程，未执行命令") from exc


def execute_tracked(args):
    """Keep child output and exit code; record only the fixed wrapper name."""
    sessions = _session_tools()
    from agents_activity import safe_touch_activity, touch_activity
    from agents_jobs import register_job, attach_child, unregister_job
    command = list(args.command)
    if not command or any(not isinstance(part, str) or "\x00" in part for part in command) or not command[0]:
        raise ValueError("run 需要 -- 后的实际命令及参数")
    _validate_output_streams()
    requested_cwd = Path(args.root).expanduser().absolute()
    cwd = requested_cwd.resolve()
    root = Path(sessions.git(cwd, "rev-parse", "--show-toplevel")).resolve()
    try:
        relative_cwd = cwd.relative_to(root)
    except ValueError:
        raise ValueError("命令目录不在核验的工作区内") from None
    if ".git" in relative_cwd.parts:
        raise ValueError("不能在 Git 元数据目录运行任务命令")
    args._notification_root = root
    branch, roots = _validate_owner(root, args.agent, args.session, check_claims=True)
    args._notification_roots = roots
    touch_activity(root, args.agent, args.session, "agents_session.run", force=True, branch=branch)
    _validate_owner(root, args.agent, args.session)
    options = {"cwd": str(cwd), "shell": False, "stdin": sys.stdin if sys.stdin is not None else subprocess.DEVNULL,
               "stdout": sys.stdout, "stderr": sys.stderr}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    job_id = uuid.uuid4().hex
    register_job(root, args.agent, args.session, job_id, branch=branch)
    child = None
    notice_cursor = None
    result = None
    group_cleanup_error = None
    try:
        notice_cursor = _notice(root, args.agent, args.session, roots)
        if requested_cwd.resolve() != cwd:
            raise sessions.ConflictError("命令目录在核对期间被重定向，未启动子进程")
        # Keep the marker attached to a supervisor that outlives the original
        # command while any inherited background descendant can still write.
        managed_command = [sys.executable, "-B", str(Path(__file__).with_name("agents_processes.py")),
                           "--", *command]
        child = _launch(managed_command, **options)
        attach_child(root, args.agent, args.session, job_id, child.pid,
                     group_id=child.pid if os.name != "nt" else None)
        next_activity = time.monotonic() + ACTIVITY_INTERVAL
        while result is None:
            try:
                result = child.wait(timeout=WAIT_SLICE)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= next_activity:
                    branch, roots = _validate_owner(root, args.agent, args.session, enforce_ack=False)
                    touch_activity(root, args.agent, args.session, "agents_session.run", force=True, branch=branch)
                    notice_cursor = _notice(root, args.agent, args.session, roots, notice_cursor)
                    next_activity = time.monotonic() + ACTIVITY_INTERVAL
    except KeyboardInterrupt:
        if child is not None:
            _stop_child(child)
        result = 130
    except BaseException:
        if child is not None:
            _stop_child(child)
        raise
    finally:
        if child is not None and os.name != "nt":
            try:
                from agents_processes import stop_owned_group
                # Even a separately killed supervisor cannot leave an owned
                # group writing after its marker is removed.
                stop_owned_group(child.pid)
            except (ImportError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                group_cleanup_error = "无法确认本次隔离组已停止，保留运行记录：" + str(exc)[:500]
                print("[进程组清理失败] " + group_cleanup_error, file=sys.stderr, flush=True)
        if group_cleanup_error is None:
            try:
                unregister_job(root, args.agent, args.session, job_id)
            except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                print("[运行记录清理失败] " + str(exc)[:500], file=sys.stderr, flush=True)
        activity = safe_touch_activity(root, args.agent, args.session, "agents_session.run", force=True)
        if activity.get("warning"):
            print("[活动记录失败] " + activity["warning"], file=sys.stderr, flush=True)
    if group_cleanup_error is not None:
        raise OSError(group_cleanup_error)
    _notice(root, args.agent, args.session, roots, notice_cursor)
    if result is not None and result < 0:
        return 128 - result
    return result
