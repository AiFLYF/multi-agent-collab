"""Declared terminal states cannot hide commands that still own project files."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

from selftest import KIT, rmtree_readonly
import agents_activity as activity
import agents_jobs as jobs
import agents_session as sessions
import agents_status as board


class TestTerminalJobClaims(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="mac-terminal-job-tests-")).resolve()
        cls.root = cls.base / "project"
        cls.root.mkdir()
        cls.git = board.resolve_git()
        cls.branch = "feature/terminal-jobs"
        # Git operations belong only to this verified temporary test project.
        for arguments in (("init", "-q", "-b", "main"), ("config", "user.name", "Terminal Jobs Test"),
                          ("config", "user.email", "terminal-jobs@example.invalid")):
            cls.git_call(*arguments)
        (cls.root / "README.md").write_text("# Terminal jobs fixture\n", encoding="utf-8")
        (cls.root / ".gitignore").write_text(".agents/\n", encoding="utf-8")
        cls.git_call("add", "README.md", ".gitignore")
        cls.git_call("commit", "-q", "-m", "test: fixture")
        cls.git_call("switch", "-c", cls.branch)
        cls.state = cls.root / ".agents"
        for directory in ("sessions", "inbox", "worklog"):
            (cls.state / directory).mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-terminal-job-tests-"):
            raise RuntimeError("Refusing terminal-jobs fixture cleanup outside its temporary workspace")
        rmtree_readonly(cls.base)

    @classmethod
    def git_call(cls, *arguments):
        result = subprocess.run([cls.git, *arguments], cwd=str(cls.root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.identifier = "codex-terminal-jobs-test"
        self.path = self.state / "sessions" / (self.identifier + ".md")
        self.path.write_bytes(("# Session\n- agent: codex\n- branch: " + self.branch
                               + "\n- task: Registered terminal regression\n- status: active\n- files: [\"app.py\"]\n"
                               + "- heartbeat: " + datetime.now().strftime("%Y-%m-%d %H:%M")
                               + "\n- acknowledged_inbox: []\n- acknowledged_inbox_versions: {}\n\n## Notes\nKeep original history.\n").encode("utf-8"))
        (self.root / "app.py").write_text("original owner content", encoding="utf-8")

    def record(self):
        return board.parse_kv(self.path)

    def change(self, **fields):
        text = self.path.read_text(encoding="utf-8")
        self.path.write_bytes(activity.update_session_fields(text, fields).encode("utf-8"))

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    def observe(self):
        before = self.snapshot()
        now = datetime.now()
        loaded = sessions.sessions_in([self.root])
        payload = board.collect(now, False, 15, root=self.root, compact=True, include_logs=False)
        conflicts = sessions.check_conflicts(self.root, "feature/next-owner", ["app.py"], loaded)
        self.assertEqual(self.snapshot(), before, "Reading state must not repair or rewrite session records")
        return loaded[0], payload, conflicts

    @contextlib.contextmanager
    def registered_holder(self):
        events = self.base / ("holder-" + uuid.uuid4().hex)
        events.mkdir()
        ready, release = events / "ready.json", events / "release"
        child_source = ("from pathlib import Path\nimport sys,time\n"
                        "release=Path(sys.argv[1]); deadline=time.monotonic()+40\n"
                        "while not release.exists() and time.monotonic()<deadline:\n time.sleep(.02)\n"
                        "if not release.exists(): sys.exit(3)\n"
                        "Path('app.py').write_text('registered child result',encoding='utf-8')\n")
        bootstrap = ("import json,os,subprocess,sys,uuid\nfrom pathlib import Path\n"
                     "sys.dont_write_bytecode=True\nsys.path.insert(0,sys.argv[1])\nimport agents_jobs as jobs\n"
                     "root=Path(sys.argv[2]); identifier=sys.argv[3]; ready=Path(sys.argv[4]); release=Path(sys.argv[5])\n"
                     "token=jobs.register_job(root,'codex',identifier,uuid.uuid4().hex)\nchild=None\n"
                     "try:\n"
                     " child=subprocess.Popen([sys.executable,'-c',sys.argv[6],str(release)],cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
                     " jobs.attach_child(root,'codex',identifier,token,child.pid)\n"
                     " ready.write_text(json.dumps({'owner_pid':os.getpid(),'child_pid':child.pid,'token':token}),encoding='utf-8')\n"
                     " result=child.wait()\n"
                     "finally:\n"
                     " if child is not None and child.poll() is None:\n  child.terminate(); child.wait(timeout=10)\n"
                     " try: jobs.unregister_job(root,'codex',identifier,token)\n"
                     " except (ValueError,OSError) as error: print('fixture cleanup: '+str(error),file=sys.stderr)\n"
                     "sys.exit(result)\n")
        options = {"cwd": str(self.root), "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        wrapper = subprocess.Popen([sys.executable, "-B", "-c", bootstrap, str(KIT / "tools"), str(self.root),
                                    self.identifier, str(ready), str(release), child_source], **options)
        handle, kernel = None, None
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and time.monotonic() < deadline:
                if wrapper.poll() is not None:
                    output, errors = wrapper.communicate(timeout=10)
                    self.fail((output + errors).decode("utf-8", errors="replace"))
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "The real wrapper never registered its child")
            metadata = json.loads(ready.read_text(encoding="utf-8"))
            self.assertEqual(metadata["owner_pid"], wrapper.pid)
            self.assertEqual(jobs.jobs_for_record(self.record())[metadata["token"]]["child_pid"], metadata["child_pid"])
            if os.name == "nt":
                # Retain the child object so Windows cannot recycle this PID
                # before the dead-orphan compatibility assertions finish.
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel.CloseHandle.restype = wintypes.BOOL
                handle = kernel.OpenProcess(0x00100000 | 0x00001000, False, metadata["child_pid"])
                self.assertTrue(handle)
            yield wrapper, release, metadata
        finally:
            release.write_text("release", encoding="utf-8")
            try:
                wrapper.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                from agents_run import _stop_child
                _stop_child(wrapper)
                wrapper.communicate(timeout=20)
            if handle is not None and kernel is not None:
                kernel.CloseHandle(handle)

    def assert_blocking_terminal(self, status, *, damaged=False):
        item, payload, conflicts = self.observe()
        self.assertEqual(item["status"], status, "A conflicting declaration must remain visible, not silently become active")
        self.assertFalse(item["closed"])
        self.assertTrue(item["occupies"])
        self.assertTrue(item["terminal_job_conflict"])
        self.assertEqual(item["jobs_error"], damaged)
        self.assertTrue(conflicts, "An existing command must stop a new writer from claiming app.py")
        self.assertTrue(payload["warnings"], "Conflicting terminal declarations must warn at the normal status boundary")
        self.assertEqual(board.gate_rc(payload), 1)

    def test_live_registered_commands_override_done_closed_and_abandoned_claims(self):
        with self.registered_holder() as (wrapper, _release, metadata):
            for status in ("done", "closed", "abandoned"):
                with self.subTest(status=status):
                    self.change(status=status)
                    self.assertTrue(jobs.process_is_alive(metadata["child_pid"]))
                    self.assertIsNone(wrapper.poll())
                    self.assert_blocking_terminal(status)

    def test_unknown_process_state_does_not_release_declared_terminal_files(self):
        with self.registered_holder():
            for status in ("done", "closed", "abandoned"):
                with self.subTest(status=status):
                    self.change(status=status)
                    with mock.patch.object(jobs, "process_is_alive", return_value=None):
                        self.assert_blocking_terminal(status)

    def test_damaged_job_data_cannot_be_hidden_by_a_terminal_word(self):
        with self.registered_holder():
            for status in ("done", "closed", "abandoned"):
                for broken in ("not-json", '{"bad-token":{}}'):
                    with self.subTest(status=status, broken=broken):
                        self.change(status=status, running_jobs=broken)
                        self.assert_blocking_terminal(status, damaged=True)

    def test_false_terminal_gate_never_allows_a_new_writer_to_be_overwritten(self):
        with self.registered_holder() as (wrapper, release, _metadata):
            self.change(status="done")
            _item, _payload, conflicts = self.observe()
            attempted_next_write = not conflicts
            if attempted_next_write:
                (self.root / "app.py").write_text("new writer content", encoding="utf-8")
            release.write_text("release", encoding="utf-8")
            output, errors = wrapper.communicate(timeout=20)
            self.assertEqual(wrapper.returncode, 0, (output + errors).decode("utf-8", errors="replace"))
            self.assertFalse(attempted_next_write, "The live command would overwrite a writer admitted by the false terminal state")
            self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "registered child result")

    def test_confirmed_dead_registered_pids_keep_terminal_history_closed(self):
        with self.registered_holder() as (wrapper, release, metadata):
            raw_markers = self.record()["running_jobs"]
            release.write_text("release", encoding="utf-8")
            output, errors = wrapper.communicate(timeout=20)
            self.assertEqual(wrapper.returncode, 0, (output + errors).decode("utf-8", errors="replace"))
            self.assertFalse(jobs.process_is_alive(metadata["owner_pid"]))
            self.assertFalse(jobs.process_is_alive(metadata["child_pid"]))
            # A crash between child exit and record cleanup can leave this exact
            # previously registered metadata behind. It must not revive history.
            for status in ("done", "closed", "abandoned"):
                with self.subTest(status=status):
                    self.change(status=status, running_jobs=raw_markers)
                    item, _payload, conflicts = self.observe()
                    self.assertEqual(item["status"], status)
                    self.assertTrue(item["closed"])
                    self.assertFalse(item["occupies"])
                    self.assertFalse(item.get("terminal_job_conflict", False))
                    self.assertFalse(item["jobs_error"])
                    self.assertEqual(conflicts, [])

    def test_old_terminal_records_without_jobs_remain_compatible(self):
        for status in ("done", "closed", "abandoned"):
            with self.subTest(status=status):
                self.change(status=status)
                item, _payload, conflicts = self.observe()
                self.assertTrue(item["closed"])
                self.assertFalse(item["occupies"])
                self.assertFalse(item.get("terminal_job_conflict", False))
                self.assertEqual(item["running_jobs_count"], 0)
                self.assertEqual(conflicts, [])

    def test_empty_job_marker_does_not_reactivate_a_terminal_record(self):
        self.change(status="done", running_jobs="{}")
        item, _payload, conflicts = self.observe()
        self.assertTrue(item["closed"])
        self.assertFalse(item["occupies"])
        self.assertFalse(item.get("terminal_job_conflict", False))
        self.assertEqual(conflicts, [])

    def test_standby_with_nonempty_jobs_still_cannot_claim_to_be_unoccupied(self):
        with self.registered_holder():
            self.change(status="standby", files="[]")
            item, _payload, conflicts = self.observe()
            self.assertEqual(item["status"], "standby")
            self.assertFalse(item["standby"])
            self.assertFalse(item["closed"])
            self.assertTrue(item["occupies"])
            self.assertTrue(item["claims_error"])
            self.assertFalse(sessions.valid_standby(self.record()))
            self.assertTrue(conflicts)


if __name__ == "__main__":
    unittest.main()
