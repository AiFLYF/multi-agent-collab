"""Real business calls identify only their caller and keep read-only contracts."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import subprocess
import threading
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from selftest import KIT, PY, inst, run, rmtree_readonly
import agents_activity as activity
import agents_messages as messages
import agents_notifications as notifications
import agents_session as sessions
import agents_status as board


class TestActivityIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="mac-activity-integration-")).resolve()
        cls.root = cls.base / "project"
        cls.root.mkdir()
        cls.git = inst.resolve_git()
        cls.branch = "feature/activity-business"
        for arguments in (("init", "-q", "-b", "main"), ("config", "user.name", "Activity Integration"),
                          ("config", "user.email", "activity-integration@example.invalid")):
            cls.git_command(*arguments)
        (cls.root / "README.md").write_text("# Actual installed test project\n", encoding="utf-8")
        cls.git_command("add", "README.md")
        cls.git_command("commit", "-q", "-m", "test: activity project fixture")
        cls.git_command("switch", "-c", cls.branch)
        result = run(PY, "-B", str(KIT / "install.py"), "install", str(cls.root),
                     "--platforms", "codex", "--workflow", "github-flow", cwd=KIT)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.state = cls.root / ".multi-agent-collab"
        cls.tools = cls.state / "tools"
        result = run(PY, "-B", str(cls.tools / "agents_session.py"), "--root", str(cls.root),
                     "start", "--agent", "codex", "--task", "Actual activity test", "--files", "src/app.py", "--json", cwd=cls.root)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        initial = json.loads(result.stdout)
        cls.identifier = initial["id"]
        cls.own_path = Path(initial["file"])
        cls.initial_record = cls.own_path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-activity-integration-"):
            raise RuntimeError("Refusing integration fixture cleanup outside its temporary workspace")
        rmtree_readonly(cls.base)

    @classmethod
    def git_command(cls, *arguments):
        result = subprocess.run([cls.git, *arguments], cwd=str(cls.root), capture_output=True,
                                encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.own_path.write_bytes(self.initial_record)
        self.change_record(heartbeat="2000-01-01 00:00", last_activity="2000-01-01T00:00:00", last_command="agents_session.start")
        for path in (self.state / "inbox").glob("Q-activity-*.md"):
            path.unlink()
        for path in (self.state / "sessions").glob("*activity-other*.md"):
            path.unlink()
        receipts = self.state / "notifications"
        if receipts.exists():
            if receipts.resolve().parent != self.state.resolve() or receipts.is_symlink():
                raise RuntimeError("Unexpected aliased receipt fixture")
            rmtree_readonly(receipts)

    def change_record(self, **fields):
        source = self.own_path.read_text(encoding="utf-8")
        self.own_path.write_bytes(sessions.put_fields(source, fields).encode("utf-8"))

    def record(self, path=None):
        return board.parse_kv(path or self.own_path)

    def snapshot(self):
        # Git's own administrative reads are separate from installed product
        # and business files; every other byte and mtime participates.
        return {path.relative_to(self.root).as_posix():
                ("directory", path.stat().st_mtime_ns) if path.is_dir() else
                ("file", path.stat().st_mtime_ns, path.read_bytes())
                for path in self.root.rglob("*") if ".git" not in path.relative_to(self.root).parts}

    def identity(self):
        return ["--agent", "codex", "--session", self.identifier]

    def installed(self, tool, *arguments):
        return run(PY, "-B", str(self.tools / (tool + ".py")), *arguments, cwd=self.root)

    def installer(self, command, *arguments):
        return run(PY, "-B", str(KIT / "install.py"), command, str(self.root), *arguments, cwd=self.root)

    def payload(self, result):
        self.assertTrue(result.stdout.strip(), result.stderr)
        parsed = json.loads(result.stdout)  # rejects concatenated JSON replies
        self.assertIsInstance(parsed, dict)
        return parsed

    def assert_presence(self, command=None):
        record = self.record()
        actual = board.parse_time(record["last_activity"])
        self.assertIsNotNone(actual)
        self.assertLess(abs((datetime.now() - actual).total_seconds()), 60)
        if command:
            self.assertEqual(record["last_command"], command)
        return record

    def inbox(self, name="message", sender="claude", target=None, body="Please verify the actual business call.", action="message"):
        path = self.state / "inbox" / ("Q-activity-" + name + ".md")
        path.write_bytes(("# Activity integration message\n- from: " + sender + "\n- to: " + (target or self.identifier)
                          + "\n- status: open\n- action: " + action + "\n- created: " + datetime.now().strftime("%Y-%m-%d %H:%M")
                          + "\n\n## 问题\n\n" + body + "\n\n## 回复\n\n").encode("utf-8"))
        return path

    def other_session(self, status="standby", agent="claude", identifier="claude-activity-other"):
        path = self.state / "sessions" / (identifier + ".md")
        path.write_bytes(("# Other real record\n- agent: " + agent + "\n- branch: " + self.branch
                          + "\n- task: Other caller\n- status: " + status
                          + "\n- files: " + ("[]" if status == "standby" else '["docs/guide.md"]')
                          + "\n- heartbeat: " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\nOther notes remain.\n").encode("utf-8"))
        return path

    def test_installed_status_refreshes_own_stale_record_without_success_noise(self):
        result = self.installed("agents_status", "--root", str(self.root), "--json", *self.identity())
        payload = self.payload(result)
        self.assertEqual(result.returncode, 0, payload)
        self.assertNotIn("activity_warning", payload)
        self.assertEqual(result.stderr, "")
        own = next(record for record in payload["sessions"] if record["id"] == self.identifier)
        self.assertFalse(own["stale"])
        self.assertEqual(own["status"], "active")
        self.assert_presence("agents_status")

    def test_installed_message_list_and_read_refresh_presence_without_marking_read(self):
        message = self.inbox()
        original = message.read_bytes()
        for command in ("list", "read"):
            with self.subTest(command=command):
                self.change_record(heartbeat="2000-01-01 00:00", last_activity="2000-01-01T00:00:00")
                result = self.installed("agents_messages", command, "--root", str(self.root), "--json", *self.identity())
                payload = self.payload(result)
                self.assertEqual(result.returncode, 0, payload)
                self.assertNotIn("activity_warning", payload)
                self.assertEqual(payload["marked_read"], [])
                self.assertTrue(payload["items"][0]["unread"])
                self.assert_presence("agents_messages." + command)
                self.assertEqual(message.read_bytes(), original)

    def test_installed_session_check_preserves_failed_gate_and_only_updates_actor(self):
        question = self.inbox(sender="human", body="Please include the additional test before editing.")
        other = self.other_session()
        other_before = other.read_bytes(), other.stat().st_mtime_ns
        original_question = question.read_bytes()
        arguments = ("--root", str(self.root), "check", "--json", "--files", "src/app.py", *self.identity())
        baseline = self.installed("agents_session", *arguments, "--no-activity")
        baseline_payload = self.payload(baseline)
        result = self.installed("agents_session", *arguments)
        payload = self.payload(result)
        self.assertEqual(baseline.returncode, 1, baseline_payload)
        self.assertEqual(result.returncode, baseline.returncode, payload)
        self.assertEqual(payload["human_inbox"], baseline_payload["human_inbox"])
        self.assert_presence("agents_session.check")
        self.assertEqual((other.read_bytes(), other.stat().st_mtime_ns), other_before)
        self.assertEqual(question.read_bytes(), original_question)
        self.assertEqual(self.record()["acknowledged_inbox"], "[]")

    def test_installer_doctor_identifies_actor_without_activity_success_noise(self):
        result = self.installer("doctor", *self.identity())
        self.assertTrue(result.stdout.strip(), result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assert_presence("install.doctor")

    def test_real_installed_update_records_actor_presence(self):
        result = self.installed("agents_update", "--root", str(self.root), "--json", *self.identity())
        payload = self.payload(result)
        self.assertEqual(result.returncode, 0, payload)
        self.assertNotIn("activity_warning", payload)
        self.assert_presence("agents_update")

    def test_direct_source_update_check_with_identity_is_strictly_zero_write(self):
        before = self.snapshot()
        result = self.installer("update", "--check", "--json", *self.identity())
        self.payload(result)
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_installed_update_check_with_identity_is_strictly_zero_write(self):
        before = self.snapshot()
        result = self.installed("agents_update", "--root", str(self.root), "--check", "--json", *self.identity())
        self.payload(result)
        self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_update_check_under_pause_remains_read_only_and_allowed(self):
        self.inbox(name="pause", sender="human", action="pause", body="Pause writes, but permit examining the update.")
        for installed in (False, True):
            with self.subTest(installed=installed):
                before = self.snapshot()
                result = (self.installed("agents_update", "--root", str(self.root), "--check", "--json", *self.identity())
                          if installed else self.installer("update", "--check", "--json", *self.identity()))
                self.payload(result)
                self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_preview_commands_preserve_every_installed_byte_and_mtime(self):
        operations = [("install", []), ("sync", ["--platforms", "codex"]), ("uninstall", []), ("update", [])]
        for command, options in operations:
            with self.subTest(command=command):
                before = self.snapshot()
                result = self.installer(command, "--dry-run", *options, *self.identity())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_updater_preview_remains_zero_write_with_identity(self):
        before = self.snapshot()
        result = self.installed("agents_update", "--root", str(self.root), "--dry-run", "--json", *self.identity())
        self.payload(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_observers_with_no_session_never_refresh_somebody_else(self):
        for identity in ([], ["--agent", "codex"], ["--session", self.identifier]):
            for tool, arguments in (("agents_status", ["--root", str(self.root), "--json"]),
                                    ("agents_messages", ["list", "--root", str(self.root), "--json"]),
                                    ("agents_session", ["--root", str(self.root), "check", "--json"])):
                with self.subTest(identity=identity, tool=tool):
                    before = self.snapshot()
                    result = self.installed(tool, *arguments, *identity)
                    self.payload(result)
                    self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_installer_observer_with_only_platform_is_zero_write(self):
        before = self.snapshot()
        result = self.installer("doctor", "--agent", "codex")
        self.assertTrue(result.stdout.strip(), result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_no_activity_suppresses_presence_in_all_read_only_business_tools(self):
        operations = [("agents_status", ["--root", str(self.root), "--json"]),
                      ("agents_messages", ["read", "--root", str(self.root), "--json"]),
                      ("agents_session", ["--root", str(self.root), "check", "--json"])]
        for tool, arguments in operations:
            with self.subTest(tool=tool):
                before = self.snapshot()
                result = self.installed(tool, *arguments, *self.identity(), "--no-activity")
                self.payload(result)
                self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)
        before = self.snapshot()
        result = self.installer("doctor", *self.identity(), "--no-activity")
        self.assertTrue(result.stdout.strip(), result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_blocked_business_call_updates_liveness_without_resuming_or_ack(self):
        self.change_record(status="blocked")
        self.inbox(name="pause", sender="human", action="pause", body="Pause this actor; keep its occupancy.")
        before_files = self.record()["files"]
        result = self.installed("agents_messages", "list", "--root", str(self.root), "--json", *self.identity())
        payload = self.payload(result)
        self.assertEqual(result.returncode, 0, payload)
        self.assert_presence("agents_messages.list")
        self.assertEqual(self.record()["status"], "blocked")
        self.assertEqual(self.record()["files"], before_files)
        self.assertEqual(self.record()["acknowledged_inbox"], "[]")

    def test_standby_caller_can_receive_messages_while_remaining_unclaimed(self):
        self.change_record(status="standby", files="[]", task="")
        question = self.inbox(body="A new task is available; inspect it before activation.")
        original_question = question.read_bytes()
        result = self.installed("agents_status", "--root", str(self.root), "--json", *self.identity())
        payload = self.payload(result)
        self.assertEqual(result.returncode, 1, payload)
        self.assertEqual(payload["gate_rc"], 1)
        own = next(record for record in payload["sessions"] if record["id"] == self.identifier)
        self.assertTrue(own["standby"])
        self.assertFalse(own["occupies"])
        self.assertEqual(own["files"], [])
        self.assertGreater(payload["notifications"]["unread_count"], 0)
        self.assert_presence("agents_status")
        self.assertEqual(self.record()["status"], "standby")
        self.assertEqual(question.read_bytes(), original_question)

    def test_terminal_business_queries_never_reactivate_or_rewrite_record(self):
        for status in ("done", "closed"):
            self.change_record(status=status)
            for tool, arguments in (("agents_status", ["--root", str(self.root), "--json"]),
                                    ("agents_messages", ["list", "--root", str(self.root), "--json"])):
                with self.subTest(status=status, tool=tool):
                    before = self.snapshot()
                    result = self.installed(tool, *arguments, *self.identity())
                    self.payload(result)
                    self.assertIn(result.returncode, (0, 1), result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_wrong_owner_cli_rejects_identity_without_touching_any_record(self):
        before = self.snapshot()
        result = self.installed("agents_messages", "list", "--root", str(self.root), "--json",
                                "--agent", "claude", "--session", self.identifier)
        payload = self.payload(result)
        self.assertEqual(result.returncode, 2, payload)
        self.assertEqual(self.snapshot(), before)

    def test_activity_branch_failure_preserves_message_business_code_and_single_json(self):
        self.git_command("switch", "-c", "feature/activity-warning")
        before = self.snapshot()
        try:
            arguments = ("list", "--root", str(self.root), "--json", *self.identity())
            baseline = self.installed("agents_messages", *arguments, "--no-activity")
            baseline_payload = self.payload(baseline)
            result = self.installed("agents_messages", *arguments)
            payload = self.payload(result)
            self.assertEqual(result.returncode, baseline.returncode)
            self.assertEqual(payload["items"], baseline_payload["items"])
            self.assertIn("activity_warning", payload)
            self.assertEqual(self.snapshot(), before)
        finally:
            self.git_command("switch", self.branch)

    def test_takeover_inspection_does_not_refresh_the_target_as_if_it_were_actor(self):
        target = self.other_session(status="blocked", agent="codex", identifier="codex-activity-other-target")
        target_before = target.read_bytes(), target.stat().st_mtime_ns
        arguments = ("--root", str(self.root), "takeover-inspect", target.stem, "--agent", "codex", "--json")
        baseline = self.snapshot()
        result = self.installed("agents_session", *arguments)
        self.payload(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), baseline)
        result = self.installed("agents_session", *arguments, "--actor-session", self.identifier)
        self.payload(result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_presence("agents_session.takeover-inspect")
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), target_before)

    def test_web_data_polling_is_observation_and_never_counts_as_agent_activity(self):
        server = board.make_server(self.root, port=0, directives=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        before = self.snapshot()
        try:
            for _ in range(2):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                try:
                    connection.request("GET", "/data?prs=0")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIsInstance(json.loads(response.read().decode("utf-8")), dict)
                finally:
                    connection.close()
            self.assertEqual(self.snapshot(), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)

    def test_status_help_version_and_selfcheck_do_not_refresh_explicit_identity(self):
        for option in ("--help", "--version", "--selfcheck"):
            with self.subTest(option=option):
                before = self.snapshot()
                result = self.installed("agents_status", "--root", str(self.root), *self.identity(), option)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_no_activity_and_mark_read_are_separate_explicit_operations(self):
        self.inbox()
        own_before = self.own_path.read_bytes(), self.own_path.stat().st_mtime_ns
        result = self.installed("agents_messages", "read", "--root", str(self.root), "--json", *self.identity(), "--mark-read", "--no-activity")
        payload = self.payload(result)
        self.assertEqual(result.returncode, 0, payload)
        self.assertTrue(payload["marked_read"])
        self.assertEqual((self.own_path.read_bytes(), self.own_path.stat().st_mtime_ns), own_before)
        _, items = notifications.scan_messages(self.root, "codex", self.identifier, roots=[self.root])
        self.assertFalse(items[0]["unread"])

    def test_failed_stdout_keeps_receipt_contract_even_when_presence_is_recorded(self):
        self.inbox(name="previous")
        result = self.installed("agents_messages", "read", "--root", str(self.root), "--json", *self.identity(), "--mark-read", "--no-activity")
        self.assertEqual(result.returncode, 0, self.payload(result))
        identity = notifications.notification_identity(self.root, "codex", self.identifier)
        receipt = notifications._receipt_path(self.root, identity)
        previous_receipt = receipt.read_bytes()
        self.inbox(name="undelivered", body="This message must remain unread after failed output.")

        class FailFlush(io.StringIO):
            def flush(self):
                raise OSError("controlled output flush failure")

        output, errors = FailFlush(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = messages.main(["read", "--root", str(self.root), "--json", *self.identity(), "--mark-read"])
        self.assertEqual(rc, 2, errors.getvalue())
        self.assertEqual(receipt.read_bytes(), previous_receipt)
        _, items = notifications.scan_messages(self.root, "codex", self.identifier, roots=[self.root])
        undelivered = next(item for item in items if item["id"].endswith("undelivered.md"))
        self.assertTrue(undelivered["unread"])
        self.assert_presence("agents_messages.read")

    def test_absent_stdout_initialization_remains_zero_write(self):
        self.inbox()
        before = self.snapshot()
        errors = io.StringIO()
        with contextlib.redirect_stdout(None), contextlib.redirect_stderr(errors):
            rc = messages.main(["read", "--root", str(self.root), "--json", *self.identity(), "--mark-read"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
