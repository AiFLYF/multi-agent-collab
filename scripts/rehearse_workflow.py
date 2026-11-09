#!/usr/bin/env python3
"""在隔离目录真实演练安装、协作、网页指令、恢复、业务测试、升级与卸载。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import install as kit  # noqa: E402


class Rehearsal:
    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="mac-rehearsal-")).resolve()
        self.steps = []
        self.server = None
        self.server_log = None

    def run(self, name, *args, cwd=None, expected=0):
        started = time.monotonic()
        command = list(map(str, args))
        if command and command[0] == "git":
            command[0] = kit.prefer_native_git(shutil.which("git")) or command[0]
        result = subprocess.run(command, cwd=str(cwd or self.root), capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=180)
        self.steps.append({"name": name, "command": subprocess.list2cmdline(list(map(str, args))),
                           "expected": expected, "rc": result.returncode,
                           "seconds": round(time.monotonic() - started, 2),
                           "output": (result.stdout + result.stderr).strip()[-1800:]})
        print(f"{'PASS' if result.returncode == expected else 'FAIL'} {name}", flush=True)
        if result.returncode != expected:
            raise RuntimeError(f"{name}：预期退出码 {expected}，实际 {result.returncode}")
        return result.stdout

    def check(self, name, condition):
        self.steps.append({"name": name, "command": "结果核对", "expected": 0,
                           "rc": 0 if condition else 1, "seconds": 0, "output": ""})
        print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)
        if not condition:
            raise AssertionError(name)

    def session(self, name, project, *args, expected=0):
        return json.loads(self.run(name, sys.executable, project / ".multi-agent-collab/tools/agents_session.py",
                                   "--root", project, *args, "--json", cwd=project, expected=expected))

    def initialize(self, project, business=False):
        project.mkdir(parents=True)
        self.run("初始化演练项目 " + project.name, "git", "init", "-q", cwd=project)
        self.run("设置演练作者", "git", "config", "user.name", "Workflow Rehearsal", cwd=project)
        self.run("设置演练邮箱", "git", "config", "user.email", "rehearsal@example.invalid", cwd=project)
        if business:
            (project / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            (project / "cart.py").write_text("def total(items):\n    return sum(price * quantity for price, quantity in items)\n", encoding="utf-8")
            (project / "test_cart.py").write_text(
                "import unittest\nfrom cart import total\n\nclass CartTest(unittest.TestCase):\n"
                "    def test_empty(self): self.assertEqual(total([]), 0)\n"
                "    def test_items(self): self.assertEqual(total([(120, 2), (50, 1)]), 290)\n", encoding="utf-8")
            (project / "README.md").write_text("# 演练商店\n", encoding="utf-8")
            self.run("执行演练项目原始测试", sys.executable, "-m", "unittest", "-q", cwd=project)
            self.run("暂存演练基线", "git", "add", ".gitignore", "README.md", "cart.py", "test_cart.py", cwd=project)
            self.run("提交演练基线", "git", "commit", "-q", "-m", "test: seed rehearsal", cwd=project)
            self.run("建立演练 develop 基线", "git", "branch", "develop", cwd=project)

    def exercise(self):
        cached = self.root / "home/multi-agent-collab"
        cached.parent.mkdir()
        self.run("真实 clone 一份可复用的套件", "git", "clone", "--local", "--no-hardlinks", "-q", KIT, cached)
        first, second, worker = self.root / "shop", self.root / "other-project", self.root / "docs-worker"
        self.initialize(first, business=True)
        self.initialize(second)
        installer = cached / "install.py"
        # Rehearse the current reviewable source, including uncommitted new templates.
        sources = {"install.py"} | {src for src, _dst, _zone in kit.CORE_FILES + kit.platform_files(kit.ALL_PLATFORMS)}
        for relative in sources:
            destination = cached / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(KIT / relative, destination)
        self.run("从当前验收版本首次安装 Codex", sys.executable, installer, "install", first, "--platforms", "codex")
        self.check("原生安装不生成 Claude 入口", not (first / "CLAUDE.md").exists())
        self.run("同一副本安装第二个项目", sys.executable, installer, "install", second, "--platforms", "gemini,qwen")
        a, b = [json.loads((p / kit.INSTALL_META).read_text(encoding="utf-8")) for p in (first, second)]
        self.check("两个项目复用同一安装源", a["source_dir"] == b["source_dir"] == str(cached))
        agents = first / ".multi-agent-collab/RULES.md"
        text = agents.read_text(encoding="utf-8")
        text = kit.ZONE_RE.sub(lambda m: m[1] + "\n- 运行时：Python 3.8+\n- 测试：python -m unittest -q\n- 项目标记：REHEARSAL\n" + m[4]
                              if m[2] == "project-env" else m[0], text)
        kit.write_lf(agents, text)
        note = first / ".multi-agent-collab/worklog/2000-01-01.md"
        note.write_text("演练前已有的协作记录\n", encoding="utf-8")
        # clone 保留真实提交历史；仅覆盖本次要验证的发布源文件，不拷贝本机记忆或凭据。
        sources = {"install.py"} | {src for src, _dst, _zone in kit.CORE_FILES + kit.platform_files(kit.ALL_PLATFORMS)}
        for relative in sources:
            destination = cached / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(KIT / relative, destination)
        changed_tool = cached / "tools/agents_status.py"
        changed_tool.write_bytes(changed_tool.read_bytes() + b"\n# Same-version rehearsal update.\n")
        before_update = {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob("*") if p.is_file()}
        preview = json.loads(self.run("检查同版本套件内容变化", sys.executable, installer, "update", first,
                                      "--check", "--json", expected=1))
        self.check("同版本变化被内容检查识别", preview["update_available"] and
                   preview["installed_version"] == preview["available_version"])
        self.run("更新预览不写盘", sys.executable, installer, "update", first, "--dry-run", "--json")
        self.check("检查与预览保持原文件字节", before_update ==
                   {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob("*") if p.is_file()})
        updater = first / ".multi-agent-collab/tools/agents_update.py"
        self.run("从已安装入口完整更新核心", sys.executable, updater, "--root", first, "--json")
        self.check("已安装工具等于更新源内容", (first / ".multi-agent-collab/tools/agents_status.py").read_bytes() == changed_tool.read_bytes())
        after_update = {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob("*") if p.is_file()}
        self.run("重复更新已是当前内容", sys.executable, updater, "--root", first, "--json")
        self.check("重复更新不重写字节", after_update ==
                   {p.relative_to(first).as_posix(): p.read_bytes() for p in first.rglob("*") if p.is_file()})
        self.run("重复内容检查返回最新", sys.executable, installer, "update", first, "--check", "--json")
        self.check("升级保留项目定制和已有记录", "REHEARSAL" in agents.read_text(encoding="utf-8") and note.read_text(encoding="utf-8") == "演练前已有的协作记录\n")
        self.run("安装体检", sys.executable, installer, "doctor", first)
        self.run("新对话开工摘要", sys.executable, first / ".multi-agent-collab/tools/agents_status.py", "--brief", cwd=first)
        self.run("创建业务任务分支", "git", "switch", "-c", "feature/cart-price", cwd=first)
        self.run("暂存共享忽略配置", "git", "add", ".gitignore", cwd=first)
        self.run("提交安装产生的共享忽略配置", "git", "commit", "-q", "-m", "chore: ignore private collaboration state", cwd=first)
        owner = self.session("登记 Codex 文件范围", first, "start", "--agent", "codex", "--task", "整理价格计算", "--files", "cart.py")
        context = self.run("已安装工具输出有界上下文", sys.executable,
                           first / ".multi-agent-collab/tools/agents_status.py", "--context", "--agent", "codex",
                           "--session", owner["id"], "--max-chars", "6000", "--json", cwd=first)
        compact = json.loads(context)
        self.check("上下文保留策略、完整占用与任务身份", len(context) <= 6000 and compact["gate_rc"] == 0
                   and compact["policy"]["base_branch"] == "develop"
                   and compact["sessions"][0]["files"] == ["cart.py"])
        self.session("阻止同工作区重复写任务", first, "start", "--agent", "claude", "--task", "重复任务", "--files", "README.md", expected=1)
        self.run("先创建独立分支引用", "git", "branch", "feature/docs", "develop", cwd=first)
        self.run("校验分支引用", "git", "rev-parse", "--verify", "feature/docs", cwd=first)
        self.run("建立独立文档工作区", "git", "worktree", "add", "-q", worker, "feature/docs", cwd=first)
        self.run("向 worktree 安装平台入口", sys.executable, installer, "install", worker, "--platforms", "claude")
        self.run("暂存文档分支忽略配置", "git", "add", ".gitignore", cwd=worker)
        self.run("提交文档分支忽略配置", "git", "commit", "-q", "-m", "chore: ignore private collaboration state", cwd=worker)
        conflict = self.session("阻止跨 worktree 的重复文件", worker, "start", "--agent", "claude", "--task", "冲突任务", "--files", "cart.py", expected=1)
        self.check("冲突原因确为文件范围", "文件范围重叠" in conflict["error"])
        peer = self.session("允许独立文件任务", worker, "start", "--agent", "claude", "--task", "完善项目说明", "--files", "README.md")
        ai_note = first / ".multi-agent-collab/inbox/Q-ai-coordination.md"
        ai_note.write_text(f"# 协作消息\n- from: {peer['id']}\n- to: {owner['id']}\n- status: open\n\n"
                           "## 背景\n文档工作区已准备好，请确认价格接口。\n\n## 回复\n", encoding="utf-8")
        notice = self.session("心跳顺带发现 AI 新消息", first, "heartbeat", owner["id"], "--agent", "codex")
        self.check("通知只给数量和读取入口", notice.get("notifications", {}).get("unread_count") == 1
                   and "文档工作区已准备好" not in json.dumps(notice, ensure_ascii=False))
        reader = first / ".multi-agent-collab/tools/agents_messages.py"
        read = self.run("按需读取并标读 AI 消息", sys.executable, reader, "read", "--agent", "codex",
                        "--session", owner["id"], "--mark-read", "--json", cwd=first)
        self.check("阅读正文完整且不替代任务完成", "文档工作区已准备好" in read
                   and "- status: open" in ai_note.read_text(encoding="utf-8"))
        notice = self.session("已读普通消息不再重复提示", first, "heartbeat", owner["id"], "--agent", "codex")
        self.check("普通阅读回执压住重复通知", "notifications" not in notice)
        ai_note.write_text(ai_note.read_text(encoding="utf-8").replace("- status: open", "- status: answered")
                           + "\n价格接口已确认，可继续完善说明。\n", encoding="utf-8")
        reply = self.session("原提问者收到已答回复提示", worker, "heartbeat", peer["id"], "--agent", "claude")
        self.check("已答回复仍可发现", reply.get("notifications", {}).get("reply_count") == 1)
        self.run("原提问者读取跨工作区回复", sys.executable, worker / ".multi-agent-collab/tools/agents_messages.py",
                 "read", "--agent", "claude", "--session", peer["id"], "--mark-read", "--json", cwd=worker)
        log_path = self.root / "board-server.log"
        self.server_log = log_path.open("w", encoding="utf-8")
        self.server = subprocess.Popen([sys.executable, "-u", str(first / ".multi-agent-collab/tools/agents_status.py"), "--web", "--port", "0", "--no-open"],
                                       cwd=first, stdout=self.server_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        match = None
        while time.monotonic() < deadline:
            match = re.search(r"http://127\.0\.0\.1:(\d+)", log_path.read_text(encoding="utf-8"))
            if match or self.server.poll() is not None:
                break
            time.sleep(0.1)
        if not match:
            raise RuntimeError("演练看板未能启动：" + log_path.read_text(encoding="utf-8")[-500:])
        base = match.group(0)
        def read_board():
            with urllib.request.urlopen(base + "/data", timeout=10) as response:
                return json.load(response)
        self.check("真实网页 API 聚合两个会话", len(read_board()["sessions"]) == 2)
        pause_request = urllib.request.Request(
            base + "/directive", data=json.dumps({"to": owner["id"], "action": "pause", "body": "暂停价格任务，等待协调。"}).encode(),
            headers={"Content-Type": "application/json", "X-Mac-Board-Action": "1", "Origin": base})
        with urllib.request.urlopen(pause_request, timeout=10) as response:
            pause = json.load(response)
        paused_note = first / ".multi-agent-collab/inbox" / pause["file"]
        self.check("网页显式暂停类型真实保存", "- action: pause" in paused_note.read_text(encoding="utf-8"))
        self.session("确认不能解除显式暂停", first, "resume", owner["id"], "--agent", "codex",
                     "--ack-inbox", pause["file"], expected=1)
        self.session("显式暂停先拦截范围变更", first, "heartbeat", owner["id"], "--agent", "codex",
                     "--files", "cart.py", "test_cart.py", expected=1)
        paused_note.write_text(paused_note.read_text(encoding="utf-8").replace("- status: open", "- status: closed")
                              + "\n演练人类解除暂停，允许继续原任务。\n", encoding="utf-8")
        request = urllib.request.Request(base + "/directive", data=json.dumps({"to": "any", "body": "演练指令：完成后记录实际测试结果。"}).encode(),
                                         headers={"Content-Type": "application/json", "X-Mac-Board-Action": "1", "Origin": base})
        with urllib.request.urlopen(request, timeout=10) as response:
            directive = json.load(response)
        self.check("网页指令真实落盘", (first / ".multi-agent-collab/inbox" / directive["file"]).is_file())
        context = self.run("上下文对未答人类指令返回告警", sys.executable,
                           first / ".multi-agent-collab/tools/agents_status.py", "--context", "--agent", "codex",
                           "--session", owner["id"], "--json", cwd=first, expected=1)
        compact = json.loads(context)
        self.check("定向上下文保留人类指令原文与原文件路径", len(context) <= 6000 and compact["inbox"]
                   and compact["inbox"][0]["body"] == "演练指令：完成后记录实际测试结果。"
                   and Path(compact["inbox"][0]["path"]).is_file())
        rejected = self.session("未答人类指令阻止新开工", worker, "start", "--agent", "roo", "--task", "新任务", "--files", "notes/", expected=1)
        self.check("阻止原因为人类 inbox", "人类指令" in rejected["error"])
        question = first / ".multi-agent-collab/inbox" / directive["file"]
        content = question.read_text(encoding="utf-8")
        kit.write_lf(question, content + "\n演练 agent 回执：已阅读并纳入任务，测试完成后补充结果。\n")
        self.check("已确认的执行事项仍保持待处理", bool(read_board()["inbox_open"]))
        self.session("更新原会话心跳", first, "heartbeat", owner["id"], "--agent", "codex", "--note", "已经处理指令")
        handoff = self.session("记录中断与交接", first, "handoff", owner["id"], "--agent", "codex", "--summary", "等待确认后恢复价格计算任务")
        self.session("未确认指令时阻止恢复", first, "resume", owner["id"], "--agent", "codex", expected=1)
        resumed = self.session("确认指令后原会话恢复任务", first, "resume", owner["id"], "--agent", "codex", "--note", "已确认，继续原任务", "--ack-inbox", directive["file"])
        self.check("恢复沿用 ID 并关闭交接留言", resumed["id"] == owner["id"] and resumed["status"] == "active" and
                   "- status: answered" in (first / handoff["inbox"]).read_text(encoding="utf-8"))
        (first / "cart.py").write_text("def total(items):\n    subtotals = [price * quantity for price, quantity in items]\n    return sum(subtotals)\n", encoding="utf-8")
        self.run("包装命令执行真实业务代码测试", sys.executable, first / ".multi-agent-collab/tools/agents_session.py",
                 "--root", first, "run", owner["id"], "--agent", "codex", "--", sys.executable, "-m", "unittest", "-q", cwd=first)
        activity_record = kit.read_text(first / ".multi-agent-collab/sessions" / (owner["id"] + ".md"))
        self.check("业务测试记录活动但不自动结束任务", "- last_command: agents_session.run" in activity_record
                   and "- last_activity:" in activity_record and "- status: active" in activity_record)
        content = question.read_text(encoding="utf-8").replace("- status: open", "- status: answered")
        kit.write_lf(question, content + "\n演练 agent 完成回复：python -m unittest -q，2 项业务测试通过，结果已记录。\n")
        self.check("完成后网页 API 显示已答复", not read_board()["inbox_open"] and bool(read_board()["inbox_replied"]))
        self.run("仅暂存本任务文件", "git", "add", "cart.py", cwd=first)
        self.run("测试通过后提交业务改动", "git", "commit", "-q", "-m", "refactor(cart): clarify subtotals", cwd=first)
        remote = self.root / "rehearsal-remote.git"
        self.run("建立隔离的 bare 远端", "git", "init", "--bare", "-q", remote)
        self.run("配置演练远端", "git", "remote", "add", "origin", remote, cwd=first)
        self.run("向演练远端推送 feature 分支", "git", "push", "-q", "-u", "origin", "feature/cart-price", cwd=first)
        self.session("完成文档会话", worker, "finish", peer["id"], "--agent", "claude", "--summary", "项目说明核对完成", "--tests", "未修改业务代码，已有测试在主工作区执行通过")
        handoff = self.session("再次交接保留审计记录", first, "handoff", owner["id"], "--agent", "codex", "--summary", "任务已实现，等待收尾")
        self.session("完成原任务后进入待机并释放占用", first, "finish", owner["id"], "--agent", "codex", "--summary", "价格计算整理完成并推送本地演练远端", "--tests", "python -m unittest -q，2 tests passed", "--standby")
        self.check("收尾关闭关联交接", "- status: answered" in (first / handoff["inbox"]).read_text(encoding="utf-8"))
        waiting = next(s for s in read_board()["sessions"] if s["id"] == owner["id"])
        self.check("待机保留身份且不占文件", waiting["standby"] and not waiting["occupies"] and waiting["files"] == [])
        next_owner = self.session("待机后其它会话可登记原文件", first, "start", "--agent", "roo", "--task", "验证文件释放", "--files", "cart.py")
        refused = self.session("待机不能抢占正在执行的任务", first, "activate", owner["id"], "--agent", "codex",
                               "--task", "再次核对业务测试", "--files", "cart.py", "test_cart.py", expected=1)
        self.check("待机激活拒绝来自实际占用", "同一工作区" in refused["error"] or "文件范围重叠" in refused["error"])
        self.session("结束文件释放核对任务", first, "finish", next_owner["id"], "--agent", "roo",
                     "--summary", "实际登记与占用拒绝已验证，未修改业务代码", "--tests", "命令退出码与待机范围检查通过")
        self.session("待机收到新任务重新登记范围", first, "activate", owner["id"], "--agent", "codex",
                     "--task", "再次核对业务测试", "--files", "cart.py", "test_cart.py")
        self.run("新任务的包装命令仍执行真实测试", sys.executable, first / ".multi-agent-collab/tools/agents_session.py",
                 "--root", first, "run", owner["id"], "--agent", "codex", "--", sys.executable, "-m", "unittest", "-q", cwd=first)
        self.session("新任务完成后明确结束会话", first, "finish", owner["id"], "--agent", "codex",
                     "--summary", "待机、占用冲突与重新激活闭环完成", "--tests", "python -m unittest -q，2 tests passed")
        data = read_board()
        self.check("看板收尾无悬挂会话或问题", all(s["closed"] for s in data["sessions"]) and not data["inbox_open"] and not data["warnings"])
        self.run("追加平台并同步", sys.executable, installer, "sync", first, "--platforms", "qwen,gemini,cline")
        self.check("追加未覆盖原项目约定", "REHEARSAL" in agents.read_text(encoding="utf-8"))
        self.run("完整流程后的体检", sys.executable, installer, "doctor", first)
        status = self.run("核对 Git 工作区", "git", "status", "--porcelain", cwd=first)
        self.check("全部协作产物均被忽略", not status.strip())
        original = note.read_bytes()
        self.run("卸载预览", sys.executable, installer, "uninstall", first, "--purge-files", "--dry-run")
        self.check("卸载预览不丢协作记录", note.read_bytes() == original)

        # 第二项目用单主干流程，证明不依赖 develop；仅操作本次创建的隔离仓库。
        self.run("为第二项目选择 GitHub Flow", sys.executable, installer, "install", second, "--workflow", "github-flow")
        self.run("建立第二项目 main 基线", "git", "switch", "-c", "main", cwd=second)
        (second / "README.md").write_text("# 单主干演练项目\n", encoding="utf-8")
        self.run("暂存第二项目演练基线", "git", "add", "README.md", ".gitignore", cwd=second)
        self.run("提交第二项目演练基线", "git", "commit", "-q", "-m", "test: seed single-main project", cwd=second)
        self.run("创建策略允许的任务分支", "git", "switch", "-c", "codex/policy-rehearsal", cwd=second)
        policy = self.session("读取实际生效的项目策略", second, "check", "--files", "README.md")
        self.check("单主干策略允许 codex 前缀", policy["policy"]["base_branch"] == "main" and policy["task_branch_allowed"])
        cli = json.loads(self.run("检查已选客户端就绪信息", sys.executable, installer, "clients", second, "--json"))
        self.check("客户端探测不冒充新对话验证", len(cli["clients"]) == 2 and all(not row["session_verified"] for row in cli["clients"]))
        stale = self.session("登记接管演练会话", second, "start", "--agent", "qwen", "--task", "接管流程演练", "--files", "README.md")
        session_path = Path(stale["file"])
        old_text = re.sub(r"(?m)^- heartbeat:.*$", "- heartbeat: 2000-01-01 00:00", session_path.read_text(encoding="utf-8"))
        kit.write_lf(session_path, old_text)
        old_time = time.time() - 1800
        os.utime(session_path, (old_time, old_time))
        inspected = self.session("只读核对失联快照", second, "takeover-inspect", stale["id"])
        self.check("演练快照可供人工核对", not inspected["blockers"])
        planned = self.session("发布演练接管计划", second, "takeover-plan", stale["id"], "--agent", "gemini", "--reason", "隔离夹具中的失联会话")
        rejected = self.session("真实命令拒绝跳过等待", second, "takeover-apply", planned["plan"], "--agent", "gemini",
                                "--confirm-session", stale["id"], "--approval-note", "仅验证等待门禁的隔离测试", expected=1)
        self.check("阻止原因确为未满五分钟", "5 分钟" in rejected["error"])
        self.session("取消接管并保留记录", second, "takeover-cancel", planned["plan"], "--agent", "gemini", "--reason", "演练结束，原会话继续")
        self.check("取消未修改原会话", session_path.read_text(encoding="utf-8") == old_text)
        self.session("原会话完成演练收尾", second, "finish", stale["id"], "--agent", "qwen", "--summary", "接管检查和取消已验证",
                     "--tests", "真实 CLI 检查、等待拒绝、取消保留均通过；未执行强制接管")
        self.run("第二项目协作记录闭环", sys.executable, second / ".multi-agent-collab/tools/agents_status.py", "--brief", cwd=second)

    def cleanup(self):
        if self.server is not None:
            self.server.terminate()
            self.server.wait(timeout=10)
        if self.server_log is not None:
            self.server_log.close()
        if self.root.parent != Path(tempfile.gettempdir()).resolve() or not self.root.name.startswith("mac-rehearsal-"):
            raise RuntimeError("拒绝清理演练目录之外的路径")
        def writable(function, path, _error):
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
            function(path)
        shutil.rmtree(self.root, onerror=writable)

    def report(self, path, error):
        text = ["# 协作流程实际演练报告", "", f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
                f"- 结果：{'未通过' if error else '通过'}", f"- 检查数：{len(self.steps)}", f"- Python：{sys.version.split()[0]}",
                "- 范围：真实本地 clone、Git/worktree、安装器、项目策略、会话/接管工具、HTTP 看板、业务测试和隔离 bare 远端。",
                "- 接管：真实 CLI 验证 inspect/plan/等待拒绝/cancel；成功 apply 的时间边界由受控时钟回归验证。",
                "- 边界：不创建真实 GitHub PR，不调用模型，不代替各平台的新会话加载验收。", ""]
        if error:
            text += ["## 断点", "", str(error), ""]
        for i, step in enumerate(self.steps, 1):
            text += [f"## {i}. {step['name']}", "", f"退出码 {step['rc']}（预期 {step['expected']}），耗时 {step['seconds']} 秒。", "", "```text", step["command"], "```", ""]
            if step["output"]:
                text += ["```text", step["output"], "```", ""]
        output = "\n".join(text).replace(str(self.root), "<演练目录>").replace(str(KIT), "<套件工作区>")
        path.parent.mkdir(parents=True, exist_ok=True)
        kit.write_lf(path, output)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=KIT / ".agents/rehearsal-latest.md")
    args = parser.parse_args()
    rehearsal = Rehearsal()
    error = None
    try:
        rehearsal.exercise()
    except Exception as exc:
        error = exc
        print(f"FAIL {exc}", flush=True)
    finally:
        try:
            rehearsal.cleanup()
        except Exception as exc:
            error = error or exc
        rehearsal.report(args.report.resolve(), error)
    print(f"报告：{args.report.resolve()}")
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
