"""项目分支策略：安装器与会话工具共用，纯标准库。"""

from __future__ import annotations

import json
import re
from pathlib import Path

POLICY_FILE = ".agents/policy.json"
POLICY_DOC = ".agents/POLICY.md"
ALWAYS_PROTECTED = {"main", "master", "develop"}
PRESETS = {
    "git-flow": ("develop", ["feature/", "release/", "hotfix/"]),
    "github-flow": ("main", ["feature/", "fix/", "docs/", "chore/", "codex/"]),
}


def valid_ref(value: str, prefix: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("分支名称和前缀必须是字符串")
    value = value.strip()
    check = value[:-1] if prefix and value.endswith("/") else value
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", check)
            or ".." in check or "//" in check or check.endswith((".", "/", ".lock"))
            or any(part.startswith(".") or part.endswith(".lock") for part in check.split("/"))
            or (prefix and not value.endswith("/"))):
        raise ValueError(f"无效的分支{'前缀' if prefix else '名称'}：{value}；使用英文、数字、点、下划线和连字符，前缀以 / 结尾")
    return value


def build_policy(workflow="git-flow", base_branch=None, task_prefixes=None, protected_branches=None):
    if workflow not in (*PRESETS, "custom"):
        raise ValueError("workflow 必须为 git-flow、github-flow 或 custom")
    if workflow == "custom" and (not base_branch or not task_prefixes):
        raise ValueError("custom 策略必须明确 --base-branch 和 --task-prefixes")
    default_base, defaults = PRESETS.get(workflow, (base_branch, task_prefixes))
    base = valid_ref(base_branch or default_base)
    prefixes = task_prefixes if task_prefixes is not None else defaults
    protected = protected_branches if protected_branches is not None else []
    if not isinstance(prefixes, list) or not prefixes or not isinstance(protected, list):
        raise ValueError("task_prefixes 必须为非空数组，protected_branches 必须为数组")
    if workflow == "git-flow" and base in ("main", "master"):
        raise ValueError("Git Flow 的开发基线不能是生产主干，请选择 github-flow 或 custom")
    return {"schema_version": 1, "workflow": workflow, "base_branch": base,
            "task_prefixes": list(dict.fromkeys(valid_ref(p, prefix=True) for p in prefixes)),
            "protected_branches": sorted(ALWAYS_PROTECTED | {base} | {valid_ref(p) for p in protected})}


def validate_policy(value):
    required = {"schema_version", "workflow", "base_branch", "task_prefixes", "protected_branches"}
    if (not isinstance(value, dict) or set(value) != required
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["workflow"], str) or not isinstance(value["base_branch"], str)
            or not value["base_branch"].strip() or not isinstance(value["task_prefixes"], list)
            or not isinstance(value["protected_branches"], list)):
        raise ValueError("策略字段或版本无效；不会静默退回默认策略")
    return build_policy(value["workflow"], value["base_branch"], value["task_prefixes"], value["protected_branches"])


def read_policy(root: Path):
    path = root / POLICY_FILE
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("策略文件指向工作区外部，停止读取") from None
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_policy(value)
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取 {path}，请修复策略后重试：{exc}") from None


def effective_policy(root: Path, primary: Path | None = None):
    # 主工作区是同一仓库的策略源；linked worktree 中的副本用于展示和离线检查。
    if primary is not None:
        policy = read_policy(primary)
        if policy is not None:
            return policy
    return read_policy(root) or build_policy()


def check_task_branch(branch: str, policy):
    if not branch or branch in policy["protected_branches"]:
        raise ValueError("请先切到任务分支；不能在受保护主干登记写任务")
    if not branch.startswith(tuple(policy["task_prefixes"])):
        raise ValueError("任务分支不符合项目策略，允许的前缀：" + ", ".join(policy["task_prefixes"]))


def render_policy(policy):
    prefixes = "、".join(f"`{p}*`" for p in policy["task_prefixes"])
    protected = "、".join(f"`{p}`" for p in policy["protected_branches"])
    extra = ("Git Flow 中，生产主干 main/master 只接受 release/hotfix；开发基线接受上列任务分支的 PR。"
             if policy["workflow"] == "git-flow" else "任务分支通过 PR 合入上述基线；不直接提交或推送主干。")
    return ("# 当前项目分支策略\n\n此文件由安装器生成，来源为同一仓库主工作区的 policy.json。\n\n"
            f"- 工作流：`{policy['workflow']}`\n- PR 基线：`{policy['base_branch']}`\n"
            f"- 写任务允许前缀：{prefixes}\n- 受保护分支：{protected}\n\n{extra}\n\n"
            "策略不会自动建立分支、切换工作区、合并或推送。实际用户指令与项目约定仍优先。\n"
            "变更策略应在主工作区明确配置，再对各 worktree 执行 sync；会话工具读取主策略，不能用旧副本绕过保护。\n"
            "main、master、develop 及所选基线始终受保护；额外保护分支可以添加，不能静默解除。\n")
