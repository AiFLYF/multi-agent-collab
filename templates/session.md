# Session: <会话ID>

- agent: <trae | workbuddy | claude | human | ...>
- branch: feature/<任务名>
- task: <一句话任务>
- files: []
- status: active
- started: YYYY-MM-DD HH:MM
- heartbeat: YYYY-MM-DD HH:MM
- pr: <PR 链接，没有就留空>

## 当前进度

文件范围建议用 `agents_session.py start --files ...` 登记；手填时 files 使用 JSON 数组，
例如 `["src/login.py", "docs/"]`。目录以 `/` 结尾，失活不会自动释放占用。

- <正在做什么，最近一次更新写在最上面>

## 下一步

- <接下来要做的事>

## 阻塞点 / handoff

- <没有就写"无"。离开时必填：未完成部分、相关文件、未推送改动位置>
