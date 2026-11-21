# 自动化、定时任务与 REST API

> 若有差异，请以[英文版](../../../README.md)为准。

[中文首页](../README.md) · [上一指南](getting-started.md) · [下一指南](architecture.md)

---

## GitHub + Claude Code 自动化

### 示例：自动文档同步

每次推送到 `main`，Claude Code：
1. 拉取最新更改并分析差异
2. 更新英文文档
3. 翻译为日文（或任何目标语言）
4. 创建带双语摘要的 PR
5. 启用自动合并——CI 通过后自动合并

**GitHub Actions：**

```yaml
# .github/workflows/docs-sync.yml
name: Documentation Sync
on:
  push:
    branches: [main]
jobs:
  trigger:
    if: "!contains(github.event.head_commit.message, '[docs-sync]')"
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST "${{ secrets.DISCORD_WEBHOOK_URL }}" \
            -H "Content-Type: application/json" \
            -d '{"content": "🔄 docs-sync"}'
```

**Bot 配置：**

```python
from claude_discord import WebhookTriggerCog, WebhookTrigger, ClaudeRunner

runner = ClaudeRunner(command="claude", model="sonnet")

triggers = {
    "🔄 docs-sync": WebhookTrigger(
        prompt="Analyze changes, update docs, create a PR with bilingual summary, enable auto-merge.",
        working_dir="/home/user/my-project",
        timeout=600,
    ),
}

await bot.add_cog(WebhookTriggerCog(
    bot=bot,
    runner=runner,
    triggers=triggers,
    channel_ids={YOUR_CHANNEL_ID},
))
```

**安全性：** 提示在服务器端定义。Webhook 只选择触发哪个触发器——不存在任意提示注入。

### 示例：自动批准所有者的 PR

```yaml
# .github/workflows/auto-approve.yml
name: Auto Approve Owner PRs
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  auto-approve:
    if: github.event.pull_request.user.login == 'your-username'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: write
    steps:
      - env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr review "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --approve
          gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --squash
```

---

## 定时任务

在运行时注册定期 Claude Code 任务——无需修改代码，无需重新部署。

在 Discord 会话中，Claude 可以注册一个任务：

```bash
# Claude 在会话中调用：
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

或从你自己的脚本注册：

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

30 秒主循环会拾取到期任务并自动派生 Claude Code 会话。

---

## 自动升级

在新版本发布时自动升级 Bot：

```python
from claude_discord import AutoUpgradeCog, UpgradeConfig

config = UpgradeConfig(
    package_name="claude-code-discord-bridge",
    trigger_prefix="🔄 bot-upgrade",
    working_dir="/home/user/my-bot",
    restart_command=["sudo", "systemctl", "restart", "my-bot.service"],
    restart_approval=True,       # 在线程中 ✅ 反应，或点击频道中的按钮
    slash_command_enabled=True,  # 启用 /upgrade 斜杠命令（选择启用，默认 False）
)

await bot.add_cog(AutoUpgradeCog(bot, config))
```

#### 通过 `/upgrade` 手动触发

当 `slash_command_enabled=True` 时，任何授权用户都可以直接在 Discord 中运行 `/upgrade` 来触发同样的升级流水线——无需 webhook。该命令在文本频道和线程中都能工作（在线程内运行会在父频道中创建升级线程）。它遵守 `upgrade_approval` 和 `restart_approval` 关卡，创建一个进度线程，并优雅地处理并发运行（若升级已在进行中，则以临时消息回复）。

重启前，`AutoUpgradeCog`：

1. **快照活跃会话** — 收集所有带运行中 Claude 会话的线程（鸭子类型：任何带 `_active_runners` 字典的 Cog 都会被自动发现）。
2. **排空** — 等待活跃会话自然完成。
3. **标记为待恢复** — 将活跃线程 ID 保存到待恢复表。下次启动时，这些会话以安全优先的提示恢复：Claude 报告它当时在做什么，并请用户在恢复任何实现工作（代码更改、提交、PR）前再次确认。这可防止上下文压缩可能抹除任务批准状态后发生意外操作。
4. **重启** — 执行配置的重启命令。

任何带 `active_count` 属性的 Cog 都会被自动发现并排空：

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

会话标记完全为选择启用——它只在 `setup_bridge()` 已初始化会话数据库（默认）时激活。启用时，会话以 `--resume` 连续性恢复，因此 Claude Code 能从它离开的确切对话点接续。

> **覆盖范围：** `AutoUpgradeCog` 覆盖升级触发的重启。对于*所有其他*关闭（`systemctl stop`、`bot.close()`、SIGTERM），`ClaudeChatCog.cog_unload()` 提供了第二道自动安全网。

---

## REST API

可选的 REST API，用于通知和任务管理。需要 aiohttp：

```bash
uv add "claude-code-discord-bridge[api]"
```

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/notify` | 发送即时通知 |
| POST | `/api/schedule` | 安排一条通知 |
| GET | `/api/scheduled` | 列出待处理通知 |
| DELETE | `/api/scheduled/{id}` | 取消一条通知 |
| POST | `/api/tasks` | 注册一个定时 Claude Code 任务 |
| GET | `/api/tasks` | 列出已注册任务 |
| DELETE | `/api/tasks/{id}` | 删除一个任务 |
| PATCH | `/api/tasks/{id}` | 更新一个任务（启用/禁用，更改计划） |
| POST | `/api/spawn` | 创建新 Discord 线程并启动 Claude Code 会话（非阻塞）；传入 `auto_start: false` 可将 Claude 推迟到首条用户回复 |
| POST | `/api/ingest` | 认证的外部派生（浏览器扩展 / webhook），支持 base64 附件；配置结果检索时返回 `result_id` |
| GET | `/api/ingest/{result_id}` | 轮询派生会话的最终回复（`status`/`result`/`error`/`thread_id`） |
| POST | `/api/mark-resume` | 标记一个线程在下次 Bot 启动时自动恢复 |
| GET | `/api/lounge` | 读取最近的 AI 休息室消息 |
| POST | `/api/lounge` | 向 AI 休息室发布一条消息（带可选 `label`） |
| GET | `/api/sessions` | 列出每个会话——活跃与已存储——带状态、工作目录和最新休息室笔记（`state=running`、`exclude_thread`、`limit`） |
| GET | `/api/threads/{thread_id}/messages` | 读取另一个线程的对话，最早在前（`limit`） |
| POST | `/api/claims` | 在工作前占用一个资源——获取成功时 201，被占用时 409 并附上持有者 |
| GET | `/api/claims` | 列出活跃占用（可选 `resource` 过滤） |
| DELETE | `/api/claims` | 释放一个占用（`resource`、`thread_id`、可选 `force=true`） |
| POST | `/api/threads/{thread_id}/message` | 将一条消息从一个会话转发到另一个（`text`、`from_thread`、`mode`、`hop`） |

```bash
# 发送通知（embed 格式，默认）
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Build succeeded!", "title": "CI/CD"}'

# 发送纯文本通知（无 embed）
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Deployment done!", "format": "text"}'

# 发送一个 Discord 投票
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Vote now",
    "poll": {
      "question": "Which release track?",
      "answers": ["Stable", "Beta", "Nightly"],
      "duration_hours": 24,
      "allow_multiselect": false
    }
  }'

# 注册一个循环任务
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Daily standup summary", "interval_seconds": 86400}'
```
