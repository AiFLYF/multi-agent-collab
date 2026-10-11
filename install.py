#!/usr/bin/env python3
"""multi-agent-collab 安装器（零依赖，纯标准库；Windows / macOS / Linux 通用）

把「Git Flow + 本机共享记忆」协作体系安装到任意 git 项目：
  AGENTS.md / CLAUDE.md / .agents/ / tools/agents_status.py / 各平台入口

用法：
    python install.py install [TARGET]            # 安装/升级到 TARGET（默认当前目录）
    python install.py doctor [TARGET]             # 体检：文件齐不齐、忽略生不生效、看板能不能跑
    python install.py mirror [TARGET]             # 改完 AGENTS.md 后重生成 WorkBuddy 镜像

常用选项：
    --platforms trae,claude,workbuddy             # 选装平台入口（默认 all；none=不装；不会卸载旧入口）
    --project-name NAME                           # 替换 {{PROJECT_NAME}}（默认读 .agents/install.json）
    --dry-run                                     # 只打印要做的事，不落盘

设计约定：
- 幂等：内容相同跳过；不同则先备份（统一放 .agents/backups/，天然被忽略）再覆盖
- 定制区：AGENTS.md / CLAUDE.md / TROUBLESHOOTING.md 里 mac:zone 标记之间的内容
  升级时自动保留（项目门禁、项目速览、你追加的 D 组故障条目）
- .git/info/exclude 由两个标记行管理，重跑安装器会替换该段（含残片清理），不碰其它规则
- linked worktree（.git 是文件）也能正确识别，忽略块写入该 worktree 与公共目录两处
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
KIT_VERSION = "1.1.0"   # 升级 kit 时记得改；会写进目标项目的 .agents/install.json

# (kit 内源路径, 安装目标相对路径, 定制区名或 None；"__troubles__" 走专用合并)
CORE_FILES = [
    ("AGENTS.md", "AGENTS.md", "project-env"),
    ("CLAUDE.md", "CLAUDE.md", "project-overview"),
    ("tools/agents_status.py", "tools/agents_status.py", None),
    ("docs/README.md", ".agents/README.md", None),
    ("docs/TROUBLESHOOTING.md", ".agents/TROUBLESHOOTING.md", "__troubles__"),
    ("templates/session.md", ".agents/sessions/_template.md", None),
    ("templates/inbox.md", ".agents/inbox/_template.md", None),
    ("templates/worklog.md", ".agents/worklog/_template.md", None),
]

PLATFORM_FILES = {
    "trae": ("platforms/trae-rule.md", ".trae/rules/git-flow-agent.md"),
    "claude": ("platforms/claude-command.md", ".claude/commands/git-flow.md"),
}
WORKBUDDY_HEADER = "platforms/workbuddy-header.md"
WORKBUDDY_TARGET = ".workbuddy-ai/git-flow-agent.md"

ALL_PLATFORMS = ["trae", "claude", "workbuddy"]
INSTALL_META = ".agents/install.json"

# .git/info/exclude 里被托管的标记块
MARK_BEGIN = "# >>> multi-agent-collab (local-only; managed by install.py) >>>"
MARK_END = "# <<< multi-agent-collab <<<"
# 「本机私有路径清单」的唯一事实源 —— AGENTS.md / README / 两个平台入口里的清单
# 必须与此一致（tests/selftest.py 有一致性断言，漂移立刻红）。
EXCLUDE_ENTRIES = [
    "/AGENTS.md",
    "/CLAUDE.md",
    "/.agents/",
    "/.agent/",
    "/.claude/",
    "/.trae/",
    "/.workbuddy-ai/",
    "/.workbuddy/",
    "/tools/agents_status.py",
]

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
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def write_lf(path: Path, content: str) -> None:
    """统一以 LF + UTF-8 写入（.git 元数据类文件怕 CRLF，见手册 B12）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_dest(dest: Path, target: Path) -> Path:
    """备份统一放 <target>/.agents/backups/（被 /​.agents/ 忽略，绝不污染 git status）。"""
    rel = dest.relative_to(target)
    flat = str(rel).replace("\\", "/").replace("/", "__")
    bdir = target / ".agents" / "backups"
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
            current = dest.read_text(encoding="utf-8")
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
    return {m.group(2): m.group(3) for m in ZONE_RE.finditer(text)}


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
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(target), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        return r.returncode == 0, r.stdout
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


def managed_block() -> str:
    return MARK_BEGIN + "\n" + "\n".join(EXCLUDE_ENTRIES) + "\n" + MARK_END + "\n"


def update_excludes(target: Path, dry_run: bool) -> str:
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
                continue  # 读不了（非 UTF-8 且存在异常）就不碰它
        cleaned = strip_managed(current)
        if cleaned and not cleaned.endswith("\n"):
            cleaned += "\n"
        new_text = cleaned + managed_block()
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
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_meta(target: Path, name: str, dry_run: bool) -> None:
    payload = {
        "project_name": name,
        "kit_version": KIT_VERSION,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
    }
    dest = target / INSTALL_META
    if not dry_run:
        write_lf(dest, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def parse_platforms(raw: str) -> list[str]:
    if raw.strip().lower() == "none":
        return []
    names = [x.strip().lower() for x in raw.split(",") if x.strip()]
    if "all" in names:
        return list(ALL_PLATFORMS)
    bad = [x for x in names if x not in ALL_PLATFORMS]
    if bad:
        raise SystemExit(f"未知平台：{', '.join(bad)}；可选：{', '.join(ALL_PLATFORMS)} 或 all / none")
    return names


def cmd_install(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()

    # 自举保护：kit 自身（及 kit 目录内任何位置）不能作为安装目标——
    # 模板文件是产品源码、由 git 管理；往里"安装"会触发定制区自迁移、
    # 生成 .agents/ 等安装产物，污染仓库。
    try:
        target.relative_to(KIT_ROOT)
        inside_kit = True
    except ValueError:
        inside_kit = False
    if inside_kit:
        print("[FAIL] 目标在 kit 目录内部，拒绝安装：")
        print(f"       kit    = {KIT_ROOT}")
        print(f"       目标   = {target}")
        print("       install.py 是把套件装进【别的 git 项目】用的；")
        print("       kit 仓库的模板（AGENTS.md / docs/ / platforms/ ……）直接由 git 管理，")
        print("       不要在仓库里自安装。")
        return 2

    project_name = load_project_name(target, args.project_name)
    platforms = parse_platforms(args.platforms)

    print("multi-agent-collab 安装器")
    print(f"  kit 源目录  {KIT_ROOT}")
    print(f"  目标项目    {target}")
    print(f"  项目名      {project_name}")
    print(f"  平台入口    {', '.join(platforms) if platforms else '不装'}"
          "（缩容不会卸载已装入口，不用的手工删即可）")
    if args.dry_run:
        print("  （--dry-run：只演示，不写任何文件）")
    print()

    if not target.exists():
        print(f"+ 创建目标目录 {target}")
        if not args.dry_run:
            target.mkdir(parents=True, exist_ok=True)

    stats = {"write": 0, "skip": 0}
    rendered_agents = ""

    print("【核心文件】")
    for src_rel, dst_rel, zone in CORE_FILES:
        src = KIT_ROOT / src_rel
        template = read_text(src)
        if template is None:
            print(f"  ! 缺少 kit 源文件 {src}，安装器不完整")
            return 2
        dest = target / dst_rel
        old = read_text(dest)
        old_exists = dest.exists()
        if zone == "__troubles__":
            content = merge_troubleshooting(template, old if old is not None else None,
                                            project_name)
            if old_exists and old is not None and "mac:zone:project-troubles" not in old:
                entries = extract_d_entries(old)
                if entries:
                    print(f"  ↻ 从旧版手册迁移了 {len(entries)} 条 D 组项目专属条目")
        elif zone:
            content = render(template, old, project_name)
            # M9：旧文件存在，但既读不出（非 UTF-8）又没有定制区标记 → 明确警告
            if old_exists and (old is None or "mac:zone:" not in old):
                reason = "非 UTF-8 编码" if old is None else "没有定制区标记（mac:zone）"
                bk_note = "（旧内容已字节备份，安装后请人工比对把定制补进「项目定制区」）"
                print(f"  ⚠ 注意：{dst_rel} {reason}，定制内容无法自动迁移 {bk_note}")
        else:
            content = template.replace("{{PROJECT_NAME}}", project_name)
        if src_rel == "AGENTS.md":
            rendered_agents = content
        install_file(dest, content, target, args.dry_run, stats)

    save_meta(target, project_name, args.dry_run)

    print("【平台入口】")
    for name in platforms:
        if name in PLATFORM_FILES:
            src_rel, dst_rel = PLATFORM_FILES[name]
            src_text = read_text(KIT_ROOT / src_rel)
            if src_text is None:
                print(f"  ! 缺少 kit 源文件 platforms/{name}，跳过")
                continue
            content = src_text.replace("{{PROJECT_NAME}}", project_name)
            install_file(target / dst_rel, content, target, args.dry_run, stats)
        elif name == "workbuddy":
            header = read_text(KIT_ROOT / WORKBUDDY_HEADER)
            if header is None:
                print("  ! 缺少 workbuddy 头模板，跳过")
                continue
            mirror = header.rstrip() + "\n\n" + rendered_agents
            install_file(target / WORKBUDDY_TARGET, mirror, target, args.dry_run, stats)

    print("【git 本地忽略】")
    state = update_excludes(target, args.dry_run)
    if state == "nogit":
        print("  ! 目标不是 git 仓库（或 git 不可用）：文件已安装，但没写 .git/info/exclude。")
        print("    git init 之后重跑本安装器即可补上；或手工把以下规则加进 .git/info/exclude：")
        for e in EXCLUDE_ENTRIES:
            print(f"      {e}")
    elif state == "ok":
        print("  = 忽略规则已是最新，跳过")
    print()

    print("【安装后要做的 3 件事】")
    print("  1. 打开 AGENTS.md 第 6 节、CLAUDE.md「项目速览」，把示例换成你的项目命令"
          "（升级时自动保留，放心改）")
    print(f"  2. 到项目根目录跑一次看板：cd \"{target}\" ; python tools/agents_status.py")
    print("  3. 开一个新对话，问 AI「你开工前要先做什么？」，应答出 fetch + 看板 + 看 inbox")
    print()
    print(f"完成：新建/覆盖 {stats['write']} 个，相同跳过 {stats['skip']} 个。"
          f"旧版备份统一在 .agents/backups/。")
    print(f"体检随时可跑：python \"{KIT_ROOT / 'install.py'}\" doctor \"{target}\"")
    return 0


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def cmd_doctor(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
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
    if meta:
        inst_ver = meta.get("kit_version", "未知（旧版本安装）")
        if inst_ver == KIT_VERSION:
            ok(f"套件版本 {inst_ver}（install.json）")
        else:
            warn(f"安装时版本 {inst_ver}，当前 kit 是 {KIT_VERSION}"
                 " —— 建议跑一次 install.py install 升级（定制区会自动保留）")
    elif (target / "AGENTS.md").exists():
        warn("没有 .agents/install.json（1.0 版之前手工安装？）—— 重跑一次安装器可登记版本")

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

    print("\n【平台入口】（可选）")
    for name, (_src, dst_rel) in PLATFORM_FILES.items():
        p = target / dst_rel
        parent_exists = p.parent.exists()
        if p.is_file():
            ok(f"{name}: {dst_rel}")
        elif parent_exists:
            warn(f"{name}: 平台目录在但入口缺失：{dst_rel}")
        else:
            print(f"  [--]   {name}: 未安装（可选）")
    p = target / WORKBUDDY_TARGET
    if p.is_file():
        ok(f"workbuddy: {WORKBUDDY_TARGET}")
    elif (target / ".workbuddy-ai").exists():
        warn(f"workbuddy: .workbuddy-ai/ 在但镜像缺失：{WORKBUDDY_TARGET}")
    else:
        print("  [--]   workbuddy: 未安装（可选）")

    print("\n【git 与本地忽略】")
    if not is_git_repo(target):
        warn("不是 git 仓库（或 git 不可用）；git init 后重跑安装器即可补上忽略规则")
    else:
        locs = exclude_paths(target)
        if not locs:
            fail("git 仓库识别成功但取不到 info/exclude 路径")
        else:
            has_block = any(MARK_BEGIN in (read_text(p) or "") for p in locs)
            (ok if has_block else fail)("托管忽略块存在（私有/公共 exclude 至少一处）")
            # 以实际效果为准
            for entry in EXCLUDE_ENTRIES:
                probe = entry.lstrip("/").rstrip("/")
                if entry.endswith("/"):
                    probe += "/__mac_probe__"
                good, _ = git_ok(target, "check-ignore", "-q", probe)
                (ok if good else fail)(f"{entry} 生效")

    print("\n【看板冒烟测试】")
    board = target / "tools/agents_status.py"
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

    print("\n【定制区】")
    agents = read_text(target / "AGENTS.md")
    if agents and "安装后第一件事" in agents:
        warn("AGENTS.md 第 6 节还是示例内容，记得替换成你的项目门禁命令")
    else:
        ok("AGENTS.md 项目定制区似乎已填写")
    buddy = read_text(target / WORKBUDDY_TARGET)
    if buddy is not None and agents is not None:
        header = read_text(KIT_ROOT / WORKBUDDY_HEADER) or ""
        expect = header.rstrip() + "\n\n" + agents
        if buddy.strip() == expect.strip():
            ok("WorkBuddy 镜像与 AGENTS.md 一致")
        else:
            warn("WorkBuddy 镜像比 AGENTS.md 旧 —— 运行 install.py mirror 可刷新")

    print(f"\n结果：{fails} 个 FAIL，{warns} 个 WARN")
    if fails:
        print("有硬性问题，按上面提示修复（多数重跑 install.py 即可）。")
        return 1
    print("没有硬性问题。" + ("有几条提醒，看一眼。" if warns else "一切正常。"))
    return 0


# --------------------------------------------------------------------------- #
# mirror
# --------------------------------------------------------------------------- #

def cmd_mirror(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    agents = read_text(target / "AGENTS.md")
    if agents is None and not (target / "AGENTS.md").exists():
        print(f"[FAIL] {target / 'AGENTS.md'} 不存在，先跑 install。")
        return 2
    if agents is None:
        print(f"[FAIL] {target / 'AGENTS.md'} 不是合法 UTF-8，无法生成镜像；"
              "请先把文件转成 UTF-8。")
        return 2
    header = read_text(KIT_ROOT / WORKBUDDY_HEADER)
    if header is None:
        print(f"[FAIL] kit 缺少 {WORKBUDDY_HEADER}")
        return 2
    content = header.rstrip() + "\n\n" + agents
    dest = target / WORKBUDDY_TARGET
    stats = {"write": 0, "skip": 0}
    install_file(dest, content, target, args.dry_run, stats)
    print("完成。" if stats["write"] else "镜像已是最新。")
    return 0


# --------------------------------------------------------------------------- #
# uninstall
# --------------------------------------------------------------------------- #

# 卸载后允许删除空目录的白名单（从深到浅尝试；.agents 下有会话/备份时不会空）
PRUNE_DIRS = [
    ".trae/rules", ".trae",
    ".claude/commands", ".claude",
    ".workbuddy-ai",
    "tools",
    ".agents/sessions", ".agents/inbox", ".agents/worklog", ".agents",
]


def cmd_uninstall(args: argparse.Namespace) -> int:
    target = Path(args.target).resolve()
    print(f"multi-agent-collab 卸载  {target}")
    if args.dry_run:
        print("  （--dry-run：只演示，不写任何文件/目录）")

    # 1) 摘掉所有 exclude 位置上的托管块（这步永远做，且不动用户其它忽略规则）
    locs = exclude_paths(target)
    for exclude in locs:
        current = read_text(exclude)
        if current is None or MARK_BEGIN not in current:
            continue
        new_text = strip_managed(current)
        try:
            rel = str(exclude.relative_to(target))
        except ValueError:
            rel = str(exclude) + "（git 公共目录）"
        print(f"  - 移除忽略托管块  {rel}")
        if not args.dry_run:
            write_lf(exclude, new_text)
    if not locs:
        print("  （目标不是 git 仓库或取不到 info/exclude，跳过忽略块清理）")

    if not args.purge_files:
        print("\n已完成：只移除了忽略托管块。所有协作文件仍在磁盘上（此刻起会出现在 git status）。")
        print("会话/worklog/inbox/备份/手册 D 组条目都是你的数据，本命令永不删除。")
        print("如要一并清理**未被你改过的**套件文件，加 --purge-files 再跑一次"
              "（改过的文件会自动保留并列出）。")
        return 0

    # 2) 只删「与当前 kit 原件逐字节相同」的托管文件 —— 用户改过/定制过的一律留
    project_name = read_meta(target).get("project_name") or target.name
    candidates: list[tuple[Path, str]] = [(target / dst, src) for src, dst, _z in CORE_FILES]
    for _name, (src_rel, dst_rel) in PLATFORM_FILES.items():
        candidates.append((target / dst_rel, src_rel))
    candidates.append((target / WORKBUDDY_TARGET, WORKBUDDY_HEADER))
    candidates.append((target / INSTALL_META, "__meta__"))

    deleted, kept = [], []
    for dest, src_rel in candidates:
        if not dest.exists():
            continue
        if src_rel == "__meta__":
            pristine = True   # install.json 是纯套件元数据
        else:
            kit_text = (read_text(KIT_ROOT / src_rel) or "").replace("{{PROJECT_NAME}}", project_name)
            # workbuddy 镜像在套件里只有头；安装内容 = 头 + 当前 kit 的 AGENTS 模板
            if src_rel == WORKBUDDY_HEADER:
                agents_tpl = (read_text(KIT_ROOT / "AGENTS.md") or "").replace(
                    "{{PROJECT_NAME}}", project_name)
                kit_text = kit_text.rstrip() + "\n\n" + agents_tpl
            try:
                pristine = dest.read_text(encoding="utf-8") == kit_text
            except (OSError, UnicodeDecodeError):
                pristine = False
        if pristine:
            print(f"  - 删除未修改的套件文件  {dest}")
            if not args.dry_run:
                dest.unlink()
            deleted.append(dest)
        else:
            print(f"  = 保留（含你的定制/数据）  {dest}")
            kept.append(dest)

    # 3) 清理因此变空的白名单目录
    for rel in PRUNE_DIRS:
        d = target / rel
        if d.is_dir():
            try:
                next(d.iterdir())
            except StopIteration:
                print(f"  - 删除空目录  {rel}")
                if not args.dry_run:
                    d.rmdir()
            except OSError:
                pass

    print(f"\n完成：删除 {len(deleted)} 个未修改的套件文件，保留 {len(kept)} 个含定制的文件。")
    backups = target / ".agents" / "backups"
    if backups.exists():
        print(f"历史备份仍在 {backups}（由你决定是否手工删除）。")
    return 0


# --------------------------------------------------------------------------- #

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="multi-agent-collab 安装器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_inst = sub.add_parser("install", help="安装/升级协作体系到目标项目")
    p_inst.add_argument("target", nargs="?", default=".", help="目标项目路径（默认当前目录）")
    p_inst.add_argument("--platforms", default="all",
                        help="逗号分隔：trae,claude,workbuddy / all（默认）/ none（只不装新的，不卸载旧的）")
    p_inst.add_argument("--project-name", default=None,
                        help="替换 {{PROJECT_NAME}}（默认读 .agents/install.json，再默认用目录名）")
    p_inst.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    p_inst.set_defaults(func=cmd_install)

    p_doc = sub.add_parser("doctor", help="体检安装结果")
    p_doc.add_argument("target", nargs="?", default=".")
    p_doc.set_defaults(func=cmd_doctor)

    p_mir = sub.add_parser("mirror", help="按目标当前 AGENTS.md 重生成 WorkBuddy 镜像")
    p_mir.add_argument("target", nargs="?", default=".")
    p_mir.add_argument("--dry-run", action="store_true")
    p_mir.set_defaults(func=cmd_mirror)

    p_un = sub.add_parser("uninstall",
                          help="移除忽略托管块；加 --purge-files 再删未被改过的套件文件（用户数据永不删）")
    p_un.add_argument("target", nargs="?", default=".")
    p_un.add_argument("--purge-files", action="store_true",
                      help="删除与 kit 原件逐字节相同的托管文件；含定制/数据的自动保留")
    p_un.add_argument("--dry-run", action="store_true")
    p_un.set_defaults(func=cmd_uninstall)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
