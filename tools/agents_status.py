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
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
# 默认项目根：main() 里通过 detect_root() 自动探测（显式 --root > cwd > git 顶层）
ROOT = Path.cwd()

STALE_MIN_DEFAULT = 15
WORKTREE_WARN_MIN = 30
OLD_INBOX_DAYS = 7          # 旧普通问题可只提示；未答人类指令始终阻断
FUTURE_HB_TOL_MIN = 10      # 心跳比当前时刻快超过这么多 → 视为无效（时钟错乱/手滑）

# 版本必须与 install.py 的 KIT_VERSION 保持一致（有测试锁定，防两边漂移）。
# 看板独立分发到目标项目，不能 import 安装器，所以各存一份常量。
BOARD_VERSION = "1.8.0"

# 人类可视化模式（--web / --html）：纯本机，不影响 JSON/终端模式与 agent 用法
WEB_PORT_DEFAULT = 8731
WEB_REFRESH_SEC = 5
from agents_layout import state_path, state_dir, prefer_native_git

SNAPSHOT_DEFAULT = ".agents/board.html"

# 人类指令通道（网页 → .agents/inbox/）
DIRECTIVE_MAX_LEN = 4000
DIRECTIVE_ENV_PASSWORD = "MULTI_AGENT_BOARD_PASSWORD"
DIRECTIVE_ENV_READONLY = "MULTI_AGENT_BOARD_READONLY"
LOOPBACK_BINDS = {"127.0.0.1", "localhost", "::1"}
DIRECTIVE_HEADER = "X-Mac-Board-Action"   # 跨站表单伪造不出这个头（会触发 CORS 预检）
DIRECTIVE_ACTIONS = {"message", "pause", "cancel", "avoid"}
AUTH_FAIL_LIMIT = 5                        # 同一 IP 连续密码错误上限
AUTH_LOCK_SEC = 60
REQUEST_TIMEOUT_SEC = 10
CONTEXT_MAX_CHARS = 6000
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,100}$")
_UNIQUE_METADATA_KEYS = {
    "agent", "branch", "status", "files", "started", "heartbeat", "last_activity", "last_command", "running_jobs", "workspace",
    "from", "to", "created", "date", "action", "acknowledged_inbox",
    "acknowledged_inbox_versions", "schema_version",
}

# 精确匹配的「已结束」状态词（取状态首词，小写）。
# 注意：blocked 不算结束 —— 「被卡住」恰恰需要别人看见并接手。
CLOSED_WORDS = {
    "done", "superseded", "closed", "abandoned", "cancelled", "canceled",
    "完成", "已完成", "已关闭", "已取消", "废弃",
}
SESSION_STATUS_WORDS = CLOSED_WORDS | {"active", "blocked", "standby"}
# inbox 的关闭态：会话关闭词之外，"已回复"也算关闭
INBOX_CLOSED_WORDS = CLOSED_WORDS | {"answered"}
CONTROL_RELEASE_WORDS = {"closed", "已关闭", "superseded"}


def inbox_is_pending(status: str, action: str = "message", from_human: bool = False) -> bool:
    """Replying to a control does not revoke its effect on the recipient's work."""
    status = status_word(status)
    if from_human and action in {"pause", "cancel", "avoid"}:
        return status not in CONTROL_RELEASE_WORDS
    return status not in INBOX_CLOSED_WORDS

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
    _git_bin = prefer_native_git(found)
    return _git_bin


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

    用于区分近期活动和历史记录；失活记录的占用与门禁不因时间自动解除。
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


def parse_kv_text(text: str) -> dict:
    """Parse only the metadata block, never fields embedded in a task's body."""
    data: dict[str, str] = {}
    started = False
    for line in text.lstrip("\ufeff").splitlines():
        m = re.match(r"^\s*[-*]\s*\*{0,2}([A-Za-z_][\w -]*?)\*{0,2}\s*[:：]\s*(.*?)\s*$", line)
        if m:
            started = True
            key, value = m.group(1).strip().lower(), m.group(2).strip()
            if key in _UNIQUE_METADATA_KEYS and key in data and data[key] != value:
                raise ValueError(f"顶部关键字段重复且冲突：{key}；请核对原记录，不能据此释放占用或确认指令")
            data.setdefault(key, value)
        elif line.strip() and (started or not line.lstrip().startswith(("# ", "<!--"))):
            break
    return data


def parse_kv(path: Path) -> dict:
    """把 Markdown 顶部的 `- key: value` 列表解析成 dict。"""
    try:
        lines = []
        started = False
        size = 0
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                lines.append(line)
                size += len(line)
                field = bool(re.match(r"^\s*[-*]\s*\*{0,2}[A-Za-z_][\w -]*?\*{0,2}\s*[:：]", line))
                if field:
                    started = True
                elif started and line.strip():
                    break
                elif line.strip() and not line.lstrip("\ufeff \t").startswith(("# ", "<!--")):
                    break
                if size > 65536:
                    return {"_read_error": "metadata exceeds 65536 characters"}
        text = "".join(lines)
    except OSError:
        return {}
    try:
        return parse_kv_text(text)
    except ValueError as exc:
        raise ValueError(f"{path}：{exc}") from None


def parse_time(raw: str) -> datetime | None:
    """Legacy timestamps are local; explicit ISO offsets are converted to local time."""
    if not raw:
        return None
    s = raw.strip().strip("`")
    s = re.sub(r"\s*[（(].*$", "", s)          # 去掉「（心跳…）」之类后缀
    if s.upper().endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        parsed = datetime.fromisoformat(s.strip())
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
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


def session_status(raw: str) -> str:
    """Accept a complete session state, with one optional legacy annotation."""
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw.splitlines() != [raw]:
        return "?"
    match = re.fullmatch(r"\s*([^\s()（）]+)\s*(?:\([^()\r\n]*\)|（[^（）\r\n]*）)?\s*", raw)
    word = match.group(1).lower() if match else "?"
    return word if word in SESSION_STATUS_WORDS else "?"


def validate_session_status(record: dict) -> str:
    state = session_status(record.get("status", ""))
    if state == "?" or record.get("_read_error"):
        raise ValueError("会话状态缺失、无效或元数据损坏；先核对原记录，不能更新或释放占用")
    return state


def _norm(p: str) -> str:
    # Git reports long paths while cwd/TEMP can use Windows 8.3 aliases.
    # Resolve filesystem aliases before deciding whether a worktree is ourselves.
    try:
        p = str(Path(p).resolve())
    except (OSError, RuntimeError):
        pass  # Missing/inaccessible worktrees still get a stable lexical key.
    value = p.replace("\\", "/").rstrip("/")
    return value.lower() if os.name == "nt" else value


def detect_root(explicit: str | None) -> Path:
    """Normalize a project subdirectory to its Git root, even without Git."""
    cwd = Path(explicit).resolve() if explicit else Path.cwd().resolve()
    # Git may be absent or broken. The closest .git marker still identifies
    # which root's coordination records must be read and which error to report.
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            return candidate
    # An explicit non-Git project remains independent of any ancestor's state.
    if explicit:
        return cwd
    if state_path(cwd).is_dir():
        return cwd
    top_raw = _git_at(cwd, "rev-parse", "--show-toplevel").strip()
    if top_raw:
        top = Path(top_raw).resolve()
        if top != cwd:
            rel = str(cwd.relative_to(top)) if top in cwd.parents else str(cwd)
            print(f"提示：当前目录（{rel}）没有 .agents/，已改用仓库根 {top}",
                  file=sys.stderr)
            return top
    for parent in cwd.parents:
        if state_path(parent).is_dir():
            return parent
    return cwd


def parse_worktree_porcelain(raw: str) -> list[dict]:
    """Accept NUL porcelain without stripping valid whitespace from paths."""
    rows: list[dict] = []
    current: dict = {}
    for line in (raw.split("\0") if "\0" in raw else raw.splitlines()):
        if line.startswith("worktree "):
            if current:
                rows.append(current)
            current = {"path": line[9:]}
        elif current and line.startswith("HEAD "):
            current["head"] = line[5:][:7]
        elif current and line.startswith("branch "):
            branch = line[7:]
            current["branch"] = branch[11:] if branch.startswith("refs/heads/") else branch
        elif current and line == "detached":
            current["branch"] = "(detached)"
        elif current and line == "bare":
            current["bare"] = True
    if current:
        rows.append(current)
    for row in rows:
        row.setdefault("branch", "?")
        row.setdefault("head", "?")
    return rows


def agent_roots(root: Path | None = None, rows: list[dict] | None = None) -> list[tuple[Path, bool]]:
    """返回 (worktree 路径, 是否主工作区)，只保留里面真有 .agents/ 的。

    linked worktree 各自独立工作区、各自一份未跟踪的 .agents/，必须逐个扫
    （TROUBLESHOOTING A1：只扫主工作区会让别的 worktree 里的会话彻底隐形）。
    非 git 仓库或没有任何 worktree 输出时，退化为只看 ROOT。
    """
    root = root or ROOT
    if rows is None:
        rows = parse_worktree_porcelain(_git_at(root, "worktree", "list", "--porcelain", "-z"))
    found: list[tuple[Path, bool]] = []
    if not rows:
        return [(root, True)]

    root_norm = _norm(str(root))
    for row in rows:
        p = Path(row["path"])
        is_main = _norm(str(p)) == root_norm
        if (state_path(p)).is_dir():
            found.append((p, is_main))
    # git 认为的主工作区可能不是当前 ROOT（从 linked worktree 里跑看板时，
    # git 的"主"是另一个目录）。此时以 ROOT 为"当前工作区"，其余都算别的 worktree。
    if not any(_norm(str(p)) == root_norm for p, _ in found) and (state_path(root)).is_dir():
        found.insert(0, (root, True))
    if not found:
        found = [(root, True)]
    return found


def parse_claimed_files(raw: str) -> tuple[list[str], bool]:
    if not raw:
        return [], True
    try:
        value = json.loads(raw)
        if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
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


def _checked_state_path(root: Path, path: Path) -> Path:
    """State records may not alias another directory or escape their workspace."""
    try:
        relative = path.absolute().relative_to(root.absolute())
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        raise ValueError(f"协作状态路径越界：{path}") from None
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in (0xA0000003, 0xA000000C):
            raise ValueError(f"协作状态路径含 symlink/junction，停止读取：{cursor}")
    return path


def _checked_state_directory(root: Path, kind: str) -> Path:
    path = _checked_state_path(root, state_path(root, kind))
    if path.exists() and not path.is_dir():
        raise ValueError(f"协作状态目录不是目录：{path}")
    return path


def _state_record_files(root: Path, kind: str, pattern: str) -> list[Path]:
    directory = _checked_state_directory(root, kind)
    if not directory.exists():
        return []
    # iterdir surfaces inaccessible directories instead of silently returning
    # an empty glob and claiming there are no reservations or instructions.
    result = []
    for path in directory.iterdir():
        if path.match(pattern):
            _checked_state_path(root, path)
            if not path.is_file():
                raise ValueError(f"协作记录不是普通文件：{path}")
            result.append(path)
    return sorted(result)


def load_sessions(roots: list[tuple[Path, bool]], now: datetime,
                  stale_min: int) -> list[dict]:
    out: list[dict] = []
    for root, is_main in roots:
        tag = "" if is_main else str(root)
        for f in _state_record_files(root, "sessions", "*.md"):
            if f.name.startswith("_"):          # _template.md
                continue
            kv = parse_kv(f)
            hb = valid_heartbeat(kv.get("heartbeat", ""), now)
            activity_raw = kv.get("last_activity", "")
            last_activity = valid_heartbeat(activity_raw, now)
            if last_activity is not None and (hb is None or last_activity > hb):
                hb = last_activity
            age = (now - hb).total_seconds() / 60 if hb else None
            if age is not None and age < 0:
                age = 0.0
            try:
                ftime = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                ftime = None
            st = session_status(kv.get("status", ""))
            status_error = st == "?" or bool(kv.get("_read_error"))
            terminal = st in CLOSED_WORDS
            closed = terminal
            claimed, claims_error = parse_claimed_files(kv.get("files", ""))
            jobs_count, jobs_error, jobs_blocking = 0, False, False
            if "running_jobs" in kv:
                try:
                    from agents_jobs import jobs_for_record, ensure_no_running_jobs
                    jobs_count = len(jobs_for_record(kv))
                    if terminal and jobs_count:
                        try:
                            ensure_no_running_jobs(kv)
                        except ValueError:
                            jobs_blocking = True
                except (ValueError, TypeError):
                    jobs_error, jobs_blocking = True, True
            terminal_job_conflict = terminal and jobs_blocking
            if terminal_job_conflict:
                closed = False
            standby = False
            if st == "standby" and not kv.get("_read_error"):
                try:
                    empty_claims = json.loads(kv.get("files", ""))
                    standby = isinstance(empty_claims, list) and not empty_claims and not jobs_count and not jobs_error
                except (ValueError, TypeError):
                    pass
                claims_error = not standby
                if standby:
                    claimed = []
            item = {
                "id": f.stem,
                "agent": kv.get("agent", "?"),
                "branch": kv.get("branch", "?"),
                "task": kv.get("task", ""),
                "status": kv.get("status", "?"),
                "status_error": status_error,
                "heartbeat": hb,
                "age_min": age,
                "file_mtime": ftime,
                "closed": closed,
                "blocked": st == "blocked",
                "standby": standby,
                "occupies": not closed and not standby,
                "stale": (not closed) and (age is None or age > stale_min),
                "last_activity": last_activity.isoformat(timespec="seconds") if last_activity else None,
                "last_command": kv.get("last_command", ""),
                "activity_error": bool(activity_raw and last_activity is None),
                "running_jobs_count": jobs_count,
                "jobs_error": jobs_error,
                "terminal_job_conflict": terminal_job_conflict,
                "last_task": kv.get("last_task", ""),
                "task_completed_at": kv.get("task_completed_at", ""),
                "pr": kv.get("pr", ""),
                "file": f.name,
                "path": str(f.resolve()),
                "worktree": tag,
                "workspace": str(root.resolve()),
                "files": claimed,
                "claims_error": claims_error,
                "progress": kv.get("progress", ""),
                "handoff": kv.get("handoff", ""),
            }
            out.append(item)
    counts: dict[str, int] = {}
    for item in out:
        counts[item["id"]] = counts.get(item["id"], 0) + 1
    for item in out:
        item["duplicate_id"] = counts[item["id"]] > 1
    return sorted(out,
                  key=lambda s: (s["heartbeat"] or datetime.min), reverse=True)


def _inbox_body_reply(text: str) -> tuple[str, str]:
    """从问事板文件抽出（正文, 回复）。正文去头部 kv 块；回复取 ## 回复 之后。"""
    reply = ""
    m = re.search(r"^##\s*回复[^\n]*\n(?P<r>[\s\S]*)$", text, re.M)
    if m:
        reply = "\n".join(line for line in m.group("r").splitlines()
                          if not line.strip().startswith(("（待回复", "(待回复"))).strip()
    lines = text.splitlines()
    i = 1 if lines and lines[0].lstrip().startswith("#") else 0
    # 跳过连续的头部 kv 行、HTML 注释与紧随的空行
    while i < len(lines) and (not lines[i].strip()
                              or lines[i].lstrip().startswith("- ")
                              or lines[i].lstrip().startswith("<!--")):
        i += 1
    body_lines = []
    for line in lines[i:]:
        if line.startswith("## 回复") or line.strip() == "---":
            break
        # 跳过「## 指令」「## 背景」这类正文小节标题，只留正文本身
        if line.startswith("## "):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    # 去掉转义时为防结构注入加的行首空格（仅去我们加的那种整行缩进）
    body = "\n".join(line[1:] if line.startswith(" ") and line[1:2] in ("#", "-", ">", "<")
                     else line for line in body.split("\n"))
    return body.strip(), reply


def load_inbox(roots: list[tuple[Path, bool]], lean: bool = False) -> list[dict]:
    items: list[dict] = []
    for root, _is_main in roots:
        for f in _state_record_files(root, "inbox", "Q-*.md"):
            kv = parse_kv(f)
            st = status_word(kv.get("status", "open"))
            text = ""
            action = kv.get("action", "message")
            frm = kv.get("from", "?")
            from_human = frm.casefold().startswith("human")
            pending = inbox_is_pending(st, action, from_human)
            read_error = not bool(kv) or bool(kv.get("_read_error")) or action not in DIRECTIVE_ACTIONS
            if not lean or pending:
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    read_error = True
            # 模板字段叫 created，早期版本看板读 date —— 两个都兼容
            date_raw = kv.get("date") or kv.get("created") or ""
            body, reply = _inbox_body_reply(text)
            items.append({
                "file": f.name,
                "workspace": str(root.resolve()),
                "path": str(f.resolve()),
                "status": st,
                "to": kv.get("to", "any"),
                "from": frm,   # 人类指令形如 "human（小白）"，称呼已含在内
                "from_human": from_human,
                "action": action,
                "date": date_raw,
                "created_dt": parse_time(date_raw),
                "body": body,
                "reply": reply,
                "open": pending,
                "read_error": read_error,
            })
    counts: dict[str, int] = {}
    for item in items:
        counts[item["file"]] = counts.get(item["file"], 0) + 1
    for item in items:
        item["duplicate_file"] = counts[item["file"]] > 1
    return items


# ---- 人类指令：网页写 inbox（复用问事板协议，agent 端无需新格式）----

def _clean_one_line(s: str, limit: int) -> str:
    s = " ".join(s.splitlines())
    s = re.sub(r"[\x00-\x1f\x7f]", "", s).strip()
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
                     from_name: str = "", now: datetime | None = None,
                     action: str = "message", sender: str | None = None,
                     label: str = "人类指令") -> Path:
    """把一条消息写成 Q-YYYY-MM-DD-NN.md（O_EXCL 抢序号，并发也不互撞）。

    ``sender`` 非空时写入真实会话 ID，用于 agent 之间的协作消息；人类看板
    仍然走默认分支，``from`` 固定为 ``human``（含显示名）。
    """
    to = (to or "any").strip()
    if to != "any" and not _TARGET_RE.match(to):
        raise ValueError(f"非法目标：{to!r}")
    if not isinstance(action, str) or action not in DIRECTIVE_ACTIONS:
        raise ValueError("action 必须为 message、pause、cancel 或 avoid")
    body = "\n".join((body or "").splitlines())
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", body).strip()
    if not body:
        raise ValueError("指令正文为空")
    if len(body) > DIRECTIVE_MAX_LEN:
        raise ValueError(f"指令超过 {DIRECTIVE_MAX_LEN} 字")
    body = _neutralize_body(body)
    if sender is not None:
        if (not isinstance(sender, str) or not _TARGET_RE.fullmatch(sender)
                or sender.casefold().startswith("human")):
            raise ValueError("发送者必须是合法会话 ID")
        if action != "message":
            raise ValueError("agent 只能发送普通协作消息")
        who = sender
    else:
        display = _clean_one_line(from_name or "", 20)
        who = f"human（{display}）" if display else "human"
    now = now or datetime.now()
    stamp = f"{now:%Y-%m-%d %H:%M}"
    prefix = f"Q-{now:%Y-%m-%d}-"
    existing = [p.name for p in inbox_dir.glob(prefix + "*.md")]
    seq = len(existing) + 1
    title = _clean_one_line(body.splitlines()[0], 24)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(50):
        name = f"{prefix}{seq:02d}.md"
        dest = inbox_dir / name
        content = (
            f"# Q-{now:%Y-%m-%d}-{seq:02d}: {label}：{title}\n\n"
            f"- from: {who}\n- to: {to}\n- status: open\n- action: {action}\n"
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


def load_worklogs(roots: list[tuple[Path, bool]], n: int = 3,
                  since: datetime | None = None, tail_chars: int = 0) -> list[dict]:
    items: list[dict] = []
    for root, _is_main in roots:
        candidates = list(reversed(_state_record_files(root, "worklog", "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md")))
        for f in candidates[:n]:
            if since is not None and f.stem < since.strftime("%Y-%m-%d"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                heads = [l.strip() for l in text.splitlines() if l.startswith("## ")]
            except OSError:
                text = ""
                heads = []
            item = {"file": f.name, "path": str(f.resolve()), "workspace": str(root.resolve()),
                    "sections": heads[-3:], "count": len(heads)}
            if tail_chars:
                item["tail"] = text[-tail_chars:]
                item["tail_truncated"] = len(text) > tail_chars
            items.append(item)
    return sorted(items, key=lambda x: x["file"], reverse=True)[:n]


def worktrees(root: Path | None = None) -> list[dict]:
    """解析 `git worktree list --porcelain`。"""
    return parse_worktree_porcelain(_git_at(root or ROOT, "worktree", "list", "--porcelain", "-z"))


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


def fetch_open_prs(root: Path | None = None) -> list[dict] | None:
    """只读地查 open PR。gh 缺失/未登录/超时/不在 GitHub 仓库 → 返回 None（调用方降级提示）。"""
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--limit", "50",
             "--json", "number,title,headRefName,updatedAt,author"],
            cwd=str(root or ROOT), capture_output=True, text=True,
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
    raw = _git_at(Path(path), "status", "--porcelain", "-z", "--untracked-files=normal")
    n = 0
    entries = raw.split("\0") if "\0" in raw else raw.splitlines()
    index = 0
    while index < len(entries):
        line = entries[index]
        index += 1
        if len(line) < 4:
            continue
        name = line[3:]
        renamed = any(flag in line[:2] for flag in "RC")
        if "\0" in raw and renamed:
            index += 1  # NUL format lists the destination first, then source.
        elif " -> " in name and renamed:
            name = name.split(" -> ", 1)[1]
        if renamed or "D" in line[:2]:
            n += 1
            continue
        p = Path(path) / (name if "\0" in raw else name.strip('"'))
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
            f"- heartbeat: {now:%Y-%m-%d %H:%M}\n- task: 活跃会话\n"
            '- files: ["src/x.py"]\n', encoding="utf-8")
        (sdir / "buddy-250115-0800-bbbb.md").write_text(
            "# Session\n- agent: workbuddy\n- branch: feature/y\n- status: active\n"
            f"- heartbeat: {(now - timedelta(minutes=40)):%Y-%m-%d %H:%M}\n"
            '- task: 失活会话\n- files: ["src/y.py"]\n', encoding="utf-8")
        (sdir / "claude-250112-1000-cccc.md").write_text(
            "# Session\n- agent: claude\n- branch: feature/z\n- status: done\n"
            f"- heartbeat: {(now - timedelta(days=3)):%Y-%m-%d %H:%M}\n",
            encoding="utf-8")
        (sdir / "trae-250115-0700-eeee.md").write_text(
            "# Session\n- agent: trae\n- branch: feature/bad\n- status: active\n"
            '- heartbeat: 刚才写了中文（坏心跳）\n- files: ["src/bad.py"]\n', encoding="utf-8")
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

def collect(now: datetime, with_prs: bool, stale_min: int,
            root: Path | None = None, compact: bool = False,
            include_logs: bool = True) -> dict:
    """扫一遍本机全部工作区，返回可直接 JSON 序列化的看板数据。"""
    root = root or ROOT
    wts = worktrees(root)
    roots = agent_roots(root, wts)
    sessions = load_sessions(roots, now, stale_min)
    inbox = load_inbox(roots, lean=compact)
    logs = (load_worklogs(roots, n=3,
                         since=now - timedelta(days=1) if compact else None,
                         tail_chars=900 if compact else 0) if include_logs else [])

    open_q = [q for q in inbox if q["open"]]
    # 远古 open 问题：只提示，不算告警（忘关的问题不该让开工门禁常年失败）
    old_cut = now - timedelta(days=OLD_INBOX_DAYS)
    fresh_q, old_q = [], []
    for q in open_q:
        dt = q["created_dt"]
        (old_q if (not q["from_human"] and dt is not None and dt < old_cut) else fresh_q).append(q)
    # 近期已回复（近 7 天、有日期）：网页要让人类看到 agent 对自己指令的回复，形成闭环
    replied_q = [q for q in inbox if not q["open"] and q["created_dt"] is not None
                 and q["created_dt"] >= old_cut]
    # 只有「还在值」的会话能抑制未注册 worktree 告警：done/超 24h 的历史会话不行
    registered_branches = {
        s["branch"].split("（")[0].split("(")[0].strip()
        for s in sessions if s.get("occupies", not s["closed"]) and is_fresh_session(s, now)
    }

    warnings: list[str] = []
    open_sessions = [session for session in sessions if not session["closed"]]
    for index, session in enumerate(open_sessions):
        if session["occupies"] and session["stale"] and not is_fresh_session(session, now):
            warnings.append(f"历史失活占用未释放：{session['id']} @ {session['workspace']}；按接管流程处理")
        if session.get("duplicate_id"):
            warnings.append(f"重复会话 ID：{session['id']} @ {session['workspace']}；各副本占用均保留")
        if session["claims_error"]:
            warnings.append(f"文件占用记录无效：{session['id']} 的 files 应为 JSON 字符串数组")
        if session.get("status_error"):
            warnings.append(f"会话状态无效：{session['id']}；核对原记录前保留文件占用")
        if session.get("activity_error"):
            warnings.append(f"最近活动时间无效：{session['id']}，请核对原会话记录")
        if session.get("jobs_error"):
            warnings.append(f"运行命令记录无效：{session['id']}，不能据此释放占用")
        if session.get("terminal_job_conflict"):
            warnings.append(f"结束状态与运行命令冲突：{session['id']} 的命令仍在运行或状态无法核验；文件占用保留")
        for other in open_sessions[index + 1:]:
            if not session["occupies"] or not other["occupies"]:
                continue
            overlap = overlapping_files(session["files"], other["files"])
            if overlap:
                warnings.append(f"文件占用冲突：{session['id']} 与 {other['id']}：{', '.join(overlap[:5])}")
    for q in open_q:
        if q.get("read_error"):
            warnings.append(f"inbox 无法可靠读取：{q['path']}")
    if with_prs:
        prs = fetch_open_prs(root)
        if prs is None:
            warnings.append("open PR 未核验：gh 不可用、未登录或网络失败；不能把失败当作没有 PR")
    else:
        prs = None
    git_ok_flag = resolve_git() is not None
    # 在 git 仓库里却调不动 git：worktree 聚合/分支信息全部失效，必须明确喊出来，
    # 不能静默显示「worktree 0 个 / ✅ 无告警」（手册 A3：假绿比报错更危险）
    if not git_ok_flag and (root / ".git").exists():
        warnings.append(
            "git 不可用（PATH 与常见安装路径都没找到）：worktree/分支信息已降级，"
            f"请把 Git 加入 PATH，或用环境变量 {GIT_ENV_VAR} 指向 git.exe")
    if git_ok_flag and not wts and (root / ".git").exists():
        warnings.append("Git 工作区读取失败：无法确认分支与跨 worktree 占用")
    root_norm = _norm(str(root))
    if wts and (root / ".git").exists() and not any(_norm(w["path"]) == root_norm for w in wts):
        warnings.append("当前项目根未在 Git 工作区清单中，无法确认工作区身份")
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
        "root": str(root),
        "version": BOARD_VERSION,
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
        "open_prs": prs,
        "warnings": warnings,
        # 快照默认按"可指挥"渲染；web 服务会按 --no-directives/环境变量覆盖这个值
        "directives_enabled": True,
    }


def gate_rc(payload: dict) -> int:
    """Unresolved claims and human directives never expire out of the gate."""
    if any(session.get("status_error") for session in payload["sessions"]):
        return 2
    if payload["warnings"]:
        return 1
    if any(s.get("occupies", not s["closed"]) and s["stale"] for s in payload["sessions"]):
        return 1
    if any(q.get("from_human") for q in payload.get("inbox_open_old", [])):
        return 1
    return 1 if payload["inbox_open"] else 0


def context_payload(payload: dict, agent: str, session_id: str | None = None) -> dict:
    """Project current coordination facts; historical records stay on disk."""
    from agents_policy import effective_policy

    root = Path(payload["root"])
    wts = payload["worktrees"]
    primary = Path(wts[0]["path"]) if wts else root
    policy = effective_policy(root, primary)
    branch = next((w["branch"] for w in wts if _norm(w["path"]) == _norm(str(root))), "?")
    targets = {"any", agent}
    if session_id:
        targets.add(session_id)
    open_q = payload["inbox_open"] + payload.get("inbox_open_old", [])
    relevant = [q for q in open_q if q.get("to", "any") in targets]
    relevant.sort(key=lambda q: (not q.get("from_human", False), q.get("date", ""), q["path"]))
    sessions = [s for s in payload["sessions"] if not s["closed"]]
    result = {
        "schema_version": 1,
        "root": str(root), "now": payload["now"], "branch": branch, "version": BOARD_VERSION,
        "policy": policy, "agent": agent, "session": session_id,
        "gate_rc": gate_rc(payload), "truncated": False,
        "counts": {"open_sessions": len(sessions),
                   "closed_sessions": len(payload["sessions"]) - len(sessions),
                   "relevant_inbox": len(relevant), "other_inbox": len(open_q) - len(relevant)},
        "warnings": list(payload["warnings"]),
        "sessions": [{k: (round(s[k], 1) if k == "age_min" and s.get(k) is not None else s.get(k))
                      for k in ("id", "agent", "branch", "task", "progress", "status", "age_min", "files", "path", "workspace")}
                     for s in sessions],
        "inbox": [{"file": q["path"] if q.get("duplicate_file") else q["file"],
                   "path": q["path"], "from": q["from"], "to": q["to"],
                   "body": q["body"], "reply": q["reply"],
                   **({"action": q["action"]} if q.get("action", "message") != "message" else {})}
                  for q in relevant],
        "worklogs": [{k: log.get(k) for k in ("path", "sections", "tail", "tail_truncated")}
                     for log in payload["worklogs"]],
        "unread_count": 0, "unread_paths": [],
        "note": "摘要用于协调；写入前仍需进程/reflog/mtime核对和会话登记。未读指令必须先读原文件。",
    }
    if payload.get("prs_requested"):
        result["open_prs"] = payload.get("open_prs")
    for source, output in zip(sessions, result["sessions"]):
        if status_word(source.get("status", "")) == "standby":
            output.update(standby=source.get("standby", False), occupies=source.get("occupies", True))
        if source.get("running_jobs_count"):
            output["running_jobs_count"] = source["running_jobs_count"]
    standby_count = sum(bool(s.get("standby")) for s in sessions)
    if standby_count:
        result["counts"]["standby_sessions"] = standby_count
        result["counts"]["claimed_sessions"] = len(sessions) - standby_count
    return result


def _context_inbox_header(item: dict) -> str:
    header = f"inbox {item['file']} | from={item['from']} to={item['to']}"
    if item.get("action", "message") != "message":
        header += " action=" + item["action"]
    return header


def format_context(payload: dict, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"分支 {payload['branch']} | gate={payload['gate_rc']} | {payload['now']}",
             f"项目 {payload['root']}",
             "策略 " + json.dumps(payload["policy"], ensure_ascii=False, separators=(",", ":")),
             "数量 " + json.dumps(payload["counts"], ensure_ascii=False, separators=(",", ":"))]
    for warning in payload["warnings"]:
        lines.append("告警 " + warning)
    for session in payload["sessions"]:
        label = "待机 " if session.get("standby") else "占用 "
        lines.append(label + json.dumps(session, ensure_ascii=False, separators=(",", ":")))
    for q in payload["inbox"]:
        lines.extend([_context_inbox_header(q), q["body"]])
        if q.get("reply"):
            lines.append("已有回执 " + q["reply"])
        if q.get("unread"):
            lines.append("未读：预算不足，先读原文 " + q["path"])
    for log in payload["worklogs"]:
        lines.append("日志 " + json.dumps(log, ensure_ascii=False, separators=(",", ":")))
    if payload.get("notifications"):
        from agents_notifications import format_notification
        lines.append(format_notification(payload["notifications"]))
    if payload.get("activity_warning"):
        lines.append("活动记录失败 " + payload["activity_warning"])
    if "open_prs" in payload:
        lines.append("open PR " + json.dumps(payload["open_prs"], ensure_ascii=False, separators=(",", ":")))
    if payload["truncated"]:
        lines.append(f"截断：{payload['unread_count']} 项未完整展示；读取原文件或提高 --max-chars 后重查。")
        lines.extend("未读 " + path for path in payload["unread_paths"])
        if payload.get("expand_args"):
            lines.append("展开参数 " + json.dumps(payload["expand_args"], ensure_ascii=False))
    lines.append(payload["note"])
    return "\n".join(lines)


def fit_context(payload: dict, max_chars: int, as_json: bool = False) -> dict:
    """Bound rendered characters, without silently shortening an instruction."""
    # A JSON roundtrip isolates the caller's authoritative collection.
    out = json.loads(json.dumps(payload, ensure_ascii=False))
    out["max_chars"] = max_chars
    unread_seen: set[str] = set()
    tool_dir = state_dir(Path(out["root"]))
    tool = "tools/agents_status.py" if tool_dir == ".agents" else tool_dir + "/tools/agents_status.py"
    rendered_size = len(format_context(out, as_json)) + 1

    def dump_size(value) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    def omission_size() -> int:
        # These fields are bounded independently of the potentially large inbox.
        if as_json:
            return dump_size({key: out[key] for key in
                              ("truncated", "gate_rc", "unread_count", "unread_paths", "expand_args")
                              if key in out})
        if not out["truncated"]:
            return 0
        lines = [f"截断：{out['unread_count']} 项未完整展示；读取原文件或提高 --max-chars 后重查。"]
        lines.extend("未读 " + path for path in out["unread_paths"])
        if out.get("expand_args"):
            lines.append("展开参数 " + json.dumps(out["expand_args"], ensure_ascii=False))
        return sum(len(line) + 1 for line in lines)

    def item_size(key: str, item) -> int:
        if as_json:
            return dump_size(item)
        if key == "inbox":
            lines = [_context_inbox_header(item), item["body"]]
            if item.get("reply"):
                lines.append("已有回执 " + item["reply"])
            if item.get("unread"):
                lines.append("未读：预算不足，先读原文 " + item["path"])
            return sum(len(line) + 1 for line in lines)
        if key == "warnings":
            return len("告警 " + item) + 1
        return len("日志 " if key == "worklogs" else "占用 ") + dump_size(item) + 1

    def fits() -> bool:
        return rendered_size <= max_chars

    def omitted(path: str = "") -> None:
        nonlocal rendered_size
        before = omission_size()
        out["truncated"] = True
        out["gate_rc"] = 1
        if not path or path not in unread_seen:
            out["unread_count"] += 1
            if path:
                unread_seen.add(path)
        if path and path not in out["unread_paths"] and len(out["unread_paths"]) < 5:
            out["unread_paths"].append(path)
        out["expand_args"] = ["python", str(Path(out["root"]) / tool), "--root", out["root"], "--json"]
        rendered_size += omission_size() - before

    def remove_last(key: str) -> None:
        nonlocal rendered_size
        values = out[key]
        item = values.pop()
        if key == "open_prs":
            # JSON and the single text PR line both render this as one JSON list.
            rendered_size -= dump_size(item) + (1 if values else 0)
        else:
            rendered_size -= item_size(key, item) + (1 if as_json and values else 0)
        omitted(item.get("path", "") if isinstance(item, dict) else "")

    if fits():
        return out
    # Advisory logs and acknowledgements yield before task and directive bodies.
    while out["worklogs"] and not fits():
        remove_last("worklogs")
    for q in reversed(out["inbox"]):
        if fits():
            break
        if q["reply"]:
            before = item_size("inbox", q)
            q["reply"] = ""
            rendered_size += item_size("inbox", q) - before
            omitted(q["path"])
    for session in reversed(out["sessions"]):
        if fits():
            break
        if session.get("files"):
            before = item_size("sessions", session)
            session["files_count"] = len(session["files"])
            session["files"] = []
            session["unread"] = True
            rendered_size += item_size("sessions", session) - before
            omitted(session.get("path", ""))
    for q in reversed(out["inbox"]):
        if fits():
            break
        before = item_size("inbox", q)
        q["body"] = ""
        q["unread"] = True
        rendered_size += item_size("inbox", q) - before
        omitted(q["path"])
    for key in ("open_prs", "sessions", "inbox", "warnings"):
        values = out.get(key)
        while isinstance(values, list) and values and not fits():
            remove_last(key)
    if not fits():
        # Thousands of filenames also need an explicit bounded escape hatch.
        out["unread_paths"] = out["unread_paths"][:1]
    # Verify the real final output, rather than relying solely on the running size.
    if len(format_context(out, as_json)) + 1 > max_chars:
        raise ValueError("上下文预算不足以展示必要的项目身份；请提高 --max-chars")
    return out


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
<title>本机协作看板 · Multi-Agent Collab</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230f766e'/%3E%3Cpath d='M8 10h6v6H8zm10 6h6v6h-6z' fill='white'/%3E%3Cpath d='M14 13h7v3M11 16v3h7' fill='none' stroke='white' stroke-width='2'/%3E%3C/svg%3E">
<style>
  :root {
    --bg:#f3f5f4; --panel:#fff; --ink:#172d2a; --muted:#60716c; --line:#dce5df;
    --green:#12745c; --red:#b83a39; --amber:#946014; --gray:#7b8983; --blue:#0f766e;
    --chip:#edf3ef; --done-ink:#65726c; --action-ink:#fff; --shadow:0 4px 22px #163d2d05;
  }
  @media (prefers-color-scheme:dark) {
    :root { --bg:#111916; --panel:#19231f; --ink:#e5eeea; --muted:#a2b4aa;
      --line:#324139; --green:#76d4ad; --red:#ffaaa1; --amber:#ebc27a; --gray:#96a89e;
      --blue:#78d6c6; --chip:#26372e; --done-ink:#a2b1a8; --action-ink:#102d25; --shadow:none; }
  }
  * { box-sizing:border-box; } [hidden] { display:none !important; }
  body { margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,
    "Segoe UI","Microsoft YaHei",Helvetica,Arial,sans-serif; font-size:14px; line-height:1.6; }
  button,input,select,textarea { font:inherit; } button { cursor:pointer; }
  :focus-visible { outline:3px solid var(--blue); outline-offset:3px; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  .skip { position:absolute; left:20px; top:-60px; z-index:10; padding:10px 16px;
    background:var(--panel); color:var(--blue); border:1px solid var(--blue); border-radius:8px; }
  .skip:focus { top:12px; }
  header,.wrap,footer { max-width:1480px; margin:0 auto; padding-left:32px; padding-right:32px; }
  header { padding-top:30px; padding-bottom:22px; }
  .heading { display:flex; justify-content:space-between; align-items:center; gap:20px; }
  .brand { display:flex; align-items:center; gap:15px; min-width:0; }
  .brand-mark { width:46px; height:46px; flex:none; border-radius:13px; display:grid;
    place-items:center; background:var(--blue); color:var(--action-ink); }
  .eyebrow { margin:0 0 2px; color:var(--muted); font-size:10px; letter-spacing:1.8px; font-weight:700; }
  h1 { margin:0; font-size:27px; line-height:1.3; letter-spacing:-.6px; }
  .head-actions { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
  .root-row { display:flex; align-items:center; gap:10px; margin-top:15px; }
  .root { color:var(--muted); font-size:12px; overflow-wrap:anywhere; min-width:0; } .root-copy { flex:none; }
  .topbar { display:flex; flex-wrap:wrap; gap:7px 12px; align-items:center; margin-top:10px; }
  .pill { display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:6px;
    background:var(--chip); color:var(--muted); font-size:11px; font-weight:600; }
  .pill.live { color:var(--green); } .pill.snap { color:var(--amber); } .pill.off { color:var(--red); }
  .refresh-meta { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; } #countdown { min-width:100px; }
  .connection-note { margin:8px 0 0; color:var(--red); font-size:12px; overflow-wrap:anywhere; }
  .wrap { padding-bottom:25px; }
  .banner { border:1px solid var(--line); border-left:3px solid var(--green); border-radius:11px;
    background:var(--panel); padding:13px 17px; margin-bottom:16px; }
  .banner.bad { border-left-color:var(--amber); } .banner.error { border-left-color:var(--red); }
  .banner-title { font-size:13px; font-weight:700; } .banner-note { color:var(--muted); font-size:12px; margin-top:2px; }
  .banner ul { margin:7px 0 0; padding-left:20px; color:var(--muted); font-size:12px; }
  .banner details { margin-top:5px; } .banner summary { font-size:12px; color:var(--amber); }
  .cards { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:12px; margin-bottom:16px; }
  .card { text-align:left; background:var(--panel); border:1px solid var(--line); border-radius:11px;
    padding:15px 17px; color:var(--ink); min-width:0; box-shadow:var(--shadow); }
  button.card:hover { border-color:var(--blue); background:var(--chip); }
  .card .lbl { font-size:12px; color:var(--muted); }
  .card .num { font-size:29px; font-weight:700; line-height:1.25; margin-top:5px; font-variant-numeric:tabular-nums; }
  .card .stat-note { margin-top:5px; color:var(--muted); font-size:10px; }
  .num.green { color:var(--green); } .num.red { color:var(--red); } .num.amber { color:var(--amber); } .num.gray { color:var(--gray); }
  section { min-width:0; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:20px; box-shadow:var(--shadow); }
  .grid { display:grid; grid-template-columns:minmax(0,1.06fr) minmax(0,1fr); gap:16px; align-items:start; }
  .support-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-top:16px; }
  h2 { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin:0; font-size:15px; letter-spacing:.1px; }
  h2 .count { margin-left:auto; color:var(--muted); font-size:11px; font-weight:400; }
  .section-note { font-size:11px; color:var(--muted); margin:5px 0 14px; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:12px; } .search { width:100%; min-width:0; }
  input,textarea,select { border:1px solid var(--line); border-radius:8px; background:var(--bg);
    color:var(--ink); padding:9px 11px; width:100%; min-height:40px; }
  input::placeholder,textarea::placeholder { color:var(--muted); opacity:.9; } select { cursor:pointer; }
  .toolbar select { width:115px; flex:none; }
  .filters { display:flex; flex-wrap:wrap; gap:5px; margin-bottom:15px; }
  .filter { border:1px solid transparent; background:transparent; border-radius:7px; padding:6px 9px; font-size:11px; color:var(--muted); min-height:34px; }
  .filter:hover { background:var(--chip); }
  .filter[aria-pressed=true] { color:var(--blue); border-color:var(--line); background:var(--chip); font-weight:700; }
  .session { border:1px solid var(--line); border-left:3px solid var(--green); border-radius:9px; padding:14px 15px; margin-bottom:10px; min-width:0; }
  .session.stale { border-left-color:var(--red); } .session.blocked { border-left-color:var(--amber); } .session.done { border-left-color:var(--gray); } .session.standby { border-left-color:var(--blue); background:var(--chip); }
  .session.done .task,.session.done .sid { color:var(--done-ink); }
  .s-head { display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
  .dot { width:7px; height:7px; flex:none; display:inline-block; background:var(--green); border-radius:50%; }
  .dot.stale { background:var(--red); } .dot.blocked { background:var(--amber); } .dot.done { background:var(--gray); } .dot.standby { background:var(--blue); }
  .sid { font-family:ui-monospace,Consolas,monospace; font-size:11px; font-weight:600; overflow-wrap:anywhere; }
  .tag { display:inline-flex; background:var(--chip); border-radius:5px; padding:1px 6px; color:var(--muted); font-size:10px; overflow-wrap:anywhere; }
  .tag.branch { color:var(--blue); margin-top:7px; }
  .tag.pending,.tag.control { color:var(--amber); background:color-mix(in srgb,var(--amber) 10%,var(--panel)); }
  .tag.replied { color:var(--green); background:color-mix(in srgb,var(--green) 10%,var(--panel)); }
  .tag.unread { color:var(--blue); background:color-mix(in srgb,var(--blue) 10%,var(--panel)); } .tag.error { color:var(--red); }
  .task { margin:8px 0 5px; font-size:14px; font-weight:600; overflow-wrap:anywhere; }
  .progress { margin:6px 0 8px; font-size:12px; color:var(--muted); white-space:pre-wrap; overflow-wrap:anywhere; }
  .meta { font-size:11px; color:var(--muted); overflow-wrap:anywhere; }
  .session-footer { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .age-ok { color:var(--green); } .age-bad { color:var(--red); } .age-warn { color:var(--amber); } .age-done { color:var(--done-ink); }
  .record-details { margin-top:10px; border-top:1px solid var(--line); padding-top:9px; }
  summary { cursor:pointer; font-size:11px; color:var(--muted); padding:3px 0; }
  .detail-label { font-size:10px; color:var(--muted); margin:10px 0 4px; font-weight:600; }
  .detail-text { font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .files { margin:5px 0; padding-left:17px; color:var(--muted); font:11px/1.7 ui-monospace,Consolas,monospace; } .files li { overflow-wrap:anywhere; }
  .path-row { display:flex; align-items:center; gap:8px; margin-top:7px; }
  .path-row code { color:var(--muted); font-size:10px; overflow-wrap:anywhere; min-width:0; flex:1; } .path-row button { flex:none; }
  .q { border:1px solid var(--line); border-radius:9px; padding:13px 15px; margin-bottom:10px; }
  .q.control { border-left:3px solid var(--amber); } .q.unread { background:color-mix(in srgb,var(--blue) 3%,var(--panel)); }
  .message-route { color:var(--muted); font-size:11px; margin-top:7px; overflow-wrap:anywhere; }
  .bubble { margin:8px 0; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; }
  .clamp { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:5; overflow:hidden; }
  .reply-box { padding:10px 12px; margin-top:10px; border-radius:7px; background:var(--chip); font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
  .reply-title { font-size:10px; font-weight:700; color:var(--green); margin-bottom:4px; }
  .reply-box.pending { color:var(--muted); background:var(--bg); font-size:11px; }
  .message-actions { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .inbox-bottom { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; border-top:1px solid var(--line); margin-top:13px; padding-top:10px; }
  .btn,.mini { border:1px solid var(--line); background:var(--panel); color:var(--ink); border-radius:7px; padding:8px 13px; font-size:12px; min-height:38px; }
  .btn:hover,.mini:hover { border-color:var(--blue); color:var(--blue); }
  .btn.primary { background:var(--blue); border-color:var(--blue); color:var(--action-ink); font-weight:600; }
  .mini { padding:5px 9px; min-height:30px; color:var(--muted); font-size:10px; } button:disabled { opacity:.55; cursor:not-allowed; }
  .composer { margin-bottom:16px; padding:15px 20px; }
  .composer-heading { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .composer-heading h2 { font-size:13px; } .composer-heading .count { margin-left:0; }
  .composer-note { color:var(--muted); font-size:11px; margin-top:5px; } .composer.collapsed #directive-form { display:none; }
  #directive-form { border-top:1px solid var(--line); margin-top:14px; padding-top:14px; }
  .form-grid { display:grid; grid-template-columns:minmax(0,1.6fr) minmax(0,1fr) minmax(0,.9fr); gap:12px; }
  .field label { display:block; font-size:11px; font-weight:600; margin-bottom:5px; } .field-body { margin-top:12px; }
  .label-row { display:flex; justify-content:space-between; gap:8px; } .label-row span { font-size:10px; color:var(--muted); }
  textarea { min-height:105px; resize:vertical; }
  .action-help { font-size:11px; color:var(--muted); padding:9px 11px; background:var(--bg); border-radius:7px; margin-top:10px; } .action-help.control { color:var(--amber); }
  .send-row { display:flex; gap:12px; justify-content:space-between; align-items:center; flex-wrap:wrap; margin-top:12px; }
  .send-status { font-size:12px; min-height:20px; overflow-wrap:anywhere; flex:1; min-width:180px; }
  .send-status.error { color:var(--red); } .send-status.success { color:var(--green); }
  .send-result-actions { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
  .wt { padding:11px 0; border-bottom:1px solid var(--line); } .wt:last-child { border-bottom:0; padding-bottom:0; }
  .wt-title { font-size:12px; font-weight:600; overflow-wrap:anywhere; } .wt .tag { margin-top:5px; }
  .logitem { padding:9px 0; border-bottom:1px solid var(--line); } .logitem:last-child { border-bottom:0; }
  .logfile { font-size:12px; font-weight:600; } .logsec { font-size:11px; color:var(--muted); padding-top:5px; overflow-wrap:anywhere; }
  .pr { padding:10px 0; border-bottom:1px solid var(--line); font-size:12px; overflow-wrap:anywhere; } .pr:last-child { border-bottom:0; }
  a { color:var(--blue); text-underline-offset:3px; }
  .empty { text-align:center; padding:26px 14px; border:1px dashed var(--line); border-radius:9px; color:var(--muted); font-size:12px; }
  .empty-title { font-weight:600; color:var(--ink); margin-bottom:5px; font-size:13px; } .empty button { margin-top:12px; }
  label.chk { display:flex; align-items:center; gap:6px; cursor:pointer; color:var(--muted); font-size:10px; font-weight:400; margin:8px 0; }
  label.chk input { width:16px; min-height:16px; margin:0; }
  footer { color:var(--muted); font-size:11px; padding-top:4px; padding-bottom:28px; }
  @media (max-width:1050px) {
    header,.wrap,footer { padding-left:22px; padding-right:22px; } .cards { gap:9px; } .card { padding:12px; }
    .support-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  }
  @media (max-width:800px) {
    .grid { grid-template-columns:minmax(0,1fr); } .cards { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .heading { align-items:flex-start; } .head-actions { max-width:210px; } h1 { font-size:23px; }
    .form-grid { grid-template-columns:minmax(0,1fr) minmax(0,1fr); } .field-name { grid-column:1/-1; }
  }
  @media (max-width:520px) {
    header,.wrap,footer { padding-left:14px; padding-right:14px; } header { padding-top:20px; padding-bottom:16px; }
    .heading { flex-wrap:wrap; gap:15px; } .head-actions { max-width:none; justify-content:flex-start; }
    .brand-mark { width:40px; height:40px; border-radius:11px; } h1 { font-size:23px; }
    section { padding:15px; } .composer { padding:13px 15px; } .card { padding:12px 10px; } .card .num { font-size:24px; }
    .card .stat-note { display:none; } .cards { gap:8px; } .support-grid { grid-template-columns:minmax(0,1fr); }
    .form-grid { grid-template-columns:minmax(0,1fr); } .field-name { grid-column:auto; }
    .root-copy { padding:4px 7px; } .session,.q { padding:12px; }
    .composer-heading { align-items:flex-start; } .composer-heading h2 { max-width:65%; }
    .send-row .primary { width:100%; } .send-status { flex-basis:100%; }
    .filter { padding:6px 8px; } .toolbar select { width:105px; }
  }
  @media (pointer:coarse) {
    .btn,.mini,.filter { min-height:44px; } summary { min-height:38px; padding-top:7px; }
    label.chk { padding:8px 0; } input,select { min-height:44px; }
  }
  @media (prefers-reduced-motion:no-preference) { button { transition:background .12s,border-color .12s; } }
</style>
</head>
<body>
<a class="skip" href="#main">跳到看板内容</a>
<header>
  <div class="heading">
    <div class="brand"><div class="brand-mark" aria-hidden="true"><svg width="27" height="27" viewBox="0 0 32 32" fill="none"><rect x="4" y="5" width="9" height="9" rx="2" fill="currentColor"/><rect x="19" y="18" width="9" height="9" rx="2" fill="currentColor"/><path d="M13 9h10v9M9 14v9h10" stroke="currentColor" stroke-width="2"/></svg></div><div><p class="eyebrow">MULTI-AGENT COLLAB / LOCAL WORKSPACE</p><h1>本机协作看板</h1></div></div>
    <div class="head-actions"><button class="btn" id="refresh-btn" type="button">立即刷新</button><button class="btn" id="notify-btn" type="button" aria-pressed="false">开启回复通知</button></div>
  </div>
  <div class="root-row"><div class="root" id="root">正在读取项目与工作区…</div><button class="mini root-copy" id="root-copy" type="button" disabled>复制项目路径</button></div>
  <div class="topbar"><span class="pill" id="mode">读取中</span><span class="pill" id="conn"></span><span class="refresh-meta" id="updated"></span><span class="refresh-meta" id="countdown"></span></div>
  <p class="connection-note" id="connection-note" role="status" aria-live="polite" hidden></p>
</header>
<main class="wrap" id="main" tabindex="-1">
  <div id="banner"><div class="banner"><div class="banner-title">正在读取协作记录</div><div class="banner-note">会话、指令与回复将在这里显示。</div></div></div>
  <div class="cards" id="cards" aria-label="统计与快捷筛选"></div>
  <section class="composer collapsed" id="directive-box" aria-label="发送消息或控制指令">
    <div class="composer-heading"><h2>发送消息或控制指令 <span class="count" id="directive-hint">连接后可用</span></h2><button class="btn" id="composer-toggle" type="button" aria-expanded="false" aria-controls="directive-form" disabled>写一条指令</button></div>
    <div class="composer-note" id="composer-note">消息会写入 .agents/inbox/；agent 在下次检查点读取，页面不会自动唤醒它。</div>
    <form id="directive-form">
      <div class="form-grid">
        <div class="field"><label for="directive-to">接收会话</label><select id="directive-to" required><option value="any">所有 agent（广播）</option></select></div>
        <div class="field"><label for="directive-action">消息类型</label><select id="directive-action"><option value="message">普通消息</option><option value="pause">暂停工作</option><option value="cancel">取消任务</option><option value="avoid">避让协调</option></select></div>
        <div class="field field-name"><label for="directive-name">你的称呼（可选）</label><input id="directive-name" type="text" maxlength="20" autocomplete="off" placeholder="例：项目负责人"></div>
      </div>
      <div class="field field-body"><div class="label-row"><label for="directive-body">消息正文</label><span id="body-count">0 / 4000</span></div><textarea id="directive-body" maxlength="4000" rows="4" aria-describedby="action-help directive-status" placeholder="写清具体要求、涉及文件与期望结果。例：检查登录流程，将测试结果回复到这条消息。"></textarea></div>
      <div class="action-help" id="action-help"></div>
      <div class="send-row"><div class="send-status" id="directive-status" role="status" aria-live="polite"></div><button class="btn primary" id="directive-send" type="submit">发送消息</button></div>
      <div class="send-result-actions" id="send-result-actions"></div>
    </form>
  </section>
  <div class="grid">
    <section id="session-section" aria-labelledby="session-title">
      <h2 id="session-title">协作会话 <span class="count" id="s-count"></span></h2><p class="section-note">心跳是登记记录；失活与受阻会话继续保留文件占用。</p>
      <div class="toolbar"><label class="sr-only" for="session-search">搜索会话、任务、分支或文件</label><input class="search" id="session-search" type="search" autocomplete="off" placeholder="搜索任务、分支、文件…" aria-controls="sessions"><label class="sr-only" for="agent-filter">按平台筛选</label><select id="agent-filter" aria-controls="sessions"><option value="all">所有平台</option></select></div>
      <div class="filters" id="session-filters" aria-label="会话状态筛选"><button class="filter" type="button" data-sfilter="open" aria-pressed="true">未结束</button><button class="filter" type="button" data-sfilter="standby" aria-pressed="false">待机</button><button class="filter" type="button" data-sfilter="stale" aria-pressed="false">失活</button><button class="filter" type="button" data-sfilter="blocked" aria-pressed="false">受阻</button><button class="filter" type="button" data-sfilter="done" aria-pressed="false">已结束</button><button class="filter" type="button" data-sfilter="all" aria-pressed="false">全部</button></div>
      <div id="sessions"></div>
    </section>
    <section id="inbox-section" aria-labelledby="inbox-title">
      <h2 id="inbox-title">指令与回复 <span class="count" id="q-count"></span></h2><p class="section-note">本页未读仅表示你在本浏览器是否看过，与 agent 标读、ack 和任务完成分别记录。</p>
      <div class="toolbar"><label class="sr-only" for="inbox-search">搜索消息正文、回复、收发人或文件</label><input class="search" id="inbox-search" type="search" autocomplete="off" placeholder="搜索消息、回复、收发人…" aria-controls="inbox"></div>
      <div class="filters" id="inbox-filters" aria-label="指令与回复筛选"><button class="filter" type="button" data-qfilter="pending" aria-pressed="true">待处理</button><button class="filter" type="button" data-qfilter="control" aria-pressed="false">控制有效</button><button class="filter" type="button" data-qfilter="unread" aria-pressed="false">本页未读</button><button class="filter" type="button" data-qfilter="replies" aria-pressed="false">已有回复</button><button class="filter" type="button" data-qfilter="all" aria-pressed="false">全部</button></div>
      <div id="inbox"></div><div class="inbox-bottom"><span class="meta" id="inbox-scope"></span><button class="mini" id="mark-visible" type="button">将显示项标为看过</button></div>
    </section>
  </div>
  <div class="support-grid">
    <section id="worktree-section"><h2>工作区 <span class="count" id="w-count"></span></h2><p class="section-note">交叉查看工作区与未结束会话的登记。</p><div id="worktrees"></div></section>
    <section id="pr-section"><h2>打开的 PR <span class="count" id="p-count"></span></h2><label class="chk" id="pr-toggle"><input type="checkbox" id="pr-cb">附带 GitHub PR 查询（使用 gh）</label><div id="prs"></div></section>
    <section><h2>最近工作日志</h2><p class="section-note">最近 3 份日志，保留各阶段记录。</p><div id="worklogs"></div></section>
  </div>
</main>
<footer>数据来自本机协作记录，窗口不会自动唤醒模型。发送指令后，可将「复制提醒」粘贴到目标 agent 的对话。
  实时看板默认仅本机可访问；快照可离线查看，无法发送指令。</footer>
<p class="sr-only" id="live" role="status" aria-live="polite"></p>
<script id="board-data" type="application/json">__DATA__</script>
<script>
"use strict";
const LIVE = __LIVE__;
const REFRESH_SEC = __REFRESH__;
let wantPrs = false, cd = REFRESH_SEC, lastData = null;
let sessionFilter = "open", inboxFilter = "pending", visibleInbox = [];
let composerOpen = false, sending = false, refreshing = false, connected = false;
let authHeader = null, authCancelled = false, lastSummary = "";
let pendingViewPath = null;
const expanded = new Set(), seen = new Map();
let seenRoot = null;
const prevReplies = new Map();
let notifyBaseline = false, notifyOn = false;
const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function announce(text) { $("live").textContent = text; }
function ageText(s) {
  if (!s.heartbeat) return "无有效心跳";
  const m = Math.max(0, Math.round(s.age_min || 0));
  return m < 1 ? "刚刚" : m + " 分钟前";
}
function shortTime(iso) { return iso ? iso.replace("T", " ").slice(0, 19) : "—"; }
function ageClass(s) {
  if (s.closed) return "age-done";
  if (s.standby && s.stale) return "age-done";
  if (!s.heartbeat || s.stale) return "age-bad";
  return (s.age_min || 0) <= 2 ? "age-ok" : ((s.age_min || 0) > 10 ? "age-warn" : "");
}
function sessionKey(s) { return s.path || ((s.workspace || "") + "/" + s.id); }
function inboxKey(q) { return q.path || ((q.workspace || "") + "/" + q.file); }
function allInbox(d) {
  const byPath = new Map();
  (d.inbox_open || []).concat(d.inbox_open_old || [], d.inbox_replied || []).forEach(q => byPath.set(inboxKey(q), q));
  return Array.from(byPath.values()).sort((a,b) => Number(controlActive(b)) - Number(controlActive(a)) || Number(b.open) - Number(a.open) || String(b.date || "").localeCompare(String(a.date || "")));
}
function controlActive(q) { return !!q.from_human && ["pause","cancel","avoid"].includes(q.action) && !!q.open; }
function actionLabel(action) { return ({message:"普通消息",pause:"暂停工作",cancel:"取消任务",avoid:"避让协调"})[action] || "未知类型"; }
function statusLabel(q) {
  if (controlActive(q)) return "控制仍有效";
  if (q.open) return "待处理";
  if (q.from_human && ["pause","cancel","avoid"].includes(q.action)) return q.status === "superseded" ? "控制已被替代" : "控制已关闭";
  return q.reply ? "已有回复" : "已结束 · 无回复记录";
}
// 本浏览器阅读标记只用于展示，不写协作文件，也不作为 agent 已读或授权证据。
function revision(q) {
  const text = JSON.stringify([q.body,q.reply,q.status,q.action,q.to,q.from]);
  let hash = 2166136261;
  for (let i=0;i<text.length;i++) hash = Math.imul(hash ^ text.charCodeAt(i),16777619);
  return text.length + ":" + (hash >>> 0).toString(16);
}
function loadSeen(d) {
  if (seenRoot === d.root) return;
  seenRoot = d.root; seen.clear();
  try {
    const rows = JSON.parse(sessionStorage.getItem("mac-board-seen:" + seenRoot) || "[]");
    if (Array.isArray(rows)) rows.forEach(row => { if (Array.isArray(row) && typeof row[0] === "string" && typeof row[1] === "string") seen.set(row[0],row[1]); });
  } catch (_) { /* 禁用存储时仍可在当前页面使用 */ }
}
function saveSeen() {
  try { sessionStorage.setItem("mac-board-seen:" + seenRoot,JSON.stringify(Array.from(seen.entries()).slice(-2000))); } catch (_) {}
}
function unread(q) { return seen.get(inboxKey(q)) !== revision(q); }
function markSeen(items) {
  items.forEach(q => seen.set(inboxKey(q),revision(q))); saveSeen();
  if (lastData) render(lastData);
  announce("已将 " + items.length + " 条消息在本页标为看过；未改变 agent 回执或事项状态。");
}
function button(text,key,callback,cls="mini") {
  const b = el("button",cls,text); b.type = "button";
  if (key) b.dataset.fkey = key;
  b.addEventListener("click",callback); return b;
}
async function copyText(text,btn) {
  const label = btn.textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try {
      await navigator.clipboard.writeText(text); btn.textContent = "已复制"; announce("已复制到剪贴板。");
      setTimeout(() => { if (btn.isConnected) btn.textContent = label; },1500); return;
    } catch (_) {}
  }
  // prompt 仅提供手动复制，取消或关闭不能被宣称为复制成功。
  window.prompt("浏览器未允许自动复制，请手动复制下面的内容：",text);
  announce("已打开手动复制框；复制结果由你确认。");
}
function reminderText(q) {
  const path = q.path || (".agents/inbox/" + q.file);
  return "请读取 " + JSON.stringify(path) + "，按已有授权与协作规则处理，并在原 inbox 回复结果。" +
    (controlActive(q) ? "这是有效的" + actionLabel(q.action) + "指令；标读、ack 或回复都不解除控制。" : "阅读或确认收到不代表任务完成。");
}
function pathRow(path,key) {
  const row = el("div","path-row"); row.appendChild(el("code","",path || "未记录路径"));
  if (path) { const b = button("复制路径","copy:" + key,() => copyText(path,b)); row.appendChild(b); }
  return row;
}
function detailNode(title,key) {
  const d = el("details","record-details"); d.open = expanded.has(key);
  const summary = el("summary","",title); summary.dataset.fkey = "detail:" + key; d.appendChild(summary);
  d.addEventListener("toggle",() => { if (d.open) expanded.add(key); else expanded.delete(key); }); return d;
}
function emptyNode(title,note,reset) {
  const node = el("div","empty"); node.appendChild(el("div","empty-title",title)); node.appendChild(el("div","",note));
  if (reset) node.appendChild(button("清除筛选",null,reset)); return node;
}
function sessionNode(s) {
  const key = sessionKey(s), cls = s.closed ? "done" : (s.status_error || s.terminal_job_conflict ? "blocked" : (s.standby ? "standby" : (s.blocked ? "blocked" : (s.stale ? "stale" : "active"))));
  const node = el("article","session " + cls), head = el("div","s-head");
  const dot = el("span","dot " + cls); dot.setAttribute("aria-hidden","true"); head.appendChild(dot);
  head.appendChild(el("span","sid",s.id)); head.appendChild(el("span","tag",s.agent || "未知平台"));
  head.appendChild(el("span","tag " + (s.status_error || s.blocked || s.terminal_job_conflict ? "pending" : ""),s.status_error ? "状态无效 · 占用保留" : (s.closed ? "已结束" : (s.terminal_job_conflict ? "结束标记冲突 · 占用保留" : (s.standby ? (s.stale ? "待机 · 离线" : "待机") : (s.blocked ? "受阻" : (s.stale ? "失活" : "进行中")))))));
  if (s.blocked && s.stale) head.appendChild(el("span","tag error","心跳失活"));
  if (s.duplicate_id) head.appendChild(el("span","tag error","重复 ID"));
  if (s.running_jobs_count) head.appendChild(el("span","tag pending",s.running_jobs_count + " 项跟踪命令记录"));
  node.appendChild(head); node.appendChild(el("div","task",s.standby ? "等待新任务 · 不占文件" : (s.task || "未填写任务")));
  if (s.standby && s.last_task) node.appendChild(el("div","progress clamp","上项任务：" + s.last_task));
  node.appendChild(el("span","tag branch",s.branch || "未记录分支"));
  if (s.progress) node.appendChild(el("div","progress clamp",s.progress));
  if (s.handoff) node.appendChild(el("div","progress clamp","交接：" + s.handoff));
  const foot = el("div","session-footer"), meta = el("div","meta");
  meta.appendChild(el("span","",(s.files || []).length + " 项文件范围 · 最近活动 "));
  meta.appendChild(el("span",ageClass(s),ageText(s))); foot.appendChild(meta);
  if (!s.closed && LIVE && lastData.directives_enabled) {
    const b = button("发消息给此会话","target:" + key,() => composeFor(s)); b.disabled = sending || !!s.duplicate_id; foot.appendChild(b);
  }
  node.appendChild(foot);
  const details = detailNode("查看文件范围与记录详情",key);
  details.appendChild(el("div","detail-label","原始状态 / 最后心跳")); details.appendChild(el("div","detail-text",(s.status || "未记录") + " / " + shortTime(s.heartbeat)));
  if (s.files && s.files.length) { const list = el("ul","files"); s.files.forEach(f => list.appendChild(el("li","",f))); details.appendChild(list); }
  else details.appendChild(el("div","meta","未声明文件范围；请核对原记录。"));
  if (s.claims_error) details.appendChild(el("div","meta age-bad","文件范围记录无效，不能视为没有占用。"));
  if (s.progress) { details.appendChild(el("div","detail-label","完整进度")); details.appendChild(el("div","detail-text",s.progress)); }
  if (s.handoff) { details.appendChild(el("div","detail-label","完整交接")); details.appendChild(el("div","detail-text",s.handoff)); }
  if (s.workspace || s.worktree) { details.appendChild(el("div","detail-label","工作区")); details.appendChild(pathRow(s.workspace || s.worktree,"workspace:" + key)); }
  details.appendChild(el("div","detail-label","会话原文件")); details.appendChild(pathRow(s.path,"session:" + key));
  if (s.pr && /^https?:\/\//.test(s.pr)) { const a = el("a","meta","查看 PR ↗"); a.href=s.pr; a.target="_blank"; a.rel="noopener noreferrer"; a.dataset.fkey="pr:" + key; details.appendChild(a); }
  node.appendChild(details); return node;
}
function directiveNode(q) {
  const key = inboxKey(q), isUnread = unread(q);
  const node = el("article","q" + (controlActive(q) ? " control" : "") + (isUnread ? " unread" : ""));
  node.dataset.record = key; node.dataset.fkey = "message:" + key; node.tabIndex = -1;
  const head = el("div","s-head"); head.appendChild(el("span","tag " + (controlActive(q) ? "control" : ""),actionLabel(q.action)));
  head.appendChild(el("span","tag " + (q.open ? "pending" : (q.reply ? "replied" : "")),statusLabel(q)));
  if (isUnread) head.appendChild(el("span","tag unread","本页未读"));
  if (q.read_error) head.appendChild(el("span","tag error","原文读取异常")); node.appendChild(head);
  node.appendChild(el("div","message-route",(q.from || "未知来源") + " → " + (q.to || "any") + (q.date ? " · " + shortTime(q.date) : "")));
  const body = (q.body || "").replace(/^##\s*指令\s*/,"").trim();
  node.appendChild(el("div","bubble clamp",body || "未读取到消息正文，请核对原文件。"));
  if (q.reply) {
    const reply = el("div","reply-box"); reply.appendChild(el("div","reply-title",controlActive(q) ? "已有回复 · 控制仍有效" : (q.open ? "已有回复 · 事项仍待处理" : "回复记录")));
    reply.appendChild(el("div","clamp",q.reply)); node.appendChild(reply);
  } else if (q.open) node.appendChild(el("div","reply-box pending","尚无回复。agent 在检查点读取；空闲时可将「复制提醒」粘进它的对话。"));
  if (controlActive(q)) node.appendChild(el("div","meta age-warn","标读、ack、answered / done / cancelled 不解除控制；解除需真实人类指令。"));
  const actions = el("div","message-actions");
  const copy = button("复制提醒","remind:" + key,() => copyText(reminderText(q),copy)); actions.appendChild(copy);
  const view = button(isUnread ? "本页标为看过" : "本页已看过","seen:" + key,() => markSeen([q])); view.disabled = !isUnread; actions.appendChild(view); node.appendChild(actions);
  const details = detailNode("展开全文与原文件", "inbox:" + key);
  details.appendChild(el("div","detail-label","消息全文")); details.appendChild(el("div","detail-text",body || "未读取到正文"));
  if (q.reply) { details.appendChild(el("div","detail-label","完整回复")); details.appendChild(el("div","detail-text",q.reply)); }
  details.appendChild(el("div","detail-label","原始状态")); details.appendChild(el("div","meta",q.status || "未记录"));
  details.appendChild(el("div","detail-label","收件箱原文件")); details.appendChild(pathRow(q.path || (".agents/inbox/" + q.file),"inbox:" + key));
  node.appendChild(details); return node;
}
function chooseSessionFilter(value) { sessionFilter=value; if (lastData) render(lastData); $("session-section").scrollIntoView({block:"start"}); }
function chooseInboxFilter(value) { cancelPendingView(); inboxFilter=value; if (lastData) render(lastData); $("inbox-section").scrollIntoView({block:"start"}); }
function matchesSession(s,value) {
  return value === "all" || (value === "open" && !s.closed) || (value === "standby" && s.standby) || (value === "stale" && !s.closed && !s.standby && s.stale) || (value === "blocked" && !s.closed && s.blocked) || (value === "done" && s.closed);
}
function matchesInbox(q,value) { return value === "all" || (value === "pending" && q.open) || (value === "control" && controlActive(q)) || (value === "unread" && unread(q)) || (value === "replies" && !!q.reply); }
function resetSessions() { sessionFilter="open"; $("agent-filter").value="all"; $("session-search").value=""; if (lastData) render(lastData); }
function resetInbox() { cancelPendingView(); inboxFilter="pending"; $("inbox-search").value=""; if (lastData) render(lastData); }
function renderLists(d,items) {
  const agents=Array.from(new Set(d.sessions.map(s => s.agent || "未知平台"))).sort(), sel=$("agent-filter"), prior=sel.value;
  sel.replaceChildren(); const all=el("option","","所有平台"); all.value="all"; sel.appendChild(all);
  agents.forEach(a => { const o=el("option","",a); o.value=a; sel.appendChild(o); }); sel.value=agents.includes(prior) ? prior : "all";
  const search=$("session-search").value.trim().toLocaleLowerCase();
  const sessions=d.sessions.filter(s => matchesSession(s,sessionFilter) && (sel.value === "all" || (s.agent || "未知平台") === sel.value) && [s.id,s.task,s.branch,s.workspace,s.progress,s.handoff].concat(s.files || []).join(" ").toLocaleLowerCase().includes(search));
  $("sessions").replaceChildren(...sessions.map(sessionNode));
  if (!sessions.length) $("sessions").appendChild(emptyNode(d.sessions.length ? "没有符合筛选的会话" : "还没有会话记录",d.sessions.length ? "调整状态、平台或搜索词，可查看其它登记。" : "agent 登记任务后，这里会显示会话与文件范围。",d.sessions.length ? resetSessions : null));
  $("s-count").textContent="显示 " + sessions.length + " / " + d.sessions.length;
  const slabels={open:"未结束",standby:"待机",stale:"失活",blocked:"受阻",done:"已结束",all:"全部"};
  document.querySelectorAll("[data-sfilter]").forEach(b => { const f=b.dataset.sfilter; b.textContent=slabels[f] + " " + d.sessions.filter(s => matchesSession(s,f)).length; b.setAttribute("aria-pressed",String(sessionFilter === f)); });
  const qsearch=$("inbox-search").value.trim().toLocaleLowerCase();
  visibleInbox=items.filter(q => matchesInbox(q,inboxFilter) && [q.body,q.reply,q.from,q.to,q.file,q.path].join(" ").toLocaleLowerCase().includes(qsearch));
  $("inbox").replaceChildren(...visibleInbox.map(directiveNode));
  if (!visibleInbox.length) $("inbox").appendChild(emptyNode(items.length ? "没有符合筛选的消息" : "收件箱暂无消息",items.length ? "可切换到已有回复或全部，或调整搜索词。" : "发送一条消息后，可在这里查看原文与回复。",items.length ? resetInbox : null));
  $("q-count").textContent=items.filter(q => q.open).length + " 待处理 · " + items.filter(unread).length + " 本页未读";
  const qlabels={pending:"待处理",control:"控制有效",unread:"本页未读",replies:"已有回复",all:"全部"};
  document.querySelectorAll("[data-qfilter]").forEach(b => { const f=b.dataset.qfilter; b.textContent=qlabels[f] + " " + items.filter(q => matchesInbox(q,f)).length; b.setAttribute("aria-pressed",String(inboxFilter === f)); });
  $("inbox-scope").textContent="显示 " + visibleInbox.length + " 条 · 近 7 天已处理记录及全部待处理项";
  $("mark-visible").disabled=!visibleInbox.some(unread);
}
function statCards(d,items) {
  const open=d.sessions.filter(s => !s.closed).length, standby=d.sessions.filter(s => s.standby).length, stale=d.sessions.filter(s => !s.closed && !s.standby && s.stale).length, done=d.sessions.filter(s => s.closed).length;
  const pending=items.filter(q => q.open).length, control=items.filter(controlActive).length;
  const cards=[
    ["未结束会话",open,"","执行、受阻与待机",() => chooseSessionFilter("open")],
    ["待机会话",standby,"","等待新任务，不占文件",() => chooseSessionFilter("standby")],
    ["失活会话",stale,stale ? "red" : "gray","占用仍保留",() => chooseSessionFilter("stale")],
    ["待处理事项",pending,pending ? "amber" : "gray","查看原文与回复",() => chooseInboxFilter("pending")],
    ["有效控制",control,control ? "amber" : "gray","暂停 / 取消 / 避让",() => chooseInboxFilter("control")],
    ["已结束会话",done,"gray","查看历史记录",() => chooseSessionFilter("done")],
    ["工作区",d.n_roots,"","聚合本机登记",() => $("worktree-section").scrollIntoView({block:"start"})]
  ];
  $("cards").replaceChildren(...cards.map(([label,num,color,note,action]) => {
    const card=button("","stat:" + label,action,"card"); card.setAttribute("aria-label",label + " " + num + "，" + note);
    card.appendChild(el("div","lbl",label)); card.appendChild(el("div","num " + color,String(num))); card.appendChild(el("div","stat-note",note + " ↗")); return card;
  })); return {open,standby,stale,done,pending};
}
function fillTargets(d) {
  if (sending) return;
  const sel=$("directive-to"), prev=sel.value || "any";
  sel.replaceChildren(); const any=el("option","","所有 agent（广播）"); any.value="any"; sel.appendChild(any);
  d.sessions.filter(s => !s.closed).forEach(s => {
    const o=el("option","",s.id + " · " + (s.branch || "未记录分支") + (s.status_error ? " · 状态无效 · 占用保留" : (s.standby ? " · 待机" : (s.blocked ? " · 受阻" : (s.stale ? " · 失活" : "")))));
    o.value=s.id; if (s.duplicate_id) { o.disabled=true; o.textContent += " · 重复 ID，需协调"; } sel.appendChild(o);
  });
  if (!Array.from(sel.options).some(o => o.value === prev)) { const gone=el("option","",prev + " · 已结束或不可用，请重新选择"); gone.value=prev; gone.disabled=true; sel.appendChild(gone); }
  sel.value=prev;
}
function validTarget() { const o=$("directive-to").selectedOptions[0]; return !!o && !o.disabled; }
function applyComposerState() {
  $("directive-box").classList.toggle("collapsed",!composerOpen);
  $("composer-toggle").setAttribute("aria-expanded",String(composerOpen)); $("composer-toggle").textContent=composerOpen ? "收起表单" : "写一条指令";
}
function setComposerOpen(open) { composerOpen=open; applyComposerState(); if (open && !$("directive-body").disabled) $("directive-to").focus(); }
function composeFor(s) {
  if (sending) { announce("当前指令仍在发送，请等待写入结果。"); return; }
  if (!LIVE || !lastData || !lastData.directives_enabled || s.closed || s.duplicate_id) return;
  $("directive-to").value=s.id; $("directive-action").value="message"; setComposerOpen(true); updateActionHelp();
  $("directive-box").scrollIntoView({block:"start"}); $("directive-body").focus();
}
function updateActionHelp() {
  const action=$("directive-action").value, target=$("directive-to").value, control=action !== "message";
  $("action-help").className="action-help" + (control ? " control" : "");
  const scope=target === "any" ? "接收范围：所有 agent（广播）。" : "接收会话：" + target + "。";
  const help={message:"普通消息用于问题、需求或结果沟通，agent 按已有授权处理。",pause:"暂停工作要求相关会话先暂停；标读、ack 或回复都不解除暂停。",cancel:"取消任务要求相关会话停止该任务；标读、ack 或回复都不解除取消。",avoid:"避让协调要求相关会话避开正文指定范围；标读、ack 或回复都不解除避让。"};
  $("action-help").textContent=validTarget() ? scope + (help[action] || "请重新选择消息类型。") + (control ? "人类明确解除后才可关闭旧控制，或由新指令明确替代。" : "") : "原目标已结束或不可用，请重新选择接收会话。";
  $("directive-send").textContent=sending ? "正在发送…" : (control ? "发送" + actionLabel(action) + "指令" : "发送消息");
  $("directive-send").disabled=sending || !LIVE || !lastData || !lastData.directives_enabled || !validTarget();
}
function renderComposer(d) {
  const writable=LIVE && !!d.directives_enabled;
  $("directive-hint").textContent=!LIVE ? "离线快照" : (writable ? "可写入收件箱" : "只读模式");
  $("composer-toggle").hidden=!writable; $("composer-toggle").disabled=!writable;
  $("directive-form").hidden=!writable;
  $("composer-note").textContent=!LIVE ? "这是离线快照，不能发送指令。要使用实时看板，请在项目根运行：python tools/agents_status.py --web" : (writable ? "消息写入 .agents/inbox/；agent 在下次检查点读取。页面不会自动唤醒它，空闲时可复制提醒。" : "当前服务为只读模式：可以筛选和查看记录，无法在页面发送指令。");
  if (writable) fillTargets(d);
  ["directive-body","directive-to","directive-name","directive-action"].forEach(id => { $(id).disabled=!writable || sending; });
  updateActionHelp(); applyComposerState();
}
function samePath(a,b) { const norm=p => String(p || "").replace(/\\/g,"/").replace(/\/$/,"").toLowerCase(); return norm(a) === norm(b); }
function renderSupport(d) {
  $("worktrees").replaceChildren(...d.worktrees.map(w => {
    const matches=d.sessions.filter(s => !s.closed && !s.standby && (s.workspace ? samePath(s.workspace,w.path) : s.branch === w.branch));
    const node=el("div","wt"); node.appendChild(el("div","wt-title",w.branch || "未记录分支"));
    node.appendChild(el("span","tag " + (!matches.length ? "pending" : ""),matches.length ? matches.length + " 个占用会话" : "当前无登记占用"));
    node.appendChild(el("div","meta","HEAD " + (w.head || "未记录"))); node.appendChild(pathRow(w.path,"worktree:" + w.path)); return node;
  }));
  if (!d.worktrees.length) $("worktrees").appendChild(emptyNode("无工作区信息",d.git_available ? "未获取到 Git 工作区清单，请核对终端提示。" : "Git 信息未核验；会话占用仍按本机记录保留。"));
  $("w-count").textContent=d.worktrees.length + " 个";
  $("prs").replaceChildren(); $("p-count").textContent=d.open_prs === null ? "未核验" : d.open_prs.length + " 个";
  if (d.open_prs === null) $("prs").appendChild(emptyNode(d.prs_requested ? "PR 查询未完成核验" : "尚未查询 PR",d.prs_requested ? "查看协作提醒与 gh 配置；查询失败不代表没有 PR。" : (LIVE ? "勾选上方选项，将在刷新时只读查询。" : "生成快照时加 --prs，可附带查询结果。")));
  else if (!d.open_prs.length) $("prs").appendChild(emptyNode("当前没有打开的 PR","上次查询返回 0 条记录。"));
  else d.open_prs.forEach(pr => { const node=el("div","pr"); node.appendChild(el("div","","#" + pr.number + " · " + pr.title)); node.appendChild(el("div","meta",pr.branch + " · " + pr.author + " · " + shortTime(pr.updated))); $("prs").appendChild(node); });
  $("pr-toggle").hidden=!LIVE;
  $("worklogs").replaceChildren(...d.worklogs.slice(0,3).map(log => { const node=el("div","logitem"); node.appendChild(el("div","logfile",log.file + " · " + log.count + " 段")); (log.sections || []).forEach(h => node.appendChild(el("div","logsec",h.replace(/^##\s*/,"")))); if (log.path) node.appendChild(pathRow(log.path,"log:" + log.path)); return node; }));
  if (!d.worklogs.length) $("worklogs").appendChild(emptyNode("暂无工作日志","阶段记录追加后，会显示在这里。"));
}
function focusKeyOf(node) { const hit=node && node.closest ? node.closest("[data-fkey]") : null; return hit ? hit.dataset.fkey : null; }
function restoreFocus(key) {
  if (!key) return;
  const next=Array.from(document.querySelectorAll("[data-fkey]")).find(n => n.dataset.fkey === key);
  if (next && !next.disabled) next.focus({preventScroll:true});
  else if (next) { const card=next.closest("[data-record]"); if (card) card.focus({preventScroll:true}); }
}
function render(d) {
  if (!d || typeof d.root !== "string" || !Array.isArray(d.sessions) || !Array.isArray(d.worktrees) || !Array.isArray(d.worklogs) || !Array.isArray(d.warnings)) throw new Error("看板数据格式不完整");
  const keep=focusKeyOf(document.activeElement); lastData=d; loadSeen(d); const items=allInbox(d);
  $("root").textContent=d.root + " · " + d.n_roots + " 个工作区"; $("root-copy").disabled=false;
  $("updated").textContent="数据更新 " + shortTime(d.now); $("mode").textContent=LIVE ? "实时看板" : "离线快照"; $("mode").className="pill " + (LIVE ? "live" : "snap");
  const alerts=d.warnings.slice(); d.sessions.filter(s => !s.standby && s.stale && s.fresh).forEach(s => alerts.push("最近活动超时：" + s.id + " · " + ageText(s)));
  const pending=(d.inbox_open || []).length; if (pending) alerts.push(pending + " 条待处理事项；请核对原指令与回复。");
  const banner=el("div","banner" + (alerts.length ? " bad" : "")); banner.appendChild(el("div","banner-title",alerts.length ? alerts.length + " 项协作提醒需要查看" : "当前没有协作告警"));
  banner.appendChild(el("div","banner-note","以实际进程、Git 与文件状态交叉核对；看板记录不证明授权或测试通过。"));
  if (alerts.length) { const details=el("details"); details.open=expanded.has("alerts"); const summary=el("summary","","查看提醒详情"); summary.dataset.fkey="alerts"; details.appendChild(summary); const list=el("ul"); alerts.forEach(a => list.appendChild(el("li","",a))); details.appendChild(list); details.addEventListener("toggle",() => { if (details.open) expanded.add("alerts"); else expanded.delete("alerts"); }); banner.appendChild(details); }
  $("banner").replaceChildren(banner);
  const counts=statCards(d,items); renderComposer(d); renderLists(d,items); renderSupport(d);
  $("notify-btn").hidden=!LIVE; $("refresh-btn").hidden=!LIVE; $("countdown").hidden=!LIVE;
  if (!LIVE) { $("conn").hidden=true; $("connection-note").hidden=true; }
  restoreFocus(keep); notifyAnswered(items);
  const summary="未结束会话 " + counts.open + "，待机 " + counts.standby + "，失活 " + counts.stale + "，待处理事项 " + counts.pending + "，本页未读 " + items.filter(unread).length;
  if (summary !== lastSummary) { lastSummary=summary; announce(summary); }
  revealPendingMessage();
}
function setStatus(text,type="info") { $("directive-status").textContent=text; $("directive-status").className="send-status " + type; }
function setConn(ok,message="") {
  connected=ok; $("conn").textContent=ok ? "已连接" : "连接需处理"; $("conn").className="pill " + (ok ? "live" : "off");
  $("connection-note").hidden=ok; $("connection-note").textContent=message + (lastData ? " 保留上次成功读取的数据。" : " 可点击立即刷新重试。");
}
function updateCountdown() { $("countdown").textContent=refreshing ? "正在读取数据…" : (authCancelled ? "等待输入密码" : Math.max(0,cd) + " 秒后" + (connected ? "刷新" : "重试")); }
function askPassword() {
  const pw=window.prompt("输入本地看板访问密码（只保存在当前页面内存中）：");
  if (!pw) { authCancelled=true; setConn(false,"需要访问密码。点击立即刷新可重新输入。"); return false; }
  const bytes=new TextEncoder().encode("board:" + pw);
  authHeader={Authorization:"Basic " + btoa(Array.from(bytes,b => String.fromCharCode(b)).join(""))}; authCancelled=false; return true;
}
async function requestJson(url,options) {
  const controller=new AbortController(), timeout=setTimeout(() => controller.abort(),20000);
  try {
    const response=await fetch(url,Object.assign({},options,{signal:controller.signal}));
    let data=null;
    try { data=await response.json(); } catch (e) { if (e.name === "AbortError") throw e; }
    return {response,data};
  } finally { clearTimeout(timeout); }
}
async function tick() {
  if (authCancelled || refreshing) return false;
  refreshing=true; cd=REFRESH_SEC; $("refresh-btn").disabled=true; $("refresh-btn").textContent="刷新中…"; updateCountdown();
  let retryAuth=false;
  try {
    const {response:r,data}=await requestJson("data?prs=" + (wantPrs ? 1 : 0),{cache:"no-store",headers:authHeader || {}});
    if (r.status === 401) { retryAuth=askPassword(); return false; }
    if (!r.ok) throw new Error((data && data.error) || "服务器返回 HTTP " + r.status);
    render(data); setConn(true); return true;
  } catch (e) {
    const message=e.name === "AbortError" ? "读取数据超时。" : "数据更新失败：" + e.message;
    setConn(false,message);
    if (pendingViewPath) setStatus("指令已写入，列表更新失败；恢复连接后会继续自动定位。","error");
    if (!lastData) { const banner=el("div","banner error"); banner.appendChild(el("div","banner-title","尚未读取到协作数据")); banner.appendChild(el("div","banner-note",message)); $("banner").replaceChildren(banner); }
    return false;
  } finally {
    refreshing=false; cd=REFRESH_SEC; $("refresh-btn").disabled=false; $("refresh-btn").textContent="立即刷新"; updateCountdown();
    if (retryAuth) setTimeout(tick,0);
  }
}
function resultPath(file) { return lastData.root.replace(/[\\/]$/,"") + "/.agents/inbox/" + file; }
function setPendingView(path) {
  pendingViewPath=path;
  $("inbox-section").setAttribute("aria-busy",String(!!path));
  const btn=$("view-message-result");
  if (btn) { btn.disabled=!!path; btn.textContent=path ? "正在定位…" : "查看这条指令"; }
}
function cancelPendingView() {
  if (!pendingViewPath) return;
  setPendingView(null); setStatus("自动定位已取消，指令已写入收件箱。","success");
}
function revealPendingMessage() {
  if (!pendingViewPath || !lastData) return;
  const q=allInbox(lastData).find(q => q.path && samePath(q.path,pendingViewPath));
  const node=q && Array.from(document.querySelectorAll("[data-record]")).find(n => n.dataset.record === inboxKey(q));
  if (!node) return;
  setPendingView(null);
  node.scrollIntoView({block:"center"}); node.focus({preventScroll:true});
  setStatus("已定位 " + q.file + "。agent 在下次检查点读取。","success");
  announce("已定位到这条指令卡片。");
}
function viewMessage(file) {
  if (!lastData) return;
  setPendingView(resultPath(file));
  setStatus("指令已写入，正在更新列表并定位这条指令…");
  inboxFilter="all"; $("inbox-search").value=""; render(lastData);
  if (pendingViewPath) {
    $("inbox-section").scrollIntoView({block:"start"}); announce("指令已写入，正在等待列表更新后自动定位。");
    if (!refreshing) tick();
  }
}
async function sendDirective(ev) {
  ev.preventDefault(); if (sending) return;
  if (!LIVE || !lastData || !lastData.directives_enabled) { setStatus("当前模式无法发送指令。","error"); return; }
  if (authCancelled && !askPassword()) { setStatus("需要访问密码才能发送。","error"); return; }
  const to=$("directive-to").value, body=$("directive-body").value.trim(), from_name=$("directive-name").value.trim(), action=$("directive-action").value;
  if (!validTarget()) { setStatus("请选择仍可接收消息的目标会话。","error"); $("directive-to").focus(); return; }
  if (!body) { setStatus("请先填写消息正文。","error"); $("directive-body").setAttribute("aria-invalid","true"); $("directive-body").focus(); return; }
  cancelPendingView();
  $("directive-body").removeAttribute("aria-invalid"); sending=true; renderComposer(lastData); setStatus("正在写入收件箱…"); $("send-result-actions").replaceChildren();
  try {
    const {response:r,data}=await requestJson("directive",{method:"POST",headers:Object.assign({"Content-Type":"application/json","X-Mac-Board-Action":"1"},authHeader || {}),body:JSON.stringify({to,body,from_name,action})});
    if (r.status === 401) { askPassword(); setStatus("身份验证未通过。确认密码后可重新发送，正文已保留。","error"); return; }
    if (!r.ok) { setStatus("发送失败：" + ((data && data.error) || "HTTP " + r.status) + "。正文已保留。","error"); return; }
    if (!data || !data.ok || typeof data.file !== "string" || !/^Q-[a-zA-Z0-9_-]+\.md$/.test(data.file)) { setStatus("服务已响应，但写入结果未确认。先刷新收件箱并核对原文件，再决定是否重试。","error"); return; }
    $("directive-body").value=""; $("body-count").textContent="0 / 4000";
    setStatus("已写入 " + data.file + "。agent 在下次检查点读取，页面不会自动唤醒它。","success");
    const q={file:data.file,path:resultPath(data.file),from_human:true,action,open:true};
    const copy=button("复制提醒",null,() => copyText(reminderText(q),copy));
    const view=button("查看这条指令",null,() => viewMessage(data.file)); view.id="view-message-result";
    $("send-result-actions").appendChild(copy); $("send-result-actions").appendChild(view);
    await tick();
  } catch (e) {
    setStatus((e.name === "AbortError" ? "发送请求超时。" : "发送连接中断：" + e.message + "。") + "写入结果未确认，正文已保留；先刷新收件箱并核对原文件，再决定是否重试。","error");
  } finally { sending=false; if (lastData) renderComposer(lastData); }
}
// 回复新增或内容变化都可通知，包括已有回复但控制仍有效的事项。
function notifyAnswered(items) {
  if (notifyOn && notifyBaseline && "Notification" in window && Notification.permission === "granted") {
    items.forEach(q => { if (q.reply && prevReplies.get(inboxKey(q)) !== q.reply) { try { new Notification("agent 回复：" + q.file,{body:q.reply.slice(0,120)}); } catch (_) {} } });
  }
  prevReplies.clear(); items.forEach(q => prevReplies.set(inboxKey(q),q.reply || "")); notifyBaseline=true;
}
// 快照和实时页都可本地筛选、展开与复制；这些操作不改变协作状态。
document.querySelectorAll("[data-sfilter]").forEach(b => b.addEventListener("click",() => { sessionFilter=b.dataset.sfilter; if (lastData) render(lastData); }));
document.querySelectorAll("[data-qfilter]").forEach(b => b.addEventListener("click",() => { cancelPendingView(); inboxFilter=b.dataset.qfilter; if (lastData) render(lastData); }));
["session-search","inbox-search"].forEach(id => $(id).addEventListener("input",() => { if (id === "inbox-search") cancelPendingView(); if (lastData) render(lastData); }));
$("agent-filter").addEventListener("change",() => { if (lastData) render(lastData); });
$("mark-visible").addEventListener("click",() => markSeen(visibleInbox));
$("root-copy").addEventListener("click",() => { if (lastData) copyText(lastData.root,$("root-copy")); });
$("directive-body").addEventListener("input",() => { $("body-count").textContent=$("directive-body").value.length + " / 4000"; $("directive-body").removeAttribute("aria-invalid"); });
["directive-action","directive-to"].forEach(id => $(id).addEventListener("change",updateActionHelp));
$("composer-toggle").addEventListener("click",() => setComposerOpen(!composerOpen));
$("directive-form").addEventListener("submit",sendDirective);
if (LIVE) {
  $("refresh-btn").addEventListener("click",() => { if (!authCancelled || askPassword()) tick(); });
  $("pr-cb").addEventListener("change",function() { wantPrs=this.checked; tick(); });
  $("notify-btn").addEventListener("click",async function() {
    if (!("Notification" in window)) { this.textContent="浏览器不支持通知"; this.disabled=true; return; }
    try {
      if (Notification.permission === "granted") notifyOn=!notifyOn;
      else { const permission=await Notification.requestPermission(); notifyOn=permission === "granted"; if (!notifyOn) { this.textContent="通知未获浏览器许可"; announce("浏览器未允许通知，请在浏览器站点设置中调整。"); return; } }
      this.textContent=notifyOn ? "回复通知已开启" : "开启回复通知"; this.setAttribute("aria-pressed",String(notifyOn));
    } catch (_) { this.textContent="回复通知暂不可用"; announce("浏览器未能启用通知，可继续在看板查看回复。"); }
  });
  setInterval(() => { if (!refreshing && !authCancelled) cd-=1; updateCountdown(); if (cd <= 0) tick(); },1000); tick();
} else {
  $("refresh-btn").hidden=true; $("notify-btn").hidden=true; $("countdown").hidden=true;
  try { render(JSON.parse($("board-data").textContent)); }
  catch (e) { const banner=el("div","banner error"); banner.appendChild(el("div","banner-title","快照数据无法读取")); banner.appendChild(el("div","banner-note",e.message)); $("banner").replaceChildren(banner); }
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


def render_html(payload: dict | None, live: bool, root: Path | None = None) -> str:
    """渲染自包含 HTML 看板。live=True 给 --web（JS 轮询 /data）；
    False 给 --html 快照（数据内嵌、离线可开）。"""
    directory = state_dir(root or ROOT)
    return (_DASHBOARD_HTML
            .replace(".agents/", directory + "/")
            .replace("python tools/agents_", "python " + (directory + "/" if directory != ".agents" else "") + "tools/agents_")
            .replace("__DATA__", _embed_json(payload))
            .replace("__LIVE__", "true" if live else "false")
            .replace("__REFRESH__", str(WEB_REFRESH_SEC)))


def _write_lf_atomic(path: Path, content: str) -> None:
    """LF + UTF-8 原子写，保留旧权限；新记录保留 mkstemp 的私有默认权限。"""
    try:
        previous_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        previous_mode = None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            if previous_mode is not None:
                fchmod = getattr(os, "fchmod", None)
                if fchmod is not None:
                    fchmod(f.fileno(), previous_mode)
                else:
                    os.chmod(tmp, previous_mode)
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

    def __init__(self, *args, **kwargs):
        self._workers = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbusy")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()


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
                directives: bool = True,
                request_timeout: float = REQUEST_TIMEOUT_SEC) -> _BoardServer:
    """提供看板页面、只读 /data 与 POST /directive。

    - bind：127.0.0.1（默认，只本机）或 0.0.0.0（对局域网开放）
    - password：非空时所有请求走 HTTP Basic（用户名任意，用 board 即可）
    - directives=False：只读分享，POST 一律 403
    调用方应先用 check_serve_options() 校验组合。
    """
    problem = check_serve_options(bind, directives, password)
    if problem:
        raise ValueError(problem)
    server_root = Path(root).resolve()
    state_dir(server_root)  # Invalid ownership is a startup error, not an empty board.
    auth_fails: dict[str, list] = {}   # ip -> 失败时间戳列表（进程内存，重启即清零）
    auth_lock = threading.Lock()

    class _Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(request_timeout)
            super().setup()

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
            now = time.monotonic()
            with auth_lock:
                fails = [t for t in auth_fails.get(ip, []) if now - t < AUTH_LOCK_SEC]
                if fails:
                    auth_fails[ip] = fails
                else:
                    auth_fails.pop(ip, None)
                return len(fails) >= AUTH_FAIL_LIMIT

        def _record_fail(self) -> None:
            with auth_lock:
                ip = self.client_address[0]
                if ip not in auth_fails and len(auth_fails) >= 1024:
                    auth_fails.pop(next(iter(auth_fails)))
                fails = auth_fails.setdefault(ip, [])
                fails.append(time.monotonic())
                del fails[:-AUTH_FAIL_LIMIT]

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
            return hmac.compare_digest(given.encode("utf-8"), password.encode("utf-8"))

        def _gate_auth(self) -> bool:
            if not password:
                return True
            if self._locked_out():
                self._json(429, {"error": f"密码错误过多，请 {AUTH_LOCK_SEC} 秒后再试"})
                return False
            if not self._authed():
                self._record_fail()
                self._send(401, "auth required", "text/plain; charset=utf-8",
                           {"WWW-Authenticate": 'Basic realm="multi-agent-board", charset="UTF-8"'})
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
                try:
                    page = render_html(None, live=True, root=server_root)
                except (ValueError, OSError) as exc:
                    self._json(500, {"error": str(exc), "gate_rc": 2})
                    return
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/data":
                qs = parse_qs(urlsplit(self.path).query)
                want_prs = with_prs or qs.get("prs", ["0"])[0] == "1"
                try:
                    payload = collect(datetime.now(), want_prs, stale_min, root=server_root)
                    payload["directives_enabled"] = directives
                    payload["gate_rc"] = gate_rc(payload)
                except Exception as e:   # 看板进程不该因为一次坏数据就 500 整页挂
                    self._json(500, {"error": f"数据收集失败：{e}", "gate_rc": 2})
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
                self.close_connection = True
                self._json(400, {"error": "Content-Length 无效"})
                return
            if length < 0 or length > 16384:
                # 声明得离谱：不读，显式断开，别把垃圾读进内存
                self.close_connection = True
                self._json(413, {"error": "请求体过大"})
                return
            try:
                raw = self.rfile.read(length) if length else b""
            except (TimeoutError, OSError):
                self.close_connection = True
                self._json(408, {"error": "请求体读取超时"})
                return
            if len(raw) != length:
                self._json(400, {"error": "请求体不完整"})
                return

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
                if not isinstance(data, dict) or any(
                        not isinstance(data.get(key, default), str)
                        for key, default in (("to", "any"), ("body", ""), ("from_name", ""), ("action", "message"))):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "请求不是合法 JSON"})
                return
            to = str(data.get("to", "any")).strip()
            body = str(data.get("body", ""))
            name = str(data.get("from_name", ""))
            action = data.get("action", "message")
            if len(body) > DIRECTIVE_MAX_LEN:
                self._json(413, {"error": f"指令超过 {DIRECTIVE_MAX_LEN} 字"})
                return
            if to != "any":
                # 聚合主工作区与所有 linked worktree 的会话（与看板口径一致）
                try:
                    candidates = [s for s in load_sessions(agent_roots(server_root), datetime.now(), stale_min)
                                  if not s["closed"] and s["id"] == to]
                except (ValueError, OSError) as exc:
                    self._json(500, {"error": str(exc), "gate_rc": 2})
                    return
                if len(candidates) > 1:
                    self._json(409, {"error": f"目标会话 ID 在多个工作区重复：{to}；先协调原记录"})
                    return
                if not candidates:
                    self._json(400, {"error": f"目标会话不存在：{to}（选 any 或当前会话）"})
                    return
            try:
                dest = create_directive(_checked_state_directory(server_root, "inbox"), to, body, name, action=action)
            except (ValueError, OSError) as e:
                self._json(400, {"error": str(e)})
                return
            _audit(f"[指令] {datetime.now():%H:%M:%S} → {to}  {dest.name}"
                   + (f"（{_clean_one_line(name, 20)}）" if name else ""))
            self._json(200, {"ok": True, "file": dest.name})

    if ":" in bind:
        class _IPv6BoardServer(_BoardServer):
            address_family = socket.AF_INET6
        return _IPv6BoardServer((bind, port), _Handler)
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
    for candidate in range(port, min(port + 10, 65536)):
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

    show_host = "[::1]" if bind == "::1" else ("127.0.0.1" if bind in LOOPBACK_BINDS else "本机IP")
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
            webbrowser.open(url)
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


def _main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--context", action="store_true", help="输出有界的当前策略/占用/定向指令/近期日志摘要")
    ap.add_argument("--agent", help="当前平台，用于筛选定向消息及 --context")
    ap.add_argument("--session", help="当前会话 ID，用于隔离消息阅读回执")
    ap.add_argument("--no-activity", action="store_true", help="只读审计，不更新自己的最近活动")
    ap.add_argument("--max-chars", type=int, default=CONTEXT_MAX_CHARS,
                    help=f"--context 输出字符上限，含换行（默认 {CONTEXT_MAX_CHARS}；不是精确 token 数）")
    ap.add_argument("--prs", action="store_true",
                    help="同时查询 open PR（调 gh；不可核验时发出告警并返回1）")
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

    if args.stale_min <= 0 or not 0 <= args.port <= 65535:
        ap.error("--stale-min 必须大于0；--port 应为0至65535")
    if args.context:
        if args.brief or args.web or args.html is not None or args.selfcheck:
            ap.error("--context 不能与 --brief/--web/--html/--selfcheck 合用")
        if not args.agent or not _TARGET_RE.fullmatch(args.agent):
            ap.error("--context 需要合法的 --agent 平台名称")
        if args.session and not _TARGET_RE.fullmatch(args.session):
            ap.error("--session 会话 ID 无效")
        if not 1024 <= args.max_chars <= 100000:
            ap.error("--max-chars 应为1024至100000")
    elif args.max_chars != CONTEXT_MAX_CHARS:
        ap.error("--max-chars 仅用于 --context")
    if args.agent and not _TARGET_RE.fullmatch(args.agent):
        ap.error("--agent 平台名称无效")
    if args.session and not _TARGET_RE.fullmatch(args.session):
        ap.error("--session 会话 ID 无效")

    if args.selfcheck:
        return selfcheck()

    ROOT = detect_root(args.root)

    activity_warning = None
    if args.agent and args.session and not args.no_activity:
        from agents_activity import safe_touch_activity
        activity_warning = safe_touch_activity(ROOT, args.agent, args.session, "agents_status").get("warning")

    if args.web:
        if activity_warning:
            print("[活动记录失败] " + activity_warning, file=sys.stderr)
        directives = (not args.no_directives) and (not _env_truthy(DIRECTIVE_ENV_READONLY))
        password = os.environ.get(DIRECTIVE_ENV_PASSWORD, "").strip() or None
        return run_web(port=args.port, stale_min=args.stale_min, with_prs=args.prs,
                       open_browser=not args.no_open, bind=args.bind.strip(),
                       directives=directives, password=password)

    payload = collect(datetime.now(), args.prs, args.stale_min,
                      compact=args.context or args.brief, include_logs=not args.brief)
    payload["gate_rc"] = gate_rc(payload)
    if activity_warning:
        payload["activity_warning"] = activity_warning
    from agents_notifications import collect_notifications, format_notification
    roots = [Path(item["path"]) for item in payload["worktrees"]] or [ROOT]
    notice = collect_notifications(ROOT, args.agent, args.session, roots)
    if notice:
        payload["notifications"] = notice

    if args.context:
        context = context_payload(payload, args.agent, args.session)
        if notice:
            context["notifications"] = notice
        if activity_warning:
            context["activity_warning"] = activity_warning
        context = fit_context(context, args.max_chars, args.json)
        print(format_context(context, args.json))
        return context["gate_rc"]

    if args.html is not None:
        out = _checked_state_directory(ROOT, "") / "board.html" if args.html == SNAPSHOT_DEFAULT else Path(args.html)
        if not out.is_absolute():
            out = ROOT / out
        _write_lf_atomic(out, render_html(payload, live=False))
        print(f"HTML 快照已生成：{out}（用浏览器直接打开即可；重新运行本命令刷新）",
              file=sys.stderr)
        if args.open_html:
            webbrowser.open(out.as_uri())
        if notice:
            print(format_notification(notice))
        if activity_warning:
            print("[活动记录失败] " + activity_warning, file=sys.stderr)
        return gate_rc(payload)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return gate_rc(payload)

    if args.brief:
        print_brief(payload)
        if notice:
            print(format_notification(notice))
        if activity_warning:
            print("[活动记录失败] " + activity_warning, file=sys.stderr)
        return gate_rc(payload)

    print_human(payload)
    if notice:
        print(format_notification(notice))
    if activity_warning:
        print("[活动记录失败] " + activity_warning, file=sys.stderr)
    return gate_rc(payload)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (ValueError, OSError, RuntimeError) as exc:
        args = argv if argv is not None else sys.argv[1:]
        error = {"ok": False, "gate_rc": 2, "error": str(exc)[:500]}
        print(json.dumps(error, ensure_ascii=False) if "--json" in args else "[FAIL] " + error["error"])
        return 2


def print_brief(p: dict) -> None:
    """开工协调用的一行摘要：没事就 OK；有事单行点名（agent 据此决定要不要展开看板）。"""
    parts = []
    if p["warnings"]:
        parts.append(f"告警{len(p['warnings'])}:" + "；".join(
            w.split("（")[0][:28] for w in p["warnings"][:3]))
    stale = [s for s in p["sessions"] if s["stale"] and s.get("occupies", not s["closed"])]
    if stale:
        parts.append("失活 " + ",".join(s["id"] for s in stale[:4]))
    if p["inbox_open"]:
        qs = p["inbox_open"]
        parts.append(f"未答{len(qs)}:" + ",".join(q["file"] for q in qs[:4]))
    n_active = sum(1 for s in p["sessions"] if s.get("occupies", not s["closed"]) and not s["stale"])
    n_standby = sum(bool(s.get("standby")) for s in p["sessions"])
    waiting = "；待机" + str(n_standby) if n_standby else ""
    if parts:
        print(f"看板:活跃{n_active}{waiting} | " + " | ".join(parts)
              + "（跑 python tools/agents_status.py 看详情）")
    else:
        print(f"OK 看板无告警：活跃{n_active}{waiting}；写入仍需当前占用核对与会话登记")


def print_human(p: dict) -> None:
    """终端人类模式（与 JSON/HTML/web 共用 collect 数据，口径一致）。"""
    sessions = p["sessions"]
    fresh_q, old_q, logs, wts = p["inbox_open"], p["inbox_open_old"], p["worklogs"], p["worktrees"]
    warnings, git_available = p["warnings"], p["git_available"]
    active = [s for s in sessions if s.get("occupies", not s["closed"]) and not s["stale"]]
    stale = [s for s in sessions if s.get("occupies", not s["closed"]) and s["stale"]]
    standby = [s for s in sessions if s.get("standby")]
    stale_recent = [s for s in stale if s["fresh"]]
    stale_old = [s for s in stale if not s["fresh"]]
    registered_branches = {
        s["branch"].split("（")[0].split("(")[0].strip()
        for s in sessions if s.get("occupies", not s["closed"]) and s["fresh"]
    }

    line = "─" * 68
    print(f"\n本机协作看板   {p['now']}   (失活阈值 {p['stale_min']} 分钟)")
    print(f"项目根 {ROOT}（{p['n_roots']} 个工作区的 .agents/）")
    print(line)

    # ---- 会话 ----
    print(f"【会话】共 {len(sessions)} 个，活跃 {len(active)}，失活 {len(stale)}，待机 {len(standby)}")
    for s in sessions:
        if s["closed"] and not s["stale"]:
            mark = "✓"
        elif s.get("standby"):
            mark = "○"
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
        print(f"      状态 {s['status']}   最近活动 {age}{blocked_tag}")
        if s.get("standby"):
            print("      待机，不占文件" + ("；最近未活动" if s["stale"] else ""))
        if s["task"]:
            print(f"      任务 {s['task'][:70]}")
        if s.get("files"):
            print(f"      文件 {', '.join(s['files'][:8])}")
        if s.get("progress"):
            print(f"      进度 {s['progress'][:100]}")
    print("  (● 活跃   ! 失活且保留占用   ○ 待机且无占用   ✓ 已结束)")

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
    # 失活占用不会因时间自动释放；新旧记录都影响门禁。
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
              f"仍保留占用并影响门禁；按接管流程核实，不得直接置 done 或删除")
    if old_q:
        print(f"  ℹ️  {len(old_q)} 条超过 {OLD_INBOX_DAYS} 天的旧 open 问题，"
              f"记得 answered/closed 掉")
    if not (warnings or stale_recent or fresh_q or stale_old or old_q):
        print("  ✅ 无告警")
    print()


if __name__ == "__main__":
    sys.exit(main())
