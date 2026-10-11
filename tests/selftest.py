#!/usr/bin/env python3
"""multi-agent-collab 自测（纯标准库，零依赖）

在系统临时目录里：
  1. 测看板解析函数与 --selfcheck
  2. 造真实 git 仓库，端到端跑 install / doctor / mirror
     （安装、忽略生效、幂等、备份、定制区迁移、dry-run、linked worktree、编码兼容）

运行：
    python tests/selftest.py
    KEEP_TMP=1 python tests/selftest.py      # 保留临时目录便于人工排查
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # 某些沙箱禁止写 __pycache__


def rmtree_readonly(path: Path) -> None:
    """删除含 .git 的临时目录（Windows 专项加固）：

    - git 对象/锁带只读位，普通 rmtree 会失败；
    - linked worktree 的 admin 元数据要先让 git 自己摘除；
    - git 子进程刚退出时句柄会延迟释放（杀软扫描同理），需要多轮重试。
    """
    if not path.exists():
        return

    # 1) 找出目录下所有真正的 git 仓库（.git 是目录；linked worktree 的 .git 是文件不算），
    #    逐个摘掉它名下的 linked worktree 并 prune
    repos = [path] if (path / ".git").is_dir() else []
    repos += [g.parent for g in path.rglob(".git") if g.is_dir()]
    for repo in dict.fromkeys(repos):
        r = run("git", "worktree", "list", "--porcelain", cwd=repo)
        wt_dirs = [ln[9:].strip() for ln in r.stdout.splitlines()
                   if ln.startswith("worktree ")][1:]   # 第一条是主工作区
        for d in wt_dirs:
            run("git", "worktree", "remove", "--force", d, cwd=repo)
        run("git", "worktree", "prune", cwd=repo)

    # 2) 多轮 chmod + 删除：句柄延迟释放时单轮重试不够（实测需要等到 1 秒以上）
    def _on_error(func, fpath, exc_info):
        try:
            os.chmod(fpath, stat.S_IWRITE | stat.S_IREAD)
            func(fpath)
        except OSError:
            pass

    for delay in (0, 0.3, 0.8, 1.5):
        if delay:
            time.sleep(delay)
        shutil.rmtree(path, onerror=_on_error)
        if not path.exists():
            return

    # 3) 最终兜底：深度优先逐个强删；仍然失败就明确告警，绝不静默残留
    if path.exists():
        for p in sorted(path.rglob("*"), reverse=True):
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                (p.rmdir if p.is_dir() and not p.is_symlink() else p.unlink)()
            except OSError:
                pass
        try:
            path.rmdir()
        except OSError:
            print(f"[WARN] 临时目录未能完全删除（可手工删）：{path}", file=sys.stderr)

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "tools"))
sys.path.insert(0, str(KIT))

import agents_status as board  # noqa: E402
import install as inst  # noqa: E402

PY = sys.executable

CORE_TARGETS = [dst for _src, dst, _zone in inst.CORE_FILES]
PLATFORM_TARGETS = [dst for _src, dst in inst.PLATFORM_FILES.values()] + [inst.WORKBUDDY_TARGET]


def run(*args: str, cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


def git_available() -> bool:
    try:
        r = run("git", "--version", cwd=Path(tempfile.gettempdir()), timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def install(proj: Path, *extra: str) -> subprocess.CompletedProcess:
    return run(PY, str(KIT / "install.py"), "install", str(proj), *extra, cwd=KIT)


class TestBoardUnits(unittest.TestCase):
    def test_parse_helpers(self) -> None:
        self.assertEqual(board.status_word("active（干活中）"), "active")
        self.assertIsNotNone(board.parse_time("2025-01-15 10:00:00"))
        self.assertIsNone(board.parse_time("乱写"))
        self.assertNotIn("blocked", board.CLOSED_WORDS)
        self.assertIn("done", board.CLOSED_WORDS)
        self.assertNotIn(board.status_word("未完成（继续干）"), board.CLOSED_WORDS)
        with self.subTest("首值优先，正文列表项不覆盖头部"):
            d = tempfile.mkdtemp(prefix="mac-kv-")
            p = Path(d) / "q.md"
            p.write_text("- from: a\n- to: any\n\n正文\n- from: b\n", encoding="utf-8")
            self.assertEqual(board.parse_kv(p).get("from"), "a")
            shutil.rmtree(d, ignore_errors=True)

    def test_selfcheck_passes(self) -> None:
        r = run(PY, str(KIT / "tools" / "agents_status.py"), "--selfcheck", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"selfcheck: 24/24 通过", r.stdout)


class TestSingleSource(unittest.TestCase):
    """「本机私有清单」必须在 5 处保持一致：install.py 是唯一事实源。"""

    FILES = ["AGENTS.md", "README.md",
             "platforms/trae-rule.md", "platforms/workbuddy-header.md"]

    def test_exclude_entries_documented_everywhere(self) -> None:
        for entry in inst.EXCLUDE_ENTRIES:
            token = entry.lstrip("/")          # 文档里写 .agents/ 不带前导斜杠
            for rel in self.FILES:
                text = (KIT / rel).read_text(encoding="utf-8")
                self.assertIn(token, text, f"{entry} 没有出现在 {rel}（清单漂移）")

    def test_no_extra_private_dirs_claimed(self) -> None:
        """文档声称私有的 .xxx 目录，都必须在 EXCLUDE_ENTRIES 里（防再次漏掉 .workbuddy）。"""
        claimed = set()
        for rel in self.FILES:
            text = (KIT / rel).read_text(encoding="utf-8")
            claimed.update(re.findall(r"[/` ](\.(?:agents?|claude|trae|workbuddy[a-z-]*))[/`\s、，]",
                                      text))
        ignored = {e.strip("/") for e in inst.EXCLUDE_ENTRIES if e.startswith("/.")}
        ghost = claimed - ignored
        self.assertFalse(ghost, f"文档声称私有但未忽略的目录：{ghost}")


@unittest.skipUnless(git_available(), "环境里没有 git，跳过安装器端到端测试")
class TestInstallerE2E(unittest.TestCase):
    root: Path
    proj: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="mac-selftest-"))
        cls.proj = cls.root / "demo"
        cls.proj.mkdir()
        r = run("git", "init", "-q", cwd=cls.proj)
        if r.returncode != 0:
            raise unittest.SkipTest(f"git init 失败：{r.stderr}")
        run("git", "config", "user.email", "a@b.c", cwd=cls.proj)
        run("git", "config", "user.name", "demo", cwd=cls.proj)
        (cls.proj / "README.md").write_text("# demo\n", encoding="utf-8")
        (cls.proj / "src").mkdir()
        (cls.proj / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        run("git", "add", ".", cwd=cls.proj)
        run("git", "commit", "-q", "-m", "chore: init", cwd=cls.proj)
        run("git", "branch", "develop", cwd=cls.proj)

    @classmethod
    def tearDownClass(cls) -> None:
        import os
        if os.environ.get("KEEP_TMP"):
            print(f"\n[KEEP_TMP] 临时目录保留在 {cls.root}")
        else:
            rmtree_readonly(cls.root)

    def test_01_install(self) -> None:
        r = install(self.proj, "--project-name", "Demo 项目")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in CORE_TARGETS + PLATFORM_TARGETS:
            self.assertTrue((self.proj / rel).is_file(), f"缺少 {rel}")

        trae = (self.proj / ".trae/rules/git-flow-agent.md").read_text(encoding="utf-8")
        self.assertIn("alwaysApply: true", trae)
        self.assertIn("Demo 项目", trae)

        buddy = (self.proj / inst.WORKBUDDY_TARGET).read_text(encoding="utf-8")
        self.assertIn("WorkBuddy 入口", buddy)
        self.assertIn("## 1. 分支铁律", buddy)

        for entry in inst.EXCLUDE_ENTRIES:
            probe = entry.lstrip("/").rstrip("/")
            if entry.endswith("/"):
                probe += "/__mac_probe__"
            chk = run("git", "check-ignore", "-q", probe, cwd=self.proj)
            self.assertEqual(chk.returncode, 0, f"{entry} 没有被忽略")

        br = run(PY, "tools/agents_status.py", "--json", cwd=self.proj)
        self.assertIn(br.returncode, (0, 1))
        self.assertEqual(json.loads(br.stdout)["sessions"], [])

    def test_02_doctor_clean(self) -> None:
        r = run(PY, str(KIT / "install.py"), "doctor", str(self.proj), cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("[FAIL]", r.stdout)
        self.assertIn("0 个 FAIL", r.stdout)
        self.assertIn("看板可运行·人类模式", r.stdout)

    def test_03_idempotent_no_backups(self) -> None:
        r = install(self.proj, "--project-name", "Demo 项目")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 内容相同：不新建备份；backups 目录此时应该不存在
        self.assertFalse((self.proj / ".agents/backups").exists(),
                         "二次相同安装不该产生任何备份")
        self.assertIn("新建/覆盖 0 个", r.stdout)

    def test_04_project_name_remembered(self) -> None:
        """不带 --project-name 重跑：读 install.json，trae 规则保持原名、不产生覆盖。"""
        r = install(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        trae = (self.proj / ".trae/rules/git-flow-agent.md").read_text(encoding="utf-8")
        self.assertIn("Demo 项目", trae)
        self.assertIn("新建/覆盖 0 个", r.stdout)

    def test_05_zone_preserved_on_upgrade(self) -> None:
        agents = self.proj / "AGENTS.md"
        claude = self.proj / "CLAUDE.md"
        a_text = agents.read_text(encoding="utf-8")
        a_text = a_text.replace("- 语言/运行时：",
                                "- 语言/运行时：Rust 1.80（我的定制门禁）\n- 我的门禁：cargo test --workspace\n- 占位：")
        a_text = a_text.replace("你是一个严格遵守 Git Flow 工作流的开发助手。",
                                "你是一个严格遵守 Git Flow 工作流的开发助手。<!-- 私自改的 -->")
        agents.write_text(a_text, encoding="utf-8")
        c_text = claude.read_text(encoding="utf-8").replace(
            "- 运行时：", "- 运行时：Rust 1.80（定制）\n- 占位：")
        claude.write_text(c_text, encoding="utf-8")

        r = install(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        new_a = agents.read_text(encoding="utf-8")
        new_c = claude.read_text(encoding="utf-8")
        self.assertIn("cargo test --workspace", new_a, "AGENTS 定制区内容应被保留")
        self.assertIn("Rust 1.80（定制）", new_c, "CLAUDE 定制区内容应被保留")
        self.assertNotIn("私自改的", new_a, "非定制区应回到 kit 模板（旧版有备份）")
        backups = list((self.proj / ".agents/backups").rglob("AGENTS.md*.bak"))
        self.assertTrue(backups, "覆盖时应在 .agents/backups 留下备份")

        # M8：备份绝不出现在 git status，根目录也不允许散落 .bak
        status = run("git", "status", "--porcelain", cwd=self.proj).stdout
        self.assertNotIn(".bak", status, f"备份污染 git status：\n{status}")
        self.assertFalse(list(self.proj.glob("*.bak-*")))

    def test_06_troubleshooting_d_zone_migrated(self) -> None:
        troubles = self.proj / ".agents/TROUBLESHOOTING.md"
        # 用户在带标记的新版里追加 D 条目 → 升级原样保留
        text = troubles.read_text(encoding="utf-8").replace(
            "<!-- <<< mac:zone:project-troubles -->",
            '## D7 我们的坑：支付沙箱每天凌晨重置\n\n- 现象：x\n- 修复：y\n\n'
            "<!-- <<< mac:zone:project-troubles -->")
        troubles.write_text(text, encoding="utf-8")
        r = install(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        merged = troubles.read_text(encoding="utf-8")
        self.assertIn("支付沙箱每天凌晨重置", merged)
        self.assertIn("## A1", merged)
        self.assertIn("## C9", merged)
        # 再次升级仍然还在（zone 持久）
        install(self.proj)
        self.assertIn("支付沙箱每天凌晨重置", troubles.read_text(encoding="utf-8"))

    def test_07_legacy_troubleshooting_without_markers(self) -> None:
        """无 mac:zone 标记的旧版手册：用户 D 条目自动迁移，A/B/C 以新版为准。"""
        legacy = self.root / "legacy-tb"
        legacy.mkdir(exist_ok=True)
        run("git", "init", "-q", cwd=legacy)
        # 先正常装一遍
        r = install(legacy, "--platforms", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tb = legacy / ".agents/TROUBLESHOOTING.md"
        tb.write_text(
            "# 老手册\n\n## T1 老掉牙的通用条目\n\n会被新版取代\n\n"
            "## D3 老项目专属：内网 Maven 镜像每天 3 点抽风\n\n- 修复：换镜像\n\n"
            "## D4 老项目专属：发版要关特性开关\n\n- 修复：检查开关\n",
            encoding="utf-8")
        r = install(legacy, "--platforms", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        new = tb.read_text(encoding="utf-8")
        self.assertIn("内网 Maven 镜像每天 3 点抽风", new, "D3 应迁入")
        self.assertIn("发版要关特性开关", new, "D4 应迁入")
        self.assertIn("## B1", new, "通用部分应换成新版编号")
        self.assertNotIn("老掉牙的通用条目", new, "旧通用条目不该残留")
        self.assertIn("迁移了 2 条", r.stdout)

    def test_08_gbk_files_and_unmarked_agents(self) -> None:
        """非 UTF-8 的旧 AGENTS.md：不崩、字节备份、给出无法迁移的警告。"""
        gbk = self.root / "legacy-gbk"
        gbk.mkdir(exist_ok=True)
        run("git", "init", "-q", cwd=gbk)
        (gbk / "AGENTS.md").write_bytes(
            "# 我们团队自己的规矩\r\n强制 code review\r\n".encode("gbk"))
        r = install(gbk, "--platforms", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr or "")
        self.assertIn("无法自动迁移", r.stdout)
        baks = list((gbk / ".agents/backups").rglob("AGENTS.md*.bak"))
        self.assertEqual(len(baks), 1)
        # 字节备份保留了 GBK 原文
        self.assertIn("强制 code review".encode("gbk"), baks[0].read_bytes())

    def test_09_board_subdir_and_sessions_semantics(self) -> None:
        sdir = self.proj / ".agents/sessions"
        idir = self.proj / ".agents/inbox"
        hb = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
        (sdir / "trae-251009-0900-aaaa.md").write_text(
            f"# S\n- agent: trae\n- branch: feature/order\n- status: active\n"
            f"- heartbeat: {hb}\n- task: 订单\n", encoding="utf-8")
        # blocked 且心跳老：应当被算作失活（不是已结束）
        (sdir / "buddy-251009-0800-bbbb.md").write_text(
            "# S\n- agent: workbuddy\n- branch: feature/pay\n- status: blocked\n"
            "- heartbeat: 2025-01-01 00:00\n", encoding="utf-8")
        # 坏心跳：人类模式不许崩
        (sdir / "claude-251009-0700-eeee.md").write_text(
            "# S\n- agent: claude\n- branch: feature/x\n- status: active\n"
            "- heartbeat: 刚写了中文\n", encoding="utf-8")
        # 未来心跳：按无有效心跳处理，不许出现负年龄
        (sdir / "human-251009-0600-ffff.md").write_text(
            "# S\n- agent: human\n- branch: feature/y\n- status: active\n"
            "- heartbeat: 2099-01-01 00:00\n", encoding="utf-8")
        (idir / "Q-2025-10-09-01.md").write_text(
            "# Q\n- from: x\n- to: any\n- status: open\n"
            f"- created: {hb}\n", encoding="utf-8")

        # 子目录运行：自动探测仓库根（JSON 输出在 stdout，不能混入提示行）
        sub = self.proj / "src"
        r = run(PY, str(self.proj / "tools/agents_status.py"), "--json", cwd=sub)
        self.assertEqual(r.returncode, 1, r.stderr)
        data = json.loads(r.stdout)  # stdout 必须是纯 JSON
        ids = {s["id"]: s for s in data["sessions"]}
        self.assertEqual(len(ids), 4)
        self.assertTrue(ids["buddy-251009-0800-bbbb"]["stale"], "blocked 老心跳应失活")
        self.assertFalse(ids["buddy-251009-0800-bbbb"]["closed"])
        self.assertTrue(ids["claude-251009-0700-eeee"]["stale"])
        fut = ids["human-251009-0600-ffff"]
        self.assertTrue(fut["stale"] and fut["age_min"] is None)
        self.assertEqual(data["inbox_open"][0]["date"], hb, "应兼容 created 字段")

        # 人类模式也不崩
        r2 = run(PY, str(self.proj / "tools/agents_status.py"), cwd=sub)
        self.assertIn(r2.returncode, (0, 1))
        self.assertNotIn("Traceback", r2.stdout + r2.stderr)

    def test_10_linked_worktree(self) -> None:
        wt = self.root / "demo-wt"
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        r = run("git", "worktree", "add", "-q", str(wt), "-b", "feature/coupon",
                "develop", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)

        # M1：linked worktree 里 doctor 不误判"不是 git 仓库"；缺文件只 WARN 不 FAIL
        d = run(PY, str(KIT / "install.py"), "doctor", str(wt), cwd=KIT)
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertNotIn("不是 git 仓库", d.stdout)
        self.assertIn("linked worktree", d.stdout)
        chk = run("git", "check-ignore", "-q", "AGENTS.md", cwd=wt)
        self.assertEqual(chk.returncode, 0, "linked worktree 里协作文件也应被忽略")

        # 在该 worktree 补齐安装：项目名应从主工作区 install.json 继承（不叫 demo-wt）
        i = install(wt)
        self.assertEqual(i.returncode, 0, i.stdout + i.stderr)
        self.assertTrue((wt / "tools/agents_status.py").is_file())
        trae_wt = (wt / ".trae/rules/git-flow-agent.md").read_text(encoding="utf-8")
        self.assertIn("Demo 项目", trae_wt)
        d2 = run(PY, str(KIT / "install.py"), "doctor", str(wt), cwd=KIT)
        self.assertEqual(d2.returncode, 0, d2.stdout + d2.stderr)
        self.assertNotIn("[FAIL]", d2.stdout)

        # 模拟另一个 agent 在 worktree 改文件（git status 口径）
        time = __import__("time")
        time.sleep(1.0)
        (wt / "src").mkdir(exist_ok=True)
        (wt / "src/coupon.py").write_text("# coupon\n", encoding="utf-8")
        br = run(PY, "tools/agents_status.py", "--json", cwd=self.proj)
        data = json.loads(br.stdout)
        # M2：主工作区自己不许自告警；linked wt 改动要告警
        warned_paths = [w.split("（分支", 1)[0].replace("未注册 worktree：", "").strip()
                        for w in data["warnings"]]
        warned_paths = [p.replace("\\", "/").rstrip("/") for p in warned_paths]
        self.assertNotIn(str(self.proj).replace("\\", "/"), warned_paths,
                         f"主工作区被误报：{warned_paths}")
        self.assertIn(str(wt).replace("\\", "/"), warned_paths)
        self.assertIn("feature/coupon", "\n".join(data["warnings"]))

        # 在 worktree 注册会话（拷整个 .agents 过去是 AGENTS.md 规定做法）
        shutil.copytree(self.proj / ".agents", wt / ".agents", dirs_exist_ok=True)
        (wt / ".agents/sessions/claude-251009-1000-dddd.md").write_text(
            "# S\n- agent: claude\n- branch: feature/coupon\n- status: active\n"
            f"- heartbeat: {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}\n",
            encoding="utf-8")
        br2 = run(PY, "tools/agents_status.py", "--json", cwd=self.proj)
        data2 = json.loads(br2.stdout)
        ids = {s["id"] for s in data2["sessions"]}
        self.assertIn("claude-251009-1000-dddd", ids, "主看板必须看到 worktree 里注册的会话")
        # 已注册 → 未注册告警消失
        self.assertFalse(any("feature/coupon" in w for w in data2["warnings"]))

    def test_11_mirror_regenerated(self) -> None:
        agents = self.proj / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8")
                          + "\n<!-- 镜像测试标记 -->\n", encoding="utf-8")
        r = run(PY, str(KIT / "install.py"), "mirror", str(self.proj), cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        buddy = (self.proj / inst.WORKBUDDY_TARGET).read_text(encoding="utf-8")
        self.assertIn("镜像测试标记", buddy)

    def test_12_dry_run_writes_nothing(self) -> None:
        target = self.root / "dryrun-target"
        r = run(PY, str(KIT / "install.py"), "install", str(target),
                "--dry-run", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(target.exists(), "--dry-run 不该创建目标目录")

    def test_13_version_recorded_and_doctor_shows(self) -> None:
        meta = json.loads((self.proj / inst.INSTALL_META).read_text(encoding="utf-8"))
        self.assertEqual(meta["kit_version"], inst.KIT_VERSION)
        self.assertEqual(meta["project_name"], "Demo 项目")
        d = run(PY, str(KIT / "install.py"), "doctor", str(self.proj), cwd=KIT)
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertIn(f"套件版本 {inst.KIT_VERSION}", d.stdout)

    def test_14_uninstall_safe_and_purge_keeps_user_data(self) -> None:
        t = self.root / "uninst"
        t.mkdir()
        self.assertEqual(run("git", "init", "-q", cwd=t).returncode, 0)
        r = install(t, "--platforms", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 安装到真实 git 仓库：exclude 必须先在位，后面才有"摘除"可验证
        self.assertIn(inst.MARK_BEGIN,
                      (t / ".git/info/exclude").read_text(encoding="utf-8"))

        # 制造三类"用户资产"：定制区、D 组条目、真实会话
        agents = t / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace(
            "- 语言/运行时：", "- 语言/运行时：Elixir 1.17（卸载测试）\n- 占位："),
            encoding="utf-8")
        tb = t / ".agents/TROUBLESHOOTING.md"
        tb.write_text(tb.read_text(encoding="utf-8").replace(
            "<!-- <<< mac:zone:project-troubles -->",
            "## D2 卸载测试专属坑\n\n- 修复：别删我\n\n<!-- <<< mac:zone:project-troubles -->"),
            encoding="utf-8")
        (t / ".agents/sessions/trae-250115-1200-abcd.md").write_text(
            "# S\n- agent: trae\n- branch: feature/x\n- status: done\n"
            "- heartbeat: 2025-01-15 12:00\n", encoding="utf-8")
        install(t, "--platforms", "none")   # 让定制落定

        # 1) 默认卸载：只摘托管块，文件全留，忽略随之失效
        u1 = run(PY, str(KIT / "install.py"), "uninstall", str(t), cwd=KIT)
        self.assertEqual(u1.returncode, 0, u1.stdout + u1.stderr)
        exclude = (t / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertNotIn(inst.MARK_BEGIN, exclude)
        chk = run("git", "check-ignore", "-q", "AGENTS.md", cwd=t)
        self.assertNotEqual(chk.returncode, 0, "托管块摘除后 AGENTS.md 应不再被忽略")
        self.assertTrue((t / "AGENTS.md").is_file())
        self.assertTrue((t / "tools/agents_status.py").is_file())

        # 2) --purge-files：未改过的删掉，用户资产全部保留
        u2 = run(PY, str(KIT / "install.py"), "uninstall", str(t),
                 "--purge-files", cwd=KIT)
        self.assertEqual(u2.returncode, 0, u2.stdout + u2.stderr)
        self.assertFalse((t / "tools/agents_status.py").exists(),
                         "未修改的看板应被删除")
        self.assertFalse((t / inst.INSTALL_META).exists())
        self.assertTrue((t / "AGENTS.md").is_file(), "定制过的 AGENTS.md 必须保留")
        self.assertIn("Elixir 1.17（卸载测试）", agents.read_text(encoding="utf-8"))
        kept_tb = tb.read_text(encoding="utf-8")
        self.assertIn("卸载测试专属坑", kept_tb, "D 组条目必须保留")
        self.assertTrue((t / ".agents/sessions/trae-250115-1200-abcd.md").is_file(),
                        "真实会话文件永不删除")
        self.assertIn("保留（含你的定制/数据）", u2.stdout)

    def test_15_exclude_has_workbuddy_dir(self) -> None:
        self.assertIn("/.workbuddy/", inst.EXCLUDE_ENTRIES)

    def test_16_self_install_rejected(self) -> None:
        """把 kit 自己（含其子目录）当安装目标必须拒绝，防止模板被自迁移污染。"""
        for bad in (KIT, KIT / "tests", KIT / "tools"):
            r = run(PY, str(KIT / "install.py"), "install", str(bad),
                    "--dry-run", cwd=KIT)
            self.assertEqual(r.returncode, 2, f"应拒绝 {bad}\n{r.stdout}")
            self.assertIn("拒绝安装", r.stdout)
        # 控制点：kit 外的临时目录不受影响（--dry-run 不落盘即可）
        outside = self.root / "outside-kit"
        r = run(PY, str(KIT / "install.py"), "install", str(outside),
                "--dry-run", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
