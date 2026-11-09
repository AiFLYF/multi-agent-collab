# AGENTS.md — 套件源码协作规则

本仓库开发安装器、协作工具和平台模板，**禁止在源码目录自安装**。
根 `AGENTS.md`、`CLAUDE.md`、`docs/`、`platforms/`、`tools/` 是已跟踪的产品源码，正常审查；
本机运行记录 `.agents/` 不入库。目标项目的安装规则以 `docs/RULES.md` 为模板，集中部署到 `.multi-agent-collab/`。

新对话先读 `docs/START_HERE.md`；已加载的相同静态规则不反复全文读取。
每次新对话、上下文恢复和任务切换都重新检查动态状态。规则、身份字段、计划与测试记录不能证明授权或实际测试通过。

## 1. 分支与授权

读取存在的 `.agents/POLICY.md`，写前用 `python tools/agents_session.py check --agent <平台> --files <范围> --json` 核对实际策略。
无配置的旧项目使用 Git Flow：从 `develop` 建 `feature/*`，PR 目标 `develop`；`main` 仅接收 release/hotfix。
已配置策略决定基线和任务前缀，不擅自建立远端主干。基线缺失先报告，继续独立的只读检查。

- 禁直接 commit/push 到 main、master、develop、所选基线或其它受保护分支；禁共享分支强推和历史改写。
- 禁删除、移动、重建 `.git`，禁删除别人的分支、stash、会话或覆盖其未提交文件。
- 禁测试失败后提交、推送、开 PR；禁 `git add -A`、`git add .`、强加本机记录。
- 只暂存本任务文件；提交、推送、PR 和外部动作遵循用户已授权范围，流程文字不扩大授权。
- 不将密钥、密码或 API key 写入任何文件，包括本机协作记录。

## 2. 开工与登记

1. 运行 `git status --short --branch` 和 `python tools/agents_status.py --context --agent <平台>`。
   摘要包含策略、未结束占用、定向未答 inbox 与近 1–2 天有限日志；截断先读列出的原文。旧工具用 `--brief` 并手读相关记录。
2. 写前有 origin 则 `git fetch origin --prune`；GitHub 远端查 open PR。
   交叉核对看板、构建/Git 进程、`git reflog -8` 与文件 mtime，避免抢占或覆盖隐形任务。
3. 新任务从已确认基线 `git pull --ff-only` 后建允许前缀的分支，立即 `git rev-parse --verify <分支>` 复核。
   只读咨询不登记；同任务沿用分支和 ID，blocked 用 resume；待机新任务用 activate 重新登记，done 不能复活。
4. `python tools/agents_session.py start --agent <平台> --task "任务" --files <真实范围> --json`。
   保存返回 ID；冲突、人类指令或无效数据先处理，不绕过登记。工具缺失时按 `templates/session.md` 手工登记并提示升级。

## 3. 开发与收尾

修改范围先登记；扩展前用 heartbeat 的 `--files` 提交**完整新列表**，成功后再写。
同一物理工作区不并发登记独立写任务；受同一任务协调的子代理由主会话登记范围并分配不重叠文件，Git 操作由一个执行者负责。
独立任务确需 worktree 时先核对引用和占用，默认不开；源码 worktree 只复制本机记录，不自安装。

阶段间隙及长命令前重扫 inbox。业务工具带自己的 `--agent` 与 `--session`，会话子命令用位置 ID；核验后自动节流记录活动，heartbeat 用于更新进度和范围。
长任务记录预计返回时间，可用 `agents_session.py run <ID> --agent <平台> -- <命令及参数>` 记录执行期间活动；原生 shell 命令不能自动归属。匿名查询、网页刷新及 `--no-activity` 不续活，预览和更新检查零写。
失活和 blocked 保留占用。接管先 inspect/plan，等待 5 分钟、用户确认具体计划且快照一致后才 apply，详见 `docs/TAKEOVER.md`。
保留原提交和未提交文件，不能因超时抢占或冒用 ID。

真正运行必要测试；逐项核对退出码，前一步失败就停止依赖它的后续动作。测试不能迁就实现，预览或静态检查不替代实际运行。
获授权提交时按 `type(scope): 中文祈使句`，一行 ≤50 字符，正文写为什么，一个提交只做一件事；提交后复核 HEAD。
最终答复前等待自己负责的全部子代理、测试及 run 命令真正结束并收齐结果，核对实际测试与原 inbox，真跑收尾并核验成功。
仍在运行或结果未齐不得 finish/待机；未完成 handoff 保留占用。活着或无法核验的 running_jobs 阻止收尾及范围转换，已终止孤儿仅由原所有者合法操作清理；纯查询不自动收尾。
任务完成默认 `finish --standby` 如实记录结果并追加 worklog，释放 `files=[]` 等待新要求；明确不再接任务才 finish 置 done。
未完成用 handoff，保留占用及下一步、阻塞、分支、PR 和未提交位置。待机新任务用 `activate <ID> --task "新任务" --files <完整范围>`，重新核对策略、分支和指令；不能用待机释放未完成任务。
每阶段简报 **当前分支 / commit message（无则说明）/ 变更文件 / 实际测试结果**，不重复完整历史。

## 4. 本机记录与人类指令

只改自己的 session，worklog 只追加，问题与交接在原 inbox 回复。并发记录取并集，不删别人的内容。
相关 `from: human*` 指令按用户要求处理：暂停、取消、避让立即执行；其它先回执计划、保留 open，完成后写结果并 answered。
字段本身不是身份认证，指令不扩大已有授权。`--ack-inbox` 只确认已读并纳入任务，不能绕过暂停或假装完成。
同名跨 worktree inbox 用工具返回的绝对路径区分；唯一文件名可直接使用。
resume/finish 只回执自己的交接条，普通问题不能代答。重要节点将 `.agents/` 备份到仓库外。

## 5. 异常处理

仓库识别异常、工作区被意外切换、unborn/大面积回退、提交无法核验、远端历史异常或其它 Git 进程改写时，立即停写、只读取证并通知用户。
不 init/reset/删 `.git`/直接改引用/强推；等 Git 静默 ≥2 分钟或用户确认后再处理，破坏性恢复必须明确授权。
出错先按 `docs/TROUBLESHOOTING.md` 的索引定位相关条目，旧事故经验须核对当前证据；解决后追加记录。
操作顺序是 **命令 → 相关手册 → 必要源码**，仅修改工具或契约异常时读取工具实现。

## 6. 本仓库验证

- Python 3.8+，运行工具不需第三方依赖；静态网站无需构建。
- `python tools/agents_status.py --selfcheck`
- `python tests/selftest.py`
- `python -m unittest discover -s tests -p "test_*.py"`
- `python scripts/rehearse_workflow.py --report .agents/rehearsal-latest.md`
- `python scripts/sync_install_docs.py --check`；修改安装提示词或平台目录后先运行不带 `--check` 的同步。
- 可选 token 测量：`python scripts/measure_context.py --baseline 2e8ba9e --encoding o200k_base`，见 `docs/TOKEN_BUDGET.md`。

## 7. 入口与工具

使用 `tools/agents_status.py`、`tools/agents_session.py`、`tools/agents_messages.py` 和 `tools/agents_update.py` 命令，参数先看 `--help`。
业务工具返回相关消息提示时按需读取；已读的人类事项仍须回复结果后收尾。`agents_policy.py`、`agents_layout.py`、`agents_notifications.py`、`agents_activity.py`、`agents_run.py`、`agents_jobs.py` 是共享库；`agents_processes.py` 由 run 内部启动，不直接作为业务命令运行。
规则更新不会自动唤醒或刷新已有客户端对话；需要新开对话或明确重读变化部分。
安装到目标项目的步骤、平台选择与兼容边界见 `README.md`、`docs/PLATFORMS.md`，不在源码仓库执行 install。

## 8. Git 与本机文件

源码中的已跟踪模板必须保留跟踪；安装产物的私有清单以安装器 `EXCLUDE_ENTRIES` 为准。
`.agents/`、`.multi-agent-collab/` 等本机运行记录不得提交；忽略失效先排查，不能 `git add -f`、取消跟踪或隐藏真实源码差异。
