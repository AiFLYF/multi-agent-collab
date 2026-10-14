---
inclusion: always
---

# {{PROJECT_NAME}} · Kiro 协作入口

每次新会话读取根 `AGENTS.md` 与 `.agents/START_HERE.md`。
Spec 任务写设计、需求或任务清单时也先登记文件范围；实现阶段沿用自己的会话 ID 并更新范围。
使用 `python tools/agents_session.py` 完成 check、start、heartbeat、handoff、resume 或 finish。
本规则适用于可访问本工作区的客户端；云端任务需要在其独立工作区另行安装，不能看到本机记录。
