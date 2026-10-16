"""Resolve private state independently for every worktree; keep legacy installs readable."""

from pathlib import Path

PRIVATE_DIR = ".multi-agent-collab"
LEGACY_DIR = ".agents"


def state_dir(root: Path) -> str:
    # The installer rejects an unowned directory before creating these files.
    if (root / PRIVATE_DIR).is_dir():
        return PRIVATE_DIR
    return LEGACY_DIR


def state_path(root: Path, relative: str = "") -> Path:
    return root / state_dir(root) / relative


def state_relative(root: Path, relative: str) -> str:
    """Translate legacy internal paths, leaving arbitrary project paths alone."""
    if relative == LEGACY_DIR or relative.startswith(LEGACY_DIR + "/"):
        return state_dir(root) + relative[len(LEGACY_DIR):]
    return relative
