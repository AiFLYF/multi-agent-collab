# 每次新对话，从这里开始

这是当前项目的开工入口。新建对话时执行一次；任务阶段间隙重扫 inbox、更新自己的心跳。
平台入口会加载或指向根目录 `AGENTS.md`。如果它尚未进入上下文，先完整读取一次，随后读取本文件。
旧对话不会因为安装或同步自动重新加载规则，需要新开对话，或明确要求重新读取。

## 最小阅读顺序

1. 确认当前工作区和分支，遵守根 `AGENTS.md`。不要默认其他会话已替你检查。
2. `git fetch origin --prune`，然后 `python tools/agents_status.py --brief`。
   返回 1 再展开完整看板；命令失败按 `.agents/TROUBLESHOOTING.md` 排查。
3. 读 `.agents/inbox/` 中发给当前平台、当前会话或 `any` 的未答条目；读近 1–2 天 worklog。
   这些文件会变化，每个新对话都要重新读。无文件就跳过，不能把“没有记录”当作“没有人在工作”。
4. 查看开放 PR，结合进程、reflog 和文件时间确认没有同分支、同文件冲突。
5. 编码任务开始写入前，按 `AGENTS.md` 建任务分支，然后运行：

   ```text
   python tools/agents_session.py start --agent <当前平台> --task "任务" --files <相对文件或目录> --json
   ```

   保存返回的会话 ID。若同工作区、分支或文件范围已被占用，停止并协调；不能重写别人的记录。
   旧版没有此工具时，用 `.agents/sessions/_template.md` 手动登记并提示升级核心工具。

## 执行期间与收尾

```text
python tools/agents_session.py heartbeat <会话ID> --agent <当前平台> --note "当前进度"
python tools/agents_session.py finish <会话ID> --agent <当前平台> --summary "结果" --tests "实际测试结果"
python tools/agents_session.py handoff <会话ID> --agent <当前平台> --summary "下一步、阻塞点和未提交文件"
```

阶段间隙和长命令前仍需重扫 inbox；心跳不能证明已处理人类指令。
扩大文件范围时先给 heartbeat 加 `--files` 声明完整新范围，再修改文件。
finish 追加日志并释放占用；handoff 写交接留言、保留 blocked 占用，等待明确接管。
文件范围只是协作约定，系统锁只保护登记过程，不锁住源码，也不能阻止绕过协议的程序。

共享格式不熟悉时再读 `.agents/README.md`；发生故障时再查手册相应条目。
获取现状优先跑命令，不要每个新对话都读安装器、看板源码或全部历史日志。

## 新增平台与同步

各平台读取同一个 `AGENTS.md` 和本入口；更新这两份文档后，新对话读取的是最新内容。
WorkBuddy 的完整镜像需要同步；新增平台也使用同一条命令：

```text
python "<套件目录>/install.py" sync "<项目目录>" --platforms cursor
python "<套件目录>/install.py" sync "<项目目录>"
```

从 `.agents/install.json` 的 `source_dir` 找到套件目录；默认是用户主目录下的
`multi-agent-collab`。`sync` 追加平台、刷新入口和忽略规则，保留项目总章与协作记录；
更新套件核心模板要运行 `install`。无需常驻服务，不会自动下载代码或修改平台的全局配置。

安装器只能验证入口路径、内容和忽略规则。平台版本、工作区信任设置及用户关闭规则加载等因素
仍会影响自动读取。新对话验证时，确认 agent 能说明当前分支、看板结果及 inbox，而不只是复述口号。
