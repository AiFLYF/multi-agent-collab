#!/usr/bin/env python3
"""从安装器目录和提示词模板生成 README / 展示页内容；--check 用于 CI。"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import install as kit  # noqa: E402

DEFAULT_SELECTION = (
    "优先根据当前对话确认正在使用的平台并告诉我；无法确认时先问我使用哪些平台（可多选）："
    + "、".join(kit.ALL_PLATFORMS)
    + "。使用对应的 --platforms 名称；不要根据电脑里装过哪些软件就全部安装。"
)
DEFAULT_WORKFLOW = (
    "读取项目现有分支约定与 .agents/policy.json；已有策略沿用，首次安装按已确认流程选择 git-flow、github-flow 或 custom。"
    "只有 main 的项目可使用 github-flow；无法确认时只询问分支流程，不擅自建立 develop 或改变远端结构。"
)


def replace_block(text: str, name: str, content: str) -> str:
    pattern = rf"(<!-- BEGIN {name} -->)\n.*?(<!-- END {name} -->)"
    result, count = re.subn(pattern, lambda m: m[1] + "\n" + content.rstrip() + "\n" + m[2],
                            text, flags=re.S)
    if count != 1:
        raise ValueError(f"{name} 托管块应恰好出现一次，实际 {count}")
    return result


def outputs() -> dict[Path, str]:
    template = (ROOT / "templates/install-prompt.md").read_text(encoding="utf-8").strip()
    if any(template.count("{{" + name + "}}") != 1 for name in ("PLATFORM_SELECTION", "WORKFLOW_SELECTION")):
        raise ValueError("安装提示词应各包含一个 PLATFORM_SELECTION 和 WORKFLOW_SELECTION 占位符")
    prompt = template.replace("{{PLATFORM_SELECTION}}", DEFAULT_SELECTION).replace("{{WORKFLOW_SELECTION}}", DEFAULT_WORKFLOW)
    catalog = kit.platform_catalog()
    table = ["| `--platforms` 名称 | 平台 | 本套件安装的入口 |", "|---|---|---|"]
    for item in catalog:
        entry = "<br>".join(f"`{path}`" for path in item["files"]) or "共用 `AGENTS.md`，无额外文件"
        table.append(f"| `{item['id']}` | {item['label']} | {entry} |")
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    readme = replace_block(readme, "INSTALL_PROMPT", "```text\n" + prompt + "\n```")
    readme = replace_block(readme, "PLATFORM_TABLE", "\n".join(table))
    index_path = ROOT / "site/index.html"
    index = index_path.read_text(encoding="utf-8")
    index, count = re.subn(r'(<code id="prompt-agent">).*?(</code>)',
                          lambda m: m[1] + html.escape(prompt, quote=False) + m[2], index, flags=re.S)
    if count != 1:
        raise ValueError("展示页必须包含一个 prompt-agent 代码块")
    data = {"version": kit.KIT_VERSION, "platforms": catalog,
            "coreFiles": [dst for _src, dst, _zone in kit.CORE_FILES]
                         + [kit.POLICY_FILE, kit.POLICY_DOC, kit.INSTALL_META],
            "promptTemplate": template, "defaultSelection": DEFAULT_SELECTION, "defaultWorkflow": DEFAULT_WORKFLOW}
    script = "// 由 scripts/sync_install_docs.py 生成，请修改安装器或提示词模板。\n"
    script += "window.MAC_INSTALL = " + json.dumps(data, ensure_ascii=False, indent=2).replace("<", "\\u003c") + ";\n"
    return {readme_path: readme, index_path: index, ROOT / "site/assets/js/install-data.js": script}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="仅检查生成内容是否最新")
    args = parser.parse_args()
    dirty = []
    for path, content in outputs().items():
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        dirty.append(path.relative_to(ROOT).as_posix())
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
    if args.check and dirty:
        print("安装说明已过期，请运行 python scripts/sync_install_docs.py：" + ", ".join(dirty))
        return 1
    print("安装说明一致。" if not dirty else "已同步：" + ", ".join(dirty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
