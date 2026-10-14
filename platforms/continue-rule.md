---
name: Multi-agent collaboration
description: 当前项目的协作规则、会话注册与文件占用
alwaysApply: true
---

# {{PROJECT_NAME}} · Continue 协作入口

每次新对话先读取根 `AGENTS.md` 与 `.agents/START_HERE.md`。
Chat / Plan 等只读阶段先检查协作状态；可写 Agent 模式执行编码前登记自己的会话和文件范围。
阶段间隙运行 `agents_session.py heartbeat` 并检查 inbox，完成后用 finish 记录实际测试结果。
