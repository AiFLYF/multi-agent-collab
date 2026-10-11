# 会话文件填写范例（匿名化，来自真实协作）

> **这个文件是什么**：`templates/session.md` 是空模板，第一次用可能不知道"填出来长什么样"。
> 下面是一个**虚构但完全写实**的范例（一个 Web 后端项目的限流功能任务），
> 展示会话文件在任务全生命周期里怎么变化。安装器不会把本文件装进你的项目。

---

## 例 1：任务刚开始（注册时）

文件名：`.agents/sessions/trae-261009-1015-a1b2.md`

```markdown
# Session: trae-261009-1015-a1b2

- agent: trae
- branch: feature/login-rate-limit
- task: 登录接口加速率限制（内存令牌桶 + 429 响应）
- status: active
- started: 2026-10-09 10:15
- heartbeat: 2026-10-09 10:15
- pr:

## 当前进度

- 10:15 已 fetch，看板无活跃会话；open PR 只有 #42（支付模块，不相关）
- 已从 origin/develop 切出 feature/login-rate-limit
- 正在读 app/auth/login.py 与现有中间件结构

## 下一步

- 设计 RateLimiter 接口，先在 inbox 问一句是否有人在动 middleware.py

## 阻塞点 / handoff

- 无
```

## 例 2：任务进行中（只改心跳 + 进度，最新更新写最上面）

```markdown
- heartbeat: 2026-10-09 10:40

## 当前进度

- 10:40 RateLimiter 已实现（app/core/rate_limit.py），单测 12 条全绿
- 10:25 已在 inbox 与 trae-261009-0930-c3d4 确认 middleware.py 改动零重叠
- 正在接 login 路由，预计还要改 2 个文件

## 下一步

- 跑全量回归 pytest -q；绿了就提交、推分支、开 PR
```

## 例 3：要离开但任务没做完（handoff 必填，inbox 同步留交接）

```markdown
- status: blocked
- heartbeat: 2026-10-09 11:05

## 阻塞点 / handoff

- 代码与单测都完成了，但全量回归在 tests/test_webhook.py 连续 3 次超时，
  看起来与本改动无关（stash 后在干净 develop 上也超时），已在 inbox Q-2026-10-09-03 提问
- 未提交：无（工作区干净）；本地分支 feature/login-rate-limit 比 develop 多 2 个 commit，
  尚未 push
- 接手者：先看 inbox 那条问题；若 webhook 超时确认是环境问题，直接 push + 开 PR 即可，
  PR 描述草稿在分支最后一个 commit 的正文里
- 预计 14:00 回来
```

## 例 4：任务收尾（置 done 后不再更新心跳）

```markdown
- status: done
- heartbeat: 2026-10-09 11:30
- pr: https://github.com/<org>/<repo>/pull/47

## 当前进度

- 11:20 PR #47 已开（base=develop），全量回归 187 项全绿
- 11:30 worklog 已追加总结；本会话结束
```

---

## 要点回顾

- **文件名就是会话 ID**：`<agent>-<YYMMDD-HHMM>-<4位随机>`，不要改名
- **心跳只需"保存文件"**，不用 git；≤15 分钟或每完成一个阶段更新一次
- 最新进度永远写在 `## 当前进度` **最上面**，方便别人扫一眼就懂
- 离开时没做完：status 置 `blocked`，handoff 写清"未完成部分 / 未推送改动在哪 / 接手第一步"，
  并在 inbox 留一条点名接手方或 `to: any` 的问题
