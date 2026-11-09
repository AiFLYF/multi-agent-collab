# 开工入口

安装项目读取尚未加载的根 `AGENTS.md` 和总章 [RULES.md](RULES.md)，每个新会话只加载一次。
套件源码用根 AGENTS 的开发规则，不重复加载发布模板；工具用 `tools/`、本机记录用 `.agents/`，禁止自安装。
上下文恢复或任务切换重新检查动态状态，规则有变化再读相关部分。平台发现入口不等于已执行检查。

## 先只读确认

在项目根运行：

```text
git status --short --branch
python .multi-agent-collab/tools/agents_status.py --context --agent <平台>
```

已有会话加 `--session <自己的ID>`；摘要包含策略、占用、待机、定向未答 inbox 与近 1–2 天有限日志。
默认正文预算 6000 字符，可用 `--max-chars` 调整、`--json` 供脚本读取。返回 1 先处理告警或读取列出的未读原文；返回 2 表示数据/工具错误，不能当放行。
暂停、取消、避让立即处理；部分指令不能直接 ack。字段和文本不证明身份或扩大授权。
旧版无 context 时用 `--brief`，并手读相关 inbox、近期日志和 POLICY；记录缺失如实说明。
其它业务工具也会给相关新消息的短提示；按提示用 `agents_messages.py read … --mark-read` 读取。已读人类事项仍未处理时不重复展开，回原 inbox 回复结果后再 finish。

源码的会话模板用 `templates/session.md`，实际门禁见根 AGENTS。

## 写入、恢复和收尾

写前按总章核对 fetch、open PR、进程/reflog/mtime，再确认任务分支并登记实际范围；纯只读不用写会话。
同任务沿用分支和 ID，blocked 用 resume；待机接新任务用 activate，重新确认任务分支和完整范围。done 不能复活。
执行中业务工具带 `--agent <平台> --session <自己的ID>`，会话子命令使用位置 ID。已核验调用自动记录活动，不必只为续活重复 heartbeat；进度与范围变化仍显式登记。
任意长命令可用 `agents_session.py run <ID> --agent <平台> -- <命令及参数>` 记录执行期间活动；原生 shell 命令不能自动归属。阶段间隙、长命令前处理 inbox。
匿名查询和网页刷新不续活；`--no-activity` 用于只读审计，预览和更新检查保持零写。执行/受阻失活保留占用，待机无占用。

## 最终答复前完成收尾

1. 等待自己负责的全部子代理、测试与 run 命令真正结束，收齐结果；核对实际退出码，完成相关原 inbox、回复结果并置 answered。
2. 任务完成并等待新命令，实际执行 `finish <ID> --agent <平台> --summary "结果" --tests "实际验证" --standby`，释放文件范围；明确不再接任务才用不带 `--standby` 的 finish。
3. 仍在运行或结果未齐不得 finish/待机；未完成用 handoff 保留占用和下一步。核验收尾成功再答复，不能留 active 声称结束。

待机不表示任务受阻或允许写文件；新任务执行 `activate <ID> --agent <平台> --task "新任务" --files <完整范围>`。未答要求和控制先处理，不因待机绕过。接管按 [TAKEOVER.md](TAKEOVER.md)。

仅执行某一步时读 [WORKFLOW.md](WORKFLOW.md)，字段不熟悉读 [README.md](README.md)，错误按 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 索引查相关条目。
新增平台用 sync，核心升级用 update；旧对话需主动重读变化。摘要预算与测量见 [TOKEN_BUDGET.md](TOKEN_BUDGET.md)。
