"""Unknown session states cannot be rewritten or hide reservations."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from selftest import KIT, PY, board, rmtree_readonly, run
import agents_activity as activity
import agents_session as sessions


class TestStatusValidation(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-status-validation-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "symbolic-ref", "HEAD", "refs/heads/feature/validation", cwd=self.root).returncode, 0)
        self.state = self.root / ".agents"
        for directory in ("sessions", "inbox", "worklog"):
            (self.state / directory).mkdir(parents=True)
        (self.state / "coordination.lock").write_bytes(b"0")
        self.path = self.state / "sessions/codex-validation.md"
        self.write_record("active")

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-status-validation-"):
            raise RuntimeError("Refusing to remove a non-fixture directory")
        rmtree_readonly(self.base)

    def write_record(self, status, files='["src/app.py"]'):
        status_line = "" if status is None else "- status: " + status + "\n"
        self.path.write_text("# Session\n- agent: codex\n- branch: feature/validation\n"
                             "- task: Original task\n" + status_line + "- files: " + files + "\n"
                             f"- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n\n"
                             "## Original notes\nPreserve the original task.\n", encoding="utf-8")

    def snapshot(self):
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def command(self, *args):
        result = run(PY, str(KIT / "tools/agents_session.py"), "--root", str(self.root),
                     *args, "--json", "--no-activity", cwd=self.root)
        return result, json.loads(result.stdout)

    def test_unknown_state_refuses_every_mutation_without_side_effects(self):
        commands = (("heartbeat", "--note", "Do not normalize this"),
                    ("heartbeat", "--files", "src/other.py"),
                    ("resume", "--note", "Do not revive this"),
                    ("handoff", "--summary", "Do not create an inbox"),
                    ("finish", "--summary", "Do not close this", "--tests", "Not tested"),
                    ("finish", "--summary", "Do not release this", "--tests", "Not tested", "--standby"))
        for status in ("typo", "active unexpected", "done unexpected", "standby unexpected",
                       "done (\x00)", "active（\x00）", "", None):
            for command in commands:
                with self.subTest(status=status, command=command):
                    self.write_record(status)
                    before = self.snapshot()
                    result, data = self.command(command[0], self.path.stem, "--agent", "codex", *command[1:])
                    self.assertEqual(result.returncode, 2, data)
                    self.assertIn("状态", data.get("error", ""))
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_unknown_state_is_visible_and_blocks_another_writer(self):
        for status in ("typo", "done unexpected", "standby unexpected", "done (\x00)", None):
            with self.subTest(status=status):
                self.write_record(status, "[]" if status == "standby unexpected" else '["src/app.py"]')
                before = self.snapshot()
                result = run(PY, str(KIT / "tools/agents_status.py"), "--root", str(self.root),
                             "--json", "--no-activity", cwd=self.root)
                data = json.loads(result.stdout)
                self.assertEqual(result.returncode, 2, data)
                record = data["sessions"][0]
                self.assertTrue(record["status_error"])
                self.assertTrue(record["occupies"])
                self.assertFalse(record["closed"])
                self.assertFalse(record["standby"])
                self.assertTrue(any("状态" in warning for warning in data["warnings"]))
                result, data = self.command("start", "--agent", "claude", "--task", "Another task", "--files", "docs/")
                self.assertEqual(result.returncode, 2, data)
                self.assertEqual(self.snapshot(), before)

    def test_check_cannot_exclude_its_own_unknown_state(self):
        self.write_record("typo")
        before = self.snapshot()
        result, data = self.command("check", "--agent", "codex", "--session", self.path.stem, "--files", "src/app.py")
        self.assertEqual(result.returncode, 2, data)
        self.assertEqual(self.snapshot(), before)

    def test_activity_does_not_accept_a_terminal_prefix_as_a_valid_state(self):
        self.write_record("done unexpected")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "状态"):
            activity.touch_activity(self.root, "codex", self.path.stem, "agents_status", force=True)
        self.assertEqual(self.snapshot(), before)

    def test_supported_current_and_legacy_states_keep_their_meaning(self):
        for status in ("active", "blocked", "ACTIVE（执行中）", *sorted(board.CLOSED_WORDS)):
            with self.subTest(status=status):
                self.write_record(status)
                loaded = board.load_sessions([(self.root, True)], datetime.now(), 15)[0]
                self.assertFalse(loaded.get("status_error"))
                self.assertEqual(loaded["closed"], board.status_word(status) in board.CLOSED_WORDS)
                self.assertEqual(loaded["occupies"], not loaded["closed"])
        self.write_record("standby（等候新任务）", "[]")
        loaded = board.load_sessions([(self.root, True)], datetime.now(), 15)[0]
        self.assertFalse(loaded.get("status_error"))
        self.assertTrue(loaded["standby"])
        self.assertFalse(loaded["occupies"])
