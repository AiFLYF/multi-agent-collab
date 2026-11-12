"""Message mutations must retain identity, original requests and archive history."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import unittest
from datetime import datetime, timedelta
from unittest import mock

from selftest import board, run
import agents_messages as messages
import agents_notifications as notifications
import agents_session as sessions
from test_notifications import _NotificationFixture


class TestMessageWriteSafety(_NotificationFixture):
    def setUp(self):
        super().setUp()
        self.assertEqual(run("git", "init", "-q", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "symbolic-ref", "HEAD", "refs/heads/feature/notifications",
                             cwd=self.root).returncode, 0)
        (self.root / ".git/info/exclude").write_text(".agents/\n", encoding="utf-8")
        (self.root / ".agents/coordination.lock").write_bytes(b"0")

    def action(self, command, *extra, identity=True):
        output, errors = io.StringIO(), io.StringIO()
        arguments = [command, "--root", str(self.root), "--json", *extra]
        if identity:
            arguments += ["--agent", "codex", "--session", self.session_id]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = messages.main(arguments)
        return rc, json.loads(output.getvalue()) if output.getvalue().strip() else {}, errors.getvalue()

    def expired_presence(self):
        path = self.root / ".agents/sessions" / (self.session_id + ".md")
        content = board.parse_kv(path)
        path.write_text(sessions.put_fields(path.read_text(encoding="utf-8"),
                        {"heartbeat": "2000-01-01 00:00", "last_activity": "2000-01-01T00:00:00"}),
                        encoding="utf-8")
        return path, content

    def old_message(self, name="Q-old.md", answered=None, root=None):
        root = root or self.root
        path = self.inbox(name, root=root, sender="human", target="codex", status="answered",
                          reply="Completed result.")
        old = datetime.now() - timedelta(days=45)
        path.write_text(sessions.put_fields(path.read_text(encoding="utf-8"), {
            "created": old.isoformat(timespec="seconds"),
            "answered": (answered or old).isoformat(timespec="seconds"),
        }), encoding="utf-8")
        os.utime(path, (old.timestamp(), old.timestamp()))
        return path

    def test_send_and_reply_refresh_activity_without_changing_task_state(self):
        for command in ("send", "reply"):
            with self.subTest(command=command):
                session, original = self.expired_presence()
                if command == "send":
                    extra = ["--to", "any", "--body", "Check the interface."]
                else:
                    message = self.inbox("Q-presence.md", target=self.session_id)
                    extra = [message.name, "--body", "Review in progress.", "--status", "open"]
                rc, data, errors = self.action(command, *extra)
                self.assertEqual(rc, 0, (data, errors))
                self.assertNotIn("activity_warning", data)
                after = board.parse_kv(session)
                self.assertEqual(after["last_command"], "agents_messages." + command)
                self.assertEqual(after["status"], original["status"])
                self.assertEqual(after["files"], original["files"])

    def test_sender_closed_while_waiting_for_lock_cannot_send(self):
        session = self.root / ".agents/sessions" / (self.session_id + ".md")
        original_lock = sessions.coordination_lock

        @contextlib.contextmanager
        def changed_sender(primary):
            with original_lock(primary):
                session.write_text(sessions.put_fields(session.read_text(encoding="utf-8"),
                                   {"status": "done"}), encoding="utf-8")
                yield

        with mock.patch.object(sessions, "coordination_lock", changed_sender):
            rc, data, _errors = self.action("send", "--to", "any", "--body", "Stale sender.")
        self.assertEqual(rc, 2, data)
        self.assertEqual(list((self.root / ".agents/inbox").glob("Q-*.md")), [])
        self.assertEqual(board.parse_kv(session)["status"], "done")

    def test_reply_default_preserves_unfinished_human_request(self):
        path = self.inbox("Q-human-progress.md", sender="human", target=self.session_id)
        rc, data, errors = self.action("reply", path.name, "--body", "I am verifying the result.")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(board.parse_kv(path)["status"], "open")
        self.assertIn(path.name, notifications.human_requests([self.root], "codex", self.session_id))

    def test_reply_preserves_bom_newlines_mode_and_fenced_request(self):
        path = self.inbox("Q-preserve.md", target=self.session_id,
                          body="Example:\n```markdown\n## 回复\nexample only\n```\nKeep this requirement.")
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        os.chmod(path, 0o640)
        original = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        version = self.items()[0]["request_version"]
        rc, data, errors = self.action("reply", path.name, "--body", "Verified result.", "--status", "answered")
        self.assertEqual(rc, 0, (data, errors))
        after = path.read_bytes()
        self.assertTrue(after.startswith(b"\xef\xbb\xbf"))
        request = original.split(b"\r\n---\r\n", 1)[0].split(b"\r\n## ", 1)[1]
        self.assertIn(request, after)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        current = notifications.scan_messages(self.root, "codex", self.session_id,
                                             roots=[self.root], include_closed=True)[1][0]
        self.assertEqual(current["request_version"], version)

    def test_reply_keeps_external_edit_before_replacement(self):
        path = self.inbox("Q-edited.md", target=self.session_id)
        original_append = messages._append_reply
        changed = path.read_bytes().replace(b"Please check compatibility.", b"Changed requirement.")

        def edited(*args):
            path.write_bytes(changed)
            return original_append(*args)

        with mock.patch.object(messages, "_append_reply", edited):
            rc, data, _errors = self.action("reply", path.name, "--body", "Answer to old content.")
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), changed)

    def test_archive_apply_requires_an_explicit_session(self):
        path = self.old_message()
        original = path.read_bytes()
        rc, data, _errors = self.action("archive", "--apply", identity=False)
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), original)

    def test_archive_keeps_recent_answer_to_old_request(self):
        path = self.old_message(answered=datetime.now())
        rc, data, errors = self.action("archive", "--apply")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(data["moved"], [])
        self.assertTrue(path.is_file())

    def test_archive_rechecks_status_after_waiting_for_lock(self):
        path = self.old_message()
        changed = path.read_bytes().replace(b"status: answered", b"status: open")
        original_lock = sessions.coordination_lock

        @contextlib.contextmanager
        def reopened(primary):
            with original_lock(primary):
                path.write_bytes(changed)
                yield

        with mock.patch.object(sessions, "coordination_lock", reopened):
            rc, data, _errors = self.action("archive", "--apply")
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), changed)

    def test_archive_keeps_messages_referenced_by_unfinished_handoff(self):
        path = self.old_message()
        owner = self.session("claude-blocked", agent="claude")
        owner.write_text(sessions.put_fields(owner.read_text(encoding="utf-8"), {
            "status": "blocked", "handoff_inbox": ".agents/inbox/" + path.name,
        }), encoding="utf-8")
        rc, data, errors = self.action("archive", "--apply")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(data["moved"], [])
        self.assertTrue(path.is_file())

    def test_archive_only_moves_current_workspace_history(self):
        other = self.base / "linked"
        self.session("claude-linked", root=other, agent="claude")
        path = self.old_message(root=other)
        with mock.patch.object(notifications, "notification_roots", return_value=[self.root, other]):
            rc, data, errors = self.action("archive", "--apply")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(data["moved"], [])
        self.assertTrue(path.is_file())

    def test_archive_can_preview_when_inbox_exceeds_live_scan_limit(self):
        self.old_message("Q-old-1.md")
        self.old_message("Q-old-2.md")
        with mock.patch.object(notifications, "MAX_RECORDS", 1):
            rc, data, errors = self.action("archive", identity=False)
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(len(data["candidates"]), 2)

    def test_archive_preview_respects_output_budget_and_reports_total(self):
        for index in range(20):
            self.old_message("Q-old-%03d.md" % index)
        before = self.snapshot()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = messages.main(["archive", "--root", str(self.root), "--max-chars", "1024", "--json"])
        data = json.loads(output.getvalue())
        self.assertEqual(rc, 1, data)
        self.assertLessEqual(len(output.getvalue()), 1024)
        self.assertEqual(data["candidates_count"], 20)
        self.assertTrue(data["truncated"])
        self.assertEqual(self.snapshot(), before)

    def test_archive_failure_rolls_back_earlier_moves(self):
        first = self.old_message("Q-a.md")
        second = self.old_message("Q-b.md")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (first, second)}
        original_move = messages._move_no_overwrite

        def fail_second(root, source, destination, expected):
            if source == second:
                raise OSError("Injected second move failure")
            return original_move(root, source, destination, expected)

        with mock.patch.object(messages, "_move_no_overwrite", fail_second):
            rc, data, _errors = self.action("archive", "--apply")
        self.assertEqual(rc, 2, data)
        for path, (raw, mtime) in before.items():
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(list((self.root / ".agents/inbox/archive").rglob("Q-*.md")), [])

    def test_archive_keeps_unread_answer_for_a_registered_sender(self):
        path = self.old_message()
        fields = sessions.put_fields(path.read_text(encoding="utf-8"), {"from": self.session_id})
        path.write_text(fields, encoding="utf-8")
        old = datetime.now() - timedelta(days=45)
        os.utime(path, (old.timestamp(), old.timestamp()))
        rc, data, errors = self.action("archive", "--apply")
        self.assertEqual(rc, 0, (data, errors))
        self.assertTrue(path.is_file())
        self.assertEqual(data["moved"], [])
        self.assertIn("未读回复", data["skipped"][0]["reason"])
        self.mark()
        rc, data, errors = self.action("archive", "--apply")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(data["moved_count"], 1)

    def test_archive_no_overwrite_if_destination_appears_during_move(self):
        source = self.old_message()
        original = source.read_bytes()
        original_move = messages._move_no_overwrite
        injected = []

        def collision(root, src, destination, expected):
            destination.write_bytes(b"Concurrent archive file.")
            injected.append(destination)
            return original_move(root, src, destination, expected)

        with mock.patch.object(messages, "_move_no_overwrite", collision):
            rc, data, _errors = self.action("archive", "--apply")
        self.assertEqual(rc, 2, data)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(injected[0].read_bytes(), b"Concurrent archive file.")

    def test_reply_preserves_released_control_without_reviving_pause(self):
        path = self.inbox("Q-released.md", sender="human", target=self.session_id, status="closed")
        path.write_text(sessions.put_fields(path.read_text(encoding="utf-8"), {"action": "pause"}), encoding="utf-8")
        before = path.read_bytes()
        rc, data, _errors = self.action("reply", path.name, "--body", "Old control response.")
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {})

    def test_reply_version_rejects_changed_original_request(self):
        path = self.inbox("Q-versioned.md", target=self.session_id)
        version = self.items()[0]["request_version"]
        path.write_text(path.read_text(encoding="utf-8").replace("Please check compatibility.", "New requirement."),
                        encoding="utf-8")
        before = path.read_bytes()
        rc, data, _errors = self.action("reply", path.name, "--body", "Answer to old version.",
                                       "--status", "answered", "--request-version", version)
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), before)

    def test_reply_unknown_state_and_unclosed_fence_are_zero_write(self):
        path = self.inbox("Q-bad-reply.md", target=self.session_id, status="unknown")
        original = path.read_bytes()
        rc, data, _errors = self.action("reply", path.name, "--body", "Do not repair unknown state.")
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), original)
        path = self.inbox("Q-unclosed.md", target=self.session_id, body="```markdown\nUnclosed example.")
        original = path.read_bytes()
        rc, data, _errors = self.action("reply", path.name, "--body", "Reply cannot enter the request.")
        self.assertEqual(rc, 2, data)
        self.assertEqual(path.read_bytes(), original)

    def test_explicit_archive_preview_does_not_update_activity(self):
        self.old_message()
        self.expired_presence()
        before = self.snapshot()
        rc, data, errors = self.action("archive")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(self.snapshot(), before)

    def test_sending_a_message_also_delivers_other_pending_hints(self):
        path = self.inbox("Q-pending-hint.md", sender="human", target=self.session_id)
        original = path.read_bytes()
        rc, data, errors = self.action("send", "--to", "any", "--body", "An unrelated outgoing message.")
        self.assertEqual(rc, 0, (data, errors))
        self.assertEqual(data["notifications"]["human_pending"], 1)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
