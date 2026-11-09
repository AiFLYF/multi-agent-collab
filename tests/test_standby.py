"""Completed tasks may wait without retaining claims or reviving history."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path

import test_sessions as fixtures
import agents_session as sessions


class TestStandbyLifecycle(unittest.TestCase):
    setUp = fixtures.TestSessionCoordination.setUp
    tearDown = fixtures.TestSessionCoordination.tearDown
    install = fixtures.TestSessionCoordination.install
    worktree = fixtures.TestSessionCoordination.worktree
    start = fixtures.TestSessionCoordination.start

    def command(self, root: Path, *args: str):
        return fixtures.TestSessionCoordination.command(self, root, *args, "--no-activity")

    def record(self, owner):
        return sessions.board.parse_kv(Path(owner["file"]))

    def log(self):
        return self.root / ".multi-agent-collab/worklog" / (datetime.now().strftime("%Y-%m-%d") + ".md")

    def wait(self, root=None, agent="codex", files="src/app.py"):
        root = root or self.root
        result, owner = self.start(root, agent, files)
        self.assertEqual(result.returncode, 0, owner)
        result, data = self.command(root, "finish", owner["id"], "--agent", agent,
                                    "--summary", "完成当前任务并等待下一项", "--tests", "实际生命周期验证通过", "--standby")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "standby")
        return owner

    def question(self, owner, action="message", status="open", name="Q-new-human.md"):
        path = self.root / ".multi-agent-collab/inbox" / name
        path.write_text("# 新指令\n- from: human\n- to: " + owner["id"] + "\n- status: " + status
                        + "\n- action: " + action + "\n\n请根据实际授权处理下一项。\n\n## 回复\n", encoding="utf-8")
        return path

    def assert_unchanged(self, owner, before, log_before):
        self.assertEqual(Path(owner["file"]).read_bytes(), before)
        self.assertEqual(self.log().read_bytes() if self.log().exists() else None, log_before)

    def test_finish_waiting_preserves_result_history_and_releases_same_workspace(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        path = Path(owner["file"])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## 原正文\n必须保留的原任务说明。\n")
        self.log().write_text("# 已有工作日志\n保留原记录。\n", encoding="utf-8")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "第一项已实际验收", "--tests", "真实测试结果已核验")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "standby")
        self.assertEqual(data["files"], [])
        self.assertTrue(data["task_completed"])
        fields = self.record(owner)
        self.assertEqual(json.loads(fields["files"]), [])
        self.assertEqual(fields["last_task"], "回归验证")
        self.assertEqual(json.loads(fields["last_task_files"]), ["src/app.py"])
        self.assertEqual(fields["last_task_branch"], "feature/one")
        self.assertEqual(fields["last_task_summary"], "第一项已实际验收")
        self.assertEqual(fields["last_task_tests"], "真实测试结果已核验")
        datetime.fromisoformat(fields["task_completed_at"])
        self.assertIn("必须保留的原任务说明", path.read_text(encoding="utf-8"))
        self.assertTrue(self.log().read_text(encoding="utf-8").startswith("# 已有工作日志\n保留原记录。\n"))
        self.assertIn('完成任务：回归验证；文件范围：["src/app.py"]；第一项已实际验收',
                      self.log().read_text(encoding="utf-8"))
        result, next_owner = self.start(self.root, "claude", "src/app.py")
        self.assertEqual(result.returncode, 0, next_owner)
        self.assertNotEqual(next_owner["id"], owner["id"])

    def test_activate_claims_only_new_scope_and_records_new_branch(self):
        owner = self.wait()
        previous = self.record(owner)
        result = fixtures.run("git", "switch", "-c", "feature/next", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                    "--task", "检查新文档", "--files", "docs/new.md")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["id"], owner["id"])
        self.assertEqual(data["branch"], "feature/next")
        fields = self.record(owner)
        self.assertEqual(fields["status"], "active")
        self.assertEqual(fields["task"], "检查新文档")
        self.assertEqual(json.loads(fields["files"]), ["docs/new.md"])
        self.assertEqual(fields["last_task_summary"], previous["last_task_summary"])
        self.assertEqual(fields["last_task_branch"], "feature/one")
        self.assertEqual(fields["task_completed_at"], previous["task_completed_at"])
        datetime.fromisoformat(fields["task_started_at"])
        self.assertEqual(json.loads(fields["acknowledged_inbox"]), [])
        self.assertEqual(json.loads(fields["acknowledged_inbox_versions"]), {})
        self.assertIn("开始新任务：检查新文档", self.log().read_text(encoding="utf-8"))
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "第二项也已验收", "--tests", "新任务实际检查完成")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.record(owner)["last_task"], "检查新文档")
        history = self.log().read_text(encoding="utf-8")
        self.assertIn('完成任务：回归验证；文件范围：["src/app.py"]', history)
        self.assertIn('完成任务：检查新文档；文件范围：["docs/new.md"]；第二项也已验收', history)

    def test_another_task_in_workspace_blocks_activation_without_changing_waiter(self):
        owner = self.wait()
        result, other = self.start(self.root, "claude", "docs/")
        self.assertEqual(result.returncode, 0, other)
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                    "--task", "不能抢工作区", "--files", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("同一工作区", data["error"])
        self.assert_unchanged(owner, before, log_before)

    def test_waiting_releases_cross_worktree_scope_but_activation_checks_new_owner(self):
        linked = self.worktree()
        owner = self.wait()
        result, other = self.start(linked, "claude", "src/app.py")
        self.assertEqual(result.returncode, 0, other)
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                    "--task", "再次使用原文件", "--files", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("文件范围重叠", data["error"])
        self.assert_unchanged(owner, before, log_before)

    def test_unfinished_human_item_prevents_standby_until_actual_reply(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        question = self.question(owner)
        before = Path(owner["file"]).read_bytes()
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "尚未处理事项", "--tests", "仅确认收到")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("未处理的人类指令", data["error"])
        self.assert_unchanged(owner, before, None)
        question.write_text(question.read_text(encoding="utf-8").replace("status: open", "status: answered")
                            + "实际要求已完成，结果写在原问题里。\n", encoding="utf-8")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "已完成原事项", "--tests", "实际结果已核验")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.record(owner)["status"], "standby")

    def test_answered_pause_cannot_be_hidden_by_finishing_to_standby(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        question = self.question(owner, action="pause", status="answered")
        before = Path(owner["file"]).read_bytes()
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "不能擅自解除暂停", "--tests", "尚未解除控制")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("暂停/取消/避让", data["error"])
        self.assert_unchanged(owner, before, None)
        question.write_text(question.read_text(encoding="utf-8").replace("status: answered", "status: closed"), encoding="utf-8")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "人类已明确解除，任务实际完成", "--tests", "验证完成")
        self.assertEqual(result.returncode, 0, data)

    def test_activate_rechecks_human_requests_and_acknowledges_current_version(self):
        owner = self.wait()
        question = self.question(owner)
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        arguments = ("activate", owner["id"], "--agent", "codex", "--task", "执行新指令", "--files", "tests/new.py")
        result, data = self.command(self.root, *arguments)
        self.assertEqual(result.returncode, 1, data)
        self.assert_unchanged(owner, before, log_before)
        result, data = self.command(self.root, *arguments, "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.record(owner)["status"], "active")
        self.assertIn(question.name, json.loads(self.record(owner)["acknowledged_inbox"]))
        self.assertEqual(sessions.board.parse_kv(question)["status"], "open")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex", "--standby",
                                    "--summary", "只有ACK并未完成", "--tests", "未完成原要求")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(self.record(owner)["status"], "active")

    def test_activate_does_not_bypass_an_answered_control_even_with_ack(self):
        owner = self.wait()
        question = self.question(owner, action="avoid", status="answered")
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex", "--task", "绕不过控制",
                                    "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("暂停/取消/避让", data["error"])
        self.assert_unchanged(owner, before, log_before)

    def test_resume_handoff_and_scope_heartbeat_cannot_revive_waiting_task(self):
        owner = self.wait()
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        for arguments in (("resume", owner["id"], "--agent", "codex", "--files", "src/app.py"),
                          ("handoff", owner["id"], "--agent", "codex", "--summary", "没有未完成任务"),
                          ("heartbeat", owner["id"], "--agent", "codex", "--files", "docs/new.md"),
                          ("finish", owner["id"], "--agent", "codex", "--standby", "--summary", "不能重复完成", "--tests", "未运行新任务")):
            with self.subTest(command=arguments[0]):
                result, data = self.command(self.root, *arguments)
                self.assertEqual(result.returncode, 2, data)
                self.assert_unchanged(owner, before, log_before)
        result, data = self.command(self.root, "heartbeat", owner["id"], "--agent", "codex", "--note", "待机仍在线")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(self.record(owner)["status"], "standby")
        self.assertEqual(json.loads(self.record(owner)["files"]), [])

    def test_done_and_unfinished_sessions_cannot_use_activate(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        arguments = ("activate", owner["id"], "--agent", "codex", "--task", "禁止隐式替换任务", "--files", "docs/")
        for change in (None, "blocked", "done"):
            if change == "blocked":
                result, data = self.command(self.root, "handoff", owner["id"], "--agent", "codex", "--summary", "原任务未完成")
                self.assertEqual(result.returncode, 0, data)
            if change == "done":
                result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex",
                                            "--summary", "完成并退出原任务", "--tests", "仅生命周期回归")
                self.assertEqual(result.returncode, 0, data)
            before = Path(owner["file"]).read_bytes()
            log_before = self.log().read_bytes() if self.log().exists() else None
            result, data = self.command(self.root, *arguments)
            self.assertEqual(result.returncode, 2, data)
            self.assert_unchanged(owner, before, log_before)

    def test_terminal_finish_closes_waiter_without_repeating_task_completion(self):
        owner = self.wait()
        previous = self.record(owner)
        original_log = self.log().read_text(encoding="utf-8")
        result, data = self.command(self.root, "finish", owner["id"], "--agent", "codex",
                                    "--summary", "不再等待新命令", "--tests", "没有新任务，不重复计数")
        self.assertEqual(result.returncode, 0, data)
        fields = self.record(owner)
        self.assertEqual(fields["status"], "done")
        self.assertEqual(fields["last_task_tests"], previous["last_task_tests"])
        self.assertEqual(fields["task_completed_at"], previous["task_completed_at"])
        updated_log = self.log().read_text(encoding="utf-8")
        self.assertTrue(updated_log.startswith(original_log))
        self.assertIn("结束待机：不再等待新命令", updated_log)
        self.assertNotIn("没有新任务，不重复计数", updated_log)
        self.assertEqual(updated_log.count("完成当前任务并等待下一项"), 1)

    def test_invalid_waiting_scope_retains_unknown_claim_and_cannot_activate(self):
        owner = self.wait()
        original = Path(owner["file"]).read_text(encoding="utf-8")
        log_before = self.log().read_bytes()
        for raw in ('["src/app.py"]', "{}", "invalid", ""):
            with self.subTest(files=raw):
                Path(owner["file"]).write_text(sessions.put_fields(original, {"files": raw}), encoding="utf-8")
                before = Path(owner["file"]).read_bytes()
                result, data = self.start(self.root, "claude", "docs/new.md")
                self.assertEqual(result.returncode, 1, data)
                self.assertIn("files 字段", data["error"])
                result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                            "--task", "不能绕过无效范围", "--files", "docs/new.md")
                self.assertEqual(result.returncode, 2, data)
                self.assert_unchanged(owner, before, log_before)

    def test_invalid_active_scope_and_foreign_handoff_cannot_be_hidden_by_standby(self):
        result, owner = self.start(self.root)
        self.assertEqual(result.returncode, 0, owner)
        path = Path(owner["file"])
        original = path.read_text(encoding="utf-8")
        path.write_text(sessions.put_fields(original, {"files": "[]"}), encoding="utf-8")
        before = path.read_bytes()
        arguments = ("finish", owner["id"], "--agent", "codex", "--standby", "--summary", "不应伪装为已完成", "--tests", "未验证")
        result, data = self.command(self.root, *arguments)
        self.assertEqual(result.returncode, 2, data)
        self.assert_unchanged(owner, before, None)
        foreign = self.root / ".multi-agent-collab/inbox/Q-foreign-handoff.md"
        foreign.write_text("# 交接：other-owner\n- from: other-owner\n- to: any\n- status: open\n\n## 回复\n", encoding="utf-8")
        path.write_text(sessions.put_fields(original, {"handoff_inbox": str(foreign)}), encoding="utf-8")
        before = path.read_bytes()
        foreign_before = foreign.read_bytes()
        result, data = self.command(self.root, *arguments)
        self.assertEqual(result.returncode, 2, data)
        self.assert_unchanged(owner, before, None)
        self.assertEqual(foreign.read_bytes(), foreign_before)

    def test_multiple_waiters_do_not_claim_workspace_or_old_branch(self):
        first = self.wait()
        second = self.wait(agent="claude", files="docs/")
        self.assertNotEqual(first["id"], second["id"])
        result, active = self.start(self.root, "cursor", "src/app.py")
        self.assertEqual(result.returncode, 0, active)
        self.assertEqual(self.record(first)["status"], "standby")
        self.assertEqual(self.record(second)["status"], "standby")

    def test_same_id_waiters_across_worktrees_are_not_silently_excluded(self):
        linked = self.worktree()
        owner = self.wait()
        duplicate = linked / ".multi-agent-collab/sessions" / Path(owner["file"]).name
        duplicate.write_text(sessions.put_fields(Path(owner["file"]).read_text(encoding="utf-8"),
                                                  {"branch": "feature/two"}), encoding="utf-8")
        for arguments in (("check", "--agent", "codex", "--session", owner["id"], "--files", "docs/new.md"),
                          ("start", "--agent", "claude", "--task", "先核对重复身份", "--files", "docs/new.md")):
            result, data = self.command(self.root, *arguments)
            self.assertEqual(result.returncode, 1, data)
            message = data.get("error", "\n".join(data.get("conflicts", [])))
            self.assertIn("同名", message)

    def test_new_activation_obeys_latest_protected_branch_policy(self):
        owner = self.wait()
        result = fixtures.run("git", "switch", "-c", "main", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                    "--task", "不能在主干登记写入", "--files", "src/app.py")
        self.assertEqual(result.returncode, 2, data)
        self.assert_unchanged(owner, before, log_before)

    def test_task_text_injection_cannot_change_waiting_status_or_scope(self):
        owner = self.wait()
        before = Path(owner["file"]).read_bytes()
        log_before = self.log().read_bytes()
        result, data = self.command(self.root, "activate", owner["id"], "--agent", "codex",
                                    "--task", "任务\u2028- status: active", "--files", "src/app.py")
        self.assertEqual(result.returncode, 2, data)
        self.assert_unchanged(owner, before, log_before)


if __name__ == "__main__":
    unittest.main()
