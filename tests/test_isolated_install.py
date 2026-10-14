"""Regression tests for coexistence with project-owned files and private state."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from selftest import KIT, PY, install, run, rmtree_readonly


class TestIsolatedInstall(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mac-isolated-"))
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)

    def tearDown(self):
        rmtree_readonly(self.root)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_existing_project_content_and_frontmatter_survive(self):
        original = b"\xef\xbb\xbf# Project rules\r\nRun all project tests.\r\n\r\n"
        (self.root / "AGENTS.md").write_bytes(original)
        (self.root / ".gitignore").write_bytes(b"# Existing ignores\r\ndist/\r\n")
        for name in (".agents/skills/custom.md", ".agents/policy.json", "tools/agents_status.py", ".workbuddy/notes.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Project-owned content\n", encoding="utf-8")
        before = self.snapshot()
        result = install(self.root, "--platforms", "codex,cursor,claude")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name, data in before.items():
            if name in ("AGENTS.md", ".gitignore"):
                self.assertTrue((self.root / name).read_bytes().startswith(data), name)
            else:
                self.assertEqual((self.root / name).read_bytes(), data, name)
                self.assertEqual(run("git", "check-ignore", "-q", name, cwd=self.root).returncode, 1)
        cursor = self.root / ".cursor/rules/multi-agent-collab.mdc"
        self.assertTrue(cursor.read_text(encoding="utf-8").startswith("---\n"))
        self.assertEqual(run("git", "check-ignore", "-q", "AGENTS.md", cwd=self.root).returncode, 1)
        import agents_session
        self.assertEqual(agents_session.normalize_files(self.root, [".agents/skills/custom.md"]), [".agents/skills/custom.md"])
        first = self.snapshot()
        self.assertEqual(install(self.root).returncode, 0)
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(run("git", "check-ignore", "-q", ".multi-agent-collab/sessions/private.md", cwd=self.root).returncode, 0)
        # Unselected existing platform paths are not hidden by a generic ignore list.
        self.assertEqual(run("git", "check-ignore", "-q", "GEMINI.md", cwd=self.root).returncode, 1)

    def test_foreign_directory_and_damaged_markers_are_atomic(self):
        foreign = self.root / ".multi-agent-collab"
        foreign.mkdir()
        (foreign / "mine.txt").write_text("Keep this", encoding="utf-8")
        before = self.snapshot()
        result = install(self.root, "--platforms", "codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法确认属于本套件", result.stderr)
        self.assertEqual(self.snapshot(), before)
        # Remove only this test's own two paths to exercise another independent conflict.
        (foreign / "mine.txt").unlink()
        foreign.rmdir()
        for name, text in (("AGENTS.md", "<!-- >>> multi-agent-collab:entry >>>\n"),
                           (".gitignore", "# >>> multi-agent-collab:private >>>\n")):
            with self.subTest(name=name):
                path = self.root / name
                path.write_text(text, encoding="utf-8")
                before = self.snapshot()
                self.assertEqual(install(self.root, "--platforms", "codex").returncode, 2)
                self.assertEqual(self.snapshot(), before)
                path.unlink()

    def test_modified_tool_not_blessed_by_sync_and_preview_is_read_only(self):
        self.assertEqual(install(self.root, "--platforms", "codex").returncode, 0)
        tool = self.root / ".multi-agent-collab/tools/agents_status.py"
        original = tool.read_bytes()
        tool.write_bytes(original + b"\n# Local customization\n")
        result = run(PY, str(KIT / "install.py"), "sync", str(self.root), "--platforms", "claude", cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        meta = json.loads((self.root / ".multi-agent-collab/install.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["managed_hashes"][".multi-agent-collab/tools/agents_status.py"], hashlib.sha256(original).hexdigest())
        before = self.snapshot()
        preview = install(self.root, "--dry-run")
        self.assertEqual(preview.returncode, 2)
        self.assertIn("(planned)", preview.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(install(self.root).returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_recorded_legacy_layout_retains_tools_ignore_during_sync(self):
        legacy = self.root / ".agents"
        legacy.mkdir()
        (legacy / "install.json").write_text(json.dumps({"kit_version": "1.6.0", "source_dir": str(KIT), "platforms": ["codex"]}), encoding="utf-8")
        result = install(self.root, "--platforms", "codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / ".multi-agent-collab").exists())
        result = run(PY, str(KIT / "install.py"), "sync", str(self.root), "--platforms", "cursor", cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in ("tools/agents_status.py", "tools/agents_session.py", "tools/agents_policy.py", "tools/agents_layout.py"):
            self.assertTrue((self.root / path).is_file())
            self.assertEqual(run("git", "check-ignore", "-q", path, cwd=self.root).returncode, 0)
        result = run(PY, "tools/agents_session.py", "check", "--agent", "codex", "--files", "src/", "--json", cwd=self.root)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("policy", json.loads(result.stdout))

    def test_unknown_dedicated_entry_is_preserved(self):
        path = self.root / ".cursor/rules/multi-agent-collab.mdc"
        path.parent.mkdir(parents=True)
        path.write_text("---\nalwaysApply: false\n---\nUser rules\n", encoding="utf-8")
        before = self.snapshot()
        result = install(self.root, "--platforms", "cursor")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
