// 由 scripts/sync_install_docs.py 生成，请修改安装器或提示词模板。
window.MAC_INSTALL = {
  "version": "1.6.0",
  "platforms": [
    {
      "id": "codex",
      "label": "Codex",
      "native": true,
      "files": []
    },
    {
      "id": "claude",
      "label": "Claude Code",
      "native": false,
      "files": [
        "CLAUDE.md",
        ".claude/commands/git-flow.md"
      ]
    },
    {
      "id": "opencode",
      "label": "OpenCode",
      "native": true,
      "files": []
    },
    {
      "id": "pi",
      "label": "Pi",
      "native": true,
      "files": []
    },
    {
      "id": "trae",
      "label": "Trae",
      "native": false,
      "files": [
        ".trae/rules/git-flow-agent.md"
      ]
    },
    {
      "id": "workbuddy",
      "label": "WorkBuddy",
      "native": false,
      "files": [
        ".workbuddy-ai/git-flow-agent.md"
      ]
    },
    {
      "id": "cursor",
      "label": "Cursor",
      "native": false,
      "files": [
        ".cursor/rules/multi-agent-collab.mdc"
      ]
    },
    {
      "id": "copilot",
      "label": "GitHub Copilot",
      "native": false,
      "files": [
        ".github/copilot-instructions.md"
      ]
    },
    {
      "id": "windsurf",
      "label": "Windsurf",
      "native": false,
      "files": [
        ".windsurf/rules/multi-agent-collab.md"
      ]
    },
    {
      "id": "gemini",
      "label": "Gemini CLI",
      "native": false,
      "files": [
        "GEMINI.md"
      ]
    },
    {
      "id": "qwen",
      "label": "Qwen Code",
      "native": false,
      "files": [
        ".qwen/QWEN.local.md"
      ]
    },
    {
      "id": "cline",
      "label": "Cline",
      "native": false,
      "files": [
        ".clinerules/multi-agent-collab.md"
      ]
    },
    {
      "id": "roo",
      "label": "Roo Code",
      "native": false,
      "files": [
        ".roo/rules/00-multi-agent-collab.md"
      ]
    },
    {
      "id": "continue",
      "label": "Continue",
      "native": false,
      "files": [
        ".continue/rules/multi-agent-collab.md"
      ]
    },
    {
      "id": "kiro",
      "label": "Kiro",
      "native": false,
      "files": [
        ".kiro/steering/multi-agent-collab.md"
      ]
    }
  ],
  "coreFiles": [
    "AGENTS.md",
    "tools/agents_status.py",
    "tools/agents_session.py",
    "tools/agents_policy.py",
    ".agents/README.md",
    ".agents/START_HERE.md",
    ".agents/WORKFLOW.md",
    ".agents/TAKEOVER.md",
    ".agents/TROUBLESHOOTING.md",
    ".agents/sessions/_template.md",
    ".agents/inbox/_template.md",
    ".agents/worklog/_template.md",
    ".agents/policy.json",
    ".agents/POLICY.md",
    ".agents/install.json"
  ],
  "promptTemplate": "帮我把 multi-agent-collab 安装到当前项目，并验证所选平台的新对话入口。\n套件仓库：https://github.com/AiFLYF/multi-agent-collab\n\n1. 检查 Python 3.8+ 和 Git，确认当前项目的 Git 根目录并记下绝对路径 TARGET。缺依赖时说明，不自行安装软件或 git init。\n2. {{PLATFORM_SELECTION}}\n   {{WORKFLOW_SELECTION}}\n3. 套件默认放在用户主目录的 multi-agent-collab：Windows 为 $HOME\\multi-agent-collab，macOS/Linux 为 ~/multi-agent-collab。这里是可供多个项目复用的源码目录，不要 clone 到 TARGET 内。\n   目录不存在时执行 git clone https://github.com/AiFLYF/multi-agent-collab \"\u003c用户主目录>/multi-agent-collab\"。已存在时核对 origin 和 install.py 后复用；来源不符或有本地修改时保留现场并说明，不覆盖、不删除、不重置。只有确认工作区干净且配置了上游后，才用 git pull --ff-only 更新。\n4. 使用绝对路径运行 python \"\u003c套件目录>/install.py\" install \"\u003cTARGET>\" --platforms \u003c已选名称> --dry-run；首次安装附加已确认的 --workflow 参数，自定义流程还要 --base-branch 和 --task-prefixes。核对文件清单与分支策略后执行同一条命令去掉 --dry-run；不要默认 all。只选原生平台时不生成其它平台配置。\n5. 安装器负责把本机协作路径写入 .git/info/exclude。若已有规则被 Git 跟踪或忽略无法生效，报告具体文件和原因，不自动取消跟踪、不改写项目 .gitignore、不绕过检查。\n6. 根据项目的真实配置填写生成的 AGENTS.md 第 6 节；选了 Claude 再填写 CLAUDE.md 速览。保留已有定制、会话、日志和备份。若改过总章，再运行 python \"\u003c套件目录>/install.py\" sync \"\u003cTARGET>\" 刷新已选入口。\n7. 执行 python \"\u003c套件目录>/install.py\" doctor \"\u003cTARGET>\"，并核对 git status --short；可用 clients \"\u003cTARGET>\" --versions 检查 CLI 可用性，不启动模型。汇报套件复用路径、已选平台、项目分支策略、生成文件和体检结果；解释 WARN，版本查询不代表新对话已加载规则。\n8. 告诉我新开对话验证：让 agent 读取 .agents/START_HERE.md，报告当前分支、看板和 inbox 的实际结果。之后增加平台用 sync \"\u003cTARGET>\" --platforms \u003c新增名称>，无需重复 clone；旧对话需要主动重新读取规则。\n\n执行安装范围内可恢复的修复即可；遇到需要改变现有项目规则或 Git 跟踪状态的冲突，再说明具体情况让我决定。",
  "defaultSelection": "优先根据当前对话确认正在使用的平台并告诉我；无法确认时先问我使用哪些平台（可多选）：codex、claude、opencode、pi、trae、workbuddy、cursor、copilot、windsurf、gemini、qwen、cline、roo、continue、kiro。使用对应的 --platforms 名称；不要根据电脑里装过哪些软件就全部安装。",
  "defaultWorkflow": "读取项目现有分支约定与 .agents/policy.json；已有策略沿用，首次安装按已确认流程选择 git-flow、github-flow 或 custom。只有 main 的项目可使用 github-flow；无法确认时只询问分支流程，不擅自建立 develop 或改变远端结构。"
};
