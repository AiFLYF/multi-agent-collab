#!/usr/bin/env python3
"""Measure startup text offline; token counts require an existing optional cache."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("AGENTS.md", "CLAUDE.md", "docs/RULES.md", "docs/START_HERE.md", "platforms/claude-command.md")
STARTUP_FILES = ("docs/RULES.md", "docs/START_HERE.md")
MAX_STARTUP_CHARS = 6500
MAX_STARTUP_TOKENS = 3200


def load_encoder(name):
    try:
        import tiktoken
        import tiktoken.load as token_load
    except ImportError:
        return None, "tiktoken unavailable; characters and bytes are still measured"
    original = token_load.read_file

    def offline_read(path):
        if str(path).startswith(("https://", "http://")):
            raise RuntimeError("tokenizer cache missing; network downloads are disabled")
        return original(path)

    token_load.read_file = offline_read
    try:
        return tiktoken.get_encoding(name), "available from local cache"
    except Exception as exc:
        return None, str(exc)
    finally:
        token_load.read_file = original


def measure(text, encoder):
    return {"chars": len(text), "utf8_bytes": len(text.encode("utf-8")),
            "tokens": len(encoder.encode(text, disallowed_special=())) if encoder else None}


def total(entries, paths):
    tokens = [entries[path]["tokens"] for path in paths]
    return {"chars": sum(entries[path]["chars"] for path in paths),
            "utf8_bytes": sum(entries[path]["utf8_bytes"] for path in paths),
            "tokens": sum(tokens) if all(value is not None for value in tokens) else None}


def git_read(*args):
    executable = shutil.which("git")
    if not executable:
        raise ValueError("Git is required for --baseline")
    result = subprocess.run([executable, *args], cwd=str(ROOT), capture_output=True,
                            text=True, encoding="utf-8", errors="strict", timeout=30)
    if result.returncode:
        raise ValueError("Cannot read baseline: " + result.stderr.strip()[:250])
    return result.stdout


def report(baseline=None, encoding="o200k_base"):
    encoder, status = load_encoder(encoding)
    current = {path: measure((ROOT / path).read_text(encoding="utf-8"), encoder) for path in FILES}
    startup = total(current, STARTUP_FILES)
    out = {"encoding": encoding, "tokenizer_status": status, "files": current,
           "installed_startup": startup,
           "source_startup": total(current, ("AGENTS.md", "docs/START_HERE.md")),
           "budget": {"max_chars": MAX_STARTUP_CHARS, "max_tokens_if_available": MAX_STARTUP_TOKENS},
           "budget_ok": startup["chars"] <= MAX_STARTUP_CHARS and
                        (startup["tokens"] is None or startup["tokens"] <= MAX_STARTUP_TOKENS),
           "note": "Token totals sum individual files with the named encoding; they are not a model billing measurement."}
    if baseline:
        commit = git_read("rev-parse", "--verify", "--end-of-options", baseline + "^{commit}").strip()
        previous = {path: measure(git_read("show", commit + ":" + path), encoder) for path in FILES}
        before = total(previous, STARTUP_FILES)
        metric = "tokens" if before["tokens"] is not None else "chars"
        out.update({"baseline": commit, "baseline_files": previous, "baseline_installed_startup": before,
                    "reduction_metric": metric,
                    "reduction_percent": round((1 - startup[metric] / before[metric]) * 100, 2) if before[metric] else 0})
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="Compare with a local Git commit/ref, without fetching")
    parser.add_argument("--encoding", default="o200k_base")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true", help="Fail when installed startup exceeds the maintained budget")
    args = parser.parse_args(argv)
    try:
        result = report(args.baseline, args.encoding)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("file\tchars\tutf8_bytes\ttokens")
        for path, item in result["files"].items():
            print(f"{path}\t{item['chars']}\t{item['utf8_bytes']}\t{item['tokens']}")
        print("installed startup: " + json.dumps(result["installed_startup"]))
        if "reduction_percent" in result:
            print(f"reduction: {result['reduction_percent']}% ({result['reduction_metric']}, local baseline {result['baseline'][:7]})")
        print("tokenizer: " + result["tokenizer_status"])
        print("budget: " + ("PASS" if result["budget_ok"] else "FAIL"))
    return 1 if args.check and not result["budget_ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
