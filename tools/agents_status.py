#!/usr/bin/env python3
"""本机协作看板 —— AGENTS.md 第 3 节「开工协调」要跑的东西。

它读的全是本机文件（各 worktree 下的 `.agents/`）+ 本地 git 状态，不联网、不写任何东西。

用法：
    cd <你的项目根>（或任意子目录）
    python tools/agents_status.py                 # 人类可读（自动探测仓库根）
    python tools/agents_status.py --root D:/repo  # 显式指定项目根
    python tools/agents_status.py --stale-min 30  # 改失活阈值（默认 15 分钟）
    python tools/agents_status.py --json          # 给脚本用
    python tools/agents_status.py --prs           # 附带 open PR（只读 gh，失败降级）
    python tools/agents_status.py --selfcheck     # 内置自测（在临时目录造场景）

退出码：0 = 一切正常；1 = 有值得注意的事（失活会话/未答问题/worktree 告警）。
可以当开工门禁用：`python tools/agents_status.py || echo "先处理上面的告警"`

⚠️ 关于「与远程的差异」：本脚本**只读本地 remote-tracking 引用**，
而某些 AI 工具沙箱会静默回滚 .git/refs 的写入（见 TROUBLESHOOTING B8/B10），
所以 ahead/behind 可能是假的。需要真相请用 `gh api` 或 `git ls-remote`。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# 默认项目根：main() 里通过 detect_root() 自动探测（显式 --root > cwd > git 顶层）
ROOT = Path.cwd()

STALE_MIN_DEFAULT = 15
WORKTREE_WARN_MIN = 30
OLD_INBOX_DAYS = 7          # 超过这么多天的 open 问题只提示、不影响退出码
FUTURE_HB_TOL_MIN = 10      # 心跳比当前时刻快超过这么多 → 视为无效（时钟错乱/手滑）

# 精确匹配的「已结束」状态词（取状态首词，小写）。
# 注意：blocked 不算结束 —— 「被卡住」恰恰需要别人看见并接手。
CLOSED_WORDS = {
    "done", "superseded", "closed", "abandoned", "cancelled", "canceled",
    "完成", "已完成", "已关闭", "已取消", "废弃",
}


def _git_at(cwd: Path, *args: str) -> str:
    """在指定目录跑 git 并返回 stdout；失败返回空串（看板不该因为 git 报错就崩）。"""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _git(*args: str) -> str:
    return _git_at(ROOT, *args)


def parse_kv(path: Path) -> dict:
    """把 Markdown 顶部的 `- key: value` 列表解析成 dict。"""
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return data
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]\s*\*{0,2}([A-Za-z_][\w -]*?)\*{0,2}\s*[:：]\s*(.+?)\s*$", line)
        if m:
            key = m.group(1).strip().lower()
            # 同一个 key 只保留第一次出现（避免正文里的 `- x: y` 覆盖头部）
            data.setdefault(key, m.group(2).strip())
    return data


def parse_time(raw: str) -> datetime | None:
    """容忍多种写法：YYYY-MM-DD HH:MM[:SS] / YYYY-MM-DD / 带时区后缀（按本机时间解释）。"""
    if not raw:
        return None
    s = raw.strip().strip("`")
    s = re.sub(r"\s*[（(].*$", "", s)          # 去掉「（心跳…）」之类后缀
    s = re.sub(r"([+-]\d{2}:?\d{2}|Z)$", "", s)  # 去掉时区（约定：一律写本机本地时间）
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def valid_heartbeat(raw: str, now: datetime) -> datetime | None:
    """解析心跳；缺失/乱码/明显的未来时间一律返回 None（按失活处理，绝不让看板崩）。"""
    hb = parse_time(raw)
    if hb is None:
        return None
    if hb > now + timedelta(minutes=FUTURE_HB_TOL_MIN):
        return None
    return hb


def status_word(raw: str) -> str:
    """取状态的首个词，去掉后面的括号说明。"""
    s = re.sub(r"[（(].*$", "", raw or "").strip()
    return s.split()[0].lower() if s.split() else "?"


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/").lower()


def detect_root(explicit: str | None) -> Path:
    """项目根探测：显式 --root > 当前目录（有 .agents/）> git 顶层 > 当前目录。"""
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd()
    if (cwd / ".agents").is_dir():
        return cwd
    top_raw = _git_at(cwd, "rev-parse", "--show-toplevel").strip()
    if top_raw:
        top = Path(top_raw).resolve()
        if (top / ".agents").is_dir() and top != cwd:
            rel = str(cwd.relative_to(top)) if top in cwd.parents else str(cwd)
            print(f"提示：当前目录（{rel}）没有 .agents/，已改用仓库根 {top}",
                  file=sys.stderr)
            return top
    return cwd


def agent_roots() -> list[tuple[Path, bool]]:
    """返回 (worktree 路径, 是否主工作区)，只保留里面真有 .agents/ 的。

    linked worktree 各自独立工作区、各自一份未跟踪的 .agents/，必须逐个扫
    （TROUBLESHOOTING A1：只扫主工作区会让别的 worktree 里的会话彻底隐形）。
    非 git 仓库或没有任何 worktree 输出时，退化为只看 ROOT。
    """
    raw = _git("worktree", "list", "--porcelain")
    found: list[tuple[Path, bool]] = []
    main_path = ""
    cur: dict | None = None
    rows: list[dict] = []
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur:
                rows.append(cur)
            cur = {"path": line[9:].strip()}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].strip().replace("refs/heads/", "")
        elif line.strip() == "detached":
            cur["branch"] = "(detached)"
    if cur:
        rows.append(cur)
    if not rows:
        return [(ROOT, True)]

    root_norm = _norm(str(ROOT))
    for row in rows:
        p = Path(row["path"])
        is_main = _norm(str(p)) == root_norm
        if is_main:
            main_path = str(p)
        if (p / ".agents").is_dir():
            found.append((p, is_main))
    # git 认为的主工作区可能不是当前 ROOT（从 linked worktree 里跑看板时，
    # git 的"主"是另一个目录）。此时以 ROOT 为"当前工作区"，其余都算别的 worktree。
    if not any(p == ROOT for p, _ in found) and (ROOT / ".agents").is_dir():
        found.insert(0, (ROOT, False))
    if not found:
        found = [(ROOT, True)]
    return found


def load_sessions(roots: list[tuple[Path, bool]], now: datetime,
                  stale_min: int) -> list[dict]:
    out: dict[str, dict] = {}
    for root, is_main in roots:
        sdir = root / ".agents" / "sessions"
        if not sdir.is_dir():
            continue
        tag = "" if is_main else str(root)
        for f in sorted(sdir.glob("*.md")):
            if f.name.startswith("_"):          # _template.md
                continue
            kv = parse_kv(f)
            hb = valid_heartbeat(kv.get("heartbeat", ""), now)
            age = (now - hb).total_seconds() / 60 if hb else None
            if age is not None and age < 0:
                age = 0.0
            st = status_word(kv.get("status", ""))
            closed = st in CLOSED_WORDS
            item = {
                "id": f.stem,
                "agent": kv.get("agent", "?"),
                "branch": kv.get("branch", "?"),
                "task": kv.get("task", ""),
                "status": kv.get("status", "?"),
                "heartbeat": hb,
                "age_min": age,
                "closed": closed,
                "blocked": st == "blocked",
                "stale": (not closed) and (age is None or age > stale_min),
                "pr": kv.get("pr", ""),
                "file": f.name,
                "worktree": tag,
            }
            # 同一会话 ID 在多个 worktree 各有一份（拷过 .agents/）→ 留心跳最新的
            old = out.get(f.stem)
            if old is None or (item["heartbeat"] or datetime.min) > (old["heartbeat"] or datetime.min):
                out[f.stem] = item
    return sorted(out.values(),
                  key=lambda s: (s["heartbeat"] or datetime.min), reverse=True)


def load_inbox(roots: list[tuple[Path, bool]]) -> list[dict]:
    seen: dict[str, dict] = {}
    for root, _is_main in roots:
        idir = root / ".agents" / "inbox"
        if not idir.is_dir():
            continue
        for f in sorted(idir.glob("Q-*.md")):
            if f.name in seen:
                continue
            kv = parse_kv(f)
            st = status_word(kv.get("status", "open"))
            # 模板字段叫 created，早期版本看板读 date —— 两个都兼容
            date_raw = kv.get("date") or kv.get("created") or ""
            seen[f.name] = {
                "file": f.name,
                "status": st,
                "to": kv.get("to", "any"),
                "from": kv.get("from", "?"),
                "date": date_raw,
                "created_dt": parse_time(date_raw),
                "open": st not in ("answered", "closed", "done"),
            }
    return list(seen.values())


def load_worklogs(roots: list[tuple[Path, bool]], n: int = 3) -> list[dict]:
    seen: dict[str, dict] = {}
    for root, _is_main in roots:
        ldir = root / ".agents" / "worklog"
        if not ldir.is_dir():
            continue
        for f in sorted(ldir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md")):
            if f.name in seen:
                continue
            try:
                heads = [l.strip() for l in
                         f.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.startswith("## ")]
            except OSError:
                heads = []
            seen[f.name] = {"file": f.name, "sections": heads[-3:], "count": len(heads)}
    return sorted(seen.values(), key=lambda x: x["file"], reverse=True)[:n]


def worktrees() -> list[dict]:
    """解析 `git worktree list --porcelain`。"""
    raw = _git("worktree", "list", "--porcelain")
    items, cur = [], {}
    for line in raw.splitlines():
        if line.startswith("worktree "):
            if cur:
                items.append(cur)
            cur = {"path": line[9:].strip()}
        elif line.startswith("HEAD "):
            cur["head"] = line[5:].strip()[:7]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].strip().replace("refs/heads/", "")
        elif line.strip() == "detached":
            cur["branch"] = "(detached)"
    if cur:
        items.append(cur)
    for it in items:
        it.setdefault("branch", "?")
        it.setdefault("head", "?")
    return items


def parse_prs(raw: str) -> list[dict] | None:
    """解析 `gh pr list --json number,title,headRefName,updatedAt,author` 的输出。

    纯函数，便于自测；输入不是合法 JSON 数组时返回 None。
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    out: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        author = it.get("author")
        out.append({
            "number": it.get("number"),
            "title": it.get("title", ""),
            "branch": it.get("headRefName", "?"),
            "updated": (it.get("updatedAt") or "")[:16].replace("T", " "),
            "author": author.get("login", "?") if isinstance(author, dict) else "?",
        })
    return out


def fetch_open_prs() -> list[dict] | None:
    """只读地查 open PR。gh 缺失/未登录/超时/不在 GitHub 仓库 → 返回 None（调用方降级提示）。"""
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "50",
             "--json", "number,title,headRefName,updatedAt,author"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=12)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return parse_prs(r.stdout)


def wt_recent_changes(path: str, minutes: int) -> int:
    """统计某 worktree 里 N 分钟内有活动的**改动文件**数（含未跟踪）。

    用 `git status --porcelain` 而不是全树 os.walk：
    - checkout/switch 会把所有签出文件 mtime 刷新成现在，但它们不在 status 里
      —— 因此「刚切了分支」不会再触发假告警；
    - 大仓库里 vendor/build/虚拟环境等未改动的目录直接被 git 忽略，零遍历成本；
    - 删除的文件 stat 不到，但「删除」本身就是活动信号，计 1。
    """
    cutoff = datetime.now() - timedelta(minutes=minutes)
    raw = _git_at(Path(path), "status", "--porcelain", "--untracked-files=normal")
    n = 0
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        name = line[3:].strip()
        if " -> " in name:                  # 重命名：R  old -> new
            name = name.split(" -> ", 1)[1]
        p = Path(path) / name.strip('"')
        try:
            if datetime.fromtimestamp(p.stat().st_mtime) > cutoff:
                n += 1
        except OSError:
            n += 1                          # 已删除/不可见：有改动就算活动
    return n


# --------------------------------------------------------------------------- #
# 内置自测
# --------------------------------------------------------------------------- #

def selfcheck() -> int:
    """在临时目录里造一组会话/inbox/日志，走一遍 JSON 与人类可读输出并断言。"""
    checks: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        checks.append((name, bool(cond)))

    # 1) 纯函数（临时目录放系统 TEMP：某些沙箱不允许进程在工作区里建目录）
    with tempfile.TemporaryDirectory(prefix="agents-selfcheck-") as td:
        tmp = Path(td)
        f = tmp / "q.md"
        f.write_text(
            "# Q\n- from: a\n- to: any\n- status: open\n"
            "- created: 2025-01-15 10:00\n\n正文里的 `- x: y` 不该覆盖头部\n",
            encoding="utf-8")
        kv = parse_kv(f)
        check("parse_kv 解析头部", kv.get("from") == "a" and kv.get("to") == "any")
        check("parse_kv 首值优先", kv.get("x") is None)
        check("parse_time 秒级", parse_time("2025-01-15 10:00:30") is not None)
        check("parse_time 容错括号", parse_time("2025-01-15 10:00（心跳）") is not None)
        check("parse_time 垃圾返回 None", parse_time("乱写") is None)
        check("status_word 去括号", status_word("active（干活中）") == "active")
        now = datetime.now()
        check("未来心跳判定无效", valid_heartbeat("2099-01-01 00:00", now) is None)
        check("正常心跳有效", valid_heartbeat(f"{now:%Y-%m-%d %H:%M}", now) is not None)
        check("状态精确匹配：未完成≠已结束",
              status_word("未完成（继续干）") not in CLOSED_WORDS)
        check("状态精确匹配：done 才算结束", "done" in CLOSED_WORDS)
        check("blocked 不算结束", "blocked" not in CLOSED_WORDS)
        prs = parse_prs('[{"number":7,"title":"修登录","headRefName":"feature/login",'
                        '"updatedAt":"2025-01-15T09:30:00Z","author":{"login":"ai"}}]')
        check("parse_prs 正常解析", bool(prs) and prs[0]["number"] == 7
              and prs[0]["branch"] == "feature/login" and prs[0]["author"] == "ai")
        check("parse_prs 垃圾输入返回 None", parse_prs("not json") is None
              and parse_prs('{"a":1}') is None)

        # 2) 造一个完整 .agents 场景
        sdir = tmp / ".agents" / "sessions"
        idir = tmp / ".agents" / "inbox"
        ldir = tmp / ".agents" / "worklog"
        for d in (sdir, idir, ldir):
            d.mkdir(parents=True)
        (sdir / "_template.md").write_text("模板，不该被解析\n", encoding="utf-8")
        (sdir / "trae-250115-0900-aaaa.md").write_text(
            "# Session\n- agent: trae\n- branch: feature/x\n- status: active\n"
            f"- heartbeat: {now:%Y-%m-%d %H:%M}\n- task: 活跃会话\n", encoding="utf-8")
        (sdir / "buddy-250115-0800-bbbb.md").write_text(
            "# Session\n- agent: workbuddy\n- branch: feature/y\n- status: active\n"
            f"- heartbeat: {(now - timedelta(minutes=40)):%Y-%m-%d %H:%M}\n"
            "- task: 失活会话\n", encoding="utf-8")
        (sdir / "claude-250112-1000-cccc.md").write_text(
            "# Session\n- agent: claude\n- branch: feature/z\n- status: done\n"
            f"- heartbeat: {(now - timedelta(days=3)):%Y-%m-%d %H:%M}\n",
            encoding="utf-8")
        (sdir / "trae-250115-0700-eeee.md").write_text(
            "# Session\n- agent: trae\n- branch: feature/bad\n- status: active\n"
            "- heartbeat: 刚才写了中文（坏心跳）\n", encoding="utf-8")
        (idir / "Q-2025-01-15-01.md").write_text(
            "# Q\n- from: trae-x\n- to: any\n- status: open\n"
            f"- created: {now:%Y-%m-%d %H:%M}\n", encoding="utf-8")
        (idir / "Q-2025-01-14-01.md").write_text(
            "# Q\n- from: trae-x\n- to: any\n- status: answered\n", encoding="utf-8")
        (ldir / "2025-01-15.md").write_text(
            "# log\n## 10:00 · trae-x\n\n做了点事\n", encoding="utf-8")

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc_json = main(["--json", "--root", str(tmp)])
        payload = json.loads(buf.getvalue())

        sessions = payload["sessions"]
        check("JSON 解析 4 个会话（模板被跳过）", len(sessions) == 4)
        active = [s for s in sessions if not s["closed"] and not s["stale"]]
        stale = [s for s in sessions if s["stale"]]
        check("1 个活跃会话", len(active) == 1 and active[0]["id"].startswith("trae-250115-0900"))
        check("2 个失活会话（含坏心跳）",
              {s["id"] for s in stale} == {"buddy-250115-0800-bbbb", "trae-250115-0700-eeee"})
        check("done 会话不算失活",
              all(s["id"] != "claude-250112-1000-cccc" for s in stale))
        check("未答问题只算 open", len(payload["inbox_open"]) == 1)
        check("inbox 日期兼容 created 字段", payload["inbox_open"][0]["date"] != "")
        check("worklog 读到 1 天", len(payload["worklogs"]) == 1)
        check("有告警时退出码 1", rc_json == 1)

        # 3) 坏心跳在【人类模式】也不许崩（回归 S1）
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc_human = main(["--root", str(tmp)])
        human = buf2.getvalue()
        check("坏心跳人类模式不崩", rc_human in (0, 1) and "Traceback" not in human)
        check("坏心跳有可读提示", "无有效心跳" in human)

        # 4) 干净场景（只留 done 会话 + 已答问题）→ 退出码 0
        (sdir / "buddy-250115-0800-bbbb.md").unlink()
        (sdir / "trae-250115-0700-eeee.md").unlink()
        (idir / "Q-2025-01-15-01.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            rc_clean = main(["--json", "--root", str(tmp)])
        check("无告警时退出码 0", rc_clean == 0)

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print(f"\nselfcheck: {len(checks) - len(failed)}/{len(checks)} 通过")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    global ROOT
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 控制台中文
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="本机协作看板")
    ap.add_argument("--stale-min", type=int, default=STALE_MIN_DEFAULT,
                    help=f"心跳超过多少分钟算失活（默认 {STALE_MIN_DEFAULT}）")
    ap.add_argument("--root", type=str, default=None,
                    help="项目根目录（默认自动探测：当前目录 → git 顶层）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--prs", action="store_true",
                    help="同时只读查询 open PR（调 gh；gh 不可用时静默降级，不影响退出码）")
    ap.add_argument("--selfcheck", action="store_true", help="运行内置自测")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    ROOT = detect_root(args.root)

    open_prs = fetch_open_prs() if args.prs else []

    now = datetime.now()
    roots = agent_roots()
    sessions = load_sessions(roots, now, args.stale_min)
    inbox = load_inbox(roots)
    logs = load_worklogs(roots)
    wts = worktrees()

    active = [s for s in sessions if not s["closed"] and not s["stale"]]
    stale = [s for s in sessions if s["stale"]]
    open_q = [q for q in inbox if q["open"]]
    # 远古 open 问题：只提示，不算告警（忘关的问题不该让开工门禁常年失败）
    old_cut = now - timedelta(days=OLD_INBOX_DAYS)
    fresh_q, old_q = [], []
    for q in open_q:
        dt = q["created_dt"]
        (old_q if (dt is not None and dt < old_cut) else fresh_q).append(q)
    registered_branches = {s["branch"].split("（")[0].split("(")[0].strip() for s in sessions}

    warnings: list[str] = []
    root_norm = _norm(str(ROOT))
    for w in wts:
        # 只盯 linked worktree；当前工作区/主工作区永不自告警（切分支会刷新 mtime，是假信号）
        if _norm(w["path"]) == root_norm:
            continue
        if w["branch"] in registered_branches:
            continue
        changed = wt_recent_changes(w["path"], WORKTREE_WARN_MIN)
        if changed:
            warnings.append(
                f"未注册 worktree：{w['path']}（分支 {w['branch']}）"
                f"{WORKTREE_WARN_MIN} 分钟内 {changed} 个改动文件，但没有会话注册该分支")

    if args.json:
        def q_json(q: dict) -> dict:
            return {k: v for k, v in q.items() if k != "created_dt"}
        print(json.dumps({
            "sessions": [{**s, "heartbeat": s["heartbeat"].isoformat() if s["heartbeat"] else None}
                         for s in sessions],
            "inbox_open": [q_json(q) for q in fresh_q],
            "inbox_open_old": [q_json(q) for q in old_q],
            "worklogs": logs, "worktrees": wts,
            "open_prs": open_prs if args.prs else None,
            "warnings": warnings, "now": now.isoformat(),
        }, ensure_ascii=False, indent=2))
        stale_recent = [s for s in stale
                        if s["age_min"] is None or s["age_min"] < 24 * 60]
        return 1 if (warnings or stale_recent or fresh_q) else 0

    line = "─" * 68
    print(f"\n本机协作看板   {now:%Y-%m-%d %H:%M}   (失活阈值 {args.stale_min} 分钟)")
    print(f"项目根 {ROOT}（{len(roots)} 个工作区的 .agents/）")
    print(line)

    # ---- 会话 ----
    print(f"【会话】共 {len(sessions)} 个，活跃 {len(active)}，失活 {len(stale)}")
    for s in sessions:
        if s["closed"] and not s["stale"]:
            mark = "✓"
        elif s["stale"]:
            mark = "!"
        else:
            mark = "●"
        if s["age_min"] is not None:
            age = f"{max(0.0, s['age_min']):.0f} 分钟前"
        else:
            age = "无有效心跳（缺失/无法解析/时间在未来）"
        loc = "" if not s["worktree"] else f"  [wt] {s['worktree']}"
        blocked_tag = "  (blocked)" if s["blocked"] and s["stale"] else ""
        print(f"  {mark} {s['id']}{loc}")
        print(f"      分支 {s['branch']}")
        print(f"      状态 {s['status']}   心跳 {age}{blocked_tag}")
        if s["task"]:
            print(f"      任务 {s['task'][:70]}")
    print("  (● 活跃   ! 失活   ✓ 已结束)")

    # ---- 未答问题 ----
    print(f"\n【inbox】未答 {len(fresh_q)}（另有 {len(old_q)} 个超过 {OLD_INBOX_DAYS} 天的旧问题）"
          f" / 共 {len(inbox)}")
    for q in fresh_q:
        print(f"  ? {q['file']}   to={q['to']}  from={q['from']}  ({q['date']})")
    if not fresh_q and inbox:
        print("  （没有需要近期处理的问题）")

    # ---- 工作日志 ----
    print("\n【worklog】最近")
    for lg in logs:
        print(f"  {lg['file']}  {lg['count']} 段")
        for h in lg["sections"]:
            print(f"      {h[:72]}")

    # ---- worktree ----
    print(f"\n【worktree】{len(wts)} 个")
    for w in wts:
        is_main = _norm(w["path"]) == root_norm
        tag = "当前" if is_main else "linked"
        reg = "已注册" if w["branch"] in registered_branches else "未注册"
        print(f"  [{tag}/{reg}] {w['branch']} @ {w['head']}")
        print(f"      {w['path']}")

    # ---- open PR（仅 --prs；开着的 PR 也是"活跃会话"，见 AGENTS.md 第 3 节）----
    if args.prs:
        print(f"\n【open PR】")
        if open_prs is None:
            print("  ℹ️  gh 不可用/未登录/超时 —— 请手工跑 `gh pr list --state open` 后再开工")
        elif not open_prs:
            print("  （没有 open PR）")
        else:
            for pr in open_prs:
                print(f"  #{pr['number']:<4} [{pr['branch']}] {pr['title'][:60]}"
                      f"  ({pr['author']}, 更新 {pr['updated']})")

    # ---- git 状态 ----
    print("\n【git】")
    print(f"  当前分支 {_git('branch', '--show-current').strip() or '?'}"
          f" @ {_git('rev-parse', '--short', 'HEAD').strip() or '?'}")
    dirty = _git("status", "--short").splitlines()
    print(f"  工作区改动 {len(dirty)} 项")
    loose = _git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    print(f"  本地分支 {len(loose)} 个")
    print("  ⚠️ 本脚本只读**本地** remote-tracking 引用，而它可能被沙箱静默回滚（B8/B10），"
          "\n     所以这里不报 ahead/behind —— 要真相请用 `gh api` 或 `git ls-remote`。")

    # ---- 告警汇总 ----
    # 失活：无心跳的始终提示；有时间的只对 24 小时内的报警（更老的是历史记录）
    stale_recent = [s for s in stale if s["age_min"] is None or s["age_min"] < 24 * 60]
    print(f"\n{line}")
    if warnings:
        for w in warnings:
            print(f"  ⚠️  {w}")
    for s in stale_recent:
        if s["age_min"] is not None:
            age_txt = f"{s['age_min']:.0f} 分钟前"
        else:
            age_txt = "无有效心跳（检查 heartbeat 字段格式/机器时间）"
        extra = " [wt] " + s["worktree"] if s["worktree"] else ""
        print(f"  ⚠️  失活会话 {s['id']}（{s['branch']}，{age_txt}）{extra}"
              f"—— 要接管请先读 AGENTS.md 第 4 节")
    if fresh_q:
        print(f"  ⚠️  {len(fresh_q)} 条未答问题，离开前顺手答一下")
    if old_q:
        print(f"  ℹ️  {len(old_q)} 条超过 {OLD_INBOX_DAYS} 天的旧 open 问题，"
              f"记得 answered/closed 掉")
    if not (warnings or stale_recent or fresh_q or old_q):
        print("  ✅ 无告警")
    print()

    return 1 if (warnings or stale_recent or fresh_q) else 0


if __name__ == "__main__":
    sys.exit(main())
