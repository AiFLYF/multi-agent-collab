# multi-agent-collab

[![python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![deps](https://img.shields.io/badge/dependencies-none-success)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![changelog](https://img.shields.io/badge/changelog-1.4.0-orange)](CHANGELOG.md)

让**同一台电脑上的多个 AI 编码会话**（Claude Code / Codex / OpenCode / Pi / Trae / WorkBuddy，
也可以是真人）在**同一个 git 项目**里并行干活——互不覆盖、不撞车、走了能交接。

- **零基础设施**：没有服务端、数据库、消息队列，协作全部落在 Markdown 文件上
- **零依赖**：Python 标准库即可，一条命令装进任意 git 项目
- **语言/框架/硬件无关**：Web、后端、脚本、嵌入式都适用

```
Claude Code / Codex / OpenCode / Pi / Trae / WorkBuddy ──▶  <你的项目>/.agents/
                                    ├── sessions/  谁在哪个分支干什么（心跳）
                                    ├── worklog/   今天做完了什么
                                    └── inbox/     要问谁、谁来接手
        未跟踪、切分支不丢、任何分支下都看得见同一份
```

---

## 快速开始

需要 Python 3.8+（只用标准库）。目标是 git 项目；还没 `git init` 也能装，初始化后重跑一次即可。

```bash
# 在本 kit 目录，装进任意项目
python install.py install /path/to/your-project

# 或先 cd 到目标项目
cd /path/to/your-project
python /path/to/multi-agent-collab/install.py install .
```

Windows PowerShell 路径写法相同，例如 `python install.py install D:\code\my-app`。
先看会发生什么、不落盘：加 `--dry-run`。

**装完 3 分钟收尾：**

1. 打开 `AGENTS.md` 第 6 节和 `CLAUDE.md`「项目速览」，把示例换成你的测试/构建命令
   （这两段是「项目定制区」，以后升级会自动保留）
2. 跑一次 `python tools/agents_status.py` 看看板
3. **开一个新对话**，问 AI「你开工前要先做什么？」——应答出 *fetch + 看板 + 看 inbox + 扫 open PR*

不想跑脚本？文末「[手动安装](#手动安装)」有等价的拷贝命令。

---

## 每个 agent 的日常循环

```
fetch + 扫 open PR  →  看看板和 inbox  →  注册会话文件  →  切 feature 分支
→  开发（≤15 分钟更一次心跳）→  跑测试门禁  →  提 PR  →  置 done、写 worklog
```

日常开工最省 token 的看法是一行摘要（无告警就直接开工，有告警才展开）：

```text
$ python tools/agents_status.py --brief
OK 看板无告警：活跃2，无失活/未答/worktree 异常，可直接开工
```

需要完整信息再跑无参数版；给人看用 `--web`（图形化、可下指令）或 `--html`（单文件快照）。
完整终端看板长这样：

```text
【会话】共 2 个，活跃 2，失活 0
  ● trae-261009-1015-a1b2
      分支 feature/order-api     心跳 2 分钟前
      任务 订单接口
  ● claude-261009-1030-c3d4      [wt] D:/code/app-wt
      分支 feature/coupon        心跳 5 分钟前
      任务 优惠券
【inbox】未答 1 / 共 3
  ? Q-2026-10-09-01.md   to=any  from=trae-…  (2026-10-09 10:18)
【worktree】2 个
  [当前/已注册] feature/order-api @ a1b2c3d
  [linked/已注册] feature/coupon @ a1b2c3d
```

会话文件就是一个 8 行的 Markdown 头，完整的填法范例见
[docs/examples/session-example.md](docs/examples/session-example.md)；
两个会话协商"同文件不同函数、零重叠并行"的真实问答见
[docs/examples/inbox-example.md](docs/examples/inbox-example.md)。

---

## 为什么这样设计

四个决定，每一个都是事故换来的：

1. **分片写，不用大文件**：一会话一文件、一天一日志、一问题一问事板。
   各自写各自的文件，天然无冲突；万一撞上，合并时取并集，只多不少。
2. **心跳，不用锁**：锁在 agent 崩溃时会永久卡死。15 分钟没心跳 = 失活，
   接管前先在 inbox 声明、等 5 分钟无反对，并且**保留原会话的全部提交**。
3. **判活必须多信号交叉验证**：看板显示 0 活跃 ≠ 没人干活——忘了注册会话文件的人是隐形的。
   真实事故里，B 会话切完分支才在 `git status` 发现两个一分钟前刚改的文件，差点撞上。
   所以看板之外还要看 `git reflog`、文件 mtime、构建进程。
4. **本机私有，不进 git**：协作文件走 `.git/info/exclude`，切分支不丢、不会误提交；
   代价是没有远程备份——重要节点把 `.agents/` 拷到仓库外即可。

配套看板 `tools/agents_status.py` 的行为也为这些教训兜底：

- 自动聚合主工作区和**所有 linked worktree** 的 `.agents/`；在项目任意子目录运行都能定位到仓库根
- 未注册的 worktree 30 分钟内有真实改动会红色告警（用 `git status` 判定，
  切分支刷新 mtime 不会误报）
- 损坏/乱码/未来时间的心跳不会让看板崩，按失活处理并给可读提示
- `--prs` 可附带只读列出 open PR；`--json` 供脚本使用；退出码 0/1 可当开工门禁
- **给人看的两种模式（不影响 agent 用 JSON/终端）**：`--web` 起一个只监听
  127.0.0.1 的本地网页，浏览器自动打开、5 秒实时刷新；`--html` 导出零外链单文件快照，
  可直接发给别人或离线打开。两种模式与 agent 共用同一份数据口径，只读不写会话文件

---

## 命令速查

| 命令 | 作用 |
|---|---|
| `python install.py install <项目>` | 安装 / 升级（幂等，定制区自动迁移） |
| `python install.py doctor <项目>` | 体检：文件、忽略规则、版本新旧、看板冒烟 |
| `python install.py mirror <项目>` | 改完 AGENTS.md 后刷新 WorkBuddy 镜像 |
| `python install.py uninstall <项目>` | 只移除忽略托管块，文件全部保留 |
| `python install.py uninstall <项目> --purge-files` | 再删**未被你改过**的套件文件；会话/日志/问事板/D 组条目/备份永不删 |
| `python tools/agents_status.py` | 看板（`--root` / `--json` / `--prs` / `--stale-min N`） |
| `python tools/agents_status.py --brief` | **开工协调首选**：一行摘要，没事就 `OK`、有事才点名（约 80 token，rc=0 开工/rc=1 查详情） |
| `python tools/agents_status.py --version` / `--selfcheck` | 查看版本 / 内置 48 项自测；全部参数看 `--help` |
| `python tools/agents_status.py --web` | **图形化看板**：浏览器自动打开，5 秒实时刷新，默认仅 127.0.0.1；网页上可直接**对 agent 下指令** |
| `python tools/agents_status.py --web --bind 0.0.0.0` | 局域网可访问（手机/其他电脑）：必须配 `--no-directives` 只读，或先设密码（见下） |
| `python tools/agents_status.py --html [路径]` | 自包含 HTML 单文件**快照**（默认 `.agents/board.html`，离线可发可开，加 `--open` 直接打开） |

**网页指挥 agent / 局域网访问安全档**：

```powershell
# 仅本机（默认）：可直接在网页发指令
python tools/agents_status.py --web

# 局域网只读大屏：能看不能动
python tools/agents_status.py --web --bind 0.0.0.0 --no-directives

# 局域网可指挥：先设密码（浏览器弹登录框，用户名任意）
$env:MULTI_AGENT_BOARD_PASSWORD = "你的密码"
python tools/agents_status.py --web --bind 0.0.0.0
```

"对外可写又无密码"会被拒绝启动。HTTP 是明文，仅限可信 WiFi/VPN；外网安全访问用 SSH 端口转发
（`ssh -L 8731:127.0.0.1:8731 用户@电脑`，不开 0.0.0.0）。一键只读开关：环境变量
`MULTI_AGENT_BOARD_READONLY=1`。网页发的指令就是 `.agents/inbox/` 里 `from: human` 的标准
问事板文件，agent 按 AGENTS.md 规程在阶段间隙读取并把回复写回同一文件，页面实时显示并可
弹桌面通知；agent 空闲时网页提供「复制提醒」一句话，粘进对话框即可唤醒。

安装器的关键保证：

- **幂等**：内容相同跳过；不同先字节备份到 `.agents/backups/`（被忽略、不污染 `git status`），
  旧文件即便是 GBK 编码也不丢内容
- **三个定制区升级保留**：项目门禁、CLAUDE 项目速览、故障手册 D 组条目
  （连没有标记的旧版手册都能自动识别搬迁）
- **linked worktree 支持**：正确识别 `.git` 文件形态，私有/公共 exclude 两处都写，
  项目名自动沿用主工作区
- **忽略清单单一事实源**：以 `install.py` 的 `EXCLUDE_ENTRIES` 为准，文档有一致性自测兜底

---

## 装完之后，项目里多了什么

```text
<你的项目>/
├── AGENTS.md                     # 协作总章（本机私有，自动忽略）
├── CLAUDE.md                     # Claude Code / TraeCode CLI 入口
├── tools/agents_status.py        # 看板
├── .agents/
│   ├── README.md
│   ├── TROUBLESHOOTING.md        # 29 条通用故障经验 + 留给你的 D 组
│   ├── install.json              # 项目名与套件版本
│   ├── sessions/ worklog/ inbox/ # 只有 _template.md，真实数据由你们产生
│   └── backups/                  # 升级时的旧版备份
├── .trae/rules/git-flow-agent.md       # 可选（--platforms 控制）
├── .claude/commands/git-flow.md        # 可选，斜杠命令 /git-flow
└── .workbuddy-ai/git-flow-agent.md     # 可选，AGENTS.md 的镜像
```

各平台规则在**新对话开始前**自动注入上下文（改完规则旧对话不刷新，需新建对话）：

| 平台 | 生效方式 |
|---|---|
| **Claude Code / TraeCode CLI** | 根目录 `AGENTS.md` 自动加载，`CLAUDE.md` 用 `@AGENTS.md` 引入 |
| **OpenAI Codex CLI** | 根目录 `AGENTS.md` 原生自动加载（另有 `~/.codex/AGENTS.md` 全局位）——**零配置** |
| **OpenCode（sst/opencode）** | 根目录 `AGENTS.md` 就是它的项目规则（官方约定，类似 Cursor rules）——**零配置** |
| **Pi（pi.dev）** | 从工作目录及父目录自动发现根 `AGENTS.md`（context file，无需 trust）——**零配置** |
| Trae IDE | `.trae/rules/git-flow-agent.md`（`alwaysApply: true`），或设置里打开 "Include AGENTS.md" |
| WorkBuddy | `.workbuddy-ai/git-flow-agent.md` 镜像（改完总章跑 `install.py mirror` 刷新） |
| 斜杠命令 | `.claude/commands/git-flow.md` 提供 `/git-flow <任务>` |

> Codex / OpenCode / Pi 与 Claude Code 同属「AGENTS.md 原生派」：安装器只放根 `AGENTS.md`，
> 不往 `.codex/`、`.opencode/`、`.pi/` 写任何东西；`install.py doctor` 会自动识别并提示。

---

## 故障手册

`.agents/TROUBLESHOOTING.md` 收录 **29 条从真实事故里长出来的通用经验**：

- **A 组 · 协作流程（6 条）**：隐形会话/看板盲区、并发覆盖、假绿测试、长任务被回合回收、假验证
- **B 组 · Git 与引用（14 条）**：引用"消失"、沙箱静默拦截写 ref、假 ahead/behind、
  `gh api` 推送兜底、CRLF 写坏 packed-refs……
- **C 组 · 网络/沙箱/终端（9 条）**：schannel 502、代理拦截、环境变量漂移先探针、
  `gh` 因 APPDATA 报未登录、PowerShell 兼容……
- **D 组 · 本项目专属（空区）**：你自己的框架/硬件/语言的坑，从 D1 开始记，升级自动保留

不会写项目专属条目？[docs/examples/project-specific-troubleshooting.md](docs/examples/project-specific-troubleshooting.md)
给了 4 条嵌入式项目精华作为写法示范（热点密码导致设备重启、构建被杀零报错、
摄像头参数写坏管线、设备连不上先查配网），每条都抽出了跨技术栈的教训——
范例只留在 kit，不会装进你的项目。

---

## 适用边界

**适合**：同一台电脑、同一个 git 工作区、多个 AI 会话（含真人）；想要零基础设施。

**不适合 / 先想清楚的取舍**：

- 跨机器协作（需要真正的远程同步）
- agent 之间需要实时通信（这里最快是"下一次工具调用时读到"）
- 无法保证 agent 会读 `AGENTS.md` —— 那就退回"一次只让一个 agent 动同一文件"
- **想把 AGENTS.md 提交进仓库全员共享**：本套件的取舍是"规则本机私有、各自定制"。
  要团队共享，可把共享部分放进仓库文档让本机 AGENTS.md 引用；或安装后手工删 exclude
  托管块里对应两行（此时分片保护不再覆盖这两个文件）

---

<details>
<summary><b>手动安装（不想跑脚本时，点击展开）</b></summary>

```bash
SRC=/path/to/multi-agent-collab
DST=/path/to/your-project

cp "$SRC/AGENTS.md" "$SRC/CLAUDE.md" "$DST/"
mkdir -p "$DST/tools" "$DST/.agents/sessions" "$DST/.agents/worklog" "$DST/.agents/inbox"
cp "$SRC/tools/agents_status.py"  "$DST/tools/"
cp "$SRC/docs/README.md"          "$DST/.agents/README.md"
cp "$SRC/docs/TROUBLESHOOTING.md" "$DST/.agents/"
cp "$SRC/templates/session.md"    "$DST/.agents/sessions/_template.md"
cp "$SRC/templates/inbox.md"      "$DST/.agents/inbox/_template.md"
cp "$SRC/templates/worklog.md"    "$DST/.agents/worklog/_template.md"

# 平台入口按需（Claude Code / Codex / OpenCode / Pi 直接读根 AGENTS.md，无需拷贝）：
# cp "$SRC/platforms/trae-rule.md"       "$DST/.trae/rules/git-flow-agent.md"
# cp "$SRC/platforms/claude-command.md"  "$DST/.claude/commands/git-flow.md"

# 关键：写进 .git/info/exclude（清单与 install.py 的 EXCLUDE_ENTRIES 保持一致）
cat >> "$DST/.git/info/exclude" <<'EOF'
/AGENTS.md
/CLAUDE.md
/.agents/
/.agent/
/.claude/
/.trae/
/.workbuddy-ai/
/.workbuddy/
/tools/agents_status.py
EOF
```

WorkBuddy 镜像：`platforms/workbuddy-header.md` 内容在前，后面拼上目标项目的
`AGENTS.md` 全文（或直接 `python install.py mirror <项目>`）。

</details>

---

## 仓库结构与自测

```text
multi-agent-collab/
├── install.py                  # install / doctor / mirror / uninstall
├── AGENTS.md  CLAUDE.md        # 规则模板（含项目定制区标记）
├── tools/agents_status.py      # 看板（零依赖）
├── templates/                  # session / inbox / worklog 三个模板
├── platforms/                  # trae / claude / workbuddy 三个平台入口
├── docs/
│   ├── README.md  TROUBLESHOOTING.md
│   └── examples/               # 会话/问事板填法 + 4 条 D 组写法范例
├── tests/selftest.py           # 34 个测试用例（15 单元 + 19 安装器 E2E）
└── .github/workflows/selftest.yml  # 3 系统 × 4 Python 版本 CI
```

```bash
python tools/agents_status.py --selfcheck   # 看板内置 48 项断言
python tests/selftest.py                    # 临时真实 git 仓库端到端演练（含 linked worktree）
```

> README 里的用例计数由 `test_readme_e2e_count_matches_suite` 自动核对（看板内置 48 项断言另有独立自测），改了用例数量必须同步改这里。

自测覆盖：忽略规则逐条生效、幂等/备份、三个定制区迁移、GBK 旧文件、子目录探测、
坏心跳与未来时钟语义、worktree 跨区可见且不误报、卸载安全（用户数据不丢）、
忽略清单文档一致性等。Windows 上会自动处理 git 只读对象；`KEEP_TMP=1` 可保留现场。

**贡献提示**：kit 根目录的 `AGENTS.md` / `CLAUDE.md` / `platforms/` 是**要发布的产品模板**
（正常入库），改它们等于改产品；测试一律通过 `tests/selftest.py` 在系统临时目录进行，
**不要**在 kit 目录里自安装（安装器会拒绝）。变更请同步更新
[CHANGELOG.md](CHANGELOG.md) 与 `install.py` 的 `KIT_VERSION`。

---

## 起源

规则全部来自真实产品 [ego-link-esp32](https://github.com/AiFLYF/ego-link-esp32)
（ESP32-S3 教学硬件 + Python 课堂服务器）在 15+ 个 AI 协作会话里的事故与复盘。
抽离为通用套件时，规则正文与手册已完全去技术栈化；原项目的完整技术栈手册仍留在它自己的仓库。

## License

[MIT](LICENSE)
