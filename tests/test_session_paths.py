"""Real path redirection must not release claims or launch a child elsewhere."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "tools"))
import agents_activity as activity
import agents_run as runner
import agents_session as sessions
from test_session_safety import directory_alias
from selftest import rmtree_readonly

PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")


class SessionPathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-session-paths-")).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.aliases = []
        self.git = sessions.board.resolve_git()
        if not self.git:
            self.addCleanup(self.cleanup)
            self.skipTest("Git required for real session path validation")
        for arguments in (("init", "-q", "-b", "main"),
                          ("config", "user.name", "Session Path Trial"),
                          ("config", "user.email", "session-path@example.invalid")):
            result = subprocess.run([self.git, *arguments], cwd=str(self.root), capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        (self.root / "marker.txt").write_bytes(b"original-business-marker\n")
        for arguments in (("add", "marker.txt"), ("commit", "-qm", "temporary path fixture"),
                          ("switch", "-c", "feature/session-paths")):
            result = subprocess.run([self.git, *arguments], cwd=str(self.root), capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.state = self.root / ".agents"
        for name in ("sessions", "inbox", "worklog"):
            (self.state / name).mkdir(parents=True)
        (self.state / "policy.json").write_text(json.dumps({"schema_version": 1, "workflow": "github-flow",
                    "base_branch": "main", "task_prefixes": ["feature/"],
                    "protected_branches": ["main", "master", "develop"]}) + "\n", encoding="utf-8")
        self.identifier = "codex-path-owner"
        self.record = self.state / "sessions" / (self.identifier + ".md")
        old = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        self.record.write_text("# Session\n\n- agent: codex\n- branch: feature/session-paths\n"
                               "- task: validate real path redirection\n- status: active\n- files: [\"marker.txt\"]\n"
                               "- heartbeat: " + old + "\n- workspace: " + str(self.root)
                               + "\n- acknowledged_inbox: []\n- acknowledged_inbox_versions: {}\n"
                               "\n## Original notes\nKeep this session body.\n", encoding="utf-8")
        self.log = self.state / "worklog" / (datetime.now().strftime("%Y-%m-%d") + ".md")
        self.log.write_bytes(b"# Existing worklog\nKeep the complete original worklog.\n")
        self.question = self.state / "inbox/Q-original.md"
        self.question.write_bytes(b"# Earlier item\n- from: another-agent\n- to: any\n- status: answered\n"
                                  b"\n## Replies\nOriginal reply, no pending human instruction.\n")

    def tearDown(self):
        self.cleanup()

    def cleanup(self):
        base = self.base.resolve()
        if base.parent != Path(tempfile.gettempdir()).resolve() or not base.name.startswith("mac-session-paths-"):
            raise RuntimeError("Refusing cleanup outside the explicitly created temporary fixture")
        for alias in reversed(self.aliases):
            self.unlink_alias(alias)
        if base.exists():
            rmtree_readonly(base)

    def check_inside_fixture(self, path):
        path.resolve().relative_to(self.base)

    def link(self, alias, target):
        self.check_inside_fixture(alias.parent)
        self.check_inside_fixture(target)
        directory_alias(self, alias, target)
        if alias not in self.aliases:
            self.aliases.append(alias)

    def unlink_alias(self, alias):
        try:
            info = alias.lstat()
        except FileNotFoundError:
            return
        linked = stat.S_ISLNK(info.st_mode) or (os.name == "nt" and
                  bool(getattr(info, "st_file_attributes", 0) & 0x400))
        if not linked:
            return
        self.check_inside_fixture(alias.parent)
        self.check_inside_fixture(alias)
        if os.name == "nt" and not stat.S_ISLNK(info.st_mode):
            alias.rmdir()  # Removes only the verified junction, never its target.
        else:
            alias.unlink()

    def cli(self, *arguments):
        result = subprocess.run([PY, "-B", str(KIT / "tools/agents_session.py"), "--root", str(self.root),
                                 *arguments, "--json"], cwd=str(self.root), env=ENV,
                                capture_output=True, encoding="utf-8", timeout=90)
        return result, json.loads(result.stdout)

    def alias_commands_preserve_all_records(self, target):
        original = self.state / "sessions-original"
        self.check_inside_fixture(self.record.parent)
        self.check_inside_fixture(original)
        self.check_inside_fixture(target)
        self.record.parent.rename(original)
        shutil.copytree(original, target)
        target_record = target / self.record.name
        with target_record.open("a", encoding="utf-8") as stream:
            stream.write("\n## Shadow target\nThis distinct record must also be preserved.\n")
        self.link(self.state / "sessions", target)
        physical_original = original / self.record.name
        protected = [physical_original, target_record, self.log, self.question]
        before = {path: path.read_bytes() for path in protected}
        for arguments in (("finish", self.identifier, "--agent", "codex", "--standby", "--no-activity",
                           "--summary", "must not release an aliased claim", "--tests", "not completed"),
                          ("heartbeat", self.identifier, "--agent", "codex", "--no-activity", "--note", "must not rewrite shadow"),
                          ("resume", self.identifier, "--agent", "codex", "--note", "must not follow redirected record")):
            with self.subTest(command=arguments[0], target=str(target)):
                result, payload = self.cli(*arguments)
                self.assertEqual(result.returncode, 2, payload)
                self.assertFalse(payload["ok"])
                self.assertRegex(payload["error"], "链接|别名|工作区外部|alias|junction|symlink")
                for path, content in before.items():
                    self.assertEqual(path.read_bytes(), content, str(path))
                for path in (physical_original, target_record):
                    fields = sessions.board.parse_kv(path)
                    self.assertEqual(fields["status"], "active")
                    self.assertEqual(json.loads(fields["files"]), ["marker.txt"])

    def test_internal_session_directory_junction_never_rewrites_or_releases_shadow(self):
        self.alias_commands_preserve_all_records(self.root / "shadow-records")

    def test_external_session_directory_junction_never_rewrites_or_releases_target(self):
        self.alias_commands_preserve_all_records(self.base / "outside-workspace-records")

    def test_run_root_redirection_after_notice_cannot_launch_in_new_directory(self):
        target = self.base / "unowned-directory"
        target.mkdir()
        target_marker = target / "marker.txt"
        target_marker.write_bytes(b"distinct-unowned-marker\n")
        alias = self.base / "requested-workspace"
        self.link(alias, self.root)
        original_marker = self.root / "marker.txt"
        before_original = original_marker.read_bytes()
        before_target = target_marker.read_bytes()
        redirected = []

        def redirect(_root, _agent, _identifier, _roots):
            if not redirected:
                self.unlink_alias(alias)
                self.link(alias, target)
                redirected.append(True)

        args = argparse.Namespace(root=str(alias), agent="codex", session=self.identifier,
                                  command=[PY, "-B", "-c", "from pathlib import Path; Path('marker.txt').write_bytes(b'unsafe child launched')"])
        # Only the notification boundary injects a real filesystem race.
        # Popen and the child command remain real, so removing the guard fails the test.
        with mock.patch.object(runner, "_notice", side_effect=redirect):
            with self.assertRaisesRegex(sessions.ConflictError, "重定向"):
                runner.execute_tracked(args)
        self.assertEqual(redirected, [True])
        self.assertEqual(alias.resolve(), target)
        self.assertEqual(original_marker.read_bytes(), before_original)
        self.assertEqual(target_marker.read_bytes(), before_target)
        fields = sessions.board.parse_kv(self.record)
        self.assertEqual(fields["status"], "active")
        self.assertEqual(json.loads(fields["files"]), ["marker.txt"])

    def test_external_record_edit_before_replace_is_preserved(self):
        original = self.record.read_bytes()
        external = original + b"\n## Concurrent external edit\nKeep this exact externally written data.\n"
        original_assert = activity._assert_original
        checked = []

        def edit_at_final_check(root, identifier, path, expected, info):
            checked.append(path)
            if len(checked) == 2:
                self.assertEqual(path, self.record)
                path.write_bytes(external)
            return original_assert(root, identifier, path, expected, info)

        before_log = self.log.read_bytes()
        before_question = self.question.read_bytes()
        args = argparse.Namespace(root=str(self.root), cmd="heartbeat", agent="codex", session=self.identifier,
                                  files=None, note="requested heartbeat before concurrent edit", no_activity=True)
        with mock.patch.object(activity, "_assert_original", side_effect=edit_at_final_check):
            with self.assertRaisesRegex(ValueError, "外部修改"):
                sessions.execute(args)
        self.assertEqual(len(checked), 2)
        self.assertEqual(self.record.read_bytes(), external)
        self.assertEqual(self.log.read_bytes(), before_log)
        self.assertEqual(self.question.read_bytes(), before_question)
        fields = sessions.board.parse_kv(self.record)
        self.assertEqual(fields["status"], "active")
        self.assertEqual(json.loads(fields["files"]), ["marker.txt"])
        self.assertEqual(list(self.record.parent.glob(".*.activity-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
