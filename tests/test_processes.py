"""Managed inherited descendants keep a command alive without changing its output."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from selftest import KIT, rmtree_readonly
import agents_jobs as jobs
import agents_processes as processes

SUPERVISOR = KIT / "tools/agents_processes.py"


class TestProcessTable(unittest.TestCase):
    def test_only_live_owned_group_members_count(self):
        table = b"0 0 S\n1 1 S\n101 101 Ss\n202 101 R\n303 101 Z+\n404 101 S\n505 999 S\n"
        self.assertEqual(processes._parse_ps(table, 101, 101, 202), [404])

    def test_missing_self_wrong_group_duplicate_and_bad_rows_are_not_empty(self):
        for table in (b"", b"101 999 S\n", b"101 101 S\n101 101 R\n", b"101 bad S\n",
                      b"101 101 S extra\n", b"0 101 S\n101 101 S\n", b"101 101 \xff\n"):
            with self.subTest(table=table), self.assertRaises(processes.ProcessManagementError):
                processes._parse_ps(table, 101, 101, 202)

    def test_invalid_group_identifiers_are_rejected(self):
        for values in ((0, 101, 202), (101, True, 202), (101, 101, -1)):
            with self.subTest(values=values), self.assertRaises(processes.ProcessManagementError):
                processes._parse_ps(b"101 101 S\n", *values)

    def test_residual_query_counts_live_members_after_original_leader_exits(self):
        table = b"909 999 S\n202 101 R\n303 101 Z+\n404 101 S\n505 888 S\n"
        with mock.patch.object(processes.os, "getpid", return_value=909):
            self.assertEqual(processes._parse_ps(table, 101, 101, 202, require_supervisor=False), [404])
            with self.assertRaises(processes.ProcessManagementError):
                processes._parse_ps(table, 101, 101, 202)

    def test_residual_query_missing_current_caller_is_not_an_empty_group(self):
        with mock.patch.object(processes.os, "getpid", return_value=909):
            for table in (b"", b"202 888 S\n", b"404 101 S\n"):
                with self.subTest(table=table), self.assertRaises(processes.ProcessManagementError):
                    processes._parse_ps(table, 101, 101, 202, require_supervisor=False)


class TestResidualGroupGuards(unittest.TestCase):
    def setUp(self):
        # Exercise the POSIX guards on Windows too, without replacing the
        # shared os module or sending a signal to any real process.
        self.fake_os = mock.Mock(wraps=os)
        self.fake_os.name = "posix"
        self.fake_os.getpid.return_value = 909
        self.fake_os.getpgrp = mock.Mock(return_value=999)
        self.fake_os.killpg = mock.Mock()
        self.os_patch = mock.patch.object(processes, "os", self.fake_os)
        self.os_patch.start()
        self.addCleanup(self.os_patch.stop)

    def test_invalid_or_current_caller_groups_never_send_signals(self):
        for pgid in (None, "101", True, 0, -1, 0x80000000, 909, 999):
            with self.subTest(pgid=pgid), mock.patch.object(processes, "_query_members") as query:
                with self.assertRaises(processes.ProcessManagementError):
                    processes.stop_owned_group(pgid)
                query.assert_not_called()
                self.fake_os.killpg.assert_not_called()

    def test_a_live_or_reused_group_leader_refuses_cleanup(self):
        with mock.patch.object(processes, "_query_members", return_value=[101, 404]):
            self.assertIs(processes.group_is_alive(101), True)
            with self.assertRaises(processes.ProcessManagementError):
                processes.stop_owned_group(101)
        self.fake_os.killpg.assert_not_called()

    def test_a_verified_empty_group_does_not_receive_signals(self):
        with mock.patch.object(processes, "_query_members", return_value=[]):
            self.assertIs(processes.group_is_alive(101), False)
            self.assertIsNone(processes.stop_owned_group(101))
        self.fake_os.killpg.assert_not_called()

    def test_missing_ps_yields_unknown_liveness_and_refuses_cleanup(self):
        with mock.patch.object(processes, "_find_ps", side_effect=processes.ProcessManagementError("missing ps")), \
                mock.patch.object(processes.subprocess, "Popen") as launch:
            self.assertIsNone(processes.group_is_alive(101))
            with self.assertRaises(processes.ProcessManagementError):
                processes.stop_owned_group(101)
            launch.assert_not_called()
        self.fake_os.killpg.assert_not_called()

    def test_failed_or_unverifiable_ps_yields_unknown_not_dead(self):
        cases = [(1, b"909 999 S\n", b"query refused"),
                 (0, b"404 101 S\n", b""),
                 (0, b"909 999 S\n404 bad S\n", b"")]
        for code, output, errors in cases:
            with self.subTest(code=code, output=output):
                query = mock.Mock(pid=202, returncode=code)
                query.communicate.return_value = (output, errors)
                with mock.patch.object(processes, "_find_ps", return_value="/fixed/ps"), \
                        mock.patch.object(processes.subprocess, "Popen", return_value=query):
                    self.assertIsNone(processes.group_is_alive(101))
                    with self.assertRaises(processes.ProcessManagementError):
                        processes.stop_owned_group(101)
                self.fake_os.killpg.assert_not_called()


class TestManagedProcesses(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-managed-process-tests-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-managed-process-tests-"):
            raise RuntimeError("Refusing process test cleanup outside its temporary workspace")
        rmtree_readonly(self.base)

    def launch(self, command, *, bootstrap=None, input_pipe=False):
        arguments = ([sys.executable, "-B", "-c", bootstrap] if bootstrap is not None
                     else [sys.executable, "-B", str(SUPERVISOR)])
        options = {"cwd": str(self.root), "stdin": subprocess.PIPE if input_pipe else subprocess.DEVNULL,
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            options["start_new_session"] = True
        return subprocess.Popen([*arguments, "--", *command], **options)

    def boot(self, patch):
        return ("import sys\nsys.dont_write_bytecode=True\nsys.path.insert(0," + repr(str(KIT / "tools"))
                + ")\nimport agents_processes as p\n" + patch + "\nsys.exit(p.main(sys.argv[1:]))\n")

    def running(self, pid):
        if os.name == "nt":
            return jobs.process_is_alive(pid) is True
        ps = processes._find_ps()
        result = subprocess.run([ps, "-p", str(pid), "-o", "stat="], capture_output=True, timeout=5)
        state = result.stdout.decode("ascii", errors="replace").strip()
        return bool(state) and not state.startswith(("Z", "X"))

    def stop(self, child):
        if child.poll() is not None:
            return
        if os.name == "nt":
            child.terminate()  # Last noninherited job handle closes, killing descendants.
        else:
            os.killpg(child.pid, signal.SIGTERM)
        try:
            child.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                child.kill()
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=5)

    def parent_command(self, ready, release, exit_code=0, *, ignore_term=False):
        grandchild = ("import json,os,sys,time,signal\nfrom pathlib import Path\n"
                      + ("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else "")
                      + "release=Path(sys.argv[1]); deadline=time.monotonic()+35\n"
                      "while not release.exists() and time.monotonic()<deadline:\n time.sleep(.02)\n"
                      "if not release.exists(): sys.exit(4)\n"
                      "Path('app.py').write_text('GRANDCHILD_RESULT',encoding='utf-8')\n")
        parent = ("import json,os,subprocess,sys\nfrom pathlib import Path\n"
                  "options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}\n"
                  "if os.name=='nt': options['creationflags']=subprocess.CREATE_NO_WINDOW\n"
                  "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]],**options)\n"
                  "data={'parent':os.getpid(),'grandchild':child.pid,'pgid':os.getpgrp() if hasattr(os,'getpgrp') else None}\n"
                  "Path(sys.argv[2]).write_text(json.dumps(data),encoding='utf-8')\n"
                  "print(json.dumps(data),flush=True)\nsys.exit(int(sys.argv[4]))\n")
        return [sys.executable, "-c", parent, grandchild, str(ready), str(release), str(exit_code)]

    def ready(self, child, path):
        deadline = time.monotonic() + 15
        while not path.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                output, errors = child.communicate(timeout=5)
                self.fail((output + errors).decode("utf-8", errors="replace"))
            time.sleep(0.02)
        self.assertTrue(path.exists(), "The original command did not start")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_parent_exit_does_not_finish_until_inherited_grandchild_finishes(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                ready, release = self.base / ("ready-" + str(exit_code)), self.base / ("release-" + str(exit_code))
                child = self.launch(self.parent_command(ready, release, exit_code))
                try:
                    lineage = self.ready(child, ready)
                    time.sleep(0.2)
                    self.assertFalse(self.running(lineage["parent"]))
                    self.assertTrue(self.running(lineage["grandchild"]))
                    self.assertIsNone(child.poll(), "The supervisor exited while its inherited grandchild was still alive")
                    if os.name != "nt":
                        self.assertEqual(os.getpgid(lineage["grandchild"]), child.pid)
                    release.write_text("release", encoding="utf-8")
                    output, errors = child.communicate(timeout=12)
                    self.assertEqual(child.returncode, exit_code, errors.decode("utf-8", errors="replace"))
                    self.assertEqual(json.loads(output.decode("utf-8")), lineage)
                    self.assertEqual(errors, b"")
                    self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "GRANDCHILD_RESULT")
                finally:
                    release.write_text("release", encoding="utf-8")
                    self.stop(child)

    def test_stdin_and_literal_arguments_are_preserved_without_files(self):
        values = ["two words", "$(not-a-shell)", "a;not-a-command", "--json", "sentinel-secret-value"]
        command = [sys.executable, "-c", "import json,sys; print(json.dumps({'stdin':sys.stdin.read(),'args':sys.argv[1:]}))", *values]
        child = self.launch(command, input_pipe=True)
        output, errors = child.communicate(input=b"original input\n", timeout=15)
        self.assertEqual(child.returncode, 0, errors.decode("utf-8", errors="replace"))
        self.assertEqual(json.loads(output.decode("utf-8")), {"stdin": "original input\n", "args": values})
        self.assertEqual(errors, b"")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_internal_separator_never_executes_command(self):
        child = self.launch([sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('bad')"],
                            bootstrap=self.boot("sys.argv=sys.argv[:1]+sys.argv[2:]"))
        output, _errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(output, b"")
        self.assertFalse((self.root / "executed").exists())

    def test_unavailable_stdout_or_stderr_never_executes_command(self):
        for patch in ("sys.stdout=None", "sys.stderr=None", "import os; os.close(1)"):
            with self.subTest(patch=patch):
                child = self.launch([sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('bad')"],
                                    bootstrap=self.boot(patch))
                output, _errors = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 2)
                self.assertEqual(output, b"")
                self.assertFalse((self.root / "executed").exists())

    def test_failed_manager_setup_is_not_an_unsafe_parent_pid_fallback(self):
        patch = ("class Failed:\n"
                 " def prepare(self): raise p.ProcessManagementError('controlled setup failure')\n"
                 " def abort(self,code): pass\n def close(self): pass\n"
                 "p._manager=lambda:Failed()")
        child = self.launch([sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('bad')"],
                            bootstrap=self.boot(patch))
        output, errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(output, b"")
        self.assertIn(b"controlled setup failure", errors)
        self.assertFalse((self.root / "executed").exists())

    def test_missing_executable_diagnostic_is_strict_utf8(self):
        child = self.launch([str(self.root / "missing-可执行文件")])
        output, errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(output, b"")
        diagnostic = errors.decode("utf-8", errors="strict")
        self.assertIn("进程管理失败", diagnostic)
        self.assertNotIn("Traceback", diagnostic)

    @unittest.skipUnless(os.name == "nt", "Windows JobObject assignment regression")
    def test_failed_job_assignment_never_starts_real_command(self):
        patch = ("native=p._windows_api\n"
                 "def refused():\n kernel,limits,accounting=native()\n"
                 " kernel.AssignProcessToJobObject=lambda *args:False\n return kernel,limits,accounting\n"
                 "p._windows_api=refused")
        child = self.launch([sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('bad')"],
                            bootstrap=self.boot(patch))
        output, _errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(output, b"")
        self.assertFalse((self.root / "executed").exists())

    @unittest.skipUnless(os.name == "nt", "Windows nested JobObject support")
    def test_nested_windows_jobs_complete_with_original_output(self):
        child = self.launch([sys.executable, "-B", str(SUPERVISOR), "--", sys.executable, "-c", "print('NESTED_SUCCESS')"])
        output, errors = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, errors.decode("utf-8", errors="replace"))
        self.assertEqual(output.decode("utf-8").splitlines(), ["NESTED_SUCCESS"])
        self.assertEqual(errors, b"")

    @unittest.skipUnless(os.name == "nt", "Windows DWORD process statuses")
    def test_high_bit_windows_exit_status_preserves_original_dword(self):
        child = self.launch([sys.executable, "-c", "import ctypes; ctypes.windll.kernel32.ExitProcess(0xC0000005)"])
        output, errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 0xC0000005, errors.decode("utf-8", errors="replace"))
        self.assertEqual(output, b"")
        self.assertEqual(errors, b"")

    @unittest.skipUnless(os.name == "nt", "Windows last-job-handle cleanup")
    def test_terminating_supervisor_kills_its_inherited_background_grandchild(self):
        ready, release = self.base / "ready-kill", self.base / "release-kill"
        child = self.launch(self.parent_command(ready, release))
        try:
            lineage = self.ready(child, ready)
            self.assertTrue(self.running(lineage["grandchild"]))
            child.terminate()
            child.communicate(timeout=10)
            deadline = time.monotonic() + 5
            while self.running(lineage["grandchild"]) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self.running(lineage["grandchild"]), "Inherited job handle leaked or the descendant was not assigned")
            release.write_text("release", encoding="utf-8")
            time.sleep(0.2)
            self.assertFalse((self.root / "app.py").exists())
        finally:
            self.stop(child)

    @unittest.skipIf(os.name == "nt", "POSIX process-group termination")
    def test_posix_group_termination_keeps_no_live_inherited_descendant(self):
        ready, release = self.base / "ready-term", self.base / "release-term"
        child = self.launch(self.parent_command(ready, release, ignore_term=True))
        try:
            lineage = self.ready(child, ready)
            time.sleep(0.1)
            os.killpg(child.pid, signal.SIGTERM)
            child.communicate(timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertFalse(self.running(lineage["grandchild"]))
            release.write_text("release", encoding="utf-8")
            time.sleep(0.1)
            self.assertFalse((self.root / "app.py").exists())
        finally:
            self.stop(child)

    @unittest.skipIf(os.name == "nt", "POSIX residual group after a supervisor-only kill")
    def test_killed_posix_supervisor_leaves_detectable_owned_group_until_cleanup(self):
        ready, release = self.base / "ready-killed-leader", self.base / "release-killed-leader"
        child = self.launch(self.parent_command(ready, release, ignore_term=True))
        try:
            lineage = self.ready(child, ready)
            time.sleep(0.2)
            self.assertFalse(self.running(lineage["parent"]))
            self.assertTrue(self.running(lineage["grandchild"]))
            os.kill(child.pid, signal.SIGKILL)  # Deliberately leave its group alive.
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL)
            self.assertIs(processes.group_is_alive(child.pid), True)
            self.assertIn(lineage["grandchild"], processes.group_members(child.pid))
            processes.stop_owned_group(child.pid)
            self.assertIs(processes.group_is_alive(child.pid), False)
            self.assertFalse(self.running(lineage["grandchild"]))
            release.write_text("release", encoding="utf-8")
            time.sleep(0.2)
            self.assertFalse((self.root / "app.py").exists())
        finally:
            self.stop(child)
            # This numeric group belongs to our fresh start_new_session Popen.
            # Cleanup must also cover a failed assertion after its leader died.
            if processes.group_is_alive(child.pid) is True:
                processes.stop_owned_group(child.pid)

    @unittest.skipIf(os.name == "nt", "POSIX setup query requirement")
    def test_missing_ps_fails_before_real_command(self):
        patch = "p._find_ps=lambda: (_ for _ in ()).throw(p.ProcessManagementError('controlled missing ps'))"
        child = self.launch([sys.executable, "-c", "from pathlib import Path; Path('executed').write_text('bad')"],
                            bootstrap=self.boot(patch))
        output, errors = child.communicate(timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(output, b"")
        self.assertIn(b"controlled missing ps", errors)
        self.assertFalse((self.root / "executed").exists())

    def _runtime_failure_case(self, exception_name):
        ready, release = self.base / "ready-query", self.base / "release-query"
        target = "p._WindowsJob" if os.name == "nt" else "p._PosixGroup"
        patch = ("kind=" + target + "\nnative=kind.remaining\n"
                 "def broken(self):\n"
                 " if self.child_started: raise " + exception_name + "('controlled runtime query failure')\n"
                 " return native(self)\nkind.remaining=broken")
        child = self.launch(self.parent_command(ready, release), bootstrap=self.boot(patch))
        lineage = None
        try:
            # It can fail quickly after the parent wrote ready; do not require
            # the supervisor to stay alive after the intentional query error.
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            lineage = json.loads(ready.read_text(encoding="utf-8"))
            output, errors = child.communicate(timeout=10)
            self.assertNotEqual(child.returncode, 0)
            self.assertEqual(json.loads(output.decode("utf-8")), lineage)
            self.assertIn(b"controlled runtime query failure", errors)
            deadline = time.monotonic() + 5
            while self.running(lineage["grandchild"]) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self.running(lineage["grandchild"]))
            release.write_text("release", encoding="utf-8")
            time.sleep(0.1)
            self.assertFalse((self.root / "app.py").exists())
        finally:
            self.stop(child)

    def test_runtime_query_failure_does_not_leave_inherited_child_writing(self):
        self._runtime_failure_case("p.ProcessManagementError")

    def test_unexpected_management_exception_also_stops_inherited_children(self):
        self._runtime_failure_case("LookupError")


class TestTreeRunIntegration(unittest.TestCase):
    def test_managed_run_blocks_finish_until_background_grandchild_completes(self):
        self._managed_run_case()

    @unittest.skipIf(os.name == "nt", "POSIX supervisor-only kill with surviving descendants")
    def test_run_cleans_owned_group_after_supervisor_kill_without_late_write(self):
        self._managed_run_case(kill_supervisor=True)

    def _managed_run_case(self, *, kill_supervisor=False):
        base = Path(tempfile.mkdtemp(prefix="mac-tree-run-integration-")).resolve()
        root = base / "project"
        root.mkdir()
        wrapper = None
        release = base / "release"
        try:
            import agents_session as sessions
            git = sessions.board.resolve_git()
            for arguments in (("init", "-q", "-b", "develop"), ("config", "user.name", "Tree Run Test"),
                              ("config", "user.email", "tree-run@example.invalid")):
                result = subprocess.run([git, *arguments], cwd=str(root), capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            (root / "README.md").write_text("# Tree run fixture\n", encoding="utf-8")
            (root / ".gitignore").write_text(".agents/\n", encoding="utf-8")
            for arguments in (("add", "README.md", ".gitignore"), ("commit", "-q", "-m", "test: fixture"),
                              ("switch", "-c", "feature/tree-run")):
                result = subprocess.run([git, *arguments], cwd=str(root), capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            for name in ("sessions", "inbox", "worklog"):
                (root / ".agents" / name).mkdir(parents=True)
            identifier = "codex-tree-run-test"
            record = root / ".agents/sessions" / (identifier + ".md")
            record.write_text("# Session\n- agent: codex\n- branch: feature/tree-run\n- task: Tree integration\n"
                              "- status: active\n- files: [\"app.py\"]\n- heartbeat: 2000-01-01 00:00\n", encoding="utf-8")
            ready = base / "ready"
            grand_source = ("import sys,time\nfrom pathlib import Path\n"
                            "release=Path(sys.argv[1]); deadline=time.monotonic()+35\n"
                            "while not release.exists() and time.monotonic()<deadline:\n time.sleep(.02)\n"
                            "if not release.exists(): sys.exit(3)\n"
                            "Path('app.py').write_text('MANAGED_RESULT',encoding='utf-8')\n")
            parent_source = ("import json,os,subprocess,sys\nfrom pathlib import Path\n"
                             "options={'stdin':subprocess.DEVNULL,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL}\n"
                             "if os.name=='nt': options['creationflags']=subprocess.CREATE_NO_WINDOW\n"
                             "child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]],**options)\n"
                             "Path(sys.argv[2]).write_text(json.dumps({'parent':os.getpid(),'grandchild':child.pid,"
                             "'pgid':os.getpgrp() if hasattr(os,'getpgrp') else None}),encoding='utf-8')\n"
                             "print('ORIGINAL_PARENT_OUTPUT',flush=True)\n")
            cli = [sys.executable, "-B", str(KIT / "tools/agents_session.py"), "--root", str(root)]
            options = {"cwd": str(root), "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            wrapper = subprocess.Popen([*cli, "run", identifier, "--agent", "codex", "--", sys.executable, "-c",
                                        parent_source, grand_source, str(ready), str(release)], **options)
            deadline = time.monotonic() + 20
            while not ready.exists() and wrapper.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists())
            lineage = json.loads(ready.read_text(encoding="utf-8"))
            time.sleep(0.2)
            self.assertIsNone(wrapper.poll(), "run lost its inherited descendant after the original parent exited")
            fields = sessions.board.parse_kv(record)
            self.assertTrue(jobs.has_job_markers(fields))
            finish = subprocess.run([*cli, "finish", identifier, "--agent", "codex", "--summary", "Premature tree test",
                                     "--tests", "Grandchild still running", "--standby", "--no-activity", "--json"],
                                    cwd=str(root), capture_output=True, encoding="utf-8", timeout=20)
            self.assertEqual(finish.returncode, 1, finish.stdout + finish.stderr)
            self.assertEqual(sessions.board.parse_kv(record)["files"], '["app.py"]')
            if kill_supervisor:
                self.assertIs(processes.group_is_alive(lineage["pgid"]), True)
                os.kill(lineage["pgid"], signal.SIGKILL)
            else:
                release.write_text("release", encoding="utf-8")
            output, errors = wrapper.communicate(timeout=15)
            self.assertEqual(wrapper.returncode, 137 if kill_supervisor else 0,
                             errors.decode("utf-8", errors="replace"))
            self.assertEqual(output.decode("utf-8").splitlines(), ["ORIGINAL_PARENT_OUTPUT"])
            self.assertFalse(jobs.has_job_markers(sessions.board.parse_kv(record)))
            if kill_supervisor:
                self.assertIs(processes.group_is_alive(lineage["pgid"]), False)
                release.write_text("release", encoding="utf-8")
                time.sleep(0.2)
                self.assertFalse((root / "app.py").exists())
            else:
                self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "MANAGED_RESULT")
        finally:
            release.write_text("release", encoding="utf-8")
            if wrapper is not None:
                try:
                    wrapper.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    from agents_run import _stop_child
                    _stop_child(wrapper)
                    wrapper.communicate(timeout=10)
            if base.parent != Path(tempfile.gettempdir()).resolve() or not base.name.startswith("mac-tree-run-integration-"):
                raise RuntimeError("Refusing tree-run cleanup outside its temporary workspace")
            rmtree_readonly(base)


if __name__ == "__main__":
    unittest.main()
