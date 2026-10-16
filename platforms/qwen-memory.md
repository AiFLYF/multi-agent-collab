# {{PROJECT_NAME}} · Qwen Code 协作入口

@../AGENTS.md
@../.multi-agent-collab/START_HERE.md

每次新对话先读取项目根 `AGENTS.md` 与 `.multi-agent-collab/START_HERE.md`，检查最新协作记录。
编码前使用 `python .multi-agent-collab/tools/agents_session.py start --agent qwen --task "任务" --files <文件范围>`。
保存返回的会话 ID，阶段间隙更新心跳、检查 inbox，完成后使用 finish 追加总结并释放占用。
本文件使用 Qwen 的本机 QWEN.local.md 入口；团队 QWEN.md 不改写。
其它本机说明保留在托管块外；以根总章为规则来源。

协作总章：`.multi-agent-collab/RULES.md`；项目原有规则与已确认策略继续有效。
