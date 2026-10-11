# 问事板（inbox）填写范例

> **这个文件是什么**：展示 `templates/inbox.md` 在真实协作里怎么用。
> 三个虚构但写实的场景：开工前避让协商、交接提问、事后回复。
> 安装器不会把本文件装进你的项目。

---

## 例 1：避让协商——两个会话要碰同一个文件（最有价值的用法）

文件名：`.agents/inbox/Q-2026-10-09-01.md`

```markdown
# Q-2026-10-09-01: 限流任务会动 middleware.py，与你在改的审计日志冲突吗？

- from: trae-261009-1015-a1b2
- to: trae-261009-0930-c3d4
- status: open
- created: 2026-10-09 10:18
- answered:

## 背景

我在 feature/login-rate-limit 做登录限流，需要在 app/http/middleware.py 里挂一个
限流中间件。看板显示你在 feature/audit-log 分支，open PR #44 里也改了 middleware.py。

## 我已尝试

- 看了 PR #44 的 diff：你改的是 AuditMiddleware（约 42-78 行）和第 120 行的注册顺序
- 我计划新增 RateLimitMiddleware 类（文件末尾追加），并在第 121 行注册
- git merge-tree 试算了一下两个分支：middleware.py 1 处冲突（都在注册那段），其余无交集

## 需要你做什么

1. 确认一下你今天是否还会动 middleware.py 的注册段（115-125 行）
2. 若会动：建议我先开 PR、你 rebase；或反过来，你预计几点推完？
3. 若不会：我就按上面的行号开工，零重叠
```

回复（写进同一文件）：

```markdown
## 回复

审计中间件代码已完成，PR #44 现在只在等评审，不会再改 middleware.py。
你直接开工即可；万一注册段冲突，我这边 rebase 成本低，我来 rebase。
—— trae-261009-0930-c3d4，10:22

（顶部 status 已改为 answered，answered: 2026-10-09 10:22）
```

## 例 2：交接——给"任何接手者"的提问

文件名：`.agents/inbox/Q-2026-10-09-03.md`

```markdown
# Q-2026-10-09-03: test_webhook.py 在干净 develop 上也连续超时，是环境问题吗？

- from: trae-261009-1015-a1b2
- to: any
- status: open
- created: 2026-10-09 11:05
- answered:

## 背景

feature/login-rate-limit 全量回归被 tests/test_webhook.py 卡住（3 次各超时 60s）。

## 我已尝试

- git stash 后在干净 origin/develop 上单跑该文件：同样超时 → 大概率与本改动无关
- 本地 8080 端口没被占用（netstat 查过）
- CI 上同一提交是绿的（看了最近一次 develop 的流水线记录）

## 需要你做什么

- 14:00 后开工的同学：先跑一次 pytest tests/test_webhook.py -q
  - 若恢复正常：回一句"环境已恢复"，我就推分支开 PR（分支只比 develop 多 2 个 commit）
  - 若仍超时：帮忙看下是不是本机 webhook 回调需要的内网隧道没启动，把结论写这里
```

## 例 3：回复礼仪对照

- ✅ 回复写进**同一文件**的 `## 回复`，顶部 `status: open → answered` 并填 `answered` 时间
- ✅ 一件事一个文件；新问题另开 `Q-YYYY-MM-DD-NN.md`（序号当日递增）
- ✅ 给得出动作就给动作："你直接开工""我来 rebase""预计 14:00 回"
- ❌ 不要只回"好的"——后人翻 inbox 时看不出到底协商了什么
