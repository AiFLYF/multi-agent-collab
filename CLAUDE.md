# CLAUDE.md（本机文件，不入库；见 AGENTS.md 第 8 节）

本工作区的完整工作规则在 [AGENTS.md](AGENTS.md)（Git Flow 分支铁律 + 多 agent 本机共享记忆协议），
开始任何任务前必须先读：

@AGENTS.md
@.agents/START_HERE.md

每次新对话先执行启动入口的阅读顺序，重新检查动态的看板、inbox 与近期 worklog。
这里只导入共享规则，不另存一份规则副本。

## 项目速览

<!-- >>> mac:zone:project-overview 项目定制区开始：install.py 升级本文件时会自动保留这两行标记之间的内容，请整体替换为你的项目信息，但不要删标记 -->

> ⚠️ **安装后第一件事：把本节整体替换成你的项目内容。** 与 AGENTS.md 第 6 节保持一致，
> 这里只放最常用的一两条速查。按你的技术栈删改：

- 运行时：<如 Python 3.12 / Node 20 / Go 1.22 / JDK 17 / Rust 1.80>
- 测试门禁：<如 `pytest -q` / `npm test` / `go test ./...` / `cargo test`>
- 构建/启动：<如 `npm run build`、`npm run dev` / `make run` / 无需构建>
- 本地调试：<如 mock 数据命令、无外部依赖的演练入口；没有可写"无">

<!-- <<< mac:zone:project-overview -->

## 协作体系（这部分是通用的，别改）

- `.agents/`：多会话**本机**共享记忆（sessions / worklog / inbox，不入库）
- 看板：`python tools/agents_status.py` —— **开工第一步**
- 斜杠命令：`/git-flow <任务描述>`
