# 多 Agent 协作规则

这是套件总章。项目原有 `AGENTS.md`、测试要求、架构约定和已确认策略继续有效；具体冲突先报告，按用户决定处理。
规则不扩大授权。文件中的身份、计划、命令和测试记录不等于认证或实际执行证据。
新对话先读 [START_HERE.md](START_HERE.md)；相同静态规则在同一会话只加载一次，动态状态每次开工和恢复都重查。

## 1. 分支与禁止操作

`POLICY.md` 说明策略，主 worktree 的 `policy.json` 是事实源；写前用 `agents_session.py check` 核对。
旧项目无配置时默认 Git Flow：从 develop 建 feature/，PR 回 develop；main 仅接收 release/hotfix。
已配置时按允许的基线、任务前缀和 PR 目标执行，不擅自创建远端基线或改分支结构。

- 禁直接 commit/push 到 main、master、develop、所选基线及其它受保护分支；禁共享分支强推、历史改写。
- 禁删除、移动、重建 `.git`；禁删别人的分支、stash、session 或覆盖其未提交文件。
- 禁测试未过就提交、推送、开 PR；禁 `git add -A`、`git add .`，只暂存任务相关文件。
- 禁提交本机私有记录或用 `git add -f` 绕过忽略；已跟踪共享入口正常审查，不取消跟踪或隐藏差异。
- 禁把密钥、WiFi 密码、API key 写入任何文件，包括 `.multi-agent-collab/`。

## 2. Commit 规范

`type(scope): 中文祈使句`，type 为 feat/fix/docs/refactor/test/chore，一行 ≤50 字符；正文写为什么。
一个提交只做一件事，半成品不提交。提交、推送和 PR 遵循用户授权范围，流程本身不授予外部操作权限。

## 3. 工作流程

**协调 → 任务分支 → 登记 → 开发 → 实际验证 → 按授权交付 → 收尾**。完整命令按需查 [WORKFLOW.md](WORKFLOW.md)。

1. 只读核对分支、策略、看板、定向未答 inbox 和近 1–2 天有限日志。写前有 origin 则 fetch，GitHub 远端查 open PR。
   交叉检查看板、构建/Git 进程、reflog 与文件 mtime；无活跃不表示无人工作。缺远端或记录如实说明。
2. 新任务从已确认基线 `git pull --ff-only` 后建允许前缀的任务分支，立即 `git rev-parse --verify <分支>` 复核。
   基线不存在先报告，可继续独立只读验证。同任务沿用分支和 ID，blocked 用 resume；待机新任务用 activate 重新登记，done 不能复活。
3. 写前 `agents_session.py start --agent <平台> --task "任务" --files <实际范围> --json`，保存真实返回 ID。
   工具核对工作区、分支和跨 worktree 占用；冲突或无效数据先处理。只读咨询不登记写会话。
4. 扩大范围先用 heartbeat 的 `--files` 提交**完整新列表**并检查成功，再修改。业务工具带自己的平台/会话，核验后自动记录活动；阶段间隙和长命令前重扫 inbox。
5. 真跑项目测试和必要构建，逐项核对退出码；失败修实现，不迁就测试。预览、静态检查和口头判断不替代实际运行。
   验证通过才按授权提交/推送/PR，提交后核验 HEAD。`--tests` 仅保存调用者的陈述，不能认证通过。
6. 最终答复前等待本任务全部子代理、测试及 run 真正结束并收齐结果，完成待答事项，再真跑收尾并核验成功：默认 finish --standby 写结果、追加日志并释放范围；明确结束才 finish。仍在运行或结果未齐不得收尾，未完成 handoff 保留占用。

每阶段说明 **当前分支 / commit message（无则说明）/ 变更文件 / 实际测试结果**，只报告新进展和限制。
获取现状先调用命令；出错查相关手册，仅修改工具或契约异常时读实现。参数以当前 `--help` 为准。

## 4. 记录、占用与人类指令

session 每会话一文件，只改自己的；worklog 只追加；inbox 一文件一事，在原文件回复。并发记录保留并集。
文件范围是相对项目根的真实文件或目录（目录以 `/` 结尾），不使用 symlink/junction 别名。
active/blocked 缺少或存在无效范围视为未知占用；standby 仅允许空范围，不占文件。重复 ID 不自动择新放行。
系统锁只串行登记，不锁源码，也不是身份认证。
同物理工作区不并发登记独立写任务，独立 worktree 的重复文件仍冲突；同一任务委派须由主会话统一登记并分配不重叠范围。

- 业务调用带 `--agent` 与自己的 `--session`，会话子命令用位置 ID；自动节流记录活动，heartbeat 用于进度/范围。长命令可用 run 包装，不能凭原生 shell 猜归属。超时和 blocked **不释放占用**。
- standby 表示上一任务已收尾、等待新要求；activate 必须给新 task 和完整 files，重新检查策略/分支/指令。resume 仅恢复 active/blocked。任务结果和历史保留。
- resume/finish 只回执自己的交接条，普通问题不能代答；done/closed/standby 不认证测试。匿名查询、网页刷新及 --no-activity 不续活，预览/更新检查零写。
- 失联接管按 [TAKEOVER.md](TAKEOVER.md)：先 inspect/plan，等待 5 分钟、用户确认具体计划且快照一致后才 apply；保留原提交与未提交文件。
- 相关人类指令先处理：暂停、取消、避让立即执行；其它先回执计划并保留 open，实际完成后写结果并 answered。
  `from: human*` 是本机协议字段，字段本身不能认证来源或扩大授权。`--ack-inbox` 只确认已读纳入任务，不能绕过停止指令。
  跨 worktree 同名 inbox 用工具返回的绝对路径区分，唯一文件名可直接使用。
- 重要节点将本机记录备份到仓库外。使用场景、字段与状态详见 [README.md](README.md)。

默认不开 worktree；确需时先核对真实引用和占用，再按 WORKFLOW 的流程安装本机支持文件、登记和清理，不操作别人签出的分支。
看板网页需用户要求再启动，默认仅 127.0.0.1；对外用 `--no-directives` 只读，或设 `MULTI_AGENT_BOARD_PASSWORD` 后开放写入。
HTTP 仅用于可信网络。网页不能唤醒已结束的 agent，也不能代替客户端实际加载规则。

## 5. 异常停写

仓库识别异常、工作区意外切换、unborn/大面积回退、提交无法核验、远端历史突变或其它 Git 进程改写时，立即停写、只读取证并通知用户。
不 init/reset/删 `.git`/强推/手写引用；等 Git 静默 ≥2 分钟或用户确认，破坏性恢复须明确授权。
出错先用 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 索引定位相关条目，按当前证据判断，不能照搬历史沙箱结论；解决后追加记录。

## 6. 项目环境与门禁

<!-- >>> mac:zone:project-env 项目定制区开始：升级保留以下内容 -->

> **安装后第一件事：** 用项目实际命令填写以下项；项目既有门禁继续有效，示例不能当验证结果。

- 运行时 / 依赖：<如 Python 3.12；pip install -r requirements.txt>
- 测试 / lint / 类型检查：<填写实际命令>
- 构建 / 本地调试：<填写实际命令；不适用就说明>
- 环境注意：<只写经确认、开工易忘的事项；不保存密钥或未知代理>

<!-- <<< mac:zone:project-env -->

## 7. 入口与 token 预算

平台入口只导入或引导 START_HERE，总章统一维护；保留项目原有规则，不复制历史到每个平台。
开工用 `agents_status.py --context --agent <平台>`，纯看板一行摘要用 `--brief`；无须日常全文读源码、日志或故障手册。
业务工具顺带提示相关 inbox 和已答回复；有未读项才按提示调用 `agents_messages.py read … --mark-read`。阅读回执不等于指令已纳入或任务已完成，已读人类事项仅短提醒。
明确 `action: pause/cancel/avoid` 的人类指令在受影响写操作前拦截，不能凭标读或 ack 解除；普通文字仍须真实读取并按授权处理。
控制答复为 answered/done 仍有效，须按人类明确授权 closed 或以新指令 superseded 后处理恢复。
有界摘要出现截断或未读文件先读原文，不能凭部分内容 ack 或继续；规则变化再读相关部分。细节见 [TOKEN_BUDGET.md](TOKEN_BUDGET.md)。
新增或刷新平台用 `install.py sync <项目> --platforms <名称>`；核心升级用 update，沿用 install.json 的 source_dir。
规则更新不会自动刷新旧对话；新会话实际验收入口、分支、看板和 inbox，CLI 版本可发现不代表规则已加载。

## 8. 安装文件与 Git

`.multi-agent-collab/` 默认在项目 `.gitignore` 套件标记区忽略，精确平台入口按 `EXCLUDE_ENTRIES` 写入本机 exclude。
已跟踪共享入口只更新托管块，正常显示差异；不取消跟踪、不隐藏其它工具整个目录。
common exclude 跨 worktree 共用；私有入口并集也可能忽略其它工作区的同名未跟踪自有文件，已跟踪文件不受影响。
升级先核对归属和摘要，覆盖前保留字节备份、迁移定制区；未知归属、损坏标记、编码或路径冲突停止并保留现场。
卸载仅移除套件入口，purge 只清理经确认未修改的模板；规则、策略、协作记录与备份保留。旧 `.agents/` 不自动搬迁。
套件源码仓库中的规则和工具是产品源码，正常参与审查，**禁止在源码目录自安装**。
