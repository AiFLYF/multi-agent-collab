"""真实临时 Git 仓库验证项目策略与接管；等待边界仅在测试中替换时钟。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import test_sessions as fixtures
from selftest import KIT, PY, inst, run
import agents_session as sessions
from agents_policy import build_policy, check_task_branch, read_policy, validate_policy


class GitFixture(unittest.TestCase):
    # 复用真实 Git 环境，不继承已有测试，避免重复运行同一套用例。
    def setUp(self):
        fixtures.TestSessionCoordination.setUp(self)
        # Installation deliberately changes this shared project file; the fixture
        # accepts it before simulating an abandoned business task.
        self.assertEqual(run("git", "add", ".gitignore", cwd=self.root).returncode, 0)
        self.assertEqual(run("git", "commit", "-q", "-m", "test: accept private state ignore", cwd=self.root).returncode, 0)

    tearDown = fixtures.TestSessionCoordination.tearDown
    install = fixtures.TestSessionCoordination.install
    command = fixtures.TestSessionCoordination.command
    start = fixtures.TestSessionCoordination.start
    worktree = fixtures.TestSessionCoordination.worktree

    def configure(self, *args, root=None, expected=0):
        result = run(PY, str(KIT / "install.py"), "install", str(root or self.root), *args, cwd=KIT)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result


class TestPolicyValues(unittest.TestCase):
    def test_presets_and_custom_always_protect_main_and_base(self):
        for policy in (build_policy(), build_policy("github-flow"),
                       build_policy("custom", "trunk", ["task/"], ["task/integration"])):
            for branch in {"main", "master", "develop", policy["base_branch"]} | set(policy["protected_branches"]):
                with self.subTest(policy=policy["workflow"], branch=branch), self.assertRaises(ValueError):
                    check_task_branch(branch, policy)
            check_task_branch(policy["task_prefixes"][0] + "sample", policy)
        self.assertEqual(build_policy("github-flow")["base_branch"], "main")
        self.assertIn("codex/", build_policy("github-flow")["task_prefixes"])

    def test_custom_requires_explicit_valid_branch_and_prefixes(self):
        for kwargs in ({}, {"base_branch": "trunk"}, {"base_branch": "../main", "task_prefixes": ["task/"]},
                       {"base_branch": "trunk", "task_prefixes": ["task"]},
                       {"base_branch": "trunk", "task_prefixes": [None]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build_policy("custom", **kwargs)
        with self.assertRaises(ValueError):
            build_policy("git-flow", "main")

    def test_invalid_saved_fields_never_fall_back_to_defaults(self):
        original = build_policy()
        invalid = [None, [], {}, dict(original, schema_version=True), dict(original, base_branch=""),
                   dict(original, task_prefixes=None), dict(original, protected_branches={}),
                   dict(original, workflow=["git-flow"]), dict(original, extra=True)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_policy(value)


class TestProjectPolicy(GitFixture):
    def test_install_upgrade_and_sync_preserve_explicit_policy(self):
        self.configure("--workflow", "custom", "--base-branch", "trunk", "--task-prefixes", "feature/,task/",
                       "--protect-branches", "task/integration")
        before = read_policy(self.root)
        self.configure()
        result = run(PY, str(KIT / "install.py"), "sync", str(self.root), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_policy(self.root), before)
        self.assertIn("`trunk`", (self.root / ".multi-agent-collab/POLICY.md").read_text(encoding="utf-8"))
        for path in (".multi-agent-collab/policy.json", ".multi-agent-collab/POLICY.md", ".multi-agent-collab/tools/agents_policy.py"):
            self.assertEqual(run("git", "check-ignore", "-q", path, cwd=self.root).returncode, 0)

    def test_policy_controls_registration_and_check_but_allows_existing_task_cleanup(self):
        _, record = self.start(self.root)
        self.configure("--workflow", "custom", "--base-branch", "trunk", "--task-prefixes", "task/")
        result, data = self.command(self.root, "check", "--session", record["id"], "--files", "src/app.py")
        self.assertEqual(result.returncode, 1, data)
        self.assertFalse(data["task_branch_allowed"])
        self.assertEqual(data["policy"]["base_branch"], "trunk")
        result, data = self.command(self.root, "heartbeat", record["id"], "--agent", "codex", "--note", "准备交接")
        self.assertEqual(result.returncode, 0, data)
        result, _ = self.command(self.root, "heartbeat", record["id"], "--agent", "codex", "--files", "src/new.py")
        self.assertEqual(result.returncode, 2)
        result, data = self.command(self.root, "finish", record["id"], "--agent", "codex", "--summary", "记录收尾",
                                    "--tests", "测试夹具，没有业务任务")
        self.assertEqual(result.returncode, 0, data)

    def test_main_worktree_policy_is_authoritative_and_linked_override_is_rejected(self):
        self.configure("--workflow", "github-flow")
        linked = self.worktree()
        self.assertEqual(read_policy(linked), read_policy(self.root))
        before = (linked / ".multi-agent-collab/policy.json").read_bytes()
        self.configure("--workflow", "custom", "--base-branch", "trunk", "--task-prefixes", "task/",
                       root=linked, expected=2)
        self.assertEqual((linked / ".multi-agent-collab/policy.json").read_bytes(), before)
        self.configure("--workflow", "custom", "--base-branch", "trunk", "--task-prefixes", "task/")
        result, data = self.start(linked, "claude")
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("前缀", data["error"], "linked 旧副本不能绕过主策略")
        result = run(PY, str(KIT / "install.py"), "sync", str(linked), cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(read_policy(linked), read_policy(self.root))

    def test_malformed_policy_fails_before_install_or_session_writes(self):
        path = self.root / ".multi-agent-collab/policy.json"
        path.write_text("[]", encoding="utf-8")
        agents_before = (self.root / "AGENTS.md").read_bytes()
        self.configure(expected=2)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), agents_before)
        result, data = self.start(self.root)
        self.assertEqual(result.returncode, 2, data)
        self.assertIn("策略", data["error"])

    def test_missing_policy_keeps_legacy_git_flow_and_uninstall_preserves_policy_data(self):
        path = self.root / ".multi-agent-collab/policy.json"
        path.unlink()
        result, data = self.command(self.root, "check", "--files", "src/app.py")
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["policy"]["workflow"], "git-flow")
        self.configure()
        original = path.read_bytes()
        result = run(PY, str(KIT / "install.py"), "uninstall", str(self.root), "--purge-files", cwd=KIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue((self.root / ".multi-agent-collab/POLICY.md").is_file())


class TestClientReadiness(unittest.TestCase):
    def test_discovery_is_separate_from_versions_and_ide_manual_verification(self):
        with mock.patch.object(inst.shutil, "which", return_value=None), mock.patch.object(inst.subprocess, "run") as probe:
            rows = inst.client_readiness(["codex", "cursor"], True)
            probe.assert_not_called()
        self.assertEqual([r["probe_status"] for r in rows], ["not-found", "manual"])
        self.assertIsNone(rows[1]["discovered"])
        with mock.patch.object(inst.shutil, "which", return_value="codex"), mock.patch.object(inst.subprocess, "run") as probe:
            self.assertEqual(inst.client_readiness(["codex"])[0]["probe_status"], "not-requested")
            probe.assert_not_called()

    def test_version_probes_keep_only_version_and_never_certify_session_loading(self):
        result = subprocess.CompletedProcess(["codex", "--version"], 0, "private-output\ncodex-cli 0.162.1\n", "private-error")
        with mock.patch.object(inst.shutil, "which", return_value="known-codex"), \
                mock.patch.object(inst.subprocess, "run", return_value=result) as probe:
            row = inst.client_readiness(["codex"], True)[0]
        self.assertEqual(probe.call_args.args[0], ["known-codex", "--version"])
        self.assertEqual(row["version"], "0.162.1")
        self.assertFalse(row["session_verified"])
        self.assertNotIn("private", json.dumps(row))

    def test_failed_and_timed_out_probes_report_status_without_raw_output(self):
        for response, expected in ((subprocess.CompletedProcess([], 1, "secret 1.2.3", "secret"), "failed"),
                                   (subprocess.CompletedProcess([], 0, "unusual output", "secret"), "unrecognized"),
                                   (subprocess.TimeoutExpired("codex", 12, output="secret"), "timeout")):
            kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with self.subTest(expected=expected), mock.patch.object(inst.shutil, "which", return_value="codex"), \
                    mock.patch.object(inst.subprocess, "run", **kwargs):
                row = inst.client_readiness(["codex"], True)[0]
            self.assertEqual(row["probe_status"], expected)
            self.assertIsNone(row["version"])
            self.assertNotIn("secret", json.dumps(row))


class TestTakeover(GitFixture):
    def make_stale(self, path):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^- heartbeat:.*$", "- heartbeat: 2000-01-01 00:00", text)
        path.write_text(text, encoding="utf-8")
        old = time.time() - 1800
        os.utime(path, (old, old))

    def stale_session(self, dirty=False):
        result, record = self.start(self.root)
        self.assertEqual(result.returncode, 0, record)
        path = Path(record["file"])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## 保留的交接上下文\n不能丢失原任务记录。\n")
        self.make_stale(path)
        if dirty:
            old = time.time() - 1800
            for name in ("README.md", "notes with space.txt"):
                target = self.root / name
                target.write_text("未提交内容，必须原样保留。\n", encoding="utf-8")
                os.utime(target, (old, old))
        return record, path

    def plan(self, record):
        result, data = self.command(self.root, "takeover-plan", record["id"], "--agent", "claude", "--reason", "测试夹具原会话失联")
        self.assertEqual(result.returncode, 0, data)
        path = self.root / ".multi-agent-collab/takeovers" / (data["plan"] + ".json")
        return data, path, json.loads(path.read_text(encoding="utf-8"))

    def apply(self, plan, offset=300, **overrides):
        values = dict(cmd="takeover-apply", root=str(self.root), plan=plan["id"], agent="claude",
                      confirm_session=plan["snapshot"]["session_id"], approval_note="测试夹具中的明确用户确认", ack_inbox=None)
        values.update(overrides)
        with mock.patch.object(sessions.time, "time", return_value=plan["created_at"] + offset):
            return sessions.execute(argparse.Namespace(**values))

    def test_fresh_owner_cannot_be_reclaimed(self):
        _, record = self.start(self.root)
        before = Path(record["file"]).read_bytes()
        result, data = self.command(self.root, "takeover-inspect", record["id"])
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["blockers"])
        result, data = self.command(self.root, "takeover-plan", record["id"], "--agent", "claude", "--reason", "测试")
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(Path(record["file"]).read_bytes(), before)

    def test_stale_plan_waits_then_closes_only_coordination_preserving_git_and_dirty_files(self):
        record, path = self.stale_session(dirty=True)
        git_before = run("git", "status", "--porcelain", cwd=self.root).stdout
        refs_before = run("git", "show-ref", cwd=self.root).stdout
        file_before = {name: (self.root / name).read_bytes() for name in ("README.md", "notes with space.txt")}
        data, plan_path, plan = self.plan(record)
        self.assertEqual(self.start(self.root, "claude")[0].returncode, 1)
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan, 299.99)
        result = self.apply(plan, 300)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["status"], "applied")
        self.assertIn("- status: closed", path.read_text(encoding="utf-8"))
        self.assertIn("不能丢失原任务记录", path.read_text(encoding="utf-8"))
        self.assertNotIn("- status: done", path.read_text(encoding="utf-8"))
        self.assertEqual(run("git", "status", "--porcelain", cwd=self.root).stdout, git_before)
        self.assertEqual(run("git", "show-ref", cwd=self.root).stdout, refs_before)
        self.assertEqual(run("git", "branch", "--show-current", cwd=self.root).stdout.strip(), "feature/one")
        for name, content in file_before.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertIn("- status: answered", (self.root / data["declaration"]).read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual(self.start(self.root, "claude")[0].returncode, 0)

    def test_wrong_requester_and_repeated_target_are_rejected_without_mutation(self):
        record, path = self.stale_session()
        _, plan_path, plan = self.plan(record)
        original, plan_before = path.read_bytes(), plan_path.read_bytes()
        for override in ({"agent": "codex"}, {"confirm_session": "wrong-session"}, {"approval_note": ""}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.apply(plan, **override)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(plan_path.read_bytes(), plan_before)

    def test_recent_working_file_prevents_stale_classification(self):
        record, _ = self.stale_session()
        (self.root / "README.md").write_text("另一个进程仍在工作", encoding="utf-8")
        result, data = self.command(self.root, "takeover-plan", record["id"], "--agent", "claude", "--reason", "测试")
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("改动文件", data["error"])

    def test_reply_blocks_apply_and_cancel_preserves_the_reply(self):
        record, path = self.stale_session()
        data, _, plan = self.plan(record)
        declaration = self.root / data["declaration"]
        with declaration.open("a", encoding="utf-8") as stream:
            stream.write("\n原执行者回复：还在工作，不同意接管。\n")
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        result, cancelled = self.command(self.root, "takeover-cancel", plan["id"], "--agent", "claude", "--reason", "原执行者已回复")
        self.assertEqual(result.returncode, 0, cancelled)
        self.assertIn("不同意接管", declaration.read_text(encoding="utf-8"))
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.apply(plan)

    def test_owner_heartbeat_invalidates_plan(self):
        record, path = self.stale_session()
        _, _, plan = self.plan(record)
        self.command(self.root, "heartbeat", record["id"], "--agent", "codex", "--note", "原会话回来了")
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)

    def test_working_content_change_is_detected_even_if_git_status_and_mtime_match(self):
        record, path = self.stale_session(dirty=True)
        _, _, plan = self.plan(record)
        target = self.root / "notes with space.txt"
        old_stat = target.stat()
        target.write_text("同样未跟踪，但内容改变", encoding="utf-8")
        os.utime(target, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)

    def test_index_changes_are_detected_when_working_content_stays_the_same(self):
        record, path = self.stale_session(dirty=True)
        target = self.root / "README.md"
        run("git", "add", "README.md", cwd=self.root)
        target.write_text("工作内容", encoding="utf-8")
        old = time.time() - 1800
        os.utime(target, (old, old))
        _, _, plan = self.plan(record)
        before_stat = target.stat()
        target.write_text("不同的暂存内容", encoding="utf-8")
        self.assertEqual(run("git", "add", "README.md", cwd=self.root).returncode, 0)
        target.write_text("工作内容", encoding="utf-8")
        os.utime(target, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)

    def test_policy_change_invalidates_plan(self):
        record, path = self.stale_session()
        _, _, plan = self.plan(record)
        self.configure("--workflow", "github-flow")
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)

    def test_branch_change_invalidates_plan(self):
        record, path = self.stale_session()
        _, _, plan = self.plan(record)
        self.assertEqual(run("git", "switch", "-c", "feature/moved", cwd=self.root).returncode, 0)
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)

    def test_new_commit_invalidates_plan_without_rewriting_history(self):
        record, path = self.stale_session()
        _, _, plan = self.plan(record)
        self.assertEqual(run("git", "commit", "--allow-empty", "-m", "test: new owner activity", cwd=self.root).returncode, 0)
        head = run("git", "rev-parse", "HEAD", cwd=self.root).stdout
        before = path.read_bytes()
        with self.assertRaises(sessions.ConflictError):
            self.apply(plan)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(run("git", "rev-parse", "HEAD", cwd=self.root).stdout, head)

    def test_plan_schema_and_declaration_path_fail_cleanly_before_writes(self):
        record, path = self.stale_session()
        data, plan_path, plan = self.plan(record)
        before = path.read_bytes()
        readme = (self.root / "README.md").read_bytes()
        declaration = (self.root / data["declaration"]).read_bytes()
        for invalid in ([], {}, dict(plan, snapshot=[]), dict(plan, created_at=float("nan")),
                        dict(plan, created_at=10 ** 1000), dict(plan, schema_version=True), dict(plan, declaration="README.md")):
            with self.subTest(invalid=type(invalid).__name__):
                plan_path.write_text(json.dumps(invalid), encoding="utf-8")
                result, rejected = self.command(self.root, "takeover-cancel", plan["id"], "--agent", "claude", "--reason", "测试")
                self.assertEqual(result.returncode, 2, rejected)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual((self.root / "README.md").read_bytes(), readme)
                self.assertEqual((self.root / data["declaration"]).read_bytes(), declaration)

    def test_cancel_cannot_modify_another_declaration(self):
        record, path = self.stale_session()
        data, _, plan = self.plan(record)
        declaration = self.root / data["declaration"]
        declaration.write_text("# 其它问题\n- from: human\n- to: any\n- status: open\n", encoding="utf-8")
        before = declaration.read_bytes(), path.read_bytes()
        result, rejected = self.command(self.root, "takeover-cancel", plan["id"], "--agent", "claude", "--reason", "测试")
        self.assertEqual(result.returncode, 2, rejected)
        self.assertEqual((declaration.read_bytes(), path.read_bytes()), before)

    def test_duplicate_sessions_and_plans_are_not_automatically_taken_over(self):
        record, path = self.stale_session()
        _, _, plan = self.plan(record)
        result, rejected = self.command(self.root, "takeover-plan", record["id"], "--agent", "claude", "--reason", "重复计划")
        self.assertEqual(result.returncode, 1, rejected)
        self.assertIn(plan["id"], rejected["error"])
        linked = self.worktree()
        (linked / ".multi-agent-collab/sessions" / path.name).write_bytes(path.read_bytes())
        result, rejected = self.command(self.root, "takeover-inspect", record["id"])
        self.assertEqual(result.returncode, 2, rejected)
        self.assertIn("相同 ID", rejected["error"])

    def test_targeted_human_instruction_blocks_plan_without_fake_completion(self):
        record, _ = self.stale_session()
        inbox = self.root / ".multi-agent-collab/inbox/Q-targeted.md"
        inbox.write_text(f"# 暂停\n- from: human\n- to: {record['id']}\n- status: open\n\n先等我确认。\n", encoding="utf-8")
        result, rejected = self.command(self.root, "takeover-plan", record["id"], "--agent", "claude", "--reason", "测试")
        self.assertEqual(result.returncode, 1, rejected)
        self.assertIn("人类指令", rejected["error"])
        self.assertIn("- status: open", inbox.read_text(encoding="utf-8"))

    def test_invalid_handoff_leaves_plan_pending_and_session_intact(self):
        record, path = self.stale_session()
        path.write_text(sessions.put_fields(path.read_text(encoding="utf-8"), {"handoff_inbox": "README.md"}), encoding="utf-8")
        self.make_stale(path)
        _, plan_path, plan = self.plan(record)
        before = path.read_bytes(), plan_path.read_bytes()
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual((path.read_bytes(), plan_path.read_bytes()), before)

    def test_interrupted_apply_keeps_an_audit_marker_and_refuses_blind_retry(self):
        record, path = self.stale_session()
        _, plan_path, plan = self.plan(record)
        before = path.read_bytes()
        real_write = sessions.board._write_lf_atomic
        def fail_session_write(target, content):
            if target == path:
                raise OSError("模拟写会话失败")
            return real_write(target, content)
        with mock.patch.object(sessions.board, "_write_lf_atomic", side_effect=fail_session_write):
            with self.assertRaises(OSError):
                self.apply(plan)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(json.loads(plan_path.read_text(encoding="utf-8"))["status"], "applying")
        with self.assertRaisesRegex(ValueError, "执行中断"):
            self.apply(plan)

    def test_large_or_locked_git_workspace_requires_manual_review(self):
        record, _ = self.stale_session()
        target = self.root / "large.bin"
        with target.open("wb") as stream:
            stream.truncate(sessions.TAKEOVER_LIMIT + 1)
        result, rejected = self.command(self.root, "takeover-inspect", record["id"])
        self.assertEqual(result.returncode, 2, rejected)
        self.assertIn("8 MiB", rejected["error"])
        target.unlink()
        lock = self.root / ".git/index.lock"
        lock.write_text("测试锁", encoding="utf-8")
        result, rejected = self.command(self.root, "takeover-inspect", record["id"])
        self.assertEqual(result.returncode, 1, rejected)
        self.assertTrue(lock.exists(), "不能自动删除其它 Git 进程的锁")


if __name__ == "__main__":
    unittest.main(verbosity=2)
