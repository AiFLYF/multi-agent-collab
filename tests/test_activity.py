"""Activity evidence must never resurrect a task or override another writer."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from selftest import rmtree_readonly
import agents_activity as activity
import agents_session as sessions
from test_session_safety import directory_alias


class TestActivity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="mac-activity-tests-")).resolve()
        cls.root = cls.base / "project"
        cls.root.mkdir()
        cls.git = sessions.board.resolve_git()
        cls.branch = "feature/activity-test"
        for arguments in (("init", "-q"), ("config", "user.name", "Activity Test"),
                          ("config", "user.email", "activity@example.invalid")):
            cls.git_call(*arguments)
        (cls.root / "README.md").write_text("# Activity fixture\n", encoding="utf-8")
        (cls.root / ".gitignore").write_text(".agents/\n.multi-agent-collab/\n", encoding="utf-8")
        cls.git_call("add", "README.md", ".gitignore")
        cls.git_call("commit", "-q", "-m", "test: activity fixture")
        cls.git_call("switch", "-c", cls.branch)
        cls.state = cls.root / ".agents"
        for folder in ("sessions", "inbox", "worklog"):
            (cls.state / folder).mkdir(parents=True)
        (cls.state / "coordination.lock").write_bytes(b"0")

    @classmethod
    def tearDownClass(cls):
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-activity-tests-"):
            raise RuntimeError("Refusing activity test cleanup outside its temporary workspace")
        rmtree_readonly(cls.base)

    @classmethod
    def git_call(cls, *arguments, root=None):
        result = subprocess.run([cls.git, *arguments], cwd=str(root or cls.root),
                                capture_output=True, encoding="utf-8", errors="replace", timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout.strip()

    def setUp(self):
        self.identifier = "codex-activity-test"
        self.path = self.state / "sessions" / (self.identifier + ".md")
        self.now = datetime.now().replace(microsecond=0)
        self.original = ("# Session: " + self.identifier + "\n\n- agent: codex\n- branch: " + self.branch + "\n"
                         "- task: Validate activity\n- status: active\n- files: [\"src/app.py\"]\n"
                         "- progress: Preserve progress\n- acknowledged_inbox: []\n"
                         "- heartbeat: 2000-01-01 00:00\n\n## Notes\n\nkeep-original!\n"
                         "- status: done is a body example\n- heartbeat: body example stays\n")
        self.path.write_bytes(self.original.encode("utf-8"))

    def touch(self, **kwargs):
        parameters = dict(now=self.now, branch=self.branch)
        parameters.update(kwargs)
        return activity.touch_activity(self.root, "codex", self.identifier, "agents_messages.read", **parameters)

    def record(self):
        return sessions.board.parse_kv(self.path)

    def snapshot(self):
        return self.path.read_bytes(), self.path.stat().st_mtime_ns

    def write_fields(self, **fields):
        self.path.write_text(sessions.put_fields(self.path.read_text(encoding="utf-8"), fields), encoding="utf-8")

    def test_anonymous_or_incomplete_identity_does_no_io_or_git(self):
        with mock.patch.object(activity, "_modules", side_effect=AssertionError("Anonymous activity imported/scanned tools")):
            for agent, identifier in ((None, None), ("codex", None), (None, self.identifier), ("", self.identifier)):
                with self.subTest(agent=agent, identifier=identifier):
                    self.assertFalse(activity.touch_activity(self.root, agent, identifier, "agents_status")["updated"])

    def test_explicit_activity_changes_only_presence_fields(self):
        before = self.record()
        result = self.touch()
        after = self.record()
        self.assertTrue(result["updated"])
        self.assertEqual(result["at"], self.now.isoformat(timespec="seconds"))
        self.assertEqual(after["last_command"], "agents_messages.read")
        self.assertEqual(after["last_activity"], result["at"])
        self.assertEqual(after["heartbeat"], self.now.strftime("%Y-%m-%d %H:%M"))
        for key in before:
            if key != "heartbeat":
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.path.read_bytes().split(b"## Notes", 1)[1], self.original.encode().split(b"## Notes", 1)[1])

    def test_blocked_activity_never_resumes_task_or_releases_files(self):
        self.write_fields(status="blocked")
        self.assertTrue(self.touch()["updated"])
        self.assertEqual(self.record()["status"], "blocked")
        self.assertEqual(self.record()["files"], '["src/app.py"]')

    def test_standby_remains_unclaimed_across_a_branch_change(self):
        self.write_fields(status="standby", files="[]")
        self.git_call("switch", "-c", "feature/activity-other")
        try:
            self.assertTrue(self.touch()["updated"])
            self.assertEqual(self.record()["status"], "standby")
            self.assertEqual(self.record()["files"], "[]")
            self.assertEqual(self.record()["branch"], self.branch)
        finally:
            self.git_call("switch", self.branch)

    def test_terminal_records_are_not_resurrected_even_with_force(self):
        for state in ("done", "closed", "abandoned", "superseded", "cancelled"):
            with self.subTest(state=state):
                self.write_fields(status=state)
                before = self.snapshot()
                self.assertEqual(self.touch(force=True)["reason"], "closed")
                self.assertEqual(self.snapshot(), before)

    def test_wrong_owner_and_missing_session_are_zero_write_errors(self):
        before = self.snapshot()
        for owner, identifier in (("claude", self.identifier), ("codex", "codex-missing")):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                activity.touch_activity(self.root, owner, identifier, "agents_status", now=self.now, branch=self.branch)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.state / "sessions/codex-missing.md").exists())

    def test_command_payload_or_secret_is_never_stored_or_echoed(self):
        secret = "agents_session.run --api-key secret-test-value"
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            activity.touch_activity(self.root, "codex", self.identifier, secret, now=self.now)
        self.assertNotIn("secret-test-value", str(caught.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertIn("agents_session.activate", activity.ACTIVITY_COMMANDS)
        self.assertIn("agents_session.run", activity.ACTIVITY_COMMANDS)

    def test_invalid_identity_is_not_used_as_a_path(self):
        before = self.snapshot()
        for owner, identifier in (("codex\n- status: done", self.identifier), ("codex", "../outside")):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                activity.touch_activity(self.root, owner, identifier, "agents_status", now=self.now)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_state_and_inconsistent_file_claims_are_rejected(self):
        for status, files in (("waiting-unknown", '["src/app.py"]'), ("active", "[]"),
                              ("blocked", "[]"), ("standby", '["src/app.py"]'), ("standby", "not-json")):
            with self.subTest(status=status, files=files):
                self.path.write_text(self.original, encoding="utf-8")
                self.write_fields(status=status, files=files)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.touch()
                self.assertEqual(self.snapshot(), before)

    def test_conflicting_header_fields_do_not_get_repaired_by_activity(self):
        for duplicate in ("- status: done", "- agent: claude", "- files: []"):
            with self.subTest(duplicate=duplicate):
                self.path.write_text(self.original.replace("\n\n## Notes", "\n" + duplicate + "\n\n## Notes"), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.touch()
                self.assertEqual(self.snapshot(), before)

    def test_metadata_budget_and_invalid_utf8_are_zero_write_errors(self):
        originals = [self.original.replace("\n\n## Notes", "\n- oversized: " + "x" * 65536 + "\n\n## Notes").encode("utf-8"),
                     self.original.encode("utf-8") + b"\xff"]
        for source in originals:
            with self.subTest(length=len(source)):
                self.path.write_bytes(source)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.touch()
                self.assertEqual(self.snapshot(), before)

    def test_invalid_options_are_rejected_before_record_io(self):
        with mock.patch.object(activity, "_modules", side_effect=AssertionError("Invalid options read state")):
            for options in ({"min_interval": -1}, {"min_interval": float("nan")}, {"force": "yes"}, {"branch": 12}, {"now": "not-a-time"}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    activity.touch_activity(self.root, "codex", self.identifier, "agents_status", **options)

    def test_workspace_field_cannot_claim_another_project(self):
        self.write_fields(workspace=str(self.base / "somewhere-else"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "工作区"):
            self.touch()
        self.assertEqual(self.snapshot(), before)

    def test_active_branch_mismatch_does_not_update_presence(self):
        self.git_call("switch", "-c", "feature/activity-mismatch")
        before = self.snapshot()
        try:
            with self.assertRaisesRegex(ValueError, "分支"):
                self.touch()
            self.assertEqual(self.snapshot(), before)
        finally:
            self.git_call("switch", self.branch)

    def test_cached_branch_is_rechecked_after_waiting_for_shared_lock(self):
        original_lock = sessions.coordination_lock
        before = self.snapshot()

        @contextmanager
        def switched_lock(primary):
            with original_lock(primary):
                self.git_call("switch", "-c", "feature/activity-during-wait")
                try:
                    yield
                finally:
                    self.git_call("switch", self.branch)

        with mock.patch.object(sessions, "coordination_lock", switched_lock):
            with self.assertRaisesRegex(ValueError, "分支"):
                self.touch()
        self.assertEqual(self.snapshot(), before)

    def test_recent_activity_preserves_bytes_mtime_and_skips_topology_queries(self):
        self.assertTrue(self.touch()["updated"])
        before = self.snapshot()
        with mock.patch.object(sessions, "worktree_roots", side_effect=AssertionError("Throttled activity queried Git topology")):
            self.assertEqual(self.touch(now=self.now + timedelta(seconds=59))["reason"], "throttled")
        self.assertEqual(self.snapshot(), before)

    def test_interval_boundary_records_activity_at_sixty_seconds(self):
        self.assertTrue(self.touch()["updated"])
        self.assertTrue(self.touch(now=self.now + timedelta(seconds=60))["updated"])
        self.assertEqual(self.record()["last_activity"], (self.now + timedelta(seconds=60)).isoformat(timespec="seconds"))

    def test_recent_manual_heartbeat_also_prevents_redundant_write(self):
        self.write_fields(heartbeat=self.now.isoformat(timespec="seconds"))
        before = self.snapshot()
        self.assertEqual(self.touch()["reason"], "throttled")
        self.assertEqual(self.snapshot(), before)

    def test_force_records_run_boundaries_but_identical_bytes_are_unchanged(self):
        self.assertTrue(self.touch()["updated"])
        self.assertTrue(self.touch(now=self.now + timedelta(seconds=2), force=True)["updated"])
        before = self.snapshot()
        self.assertEqual(self.touch(now=self.now + timedelta(seconds=2), force=True)["reason"], "unchanged")
        self.assertEqual(self.snapshot(), before)

    def test_timezone_aware_now_is_recorded_in_local_time(self):
        aware = self.now.astimezone(timezone.utc)
        self.assertTrue(self.touch(now=aware)["updated"])
        self.assertEqual(sessions.board.parse_time(self.record()["last_activity"]), self.now)

    def test_bom_crlf_and_untouched_metadata_style_are_preserved(self):
        source = self.original.replace("- heartbeat:", "* **heartbeat**：", 1).replace("\n", "\r\n")
        self.path.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))
        body = self.path.read_bytes().split(b"## Notes", 1)[1]
        self.assertTrue(self.touch()["updated"])
        written = self.path.read_bytes()
        self.assertTrue(written.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(written.split(b"## Notes", 1)[1], body)
        self.assertNotIn(b"\n", written.replace(b"\r\n", b""))
        self.assertIn("* **heartbeat**：".encode("utf-8"), written)

    def test_equal_activity_duplicates_update_together_and_body_is_untouched(self):
        duplicate = "- heartbeat: 2000-01-01 00:00\n- last_activity: 2000-01-01T00:00:00\n* **last_activity**: 2000-01-01T00:00:00"
        self.path.write_text(self.original.replace("- heartbeat: 2000-01-01 00:00", duplicate), encoding="utf-8")
        self.assertTrue(self.touch()["updated"])
        written = self.path.read_text(encoding="utf-8")
        self.assertEqual(written.count(self.now.isoformat(timespec="seconds")), 2)
        self.assertIn("- heartbeat: body example stays", written)

    def test_conflicting_activity_duplicates_are_zero_write(self):
        self.path.write_text(self.original.replace("\n\n## Notes", "\n- last_activity: 2000-01-01T00:00:00\n- last_activity: 2001-01-01T00:00:00\n\n## Notes"), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "重复.*冲突"):
            self.touch()
        self.assertEqual(self.snapshot(), before)

    def test_same_size_and_mtime_external_edit_before_replace_is_preserved(self):
        original_mkstemp = activity.tempfile.mkstemp
        before_info = self.path.stat()
        foreign = self.original.replace("keep-original!", "external-edit!").encode("utf-8")
        self.assertEqual(len(foreign), len(self.original.encode()))

        def external_edit(*args, **kwargs):
            self.path.write_bytes(foreign)
            os.utime(self.path, ns=(before_info.st_atime_ns, before_info.st_mtime_ns))
            return original_mkstemp(*args, **kwargs)

        with mock.patch.object(activity.tempfile, "mkstemp", external_edit):
            with self.assertRaisesRegex(ValueError, "外部修改"):
                self.touch()
        self.assertEqual(self.path.read_bytes(), foreign)
        self.assertEqual(list(self.path.parent.glob(".*.activity-*.tmp")), [])

    def test_editor_note_written_while_waiting_is_merged_under_lock(self):
        original_lock = sessions.coordination_lock

        @contextmanager
        def edited_lock(primary):
            with original_lock(primary):
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write("\nHuman note added while waiting.\n")
                yield

        with mock.patch.object(sessions, "coordination_lock", edited_lock):
            self.assertTrue(self.touch()["updated"])
        self.assertIn("Human note added while waiting.", self.path.read_text(encoding="utf-8"))

    def test_failed_replacement_preserves_record_and_cleans_own_temporary(self):
        before = self.snapshot()
        with mock.patch.object(activity.os, "replace", side_effect=OSError("injected replace failure")):
            with self.assertRaisesRegex(OSError, "injected replace failure"):
                self.touch()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.path.parent.glob(".*.activity-*.tmp")), [])

    @contextmanager
    def shared_reader_then_edit(self, edit):
        original_replace = activity.os.replace
        reader = self.path.open("rb")
        denials = []

        def observed_replace(source, destination):
            try:
                return original_replace(source, destination)
            except PermissionError as error:
                self.assertIn(error.winerror, (5, 32))
                if not denials:
                    denials.append(error.winerror)
                    reader.close()
                    edit(Path(source))
                raise

        try:
            with mock.patch.object(activity.os, "replace", observed_replace):
                yield denials
        finally:
            reader.close()

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_preserves_new_editor_bytes(self):
        foreign = self.original.encode("utf-8") + b"\nA new editor note stays.\n"
        edited = []

        def edit(_temporary):
            self.path.write_bytes(foreign)
            edited.append(self.snapshot())

        with self.shared_reader_then_edit(edit) as denials:
            with self.assertRaisesRegex(ValueError, "外部修改"):
                self.touch(force=True)
        self.assertTrue(denials)
        self.assertEqual(self.snapshot(), edited[0])
        self.assertEqual(list(self.path.parent.glob(".*.activity-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_preserves_editor_permission_change(self):
        edited = []

        def edit(_temporary):
            os.chmod(self.path, stat.S_IREAD)
            edited.append((self.snapshot(), self.path.stat().st_mode))

        try:
            with self.shared_reader_then_edit(edit) as denials:
                with self.assertRaisesRegex(ValueError, "外部修改"):
                    self.touch(force=True)
            self.assertTrue(denials)
            self.assertEqual((self.snapshot(), self.path.stat().st_mode), edited[0])
            self.assertEqual(list(self.path.parent.glob(".*.activity-*.tmp")), [])
        finally:
            os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_preserves_modified_temporary_with_same_metadata(self):
        before = self.snapshot()
        edited = []

        def edit(temporary):
            info = temporary.stat()
            payload = temporary.read_bytes()
            temporary.write_bytes(b"X" + payload[1:])
            os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
            edited.append((temporary, temporary.read_bytes(), temporary.stat().st_mtime_ns))

        try:
            with self.shared_reader_then_edit(edit) as denials:
                with self.assertRaisesRegex(ValueError, "临时文件被修改"):
                    self.touch(force=True)
            self.assertTrue(denials)
            self.assertEqual(self.snapshot(), before)
            temporary, payload, modified_at = edited[0]
            self.assertEqual((temporary.read_bytes(), temporary.stat().st_mtime_ns), (payload, modified_at))
        finally:
            for temporary, _payload, _modified_at in edited:
                temporary.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_preserves_reused_temporary_name(self):
        before = self.snapshot()
        edited = []

        def edit(temporary):
            info, payload = temporary.stat(), temporary.read_bytes()
            saved = temporary.with_suffix(".saved")
            temporary.rename(saved)
            temporary.write_bytes(payload)
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
            os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
            self.assertNotEqual(temporary.stat().st_ino, saved.stat().st_ino)
            edited.append((temporary, saved, payload))

        try:
            with self.shared_reader_then_edit(edit) as denials:
                with self.assertRaisesRegex(ValueError, "临时文件被修改"):
                    self.touch(force=True)
            self.assertTrue(denials)
            self.assertEqual(self.snapshot(), before)
            temporary, saved, payload = edited[0]
            self.assertEqual(temporary.read_bytes(), payload)
            self.assertEqual(saved.read_bytes(), payload)
        finally:
            for temporary, saved, _payload in edited:
                temporary.unlink(missing_ok=True)
                saved.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_preserves_temporary_permission_change(self):
        before = self.snapshot()
        edited = []

        def edit(temporary):
            os.chmod(temporary, stat.S_IREAD)
            edited.append((temporary, temporary.read_bytes(), temporary.stat().st_mode))

        try:
            with self.shared_reader_then_edit(edit) as denials:
                with self.assertRaisesRegex(ValueError, "临时文件被修改"):
                    self.touch(force=True)
            self.assertTrue(denials)
            self.assertEqual(self.snapshot(), before)
            temporary, payload, mode = edited[0]
            self.assertEqual((temporary.read_bytes(), temporary.stat().st_mode), (payload, mode))
        finally:
            for temporary, _payload, _mode in edited:
                os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
                temporary.unlink(missing_ok=True)

    @unittest.skipUnless(os.name == "nt", "Windows read handles deny deletion")
    def test_windows_retry_directory_redirect_preserves_external_files(self):
        folder, original = self.path.parent, self.path.read_bytes()
        saved = folder.with_name("saved-retry-sessions")
        outside = self.base / "outside-retry"
        outside.mkdir()
        foreign_record = outside / self.path.name
        foreign_record.write_bytes(b"external record remains")
        foreign_temporary = []

        def edit(temporary):
            sentinel = outside / temporary.name
            sentinel.write_bytes(b"external temporary remains")
            foreign_temporary.append((sentinel, sentinel.stat().st_mtime_ns))
            folder.rename(saved)
            directory_alias(self, folder, outside)

        try:
            with self.shared_reader_then_edit(edit) as denials:
                with self.assertRaisesRegex(ValueError, "越界|symlink|junction"):
                    self.touch(force=True)
            self.assertTrue(denials)
            self.assertEqual(foreign_record.read_bytes(), b"external record remains")
            sentinel, modified_at = foreign_temporary[0]
            self.assertEqual((sentinel.read_bytes(), sentinel.stat().st_mtime_ns), (b"external temporary remains", modified_at))
            self.assertEqual((saved / self.path.name).read_bytes(), original)
        finally:
            if saved.exists():
                if folder.exists():
                    folder.rmdir()
                saved.rename(folder)
            for temporary in folder.glob(".*.activity-*.tmp"):
                temporary.unlink()

    def test_identical_bytes_in_a_replaced_session_are_not_overwritten(self):
        original_mkstemp = activity.tempfile.mkstemp
        before = self.path.read_bytes()
        saved = self.path.with_suffix(".saved")

        def replace_original(*args, **kwargs):
            self.path.rename(saved)
            self.path.write_bytes(before)
            return original_mkstemp(*args, **kwargs)

        try:
            with mock.patch.object(activity.tempfile, "mkstemp", replace_original):
                with self.assertRaisesRegex(ValueError, "外部修改"):
                    self.touch()
            self.assertEqual(self.path.read_bytes(), before)
        finally:
            saved.unlink(missing_ok=True)

    def test_modified_temporary_is_preserved_instead_of_installed_or_deleted(self):
        original_chmod = activity.os.chmod
        before = self.snapshot()
        touched = []

        def replace_temporary(path, mode):
            original_chmod(path, mode)
            path = Path(path)
            path.write_bytes(b"external temporary edit")
            touched.append(path)

        try:
            with mock.patch.object(activity.os, "chmod", replace_temporary):
                with self.assertRaisesRegex(ValueError, "临时文件被修改"):
                    self.touch()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(len(touched), 1)
            self.assertEqual(touched[0].read_bytes(), b"external temporary edit")
        finally:
            for temporary in touched:
                temporary.unlink(missing_ok=True)

    def test_session_directory_alias_never_updates_external_record(self):
        folder = self.path.parent
        saved = folder.with_name("saved-sessions")
        outside = self.base / "outside-alias"
        outside.mkdir()
        foreign_path = outside / self.path.name
        foreign_path.write_text(self.original, encoding="utf-8")
        before = foreign_path.read_bytes()
        folder.rename(saved)
        directory_alias(self, folder, outside)
        try:
            with self.assertRaisesRegex(ValueError, "越界|symlink|junction"):
                self.touch()
            self.assertEqual(foreign_path.read_bytes(), before)
        finally:
            if os.name == "nt":
                folder.rmdir()
            else:
                folder.unlink()
            saved.rename(folder)

    def test_directory_redirect_before_replace_preserves_foreign_names(self):
        original_chmod = activity.os.chmod
        folder = self.path.parent
        saved = folder.with_name("saved-redirect-sessions")
        outside = self.base / "outside-redirect"
        outside.mkdir()
        foreign_record = outside / self.path.name
        foreign_record.write_bytes(b"external record stays untouched")
        foreign_temporary = []
        original = self.path.read_bytes()

        def redirect_after_handle_closed(path, mode):
            original_chmod(path, mode)
            path = Path(path)
            sentinel = outside / path.name
            sentinel.write_bytes(b"external temporary stays untouched")
            foreign_temporary.append(sentinel)
            folder.rename(saved)
            directory_alias(self, folder, outside)

        try:
            with mock.patch.object(activity.os, "chmod", redirect_after_handle_closed):
                with self.assertRaisesRegex(ValueError, "越界|symlink|junction"):
                    self.touch()
            self.assertEqual(foreign_record.read_bytes(), b"external record stays untouched")
            self.assertEqual(foreign_temporary[0].read_bytes(), b"external temporary stays untouched")
            self.assertEqual((saved / self.path.name).read_bytes(), original)
        finally:
            if folder.exists() and saved.exists():
                if os.name == "nt":
                    folder.rmdir()
                else:
                    folder.unlink()
                saved.rename(folder)
            for path in folder.glob(".*.activity-*.tmp"):
                path.unlink()

    def test_non_regular_session_record_is_not_replaced(self):
        original = self.path.read_bytes()
        self.path.unlink()
        self.path.mkdir()
        try:
            with self.assertRaisesRegex(ValueError, "普通文件"):
                self.touch()
            self.assertTrue(self.path.is_dir())
        finally:
            self.path.rmdir()
            self.path.write_bytes(original)

    def test_claimed_alias_is_rejected_without_touching_activity(self):
        target = self.root / "src-claim"
        target.mkdir()
        alias = self.root / "alias-claim"
        directory_alias(self, alias, target)
        self.write_fields(files='["alias-claim/new.py"]')
        before = self.snapshot()
        try:
            with self.assertRaisesRegex(ValueError, "符号链接|junction"):
                self.touch()
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(list(target.iterdir()), [])
        finally:
            if os.name == "nt":
                alias.rmdir()
            else:
                alias.unlink()
            target.rmdir()

    def test_modern_private_layout_records_activity_without_touching_legacy(self):
        private = self.root / ".multi-agent-collab"
        (private / "sessions").mkdir(parents=True)
        (private / "install.json").write_text(json.dumps({"owner": "multi-agent-collab", "layout": ".multi-agent-collab"}), encoding="utf-8")
        modern = private / "sessions" / self.path.name
        modern.write_bytes(self.original.encode("utf-8"))
        legacy = self.snapshot()
        try:
            self.assertTrue(self.touch()["updated"])
            self.assertIn("last_activity", sessions.board.parse_kv(modern))
            self.assertEqual(self.snapshot(), legacy)
        finally:
            if private.resolve().parent != self.root.resolve() or private.is_symlink():
                raise RuntimeError("Refusing cleanup of aliased private activity fixture")
            rmtree_readonly(private)

    def test_duplicate_id_across_real_worktrees_never_updates_either_record(self):
        linked = self.base / "linked"
        self.git_call("worktree", "add", "-b", "feature/activity-linked", str(linked))
        linked_state = linked / ".agents/sessions"
        linked_state.mkdir(parents=True)
        other = linked_state / self.path.name
        other.write_text(self.original.replace(self.branch, "feature/activity-linked"), encoding="utf-8")
        before, other_before = self.snapshot(), other.read_bytes()
        with self.assertRaisesRegex(ValueError, "相同会话 ID"):
            self.touch()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(other.read_bytes(), other_before)
        other.unlink()
        linked_state.rmdir()
        linked_state.parent.rmdir()
        self.git_call("worktree", "remove", str(linked))

    def test_activity_invalidates_takeover_snapshot_and_proves_recent_use(self):
        old = datetime.now() - timedelta(hours=2)
        self.write_fields(heartbeat=old.strftime("%Y-%m-%d %H:%M"))
        os.utime(self.path, (old.timestamp(), old.timestamp()))
        before = sessions.takeover_snapshot([self.root], self.identifier)
        self.assertEqual(sessions.takeover_blockers(before), [])
        self.assertTrue(self.touch(force=True)["updated"])
        after = sessions.takeover_snapshot([self.root], self.identifier)
        self.assertNotEqual(before["record_sha256"], after["record_sha256"])
        self.assertNotEqual(before["heartbeat"], after["heartbeat"])
        self.assertTrue(any("15 分钟" in reason for reason in sessions.takeover_blockers(after)))

    def test_safe_wrapper_returns_separate_warning_without_output(self):
        with mock.patch.object(activity, "touch_activity", side_effect=OSError("activity unavailable")), \
                mock.patch("builtins.print", side_effect=AssertionError("activity printed a second response")):
            self.assertEqual(activity.safe_touch_activity(self.root, "codex", self.identifier, "agents_status"),
                             {"updated": False, "warning": "activity unavailable"})

    def test_future_activity_does_not_throttle_a_verified_current_call(self):
        now = datetime.now().replace(microsecond=0)
        self.write_fields(last_activity=(now + timedelta(minutes=5)).isoformat(timespec="seconds"))
        self.assertTrue(self.touch(now=now)["updated"])
        record = sessions.board.parse_kv(self.path)
        self.assertEqual(record["last_activity"], now.isoformat(timespec="seconds"))

    def test_public_field_updates_preserve_newlines_and_reject_line_injection(self):
        text = "# Session\r\n\r\n- agent: codex\r\n* **STATUS**： active\r\n\r\n## Progress\r\noriginal\r\n"
        updated = activity.update_session_fields(text, {"status": "blocked", "progress": "waiting"})
        self.assertIn("* **STATUS**： blocked\r\n", updated)
        self.assertIn("- progress: waiting\r\n", updated)
        self.assertTrue(updated.endswith("## Progress\r\noriginal\r\n"))
        for value in ("\n", "text\r\n- status: done", "\u2028", "\x00"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                activity.update_session_fields(text, {"progress": value})

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not applicable on Windows")
    def test_activity_replacement_preserves_record_permissions(self):
        os.chmod(self.path, 0o640)
        self.assertTrue(self.touch()["updated"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
