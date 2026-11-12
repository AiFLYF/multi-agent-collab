"""Optional drift checks must stay conservative, bounded and tied to one task."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from selftest import board
import agents_session as sessions
import test_scope_drift as fixtures


class TestDriftSafety(fixtures._ScopeDriftFixture):
    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = sessions.main(["--root", str(self.root), *args, "--json", "--no-activity"])
        return rc, json.loads(output.getvalue())

    def test_wrong_json_type_disables_detail_without_crashing(self):
        self.session()
        path = self.baseline([])
        for raw in ("[]", "null", '"wrong type"', '{"schema_version":99,"files":[]}'):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                data = self.collect()
                record = data["sessions"][0]
                self.assertFalse(record["drift_available"])
                self.assertTrue(record["occupies"])
                self.assertEqual(record["files"], ["src/app.py"])

    def test_failed_git_scan_cannot_claim_no_drift(self):
        self.session()
        self.baseline([])
        self.write("src/outside.py")
        with mock.patch.object(board, "resolve_git", return_value=None):
            record = self.collect()["sessions"][0]
        self.assertFalse(record["drift_available"])
        self.assertTrue(record["occupies"])

    def test_existing_dirty_path_modified_again_is_detected(self):
        path = self.write("src/preexisting.py", "Existing edit.")
        rc, data = self.invoke("start", "--agent", "codex", "--task", "Review scope", "--files", "src/app.py")
        self.assertEqual(rc, 0, data)
        path.write_text("New changes after this task started.", encoding="utf-8")
        record = self.collect()["sessions"][0]
        self.assertIn("src/preexisting.py", record["drift"])

    def test_failed_activate_snapshot_cannot_reuse_old_task_baseline(self):
        rc, started = self.invoke("start", "--agent", "codex", "--task", "First task", "--files", "src/app.py")
        self.assertEqual(rc, 0, started)
        identifier = started["id"]
        rc, data = self.invoke("finish", identifier, "--agent", "codex", "--summary", "Fixture complete",
                               "--tests", "Only lifecycle verification", "--standby")
        self.assertEqual(rc, 0, data)
        self.write("src/new-before-second-task.py")
        original_write = board._write_lf_atomic

        def baseline_failure(path, content):
            if Path(path).name.endswith(".baseline.json"):
                raise OSError("Injected baseline write failure")
            return original_write(path, content)

        with mock.patch.object(board, "_write_lf_atomic", baseline_failure):
            rc, data = self.invoke("activate", identifier, "--agent", "codex", "--task", "Second task",
                                   "--files", "src/app.py")
        self.assertEqual(rc, 0, data)
        self.assertFalse(self.collect()["sessions"][0]["drift_available"])


if __name__ == "__main__":
    unittest.main()
