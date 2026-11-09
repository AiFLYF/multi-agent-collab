"""Message hints must stay small without losing changes or confusing read receipts."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from selftest import KIT, PY, install, run, rmtree_readonly
import agents_messages as messages
import agents_notifications as notifications


class _NotificationFixture(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="mac-notification-tests-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        for kind in ("sessions", "inbox", "worklog"):
            (self.root / ".agents" / kind).mkdir(parents=True)
        self.session_id = "codex-notification-one"
        self.session(self.session_id)

    def tearDown(self):
        if self.base.parent != Path(tempfile.gettempdir()).resolve() or not self.base.name.startswith("mac-notification-tests-"):
            raise RuntimeError("unsafe notification fixture cleanup target")
        rmtree_readonly(self.base)

    def session(self, session_id, root=None, agent="codex"):
        root = root or self.root
        path = root / ".agents" / "sessions" / (session_id + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Session\n"
            f"- agent: {agent}\n- branch: feature/notifications\n"
            "- task: Verify message hints\n- status: active\n"
            "- files: [\"src/app.py\"]\n"
            f"- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n",
            encoding="utf-8")
        return path

    def inbox(self, name="Q-message.md", root=None, sender="claude", target="codex", status="open", body="Please check compatibility.", reply=""):
        root = root or self.root
        path = root / ".agents" / "inbox" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Question\n"
            f"- from: {sender}\n- to: {target}\n- status: {status}\n"
            f"- created: {datetime.now():%Y-%m-%d %H:%M}\n\n"
            f"## 问题\n\n{body}\n\n---\n\n## 回复\n\n{reply}\n",
            encoding="utf-8")
        return path

    def snapshot(self, root=None):
        root = root or self.root
        return {p.relative_to(root).as_posix():
                ("directory", p.stat().st_mtime_ns) if p.is_dir() else
                ("file", p.stat().st_mtime_ns, p.read_bytes())
                for p in root.rglob("*")}

    def collect(self, agent="codex", session_id=None, root=None, roots=None):
        root = root or self.root
        return notifications.collect_notifications(
            root, agent=agent, session_id=session_id or self.session_id,
            roots=roots or [root])

    def items(self, agent="codex", session_id=None, root=None, roots=None):
        root = root or self.root
        _identity, items = notifications.scan_messages(
            root, agent=agent, session_id=session_id or self.session_id,
            roots=roots or [root])
        return items

    def mark(self, items=None, session_id=None, root=None):
        root = root or self.root
        return notifications.mark_read(
            root, self.items(root=root, session_id=session_id) if items is None else items,
            agent="codex", session_id=session_id or self.session_id)

    def cli(self, command="read", *extra):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = messages.main([command, "--root", str(self.root), "--agent", "codex",
                                "--session", self.session_id, "--json", *extra])
        text = output.getvalue()
        self.assertTrue(text.strip(), errors.getvalue())
        return rc, json.loads(text), text


class TestNotificationDiscovery(_NotificationFixture):
    def test_compact_scan_preserves_versions_unread_and_reply_direction_without_payloads(self):
        self.inbox("Q-human.md", sender="human", body="Keep the whole human requirement version.")
        self.inbox("Q-incoming.md", body="Incoming text should remain available to read.")
        self.inbox("Q-reply.md", sender=self.session_id, target="claude", status="answered",
                   body="The original outgoing question.", reply="The complete answer must still notify its author.")
        before = self.snapshot()
        identity, full = notifications.scan_messages(self.root, "codex", self.session_id, roots=[self.root])
        compact_identity, compact = notifications.scan_messages(self.root, "codex", self.session_id,
                                                                roots=[self.root], include_content=False)
        self.assertEqual(compact_identity, identity)
        self.assertEqual(compact, [{key: value for key, value in item.items()
                                   if key not in ("body", "reply", "text")} for item in full])
        self.assertEqual({item["kind"] for item in compact}, {"incoming", "reply"})
        self.assertTrue(all(item["unread"] for item in compact))
        self.assertTrue(all(item["text"] for item in full))
        self.assertEqual(self.snapshot(), before)

    def test_no_messages_return_none_and_do_not_create_state(self):
        before = self.snapshot()
        self.assertIsNone(self.collect())
        self.assertIsNone(self.collect())
        self.assertEqual(self.snapshot(), before)

    def test_non_git_discovery_does_not_launch_git_or_query_worktrees(self):
        self.inbox()
        self.assertFalse((self.root / ".git").exists())
        with mock.patch.object(notifications._board(), "worktrees", side_effect=AssertionError("non-Git project queried worktrees")), \
             mock.patch("subprocess.run", side_effect=AssertionError("non-Git notification scan launched a command")):
            summary = notifications.collect_notifications(self.root, "codex", self.session_id)
        self.assertEqual(summary["unread_count"], 1)

    def test_long_message_is_a_short_hint_without_message_body(self):
        body = "PRIVATE_MESSAGE_CONTENT_SHOULD_BE_READ_ON_DEMAND " * 1000
        self.inbox(body=body)
        before = self.snapshot()
        summary = self.collect()
        self.assertEqual(summary["unread_count"], 1)
        self.assertEqual(summary["human_pending"], 0)
        self.assertNotIn("PRIVATE_MESSAGE_CONTENT", json.dumps(summary))
        formatted = notifications.format_notification(summary)
        self.assertNotIn("PRIVATE_MESSAGE_CONTENT", formatted)
        self.assertLess(len(formatted), 800)
        self.assertEqual(self.snapshot(), before)

    def test_read_command_preserves_project_and_session_identity(self):
        self.inbox()
        args = self.collect()["read_command"]
        self.assertIsInstance(args, list)
        self.assertTrue(all(isinstance(arg, str) for arg in args))
        self.assertTrue(Path(args[1]).is_absolute())
        self.assertEqual(args[args.index("--root") + 1], str(self.root))
        self.assertEqual(args[args.index("--agent") + 1], "codex")
        self.assertEqual(args[args.index("--session") + 1], self.session_id)

    def test_only_broadcast_platform_and_own_session_messages_are_counted(self):
        self.inbox("Q-any.md", target="any")
        self.inbox("Q-platform.md", target="codex")
        self.inbox("Q-own.md", target=self.session_id)
        self.inbox("Q-other-session.md", target="codex-notification-two")
        self.inbox("Q-other-platform.md", target="claude")
        self.assertEqual(self.collect()["unread_count"], 3)
        self.session("codex-notification-two")
        self.assertEqual(self.collect(session_id="codex-notification-two")["unread_count"], 3)

    def test_generic_sender_on_the_same_platform_does_not_hide_another_agents_message(self):
        self.inbox(sender="codex", target="codex", body="A finding from another Codex session.")
        summary = self.collect()
        self.assertEqual(summary["unread_count"], 1)
        self.assertEqual(summary["reply_count"], 0)

    def test_a_self_authored_session_broadcast_does_not_notify_its_author(self):
        self.inbox(sender=self.session_id, target="any", body="My own outbound question.")
        self.assertIsNone(self.collect())

    def test_answered_reply_is_delivered_to_the_original_sender(self):
        self.inbox(sender="codex", target="claude", status="answered",
                   reply="There is a compatibility issue to discuss.")
        summary = self.collect()
        self.assertEqual(summary["unread_count"], 1)
        self.assertEqual(summary["reply_count"], 1)
        self.assertEqual(summary["human_pending"], 0)
        self.mark()
        self.assertIsNone(self.collect())

    def test_a_new_reply_after_reading_the_previous_reply_is_delivered_again(self):
        path = self.inbox(sender="codex", target="claude", status="answered", reply="First result.")
        self.mark()
        self.assertIsNone(self.collect())
        path.write_text(path.read_text(encoding="utf-8").replace("First result.", "A new finding."), encoding="utf-8")
        self.assertEqual(self.collect()["reply_count"], 1)

    def test_reply_date_refresh_does_not_hide_or_invent_a_new_reply(self):
        path = self.inbox(sender=self.session_id, target="claude", status="answered", reply="Reply A.")
        original = self.items()[0]
        self.mark(items=[original])
        changed_date = ["- created: 2000-01-01 00:00" if line.startswith("- created:") else line
                        for line in path.read_text(encoding="utf-8").splitlines()]
        path.write_text("\n".join(changed_date) + "\n", encoding="utf-8")
        refreshed = self.items()[0]
        self.assertNotEqual(refreshed["version"], original["version"])
        self.assertEqual(refreshed["notification_version"], original["notification_version"])
        self.assertIsNone(self.collect())
        path.write_text(path.read_text(encoding="utf-8").replace("Reply A.", "Reply B."), encoding="utf-8")
        self.assertEqual(self.collect()["unread_count"], 1)
        self.assertEqual(self.collect()["reply_count"], 1)

    def test_same_size_and_mtime_cannot_hide_a_message_change(self):
        path = self.inbox(body="Version A")
        self.mark()
        self.assertIsNone(self.collect())
        before = path.stat()
        original = path.read_bytes()
        changed = original.replace(b"Version A", b"Version B")
        self.assertEqual(len(changed), len(original))
        path.write_bytes(changed)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertNotEqual(hashlib.sha256(changed).digest(), hashlib.sha256(original).digest())
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_read_receipts_are_isolated_between_sessions_on_the_same_platform(self):
        self.session("codex-notification-two")
        self.inbox(target="codex")
        self.mark()
        self.assertIsNone(self.collect())
        self.assertEqual(self.collect(session_id="codex-notification-two")["unread_count"], 1)

    def test_a_unique_local_session_can_supply_the_notification_identity(self):
        self.inbox(target=self.session_id)
        summary = notifications.collect_notifications(self.root, agent="codex", roots=[self.root])
        self.assertEqual(summary["unread_count"], 1)
        args = summary["read_command"]
        self.assertEqual(args[args.index("--session") + 1], self.session_id)

    def test_same_message_name_in_different_workspaces_remains_distinct(self):
        other = self.base / "linked"
        other.mkdir()
        other_session = "codex-notification-linked"
        self.session(other_session, root=other)
        first = self.inbox(body="Current workspace request")
        second = self.inbox(root=other, body="Linked workspace request")
        roots = [self.root, other]
        self.assertEqual(self.collect(roots=roots)["unread_count"], 2)
        items = self.items(roots=roots)
        self.assertEqual({Path(item["path"]) for item in items}, {first, second})
        self.mark(items=[item for item in items if Path(item["path"]) == first])
        self.assertEqual(self.collect(roots=roots)["unread_count"], 1)
        self.assertEqual(self.collect(root=other, roots=roots, session_id=other_session)["unread_count"], 2)

    def test_reading_an_open_human_request_does_not_mark_it_processed(self):
        path = self.inbox(sender="human", body="Please prioritize compatibility.")
        original = path.read_bytes()
        self.mark()
        summary = self.collect()
        self.assertEqual(summary["unread_count"], 0)
        self.assertEqual(summary["human_pending"], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_reply_edits_do_not_change_the_original_human_request_version(self):
        path = self.inbox(sender="human", body="Use the confirmed file range.")
        first = notifications.human_requests([self.root], "codex", self.session_id)
        self.assertEqual(len(first), 1)
        path.write_text(path.read_text(encoding="utf-8") + "Acknowledged; working on the confirmed range.\n", encoding="utf-8")
        second = notifications.human_requests([self.root], "codex", self.session_id)
        self.assertEqual(second, first)
        path.write_text(path.read_text(encoding="utf-8").replace("confirmed file range", "new file range"), encoding="utf-8")
        self.assertNotEqual(notifications.human_requests([self.root], "codex", self.session_id), first)

    def test_changing_the_human_request_title_invalidates_the_previous_version(self):
        path = self.inbox(sender="human", body="Keep the requested scope.")
        before = notifications.human_requests([self.root], "codex", self.session_id)
        path.write_text(path.read_text(encoding="utf-8").replace("# Question", "# Pause the requested scope"), encoding="utf-8")
        self.assertNotEqual(notifications.human_requests([self.root], "codex", self.session_id), before)

    def test_indented_code_fence_reply_text_cannot_hide_a_later_human_requirement(self):
        body = "   ```markdown\n## 回复\nLiteral code content.\n   ```\nActual requirement A."
        path = self.inbox(sender="human", body=body, reply="Acknowledged separately.")
        before = notifications.human_requests([self.root], "codex", self.session_id)
        path.write_text(path.read_text(encoding="utf-8").replace("Actual requirement A.", "Actual requirement B."), encoding="utf-8")
        self.assertNotEqual(notifications.human_requests([self.root], "codex", self.session_id), before)

    def test_code_fence_info_string_is_not_a_closing_fence(self):
        body = "```markdown\n```python\n## 回复\nLiteral code content.\n```\nActual requirement A."
        path = self.inbox(sender="human", body=body, reply="Actual reply A.")
        before = notifications.human_requests([self.root], "codex", self.session_id)
        path.write_text(path.read_text(encoding="utf-8").replace("Actual requirement A.", "Actual requirement B."), encoding="utf-8")
        changed = notifications.human_requests([self.root], "codex", self.session_id)
        self.assertNotEqual(changed, before)
        path.write_text(path.read_text(encoding="utf-8").replace("Actual reply A.", "Actual reply B."), encoding="utf-8")
        self.assertEqual(notifications.human_requests([self.root], "codex", self.session_id), changed)

    def test_typed_human_control_survives_a_read_receipt(self):
        path = self.inbox(sender="human", body="Wait for the confirmed plan.")
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: pause"), encoding="utf-8")
        self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {path.name: "pause"})
        self.mark()
        self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {path.name: "pause"})
        self.assertEqual(self.collect()["human_pending"], 1)

    def test_answering_a_pause_does_not_revoke_the_active_human_control(self):
        path = self.inbox(sender="human", body="Pause until the user authorizes resuming.")
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: pause"), encoding="utf-8")
        self.mark()
        previous = "open"
        for status in ("answered", "done", "cancelled"):
            with self.subTest(status=status):
                path.write_text(path.read_text(encoding="utf-8").replace("- status: " + previous, "- status: " + status), encoding="utf-8")
                previous = status
                self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {path.name: "pause"})
                self.assertIn(path.name, notifications.human_requests([self.root], "codex", self.session_id))
                self.assertEqual(self.collect()["human_pending"], 1)
        # The fixture models an explicit, authorized withdrawal of the control.
        path.write_text(path.read_text(encoding="utf-8").replace("- status: " + previous, "- status: closed"), encoding="utf-8")
        self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {})
        self.assertEqual(notifications.human_requests([self.root], "codex", self.session_id), {})
        self.assertIsNone(self.collect())

    def test_ai_message_cannot_become_a_human_control(self):
        path = self.inbox(sender="claude", body="Suggestion from another agent.")
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: pause"), encoding="utf-8")
        self.assertEqual(notifications.human_controls([self.root], "codex", self.session_id), {})
        self.assertEqual(self.collect()["human_pending"], 0)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_changing_control_action_changes_the_human_request_version(self):
        path = self.inbox(sender="human", body="Keep the requested scope.")
        before = notifications.human_requests([self.root], "codex", self.session_id)
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: avoid"), encoding="utf-8")
        self.assertNotEqual(notifications.human_requests([self.root], "codex", self.session_id), before)

    def test_conflicting_control_actions_are_a_data_error(self):
        path = self.inbox(sender="human")
        path.write_text(path.read_text(encoding="utf-8").replace("- status: open", "- status: open\n- action: message\n- action: cancel"), encoding="utf-8")
        with self.assertRaises(ValueError):
            notifications.human_controls([self.root], "codex", self.session_id)

    def test_same_human_request_name_requires_absolute_identity_across_workspaces(self):
        other = self.base / "linked"
        first = self.inbox(sender="human")
        second = self.inbox(root=other, sender="human", body="Different workspace instruction")
        requests = notifications.human_requests([self.root, other], "codex", self.session_id)
        self.assertEqual(set(requests), {str(first), str(second)})
        self.assertTrue(all(len(value) == 64 for value in requests.values()))

    def test_changed_content_between_read_and_receipt_is_not_marked_read(self):
        path = self.inbox(body="Snapshot before reading")
        items = self.items()
        path.write_text(path.read_text(encoding="utf-8").replace("Snapshot before reading", "Edited during reading"), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises((ValueError, OSError)):
            self.mark(items=items)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_batch_with_one_changed_message_does_not_acknowledge_other_messages(self):
        self.inbox("Q-first.md", body="Still unchanged")
        second = self.inbox("Q-second.md", body="Old second message")
        items = self.items()
        second.write_text(second.read_text(encoding="utf-8").replace("Old second message", "New second message"), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises((ValueError, OSError)):
            self.mark(items=items)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 2)

    def test_conflicting_message_header_cannot_report_no_messages(self):
        path = self.inbox(sender="human")
        path.write_text(path.read_text(encoding="utf-8").replace("- to: codex", "- to: codex\n- to: claude"), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.collect()

    def test_a_message_directory_cannot_masquerade_as_an_empty_queue(self):
        (self.root / ".agents" / "inbox" / "Q-invalid.md").mkdir()
        with self.assertRaises((ValueError, OSError)):
            self.collect()

    def test_corrupt_read_receipts_cannot_silently_hide_messages(self):
        self.inbox()
        self.mark()
        directory = self.root / ".agents" / "notifications"
        receipts = list(directory.glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipts[0].write_text("{invalid receipt", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises((ValueError, OSError)):
            self.collect()
        self.assertEqual(self.snapshot(), before)

    def test_marking_the_same_version_again_preserves_receipt_bytes_and_mtime(self):
        self.inbox()
        items = self.items()
        self.mark(items=items)
        before = self.snapshot()
        self.mark(items=items)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == "nt", "PowerShell command quoting is Windows specific")
    def test_notification_command_is_executable_with_literal_powershell_metacharacters(self):
        self.root = self.base / "project $cash;`tick's $(Write-Output SHOULD_NOT_RUN) 中文"
        self.root.mkdir()
        for kind in ("sessions", "inbox", "worklog"):
            (self.root / ".agents" / kind).mkdir(parents=True)
        self.session(self.session_id)
        path = self.inbox(body="PowerShell reached the literal target project.")
        original = path.read_bytes()
        hint = notifications.format_notification(self.collect())
        self.assertIn("读取：", hint)
        command = hint.split("读取：", 1)[1]
        result = run("powershell", "-NoProfile", "-NonInteractive", "-Command", command,
                     cwd=self.root, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PowerShell reached the literal target project.", result.stdout)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(self.collect())


class TestMessageCommands(_NotificationFixture):
    def test_git_unavailable_in_a_real_repository_is_a_json_error_without_writes(self):
        result = run("git", "init", "-q", "-b", "main", cwd=self.root, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.inbox()
        before = self.snapshot()
        with mock.patch.object(notifications._board(), "resolve_git", return_value=None):
            with self.assertRaises(ValueError):
                notifications.collect_notifications(self.root, "codex", self.session_id)
            for command in ("list", "read"):
                with self.subTest(command=command):
                    rc, data, _output = self.cli(command)
                    self.assertEqual(rc, 2, data)
                    self.assertFalse(data["ok"])
                    self.assertEqual(self.snapshot(), before)

    def test_list_metadata_and_default_read_preserve_all_state(self):
        body = "MESSAGE_BODY_FOR_ON_DEMAND_READ " * 50
        self.inbox(body=body)
        before = self.snapshot()
        rc, listed, listing = self.cli("list")
        self.assertEqual(rc, 0, listed)
        self.assertEqual(len(listed["items"]), 1)
        self.assertNotIn("MESSAGE_BODY_FOR_ON_DEMAND_READ", listing)
        self.assertEqual(self.snapshot(), before)
        rc, read, output = self.cli("read")
        self.assertEqual(rc, 0, read)
        self.assertFalse(read["truncated"])
        self.assertEqual(read["marked_read"], [])
        self.assertIn(body.strip(), output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_full_read_can_mark_messages_read_without_rewriting_messages(self):
        path = self.inbox(body="Read this complete request.")
        original = path.read_bytes()
        rc, data, output = self.cli("read", "--mark-read")
        self.assertEqual(rc, 0, data)
        self.assertFalse(data["truncated"])
        self.assertEqual(len(data["marked_read"]), 1)
        self.assertIn("Read this complete request.", output)
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(self.collect())

    def test_failed_output_does_not_hide_an_undelivered_message(self):
        path = self.inbox(body="The output must deliver this finding.")
        original = path.read_bytes()

        class FailFirstWrite(io.StringIO):
            failed = False

            def write(self, text):
                if not self.failed:
                    self.failed = True
                    raise BrokenPipeError("output consumer disconnected")
                return super().write(text)

        output = FailFirstWrite()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                mock.patch.object(messages.os, "dup2") as redirect:
            rc = messages.main(["read", "--root", str(self.root), "--agent", "codex",
                                "--session", self.session_id, "--json", "--mark-read"])
        self.assertEqual(rc, 2, errors.getvalue())
        self.assertFalse(json.loads(errors.getvalue())["ok"])
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("The output must deliver this finding.", output.getvalue())
        redirect.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_zero_progress_output_is_an_error_and_does_not_mark_read(self):
        path = self.inbox(body="No output must never count as a complete read.")
        original = path.read_bytes()

        class NoProgress(io.StringIO):
            def write(self, text):
                return 0

        output, errors = NoProgress(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                mock.patch.object(messages.os, "dup2") as redirect:
            rc = messages.main(["read", "--root", str(self.root), "--agent", "codex",
                                "--session", self.session_id, "--json", "--mark-read"])
        self.assertEqual(rc, 2, errors.getvalue())
        self.assertFalse(json.loads(errors.getvalue())["ok"])
        self.assertEqual(output.getvalue(), "")
        redirect.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_einval_during_flush_rolls_back_without_a_second_stdout_document(self):
        path = self.inbox(body="A successful write with a failed flush is not delivered.")
        original = path.read_bytes()

        class FailFlush(io.StringIO):
            def flush(self):
                raise OSError(22, "Invalid argument")

        output, errors = FailFlush(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                mock.patch.object(messages.os, "dup2") as redirect:
            rc = messages.main(["read", "--root", str(self.root), "--agent", "codex",
                                "--session", self.session_id, "--json", "--mark-read"])
        self.assertEqual(rc, 2, errors.getvalue())
        self.assertFalse(json.loads(errors.getvalue())["ok"])
        self.assertIn("A successful write with a failed flush is not delivered.", json.loads(output.getvalue())["items"][0]["text"])
        redirect.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_real_closed_stdout_exits_two_and_preserves_previous_receipts(self):
        self.inbox("Q-already-read.md", body="Keep the previous successful receipt.")
        self.mark()
        self.inbox("Q-undelivered.md", body="The closed consumer cannot read this request.")
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        receipts = self.root / ".agents" / "notifications"
        self.assertTrue(list(receipts.glob("*.json")))
        command = [PY, "-B", str(KIT / "tools/agents_messages.py"), "read", "--root", str(self.root),
                   "--agent", "codex", "--session", self.session_id, "--json", "--mark-read"]
        child = subprocess.Popen(command, cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertIsNone(child.poll(), "producer must still be running when stdout closes")
            child.stdout.close()
            child.stdout = None
            _output, errors = child.communicate(timeout=90)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=90)
        error_text = errors.decode("utf-8")
        self.assertEqual(child.returncode, 2, error_text)
        self.assertFalse(json.loads(error_text)["ok"])
        self.assertNotIn("Exception ignored", error_text)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_closed_stdout_descriptor_during_initialization_is_a_json_error_without_writes(self):
        self.inbox(body="Initialization failure must not consume this request.")
        before = self.snapshot()
        arguments = ["read", "--root", str(self.root), "--agent", "codex",
                     "--session", self.session_id, "--json", "--mark-read"]
        code = ("import os, sys\n"
                "sys.path.insert(0, " + repr(str(KIT / "tools")) + ")\n"
                "import agents_messages\n"
                "os.close(1)\n"
                "sys.exit(agents_messages.main(" + repr(arguments) + "))\n")
        result = run(PY, "-B", "-c", code, cwd=self.root, timeout=90)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(json.loads(result.stderr)["ok"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Exception ignored", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_missing_stdout_object_is_a_json_error_without_writes(self):
        self.inbox(body="An unavailable stdout must leave this request unread.")
        before = self.snapshot()
        arguments = ["read", "--root", str(self.root), "--agent", "codex",
                     "--session", self.session_id, "--json", "--mark-read"]
        code = ("import os, sys\n"
                "sys.path.insert(0, " + repr(str(KIT / "tools")) + ")\n"
                "import agents_messages\n"
                "os.close(1)\n"
                "sys.stdout = None\n"
                "sys.exit(agents_messages.main(" + repr(arguments) + "))\n")
        result = run(PY, "-B", "-c", code, cwd=self.root, timeout=90)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(json.loads(result.stderr)["ok"])
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Exception ignored", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_missing_stderr_never_retries_stdout_after_a_failed_flush(self):
        path = self.inbox(body="Keep a single stdout document even without stderr.")
        original = path.read_bytes()

        class FailFlush(io.StringIO):
            def flush(self):
                raise OSError(22, "Invalid argument")

        output = FailFlush()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(None), \
                mock.patch.object(messages.os, "dup2") as redirect:
            rc = messages.main(["read", "--root", str(self.root), "--agent", "codex",
                                "--session", self.session_id, "--json", "--mark-read"])
        self.assertEqual(rc, 2)
        self.assertIn("Keep a single stdout document even without stderr.", json.loads(output.getvalue())["items"][0]["text"])
        redirect.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.collect()["unread_count"], 1)

    def test_truncated_read_never_marks_any_message_read(self):
        self.inbox("Q-short.md", body="Short first request")
        self.inbox("Q-long.md", body="COMPLETE_DIRECTIVE_REQUIRED " * 1000)
        before = self.snapshot()
        rc, data, output = self.cli("read", "--max-chars", "1024", "--mark-read")
        self.assertEqual(rc, 1, data)
        self.assertTrue(data["truncated"])
        self.assertEqual(data["marked_read"], [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.collect()["unread_count"], 2)
        self.assertNotIn("COMPLETE_DIRECTIVE_REQUIRED " * 1000, output)

    def test_global_options_before_and_after_subcommand_are_supported(self):
        self.inbox()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = messages.main(["--root", str(self.root), "--agent", "codex", "--session", self.session_id,
                                "--json", "list"])
        self.assertEqual(rc, 0, output.getvalue())
        self.assertEqual(len(json.loads(output.getvalue())["items"]), 1)
        rc, data, _output = self.cli("list")
        self.assertEqual(rc, 0, data)
        self.assertEqual(len(data["items"]), 1)

    def test_read_human_pending_reminds_without_repeating_the_body_or_read_command(self):
        self.inbox(sender="human", body="HUMAN_REQUIREMENT_ALREADY_READ should remain pending.")
        rc, read, _output = self.cli("read", "--mark-read")
        self.assertEqual(rc, 0, read)
        self.assertEqual(read["human_pending"], 1)
        before = self.snapshot()
        rc, again, output = self.cli("read")
        self.assertEqual(rc, 0, again)
        self.assertEqual(again["items"], [])
        self.assertEqual(again["human_pending"], 1)
        self.assertEqual(again["marked_read"], [])
        self.assertNotIn("HUMAN_REQUIREMENT_ALREADY_READ", output)
        summary = self.collect()
        self.assertIsNone(summary["read_command"])
        self.assertNotIn("agents_messages.py", json.dumps(summary))
        hint = notifications.format_notification(summary)
        self.assertNotIn("agents_messages.py", hint)
        self.assertNotIn("--mark-read", hint)
        self.assertLess(len(hint), 150)
        self.assertEqual(self.snapshot(), before)

    def test_own_plan_reply_does_not_make_the_read_human_request_new_again(self):
        path = self.inbox(sender="human", body="HUMAN_REQUIREMENT_READ_ONCE should remain in effect.")
        requests = notifications.human_requests([self.root], "codex", self.session_id)
        rc, read, _output = self.cli("read", "--mark-read")
        self.assertEqual(rc, 0, read)
        path.write_text(path.read_text(encoding="utf-8") + "I acknowledged this requirement and am working on it.\n", encoding="utf-8")
        self.assertEqual(notifications.human_requests([self.root], "codex", self.session_id), requests)
        summary = self.collect()
        self.assertEqual(summary["unread_count"], 0)
        self.assertEqual(summary["human_pending"], 1)
        rc, again, output = self.cli("read")
        self.assertEqual(rc, 0, again)
        self.assertEqual(again["items"], [])
        self.assertNotIn("HUMAN_REQUIREMENT_READ_ONCE", output)

    def test_invalid_character_budget_is_a_single_json_error_without_writes(self):
        self.inbox()
        before = self.snapshot()
        for value in ("not-an-integer", "0"):
            with self.subTest(value=value):
                rc, data, _output = self.cli("read", "--max-chars", value, "--mark-read")
                self.assertEqual(rc, 2, data)
                self.assertFalse(data["ok"])
                self.assertEqual(self.snapshot(), before)

    def test_missing_message_id_returns_json_error_without_writing(self):
        before = self.snapshot()
        rc, data, _output = self.cli("read", "Q-does-not-exist.md", "--mark-read")
        self.assertEqual(rc, 2, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_data_error_returns_one_json_document_and_preserves_records(self):
        path = self.inbox(sender="human")
        path.write_text(path.read_text(encoding="utf-8").replace("- from: human", "- from: human\n- from: claude"), encoding="utf-8")
        before = self.snapshot()
        rc, data, _output = self.cli("list")
        self.assertEqual(rc, 2, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_session_does_not_share_a_platform_read_cursor(self):
        self.session("codex-notification-two")
        self.inbox(target="any")
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = messages.main(["read", "--root", str(self.root), "--agent", "codex", "--mark-read", "--json"])
        data = json.loads(output.getvalue())
        self.assertEqual(rc, 2, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.snapshot(), before)


class TestInstalledMessageEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="mac-installed-notification-tests-")).resolve()
        cls.root = cls.base / "project 中文"
        cls.root.mkdir()
        result = run("git", "init", "-q", "-b", "main", cwd=cls.root)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        result = install(cls.root, "--platforms", "codex", "--workflow", "github-flow")
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        cls.state = cls.root / ".multi-agent-collab"
        cls.entry = cls.state / "tools" / "agents_messages.py"

    @classmethod
    def tearDownClass(cls):
        if cls.base.parent != Path(tempfile.gettempdir()).resolve() or not cls.base.name.startswith("mac-installed-notification-tests-"):
            raise RuntimeError("unsafe installed notification cleanup target")
        rmtree_readonly(cls.base)

    def test_installed_message_entry_reads_from_a_project_subdirectory(self):
        session_id = "codex-installed-notification"
        session = self.state / "sessions" / (session_id + ".md")
        session.write_text("# S\n- agent: codex\n- branch: main\n- status: active\n- files: [\"src/\"]\n"
                           f"- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
        path = self.state / "inbox" / "Q-installed.md"
        path.write_text("# Q\n- from: claude\n- to: codex\n- status: open\n\n## 问题\n\nInstalled notification body.\n", encoding="utf-8")
        subdirectory = self.root / "src" / "nested"
        subdirectory.mkdir(parents=True, exist_ok=True)
        before = {p.relative_to(self.root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
                  for p in self.root.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.root).parts}
        result = run(PY, "-B", str(self.entry), "read", "--root", str(subdirectory), "--agent", "codex",
                     "--session", session_id, "--json", cwd=subdirectory, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(Path(data["root"]), self.root)
        self.assertIn("Installed notification body.", result.stdout)
        self.assertEqual(data["marked_read"], [])
        after = {p.relative_to(self.root).as_posix(): (p.stat().st_mtime_ns, p.read_bytes())
                 for p in self.root.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.root).parts}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
