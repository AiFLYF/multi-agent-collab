"""Platform sync must not install prompts that retained tools cannot execute."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from selftest import KIT, PY, inst, rmtree_readonly, run
from test_session_safety import directory_alias


LEGACY_MAIN = '''def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    commands = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "start", "heartbeat", "handoff", "resume", "finish"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--agent")
        if name in ("heartbeat", "resume", "finish", "handoff"):
            sub.add_argument("session")
        if name == "finish":
            sub.add_argument("--summary")
            sub.add_argument("--tests")
    args = parser.parse_args()
    print(json.dumps({"ok": True, "cmd": args.cmd}))
    return 0
'''


class TestLiteralSyncContract(unittest.TestCase):
    def test_current_session_parser_supports_required_literal_contract(self):
        tree = ast.parse((KIT / "tools/agents_session.py").read_text(encoding="utf-8-sig"))
        contract = inst.session_cli_contract(tree)
        for name, flags in inst.SYNC_SESSION_REQUIREMENTS.items():
            self.assertIn(name, contract)
            self.assertFalse(flags - contract[name], name)

    def test_keywords_and_unused_parser_do_not_prove_capabilities(self):
        source = '''import argparse
DESCRIPTION = "activate run --standby --no-activity"
def main():
    unused = argparse.ArgumentParser()
    unused_commands = unused.add_subparsers()
    for name in ("activate", "run", "finish"):
        unused_sub = unused_commands.add_parser(name)
        unused_sub.add_argument("--standby")
        unused_sub.add_argument("--no-activity")
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers()
    sub = commands.add_parser("finish")
    args = parser.parse_args()
'''
        self.assertEqual(inst.session_cli_contract(ast.parse(source)), {"finish": set()})

    def test_false_or_unknown_guarded_options_are_not_claimed(self):
        source = '''import argparse
def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers()
    sub = commands.add_parser("finish")
    if False:
        sub.add_argument("--standby")
    if runtime_choice():
        sub.add_argument("--no-activity")
    args = parser.parse_args()
'''
        self.assertEqual(inst.session_cli_contract(ast.parse(source)), {"finish": set()})

    def test_constructor_reuse_in_loop_does_not_union_old_and_new_parsers(self):
        source = '''import argparse
def main():
    for version in ("new", "old"):
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers()
        sub = commands.add_parser("finish")
        if version == "new":
            sub.add_argument("--standby")
    args = parser.parse_args()
'''
        self.assertEqual(inst.session_cli_contract(ast.parse(source)), {"finish": set()})


class TestSyncCompatibility(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-sync-compat-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.aliases = []
        self.git = inst.resolve_git()
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Sync Contract"),
                     ("config", "user.email", "sync-contract@example.invalid")):
            result = run(self.git, *args, cwd=self.root)
            self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / "README.md").write_text("# controlled sync contract project\n", encoding="utf-8")
        self.assertEqual(run(self.git, "add", "README.md", cwd=self.root).returncode, 0)
        self.assertEqual(run(self.git, "commit", "-qm", "temporary fixture", cwd=self.root).returncode, 0)

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-sync-compat-"):
            raise RuntimeError("Refusing cleanup outside the created temporary fixture")
        for path in reversed(self.aliases):
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                path.parent.resolve().relative_to(self.base)
                path.rmdir() if os.name == "nt" and not path.is_symlink() else path.unlink()
        rmtree_readonly(self.base)

    def cli(self, command, *args, source=KIT, expected=0):
        result = run(PY, "-B", str(source / "install.py"), command, str(self.root), *args, cwd=self.root)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def install(self, source=KIT, platforms="codex", legacy=False):
        if legacy:
            state = self.root / ".agents"
            state.mkdir()
            (state / "install.json").write_text(json.dumps({"owner": "multi-agent-collab", "kit_version": "1.6.0",
                                "source_dir": str(source), "platforms": ["codex"]}), encoding="utf-8")
        self.cli("install", "--platforms", platforms, "--workflow", "github-flow", source=source)
        self.state = self.root / (".agents" if legacy else ".multi-agent-collab")
        self.tools = self.root / "tools" if legacy else self.state / "tools"
        self.metadata = self.state / "install.json"
        (self.state / "sessions/keep-record.md").write_text("# retained record\n- agent: codex\n- status: done\n- files: []\n", encoding="utf-8")
        (self.state / "worklog/2000-01-01.md").write_text("# retained worklog\n", encoding="utf-8")

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def assert_refused_zero_write(self, command="sync", *extra):
        before = self.snapshot()
        result = self.cli(command, *extra, expected=2)
        self.assertIn("update / install", result.stderr)
        self.assertEqual(self.snapshot(), before)
        return result

    def same_version_old_source(self):
        source = self.base / "same-version-old-source"
        names = {src for src, _target, _zone in inst.LEGACY_CORE_FILES}
        names.update(src for src, _target, _zone in inst.platform_files(inst.ALL_PLATFORMS))
        names.add("install.py")
        for name in names:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(KIT / name, target)
        path = source / "tools/agents_session.py"
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        lines = text.splitlines(keepends=True)
        path.write_text("".join(lines[:function.lineno - 1]) + LEGACY_MAIN + "".join(lines[function.end_lineno:]), encoding="utf-8")
        return source

    def test_actual_1_7_install_refuses_sync_then_full_update_allows_it(self):
        value = os.environ.get("MULTI_AGENT_TEST_OLD_KIT")
        if not value:
            self.skipTest("Actual 1.7 fixture supplied via MULTI_AGENT_TEST_OLD_KIT; portable old-parser cases run separately")
        source = Path(value).resolve()
        self.assertTrue((source / "install.py").is_file(), "Provided actual old kit is missing")
        self.assertIn('KIT_VERSION = "1.7.0"', (source / "install.py").read_text(encoding="utf-8"))
        self.install(source=source)
        self.assert_refused_zero_write("sync", "--platforms", "cursor")
        self.assert_refused_zero_write("sync", "--platforms", "cursor", "--dry-run")
        self.cli("update")
        self.cli("sync", "--platforms", "cursor")
        data = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertEqual(data["kit_version"], inst.KIT_VERSION)
        self.assertIn("cursor", data["platforms"])
        self.assertTrue((self.tools / "agents_jobs.py").is_file())
        self.assertTrue((self.tools / "agents_processes.py").is_file())

    def test_same_version_old_parser_is_not_accepted_by_version_or_keywords(self):
        source = self.same_version_old_source()
        self.install(source=source)
        self.assertEqual(json.loads(self.metadata.read_text(encoding="utf-8"))["kit_version"], inst.KIT_VERSION)
        self.assertIn("activate", (self.tools / "agents_session.py").read_text(encoding="utf-8"))
        self.assert_refused_zero_write("sync", "--platforms", "all")
        self.assert_refused_zero_write("mirror")
        self.assert_refused_zero_write("mirror", "--dry-run")
        self.cli("update")
        self.cli("sync", "--platforms", "all")
        self.assertEqual(set(json.loads(self.metadata.read_text(encoding="utf-8"))["platforms"]), set(inst.ALL_PLATFORMS))

    def test_each_missing_common_tool_is_rejected_without_new_files(self):
        self.install(platforms="none")
        names = [Path(src).stem for src, _target, _zone in inst.LEGACY_CORE_FILES
                 if src.startswith("tools/agents_") and src.endswith(".py")]
        for name in names:
            with self.subTest(tool=name):
                path = self.tools / (name + ".py")
                original = path.read_bytes()
                path.unlink()
                try:
                    self.assert_refused_zero_write("sync", "--platforms", "cursor")
                finally:
                    path.write_bytes(original)

    def test_damaged_or_oversized_core_never_writes_entries(self):
        self.install()
        path = self.tools / "agents_session.py"
        original = path.read_bytes()
        for damaged in (b"", b"\xff", b"def invalid(:\n", b"#" + b"a" * inst.SYNC_CORE_MAX_BYTES):
            with self.subTest(size=len(damaged)):
                path.write_bytes(damaged)
                try:
                    self.assert_refused_zero_write("sync", "--platforms", "cursor", "--dry-run")
                finally:
                    path.write_bytes(original)

    def test_stubbed_import_dependency_is_not_treated_as_complete_library(self):
        self.install()
        (self.tools / "agents_activity.py").write_text("pass\n", encoding="utf-8")
        self.assert_refused_zero_write("sync", "--platforms", "cursor")

    def test_supervisor_without_an_entry_is_not_treated_as_complete_core(self):
        self.install()
        (self.tools / "agents_processes.py").write_text("pass\n", encoding="utf-8")
        self.assert_refused_zero_write("sync", "--platforms", "cursor")

    def test_comments_and_missing_hash_are_preserved_without_reclaiming_core(self):
        self.install()
        data = json.loads(self.metadata.read_text(encoding="utf-8"))
        retained = {}
        for name in ("agents_session", "agents_jobs", "agents_notifications"):
            path = self.tools / (name + ".py")
            path.write_bytes(path.read_bytes() + b"\n# Harmless local customization\n")
            retained[path] = path.read_bytes()
        ignored = (self.tools / "agents_activity.py").relative_to(self.root).as_posix()
        data["managed_hashes"].pop(ignored)
        self.metadata.write_text(json.dumps(data), encoding="utf-8")
        old_hashes = dict(data["managed_hashes"])
        self.cli("sync", "--platforms", "claude")
        after = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertNotIn(ignored, after["managed_hashes"])
        for path, content in retained.items():
            self.assertEqual(path.read_bytes(), content)
            relative = path.relative_to(self.root).as_posix()
            self.assertEqual(after["managed_hashes"][relative], old_hashes[relative])

    def test_guard_parses_user_core_without_executing_its_top_level_code(self):
        self.install()
        marker = self.root / "core-must-not-be-executed"
        path = self.tools / "agents_session.py"
        original = path.read_bytes()
        text = original.decode("utf-8").replace("import argparse\n", "import argparse\nfrom pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unexpected')\n", 1)
        path.write_text(text, encoding="utf-8")
        self.cli("sync", "--platforms", "cursor")
        self.assertFalse(marker.exists(), "Compatibility guard executed a user-modified tool")
        after = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertEqual(after["managed_hashes"][path.relative_to(self.root).as_posix()], hashlib.sha256(original).hexdigest())

    def test_modern_and_legacy_current_core_support_minimal_and_all_sync(self):
        self.install(legacy=True, platforms="none")
        original = {path: path.read_bytes() for path in self.tools.glob("agents_*.py")}
        self.cli("sync", "--platforms", "all")
        self.cli("mirror")
        self.assertFalse((self.root / ".multi-agent-collab").exists())
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_internal_and_external_tool_directory_aliases_are_rejected(self):
        self.install()
        original = self.tools.with_name("saved-tools")
        self.tools.resolve().relative_to(self.base)
        original.resolve().relative_to(self.base)
        self.tools.rename(original)
        for target in (self.root / "shadow-tools", self.base / "outside-tools"):
            target.resolve().relative_to(self.base)
            shutil.copytree(original, target)
            directory_alias(self, self.tools, target)
            self.aliases.append(self.tools)
            try:
                self.assert_refused_zero_write("sync", "--platforms", "cursor")
            finally:
                self.tools.rmdir() if os.name == "nt" and not self.tools.is_symlink() else self.tools.unlink()
                self.aliases.remove(self.tools)


if __name__ == "__main__":
    unittest.main()
