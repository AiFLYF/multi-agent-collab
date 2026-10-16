# TROUBLESHOOTING — 协作流程问题手册（本机，不入库）

> 本文件收集**使用本协作流程（AGENTS.md 的 Git Flow + 多 agent 本机共享记忆）时
> 实际踩过的通用问题**：现象 → 怎么判断 → 怎么修 → 怎么避免。
> 条目与具体语言、框架、硬件无关，任何项目都适用。
>
> 编号分三组、连续编号：
>
> - **A 组 · 协作与流程**（A1–A6）：看板盲区、并发覆盖、假绿测试、长任务回收、假验证
> - **B 组 · Git 与引用**（B1–B14）：引用消失、沙箱拦截写 ref、假 ahead/behind、推送兜底
> - **C 组 · 网络 / 沙箱 / 终端环境**（C1–C9）：代理、TLS、环境变量漂移、PowerShell
>
> 你自己项目里**技术栈专属**的坑（某框架、某硬件、某语言）不要写进本文件，
> 直接追加在本文件末尾的「D 组 · 本项目专属」里，编号 D1、D2…… 写法范例见
> kit 仓库的 `docs/examples/project-specific-troubleshooting.md`。
>
> 遇到新问题解决后**必须追加一条**：只追加，不改旧条目；订正旧结论时新写一条，
> 注明"订正 X编号"。
>
> ## 索引（按现象速查）
>
> - [A1 看板显示"0 活跃"但明明有 agent 在干活](#a1)
> - [A2 两个会话互相覆盖工作区 / 文件大面积回退](#a2)
> - [A3 测试"全绿"但行为是错的（测试依赖了 bug）](#a3)
> - [A4 后台长任务（构建/测试）在 agent 回合结束时被回收](#a4)
> - [A5 预览/仿真工具镜像实现 → 改了代码产物不变（假验证）](#a5)
> - [A6 看板脚本自身崩溃（缺 import，只在特定分支炸）](#a6)
> - [B1 分支引用突然"消失" / unborn / 全部文件显示 staged 新增](#b1)
> - [B2 git commit 报成功但 log 说没有提交](#b2)
> - [B3 shell 建目录"成功"但不存在 / git 写 ref 被静默丢弃](#b3)
> - [B4 git status 报 not a git repository 但 .git 目录在](#b4)
> - [B5 本地协作文件意外出现在 git status / 差点被提交](#b5)
> - [B6 远程分支被重建 / 推过的分支在远端消失](#b6)
> - [B7 `.gitattributes` 规则顺序错误，把源码变成二进制](#b7)
> - [B8 沙箱会话下 ref 写不进去（确认复现；订正 B3）](#b8)
> - [B9 `git switch` 被中断 → 工作区文件大面积"消失" + 残留 index.lock](#b9)
> - [B10 `git fetch` 成功但 remote-tracking 引用没落地 → 假的 ahead/behind](#b10)
> - [B11 git 推拉全挂时用 `gh api` 在服务端造提交（附本地重建同 SHA 提交）](#b11)
> - [B12 手工写 packed-refs 被文本模式写成 CRLF → 全仓库引用失效](#b12)
> - [B13 带斜杠的新分支名"成功"但 unborn + 索引变"全部 staged"](#b13)
> - [B14 建带斜杠的分支引用写不进去 —— 直接写 ref 文件，worktree 别带 -b](#b14)
> - [C1 push 报 502 / schannel / TLS 重协商](#c1)
> - [C2 push 报 Connection reset / 连不上远端主机](#c2)
> - [C3 PowerShell 终端 git 命令行为异常 / 工作目录错乱](#c3)
> - [C4 `gh` 报 "not logged into any GitHub hosts"，其实是 `APPDATA` 为空](#c4)
> - [C5 git push 报 `CONNECT tunnel failed 502` —— 注入的代理在拦](#c5)
> - [C6 去代理直连也失败：先探针再选路，`gh api` 是兜底通道](#c6)
> - [C7 订正 C6：沙箱环境随时间漂移，别缓存环境结论](#c7)
> - [C8 git 推拉全挂时先问一句「你机器上有本地代理吗」](#c8)
> - [C9 git 的 TLS 挂了但 `gh api` 小请求通、大请求必断 —— 分层探测](#c9)

---

# A 组 · 协作与流程

<a id="a1"></a>
## A1 看板显示"0 活跃"但明明有 agent 在干活

**典型场景**：附加 worktree 里的 agent 活动了数小时，看板一直显示 0 活跃，
误导了后续会话；或会话开工后**忘了注册会话文件**，人在干活但完全隐形。

**真因**：

1. `git worktree` 每个工作区独立，未跟踪的 `.multi-agent-collab/` 不会自动出现在新 worktree；
   只扫主工作区的看板看不见它。
2. 会话没按流程注册 `.multi-agent-collab/sessions/<会话ID>.md`，看板自然没有它的数据。

**修复/预防**：

- 看板会枚举全部 linked worktree 并各自扫描 `.multi-agent-collab/`，且对"30 分钟内有改动
  但无会话注册"的 worktree 给出 **`[未注册 worktree 告警]`**。
- **在附加 worktree 里工作时**，必须在**该 worktree 自己的** `.multi-agent-collab/sessions/`
  注册会话文件（把整个 `.multi-agent-collab/` 拷过去即可），否则别人只能看到"有未知活动"。
- 开工协调不能只看看板（AGENTS.md 第 3 节的多信号交叉验证就是为这个设计的）。

**仍可能的盲区**：agent 在 worktree 外的目录干活、或只改未跟踪文件。
看到告警先 `git worktree list` 再去那个目录确认。

---

<a id="a2"></a>
## A2 两个会话互相覆盖工作区 / 文件大面积回退

**典型场景**：一个会话重建了仓库/远程历史，另一会话 stash 的改动随之丢失；
或主工作区源码目录被并发的分支切换清空。

**预防（流程层面，比修复重要）**

- 开工**必须**先 `git fetch` + `python .multi-agent-collab/tools/agents_status.py` + 看 inbox + 扫开着的 PR
- 同一分支/同一批文件已有活跃会话：不抢活，inbox 留言或等失活
- 长时间占用主工作区时，另一个会话应开 worktree 而不是并发 switch
  （但要遵守 [A1](#a1) 的注册义务）
- 接管失活会话分支前：按 [TAKEOVER.md](TAKEOVER.md) 生成计划和声明、等 5 分钟无反对，用户确认后才收回占用；保留并注明其全部提交与未提交文件
- 两个会话确实要碰同一个文件时：先在 inbox 交换"我改哪几个函数/哪几行"，
  确认零重叠再动手；后动手的一方在对方基础上适配，而不是各改各的

**真发生了怎么办**

1. 提交对象优先从 reflog / `cat-file` 找回（见 [B1](#b1)）；未提交改动找回收站、
   编辑器 Local History（如 Trae：`%APPDATA%\Trae CN\User\History`）、仓库外备份目录
2. 别人未提交的工作区改动：先备份到仓库外（如 `../_salvage/<日期>-<事件>/`）再动
3. 全程只读排查，破坏性恢复（reset --hard 等）必须用户明确授权

---

<a id="a3"></a>
## A3 测试"全绿"但行为是错的（测试依赖了 bug）

**典型场景**：修对一处时间/数值计算后，一条既有用例立刻失败——它原本靠**错误的旧行为**
才能通过；同一天还发现全绿测试没覆盖关键的跨模块一致性，导致一个严重 bug 漏网。

**守则**

- 修 bug 导致旧测试失败时，先判断是不是测试在**依赖错误行为**：是的话改测试并注释原因，
  **不许改代码迁就测试**。
- 关键契约（端↔云的坐标系/单位、协议字段、接口出入参形状）要有**显式契约断言**，
  绿不等于对；新增协议字段时同步加契约用例。
- 预览/静态分析只证明"文本自洽"，**不证明能编译/能运行**：
  原生代码的硬门禁永远是真实编译 + 零警告。
- 只在真实外部环境才暴露的路径（硬件、真机、真实网络），"编译通过 + 单测绿"
  不算完成（这类项目专属案例见 kit 的 `docs/examples/`）。

---

<a id="a4"></a>
## A4 后台长任务（构建/测试）在 agent 回合结束时被回收

**现象**：长任务启动后一切正常，但日志停在某一时刻——**往往正好是上一轮回复发出的时刻**；
没有任何报错，stdout/stderr 文件是空的，看起来像"任务卡死"。

**判断**：日志停下的时刻与"上一轮回复发出时刻"吻合、目录里没有错误也没有残留进程
→ 大概率是被回收，不是任务失败。

**真因**：AI 工具的非交互后台长进程**可能活不过回合边界**——主 agent 结束回合时，
即使已派生后台子进程也会被一并退出。

**对策**

- 长任务**前台分段跑**：每次吃满工具超时被杀，但构建系统的增量记录
  （ninja 的 `.ninja_log`、各类 cache、tsbuildinfo 等）已落盘，下一段自动续跑、进度不丢。
- **不要** `nohup ... &` 后立刻结束回合；要么前台等，要么在同一回合内持续轮询。
- 判断是否真在推进：看增量产物的 mtime/大小，别只看日志尾部——
  后台任务的 stdout 可能已丢失（进程活着却不写日志，看着像卡死）。
- 无任何错误输出的"构建失败"，先怀疑进程是不是被杀了
  （这类技术栈专属案例的写法范例，见 kit 仓库 `docs/examples/project-specific-troubleshooting.md` 的 D-例 2）。

---

<a id="a5"></a>
## A5 预览/仿真工具镜像实现 → 改了代码产物不变（假验证）

**现象**：改了源码里的文案/常量，重跑预览/快照工具，生成物**和改动前逐字节相同**
（文件大小一模一样），而工具文档还承诺"改源码产物会自动跟着变"。

**判断**

- 先看产物的**大小/哈希**有没有变；没变就说明改动根本没进产物。
- 再查工具怎么拿内容：是**解析源文件**，还是**在脚本里重新抄了一份**？

**真因**：工具确实解析了一部分源定义（几何、配色等结构化常量），但另一些内容
（如格式化字符串、自然语言文案）无法解析，于是"顺手抄了一份"脚本语言版。
抄的那份不随源文件变，于是**"不会失真"的承诺只对解析的那部分成立**。

**修复**

1. 把缺失的改动补进工具，让产物重新反映代码。
2. **把文档里过强的承诺改成如实描述**，就地标注"这几行是手工镜像，改源码时必须同步改这里"。
   假验证比没有验证更危险：它让你以为改动生效了，其实没有。

**避免**

- 做"预览/仿真/快照"类工具：**能解析就解析，解析不了就显式标注"手工镜像"**。
- 任何生成物验证，第一步先比对**产物有没有真的变**（大小/哈希/时间戳），再看内容。
- 同类陷阱：测试的"期望值"若从被测代码复制而来，就变成了自证。

---

<a id="a6"></a>
## A6 看板自身崩溃：一个坏会话文件让所有 agent 的开工第一步挂掉

**现象**：开工第一步跑看板，直接 traceback 退出。看板是协作体系的"开工第一件事"，
它坏了等于所有 agent 都瞎了。这类事故发生过两次：

1. 某函数缺 `import os`，而该代码路径**只在存在 linked worktree 时才触发**
   ——没用 worktree 的机器上永远发现不了；
2. 某个会话文件的 `heartbeat` 填了无法解析的内容，**人类可读模式**格式化年龄时崩溃；
   更阴的是体检脚本只跑了 `--json`（JSON 模式不崩），于是体检一路绿灯。

**修复（两层）**：

- 看板对任何坏数据**容错不崩**：解析不了的心跳按"失活"处理并给可读提示
- 体检必须覆盖**所有入口**：JSON 和人类两种模式都要实跑

**教训**：

- 改完看板**真的跑一次 `python .multi-agent-collab/tools/agents_status.py --selfcheck`**
  （内置 46 项断言，专门覆盖这些分支），别只靠读代码。
- "只在特定数据/特定分支上炸"的问题，自测必须把**坏数据场景**也造出来
  （缺失字段、乱码、未来时间），正常路径全绿说明不了什么。

---

# B 组 · Git 与引用

<a id="b1"></a>
## B1 分支引用突然"消失" / unborn / 全部文件显示 staged 新增

**现象**

- `git branch` 里分支不见了，或当前分支变成 unborn（`No commits yet`）
- `git status` 把所有文件列成 `A`（暂存的新增）
- `git switch -c feature/x` 后分支写不进引用列表

**判断（先确认不是损坏）**

```bash
git for-each-ref                       # 分支都列得出来 = 仓库健康
git reflog                             # 提交历史在
git cat-file -t <疑似丢失的SHA>        # 输出 commit = 对象完好
```

**真因**：任一会话跑了 `git pack-refs`（**正常操作**）→ 松散引用被收进 `packed-refs`、
空目录 `.git/refs/heads/feature/` 被删；之后 git 写松散引用需重建该目录，
而 AI 工具沙箱可能**静默禁止对 `.git/refs` 的写入**（命令退出码 0、引用却没写进去，
见 [B3](#b3)/[B8](#b8)）。**提交对象和 reflog 始终完好，数据零丢失。**

**修复（非破坏性；动 ref 前告知用户）**

1. 首选，在**非沙箱终端**（系统自带 PowerShell/cmd/终端）执行：
   `git update-ref refs/heads/feature/<分支> <reflog 里的目标 SHA>`
2. 次选，在沙箱内用**文件编辑工具**创建 `.git/refs/heads/feature/<分支>`，
   内容为 `40位SHA + 换行`（不要用 shell mkdir）
3. 立刻 `git pack-refs --all`：把引用打包进 packed-refs，躲过松散引用写入问题
4. `git for-each-ref` 复查所有分支 HEAD 正常

**避免**："引用消失"先查 `for-each-ref`，不要当损坏处理；恢复引用的操作写进 worklog。

---

<a id="b2"></a>
## B2 git commit 报成功但 log 说没有提交

**现象**：`git commit` 输出了新 SHA，但 `git log` 报
`your current branch does not have any commits yet`。

**真因**：与 [B1](#b1) 相同——提交对象已落库，只是分支 ref 没更新。

**修复**：`git cat-file -t <报出的SHA>` 确认对象在 → 按 B1 的手法把 ref 指向该 SHA。
**不要**重新 commit（会产生重复对象，虽然无害但会混乱）。

---

<a id="b3"></a>
## B3 shell 建目录"成功"但不存在 / git 写 ref 被静默丢弃

**现象**：`mkdir -p .git/refs/heads/feature` 退出码 0，后续写引用仍失败；
某些工具里 PowerShell `New-Item` 也可能被拦（`Sandbox Error: hit restricted`）。

**修复**：

- 需要在 `.git` 下落文件时，用**文件编辑工具直接写文件全路径**
  （工具层会创建父目录），不要经过 shell 的 mkdir。
- 或请用户在**非沙箱终端**执行 git 命令（`git update-ref` / `git pack-refs`）。

> ⚠️ **机制订正（重要）**：早期以为真因是"禁止建目录"，决定性实验推翻了它：
>
> ```bash
> mkdir -p .git/refs/heads/feature           # ① 目录真的建出来了
> git update-ref refs/heads/feature/x <sha>  # ② 退出码 0
> cat .git/refs/heads/feature/x              #    文件不存在！
> git rev-parse refs/heads/feature/x         #    unknown revision
> ```
>
> **真正的机制是「git 对 `.git/refs` 的写入被静默拦截」**，不是建目录。
> 完整复现见 [B8](#b8)。
> **教训：否证一条结论前，必须先复现它描述的失败路径本身，而不是复现一个相似但不同的操作。**

**拦截是间歇性的**：同一命令连跑两次，可能一次成功一次被丢。
所以**每次 commit 后都要 `git rev-parse --short HEAD` 复核一次**。

**恢复步骤（照抄）**：

1. `tail -1 .git/logs/refs/heads/<分支>` → 取第 2 列（新 SHA）
2. `git cat-file -t <sha>` 确认是 `commit`（对象没丢）
3. 用**文件编辑工具**写 `.git/refs/heads/<分支>`，内容 = 40 位 SHA + 换行
4. `git rev-parse --short HEAD` 复核；`git status` 应干净

---

<a id="b4"></a>
## B4 git status 报 not a git repository 但 .git 目录在

**现象**：目录里能看到 `.git`，git 却说不是仓库；可能伴随 `.git` 正在被其他进程改写。

**处置（按 AGENTS.md 第 5 节，立即停手）**

1. 不再执行任何写操作和 `git checkout`
2. 只读取证：看 `.git/refs`、`.git/objects` 的修改时间是否在当前时刻附近跳动
   （跳动 = 有别的 git 进程在跑，等它静默 ≥2 分钟）
3. 检查是否有自己不认识的分支名/HEAD 指向（典型事故：另一个会话 `git init` 重建了仓库）
4. 把发现写进 `.multi-agent-collab/inbox/` 通知其他 agent，向用户汇报，等指示

**不要**自行 `git init` / 删 `.git` / reset。

---

<a id="b5"></a>
## B5 本地协作文件意外出现在 git status / 差点被提交

**现象**：`AGENTS.md`、`.multi-agent-collab/`、`.claude/`、`.trae/` 等本应本机私有的路径
出现在 `git status`，或 `git add -A` 差点把它们带进提交。

**判断**：`git check-ignore -v <路径>` 看是否被忽略、由哪条规则忽略；
`git ls-files <路径>` 看是否已被跟踪。

**修复**：

- 本机私有清单统一写在 `.git/info/exclude`（纯本机配置，不污染被跟踪的 .gitignore）。
  安装器会用带标记的托管块自动维护；手工添加的清单见 AGENTS.md 第 8 节。
- 若文件已被跟踪：`git rm --cached <路径>`（保留本机文件、只取消跟踪），
  该改动属于仓库历史变更，需按正常 feature/PR 流程并告知用户。
- 永远用精确路径 `git add <具体文件>`，禁止 `git add -A` / `git add .`。

---

<a id="b6"></a>
## B6 远程分支被重建 / 推过的分支在远端消失

**处置**：

1. `git ls-remote --heads origin` 与本地 `git for-each-ref` 对比，只读取证
2. 不要 force push"恢复"任何东西；先弄清是谁、为什么重建（问用户/看 inbox）
3. 需要本地对齐远程时，`reset --hard` 必须用户明确授权；对齐前先备份工作区
   未提交/未跟踪的关键文件到仓库外
4. GitHub 关闭的 PR 内部快照（`refs/pull/N/head`）任何人都无法自行删除；
   含敏感内容时只能改仓库可见性或联系平台支持

---

<a id="b7"></a>
## B7 `.gitattributes` 规则顺序错误，把源码变成二进制

**现象**：`git diff --stat` 里源码文件显示 `Bin 18079 -> 20123 bytes`
——代码 diff 不可读、PR 无法评审、三方合并用不上文本驱动。

**真因**：`.gitattributes` 是**后写覆盖先写**（last match wins），不是"更具体优先"。
`* binary` 写在 `*.c text eol=lf` 后面时，`binary` 宏（展开为 `-diff -merge -text`）
会把前面的规则全部推翻。

**诊断**：

```bash
git check-attr -a path/to/source.c     # 看到 binary: set / text: unset 就是中招
git ls-files --eol | grep -c "w/crlf"  # 估算修复后会被规范化的文件数
```

**修复**：兜底规则放**最前**，例外放**后面**：

```gitattributes
* text=auto eol=lf        # 兜底：自动判别文本/二进制，文本保持 LF
*.bat text eol=crlf       # Windows 批处理例外
*.png binary              # 真二进制显式列出
*.c text diff eol=lf      # 源码显式声明文本 + 启用文本 diff
vendor/** binary          # 必须放最后才能盖住前面的规则
```

注意 `eol=lf` **不会**让 CRLF 的工作区文件永久显示为"已修改"——git 比较时会先规范化。

---

<a id="b8"></a>
## B8 沙箱会话下 ref 写不进去（确认复现；订正 B3）

**证据（连续多次，都在真实工作区仓库，不是临时目录里的隔离仓库）**

```bash
$ git update-ref refs/heads/feature/x <sha>
update-ref 退出码: 0                     # ← 看起来成功
$ git rev-parse refs/heads/feature/x
fatal: ... unknown revision              # ← 实际没建
```

```bash
$ git commit ...                         # 提交退出码 0
$ git log --oneline -1
fatal: your current branch 'feature/x' does not have any commits yet
$ tail -1 .git/logs/refs/heads/feature/x
<old> <new> commit: ...                  # ← reflog 记下了，对象也在
$ ls .git/refs/heads/feature/x
No such file or directory                # ← 但 ref 文件不存在
```

同一时段还可能观察到：**用文件编辑工具建好的 ref 文件，过一段时间自己消失**。

**判断**

- `git for-each-ref` 能列出其它分支、`git cat-file -t <sha>` 查得到提交对象
  → **仓库健康，数据零丢失**，只是 ref 落不了地。
- 隔离仓库里测不出来：沙箱覆盖层往往只作用于工作区路径。

**修复（每笔提交后都做一次，别嫌烦）**

1. 从 reflog 取新 SHA：`tail -1 .git/logs/refs/heads/<分支> | awk '{print $2}'`
2. 用**文件编辑工具**写 `.git/refs/heads/<分支>`，内容 = 40 位 SHA + 一个换行
3. 立刻验证：`git log --oneline -3` 应能看到刚提交的条目

**降低风险**：提交完就 `git bundle create ../_salvage/<分支>-<时间>.bundle <分支>`
做仓库外备份（`git bundle verify` 能校验）。

**根治缓解：把引用固化进 `packed-refs`（验证有效）**

松散引用会被反复回滚，但 **`packed-refs` 这个已存在文件通常不会被回滚**。补完 ref 后立刻：

```bash
git pack-refs --all      # 把全部本地分支收进 packed-refs，并删掉松散引用
git branch --list        # 验证：所有分支都在
```

注意：pack-refs 之后再新建分支又会产生松散引用、又可能被丢，
所以**每次新建分支后都要再 pack 一次**（或按 [B14](#b14) 直接写文件）。

---

<a id="b9"></a>
## B9 `git switch` 被中断 → 工作区文件大面积"消失" + 残留 index.lock

**现象（两个问题叠加）**

1. `git branch --show-current` 显示**还是旧分支**（切换没完成），但 `git status` 里
   一大批文件显示为 ` D`（工作区已删除、未暂存），磁盘上确实不存在。
2. 再执行 `git restore .` 报 `Unable to create '.git/index.lock': File exists`。

**判断**

- 先确认没有真的 git 进程在跑；看锁文件 mtime 是否明显早于当前时间、大小为 0
  → 那是**上次被杀留下的残锁**。
- `git status` 里是 ` D`（第 2 列，未暂存）而不是 `D `（第 1 列，已暂存）
  → **索引没被破坏**，从索引恢复是安全的。

**修复**

```bash
# 确认无 git 进程后：
rm -f .git/index.lock              # Windows: del .git\index.lock（PowerShell 用 Remove-Item）
git restore .
git status --short                 # 复核：应只剩本来就在的未跟踪文件
```

**避免**：大仓库分支切换可能很慢（实测 40 秒以上），**给足超时**（≥10 分钟），
别用默认短超时导致切一半被杀。切换前先确认工作区干净。

> 🔴 **被打断的切换连未跟踪文件也会一起弄丢**，`git restore` 救不了它们。
> 切换分支前，把未跟踪但有价值的东西（草稿、日志、临时数据）**先挪出仓库或提交**。

---

<a id="b10"></a>
## B10 `git fetch` 成功但 remote-tracking 引用没落地 → 假的 ahead/behind

**现象**

- `git fetch origin --prune` 输出正常，**每次都打印同一批更新**，退出码 0；
  但紧接着查，引用还是旧的，同一个"新分支"能被新建三次。
- **最坑的连带后果**：`git for-each-ref --format='%(upstream:track)'` 报
  `develop [ahead 30]`，而本地与远程 SHA **完全相同，根本没有 ahead**。
  信了这个数就会去 push / merge，全是白干。

**判断**

```bash
git ls-remote origin <分支>       # 远程真相（可靠）
git rev-parse origin/<分支>       # 本地记录（可能陈旧）
find .git/refs/remotes -type f    # 空 → 远程引用全躺在 packed-refs 里（陈旧值）
```

`FETCH_HEAD` 和 `.git/objects` 的时间戳确实更新了（对象下载成功），只有 ref 落不了地
→ **对象零丢失，纯粹是引用陈旧**。真因同 [B8](#b8)。

**修复 / 绕过**

1. **判断 ahead/behind 一律用 `git ls-remote` 复核**，不信 fetch 输出，也不信 track 标记：

   ```bash
   git ls-remote origin <分支>       # 远程真实 SHA
   git rev-parse <本地分支>          # 本地 SHA；相等 → 无 ahead/behind
   ```

2. **判断是否真分叉**用祖先判定，不要数个数：

   ```bash
   git merge-base --is-ancestor <A> <B> && echo "A 是 B 的祖先（无分叉）"
   ```

3. 想修正本地 remote-tracking 引用：用**文件编辑工具**写
   `.git/refs/remotes/origin/<分支>`（40 位 SHA + 换行），再 `git pack-refs --all` 固化。

   > ⚠️ **`pack-refs` 只打包、不改值。** 陈旧引用打包后依然陈旧，必须先重写再 pack。

   修完用 `git for-each-ref` 复查 `[%(upstream:track)]` 是否为空。

**避免**：开工时"有没有未推送提交"这类结论**必须用 `git ls-remote` 复核**；
看到 `ahead N` 且 N 很大，先怀疑引用陈旧。

---

<a id="b11"></a>
## B11 git 推拉全挂时：用 `gh api` 在服务端造提交

**适用**：`git push` / `fetch` 全部失败、重试无用，但同一时刻
`gh api repos/.../git/ref/heads/<branch>` 正常返回（GitHub 环境；
其他 Git 托管平台有各自等价的 REST API）。

**真因**：`gh` 走自己的 HTTP 客户端，不受 git 的 CONNECT 隧道 / TLS 重协商影响。
**远程状态永远问得到，只是 git 推拉暂时不能用。**

**用 Git Data API 在服务端造提交**（全程不需要 git 传输层）

```bash
# 1) 建 blob（内容 base64）。大文件别用 -f 传参（Windows 命令行有长度上限），用 --input
gh api repos/$R/git/blobs   --input body.json    # {"content": b64, "encoding": "base64"}
# 2) 基于父提交的 tree 造新 tree（只列改动的 path）
gh api repos/$R/git/trees   --input tree.json
# 3) 造 commit（parent 指向旧 tip）
gh api repos/$R/git/commits --input commit.json
# 4) 移动分支引用
gh api repos/$R/git/refs/heads/$BR -X PATCH -f sha=<新commit>
```

**坑**：`-f force=false` 会被拒（`"false" is not a boolean`）—— 布尔字段用 `-F`，
或干脆不传（不传即非强推）。

**验证**：树 SHA 与本地 `git write-tree` 对一下，一致即说明服务端内容与本地索引逐字节相同。

**本地怎么拿到"只在服务端存在"的提交？** 服务端提交的 SHA 取决于
`tree/parent/author/committer/message` **以及时区偏移**：API 显示 UTC，
原始对象存的是**本地偏移**（如 `+0800`）。逐字节重建：

```python
raw = ("tree %s\nparent %s\nauthor %s <%s> %d +0800\ncommitter %s <%s> %d +0800\n\n%s"
       % (tree, parent, name, email, epoch, name, email, epoch, message))  # message 后无换行
subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], input=raw.encode())
```

常见错配：`+0000`、message 末尾多 `\n`、用 `git commit-tree -F`（它自己补尾换行）。
对不上就逐个变体试，别硬猜。

**避免**：推送失败别停下——远程状态先问 API；本地 ref 被回滚时可直接往
`.git/packed-refs` 写条目（必须二进制写，[B12](#b12)）。

---

<a id="b12"></a>
## B12 手工写 packed-refs 被文本模式写成 CRLF → 全仓库引用失效

**现象**：`git for-each-ref` 输出为空，`git log <任何分支>` 报 `unknown revision`，满屏：

```
warning: ignoring ref with broken name refs/heads/develop?
warning: ignoring ref with broken name refs/heads/main?
```

那个 **`?`** 就是 `\r` 被终端显示出来的样子。

**判断**：内容看着完全正常也别急着怀疑内容，先查行尾——
`python -c "print(open('.git/packed-refs','rb').read().count(b'\r\n'))"`，非 0 即中招。

**真因**：`packed-refs` 是 **LF-only**。Windows 下 Python 文本模式
（`open(path, "w")`）会把 `\n` 翻译成 `\r\n`，每条 ref 名末尾多个 `\r`
→ 整份引用表被丢弃。**对象库没坏，只是引用表读不出来。**

**修复**：

```python
raw = open(path, "rb").read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
open(path, "wb").write(raw)        # ✅ 二进制写，绝不翻译
```

**避免**：

- **动 `.git/` 下的文件一律二进制读写**（`"rb"`/`"wb"`）或 `open(..., newline="")`，
  git 元数据都是 LF-only。
- 文件编辑工具不翻译换行，是安全的；写 loose ref 内容就是 40 位 SHA + `\n`。
- 改完立刻 `git for-each-ref` + `git log -1 <分支>` 复核。

---

<a id="b13"></a>
## B13 带斜杠的新分支名"成功"但 unborn + 索引变"全部 staged"

**现象**

```bash
$ git switch -c tmp/trial-x develop
Switched to a new branch 'tmp/trial-x'      # 退出码 0，毫无警告
$ git rev-parse HEAD
fatal: ambiguous argument 'HEAD': unknown revision   # ← HEAD 是 unborn
$ git status
## No commits yet on tmp/trial-x
A  .gitattributes                                     # 所有文件变成 staged 新增
```

`.git/refs/heads/tmp/` 没建出来，`git branch --list 'tmp/*'` 为空（幻影分支）；
**工作区文件完好无损**——这不是 [B9](#b9) 那种丢文件。

**真因**：写嵌套路径的松散引用（`refs/heads/tmp/xxx`）需要现场创建子目录，
这一步被静默拦掉（同 [B8](#b8)）；但 HEAD 切换已落盘 → 变成 unborn。
直接落在 `refs/heads/` 下的名字（`develop`/`main`）不受影响。

**修复（无损，30 秒）**

```bash
git symbolic-ref HEAD refs/heads/<一个确实存在的分支>
git rev-parse --short HEAD && git status -sb   # 索引随 HEAD 自动变干净，不用手动管
```

**避免**

- 临时/试合并分支**不要用嵌套名**（`tmp/x`、`test/x`），用扁平名。
- 想知道"合起来会不会冲突"，用 `git merge-tree --write-tree <A> <B>`（git ≥ 2.38）：
  只在对象库做三路合并、打印冲突文件，**不写引用、不动索引、不动工作区**，零副作用。

---

<a id="b14"></a>
## B14 建带斜杠的分支引用写不进去 —— 直接写 ref 文件，worktree 别带 -b

**现象（同一件事的多种失败样子）**

```bash
git branch feature/x develop                  # 退出码 0，但分支不存在
git update-ref refs/heads/feature/x <sha>     # 同样静默失败
git worktree add ../wt -b feature/x           # fatal: invalid reference
```

**真因**：git 写松散引用要求 `refs/heads/feature/` 目录**已经存在**；
该目录不会被创建（或创建了又被回滚），写文件失败却**不报错**（退出码 0）。
`git worktree add -b` 更狠：它会**删掉**你刚写进去的引用文件再报错，
worktree 目录残留在磁盘上、HEAD 是 unborn。

**修复**：绕过 git，用**文件编辑工具**直接写引用文件。loose ref 格式就是
「40 位 SHA + 换行」：

```
.git/refs/heads/feature/x
---------------------------------------
7e88fc4423d246039a2ad5ea726d867ebde2ac4a
```

写完立刻验证——**唯一可信的判据是 rev-parse 能解析**：

```bash
git rev-parse feature/x          # 必须返回 SHA；branch --list 有输出 ≠ 落地
```

**坑中坑（顺序）**：分支**先真实存在**，再 `git worktree add <dir> <branch>`，
**不要带 `-b`**（带了会重建引用、删掉你写的文件）。
建完顺手 `git pack-refs --all` 固化（[B8](#b8)）。

---

# C 组 · 网络 / 沙箱 / 终端环境

<a id="c1"></a>
## C1 push 报 502 / schannel / TLS 重协商（代理环境）

**现象**：`git push` → `CONNECT tunnel failed, response 502`，详细日志
（`GIT_CURL_VERBOSE=1`）里是 `schannel: renegotiating SSL/TLS connection` 后失败。
**关键判据**：同一代理下 `curl https://github.com` 返回 200（代理和网络都通）。

**真因**：Git for Windows 默认 **schannel** TLS 后端在代理隧道里重协商会失败，
不是网络、不是认证、不是平台故障。

**修复（实测稳定）**

```bash
git -c http.sslBackend=openssl push -u origin <分支>
# 大推送再叠加：
git -c http.sslBackend=openssl -c http.postBuffer=524288000 \
    -c http.version=HTTP/1.1 -c http.lowSpeedLimit=0 push origin <分支>
# 本地引用缺失但对象还在：绕过引用按 SHA 直推
git push origin <40位SHA>:refs/heads/<分支名>
```

长期生效可 `git config http.sslBackend openssl`（本机级配置，非仓库改动）。

---

<a id="c2"></a>
## C2 push 报 Connection reset / 连不上远端主机

**判断**：curl 也不通 = 真断网/抖动（本条）；curl 通但 git 不通 = [C1](#c1)/[C5](#c5)。

**修复**：

- 纯瞬时抖动：等 1–2 分钟重试（经常数分钟内自愈）
- 用户已提供代理时才走代理
- 推送前先 `git ls-remote origin` 探活，别盲目长循环重试

**避免**：不要把网络问题误判成仓库损坏去动 `.git`。

---

<a id="c3"></a>
## C3 PowerShell 终端 git 命令行为异常 / 工作目录错乱

**现象**：同一条 git 命令在一个终端报 `not a git repository`，换新终端就正常；
PowerShell 5 不支持 `??` 和 `&&`；git 的 stderr 被包成红色 CLIXML
（看着像报错，实际 `$LASTEXITCODE` 为 0）。

**处置**

- 终端状态可疑时直接**开新终端**，工作目录显式设为仓库根；必要时 `git -C <路径>`
- PS5 语法：用 `if ($x) {...} else {...}` 替代 `??`，用 `;` 替代 `&&`
- 判断 git 成败看 `$LASTEXITCODE`，不要看红字（git 把进度信息写 stderr）

---

<a id="c4"></a>
## C4 `gh` 报 "not logged into any GitHub hosts"，其实是 `APPDATA` 为空

**现象**：20 分钟前 `gh auth status` 还正常，突然报"没登录过任何 host"，连续重试都一样。

**真因**：`gh` 的配置在 `%APPDATA%\GitHub CLI\hosts.yml`。某些沙箱 shell 会话里
**`APPDATA` 是空的**，`gh` 找不到配置目录就报未登录，根本没走到 keyring。

**判断**：

```bash
printf "APPDATA=%s\nLOCALAPPDATA=%s\n" "$APPDATA" "$LOCALAPPDATA"
# APPDATA 为空而 LOCALAPPDATA 有值 → 就是这个坑
```

**修复**：显式传 `APPDATA`（不必改全局环境）：

```bash
export APPDATA="C:\\Users\\<你>\\AppData\\Roaming"   # Windows
gh auth status
```

环境变量**时有时无**，别凭"上次能用"断定这次能用——用之前先探一下。

---

<a id="c5"></a>
## C5 git push 报 `CONNECT tunnel failed 502` —— 注入的代理在拦

**现象**：连续十几次失败，在 `CONNECT tunnel failed, response 502` 和
`Empty reply from server` 之间跳；**同一时刻 `gh api` / `gh pr create` 完全正常**。

**真因**：沙箱注入了本地代理（`http_proxy=http://127.0.0.1:<端口>` 四个变量）。
git 走该代理的 CONNECT 隧道被 502；`gh` 用自己的 HTTP 客户端所以没事
——这解释了"为什么 gh 行 git 不行"。代理本身**时好时坏**。

**修复（二选一）**

```bash
# 首选：推送时剥掉代理
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  git -c http.sslBackend=openssl push -u origin <分支>

# 备用：用 API 服务端建分支（见 B11）
gh api repos/<owner>/<repo>/git/refs -X POST -f ref="refs/heads/<分支>" -f sha="<40位SHA>"
```

需要凭据时可按命令借用 `gh` 的 token：

```bash
git -c credential.helper= -c credential.helper='!gh auth git-credential' push ...
```

---

<a id="c6"></a>
## C6 去代理直连也失败：先探针再选路，`gh api` 是兜底通道

> ⚠️ 本条"直连没有路由"的概括已被 [C7](#c7) 订正；
> 「先探针再选路」「`gh api` 可用」两点仍然成立。

**现象**：按 [C5](#c5) 剥掉代理跑 git 反而彻底超时；带代理又是 502。两条路都不通时，
别硬重试，先探针：

```bash
curl -s -o /dev/null -w "直连=%{http_code}\n"   --max-time 10 --noproxy '*' https://github.com
curl -s -o /dev/null -w "走代理=%{http_code}\n" --max-time 10 https://github.com
# 两者都 000/502 → git 通道暂时不可用，改走 API
```

**可用通道：`gh api`**（走独立 HTTP 客户端，不受 CONNECT 隧道影响）：

```bash
export APPDATA="C:\Users\<你>\AppData\Roaming"     # Windows；见 C4
gh api repos/<owner>/<repo>/branches --jq '.[] | "\(.name)\t\(.commit.sha)"'
gh api repos/<owner>/<repo>/pulls    --jq '.[] | "\(.number) \(.state)"'
```

结论：**远程状态永远问得到，只是 git 推拉暂时不能用**，别因此停工。

> ⚠️ 对账脚本用 `git rev-parse --verify "refs/heads/$br"`，不能只写分支名——
> 不存在的分支名会被当路径回显成字符串，骗对账逻辑。

---

<a id="c7"></a>
## C7 订正 C6：沙箱环境随时间漂移，别缓存环境结论

**反例（同一台机器、同一工作区，相隔一分钟）**：09:35 去代理 `git ls-remote` 超时；
09:34 / 09:36 / 09:38 连测三次同样写法全部成功。受控实验还证明叠加了第二层：
**出口抖动**——环境变量全程不变，同一条 curl 背靠背一次 000、一次 200。

**真因：沙箱环境本身在变，不是固定配置。** 同一会话前后两次的 `APPDATA`、
代理变量都可能不同；网络出口也独立抖动。

**正确做法：每次先探针，别缓存上次的结论。**

```bash
printf "APPDATA=[%s] proxy=[%s]\n" "$APPDATA" "$(env | grep -ci '^https\?_[Pp][Rr][Oo][Xx][Yy]')"
curl -s -o /dev/null -w "直连=%{http_code} " --max-time 8 --noproxy '*' https://github.com
curl -s -o /dev/null -w "代理=%{http_code}\n" --max-time 8 https://github.com
```

**可操作结论（按可靠性排序）**

1. **`gh api` 是从未失败过的通道** —— 远程状态一律先问它。
2. **git 推拉要重试**：一次失败 ≠ 不通，剥代理 + openssl 后端重试 2–3 次。
3. **别把任何一次观测写成稳定事实**——下结论前先问：这是一次观测，还是重复验证过的？

---

<a id="c8"></a>
## C8 git 推拉全挂时先问一句「你机器上有本地代理吗」

曾为"git 不通"绕了一大圈（API 造提交、逐字节重建对象、手写 packed-refs），
全是白费劲——用户一句 `set http_proxy=http://127.0.0.1:<端口>` 后立刻全通。

**判断顺序（别跳）**

```bash
# 1) 先问用户有没有本地代理（最省事，先做这个！）
# 2) 常见端口：Clash/v2ray 等常见 10808(socks)、10809(http)、7890
http_proxy=http://127.0.0.1:<端口> https_proxy=http://127.0.0.1:<端口> \
    git -c http.sslBackend=openssl ls-remote origin
```

**修复**：命令前带环境变量即可（**不要**写进 `.gitconfig`：代理不一定常开，
写死了没开代理时更迷惑）。

**顺序很重要**：git 不通 → **先问代理** → 再试剥代理（[C5](#c5)）→
最后才绕行 API（[B11](#b11)）。顺序反了会浪费大量时间。

---

<a id="c9"></a>
## C9 git 的 TLS 挂了但 `gh api` 小请求通、大请求必断 —— 分层探测

**现象分层（别一棍子打死"网断了"）**

- `git ls-remote` / `git push`：`schannel: failed to receive handshake`、
  `unexpected eof` —— git 完全用不了
- `gh api`（小 GET）：正常
- `gh api .../git/blobs`（几十 KB 以上的 POST）：`Post ...: EOF`，重试多次必断

**结论**：不同工具的 TLS 实现不同，代理对它们的影响不一样；**而且大包比小包更容易断**。

**应对**

1. 先分层探测（小 GET / `git ls-remote` / 大 POST 各一次），再决定走哪条路
2. 小请求走 API（[B11](#b11)），大文件传不上去时别死磕
3. 都不行就：**先把提交安全落到本地**（内容是对的），网络恢复后再推
4. 本地能验证的工作不受影响——修复当场就能验证，不必等推送

---

# D 组 · 本项目专属（你自己的团队条目记在这里）

<!-- >>> mac:zone:project-troubles 自动迁移区开始：升级本手册时会自动保留这两行标记之间的内容；你追加的 Dn 条目就写在这里，不要删标记 -->

> 上面 A/B/C 三组与技术栈无关。**你项目特有的坑**（某框架、某硬件、某语言、某内部服务）
> 从 D1 开始往下记，格式同 A/B/C：现象 / 判断 / 真因 / 修复 / 避免。
>
> 不知道怎么写？kit 仓库的 `docs/examples/project-specific-troubleshooting.md`
> 给了 4 条来自一个嵌入式项目的精华范例（SoftAP 密码导致板子重启、构建进程被杀却零报错、
> 摄像头控制项写坏管线、设备连不上先查配网），可直接照着写。
>
> 例（安装后把下面这个占位条目替换成你的第一条真实条目）：
>
> <a id="d1"></a>
> ## D1 （示例占位，安装后替换或删除）<你的技术栈> 在 <场景> 下会 <坑>
>
> - 现象：……
> - 判断：……
> - 真因：……
> - 修复：……
> - 避免：……

<!-- <<< mac:zone:project-troubles -->

---

## 维护规则

- 格式固定：`## <组编号> 标题` + 现象/判断/真因/修复/避免，锚点加在顶部索引
- 编号只增不复用；订正旧条目写新条目并引用旧编号（如"订正 B3"）
- 一条只讲一个问题；命令给可直接复制的版本
- A/B/C 组只放通用内容；技术栈专属一律进 D 组
- 本文件是 `.multi-agent-collab/` 的一部分，**本机私有不入库**，换机时重跑安装器即可恢复


## 安装兼容性：CRLF 与项目自有 .agents 路径

- 现象：Windows 中只改定制区却被判为区外修改，或项目自有 `.agents/skills/` 被解释成协作记录。
- 判断：分别对比原始字节、规范化换行后的定制区摘要，以及命令返回的 files 范围。
- 真因：共享入口需要保留原始换行，但定制区解析曾只接受 LF；通用路径校验曾承担内部布局转换。
- 修复：仅在解析定制区时规范化换行；入口区外保留字节，内部标记延续原换行；只对明确的套件内部路径转换布局。
- 验证：定制区升级、CRLF 入口幂等、项目自有路径占用与接管快照回归。

## 本机浏览器预览通道未附加

- 现象：打开本地页面时返回 `Timed out waiting for the Browser webview to attach`。
- 判断：本地服务进程已启动，但隐藏和可见标签都无法附加；属于浏览器自动化通道问题。
- 处理：停止重复重试，关闭本次临时 HTTP 服务，保留真实运行测试与静态检查结果，并明确本次未完成视觉验收；通道恢复后再验收页面。

## Windows 短路径导致主工作区被误报

- 现象：Windows CI 中主工作区被列为未登记 worktree，Linux 与本地长路径环境正常。
- 真因：TEMP/cwd 使用 RUNNER~1 一类 8.3 别名，Git 返回完整路径；仅替换分隔符和转小写不能识别同一个目录。新 .gitignore 改动使原有比较缺陷变成可见告警。
- 修复：路径身份比较先使用 Path.resolve 解析别名，再保留原有大小写与分隔符规范化；不放宽未登记工作区告警。
- 验证：相同目录的父路径别名、Windows GetShortPathNameW 别名和真实 linked worktree 回归。
