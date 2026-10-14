---
alwaysApply: true
---

# 本项目的强制工作规程（本机规则，不入库）

在这个仓库（{{PROJECT_NAME}}）**每次新对话开始工作前**：

1. 先用 Read 工具完整阅读项目根目录的 `AGENTS.md` 并严格遵守——它是唯一权威规程
   （Git Flow 分支铁律、禁止操作、commit 规范、标准流程、多 agent 协作、事故应急）。
   然后读取 `.agents/START_HERE.md`，按最小阅读顺序检查最新的协作记录。
2. 按启动清单先做只读检查。准备写入时再完成 fetch、PR 和占用协调；
   `python tools/agents_status.py --brief` 返回 1 时展开详情和相关 inbox，有冲突不要抢活。
   同任务继续沿用自己的 ID；blocked 用 resume，不能新建 ID 绕过占用。
3. `AGENTS.md`、`CLAUDE.md`、`.agents/`、`.agent/`、`.claude/`、`.trae/`、
   `.workbuddy-ai/`、`.workbuddy/`、`tools/agents_status.py`
   都是**本机私有文件，禁止 git add/commit/push**（完整清单以安装器 EXCLUDE_ENTRIES 为准）。
   另外，`.cursor/rules/multi-agent-collab.mdc`、`.github/copilot-instructions.md`、
   `.windsurf/rules/multi-agent-collab.md` 也是按需安装的私有入口，不忽略它们所在的整个配置目录。
   新增入口与工具：`tools/agents_session.py`、`GEMINI.md`、`.qwen/QWEN.local.md`、`.clinerules/multi-agent-collab.md`、
   `.roo/rules/00-multi-agent-collab.md`、`.continue/rules/multi-agent-collab.md`、`.kiro/steering/multi-agent-collab.md`。
4. 每个阶段用中文向用户汇报：当前分支 / commit message / 变更文件 / 实际测试结果。
5. 遇到 AGENTS.md 第 5 节的异常信号（.git 被其他进程改动、来历不明的分支、
   远程历史突变）立即停手只读取证并通知用户。
6. 用户想看图形化看板或想在网页上指挥时，按 AGENTS.md 第 4 节帮他启动
   `python tools/agents_status.py --web`（默认仅本机；局域网分享必须只读或带密码）；
   长任务阶段间隙重扫 `.agents/inbox/`，`from: human*` 的指令是用户本人要求，优先级最高。

本规则文件本身也是本机文件（已加入 .git/info/exclude），不要提交它。
