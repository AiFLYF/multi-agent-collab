# WorkBuddy 入口：Git Flow 多 agent 开发规则（本机文件，不入库）

> 本文件是仓库根 AGENTS.md 的本地镜像，由 multi-agent-collab 的 `install.py` 生成。
> - 不一致时以 AGENTS.md 为准；改完 AGENTS.md 后运行
>   `python <套件目录>/install.py sync <项目路径>` 重新生成本文件（兼容旧 mirror 命令）
> - 每次新对话先读项目根 `AGENTS.md` 与 `.agents/START_HERE.md`，然后按开工顺序读最新协作记录
> - 本机私有目录：.agents/、.agent/、.claude/、.trae/、.workbuddy-ai/、.workbuddy/
>   及根目录 AGENTS.md、CLAUDE.md、tools/agents_status.py，均不进 git
>   （完整清单以安装器 EXCLUDE_ENTRIES 为准）
> - 按需新增入口：.cursor/rules/multi-agent-collab.mdc、.github/copilot-instructions.md、
>   .windsurf/rules/multi-agent-collab.md；只忽略这些文件，不隐藏整个配置目录
> - tools/agents_session.py、GEMINI.md、.qwen/QWEN.local.md、.clinerules/multi-agent-collab.md、
>   .roo/rules/00-multi-agent-collab.md、.continue/rules/multi-agent-collab.md、.kiro/steering/multi-agent-collab.md 同样本机私有
> - 报错先查 .agents/TROUBLESHOOTING.md

---
