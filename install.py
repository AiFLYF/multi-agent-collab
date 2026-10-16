#!/usr/bin/env python3
"""multi-agent-collab 安装器（零依赖，纯标准库；Windows / macOS / Linux 通用）

把「Git Flow + 本机共享记忆」协作体系安装到任意 git 项目：
  .multi-agent-collab/ 独立目录 + 保留原文的 AGENTS.md / 各平台标记入口

用法：
    python install.py install [TARGET]            # 安装/升级到 TARGET（默认当前目录）
    python install.py doctor [TARGET]             # 体检：文件齐不齐、忽略生不生效、看板能不能跑
    python install.py sync [TARGET]               # 同步已选平台；--platforms 可追加新平台
    python install.py platforms                   # 查看支持的平台与入口
    python install.py clients [TARGET]            # 只检查所选平台的 CLI 可发现性；--versions 可查询版本
    python install.py mirror [TARGET]             # 兼容旧命令：同步 WorkBuddy 镜像

常用选项：
    --platforms codex,claude                       # 按需选装；首次询问，升级沿用，追加不删除旧入口
    --project-name NAME                           # 替换 {{PROJECT_NAME}}（默认读独立目录 install.json）
    --dry-run                                     # 只打印要做的事，不落盘

设计约定：
- 幂等：内容相同跳过；不同则先备份（统一放独立目录 backups/，天然被忽略）再覆盖
- 定制区：RULES.md / TROUBLESHOOTING.md 里 mac:zone 标记之间的内容
  升级时自动保留（项目门禁、项目速览、你追加的 D 组故障条目）
- 默认在 .gitignore 忽略独立目录；.git/info/exclude 仅维护新建的精确入口，不隐藏项目原规则
- linked worktree（.git 是文件）也能正确识别，忽略块写入该 worktree 与公共目录两处
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
KIT_VERSION = "1.7.0"   # 升级 kit 时记得改；会写进目标项目的 .agents/install.json
sys.dont_write_bytecode = True
sys.path.insert(0, str(KIT_ROOT / "tools"))
from agents_policy import POLICY_FILE, POLICY_DOC, build_policy, effective_policy, render_policy  # noqa: E402

# 与 tools/agents_status.py 同名同义：git 可执行文件解析的环境变量覆盖
# （两份小拷贝是有意为之——看板会被单独装进目标项目，不能 import 安装器）。
GIT_ENV_VAR = "MULTI_AGENT_GIT"
_GIT_FORCE_OFF = {"none", "0", "off", "no", "false"}
_GIT_UNSET = object()
_git_bin_cache: object = _GIT_UNSET


def _candidate_git_paths() -> list[str]:
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
        cands += ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git"]
    return cands


def resolve_git() -> str | None:
    """环境变量 > PATH > 常见安装路径；找不到返回 None（结果缓存）。"""
    global _git_bin_cache
    if _git_bin_cache is not _GIT_UNSET:
        return _git_bin_cache  # type: ignore[return-value]
    override = os.environ.get(GIT_ENV_VAR, "").strip()
    if override.lower() in _GIT_FORCE_OFF:
        _git_bin_cache = None
        return None
    if override:
        _git_bin_cache = override
        return override
    found = shutil.which("git")
    if not found:
        for cand in _candidate_git_paths():
            if os.path.isfile(cand):
                found = cand
                break
    _git_bin_cache = found
    return found


# (kit 内源路径, 安装目标相对路径, 定制区名或 None；"__troubles__" 走专用合并)
CORE_FILES = [
    ("docs/RULES.md", ".agents/RULES.md", "project-env"),
    ("platforms/agents-entry.md", "AGENTS.md", "__entry__"),
    ("tools/agents_status.py", "tools/agents_status.py", None),
    ("tools/agents_session.py", "tools/agents_session.py", None),
    ("tools/agents_policy.py", "tools/agents_policy.py", None),
    ("tools/agents_layout.py", "tools/agents_layout.py", None),
    ("docs/README.md", ".agents/README.md", None),
    ("docs/START_HERE.md", ".agents/START_HERE.md", None),
    ("docs/WORKFLOW.md", ".agents/WORKFLOW.md", None),
    ("docs/TAKEOVER.md", ".agents/TAKEOVER.md", None),
    ("docs/TROUBLESHOOTING.md", ".agents/TROUBLESHOOTING.md", "__troubles__"),
    ("templates/session.md", ".agents/sessions/_template.md", None),
    ("templates/inbox.md", ".agents/inbox/_template.md", None),
    ("templates/worklog.md", ".agents/worklog/_template.md", None),
]

PLATFORM_FILES = {
    "trae": ("platforms/trae-rule.md", ".trae/rules/git-flow-agent.md"),
    "claude": ("platforms/claude-command.md", ".claude/commands/git-flow.md"),
    "cursor": ("platforms/cursor-rule.mdc", ".cursor/rules/multi-agent-collab.mdc"),
    "copilot": ("platforms/copilot-instructions.md", ".github/copilot-instructions.md"),
    "windsurf": ("platforms/windsurf-rule.md", ".windsurf/rules/multi-agent-collab.md"),
    "gemini": ("platforms/gemini-memory.md", "GEMINI.md"),
    "qwen": ("platforms/qwen-memory.md", ".qwen/QWEN.local.md"),
    "cline": ("platforms/cline-rule.md", ".clinerules/multi-agent-collab.md"),
    "roo": ("platforms/roo-rule.md", ".roo/rules/00-multi-agent-collab.md"),
    "continue": ("platforms/continue-rule.md", ".continue/rules/multi-agent-collab.md"),
    "kiro": ("platforms/kiro-steering.md", ".kiro/steering/multi-agent-collab.md"),
}
WORKBUDDY_HEADER = "platforms/workbuddy-header.md"
WORKBUDDY_TARGET = ".workbuddy-ai/git-flow-agent.md"

# 安装器、platforms 命令和展示页共用这一份目录；原生平台不生成空壳配置目录。
PLATFORM_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "opencode": "OpenCode",
    "pi": "Pi",
    "trae": "Trae",
    "workbuddy": "WorkBuddy",
    "cursor": "Cursor",
    "copilot": "GitHub Copilot",
    "windsurf": "Windsurf",
    "gemini": "Gemini CLI",
    "qwen": "Qwen Code",
    "cline": "Cline",
    "roo": "Roo Code",
    "continue": "Continue",
    "kiro": "Kiro",
}
NATIVE_PLATFORMS = {"codex", "opencode", "pi"}
ALL_PLATFORMS = list(PLATFORM_LABELS)
INSTALL_META = ".agents/install.json"
LEGACY_ENTRY_BEGIN = "<!-- >>> multi-agent-collab:entry >>>"
LEGACY_ENTRY_END = "<!-- <<< multi-agent-collab:entry <<<"
ENTRY_BEGIN = LEGACY_ENTRY_BEGIN + " -->"
ENTRY_END = LEGACY_ENTRY_END + " -->"
SHARED_ENTRY_TARGETS = {"AGENTS.md", "CLAUDE.md", "GEMINI.md", ".qwen/QWEN.local.md", ".github/copilot-instructions.md"}


def platform_files(names: list[str]) -> list[tuple[str, str, str | None]]:
    files = []
    for name in names:
        if name == "claude":
            files.append(("CLAUDE.md", "CLAUDE.md", "project-overview"))
        if name in PLATFORM_FILES:
            src, dst = PLATFORM_FILES[name]
            files.append((src, dst, None))
        elif name == "workbuddy":
            files.append((WORKBUDDY_HEADER, WORKBUDDY_TARGET, "__mirror__"))
    return files


def platform_catalog() -> list[dict]:
    return [{"id": name, "label": label, "native": name in NATIVE_PLATFORMS,
             "files": [dst for _src, dst, _zone in platform_files([name])]}
            for name, label in PLATFORM_LABELS.items()]

# 这些平台**原生读取项目根 AGENTS.md**，套件无需为它们部署任何专属文件
# （官方约定：Codex CLI、OpenCode、Pi 都把根 AGENTS.md 当项目指令）。
# 安装器/doctor 只检测痕迹并提示，不写 .codex/.opencode/.pi 下的任何东西。
NATIVE_AGENTS_PLATFORM_HINT = (
    "根目录 AGENTS.md 即该平台的项目指令文件，新装/升级后开新对话即自动生效，无需额外入口"
)


def detect_native_agents_platforms(target: Path) -> list[str]:
    """识别目标项目里"原生读 AGENTS.md"的平台痕迹。纯只读，不写任何文件。

    - opencode：项目内有 .opencode/ 目录或 opencode.json 配置
    - pi：项目内有 .pi/ 目录（pi.dev 的项目配置目录；AGENTS.md 从工作目录自动发现）
    - codex：项目级无标记，靠 CODEX_HOME（默认 ~/.codex）识别是否装了 Codex CLI
    """
    hits: list[str] = []
    if (target / ".opencode").exists() or (target / "opencode.json").exists():
        hits.append("opencode")
    if (target / ".pi").exists():
        hits.append("pi")
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    if codex_home.is_dir():
        hits.append("codex")
    return hits

# .git/info/exclude 里被托管的标记块
MARK_BEGIN = "# >>> multi-agent-collab (local-only; managed by install.py) >>>"
MARK_END = "# <<< multi-agent-collab <<<"
# 「本机私有路径清单」的唯一事实源 —— AGENTS.md / README / 两个平台入口里的清单
# 必须与此一致（tests/selftest.py 有一致性断言，漂移立刻红）。
# Only the private directory and exact platform entry paths belong to the kit.
LEGACY_CORE_FILES = list(CORE_FILES)
STATE_DIR = ".multi-agent-collab"

def layout_path(relative: str) -> str:
    if relative.startswith(".agents/"):
        return STATE_DIR + relative[len(".agents"):]
    if relative.startswith("tools/agents_") and STATE_DIR != ".agents":
        return STATE_DIR + "/" + relative
    return relative


def configure_layout(target: Path | None = None) -> None:
    global STATE_DIR, CORE_FILES, INSTALL_META, POLICY_FILE, POLICY_DOC, EXCLUDE_ENTRIES
    STATE_DIR = ".multi-agent-collab"
    # Existing recorded installs retain their layout. Never adopt an unrelated .agents/.
    if target is not None and not (target / STATE_DIR).exists():
        raw = read_text(target / ".agents/install.json")
        if not raw:
            primary = primary_worktree(target)
            if primary is not None and primary != target and not (primary / STATE_DIR).exists():
                raw = read_text(primary / ".agents/install.json")
        try:
            legacy = json.loads(raw or "{}")
        except ValueError:
            legacy = {}
        if isinstance(legacy, dict) and legacy.get("kit_version") and legacy.get("source_dir"):
            STATE_DIR = ".agents"
    CORE_FILES = [(src, layout_path(dst), zone) for src, dst, zone in LEGACY_CORE_FILES]
    INSTALL_META = STATE_DIR + "/install.json"
    POLICY_FILE = STATE_DIR + "/policy.json"
    POLICY_DOC = STATE_DIR + "/POLICY.md"
    EXCLUDE_ENTRIES = ["/" + STATE_DIR + "/", "/AGENTS.md", "/CLAUDE.md"]
    EXCLUDE_ENTRIES += ["/" + dst for _src, dst, _zone in platform_files(ALL_PLATFORMS)]
    if STATE_DIR == ".agents":
        EXCLUDE_ENTRIES += ["/tools/agents_" + name + ".py" for name in ("status", "session", "policy", "layout")]
    EXCLUDE_ENTRIES = list(dict.fromkeys(EXCLUDE_ENTRIES))


configure_layout()


def render_layout(text: str) -> str:
    if STATE_DIR == ".agents":
        return text.replace(".multi-agent-collab/tools/agents_", "tools/agents_").replace(".multi-agent-collab/", ".agents/")
    return text


ZONE_RE = re.compile(
    r"(<!-- >>> mac:zone:(\S+)[^\n]*-->\n)(.*?)(\n<!-- <<< mac:zone:\2 -->)",
    re.S,
)


# --------------------------------------------------------------------------- #
# 读/写/备份
# --------------------------------------------------------------------------- #

def read_text(path: Path) -> str | None:
    """读 UTF-8 文本；文件不存在或不是合法 UTF-8（如老 GBK 文件）都返回 None，绝不抛异常。"""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def write_lf(path: Path, content: str) -> None:
    """统一以 LF + UTF-8 原子写入（同目录临时文件 + os.replace）。

    .git 元数据类文件怕 CRLF（见手册 B12），也怕写到一半进程被杀（手册 A4）：
    直接 open("w") 会先截断目标，崩溃后只剩半个文件。临时文件与目标同目录，
    os.replace 在同卷上是原子替换。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_dest(dest: Path, target: Path) -> Path:
    """备份统一放 <target>/.agents/backups/（被 /​.agents/ 忽略，绝不污染 git status）。"""
    rel = dest.relative_to(target)
    flat = str(rel).replace("\\", "/").replace("/", "__")
    bdir = target / STATE_DIR / "backups"
    ts = stamp()
    cand = bdir / f"{flat}.{ts}.bak"
    i = 2
    while cand.exists():       # 同一秒内多次覆盖：加序号，绝不冲掉上一份
        cand = bdir / f"{flat}.{ts}-{i}.bak"
        i += 1
    return cand


def install_file(dest: Path, content: str, target: Path,
                 dry_run: bool, stats: dict) -> None:
    """内容相同跳过；不同先字节备份再覆盖。"""
    if dest.exists():
        try:
            current = dest.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            current = None     # 旧文件非 UTF-8：无法逐字比较，按"不同"处理，字节备份
        except OSError:
            current = None
        if current == content:
            print(f"  = 跳过（相同）  {dest}")
            stats["skip"] += 1
            return
        bk = backup_dest(dest, target)
        print(f"  ~ 覆盖（旧版备份 {bk.relative_to(target)}）  {dest}")
        if not dry_run:
            bk.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, bk)   # 字节级备份，非 UTF-8 旧文件也不会在这一步炸
    else:
        print(f"  + 新建  {dest}")
    if not dry_run:
        write_lf(dest, content)
    stats["write"] += 1


def extract_zones(text: str) -> dict[str, str]:
    return {m.group(2): m.group(3) for m in ZONE_RE.finditer(text.replace("\r\n", "\n"))}


def render(template: str, old: str | None, project_name: str) -> str:
    """模板渲染：替换项目名；把旧文件的定制区内容迁移进新模板。"""
    text = template.replace("{{PROJECT_NAME}}", project_name)
    if not old:
        return text
    old_zones = extract_zones(old)

    def repl(m: re.Match) -> str:
        head, name, _inner, tail = m.group(1), m.group(2), m.group(3), m.group(4)
        if name in old_zones:
            return head + old_zones[name] + tail
        return m.group(0)

    return ZONE_RE.sub(repl, text)


# ---- TROUBLESHOOTING 的 D 组（项目专属条目）合并 ----

D_HEAD_RE = re.compile(r"^##\s+(D\d+)\s.*$", re.M)


def extract_d_entries(text: str) -> list[str]:
    """从旧版手册里抽出用户自填的 `## Dn …` 条目（标题到下一个 ## 标题之间）。

    kit 自带的占位条目（标题含「示例占位」）不算用户内容。
    """
    matches = list(D_HEAD_RE.finditer(text))
    out: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start():end].strip().strip("-").strip()
        if "示例占位" in m.group(0):
            continue
        out.append(block)
    return out


def merge_troubleshooting(template: str, old: str | None, project_name: str) -> str:
    """新手册 + 旧手册 → 安装内容。

    - 旧版已有 mac:zone 标记：标准 zone 迁移（用户 D 组原样保留）
    - 旧版无标记：扫描旧文件里的 D 组条目，自动搬进新版迁移区
    """
    text = template.replace("{{PROJECT_NAME}}", project_name)
    if not old:
        return text
    if "mac:zone:project-troubles" in old:
        return render(template, old, project_name)
    entries = extract_d_entries(old)
    if not entries:
        return text
    migrated = ("\n> 以下条目从旧版手册自动迁移（升级时保留，可自由编辑）：\n\n"
                + "\n\n---\n\n".join(entries) + "\n")

    def repl(m: re.Match) -> str:
        return m.group(1) + "\n" + migrated + m.group(4)
    return ZONE_RE.sub(repl, text, count=1)


# --------------------------------------------------------------------------- #
# git 辅助
# --------------------------------------------------------------------------- #

def git_ok(target: Path, *args: str) -> tuple[bool, str]:
    git = resolve_git()
    if not git:
        return False, "git 不可用（PATH 与常见安装路径都没找到；可用环境变量 " + GIT_ENV_VAR + " 指定）"
    try:
        r = subprocess.run(
            [git, *args], cwd=str(target), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        return r.returncode == 0, r.stdout if r.returncode == 0 else (r.stderr or r.stdout)
    except Exception as e:
        return False, str(e)


def is_git_repo(target: Path) -> bool:
    ok, _ = git_ok(target, "rev-parse", "--is-inside-work-tree")
    return ok


def primary_worktree(target: Path) -> Path | None:
    """主工作区路径（git worktree list 的第一条）；不是 git 仓库时返回 None。"""
    ok, out = git_ok(target, "worktree", "list", "--porcelain")
    if not ok:
        return None
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line[9:].strip()).resolve()
    return None


def in_linked_worktree(target: Path) -> bool:
    p = primary_worktree(target)
    return p is not None and p != target.resolve()


def exclude_paths(target: Path) -> list[Path]:
    """返回需要维护 ignore 的 exclude 路径。

    - 普通仓库：<.git>/info/exclude
    - linked worktree（.git 是文件）：该 worktree 私有 git 目录一份 + 公共目录一份，
      两处都写，无论用户从主工作区还是 worktree 安装都不漏。
    """
    ok, out = git_ok(target, "rev-parse", "--path-format=absolute",
                     "--git-path", "info/exclude")
    if not ok or not out.strip():
        # 老版本 git 没有 --path-format（<2.31）：相对路径相对命令 cwd
        ok, out = git_ok(target, "rev-parse", "--git-path", "info/exclude")
        if not ok or not out.strip():
            return []
    locs: list[Path] = []
    first = Path(out.strip())
    if not first.is_absolute():
        first = (target / first).resolve()
    locs.append(first)

    ok2, common = git_ok(target, "rev-parse", "--path-format=absolute",
                         "--git-common-dir")
    if not ok2 or not common.strip():
        ok2, common = git_ok(target, "rev-parse", "--git-common-dir")
    if ok2 and common.strip():
        cp = Path(common.strip())
        if not cp.is_absolute():
            cp = (target / cp).resolve()
        cp_ex = cp / "info" / "exclude"
        if cp_ex not in locs:
            locs.append(cp_ex)
    return locs


# --------------------------------------------------------------------------- #
# exclude 托管块
# --------------------------------------------------------------------------- #

def strip_managed(current: str) -> str:
    """去掉旧的完整托管块；顺手清理只剩一半的残片标记行。"""
    full = re.compile(
        r"^[ \t]*" + re.escape(MARK_BEGIN) + r".*?" + re.escape(MARK_END)
        + r"[ \t]*\r?\n?",
        re.S | re.M,
    )
    text = full.sub("", current)
    text = re.sub(r"^[ \t]*" + re.escape(MARK_BEGIN) + r".*$(\r?\n)?",
                  "", text, flags=re.M)
    text = re.sub(r"^[ \t]*" + re.escape(MARK_END) + r".*$(\r?\n)?",
                  "", text, flags=re.M)
    return text


def managed_block(entries=None) -> str:
    return MARK_BEGIN + "\n" + "\n".join(EXCLUDE_ENTRIES if entries is None else entries) + "\n" + MARK_END + "\n"


def update_excludes(target: Path, dry_run: bool, entries=None) -> str:
    """幂等更新全部 exclude 位置。返回 'updated' / 'ok' / 'nogit'。"""
    locs = exclude_paths(target)
    if not locs:
        return "nogit"
    state = "ok"
    for exclude in locs:
        current = read_text(exclude)
        if current is None:
            if not exclude.exists():
                current = ""
            else:
                # 读不了（存在但非 UTF-8）：绝不静默跳过——否则结尾可能误报"已是最新"
                try:
                    where = str(exclude.relative_to(target))
                except ValueError:
                    where = str(exclude) + "（git 公共目录）"
                print(f"  ⚠ 跳过非 UTF-8 的忽略文件 {where}：未改写其内容，"
                      "托管块也没写进去；请先把它转成 UTF-8 后重跑安装器，"
                      "或手工把 EXCLUDE_ENTRIES 加进去")
                return "error"
        cleaned = strip_managed(current)
        if cleaned and not cleaned.endswith("\n"):
            cleaned += "\n"
        new_text = cleaned + managed_block(entries)
        if new_text == current:
            continue
        try:
            rel = exclude.relative_to(target)
            where = str(rel)
        except ValueError:
            where = str(exclude) + "（git 公共目录）"
        print(f"  ~ 更新忽略规则  {where}")
        if not dry_run:
            write_lf(exclude, new_text)
        state = "updated"
    return state


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #

def load_project_name(target: Path, cli_name: str | None) -> str:
    if cli_name:
        return cli_name
    # 本工作区 → 主工作区（linked worktree 首装时沿用项目名，不用 worktree 目录名）
    for base in (target, primary_worktree(target)):
        if not base:
            continue
        saved = read_meta(base).get("project_name")
        if saved:
            return saved
    return target.name


def read_meta(target: Path) -> dict:
    raw = read_text(target / INSTALL_META)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_meta(target: Path, name: str, platforms: list[str], dry_run: bool,
              update_core: bool = True, project_entries=None) -> None:
    old = read_meta(target)
    payload = dict(old, **{
        "project_name": name,
        "kit_version": KIT_VERSION if update_core else old.get("kit_version", "unknown"),
        "entry_version": KIT_VERSION,
        "schema_version": 3,
        "owner": "multi-agent-collab",
        "layout": STATE_DIR,
        "platforms": platforms,
        "source_dir": str(KIT_ROOT),
        "installed_at": old.get("installed_at") or datetime.now().isoformat(timespec="seconds"),
    })
    hashes = dict(old.get("managed_hashes", {}))
    bases = dict(old.get("zone_base_hashes", {}))
    managed = CORE_FILES + platform_files(platforms) if update_core else [("", "AGENTS.md", None)] + platform_files(platforms)
    for _src, rel, _zone in managed:
        path = target / rel
        if path.is_file():
            hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            text = read_text(path) or ""
            if extract_zones(text):
                bases[rel] = zone_base_hash(text)
    payload["managed_hashes"] = hashes
    payload["zone_base_hashes"] = bases
    payload["project_entries"] = sorted(set(project_entries if project_entries is not None else old.get("project_entries", [])))
    dest = target / INSTALL_META
    if not dry_run and payload != old:
        write_lf(dest, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def parse_platforms(raw: str) -> list[str]:
    raw = raw.strip().lower()
    if raw == "none":
        return []
    if raw == "all":
        return list(ALL_PLATFORMS)
    aliases = {label.lower().replace(" ", "-"): key for key, label in PLATFORM_LABELS.items()}
    aliases.update({"trae-ide": "trae", "kiro-cli": "kiro"})
    names = [aliases.get(x.strip().replace(" ", "-"), x.strip()) for x in raw.split(",") if x.strip()]
    bad = [x for x in names if x not in ALL_PLATFORMS]
    if bad or not names:
        raise ValueError(f"未知或空的平台选择：{', '.join(bad) or raw!r}；"
                         f"可选：{', '.join(ALL_PLATFORMS)}，或单独使用 all / none")
    return [name for name in ALL_PLATFORMS if name in names]


def saved_platforms(target: Path) -> list[str] | None:
    """优先沿用本工作区；linked worktree 首装时继承主工作区的选择。"""
    for base in (target, primary_worktree(target)):
        if base is None:
            continue
        meta = read_meta(base)
        if "platforms" in meta:
            names = meta["platforms"]
            if not isinstance(names, list) or any(name not in ALL_PLATFORMS for name in names):
                raise ValueError(f"{base / INSTALL_META} 的 platforms 无效，请检查安装记录")
            return [name for name in ALL_PLATFORMS if name in names]
        if meta:
            # 1.4 及更早版本没有平台记录，只迁移实际存在的入口，不能重新默认 all。
            return [name for name in ALL_PLATFORMS
                    if any((base / dst).is_file() for _src, dst, _zone in platform_files([name]))]
    return None


def choose_platforms(target: Path, raw: str | None, dry_run: bool = False) -> list[str]:
    previous = saved_platforms(target)
    if raw is not None:
        selected = parse_platforms(raw)
        return [name for name in ALL_PLATFORMS if name in (previous or []) + selected]
    if previous is not None:
        return previous
    if dry_run:
        print("  ℹ 未选择平台，本次仅预览通用规则；正式安装请指定 --platforms。")
        return []
    if not sys.stdin.isatty():
        raise ValueError("首次安装需要选择平台；非交互环境请传 --platforms codex（可逗号多选），"
                         "或 --platforms none 只安装通用规则。不会默认全装。")
    print("选择你在当前项目使用的平台（可多选）：")
    for i, item in enumerate(platform_catalog(), 1):
        detail = "直接读 AGENTS.md，无额外文件" if item["native"] else "、".join(item["files"])
        print(f"  {i}. {item['label']} ({item['id']}) — {detail}")
    hints = detect_native_agents_platforms(target)
    if hints:
        print(f"检测到 {', '.join(hints)} 的安装痕迹，仅供选择参考。")
    try:
        answer = input("输入名称或编号，逗号分隔；回车只安装通用规则：").strip()
    except EOFError:
        raise ValueError("没有收到平台选择；非交互环境请指定 --platforms，未安装任何文件") from None
    except KeyboardInterrupt:
        raise ValueError("平台选择已取消，未安装任何文件") from None
    tokens = []
    for token in answer.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(ALL_PLATFORMS):
            token = ALL_PLATFORMS[int(token) - 1]
        tokens.append(token)
    return parse_platforms(",".join(tokens) or "none")


def resolve_target(raw: str) -> Path:
    target = Path(raw).expanduser().resolve()
    if target.is_dir():
        ok, root = git_ok(target, "rev-parse", "--show-toplevel")
        if ok and root.strip():
            target = Path(root.strip()).resolve()
    try:
        target.relative_to(KIT_ROOT)
    except ValueError:
        return target
    raise ValueError("目标在 kit 目录内部，拒绝安装或同步。套件模板是产品源码；"
                     "请指定另一个 git 项目。")


def entry_span(text: str, label: str):
    begins = list(re.finditer(re.escape(LEGACY_ENTRY_BEGIN) + r"(?: -->)?", text))
    ends = list(re.finditer(re.escape(LEGACY_ENTRY_END) + r"(?: -->)?", text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or begins[0].start() > ends[0].start():
        raise ValueError(f"{label} 的入口托管标记不完整、重复或顺序错误；未写入文件")
    return begins[0].start(), ends[0].end()


def entry_content(src: str, dst: str, zone: str | None, project_name: str,
                  agents: str, old: str | None = None) -> str:
    template = read_text(KIT_ROOT / src)
    if template is None:
        raise ValueError(f"缺少或无法读取 kit 源文件 {src}，未写入任何文件")
    content = render_layout(render(template, None, project_name))
    # WorkBuddy now reads the shared rules through a small entry too.
    block = ENTRY_BEGIN + "\n" + content.rstrip() + "\n" + ENTRY_END
    span = entry_span(old or "", dst)
    if span is not None:
        begin, end = span
        if content.startswith("---\n") and old.startswith(("---\n", "---\r\n")):
            front_end = content.find("\n---\n", 4)
            if front_end >= 0:
                block = ENTRY_BEGIN + "\n" + content[front_end + 5:].strip() + "\n" + ENTRY_END
        if end < begin:
            raise ValueError(f"{dst} 的入口托管标记顺序错误")
        if "\r\n" in old[begin:end]:
            block = block.replace("\n", "\r\n")
        return old[:begin] + block + old[end:]
    if old:
        return old + ("" if old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n") + block + "\n"
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end >= 0:
            header, body = content[:end + 5], content[end + 5:]
            return header + "\n" + ENTRY_BEGIN + "\n" + body.strip() + "\n" + ENTRY_END + "\n"
    return block + "\n"


def install_plan(target: Path, project_name: str, platforms: list[str],
                 sync_only: bool = False) -> list[tuple[str, str]]:
    plan = []
    agents = read_text(target / "AGENTS.md") or ""
    for src, dst, zone in ([] if sync_only else CORE_FILES):
        template = read_text(KIT_ROOT / src)
        if template is None:
            raise ValueError(f"缺少或无法读取 kit 源文件 {src}，未写入任何文件")
        old = read_text(target / dst)
        if dst.endswith("/RULES.md") and old is None and STATE_DIR == ".agents":
            old = read_text(target / "AGENTS.md")
        if zone == "__entry__":
            plan.append((dst, entry_content(src, dst, zone, project_name, agents, old)))
            continue
        if not src.startswith("tools/"):
            template = render_layout(template)
        if zone == "__troubles__":
            content = merge_troubleshooting(template, old, project_name)
            if old and "mac:zone:project-troubles" not in old:
                entries = extract_d_entries(old)
                if entries:
                    print(f"  ↻ 从旧版手册迁移了 {len(entries)} 条 D 组项目专属条目")
        else:
            content = render(template, old, project_name)
            if zone and (target / dst).exists() and (old is None or "mac:zone:" not in old):
                print(f"  ⚠ {dst} 非 UTF-8 或没有定制区标记，定制内容无法自动迁移；"
                      "覆盖前会字节备份，安装后请比对备份补回项目定制。")
        if dst == "AGENTS.md":
            agents = content
        plan.append((dst, content))
    if sync_only:
        plan.append(("AGENTS.md", entry_content("platforms/agents-entry.md", "AGENTS.md", None, project_name, agents, agents)))
    for src, dst, zone in platform_files(platforms):
        plan.append((dst, entry_content(src, dst, zone, project_name, agents,
                                        read_text(target / dst))))
    return plan


def preflight(target: Path, paths: list[str], dry_run: bool) -> None:
    """在覆盖规则前一次检查目标、已跟踪文件及忽略文件，避免部分安装。"""
    if target.exists() and not target.is_dir():
        raise ValueError(f"目标不是目录：{target}")
    for rel in paths:
        dest = target / rel
        try:
            dest.resolve().relative_to(target)
        except ValueError:
            raise ValueError(f"{rel} 经符号链接指向项目外部，拒绝覆盖") from None
        if dest.exists() and not dest.is_file():
            raise ValueError(f"{rel} 不是普通文件，拒绝覆盖")
        if dest.is_symlink() or getattr(dest, "is_junction", lambda: False)():
            raise ValueError(f"{rel} 是链接路径，拒绝覆盖")
        for parent in dest.parents:
            if parent == target:
                break
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise ValueError(f"{rel} 的父路径是链接，拒绝覆盖")
            if parent.exists() and not parent.is_dir():
                raise ValueError(f"{rel} 的父路径 {parent.name} 已是文件，不能创建规则目录；未安装任何文件")
    if not is_git_repo(target):
        if dry_run:
            print("  ℹ 目标还不是可访问的 git 仓库，仅预览；正式安装需 git 仓库以保证本地忽略。")
            return
        raise ValueError("目标不是可访问的 git 仓库，无法保证本地忽略。请检查项目路径与 Git；"
                         "未创建或覆盖任何文件，也不会自动 git init。")
    ok, tracked = git_ok(target, "ls-files", "-z", "--", *paths)
    if not ok:
        raise ValueError(f"无法核对 Git 跟踪状态：{tracked}")
    tracked_names = tracked.rstrip("\0").split("\0") if tracked else []
    private_tracked = [name for name in tracked_names if name not in entry_targets() and name != ".gitignore"]
    if private_tracked:
        names = ", ".join(private_tracked)
        raise ValueError(f"以下安装目标已被 Git 跟踪：{names}。info/exclude 对已跟踪文件无效；"
                         "请先核对私有目录的跟踪状态，安装器不会修改索引或覆盖它们。")
    locs = exclude_paths(target)
    if not locs:
        raise ValueError("无法定位 git info/exclude，未安装任何文件")
    for path in locs:
        if path.exists() and read_text(path) is None:
            raise ValueError(f"忽略文件非 UTF-8 或不可读取：{path}；请先修复编码或权限，未安装任何文件")


def unignored_paths(target: Path, paths: list[str], project_entries=None) -> list[str]:
    git = resolve_git()
    if not git:
        raise ValueError("Git 不可用，无法验证本地忽略")
    result = subprocess.run([git, "check-ignore", "--stdin", "-z"], cwd=str(target),
                            input="\0".join(paths) + "\0", capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=20)
    if result.returncode not in (0, 1):
        raise ValueError(f"忽略验证失败：{result.stderr.strip()}")
    ignored = set(result.stdout.rstrip("\0").split("\0"))
    ok, tracked = git_ok(target, "ls-files", "-z", "--", *paths)
    if not ok:
        raise ValueError("无法验证入口跟踪状态")
    shared_tracked = set(tracked.rstrip("\0").split("\0")) & entry_targets()
    project_owned = set(project_entries if project_entries is not None else read_meta(target).get("project_entries", [])) & entry_targets()
    return [path for path in paths if path not in ignored and path not in shared_tracked and path not in project_owned and path != ".gitignore"]


def apply_install_plan(target: Path, plan: list[tuple[str, str]], dry_run: bool) -> dict:
    paths = [dst for dst, _content in plan] + [INSTALL_META]
    preflight(target, paths + [".gitignore"], dry_run)
    if dry_run:
        for dst, content in plan:
            old = read_text(target / dst)
            if old is not None and old != content:
                print("".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile=dst, tofile=dst + " (planned)")))
    ownership_preflight(target, plan)
    meta = read_meta(target)
    project_entries = set(meta.get("project_entries", []))
    for path in paths:
        old = read_text(target / path)
        if path in entry_targets() and old is not None and path not in meta.get("managed_hashes", {}) and LEGACY_ENTRY_BEGIN not in old:
            project_entries.add(path)
    print("【git 本地忽略】")
    update_gitignore(target, dry_run)
    ignore_paths = list(dict.fromkeys([dst for _src, dst, _zone in CORE_FILES] + paths))
    ignores = ["/" + STATE_DIR + "/"] + ["/" + path for path in ignore_paths if (path in entry_targets() and path not in project_entries) or path.startswith("tools/agents_")]
    state = update_excludes(target, dry_run, ignores)
    if state == "error":
        raise ValueError("本地忽略未能写入，未安装规则文件")
    if state == "ok":
        print("  = 忽略规则已是最新")
    if not dry_run:
        missing = unignored_paths(target, paths, project_entries)
        if missing:
            raise ValueError(f"以下路径未被忽略：{', '.join(missing)}。请检查 .gitignore 的反向规则；"
                             "本次只更新了 Git 忽略标记区，未写入规则文件。")
    stats = {"write": 0, "skip": 0, "project_entries": sorted(project_entries)}
    print("【文件】")
    for dst, content in plan:
        install_file(target / dst, content, target, dry_run, stats)
    return stats


def entry_targets() -> set[str]:
    return SHARED_ENTRY_TARGETS | {dst for _src, dst, _zone in platform_files(ALL_PLATFORMS)}


IGNORE_BEGIN = "# >>> multi-agent-collab:private >>>"
IGNORE_END = "# <<< multi-agent-collab:private <<<"


def gitignore_content(target: Path) -> str:
    path = target / ".gitignore"
    old = read_text(path)
    if path.exists() and old is None:
        raise ValueError(".gitignore 非 UTF-8 或不可读取；未安装任何文件")
    old = old or ""
    block = IGNORE_BEGIN + "\n/" + STATE_DIR + "/\n" + IGNORE_END
    if IGNORE_BEGIN in old or IGNORE_END in old:
        if old.count(IGNORE_BEGIN) != 1 or old.count(IGNORE_END) != 1:
            raise ValueError(".gitignore 套件标记不完整或重复；未写入")
        begin, end = old.index(IGNORE_BEGIN), old.index(IGNORE_END) + len(IGNORE_END)
        if end < begin:
            raise ValueError(".gitignore 套件标记顺序错误；未写入")
        return old[:begin] + block + old[end:]
    return old + ("\n" if old and not old.endswith("\n") else "") + block + "\n"


def update_gitignore(target: Path, dry_run: bool) -> None:
    content = gitignore_content(target)
    if read_text(target / ".gitignore") != content:
        print(f"  ~ .gitignore 默认忽略 /{STATE_DIR}/（保留项目其它规则）")
        if not dry_run:
            write_lf(target / ".gitignore", content)


def zone_base_hash(text: str) -> str:
    return hashlib.sha256(ZONE_RE.sub(lambda m: m[1] + m[4], text.replace("\r\n", "\n")).encode("utf-8")).hexdigest()


def ownership_preflight(target: Path, plan: list[tuple[str, str]]) -> None:
    gitignore_content(target)  # Validate every shared file before the first write.
    meta = read_meta(target)
    private = target / STATE_DIR
    if private.exists() and STATE_DIR != ".agents" and meta.get("owner") != "multi-agent-collab":
        raise ValueError(f"{STATE_DIR}/ 已存在但无法确认属于本套件；保留现场，未安装任何文件")
    hashes = meta.get("managed_hashes", {})
    if not isinstance(hashes, dict):
        raise ValueError("安装文件归属清单损坏；未写入")
    for rel, content in plan:
        dest = target / rel
        if not dest.exists():
            continue
        old = read_text(dest)
        if old is None:
            raise ValueError(f"{rel} 非 UTF-8 或不可读取；保留现场，未安装任何文件")
        if rel in entry_targets():
            if rel not in SHARED_ENTRY_TARGETS and rel not in hashes and LEGACY_ENTRY_BEGIN not in old:
                raise ValueError(f"{rel} 已存在且归属未知；保留原入口，未安装任何文件")
            continue
        if old == content or rel in (POLICY_FILE, POLICY_DOC):
            continue
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        if rel in hashes and digest == hashes[rel]:
            continue
        # Explicit customization zones are an established, lossless upgrade contract.
        if rel.endswith(("/RULES.md", "/TROUBLESHOOTING.md")) and extract_zones(old):
            if zone_base_hash(old) == meta.get("zone_base_hashes", {}).get(rel):
                continue
            template = next((read_text(KIT_ROOT / src) for src, dst, _z in CORE_FILES if dst == rel), None)
            if template and render_layout(render(template, old, meta.get("project_name", target.name))) == old:
                continue
        raise ValueError(f"{rel} 已修改或归属未知；请对照 --dry-run 和备份处理，未覆盖任何文件")


def policy_plan(target: Path, args=None):
    primary = primary_worktree(target)
    # A fresh isolated installation must not adopt another tool's .agents/policy.json.
    if STATE_DIR != ".agents" and primary in (None, target) and not (target / POLICY_FILE).exists():
        current = build_policy()
    else:
        current = effective_policy(target, primary)
    changed = args is not None and any(getattr(args, key, None) is not None for key in
                                      ("workflow", "base_branch", "task_prefixes", "protect_branches"))
    if changed:
        prefixes = args.task_prefixes.split(",") if args.task_prefixes is not None else None
        protected = args.protect_branches.split(",") if args.protect_branches is not None else None
        policy = build_policy(args.workflow or current["workflow"], args.base_branch or
                              (None if args.workflow else current["base_branch"]),
                              prefixes if prefixes is not None else (None if args.workflow else current["task_prefixes"]),
                              protected if protected is not None else current["protected_branches"])
        if primary is not None and primary != target and policy != current:
            raise ValueError(f"请在主工作区 {primary} 修改仓库策略，再同步各 worktree")
    else:
        policy = current
    return [(POLICY_FILE, json.dumps(policy, ensure_ascii=False, indent=2) + "\n"),
            (POLICY_DOC, render_policy(policy))]


def cmd_install(args: argparse.Namespace) -> int:
    try:
        target = resolve_target(args.target)
    except ValueError as exc:
        # 保留自安装保护原有的 stdout 诊断契约。
        print(f"[FAIL] {exc}")
        return 2
    platforms = choose_platforms(target, args.platforms, args.dry_run)
    project_name = load_project_name(target, args.project_name)
    print("multi-agent-collab 安装器")
    print(f"  kit 源目录  {KIT_ROOT}")
    print(f"  目标项目    {target}")
    print(f"  项目名      {project_name}")
    print(f"  已选平台    {', '.join(platforms) or 'none（仅通用规则）'}")
    print(f"  选择会保存在 {INSTALL_META}；重装沿用，新增平台累加，已有入口不删除。")
    if args.dry_run:
        print("  （--dry-run：只演示，不写任何文件）")
    plan = install_plan(target, project_name, platforms)
    plan.extend(policy_plan(target, args))
    stats = apply_install_plan(target, plan, args.dry_run)
    save_meta(target, project_name, platforms, args.dry_run, project_entries=stats["project_entries"])
    for name in platforms:
        if name in NATIVE_PLATFORMS:
            print(f"  ✔ {PLATFORM_LABELS[name]}：{NATIVE_AGENTS_PLATFORM_HINT}")
    print("\n【安装后验证】")
    print(f"  1. 保留原项目规则；在 {STATE_DIR}/RULES.md 项目定制区填写缺少的测试/构建命令。")
    print(f"  2. 在项目根目录运行 python {layout_path('tools/agents_status.py')} --brief。")
    print(f"  3. 新开对话，确认 AI 读取 {STATE_DIR}/START_HERE.md、看板、inbox 和近期 worklog。")
    prefix = "演练完成（未写盘）：将" if args.dry_run else "完成："
    print(f"{prefix}新建/覆盖 {stats['write']} 个，相同跳过 {stats['skip']} 个。"
          f"旧版备份统一在 {STATE_DIR}/backups/。")
    print(f'体检：python "{KIT_ROOT / "install.py"}" doctor "{target}"')
    print(f'新增平台：python "{KIT_ROOT / "install.py"}" sync "{target}" --platforms cursor')
    print(f"项目策略：{target / POLICY_DOC}（配置不修改实际 Git 分支）")
    return 0


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def cmd_doctor(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    fails, warns = 0, 0

    def ok(msg: str) -> None:
        print(f"  [OK]   {msg}")

    def warn(msg: str) -> None:
        nonlocal warns
        warns += 1
        print(f"  [WARN] {msg}")

    def fail(msg: str) -> None:
        nonlocal fails
        fails += 1
        print(f"  [FAIL] {msg}")

    print(f"multi-agent-collab 体检  {target}\n")

    if not target.is_dir():
        print(f"[FAIL] 目标目录不存在：{target}")
        return 2
    ok(f"目标目录存在（Python {sys.version_info.major}.{sys.version_info.minor}）")

    meta = read_meta(target)
    policy = effective_policy(target, primary_worktree(target))
    if meta:
        inst_ver = meta.get("kit_version", "未知（旧版本安装）")
        if inst_ver == KIT_VERSION:
            ok(f"套件版本 {inst_ver}（install.json）")
        else:
            warn(f"安装时版本 {inst_ver}，当前 kit 是 {KIT_VERSION}"
                 " —— 建议跑一次 install.py install 升级（定制区会自动保留）")
    elif (target / "AGENTS.md").exists():
        warn("没有 .agents/install.json（1.0 版之前手工安装？）—— 重跑一次安装器可登记版本")

    print("\n【项目分支策略】")
    ok(f"{policy['workflow']}；PR 基线 {policy['base_branch']}；任务前缀 {', '.join(policy['task_prefixes'])}")
    if read_text(target / POLICY_DOC) != render_policy(policy):
        warn("项目策略说明未生成或已过期，请执行 install / sync 同步")
    base_ready, _ = git_ok(target, "rev-parse", "--verify", "refs/heads/" + policy["base_branch"])
    remote_ready, _ = git_ok(target, "rev-parse", "--verify", "refs/remotes/origin/" + policy["base_branch"])
    if not base_ready and not remote_ready:
        warn(f"未找到基线 {policy['base_branch']}；请按项目实际流程确认，不会自动建立远端分支")

    print("\n【核心文件】")
    main_wt = primary_worktree(target)
    linked = main_wt is not None and main_wt != target
    sibling_ready = linked and main_wt is not None and all(
        (main_wt / dst).is_file() for _src, dst, _z in CORE_FILES)
    if linked and sibling_ready:
        print(f"  ℹ️  这是 linked worktree；主工作区 {main_wt} 已装齐本套件。")
    for _src, dst_rel, _zone in CORE_FILES:
        p = target / dst_rel
        if p.is_file():
            ok(dst_rel)
        elif sibling_ready:
            warn(f"{dst_rel} 本 worktree 未拷贝 —— 在本目录重跑 install.py 即可补齐"
                 "（忽略规则已在 git 层生效）")
        else:
            fail(dst_rel)

    print("\n【已选平台与启动入口】")
    platforms = saved_platforms(target) or []
    agents = read_text(target / "AGENTS.md")
    project_name = load_project_name(target, None)
    expected_root = entry_content("platforms/agents-entry.md", "AGENTS.md", None, project_name, agents or "", agents)
    (ok if agents == expected_root else warn if sibling_ready and agents is None else fail)("AGENTS.md 协作标记入口完整")
    for name in ALL_PLATFORMS:
        if name not in platforms:
            print(f"  [--]   {name}: 未启用，不要求安装入口")
            continue
        if name in NATIVE_PLATFORMS:
            (ok if agents else warn if sibling_ready else fail)(
                f"{name}: 原生读取 AGENTS.md，无额外配置文件")
            if name == "codex" and (target / "AGENTS.override.md").is_file():
                warn("Codex 会优先读取 AGENTS.override.md；请确认其中引导读取本套件总章，安装器不会覆盖它")
        for src, dst, zone in platform_files([name]):
            actual = read_text(target / dst)
            if actual is None:
                (warn if sibling_ready else fail)(f"{name}: 已选平台入口缺失或非 UTF-8：{dst}")
                continue
            expect = entry_content(src, dst, zone, project_name, agents or "", actual)
            if actual == expect:
                ok(f"{name}: {dst}（启动入口已同步）")
            else:
                warn(f"{name}: {dst} 与当前规则不一致，运行 install.py sync 刷新")

    print("\n【git 与本地忽略】")
    if not is_git_repo(target):
        if (target / ".git").exists():
            warn(f"检测到 .git 但 git 调不动：请把 Git 加入 PATH，或用环境变量 "
                 f"{GIT_ENV_VAR} 指向 git 可执行文件后重跑体检")
        else:
            warn("不是 git 仓库（或 git 不可用）；git init 后重跑安装器即可补上忽略规则")
    else:
        locs = exclude_paths(target)
        if not locs:
            fail("git 仓库识别成功但取不到 info/exclude 路径")
        else:
            has_block = any(MARK_BEGIN in (read_text(p) or "") for p in locs)
            (ok if has_block else fail)("托管忽略块存在（私有/公共 exclude 至少一处）")
            # 验证本次实际管理的路径。未选平台已有的团队配置不属于本套件。
            paths = [dst for _src, dst, _zone in CORE_FILES + platform_files(platforms)]
            paths.extend([POLICY_FILE, POLICY_DOC])
            if meta:
                paths.append(INSTALL_META)
            missing = set(unignored_paths(target, paths))
            tracked_ok, tracked_text = git_ok(target, "ls-files", "-z", "--", *paths)
            tracked_set = set(tracked_text.rstrip("\0").split("\0")) if tracked_ok else set()
            for path in paths:
                if path in tracked_set and path in entry_targets():
                    ok(f"/{path} 已跟踪：入口差异正常显示，忽略不会隐藏改动")
                elif path in meta.get("project_entries", []):
                    ok(f"/{path} 项目原有入口：保持原本可见性，不额外忽略")
                else:
                    (fail if path in missing else ok)(f"/{path} 忽略生效")

    print("\n【看板冒烟测试】")
    board = target / layout_path("tools/agents_status.py")
    if board.is_file():
        for mode, extra in (("JSON", ["--json"]), ("人类模式", [])):
            try:
                r = subprocess.run(
                    [sys.executable, str(board), *extra],
                    cwd=str(target), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30)
                combined = r.stdout + r.stderr
                if r.returncode in (0, 1) and "Traceback" not in combined:
                    ok(f"看板可运行·{mode}（退出码 {r.returncode}，1=有告警是正常的）")
                else:
                    fail(f"看板{mode}异常（rc={r.returncode}）\n{combined[-300:]}")
            except Exception as e:
                fail(f"看板{mode}跑不起来：{e}")
    elif sibling_ready:
        warn("本 worktree 没有 tools/agents_status.py，重跑 install.py 补齐后再体检")

    session_tool = target / layout_path("tools/agents_session.py")
    if session_tool.is_file():
        result = subprocess.run([sys.executable, str(session_tool), "--help"], cwd=str(target),
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        (ok if result.returncode == 0 else fail)("会话登记工具可运行" +
                                               ("" if result.returncode == 0 else "：" + result.stderr[-300:]))

    print("\n【定制区】")
    rules = read_text(target / (STATE_DIR + "/RULES.md")) or ""
    if "安装后第一件事" in rules:
        warn(f"{STATE_DIR}/RULES.md 第 6 节还是示例内容，记得填写缺少的项目门禁命令")
    else:
        ok("协作规则项目定制区似乎已填写")

    print("\n【新对话验证】")
    print("  入口文件检查通过不等于平台已实际加载。请在目标项目新开对话，")
    print(f"  确认 AI 读取 {STATE_DIR}/START_HERE.md，并报告真实的分支、看板与 inbox 结果。")

    print(f"\n结果：{fails} 个 FAIL，{warns} 个 WARN")
    if fails:
        print("有硬性问题，按上面提示修复（多数重跑 install.py 即可）。")
        return 1
    print("没有硬性问题。" + ("有几条提醒，看一眼。" if warns else "一切正常。"))
    return 0


# --------------------------------------------------------------------------- #
# mirror
# --------------------------------------------------------------------------- #

def cmd_sync(args: argparse.Namespace) -> int:
    target = resolve_target(args.target)
    agents = read_text(target / "AGENTS.md")
    if agents is None or not (target / (STATE_DIR + "/README.md")).is_file():
        raise ValueError("项目未安装完整协作规则，或 AGENTS.md 不是 UTF-8；请先运行 install。")
    if not (target / (STATE_DIR + "/RULES.md")).is_file():
        raise ValueError("旧版尚未建立独立协作总章；请先用 install --dry-run 核对升级差异，未改动入口")
    platforms = choose_platforms(target, args.platforms, args.dry_run)
    project_name = load_project_name(target, None)
    print(f"multi-agent-collab 平台同步  {target}")
    print(f"  已选平台：{', '.join(platforms) or 'none（仅通用规则）'}")
    plan = install_plan(target, project_name, platforms, sync_only=True)
    plan.extend(policy_plan(target))
    # 给 1.4 安装补上新入口；已有的启动文档和总章不在 sync 中覆盖。
    if not (target / (STATE_DIR + "/START_HERE.md")).exists():
        startup = read_text(KIT_ROOT / "docs/START_HERE.md")
        if startup is None:
            raise ValueError("kit 缺少 docs/START_HERE.md")
        plan.insert(0, (STATE_DIR + "/START_HERE.md", render_layout(startup)))
    stats = apply_install_plan(target, plan, args.dry_run)
    save_meta(target, project_name, platforms, args.dry_run, update_core=False, project_entries=stats["project_entries"])
    prefix = "演练完成（未写盘）" if args.dry_run else "同步完成"
    print(f"{prefix}：新建/覆盖 {stats['write']} 个，相同跳过 {stats['skip']} 个。")
    print("共享入口只刷新标记区，已有启动文档及会话记录保持原样。新开对话后验证平台是否加载入口。")
    return 0


def cmd_mirror(args: argparse.Namespace) -> int:
    args.platforms = "workbuddy"
    return cmd_sync(args)


def cmd_platforms(args: argparse.Namespace) -> int:
    catalog = platform_catalog()
    if args.json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
    else:
        print("支持的平台（--platforms 可逗号多选；all 全选，none 仅通用规则）：")
        for item in catalog:
            files = "、".join(item["files"]) or "AGENTS.md（原生，无额外文件）"
            print(f"  {item['id']:<10} {item['label']:<16} {files}")
    return 0


CLIENT_COMMANDS = {"codex": "codex", "claude": "claude", "opencode": "opencode", "pi": "pi",
                   "gemini": "gemini", "qwen": "qwen"}


def client_readiness(platforms: list[str], versions: bool = False) -> list[dict]:
    """只运行固定命令的 --version，不启动模型、不读取配置或记录原始输出。"""
    rows = []
    for name in platforms:
        command = CLIENT_COMMANDS.get(name)
        row = {"id": name, "label": PLATFORM_LABELS[name], "command": command,
               "discovered": None, "probe_status": "manual", "version": None,
               "session_verified": False}
        if command:
            executable = shutil.which(command)
            row.update(discovered=executable is not None,
                       probe_status="not-requested" if executable else "not-found")
            if executable and versions:
                try:
                    result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                            encoding="utf-8", errors="replace", timeout=12,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    match = re.search(r"(?<![\w.])(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?)(?![\w.])",
                                      result.stdout)
                    if result.returncode:
                        row["probe_status"] = "failed"
                    elif match:
                        row.update(probe_status="ok", version=match[1])
                    else:
                        row["probe_status"] = "unrecognized"
                except subprocess.TimeoutExpired:
                    row["probe_status"] = "timeout"
                except OSError:
                    row["probe_status"] = "failed"
        rows.append(row)
    return rows


def cmd_clients(args: argparse.Namespace) -> int:
    platforms = parse_platforms(args.platforms) if args.platforms is not None else saved_platforms(Path(args.target).resolve())
    if platforms is None:
        raise ValueError("没有已保存的平台选择；请用 --platforms codex,claude 指定要检查的平台")
    rows = client_readiness(platforms, args.versions)
    notice = "此检查不启动模型。PATH 未发现命令不代表客户端未安装；新对话是否加载规则仍需在客户端验证。"
    if args.json:
        print(json.dumps({"clients": rows, "notice": notice}, ensure_ascii=False, indent=2))
    else:
        labels = {"manual": "IDE/应用内手动验证", "not-found": "PATH 未发现 CLI",
                  "not-requested": "CLI 已发现（加 --versions 查询版本）", "ok": "版本查询成功",
                  "failed": "版本命令失败", "timeout": "版本命令超时", "unrecognized": "输出中未识别版本号"}
        for row in rows:
            version = " " + row["version"] if row["version"] else ""
            print(f"  {row['label']}: {labels[row['probe_status']]}{version}；新对话加载：待验证")
        print(notice)
    return 0


# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #

# 卸载后允许删除空目录的白名单（从深到浅尝试；.agents 下有会话/备份时不会空）
PRUNE_DIRS = [
    ".trae/rules", ".trae",
    ".claude/commands", ".claude",
    ".workbuddy-ai",
    ".cursor/rules", ".cursor",
    ".windsurf/rules", ".windsurf",
    ".clinerules", ".roo/rules", ".roo", ".continue/rules", ".continue", ".kiro/steering", ".kiro",
    "tools",
    ".agents/sessions", ".agents/inbox", ".agents/worklog", ".agents",
]


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = resolve_target(args.target)
    meta = read_meta(target)
    if not meta or (STATE_DIR != ".agents" and meta.get("owner") != "multi-agent-collab"):
        raise ValueError("没有可确认归属的安装记录；未删除任何文件")
    hashes = meta.get("managed_hashes", {})
    print(f"multi-agent-collab 卸载 {target}")
    # Build the entire removal plan before mutating any entry.
    changes = []
    for rel in sorted(entry_targets()):
        if rel not in hashes:
            continue
        dest = target / rel
        preflight(target, [rel], args.dry_run)
        old = read_text(dest)
        if old is None:
            continue
        span = entry_span(old, rel)
        if span is None:
            continue  # Already uninstalled; keep subsequent project edits.
        start, end = span
        clean = old[:start] + old[end:]
        pristine = hashlib.sha256(dest.read_bytes()).hexdigest() == hashes[rel]
        # Preserve frontmatter and all project-authored text; do not delete tracked files.
        tracked, names = git_ok(target, "ls-files", "--", rel)
        remove = not clean.strip() and pristine and tracked and not names.strip()
        changes.append((dest, None if remove else clean))
    if args.purge_files:
        for _src, rel, _zone in CORE_FILES:
            if rel in entry_targets() or rel.endswith(("/RULES.md", "/TROUBLESHOOTING.md")):
                continue
            dest = target / rel
            preflight(target, [rel], args.dry_run)
            if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == hashes.get(rel):
                changes.append((dest, None))
    for dest, content in changes:
        print(f"  - {'删除未修改文件' if content is None else '仅移除入口标记区'} {dest.relative_to(target)}")
        if not args.dry_run:
            if content is None:
                dest.unlink()
            else:
                write_lf(dest, content)
    print("完成：保留项目原规则、会话、策略、故障记录与备份；默认 Git 忽略继续生效，避免私有数据被误提交。")
    return 0


# --------------------------------------------------------------------------- #

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    ap = argparse.ArgumentParser(description="multi-agent-collab 安装器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_inst = sub.add_parser("install", help="安装/升级协作体系到目标项目")
    p_inst.add_argument("target", nargs="?", default=".", help="目标项目路径（默认当前目录）")
    p_inst.add_argument("--platforms", default=None,
                        help="逗号多选（platforms 命令列出）；首次询问，升级沿用；"
                             "all 全选 / none 只装通用规则；追加不删除旧入口")
    p_inst.add_argument("--project-name", default=None,
                        help="替换 {{PROJECT_NAME}}（默认读独立目录 install.json，再默认用目录名）")
    p_inst.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    p_inst.add_argument("--workflow", choices=["git-flow", "github-flow", "custom"], default=None,
                        help="明确选择分支策略；初装默认 git-flow，升级沿用")
    p_inst.add_argument("--base-branch", default=None, help="PR 基线，不自动创建分支")
    p_inst.add_argument("--task-prefixes", default=None, help="允许的任务分支前缀，逗号分隔，均以 / 结尾")
    p_inst.add_argument("--protect-branches", default=None, help="额外保护分支，逗号分隔；核心主干始终保护")
    p_inst.set_defaults(func=cmd_install)

    p_doc = sub.add_parser("doctor", help="体检安装结果")
    p_doc.add_argument("target", nargs="?", default=".")
    p_doc.set_defaults(func=cmd_doctor)

    p_sync = sub.add_parser("sync", help="同步已选入口，--platforms 追加新平台；保留项目总章")
    p_sync.add_argument("target", nargs="?", default=".")
    p_sync.add_argument("--platforms", default=None, help="逗号分隔的新平台，追加到已保存选择")
    p_sync.add_argument("--dry-run", action="store_true", help="只预览，不写盘")
    p_sync.set_defaults(func=cmd_sync)

    p_list = sub.add_parser("platforms", help="列出可选平台及生成的文件")
    p_list.add_argument("--json", action="store_true", help="输出结构化平台目录")
    p_list.set_defaults(func=cmd_platforms)

    p_clients = sub.add_parser("clients", help="检查所选客户端 CLI；不会调用模型或证明规则已加载")
    p_clients.add_argument("target", nargs="?", default=".")
    p_clients.add_argument("--platforms", default=None, help="待检查的平台；默认使用项目保存的选择")
    p_clients.add_argument("--versions", action="store_true", help="运行固定的 --version 命令，每个最多等待 12 秒")
    p_clients.add_argument("--json", action="store_true")
    p_clients.set_defaults(func=cmd_clients)

    p_mir = sub.add_parser("mirror", help="兼容旧命令：同步 WorkBuddy 标记入口")
    p_mir.add_argument("target", nargs="?", default=".")
    p_mir.add_argument("--dry-run", action="store_true")
    p_mir.set_defaults(func=cmd_mirror)

    p_un = sub.add_parser("uninstall",
                          help="移除套件入口标记；--purge-files 清理未修改工具，保留用户数据和忽略规则")
    p_un.add_argument("target", nargs="?", default=".")
    p_un.add_argument("--purge-files", action="store_true",
                      help="按安装时摘要清理未修改工具与模板；规则、策略、记录、备份保留")
    p_un.add_argument("--dry-run", action="store_true")
    p_un.set_defaults(func=cmd_uninstall)

    args = ap.parse_args()
    try:
        if hasattr(args, "target"):
            layout_target = Path(args.target).expanduser().resolve()
            ok, top = git_ok(layout_target, "rev-parse", "--show-toplevel") if layout_target.is_dir() else (False, "")
            configure_layout(Path(top.strip()).resolve() if ok and top.strip() else layout_target)
        return args.func(args)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
