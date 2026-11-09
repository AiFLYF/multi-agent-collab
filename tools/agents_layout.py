"""Resolve private state independently for every worktree; keep legacy installs readable."""

from __future__ import annotations

import json
import os
from pathlib import Path

PRIVATE_DIR = ".multi-agent-collab"
LEGACY_DIR = ".agents"


def prefer_native_git(executable: str | None) -> str | None:
    """Avoid the Git for Windows cmd launcher for default discovery only."""
    if not executable or os.name != "nt":
        return executable
    path = Path(executable)
    if path.name.casefold() != "git.exe" or path.parent.name.casefold() != "cmd":
        return executable
    native = path.parent.parent / "mingw64" / "bin" / "git.exe"
    return str(native) if native.is_file() else executable


def state_dir(root: Path) -> str:
    private = root / PRIVATE_DIR
    if private.exists() or private.is_symlink():
        if (not private.is_dir() or private.is_symlink() or getattr(private, "is_junction", lambda: False)() or
                private.resolve() != root.resolve() / PRIVATE_DIR):
            raise ValueError(f"{private} 不是可确认归属的普通协作目录；停止读取或写入状态")
        metadata = private / "install.json"
        try:
            if metadata.is_symlink():
                raise ValueError("安装记录不能是链接")
            value = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"{metadata} 缺失、损坏或不可读取；无法确认属于本套件，停止操作") from exc
        if not isinstance(value, dict) or value.get("owner") != "multi-agent-collab":
            raise ValueError(f"{metadata} 无法确认属于本套件；停止操作，避免漏掉旧目录的占用")
        if value.get("layout", PRIVATE_DIR) != PRIVATE_DIR:
            raise ValueError(f"{metadata} 的 layout 与目录不符；停止操作")
        return PRIVATE_DIR
    return LEGACY_DIR


def state_path(root: Path, relative: str = "") -> Path:
    return root / state_dir(root) / relative


def state_relative(root: Path, relative: str) -> str:
    """Translate legacy internal paths, leaving arbitrary project paths alone."""
    if relative == LEGACY_DIR or relative.startswith(LEGACY_DIR + "/"):
        return state_dir(root) + relative[len(LEGACY_DIR):]
    return relative
