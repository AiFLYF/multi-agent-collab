# `.multi-agent-collab/` — 项目协作记录与工具

本目录保存同一项目的任务占用、工作日志、问题和指令，供本机的 AI 编码会话接力使用。
默认在项目 `.gitignore` 的套件标记区忽略；切分支会保留，换电脑或重新 clone 需要自行迁移。
根 `AGENTS.md` 的项目原有规则继续有效，协作规则见 [RULES.md](RULES.md)。

| 当前需要 | 阅读入口 |
|---|---|
| 新对话、上下文恢复或任务切换 | [START_HERE.md](START_HERE.md)：静态规则只读一次，动态状态重新检查 |
| 开工、消息、暂停、恢复或完成 | [WORKFLOW.md](WORKFLOW.md)：操作顺序与完整命令 |
| 文件字段或看板输出 | 本页 |
| 故障或失联占用 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) / [TAKEOVER.md](TAKEOVER.md) |
| 上下文预算与按需阅读 | [TOKEN_BUDGET.md](TOKEN_BUDGET.md) |

## 目录结构

```text
.multi-agent-collab/
├── RULES.md / START_HERE.md    # 协作总章 / 新对话入口
├── README.md / WORKFLOW.md    # 记录格式 / 操作手册
├── POLICY.md / policy.json   # 分支策略说明 / 配置事实源
├── install.json              # 项目名、已选平台、源码路径与版本
├── tools/                    # 看板、会话、消息与更新命令
├── sessions/                 # 每会话一文件，含 _template.md
├── worklog/                  # 每天一文件，只追加
├── inbox/                    # 每问题或指令一文件，含 _template.md
├── backups/                  # 升级前备份
├── takeovers/                # 接管计划与审计，保留历史
├── coordination.lock        # 主 worktree 共用的系统登记锁
└── TROUBLESHOOTING.md        # A/B/C 通用故障，D 组项目记录
```

按会话、事项和日期分片减少覆盖，工具用系统锁串行保护记录更新；手工编辑仍需协调。
共享库 `agents_policy.py`、`agents_layout.py`、`agents_notifications.py`、`agents_activity.py`、`agents_run.py`、`agents_jobs.py` 不作为 CLI 运行；`agents_processes.py` 由 run 内部启动。

**旧布局：** 已安装在 `.agents/` 的项目保留记录路径，工具仍在项目根 `tools/`。
以实际安装记录和根入口为准；下面示例使用新版路径，并在**目标项目根目录**运行。
套件源码开发使用源码 `tools/` 和本机 `.agents/`，禁止自安装。

## 会话文件 `sessions/<会话ID>.md`

建议用 `agents_session.py` 的 `start / heartbeat / handoff / resume / finish / activate` 维护，长命令可用 `run`，操作见 [WORKFLOW.md](WORKFLOW.md)。
文件名是实际会话 ID，格式为 `<agent>-<YYMMDD-HHMM>-<4位随机>`，例如 `trae-261009-1115-a3f9`。

| 头部字段 | 含义 |
|---|---|
| `agent` / `branch` / `task` | 平台、登记分支、当前任务 |
| `status` | `active` 执行中；`blocked` 中断保留占用；`standby` 完成上一任务等新命令；终态及兼容词见下文 |
| `files` | active/blocked 必须有完整非空范围，如 `["src/app.py", "docs/"]`；合法 standby 必须为 `[]` 且无运行记录，不占文件 |
| `heartbeat` | 已核验的业务调用、run 或显式心跳节流更新；活动不等于任务完成 |
| `last_activity / last_command` | 最近调用的秒级时间 / 固定工具名；不记录完整命令参数 |
| `running_jobs` | run 的包装进程、内部监督器、可选 POSIX 隔离组标识和开始时间；活着或无法核验时阻止收尾及范围转换，不保存参数 |
| `progress` / `handoff` | 简短进度 / 阻塞与下一步 |
| `handoff_inbox` | 本会话生成的交接条；resume / finish 只自动回执这一条 |
| `acknowledged_inbox` | start / resume 已确认纳入任务的人类事项，不改变原事项的 open 状态 |
| `closed_by / closed_at / takeover_plan` | 人工接管的出处；占用收回不表示原任务完成 |

键名不要改。支持的状态词为 `active / blocked / standby / done / superseded / closed / abandoned / cancelled / canceled / 完成 / 已完成 / 已关闭 / 已取消 / 废弃`。
状态按完整单词匹配、大小写不敏感，允许一段闭合、单行的中英文括号注释如 `active（执行中）`；
未知、缺失、拼接或损坏状态不能作为结束标记，告警并保留占用，数据错误返回 2。
这些词只是记录格式，手改终态不能绕过收尾；活着或无法核验的运行记录仍阻止释放占用。
heartbeat 保留 `YYYY-MM-DD HH:MM` 兼容格式；last_activity 使用工具写入的 ISO 秒级时间，不写口语描述。
活动时间无法解析、超过阈值或比当前时间快 10 分钟以上须核查；active/blocked 失活仍保留占用。
待机保留会话身份和历史，没有文件占用；普通调用可以记录活动，但不会变 active。
任务完成默认 `finish --standby`，新任务用 `activate --task ... --files ...` 重新检查并登记；done 不能复活。
已待机且明确不再接任务时，可用不带 --standby 的 finish 结束对话，summary/tests 仍须如实填写。
最终答复前等待自己负责的全部子代理、测试和 run 命令真正结束并收齐结果；未结束或结果未齐不得 finish/待机，未完成 handoff 保留占用。
已核验终止的孤儿运行记录可由原所有者通过合法状态操作清理；纯查询不代替任务完成，不手改记录释放范围。
头部记录当前状态，正文保留历史；状态、心跳、身份与测试文本不证明模型仍在执行、身份可信、测试通过或用户授权。
只修改自己的会话，不能冒用 ID 或因失活删除他人记录。
同一物理工作区只登记一个独立写任务，同任务子代理由主会话分配不重叠文件。

`start` / `activate` 会同时写入 `sessions/<会话ID>.baseline.json`，记录当时的改动路径与文件元数据快照；
看板据此计算 `drift`（不属于任何占用声明的实际改动）。旧记录没有基线时不推测，
基线通过随机任务标识绑定本次任务；缺失、损坏或写入失败只关闭漂移明细，不复用上一个任务的快照，不影响占用与门禁。
文件元数据用于低成本检测，不逐个读取业务文件内容；同大小、相同 mtime 的外部修改可能不可见，漂移也不能证明修改者。

## 工作日志 `worklog/YYYY-MM-DD.md`

- 只追加；订正历史时另追加一条并注明。
- 标题用 `## HH:MM · <会话ID>`，写清分支、实际变更、commit message（没有则说明）和测试结果。
- 一个可汇报阶段一条；`finish` 会追加结果，保存本机记录不需要 commit/push。

## 问事板 `inbox/Q-YYYY-MM-DD-NN.md`

一个文件一件事，写清**背景、已尝试的操作、需要对方做什么**。
AI 发布消息时 `from` 使用自己的完整会话 ID，定向 `to` 使用对方 ID，广播用 `any`；平台名仅兼容旧记录。
回复在原文件 `## 回复` 下追加，执行中保留 `open`，完成后置 `answered`。
跨 worktree 同名文件是不同事项，按工具返回的绝对路径区分。

看板的人类指令标记为 `from: human*`；该字段不证明身份，也不扩大用户授权。
控制类型使用顶部 `action: pause / cancel / avoid`，普通消息为 `message`。
控制的停止要求立即生效，阅读回执、任务确认和完成回复都不能自行解除；恢复步骤见 [WORKFLOW.md](WORKFLOW.md#中断恢复交接与收尾)。

## 按需读取消息

会话、状态、安装、同步、体检和更新工具会顺带提示相关未读事项及新回复。
出现提示再读正文；已读但仍在执行的人类事项只短提醒，无需循环读取同一内容。

```text
python .multi-agent-collab/tools/agents_messages.py list --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py read --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py read "<原文件绝对路径或唯一文件名>" --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py send --to <目标会话或平台或any> --body "..." --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py reply "<原文件绝对路径或唯一文件名>" --body "..." --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py reply "<原文件>" --body "完成结果与实际测试" --status answered --agent codex --session <自己的ID>
python .multi-agent-collab/tools/agents_messages.py archive --older-than 30
python .multi-agent-collab/tools/agents_messages.py archive --older-than 30 --limit 100 --apply --agent codex --session <自己的ID>
```

`send` / `reply` 使用当前已验证会话 ID 作为 `from`，不能匿名或冒用他人身份；
agent 只能发送 `message`，`pause / cancel / avoid` 控制指令仍只由人类看板发出。
`reply` 默认保持 `open`；处理原要求后显式 `--status answered`，记录实际完成结果与时间。
可回复发给 `any`、当前平台或当前会话的消息；`--request-version` 接受 `read` 返回的原要求版本，避免用旧要求的结果关闭新任务。
写入前核对原字节、路径和身份，保留 BOM、换行、权限、原要求和已有回复；代码围栏中的回复标题不作为分界。
消息字段不证明身份或测试通过，实际授权仍以用户要求为准；已解除的人类控制不会被回复重新激活。

`archive` 仅归档当前工作区已关闭的普通消息到 `inbox/archive/YYYY-MM/`；创建、回复和最后修改均须超过保留期（默认 30 天）。
省略 `--apply` 只预览、零写；实际归档须提供自己的 `--agent/--session`，每批默认 100 条，`--limit` 为 1–1000。
未答事项、控制、未读回复及未结束会话的引用保留；日期无效或记录损坏列入 `skipped`。等待锁及移动前复核原版本，目标同名不覆盖。
本批失败尝试逆序回滚；外部修改阻止回滚时报告保留位置。输出失败且已写入时先核对位置，不直接重复发送或归档。
归档扫描可在实时队列超过 4096 条时继续工作；输出按 `--max-chars` 裁剪，完整总数不截断。预览截断返回 1，应用成功返回 0 并标明是否省略明细。
归档后不参与实时消息扫描，历史可到归档目录读取；回执不随归档自动清除，长期使用仍需核对回执容量。

`list` 只看元数据。默认 `read` 只读相关未读正文，指定文件可重读。
明确的 `--agent / --session` 可顺带记录自己的活动；审计时加 `--no-activity` 保持零写。
确认输出完整时可加 `--mark-read`，显式保存本会话阅读回执；它不改 inbox 状态、回复或任务确认。
截断返回 1 且不标读；输出或内容版本核验失败返回 2，撤销本次回执。
接收端还应核对工具结果是否完整，回执不表示模型理解或用户授权。

阅读回执按工作区、平台、会话隔离。原要求改变会重新提醒收件人，新回复或回复状态变化提醒原提问者；
自己写的进度回复不会让自己重复阅读原要求。
未提供身份时，只安全推断当前工作区唯一未结束会话；否则仅查看广播或已指定平台，不共用别人的标读状态。
原 active/blocked 任务的新要求通过 `resume --ack-inbox … --files …` 纳入；standby 接新任务用 activate，重新给 task 和完整范围。
实际处理完再回原文件写结果，并在最终答复前完成收尾；命令见 [WORKFLOW.md](WORKFLOW.md#工具顺带提示与消息处理)。

<details>
<summary>消息预算与脚本接口</summary>

默认输出预算 6000 字符，用 `--max-chars` 调整（1024–100000）；单条上限 1 MiB，4096 条以上明确报错。
未完整阅读的事项不能凭部分文本 ack。仅已处理历史可用 `archive` 归档，不能归档未答事项绕过门禁。
JSON 仅在需要时附加可选 `notifications`；普通通知不改变原业务退出码。
只有已读但未完的人类事项时，`read_command` 为 `null`，不要求读取空集。
通知在工具调用时送达；run 可记录长命令期间活动，收到指令仍须按要求处理。待机不是后台监听或自动唤醒。

</details>

## 查看全景

```text
python .multi-agent-collab/tools/agents_status.py --context --agent codex
python .multi-agent-collab/tools/agents_status.py --web
python .multi-agent-collab/tools/agents_status.py --brief
python .multi-agent-collab/tools/agents_status.py --html
```

| 输出 | 用途 |
|---|---|
| `--context --agent <平台> [--session <自己的ID>]` | 开工摘要：策略、未结束占用、定向 inbox、近 1–2 天有限日志 |
| `--brief` | 一行看板状态，不替代完整开工核查 |
| 不加输出参数 | 本机所有 linked worktree 的会话、日志与未答事项 |
| `--web` | 给人看的实时看板，每 5 秒刷新，可发指令和查看回复 |
| `--html [路径]` | 自包含离线快照，默认 `.multi-agent-collab/board.html`；`--open` 导出后打开 |
| `--json` | 结构化数据，便于脚本读取 |
| `--prs` | 通过 gh 查询 open PR；查询不可完成或不可核验时告警并返回 1 |

`--context` 默认预算 6000 字符，可用 `--max-chars` 调整；截断会列出未读路径，先读原文。
退出码 0 表示无告警，1 表示协作告警或未读项，2 表示工具/数据错误；检查不锁住源码。
从其它目录运行时使用实际脚本路径，并可加 `--root "<项目绝对路径>"` 指定目标。

本地看板默认监听 `127.0.0.1`，空闲 agent 用「复制提醒」粘贴到对应对话中唤起。
局域网只读分享用 `--web --bind 0.0.0.0 --no-directives`；
需要对外可写时先设置 `MULTI_AGENT_BOARD_PASSWORD`，未设密码会拒绝启动。
`MULTI_AGENT_BOARD_READONLY=1` 可强制只读；HTTP 仅用于可信网络，远程访问可使用 SSH 端口转发。

## 新增平台与更新

新增平台执行 `python "<套件目录>/install.py" sync "<目标项目绝对路径>" --platforms cursor`；
省略 `--platforms` 则刷新已选入口，保留项目总章和运行记录。
安装选择与套件源路径记录在 `install.json`；完整更新、源码迁移和旧版兼容见 [WORKFLOW.md](WORKFLOW.md#更新已安装的规则与工具)。

更新后重启长驻看板，并让旧模型对话重读变化部分。
本机记录不随 Git 备份，重要节点拷贝到仓库外；任何记录都不要写入密钥、密码或 API key。
