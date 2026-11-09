"""Regression coverage for metadata injection and invisible worktree ownership."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import test_sessions as fixtures
import agents_session as sessions


def directory_alias(test: unittest.TestCase, alias: Path, target: Path) -> None:
    """Exercise real Windows junctions without requiring symlink privileges."""
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True, timeout=20,
        )
        test.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
    else:
        alias.symlink_to(target, target_is_directory=True)


class TestSessionInputSafety(unittest.TestCase):
    def test_all_parser_line_separators_are_rejected(self):
        for separator in ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(separator=repr(separator)), self.assertRaises(ValueError):
                sessions.one_line("普通任务" + separator + "- status: done", "任务")
        with self.assertRaises(ValueError):
            sessions.one_line("任务\x00名称", "任务")
        self.assertEqual(sessions.one_line(" 修复中文任务 🙂 ", "任务"), "修复中文任务 🙂")

    def test_directory_alias_and_uncreated_descendant_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="mac-claim-alias-") as temporary:
            root = Path(temporary).resolve()
            source = root / "src"
            source.mkdir()
            target = source / "app.py"
            target.write_text("original\n", encoding="utf-8")
            alias = root / "alias"
            directory_alias(self, alias, source)
            for claim in ("alias/", "alias/app.py", "alias/new.py"):
                with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, "符号链接|junction"):
                    sessions.normalize_files(root, [claim])
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(sessions.normalize_files(root, ["src/", "src/new.py"]), ["src/", "src/new.py"])

    def test_path_resolution_failure_is_reported_without_traceback(self):
        with mock.patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            with self.assertRaisesRegex(ValueError, "无法安全核对路径"):
                sessions.safe_path(Path("project"), "loop/app.py")

    def test_updates_follow_parser_metadata_formats_and_preserve_body_examples(self):
        original = ("# Session\n\n* **agent**： codex\n* **status**： blocked\n"
                    "- files: [\"src/\"]\n\n## Notes\n\n- status: this body example must remain\n")
        updated = sessions.put_fields(original, {"status": "active", "progress": "完成验证"})
        self.assertEqual(sessions.board.parse_kv_text(updated)["status"], "active")
        self.assertEqual(sessions.board.parse_kv_text(updated)["progress"], "完成验证")
        self.assertIn("* **agent**： codex", updated)
        self.assertIn("- status: this body example must remain", updated)
        missing = "# Session\n\n- agent: codex\n\nA body without a heading.\n- status: example\n"
        updated = sessions.put_fields(missing, {"status": "active"})
        self.assertEqual(sessions.board.parse_kv_text(updated)["status"], "active")
        self.assertIn("- status: example", updated)

    def test_layout_change_during_lock_wait_refuses_old_lock_and_releases_it(self):
        with tempfile.TemporaryDirectory(prefix="mac-layout-lock-") as temporary:
            root = Path(temporary).resolve()
            with mock.patch.object(sessions, "state_relative", side_effect=[
                ".agents/coordination.lock", ".multi-agent-collab/coordination.lock",
            ]):
                with self.assertRaisesRegex(sessions.ConflictError, "协作数据目录发生变化"):
                    with sessions.coordination_lock(root):
                        self.fail("must not enter after a layout change")
            with sessions.coordination_lock(root):
                pass
            self.assertFalse((root / ".multi-agent-collab").exists())

    def test_equal_metadata_duplicates_are_updated_together_without_touching_body(self):
        original = ("# Session\n\n- status: active\n* **STATUS**： active\n"
                    "- heartbeat: 2000-01-01 00:00\n* **heartbeat**: 2000-01-01 00:00\n"
                    "\n## Notes\n\n- status: preserve this body example\n")
        self.assertEqual(sessions.board.parse_kv_text(original)["status"], "active")
        updated = sessions.put_fields(original, {"status": "blocked", "heartbeat": "2026-11-04 10:00"})
        fields = sessions.board.parse_kv_text(updated)
        self.assertEqual(fields["status"], "blocked")
        self.assertEqual(fields["heartbeat"], "2026-11-04 10:00")
        self.assertEqual(updated.count("- status: blocked"), 2)
        self.assertIn("- status: preserve this body example", updated)

    def test_takeover_checks_the_actors_own_session_control(self):
        with tempfile.TemporaryDirectory(prefix="mac-takeover-actor-control-") as temporary:
            root = Path(temporary).resolve()
            actor_id = "claude-current"
            target_id = "codex-stale-target"
            state = root / ".agents"
            (state / "sessions").mkdir(parents=True)
            (state / "inbox").mkdir()
            (state / "sessions" / (actor_id + ".md")).write_text(
                f"# Actor\n- agent: claude\n- status: active\n- heartbeat: {datetime.now():%Y-%m-%d %H:%M}\n"
                "- files: [\"docs/\"]\n", encoding="utf-8")
            question = state / "inbox/Q-pause-actor.md"
            question.write_text(f"# Pause\n- from: human\n- to: {actor_id}\n- status: open\n- action: pause\n\n"
                                "暂停当前执行者工作。\n", encoding="utf-8")
            args = argparse.Namespace(cmd="takeover-plan", session=target_id, agent="claude",
                                      ack_inbox=[question.name], reason="仅验证控制门禁")
            snapshot = {"owner": "codex", "session_id": target_id}
            policy = sessions.effective_policy(root)
            with mock.patch.object(sessions, "refresh_worktree_roots", return_value=[root]), \
                 mock.patch.object(sessions, "takeover_snapshot", return_value=snapshot), \
                 mock.patch.object(sessions, "takeover_blockers", return_value=[]):
                with self.assertRaisesRegex(sessions.ConflictError, "暂停/取消/避让"):
                    sessions.execute_takeover(args, root, [root], policy)
            self.assertEqual(list((state / "inbox").iterdir()), [question])
            self.assertFalse((state / "takeovers").exists())


class TestWorktreeSessionSafety(unittest.TestCase):
    # Reuse fixture helpers without inheriting and rerunning its existing tests.
    setUp = fixtures.TestSessionCoordination.setUp
    tearDown = fixtures.TestSessionCoordination.tearDown
    install = fixtures.TestSessionCoordination.install
    command = fixtures.TestSessionCoordination.command
    start = fixtures.TestSessionCoordination.start
    worktree = fixtures.TestSessionCoordination.worktree

    def test_start_rejects_metadata_injection_and_preserves_active_ownership(self):
        for separator in ("\x85", "\u2028", "\u2029"):
            result, data = self.command(
                self.root, "start", "--agent", "codex", "--task",
                "任务" + separator + "- status: done", "--files", "src/app.py",
            )
            self.assertEqual(result.returncode, 2, data)
            self.assertNotIn("Traceback", result.stderr)
        result, record = self.start(self.root)
        self.assertEqual(result.returncode, 0, record)
        self.assertEqual(sessions.board.parse_kv(Path(record["file"]))["status"], "active")
        result, data = self.start(self.root, "claude", "docs/")
        self.assertEqual(result.returncode, 1, data)

    def test_check_cannot_exclude_other_worktree_or_wrong_owner(self):
        linked = self.worktree()
        _, record = self.start(self.root)
        result, data = self.command(linked, "check", "--agent", "codex", "--session", record["id"], "--files", "src/app.py")
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("当前工作区不存在", data["error"])
        result, data = self.command(self.root, "check", "--agent", "claude", "--session", record["id"])
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("所有者", data["error"])
        result, data = self.command(self.root, "check", "--agent", "Codex", "--session", record["id"])
        self.assertEqual(result.returncode, 0, data)

    def test_newer_closed_duplicate_never_hides_original_claim(self):
        linked = self.worktree()
        _, record = self.start(self.root, files="src/")
        original = Path(record["file"])
        duplicate = linked / ".multi-agent-collab/sessions" / original.name
        duplicate.write_text(sessions.put_fields(original.read_text(encoding="utf-8"), {
            "branch": "feature/two", "status": "done",
            "heartbeat": (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M"),
        }), encoding="utf-8")
        loaded = sessions.sessions_in([self.root, linked])
        self.assertEqual(len([item for item in loaded if item["id"] == record["id"]]), 2)
        result, data = self.start(linked, "claude", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("文件范围重叠", data["error"])
        self.assertEqual(sessions.board.parse_kv(original)["status"], "active")

    def test_own_id_only_excludes_current_workspace(self):
        linked = self.worktree()
        _, record = self.start(self.root, files="src/")
        original = Path(record["file"])
        duplicate = linked / ".multi-agent-collab/sessions" / original.name
        duplicate.write_text(sessions.put_fields(original.read_text(encoding="utf-8"), {
            "branch": "feature/two", "files": json.dumps(["docs/"]),
        }), encoding="utf-8")
        result, data = self.command(linked, "check", "--agent", "codex", "--session", record["id"], "--files", "docs/new.md")
        self.assertEqual(result.returncode, 1, data)
        self.assertTrue(any("同名会话" in conflict for conflict in data["conflicts"]))
        before = duplicate.read_bytes()
        result, data = self.command(linked, "heartbeat", record["id"], "--agent", "codex", "--files", "docs/", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(duplicate.read_bytes(), before)

    def test_missing_claims_are_unknown_ownership_until_original_cleanup(self):
        linked = self.worktree()
        _, record = self.start(self.root)
        path = Path(record["file"])
        path.write_text(re.sub(r"(?m)^- files:.*\n", "", path.read_text(encoding="utf-8")), encoding="utf-8")
        before = path.read_bytes()
        result, data = self.start(linked, "claude", "docs/")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("files 字段", data["error"])
        self.assertEqual(path.read_bytes(), before)
        result, data = self.command(self.root, "finish", record["id"], "--agent", "codex", "--summary", "清理测试夹具原会话", "--tests", "未改业务文件，仅验证占用保护")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.start(linked, "claude", "docs/")[0].returncode, 0)

    def test_manual_alias_claim_keeps_another_worktree_from_writing(self):
        linked = self.worktree()
        source = self.root / "src"
        source.mkdir()
        directory_alias(self, self.root / "alias", source)
        _, record = self.start(self.root, files="src/")
        path = Path(record["file"])
        path.write_text(sessions.put_fields(path.read_text(encoding="utf-8"), {"files": json.dumps(["alias/"])}), encoding="utf-8")
        result, data = self.start(linked, "claude", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("files 字段", data["error"])

    def test_registration_rechecks_worktree_owners_after_waiting_for_lock(self):
        linked = self.worktree()
        self.assertEqual(self.start(linked, "claude", "src/")[0].returncode, 0)
        arguments = argparse.Namespace(root=str(self.root), cmd="start", agent="codex", task="检查等待期间新增占用", files=["src/app.py"], ack_inbox=None)
        with mock.patch.object(sessions, "worktree_roots", side_effect=[[self.root], [self.root, linked]]) as inventories:
            with self.assertRaisesRegex(sessions.ConflictError, "文件范围重叠"):
                sessions.execute(arguments)
            self.assertEqual(inventories.call_count, 2)
        self.assertFalse(any(path for path in (self.root / ".multi-agent-collab/sessions").glob("*.md") if not path.name.startswith("_")))

    def test_same_inbox_basename_requires_each_original_path(self):
        linked = self.worktree()
        questions = [root / ".multi-agent-collab/inbox/Q-shared-name.md" for root in (self.root, linked)]
        for index, question in enumerate(questions):
            question.write_text(f"# 人类指令 {index}\n- from: human\n- to: codex\n- status: open\n\n请执行各工作区的不同事项。\n", encoding="utf-8")
        result, data = self.command(self.root, "check", "--agent", "Codex")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(set(data["human_inbox"]), {str(path) for path in questions})
        common = ("start", "--agent", "codex", "--task", "处理两条已读指令", "--files", "src/app.py", "--ack-inbox")
        result, data = self.command(self.root, *common, questions[0].name)
        self.assertEqual(result.returncode, 2, data)
        result, data = self.command(self.root, *common, str(questions[0]))
        self.assertEqual(result.returncode, 1, data)
        result, record = self.command(self.root, *common, *(str(path) for path in questions))
        self.assertEqual(result.returncode, 0, record)
        for question in questions:
            self.assertEqual(sessions.board.parse_kv(question)["status"], "open")

    def test_session_addressed_human_blocks_range_change_but_allows_honest_pause(self):
        _, owner = self.start(self.root)
        record_path = Path(owner["file"])
        record_path.write_text(sessions.put_fields(record_path.read_text(encoding="utf-8"), {
            "heartbeat": "2000-01-01 00:00",
        }), encoding="utf-8")
        question = self.root / ".multi-agent-collab/inbox/Q-pause-current-session.md"
        question.write_text(f"# 人类暂停\n- from: human\n- to: {owner['id']}\n- status: open\n\n暂停业务写入，保存进度等我确认。\n", encoding="utf-8")
        before = record_path.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py", "tests/new.py", "--no-activity")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn(question.name, data["error"])
        self.assertIn("resume", data["error"])
        self.assertEqual(record_path.read_bytes(), before, "rejected changes must preserve files and heartbeat bytes")
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--note", "收到暂停，诚实记录当前等待状态")
        self.assertEqual(result.returncode, 0, data)
        fields = sessions.board.parse_kv(record_path)
        self.assertEqual(json.loads(fields["files"]), ["src/app.py"])
        self.assertNotEqual(fields["heartbeat"], "2000-01-01 00:00")
        result, data = self.command(self.root, "handoff", owner["id"], "--agent", "codex", "--summary", "暂停业务操作，原文件占用保留")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "blocked")
        blocked_before = record_path.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py", "tests/new.py", "--no-activity")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record_path.read_bytes(), blocked_before)
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "blocked", "unchanged scopes must not implicitly resume the owner")
        self.assertEqual(sessions.board.parse_kv(question)["status"], "open")

    def test_acknowledged_task_can_change_range_without_faking_inbox_completion(self):
        question = self.root / ".multi-agent-collab/inbox/Q-planned-work.md"
        question.write_text("# 已读任务\n- from: human\n- to: codex\n- status: open\n\n请修复src并加入测试。\n\n## 回复\n\n已读取，准备按此任务执行。\n", encoding="utf-8")
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "执行已读且合法的新任务", "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, owner)
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(json.loads(sessions.board.parse_kv(Path(owner["file"]))["files"]), ["src/app.py", "tests/new.py"])
        self.assertEqual(sessions.board.parse_kv(question)["status"], "open")

    def test_active_resume_can_atomically_reconfirm_a_new_task_and_range(self):
        _, owner = self.start(self.root)
        record_path = Path(owner["file"])
        question = self.root / ".multi-agent-collab/inbox/Q-new-active-task.md"
        question.write_text(f"# 合法新任务\n- from: human\n- to: {owner['id']}\n- status: open\n\n请追加一个测试。\n\n## 回复\n\n收到，准备纳入原active会话。\n", encoding="utf-8")
        before = record_path.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record_path.read_bytes(), before)
        result, data = self.command(self.root, "resume", owner["id"], "--agent", "codex", "--ack-inbox", question.name,
                                    "--files", "src/app.py", "tests/new.py", "--note", "纳入新指令并继续原active会话")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["id"], owner["id"])
        self.assertEqual(data["status"], "active")
        fields = sessions.board.parse_kv(record_path)
        self.assertEqual(json.loads(fields["acknowledged_inbox"]), [question.name])
        self.assertEqual(json.loads(fields["files"]), ["src/app.py", "tests/new.py"])
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "tests/new.py", "src/app.py", "docs/new.md")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(sessions.board.parse_kv(question)["status"], "open")

    def test_duplicate_message_identity_needs_fresh_range_reconfirmation(self):
        linked = self.worktree()
        question = self.root / ".multi-agent-collab/inbox/Q-old-identifier.md"
        question.write_text("# 原任务\n- from: human\n- to: codex\n- status: open\n\n已读任务。\n", encoding="utf-8")
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "已纳入原标识", "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, owner)
        duplicate = linked / ".multi-agent-collab/inbox" / question.name
        duplicate.write_text("# 另一条人类任务\n- from: human\n- to: codex\n- status: open\n\n这不是原消息的副本。\n", encoding="utf-8")
        record_path = Path(owner["file"])
        before = record_path.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn(str(question), data["error"])
        self.assertIn(str(duplicate), data["error"])
        self.assertEqual(record_path.read_bytes(), before)

    def test_conflicting_critical_header_cannot_allow_check_or_new_start(self):
        _, owner = self.start(self.root)
        record_path = Path(owner["file"])
        original = record_path.read_bytes()
        text = original.decode("utf-8")
        text = re.sub(r"(?m)^- status: active$", "- status: done\n- status: active", text, count=1)
        text = re.sub(r"(?m)^- files:.*$", lambda match: '- files: ["docs/old.md"]\n' + match.group(0), text, count=1)
        record_path.write_text(text, encoding="utf-8")
        corrupted = record_path.read_bytes()
        existing = {path.name for path in record_path.parent.glob("*.md")}
        try:
            for arguments in (("check", "--agent", "claude", "--files", "src/app.py"),
                              ("start", "--agent", "claude", "--task", "不应获准的新任务", "--files", "docs/new.md")):
                result, data = self.command(self.root, *arguments)
                self.assertEqual(result.returncode, 2, data)
                self.assertIn(str(record_path), data["error"])
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(record_path.read_bytes(), corrupted)
                self.assertEqual({path.name for path in record_path.parent.glob("*.md")}, existing)
        finally:
            record_path.write_bytes(original)

    def test_equal_critical_duplicates_stay_valid_through_handoff_resume_finish(self):
        _, owner = self.start(self.root)
        record_path = Path(owner["file"])
        text = record_path.read_text(encoding="utf-8")
        text = text.replace("- status: active", "- status: active\n* **STATUS**： active", 1)
        record_path.write_text(text, encoding="utf-8")
        for arguments, expected_status in ((("handoff", owner["id"], "--agent", "codex", "--summary", "保留旧相同字段格式"), "blocked"),
                                           (("resume", owner["id"], "--agent", "codex", "--note", "继续同一原会话"), "active"),
                                           (("finish", owner["id"], "--agent", "codex", "--summary", "只完成协调格式回归", "--tests", "实际handoff/resume/finish全部通过"), "done")):
            result, data = self.command(self.root, *arguments)
            self.assertEqual(result.returncode, 0, data)
            self.assertEqual(data["status"], expected_status)
            self.assertEqual(sessions.board.parse_kv(record_path)["status"], expected_status)


class TestNotificationSessionGates(unittest.TestCase):
    setUp = fixtures.TestSessionCoordination.setUp
    tearDown = fixtures.TestSessionCoordination.tearDown
    install = fixtures.TestSessionCoordination.install
    start = fixtures.TestSessionCoordination.start

    def command(self, project, *arguments):
        # These tests assert byte-for-byte gate/ack transactions. Explicit audit
        # mode isolates those writes; normal CLI activity is verified separately
        # by test_activity_integration, including denied calls and output failure.
        return fixtures.TestSessionCoordination.command(self, project, *arguments, "--no-activity")

    def message(self, body="请完善更新测试。", target="codex", action="message"):
        path = self.root / ".multi-agent-collab/inbox/Q-current-request.md"
        path.write_text(f"# Request\n- from: human\n- to: {target}\n- status: open\n- action: {action}\n\n"
                        f"## 指令\n{body}\n\n---\n\n## 回复\n", encoding="utf-8")
        return path

    def test_same_path_request_edit_invalidates_the_old_scope_ack(self):
        question = self.message("第一段要求。\n---\n后半段要求：增加旧版兼容测试。")
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "执行已读要求",
                                    "--files", "src/app.py", "--ack-inbox", str(question))
        self.assertEqual(result.returncode, 0, owner)
        record = Path(owner["file"])
        self.assertIn("acknowledged_inbox_versions", sessions.board.parse_kv(record))
        question.write_text(question.read_text(encoding="utf-8").replace("旧版兼容", "并发回滚"), encoding="utf-8")
        before = record.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex",
                                    "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(data["notifications"]["human_pending"], 1)

    def test_progress_reply_does_not_invalidate_the_original_request(self):
        question = self.message()
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "执行已读要求",
                                    "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, owner)
        with question.open("a", encoding="utf-8") as stream:
            stream.write("已补充验证进度，原要求仍在执行。\n")
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex",
                                    "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(sessions.board.parse_kv(question)["status"], "open")

    def test_unclassifiable_inbox_blocks_check_and_start_without_new_sessions(self):
        question = self.message()
        question.write_text("# 暂停\n这条记录缺少收件人与来源，不能当作没有消息。\n", encoding="utf-8")
        directory = question.parent.parent / "sessions"
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        for command in (("check", "--agent", "codex", "--files", "src/app.py"),
                        ("start", "--agent", "codex", "--task", "不能漏指令", "--files", "src/app.py")):
            result, data = self.command(self.root, *command)
            self.assertEqual(result.returncode, 2, data)
            self.assertIn(str(question), data["error"])
            self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, before)

    def test_typed_pause_cannot_be_removed_by_reading_or_acknowledging(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        question = self.message(target=owner["id"], action="pause")
        record = Path(owner["file"])
        before = record.read_bytes()
        result, data = self.command(self.root, "resume", owner["id"], "--agent", "codex",
                                    "--ack-inbox", question.name, "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record.read_bytes(), before)
        from selftest import PY, run
        reader = self.root / ".multi-agent-collab/tools/agents_messages.py"
        read = run(PY, str(reader), "read", "--root", str(self.root), "--agent", "codex",
                   "--session", owner["id"], "--mark-read", "--no-activity", "--json", cwd=self.root)
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex",
                                    "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record.read_bytes(), before)
        self.assertEqual(data["notifications"]["human_pending"], 1)

    def test_ordinary_ai_notice_is_additive_to_the_check_result(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        question = self.message(target=owner["id"])
        question.write_text(question.read_text(encoding="utf-8").replace("from: human", "from: claude"), encoding="utf-8")
        result, data = self.command(self.root, "check", "--agent", "codex", "--session", owner["id"],
                                    "--files", "src/app.py")
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["notifications"]["unread_count"], 1)
        self.assertEqual(data["notifications"]["human_pending"], 0)
        self.assertNotIn("请完善更新测试", json.dumps(data, ensure_ascii=False))

    def test_legacy_unversioned_ack_requires_one_fresh_confirmation(self):
        question = self.message()
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "执行原要求",
                                    "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, owner)
        record = Path(owner["file"])
        record.write_text(re.sub(r"(?m)^- acknowledged_inbox_versions:.*\n", "", record.read_text(encoding="utf-8")),
                          encoding="utf-8")
        before = record.read_bytes()
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex",
                                    "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record.read_bytes(), before)
        result, data = self.command(self.root, "resume", owner["id"], "--agent", "codex",
                                    "--ack-inbox", question.name, "--files", "src/app.py", "tests/new.py")
        self.assertEqual(result.returncode, 0, data)

    def test_acknowledged_but_unfinished_human_request_cannot_close_the_session(self):
        question = self.message()
        result, owner = self.command(self.root, "start", "--agent", "codex", "--task", "处理原人类要求",
                                    "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, owner)
        record = Path(owner["file"])
        before = record.read_bytes()
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex",
                                    "--summary", "尚未回复原要求", "--tests", "仅临时协作流程检查")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(record.read_bytes(), before)
        question.write_text(question.read_text(encoding="utf-8").replace("- status: open", "- status: answered")
                            + "\n临时协作流程已验证，原要求已回复。\n", encoding="utf-8")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex",
                                    "--summary", "完成临时协作流程", "--tests", "原要求回复与收尾门禁实际验证")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "done")

    def test_installed_wrapper_prints_one_short_unfinished_human_notice(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        self.message(target=owner["id"])
        from selftest import PY, run
        state = self.root / ".multi-agent-collab"
        read = run(PY, str(state / "tools/agents_messages.py"), "read", "--agent", "codex",
                   "--session", owner["id"], "--mark-read", "--json", cwd=self.root)
        self.assertEqual(read.returncode, 0, read.stdout + read.stderr)
        result = run(PY, str(state / "tools/agents_update.py"), "--check", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("提醒：已读人类事项"), 1, result.stdout)
        self.assertNotIn("请完善更新测试", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
