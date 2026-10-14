# 每次新对话，从这里开始

本文件是当前项目的最短启动清单。先读取根 `AGENTS.md`，再读取这里；已在上下文中的相同规则不用反复全文读取。
平台发现入口不等于已经执行检查。新对话、上下文恢复或任务切换后，都要重新核对动态状态。

## 先只读确认

1. 确认工作区与分支，运行 `git status --short --branch` 和 `python tools/agents_status.py --brief`。
   看板返回 1 时展开完整版并查看原因，不能把“无活跃”当作“无人占用”。
   读取 `.agents/POLICY.md` 的项目分支策略；旧安装缺失时按总章的 Git Flow，并在升级后生成策略说明。
2. 读取发给当前平台、自己的会话或 `any` 的未答 inbox，以及近 1–2 天 worklog。
   收到暂停、取消或避让要求先处理；缺文件就说明，不编造记录。
3. 判断这是只读咨询、新写任务，还是当前任务继续。只读任务不用登记写会话。
   继续同一任务时沿用自己的 ID；不要每次追问都创建新会话。

## 即将写入时

按总章完成 fetch、PR 与进程/reflog 交叉检查，确认任务分支及文件范围，再登记：

```text
python tools/agents_session.py start --agent <平台> --task "任务" --files <相对文件或目录> --json
```

保存实际返回的 ID。有占用或未答人类指令时先协调。
人类指令本身就是待执行任务时，先读并回执，再用 `--ack-inbox <指令文件名>` 声明纳入计划；
保留 open，实际完成后才 answered。这个参数不能绕过暂停、取消或避让要求。
分支基线缺失、没有远端或 GitHub 不适用时，说明具体情况；不擅自创建远端基线或绕过主干保护。
旧版没有会话工具时可用 `.agents/sessions/_template.md` 手动登记，并提示升级。

## 执行、恢复与收尾

```text
python tools/agents_session.py heartbeat <ID> --agent <平台> --note "当前进度"
python tools/agents_session.py handoff <ID> --agent <平台> --summary "完成项、下一步、阻塞和文件位置"
python tools/agents_session.py resume <ID> --agent <平台> --note "继续原任务的依据"
python tools/agents_session.py finish <ID> --agent <平台> --summary "实际结果" --tests "实际测试命令与结果"
```

- 每个阶段、长命令前重扫 inbox；每 15 分钟内或每阶段更新心跳。
- 扩大修改范围前，用 heartbeat 的 `--files` 提交**完整新列表**，检查成功后再修改。
- blocked 保留占用；恢复原任务用 resume。正常收尾用 done；失联接管按 [TAKEOVER.md](TAKEOVER.md) 收回为 closed，不能复活已结束的记录。
- resume / finish 回执自己的交接条，普通问题仍需在原文件里回复。
- 测试失败不能提交或推送；预览和静态检查不能替代实际运行。

操作细节、状态选择和例外见 [WORKFLOW.md](WORKFLOW.md)；字段格式见 [README.md](README.md)；
出错按 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 查询。只读相关段落，不扫描全部历史和工具源码。

## 同步入口

新增平台或刷新 WorkBuddy 镜像用 `python "<套件目录>/install.py" sync "<项目>" --platforms <新增平台>`；
没有新增平台时省略参数。sync 同时刷新项目策略说明；核心工具和规则升级使用 install，实际套件路径记录在 install.json 的 source_dir。
已有对话不会因为文件变化就自动重新载入；需要新开对话或明确重读。
系统锁只保护登记更新，不能锁住源码；规则是否被客户端加载仍需实际新会话验收。
