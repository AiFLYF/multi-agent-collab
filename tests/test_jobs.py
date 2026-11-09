"""A registered wrapper or child must keep the task's ownership in place."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest import mock

from selftest import rmtree_readonly
import agents_activity as activity
import agents_jobs as jobs
import agents_session as sessions
from test_session_safety import directory_alias


class TestJobMetadata(unittest.TestCase):
    def value(self, owner=None, child=None):
        return {"owner_pid": owner or os.getpid(), "child_pid": child, "started": datetime.now().isoformat(timespec="seconds")}

    def record(self, value=None):
        return {"status": "active", "files": '["src/app.py"]',
                "running_jobs": json.dumps({"a" * 32: value or self.value()})}

    def test_missing_and_empty_markers_are_compatible_with_old_records(self):
        for record in ({}, {"running_jobs": "{}"}):
            self.assertEqual(jobs.jobs_for_record(record), {})
            self.assertFalse(jobs.has_job_markers(record))
            jobs.ensure_no_running_jobs(record)

    def test_job_parser_returns_copy_without_authenticating_fields(self):
        record = self.record()
        parsed = jobs.jobs_for_record(record)
        parsed["a" * 32]["owner_pid"] = 42
        self.assertEqual(jobs.jobs_for_record(record)["a" * 32]["owner_pid"], os.getpid())

    def test_malformed_or_unknown_metadata_is_a_data_error_and_blocks_standby(self):
        valid = self.value()
        malformed = ["", "not json", "null", "[]", json.dumps({"bad-token": valid}),
                     json.dumps({"a" * 32: dict(valid, argv="secret-test-value")}),
                     json.dumps({"a" * 32: dict(valid, owner_pid=0)}),
                     json.dumps({"a" * 32: dict(valid, owner_pid=True)}),
                     json.dumps({"a" * 32: dict(valid, child_pid=-1)}),
                     json.dumps({"a" * 32: dict(valid, child_pid=2 ** 40)}),
                     json.dumps({"a" * 32: dict(valid, child_pid=1235, group_id=True)}),
                     json.dumps({"a" * 32: dict(valid, child_pid=1235, group_id=1234)}),
                     json.dumps({"a" * 32: dict(valid, started="unknown time")}),
                     json.dumps({format(index, "032x"): valid for index in range(17)})]
        for raw in malformed:
            with self.subTest(raw=raw[:70]):
                record = {"running_jobs": raw}
                with self.assertRaises(ValueError) as caught:
                    jobs.jobs_for_record(record)
                self.assertNotIn("secret-test-value", str(caught.exception))
                self.assertTrue(jobs.has_job_markers(record))

    def test_repeated_json_tokens_and_nested_pid_fields_cannot_hide_a_job(self):
        token = "a" * 32
        value = json.dumps(self.value())
        for raw in ('{"' + token + '":' + value + ',"' + token + '":' + value + '}',
                    '{"' + token + '":{"owner_pid":1,"owner_pid":2,"child_pid":null,"started":"2026-01-01T00:00:00"}}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                jobs.jobs_for_record({"running_jobs": raw})

    def test_live_wrapper_prevents_completion_even_before_child_is_attached(self):
        record = self.record()
        before = dict(record)
        with self.assertRaises(sessions.ConflictError):
            jobs.ensure_no_running_jobs(record)
        self.assertEqual(record, before)
        self.assertTrue(jobs.has_job_markers(record))

    def test_unknown_process_state_remains_blocking_and_is_not_pruned(self):
        record = self.record(self.value(owner=1234, child=1235))
        with mock.patch.object(jobs, "process_is_alive", return_value=None):
            with self.assertRaises(sessions.ConflictError):
                jobs.ensure_no_running_jobs(record)
            self.assertEqual(jobs.clear_dead_jobs(record), jobs.jobs_for_record(record))

    def test_dead_markers_require_an_explicit_owner_update_before_standby(self):
        record = self.record(self.value(owner=1234, child=1235))
        with mock.patch.object(jobs, "process_is_alive", return_value=False):
            jobs.ensure_no_running_jobs(record)
            self.assertEqual(jobs.clear_dead_jobs(record), {})
            self.assertTrue(jobs.has_job_markers(record))

    def test_dead_two_pids_do_not_hide_a_live_or_unknown_inherited_group(self):
        record = self.record(dict(self.value(owner=1234, child=1235), group_id=1235))
        self.assertEqual(jobs.jobs_for_record(record)["a" * 32]["group_id"], 1235)
        with mock.patch.object(jobs, "process_is_alive", return_value=False):
            for group_state in (True, None):
                with self.subTest(group_state=group_state), mock.patch.object(jobs, "_group_state", return_value=group_state):
                    with self.assertRaises(sessions.ConflictError):
                        jobs.ensure_no_running_jobs(record)
                    self.assertEqual(jobs.clear_dead_jobs(record), jobs.jobs_for_record(record))
            with mock.patch.object(jobs, "_group_state", return_value=False):
                jobs.ensure_no_running_jobs(record)
                self.assertEqual(jobs.clear_dead_jobs(record), {})

    @unittest.skipUnless(os.name == "nt", "Foreign POSIX group records on Windows")
    def test_foreign_posix_group_is_unknown_instead_of_dead_on_windows(self):
        value = dict(self.value(owner=1234, child=1235), group_id=1235)
        with mock.patch.object(jobs, "process_is_alive", return_value=False):
            self.assertIsNone(jobs._group_state(value))
            with self.assertRaises(sessions.ConflictError):
                jobs.ensure_no_running_jobs(self.record(value))

    @unittest.skipIf(os.name == "nt", "POSIX read-only group probes")
    def test_unavailable_group_probe_remains_unknown(self):
        import agents_processes
        value = dict(self.value(owner=1234, child=1235), group_id=1235)
        with mock.patch.object(agents_processes, "group_is_alive", side_effect=OSError("controlled query failure")), \
                mock.patch.object(jobs, "process_is_alive", return_value=False):
            self.assertIsNone(jobs._group_state(value))
            with self.assertRaises(sessions.ConflictError):
                jobs.ensure_no_running_jobs(self.record(value))

    def test_invalid_pids_are_not_passed_to_operating_system(self):
        with mock.patch.object(jobs.os, "kill", side_effect=AssertionError("invalid PID was queried")):
            for pid in (None, True, 0, -1, 2 ** 40, "123"):
                with self.subTest(pid=pid), self.assertRaises(ValueError):
                    jobs.process_is_alive(pid)

    @unittest.skipIf(os.name == "nt", "POSIX query errors are not Windows process APIs")
    def test_posix_query_never_sends_a_real_signal_and_denied_access_is_unknown(self):
        with mock.patch.object(jobs.os, "kill", side_effect=PermissionError(errno.EPERM, "denied")) as query:
            self.assertIsNone(jobs.process_is_alive(1234))
            query.assert_called_once_with(1234, 0)
        with mock.patch.object(jobs.os, "kill", side_effect=ProcessLookupError(errno.ESRCH, "missing")):
            self.assertFalse(jobs.process_is_alive(1234))


class TestJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="mac-jobs-tests-")).resolve()
        cls.root = cls.base / "project"
        cls.root.mkdir()
        cls.git = sessions.board.resolve_git()
        cls.branch = "feature/jobs-test"
        for arguments in (("init", "-q", "-b", "main"), ("config", "user.name", "Jobs Test"),
                          ("config", "user.email", "jobs@example.invalid")):
            cls.git_call(*arguments)
        (cls.root / "README.md").write_text("# Jobs fixture\n", encoding="utf-8")
        (cls.root / ".gitignore").write_text(".agents/\n", encoding="utf-8")
        cls.git_call("add", "README.md", ".gitignore")
        cls.git_call("commit", "-q", "-m", "test: fixture")
        cls.git_call("switch", "-c", cls.branch)
        cls.state = cls.root / ".agents"
        for directory in ("sessions", "inbox", "worklog"):
            (cls.state / directory).mkdir(parents=True)
        (cls.state / "coordination.lock").write_bytes(b"0")
        cls.dead_processes = [cls.spawn("pass"), cls.spawn("pass")]
        for child in cls.dead_processes:
            child.wait(timeout=20)

    @classmethod
    def tearDownClass(cls):
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-jobs-tests-"):
            raise RuntimeError("Refusing jobs test cleanup outside its temporary workspace")
        rmtree_readonly(cls.base)

    @classmethod
    def git_call(cls, *arguments):
        result = subprocess.run([cls.git, *arguments], cwd=str(cls.root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout.strip()

    @classmethod
    def spawn(cls, source="import time; time.sleep(60)"):
        options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen([sys.executable, "-c", source], **options)

    def setUp(self):
        self.identifier = "codex-jobs-test"
        self.path = self.state / "sessions" / (self.identifier + ".md")
        self.original = ("# Session\n- agent: codex\n- branch: " + self.branch + "\n- task: Jobs safety\n"
                         "- status: active\n- files: [\"src/app.py\"]\n- progress: Preserve progress\n"
                         "- acknowledged_inbox: []\n- heartbeat: 2000-01-01 00:00\n\n## Notes\nPreserve notes.\n")
        self.path.write_bytes(self.original.encode("utf-8"))
        for path in (self.state / "inbox").glob("Q-jobs-*.md"):
            path.unlink()

    def record(self):
        return sessions.board.parse_kv(self.path)

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    def change(self, **fields):
        self.path.write_bytes(activity.update_session_fields(self.path.read_text(encoding="utf-8"), fields).encode("utf-8"))

    def register(self, token=None):
        return jobs.register_job(self.root, "codex", self.identifier, token or uuid.uuid4().hex, branch=self.branch)

    def dead_job(self):
        return {"owner_pid": self.dead_processes[0].pid, "child_pid": self.dead_processes[1].pid,
                "started": datetime.now().isoformat(timespec="seconds")}

    def test_register_adds_only_fixed_job_metadata_and_preserves_task_fields(self):
        before = self.record()
        token = self.register()
        after = self.record()
        parsed = jobs.jobs_for_record(after)
        self.assertEqual(set(parsed), {token})
        self.assertEqual(set(parsed[token]), {"owner_pid", "child_pid", "started"})
        self.assertEqual(parsed[token]["owner_pid"], os.getpid())
        self.assertIsNone(parsed[token]["child_pid"])
        for field, value in before.items():
            self.assertEqual(after[field], value, field)
        self.assertEqual(self.path.read_bytes().split(b"## Notes", 1)[1], self.original.encode().split(b"## Notes", 1)[1])

    def test_sixteen_commands_keep_union_and_seventeenth_is_rejected(self):
        tokens = [self.register() for _ in range(16)]
        before = self.snapshot()
        with self.assertRaises(sessions.ConflictError):
            self.register()
        self.assertEqual(set(jobs.jobs_for_record(self.record())), set(tokens))
        self.assertEqual(self.snapshot(), before)

    def test_reused_token_does_not_overwrite_a_registered_wrapper(self):
        token = self.register()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.register(token)
        self.assertEqual(self.snapshot(), before)

    def test_attach_and_unregister_wait_for_actual_child_exit(self):
        token = self.register()
        child = self.spawn()
        try:
            self.assertTrue(jobs.attach_child(self.root, "codex", self.identifier, token, child.pid))
            before = self.snapshot()
            with self.assertRaises(sessions.ConflictError):
                jobs.unregister_job(self.root, "codex", self.identifier, token)
            self.assertEqual(self.snapshot(), before)
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=20)
        self.assertTrue(jobs.unregister_job(self.root, "codex", self.identifier, token))
        self.assertEqual(jobs.jobs_for_record(self.record()), {})
        self.assertEqual(self.record()["status"], "active")
        self.assertEqual(self.record()["files"], '["src/app.py"]')
        before = self.snapshot()
        self.assertFalse(jobs.unregister_job(self.root, "codex", self.identifier, token))
        self.assertEqual(self.snapshot(), before)

    def test_repeated_attach_is_idempotent_but_different_child_is_rejected(self):
        token = self.register()
        child = self.spawn()
        try:
            jobs.attach_child(self.root, "codex", self.identifier, token, child.pid)
            before = self.snapshot()
            self.assertFalse(jobs.attach_child(self.root, "codex", self.identifier, token, child.pid))
            with self.assertRaises(ValueError):
                jobs.attach_child(self.root, "codex", self.identifier, token, self.dead_processes[0].pid)
            self.assertEqual(self.snapshot(), before)
        finally:
            child.terminate()
            child.wait(timeout=20)

    def test_real_dead_wrapper_and_child_allow_owner_to_clear_orphans(self):
        token = uuid.uuid4().hex
        self.change(running_jobs=json.dumps({token: self.dead_job()}))
        before = self.record()
        jobs.ensure_no_running_jobs(before)
        self.assertEqual(jobs.clear_dead_jobs(before), {})
        self.assertTrue(jobs.has_job_markers(before))
        new_token = self.register()
        self.assertEqual(set(jobs.jobs_for_record(self.record())), {new_token})
        self.assertEqual(self.record()["status"], "active")
        self.assertEqual(self.record()["files"], before["files"])

    def test_dead_wrapper_with_live_child_still_keeps_ownership(self):
        child = self.spawn()
        value = self.dead_job()
        value["child_pid"] = child.pid
        record = {"running_jobs": json.dumps({uuid.uuid4().hex: value})}
        try:
            with self.assertRaises(sessions.ConflictError):
                jobs.ensure_no_running_jobs(record)
            self.assertEqual(jobs.clear_dead_jobs(record), jobs.jobs_for_record(record))
        finally:
            child.terminate()
            child.wait(timeout=20)

    def test_takeover_rechecks_live_child_even_when_wrapper_and_heartbeat_are_old(self):
        child = self.spawn()
        value = self.dead_job()
        value["child_pid"] = child.pid
        self.change(running_jobs=json.dumps({uuid.uuid4().hex: value}))
        old = time.time() - 3600
        os.utime(self.path, (old, old))
        try:
            snapshot = sessions.takeover_snapshot([self.root], self.identifier)
            self.assertTrue(any("运行中命令" in reason for reason in sessions.takeover_blockers(snapshot)))
        finally:
            child.terminate()
            child.wait(timeout=20)
        self.assertEqual(sessions.takeover_blockers(sessions.takeover_snapshot([self.root], self.identifier)), [])

    @unittest.skipUnless(os.name == "nt", "Windows native process query regression")
    def test_windows_query_does_not_use_os_kill_zero_or_terminate_child(self):
        child = self.spawn()
        try:
            with mock.patch.object(jobs.os, "kill", side_effect=AssertionError("Windows kill(pid, 0) must never be used")):
                self.assertTrue(jobs.process_is_alive(child.pid))
                self.assertFalse(jobs.process_is_alive(self.dead_processes[0].pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=20)

    def test_unknown_child_prevents_unregister_without_changing_marker(self):
        token = self.register()
        jobs.attach_child(self.root, "codex", self.identifier, token, self.dead_processes[0].pid)
        before = self.snapshot()
        with mock.patch.object(jobs, "process_is_alive", return_value=None):
            with self.assertRaises(sessions.ConflictError):
                jobs.unregister_job(self.root, "codex", self.identifier, token)
        self.assertEqual(self.snapshot(), before)

    def test_other_wrapper_cannot_attach_or_unregister_its_token(self):
        token = uuid.uuid4().hex
        self.change(running_jobs=json.dumps({token: self.dead_job()}))
        before = self.snapshot()
        for operation in (lambda: jobs.attach_child(self.root, "codex", self.identifier, token, self.dead_processes[1].pid),
                          lambda: jobs.unregister_job(self.root, "codex", self.identifier, token)):
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(self.snapshot(), before)

    def test_wrong_owner_missing_session_and_invalid_token_do_no_record_write(self):
        before = self.snapshot()
        for agent, identifier, token in (("claude", self.identifier, uuid.uuid4().hex),
                                         ("codex", "codex-missing", uuid.uuid4().hex),
                                         ("codex", self.identifier, "secret argument cannot be a token")):
            with self.subTest(agent=agent, identifier=identifier), self.assertRaises(ValueError):
                jobs.register_job(self.root, agent, identifier, token, branch=self.branch)
        self.assertEqual(self.snapshot(), before)

    def test_register_requires_active_valid_scope_and_real_branch(self):
        for state, files in (("blocked", '["src/app.py"]'), ("standby", "[]"), ("done", '["src/app.py"]'), ("active", "[]")):
            with self.subTest(state=state, files=files):
                self.path.write_bytes(self.original.encode("utf-8"))
                self.change(status=state, files=files)
                before = self.snapshot()
                with self.assertRaises((ValueError, sessions.ConflictError)):
                    self.register()
                self.assertEqual(self.snapshot(), before)

    def test_register_never_ignores_active_human_control(self):
        question = self.state / "inbox/Q-jobs-pause.md"
        question.write_bytes(("# Pause\n- from: human\n- to: " + self.identifier + "\n- status: answered\n- action: pause\n\n## 问题\nStop this wrapper.\n").encode("utf-8"))
        before = self.snapshot()
        with self.assertRaises(sessions.ConflictError):
            self.register()
        self.assertEqual(self.snapshot(), before)

    def test_bom_crlf_and_body_bytes_are_preserved_when_registering_job(self):
        self.path.write_bytes(b"\xef\xbb\xbf" + self.original.replace("\n", "\r\n").encode("utf-8"))
        body = self.path.read_bytes().split(b"## Notes", 1)[1]
        self.register()
        written = self.path.read_bytes()
        self.assertTrue(written.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(written.split(b"## Notes", 1)[1], body)
        self.assertNotIn(b"\n", written.replace(b"\r\n", b""))

    def test_external_same_size_mtime_edit_during_registration_is_preserved(self):
        original_replace = activity.replace_session_record
        before = self.path.stat()
        changed = self.original.replace("Preserve notes.", "External notes.").encode("utf-8")
        self.assertEqual(len(changed), len(self.original.encode("utf-8")))

        def external_edit(root, identifier, snapshot, updated):
            self.path.write_bytes(changed)
            os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
            return original_replace(root, identifier, snapshot, updated)

        with mock.patch.object(activity, "replace_session_record", external_edit):
            with self.assertRaisesRegex(ValueError, "外部修改"):
                self.register()
        self.assertEqual(self.path.read_bytes(), changed)

    def test_directory_alias_is_rejected_even_when_target_stays_in_workspace(self):
        folder = self.path.parent
        saved = folder.with_name("saved-sessions")
        target = self.root / "shadow-sessions"
        target.mkdir()
        foreign = target / self.path.name
        foreign.write_bytes(self.original.encode("utf-8"))
        folder.rename(saved)
        directory_alias(self, folder, target)
        before = foreign.read_bytes()
        try:
            with self.assertRaisesRegex(ValueError, "symlink|junction"):
                self.register()
            self.assertEqual(foreign.read_bytes(), before)
        finally:
            folder.rmdir() if os.name == "nt" else folder.unlink()
            saved.rename(folder)
            foreign.unlink()
            target.rmdir()

    def test_duplicate_session_id_in_real_worktree_is_zero_write(self):
        linked = self.base / "linked"
        self.git_call("worktree", "add", "-b", "feature/jobs-linked", str(linked))
        state = linked / ".agents/sessions"
        state.mkdir(parents=True)
        duplicate = state / self.path.name
        duplicate.write_bytes(self.original.replace(self.branch, "feature/jobs-linked").encode("utf-8"))
        before, other = self.snapshot(), duplicate.read_bytes()
        try:
            with self.assertRaisesRegex(ValueError, "相同会话 ID"):
                self.register()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(duplicate.read_bytes(), other)
        finally:
            duplicate.unlink()
            state.rmdir()
            state.parent.rmdir()
            self.git_call("worktree", "remove", str(linked))

    def test_registration_wait_rechecks_cached_branch_before_write(self):
        original_lock = sessions.coordination_lock

        @contextlib.contextmanager
        def changed_branch(primary):
            with original_lock(primary):
                self.git_call("switch", "-c", "feature/jobs-changed")
                try:
                    yield
                finally:
                    self.git_call("switch", self.branch)

        before = self.snapshot()
        with mock.patch.object(sessions, "coordination_lock", changed_branch):
            with self.assertRaises(sessions.ConflictError):
                self.register()
        self.assertEqual(self.snapshot(), before)

    def test_blocked_and_branch_changed_cleanup_can_remove_finished_own_command(self):
        token = self.register()
        jobs.attach_child(self.root, "codex", self.identifier, token, self.dead_processes[0].pid)
        self.change(status="blocked")
        self.git_call("switch", "-c", "feature/jobs-cleanup")
        try:
            self.assertTrue(jobs.unregister_job(self.root, "codex", self.identifier, token))
            self.assertEqual(self.record()["status"], "blocked")
            self.assertEqual(self.record()["branch"], self.branch)
            self.assertEqual(self.record()["files"], '["src/app.py"]')
        finally:
            self.git_call("switch", self.branch)

    def test_parallel_registration_preserves_both_commands(self):
        original_lock = sessions.coordination_lock
        barrier = threading.Barrier(2)

        @contextlib.contextmanager
        def coordinated_entry(primary):
            barrier.wait(timeout=10)
            with original_lock(primary):
                yield

        with mock.patch.object(sessions, "coordination_lock", coordinated_entry), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.register)
            second = pool.submit(self.register)
            tokens = {first.result(timeout=30), second.result(timeout=30)}
        self.assertEqual(set(jobs.jobs_for_record(self.record())), tokens)

    def test_parallel_real_run_wrappers_keep_remaining_command_claimed(self):
        wrappers = []
        script = str(Path(sessions.__file__).resolve())
        events = self.base / "parallel-events"
        events.mkdir()
        releases = [events / "first-release", events / "second-release"]
        child_source = ("from pathlib import Path\nimport sys, time\n"
                        "release=Path(sys.argv[1])\ndeadline=time.monotonic()+40\n"
                        "while not release.exists() and time.monotonic()<deadline:\n time.sleep(.05)\n"
                        "sys.exit(0 if release.exists() else 3)\n")
        options = {"cwd": str(self.root), "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            for release in releases:
                wrappers.append(subprocess.Popen(
                    [sys.executable, "-B", script, "--root", str(self.root), "run", self.identifier,
                     "--agent", "codex", "--", sys.executable, "-c", child_source, str(release)],
                    **options))
            deadline = time.monotonic() + 20
            registered = {}
            while time.monotonic() < deadline:
                registered = jobs.jobs_for_record(self.record())
                if len(registered) == 2 and all(value["child_pid"] is not None for value in registered.values()):
                    break
                self.assertTrue(all(wrapper.poll() is None for wrapper in wrappers), "A wrapper exited before both jobs attached")
                time.sleep(0.05)
            self.assertEqual({value["owner_pid"] for value in registered.values()}, {wrapper.pid for wrapper in wrappers})
            self.assertTrue(all(value["child_pid"] is not None for value in registered.values()))
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                rc = sessions.main(["--root", str(self.root), "finish", self.identifier, "--agent", "codex",
                                    "--summary", "Premature parallel completion test", "--tests", "Two commands still running",
                                    "--standby", "--no-activity", "--json"])
            self.assertEqual(rc, 1, json.loads(output.getvalue()))
            self.assertEqual(self.record()["status"], "active")
            self.assertEqual(self.record()["files"], '["src/app.py"]')
            releases[0].write_text("release", encoding="utf-8")
            first_output, first_errors = wrappers[0].communicate(timeout=30)
            self.assertEqual(wrappers[0].returncode, 0, (first_output + first_errors).decode("utf-8", errors="replace"))
            remaining = jobs.jobs_for_record(self.record())
            self.assertEqual(len(remaining), 1)
            self.assertEqual(next(iter(remaining.values()))["owner_pid"], wrappers[1].pid)
            self.assertIsNone(wrappers[1].poll())
            releases[1].write_text("release", encoding="utf-8")
            second_output, second_errors = wrappers[1].communicate(timeout=30)
            self.assertEqual(wrappers[1].returncode, 0, (second_output + second_errors).decode("utf-8", errors="replace"))
            self.assertEqual(jobs.jobs_for_record(self.record()), {})
            self.assertEqual(self.record()["status"], "active", "A command result must not finish the task automatically")
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                rc = sessions.main(["--root", str(self.root), "finish", self.identifier, "--agent", "codex",
                                    "--summary", "Both isolated commands exited", "--tests", "Both returned zero",
                                    "--standby", "--no-activity", "--json"])
            self.assertEqual(rc, 0, json.loads(output.getvalue()))
            self.assertEqual(self.record()["status"], "standby")
            self.assertEqual(self.record()["files"], "[]")
        finally:
            # Each wrapper owns its children. Interrupt only these exact Popen
            # objects; never use process-name or workspace-wide termination.
            for wrapper in wrappers:
                if wrapper.poll() is None:
                    from agents_run import _stop_child
                    _stop_child(wrapper)
                wrapper.communicate(timeout=20)

    def test_live_registered_wrapper_prevents_actual_finish_standby(self):
        token = self.register()
        before = self.snapshot()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = sessions.main(["--root", str(self.root), "finish", self.identifier, "--agent", "codex",
                                "--summary", "Deliberately premature isolated completion", "--tests", "Command still pending",
                                "--standby", "--no-activity", "--json"])
        payload = json.loads(output.getvalue())
        self.assertEqual(rc, 1, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.snapshot(), before)
        self.assertIn(token, jobs.jobs_for_record(self.record()))


if __name__ == "__main__":
    unittest.main()
