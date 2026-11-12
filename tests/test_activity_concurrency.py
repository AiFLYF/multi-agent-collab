"""Legitimate activity and job updates must serialize their first stable read."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest import mock

from selftest import KIT, rmtree_readonly
import agents_activity as activity
import agents_jobs as jobs
import agents_session as sessions


class TestActivityConcurrency(unittest.TestCase):
    WAIT_SECONDS = 30

    @classmethod
    def setUpClass(cls):
        temporary = Path(tempfile.gettempdir()).resolve()
        if temporary == KIT.resolve() or KIT.resolve() in temporary.parents:
            raise RuntimeError("Concurrency fixtures must stay outside the source repository")
        cls.base = Path(tempfile.mkdtemp(prefix="mac-activity-concurrency-", dir=str(temporary))).resolve()
        cls.root = cls.base / "project"
        cls.root.mkdir()
        cls.branch = "feature/activity-concurrency"
        cls.git = sessions.board.resolve_git()
        cls.preserve_fixture = False
        for args in (("init", "-q", "-b", cls.branch), ("config", "user.name", "Concurrency Test"),
                     ("config", "user.email", "concurrency@example.invalid")):
            cls.git_call(*args)
        (cls.root / "README.md").write_text("# Concurrency fixture\n", encoding="utf-8")
        (cls.root / ".gitignore").write_text(".agents/\n", encoding="utf-8")
        cls.git_call("add", "README.md", ".gitignore")
        cls.git_call("commit", "-q", "-m", "test: isolated concurrency fixture")
        cls.state = cls.root / ".agents"
        for folder in ("sessions", "inbox", "worklog"):
            (cls.state / folder).mkdir(parents=True)
        (cls.state / "coordination.lock").write_bytes(b"0")

    @classmethod
    def git_call(cls, *args):
        result = subprocess.run([cls.git, *args], cwd=str(cls.root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        if cls.preserve_fixture:
            print("Concurrency failure evidence preserved in " + str(cls.base), file=sys.stderr)
            return
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-activity-concurrency-"):
            raise RuntimeError("Refusing concurrency cleanup outside its temporary workspace")
        rmtree_readonly(cls.base)

    def setUp(self):
        self.identifier = "codex-activity-concurrency"
        self.path = self.state / "sessions" / (self.identifier + ".md")
        self.now = datetime.now().replace(microsecond=0)
        self.body = b"\r\n## Notes\r\nPreserve these exact bytes.\r\n- status: done is a body example\r\n"
        header = ("# Session\r\n- agent: codex\r\n- branch: " + self.branch + "\r\n- workspace: " + str(self.root)
                  + "\r\n- task: Concurrent updates\r\n- status: active\r\n- files: [\"app.py\"]\r\n"
                  "- progress: Preserve progress\r\n- acknowledged_inbox: []\r\n- heartbeat: 2000-01-01 00:00\r\n")
        self.path.write_bytes(b"\xef\xbb\xbf" + header.encode("utf-8") + self.body)
        self.before = sessions.board.parse_kv(self.path)

    def _exercise(self, a_kind, b_kind):
        a_holds_lock = threading.Event()
        a_paused = threading.Event()
        a_ready_or_finished = threading.Event()
        b_attempts_lock = threading.Event()
        b_finished = threading.Event()
        diagnostic_lock = threading.Lock()
        timeline, outcomes, pause_observations = [], {}, []
        original_open = Path.open
        original_lock = sessions.coordination_lock
        started = time.monotonic()

        def event(label, **values):
            with diagnostic_lock:
                timeline.append(dict(event=label, seconds=round(time.monotonic() - started, 6), **values))

        @contextmanager
        def observed_lock(primary):
            role = threading.current_thread().name
            if role == "concurrency-B":
                event("B_attempts_real_lock")
                b_attempts_lock.set()
            with original_lock(primary):
                if role == "concurrency-A":
                    a_holds_lock.set()
                event(role + "_acquired_real_lock")
                try:
                    yield
                finally:
                    if role == "concurrency-A":
                        a_holds_lock.clear()
                    event(role + "_leaves_real_lock")

        class OneReadPause:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(inner, *args, **kwargs):
                payload = inner.stream.read(*args, **kwargs)
                inner.stream.close()
                held = a_holds_lock.is_set()
                pause_observations.append({"lock_held": held, "handle_closed": inner.stream.closed})
                event("A_one_read_closed_before_return", lock_held=held, bytes=len(payload))
                a_paused.set()
                a_ready_or_finished.set()
                # Windows replacement needs the old handle closed. This yields
                # at the real close -> post-read lstat window, exactly once.
                # A fixed writer holds the native lock: let it continue as
                # soon as B attempts entry, rather than waiting for B to finish.
                # On the old unlocked path B can finish, exposing the real race.
                prerequisite = b_attempts_lock if held else b_finished
                if not prerequisite.wait(self.WAIT_SECONDS):
                    raise TimeoutError("B did not reach the controlled concurrency gate")
                event("A_one_read_resumed")
                return payload

        def controlled_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if (path == self.path and mode == "rb" and threading.current_thread().name == "concurrency-A"
                    and not pause_observations):
                return OneReadPause(stream)
            return stream

        def call(role, kind):
            event(role + "_call_started", kind=kind)
            try:
                if kind == "register":
                    token = ("a" if role == "A" else "b") * 32
                    result = jobs.register_job(self.root, "codex", self.identifier, token, branch=self.branch)
                else:
                    result = activity.touch_activity(self.root, "codex", self.identifier, "agents_session.run",
                                                     now=self.now, force=True, branch=self.branch)
                outcome = {"ok": True, "result": result}
            except BaseException as error:
                outcome = {"ok": False, "type": type(error).__name__, "message": str(error),
                           "traceback": traceback.format_exc()}
            with diagnostic_lock:
                outcomes[role] = outcome
            event(role + "_call_finished", outcome=outcome)
            if role == "B":
                b_finished.set()
            else:
                a_ready_or_finished.set()

        a = threading.Thread(target=call, args=("A", a_kind), name="concurrency-A", daemon=True)
        b = threading.Thread(target=call, args=("B", b_kind), name="concurrency-B", daemon=True)
        evidence_path = self.base / (self._testMethodName + ".json")

        def evidence():
            return {"python": sys.version, "executable": sys.executable, "fixture": str(self.root),
                    "A_kind": a_kind, "B_kind": b_kind, "outcomes": outcomes,
                    "pause_observations": pause_observations, "timeline": timeline,
                    "A_alive": a.is_alive(), "B_alive": b.is_alive(), "evidence": str(evidence_path)}

        try:
            with mock.patch.object(sessions, "coordination_lock", observed_lock), \
                    mock.patch.object(Path, "open", controlled_open):
                a.start()
                try:
                    ready = a_ready_or_finished.wait(self.WAIT_SECONDS)
                    if ready and a_paused.is_set():
                        b.start()
                    a.join(self.WAIT_SECONDS)
                    if b.ident is not None:
                        b.join(self.WAIT_SECONDS)
                finally:
                    # Wake a failing gate before removing instrumentation.
                    b_attempts_lock.set()
                    b_finished.set()
                    a.join(self.WAIT_SECONDS)
                    if b.ident is not None:
                        b.join(self.WAIT_SECONDS)
            snapshot = evidence()
            evidence_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            diagnostic = json.dumps(snapshot, ensure_ascii=True, indent=2)
            self.assertFalse(a.is_alive() or b.is_alive(), diagnostic)
            self.assertEqual(pause_observations, [{"lock_held": True, "handle_closed": True}], diagnostic)
            self.assertTrue(outcomes.get("A", {}).get("ok"), diagnostic)
            self.assertTrue(outcomes.get("B", {}).get("ok"), diagnostic)
            activity_role = "A" if a_kind == "touch" else "B"
            job_role = "A" if a_kind == "register" else "B"
            token = ("a" if job_role == "A" else "b") * 32
            self.assertTrue(outcomes[activity_role]["result"]["updated"], diagnostic)
            self.assertEqual(outcomes[job_role]["result"], token, diagnostic)
            fields = sessions.board.parse_kv(self.path)
            for field in ("agent", "branch", "workspace", "task", "status", "files", "progress", "acknowledged_inbox"):
                self.assertEqual(fields.get(field), self.before.get(field), diagnostic)
            self.assertEqual(fields.get("last_activity"), self.now.isoformat(timespec="seconds"), diagnostic)
            self.assertEqual(fields.get("heartbeat"), self.now.strftime("%Y-%m-%d %H:%M"), diagnostic)
            self.assertEqual(fields.get("last_command"), "agents_session.run", diagnostic)
            markers = jobs.jobs_for_record(fields)
            self.assertEqual(set(markers), {token}, diagnostic)
            self.assertEqual(markers[token]["owner_pid"], os.getpid(), diagnostic)
            self.assertIsNone(markers[token]["child_pid"], diagnostic)
            final_bytes = self.path.read_bytes()
            self.assertTrue(final_bytes.startswith(b"\xef\xbb\xbf"), diagnostic)
            self.assertTrue(final_bytes.endswith(self.body), diagnostic)
            self.assertNotIn(b"\n", final_bytes.replace(b"\r\n", b""), diagnostic)
        except BaseException:
            type(self).preserve_fixture = True
            evidence_path.write_text(json.dumps(evidence(), ensure_ascii=False, indent=2), encoding="utf-8")
            raise

    def test_register_first_read_serializes_with_forced_activity(self):
        self._exercise("register", "touch")

    def test_forced_activity_first_read_serializes_with_registration(self):
        self._exercise("touch", "register")

    def _exercise_windows_reader(self, release_after_denial):
        original = self.path.read_bytes(), self.path.stat().st_mtime_ns
        denied = threading.Event()
        errors, outcome = [], {}
        original_replace = activity.os.replace

        def observed_replace(*args, **kwargs):
            try:
                return original_replace(*args, **kwargs)
            except PermissionError as error:
                errors.append(error.winerror)
                denied.set()
                raise

        def write_activity():
            try:
                outcome["result"] = activity.touch_activity(
                    self.root, "codex", self.identifier, "agents_session.run",
                    now=self.now, force=True, branch=self.branch)
            except BaseException as error:
                outcome.update(error_type=type(error).__name__, error=str(error),
                               winerror=getattr(error, "winerror", None), traceback=traceback.format_exc())

        writer = threading.Thread(target=write_activity, daemon=True)
        reader = self.path.open("rb")
        try:
            with mock.patch.object(activity.os, "replace", observed_replace):
                writer.start()
                self.assertTrue(denied.wait(self.WAIT_SECONDS), outcome)
                self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), original)
                if release_after_denial:
                    reader.close()
                writer.join(self.WAIT_SECONDS)
                self.assertFalse(writer.is_alive(), "Activity retry did not terminate while the reader remained open")
        finally:
            reader.close()
            writer.join(self.WAIT_SECONDS)
            if writer.is_alive():
                type(self).preserve_fixture = True
        diagnostic = json.dumps({"errors": errors, "outcome": outcome}, ensure_ascii=True)
        self.assertTrue(errors, diagnostic)
        self.assertTrue(all(error in (5, 32) for error in errors), diagnostic)
        self.assertEqual(list(self.path.parent.glob(".*.activity-*.tmp")), [], diagnostic)
        if release_after_denial:
            self.assertTrue(outcome.get("result", {}).get("updated"), diagnostic)
            fields = sessions.board.parse_kv(self.path)
            for field in ("agent", "branch", "workspace", "task", "status", "files", "progress", "acknowledged_inbox"):
                self.assertEqual(fields.get(field), self.before.get(field), diagnostic)
            self.assertEqual(fields["last_activity"], self.now.isoformat(timespec="seconds"), diagnostic)
            final = self.path.read_bytes()
            self.assertTrue(final.startswith(b"\xef\xbb\xbf"), diagnostic)
            self.assertTrue(final.endswith(self.body), diagnostic)
            self.assertNotIn(b"\n", final.replace(b"\r\n", b""), diagnostic)
        else:
            self.assertEqual(outcome.get("error_type"), "PermissionError", diagnostic)
            self.assertIn(outcome.get("winerror"), (5, 32), diagnostic)
            self.assertNotIn("result", outcome, diagnostic)
            self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), original, diagnostic)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_short_reader_allows_safe_activity_retry(self):
        self._exercise_windows_reader(True)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_persistent_reader_is_a_bounded_zero_write_error(self):
        self._exercise_windows_reader(False)


if __name__ == "__main__":
    unittest.main()
