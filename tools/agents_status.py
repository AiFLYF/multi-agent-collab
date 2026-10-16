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
    python tools/agents_status.py --web           # 浏览器实时看板（默认 127.0.0.1，网页可发指令）
    python tools/agents_status.py --web --bind 0.0.0.0 --no-directives   # 局域网只读分享
    python tools/agents_status.py --html [路径]   # 自包含 HTML 单文件快照（默认 .agents/board.html）
    python tools/agents_status.py --selfcheck     # 内置自测（在临时目录造场景）

# 局域网可指挥：先设 MULTI_AGENT_BOARD_PASSWORD 再 --web --bind 0.0.0.0
# （对外绑定+可发指令+无密码会拒绝启动；只读还可用 MULTI_AGENT_BOARD_READONLY=1）。

退出码：0 = 一切正常；1 = 有值得注意的事（失活会话/未答问题/worktree 告警）。
可以当开工门禁用：`python tools/agents_status.py || echo "先处理上面的告警"`

⚠️ 关于「与远程的差异」：本脚本**只读本地 remote-tracking 引用**，
而某些 AI 工具沙箱会静默回滚 .git/refs 的写入（见 TROUBLESHOOTING B8/B10），
所以 ahead/behind 可能是假的。需要真相请用 `gh api` 或 `git ls-remote`。

git 查找顺序：环境变量 MULTI_AGENT_GIT（可指向 git.exe；设为 none 强制禁用）
> PATH > Windows/macOS 常见安装路径。在 git 仓库里却找不到 git 时退出码为 1。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# 默认项目根：main() 里通过 detect_root() 自动探测（显式 --root > cwd > git 顶层）
ROOT = Path.cwd()

STALE_MIN_DEFAULT = 15
WORKTREE_WARN_MIN = 30
OLD_INBOX_DAYS = 7          # 超过这么多天的 open 问题只提示、不影响退出码
FUTURE_HB_TOL_MIN = 10      # 心跳比当前时刻快超过这么多 → 视为无效（时钟错乱/手滑）

# 版本必须与 install.py 的 KIT_VERSION 保持一致（有测试锁定，防两边漂移）。
# 看板独立分发到目标项目，不能 import 安装器，所以各存一份常量。
BOARD_VERSION = "1.7.0"

# 人类可视化模式（--web / --html）：纯本机，不影响 JSON/终端模式与 agent 用法
WEB_PORT_DEFAULT = 8731
WEB_REFRESH_SEC = 5
from agents_layout import state_path, state_dir

SNAPSHOT_DEFAULT = ".agents/board.html"

# 人类指令通道（网页 → .agents/inbox/）
DIRECTIVE_MAX_LEN = 4000
DIRECTIVE_ENV_PASSWORD = "MULTI_AGENT_BOARD_PASSWORD"
DIRECTIVE_ENV_READONLY = "MULTI_AGENT_BOARD_READONLY"
LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1"}
DIRECTIVE_HEADER = "X-Mac-Board-Action"   # 跨站表单伪造不出这个头（会触发 CORS 预检）
AUTH_FAIL_LIMIT = 5                        # 同一 IP 连续密码错误上限
AUTH_LOCK_SEC = 60
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# 精确匹配的「已结束」状态词（取状态首词，小写）。
# 注意：blocked 不算结束 —— 「被卡住」恰恰需要别人看见并接手。
CLOSED_WORDS = {
    "done", "superseded", "closed", "abandoned", "cancelled", "canceled",
    "完成", "已完成", "已关闭", "已取消", "废弃",
}
# inbox 的关闭态：会话关闭词之外，"已回复"也算关闭
INBOX_CLOSED_WORDS = CLOSED_WORDS | {"answered"}

# git 可执行文件解析：环境变量 > PATH > 常见安装路径。
# 环境变量设为 none/0/off 可强制视为不可用（测试与排障用）。
GIT_ENV_VAR = "MULTI_AGENT_GIT"
_GIT_FORCE_OFF = {"none", "0", "off", "no", "false"}
_GIT_UNSET = object()
_git_bin: object = _GIT_UNSET


def _candidate_git_paths() -> list[str]:
    """PATH 之外的常见安装位置（Windows 最常见：装了 Git for Windows 但终端没带上 PATH）。"""
    cands: list[str] = []
    if os.name == "nt":
        for env_name, sub in (
            ("ProgramFiles", r"Git\cmd\git.exe"),
            ("ProgramFiles(x86)", r"Git\cmd\git.exe"),
            ("LOCALAPPDATA", r"Programs\Git\cmd\git.exe"),
        ):
            base = os.environ.get(env_name)
            if base:
                cands.append(os.path.join(base, sub))
    else:
        # macOS 上 /opt/homebrew/bin 可能不在最小化 PATH 里
        cands += ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git"]
    return cands


def resolve_git() -> str | None:
    """返回可用的 git 可执行文件路径；找不到返回 None（结果缓存）。"""
    global _git_bin
    if _git_bin is not _GIT_UNSET:
        return _git_bin  # type: ignore[return-value]
    override = os.environ.get(GIT_ENV_VAR, "").strip()
    if override.lower() in _GIT_FORCE_OFF:
        _git_bin = None
        return None
    if override:
        _git_bin = override               # 用户显式指定，原样信任
        return override
    found = shutil.which("git")
    if not found:
        for cand in _candidate_git_paths():
            if os.path.isfile(cand):
                found = cand
                break
    _git_bin = found
    return found


def _reset_git_cache() -> None:
    """仅供自测：环境变量变化后重置解析缓存。"""
    global _git_bin
    _git_bin = _GIT_UNSET


def _git_at(cwd: Path, *args: str) -> str:
    """在指定目录跑 git 并返回 stdout；git 不可用/失败返回空串（看板不该因为 git 报错就崩）。"""
    git = resolve_git()
    if not git:
        return ""
    try:
        out = subprocess.run(
            [git, *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _raises(fn, exc: type) -> bool:
    """自测用：fn 是否抛指定异常。"""
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def is_fresh_session(s: dict, now: datetime) -> bool:
    """会话「还在值」：未结束，且心跳在 24h 内；心跳无效时看会话文件 mtime。

    用途两处，口径必须一致：
    - 哪些会话注册的分支可以抑制「未注册 worktree」告警（done/超 24h 的不算）；
    - 失活会话是否计入近期告警（坏心跳的老文件不该永久卡住开工门禁）。
    """
    if s.get("closed"):
        return False
    age = s.get("age_min")
    if age is not None:
        return age < 24 * 60
    mtime = s.get("file_mtime")
    return mtime is not None and (now - mtime) < timedelta(hours=24)


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
    # Git reports long paths while cwd/TEMP can use Windows 8.3 aliases.
    # Resolve filesystem aliases before deciding whether a worktree is ourselves.
    try:
        p = str(Path(p).resolve())
    except (OSError, RuntimeError):
        pass  # Missing/inaccessible worktrees still get a stable lexical key.
    return p.replace("\\", "/").rstrip("/").lower()


def detect_root(explicit: str | None) -> Path:
    """项目根探测：显式 --root > 当前目录（有 .agents/）> git 顶层 > 当前目录。"""
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd()
    if (state_path(cwd)).is_dir():
        return cwd
    top_raw = _git_at(cwd, "rev-parse", "--show-toplevel").strip()
    if top_raw:
        top = Path(top_raw).resolve()
        if (state_path(top)).is_dir() and top != cwd:
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
        if (state_path(p)).is_dir():
            found.append((p, is_main))
    # git 认为的主工作区可能不是当前 ROOT（从 linked worktree 里跑看板时，
    # git 的"主"是另一个目录）。此时以 ROOT 为"当前工作区"，其余都算别的 worktree。
    if not any(p == ROOT for p, _ in found) and (state_path(ROOT)).is_dir():
        found.insert(0, (ROOT, False))
    if not found:
        found = [(ROOT, True)]
    return found


def parse_claimed_files(raw: str) -> tuple[list[str], bool]:
    if not raw:
        return [], False
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            return [], True
        return value, False
    except (ValueError, TypeError):
        return [], True


def overlapping_files(left: list[str], right: list[str]) -> list[str]:
    def normalize(path: str) -> str:
        path = path.replace("\\", "/").rstrip("/")
        return path.lower() if os.name == "nt" else path
    matches = []
    for a in left:
        an = normalize(a)
        for b in right:
            bn = normalize(b)
            if an == bn or (a.endswith(("/", "\\")) and bn.startswith(an + "/")) or (
                    b.endswith(("/", "\\")) and an.startswith(bn + "/")):
                matches.append(a)
                break
    return sorted(set(matches))


def load_sessions(roots: list[tuple[Path, bool]], now: datetime,
                  stale_min: int) -> list[dict]:
    out: dict[str, dict] = {}
    for root, is_main in roots:
        sdir = state_path(root, "sessions")
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
            try:
                ftime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                ftime = None
            st = status_word(kv.get("status", ""))
            closed = st in CLOSED_WORDS
            claimed, claims_error = parse_claimed_files(kv.get("files", ""))
            item = {
                "id": f.stem,
                "agent": kv.get("agent", "?"),
                "branch": kv.get("branch", "?"),
                "task": kv.get("task", ""),
                "status": kv.get("status", "?"),
                "heartbeat": hb,
                "age_min": age,
                "file_mtime": ftime,
                "closed": closed,
                "blocked": st == "blocked",
                "stale": (not closed) and (age is None or age > stale_min),
                "pr": kv.get("pr", ""),
                "file": f.name,
                "worktree": tag,
                "workspace": str(root.resolve()),
                "files": claimed,
                "claims_error": claims_error,
                "progress": kv.get("progress", ""),
                "handoff": kv.get("handoff", ""),
            }
            # 同一会话 ID 在多个 worktree 各有一份（拷过 .agents/）→ 留心跳最新的
            old = out.get(f.stem)
            if old is None or (item["heartbeat"] or datetime.min) > (old["heartbeat"] or datetime.min):
                out[f.stem] = item
    return sorted(out.values(),
                  key=lambda s: (s["heartbeat"] or datetime.min), reverse=True)


def _inbox_body_reply(text: str) -> tuple[str, str]:
    """从问事板文件抽出（正文, 回复）。正文去头部 kv 块；回复取 ## 回复 之后。"""
    reply = ""
    m = re.search(r"^##\s*回复[^\n]*\n(?P<r>[\s\S]*)$", text, re.M)
    if m:
        reply = "\n".join(l for l in m.group("r").splitlines()
                          if "待回复" not in l).strip()
    lines = text.splitlines()
    i = 1 if lines and lines[0].lstrip().startswith("#") else 0
    # 跳过连续的头部 kv 行、HTML 注释与紧随的空行
    while i < len(lines) and (not lines[i].strip()
                              or lines[i].lstrip().startswith("- ")
                              or lines[i].lstrip().startswith("<!--")):
        i += 1
    body_lines = []
    for l in lines[i:]:
        if l.startswith("## 回复") or l.strip() == "---":
            break
        # 跳过「## 指令」「## 背景」这类正文小节标题，只留正文本身
        if l.startswith("## "):
            continue
        body_lines.append(l)
    body = "\n".join(body_lines).strip()
    # 去掉转义时为防结构注入加的行首空格（仅去我们加的那种整行缩进）
    body = "\n".join(l[1:] if l.startswith(" ") and l[1:2] in ("#", "-", ">", "<")
                     else l for l in body.split("\n"))
    return body.strip(), reply


def load_inbox(roots: list[tuple[Path, bool]]) -> list[dict]:
    seen: dict[str, dict] = {}
    for root, _is_main in roots:
        idir = state_path(root, "inbox")
        if not idir.is_dir():
            continue
        for f in sorted(idir.glob("Q-*.md")):
            if f.name in seen:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            kv = parse_kv(f)
            st = status_word(kv.get("status", "open"))
            # 模板字段叫 created，早期版本看板读 date —— 两个都兼容
            date_raw = kv.get("date") or kv.get("created") or ""
            body, reply = _inbox_body_reply(text)
            frm = kv.get("from", "?")
            seen[f.name] = {
                "file": f.name,
                "status": st,
                "to": kv.get("to", "any"),
                "from": frm,   # 人类指令形如 "human（小白）"，称呼已含在内
                "from_human": frm.startswith("human"),
                "date": date_raw,
                "created_dt": parse_time(date_raw),
                "body": body,
                "reply": reply,
                "open": st not in INBOX_CLOSED_WORDS,
            }
    return list(seen.values())


# ---- 人类指令：网页写 inbox（复用问事板协议，agent 端无需新格式）----

def _clean_one_line(s: str, limit: int) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s).replace("\n", " ").strip()
    return s[:limit]


def _neutralize_body(body: str) -> str:
    r"""转义正文中能伪造文件结构的行首（标题/kv/分隔线/HTML 注释）。

    否则有人可以在指令里写 `## 回复` + 假回复，让看板显示一条 agent 从没说过的话。
    行首插一个空格后它不再是 markdown 结构，原文一字不丢（前导空格阅读无感）。
    """
    out = []
    for line in body.split("\n"):
        s = line.lstrip()
        if (s.startswith("#") or s.startswith("- ") or s.startswith("---")
                or s.startswith("<!--") or s.startswith(">")):
            line = " " + line
        out.append(line)
    return "\n".join(out)


def create_directive(inbox_dir: Path, to: str, body: str,
                     from_name: str = "", now: datetime | None = None) -> Path:
    """把一条人类指令写成 Q-YYYY-MM-DD-NN.md（O_EXCL 抢序号，并发也不互撞）。"""
    to = (to or "any").strip()
    if to != "any" and not _TARGET_RE.match(to):
        raise ValueError(f"非法目标：{to!r}")
    body = re.sub(r"\r\n?", "\n", body or "")
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", body).strip()
    if not body:
        raise ValueError("指令正文为空")
    if len(body) > DIRECTIVE_MAX_LEN:
        raise ValueError(f"指令超过 {DIRECTIVE_MAX_LEN} 字")
    body = _neutralize_body(body)
    sender = _clean_one_line(from_name or "", 20)
    now = now or datetime.now()
    stamp = f"{now:%Y-%m-%d %H:%M}"
    prefix = f"Q-{now:%Y-%m-%d}-"
    existing = [p.name for p in inbox_dir.glob(prefix + "*.md")]
    seq = len(existing) + 1
    title = _clean_one_line(body.splitlines()[0], 24)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    who = f"human（{sender}）" if sender else "human"
    for _ in range(50):
        name = f"{prefix}{seq:02d}.md"
        dest = inbox_dir / name
        content = (
            f"# Q-{now:%Y-%m-%d}-{seq:02d}: 人类指令：{title}\n\n"
            f"- from: {who}\n- to: {to}\n- status: open\n"
            f"- created: {stamp}\n- answered: \n\n"
            f"## 指令\n\n{body}\n\n---\n\n## 回复\n\n"
            f"（待回复；执行后把结果写在这里，并把顶部 status 改为 answered）\n"
        )
        try:
            fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            seq += 1
            continue
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return dest
    raise RuntimeError("指令序号连续冲突 50 次，请检查 inbox 目录")


def check_serve_options(bind: str, directives: bool, password: str | None) -> str | None:
    """返回错误说明；None = 组合合法。对外绑定 + 可写 + 无密码 = 唯一禁止组合。"""
    if directives and bind not in LOOPBACK_BINDS and not password:
        return ("对外绑定（非 127.0.0.1）且允许发指令时必须设置密码"
                f"（环境变量 {DIRECTIVE_ENV_PASSWORD}），或加 --no-directives 只读分享")
    return None


def load_worklogs(roots: list[tuple[Path, bool]], n: int = 3) -> list[dict]:
    seen: dict[str, dict] = {}
    for root, _is_main in roots:
        ldir = state_path(root, "worklog")
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

        # git 解析：环境变量覆盖（测完必须还原，后面的场景要用）
        _ov = os.environ.get(GIT_ENV_VAR)
        try:
            os.environ[GIT_ENV_VAR] = "none"
            _reset_git_cache()
            check("resolve_git 强制不可用返回 None", resolve_git() is None)
            os.environ[GIT_ENV_VAR] = sys.executable
            _reset_git_cache()
            check("resolve_git 显式路径原样采用", resolve_git() == sys.executable)
        finally:
            _reset_git_cache()
            if _ov is None:
                os.environ.pop(GIT_ENV_VAR, None)
            else:
                os.environ[GIT_ENV_VAR] = _ov

        # 会话"是否还在值"：决定 worktree 告警抑制与失活是否计入近期
        check("done 会话不在值", not is_fresh_session(
            {"closed": True, "heartbeat": now, "age_min": 1.0}, now))
        check("活跃近期会话在值", is_fresh_session(
            {"closed": False, "heartbeat": now, "age_min": 5.0}, now))
        check("超过 24h 的会话不在值", not is_fresh_session(
            {"closed": False, "heartbeat": now, "age_min": 25 * 60.0}, now))
        check("坏心跳但文件刚改仍在值", is_fresh_session(
            {"closed": False, "heartbeat": None, "age_min": None,
             "file_mtime": now}, now))
        check("坏心跳且文件 3 天前不在值", not is_fresh_session(
            {"closed": False, "heartbeat": None, "age_min": None,
             "file_mtime": now - timedelta(days=3)}, now))
        check("inbox 关闭词含 answered/cancelled/abandoned",
              {"answered", "cancelled", "abandoned"} <= INBOX_CLOSED_WORDS)

        # 人类可视化：HTML 必须自包含（离线可开，零外部依赖）
        demo_payload = {
            "root": str(tmp), "now": now.isoformat(), "git_available": False,
            "stale_min": 15, "sessions": [{
                "id": "trae-250115-0900-aaaa", "agent": "trae",
                "branch": "feature/x", "task": "可视化任务", "status": "active",
                "heartbeat": now.isoformat(), "age_min": 1.0, "closed": False,
                "blocked": False, "stale": False, "fresh": True, "pr": "",
                "file": "x.md", "worktree": ""}],
            "inbox_open": [], "inbox_open_old": [], "worklogs": [],
            "worktrees": [], "open_prs": [], "warnings": [],
        }
        html = render_html(demo_payload, live=False)
        check("render_html 含会话与标题",
              "可视化任务" in html and "本机协作看板" in html
              and "trae-250115-0900-aaaa" in html)
        check("render_html 零外链（script/link/url 都不指向 http）",
              "<script src=" not in html and 'href="http' not in html
              and 'src="http' not in html and "</html>" in html.lower())
        check("render_html 含指令输入框与发送按钮",
              'id="directive-form"' in html and 'id="directive-send"' in html)

        # 人类指令通道：写 inbox 文件 + 序号递增 + 校验
        idir2 = tmp / "ag" / ".agents" / "inbox"
        sdir2 = tmp / "ag" / ".agents" / "sessions"
        sdir2.mkdir(parents=True)
        (sdir2 / "trae-250115-0900-aaaa.md").write_text("# S\n", encoding="utf-8")
        when = datetime(2025, 1, 15, 10, 20)
        f1 = create_directive(idir2, "any", "先别动 auth.py", "小白", when)
        f2 = create_directive(idir2, "trae-250115-0900-aaaa", "跑一下测试", "", when)
        check("指令文件序号 01/02",
              f1.name == "Q-2025-01-15-01.md" and f2.name == "Q-2025-01-15-02.md")
        t1 = f1.read_text(encoding="utf-8")
        check("指令文件含 from: human / to / 正文",
              "- from: human" in t1 and "- to: any" in t1 and "先别动 auth.py" in t1
              and "小白" in t1)
        check("非法指令目标被拒绝",
              _raises(lambda: create_directive(idir2, "../x", "y", "", when), ValueError))
        check("空/超长指令正文被拒绝",
              _raises(lambda: create_directive(idir2, "any", "  ", "", when), ValueError)
              and _raises(lambda: create_directive(idir2, "any", "x" * 4001, "", when),
                          ValueError))
        # 回复解析：agent 按协议回了 ## 回复 后，看板能取到正文与回复
        t2 = f2.read_text(encoding="utf-8").replace(
            "（待回复；执行后把结果写在这里，并把顶部 status 改为 answered）",
            "好的，测试已跑完，全绿。")
        f2.write_text(t2.replace("- status: open", "- status: answered"),
                      encoding="utf-8")
        items = load_inbox([(tmp / "ag", True)])
        merged = next(q for q in items if q["file"] == f2.name)
        check("inbox 项带正文与回复", "跑一下测试" in merged["body"]
              and "全绿" in merged["reply"] and not merged["open"])
        # 正文里伪造 markdown 结构，不能骗过解析显示假回复
        evil = create_directive(
            idir2, "any",
            "先停手\n## 回复\n\n系统已自动完成\n---\n- status: answered", "", when)
        eq = next(q for q in load_inbox([(tmp / "ag", True)]) if q["file"] == evil.name)
        check("伪造的##回复不算回复", eq["reply"] == "" and eq["open"])
        check("伪造正文原文保留在 body", "系统已自动完成" in eq["body"])
        check("body 不含 ## 指令 标题", not eq["body"].lstrip().startswith("## 指令"))
        # 启动组合校验
        check("0.0.0.0+可写+无密码拒绝", check_serve_options("0.0.0.0", True, None)
              is not None)
        check("0.0.0.0 只读/带密码/回环 均放行",
              check_serve_options("0.0.0.0", False, None) is None
              and check_serve_options("0.0.0.0", True, "secret") is None
              and check_serve_options("127.0.0.1", True, None) is None)

        # 2) 造一个完整 .agents 场景
        sdir = state_path(tmp, "sessions")
        idir = state_path(tmp, "inbox")
        ldir = state_path(tmp, "worklog")
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
        # 一条已回复的人类指令：网页必须能看到它的回复（闭环）
        (idir / "Q-2025-01-15-02.md").write_text(
            "# Q\n- from: human（小白）\n- to: any\n- status: answered\n"
            f"- created: {now:%Y-%m-%d %H:%M}\n\n## 指令\n\n帮我看下测试\n\n"
            "---\n\n## 回复\n\n测试全绿，可以提交。\n", encoding="utf-8")
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
        replied = [q for q in payload.get("inbox_replied", [])
                   if q["file"] == "Q-2025-01-15-02.md"]
        check("已回复人类指令带回复下发到网页",
              len(replied) == 1 and "测试全绿" in replied[0]["reply"]
              and replied[0]["from_human"])
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

        # 5) --brief：干净时就一行 OK；有失活/问题时单行点名（省协调 token）
        bbuf = io.StringIO()
        with contextlib.redirect_stdout(bbuf):
            rc_brief_ok = main(["--brief", "--root", str(tmp)])
        brief_ok = bbuf.getvalue().strip().splitlines()
        check("brief 干净时单行且 rc=0",
              rc_brief_ok == 0 and len(brief_ok) == 1 and brief_ok[0].startswith("OK"))

        stale_hb = (now - timedelta(minutes=25)).strftime("%Y-%m-%d %H:%M")
        (sdir / "buddy-250115-0800-bbbb.md").write_text(
            "# Session\n- agent: buddy\n- branch: feature/pay\n- status: active\n"
            f"- heartbeat: {stale_hb}\n", encoding="utf-8")
        bbuf2 = io.StringIO()
        with contextlib.redirect_stdout(bbuf2):
            rc_brief_bad = main(["--brief", "--root", str(tmp)])
        brief_bad = bbuf2.getvalue().strip()
        check("brief 有失活时 rc=1 且单行点名",
              rc_brief_bad == 1 and brief_bad.count("\n") == 0
              and "buddy-250115-0800-bbbb" in brief_bad and "失活" in brief_bad)

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("  PASS  " if ok else "  FAIL  ") + name)
    print(f"\nselfcheck: {len(checks) - len(failed)}/{len(checks)} 通过")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# 数据收集（终端 / JSON / HTML / web 共用同一份，保证四种模式看到的口径完全一致）
# --------------------------------------------------------------------------- #

def collect(now: datetime, with_prs: bool, stale_min: int) -> dict:
    """扫一遍本机全部工作区，返回可直接 JSON 序列化的看板数据。"""
    roots = agent_roots()
    sessions = load_sessions(roots, now, stale_min)
    inbox = load_inbox(roots)
    logs = load_worklogs(roots)
    wts = worktrees()

    open_q = [q for q in inbox if q["open"]]
    # 远古 open 问题：只提示，不算告警（忘关的问题不该让开工门禁常年失败）
    old_cut = now - timedelta(days=OLD_INBOX_DAYS)
    fresh_q, old_q = [], []
    for q in open_q:
        dt = q["created_dt"]
        (old_q if (dt is not None and dt < old_cut) else fresh_q).append(q)
    # 近期已回复（近 7 天、有日期）：网页要让人类看到 agent 对自己指令的回复，形成闭环
    replied_q = [q for q in inbox if not q["open"] and q["created_dt"] is not None
                 and q["created_dt"] >= old_cut]
    # 只有「还在值」的会话能抑制未注册 worktree 告警：done/超 24h 的历史会话不行
    registered_branches = {
        s["branch"].split("（")[0].split("(")[0].strip()
        for s in sessions if is_fresh_session(s, now)
    }

    warnings: list[str] = []
    open_sessions = [session for session in sessions if not session["closed"]]
    for index, session in enumerate(open_sessions):
        if session["claims_error"]:
            warnings.append(f"文件占用记录无效：{session['id']} 的 files 应为 JSON 字符串数组")
        for other in open_sessions[index + 1:]:
            overlap = overlapping_files(session["files"], other["files"])
            if overlap:
                warnings.append(f"文件占用冲突：{session['id']} 与 {other['id']}：{', '.join(overlap[:5])}")
    git_ok_flag = resolve_git() is not None
    # 在 git 仓库里却调不动 git：worktree 聚合/分支信息全部失效，必须明确喊出来，
    # 不能静默显示「worktree 0 个 / ✅ 无告警」（手册 A3：假绿比报错更危险）
    if not git_ok_flag and (ROOT / ".git").exists():
        warnings.append(
            "git 不可用（PATH 与常见安装路径都没找到）：worktree/分支信息已降级，"
            f"请把 Git 加入 PATH，或用环境变量 {GIT_ENV_VAR} 指向 git.exe")
    root_norm = _norm(str(ROOT))
    for w in wts:
        # 只盯 linked worktree；当前工作区/主工作区永不自告警（切分支刷新 mtime 是假信号）
        if _norm(w["path"]) == root_norm:
            continue
        if w["branch"] in registered_branches:
            continue
        changed = wt_recent_changes(w["path"], WORKTREE_WARN_MIN)
        if changed:
            warnings.append(
                f"未注册 worktree：{w['path']}（分支 {w['branch']}）"
                f"{WORKTREE_WARN_MIN} 分钟内 {changed} 个改动文件，但没有会话注册该分支")

    def q_json(q: dict) -> dict:
        return {k: v for k, v in q.items() if k != "created_dt"}

    s_out = []
    for s in sessions:
        out = {k: v for k, v in s.items() if k != "file_mtime"}
        out["heartbeat"] = s["heartbeat"].isoformat() if s["heartbeat"] else None
        out["fresh"] = is_fresh_session(s, now)
        s_out.append(out)

    return {
        "root": str(ROOT),
        "now": now.isoformat(timespec="seconds"),
        "stale_min": stale_min,
        "git_available": git_ok_flag,
        "n_roots": len(roots),
        "sessions": s_out,
        "inbox_total": len(inbox),
        "inbox_open": [q_json(q) for q in fresh_q],
        "inbox_open_old": [q_json(q) for q in old_q],
        "inbox_replied": [q_json(q) for q in replied_q],
        "worklogs": logs,
        "worktrees": wts,
        "prs_requested": with_prs,
        "open_prs": fetch_open_prs() if with_prs else None,
        "warnings": warnings,
        # 快照默认按"可指挥"渲染；web 服务会按 --no-directives/环境变量覆盖这个值
        "directives_enabled": True,
    }


def gate_rc(payload: dict) -> int:
    """开工门禁退出码：告警 / 近期失活会话 / 近期未答问题任一非空即 1。"""
    if payload["warnings"]:
        return 1
    if any(s["stale"] and s["fresh"] for s in payload["sessions"]):
        return 1
    return 1 if payload["inbox_open"] else 0


# --------------------------------------------------------------------------- #
# 人类可视化：自包含 HTML（快照）+ 127.0.0.1 实时看板
# 设计约束：零外部依赖/零外链（离线可开）、只读不写会话文件、不改动 JSON/终端口径
# --------------------------------------------------------------------------- #

_DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>本机协作看板</title>
<!-- 内联 SVG 图标：浏览器默认会去请求 /favicon.ico，服务端只认 / /data /directive，
     于是每个访客都留一条 404 控制台报错。用 data URI 直接消除，且不破坏「零外链」 -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315171c'/%3E%3Cg stroke='%232563eb' stroke-width='2.2' stroke-linecap='round'%3E%3Cpath d='M16 5.5v6M16 20.5v6M5.5 16h6M20.5 16h6'/%3E%3C/g%3E%3Ccircle cx='16' cy='16' r='4.6' fill='%232563eb'/%3E%3C/svg%3E">
<style>
  :root {
    --bg:#f5f6f8; --panel:#ffffff; --ink:#1f2329; --muted:#6b7280;
    --line:#e5e7eb; --green:#16a34a; --red:#dc2626; --amber:#d97706;
    --gray:#9ca3af; --blue:#2563eb; --chip:#eef2f7;
    /* 「已结束」会话的正文色：不再靠整体 opacity 弱化（那会等比压低
       所有子元素的对比度，白底上只剩 ~3.2:1），改用显式的灰，仍有 5.5:1 */
    --done-ink:#5b6472;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#15171c; --panel:#1e2128; --ink:#e5e7eb; --muted:#9aa3af;
      --line:#2e333d; --chip:#262b34; --done-ink:#8b95a3; }
  }
  /* 键盘用户在这块深色看板上只能看到浏览器默认描边，与页面风格不搭；
     同一产品的介绍页早有统一焦点环，这里补齐 */
  :focus-visible { outline:2px solid var(--blue); outline-offset:2px;
    border-radius:6px; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",Roboto,Helvetica,Arial,sans-serif;
    font-size:14px; line-height:1.55; }
  header { padding:18px 22px 12px; }
  h1 { margin:0 0 4px; font-size:20px; }
  .root { color:var(--muted); font-size:12px; word-break:break-all; }
  .topbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
  .pill { display:inline-flex; align-items:center; gap:5px; padding:3px 10px;
    border-radius:999px; background:var(--chip); font-size:12px; color:var(--muted); }
  .pill.live { color:var(--green); }
  .pill.snap { color:var(--amber); }
  .pill.off { color:var(--red); }
  .wrap { padding:0 22px 30px; }
  .banner { border-radius:10px; padding:12px 16px; margin-bottom:14px; font-weight:600; }
  .banner.ok { background:color-mix(in srgb, var(--green) 12%, var(--panel));
    border:1px solid color-mix(in srgb, var(--green) 35%, var(--panel)); }
  .banner.bad { background:color-mix(in srgb, var(--red) 10%, var(--panel));
    border:1px solid color-mix(in srgb, var(--red) 35%, var(--panel)); }
  .banner ul { margin:8px 0 0; padding-left:20px; font-weight:400; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:10px; margin-bottom:16px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:12px 14px; }
  .card .num { font-size:26px; font-weight:700; }
  .card .lbl { color:var(--muted); font-size:12px; }
  .num.green { color:var(--green); } .num.red { color:var(--red); }
  .num.amber { color:var(--amber); } .num.gray { color:var(--gray); }
  .grid { display:grid; grid-template-columns:2fr 1fr; gap:14px; }
  @media (max-width:900px) { .grid { grid-template-columns:1fr; } }
  section { background:var(--panel); border:1px solid var(--line);
    border-radius:10px; padding:14px 16px; margin-bottom:14px; }
  h2 { margin:0 0 10px; font-size:14px; display:flex; justify-content:space-between;
    align-items:center; gap:10px; }
  h2 .count { color:var(--muted); font-weight:400; font-size:12px; margin-left:auto; }
  .composer.collapsed #directive-form { display:none; }
  .session { border:1px solid var(--line); border-left-width:4px; border-radius:8px;
    padding:10px 12px; margin-bottom:9px; }
  .session.active { border-left-color:var(--green); }
  .session.stale { border-left-color:var(--red); }
  .session.done { border-left-color:var(--gray); }
  .session.done .sid, .session.done .task, .session.done .meta { color:var(--done-ink); }
  .s-head { display:flex; flex-wrap:wrap; gap:7px; align-items:center; }
  .dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
  .dot.active { background:var(--green); } .dot.stale { background:var(--red); }
  .dot.done { background:var(--gray); }
  .sid { font-family:ui-monospace,Consolas,monospace; font-size:12.5px; font-weight:600; }
  .tag { background:var(--chip); border-radius:6px; padding:1px 7px; font-size:11.5px;
    color:var(--muted); }
  .tag.branch { color:var(--blue); }
  .tag.blocked { background:color-mix(in srgb,var(--amber) 18%,var(--panel)); color:var(--amber); }
  .tag.wt { color:var(--amber); }
  /* 指令的「待处理 / 已回复」是这一屏最该一眼看到的状态，单独给色 */
  .tag.pending { background:color-mix(in srgb,var(--amber) 18%,var(--panel)); color:var(--amber); }
  .tag.replied { background:color-mix(in srgb,var(--green) 15%,var(--panel)); color:var(--green); }
  .task { margin:6px 0 2px; }
  .meta { color:var(--muted); font-size:12px; }
  /* 心跳色按真实距今时间分级：绿=刚更新，琥珀=正常，红=失活，灰=已结束。
     原来只判断「有没有心跳」，收工的会话照样染成健康的绿色 */
  .age-ok { color:var(--green); } .age-bad { color:var(--red); }
  .age-warn { color:var(--amber); }
  .age-done { color:var(--done-ink); }
  .q { border:1px solid var(--line); border-radius:8px; padding:8px 11px; margin-bottom:8px; }
  .q.old { opacity:.7; }
  .wt { border:1px solid var(--line); border-radius:8px; padding:7px 10px; margin-bottom:7px; }
  .wt.unreg { border-color:color-mix(in srgb,var(--red) 45%,var(--line)); }
  .wtpath { color:var(--muted); font-size:11.5px; word-break:break-all; }
  .logitem { margin-bottom:8px; }
  .logfile { font-weight:600; font-size:12.5px; }
  .logsec { color:var(--muted); font-size:12px; padding-left:8px; }
  .pr { border-bottom:1px dashed var(--line); padding:7px 0; font-size:12.5px; }
  a { color:var(--blue); }
  .empty { color:var(--muted); font-size:12.5px; }
  label.chk { display:flex; gap:6px; align-items:center; font-weight:400;
    font-size:12px; color:var(--muted); cursor:pointer; padding:4px 0; }
  label.chk input { width:16px; height:16px; }
  footer { color:var(--muted); font-size:11.5px; padding:4px 22px 20px; }
  .btn { border:1px solid var(--line); background:var(--panel); color:var(--ink);
    border-radius:8px; padding:9px 16px; cursor:pointer; font-size:13px; }
  .btn:hover { border-color:var(--blue); }
  .btn.primary { background:var(--blue); border-color:var(--blue); color:#fff; }
  .btn:disabled { opacity:.5; cursor:not-allowed; }
  /* 只读状态 pill 与可点按钮外观必须区分开：用户不该靠"点一下试试"来分辨 */
  .pill.btn { cursor:pointer; border:1px solid var(--blue); color:var(--blue);
    background:transparent; }
  .pill.btn:hover { background:color-mix(in srgb,var(--blue) 10%,transparent); }
  /* 倒计时每秒改文本宽度，会让整行持续抖动 —— 等宽数字 + 固定宽度稳住 */
  #countdown { min-width:78px; text-align:center; font-variant-numeric:tabular-nums; }
  .composer textarea, .composer select, .composer input {
    width:100%; margin:6px 0; padding:8px 10px; border:1px solid var(--line);
    border-radius:8px; background:var(--bg); color:var(--ink); font-size:13px;
    font-family:inherit; }
  .composer textarea { resize:vertical; }
  .composer .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .composer select { width:auto; min-width:200px; }
  .composer input[type=text], .composer #directive-name { width:160px; }
  .dir { border-left:4px solid var(--blue); }
  .dir .bubble { white-space:pre-wrap; margin:5px 0; }
  .reply-box { background:var(--chip); border-radius:8px; padding:7px 10px;
    margin-top:6px; white-space:pre-wrap; }
  .reply-box.pending { color:var(--muted); font-style:italic; }
  .mini { font-size:11.5px; padding:6px 12px; min-height:30px; border-radius:6px;
    cursor:pointer; border:1px solid var(--line); background:var(--panel);
    color:var(--muted); }
  .mini:hover { border-color:var(--blue); color:var(--blue); }
  /* 触摸目标：视觉尺寸不变，用 padding 把命中区撑到接近 44px */
  @media (pointer:coarse) {
    .mini { min-height:40px; padding:9px 14px; }
    .btn { min-height:44px; }
    label.chk { padding:8px 0; }
  }
</style>
</head>
<body>
<header>
  <h1>本机协作看板</h1>
  <div class="root" id="root">读取中…</div>
  <div class="topbar">
    <span class="pill" id="mode">…</span>
    <span class="pill" id="updated"></span>
    <span class="pill" id="conn"></span>
    <span class="pill" id="countdown"></span>
    <button class="pill btn" id="notify-btn" type="button">🔔 开启回复通知</button>
  </div>
</header>
<main class="wrap" id="main">
  <div id="banner"></div>
  <!-- 下指令是低频动作，监控是高频动作 —— 默认折起，别让它霸占首屏。
       初始 HTML 就带 collapsed：否则首帧到 /data 返回之间会闪一次展开的表单 -->
  <section class="composer collapsed" id="directive-box">
    <h2>给 agent 下指令 <span class="count" id="directive-hint"></span>
      <button class="mini" id="composer-toggle" type="button"
        aria-expanded="false" aria-controls="directive-form">＋ 下发指令</button>
    </h2>
    <form id="directive-form">
      <!-- 正文放最前：原来的顺序是「选目标 → 填称呼 → 点发送 → 再写指令」，
           先点发送只会得到一句报错 -->
      <textarea id="directive-body" maxlength="4000" rows="3"
        aria-label="指令正文" aria-describedby="directive-status"
        placeholder="例：先暂停重构，优先修 main 上的登录 bug；或：跑一遍全量测试把结果告诉我"></textarea>
      <!-- 状态/报错紧贴输入区，不再飘到 90px 之外 -->
      <div class="meta" id="directive-status" role="status" aria-live="polite"></div>
      <div class="row">
        <select id="directive-to" aria-label="目标会话"></select>
        <input id="directive-name" type="text" maxlength="20" aria-label="你的称呼（可选）" placeholder="你的称呼（可选）">
        <button class="btn primary" id="directive-send" type="submit">发送指令</button>
      </div>
      <div class="meta">指令会写进 .agents/inbox/，agent 下个阶段检查点就能看到；agent 空闲时用「复制提醒」唤醒它。</div>
    </form>
  </section>
  <div class="cards" id="cards"></div>
  <div class="grid">
    <div>
      <section><h2>会话 <span class="count" id="s-count"></span></h2><div id="sessions"></div></section>
    </div>
    <div>
      <section><h2>指令与回复 <span class="count" id="q-count"></span></h2><div id="inbox"></div></section>
      <section><h2>worktree <span class="count" id="w-count"></span></h2><div id="worktrees"></div></section>
      <section id="pr-section"><h2>open PR
        <label class="chk" id="pr-toggle"><input type="checkbox" id="pr-cb"> 实时模式附带（调 gh）</label>
      </h2><div id="prs"></div></section>
      <section><h2>worklog 最近</h2><div id="worklogs"></div></section>
    </div>
  </div>
</main>
<footer>看板数据只读本机文件生成；「发送指令」只向 .agents/inbox/ 写入问事板文件。
  实时模式默认仅监听 127.0.0.1；对外分享需只读模式或密码。agent 继续使用
  <code>--json</code> 与终端模式，本页面不影响其行为。</footer>
<p class="sr-only" id="live" role="status" aria-live="polite"></p>
<script id="board-data" type="application/json">__DATA__</script>
<script>
"use strict";
const LIVE = __LIVE__;
const REFRESH_SEC = __REFRESH__;
let wantPrs = false, cd = REFRESH_SEC;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function ageText(s) {
  if (!s.heartbeat) return "无有效心跳";
  const m = Math.max(0, Math.round(s.age_min || 0));
  return m < 1 ? "刚刚" : m + " 分钟前";
}
function shortTime(iso) { return iso ? iso.replace("T", " ").slice(0, 16) : "—"; }

function statCards(d, n) {
  const active = d.sessions.filter(s => !s.closed && !s.stale).length;
  const staleNow = d.sessions.filter(s => s.stale && s.fresh).length;
  const done = d.sessions.filter(s => s.closed).length;
  /* 颜色必须跟着数值走：0 个活跃不该是绿色（"没人干活"至少不是件绿色的好事），
     原来 ["活跃", active, "green"] 把颜色写死，颜色在替用户做错误判断 ——
     这跟本套件主打的「判活三信号交叉验证」正好自相矛盾 */
  const cards = [
    ["活跃", active, active ? "green" : "gray"],
    ["失活", staleNow, staleNow ? "red" : "gray"],
    ["已结束", done, "gray"], ["未答问题", d.inbox_open.length,
      d.inbox_open.length ? "amber" : "gray"],
    ["worktree", d.worktrees.length, ""],
    ["PR", d.open_prs ? d.open_prs.length : "—", ""]
  ];
  const box = el("div", "cards");
  for (const [lbl, num, c] of cards) {
    const card = el("div", "card");
    card.appendChild(el("div", "num " + c, String(num)));
    card.appendChild(el("div", "lbl", lbl));
    box.appendChild(card);
  }
  n.cards.replaceWith(box); box.id = "cards";
  return { active, staleNow, done };
}

/* 心跳色按真实距今时间分级：绿=刚更新、常规=正常、琥珀=偏久、红=失活、
   灰=已结束。原来只判断「有没有心跳 / 是否失活」，收工的会话照样是健康的绿色 */
function ageClass(s) {
  if (s.closed) return "age-done";
  if (!s.heartbeat || s.stale) return "age-bad";
  const m = s.age_min || 0;
  if (m <= 2) return "age-ok";
  return m <= 10 ? "" : "age-warn";
}

function sessionNode(s) {
  const cls = s.closed ? "done" : (s.stale ? "stale" : "active");
  const node = el("div", "session " + cls);
  const head = el("div", "s-head");
  head.appendChild(el("span", "dot " + cls));
  head.appendChild(el("span", "sid", s.id));
  head.appendChild(el("span", "tag", s.agent || "?"));
  head.appendChild(el("span", "tag branch", s.branch || "?"));
  if (s.blocked) head.appendChild(el("span", "tag blocked", "blocked"));
  if (s.worktree) head.appendChild(el("span", "tag wt", "wt: " + s.worktree));
  node.appendChild(head);
  if (s.task) node.appendChild(el("div", "task", s.task));
  if (s.files && s.files.length) node.appendChild(el("div", "meta", "文件范围：" + s.files.join("、")));
  if (s.progress) node.appendChild(el("div", "meta", "进度：" + s.progress));
  if (s.handoff) node.appendChild(el("div", "meta", "交接：" + s.handoff));
  const meta = el("div", "meta");
  meta.appendChild(el("span", "", "状态 " + s.status + "　·　心跳 "));
  meta.appendChild(el("span", ageClass(s), ageText(s)));
  if (s.pr && /^https?:\/\//.test(s.pr)) {
    const a = el("a", "", "PR ↗"); a.href = s.pr; a.target = "_blank";
    a.rel = "noopener"; a.dataset.fkey = "pr:" + s.id;
    meta.appendChild(el("span", "", "　·　")); meta.appendChild(a);
  }
  node.appendChild(meta);
  return node;
}

function reminderText(q) {
  return "请读 .agents/inbox/" + q.file + " 并执行（人类指令）";
}

function copyText(txt, btn) {
  const done = () => {
    const old = btn.textContent; btn.textContent = "已复制 ✓";
    setTimeout(() => { btn.textContent = old; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done,
      () => { window.prompt("复制这句话：", txt); done(); });
  } else { window.prompt("复制这句话：", txt); done(); }
}

function directiveNode(q) {
  const human = q.from_human;
  const node = el("div", "q dir");
  const head = el("div", "s-head");
  head.appendChild(el("span", "tag", human ? "🧑 人类" : q["from"]));
  head.appendChild(el("span", "tag", "→ " + q.to));
  /* 「回没回」是这张卡最该一眼看到的信息，单独给色（原来和普通标签同色） */
  head.appendChild(el("span", "tag " + (q.open ? "pending" : "replied"),
    q.open ? "待处理" : "已回复"));
  head.appendChild(el("span", "tag sid", q.file));
  head.appendChild(el("span", "tag", q.date || ""));
  const copyBtn = el("button", "mini", "复制提醒");
  copyBtn.type = "button";
  copyBtn.dataset.fkey = "remind:" + q.file;
  copyBtn.addEventListener("click", () => copyText(reminderText(q), copyBtn));
  head.appendChild(copyBtn);
  node.appendChild(head);
  const body = (q.body || "").replace(/^##\s*指令\s*/, "").trim() || q.file;
  node.appendChild(el("div", "bubble", body));
  if (q.reply) {
    node.appendChild(el("div", "reply-box", "💬 " + q.reply));
  } else if (q.open) {
    node.appendChild(el("div", "reply-box pending",
      "⏳ 等待 agent 回复（它会在阶段检查点看到；空闲时点「复制提醒」粘进对话框唤醒）"));
  }
  return node;
}

function fillTargets(d) {
  const sel = document.getElementById("directive-to");
  const prev = sel.value;
  sel.replaceChildren();
  const optAny = el("option");
  optAny.value = "any"; optAny.textContent = "所有 agent（广播）";
  sel.appendChild(optAny);
  d.sessions.filter(s => !s.closed).forEach(s => {
    const o = el("option");
    o.value = s.id;
    o.textContent = s.id + " · " + (s.branch || "?");
    sel.appendChild(o);
  });
  if (prev) sel.value = prev;
}

/* 下指令是低频动作，监控是高频动作：默认折起，别让 216px 的表单霸占首屏 */
let composerOpen = false;

function applyComposerState() {
  const box = document.getElementById("directive-box");
  const btn = document.getElementById("composer-toggle");
  box.classList.toggle("collapsed", !composerOpen);
  if (btn) {
    btn.setAttribute("aria-expanded", composerOpen ? "true" : "false");
    btn.textContent = composerOpen ? "收起" : "＋ 下发指令";
  }
}

function setComposerOpen(open) {
  composerOpen = open;
  applyComposerState();
  if (open) {
    const ta = document.getElementById("directive-body");
    if (ta && !ta.disabled) ta.focus();
  }
}

function renderComposer(d) {
  const box = document.getElementById("directive-box");
  const toggle = document.getElementById("composer-toggle");
  const form = document.getElementById("directive-form");
  if (!LIVE) {
    /* 快照模式：不再留一张灰掉的假表单，直接换成一句可照做的说明 */
    box.style.display = "";
    box.classList.remove("collapsed");
    if (toggle) toggle.style.display = "none";
    form.style.display = "none";
    document.getElementById("directive-hint").textContent = "快照模式";
    document.getElementById("directive-status").textContent =
      "这是单文件快照，不能发送指令。要下指令请在本机运行：python tools/agents_status.py --web";
    return;
  }
  fillTargets(d);
  if (d.directives_enabled) {
    box.style.display = "";
    form.style.display = "";
    if (toggle) toggle.style.display = "";
    document.getElementById("directive-send").disabled = false;
    document.getElementById("directive-body").disabled = false;
    document.getElementById("directive-to").disabled = false;
    applyComposerState();
  } else {
    box.style.display = "none";
  }
}

/* 每次刷新都会 replaceChildren 重建容器，落在里面的焦点会被整体抹掉 ——
   键盘/读屏用户每 5 秒被弹回页面根部，实际等于不可用。刷新前记住焦点元素
   的稳定键（data-fkey），刷新后找回同一个元素把焦点还回去。 */
function focusKeyOf(node) {
  if (!node || node === document.body || !node.closest) return null;
  const hit = node.closest("[data-fkey]");
  return hit ? hit.dataset.fkey : null;
}
function restoreFocus(key) {
  if (!key) return;
  const sel = (window.CSS && CSS.escape) ? CSS.escape(key) : key;
  const next = document.querySelector('[data-fkey="' + sel + '"]');
  if (next && typeof next.focus === "function") next.focus();
}

let lastSummary = "";

function render(d) {
  const keepFocus = focusKeyOf(document.activeElement);
  document.getElementById("root").textContent = d.root +
    "（" + d.n_roots + " 个工作区的 .agents/）";
  document.getElementById("updated").textContent = "更新 " + shortTime(d.now);
  document.getElementById("mode").textContent = LIVE ? "● 实时（每 " +
    REFRESH_SEC + " 秒刷新）" : "快照（不自动刷新）";
  document.getElementById("mode").className = "pill " + (LIVE ? "live" : "snap");

  const staleRecent = d.sessions.filter(s => s.stale && s.fresh);
  const staleOld = d.sessions.filter(s => s.stale && !s.fresh);
  const alerts = [];
  d.warnings.forEach(w => alerts.push(w));
  staleRecent.forEach(s => alerts.push("失活会话 " + s.id + "（" + s.branch +
    "，" + ageText(s) + "）"));
  if (d.inbox_open.length) alerts.push(d.inbox_open.length + " 条未答问题");
  const banner = el("div", "banner " + (alerts.length ? "bad" : "ok"));
  banner.textContent = alerts.length ? "⚠ 需要注意（" + alerts.length + "）" : "✅ 一切正常";
  if (alerts.length) {
    const ul = el("ul");
    alerts.forEach(a => ul.appendChild(el("li", "", a)));
    banner.appendChild(ul);
  }
  document.getElementById("banner").replaceChildren(banner);

  const counts = statCards(d, { cards: document.getElementById("cards") });
  document.getElementById("s-count").textContent = d.sessions.length + " 个";
  const sw = el("div");
  d.sessions.forEach(s => sw.appendChild(sessionNode(s)));
  if (!d.sessions.length) sw.appendChild(el("div", "empty", "暂无会话文件"));
  document.getElementById("sessions").replaceChildren(sw);

  const iw = el("div");
  const allQ = (d.inbox_open || []).concat(d.inbox_open_old || [])
    .concat(d.inbox_replied || []);
  allQ.forEach(q => iw.appendChild(directiveNode(q)));
  if (!allQ.length) iw.appendChild(el("div", "empty", "暂无指令/问题"));
  document.getElementById("inbox").replaceChildren(iw);
  document.getElementById("q-count").textContent =
    (d.inbox_open ? d.inbox_open.length : 0) + " 待处理 / 共 " + d.inbox_total;

  const reg = new Set(d.sessions.filter(s => s.fresh).map(
    s => (s.branch || "").split("（")[0].split("(")[0].trim()));
  const ww = el("div");
  d.worktrees.forEach(w => {
    const unreg = !reg.has(w.branch || "?");
    const node = el("div", "wt" + (unreg ? " unreg" : ""));
    node.appendChild(el("div", "", (unreg ? "⚠ 未注册 " : "✓ 已注册 ") +
      (w.branch || "?") + " @ " + (w.head || "?")));
    node.appendChild(el("div", "wtpath", w.path));
    ww.appendChild(node);
  });
  if (!d.worktrees.length) ww.appendChild(el("div", "empty", "无 worktree 信息"));
  document.getElementById("worktrees").replaceChildren(ww);
  document.getElementById("w-count").textContent = d.worktrees.length + " 个";

  const pw = el("div");
  if (d.open_prs === null) {
    pw.appendChild(el("div", "empty", LIVE
      ? "勾选右上角可附带 open PR（只读调 gh）" : "生成时未加 --prs"));
  } else if (!d.open_prs.length) {
    pw.appendChild(el("div", "empty", "没有 open PR（或 gh 不可用）"));
  } else {
    d.open_prs.forEach(pr => {
      const node = el("div", "pr", "#" + pr.number + " [" + pr.branch + "] " +
        pr.title + "（" + pr.author + "，" + pr.updated + "）");
      pw.appendChild(node);
    });
  }
  document.getElementById("prs").replaceChildren(pw);
  document.getElementById("pr-toggle").style.display = LIVE ? "" : "none";
  /* 快照模式没有事件监听，留着按钮就是「点了没反应」的死控件 */
  document.getElementById("notify-btn").style.display = LIVE ? "" : "none";

  const lw = el("div");
  d.worklogs.slice(0, 3).forEach(lg => {
    const node = el("div", "logitem");
    node.appendChild(el("div", "logfile", lg.file + "（" + lg.count + " 段）"));
    (lg.sections || []).forEach(h => node.appendChild(el("div", "logsec", h)));
    lw.appendChild(node);
  });
  if (!d.worklogs.length) lw.appendChild(el("div", "empty", "暂无工作日志"));
  document.getElementById("worklogs").replaceChildren(lw);

  if (staleOld.length) {
    const hint = el("div", "empty", "另有 " + staleOld.length +
      " 个超 24 小时的失活会话（只提示，不卡门禁）");
    document.getElementById("sessions").appendChild(hint);
  }
  /* 只在数量真的变化时播报一句，避免每 5 秒刷屏 */
  const summary = "活跃 " + counts.active + "，失活 " + counts.staleNow +
    "，已结束 " + counts.done + "，未答问题 " + d.inbox_open.length;
  if (summary !== lastSummary) {
    lastSummary = summary;
    const lv = document.getElementById("live");
    if (lv) lv.textContent = summary;
  }
  renderComposer(d);
  restoreFocus(keepFocus);
  notifyAnswered(d);
}

function setConn(ok) {
  const c = document.getElementById("conn");
  if (typeof ok === "string") { c.textContent = ok; c.className = "pill off"; return; }
  c.textContent = ok ? "连接正常" : "已断开（重试中）";
  c.className = "pill " + (ok ? "live" : "off");
}

// 鉴权（仅在服务端设了密码时需要；用户名任意，令牌只存内存）
let authHeader = null;
let authCancelled = false;   // 用户取消密码框后停止自动轮询，避免每 5 秒弹一次

async function tick() {
  if (authCancelled) return;
  cd = REFRESH_SEC;
  try {
    const r = await fetch("data?prs=" + (wantPrs ? 1 : 0), {
      cache: "no-store", headers: authHeader || {}
    });
    if (r.status === 401) {
      askPassword();
      setConn(authCancelled ? "需要密码（点发送指令可重输，或刷新页面）" : false);
      return;
    }
    if (!r.ok) throw new Error("bad status");
    render(await r.json());
    setConn(true);
  } catch (e) { setConn(false); }
}

function askPassword() {
  const pw = window.prompt("看板需要访问密码（用户名可随便填，这里只输密码）：");
  if (pw) {
    authHeader = { "Authorization": "Basic " + btoa("board:" + pw) };
    authCancelled = false;
    tick();
  } else {
    authCancelled = true;
  }
}

function setStatus(txt, bad) {
  const s = document.getElementById("directive-status");
  s.textContent = txt;
  s.style.color = bad ? "var(--red)" : "";
  /* 报错要让读屏也能听到，并把输入框标记为无效 */
  const ta = document.getElementById("directive-body");
  if (ta) {
    if (bad) ta.setAttribute("aria-invalid", "true");
    else ta.removeAttribute("aria-invalid");
  }
}

async function sendDirective(ev) {
  ev.preventDefault();
  if (authCancelled) askPassword();   // 之前取消过密码：发指令时给一次重输机会
  if (authCancelled) { setStatus("需要密码才能发送（刷新页面可重输）", true); return; }
  const to = document.getElementById("directive-to").value;
  const body = document.getElementById("directive-body").value.trim();
  const from_name = document.getElementById("directive-name").value.trim();
  if (!body) { setStatus("指令正文不能为空", true); return; }
  const btn = document.getElementById("directive-send");
  btn.disabled = true;
  setStatus("发送中…");
  try {
    const r = await fetch("directive", {
      method: "POST",
      headers: Object.assign({
        "Content-Type": "application/json",
        "X-Mac-Board-Action": "1"
      }, authHeader || {}),
      body: JSON.stringify({ to, body, from_name })
    });
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { askPassword(); setStatus("已更新密码，再发一次", true); }
    else if (r.ok) {
      document.getElementById("directive-body").value = "";
      setStatus("已写入 " + data.file + "，agent 下个检查点可见 ✔");
      tick();
    } else {
      setStatus("发送失败：" + (data.error || r.status), true);
    }
  } catch (e) {
    setStatus("发送失败：" + e, true);
  } finally {
    btn.disabled = false;
  }
}

// 桌面通知：只对「上一轮还在待处理、这一轮变成已回复」的跳变弹一次
const prevOpen = new Set();
let notifyBaseline = false;
let notifyOn = false;
function notifyAnswered(d) {
  const items = (d.inbox_open || []).concat(d.inbox_open_old || [])
    .concat(d.inbox_replied || []);
  const openNow = new Set(items.filter(q => q.open).map(q => q.file));
  if (notifyOn && notifyBaseline) {
    for (const q of items) {
      if (!q.open && prevOpen.has(q.file) && Notification.permission === "granted") {
        new Notification("agent 已回复：" + q.file,
                         { body: (q.reply || "已回复").slice(0, 120) });
      }
    }
  }
  prevOpen.clear();
  openNow.forEach(f => prevOpen.add(f));
  notifyBaseline = true;
}

if (LIVE) {
  document.getElementById("pr-cb").addEventListener("change", function () {
    wantPrs = this.checked; tick();
  });
  document.getElementById("directive-form").addEventListener("submit", sendDirective);
  document.getElementById("composer-toggle").addEventListener("click", function () {
    const collapsed = document.getElementById("directive-box").classList.contains("collapsed");
    setComposerOpen(collapsed);
  });
  document.getElementById("notify-btn").addEventListener("click", function () {
    if (!("Notification" in window)) { this.textContent = "浏览器不支持通知"; return; }
    if (Notification.permission === "granted") {
      notifyOn = !notifyOn;
      this.textContent = notifyOn ? "🔔 回复通知：开" : "🔔 开启回复通知";
    } else {
      Notification.requestPermission().then(p => {
        notifyOn = p === "granted";
        this.textContent = notifyOn ? "🔔 回复通知：开" : "🔔 通知被拒绝";
      });
    }
  });
  setInterval(function () {
    cd -= 1;
    document.getElementById("countdown").textContent = cd + "s 后刷新";
    if (cd <= 0) tick();
  }, 1000);
  tick();
} else {
  try {
    render(JSON.parse(document.getElementById("board-data").textContent));
  } catch (e) {
    document.getElementById("banner").textContent = "快照数据解析失败：" + e;
  }
}
</script>
</body>
</html>
"""


def _embed_json(payload: dict | None) -> str:
    r"""把数据嵌进 <script type=application/json>：只转义 < 与 U+2028/2029。

    不能动反斜杠——JSON 里的 Windows 路径已经是 `\\`，再翻倍会解析出双反斜杠。
    替换 `<` 为 \u003c 后，数据里不可能出现 `</script>` 截断（JSON 语义不变）。
    """
    raw = json.dumps(payload, ensure_ascii=False)
    return (raw.replace("<", "\\u003c")
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_html(payload: dict | None, live: bool) -> str:
    """渲染自包含 HTML 看板。live=True 给 --web（JS 轮询 /data）；
    False 给 --html 快照（数据内嵌、离线可开）。"""
    return (_DASHBOARD_HTML
            .replace(".agents/", state_dir(ROOT) + "/")
            .replace("python tools/agents_", "python " + (state_dir(ROOT) + "/" if state_dir(ROOT) != ".agents" else "") + "tools/agents_")
            .replace("__DATA__", _embed_json(payload))
            .replace("__LIVE__", "true" if live else "false")
            .replace("__REFRESH__", str(WEB_REFRESH_SEC)))


def _write_lf_atomic(path: Path, content: str) -> None:
    """LF + UTF-8 原子写（与安装器 write_lf 同法；看板独立分发，不能 import 安装器）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _BoardServer(ThreadingHTTPServer):
    daemon_threads = True      # 中断主进程时请求线程不拖住退出


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _audit(msg: str) -> None:
    """服务端审计日志：任何控制台编码下都不许崩。

    CI 英文 Windows 的 stdout 是 cp1252，连「[指令]」都编码不了；
    handler 一崩，客户端只看到连接被断（RemoteDisconnected），指令其实已落盘。
    主进程 main() 会 reconfigure stdout，但测试/被 import 的进程没有这层保护。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            print(msg.encode(enc, "replace").decode(enc), flush=True)
        except Exception:
            pass


def make_server(root: Path, port: int = 0, stale_min: int = STALE_MIN_DEFAULT,
                with_prs: bool = False, bind: str = "127.0.0.1",
                password: str | None = None,
                directives: bool = True) -> _BoardServer:
    """提供看板页面、只读 /data 与 POST /directive。

    - bind：127.0.0.1（默认，只本机）或 0.0.0.0（对局域网开放）
    - password：非空时所有请求走 HTTP Basic（用户名任意，用 board 即可）
    - directives=False：只读分享，POST 一律 403
    调用方应先用 check_serve_options() 校验组合。
    """
    global ROOT
    ROOT = Path(root).resolve()
    auth_fails: dict[str, list] = {}   # ip -> 失败时间戳列表（进程内存，重启即清零）

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass    # 安静：不把每次轮询刷进终端

        def _send(self, code: int, body, ctype: str,
                  extra_headers: dict | None = None) -> None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False),
                       "application/json; charset=utf-8")

        # ---- 鉴权 / 防爆破 ----
        def _locked_out(self) -> bool:
            """5 次失败后锁 60 秒；60 秒无新失败自动解除。"""
            ip = self.client_address[0]
            now = time.time()
            fails = [t for t in auth_fails.get(ip, []) if now - t < 300]
            auth_fails[ip] = fails
            return len(fails) >= AUTH_FAIL_LIMIT and now - fails[-1] < AUTH_LOCK_SEC

        def _record_fail(self) -> None:
            auth_fails.setdefault(self.client_address[0], []).append(time.time())

        def _authed(self) -> bool:
            if not password:
                return True
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(hdr[6:].strip(), validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                return False
            given = decoded.split(":", 1)[1] if ":" in decoded else ""
            return hmac.compare_digest(given, password)   # 防时序攻击

        def _gate_auth(self) -> bool:
            if self._locked_out():
                self._json(429, {"error": f"密码错误过多，请 {AUTH_LOCK_SEC} 秒后再试"})
                return False
            if not self._authed():
                self._record_fail()
                self._send(401, "auth required", "text/plain; charset=utf-8",
                           {"WWW-Authenticate": 'Basic realm="multi-agent-board"'})
                return False
            return True

        def _same_origin(self) -> bool:
            """防伪：自定义头已能挡住跨站表单；再校验 Origin/Referer 的主机与 Host 一致。"""
            src = self.headers.get("Origin") or self.headers.get("Referer") or ""
            if not src:
                return False
            host = self.headers.get("Host", "")
            return urlsplit(src).netloc == host

        def do_GET(self) -> None:    # noqa: N802（http.server 的命名约定）
            if not self._gate_auth():
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, render_html(None, live=True),
                           "text/html; charset=utf-8")
            elif path == "/data":
                qs = parse_qs(urlsplit(self.path).query)
                want_prs = qs.get("prs", ["0"])[0] == "1"
                try:
                    payload = collect(datetime.now(), want_prs, stale_min)
                    payload["directives_enabled"] = directives
                except Exception as e:   # 看板进程不该因为一次坏数据就 500 整页挂
                    self._json(200, {"error": f"数据收集失败：{e}"})
                    return
                self._send(200, json.dumps(payload, ensure_ascii=False),
                           "application/json; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:     # noqa: N802
            if not self._gate_auth():
                return
            # 先把请求体读干净，再走任何校验分支。
            # 反例（曾经的写法）：缺防伪头就直接回 403，请求体还留在接收缓冲里 ——
            # 服务器关连接时 Windows 发的是 RST 而不是 FIN，客户端拿到的是
            # ConnectionAborted(WinError 10053)，我们精心写的 403 根本到不了它手里。
            # 这个竞态在 Linux/macOS 上几乎不复现，只在 Windows CI 上偶发。
            # 放在鉴权之后读：未通过鉴权的请求不消耗服务端读取，不新增阻塞面。
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length < 0 or length > 16384:
                # 声明得离谱：不读，显式断开，别把垃圾读进内存
                self.close_connection = True
                self._json(413, {"error": "请求体过大"})
                return
            raw = self.rfile.read(length) if length else b""

            if urlsplit(self.path).path != "/directive":
                self._json(404, {"error": "未知接口"})
                return
            if not directives:
                self._json(403, {"error": "当前为只读模式（--no-directives）"})
                return
            if self.headers.get(DIRECTIVE_HEADER) != "1" or not self._same_origin():
                self._json(403, {"error": "拒绝：缺少防伪头或来源不一致"})
                return
            if not raw:
                self._json(400, {"error": "请求体为空"})
                return
            try:
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "请求不是合法 JSON"})
                return
            to = str(data.get("to", "any")).strip()
            body = str(data.get("body", ""))
            name = str(data.get("from_name", ""))
            if len(body) > DIRECTIVE_MAX_LEN:
                self._json(413, {"error": f"指令超过 {DIRECTIVE_MAX_LEN} 字"})
                return
            if to != "any":
                # 聚合主工作区与所有 linked worktree 的会话（与看板口径一致）
                alive = {s["id"] for s in
                         load_sessions(agent_roots(), datetime.now(), stale_min)}
                if to not in alive:
                    self._json(400, {"error": f"目标会话不存在：{to}（选 any 或当前会话）"})
                    return
            try:
                dest = create_directive(state_path(ROOT, "inbox"), to, body, name)
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            _audit(f"[指令] {datetime.now():%H:%M:%S} → {to}  {dest.name}"
                   + (f"（{name}）" if name else ""))
            self._json(200, {"ok": True, "file": dest.name})

    return _BoardServer((bind, port), _Handler)


def run_web(port: int, stale_min: int, with_prs: bool, open_browser: bool,
            bind: str, directives: bool, password: str | None) -> int:
    """启动本地实时看板并阻塞到 Ctrl+C。"""
    problem = check_serve_options(bind, directives, password)
    if problem:
        print("[FAIL] " + problem)
        return 2
    httpd = None
    actual = port
    for candidate in [port] + [port + i for i in range(1, 10)]:
        try:
            httpd = make_server(ROOT, port=candidate, stale_min=stale_min,
                                with_prs=with_prs, bind=bind,
                                password=password, directives=directives)
            actual = httpd.server_address[1]
            break
        except OSError:
            continue
    if httpd is None:
        print(f"[FAIL] 端口 {port}–{port + 9} 都被占用，用 --port 指定别的端口")
        return 2

    show_host = "127.0.0.1" if bind in LOOPBACK_BINDS else "本机IP"
    url = f"http://{show_host}:{actual}/"
    print("本机协作看板（人类可视化模式）")
    print(f"  项目根  {ROOT}")
    print(f"  地址    {url}")
    if bind in LOOPBACK_BINDS:
        print("  绑定    仅本机（127.0.0.1），其他设备访问不到")
    else:
        print(f"  绑定    {bind}（同局域网设备可用 http://<本机IP>:{actual}/ 访问）")
        print("  ⚠ HTTP 明文：只在可信 WiFi/VPN 下用；公共网络请改用 SSH 端口转发")
        print("  鉴权    已启用密码（浏览器会弹登录框，用户名任意）"
              if password else "  模式    只读分享（不能发指令）")
    print(f"  指挥    {'允许在网页上对 agent 下指令' if directives else '已关闭（只读）'}"
          f"；每 {WEB_REFRESH_SEC} 秒刷新；Ctrl+C 停止")
    if port == 0:
        print(f"  （系统分配可用端口 {actual}）")
    elif actual != port:
        print(f"  （默认端口 {port} 被占用，已自动改用 {actual}）")
    if open_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{actual}/")
        except Exception:
            print("  （没能自动打开浏览器，请手工复制上面的地址）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n看板已停止。")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


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
    ap.add_argument("--brief", action="store_true",
                    help="开工协调用：只输出一行摘要（无告警 OK，有告警才点名，省 token）")
    ap.add_argument("--prs", action="store_true",
                    help="同时只读查询 open PR（调 gh；gh 不可用时静默降级，不影响退出码）")
    ap.add_argument("--selfcheck", action="store_true", help="运行内置自测")
    ap.add_argument("--version", action="version",
                    version=f"agents_status {BOARD_VERSION}")
    ap.add_argument("--web", action="store_true",
                    help="启动给人看的本地实时看板（浏览器自动打开）")
    ap.add_argument("--port", type=int, default=WEB_PORT_DEFAULT,
                    help=f"--web 端口（默认 {WEB_PORT_DEFAULT}，被占用会自动顺延）")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="--web 绑定地址：127.0.0.1（默认，仅本机）或 0.0.0.0（局域网可访问）")
    ap.add_argument("--no-directives", action="store_true",
                    help="--web 只读分享：网页上不能对 agent 下指令")
    ap.add_argument("--no-open", action="store_true",
                    help="--web 时不要自动打开浏览器")
    ap.add_argument("--html", nargs="?", const=SNAPSHOT_DEFAULT, default=None,
                    help=f"导出自包含 HTML 快照（默认 {SNAPSHOT_DEFAULT}）；可给路径")
    ap.add_argument("--open", action="store_true", dest="open_html",
                    help="--html 导出后自动用浏览器打开")
    args = ap.parse_args(argv)

    if args.selfcheck:
        return selfcheck()

    ROOT = detect_root(args.root)

    if args.web:
        directives = (not args.no_directives) and (not _env_truthy(DIRECTIVE_ENV_READONLY))
        password = os.environ.get(DIRECTIVE_ENV_PASSWORD, "").strip() or None
        return run_web(port=args.port, stale_min=args.stale_min, with_prs=args.prs,
                       open_browser=not args.no_open, bind=args.bind.strip(),
                       directives=directives, password=password)

    payload = collect(datetime.now(), args.prs, args.stale_min)

    if args.html is not None:
        out = state_path(ROOT, "board.html") if args.html == SNAPSHOT_DEFAULT else Path(args.html)
        if not out.is_absolute():
            out = ROOT / out
        _write_lf_atomic(out, render_html(payload, live=False))
        print(f"HTML 快照已生成：{out}（用浏览器直接打开即可；重新运行本命令刷新）",
              file=sys.stderr)
        if args.open_html:
            webbrowser.open(out.as_uri())
        return gate_rc(payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return gate_rc(payload)

    if args.brief:
        print_brief(payload)
        return gate_rc(payload)

    print_human(payload)
    return gate_rc(payload)


def print_brief(p: dict) -> None:
    """开工协调用的一行摘要：没事就 OK；有事单行点名（agent 据此决定要不要展开看板）。"""
    parts = []
    if p["warnings"]:
        parts.append(f"告警{len(p['warnings'])}:" + "；".join(
            w.split("（")[0][:28] for w in p["warnings"][:3]))
    stale = [s for s in p["sessions"] if s["stale"] and s["fresh"]]
    if stale:
        parts.append("失活 " + ",".join(s["id"] for s in stale[:4]))
    if p["inbox_open"]:
        qs = p["inbox_open"]
        parts.append(f"未答{len(qs)}:" + ",".join(q["file"] for q in qs[:4]))
    n_active = sum(1 for s in p["sessions"] if not s["closed"] and not s["stale"])
    if parts:
        print(f"看板:活跃{n_active} | " + " | ".join(parts)
              + "（跑 python tools/agents_status.py 看详情）")
    else:
        print(f"OK 看板无告警：活跃{n_active}，无失活/未答/worktree 异常，可直接开工")


def print_human(p: dict) -> None:
    """终端人类模式（与 JSON/HTML/web 共用 collect 数据，口径一致）。"""
    sessions = p["sessions"]
    fresh_q, old_q, logs, wts = p["inbox_open"], p["inbox_open_old"], p["worklogs"], p["worktrees"]
    warnings, git_available = p["warnings"], p["git_available"]
    active = [s for s in sessions if not s["closed"] and not s["stale"]]
    stale = [s for s in sessions if s["stale"]]
    stale_recent = [s for s in stale if s["fresh"]]
    stale_old = [s for s in stale if not s["fresh"]]
    registered_branches = {
        s["branch"].split("（")[0].split("(")[0].strip()
        for s in sessions if s["fresh"]
    }

    line = "─" * 68
    print(f"\n本机协作看板   {p['now']}   (失活阈值 {p['stale_min']} 分钟)")
    print(f"项目根 {ROOT}（{p['n_roots']} 个工作区的 .agents/）")
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
        if s.get("files"):
            print(f"      文件 {', '.join(s['files'][:8])}")
        if s.get("progress"):
            print(f"      进度 {s['progress'][:100]}")
    print("  (● 活跃   ! 失活   ✓ 已结束)")

    # ---- 未答问题 ----
    print(f"\n【inbox】未答 {len(fresh_q)}（另有 {len(old_q)} 个超过 {OLD_INBOX_DAYS} 天的旧问题）"
          f" / 共 {p['inbox_total']}")
    for q in fresh_q:
        print(f"  ? {q['file']}   to={q['to']}  from={q['from']}  ({q['date']})")
    if not fresh_q and p["inbox_total"]:
        print("  （没有需要近期处理的问题）")

    # ---- 工作日志 ----
    print("\n【worklog】最近")
    for lg in logs:
        print(f"  {lg['file']}  {lg['count']} 段")
        for h in lg["sections"]:
            print(f"      {h[:72]}")

    # ---- worktree ----
    print(f"\n【worktree】{len(wts)} 个")
    root_norm = _norm(str(ROOT))
    for w in wts:
        is_main = _norm(w["path"]) == root_norm
        tag = "当前" if is_main else "linked"
        reg = "已注册" if w["branch"] in registered_branches else "未注册"
        print(f"  [{tag}/{reg}] {w['branch']} @ {w['head']}")
        print(f"      {w['path']}")

    # ---- open PR（仅 --prs；开着的 PR 也是"活跃会话"，见 AGENTS.md 第 3 节）----
    if p["prs_requested"]:
        print(f"\n【open PR】")
        if p["open_prs"] is None:
            print("  ℹ️  gh 不可用/未登录/超时 —— 请手工跑 `gh pr list --state open` 后再开工")
        elif not p["open_prs"]:
            print("  （没有 open PR）")
        else:
            for pr in p["open_prs"]:
                print(f"  #{pr['number']:<4} [{pr['branch']}] {pr['title'][:60]}"
                      f"  ({pr['author']}, 更新 {pr['updated']})")

    # ---- git 状态 ----
    print("\n【git】")
    if not git_available:
        print(f"  ⚠️ git 不可用（PATH 与常见安装路径都没找到；可用环境变量 {GIT_ENV_VAR} 指定）")
        print("     下列 git 信息已降级，worktree 盲区防护也不生效 —— 修好 git 后再开工")
    else:
        print(f"  当前分支 {_git('branch', '--show-current').strip() or '?'}"
              f" @ {_git('rev-parse', '--short', 'HEAD').strip() or '?'}")
        dirty = _git("status", "--short").splitlines()
        print(f"  工作区改动 {len(dirty)} 项")
        loose = _git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
        print(f"  本地分支 {len(loose)} 个")
        print("  ⚠️ 本脚本只读**本地** remote-tracking 引用，而它可能被沙箱静默回滚（B8/B10），"
              "\n     所以这里不报 ahead/behind —— 要真相请用 `gh api` 或 `git ls-remote`。")

    # ---- 提示人类用户：可视化模式入口 ----
    print("\n  💡 给人看的图形化看板：python tools/agents_status.py --web"
          "（实时刷新） 或 --html（单文件快照）")

    # ---- 告警汇总 ----
    # 失活：只报 24h 内仍"在值"的（无心跳时看文件 mtime）；更老的是历史记录，只做提示
    print(f"\n{line}")
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
    if stale_old:
        print(f"  ℹ️  {len(stale_old)} 个超过 24 小时的失活会话（含坏心跳的旧文件）"
              f"只提示、不卡门禁，记得置 done 或清理")
    if old_q:
        print(f"  ℹ️  {len(old_q)} 条超过 {OLD_INBOX_DAYS} 天的旧 open 问题，"
              f"记得 answered/closed 掉")
    if not (warnings or stale_recent or fresh_q or stale_old or old_q):
        print("  ✅ 无告警")
    print()


if __name__ == "__main__":
    sys.exit(main())
