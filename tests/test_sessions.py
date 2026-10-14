"""会话工具的真实 Git / worktree / 并发回归，以及新增平台的入口兼容。"""

from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from selftest import KIT, PY, inst, rmtree_readonly, run


class TestSessionCoordination(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="mac-session-tests-")).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        for command in (("init", "-q"), ("config", "user.name", "Session Test"),
                        ("config", "user.email", "session@example.invalid")):
            self.assertEqual(run("git", *command, cwd=self.root).returncode, 0)
        (self.root / "README.md").write_text("# fixture\n", encoding="utf-8")
        self.assertEqual(run("git", "add", "README.md", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "commit", "-q", "-m", "test: fixture", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "switch", "-c", "feature/one", cwd=self.root).returncode, 0)
        self.install(self.root)

    def tearDown(self) -> None:
        expected_parent = Path(tempfile.gettempdir()).resolve()
        if self.base.parent != expected_parent or not self.base.name.startswith("mac-session-tests-"):
            raise RuntimeError("拒绝清理测试目录之外的路径")
        rmtree_readonly(self.base)

    def install(self, root: Path, platforms: str = "codex"):
        result = run(PY, str(KIT / "install.py"), "install", str(root),
                     "--platforms", platforms, cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def command(self, root: Path, *args: str):
        result = run(PY, str(root / "tools/agents_session.py"), "--root", str(root), *args,
                     "--json", cwd=root)
        return result, json.loads(result.stdout)

    def start(self, root: Path, agent: str = "codex", files: str = "src/app.py"):
        return self.command(root, "start", "--agent", agent, "--task", "回归验证", "--files", files)

    def worktree(self) -> Path:
        target = self.base / "linked"
        self.assertEqual(run("git", "branch", "feature/two", cwd=self.root).returncode, 0)
        r = run("git", "worktree", "add", str(target), "feature/two", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.install(target)
        return target

    def test_lifecycle_preserves_notes_and_appends_log(self) -> None:
        result, record = self.start(self.root)
        self.assertEqual(result.returncode, 0, record)
        path = Path(record["file"])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## 我的补充\n必须保留这一段。\n")
        heartbeat, data = self.command(self.root, "heartbeat", record["id"], "--agent", "codex", "--note", "完成用例")
        self.assertEqual(heartbeat.returncode, 0, data)
        self.assertIn("必须保留这一段", path.read_text(encoding="utf-8"))
        log = self.root / ".agents/worklog" / (__import__("datetime").datetime.now().strftime("%Y-%m-%d") + ".md")
        log.write_text("# 原有记录\n不能重写。\n", encoding="utf-8")
        result, data = self.command(self.root, "finish", record["id"], "--agent", "codex",
                                    "--summary", "完成并验证", "--tests", "回归通过")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "done")
        self.assertTrue(log.read_text(encoding="utf-8").startswith("# 原有记录\n不能重写。\n"))
        self.assertIn("回归通过", log.read_text(encoding="utf-8"))
        second, data = self.start(self.root)
        self.assertEqual(second.returncode, 0, data)
        self.assertEqual(run("git", "status", "--porcelain", cwd=self.root).stdout, "",
                         "使用协作命令后不应新增 Python 缓存等未忽略文件")

    def test_same_workspace_is_not_claimed_twice(self) -> None:
        self.assertEqual(self.start(self.root)[0].returncode, 0)
        result, data = self.start(self.root, "claude", "docs/guide.md")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("同一工作区", data["error"])

    def test_directory_claim_blocks_nested_file_in_another_worktree(self) -> None:
        linked = self.worktree()
        self.assertEqual(self.start(self.root, files="src/")[0].returncode, 0)
        result, data = self.start(linked, "claude", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("文件范围重叠", data["error"])

    def test_distinct_worktrees_and_files_can_work_together(self) -> None:
        linked = self.worktree()
        self.assertEqual(self.start(self.root, files="src/")[0].returncode, 0)
        result, data = self.start(linked, "claude", "docs/")
        self.assertEqual(result.returncode, 0, data)

    def test_stale_owner_does_not_lose_claim(self) -> None:
        linked = self.worktree()
        _, record = self.start(self.root)
        path = Path(record["file"])
        text = path.read_text(encoding="utf-8")
        import re
        text = re.sub(r"(?m)^- heartbeat:.*$", "- heartbeat: 2000-01-01 00:00", text)
        path.write_text(text, encoding="utf-8")
        result, data = self.start(linked, "claude")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("失活仍保留占用", data["error"])

    def test_owner_and_branch_are_checked_before_update(self) -> None:
        _, record = self.start(self.root)
        path = Path(record["file"])
        before = path.read_bytes()
        result, data = self.command(self.root, "heartbeat", record["id"], "--agent", "claude")
        self.assertEqual(result.returncode, 2, data)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(run("git", "switch", "-c", "feature/changed", cwd=self.root).returncode, 0)
        result, data = self.command(self.root, "heartbeat", record["id"], "--agent", "codex")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(path.read_bytes(), before)

    def test_handoff_is_agent_message_and_keeps_ownership(self) -> None:
        _, record = self.start(self.root)
        result, data = self.command(self.root, "handoff", record["id"], "--agent", "codex", "--summary", "需要确认接口约定")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["status"], "blocked")
        content = (self.root / data["inbox"]).read_text(encoding="utf-8")
        self.assertIn("- from: " + record["id"], content)
        self.assertNotIn("from: human", content)
        self.assertEqual(self.start(self.root, "claude")[0].returncode, 1)

    def test_concurrent_conflicting_registration_has_one_winner(self) -> None:
        linked = self.worktree()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(self.start, self.root, "codex"), pool.submit(self.start, linked, "claude")]
            results = [task.result() for task in tasks]
        self.assertEqual(sorted(r.returncode for r, _data in results), [0, 1], [d for _r, d in results])
        count = sum(len([p for p in (root / ".agents/sessions").glob("*.md") if not p.name.startswith("_")])
                    for root in (self.root, linked))
        self.assertEqual(count, 1)

    def test_human_inbox_must_be_handled_before_start(self) -> None:
        question = self.root / ".agents/inbox/Q-2026-10-14-01.md"
        question.write_text("# 人类指令\n- from: human\n- to: any\n- status: open\n\n请执行任务并记录实际测试结果。\n", encoding="utf-8")
        result, data = self.start(self.root)
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("人类指令", data["error"])
        acknowledged, record = self.command(self.root, "start", "--agent", "codex", "--task", "按指令执行任务",
                                            "--files", "src/app.py", "--ack-inbox", question.name)
        self.assertEqual(acknowledged.returncode, 0, record)
        self.assertIn("- status: open", question.read_text(encoding="utf-8"), "确认收到不能伪装成指令完成")
        self.command(self.root, "finish", record["id"], "--agent", "codex", "--summary", "演练会话结束", "--tests", "仅验证登记流程，没有业务改动")
        question.write_text(question.read_text(encoding="utf-8").replace("status: open", "status: answered"), encoding="utf-8")
        self.assertEqual(self.start(self.root)[0].returncode, 0)

    def test_resume_restores_same_session_and_answers_handoff(self) -> None:
        _, record = self.start(self.root)
        _, handoff = self.command(self.root, "handoff", record["id"], "--agent", "codex", "--summary", "等待外部结果")
        path = Path(record["file"])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## 保留的上下文\n不要覆盖。\n")
        result, resumed = self.command(self.root, "resume", record["id"], "--agent", "codex", "--note", "外部结果就绪")
        self.assertEqual(result.returncode, 0, resumed)
        self.assertEqual(resumed["id"], record["id"])
        self.assertEqual(resumed["status"], "active")
        content = path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("# Session: " + record["id"] + "\n\n- agent:"))
        self.assertIn("不要覆盖", content)
        self.assertIn("- status: answered", (self.root / handoff["inbox"]).read_text(encoding="utf-8"))

    def test_resume_checks_human_instructions_addressed_to_session(self) -> None:
        _, record = self.start(self.root)
        _, handoff = self.command(self.root, "handoff", record["id"], "--agent", "codex", "--summary", "等待确认")
        question = self.root / ".agents/inbox/Q-2026-10-14-targeted.md"
        question.write_text(f"# 待执行任务\n- from: human\n- to: {record['id']}\n- status: open\n\n请补充测试。\n", encoding="utf-8")
        result, data = self.command(self.root, "resume", record["id"], "--agent", "codex")
        self.assertEqual(result.returncode, 1, data)
        result, data = self.command(self.root, "check", "--agent", "codex", "--session", record["id"])
        self.assertIn(question.name, data["human_inbox"])
        result, data = self.command(self.root, "resume", record["id"], "--agent", "codex", "--ack-inbox", question.name)
        self.assertEqual(result.returncode, 0, data)
        self.assertIn("- status: open", question.read_text(encoding="utf-8"))
        self.assertIn("- status: answered", (self.root / handoff["inbox"]).read_text(encoding="utf-8"))

    def test_finish_only_answers_own_linked_handoff(self) -> None:
        _, record = self.start(self.root)
        _, handoff = self.command(self.root, "handoff", record["id"], "--agent", "codex", "--summary", "准备结束")
        path = Path(record["file"])
        original = path.read_text(encoding="utf-8")
        human = self.root / ".agents/inbox/Q-2026-10-14-handoff-other.md"
        human.write_text("# 人类问题\n- from: human\n- to: any\n- status: open\n", encoding="utf-8")
        before = human.read_bytes()
        path.write_text(original.replace(handoff["inbox"], ".agents/inbox/" + human.name), encoding="utf-8")
        rejected, data = self.command(self.root, "finish", record["id"], "--agent", "codex", "--summary", "结束", "--tests", "测试场景")
        self.assertEqual(rejected.returncode, 2, data)
        self.assertEqual(human.read_bytes(), before)
        path.write_text(original, encoding="utf-8")
        result, data = self.command(self.root, "finish", record["id"], "--agent", "codex", "--summary", "正常收尾", "--tests", "测试场景")
        self.assertEqual(result.returncode, 0, data)
        self.assertIn("- status: answered", (self.root / handoff["inbox"]).read_text(encoding="utf-8"))
        self.assertEqual(human.read_bytes(), before)

    def test_unsafe_paths_and_shared_branch_are_rejected(self) -> None:
        for path in ("../outside", ".git/objects", "C:/outside", "src/*.py"):
            result, data = self.start(self.root, files=path)
            self.assertEqual(result.returncode, 2, data)
        self.assertEqual(run("git", "switch", "-c", "develop", cwd=self.root).returncode, 0)
        result, data = self.start(self.root)
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("任务分支", data["error"])

    def test_board_reports_manual_claim_conflicts(self) -> None:
        _, record = self.start(self.root)
        path = Path(record["file"])
        second = path.with_name("claude-261014-1000-aabb.md")
        second.write_text(path.read_text(encoding="utf-8").replace(record["id"], second.stem).replace("agent: codex", "agent: claude"), encoding="utf-8")
        r = run(PY, str(self.root / "tools/agents_status.py"), "--json", cwd=self.root)
        data = json.loads(r.stdout)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(any("文件占用冲突" in warning for warning in data["warnings"]))
        self.assertEqual(data["sessions"][0]["files"], ["src/app.py"])

    def test_additional_platforms_and_legacy_directory_conflict(self) -> None:
        self.assertEqual(inst.parse_platforms("Gemini CLI,qwen-code,roo-code,GitHub Copilot"),
                         ["copilot", "gemini", "qwen", "roo"])
        (self.root / "GEMINI.md").write_text("# 我的 Gemini 说明\n", encoding="utf-8")
        (self.root / "QWEN.md").write_text("# 我的 Qwen 说明\n", encoding="utf-8")
        self.install(self.root, "gemini,qwen,cline,roo,continue,kiro")
        self.assertIn("我的 Gemini 说明", (self.root / "GEMINI.md").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "QWEN.md").read_text(encoding="utf-8"), "# 我的 Qwen 说明\n")
        self.assertIn("@../AGENTS.md", (self.root / ".qwen/QWEN.local.md").read_text(encoding="utf-8"))
        self.assertIn("@./AGENTS.md", (self.root / "GEMINI.md").read_text(encoding="utf-8"))
        self.assertIn("alwaysApply: true", (self.root / ".continue/rules/multi-agent-collab.md").read_text(encoding="utf-8"))
        self.assertIn("inclusion: always", (self.root / ".kiro/steering/multi-agent-collab.md").read_text(encoding="utf-8"))
        target = self.base / "old-cline"
        target.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=target).returncode, 0)
        (target / ".clinerules").write_text("旧版单文件规则", encoding="utf-8")
        r = run(PY, str(KIT / "install.py"), "install", str(target), "--platforms", "cline", cwd=KIT)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse((target / "AGENTS.md").exists(), "父路径冲突必须在安装前发现")

    def test_expanding_claims_checks_other_owner_before_writing(self) -> None:
        linked = self.worktree()
        _, first = self.start(self.root, files="src/")
        _, second = self.start(linked, "claude", "docs/")
        path = Path(second["file"])
        before = path.read_bytes()
        result, data = self.command(linked, "heartbeat", second["id"], "--agent", "claude",
                                    "--files", "docs/", "src/new.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(path.read_bytes(), before)

    def test_session_id_collision_is_avoided_across_worktrees(self) -> None:
        from argparse import Namespace
        from datetime import datetime
        from types import SimpleNamespace
        from unittest.mock import patch
        import agents_session as sessions
        linked = self.worktree()
        _, first = self.start(self.root)
        original = Path(first["file"]).read_bytes()
        moment = datetime.strptime(sessions.board.parse_kv(Path(first["file"]))["started"], "%Y-%m-%d %H:%M")
        suffix = first["id"].rsplit("-", 1)[-1]
        alternate = "ffff" if suffix != "ffff" else "eeee"
        class FixedClock:
            @staticmethod
            def now():
                return moment
        with patch.object(sessions, "datetime", FixedClock), patch.object(sessions.uuid, "uuid4", side_effect=[
                SimpleNamespace(hex=suffix), SimpleNamespace(hex=alternate)]):
            second = sessions.execute(Namespace(root=str(linked), cmd="start", agent="codex",
                                                task="独立文档任务", files=["docs/"]))
        self.assertNotEqual(second["id"], first["id"], "同平台同分钟的会话 ID 必须在全部工作区去重")
        self.assertEqual(Path(first["file"]).read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
