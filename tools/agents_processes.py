"""Internal foreground tree supervisor; inherited descendants finish together."""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
POLL_INTERVAL = 0.1
GROUP_POLL_INTERVAL = 0.5
STOP_GRACE_SECONDS = 3
PS_TIMEOUT = 5
MAX_PS_BYTES = 4 * 1024 * 1024


class ProcessManagementError(RuntimeError):
    pass


def _configure_error_stream():
    # Only our diagnostic encoder changes. Child stdout/stderr are inherited
    # OS handles, so the original command's raw bytes remain unchanged.
    stream = sys.stderr
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError) as exc:
            _discard_failed_stream(stream)
            raise ProcessManagementError("标准错误初始化失败，未启动真实命令") from exc


def _discard_failed_stream(stream):
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
            if sink != descriptor:
                os.close(sink)
        stream.flush()
    except (OSError, ValueError):
        pass


def _report_error(error):
    stream = sys.stderr
    try:
        if stream is None:
            return
        message = "[进程管理失败] " + str(error)[:500] + "\n"
        if stream.write(message) != len(message):
            raise OSError("管理错误输出不完整")
        stream.flush()
    except (OSError, ValueError, AttributeError):
        _discard_failed_stream(stream)


def _validate_streams():
    for stream, label in ((sys.stdout, "标准输出"), (sys.stderr, "标准错误")):
        try:
            if stream is None:
                raise OSError(label + "不可用")
            descriptor = stream.fileno()
            if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
                raise OSError(label + "没有可继承的进程句柄")
            os.fstat(descriptor)
        except (OSError, ValueError, AttributeError) as exc:
            _discard_failed_stream(stream)
            raise ProcessManagementError(label + "无法传递，未启动真实命令") from exc


def _windows_api():
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t)]

    class Accounting(ctypes.Structure):
        _fields_ = [("user_time", ctypes.c_longlong), ("kernel_time", ctypes.c_longlong),
                    ("period_user_time", ctypes.c_longlong), ("period_kernel_time", ctypes.c_longlong),
                    ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                    ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = ()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetHandleInformation.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                               wintypes.DWORD, ctypes.c_void_p)
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel, ExtendedLimits, Accounting


class _WindowsJob:
    def __init__(self):
        self.kernel, self.Limits, self.Accounting = _windows_api()
        self.handle = None
        self.assigned = False
        self.child_started = False

    def _failure(self, operation):
        import ctypes
        raise ProcessManagementError(operation + "失败，Windows error=" + str(ctypes.get_last_error()))

    def _limits(self, flags):
        import ctypes
        limits = self.Limits()
        limits.basic.flags = flags
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self._failure("设置受管理进程组")

    def prepare(self):
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            self._failure("创建受管理进程组")
        if not self.kernel.SetHandleInformation(self.handle, 1, 0):
            self._failure("关闭进程组句柄继承")
        self._limits(0x00002000)  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.AssignProcessToJobObject(self.handle, self.kernel.GetCurrentProcess()):
            self._failure("绑定管理进程（不退回不安全的父 PID 模式）")
        self.assigned = True
        self.remaining()

    def remaining(self):
        import ctypes
        accounting = self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                     ctypes.sizeof(accounting), None):
            self._failure("读取受管理进程数量")
        if accounting.active < 1:
            raise ProcessManagementError("无法确认管理进程仍属于自己的进程组")
        return accounting.active - 1

    def finish(self):
        if self.remaining():
            raise ProcessManagementError("受管理子孙进程尚未结束，不能释放命令")
        self._limits(0)
        if not self.kernel.CloseHandle(self.handle):
            self._failure("关闭已完成的进程组")
        self.handle = None
        self.assigned = False

    def abort(self, exit_code):
        if not self.handle:
            return
        if self.assigned:
            # The job contains this supervisor and only its descendants. The
            # native call ends all of them, including us, with a failure code.
            self.kernel.TerminateJobObject(self.handle, exit_code)
            # If termination failed, KILL_ON_JOB_CLOSE remains the final guard.
        self.kernel.CloseHandle(self.handle)
        self.handle = None

    def close(self):
        if self.handle:
            self.abort(2)


def _find_ps():
    for candidate in ("/bin/ps", "/usr/bin/ps"):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("ps")
    if not found:
        raise ProcessManagementError("系统 ps 不可用，未启动真实命令")
    return found


def _parse_ps(output, pgid, supervisor_pid, query_pid, *, require_supervisor=True):
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
           for value in (pgid, supervisor_pid, query_pid)):
        raise ProcessManagementError("隔离进程组标识无效")
    if len(output) > MAX_PS_BYTES:
        raise ProcessManagementError("ps 输出超过安全读取上限")
    try:
        text = output.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProcessManagementError("ps 返回不可核验的进程数据") from exc
    seen, supervisor_seen, members = set(), False, []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or not parts[2]:
            raise ProcessManagementError("ps 进程字段无法解析，不能提前释放命令")
        pid, group, state = int(parts[0]), int(parts[1]), parts[2]
        if pid in seen:
            raise ProcessManagementError("ps 进程标识无效或重复")
        seen.add(pid)
        # Kernel/system rows can legitimately use PID or PGID zero. They do
        # not belong to this supervisor's positive, isolated process group.
        if pid == 0 or group == 0:
            if pid == supervisor_pid or group == pgid:
                raise ProcessManagementError("ps 返回无效的隔离组成员")
            continue
        if pid == supervisor_pid:
            supervisor_seen = group == pgid and not state.startswith("Z")
        ignored = (supervisor_pid, query_pid) if require_supervisor else (query_pid,)
        if group == pgid and pid not in ignored and not state.startswith("Z"):
            members.append(pid)
    if require_supervisor and not supervisor_seen:
        raise ProcessManagementError("ps 未确认自己的隔离管理进程")
    if not require_supervisor and os.getpid() not in seen:
        raise ProcessManagementError("ps 未确认当前查询进程，不能认定原进程组已结束")
    return members


def _query_members(pgid, supervisor_pid, *, require_supervisor=True, ps=None):
    query = subprocess.Popen([ps or _find_ps(), "-A", "-o", "pid=,pgid=,stat="],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             shell=False, close_fds=True)
    try:
        output, errors = query.communicate(timeout=PS_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        query.kill()
        query.communicate(timeout=2)
        raise ProcessManagementError("ps 查询超时，不能提前释放命令") from exc
    if query.returncode:
        raise ProcessManagementError("ps 查询失败，不能提前释放命令：" + errors.decode("utf-8", errors="replace")[:200])
    return _parse_ps(output, pgid, supervisor_pid, query.pid, require_supervisor=require_supervisor)


def _valid_group(pgid):
    if isinstance(pgid, bool) or not isinstance(pgid, int) or not 0 < pgid <= 0x7FFFFFFF:
        raise ProcessManagementError("进程组标识无效")


def group_members(pgid):
    """Read residual non-zombie POSIX members even after their leader exits."""
    _valid_group(pgid)
    if os.name == "nt":
        raise ProcessManagementError("Windows 不使用 POSIX 进程组，无法核验此组标识")
    return _query_members(pgid, pgid, require_supervisor=False)


def group_is_alive(pgid):
    """Return True/False/None; unknown groups must retain their job marker."""
    try:
        return bool(group_members(pgid))
    except (ProcessManagementError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None


def _residual_members(pgid):
    members = group_members(pgid)
    if pgid in members:
        # The caller waits for its original supervisor before cleanup. A live
        # leader now means the numeric PID may have been reused: never kill it.
        raise ProcessManagementError("原组标识现在有存活的组长，无法确认仍属本次命令；未终止进程")
    return members


def _wait_group_clear(pgid, seconds):
    deadline = time.monotonic() + seconds
    while _residual_members(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(GROUP_POLL_INTERVAL, 0.2))
    return True


def stop_owned_group(pgid):
    """Stop residual members of a freshly launched, already exited supervisor.

    Call only with the immutable PGID from the caller's own start_new_session
    Popen, after waiting for that supervisor. Record fields do not grant this
    permission. A live/reused leader or failed query refuses cleanup.
    """
    _valid_group(pgid)
    if os.name == "nt":
        raise ProcessManagementError("Windows 进程树由 JobObject 管理，不能发送 POSIX 组信号")
    if pgid in (os.getpid(), os.getpgrp()):
        raise ProcessManagementError("不能终止当前调用者或调用者的进程组")
    if not _residual_members(pgid):
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if _wait_group_clear(pgid, STOP_GRACE_SECONDS):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if not _wait_group_clear(pgid, 5):
        raise ProcessManagementError("残余进程组尚未确认退出；保留运行标志与文件占用")


class _PosixGroup:
    def __init__(self):
        self.pid = os.getpid()
        self.pgid = os.getpgrp()
        self.ps = None
        self.handlers = {}
        self.stop_signal = None
        self.stop_started = None
        self.child_started = False

    def _on_signal(self, signum, _frame):
        if self.stop_signal is None:
            self.stop_signal = signum

    def prepare(self):
        if self.pgid != self.pid:
            raise ProcessManagementError("管理进程必须由 run 在独立进程组中启动")
        self.ps = _find_ps()
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._on_signal)
        self.remaining()

    def remaining(self):
        return len(_query_members(self.pgid, self.pid, ps=self.ps))

    def poll_stop(self):
        if self.stop_signal is None:
            return
        if self.stop_started is None:
            self.stop_started = time.monotonic()
            os.killpg(self.pgid, signal.SIGTERM)
        elif time.monotonic() - self.stop_started >= STOP_GRACE_SECONDS:
            self.abort(2)

    def finish(self):
        if self.remaining():
            raise ProcessManagementError("隔离进程组尚有子孙进程，不能释放命令")

    def abort(self, _exit_code):
        if self.child_started and self.pgid == self.pid:
            # Runtime query failure cannot make us disappear while same-group
            # descendants keep writing. Kill only our dedicated group.
            os.killpg(self.pgid, signal.SIGKILL)
            os._exit(128 + signal.SIGKILL)

    def close(self):
        for signum, previous in self.handlers.items():
            signal.signal(signum, previous)
        self.handlers.clear()


def _manager():
    return _WindowsJob() if os.name == "nt" else _PosixGroup()


def _normalized_exit(code):
    return 128 - code if os.name != "nt" and code < 0 else code


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    manager = None
    verified_finish = False
    try:
        _configure_error_stream()
        if not arguments or arguments[0] != "--" or len(arguments) < 2:
            raise ProcessManagementError("内部管理入口需要 -- 后的真实命令")
        command = arguments[1:]
        if not command[0] or any(not isinstance(part, str) or "\x00" in part for part in command):
            raise ProcessManagementError("命令参数无效，未启动真实命令")
        _validate_streams()
        manager = _manager()
        manager.prepare()
        if isinstance(manager, _PosixGroup) and manager.stop_signal is not None:
            return 128 + manager.stop_signal
        options = {"shell": False, "stdin": sys.stdin if sys.stdin is not None else subprocess.DEVNULL,
                   "stdout": sys.stdout, "stderr": sys.stderr}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        child = subprocess.Popen(command, **options)
        manager.child_started = True
        original_exit = None
        while original_exit is None:
            try:
                original_exit = child.wait(timeout=POLL_INTERVAL)
            except subprocess.TimeoutExpired:
                if isinstance(manager, _PosixGroup):
                    manager.poll_stop()
        while manager.remaining():
            if isinstance(manager, _PosixGroup):
                manager.poll_stop()
            time.sleep(GROUP_POLL_INTERVAL)
        manager.finish()
        verified_finish = True
        if isinstance(manager, _PosixGroup) and manager.stop_signal is not None:
            return 128 + manager.stop_signal
        return _normalized_exit(original_exit)
    except KeyboardInterrupt:
        if manager is not None:
            manager.abort(130)
        return 130
    except BaseException as exc:
        _report_error(exc)
        if manager is not None:
            manager.abort(2)
        return 2
    finally:
        if manager is not None:
            if not verified_finish:
                manager.abort(2)
            manager.close()


if __name__ == "__main__":
    status = main()
    # Python's Windows SystemExit conversion uses a signed C long. Preserve the
    # original DWORD bit pattern for high-bit native process exit statuses.
    if os.name == "nt" and isinstance(status, int) and 0x80000000 <= status <= 0xFFFFFFFF:
        status -= 0x100000000
    sys.exit(status)
