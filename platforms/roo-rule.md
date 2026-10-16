# {{PROJECT_NAME}} · Roo Code 协作入口（所有模式）

开始新任务时读取根 `AGENTS.md` 与 `.multi-agent-collab/START_HERE.md`。Architect / Ask 模式先做只读协调；
进入可写模式后，用 `python .multi-agent-collab/tools/agents_session.py start --agent roo --task "任务" --files <文件范围>` 登记。
同一任务切换模式时沿用当前会话 ID，不重复占用文件；委派的新执行会话需独立登记范围。
阶段间隙更新心跳、检查 inbox；总章和实时记录以磁盘最新内容为准。

协作总章：`.multi-agent-collab/RULES.md`；项目原有规则与已确认策略继续有效。
