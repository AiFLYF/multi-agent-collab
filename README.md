# multi-agent-collab

[![python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![deps](https://img.shields.io/badge/dependencies-none-success)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![changelog](https://img.shields.io/badge/changelog-1.4.1-orange)](CHANGELOG.md)

> 装进任意 git 项目，让 Claude Code、Codex、OpenCode、Pi、Trae、WorkBuddy 等多个平台的
> AI 助手在同一份规则下协同干活：不互相覆盖、不重复劳动；你通过一块网页看板实时查看
> 每个 AI 的状态，直接下指令。

![网页看板：实时看到每个 AI 的状态，直接给 AI 下指令](docs/assets/board-screenshot.png)

## 这是什么

multi-agent-collab 给"一个项目里多个 AI 助手（外加你）"的协作立一套交规。安装后，项目里多出三样东西：

- **一份规则总章**（`AGENTS.md` / `CLAUDE.md`）：每个 AI 开工前必须读——先协调再干活、分支怎么切、测试怎么算过、人类指令优先级最高
- **一块本机共享黑板**（`.agents/`）：谁在哪个分支干什么、心跳还活不活、有问题问谁、前任走了怎么接手——全部是几个 Markdown 文件，不进 git，切分支不丢
- **一个看板脚本**（`tools/agents_status.py`）：零第三方依赖，AI 用它做一行摘要的开工协调，你用它开网页指挥台

没有服务器、没有数据库、没有账号。Windows / macOS / Linux 都能跑，Python 3.8+。

它不启动 AI，也不给每个 AI 隔离工作区——那是 Claude Squad、Conductor、Vibe Kanban
这类编排工具负责的部分；multi-agent-collab 管的是另一半：让已经在干活的多个 AI
互相可见、守同一套规矩、听你指挥。两类工具可以叠加使用。

```
  Claude Code ─┐
  Codex       ─┤
  OpenCode    ─┤         <你的项目>/.agents/
  Pi          ─┼── 读同一份规则 ──▶  ├── sessions/  谁在哪个分支干什么（心跳）
  Trae IDE    ─┤                     ├── worklog/   今天做完了什么
  WorkBuddy   ─┘                     └── inbox/     要问谁、谁来接手、你下的指令

  这些文件不进 git；任何分支、任何平台、任何 AI 看到的都是同一份

  你 ──▶ python tools/agents_status.py --web ──▶ 网页看板：实时看，直接指挥
```

## 解决什么问题

同一个项目里同时开着多个 AI——不同平台、不同窗口、甚至不同会话——常见的几种情况：

- 两个 AI 改了同一个文件，后保存的把先保存的覆盖掉，白干一半
- 两个 AI 各自去修同一个 bug，改动最后冲突
- 一个 AI 干到一半窗口被关掉，没人知道它改到哪、下一步是什么
- 每个平台的规则文件各写各的，换个 AI 工具就等于没有规则
- 你想中途喊一句"先别碰那个文件"，没有任何途径告诉它们

multi-agent-collab 的做法：一份规则管所有平台，一块黑板让所有会话互相可见，一个看板让你始终有指挥权。这些规则不是凭空设计的——每一条都来自真实项目里 15+ 个 AI 协作会话出过的事故。

## 30 秒上手

前提：电脑装了 Python 3.8+ 和 git，命令行能运行 `python --version` 和 `git --version`；项目需要是 git 仓库（还不是的话先 `git init`）。

### 方式一：复制一段话给 AI

不想敲命令的人用这种：把下面整段复制给你项目里的任意一个 AI（套件还没下载时 AI 会自己 clone；如果用 fork，把第二行的地址换成你的 fork）：

```text
帮我把 multi-agent-collab 套件装到当前项目。
套件仓库地址：https://github.com/AiFLYF/multi-agent-collab
1. 先确认 python 和 git 都能运行；不能运行就停下告诉我，不要自行安装任何软件。
2. 电脑里还没有这个仓库就先 git clone 上面的地址；已经有了就跳过。
3. 确认当前目录是我的项目根目录，执行 python <套件目录>/install.py install .；
   成功后再执行 python <套件目录>/install.py doctor .，把体检结果原样发给我。
除安装器生成的文件外不要动项目里的其他文件，任何报错先停下来问我。
```

装完后 AI 会把体检结果发给你，然后做下面「装完之后」的三件事。

### 方式二：自己跑一条命令

已经下载过本仓库的话，在仓库目录下执行：

```bash
python install.py install /path/to/your-project
```

Windows PowerShell 写法一样，例如：

```powershell
python install.py install D:\code\my-app
```

想先看会改动哪些文件、不落盘：把 `install` 换成 `install --dry-run`。

### 装完之后（3 分钟）

1. 打开项目里的 `AGENTS.md` 第 6 节和 `CLAUDE.md`「项目速览」，把示例换成你项目真实的测试和构建命令（这两段是项目定制区，以后升级套件会自动保留）
2. 在项目目录跑一次 `python tools/agents_status.py --web`，确认看板正常
3. 开一个新的 AI 对话（旧对话不会自动加载新规则），问它"你开工前要先做什么"——它应该回答：先看看板、查 inbox、再干活。答得出来，说明规则已经生效

不想跑脚本、想手工拷贝的，见页面底部的「手动安装」。

---

## 网页看板：你的指挥台

AI 看的是文件，你看的是网页。在项目目录运行：

```powershell
python tools/agents_status.py --web
```

浏览器会打开一块每 5 秒自动刷新的看板。你能看到：

- 每个 AI 一张卡片：哪个平台、在哪个分支、做什么任务、几分钟前还有心跳（绿色正常，红色失联）
- 未答复的问题和工作交接
- 所有 git worktree 的状态：有 AI 在没登记过的目录里动了手脚，会红色告警
- 直接对 AI 下指令：在卡片上输入"先停一下，别碰登录模块"，AI 在下个阶段间隙读到并执行，回复实时出现在网页上（可开桌面通知）。人类指令优先级最高，胜过 AI 手头的任何任务

不想开服务、只想把当前状态存成文件发给别人，用快照：

```powershell
python tools/agents_status.py --html          # 生成单文件 .agents/board.html，无外链
python tools/agents_status.py --html --open   # 生成并直接打开
```

网页服务默认只允许本机访问。想在手机或同局域网其他电脑上看，见「命令速查」下方的三档安全设置。

AI 自己日常开工用的是一行摘要（输出短、省 token）：

```text
$ python tools/agents_status.py --brief
OK 看板无告警：活跃3，无失活/未答/worktree 异常，可直接开工
```

完整的终端看板长这样（三个不同平台的会话在同一个项目里干活）：

```text
【会话】共 3 个，活跃 3，失活 0
  ● trae-261009-1015-a1b2
      分支 feature/order-api     心跳 2 分钟前
      任务 订单接口
  ● claude-261009-1030-c3d4      [wt] D:/code/app-wt
      分支 feature/coupon        心跳 5 分钟前
      任务 优惠券
  ● codex-261009-1100-e5f6
      分支 fix/login-timeout     心跳 1 分钟前
      任务 登录超时
【inbox】未答 1 / 共 3
  ? Q-2026-10-09-01.md   to=any  from=trae-…  (2026-10-09 10:18)
【worktree】2 个
  [当前/已注册] feature/order-api @ a1b2c3d
  [linked/linked] feature/coupon @ a1b2c3d
```

会话文件就是一个 8 行的 Markdown 头，填法范例见
[docs/examples/session-example.md](docs/examples/session-example.md)；
两个会话协商"同文件不同函数、零重叠并行"的真实问答见
[docs/examples/inbox-example.md](docs/examples/inbox-example.md)。

---

## 跨平台：各 AI 怎么接入

| 平台 | 怎么生效 |
|---|---|
| **Claude Code / TraeCode CLI** | 根目录 `AGENTS.md` 自动加载 |
| **Codex** | 原生自动加载 `AGENTS.md`（另有 `~/.codex/AGENTS.md` 全局位） |
| **OpenCode（sst/opencode）** | `AGENTS.md` 就是它的项目规则（官方约定） |
| **Pi（pi.dev）** | 自动从项目目录发现 `AGENTS.md` |
| **Trae IDE** | 安装器写入 `.trae/rules/git-flow-agent.md`（新对话自动生效） |
| **WorkBuddy** | 安装器生成 `.workbuddy-ai/git-flow-agent.md` 镜像（改总章后跑 `install.py mirror` 刷新） |

前四个平台零配置，装完开个新对话即可。规则都在新对话开始时加载，改完规则后旧对话不刷新，开新对话就行。

规则直接写在 AGENTS.md——面向 coding agent 的开放格式（[agents.md](https://agents.md)），
只写一遍，各平台各读各的入口；Trae 和 WorkBuddy 的入口文件由安装器生成，内容同样是这份规则。
同一个项目里可以混用多个平台，黑板是同一块。

---

## 工作方式：四条交规

1. **各写各的小文件，不共用大文件**：一个会话一个文件、一天一份日志、一个问题一张留言条，天然不撞车；万一同时写了，合并时取并集，只多不少。
2. **用心跳判断死活，不用锁**：锁在 AI 崩溃时会永远卡死。心跳超过 15 分钟算失联，别人想接手要先在留言板声明、等 5 分钟没人反对，而且必须保留前任的全部提交。
3. **看板上没人不等于真的没人**：忘了登记的 AI 是隐形的。所以除了看板，还要交叉看 git 记录、文件修改时间、有没有构建进程在跑。
4. **协作文件只留本机，不进 git**：写在 `.git/info/exclude` 里，切分支不丢、不会误提交、不污染团队仓库。代价是没有云端备份，重要节点把 `.agents/` 拷到仓库外即可。

---

## 命令速查

安装与维护（你跑）：

| 命令 | 作用 |
|---|---|
| `python install.py install <项目>` | 安装 / 升级（幂等，你改的定制内容自动保留） |
| `python install.py doctor <项目>` | 体检：文件、忽略规则、版本新旧、看板冒烟 |
| `python install.py mirror <项目>` | 改完 AGENTS.md 后刷新 WorkBuddy 镜像 |
| `python install.py uninstall <项目>` | 只移除忽略托管块，文件全部保留 |
| `python install.py uninstall <项目> --purge-files` | 再删没被你改过的套件文件；会话/日志/留言/备份永不删 |

看板（你和 AI 都用）：

| 命令 | 作用 |
|---|---|
| `python tools/agents_status.py` | 完整终端看板 |
| `python tools/agents_status.py --brief` | 一行摘要：没事只回一句 OK（AI 开工首选） |
| `python tools/agents_status.py --web` | 图形化看板（可对 AI 下指令） |
| `python tools/agents_status.py --html [路径]` | 导出单文件 HTML 快照 |
| `python tools/agents_status.py --json` | 结构化输出（给脚本用），退出码 0/1 同口径 |
| `python tools/agents_status.py --version` / `--selfcheck` / `--help` | 版本 / 48 项自检 / 全部参数 |

安装器的几个保证：幂等（内容相同跳过，不同先字节备份到 `.agents/backups/`，连 GBK 旧编码也不丢内容）；三个定制区升级保留；linked worktree 双写忽略规则。

<details>
<summary><b>局域网访问（手机或其他电脑也想看）</b></summary>

```powershell
# ① 默认：只听 127.0.0.1，手机和别的电脑访问不到，网页可直接发指令
python tools/agents_status.py --web

# ② 局域网只读大屏：能看不能动
python tools/agents_status.py --web --bind 0.0.0.0 --no-directives

# ③ 局域网可指挥：先设密码（浏览器弹登录框，用户名随便填）
$env:MULTI_AGENT_BOARD_PASSWORD = "你的密码"
python tools/agents_status.py --web --bind 0.0.0.0
```

- "对外可写又没有密码"的组合会被拒绝启动；连续输错密码有冷却限制
- HTTP 是明文传输，只适合家里/公司可信 WiFi 或 VPN；公共 WiFi、云服务器公网 IP 不要开 0.0.0.0
- 外网访问用 SSH 端口转发（`ssh -L 8731:127.0.0.1:8731 用户@电脑`）
- 一键只读开关：环境变量 `MULTI_AGENT_BOARD_READONLY=1`
- 网页发的指令就是 `.agents/inbox/` 里 `from: human` 的标准留言条；AI 空闲睡着时，每条指令旁有「复制提醒」按钮，把那句话粘进 AI 对话框即可唤醒

</details>

---

## 出问题了怎么办

每个项目安装后的 `.agents/TROUBLESHOOTING.md` 收录 29 条来自真实事故的处理经验：

- **A 组 · 协作流程**：隐形会话、并发覆盖、假绿测试、长任务被中断、假验证
- **B 组 · Git 与引用**：引用"消失"、沙箱拦截写 ref、假 ahead/behind、推送兜底……
- **C 组 · 网络/终端**：Windows schannel 502、代理拦截、PowerShell 兼容……
- **D 组 · 你的项目专属（空区）**：自己框架/硬件的坑从 D1 开始记，升级自动保留

不知道 D 组怎么写？[docs/examples/project-specific-troubleshooting.md](docs/examples/project-specific-troubleshooting.md)
有 4 条范例（范例只留在本仓库，不会装进你的项目）。

---

## 适用边界

适合：同一台电脑、同一个 git 工作区、多个 AI 会话（含真人），不想搭任何服务器。

不适合或需要想清楚的情况：

- 跨电脑协作（需要真正的远程同步，本工具刻意不做）
- AI 之间需要毫秒级实时通信（这里最快是"下一次工具调用时读到留言"）
- AI 完全不遵守 `AGENTS.md`（那就退回最朴素的规矩：同一时间只让一个 AI 动同一文件）
- 想把 AGENTS.md 提交进仓库全团队共享：本套件的取舍是"规则本机私有、各自定制"；需要共享时，可把共享部分写进仓库文档，让本机 AGENTS.md 引用它

---

## 手动安装

<details>
<summary><b>不想跑脚本？这是等价的手工拷贝命令</b></summary>

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
├── platforms/                  # trae / claude / workbuddy 平台入口
├── docs/
│   ├── README.md  TROUBLESHOOTING.md
│   └── examples/               # 会话/留言填法 + 4 条 D 组写法范例
├── tests/selftest.py           # 34 个测试用例（15 单元 + 19 安装器 E2E）
└── .github/workflows/selftest.yml  # 3 系统 × 4 Python 版本 CI
```

```bash
python tools/agents_status.py --selfcheck   # 看板内置 48 项断言
python tests/selftest.py                    # 临时真实 git 仓库端到端演练（含 linked worktree）
```

自测覆盖：忽略规则逐条生效、幂等/备份、三个定制区迁移、GBK 旧文件、子目录探测、
坏心跳与未来时钟语义、worktree 跨区可见且不误报、卸载安全（用户数据不丢）、
忽略清单文档一致性等。Windows 上会自动处理 git 只读对象；`KEEP_TMP=1` 可保留现场。

贡献提示：仓库根目录的 `AGENTS.md` / `CLAUDE.md` / `platforms/` 是要发布的产品模板
（正常入库），改它们等于改产品；测试一律通过 `tests/selftest.py` 在系统临时目录进行，
不要在 kit 目录里自安装（安装器会拒绝）。变更请同步更新
[CHANGELOG.md](CHANGELOG.md) 与 `install.py` 的 `KIT_VERSION`。

## 起源

规则全部来自真实产品 [ego-link-esp32](https://github.com/AiFLYF/ego-link-esp32)
（ESP32-S3 教学硬件 + Python 课堂服务器）在 15+ 个 AI 协作会话里的事故与复盘；
抽离为通用套件时，规则正文与手册已完全去技术栈化。

## License

[MIT](LICENSE)
