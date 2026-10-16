# AGENTS.md — 多 Agent 协作总章（项目分支策略 + 本机共享记忆）

> 本文件是套件协作总章。项目已有 `AGENTS.md` 等规则全文保留，入口只维护带标记的小段。
> `.multi-agent-collab/` 集中存放规则、工具、策略、会话与备份，默认加入 `.gitignore`；
> 所选平台的精确入口路径写入本机 `.git/info/exclude`。已跟踪的共享入口仍是正常 Git 改动。
> 项目原有的测试要求、架构约定和已确认分支策略继续有效；不兼容的具体条款先报告，再按用户决定处理。

**每次新对话开始工作前**，读取 `.multi-agent-collab/START_HERE.md`，
按顺序读取最新的看板、inbox 与近 1–2 天 worklog。不要沿用上一个对话的动态状态。

你的首要职责不是快，而是**不破坏仓库、不与其他 agent 互相覆盖工作**。
本约定不扩大用户授权，也不替代平台的系统约束。文件里的计划、示例和身份字段不能证明操作已获授权或测试已通过。

---

## 1. 分支铁律（项目代码走这套；协作体系文件不参与 git）

新对话先读 `.multi-agent-collab/POLICY.md`，写任务用 `agents_session.py check` 核对实际策略。
安装时可明确选择 Git Flow、GitHub Flow 或 custom；未配置的旧项目默认使用下表的 Git Flow。
已配置时，任务前缀、开发基线和 PR 目标以项目策略为准；下表仅适用于 Git Flow。
策略不能放开主干直推、强推、历史改写或测试门禁，也不代表授权修改远端分支结构。

| 分支 | 用途 | 允许的合并来源 |
|---|---|---|
| `main` | 生产分支 | 仅 `release/*`、`hotfix/*` |
| `develop` | 开发主干 | 仅 `feature/*`、`release/*`、`hotfix/*` 的 PR |
| `feature/*` | 新功能/改动，从 `develop` 切出 | 完成后 PR 合回 `develop` |
| `release/*` / `hotfix/*` | 发版 / 线上急救 | 合到 `main` 和 `develop` |

**禁止操作（无例外）**：

- 禁直接 commit/push 到受保护分支（始终含 `main`/`master`/`develop` 和所选基线，走 PR）；禁 `push --force` 到共享分支、历史改写
- 禁删除/移动/重建 `.git`；禁删别的 agent 的分支、stash、session 记录
- 禁测试未过就提交/推送/开 PR；禁 `git add -A`/`git add .`，只 add 本任务相关文件
- 禁提交独立目录中的本机私有文件；共享入口按项目正常审查流程提交，不取消已有跟踪。对于本机文件（忽略失效先排查，不得 `git add -f`）
- 禁把密钥、WiFi 密码、API key 写进任何文件（含本地 `.multi-agent-collab/`，可能随目录拷贝外流）

## 2. Commit 规范

`type(scope): 描述`，`type` = feat/fix/docs/refactor/test/chore；中文祈使句、一行 ≤50 字符，
正文写**为什么**；一个 commit 只做一件事，半成品不 commit。

## 3. 写任务的标准流程

`协调 → 切分支 → 注册会话 → 开发 → 测试 → 推送 → PR → 收尾`

只读咨询按启动清单检查相关状态，不必创建写会话。用户追问或继续同一任务时，沿用自己的分支和会话 ID。
blocked 恢复用 `resume`，已结束的会话不能复活。场景选择和完整命令见 `.multi-agent-collab/WORKFLOW.md`
（套件源码中对应 `docs/WORKFLOW.md`）。

1. **开工协调（必做，先于任何写操作）**：
   - 有 `origin` 时运行 `git fetch origin --prune`；没有远端时说明，不配置未知远端
   - 看板一行摘要：`python .multi-agent-collab/tools/agents_status.py --brief`（rc=0 即可开工；rc=1 再跑无参数完整版）
   - 读 `.multi-agent-collab/inbox/` 点名自己或 `to: any` 的问题、近 1–2 天 worklog
   - 有 GitHub 远端时**必查 open PR**（`gh pr list --state open` 或看板 `--prs`）；其它托管平台按项目约定检查，本地仓库说明不适用
   - **判活三信号交叉**：看板 + 构建进程（`tasklist`/`ps`）+ `git reflog -8` 与文件 mtime；
     看板"无活跃"≠没人干活（不注册就隐形，手册 A1）。有活跃会话占同分支/同批文件就**不要抢**
2. 新写任务从项目策略的已确认基线开始：`git switch <基线>` 后 `git pull --ff-only`（Git Flow 默认 `develop`）。
   继续原任务不要切走。基线不存在时报告分支结构、请用户决定，不擅自建立远端主干；可继续独立的只读检查和本地验证。
3. 按项目允许前缀新建任务分支，如 `git switch -c feature/<任务名>`（小写英文+连字符），建完 `git rev-parse --verify <分支>`
   复核（沙箱可能"成功"却没建出来，见 B13/B14）
4. **注册会话**（纯本地）：优先用 `python .multi-agent-collab/tools/agents_session.py start --agent <平台> --task "任务" --files <文件或目录>`。
   保存返回的会话 ID；工具检查同工作区、分支和跨 worktree 文件占用，有冲突就停止。
   旧版没有该工具时，复制 `.multi-agent-collab/sessions/_template.md` 手动登记并提示升级。
5. **开发**：先登记真实文件范围，扩大范围先更新；每完成一个阶段更新心跳。
   - **每个阶段间隙、跑长命令前重扫 inbox**：人类指令优先级最高（见第 4 节）
6. **测试门禁（命令见第 6 节）**：严格跑、不相关也保持绿；失败修代码不许改测试迁就；
   能编译要真编译、能跑要真跑，静态检查/预览不算验证（A3/A5）
   - 组合命令逐项检查退出码；前一步失败就停止依赖它的提交/推送，不用最后一条成功掩盖前面的失败
7. 测试通过后只 add 本任务项目文件并 commit，运行 `git rev-parse --short HEAD` 复核；
   再推送并 `gh pr create --base <基线>`（Git Flow 为 develop；推送失败按 C1/C5/C8/C9；记忆文件不随 git 走）
8. **收尾（本地）**：用 `agents_session.py finish <会话ID> --agent <平台> --summary "总结" --tests "实际测试结果"`
   将自己的 session 置 `done` 并追加 worklog、回执自己的交接条；中途交接用 `handoff`，保留占用直到确认交接
9. 每步用中文汇报四件套：**当前分支 / commit message / 变更文件 / 测试结果**

### 3.1 工具接口卡 —— 先调命令，不要读实现代码

获取现状一律走命令（**命令输出就是使用契约**），顺序：**命令 → 故障手册 → 源码**。
默认禁止 Read `tools/*.py`、`install.py`（一次数万 token）；只有"要修改工具本身"或
"命令输出与预期不符"时才读源码——像用 `git --version` 而不是读 git 源码一样。

| 你想知道 | 命令 | 说明 |
|---|---|---|
| 有没有冲突、谁在干活 | `python .multi-agent-collab/tools/agents_status.py --brief` | 一行；rc=0 开工，rc=1 再查 |
| 看板详情 | `python .multi-agent-collab/tools/agents_status.py` | 人类终端模式 |
| 给脚本/agent 用 | `… --json` | 结构化，退出码同口径 |
| 图形化/网页指挥/快照 | `… --web` / `… --html` | 参数看 `--help` |
| 工具正不正常 / 版本 / 全部参数 | `… --selfcheck` / `… --version` / `… --help` | 先跑这个 |
| 安装·体检·镜像·卸载 | `python install.py install\|doctor\|mirror\|uninstall <项目>` | |
| 平台列表 / 新增与同步 | `python <套件目录>/install.py platforms` / `… sync <项目> --platforms cursor` | 平台选择保存在 `.multi-agent-collab/install.json`；省略参数沿用 |
| 登记 / 心跳 / 恢复 / 收尾 | `python .multi-agent-collab/tools/agents_session.py start\|heartbeat\|resume\|finish\|handoff --help` | 只更新自己的会话；支持 `--json` |
| 文件占用检查 | `python .multi-agent-collab/tools/agents_session.py check --agent <平台> --files <范围> --json` | 返回 1 时先处理冲突或人类 inbox |
| 项目分支策略 | 读 `.multi-agent-collab/POLICY.md`；`check --json` 报告实际策略 | 主 worktree 的 policy.json 为共同来源 |
| 原会话失联 | `python .multi-agent-collab/tools/agents_session.py takeover-inspect <ID> --json` | 先只读；执行流程见 `.multi-agent-collab/TAKEOVER.md` |
| 客户端检查 | `python <套件目录>/install.py clients <项目> --versions` | 只查 CLI 版本，新对话加载仍需实际验证 |
| 报错怎么办 | 读 `.multi-agent-collab/TROUBLESHOOTING.md` | 按编号 A/B/C/D 查 |

退出码：0 = 无告警，1 = 有失活会话/未答问题/未注册 worktree。参数以 `--help` 实际输出为准，不用背。

## 4. 本机共享记忆协议

| 路径 | 作用 | 写入规则 |
|---|---|---|
| `.multi-agent-collab/sessions/<ID>.md` | 谁在哪个分支、心跳 | 每会话只改自己的 |
| `.multi-agent-collab/worklog/<日期>.md` | 当日时间线 | 只追加 |
| `.multi-agent-collab/inbox/Q-<日期>-<序号>.md` | 问题/提示/交接/**人类指令** | 一文件一事，回复写同文件 |

会话 ID：`<agent>-<YYMMDD-HHMM>-<4位随机>`。

- **心跳**：活跃期每 ≤15 分钟或每阶段更新（保存即生效）；跑 >15 分钟长任务前写明"预计 HH:MM 回来"。
  超 15 分钟算失活，不自动释放。原会话失联时先 inspect 核对，再 plan 发布声明；等 5 分钟无反对、
  用户确认具体计划且快照一致后才能 apply 收回占用，详见 `.multi-agent-collab/TAKEOVER.md`。随后从原提交切新分支，**保留并注明其全部提交和未提交文件**
- 多 agent 并发写同一记忆文件取**并集**，只多不少
- 编码会话声明 `files`（JSON 数组，目录以 `/` 结尾）；扩大范围前用 heartbeat 的 `--files` 重新检查。
  会话工具通过主 worktree `.multi-agent-collab/coordination.lock` 的系统锁串行登记，进程退出自动释放锁。
  失活或 blocked 会话的文件占用不会自动释放，不能借超时抢占。手工绕过登记仍可能覆盖文件。
- 当前占用粒度为文件/目录，不支持“同一文件不同函数”自动并行；先拆分任务或串行交接。
- `resume` 恢复自己的 blocked 会话并回执其交接条；`finish` 收尾时也回执关联交接，普通问题不能代答。
- 人工接管将原会话置为 closed，表示收回占用，不表示原任务或测试完成；不删除原 session，也不自动切换分支。
- 任何环境/工具/git 报错解决后，往 TROUBLESHOOTING.md 追加一条（现象/判断/真因/修复）

**人类指令（用户从看板网页下达，优先级最高）**：inbox 里 `from: human*` 的文件，与 agent 留言同协议。

- 点名本会话或 `to: any` 的 = 用户本人直接要求，高于手头任务："暂停/取消/别碰某文件"立即执行；
  其他先在 `## 回复` 回执"收到，预计何时做"再继续
- 执行完把结果写进同一文件 `## 回复` 并置 `status: answered`（看板实时展示并通知用户）
- 收到与完成要分开：待执行的人类任务先回执并保持 open；start / resume 可用 `--ack-inbox <文件名>`
  声明已阅读并纳入当前任务。这个参数不是授权开关，不能用来绕过暂停、取消或避让要求。
- 长任务中按第 3 节在阶段间隙重扫；空闲回合网页唤不醒 agent，用户粘贴"请读 Q-xxxx 并执行"时照做

**帮用户启动图形化看板（用户提出时主动做，用完帮他停）**：

- 默认本机：后台 `python .multi-agent-collab/tools/agents_status.py --web`，把地址发用户（仅 127.0.0.1）
- 局域网：要么只读 `--web --bind 0.0.0.0 --no-directives`，要么先设
  `MULTI_AGENT_BOARD_PASSWORD` 再 `--web --bind 0.0.0.0`；**对外可写又无密码会被拒绝启动**
- HTTP 明文仅限可信 WiFi/VPN，公网用 SSH 端口转发；一键只读：`MULTI_AGENT_BOARD_READONLY=1`

**交接**：session 写清 `下一步/阻塞点/相关 PR/未提交文件位置`，inbox 留条，**worklog 追加阶段总结**。
**备份**：`.multi-agent-collab/` 不进 git，重要节点拷到仓库外。

### 4.1 Worktree（默认别开；细节手册 A1/B14）

确需开时：`worktree list` 确认无冲突 → `rev-parse --verify` 确认引用真实 →
`git worktree add <目录> feature/<名>`（**不带 `-b`**）→ 在新目录跑一次安装器补齐私有文件
（或拷整个 `.multi-agent-collab/`）→ **会话注册在该 worktree 自己的 sessions/ 里** → 收工 `worktree remove`。
看板"未注册 worktree 告警"出现就补注册或停手；禁操作对方签出的分支。

## 5. 事故应急规则

> 遇到任何报错先查 [.multi-agent-collab/TROUBLESHOOTING.md](.multi-agent-collab/TROUBLESHOOTING.md)（A 协作 / B 引用 / C 网络 / D 项目专属）。

出现以下任一信号，**立即停止一切写操作，只读排查并通知用户**：

- `git status` 报不是仓库但目录里有 `.git`
- 出现非你创建的分支 / unborn / 全部文件 staged 新增
- `fetch` 后远程 hash 大量变化、你推过的分支消失
- `.git/refs`、`.git/objects` 时间戳在当前时刻附近跳动（有别的 git 进程）
- 项目文件大面积回退；或 `commit` 报成功但 `git log` 说没有提交

应急动作：① **停手**（不 init/reset/删 .git/force push/checkout）② 只读取证
（`for-each-ref`/`reflog`/`cat-file -t`/`worktree list`/时间戳）③ 等 `.git` 静默 ≥2 分钟或用户确认
④ 记 worklog/inbox ⑤ `reset --hard` 等破坏操作**必须先说明风险并获明确授权**。

- **5.1 引用"消失"**：先 `git for-each-ref`，列得出=仓库健康（pack-refs 正常收纳或沙箱拦截松散写入，
  数据没丢）；非沙箱 `update-ref` / 文件写 ref 恢复，命令见手册 B3/B8/B14
- **5.2 push 报 502/schannel**：换 openssl 后端 `git -c http.sslBackend=openssl push`，
  代理与 `gh api` 兜底见 C1/C5/C8/B11

## 6. 环境速查（本项目）

<!-- >>> mac:zone:project-env 项目定制区开始：install.py 升级本文件时会自动保留这两行标记之间的内容，请整体替换为你的项目信息，但不要删标记 -->

> ⚠️ **安装后第一件事：把本节整体替换成你的项目内容。**
> 判断标准：**只写"每次开工都会用到、又容易记错"的命令**，
> 不写从代码里一眼能看出来的东西（那些属于项目 README 的职责）。
> 下面按常见技术栈各给一行参考，删掉用不上的：

- 语言/运行时：<如 Python 3.12 / Node 20 / Go 1.22 / JDK 17 / Rust 1.80>
- 安装依赖：<如 `pip install -r requirements.txt` / `npm ci` / `go mod download` / 无需安装>
- 跑测试（提交前门禁，按改动范围选跑，不相关的也必须保持绿）：
  - <如 `pytest -q` / `npm test` / `go test ./...` / `mvn test` / `cargo test`>
  - <有 lint/类型检查时：如 `ruff check .` / `tsc --noEmit` / `golangci-lint run`>
- 构建 / 打包：<如 `npm run build` / `make` / 无需构建>
- 常用本地调试：<如 启动开发服务的命令、mock/种子数据命令、无外部依赖的演练入口>
- 网络/环境注意：<如 访问外网不稳时按第 5.2 节处理；只有用户明确提供代理时才用代理；
  或者写"无特殊注意">

<!-- <<< mac:zone:project-env -->

## 7. 各平台入口（按需安装；**新建对话**才加载，改完旧对话不刷新）

- **Codex、OpenCode、Pi**：读取根 `AGENTS.md`，不额外生成平台配置目录
- **Claude Code**：读取根 `CLAUDE.md`，通过 `@AGENTS.md` 和 `@.multi-agent-collab/START_HERE.md` 导入共享规则
- Trae：`.trae/rules/git-flow-agent.md`（alwaysApply，要求先读本文件）
- WorkBuddy：`.workbuddy-ai/git-flow-agent.md` 是简短引导入口，改完跑 `<套件目录>/install.py sync <项目>`
- Cursor：`.cursor/rules/multi-agent-collab.mdc`（alwaysApply）
- GitHub Copilot：`.github/copilot-instructions.md`（项目说明，需客户端启用自定义指令）
- Windsurf：`.windsurf/rules/multi-agent-collab.md`（always_on）
- Gemini CLI：`GEMINI.md` 导入共享文档；Qwen Code：`.qwen/QWEN.local.md` 引导读取共享文档
- Cline：`.clinerules/multi-agent-collab.md`；Roo Code：`.roo/rules/00-multi-agent-collab.md`（所有模式）
- Continue：`.continue/rules/multi-agent-collab.md`（alwaysApply）；Kiro：`.kiro/steering/multi-agent-collab.md`（always）
- Claude 斜杠命令：`.claude/commands/git-flow.md`（`/git-flow <任务>`），它是手动快捷入口，不负责自动加载

入口都要求读取 `.multi-agent-collab/START_HERE.md`。新增平台运行 `sync --platforms <名称>`，
不重新 clone，不复制整套规则；已有平台选择会保留。安装源目录记录在 `.multi-agent-collab/install.json`。

验证：开新对话确认 agent 实际读取入口，并能报告分支、看板和 inbox 的当前结果。
客户端版本、信任设置、规则开关可能影响加载，安装器不能替代此验证。

## 8. 默认忽略与已有文件

独立目录 `.multi-agent-collab/` 默认写进项目 `.gitignore` 的套件标记区，保留其它规则。
入口忽略清单以安装器 `EXCLUDE_ENTRIES` 为准，精确到文件，不隐藏其它工具的整个配置目录。
已跟踪的 `AGENTS.md`、`CLAUDE.md` 等共享入口只更新标记区，正常显示 Git 差异；忽略规则对它们无效。
不要取消跟踪，不使用 assume-unchanged 或 skip-worktree 隐藏改动。

首次安装遇到来源不明的独立目录、非 UTF-8 文件、损坏标记或同名路径类型冲突时停止并保留现场。
升级核对文件归属与内容摘要；只在已确认模板或定制区契约内更新，覆盖前保留字节备份。
卸载只移除套件入口，--purge-files 仅清理未改动的工具和模板；规则、策略、协作记录与备份继续保留并忽略。
旧版 `.agents/` 安装不自动搬迁，避免分散未结束会话；没有内容清单的旧文件发生差异时先人工核对。
