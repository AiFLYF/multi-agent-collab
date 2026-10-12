---
description: 按 Git Flow + 多 agent 本机共享记忆协议执行一个完整开发任务（开分支→注册→开发→测试→PR）
argument-hint: <任务描述>
---

@AGENTS.md

# 任务

$ARGUMENTS

# 执行要求

严格按 AGENTS.md 第 3 节「标准开发流程」执行，并在每一步用中文向用户汇报
**当前分支 / commit message / 变更文件 / 测试结果**。

开工检查清单（任何写操作之前）：

1. `git fetch origin --prune`
2. `gh pr list --state open` —— 看有没有已经开着、改了同一批文件的 PR
3. `python tools/agents_status.py --brief` —— 一行摘要确认本机没有活跃会话占用
   同一分支或同一批文件（rc=1 再跑无参数完整版）；有冲突先在 `.agents/inbox/` 留问，不要抢活
4. 读 `.agents/inbox/` 中点名 claude / any 的未答问题与近两天 worklog

随后：从最新 `develop` 切 `feature/<任务名>` → 复制
`.agents/sessions/_template.md` 注册本会话（ID `claude-<YYMMDD-HHMM>-<4位随机>`，
纯本地文件，不提交不推送）→ 开发（只 add 项目文件，禁止 `git add -A`，
禁止提交 AGENTS.md 第 8 节列出的本机协作文件）→ 跑 AGENTS.md 第 6 节约定的测试门禁 →
推送项目分支并 `gh pr create --base develop`（PR 描述写改动/原因/测试证据）
→ session 置 done、worklog 追加总结（本地保存即可）。

遇到 AGENTS.md 第 5 节的异常信号（.git 被其他进程修改、出现来历不明的分支、
远程历史突变）时立即停手，只读取证并通知用户，不得 init / reset / force push / 删 .git。

用户要求看图形化看板/网页指挥时，帮他后台启动 `python tools/agents_status.py --web`
（默认仅 127.0.0.1；`--bind 0.0.0.0` 必须配 `--no-directives` 或环境变量
MULTI_AGENT_BOARD_PASSWORD 密码，HTTP 明文仅限可信网络）。任务阶段间隙重扫
`.agents/inbox/`，`from: human*` 的人类指令优先级最高，"暂停/取消"立即执行。
