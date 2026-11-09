# 会话全生命周期示例

这是教学示例，不会安装到项目。命令在目标项目根目录运行，`<ID>` 使用 start 实际返回的值；不要复制示例时间或编造心跳。
旧安装按实际入口将 `.multi-agent-collab/tools/` 换成项目根 `tools/`；套件源码禁止自安装。

## 新任务：先协调，后登记

在已有 Git Flow 基线的项目里，先读总章与启动清单，确认没有冲突和重复 PR，再建立 feature 分支。

```text
python .multi-agent-collab/tools/agents_session.py start --agent codex --task "增加登录限流" --files app/core/rate_limit.py tests/test_rate_limit.py --json
```

返回的 ID 对应一个本机会话文件，关键字段形如：

```markdown
# Session: codex-261014-1000-a1b2

- agent: codex
- branch: feature/login-rate-limit
- task: 增加登录限流
- files: ["app/core/rate_limit.py", "tests/test_rate_limit.py"]
- status: active
- heartbeat: 2026-10-14 10:00
```

## 扩大范围：先检查再动手

后来需要修改登录路由，提交完整范围：

```text
python .multi-agent-collab/tools/agents_session.py heartbeat <ID> --agent codex --files app/core/rate_limit.py tests/test_rate_limit.py app/auth/login.py --note "准备接入登录路由"
```

若别人占用了路由文件，命令失败，原范围保留。此时先协商，不删除旧记录，也不把文件漏写在列表里。
工具缺失时按会话模板手工登记实际 active 与完整非空范围，并提示升级；工具存在但门禁失败不能手改字段绕过。

## 普通命令顺带记录活动

```text
python .multi-agent-collab/tools/agents_status.py --context --agent codex --session <ID>
python .multi-agent-collab/tools/agents_messages.py read --agent codex --session <ID>
python .multi-agent-collab/tools/agents_session.py run <ID> --agent codex -- python -m pytest -q
```

前两条已核验业务调用节流更新活动；run 记录测试前、期间和结束后的活动，检查实际测试退出码。
不需要只为续活额外循环 heartbeat；直接原生 shell 命令没有可靠会话归属。只读审计加 `--no-activity`。

## 受阻与恢复

```text
python .multi-agent-collab/tools/agents_session.py handoff <ID> --agent codex --summary "限流逻辑完成；回归因测试服务不可达受阻；未提交文件为 app/core/rate_limit.py 和 tests/test_rate_limit.py；服务恢复后先重跑全量测试"
python .multi-agent-collab/tools/agents_session.py resume <ID> --agent codex --note "测试服务恢复，继续原任务"
```

handoff 保留 blocked 占用，并把交接路径写入会话。resume 保留同一个 ID、重新检查占用，
恢复 active 并回执该交接条。它不会自动解决普通业务问题，也不会把失败测试视为通过。

## 测试与收尾

先运行项目约定的测试；任何门禁失败都先修复或说明阻塞，不提交、不推送、不用 rebase 改写历史。
测试通过、相关原 inbox 已回复完成后，按用户授权交付；仅本地授权就保留本地改动，不擅自提交或推送。
最终答复前实际执行收尾并核验成功，继续等待新任务时用：

```text
python .multi-agent-collab/tools/agents_session.py finish <ID> --agent codex --summary "登录限流已实现，本地交付位置见本次记录" --tests "pytest -q：填写实际退出码与通过数量" --standby
```

`--tests` 是实际结果的记录，不会替你运行测试。最终答复前须等自己负责的全部子代理、测试和 run 命令结束并收齐结果，
核对真实退出码与原 inbox；活着或无法核验的 running_jobs 会阻止收尾及范围转换。
finish --standby 成功后追加日志、回执自己的交接条，清空范围并保留对话 ID。
状态、心跳、身份和测试文本不证明模型仍在执行、身份可信、测试通过或用户授权。
未完成、受阻或暂停的任务须 handoff，不能改成 standby 释放范围。

## 待机接收新任务

standby 无文件占用，普通调用不会恢复 active。人类下达新任务后，先确认策略、任务分支和相关指令，再用完整新范围登记：

```text
python .multi-agent-collab/tools/agents_session.py activate <ID> --agent codex --task "完善登录审计" --files app/auth/audit.py tests/test_audit.py
```

存在相关未答人类要求时先完整读取并纳入，通过 `--ack-inbox` 列出全部原文件。activate 成功后才可写入。
明确不再接任务时使用不带 --standby 的 finish 结束对话身份；done 不能复活，下一次新对话建立新 ID。
