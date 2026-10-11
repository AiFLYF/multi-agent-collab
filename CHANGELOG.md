# 更新日志

本项目版本号写在 `install.py` 的 `KIT_VERSION`，安装时登记到目标项目的
`.agents/install.json`，`doctor` 据此提示升级。
版本号遵循语义化版本（major.minor.patch）。

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

- 看板内置断言 14 → 24；端到端用例 8 → 20（新增 GBK、D 组迁移、子目录、
  linked worktree、卸载安全、版本登记、自举保护、清单一致性等）
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
