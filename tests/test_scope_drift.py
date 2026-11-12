"""Workspace changes outside every declared claim must be visible, not guessed."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from selftest import KIT, PY, board, rmtree_readonly, run


class _ScopeDriftFixture(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-scope-drift-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "symbolic-ref", "HEAD", "refs/heads/feature/drift", cwd=self.root).returncode, 0)
        (self.root / ".git" / "info" / "exclude").write_text(".agents/\n", encoding="utf-8")
        for directory in ("sessions", "inbox", "worklog"):
            (self.root / ".agents" / directory).mkdir(parents=True)

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-scope-drift-"):
            raise RuntimeError("unsafe scope-drift fixture cleanup target")
        rmtree_readonly(self.base)

    def session(self, session_id="codex-drift-one", files='["src/app.py"]'):
        path = self.root / ".agents" / "sessions" / (session_id + ".md")
        path.write_text("# Session\n- agent: codex\n- branch: feature/drift\n"
                        "- task: Drift check\n- status: active\n"
                        f"- files: {files}\n- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n",
                        encoding="utf-8")
        return path

    def baseline(self, files, session_id="codex-drift-one"):
        path = self.root / ".agents" / "sessions" / (session_id + ".baseline.json")
        path.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
        return path

    def write(self, relative, body="x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def collect(self):
        return board.collect(datetime.now(), False, 15, root=self.root)


class TestScopeDrift(_ScopeDriftFixture):
    def test_unclaimed_change_is_reported_and_claimed_file_is_not(self):
        self.session()
        self.baseline(["src/app.py"])
        self.write("src/app.py")
        self.write("src/other.py")
        data = self.collect()
        record = data["sessions"][0]
        self.assertTrue(record["drift_available"])
        self.assertEqual(record["drift"], ["src/other.py"])
        self.assertEqual(record["drift_count"], 1)
        self.assertFalse(record["drift_truncated"])
        self.assertTrue(any("未登记改动" in warning for warning in data["warnings"]), data["warnings"])

    def test_preexisting_dirty_file_is_not_blamed(self):
        self.session()
        self.baseline(["src/other.py"])
        self.write("src/other.py")
        self.write("src/app.py")
        record = self.collect()["sessions"][0]
        self.assertEqual(record["drift"], [])

    def test_missing_baseline_is_not_guessed(self):
        self.session()
        self.write("src/other.py")
        record = self.collect()["sessions"][0]
        self.assertFalse(record["drift_available"])
        self.assertEqual(record["drift"], [])

    def test_directory_claim_covers_nested_file(self):
        self.session(files='["src/"]')
        self.baseline([])
        self.write("src/nested/other.py")
        record = self.collect()["sessions"][0]
        self.assertEqual(record["drift"], [])

    def test_dotfiles_are_not_stripped_to_their_basename(self):
        self.assertTrue(board.path_covered(".env", [".env"]))
        self.assertFalse(board.path_covered("env", [".env"]))
        self.assertTrue(board.path_covered("src/a.py", ["src/"]))
        self.assertFalse(board.path_covered("src/a.py", ["src"]))
        if os.name == "nt":
            self.assertTrue(board.path_covered("SRC/App.py", ["src/app.py"]))

    def test_drift_list_is_bounded_but_count_is_complete(self):
        self.session(files='["src/app.py"]')
        self.baseline([])
        for index in range(55):
            self.write("src/generated/file-%02d.py" % index)
        record = self.collect()["sessions"][0]
        self.assertEqual(len(record["drift"]), 50)
        self.assertEqual(record["drift_count"], 55)
        self.assertTrue(record["drift_truncated"])

    def test_start_records_the_existing_workspace_state(self):
        self.write("src/preexisting.py")
        result = run(PY, str(KIT / "tools" / "agents_session.py"), "--root", str(self.root),
                     "start", "--agent", "codex", "--task", "Drift task",
                     "--files", "src/app.py", "--json", "--no-activity", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertGreaterEqual(data.get("baseline_files", 0), 1)
        baseline = self.root / ".agents" / "sessions" / (data["id"] + ".baseline.json")
        self.assertTrue(baseline.is_file())
        payload = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertIn("src/preexisting.py", payload["files"])


class TestPrivateLayoutDrift(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-private-drift-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "symbolic-ref", "HEAD", "refs/heads/feature/private-drift", cwd=self.root).returncode, 0)
        (self.root / ".git" / "info" / "exclude").write_text(".multi-agent-collab/\n", encoding="utf-8")
        private = self.root / ".multi-agent-collab"
        for directory in ("sessions", "inbox", "worklog"):
            (private / directory).mkdir(parents=True)
        (private / "install.json").write_text(json.dumps({
            "owner": "multi-agent-collab", "layout": ".multi-agent-collab",
        }), encoding="utf-8")

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-private-drift-"):
            raise RuntimeError("unsafe private-layout fixture cleanup target")
        rmtree_readonly(self.base)

    def test_private_layout_uses_drift_without_crashing_on_state_paths(self):
        (self.root / ".multi-agent-collab" / "sessions" / "codex-private.md").write_text(
            "# Session\n- agent: codex\n- branch: feature/private-drift\n"
            "- task: Private layout\n- status: active\n- files: [\"src/app.py\"]\n"
            f"- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
        (self.root / ".multi-agent-collab" / "sessions" / "codex-private.baseline.json").write_text(
            json.dumps({"schema_version": 1, "files": []}), encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "other.py").write_text("x", encoding="utf-8")
        data = board.collect(datetime.now(), False, 15, root=self.root)
        record = data["sessions"][0]
        self.assertTrue(record["drift_available"])
        self.assertEqual(record["drift"], ["src/other.py"])


if __name__ == "__main__":
    unittest.main()
