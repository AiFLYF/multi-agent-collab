# 会话全生命周期示例

这是教学示例，不会安装到项目。命令中的 `<ID>` 使用 start 实际返回的值；不要复制示例时间或编造心跳。

## 新任务：先协调，后登记

在已有 Git Flow 基线的项目里，先读总章与启动清单，确认没有冲突和重复 PR，再建立 feature 分支。

```text
python tools/agents_session.py start --agent codex --task "增加登录限流" --files app/core/rate_limit.py tests/test_rate_limit.py --json
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
python tools/agents_session.py heartbeat <ID> --agent codex --files app/core/rate_limit.py tests/test_rate_limit.py app/auth/login.py --note "准备接入登录路由"
```

若别人占用了路由文件，命令失败，原范围保留。此时先协商，不删除旧记录，也不把文件漏写在列表里。

## 受阻与恢复

```text
python tools/agents_session.py handoff <ID> --agent codex --summary "限流逻辑完成；回归因测试服务不可达受阻；未提交文件为 app/core/rate_limit.py 和 tests/test_rate_limit.py；服务恢复后先重跑全量测试"
python tools/agents_session.py resume <ID> --agent codex --note "测试服务恢复，继续原任务"
```

handoff 保留 blocked 占用，并把交接路径写入会话。resume 保留同一个 ID、重新检查占用，
恢复 active 并回执该交接条。它不会自动解决普通业务问题，也不会把失败测试视为通过。

## 测试与收尾

先运行项目约定的测试；任何门禁失败都先修复或说明阻塞，不提交、不推送、不用 rebase 改写历史。
测试通过后，按总章只暂存本任务文件、提交、复核 SHA、推送并建立 PR，再结束会话：

```text
python tools/agents_session.py finish <ID> --agent codex --summary "登录限流已实现并完成评审准备，PR 地址见本次工作记录" --tests "pytest -q：实际通过数量填写在这里"
```

`--tests` 是实际结果的记录，不会替你运行测试。finish 追加日志、回执自己的交接条并释放占用。
终态记录保留；下一项新任务建立新 ID。
