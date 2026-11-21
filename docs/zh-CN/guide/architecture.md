# 架构与背景

> 若有差异，请以[英文版](../../../README.md)为准。

[中文首页](../README.md) · [上一指南](automation.md)

---

## 架构

```
claude_code_core/          # 与后端无关的共享核心库
  backend.py               # SessionBackend 协议 + create_backend() 工厂
  codex_runner.py          # OpenAI Codex CLI 后端
  runner.py                # Claude CLI 子进程管理器
  parser.py                # stream-json 事件解析器
  types.py                 # SDK 消息类型定义
  models.py                # SQLite schema
  session_repo.py          # 会话 CRUD
  lounge_repo.py           # AI 休息室消息 CRUD
  rewind.py                # 会话回退辅助工具
claude_discord/
  main.py                  # 独立入口（setup_bridge + 自定义 cog 加载器）
  cli.py                   # CLI 入口（ccdb setup/start 命令）
  setup.py                 # setup_bridge() — 一次调用完成 Cog 装配
  cog_loader.py            # 动态自定义 Cog 加载器（CUSTOM_COGS_DIR）
  bot.py                   # Discord Bot 类
  protocols.py             # 共享协议（DrainAware）
  frontend.py              # DiscordFrontend — resolve/create a conversation by key
  stores.py                # build_session_stores() — every repo, no frontend
  concurrency.py           # Worktree 指令 + 活跃会话注册表
  collision.py             # 文件写入跟踪 + 碰撞规则（纯函数，注入时钟）
  lounge.py                # AI 休息室提示构建器
  session_view.py          # GET /api/sessions 的跨会话视图（纯合并逻辑）
  relay.py                 # RelayGuard + 转发提示包装器（跳数/冷却/速率限制）
  session_sync.py          # CLI 会话发现与导入
  worktree.py              # WorktreeManager — 安全的 git worktree 生命周期
  cogs/
    claude_chat.py         # 交互式聊天（线程创建、消息处理）
    skill_command.py       # 带自动补全的 /skill 斜杠命令
    session_manage.py      # /sessions, /sync-sessions, /resume, /resume-info, /sync-settings
    session_sync.py        # sync-sessions 的线程创建与消息发布逻辑
    prompt_builder.py      # build_prompt_and_images() — 纯函数，无 Cog/Bot 状态
    scheduler.py           # 定期 Claude Code 任务执行器
    webhook_trigger.py     # Webhook → Claude Code 任务执行（CI/CD）
    auto_upgrade.py        # Webhook → 包升级 + 排空感知重启
    collision_watch.py     # 通报写入相同文件的会话（60 秒循环）
    event_processor.py     # EventProcessor — stream-json 事件的状态机
    run_config.py          # RunConfig 数据类 — 打包所有 CLI 执行参数
    _run_helper.py         # 薄编排层（run_claude_with_config + shim）
  claude/
    runner.py              # 从 claude_code_core 重新导出 ClaudeRunner
    parser.py              # 从 claude_code_core 重新导出 parse_line
    types.py               # 从 claude_code_core 重新导出类型定义
  database/
    models.py              # SQLite schema
    repository.py          # 会话 CRUD
    task_repo.py           # 定时任务 CRUD
    ask_repo.py            # 待处理 AskUserQuestion CRUD
    notification_repo.py   # 定时通知 CRUD
    lounge_repo.py         # AI 休息室消息 CRUD
    claims_repo.py         # 建议性资源占用 CRUD（受 TTL 约束）
    resume_repo.py         # 启动恢复 CRUD（跨 Bot 重启的待恢复项）
    settings_repo.py       # 每个 guild 的设置
    frontend_thread_repo.py  # ThreadKey → 会话所在位置
    inbox_repo.py          # 线程收件箱 CRUD（THREAD_INBOX_ENABLED）
  discord_ui/
    status.py              # 表情反应管理器（防抖）
    chunker.py             # fence 与表格感知的消息拆分
    embeds.py              # Discord embed 构建器
    views.py               # 停止按钮和共享 UI 组件
    prompt_views.py        # ChoiceView / FormModal — renders the protocol's prompts
    mentions.py            # user_mention_kwargs() — Claude 因输入暂停时通知请求者
    ask_bus.py             # AskUserQuestion 通信的事件总线
    ask_view.py            # AskUserQuestion 的按钮/下拉菜单
    ask_handler.py         # collect_ask_answers() — AskUserQuestion UI + DB 生命周期
    streaming_manager.py   # StreamingMessageManager — 防抖的原地消息编辑
    tool_timer.py          # LiveToolTimer — 长时间运行工具的已用时间计数器
    thread_dashboard.py    # 显示会话状态的实时固定 embed
    file_sender.py         # 通过 .ccdb-attachments 发送文件
    inbox_classifier.py    # classify() — 轻量的 claude -p 调用给会话打标签
    thread_renamer.py      # suggest_title() — 用于自动线程命名的后台 claude -p 调用
  ext/
    api_server.py          # REST API（可选，需要 aiohttp）
    ingest_manifest.py     # 将 attachments_manifest 与实际送达的文件进行核对
  utils/
    logger.py              # 日志设置
examples/
  ebibot/                  # 真实世界示例：带自定义 Cog 的个人 Bot
    cogs/
      reminder.py          # /remind 斜杠命令 + 定时通知
      auto_upgrade.py      # 通过 GitHub webhook 自我更新
      docs_sync.py         # 推送时自动翻译文档
```

### 设计理念

- **CLI 派生而非 API** — 调用 `claude -p --output-format stream-json`，获得完整的 Claude Code 功能（CLAUDE.md、技能、工具、记忆）而无需重新实现它们。运行在你的 Claude Pro/Max 订阅上——无 API 密钥，无按 token 计费
- **并发优先** — 多个同时会话是预期情况，而非边缘情况；每个会话都获得 worktree 指令，其余由注册表和 AI 休息室处理
- **Discord 作为胶水** — Discord 提供 UI、线程、反应、webhook 和持久通知；无需自定义前端
- **框架而非应用** — 作为包安装，向你现有的 Bot 添加 Cog，通过代码配置
- **零代码可扩展性** — 无需触碰源代码即可添加定时任务和 webhook 触发器
- **简单即安全** — 约 8000 行可审计的 Python；仅 subprocess exec，无 shell 扩展

---

## 测试

```bash
uv run pytest tests/ -v --cov=claude_discord
```

1690+ 个测试覆盖解析器、分块器、仓库、runner、流式传输、webhook 触发器、自动升级（包括 `/upgrade` 斜杠命令、线程内调用和审批按钮）、REST API、AskUserQuestion UI、线程仪表板、定时任务、会话同步、AI 休息室、跨会话可观测性、资源占用、会话间转发、启动恢复、模型切换、压缩检测、TodoWrite 进度 embed、自定义 Cog 加载器、权限/elicitation/计划模式事件解析、线程收件箱分类、每线程锁行为、SessionBackend 协议、CodexRunner、后端工厂，以及跨后端会话归属。

---

## 本项目的构建方式

**本代码库由 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**（Anthropic 的 AI 编码代理）在 [@ebibibi](https://github.com/ebibibi) 的指导下开发。人类作者定义需求、审查 pull request 并批准所有更改——由 Claude Code 负责实现。

这意味着：

- **实现由 AI 生成** — 架构、代码、测试、文档
- **人类审查在 PR 层面进行** — 每个更改在合并前都经过 GitHub pull request 和 CI
- **欢迎 bug 报告和 PR** — 将使用 Claude Code 来处理它们
- **这是人类主导、AI 实现的开源软件的真实世界范例**

项目于 2026-02-18 启动，并通过与 Claude Code 的迭代对话不断演进。

---

## 实际案例

**[`examples/ebibot/`](../../../examples/ebibot/)** — 一个基于此框架构建的个人 Discord Bot，就包含在本仓库中。它展示了自定义 Cog 加载器，包含：

- **ReminderCog** — `/remind HH:MM "message"` 斜杠命令 + 30 秒发送循环
- **AutoUpgradeCog** — 通过 GitHub webhook + systemctl restart 自我更新
- **DocsSyncCog** — 通过 webhook 在推送时自动翻译文档
- **AlertResponderCog** — 通用告警监控 Cog；监视一个可配置来源并向 Discord 发布带严重程度注释的通知

运行方式：`ccdb start --cogs-dir examples/ebibot/cogs/`

> EbiBot 的自定义 Cog 此前维护在一个[独立仓库](https://github.com/ebibibi/discord-bot)中。它们现在共置于此处，以便 Claude Code 始终对框架和定制内容都拥有完整上下文——防止意外的功能重复。

---

## 灵感来源

- [OpenClaw](https://github.com/openclaw/openclaw) — 表情状态反应、消息防抖、fence 感知分块
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — CLI 派生 + stream-json 方法
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — 权限控制模式
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — 每线程对话模型

---

## 许可证

MIT
