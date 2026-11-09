"""Installation safety against wrong targets, concurrent updates and interrupted writes."""

import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from selftest import KIT, PY, install, run, rmtree_readonly
import install as installer
import agents_layout as layout
from agents_layout import state_dir


class TestInstallSafety(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp(prefix="mac-install-safety-"))
        self.root = self.parent / "project"
        self.root.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)
        installer.configure_layout(self.root)

    def tearDown(self):
        rmtree_readonly(self.parent)

    def snapshot(self, root=None, include_git=False):
        root = root or self.root
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*")
                if path.is_file() and (include_git or ".git" not in path.relative_to(root).parts)}

    def install_project(self, root=None, platforms="codex"):
        result = install(root or self.root, "--platforms", platforms)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def seed_claude_entry(self, original):
        old = original.decode("utf-8") if original is not None else None
        text = installer.entry_content("CLAUDE.md", "CLAUDE.md", None, "Fixture", "", old)
        content = text.encode("utf-8")
        (self.root / "CLAUDE.md").write_bytes(content)
        private = self.root / installer.STATE_DIR
        private.mkdir(exist_ok=True)
        metadata = {"schema_version": 3, "owner": "multi-agent-collab", "layout": installer.STATE_DIR,
                    "project_name": "Fixture", "platforms": ["claude"],
                    "managed_hashes": {"CLAUDE.md": hashlib.sha256(content).hexdigest()},
                    "project_entries": [] if original is None else ["CLAUDE.md"],
                    "entry_boundaries": {"CLAUDE.md": installer.appended_entry_boundary(old, text)}}
        (self.root / installer.INSTALL_META).write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return content

    def directory_alias(self, source, link):
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(source), str(link))
            else:
                link.symlink_to(source, target_is_directory=True)
        except (AttributeError, ImportError, NotImplementedError, OSError) as exc:
            self.skipTest(f"Directory links are unavailable: {exc}")

    def linked_worktree(self):
        (self.root / "README.md").write_text("Fixture only\n", encoding="utf-8")
        for args in (("config", "user.name", "Fixture"), ("config", "user.email", "fixture@example.invalid"),
                     ("add", "README.md"), ("commit", "-qm", "test: seed fixture"),
                     ("branch", "feature/linked")):
            result = run("git", *args, cwd=self.root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        linked = self.parent / "linked"
        result = run("git", "worktree", "add", str(linked), "feature/linked", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return linked

    def test_git_metadata_and_bare_repository_are_never_install_targets(self):
        bare = self.parent / "bare.git"
        self.assertEqual(run("git", "init", "--bare", "-q", str(bare), cwd=self.parent).returncode, 0)
        for target in (self.root / ".git", self.root / ".git/objects", bare):
            with self.subTest(target=target):
                before = self.snapshot(self.parent, include_git=True)
                result = install(target, "--platforms", "codex")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("实际项目工作区", result.stdout + result.stderr)
                self.assertEqual(self.snapshot(self.parent, include_git=True), before)
        self.assertFalse(installer.is_git_repo(self.root / ".git"))
        self.assertFalse(installer.is_git_repo(bare))

    def test_doctor_and_clients_resolve_project_subdirectories(self):
        self.install_project()
        subdir = self.root / "src/subdir"
        subdir.mkdir(parents=True)
        result = run(PY, str(KIT / "install.py"), "doctor", str(subdir), cwd=KIT, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"体检  {self.root}", result.stdout)
        self.assertNotIn("这是 linked worktree", result.stdout)
        result = run(PY, str(KIT / "install.py"), "clients", str(subdir), "--json", cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([row["id"] for row in json.loads(result.stdout)["clients"]], ["codex"])

    def test_corrupt_metadata_rejects_every_mutating_command(self):
        self.install_project()
        metadata = self.root / ".multi-agent-collab/install.json"
        original = json.loads(metadata.read_text(encoding="utf-8"))
        invalid = ["{broken", "[]", json.dumps(dict(original, managed_hashes=[])),
                   json.dumps(dict(original, zone_base_hashes={"rules": "invalid"})),
                   json.dumps(dict(original, project_entries="AGENTS.md")),
                   json.dumps(dict(original, schema_version=99))]
        for data in invalid:
            metadata.write_text(data, encoding="utf-8")
            before = self.snapshot()
            for command in ("install", "sync", "uninstall"):
                with self.subTest(data=data[:30], command=command):
                    result = run(PY, str(KIT / "install.py"), command, str(self.root), cwd=KIT)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stdout + result.stderr)
                    self.assertEqual(self.snapshot(), before)

    def test_doctor_does_not_execute_a_modified_tool(self):
        self.install_project()
        marker = self.parent / "unexpected-execution.txt"
        tool = self.root / ".multi-agent-collab/tools/agents_status.py"
        tool.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nprint('{{}}')\n",
                        encoding="utf-8")
        result = run(PY, str(KIT / "install.py"), "doctor", str(self.root), cwd=KIT, timeout=180)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("与安装摘要不符", result.stdout)
        self.assertFalse(marker.exists())

    def test_foreign_private_directory_never_shadows_legacy_occupancy(self):
        legacy = self.root / ".agents/sessions"
        legacy.mkdir(parents=True)
        (legacy / "occupied.md").write_text("- status: active\n- files: [\"src/\"]\n", encoding="utf-8")
        private = self.root / ".multi-agent-collab"
        private.mkdir()
        for data in (None, "{broken", "[]", '{"owner": "another-tool"}',
                     '{"owner": "multi-agent-collab", "layout": ".agents"}'):
            with self.subTest(data=data):
                if data is not None:
                    (private / "install.json").write_text(data, encoding="utf-8")
                with self.assertRaises(ValueError):
                    state_dir(self.root)
        (private / "install.json").write_text('{"owner": "multi-agent-collab"}', encoding="utf-8")
        self.assertEqual(state_dir(self.root), ".multi-agent-collab")
        self.assertTrue((legacy / "occupied.md").exists())

    def test_private_directory_alias_never_shadows_legacy_occupancy(self):
        legacy = self.root / ".agents/sessions"
        legacy.mkdir(parents=True)
        occupancy = b"- status: active\n- files: [\"src/\"]\n"
        (legacy / "occupied.md").write_bytes(occupancy)
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "install.json").write_text('{"owner": "multi-agent-collab"}', encoding="utf-8")
        (outside / "marker.bin").write_bytes(b"Keep outside state\r\n")
        before = self.snapshot(outside)
        alias = self.root / ".multi-agent-collab"
        self.directory_alias(outside, alias)
        with self.assertRaises(ValueError):
            state_dir(self.root)
        self.assertEqual(self.snapshot(outside), before)
        self.assertEqual((legacy / "occupied.md").read_bytes(), occupancy)
        self.assertEqual(alias.resolve(), outside.resolve())

    @unittest.skipIf(os.name == "nt", "Windows chmod cannot represent POSIX permission bits")
    def test_atomic_replacement_preserves_existing_file_permissions(self):
        path = self.root / "rules.md"
        path.write_text("before\n", encoding="utf-8")
        path.chmod(0o640)
        installer.write_lf(path, "after\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(path.read_bytes(), b"after\n")

    def test_failed_atomic_replace_preserves_original_and_cleans_temporary_file(self):
        path = self.root / "rules.md"
        path.write_bytes(b"before\r\n")
        with patch.object(installer.os, "replace", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(OSError):
                installer.write_lf(path, "after\n")
        self.assertEqual(path.read_bytes(), b"before\r\n")
        self.assertEqual(list(self.root.glob(".rules.md.*.tmp")), [])

    def test_reinstall_preserves_frontmatter_without_duplicate_template_header(self):
        source = "platforms/cursor-rule.mdc"
        relative = ".cursor/rules/multi-agent-collab.mdc"
        installed = installer.entry_content(source, relative, None, "Fixture", "")
        start, end = installer.entry_span(installed, relative)
        after_uninstall = installed[:start] + installed[end:]
        restored = installer.entry_content(source, relative, None, "Fixture", "", after_uninstall)
        self.assertEqual(restored.count("alwaysApply:"), 1)
        self.assertEqual(restored, installer.entry_content(source, relative, None, "Fixture", "", restored))

    def test_native_git_default_selection_never_overrides_explicit_git(self):
        shim = self.parent / "Git/cmd/git.exe"
        native = self.parent / "Git/mingw64/bin/git.exe"
        for path in (shim, native):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture executable, not launched")
        with patch.object(layout, "os", SimpleNamespace(name="nt")):
            self.assertEqual(layout.prefer_native_git(str(shim)), str(native))
            alternate = str(self.parent / "Other/bin/git.exe")
            self.assertEqual(layout.prefer_native_git(alternate), alternate)
            with patch.dict(installer.os.environ, {installer.GIT_ENV_VAR: str(shim)}), \
                    patch.object(installer, "_git_bin_cache", installer._GIT_UNSET), \
                    patch.object(installer, "prefer_native_git", side_effect=AssertionError("explicit override must be preserved")):
                self.assertEqual(installer.resolve_git(), str(shim))
            native.unlink()
            self.assertEqual(layout.prefer_native_git(str(shim)), str(shim))
        with patch.object(layout, "os", SimpleNamespace(name="posix")):
            self.assertEqual(layout.prefer_native_git(str(shim)), str(shim))

    def test_failed_install_rolls_back_rules_metadata_backups_and_ignores(self):
        (self.root / "AGENTS.md").write_bytes(b"# Team rules\r\nKeep original bytes.\r\n")
        (self.root / ".gitignore").write_bytes(b"# Team ignores\r\ndist/\r\n")
        before = self.snapshot(include_git=True)
        original_write = installer.write_lf

        def fail_during_install(path, content):
            if path.name == "START_HERE.md":
                raise OSError("fixture interrupted install")
            return original_write(path, content)

        output = io.StringIO()
        with patch.object(installer, "write_lf", side_effect=fail_during_install), \
                patch.object(sys, "argv", ["install.py", "install", str(self.root), "--platforms", "codex"]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(installer.main(), 2)
        after = self.snapshot(include_git=True)
        after.pop(".git/multi-agent-collab-install.lock", None)
        self.assertEqual(after, before)
        self.assertFalse((self.root / ".multi-agent-collab").exists())
        self.assertIn("已回滚", output.getvalue())
        self.install_project()

    def test_failed_metadata_commit_also_rolls_back_complete_file_plan(self):
        before = self.snapshot()
        original_write = installer.write_lf

        def fail_metadata(path, content):
            if path.name == "install.json":
                raise OSError("fixture metadata commit failure")
            return original_write(path, content)

        with patch.object(installer, "write_lf", side_effect=fail_metadata), \
                patch.object(sys, "argv", ["install.py", "install", str(self.root), "--platforms", "codex"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(installer.main(), 2)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / ".multi-agent-collab").exists())

    def test_rollback_preserves_edits_made_after_installer_write(self):
        path = self.root / "AGENTS.md"
        path.write_text("before", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "外部改动"):
            with installer.installation_transaction():
                installer.write_lf(path, "installer update")
                path.write_text("another agent update", encoding="utf-8")
                raise OSError("fixture failure")
        self.assertEqual(path.read_text(encoding="utf-8"), "another agent update")

    def test_repository_lock_is_shared_by_linked_worktrees(self):
        linked = self.linked_worktree()
        script = ("from pathlib import Path\nimport sys\nsys.stdout.reconfigure(encoding='utf-8')\nimport install\n"
                  "try:\n with install.installation_lock(Path(sys.argv[1]), timeout=0.2):\n  print('acquired')\n"
                  "except ValueError as exc:\n print(exc)\n sys.exit(2)\n")
        with installer.installation_lock(self.root):
            result = run(PY, "-B", "-c", script, str(linked), cwd=KIT)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("未抢占锁", result.stdout)
        result = run(PY, "-B", "-c", script, str(linked), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("acquired", result.stdout)

    def test_concurrent_sync_preserves_both_platform_selections(self):
        self.install_project()
        processes = [subprocess.Popen([PY, str(KIT / "install.py"), "sync", str(self.root), "--platforms", platform],
                                      cwd=str(KIT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for platform in ("claude", "cursor")]
        try:
            for process in processes:
                stdout, stderr = process.communicate(timeout=180)
                self.assertEqual(process.returncode, 0, (stdout + stderr).decode("utf-8", "replace"))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        metadata = json.loads((self.root / ".multi-agent-collab/install.json").read_text(encoding="utf-8"))
        self.assertEqual(set(metadata["platforms"]), {"codex", "claude", "cursor"})
        self.assertTrue((self.root / "CLAUDE.md").is_file())
        self.assertTrue((self.root / ".cursor/rules/multi-agent-collab.mdc").is_file())

    def test_sync_cannot_unignore_another_worktrees_platform_entry(self):
        self.install_project()
        linked = self.linked_worktree()
        self.install_project(linked, "cursor")
        result = run(PY, str(KIT / "install.py"), "sync", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.root / ".multi-agent-collab/install.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["platforms"], ["codex"])
        self.assertEqual(run("git", "check-ignore", "-q", ".cursor/rules/multi-agent-collab.mdc", cwd=linked).returncode, 0)

    def test_shared_entries_roundtrip_preserves_bom_newline_count_and_tracking(self):
        for newline in ("\n", "\r\n"):
            for endings in (0, 1, 2):
                for bom in (False, True):
                    with self.subTest(newline=repr(newline), endings=endings, bom=bom):
                        root = self.parent / f"roundtrip-{len(newline)}-{endings}-{int(bom)}"
                        root.mkdir()
                        self.assertEqual(run("git", "init", "-q", cwd=root).returncode, 0)
                        prefix = b"\xef\xbb\xbf" if bom else b""
                        agents = prefix + ("# 原有规则" + newline + "保留字节：é/😀" + newline * endings).encode("utf-8")
                        claude = prefix + ("# 项目 Claude 规则" + newline + "@AGENTS.md" + newline +
                                           "@./.multi-agent-collab/START_HERE.md" + newline * endings).encode("utf-8")
                        originals = {"AGENTS.md": agents, "CLAUDE.md": claude}
                        for relative, content in originals.items():
                            (root / relative).write_bytes(content)
                        self.assertEqual(run("git", "add", "AGENTS.md", "CLAUDE.md", cwd=root).returncode, 0)
                        initial_index = run("git", "ls-files", "--stage", cwd=root).stdout
                        self.install_project(root, "claude")
                        installed = {relative: (root / relative).read_bytes() for relative in originals}
                        for relative, original in originals.items():
                            self.assertTrue(installed[relative].startswith(original), relative)
                        text = installed["CLAUDE.md"].decode("utf-8")
                        start, end = installer.entry_span(text, "CLAUDE.md")
                        self.assertNotIn("@AGENTS.md", text[start:end])
                        self.assertNotIn("@.multi-agent-collab/START_HERE.md", text[start:end])
                        self.install_project(root, "claude")
                        self.assertEqual({relative: (root / relative).read_bytes() for relative in originals}, installed)
                        result = run(PY, str(KIT / "install.py"), "uninstall", str(root), cwd=KIT)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual({relative: (root / relative).read_bytes() for relative in originals}, originals)
                        self.install_project(root, "claude")
                        self.assertEqual({relative: (root / relative).read_bytes() for relative in originals}, installed)
                        self.assertEqual(run("git", "ls-files", "--stage", cwd=root).stdout, initial_index)

    def test_claude_code_and_comment_examples_are_not_effective_imports(self):
        original = (b"\xef\xbb\xbf# Example only\r\n```md\r\n@AGENTS.md\r\n"
                    b"@.multi-agent-collab/START_HERE.md\r\n```\r\n<!--\r\n@AGENTS.md\r\n"
                    b"@.multi-agent-collab/START_HERE.md\r\n-->\r\n    @AGENTS.md\r\n")
        (self.root / "CLAUDE.md").write_bytes(original)
        self.install_project(platforms="claude")
        first = (self.root / "CLAUDE.md").read_bytes()
        text = first.decode("utf-8")
        start, end = installer.entry_span(text, "CLAUDE.md")
        self.assertEqual(text[start:end].count("@AGENTS.md"), 1)
        self.assertEqual(text[start:end].count("@.multi-agent-collab/START_HERE.md"), 1)
        self.install_project(platforms="claude")
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), first)
        result = run(PY, str(KIT / "install.py"), "uninstall", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), original)
        self.install_project(platforms="claude")
        self.assertEqual((self.root / "CLAUDE.md").read_bytes(), first)

    def test_legacy_metadata_recovers_only_proven_backup_separators(self):
        originals = {"AGENTS.md": b"\xef\xbb\xbf# Original project\r\nKeep original newline.\r\n",
                     "CLAUDE.md": b"\xef\xbb\xbf# Original Claude\r\n@AGENTS.md\r\n"}
        for relative, content in originals.items():
            (self.root / relative).write_bytes(content)
        self.install_project(platforms="claude")
        metadata = self.root / ".multi-agent-collab/install.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data.pop("entry_boundaries", None)
        for relative, original in originals.items():
            text = (self.root / relative).read_bytes().decode("utf-8")
            start, end = installer.entry_span(text, relative)
            block = text[start:end].replace("\r\n", "\n")
            old_style = original + b"\n" + block.encode("utf-8") + b"\n"
            (self.root / relative).write_bytes(old_style)
            data["managed_hashes"][relative] = hashlib.sha256(old_style).hexdigest()
        metadata.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.install_project(platforms="claude")
        data = json.loads(metadata.read_text(encoding="utf-8"))
        self.assertTrue(all(data["entry_boundaries"][relative]["before"] == "\n" for relative in originals))
        result = run(PY, str(KIT / "install.py"), "uninstall", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({relative: (self.root / relative).read_bytes() for relative in originals}, originals)

    def test_sync_does_not_make_user_added_outside_text_deletable(self):
        self.install_project(platforms="claude")
        path = self.root / "CLAUDE.md"
        outside = "\n# 用户新增约定\n@AGENTS.md\n保留这段正文。\n"
        path.write_bytes(path.read_bytes() + outside.encode("utf-8"))
        result = run(PY, str(KIT / "install.py"), "sync", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run(PY, str(KIT / "install.py"), "uninstall", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), outside.encode("utf-8"))

    def test_uninstall_preserves_external_edits_after_delete_and_write_planning(self):
        path = self.root / "CLAUDE.md"
        addition = b"\n# Concurrent user rule\nPreserve this tail.\r\n"
        args = SimpleNamespace(target=str(self.root), _resolved_target=self.root, dry_run=False, purge_files=False)
        with installer.git_topology_scope(self.root, self.root / ".git"):
            for original in (None, b"\xef\xbb\xbf# Original rule\r\nKeep this rule.\r\n"):
                with self.subTest(created=original is None):
                    content = self.seed_claude_entry(original)
                    before = self.snapshot(include_git=True)
                    operation = "delete_managed_file" if original is None else "write_lf"
                    real_operation = getattr(installer, operation)

                    def edit_before_operation(dest, *arguments):
                        if dest == path:
                            dest.write_bytes(dest.read_bytes() + addition)
                        return real_operation(dest, *arguments)

                    with self.assertRaisesRegex(ValueError, "外部改动"):
                        with installer.installation_transaction(), \
                                patch.object(installer, operation, side_effect=edit_before_operation):
                            installer.cmd_uninstall(args)
                    before["CLAUDE.md"] = content + addition
                    self.assertEqual(self.snapshot(include_git=True), before)

    def test_purge_preserves_tool_edits_after_removal_planning(self):
        self.seed_claude_entry(b"# Original project rule\r\n")
        relative = next(dst for src, dst, _zone in installer.CORE_FILES if src == "tools/agents_status.py")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        original = b"# Original managed tool\r\npass\r\n"
        addition = b"# Concurrent user fix\r\nKeep every byte.\r\n"
        path.write_bytes(original)
        metadata = self.root / installer.INSTALL_META
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data["managed_hashes"][relative] = hashlib.sha256(original).hexdigest()
        metadata.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = self.snapshot(include_git=True)
        args = SimpleNamespace(target=str(self.root), _resolved_target=self.root, dry_run=False, purge_files=True)
        real_delete = installer.delete_managed_file

        def edit_before_delete(dest):
            if dest == path:
                dest.write_bytes(dest.read_bytes() + addition)
            return real_delete(dest)

        with self.assertRaisesRegex(ValueError, "外部改动"):
            with installer.git_topology_scope(self.root, self.root / ".git"), \
                    installer.installation_transaction(), \
                    patch.object(installer, "delete_managed_file", side_effect=edit_before_delete):
                installer.cmd_uninstall(args)
        before[relative] = original + addition
        self.assertEqual(self.snapshot(include_git=True), before)

    def test_install_plan_preserves_edits_before_apply_and_after_ownership_check(self):
        path = self.root / "CLAUDE.md"
        addition = b"\n# Another editor\nKeep every byte.\r\n"
        (self.root / "AGENTS.md").write_bytes(b"# Original team rule\r\n")
        with installer.git_topology_scope(self.root, self.root / ".git"):
            for timing in ("before-apply", "after-ownership"):
                with self.subTest(timing=timing):
                    content = self.seed_claude_entry(b"# Original Claude rule\r\n")
                    plan = installer.install_plan(self.root, "Updated fixture", ["claude"], sync_only=True)
                    before = self.snapshot(include_git=True)
                    real_ownership = installer.ownership_preflight

                    def edit_after_ownership(target, rows):
                        real_ownership(target, rows)
                        path.write_bytes(path.read_bytes() + addition)

                    if timing == "before-apply":
                        path.write_bytes(content + addition)
                    hook = patch.object(installer, "ownership_preflight", side_effect=edit_after_ownership) \
                        if timing == "after-ownership" else contextlib.nullcontext()
                    with self.assertRaisesRegex(ValueError, "外部改动"):
                        with installer.installation_transaction(), hook:
                            installer.apply_install_plan(self.root, plan, False)
                    before["CLAUDE.md"] = content + addition
                    self.assertEqual(self.snapshot(include_git=True), before)

    def test_derived_ignore_metadata_and_policy_writes_preserve_external_changes(self):
        with installer.git_topology_scope(self.root, self.root / ".git"):
            for kind in ("gitignore", "exclude", "metadata", "policy-json", "policy-doc"):
                with self.subTest(kind=kind):
                    self.seed_claude_entry(b"# Original rule\n")
                    policy = installer.build_policy()
                    (self.root / installer.POLICY_FILE).write_text(json.dumps(policy), encoding="utf-8")
                    (self.root / installer.POLICY_DOC).write_text(installer.render_policy(policy), encoding="utf-8")
                    (self.root / ".gitignore").write_bytes(b"# Team ignores\ncache/\n")
                    exclude = self.root / ".git/info/exclude"
                    exclude.write_text(installer.managed_block(["/" + installer.STATE_DIR + "/"]), encoding="utf-8")
                    paths = {"gitignore": self.root / ".gitignore", "exclude": exclude,
                             "metadata": self.root / installer.INSTALL_META,
                             "policy-json": self.root / installer.POLICY_FILE,
                             "policy-doc": self.root / installer.POLICY_DOC}
                    path = paths[kind]
                    plan = installer.policy_plan(self.root) if kind.startswith("policy-") else None
                    before = self.snapshot(include_git=True)
                    changed = path.read_bytes() + (b"\n" if kind in ("metadata", "policy-json") else b"\n# Human added rule\n")
                    with self.assertRaisesRegex(ValueError, "外部改动"):
                        with installer.installation_transaction():
                            if kind == "gitignore":
                                installer.gitignore_content(self.root)
                            elif kind == "exclude":
                                installer.preflight(self.root, [installer.INSTALL_META], False)
                            elif kind == "metadata":
                                installer.read_meta(self.root)
                            path.write_bytes(changed)
                            if kind == "gitignore":
                                installer.update_gitignore(self.root, False)
                            elif kind == "exclude":
                                installer.update_excludes(self.root, False)
                            elif kind == "metadata":
                                installer.save_meta(self.root, "Fixture", ["claude"], False)
                            else:
                                installer.apply_install_plan(self.root, plan, False)
                    before[path.relative_to(self.root).as_posix()] = changed
                    self.assertEqual(self.snapshot(include_git=True), before)

    def test_edit_while_atomic_temporary_file_is_written_preserves_original_path(self):
        path = self.root / "AGENTS.md"
        path.write_bytes(b"Original rule\r\n")
        changed = b"Another editor owns these bytes.\r\n"
        real_fsync = installer.os.fsync

        def edit_during_fsync(fd):
            real_fsync(fd)
            path.write_bytes(changed)

        with self.assertRaisesRegex(ValueError, "外部改动"):
            with installer.installation_transaction(), patch.object(installer.os, "fsync", side_effect=edit_during_fsync):
                installer.read_text(path)
                installer.write_lf(path, "Planned installer update\n")
        self.assertEqual(path.read_bytes(), changed)
        self.assertEqual(list(self.root.glob(".AGENTS.md.*.tmp")), [])

    def test_backup_directory_alias_preserves_outside_files_and_original_entry(self):
        original = self.seed_claude_entry(b"# Original rule\r\n")
        path = self.root / "CLAUDE.md"
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "marker.bin").write_bytes(b"Outside marker\r\n")
        before = self.snapshot(outside)
        backups = self.root / installer.STATE_DIR / "backups"
        self.directory_alias(outside, backups)
        planned = installer.entry_content("CLAUDE.md", "CLAUDE.md", None, "Changed", "", original.decode("utf-8"))
        with self.assertRaisesRegex(ValueError, "备份路径.*(外部|链接)"):
            with installer.installation_transaction():
                installer.install_file(path, planned, self.root, False, {"write": 0, "skip": 0})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.snapshot(outside), before)

    def test_backup_redirection_before_temporary_creation_preserves_outside_files(self):
        original = self.seed_claude_entry(b"# Original rule\r\n")
        path = self.root / "CLAUDE.md"
        private = self.root / installer.STATE_DIR
        backups = private / "backups"
        archived = private / "backups-moved"
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "marker.bin").write_bytes(b"Outside marker\r\n")
        before = self.snapshot(outside)
        real_mkdir = Path.mkdir
        redirected = []

        def redirect_after_mkdir(directory, *arguments, **keywords):
            result = real_mkdir(directory, *arguments, **keywords)
            if directory == backups and not redirected:
                directory.resolve().relative_to(self.root.resolve())
                archived.absolute().relative_to(self.root.resolve())
                directory.rename(archived)
                self.directory_alias(outside, directory)
                redirected.append(directory)
            return result

        planned = installer.entry_content("CLAUDE.md", "CLAUDE.md", None, "Changed", "", original.decode("utf-8"))
        with self.assertRaisesRegex(ValueError, "备份路径.*(外部|链接)"):
            with installer.installation_transaction(), patch.object(Path, "mkdir", new=redirect_after_mkdir):
                installer.install_file(path, planned, self.root, False, {"write": 0, "skip": 0})
        self.assertTrue(redirected)
        self.assertEqual(self.snapshot(outside), before)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.snapshot(archived), {})
        self.assertEqual(backups.resolve(), outside.resolve())

    def test_backup_redirection_before_replace_does_not_delete_external_temporary_name(self):
        original = self.seed_claude_entry(b"# Original rule\r\n")
        path = self.root / "CLAUDE.md"
        private = self.root / installer.STATE_DIR
        backups = private / "backups"
        backups.mkdir()
        archived = private / "backups-moved"
        outside = self.parent / "outside"
        outside.mkdir()
        (outside / "marker.bin").write_bytes(b"Outside marker\r\n")
        real_mkstemp = installer.tempfile.mkstemp
        real_chmod = installer.os.chmod
        pending = []
        expected_outside = {}

        def remember_temporary(*arguments, **keywords):
            result = real_mkstemp(*arguments, **keywords)
            if Path(keywords["dir"]) == backups:
                pending.append(Path(result[1]).name)
            return result

        def redirect_before_replace(temporary, mode):
            real_chmod(temporary, mode)
            # The writer has closed its handle here; Windows can now rename
            # this directory. Keep the race before the final path guard.
            if pending and not expected_outside:
                backups.resolve().relative_to(self.root.resolve())
                archived.absolute().relative_to(self.root.resolve())
                backups.rename(archived)
                self.directory_alias(outside, backups)
                (outside / pending[0]).write_bytes(b"External file with the same temporary name\r\n")
                expected_outside.update(self.snapshot(outside))

        planned = installer.entry_content("CLAUDE.md", "CLAUDE.md", None, "Changed", "", original.decode("utf-8"))
        with self.assertRaisesRegex(ValueError, "备份路径.*(外部|链接)"):
            with installer.installation_transaction(), \
                    patch.object(installer.tempfile, "mkstemp", side_effect=remember_temporary), \
                    patch.object(installer.os, "chmod", side_effect=redirect_before_replace):
                installer.install_file(path, planned, self.root, False, {"write": 0, "skip": 0})
        self.assertTrue(pending)
        self.assertEqual(self.snapshot(outside), expected_outside)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((archived / pending[0]).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
