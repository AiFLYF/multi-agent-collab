---
description: 按当前项目规则执行协作任务、实际验证并交付
argument-hint: <任务描述>
---

@AGENTS.md
@.multi-agent-collab/START_HERE.md

# 任务

$ARGUMENTS

# 执行要求

执行 START_HERE 的只读检查，加载尚未读取的 `.multi-agent-collab/RULES.md`；相同静态内容不重复全文导入。
写前确认策略、任务分支和占用，用 `agents_session.py start --agent claude --task "任务" --files <实际范围>` 登记。
同任务沿用 ID，业务工具带 --agent claude 与自己的 --session；摘要未读项先读原文，暂停、取消或避让立即处理。
执行 START_HERE 的最终答复前清单：真跑门禁、完成原事项、按授权交付，默认 finish --standby 释放范围等新命令；未完成 handoff。核验收尾成功后再答复。
每阶段汇报 **当前分支 / commit message / 变更文件 / 实际测试结果**。具体操作按需查 WORKFLOW，项目原有规则继续有效。
