# Session: <会话ID>

- agent: <trae | workbuddy | claude | human | ...>
- branch: feature/<任务名>
- task: <上一任务或尚未开始>
- files: []
- status: standby
- started: YYYY-MM-DD HH:MM
- heartbeat: YYYY-MM-DD HH:MM
- pr: <PR 链接，没有就留空>

## 当前进度

这是未占用文件的待机模板，不能据此写代码；优先让工具返回真实 ID 和状态。
首次任务用 start；已有待机 ID 用 activate --task ... --files ... 登记新任务。
active/blocked 的 files 必须是非空 JSON 数组，如 `["src/login.py", "docs/"]`；目录以 `/` 结尾。
普通业务命令带自己的平台和 ID 会记录活动；失活或 blocked 不释放占用。
完成前真跑 finish --standby，实际记录结果与测试；未完成 handoff，不能手改待机冒充完成。
字段只是本机记录，不证明身份、模型仍在执行、测试通过或用户授权；收尾须核对真实进程和结果。

工具缺失时，先按根规则手工完成策略、分支、占用、inbox 与进程核查，再用本模板新建自己的会话文件，
把 `<会话ID>` 换成唯一 ID、`task` 换成真实任务、`status` 改为 `active`、`files` 改为完整非空 JSON 范围，
`branch / started / heartbeat` 填实际分支与当前时间后才开始写入，并提示升级工具。
不能以空范围的 standby 模板代替首次登记；工具已存在但门禁拒绝时不能退回手工登记绕过。
旧工具不能执行 finish 时，只能在任务确已完成、全部子代理/运行命令结束且结果核验后手工记录结果，
追加 worklog 并将自己的状态改为 standby、files 改为 []；未完成保留 blocked 与占用，不伪造命令通过。

- <正在做什么，最近一次更新写在最上面>

## 下一步

- <接下来要做的事>

## 阻塞点 / handoff

- <没有就写"无"。离开时必填：未完成部分、相关文件、未推送改动位置>
