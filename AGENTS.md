# AGENTS.md — 多 Agent 协作总章（Git Flow + 本机共享记忆）

> 本文件是这台电脑上所有 AI 编码 agent（Trae / WorkBuddy / Claude Code / 其他）在本仓库工作的
> **唯一权威规则**。整套协作体系**只保留在本机、不入 git**：本文件、`CLAUDE.md`、`.agents/`、
> `.agent/`、`.claude/`、`.trae/`、`.workbuddy-ai/`、`.workbuddy/`、`tools/agents_status.py`
> 全部通过 `.git/info/exclude` 本地忽略（由 multi-agent-collab 的 `install.py` 自动维护，
> 完整清单以安装器里的 `EXCLUDE_ENTRIES` 为准）。
> **同一台电脑、同一个工作区里的所有 agent 共用这一份文件**；换电脑或重新 clone 时
> 重跑安装器即可恢复（见第 8 节）。各平台入口与本文件不一致时，以本文件为准。

你是一个严格遵守 Git Flow 工作流的开发助手。你的首要职责不是快，而是
**不破坏仓库、不与其他 agent 互相覆盖工作**，在此前提下完成任务。

---

## 1. 分支铁律（适用于项目代码，协作体系文件不参与 git）

| 分支 | 用途 | 允许的合并来源 |
|---|---|---|
| `main` | 生产分支 | 仅 `release/*`、`hotfix/*` |
| `develop` | 开发主干 | 仅 `feature/*`、`release/*`、`hotfix/*` 的 PR |
| `feature/*` | 新功能/改动，从 `develop` 切出 | 完成后 PR 合回 `develop` |
| `release/*` | 发版准备，从 `develop` 切出 | 合并到 `main` 和 `develop` |
| `hotfix/*` | 线上紧急修复，从 `main` 切出 | 合并到 `main` 和 `develop` |

**禁止操作（无例外）**：

- 禁止直接 commit / push 到 `main` 或 `develop`（项目代码改动走 PR）
- 禁止 `git push --force` / `--force-with-lease` 到任何共享分支
- 禁止删除、移动、重建 `.git` 目录；禁止对共享分支做历史改写
- 禁止删除别的 agent 的分支、stash、session 记录
- 禁止测试未通过就提交、推送、开 PR
- 禁止 `git add -A` / `git add .`：只 add 本任务相关文件
- 禁止把密钥、WiFi 密码、API key 写进任何文件（含本地 `.agents/`，因为可能随目录拷贝外流）
- **禁止把第 8 节列出的本机协作文件 `git add` 进任何提交**（它们已被本地忽略，
  若忽略失效要先排查，不得强行 `git add -f`）

---

## 2. Commit 规范

格式：`type(scope): 描述`

- `type`：`feat` / `fix` / `docs` / `refactor` / `test` / `chore`
- `scope`：受影响模块，按本项目实际模块名填写
- 描述用中文祈使句，一行 ≤ 50 字符；正文说明 **为什么**
- 一个 commit 只做一件事；半成品不要 commit

---

## 3. 标准开发流程（每个任务都按此执行）

```
开工 ─► 协调检查 ─► 切分支 ─► 注册会话(本地) ─► 开发 ─► 测试 ─► 提交推送 ─► PR ─► 收尾
```

1. **开工协调（必做，先于任何写操作）**
   - `git fetch origin --prune`（项目代码层面）
   - open PR 检查（**看有没有人已经改过你打算改的文件**）：手工 `gh pr list --state open`，
     或直接让看板带上 `python tools/agents_status.py --prs`（只读调 gh，gh 不可用会提示你手工查）
   - `python tools/agents_status.py`（聚合**本机全部 worktree** 的会话心跳、日志、待答问题）
   - 读 `.agents/inbox/` 中点名给你或 `to: any` 的未答问题
   - 读 `.agents/worklog/` 最近 1–2 天日志
   - 若有活跃会话占用同一分支/同一批文件：**不要抢**，在 inbox 留问或等其失活
   - **判活不能只看看板** —— 三个信号交叉验证，任一有动静就按"有活跃会话"处理
     （把下面的 `<构建进程>` 换成本项目实际会跑的长任务，如 `ninja` / `node` / `pytest` / `cargo`）：
     ```bash
     # Windows
     tasklist | findstr /I "<构建进程>"
     # macOS / Linux
     ps aux | grep -i <构建进程> | grep -v grep
     # 两种系统通用：
     git reflog -8                                # 有没有刚冒出来的 commit
     # 再用文件管理器/ls 看你要改的文件 mtime 是不是最近几分钟
     ```

   > **为什么判活必须交叉验证（真实事故）**：
   > 会话 A 一开工就在改三个源文件，但**一直没注册会话文件**，
   > 于是看板显示「活跃 0」。会话 B 做完协调检查、切好分支，`git status` 时才发现
   > 工作区多出两个自己没碰过的改动文件 —— 一查 mtime 全在**最近一分钟内**，
   > `git reflog` 里还多了一个刚产生的 commit。**差一点就撞上。**
   >
   > 三条可复用的结论：
   > 1. **看板显示「无活跃会话」不等于没人在干活** —— 不注册就完全隐形（手册 A1）。
   > 2. **判活看进程和文件，不要看日志文件**：后台任务的 stdout 可能已经丢了
   >    （进程活着却不再写日志，看着像"卡死"），此时进程列表 + 增量产物的
   >    mtime/大小才是真信号。曾有人因此误判"任务已被回收"，白重启三轮长构建，全撞在文件锁上。
   > 3. **发现自己隐形了，第一件事是补注册会话**，别指望别人来猜。
   >    （同理：看到看板上没有自己，也要主动补，别等别人踩上来。）

   > **为什么必须扫开着的 PR（真实事故）**：开着的 PR 也是一种「活跃会话」。
   > 一个 bug 很早就被修对并提了 PR，**但一直没人合**，于是修复从未进入 `develop`；
   > 后来所有分支都从 `develop` 长出来，同一个 bug 被**又修了一遍**，两份修复合并时必然冲突。
   > 看板（`.agents/`）只能看见「正在本机干活」的会话，**看不见「已经干完但没合」的工作**——
   > 后者恰恰是最容易撞车的。
   >
   > 发现 PR 长期没合且已冲突时，不要自己闷头重做，先在 PR 里留言问，
   > 或在 `.agents/inbox/` 发一条——重复劳动 + 制造冲突比等一会儿贵得多。

2. **切到 develop 并拉最新**：`git switch develop && git pull --ff-only`
3. **创建分支**：`git switch -c feature/<任务名>`（任务名用小写英文+连字符）
   - 在会静默拦截 `.git/refs` 写入的 AI 沙箱里，带斜杠的分支可能"报成功却没建出来"：
     建完用 `git rev-parse --verify feature/<任务名>` 复核；失败的处置见手册 B13/B14
4. **注册会话（纯本地动作，不提交不推送）**：复制 `.agents/sessions/_template.md`
   创建自己的 session 文件（填好的范例见 kit 仓库 `docs/examples/session-example.md`），
   保存即可——其他 agent 通过本机文件系统立刻能看到
5. **开发并 commit**：只 add 本任务的**项目文件**；每完成一个阶段更新 session 心跳（保存文件即可）；
   **每次 commit 后跑 `git rev-parse --short HEAD` 复核**（沙箱可能假成功，手册 B1/B2/B8）
6. **运行测试（提交前门禁，命令见第 6 节「项目定制区」）**
   - 严格跑定制区里写的门禁命令；不相关的测试也必须保持绿
   - 测试失败先修代码，不许改测试迁就
   - 只跑静态检查/预览不算验证：能编译的要真编译，能跑的要真跑（手册 A3/A5）
7. **推送项目分支 + 开 PR 到 develop**：`gh pr create --base develop ...`
   （记忆文件不随 git 走，无需为更新心跳而推送；推送失败按手册 C1/C5/C8/C9 处置）
8. **收尾（本地）**：session 置 `done`、worklog 追加一段总结
9. **每次操作后向用户汇报四件套**：当前分支 / commit message / 变更文件 / 测试结果

---

## 4. 本机共享记忆协议

共享记忆放在工作区的 **`.agents/`（复数；本地目录，不入库）**。因为所有 agent 都在同一个
工作区里干活，未跟踪文件在切换分支时保持不动，所以**任何分支下都能看到同一份记忆**。

| 路径 | 作用 | 写入规则 |
|---|---|---|
| `.agents/sessions/<会话ID>.md` | 谁在哪个分支做什么、心跳 | 每个会话只改自己的文件 |
| `.agents/worklog/<YYYY-MM-DD>.md` | 当日已完成事项的时间线 | 只追加（append-only），末尾追加 |
| `.agents/inbox/Q-<日期>-<序号>.md` | 给其他 agent 的问题/提示/交接 | 一个问题一个文件，回复写进同一文件 |

**会话 ID**：`<agent>-<YYMMDD-HHMM>-<4位随机>`，如 `trae-260918-1115-a3f9`、
`buddy-260918-1042-7f3c`、`claude-260918-1420-91bd`。

**心跳与失活**：

- 活跃期间每 ≤ 15 分钟、或每个阶段完成时更新一次 `heartbeat`（保存文件即生效）
- **跑预计超过 15 分钟的长任务前**（全量构建、全量回归、大批量迁移）：先更新心跳，
  并在 session 的 task/当前进度里写明"正在跑 XX，预计 HH:MM 回来"——
  否则任务期间心跳必然过期，别人可能误判你失活而接管分支；
  看到某会话有这种说明时，即使心跳超 15 分钟也不要立即接管，先看预计时间和进程
- 心跳超过 **15 分钟** 视为失活（stale）。要接管失活会话的分支：
  先在 inbox 发一条接管声明（等 5 分钟无反对）→ 从其分支切新分支继续，
  **保留并注明其全部工作**，不得删除其提交

**并发写冲突**：多个 agent 同时写同一个记忆文件时取**并集**——保留所有会话条目、
所有日志段落、所有问答，只能多不能少。分片设计（一会话一文件、一问题一文件、一天一文件）
已经把撞车概率降到最低。

**故障沉淀（强制）**：任何环境/工具/git 报错被解决后，往
[.agents/TROUBLESHOOTING.md](.agents/TROUBLESHOOTING.md) 追加一条（现象/判断/真因/修复），
下次开工别的 agent 先查它就能直接修，不必重新踩坑。

**提问礼仪**：

- 标题写明对象与主题；`to:` 写具体会话 ID 或 `any`
- 一个文件只问一件事；给出背景、你已尝试什么、需要对方做什么
- 被问方把答案写进同一文件的 `## 回复`，并把 `status` 改为 `answered`
- 离开前扫一眼 inbox：能答的顺手答，答不了写明你预计何时回来

**交接（handoff）**：任务没做完要离开时，在 session 文件写清
`下一步 / 阻塞点 / 相关 PR / 未提交文件位置`，在 inbox 给接手方留一条，
**并在 worklog 追加一条阶段总结**——交接也是可汇报阶段，否则中途交接的任务在日志里
没有任何痕迹，接手者只能翻 inbox 拼图。

**备份**：`.agents/` 不进 git，防误删靠你自己——重要节点可把整个目录拷到仓库外
（如 `../_salvage/`）或打包带走。

### 4.1 Worktree 规则（盲区事故后新增）

`git worktree` 共享同一个 `.git`，但**每个 worktree 有独立工作区**；未跟踪的 `.agents/`
不会自动出现在新 worktree 里，于是"两个 agent 在两个目录里各干各的、看板互相看不见"。

- **默认在主工作区工作**，不要为了图省事开 worktree。
- 确需开 worktree（例如主工作区被别的分支长时间占用）：
  1. 开工先 `git worktree list` + 看板，确认没有重名/冲突
  2. 先确保分支引用真实存在（`git rev-parse --verify feature/<任务>`，沙箱坑见手册 B14），
     再 `git worktree add <目录> feature/<任务>` —— **不要带 `-b`**（它会重建引用）
  3. **在新 worktree 目录里跑一次安装器**：`python <kit>/install.py install <worktree目录>`
     —— 协作文件都是本机私有的，不会随 checkout 出现；安装器会补齐 AGENTS.md/看板、
     在该 worktree 的 git 目录写好忽略规则，并自动沿用主工作区的项目名。
     也可以手工把整个 `.agents/` 拷过去（至少要让该目录有自己的 `.agents/sessions/`）
  4. **会话文件必须注册在该 worktree 自己的 `.agents/sessions/` 里**——看板会自动聚合
     全部 linked worktree 的 `.agents/`，写对位置别人才能看见你
  5. 收工 `git worktree remove <目录>`；长期不用又不删，看板会把它列在"未注册 worktree"
- 看板有 **`[未注册 worktree 告警]`**：linked worktree 30 分钟内有文件改动、
  却没有任何会话注册时，红色告警并给出路径/分支。看到它要么补注册，要么停手。
- 不同 worktree 里的 agent **禁止操作对方签出的分支**，也不要在任一 worktree 里跑
  `git pack-refs` 之外的引用维护（见第 5 节的坑）。

---

## 5. 事故应急规则

> 📖 **遇到任何已知报错先查故障手册：[.agents/TROUBLESHOOTING.md](.agents/TROUBLESHOOTING.md)**
>
> 最高频的几条：
> - **引用类**：B1/B2 引用"消失"或 commit 假成功 · B8/B14 ref 写不进去 ·
>   B10 假 ahead/behind · B13 unborn 幻影分支 · B12 packed-refs 行尾
> - **网络类**：C1 schannel 502 · C5 代理拦截 · C6/C7 先探针再选路 ·
>   C8 先问代理 · B11 `gh api` 兜底 · C9 大小请求分层
> - **协作类**：A1 worktree/隐形会话盲区 · A2 并发覆盖 · A3 绿测试≠对 ·
>   A4 长任务被回合回收 · A5 假验证
>
> 技术栈专属的坑记在手册 D 组（写法范例见 kit 的 `docs/examples/`）。
> 解决了新问题必须按手册末尾规则追加一条，并回来把这里的高频清单补上。

出现以下任一**异常信号**，立即停止一切写操作（只读排查 + 通知用户）：

- `git status` 报 `not a git repository`，但目录里明明有 `.git`
- 出现**不是你创建**的分支 / unborn 分支 / 暂存区；全部文件显示为 staged 新增
- `git fetch` 后远程分支 hash 大量变化、你推过的分支消失
- `.git/refs`、`.git/objects` 的修改时间在**当前时刻附近跳动**（有别的 git 进程在跑）
- 工作区项目文件被大面积回退（文件大小、内容与你已知历史不符）
- `git commit` 报成功，但 `git log` 说当前分支 "does not have any commits yet"

应急动作：

1. **停手**：不 init、不 reset、不删 `.git`、不 force push、不再 `git checkout`
2. 用只读命令取证（`git for-each-ref`、`git reflog`、`git cat-file -t <sha>`、
   `git worktree list`、查看 `.git` 目录时间戳、列时间线）
3. 等 `.git` 静默 ≥ 2 分钟，或请用户确认另一个会话已停止
4. 在 `.agents/worklog/`、`.agents/inbox/` 记录事故时间线
5. 需要 `reset --hard` 等破坏性操作恢复对齐时，**必须先向用户说明风险并获明确授权**

### 5.1 引用"消失"的真伪鉴别

**先跑 `git for-each-ref`**：分支都列得出来 = 仓库健康，不是损坏。

已确认的真因（不是"有人删目录"）：

1. 任一会话跑 `git pack-refs`（**正常操作**）会把松散引用收进 `packed-refs` 并删除空的
   `.git/refs/heads/feature/` 目录；
2. 此后 git 要写松散引用时，**对 `.git/refs` 的写入会被 AI 工具沙箱静默拦截**：
   命令退出码 0、不报错，引用文件却不存在。于是 `switch -c` / `commit` / `update-ref`
   全部"成功"了但 ref 没更新（完整复现证据见手册 B3/B8）。
3. 表现：提交对象已入库（`git cat-file -t <sha>` 查得到）、reflog 也在，只是分支 ref
   没更新。**数据零丢失。**

恢复手法（非破坏性，先经用户同意）：

- 首选：在**非沙箱终端**跑 `git update-ref refs/heads/feature/<分支> <reflog里的SHA>`
- 次选：用**文件写入工具**（不是 shell mkdir——会被沙箱回滚）创建
  `.git/refs/heads/feature/<分支>`，文件内容就是 40 位 SHA + 换行
- 恢复后 `git pack-refs --all` 把引用打包进 packed-refs，可躲过松散引用写入问题
- 判断 worktree 归属：`git worktree list`，被 `+` 标记的分支签出在别的目录，
  该目录的活动共享同一个 `.git`——先看那里有没有 agent（看板的"未注册 worktree 告警"）

### 5.2 推送失败排查（代理环境）

现象：`git push` 报 `CONNECT tunnel failed, response 502` / `schannel: renegotiating`，
但同一代理下 `curl https://github.com` 是通的。真因通常是 **Git for Windows 默认 schannel
TLS 后端在代理隧道里重协商失败**，不是网络也不是认证。

```powershell
git -c http.sslBackend=openssl push -u origin <分支>      # 首选，实测稳定
# 大推送再补：-c http.postBuffer=524288000 -c http.version=HTTP/1.1 -c http.lowSpeedLimit=0
```

本地引用缺失但对象还在时，可以绕过引用直接推：
`git push origin <40位SHA>:refs/heads/<分支名>`。

如果这也不通，**先问用户机器上有没有本地代理**（手册 C8），再考虑 `gh api`
服务端造提交（B11，最后手段）。

---

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

## 7. 各平台入口与"自动生效"原理（均为本机文件）

**原理**：各平台在新对话开始、用户发第一句话之前，会自动把约定位置的规则注入 AI 上下文，
所以无需用户每次口头强调。注意：**新建对话/新会话才会加载**，改完规则后旧对话不自动刷新。

- **Trae IDE**：`.trae/rules/git-flow-agent.md`（`alwaysApply: true`，每个新对话自动生效），
  它要求 AI 先完整读本文件再开工。备选：设置 → Rules 里打开
  "Include AGENTS.md in the context" 开关。
- **Claude Code / TraeCode CLI**：根目录 `AGENTS.md`、`CLAUDE.md` 被无条件自动加载
  （`CLAUDE.md` 用 `@AGENTS.md` 引入本文件）。
- **斜杠命令 `/git-flow <任务描述>`**：用户主动调用；文件在 `.claude/commands/git-flow.md`。
- **WorkBuddy**：`.workbuddy-ai/git-flow-agent.md` 是本文件的镜像；
  不一致时以本文件为准，改完运行 `python install.py mirror <项目路径>` 重新生成。
- **验证规则是否被读到**：开一个**新对话**，直接问"你开工前要先做什么？"，
  应答出"fetch + 跑 agents_status.py 看板 + 看 inbox"。

## 8. 本机私有清单与换机迁移

以下路径**不入库**（忽略规则由安装器写在 `.git/info/exclude` 标记块里，纯本机配置，
不会随仓库走）：

```
AGENTS.md  CLAUDE.md  .agents/  .agent/  .claude/  .trae/
.workbuddy-ai/  .workbuddy/  tools/agents_status.py
```

> 这份清单的唯一事实源是安装器（`install.py` 的 `EXCLUDE_ENTRIES`）；
> 安装器写的 `.git/info/exclude` 托管块永远与它一致，别手工改其中一份。

- 重新 clone / 换电脑后：在新工作区跑一次安装器即可恢复整套体系
  （`python install.py install <项目路径>`；项目门禁、项目速览、手册 D 组条目三个定制区
  会从旧文件自动迁移，详见 kit 的 README）
- 升级覆盖旧文件前，安装器会把旧版**字节备份到 `.agents/backups/`**（该目录本身被忽略，
  不会出现在 `git status`）；同秒多次覆盖也不会互相冲掉
- `.workbuddy-ai/memory/`（平台自身日记）与本体系相互独立，别动它
- 若 `git status` 里突然出现这些路径，说明本地忽略失效：跑 `python install.py doctor <项目路径>`
  体检排查，不要提交它们
