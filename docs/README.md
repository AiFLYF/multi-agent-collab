# `.agents/` — 多 Agent 共享记忆区（本机）

这里是这台电脑上所有 AI 编码会话之间的**公告板 + 交接班本**。
**纯本机目录，不进 git**（已在 `.git/info/exclude` 中忽略）：同一工作区里切任何分支它都在，
所有 agent 直接读写同一份；换电脑/重新 clone 不会带走，需要自行拷贝。
行为规则见根目录 [AGENTS.md](../AGENTS.md)，本文件只说明格式。

## 目录结构

```
.agents/
├── README.md                 # 本文件
├── START_HERE.md             # 每次新对话的最小阅读顺序
├── install.json              # 项目名、已选平台、套件源路径与版本
├── coordination.lock         # 登记过程的系统锁（主 worktree 共用，退出自动释放）
├── sessions/                 # 每个活跃会话一个文件：我是谁、在哪个分支、心跳
│   ├── _template.md
│   └── <会话ID>.md
├── worklog/                  # 每天一个文件，只追加：做完了什么
│   └── YYYY-MM-DD.md
├── inbox/                    # 每个问题/交接一个文件：问其他 agent 的事
│   ├── _template.md
│   └── Q-YYYY-MM-DD-NN.md
└── TROUBLESHOOTING.md        # 故障手册：A/B/C 通用组 + D 组项目专属（只追加）
```

**报错先查 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)**：引用消失、push 502、
沙箱拦截 git 写引用、worktree 盲区、代理/终端环境异常等已知问题都有现成的判断和修复步骤；
解决了新问题必须往该手册追加一条。

为什么这样分片：多个 agent 同时写同一个文件必然冲突。**一会话一文件、一问题一文件、
一天一文件**，各写各的，万一冲突合并时取并集。

## 新对话与新增平台

每次新对话先读 [START_HERE.md](START_HERE.md)，不要把上个会话的看板与 inbox 状态当现状。
原生读取 `AGENTS.md` 的平台共享根规则；其它平台安装自己的启动入口并指向相同文档。
根规则修改后，新对话会重新读取；WorkBuddy 的完整镜像需要同步。

新增平台用 `python "<套件目录>/install.py" sync "<项目>" --platforms cursor`；
日常刷新入口省略 `--platforms`。选择会保存在 `install.json`，升级沿用，追加不会删除其它平台。
套件默认复用用户主目录的 `multi-agent-collab`，实际位置以 `install.json` 的 `source_dir` 为准。

## 会话文件 `sessions/<会话ID>.md`

- 文件名即会话 ID：`<agent>-<YYMMDD-HHMM>-<4位随机>`，如 `trae-261009-1115-a3f9`
- 开头的键值块供 `tools/agents_status.py` 解析，**键名不要改**，时间一律
  `YYYY-MM-DD HH:MM`（**本机本地时间**，24 小时制；不要写时区后缀、不要写口语如"刚才"、
  不要写未来时间——无法解析的心跳一律按失活处理）
- status（取第一个词，精确匹配；不要用"未完成"这种包含"完成"的写法）：
  - `active` 干活中
  - `blocked` 被卡住、等人接手（**仍然会计入失活告警**，这正是你想要的效果）
  - `done` 已完成 / `abandoned` 废弃 / `closed` 关闭 —— 这几种不再报失活
- heartbeat：每 ≤ 15 分钟或每完成一阶段更新，**保存文件即生效，不用 commit/push**；
  超过 15 分钟没更新（或时间无法解析、比当前时刻快 10 分钟以上）视为失活
- 收工把 status 置 `done`；没做完要走，置 `blocked`、填 `handoff` 并在 inbox 留交接问题
- 建议使用 `tools/agents_session.py` 的 start / heartbeat / finish / handoff 维护，避免漏字段。
  `files` 是 JSON 数组（如 `["src/app.py", "docs/"]`）；看板显示占用并提醒交叉冲突。
  同工作区不可重复登记；独立 worktree 可并行处理不重叠的文件。失活不会自动释放占用。

## 工作日志 `worklog/YYYY-MM-DD.md`

- **只追加，不修改历史条目**（订正就新追加一条并注明）
- 每条用二级标题 `## HH:MM · <会话ID>`，正文写：分支、做了什么、commit、测试结果
- 粒度：一个可汇报的阶段一条，不用每条命令都记

## 问事板 `inbox/Q-YYYY-MM-DD-NN.md`

- 一个文件一件事；`to:` 写会话 ID 或 `any`
- `from: human*` 的文件是**人类用户从看板网页发的指令**（不是 agent 留言），
  按用户最高优先级处理，回复仍写回同一文件
- status：`open` → `answered`（回复写在同文件 `## 回复`）
- 提问三要素：**背景 / 我已尝试 / 需要你做什么**
- 开工先看有没有点名自己的 open 问题；离开前把能答的答掉

## 查看全景

```powershell
python tools/agents_status.py --brief  # 开工首选：一行摘要（OK 即可开工，最省 token）
python tools/agents_status.py          # 完整看板：本机全部 worktree 的活跃会话 + 日志 + 未答
```

- **省 token**：日常开工用 `--brief`（约 80 token，无告警只回一行 OK，rc=1 才展开完整版）；
  不要为了"看现状"去读 `tools/agents_status.py` 源码——命令输出就是使用契约（AGENTS.md 3.1 接口卡）
- 在项目**任意子目录**运行都会自动定位到仓库根；也可用 `--root <路径>` 显式指定
- 看板会聚合主工作区和所有 linked worktree 里的 `.agents/`（同一会话 ID 取最新心跳）
- 加 `--prs` 可同时只读列出 open PR（gh 不可用时只提示、不报错，退出码不变）
- 退出码 0 = 无告警，1 = 有失活会话/未答问题/未注册 worktree，可当开工门禁
- **给人看（不影响 agent）**：`--web` 启动浏览器图形化实时看板
  （5 秒自动刷新）；`--html [路径]` 导出零外链单文件快照
  （默认 `.agents/board.html`，可离线打开或发给别人；`--open` 导出后自动打开）
- **人在网页上指挥 agent**：`--web` 页面可直接发指令（默认仅 127.0.0.1）。
  指令就是本目录 `inbox/` 里 `from: human` 的标准问事板文件（`to: any` 广播或点名会话），
  agent 按 AGENTS.md 规程在阶段间隙读取、把回复写回同一文件，页面实时显示回复并可弹桌面通知；
  agent 空闲时用每条指令旁的「复制提醒」把一句话粘进 agent 对话框唤醒它
- **局域网访问三档**：默认仅本机；`--bind 0.0.0.0 --no-directives` 只读分享；
  设环境变量 `MULTI_AGENT_BOARD_PASSWORD` 后再 `--bind 0.0.0.0` 为带密码可指挥
  （对外可写又无密码会拒绝启动；HTTP 明文仅限可信网络，外网用 SSH 端口转发）。
  `MULTI_AGENT_BOARD_READONLY=1` 可一键只读

## 注意

- 这里虽然不入库，但可能随目录拷贝到别处：**不要写密钥、WiFi 密码、API key**
- 不进 git 意味着没有远程备份：重要节点把 `.agents/` 拷一份到仓库外
