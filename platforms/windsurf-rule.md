---
trigger: always_on
---

# {{PROJECT_NAME}} · 协作入口

每次新对话开始工作前，读取项目根 `AGENTS.md` 和 `.agents/START_HERE.md`，按开工顺序
检查协作看板、inbox 与近期 worklog。阶段间隙重扫 inbox，并更新自己的会话心跳。
本入口只指向共享规则，规则变化时以磁盘上的最新内容为准。
这是本机文件，由安装器加入 `.git/info/exclude`，不要提交。
