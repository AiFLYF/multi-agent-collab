# 更新日志

本项目版本号写在 `install.py` 的 `KIT_VERSION`，安装时登记到目标项目的
`.agents/install.json`，`doctor` 据此提示升级。
版本号遵循语义化版本（major.minor.patch）。

## 1.3.1 — 2026-10-12

指令通道上线后的加固修复（均有测试锁定）：

- **修伪造回复**：指令正文里写 `## 回复`/`- status: answered` 等 markdown 结构，
  会让看板显示一条 agent 从没给过的"假回复"。写入时对正文结构行行首转义（原文不丢），
  解析时只认真正的回复区；看板内置断言 43 → 46
- **修桌面通知永不触发**：旧逻辑把"待处理"文件也计入已见集合，导致 open→answered
  跳变永远不通知；改为只对比相邻两轮的待处理集合
- 点名其他 **linked worktree** 里的会话不再被误判"目标不存在"（聚合全部工作区会话）
- 密码框点"取消"后不再每 5 秒重复弹出：自动轮询静默，发指令时才再给一次输入机会
- 清理：正文不再残留 `## 指令` 小节标题；移除从未写入的 `from_name` 死字段
- 测试基建：基础安装移入 `setUpClass`，消除 test_10 等对 test_01 的执行顺序耦合
  （单跑任意 E2E 用例不再必挂）；doctor/清理的 git 超时放宽到 180s 且清理改为容错，
  Windows 高负载/杀软扫描下不再偶发收尾报错

## 1.3.0 — 2026-10-12

**人类可以在图形化看板上直接对 agent 下指令**，并支持受控的局域网访问。

**指令通道**

- `--web` 页面新增指令输入框：选"所有 agent（广播）"或点名某个活跃会话即可发指令；
  指令即 `.agents/inbox/` 里 `from: human*` 的标准问事板文件（自动取当日递增序号、
  O_EXCL 防并发撞号、原子落盘），agent 无需学习新格式，回复写回同一文件
- 页面"指令与回复"面板：指令气泡、待处理/已回复状态、agent 回复、
  每条指令「复制提醒」按钮（agent 空闲回合无法被网页唤醒时，一键复制一句话粘进对话框）
- 桌面通知：指令被回复时弹系统通知（浏览器权限，纯前端）
- AGENTS.md / Trae / Claude 三个产品入口同步：人类指令优先级最高、"暂停/取消"立即执行、
  **长任务阶段间隙与长命令前必须重扫 inbox**；用户提出时主动帮其启动/停止看板

**安全（绑定地址 × 密码 × 只读，三档）**

- 新增 `--bind 127.0.0.1|0.0.0.0`、`--no-directives`；环境变量
  `MULTI_AGENT_BOARD_PASSWORD`（Basic 认证、常量时间比较、同 IP 5 次失败锁 60 秒）、
  `MULTI_AGENT_BOARD_READONLY=1`
- 启动强制：对外绑定 + 允许发指令 + 无密码 → 拒绝启动
- POST 防护：自定义请求头 + Origin/Referer 同源校验（跨站表单伪造不出）；
  目标只能是 `any` 或真实存在的会话 ID；正文 ≤4000 字、控制字符过滤；
  每次发令在服务终端打印一行审计日志
- 文档如实说明 HTTP 明文边界（仅可信 WiFi/VPN，外网用 SSH 端口转发），不做加密承诺

**测试**：看板内置断言 34 → 43；selftest 总用例 30 → 32（新增指令 POST/序号/CSRF/坏目标/
超长、401 密码挑战/错误拒绝/正确放行、只读 403、已回复指令下发；启动组合校验在内置自测里）

## 1.2.0 — 2026-10-12

新增**给人类用户的图形化看板**；agent 的终端/JSON 用法、输出口径与退出码完全不变。

**看板 `tools/agents_status.py`**

- 新增 `--web`：启动只监听 127.0.0.1 的本地网页看板，浏览器自动打开、每 5 秒轮询
  实时刷新（`--port` 改端口、占用自动顺延、`--no-open` 不弹浏览器）；
  页面可勾选附带 open PR（只读调 gh）；断开连接有显式状态提示
- 新增 `--html [路径]`：导出零外链、自包含的单文件 HTML **快照**
  （默认 `.agents/board.html`；`--open` 导出后自动打开；离线可发可开）
- 页面内容：总体状态横幅、活跃/失活/已结束/未答/worktree/PR 统计卡、
  会话卡片（状态色边、agent/分支/worktree/blocked 标签、心跳新鲜度、PR 链接）、
  未答问题（新旧分组）、worktree 注册状态、最近 worklog；自动适配深色模式与窄屏
- 数据层重构为统一的 `collect()`：终端、JSON、HTML、web 四种模式共用一份口径，
  不会再出现"网页说没事、终端说有告警"；快照原子写（临时文件 + os.replace）
- 安全/边界：纯标准库零依赖；无任何外部脚本/样式/字体引用（含 CSP 友好的内联资源）；
  只读本机文件，不写会话目录（快照除外）；服务只绑定回环地址
- 看板内置断言 32 → 34；新增 2 个测试（HTML 快照 CLI、127.0.0.1 服务 / 与 /data/404）

## 1.1.1 — 2026-10-12

代码审查后修复，重点是「假绿」与告警语义，全部有新增测试锁定。

**安装器 / 看板**

- git 解析三级回退：环境变量 `MULTI_AGENT_GIT` > PATH > Windows/macOS 常见安装路径
  （修「装了 Git for Windows 但终端 PATH 没有」时看板静默显示 worktree 0 个/✅ 无告警）
- git 仓库内 git 不可用时，看板显著告警且退出码 1（JSON 新增 `git_available` 字段）；
  非 git 目录不惩罚。doctor 区分「不是仓库」与「有 .git 但 git 调不动」
- `write_lf` 改为同目录临时文件 + `os.replace` 原子写，避免中途崩溃留下半个 exclude
- 已存在的非 UTF-8 exclude 给出显式警告且保留原字节，不再静默跳过、误报「已是最新」
- done / 超 24h 的历史会话不再永久抑制「未注册 worktree」告警（改由统一的
  `is_fresh_session` 判定，抑制口径与失活告警口径一致）
- 无有效心跳的会话超过 24h（按会话文件 mtime）降级为提示，不再永久卡住开工门禁
- inbox 关闭词复用会话关闭词表：`cancelled/abandoned/superseded/废弃` 等不再永远算 open

**测试 / CI / 文档**

- 端到端 16 → 19（git 不可用的仓库内外行为、done 会话 worktree 告警、非 UTF-8 exclude）；
  看板内置断言 24 → 32；README 用例计数改由 `test_readme_e2e_count_matches_suite` 自动核对
- 大批用例被 skip 时输出醒目 WARN，不再「全跳过却绿灯」
- CI 矩阵增加 Python 3.13；测试临时目录清理在 3.12+ 使用 `onexc`（`onerror` 已弃用）
- `install --dry-run` 结尾措辞区分「将写入」与「已写入」

## 1.1.0 — 2026-10-11

从真实多 agent 项目的两轮使用复盘后发布，重点是健壮性、worktree 与生命周期。

**安装器**

- 新增 `uninstall`：默认只摘除 ignore 托管块；`--purge-files` 仅删除与 kit 原件
  逐字节相同的文件，会话/日志/问事板/D 组条目/备份永不删除
- `.agents/install.json` 记录 `kit_version` 与项目名；`doctor` 比对版本并提示升级；
  linked worktree 安装时自动沿用主工作区项目名
- 故障手册的 **D 组项目专属条目**成为第三个自动迁移区（旧版无标记也能自动识别搬迁）
- linked worktree（`.git` 是文件）正确识别，私有/公共两处 `info/exclude` 都写
- 非 UTF-8（如 GBK）旧文件不再让安装崩溃：字节级备份 + 明确的无法迁移警告
- 备份统一到 `.agents/backups/`（天然被忽略，不污染 `git status`），同秒覆盖不互冲
- ignore 托管块支持残片清理；旧文件无定制区标记时给出显式警告
- 自举保护：拒绝把套件装进 kit 目录自身

**看板 `tools/agents_status.py`**

- 新增 `--prs`：只读列出 open PR（`gh` 不可用时降级提示，不影响退出码）
- 聚合**全部 linked worktree** 的 `.agents/`（同一会话 ID 取最新心跳）
- 在项目任意子目录运行自动探测仓库根（提示走 stderr，不污染 `--json`）
- 损坏/乱码/未来时间的心跳不再让看板崩溃，按失活处理并给可读提示
- 状态词改为精确匹配（"未完成"不再被当成"已完成"；`blocked` 正确计入待接手）
- 未注册 worktree 告警改用 `git status --porcelain` 口径：主工作区不自告警，
  切分支刷新 mtime 不再误报
- 超过 7 天的 open 问题降级为提示，不再常年卡住开工门禁
- inbox 日期同时兼容 `created` 与 `date` 字段

**规则与文档**

- AGENTS.md：长任务（>15 分钟）前更新心跳并写明预计回来时间；
  handoff 必须同时追加 worklog，消除中途交接无日志的真空
- 故障手册重排为 A/B/C 三组连续编号（A1–A6 / B1–B14 / C1–C9）+ D 组空区
- 「本机私有清单」以 `install.py:EXCLUDE_ENTRIES` 为唯一事实源，配一致性自测
- README 重写为落地页结构；新增"想共享 AGENTS.md"的边界取舍说明
- 新增 MIT LICENSE 与 GitHub Actions（3 系统 × Python 3.8/3.10/3.12）

**测试**

- 看板内置断言 14 → 24；端到端用例 8 → 16（测试总数 20；新增 GBK、D 组迁移、
  子目录、linked worktree、卸载安全、版本登记、自举保护、清单一致性等）
- 修复 Windows 下临时目录清理：git 只读对象 chmod 重试 + worktree 注销 +
  句柄延迟多轮重试，自测耗时 273s → 约 130s 且零残留

## 1.0.0 — 2026-10-09

首个通用版本。

- 零依赖安装器：`install` / `doctor` / `mirror`，幂等、定制区（项目门禁/项目速览）迁移
- 协作看板 `agents_status.py`：心跳失活、inbox、worklog、未注册 worktree 告警
- 三个 Markdown 模板（session / inbox / worklog）与三个平台入口
  （Trae 规则 / Claude 斜杠命令 / WorkBuddy 镜像）
- 故障手册 29 条通用经验（A/B/C 组）+ D 组写法范例（4 条嵌入式精华）
- 会话与问事板的真实填写范例
