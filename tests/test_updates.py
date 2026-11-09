"""Real installed-project updates, including same-version content and failure recovery."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from selftest import KIT, PY, install, run, rmtree_readonly
import install as installer


class TestInstalledUpdates(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-update-tests-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.assertEqual(run("git", "init", "-q", "-b", "main", cwd=self.root).returncode, 0)
        self.original = b"\xef\xbb\xbf# Project rules\r\nKeep user rules.\r\n"
        (self.root / "AGENTS.md").write_bytes(self.original)
        self.cursor = self.root / ".cursor/rules/team.mdc"
        self.cursor.parent.mkdir(parents=True)
        self.cursor.write_text("Team-owned rule\n", encoding="utf-8")

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-update-tests-"):
            raise RuntimeError("unsafe update fixture cleanup")
        rmtree_readonly(self.base)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def initial_install(self):
        result = install(self.root, "--platforms", "codex,claude", "--workflow", "github-flow")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.state = self.root / ".multi-agent-collab"
        return json.loads((self.state / "install.json").read_text(encoding="utf-8"))

    def update(self, *args, source=KIT):
        result = run(PY, "-B", str(source / "install.py"), "update", str(self.root), "--json", *args, cwd=KIT)
        return result, json.loads(result.stdout)

    def other_source(self):
        source = self.base / "local kit 中文"
        shutil.copytree(KIT, source, ignore=shutil.ignore_patterns(".git", ".agents", ".multi-agent-collab", "__pycache__", "site", "tests"))
        return source

    def test_uninstalled_project_is_refused_without_any_file_changes(self):
        before = self.snapshot()
        result, data = self.update()
        self.assertEqual(result.returncode, 2, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_current_install_and_repeat_update_are_byte_idempotent(self):
        self.initial_install()
        before = self.snapshot()
        for args in (("--check",), (), ("--dry-run",)):
            result, data = self.update(*args)
            self.assertEqual(result.returncode, 0, data)
            self.assertFalse(data["update_available"])
            self.assertFalse(data["applied"])
            self.assertEqual(self.snapshot(), before)

    def test_same_version_source_change_preserves_custom_rules_policy_and_sessions(self):
        first_meta = self.initial_install()
        rules = self.state / "RULES.md"
        text = rules.read_text(encoding="utf-8")
        marker = "<!-- <<< mac:zone:project-env -->"
        self.assertIn(marker, text)
        customized = text.replace(marker, "Keep my project command: python app_test.py\n" + marker)
        rules.write_bytes(b"\xef\xbb\xbf" + customized.replace("\n", "\r\n").encode("utf-8"))
        session = self.state / "sessions/codex-existing.md"
        session.write_text("# Existing\n- agent: codex\n- status: blocked\n- files: [\"src/\"]\n\nKeep record.\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_bytes((self.root / "AGENTS.md").read_bytes() + b"\r\nUser outside tail\r\n")
        preserved = {p: p.read_bytes() for p in (rules, session, self.state / "policy.json", self.cursor, self.root / "AGENTS.md")}
        source = self.other_source()
        changed_tool = source / "tools/agents_status.py"
        changed_tool.write_bytes(changed_tool.read_bytes() + b"\n# Updated content, same kit version.\n")
        before = self.snapshot()
        result, data = self.update("--check", source=source)
        self.assertEqual(result.returncode, 1, data)
        self.assertIn(".multi-agent-collab/tools/agents_status.py", data["changed_files"])
        self.assertEqual(self.snapshot(), before)
        result, data = self.update("--dry-run", source=source)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.snapshot(), before)
        result, data = self.update(source=source)
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["applied"])
        self.assertTrue(data["restart_required"])
        self.assertEqual((self.state / "tools/agents_status.py").read_bytes(), changed_tool.read_bytes())
        for path, content in preserved.items():
            self.assertEqual(path.read_bytes(), content, str(path))
        meta = json.loads((self.state / "install.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["platforms"], first_meta["platforms"])
        self.assertEqual(meta["installed_at"], first_meta["installed_at"])
        self.assertIn("updated_at", meta)
        self.assertEqual(meta["source_dir"], str(source))
        after = self.snapshot()
        result, data = self.update(source=source)
        self.assertEqual(result.returncode, 0, data)
        self.assertFalse(data["applied"])
        self.assertEqual(self.snapshot(), after)

    def test_older_source_requires_explicit_downgrade(self):
        self.initial_install()
        metadata = self.state / "install.json"
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload["kit_version"] = "9.0.0"
        metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = self.snapshot()
        result, data = self.update()
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("降级", data["error"])
        self.assertEqual(self.snapshot(), before)
        result, data = self.update("--allow-downgrade")
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["applied"])
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["kit_version"], installer.KIT_VERSION)

    def test_legacy_bom_zone_digest_remains_compatible_with_customization(self):
        self.initial_install()
        rules = self.state / "RULES.md"
        original = "\ufeff" + rules.read_text(encoding="utf-8")
        metadata = self.state / "install.json"
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        relative = ".multi-agent-collab/RULES.md"
        payload["zone_base_hashes"][relative] = installer.legacy_zone_base_hash(original)
        payload["managed_hashes"][relative] = hashlib.sha256(original.encode("utf-8")).hexdigest()
        metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        marker = "<!-- <<< mac:zone:project-env -->"
        rules.write_bytes(original.replace(marker, "Keep legacy customization\n" + marker).encode("utf-8"))
        before = self.snapshot()
        result, data = self.update("--check")
        self.assertEqual(result.returncode, 0, data)
        self.assertFalse(data["update_available"])
        self.assertEqual(self.snapshot(), before)

    def test_user_modified_tool_is_preserved_and_update_refused(self):
        self.initial_install()
        tool = self.state / "tools/agents_status.py"
        tool.write_bytes(tool.read_bytes() + b"\n# User-owned modification\n")
        before = self.snapshot()
        result, data = self.update()
        self.assertEqual(result.returncode, 2, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_ignore_drift_is_detected_and_repaired_without_losing_user_rules(self):
        self.initial_install()
        ignore = self.root / ".gitignore"
        ignore.write_text("user-cache/\n", encoding="utf-8")
        result, data = self.update("--check")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn(".gitignore", data["changed_files"])
        result, data = self.update()
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(ignore.read_text(encoding="utf-8").startswith("user-cache/\n"))
        self.assertEqual(run("git", "check-ignore", ".multi-agent-collab/install.json", cwd=self.root).returncode, 0)

    def test_installed_entry_locates_source_from_a_subdirectory(self):
        self.initial_install()
        subdir = self.root / "src/nested"
        subdir.mkdir(parents=True)
        before = self.snapshot()
        result = run(PY, "-B", str(self.state / "tools/agents_update.py"), "--root", str(subdir), "--check", "--json", cwd=subdir)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["root"], str(self.root))
        self.assertEqual(data["source"], str(KIT))
        self.assertEqual(self.snapshot(), before)

    def test_installed_entry_handles_missing_source_and_explicit_replacement(self):
        self.initial_install()
        metadata = self.state / "install.json"
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload["source_dir"] = str(self.base / "missing kit")
        metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = self.snapshot()
        entry = self.state / "tools/agents_update.py"
        result = run(PY, "-B", str(entry), "--root", str(self.root), "--json", cwd=self.root)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--source", json.loads(result.stdout)["error"])
        self.assertEqual(self.snapshot(), before)
        result = run(PY, "-B", str(entry), "--root", str(self.root), "--source", str(KIT), "--json", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["applied"])
        self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["source_dir"], str(KIT))

    def test_preview_reuses_topology_without_creating_lock_or_changing_files(self):
        self.initial_install()
        before = self.snapshot()
        original_git = installer.git_ok
        queries = []

        def counted_git(target, *args):
            queries.append(args)
            return original_git(target, *args)

        with mock.patch.object(installer, "git_ok", side_effect=counted_git), \
             mock.patch("sys.argv", [str(KIT / "install.py"), "update", str(self.root), "--check", "--json"]), contextlib.redirect_stdout(io.StringIO()):
            rc = installer.main()
        self.assertEqual(rc, 0)
        self.assertLessEqual(sum(args[:2] == ("worktree", "list") for args in queries), 1)
        self.assertEqual(self.snapshot(), before)

    def test_update_failure_rolls_back_every_prior_managed_write(self):
        self.initial_install()
        metadata = self.state / "install.json"
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        payload["kit_version"] = "1.0.0"
        metadata.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        source = self.other_source()
        tool = source / "tools/agents_status.py"
        tool.write_bytes(tool.read_bytes() + b"\n# Force an owned file update before metadata fails.\n")
        before = self.snapshot()
        original_write = installer.write_lf

        def fail_metadata(path, content):
            if path.name == "install.json":
                raise OSError("simulated update metadata failure")
            return original_write(path, content)

        with mock.patch.object(installer, "KIT_ROOT", source), mock.patch.object(installer, "write_lf", side_effect=fail_metadata), \
             mock.patch("sys.argv", [str(KIT / "install.py"), "update", str(self.root), "--json"]), contextlib.redirect_stdout(io.StringIO()):
            rc = installer.main()
        self.assertEqual(rc, 2)
        self.assertEqual(self.snapshot(), before)


    def test_installed_update_check_adds_notice_without_writing_read_state(self):
        self.initial_install()
        question = self.state / "inbox/Q-ai-note.md"
        question.write_text("# Note\n- from: claude\n- to: codex\n- status: open\n\n更新兼容性有结果，请查看。\n",
                            encoding="utf-8")
        before = self.snapshot()
        wrapper = self.state / "tools/agents_update.py"
        result = run(PY, str(wrapper), "--root", str(self.root), "--agent", "codex", "--check", "--json",
                     cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["notifications"]["unread_count"], 1)
        self.assertNotIn("更新兼容性有结果", result.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.state / "notifications").exists())

    def test_typed_pause_blocks_real_update_but_allows_zero_write_preview(self):
        self.initial_install()
        question = self.state / "inbox/Q-pause-update.md"
        question.write_text("# Pause\n- from: human\n- to: any\n- status: open\n- action: pause\n\n暂停套件更新。\n",
                            encoding="utf-8")
        before = self.snapshot()
        wrapper = self.state / "tools/agents_update.py"
        for command in ((PY, str(wrapper), "--root", str(self.root), "--json"),
                        (PY, str(KIT / "install.py"), "update", str(self.root), "--json")):
            result = run(*command, cwd=self.root)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse(json.loads(result.stdout)["ok"])
            self.assertEqual(self.snapshot(), before)
        for flags in (("--check",), ("--dry-run",)):
            result = run(PY, str(wrapper), "--root", str(self.root), "--json", *flags, cwd=self.root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["notifications"]["human_pending"], 1)
            self.assertEqual(self.snapshot(), before)

    def test_installed_wrapper_context_flags_remain_compatible_with_older_source_parser(self):
        self.initial_install()
        source = self.other_source()
        script = source / "install.py"
        text = script.read_text(encoding="utf-8")
        start = text.index("    for project_parser in (p_inst, p_upd")
        end = text.index("    args = ap.parse_args()", start)
        script.write_text(text[:start] + text[end:], encoding="utf-8")
        wrapper = self.state / "tools/agents_update.py"
        result = run(PY, str(wrapper), "--root", str(self.root), "--source", str(source),
                     "--agent", "codex", "--json", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["applied"])
        self.assertEqual(json.loads((self.state / "install.json").read_text(encoding="utf-8"))["source_dir"], str(source))


if __name__ == "__main__":
    unittest.main()
