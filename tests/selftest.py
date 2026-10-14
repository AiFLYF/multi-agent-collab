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
        # 清理是"尽力而为"：高负载/杀软扫描时 git 可能超时，任何一步失败都只警告，
        # 继续走后面的强制删除，绝不让收尾报错掩盖测试本身的结论
        try:
            r = run("git", "worktree", "list", "--porcelain", cwd=repo, timeout=180)
        except Exception as e:
            print(f"[rmtree] git worktree list 失败，跳过摘除：{e}")
            continue
        wt_dirs = [ln[9:].strip() for ln in r.stdout.splitlines()
                   if ln.startswith("worktree ")][1:]   # 第一条是主工作区
        for d in wt_dirs:
            try:
                run("git", "worktree", "remove", "--force", d, cwd=repo, timeout=180)
            except Exception as e:
                print(f"[rmtree] worktree remove {d} 失败：{e}")
        try:
            run("git", "worktree", "prune", cwd=repo, timeout=180)
        except Exception as e:
            print(f"[rmtree] worktree prune 失败：{e}")

    # 2) 多轮 chmod + 删除：句柄延迟释放时单轮重试不够（实测需要等到 1 秒以上）
    # Python 3.12 起 onerror 弃用，改用 onexc（第三参是异常实例而非 exc_info 三元组）；
    # 3.8/3.10 没有 onexc，保留旧签名。
    def _clear_readonly(func, fpath, _exc):
        try:
            os.chmod(fpath, stat.S_IWRITE | stat.S_IREAD)
            func(fpath)
        except OSError:
            pass

    def _rmtree():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_clear_readonly)
        else:
            shutil.rmtree(path, onerror=_clear_readonly)

    for delay in (0, 0.3, 0.8, 1.5):
        if delay:
            time.sleep(delay)
        _rmtree()
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
PLATFORM_TARGETS = [dst for _src, dst, _zone in inst.platform_files(inst.ALL_PLATFORMS)]


def run(*args: str, cwd: Path, timeout: int = 90,
        env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env)


def git_available() -> bool:
    """与安装器同一套解析逻辑（PATH 之外还探测常见安装路径）。"""
    return inst.resolve_git() is not None


def force_no_git_env() -> dict:
    """让子进程把 git 视为不可用（经环境变量覆盖，无需真的破坏 PATH）。"""
    e = dict(os.environ)
    e[inst.GIT_ENV_VAR] = "none"
    return e


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
        self.assertIn(f"selfcheck: 48/48 通过", r.stdout)

    def test_board_version_matches_installer(self) -> None:
        """看板 BOARD_VERSION 必须与安装器 KIT_VERSION 一致（防双份漂移）。"""
        self.assertEqual(board.BOARD_VERSION, inst.KIT_VERSION)
        r = run(PY, str(KIT / "tools" / "agents_status.py"), "--version", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(inst.KIT_VERSION, r.stdout)

    def test_native_agents_platforms_detected(self) -> None:
        """Codex/OpenCode/Pi 原生读根 AGENTS.md：检测函数只识别痕迹、不写任何文件。"""
        import os
        # 全程隔离真实用户环境（本机可能真的装了 Codex：~/.codex 存在）
        old_codex = os.environ.get("CODEX_HOME")
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            os.environ["CODEX_HOME"] = str(t / "no-codex-here")
            try:
                self.assertEqual(inst.detect_native_agents_platforms(t), [])
                (t / ".pi").mkdir()
                (t / ".opencode").mkdir()
                hits = inst.detect_native_agents_platforms(t)
                self.assertIn("pi", hits)
                self.assertIn("opencode", hits)
                self.assertNotIn("codex", hits)
                # Codex 项目级无标记，靠 CODEX_HOME（默认 ~/.codex）识别全局安装
                (t / "no-codex-here").mkdir()
                self.assertIn("codex", inst.detect_native_agents_platforms(t))
            finally:
                if old_codex is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old_codex

    def test_git_resolver_env_override(self) -> None:
        """MULTI_AGENT_GIT：none 强制不可用；显式路径原样采用。"""
        old = os.environ.get(board.GIT_ENV_VAR)
        try:
            os.environ[board.GIT_ENV_VAR] = "none"
            board._reset_git_cache()
            self.assertIsNone(board.resolve_git())
            os.environ[board.GIT_ENV_VAR] = PY
            board._reset_git_cache()
            self.assertEqual(board.resolve_git(), PY)
        finally:
            board._reset_git_cache()
            if old is None:
                os.environ.pop(board.GIT_ENV_VAR, None)
            else:
                os.environ[board.GIT_ENV_VAR] = old

    def test_git_resolver_finds_fake_git_on_path(self) -> None:
        """PATH 里放一个假 git 目录应被优先发现（证明不依赖硬编码安装路径）。"""
        d = Path(tempfile.mkdtemp(prefix="mac-fakegit-"))
        fake = d / ("git.exe" if os.name == "nt" else "git")
        fake.write_bytes(b"fake" if os.name == "nt" else b"#!/bin/sh\n")
        if os.name != "nt":
            fake.chmod(0o755)
        old_path = os.environ.get("PATH", "")
        old_ov = os.environ.pop(board.GIT_ENV_VAR, None)
        board._reset_git_cache()
        try:
            os.environ["PATH"] = str(d) + os.pathsep + old_path
            self.assertEqual(os.path.normcase(board.resolve_git()),
                             os.path.normcase(str(fake)))
        finally:
            os.environ["PATH"] = old_path
            if old_ov is not None:
                os.environ[board.GIT_ENV_VAR] = old_ov
            board._reset_git_cache()
            shutil.rmtree(d, ignore_errors=True)

    def test_inbox_extra_closed_words(self) -> None:
        """cancelled / abandoned 等关闭态不得再算作 open 问题。"""
        d = Path(tempfile.mkdtemp(prefix="mac-inbox-"))
        try:
            idir = d / ".agents" / "inbox"
            idir.mkdir(parents=True)
            (idir / "Q-1.md").write_text(
                "- from: a\n- to: any\n- status: cancelled\n- created: 2026-10-11 10:00\n",
                encoding="utf-8")
            (idir / "Q-2.md").write_text(
                "- from: a\n- to: any\n- status: abandoned\n- created: 2026-10-11 10:00\n",
                encoding="utf-8")
            (idir / "Q-3.md").write_text(
                "- from: a\n- to: any\n- status: open\n- created: 2026-10-11 10:00\n",
                encoding="utf-8")
            r = run(PY, str(KIT / "tools" / "agents_status.py"), "--json",
                    cwd=d, env=force_no_git_env())
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)  # Q-3 仍 open
            ids = {q["file"] for q in json.loads(r.stdout)["inbox_open"]}
            self.assertEqual(ids, {"Q-3.md"})
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_write_lf_atomic_no_temp_leftovers(self) -> None:
        d = Path(tempfile.mkdtemp(prefix="mac-write-"))
        try:
            p = Path(d) / "sub" / "a.txt"
            inst.write_lf(p, "hello\n")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello\n")
            leftovers = list(Path(d).rglob("*.tmp"))
            self.assertEqual(leftovers, [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_html_snapshot_cli(self) -> None:
        """--html 生成自包含单文件快照；默认落在 .agents/board.html。"""
        d = Path(tempfile.mkdtemp(prefix="mac-html-"))
        try:
            sdir = d / ".agents" / "sessions"
            sdir.mkdir(parents=True)
            hb = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
            (sdir / "trae-260101-0900-ab12.md").write_text(
                "# S\n- agent: trae\n- branch: feature/ui\n- status: active\n"
                f"- heartbeat: {hb}\n- task: 可视化快照任务\n", encoding="utf-8")
            out = d / "dash.html"
            r = run(PY, str(KIT / "tools" / "agents_status.py"),
                    "--html", str(out), cwd=d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stdout + r.stderr)
            html = out.read_text(encoding="utf-8").lower()
            self.assertTrue(html.lstrip().startswith("<!doctype html"))
            self.assertIn("可视化快照任务", html)
            self.assertIn("<meta charset=", html)
            self.assertNotIn("<script src=", html)
            self.assertNotIn('href="http', html)
            # 默认路径
            r2 = run(PY, str(KIT / "tools" / "agents_status.py"), "--html", cwd=d)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            self.assertTrue((d / ".agents" / "board.html").is_file())
            # 原子写不留临时文件
            self.assertEqual(list(d.rglob("*.tmp")), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_web_dashboard_loopback(self) -> None:
        """--web 背后的服务：127.0.0.1 上 / 返回页面、/data 返回实时 JSON。"""
        import threading
        import urllib.request
        import urllib.error
        d = Path(tempfile.mkdtemp(prefix="mac-web-"))
        try:
            sdir = d / ".agents" / "sessions"
            sdir.mkdir(parents=True)
            hb = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
            (sdir / "trae-260101-0900-ab34.md").write_text(
                "# S\n- agent: trae\n- branch: feature/live\n- status: active\n"
                f"- heartbeat: {hb}\n- task: 实时看板任务\n", encoding="utf-8")
            httpd = board.make_server(d, port=0, with_prs=False)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                # 超时给宽些：冷启动 + CI/杀软扫描 + 高负载机器上 collect() 要连跑多个 git
                page = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=15).read().decode("utf-8")
                self.assertIn("本机协作看板", page)
                data = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/data", timeout=15).read().decode("utf-8"))
                self.assertIn("sessions", data)
                self.assertIn("git_available", data)
                self.assertTrue(any(s["id"].startswith("trae-260101")
                                    for s in data["sessions"]))
                try:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/nope", timeout=15)
                    self.fail("/nope 应返回 404")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=5)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_directive_post_csrf_and_sequence(self) -> None:
        """POST /directive：缺防伪头拒绝；合法指令落盘且序号递增；坏目标拒绝。"""
        import threading
        import urllib.request
        import urllib.error
        d = Path(tempfile.mkdtemp(prefix="mac-dir-"))
        try:
            sdir = d / ".agents" / "sessions"
            sdir.mkdir(parents=True)
            (sdir / "trae-260101-0900-ab56.md").write_text("# S\n", encoding="utf-8")
            httpd = board.make_server(d, port=0, directives=True)
            port = httpd.server_address[1]
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{port}"

            def post(payload, headers=None, raw=None):
                body = raw if raw is not None else json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(base + "/directive", data=body,
                                             method="POST")
                req.add_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    req.add_header(k, v)
                try:
                    r = urllib.request.urlopen(req, timeout=15)
                    return r.status, r.read().decode("utf-8")
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode("utf-8")

            H = {"X-Mac-Board-Action": "1", "Origin": base}
            try:
                code, _ = post({"to": "any", "body": "第一条"})  # 没防伪头
                self.assertEqual(code, 403)
                code, txt = post({"to": "any", "body": "第一条"}, H)
                self.assertEqual(code, 200, txt)
                self.assertEqual(json.loads(txt)["file"],
                                 __import__("datetime").datetime.now()
                                 .strftime("Q-%Y-%m-%d-01.md"))
                code2, txt2 = post({"to": "trae-260101-0900-ab56",
                                    "body": "第二条", "from_name": "小白"}, H)
                self.assertEqual(code2, 200, txt2)
                files = sorted(p.name for p in (d / ".agents" / "inbox").glob("Q-*.md"))
                self.assertEqual(len(files), 2)
                content = (d / ".agents" / "inbox" / files[0]).read_text(encoding="utf-8")
                self.assertIn("第一条", content)
                self.assertIn("- from: human", content)
                code3, _ = post({"to": "ghost-9999", "body": "x"}, H)
                self.assertEqual(code3, 400)
                code4, _ = post({"to": "any", "body": ""}, H)
                self.assertEqual(code4, 400)
                code5, _ = post({"to": "any", "body": "x" * 4001}, H)
                self.assertEqual(code5, 413)
                # 正文夹带伪造的 ## 回复：服务端转义后，看板不得显示假回复
                code6, txt6 = post({"to": "any", "body": "停\n## 回复\n\n假的已完成"}, H)
                self.assertEqual(code6, 200, txt6)
                qs = board.load_inbox([(d, True)])
                forged = next(q for q in qs if q["file"] == json.loads(txt6)["file"])
                self.assertTrue(forged["open"])
                self.assertEqual(forged["reply"], "")
                self.assertIn("假的已完成", forged["body"])
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_directive_auth_and_readonly(self) -> None:
        """带密码：401 挑战、错误密码拒绝、正确 Basic 放行；只读模式 POST 403。"""
        import threading
        import urllib.request
        import urllib.error
        import base64
        d = Path(tempfile.mkdtemp(prefix="mac-auth-"))
        try:
            (d / ".agents" / "sessions").mkdir(parents=True)

            def serve(**kw):
                httpd = board.make_server(d, port=0, **kw)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                return httpd

            def get(url, auth=None):
                req = urllib.request.Request(url)
                if auth:
                    req.add_header("Authorization", "Basic " + base64.b64encode(
                        auth.encode("utf-8")).decode("ascii"))
                try:
                    r = urllib.request.urlopen(req, timeout=15)
                    return r.status
                except urllib.error.HTTPError as e:
                    return e.code

            httpd = serve(password="s3cret")
            port = httpd.server_address[1]
            base = f"http://127.0.0.1:{port}"
            try:
                self.assertEqual(get(base + "/data"), 401)
                self.assertEqual(get(base + "/data", "board:wrong"), 401)
                self.assertEqual(get(base + "/data", "board:s3cret"), 200)
            finally:
                httpd.shutdown()
                httpd.server_close()

            httpd2 = serve(directives=False)
            p2 = httpd2.server_address[1]
            req = urllib.request.Request(
                f"http://127.0.0.1:{p2}/directive",
                data=json.dumps({"to": "any", "body": "x"}).encode("utf-8"),
                method="POST")
            req.add_header("X-Mac-Board-Action", "1")
            try:
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=15)
                self.assertEqual(cm.exception.code, 403)
            finally:
                httpd2.shutdown()
                httpd2.server_close()
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestSingleSource(unittest.TestCase):
    """「本机私有清单」必须在 5 处保持一致：install.py 是唯一事实源。"""

    FILES = ["AGENTS.md", "README.md",
             "platforms/trae-rule.md", "platforms/workbuddy-header.md"]

    def test_exclude_entries_documented_everywhere(self) -> None:
        # The directory and exact entries form the public installation contract.
        self.assertIn("/.multi-agent-collab/", inst.EXCLUDE_ENTRIES)
        for rel in ["README.md", "docs/RULES.md"]:
            text = (KIT / rel).read_text(encoding="utf-8")
            self.assertIn(".multi-agent-collab/", text)
            self.assertIn(".gitignore", text)
            self.assertIn("已跟踪", text)

    def test_no_extra_private_dirs_claimed(self) -> None:
        broad = {"/.agents/", "/.agent/", "/.claude/", "/.trae/", "/.workbuddy-ai/", "/.workbuddy/", "/tools/"}
        self.assertFalse(broad & set(inst.EXCLUDE_ENTRIES))
        for _src, dst, _zone in inst.platform_files(inst.ALL_PLATFORMS):
            self.assertIn("/" + dst, inst.EXCLUDE_ENTRIES)

    def test_readme_e2e_count_matches_suite(self) -> None:
        """README 里的测试计数必须与套件实际数一致（防数字漂移）。"""
        n_total = len([m for m in dir(TestBoardUnits) if m.startswith("test_")]) \
            + len([m for m in dir(TestSingleSource) if m.startswith("test_")]) \
            + len([m for m in dir(TestInstallerE2E) if m.startswith("test_")])
        readme = (KIT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"# (\d+) 个测试用例", readme)
        self.assertTrue(m, "README 缺少「# N 个测试用例」计数")
        self.assertEqual(int(m.group(1)), n_total,
                         f"README 写 {m.group(1)}，实际 {n_total}；请同步计数")


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
        # 基础安装放在 setUpClass：test_02..test_19 都依赖主工作区已装好（含 worktree
        # 场景要从主工作区继承项目名）。放在 test_01 里会造成"单跑后续用例必挂"的顺序耦合。
        r = install(cls.proj, "--project-name", "Demo 项目", "--platforms", "all")
        if r.returncode != 0:
            raise RuntimeError(f"基础安装失败：{r.stdout + r.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        import os
        if os.environ.get("KEEP_TMP"):
            print(f"\n[KEEP_TMP] 临时目录保留在 {cls.root}")
        else:
            rmtree_readonly(cls.root)

    def test_01_install(self) -> None:
        # 基础安装已在 setUpClass 完成；这里校验安装结果与忽略规则
        for rel in CORE_TARGETS + PLATFORM_TARGETS:
            self.assertTrue((self.proj / rel).is_file(), f"缺少 {rel}")

        trae = (self.proj / ".trae/rules/git-flow-agent.md").read_text(encoding="utf-8")
        self.assertIn("alwaysApply: true", trae)
        self.assertIn("Demo 项目", trae)

        buddy = (self.proj / inst.WORKBUDDY_TARGET).read_text(encoding="utf-8")
        self.assertIn("WorkBuddy 协作入口", buddy)
        self.assertIn(".multi-agent-collab/RULES.md", buddy)
        self.assertNotIn("## 1. 分支铁律", buddy)

        for entry in inst.EXCLUDE_ENTRIES:
            probe = entry.lstrip("/").rstrip("/")
            if entry.endswith("/"):
                probe += "/__mac_probe__"
            chk = run("git", "check-ignore", "-q", probe, cwd=self.proj)
            self.assertEqual(chk.returncode, 0, f"{entry} 没有被忽略")

        br = run(PY, ".multi-agent-collab/tools/agents_status.py", "--json", cwd=self.proj)
        self.assertIn(br.returncode, (0, 1))
        self.assertEqual(json.loads(br.stdout)["sessions"], [])

    def test_02_doctor_clean(self) -> None:
        r = run(PY, str(KIT / "install.py"), "doctor", str(self.proj), cwd=KIT, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("[FAIL]", r.stdout)
        self.assertIn("0 个 FAIL", r.stdout)
        self.assertIn("看板可运行·人类模式", r.stdout)

    def test_03_idempotent_no_backups(self) -> None:
        r = install(self.proj, "--project-name", "Demo 项目")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # 内容相同：不新建备份；backups 目录此时应该不存在
        self.assertFalse((self.proj / ".multi-agent-collab/backups").exists(),
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
        rules = self.proj / ".multi-agent-collab/RULES.md"
        original = rules.read_text(encoding="utf-8")
        custom = original.replace("- 语言/运行时：", "- 我的门禁：cargo test --workspace\n- 语言/运行时：")
        rules.write_text(custom, encoding="utf-8")
        agents = self.proj / "AGENTS.md"
        project_rule = "\n项目额外要求：先读 architecture.md。\n"
        agents.write_text(agents.read_text(encoding="utf-8") + project_rule, encoding="utf-8")
        result = install(self.proj)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("cargo test --workspace", rules.read_text(encoding="utf-8"))
        self.assertTrue(agents.read_text(encoding="utf-8").endswith(project_rule))
        modified = rules.read_text(encoding="utf-8").replace("你的首要职责", "用户改写的协作职责")
        rules.write_text(modified, encoding="utf-8")
        result = install(self.proj)
        self.assertEqual(result.returncode, 2)
        self.assertIn("已修改或归属未知", result.stderr)
        self.assertEqual(rules.read_text(encoding="utf-8"), modified)
        rules.write_text(custom, encoding="utf-8")  # Restore the accepted zone edit for later integration cases.

    def test_06_troubleshooting_d_zone_migrated(self) -> None:
        troubles = self.proj / ".multi-agent-collab/TROUBLESHOOTING.md"
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
        target = self.fresh_project("unmarked-troubles")
        self.assertEqual(install(target, "--platforms", "none").returncode, 0)
        path = target / ".multi-agent-collab/TROUBLESHOOTING.md"
        original = "# 项目原手册\n\n## D3 保留全部约定\n不能只迁移部分内容。\n"
        path.write_text(original, encoding="utf-8")
        result = install(target, "--platforms", "none")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_08_gbk_files_and_unmarked_agents(self) -> None:
        target = self.fresh_project("legacy-gbk")
        original = "# 我们团队自己的规矩\r\n强制 code review\r\n".encode("gbk")
        (target / "AGENTS.md").write_bytes(original)
        result = install(target, "--platforms", "none")
        self.assertEqual(result.returncode, 2)
        self.assertIn("非 UTF-8", result.stderr)
        self.assertEqual((target / "AGENTS.md").read_bytes(), original)
        self.assertFalse((target / ".multi-agent-collab").exists())

    def test_09_board_subdir_and_sessions_semantics(self) -> None:
        sdir = self.proj / ".multi-agent-collab/sessions"
        idir = self.proj / ".multi-agent-collab/inbox"
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
        r = run(PY, str(self.proj / ".multi-agent-collab/tools/agents_status.py"), "--json", cwd=sub)
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
        r2 = run(PY, str(self.proj / ".multi-agent-collab/tools/agents_status.py"), cwd=sub)
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
        d = run(PY, str(KIT / "install.py"), "doctor", str(wt), cwd=KIT, timeout=180)
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertNotIn("不是 git 仓库", d.stdout)
        self.assertIn("linked worktree", d.stdout)
        chk = run("git", "check-ignore", "-q", "AGENTS.md", cwd=wt)
        self.assertEqual(chk.returncode, 0, "linked worktree 里协作文件也应被忽略")

        # 在该 worktree 补齐安装：项目名应从主工作区 install.json 继承（不叫 demo-wt）
        i = install(wt)
        self.assertEqual(i.returncode, 0, i.stdout + i.stderr)
        self.assertTrue((wt / ".multi-agent-collab/tools/agents_status.py").is_file())
        trae_wt = (wt / ".trae/rules/git-flow-agent.md").read_text(encoding="utf-8")
        self.assertIn("Demo 项目", trae_wt)
        d2 = run(PY, str(KIT / "install.py"), "doctor", str(wt), cwd=KIT, timeout=180)
        self.assertEqual(d2.returncode, 0, d2.stdout + d2.stderr)
        self.assertNotIn("[FAIL]", d2.stdout)

        # 模拟另一个 agent 在 worktree 改文件（git status 口径）
        time = __import__("time")
        time.sleep(1.0)
        (wt / "src").mkdir(exist_ok=True)
        (wt / "src/coupon.py").write_text("# coupon\n", encoding="utf-8")
        br = run(PY, ".multi-agent-collab/tools/agents_status.py", "--json", cwd=self.proj)
        data = json.loads(br.stdout)
        # M2：主工作区自己不许自告警；linked wt 改动要告警
        warned_paths = [w.split("（分支", 1)[0].replace("未注册 worktree：", "").strip()
                        for w in data["warnings"]]
        warned_paths = [p.replace("\\", "/").rstrip("/") for p in warned_paths]
        # git 回报的是真实路径；tempfile 建的却是别名路径（macOS 的 /var 软链、
        # Windows CI 的 8.3 短名 RUNNER~1）——两边先 resolve 规范化再比较
        proj_real = str(Path(self.proj).resolve()).replace("\\", "/")
        wt_real = str(Path(wt).resolve()).replace("\\", "/")
        self.assertNotIn(proj_real, warned_paths,
                         f"主工作区被误报：{warned_paths}")
        self.assertIn(wt_real, warned_paths)
        self.assertIn("feature/coupon", "\n".join(data["warnings"]))

        # 在 worktree 注册会话（拷整个 .multi-agent-collab 过去是 AGENTS.md 规定做法）
        shutil.copytree(self.proj / ".multi-agent-collab", wt / ".multi-agent-collab", dirs_exist_ok=True)
        (wt / ".multi-agent-collab/sessions/claude-251009-1000-dddd.md").write_text(
            "# S\n- agent: claude\n- branch: feature/coupon\n- status: active\n"
            f"- heartbeat: {__import__('datetime').datetime.now():%Y-%m-%d %H:%M}\n",
            encoding="utf-8")
        br2 = run(PY, ".multi-agent-collab/tools/agents_status.py", "--json", cwd=self.proj)
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
        self.assertIn(".multi-agent-collab/RULES.md", buddy)
        self.assertIn("镜像测试标记", agents.read_text(encoding="utf-8"))
        self.assertNotIn("镜像测试标记", buddy)

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
        d = run(PY, str(KIT / "install.py"), "doctor", str(self.proj), cwd=KIT, timeout=180)
        self.assertEqual(d.returncode, 0, d.stdout + d.stderr)
        self.assertIn(f"套件版本 {inst.KIT_VERSION}", d.stdout)

    def test_14_uninstall_safe_and_purge_keeps_user_data(self) -> None:
        target = self.fresh_project("uninstall")
        original = "# 项目规则\nElixir 1.17（卸载测试）\n"
        (target / "AGENTS.md").write_text(original, encoding="utf-8")
        self.assertEqual(install(target, "--platforms", "none").returncode, 0)
        record = target / ".multi-agent-collab/sessions/trae-250115-1200-abcd.md"
        record.write_text("# 我的会话记录\n", encoding="utf-8")
        rules = target / ".multi-agent-collab/RULES.md"
        rules.write_text(rules.read_text(encoding="utf-8") + "\n自定义规则\n", encoding="utf-8")
        for extra in ([], ["--purge-files"], ["--purge-files"]):
            result = run(PY, str(KIT / "install.py"), "uninstall", str(target), *extra, cwd=KIT)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((target / "AGENTS.md").read_text(encoding="utf-8").startswith(original))
            self.assertNotIn(inst.ENTRY_BEGIN, (target / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual(record.read_text(encoding="utf-8"), "# 我的会话记录\n")
            self.assertIn("自定义规则", rules.read_text(encoding="utf-8"))
            self.assertEqual(run("git", "check-ignore", "-q", str(record), cwd=target).returncode, 0)
        self.assertFalse((target / ".multi-agent-collab/tools/agents_status.py").exists())
        self.assertTrue((target / inst.INSTALL_META).exists())

    def test_15_exclude_has_workbuddy_dir(self) -> None:
        self.assertIn("/.workbuddy-ai/git-flow-agent.md", inst.EXCLUDE_ENTRIES)
        self.assertNotIn("/.workbuddy/", inst.EXCLUDE_ENTRIES)

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

    def test_17_git_unavailable_warns_in_repo_only(self) -> None:
        """git 不可用：git 仓库内必须显著告警且退出码 1；非 git 目录不惩罚。"""
        env = force_no_git_env()
        board_py = str(KIT / "tools" / "agents_status.py")
        rj = run(PY, board_py, "--json", cwd=self.proj, env=env)
        self.assertEqual(rj.returncode, 1, rj.stdout + rj.stderr)
        data = json.loads(rj.stdout)
        self.assertFalse(data["git_available"])
        self.assertTrue(any("git" in w for w in data["warnings"]), data["warnings"])
        rh = run(PY, board_py, cwd=self.proj, env=env)
        self.assertEqual(rh.returncode, 1, rh.stdout + rh.stderr)
        self.assertIn("git 不可用", rh.stdout + rh.stderr)

        plain = self.root / "plain-nogit"
        plain.mkdir(exist_ok=True)
        r2 = run(PY, board_py, "--json", cwd=plain, env=env)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        d2 = json.loads(r2.stdout)
        self.assertFalse(d2["git_available"])
        self.assertEqual(d2["warnings"], [])

    def test_18_done_session_does_not_suppress_worktree(self) -> None:
        """done 的历史会话不能永久抑制该 worktree 的未注册告警。"""
        wt = self.root / "demo-wt-done"
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        r = run("git", "worktree", "add", "-q", str(wt), "-b",
                "feature/zombie", "develop", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        sdir = wt / ".multi-agent-collab" / "sessions"
        sdir.mkdir(parents=True)
        (sdir / "human-251008-0600-zz99.md").write_text(
            "# S\n- agent: human\n- branch: feature/zombie\n- status: done\n"
            "- heartbeat: 2026-10-08 06:00\n", encoding="utf-8")
        time.sleep(1.0)
        (wt / "zombie.txt").write_text("x\n", encoding="utf-8")
        br = run(PY, ".multi-agent-collab/tools/agents_status.py", "--json", cwd=self.proj)
        warnings = json.loads(br.stdout)["warnings"]
        self.assertTrue(any("feature/zombie" in w for w in warnings),
                        f"done 会话不该抑制 worktree 告警：{warnings}")

    def test_19_non_utf8_exclude_fails_before_install(self) -> None:
        """忽略无法写入时必须拒绝安装；不能生成一批未被忽略的私有文件。"""
        t = self.root / "gbk-exclude"
        t.mkdir(exist_ok=True)
        self.assertEqual(run("git", "init", "-q", cwd=t).returncode, 0)
        exclude = t / ".git" / "info" / "exclude"
        exclude.write_bytes(b"# \xff\xfe garbage\r\n/somedir/\n")
        r = install(t, "--platforms", "none")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("非 UTF-8", r.stdout + r.stderr)
        self.assertIn(b"\xff\xfe", exclude.read_bytes(), "原 exclude 字节不应被改写")
        self.assertFalse((t / "AGENTS.md").exists())
        self.assertFalse((t / ".multi-agent-collab").exists())

    def fresh_project(self, name: str) -> Path:
        target = self.root / name
        target.mkdir()
        r = run("git", "init", "-q", cwd=target)
        self.assertEqual(r.returncode, 0, r.stderr)
        return target

    def test_20_native_selection_is_minimal_and_remembered(self) -> None:
        target = self.fresh_project("native-only")
        r = install(target, "--platforms", "codex,pi,codex,opencode")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for rel in CORE_TARGETS:
            self.assertTrue((target / rel).is_file(), rel)
        for rel in PLATFORM_TARGETS + [".codex", ".opencode", ".pi"]:
            self.assertFalse((target / rel).exists(), f"原生平台不应额外生成 {rel}")
        meta_path = target / inst.INSTALL_META
        before = meta_path.read_bytes()
        self.assertEqual(json.loads(before)["platforms"], ["codex", "opencode", "pi"])
        again = install(target)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("新建/覆盖 0 个", again.stdout)
        self.assertEqual(meta_path.read_bytes(), before, "幂等安装不重写安装时间")
        self.assertEqual(run("git", "status", "--porcelain", cwd=target).stdout, "?? .gitignore\n")

    def test_21_first_noninteractive_install_requires_choice(self) -> None:
        target = self.fresh_project("needs-choice")
        exclude = target / ".git/info/exclude"
        before = exclude.read_bytes()
        r = install(target)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("--platforms", r.stderr)
        self.assertFalse((target / "AGENTS.md").exists())
        self.assertEqual(exclude.read_bytes(), before)
        for choice in ("", "all,codex", "all,unknown", "codxe"):
            r = install(target, "--platforms", choice)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_22_sync_adds_platforms_and_preserves_shared_content(self) -> None:
        target = self.fresh_project("sync-add")
        r = install(target, "--platforms", "codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        agents = target / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8") + "\n项目额外约定：保留整段。\n", encoding="utf-8")
        startup = target / ".multi-agent-collab/START_HERE.md"
        startup.write_text(startup.read_text(encoding="utf-8") + "\n启动补充：检查本地工单。\n", encoding="utf-8")
        originals = {p: p.read_bytes() for p in (agents, startup)}
        copilot = target / ".github/copilot-instructions.md"
        copilot.parent.mkdir()
        copilot.write_text("# 我的说明\n请使用项目术语。\n", encoding="utf-8")
        r = run(PY, str(KIT / "install.py"), "sync", str(target),
                "--platforms", "cursor,copilot,workbuddy", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for p, original in originals.items():
            self.assertEqual(p.read_bytes(), original, f"sync 不得覆盖 {p.name}")
        self.assertIn("我的说明", copilot.read_text(encoding="utf-8"))
        self.assertIn(".multi-agent-collab/START_HERE.md", copilot.read_text(encoding="utf-8"))
        buddy = (target / inst.WORKBUDDY_TARGET).read_text(encoding="utf-8")
        self.assertIn(".multi-agent-collab/RULES.md", buddy)
        self.assertNotIn("项目额外约定：保留整段", buddy)
        self.assertFalse((target / "CLAUDE.md").exists())
        meta = inst.read_meta(target)
        self.assertEqual(set(meta["platforms"]), {"codex", "cursor", "copilot", "workbuddy"})
        r2 = run(PY, str(KIT / "install.py"), "sync", str(target), cwd=KIT)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("新建/覆盖 0 个", r2.stdout)
        self.assertEqual(copilot.read_text(encoding="utf-8").count(inst.ENTRY_BEGIN), 1)
        for unrelated in (".github/workflows/build.yml", ".cursor/rules/team.mdc", ".windsurf/rules/team.md"):
            chk = run("git", "check-ignore", "-q", unrelated, cwd=target)
            self.assertEqual(chk.returncode, 1, f"不应隐藏其它配置：{unrelated}")

    def test_23_legacy_platform_selection_migrates_without_expanding(self) -> None:
        target = self.fresh_project("legacy-platforms")
        r = install(target, "--platforms", "trae,claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        meta = inst.read_meta(target)
        del meta["platforms"]
        meta["kit_version"] = "1.4.1"
        inst.write_lf(target / inst.INSTALL_META, json.dumps(meta))
        r = install(target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(set(inst.read_meta(target)["platforms"]), {"claude", "trae"})
        for name in ("cursor", "copilot", "windsurf", "workbuddy"):
            for _src, dst, _zone in inst.platform_files([name]):
                self.assertFalse((target / dst).exists(), f"升级不能自动铺入 {dst}")

    def test_24_doctor_fails_for_a_selected_missing_entry(self) -> None:
        target = self.fresh_project("missing-entry")
        r = install(target, "--platforms", "cursor")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        entry = target / ".cursor/rules/multi-agent-collab.mdc"
        entry.unlink()
        doctor = run(PY, str(KIT / "install.py"), "doctor", str(target), cwd=KIT, timeout=180)
        self.assertEqual(doctor.returncode, 1, doctor.stdout + doctor.stderr)
        self.assertIn("[FAIL] cursor: 已选平台入口缺失", doctor.stdout)
        self.assertNotIn("[FAIL] claude", doctor.stdout)
        r = run(PY, str(KIT / "install.py"), "sync", str(target), cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(entry.is_file())

    def test_25_tracked_rules_are_not_overwritten(self) -> None:
        target = self.fresh_project("tracked-rules")
        agents = target / "AGENTS.md"
        original = b"# Team rules\r\nRun project tests.\r\n"
        agents.write_bytes(original)
        self.assertEqual(run("git", "add", "AGENTS.md", cwd=target).returncode, 0)
        index = run("git", "ls-files", "--stage", cwd=target).stdout
        result = install(target, "--platforms", "codex")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(agents.read_bytes().startswith(original))
        self.assertEqual(agents.read_text(encoding="utf-8").count(inst.ENTRY_BEGIN), 1)
        self.assertEqual(run("git", "ls-files", "--stage", cwd=target).stdout, index)
        self.assertIn("AGENTS.md", run("git", "diff", "--name-only", cwd=target).stdout)

    def test_26_ignore_override_prevents_false_success(self) -> None:
        target = self.fresh_project("ignore-override")
        (target / ".gitignore").write_text("!AGENTS.md\n", encoding="utf-8")
        r = install(target, "--platforms", "codex")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("未被忽略", r.stderr)
        self.assertFalse((target / "AGENTS.md").exists())
        self.assertFalse((target / ".multi-agent-collab").exists())
        self.assertTrue((target / ".gitignore").read_text(encoding="utf-8").startswith("!AGENTS.md\n"))

    def test_27_sync_dry_run_is_read_only(self) -> None:
        target = self.fresh_project("sync-preview")
        r = install(target, "--platforms", "none")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
        r = run(PY, str(KIT / "install.py"), "sync", str(target),
                "--platforms", "claude,windsurf", "--dry-run", cwd=KIT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "sync --dry-run 不应改动任何文件或平台记录")

    def test_28_claude_imports_startup_and_retains_existing_notes(self) -> None:
        target = self.fresh_project("claude-notes")
        (target / "CLAUDE.md").write_text("# 本机说明\n先运行本项目 smoke。\n", encoding="utf-8")
        r = install(target, "--platforms", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = (target / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("先运行本项目 smoke", text)
        self.assertIn("@AGENTS.md", text)
        self.assertIn("@.multi-agent-collab/START_HERE.md", text)
        r = install(target)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((target / "CLAUDE.md").read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    # 不用 unittest.main() 的直接退出：跳过大批用例时（典型：git 真没装）必须醒目提示，
    # 不能让"全 skip 但退出码 0"伪装成绿灯（AGENTS.md A3：绿测试≠对）。
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(f"\n[WARN] {len(result.skipped)} 个用例被跳过（示例原因："
              f"{result.skipped[0][1][:60]}）——核心保证未被验证，请先装 git。",
              file=sys.stderr)
    sys.exit(0 if result.wasSuccessful() else 1)
