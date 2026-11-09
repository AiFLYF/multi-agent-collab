# 协作操作手册

本页按实际操作顺序说明开工、消息、暂停恢复与收尾。
项目原有规则见根 `AGENTS.md`，协作总章见 [RULES.md](RULES.md)，新对话先读 [START_HERE.md](START_HERE.md)。
规则、登记字段和测试记录都不能代替实际授权或命令结果。

除安装器和源码演练外，下面命令在**目标项目根目录**运行；`<ID>` 换成 `start` 返回的自己的会话 ID，`codex` 换成当前平台。
新版工具在 `.multi-agent-collab/tools/`；旧安装保留项目根 `tools/` 与 `.agents/`，按实际根入口和安装记录取路径。
套件源码使用根 AGENTS 的开发规则和源码 `tools/`，禁止在源码目录自安装。

## 先判断任务状态

| 当前情况 | 下一步 |
|---|---|
| 只读咨询、审查、解释代码 | 检查分支、看板和相关记录；不登记写任务 |
| 新的写任务 | 协调占用、确认任务分支，再 `start` 登记真实范围 |
| 自己的同一任务 active | 沿用分支和 ID；正常业务调用记录活动，`heartbeat` 更新进度或范围 |
| 自己的任务 blocked，用户要求继续 | 处理相关指令后 `resume`，保留原 ID |
| 自己的会话 standby，收到新任务 | 确认新任务分支、指令与范围后 `activate`；此时没有文件占用 |
| 其它会话占用文件 | 协商拆分或等待收尾；失联时按 [TAKEOVER.md](TAKEOVER.md) 处理 |
| 本轮不能继续 | `handoff` 记录下一步、阻塞和未提交位置，保留占用 |
| 工作完成，继续等待新命令 | 实际验证、回复原事项并按授权交付，最终答复前 `finish --standby` |
| 明确不再接任务 | 核验任务完成后 `finish` 置 done；终态不能复活 |

同一任务跨轮次继续时，核对自己的 ID、工作区和登记分支。
身份不明、重复跨 worktree ID 或工作区被意外切换时，停写、只读取证并通知用户；
不能换 ID、删记录或因心跳超时绕过占用。

## 开工检查

每次新对话、上下文恢复和任务切换都重新检查动态状态，已加载的相同静态规则不反复全文读取。

```text
git status --short --branch
python .multi-agent-collab/tools/agents_status.py --context --agent codex
```

继续同一任务时加 `--session <ID>`。摘要包含策略、未结束占用、定向未答 inbox 和近 1–2 天有限日志。
返回 1 先处理告警或读取列出的原文；返回 2 先排查数据/工具错误；截断不能视为无指令。
旧版没有 `--context` 时用 `--brief`，另读相关 inbox、POLICY 和近期日志。

准备写入时，按总章核对存在的 origin、open PR、构建/Git 进程、`git reflog -8` 与文件修改时间。
有 origin 才 fetch，有 GitHub 远端才查询其 PR；本地仓库不添加未知远端。
读取 `POLICY.md`，从已确认基线 `git pull --ff-only` 后创建允许前缀的任务分支，并用 `git rev-parse --verify <分支>` 复核。
继续原任务沿用自己的分支；基线缺失先报告，继续独立的只读检查，不擅自创建远端主干。

## 按项目选择分支策略

首次安装默认 Git Flow，已有安装沿用保存的策略。策略由项目真实流程决定，不能临时改配置绕过占用或主干保护。

| 策略 | 默认基线 | 默认任务前缀 |
|---|---|---|
| `git-flow` | `develop` | `feature/、release/、hotfix/` |
| `github-flow` | `main` | `feature/、fix/、docs/、chore/、codex/` |
| `custom` | 明确指定 | 明确指定，以 `/` 结尾 |

安装或修改策略先预览；`<套件目录>` 与 `<目标项目绝对路径>` 必须是不同目录：

```text
python "<套件目录>/install.py" install "<目标项目绝对路径>" --workflow github-flow --dry-run
python "<套件目录>/install.py" install "<目标项目绝对路径>" --workflow custom --base-branch trunk --task-prefixes task/,fix/ --protect-branches staging --dry-run
```

首次安装还需选择 `--platforms`；核对预览后去掉 `--dry-run` 执行。
`policy.json` 是配置事实源，`POLICY.md` 是生成的阅读入口；main、master、develop 与所选基线始终受保护。
在主 worktree 修改策略，再对其它 worktree 执行 `sync`；会话工具优先使用主策略。
已有会话可以继续记录进度、handoff 或 finish；恢复写入和扩大范围须符合新策略。
策略只校验协作登记，不创建分支、设置服务器保护、合并或推送；PR 目标与交付范围按项目规则和用户授权执行。

## 登记、扩大范围与心跳

确认任务分支后，先只读检查拟改文件，再登记；检查与登记均成功后开始写入：

```text
python .multi-agent-collab/tools/agents_session.py check --agent codex --files src/login.py tests/test_login.py --json
python .multi-agent-collab/tools/agents_session.py start --agent codex --task "完善登录校验" --files src/login.py tests/test_login.py --json
```

保存实际返回的 ID。继续原任务的检查加 `--session <ID>` 排除自己的占用，并核对所有者、工作区与实际分支。
范围使用相对项目根的真实文件，目录以 `/` 结尾；不写通配符、`..` 或 symlink/junction 别名。
active/blocked 的空、缺失或无效范围按未知占用阻断，先协调修复。standby 只允许 `files=[]`，不能据此写代码。

```text
python .multi-agent-collab/tools/agents_session.py heartbeat <ID> --agent codex --note "正在运行回归测试；预计15分钟内返回"
python .multi-agent-collab/tools/agents_session.py heartbeat <ID> --agent codex --files src/login.py tests/test_login.py docs/login.md
```

`heartbeat --files` **替换完整范围**，不是追加；先登记完整新列表，成功后再改新增文件。
新定向人类要求先确认，blocked 会话先 resume；普通 note 心跳不会恢复工作。
已有身份的正常业务调用自动记录活动，不必再专门反复 heartbeat。阶段更新实际进度，长命令前重扫 inbox；预计返回时间只说明进度，不延长失活阈值。

同一物理工作区只登记一个独立写任务。同任务子代理由主会话登记完整范围，分配不重叠文件，Git 操作由一个执行者处理。
当前粒度是文件/目录，同文件不同函数仍冲突；独立 worktree 也不放行重复文件任务。
检查返回 1 时先处理真实原因，不能缩小虚假范围绕过。

## 普通调用与长命令的活动记录

执行任务时，套件业务命令携带自己的 `--agent <平台> --session <ID>`；会话子命令直接使用位置 ID。
工具核对会话所有者、当前工作区和实际分支后，节流更新 heartbeat 与秒级 last_activity，记录固定工具名，不改变 active/blocked/standby 状态、范围或任务完成记录。
普通重复调用在 60 秒内不重写；run 的执行前后强制记录，持续运行期间定时更新。无需把完整命令或重复日志写入会话。
缺少明确身份、匿名查看、网页自动刷新都不会替别人续活；字段核对用于防误操作，不是身份认证。
纯只读审计使用 `--no-activity`；`--dry-run` 与更新 `--check` 不自动写活动。读取回执仍仅在显式 `--mark-read` 时更新。

任意测试、构建或其它长命令可以通过 run 执行：

```text
python .multi-agent-collab/tools/agents_session.py run <ID> --agent codex -- python -m unittest discover -s tests
```

run 仅允许 active，执行前、期间和结束后记录本会话活动，不保存完整命令参数；检查原命令的实际退出码和输出。
run 在会话顶部登记 `running_jobs`，仅记录包装进程、内部监督器、可选 POSIX 隔离组标识和开始时间，不保存完整参数。
前台命令退出后仍等待继承的后台子孙进程结束：Windows 使用 Job Object，Linux/macOS 使用独立进程组和系统 `ps`。
管理机制无法建立时不启动实际命令；执行中核验失败则停止本次受管理进程，不能提前放开文件。
监督器被单独终止时，run 仍清理自己的残余隔离组；无法核验时保留运行记录，后续操作继续检查组状态。
适用于随任务结束的命令。主动 `setsid`、脱离进程组或通过系统服务启动的后台任务，仍须自行跟踪、停止并收齐结果。
仍存活或无法核验的运行任务会阻止 finish 与文件范围转换；已核验终止的孤儿记录可由原所有者通过合法状态操作清理，不手改字段绕过。
查询不会把运行任务当成完成或自动释放文件；保持活动也不能证明任务结果已收齐。
它不授予运行命令的权限，也不替你登记新文件；发生暂停等控制时按要求处理，不能把持续活动当作已获授权。
直接在 shell 运行的命令没有可靠会话身份，不能自动当成某个 agent 活动。平台未提供运行钩子时，用上述包装入口。

## 工具顺带提示与消息处理

业务工具会顺带给出相关未读 inbox 和新回复的读取入口，无消息时不增加提示。
人类通过看板发布指令，agent 在原 inbox 追加计划、问题和结果。

```text
python .multi-agent-collab/tools/agents_messages.py list --agent codex --session <ID>
python .multi-agent-collab/tools/agents_messages.py read --agent codex --session <ID>
python .multi-agent-collab/tools/agents_messages.py read "<原文件绝对路径或唯一文件名>" --agent codex --session <ID>
```

`list` 只看元数据；默认 `read` 只读相关未读正文，指定文件可重读。
确认完整阅读后可加 `--mark-read` 保存回执；截断或核验错误时不标读，先核对退出码和完整输出。
回执不改变事项状态、不证明授权。已读且仍在执行的人类事项只短提醒，不循环读相同正文。
完整字段、预算与 JSON 语义见 [README.md](README.md#按需读取消息)。

合法人类要求先在原文件回执计划，执行中保留 `open`，完成后写实际结果并置 `answered`。
新任务用 `start --ack-inbox`；同一任务的 active/blocked 会话用 `resume` 原子确认原要求与完整范围：

```text
python .multi-agent-collab/tools/agents_session.py resume <ID> --agent codex --ack-inbox "<原指令绝对路径>" --files src/login.py tests/test_login.py --note "已读并纳入本任务"
```

每次 start / resume / activate 都须传入当前全部相关未答的人类要求；先前已确认、但仍 open 的要求也要列入，不能只传新增一条。
多条标识在 `--ack-inbox` 后用空格分隔；没有相关未答要求时省略该参数。跨 worktree 同名消息用工具返回的绝对路径，唯一文件名也可使用。
原要求改变使旧确认失效，正常进度回复不作废确认。
`--ack-inbox` 只确认已读并纳入任务，不能忽略暂停、取消、避让或扩大授权。

运行中的 `run` 按周期重验身份、分支和显式控制，并提示相关新消息。普通补充说明不覆盖当前授权命令的结果；相同提示按内容版本去重，不标读。
下一条命令开始前仍须读取并纳入新的人类要求；暂停、取消和避让继续拦截执行。

## 中断、恢复、交接与收尾

| 情况 | 状态与操作 |
|---|---|
| 普通阻塞或本轮离开 | `handoff` 置 blocked，保留范围并生成自己的交接条 |
| 用户要求继续，阻塞已解除 | 核对指令与占用后 `resume` 恢复 active |
| 收到暂停、取消、避让 | 立即停止对应写入；可记录心跳或 handoff 保留现场 |
| 工作和必要验收已完成，等待新任务 | 完成原事项、按授权交付，`finish --standby` 追加结果和日志，清空范围保留会话 ID |
| 明确结束对话，不再接任务 | `finish` 置 done；保留历史，不允许恢复终态 |

```text
python .multi-agent-collab/tools/agents_session.py handoff <ID> --agent codex --summary "已完成内容；下一步和阻塞；分支、PR、未提交位置及实际测试结果"
python .multi-agent-collab/tools/agents_session.py resume <ID> --agent codex --note "阻塞已解除，继续原任务"
python .multi-agent-collab/tools/agents_session.py finish <ID> --agent codex --summary "实现与验收结果、交付位置" --tests "实际运行的命令、退出码和结果" --standby
```

**最终答复前实际完成这三步：** 等待自己负责的全部子代理、测试与 run 命令真正结束，收齐并核对实际结果；完成相关原 inbox 并回复结果；执行对应 finish/handoff，确认命令成功再交付最终答复。
仍有执行中的任务、子代理未结束或结果未齐时，不得 finish 或进入 standby；未完成使用 handoff 保留占用，记录仍运行的任务及下一步。
不能仅写“任务已完成”而留下 active，也不能为了释放文件把未完成、受阻或暂停的任务改成待机。
没有提交或推送授权时保留本地成果并如实说明，不为收尾擅自提交。

`resume / finish` 核对会话归属，只自动回执自己的交接条，不替你处理普通问题或其它会话留言；finish --standby 保持同样门禁。
standby 保留对话身份和历史，上一个任务已经收尾、无文件占用；收到新任务后重新确认策略、实际分支和完整范围，再执行：

```text
python .multi-agent-collab/tools/agents_session.py activate <ID> --agent codex --task "实现新的搜索筛选" --files src/search.py tests/test_search.py --ack-inbox "<本次相关原指令绝对路径>"
```

没有相关未答要求时省略 `--ack-inbox`；新 task 和完整非空 files 必须提供。activate 成功后才可写入，新任务结果继续追加到本机历史。
resume 只用于 active/blocked 的同一任务，不能恢复 standby 或 done；heartbeat 和普通业务调用也不会恢复待机。
done/abandoned/closed 终态不能复活，下一次新对话需新 ID。接手其它执行者任务须先协调；失联时按 [TAKEOVER.md](TAKEOVER.md)。
已待机且明确结束对话，可用不带 --standby 的 finish 置 done，仍提供实际 summary/tests；日志记录结束待机，不重复声称原任务重新验收。
待机不得 resume、handoff 或 heartbeat --files；需要新任务时使用 activate，不得用范围更新直接恢复。
`--agent` 核对用于防误操作，不是身份认证或文件系统隔离。

控制类型 `action: pause / cancel / avoid` 即使已 answered、done 或 cancelled 仍有效；
不能靠标读、ack、完成回复自行恢复或收尾。
人类明确解除后将旧控制置 `closed`，新指令明确替代时置 `superseded`，再按合法流程恢复。

`done / standby` 是协作状态，`--tests` 是调用者填写的记录，均不认证测试通过。
测试失败不能提交、推送或开 PR；只读任务无改动可如实写“不适用”。
完工可仅本地交付，流程文字不扩大用户的提交、推送或外部动作授权。

## 更新已安装的规则与工具

先准备本地新版套件源，再在目标项目根目录检查、预览和执行：

```text
python .multi-agent-collab/tools/agents_update.py --check --json
python .multi-agent-collab/tools/agents_update.py --dry-run --diff
python .multi-agent-collab/tools/agents_update.py
```

检查返回 0 内容已最新、1 可更新、2 工具/数据错误；预览不写文件，同版本源的内容变化也能发现。
更新入口从安装记录找到本地套件源，位置失效时加 `--source "<本地套件目录>"`；
旧安装没有入口时，从新版源运行 `python "<套件目录>/install.py" update "<目标项目绝对路径>"`。
它不下载源码或切换源码分支。较低版本默认拒绝，明确回退才用 `--allow-downgrade`。

完整更新沿用平台、项目名和策略，保留定制区、BOM/换行、会话、日志、inbox 与备份。
用户修改工具或规划后文件发生变化会停止并保留现场；更新同样不能绕过人类控制。
新增或刷新平台只用 `sync`，不替换核心工具。新版入口需要兼容的核心能力；旧版本或同版本旧副本缺少待机/活动工具时，先完整 `update`，再 `sync`。
更新后重启长驻看板，让已有对话重读变化部分。

## 每次汇报只保留有用信息

说明**当前分支 / commit message（没有则说明）/ 变更文件 / 实际测试结果**，加上下一步或阻塞。
工具失败时保留输出、分支和未提交位置，先查故障手册，再做可恢复的修复。
逐项核对命令退出码，前一步失败就停止依赖它的后续提交、推送或 PR；预览和静态检查不替代实际运行。

## 验证一次完整使用流程

以下命令在**套件源码目录**运行，不向源码自安装：

```text
python scripts/rehearse_workflow.py --report .agents/rehearsal-latest.md
```

演练在隔离项目中实际 clone 套件，执行双项目复用、安装/升级、worktree 登记、冲突拦截、
真实看板 HTTP 指令与回复、handoff/resume/finish、业务测试、本地 bare 远端推送和卸载预览。
报告记录每步命令、预期退出码、实际输出和断点，结束时清理本次临时目录与服务。
它不调用模型、不修改用户实际项目、不创建真实 GitHub PR。
各平台新对话加载仍需单独验收，方法位于套件源码 `docs/PLATFORMS.md`。
