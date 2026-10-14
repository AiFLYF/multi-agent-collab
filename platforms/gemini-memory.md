# {{PROJECT_NAME}} · Gemini CLI 协作入口

@./AGENTS.md
@./.agents/START_HERE.md

开始新会话时执行启动清单。编码前使用 `python tools/agents_session.py start --agent gemini`
登记任务和 `--files` 文件范围；心跳和收尾用同一个会话 ID。若导入不可用，主动读取上述两个文件。
