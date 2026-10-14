# 平台接入与新对话验证

平台名称和入口清单以 `python install.py platforms` 为准，机器读取用 `platforms --json`。
本页解释加载方式、同步边界与扩展方法。

## 自动读取如何工作

1. 平台启动会话时，发现项目级规则文件。
2. 平台入口导入或要求读取项目根 `AGENTS.md` 和 `.agents/START_HERE.md`。
3. agent 按启动清单重新读取动态的看板、inbox 和近 1–2 天 worklog，并在编码前注册会话。

`AGENTS.md` 管规则，`START_HERE.md` 管阅读顺序，动态记录按需读取。
不用在每个平台保存整套历史，也不用每次读取源码和全部故障手册。
规则文件是指导机制，不是进程钩子；它不能唤醒已结束的 agent，也不能保证模型执行每条要求。

| 平台 | 本套件的接入方式 | 验证要点 |
|---|---|---|
| Codex | 根 `AGENTS.md` | 从正确工作区启动；同目录 `AGENTS.override.md` 可优先于 AGENTS；还受文档大小上限影响 |
| Claude Code | `CLAUDE.md` 的 `@AGENTS.md`、`@.agents/START_HERE.md` 导入 | 显式导入，兼容旧版本和已有 CLAUDE 文件影响发现的场景；`/git-flow` 是手动命令 |
| OpenCode / Pi | 根 `AGENTS.md` | 确认启动目录与当前客户端的规则发现设置 |
| Trae | `.trae/rules/git-flow-agent.md`，`alwaysApply: true` | 确认项目规则开启；Trae IDE 与其它同名 CLI 不应混为一种客户端 |
| WorkBuddy | `.workbuddy-ai/git-flow-agent.md` 兼容镜像 | 需确认当前客户端确实加载此项目入口；修改总章后执行 sync |
| Cursor | `.cursor/rules/multi-agent-collab.mdc`，`alwaysApply: true` | 使用明确的项目规则入口；本文件指向最新共享规则 |
| GitHub Copilot | `.github/copilot-instructions.md` | 需在支持此文件的客户端启用项目自定义说明；已有说明保留在托管块外 |
| Windsurf | `.windsurf/rules/multi-agent-collab.md`，`trigger: always_on` | 使用明确的始终启用入口；当前客户端也可能原生支持 AGENTS |
| Gemini CLI | `GEMINI.md` 使用 `@./` 导入 | 针对采用 GEMINI.md 的客户端；若自定义了 context.fileName，需确认仍包含此入口 |
| Qwen Code | `.qwen/QWEN.local.md` | 使用官方本机入口，在团队 QWEN.md 之后加载；不改写团队规则或全局配置 |
| Cline | `.clinerules/multi-agent-collab.md` | Rules 面板可控制启用；旧单文件 .clinerules 与目录冲突时安装会提前停止 |
| Roo Code | `.roo/rules/00-multi-agent-collab.md` | 通用规则覆盖所有模式；规划先只读，同任务切换模式沿用会话 ID |
| Continue | `.continue/rules/multi-agent-collab.md`，alwaysApply | Chat / Plan 先检查，可写 Agent 模式再登记文件范围 |
| Kiro | `.kiro/steering/multi-agent-collab.md`，inclusion: always | Spec 文档修改也登记；本机状态不自动进入云端独立工作区 |

Claude Code 官方文档现已说明：v2.1.277 起，符合条件且没有其它项目 CLAUDE 文件时，
也能直接发现 AGENTS。本套件保留显式导入，便于兼容不同版本与项目现有配置。
客户端的规则发现机制会变化。上述是本套件选择的入口，不代表列出的入口是平台唯一支持的方式。
安装器不会改动用户的全局设置，也不会强制启用被用户关闭的规则加载开关。

## 安装、追加和刷新

```bash
# 首次仅启用当前项目需要的平台
python "$HOME/multi-agent-collab/install.py" install . --platforms codex,claude

# 新增 Cursor，保留先前的选择和项目总章
python "$HOME/multi-agent-collab/install.py" sync . --platforms cursor

# 刷新已选入口（包括 WorkBuddy 镜像）
python "$HOME/multi-agent-collab/install.py" sync .
```

`install` 升级核心模板并保留定制区；`sync` 刷新已选入口，保留整个项目总章及已有启动文档。
两者都维护 Git 本地忽略、先备份再覆盖，可用 `--dry-run` 预览。
`--platforms` 是追加式选择，`none` 只表示不新增平台，不负责卸载既有入口。
旧版未保存选择时，安装器根据实际入口迁移；linked worktree 首装时继承主工作区选择。

## 如何确认真的生效

可先查询项目已选客户端的 CLI：

```text
python <套件目录>/install.py clients <项目>
python <套件目录>/install.py clients <项目> --versions --json
```

不加 --versions 只查 PATH；加上后运行固定的版本命令，不启动模型或修改全局配置。
没有安装记录时用 --platforms 指定检查范围。IDE/应用内平台显示手动验证，不会误报“未安装”。
输出只保留版本与状态，不保存原始 stdout/stderr；session_verified 始终为 false，需要下面的实际新对话检查。
命令返回 0 表示检查已完成，不表示每个平台可用；检查各条目的 probe_status。

先执行 `doctor` 检查文件、镜像一致性和 Git 忽略，再新开对话使用：

```text
请读取当前项目的协作启动入口，告诉我你实际读了哪些规则文件、
当前分支是什么、看板是否有告警，以及发给当前会话或 any 的未答 inbox 有哪些。
先只做只读检查。
```

应看到实际工具读取和对应结果。只回答“我会先看看板”不能证明已加载。
若未生效，检查工作区、规则开关、入口优先级、上下文上限，再重启会话。
Codex 官方说明：默认指令大小上限为 32 KiB，按全局到项目目录顺序合并；本套件不修改此配置。

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

平台标签也接受常见名称，例如 `Gemini CLI`、`qwen-code`、`roo-code` 和 `GitHub Copilot`，
安装记录统一保存短名称。新协作命令随 `install` 升级；`sync` 不会擅自替换旧项目的核心工具。

## 为套件增加一个平台

1. 先核对该平台官方支持的项目规则入口、启用条件与文件格式。
2. 在 `install.py` 的 `PLATFORM_LABELS` 登记名称；原生使用总章的加入 `NATIVE_PLATFORMS`，
   否则在 `platforms/` 添加只指向共享规则的入口，并登记 `PLATFORM_FILES`。
3. 将新增的精确入口路径加入 `EXCLUDE_ENTRIES`；不要忽略整个 `.github/` 或编辑器目录。
4. 更新总章与平台说明的私有路径记录，补真实安装、同步、忽略与卸载测试。
5. 运行 `python scripts/sync_install_docs.py`，自动更新 README 平台表、提示词及页面数据。
   CI 的 `--check` 会检查是否漏同步。
