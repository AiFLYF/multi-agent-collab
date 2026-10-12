# multi-agent-collab

[![python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![deps](https://img.shields.io/badge/dependencies-none-success)](#)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![changelog](https://img.shields.io/badge/changelog-1.4.0-orange)](CHANGELOG.md)

## 这个工具解决什么问题？

你同时开着两个 AI 助手改同一个项目——一个加登录、一个修支付，结果：

- 它们**改了同一个文件，互相覆盖**，白干一半
- 两边都去修同一个 bug，最后改动冲突
- 一个 AI 改到一半你关掉窗口，**没人知道它干到哪、下一步是什么**
- 你想中途喊一声"先别动那个文件"，却没有任何办法插嘴

**multi-agent-collab 就是给 AI 助手们立的一套"交规"。** 它在项目里放一块共享黑板：
谁在哪个分支干活、心跳是否还活着、有问题问谁、走了怎么交接——每个 AI 开工前先看黑板，
**人也能在网页上实时查看、直接下指令**。

- 🪶 **零基础设施**：没有服务器、数据库、账号，全部就是几个 Markdown 文件
- 📦 **零依赖**：Python 标准库即可（Windows/macOS/Linux），一条命令装进任何 git 项目
- 🤖 **不挑 AI**：Claude Code、Codex、OpenCode、Pi、Trae、WorkBuddy 都能用，真人也能参与

```
Claude Code / Codex / OpenCode / Pi / Trae / WorkBuddy ──▶  <你的项目>/.agents/
                                    ├── sessions/  谁在哪个分支干什么（心跳）
                                    ├── worklog/   今天做完了什么
                                    └── inbox/     要问谁、谁来接手
        这些文件不进 git，切分支不丢，任何分支、任何 AI 看到的都是同一份
```

---

## 🚀 30 秒上手（两种方式，任选其一）

> 前提：电脑装了 **Python 3.8+**（命令行能运行 `python --version`）；你的项目是 git 仓库
> （还没有也行，装完 `git init` 后再跑一次安装即可）。

### 方式一：让 AI 自己装（最省心，推荐）

把下面这段话**原样复制**给你项目里的任意一个 AI 助手，只改第一行路径
（指向你下载/克隆本仓库的位置，Windows 路径直接写、保留引号）：

```text
我要为本项目安装 multi-agent-collab 多 AI 协作套件。安装器位置：
"c:/Users/你/multi-agent-collab/install.py"
请严格按步骤做：
1. 确认当前目录是项目根（能看到 .git；不是的话先告诉我项目在哪，不要猜）；
2. 运行：python "c:/Users/你/multi-agent-collab/install.py" install .
   （想先看会发生什么可先加 --dry-run；python 或 git 不在 PATH 就停下告诉我，不要自行下载安装软件）；
3. 运行：python "c:/Users/你/multi-agent-collab/install.py" doctor . ，把体检结果原样汇报给我；
4. 提醒我打开 AGENTS.md 第 6 节，填本项目的测试/构建命令。
除安装器产生的文件外，不要修改我项目里的任何文件；遇到任何报错先停下告诉我，不要自己想办法绕过。
```

AI 会自动装好、体检、并提醒你收尾。装完跳到「[装完之后做什么](#-装完之后做什么3-分钟)」。

### 方式二：自己跑一条命令

在本仓库目录下，把套件装进任意项目：

```bash
python install.py install /path/to/your-project
```

Windows PowerShell 写法一样，例如：

```powershell
python install.py install D:\code\my-app
```

先演练不落盘：把 `install` 换成 `install --dry-run`。

### 🎯 装完之后做什么（3 分钟）

1. **填项目命令**：打开项目里的 `AGENTS.md` 第 6 节和 `CLAUDE.md`「项目速览」，
   把里面的示例换成你项目真实的测试/构建命令（这两段是「项目定制区」，以后套件升级会自动保留）
2. **看一眼看板**：在项目目录运行 `python tools/agents_status.py`
3. **开一个新的 AI 对话**（旧对话不会自动加载新规则），问它：
   「你开工前要先做什么？」——它应该回答 *fetch、看看板、查 inbox、扫 open PR*。
   答得出来，说明规矩已经生效。

不想跑脚本？页面底部「[手动安装](#-手动安装不想跑脚本)**」有等价的手工拷贝命令。

---

## 🖥️ 给人用的看板（AI 该怎么干活由它，你只需要看）

AI 看的是文件，你看的是网页。在项目目录运行：

```powershell
python tools/agents_status.py --web
```

浏览器会自动打开一块实时看板（每 5 秒刷新），你能看到：

- **现在谁在干活**：每个 AI 一张卡片——在哪个分支、做什么任务、几分钟前还活着（绿=正常，红=失联）
- **有没有人给你留言**：未答复的问题、工作交接
- **所有 git worktree 的状态**：有没有 AI 在你没注意的目录里偷偷动手（红色告警）
- **直接对 AI 下指令** ✍️：在卡片上输入"先停一下，别碰登录模块"，AI 在下个阶段间隙就会看到并执行，
  回复会实时出现在网页上（还能开桌面通知）

不想开服务、只想把现状存成文件发给别人？用快照：

```powershell
python tools/agents_status.py --html          # 生成一个零外链单文件 .agents/board.html
python tools/agents_status.py --html --open   # 生成并直接打开
```

> 网页服务**默认只允许本机访问**、只读不写会话文件，AI 平时用的终端命令和 JSON 接口完全不受影响。
> 想在手机/同局域网其他电脑上看？见后面「命令速查」下方的折叠小节 **📱 手机/其他电脑也想看**
> （三档安全设置：本机默认 / 局域网只读 / 带密码可指挥）。

AI 自己日常开工用的是一行摘要（也帮你省 token 费用）：

```text
$ python tools/agents_status.py --brief
OK 看板无告警：活跃2，无失活/未答/worktree 异常，可直接开工
```

完整的终端看板长这样：

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
  [linked/linked] feature/coupon @ a1b2c3d
```

会话文件就是一个 8 行的 Markdown 头，填法范例见
[docs/examples/session-example.md](docs/examples/session-example.md)；
两个会话协商"同文件不同函数、零重叠并行"的真实问答见
[docs/examples/inbox-example.md](docs/examples/inbox-example.md)。

---

## 🤖 支持哪些 AI 助手？

| 平台 | 怎么生效 |
|---|---|
| **Claude Code / TraeCode CLI** | 根目录 `AGENTS.md` 自动加载 |
| **OpenAI Codex CLI** | 原生自动加载 `AGENTS.md`（另有 `~/.codex/AGENTS.md` 全局位） |
| **OpenCode（sst/opencode）** | `AGENTS.md` 就是它的项目规则（官方约定，类似 Cursor rules） |
| **Pi（pi.dev）** | 自动从项目目录发现 `AGENTS.md` |
| **Trae IDE** | 安装器写入 `.trae/rules/git-flow-agent.md`（新对话自动生效） |
| **WorkBuddy** | 安装器生成 `.workbuddy-ai/git-flow-agent.md` 镜像（改总章后跑 `install.py mirror` 刷新） |

以上前四个平台**零配置**：装完套件、开个新对话即可。所有规则都在**新对话开始时**加载，
改完规则后旧对话不会刷新——开新对话就好。

---

## 🧠 它是怎么工作的？（四条交规，每条都是事故换来的）

1. **各写各的小文件，不共用大文件**：一个会话一个文件、一天一份日志、一个问题一个留言条。
   天然不撞车；万一同时写了，合并时取并集，只多不少。
2. **用心跳判断死活，不用锁**：锁在 AI 崩溃时会永远卡死。心跳超过 15 分钟 = 失联，
   别人想接手要先在留言板声明、等 5 分钟没人反对，而且**必须保留前任的全部提交**。
3. **"看板上没人"不等于真的没人**：忘了登记的 AI 是隐形的。所以除了看板，
   还要交叉看 git 记录、文件修改时间、有没有构建进程在跑——真实事故里这么躲过一次撞车。
4. **协作文件只留本机，不进 git**：写在 `.git/info/exclude` 里，切分支不丢、不会误提交、
   不污染团队仓库。代价是没有云端备份——重要时刻把 `.agents/` 文件夹拷到仓库外面即可。

配套看板还会：聚合所有 linked worktree 的协作文件、对"有改动但没人登记"的目录红色告警、
坏心跳不崩、`--prs` 只读列出长期没合并的 PR（防止同一个 bug 被修两遍）。

---

## 📋 命令速查

| 命令 | 作用 |
|---|---|
| `python install.py install <项目>` | 安装 / 升级（幂等，你改的定制内容自动保留） |
| `python install.py doctor <项目>` | 体检：文件、忽略规则、版本新旧、看板冒烟 |
| `python install.py mirror <项目>` | 改完 AGENTS.md 后刷新 WorkBuddy 镜像 |
| `python install.py uninstall <项目>` | 只移除忽略托管块，文件全部保留 |
| `python install.py uninstall <项目> --purge-files` | 再删**没被你改过**的套件文件；会话/日志/留言/备份永不删 |
| `python tools/agents_status.py` | 完整终端看板 |
| `python tools/agents_status.py --brief` | 一行摘要：没事只回一句 OK（AI 开工首选） |
| `python tools/agents_status.py --web` | 图形化看板（可对 AI 下指令） |
| `python tools/agents_status.py --html [路径]` | 导出单文件 HTML 快照 |
| `python tools/agents_status.py --json` | 结构化输出（给脚本用），退出码 0/1 同口径 |
| `python tools/agents_status.py --version` / `--selfcheck` / `--help` | 版本 / 48 项自检 / 全部参数 |

安装器的关键保证：**幂等**（内容相同跳过，不同先字节备份到 `.agents/backups/`，
连 GBK 旧编码也不丢内容）；**三个定制区升级保留**；**linked worktree 双写忽略规则**。

<details>
<summary><b>📱 手机/其他电脑也想看（局域网访问安全档）</b></summary>

```powershell
# ① 默认（推荐）：只听 127.0.0.1，手机和别的电脑访问不到，网页可直接发指令
python tools/agents_status.py --web

# ② 局域网只读大屏：能看不能动
python tools/agents_status.py --web --bind 0.0.0.0 --no-directives

# ③ 局域网可指挥：先设密码（浏览器弹登录框，用户名随便填）
$env:MULTI_AGENT_BOARD_PASSWORD = "你的密码"
python tools/agents_status.py --web --bind 0.0.0.0
```

- "对外可写又没有密码"的危险组合**会被拒绝启动**；连续输错密码有冷却限制
- HTTP 是明文传输，只适合家里/公司可信 WiFi 或 VPN；公共 WiFi、云服务器公网 IP 不要开 0.0.0.0
- 外网安全访问用 SSH 端口转发（`ssh -L 8731:127.0.0.1:8731 用户@电脑`）
- 一键只读开关：环境变量 `MULTI_AGENT_BOARD_READONLY=1`
- 网页发的指令就是 `.agents/inbox/` 里 `from: human` 的标准留言条；AI 空闲睡着时，
  每条指令旁有「复制提醒」按钮，把那句话粘进 AI 对话框即可唤醒它

</details>

---

## 🆘 出问题了怎么办？

每个项目的 `.agents/TROUBLESHOOTING.md` 收录 **29 条从真实事故里长出来的经验**：

- **A 组 · 协作流程**：隐形会话、并发覆盖、假绿测试、长任务被中断、假验证
- **B 组 · Git 与引用**：引用"消失"、沙箱拦截写 ref、假 ahead/behind、推送兜底……
- **C 组 · 网络/终端**：Windows schannel 502、代理拦截、PowerShell 兼容……
- **D 组 · 你的项目专属（空区）**：自己框架/硬件的坑从 D1 开始记，升级自动保留

不知道怎么写 D 组条目？[docs/examples/project-specific-troubleshooting.md](docs/examples/project-specific-troubleshooting.md)
有 4 条写法示范（范例只留在本仓库，不会装进你的项目）。

---

## ⚖️ 适用边界

**适合**：同一台电脑、同一个 git 工作区、多个 AI 会话（含真人）；不想搭任何服务器。

**不适合 / 要想清楚**：

- 跨电脑协作（那需要真正的远程同步，本工具刻意不做）
- AI 之间需要毫秒级实时通信（这里最快是"下一次工具调用时读到留言"）
- AI 完全不遵守 `AGENTS.md`（那就退回最朴素的规矩：同一时间只让一个 AI 动同一文件）
- 想把 AGENTS.md 提交进仓库全团队共享：本套件的取舍是"规则本机私有、各自定制"。
  需要团队共享时，可把共享部分放进仓库文档让本机 AGENTS.md 引用；或安装后手工删掉
  exclude 托管块里对应两行（此时这两个文件不再受分片保护）

---

## 🔧 手动安装（不想跑脚本）

<details>
<summary><b>点击展开等价的手工拷贝命令</b></summary>

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

## 📁 仓库结构与自测

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

**贡献提示**：仓库根目录的 `AGENTS.md` / `CLAUDE.md` / `platforms/` 是**要发布的产品模板**
（正常入库），改它们等于改产品；测试一律通过 `tests/selftest.py` 在系统临时目录进行，
**不要**在 kit 目录里自安装（安装器会拒绝）。变更请同步更新
[CHANGELOG.md](CHANGELOG.md) 与 `install.py` 的 `KIT_VERSION`。

## 起源

规则全部来自真实产品 [ego-link-esp32](https://github.com/AiFLYF/ego-link-esp32)
（ESP32-S3 教学硬件 + Python 课堂服务器）在 15+ 个 AI 协作会话里的事故与复盘。
抽离为通用套件时，规则正文与手册已完全去技术栈化；原项目的完整技术栈手册仍留在它自己的仓库。

## License

[MIT](LICENSE)
