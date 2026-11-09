# 平台接入与新对话验证

先选择当前项目使用的平台，再分别验证文件安装、客户端可用性和新对话实际读取。
平台短名称与入口清单以本地下载副本为准：

```text
python "<套件目录>/install.py" platforms --json
```

`<套件目录>` 是复用的源码目录；`<目标项目绝对路径>` 是另一个已有 Git 项目。
**禁止在套件源码目录自安装。** 首次下载与版本状态见 [README](../README.md#安装下载一次按项目复用)。

## 安装、追加和刷新

下面的安装器命令可从任意目录运行，先将占位符换成实际绝对路径：

```text
# 首次只安装当前项目需要的平台；可先加 --dry-run 预览
python "<套件目录>/install.py" install "<目标项目绝对路径>" --platforms codex,claude

# 后来开始用 Cursor：追加入口，保留先前选择
python "<套件目录>/install.py" sync "<目标项目绝对路径>" --platforms cursor

# 刷新全部已选入口；核心工具升级使用 update
python "<套件目录>/install.py" sync "<目标项目绝对路径>"
```

首次默认 Git Flow；以 main 为基线的项目加 `--workflow github-flow`，其它约定见 [分支策略](WORKFLOW.md#按项目选择分支策略)。
终端交互可省略 `--platforms` 后多选；AI 或脚本执行时明确传入。
`codex / opencode / pi` 原生共用根 `AGENTS.md`，只选这些平台不生成其它平台目录或 `CLAUDE.md`。

`install` 安装或升级核心模板，迁移定制区；`sync` 保留总章和核心工具，刷新入口、策略说明与忽略配置。
两者的 `--platforms` 都是追加；`none` 表示不新增平台，卸载需使用 `uninstall`。
完整更新命令与源码迁移见 [WORKFLOW.md](WORKFLOW.md#更新已安装的规则与工具)。

项目原有共享说明保留原文，已跟踪入口改动正常显示；新增私有入口按精确路径写入本机 exclude。
旧版按实际入口迁移平台选择，linked worktree 首装继承主工作区选择。
Git common exclude 在各 worktree 共用：私有入口取并集，也可能忽略其它工作区同名未跟踪文件；已跟踪文件不受影响。

旧核心使用新版源追加平台前先完整更新，再 `sync`；同步只刷新入口，不能补齐缺失的活动、消息或待机工具。能力不足时工具拒绝写入并给出更新指引。

## 如何确认真的生效

**先查文件，再查客户端，最后新开对话。** 三项结果分别记录，体检或版本查询不能证明规则已经加载。

```text
python "<套件目录>/install.py" doctor "<目标项目绝对路径>"
python "<套件目录>/install.py" clients "<目标项目绝对路径>" --versions --json
```

`doctor` 检查安装文件、入口、镜像一致性与 Git 忽略，出现 WARN 时说明原因并处理。
`clients` 默认使用安装记录中的平台；无记录时加 `--platforms <名称>`。
省略 `--versions` 只查 PATH，加上后运行固定版本命令，每项最多等待 12 秒，不启动模型或改全局配置。
IDE/应用内平台显示手动验证；检查各项 `probe_status`。
返回 0 只表示检查完成，`session_verified` 仍为 false，不能当成新对话验收。

在**目标项目**新开所选客户端的对话，使用：

```text
请先只读检查当前项目的协作入口。
读取项目根 AGENTS.md 和协作启动入口，运行 Git 状态与当前平台的协作摘要，
告诉我实际读取的文件、当前分支和策略、未结束占用、告警，
以及发给当前平台、会话或 any 的未答 inbox。不要登记写任务。
```

新版启动入口为 `.multi-agent-collab/START_HERE.md`，摘要命令在项目根运行：

```text
python .multi-agent-collab/tools/agents_status.py --context --agent <平台短名称>
```

旧安装按根入口使用 `.agents/START_HERE.md` 与项目根 `tools/`。
应看到实际文件读取、工具调用和对应结果；只回答“我会先看看板”不算验收。
若未加载，依次核对工作区、客户端规则开关、入口优先级与文档大小限制，再新开对话。
Codex 官方默认指令大小上限为 32 KiB；本套件不修改该配置。

## 自动读取如何工作

1. 客户端发现项目级规则文件。
2. 平台入口导入或要求读取根 `AGENTS.md` 与协作 `START_HERE.md`，再按启动顺序加载总章。
3. agent 运行动态摘要，读取相关事项，写入前按 [WORKFLOW.md](WORKFLOW.md) 检查并登记范围。
4. 已登记会话的业务调用携带自身平台与 ID 记录活动；最终答复前完成实际验证和收尾，默认待机等待新任务。

`AGENTS.md` 保留项目规则，`RULES.md` 管协作，`START_HERE.md` 管阅读顺序。
相同静态内容每会话只读一次；开工、上下文恢复和任务切换重新检查动态状态，历史与故障手册按需读取。
摘要预算与未读路径处理见 [TOKEN_BUDGET.md](TOKEN_BUDGET.md)。

| 平台 | 本套件的接入方式 | 验证要点 |
|---|---|---|
| Codex | 根 `AGENTS.md` | 从正确工作区启动；同目录 `AGENTS.override.md` 可优先于 AGENTS；还受文档大小上限影响 |
| Claude Code | `CLAUDE.md` 的 `@AGENTS.md`、`@.multi-agent-collab/START_HERE.md` 导入 | 显式导入，兼容旧版本和已有 CLAUDE 文件影响发现的场景；`/git-flow` 是手动命令 |
| OpenCode / Pi | 根 `AGENTS.md` | 确认启动目录与当前客户端的规则发现设置 |
| Trae | `.trae/rules/git-flow-agent.md`，`alwaysApply: true` | 确认项目规则开启；Trae IDE 与其它同名 CLI 不应混为一种客户端 |
| WorkBuddy | `.workbuddy-ai/git-flow-agent.md` 简短入口 | 需确认当前客户端确实加载此项目入口；修改总章后执行 sync |
| Cursor | `.cursor/rules/multi-agent-collab.mdc`，`alwaysApply: true` | 使用明确的项目规则入口；本文件指向最新共享规则 |
| GitHub Copilot | `.github/copilot-instructions.md` | 需在支持此文件的客户端启用项目自定义说明；已有说明保留在托管块外 |
| Windsurf | `.windsurf/rules/multi-agent-collab.md`，`trigger: always_on` | 使用明确的始终启用入口；当前客户端也可能原生支持 AGENTS |
| Gemini CLI | `GEMINI.md` 使用 `@./` 导入 | 针对采用 GEMINI.md 的客户端；若自定义了 context.fileName，需确认仍包含此入口 |
| Qwen Code | `.qwen/QWEN.local.md` | 使用官方本机入口，在团队 QWEN.md 之后加载；不改写团队规则或全局配置 |
| Cline | `.clinerules/multi-agent-collab.md` | Rules 面板可控制启用；旧单文件 .clinerules 与目录冲突时安装会提前停止 |
| Roo Code | `.roo/rules/00-multi-agent-collab.md` | 通用规则覆盖所有模式；规划先只读，同任务切换模式沿用会话 ID |
| Continue | `.continue/rules/multi-agent-collab.md`，alwaysApply | Chat / Plan 先检查，可写 Agent 模式再登记文件范围 |
| Kiro | `.kiro/steering/multi-agent-collab.md`，inclusion: always | Spec 文档修改也登记；本机状态不自动进入云端独立工作区 |

Claude 保留显式 AGENTS/START 导入，总章由 START 加载，避免重复导入。
上述是本套件选择的入口；客户端规则发现机制会随版本变化，需要按前面的新对话步骤验收。
安装器不改全局设置，也不强制启用客户端被关闭的规则开关。
规则和本地消息不能自动唤醒待机或结束的会话，使用看板「复制提醒」在对应对话中继续。
待机仅保留身份、不占文件；新任务用 activate 重新登记 task 和完整范围，done 终态则需要新 ID。

### 验证活动与自动收尾

在隔离测试项目中给客户端一个明确的小任务，核对它实际登记的 ID、正常工具调用携带的身份、真实测试输出及最后的 finish --standby 返回。
完成后应看到 standby 和空范围；再给新任务，应先 activate 检查新分支、指令与完整范围，而不是直接继续写代码。
受阻或未完成时应 handoff 保留占用；不能把模型口头说“待机”当成记录已更新。
提示词提供执行要求，能否稳定遵循仍须在所用客户端版本中新开对话验收。原生 shell 没有可验证会话身份时，使用 run 包装记录活动；套件不擅自改客户端全局钩子。

平台标签也接受 `Gemini CLI`、`qwen-code`、`roo-code`、`GitHub Copilot` 等常见名称；
安装记录统一保存短名称。既有对话需要主动重读变化部分，修改总章后用 `sync` 刷新入口。

## 官方入口参考

参考：[Codex AGENTS.md](https://developers.openai.com/codex/guides/agents-md/)、
[Claude Code memory](https://code.claude.com/docs/en/memory)、
[Cursor rules](https://cursor.com/docs/rules)、
[Copilot 自定义说明](https://docs.github.com/en/copilot/customizing-copilot/adding-repository-custom-instructions-for-github-copilot)、
[Windsurf rules](https://docs.windsurf.com/windsurf/cascade/memories)。

新增平台参考：[Gemini context](https://geminicli.com/docs/cli/gemini-md/)、
[Qwen memory](https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/memory.md)、
[Cline rules](https://docs.cline.bot/customization/cline-rules)、
[Roo instructions](https://roocodeinc.github.io/Roo-Code/features/custom-instructions/)、
[Continue rules](https://docs.continue.dev/customize/deep-dives/rules)、[Kiro steering](https://kiro.dev/docs/steering/)。

## 为套件增加一个平台

以下是**套件源码开发**步骤：

1. 核对平台官方项目规则入口、启用条件和文件格式。
2. 在 `install.py` 的 `PLATFORM_LABELS` 登记名称；原生平台加入 `NATIVE_PLATFORMS`，其它平台在 `platforms/` 添加共享规则入口并登记 `PLATFORM_FILES`。
3. 将精确入口加入 `EXCLUDE_ENTRIES`，保留项目目录；更新总章与平台说明。
4. 补实际安装、选择迁移、同步、忽略与卸载测试，并单独验收该客户端新对话。
5. 运行 `python scripts/sync_install_docs.py` 更新 README 平台表、提示词及页面数据，再运行 `--check` 验证同步。
