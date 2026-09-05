> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../README.md) takes precedence.
> **注意：** 这是原始英文文档的自动翻译版本。
> 如有任何差异，以[英文版](../../README.md)为准。

# Claude & Codex Discord Bridge

*包名：`claude-code-discord-bridge`（短横线命名）*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**在手机上使用 Claude Code _或_ OpenAI Codex。多线程并行。全速真实开发。**

通过智能手机的 Discord 应用打开 Claude Code 或 OpenAI Codex，启动多个线程，并行运行开发会话——完全无需触碰键盘。每个 Discord 线程都成为完全隔离的 AI 会话。在一个线程中开发功能，在另一个线程中审查 PR，在第三个线程中运行后台任务——同时进行，甚至可以每个线程混用不同的后端。桥接器处理所有协调，让会话永不互相破坏。

**使用现有订阅，无需折腾 API 密钥。** ccdb 基于官方 CLI 运行——Claude Code（包含在 [Claude Pro/Max 订阅](https://claude.ai/pricing)中）和 OpenAI Codex（包含在 [ChatGPT Plus/Pro/Business](https://chatgpt.com)中）。用 `/backend` 切换后端或设置按线程的覆盖——让你的团队以可预测的费用通过 Discord 同时获得两种 AI。

**[English](../../README.md)** | **[日本語](../ja/README.md)** | 简体中文 | **[한국어](../ko/README.md)** | **[Español](../es/README.md)** | **[Português](../pt-BR/README.md)** | **[Français](../fr/README.md)**

> **免责声明：** 本项目与 Anthropic 或 OpenAI 没有任何关联、认可或官方关系。"Claude"和"Claude Code"是 Anthropic, PBC 的商标；"OpenAI"、"Codex"和"ChatGPT"是 OpenAI 的商标。这是一个与 Claude Code CLI 和 OpenAI Codex CLI 交互的独立开源工具。

> **完全由 Claude Code 构建。** 整个代码库——架构、实现、测试、文档——均由 Claude Code 本身编写。人类作者通过自然语言提供需求和方向。详见[本项目的构建方式](#本项目的构建方式)。

---

## 核心理念：无冲突并行会话

当你在多个独立的 Discord 线程中向 Claude Code 或 OpenAI Codex 发送任务时，桥接器会自动完成四件事——无论你选择了哪个后端：

1. **并发指令注入** — 每个会话的系统提示都包含强制指令：创建 git worktree，只在其中工作，绝不直接修改主工作目录。

2. **活跃会话注册表** — 每个运行中的会话都知道其他会话的存在。如果两个会话将要操作同一个仓库，它们可以协调而非冲突。

3. **AI 休息室（AI Lounge）** — 注入每个提示的会话间"休息室"。开始前，每个会话读取最近的休息室消息，了解其他会话在做什么，并占用它即将操作的仓库、issue 或文件（见[资源占用](#资源占用resource-claims)），从而在第二个会话重复工作之前就将其挡回。在进行破坏性操作（强制推送、重启 Bot、删除数据库）前，会话会先检查休息室，避免踩到彼此的工作。

4. **与后端无关的统一界面** — 无论线程运行的是 Claude 还是 Codex，相同的 Discord UI、斜杠命令、调度器、API 和休息室的工作方式都完全一致。如果需要，可跨线程混用后端——例如 Claude 做重构、Codex 做代码审查——通过每个线程的 `/backend` 实现。

```
线程 A（功能开发）  ──→  Claude Code  (worktree-A)  ─┐
线程 B（PR 审查）   ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
线程 C（文档）      ──→  Claude Code  (worktree-C)  ─┘    "A: auth 重构进行中"
                                                          "B: PR #42 审查完成（codex）"
                                                          "C: 更新 README"
```

无竞争条件。无工作丢失。无合并意外。无后端锁定。

---

## 能做什么

### 交互式聊天（移动端 / 桌面端）

在任何能运行 Discord 的地方使用 Claude Code _或_ OpenAI Codex——手机、平板或桌面。每条消息创建或继续一个线程，与持久化的 AI 会话一一对应。随时用 `/backend claude` 或 `/backend codex` 切换后端——可按线程切换，也可全局设为新的默认值。

### 并行开发

同时打开多个线程。每个线程都是独立的 AI 会话——Claude Code 或 Codex——拥有自己的上下文、工作目录和 git worktree。常见用法：

- **功能开发 + 同步审查**：在一个线程用 Claude 开发功能，同时 Codex 在另一个线程审查 PR。
- **多人协作**：不同团队成员各有自己的线程（以及各自偏好的后端）；会话通过 AI 休息室互相了解动态。
- **安全实验**：在线程 A 尝试某种方案，同时线程 B 保持稳定代码。
- **在两种 AI 上 A/B 同一提示**：用相同任务启动两个线程，一个用 `/backend claude`，一个用 `/backend codex`，然后并排比较两者的 diff。

### 定时任务（SchedulerCog）

无需修改代码、无需重新部署，通过 Discord 对话或 REST API 注册定期 Claude Code 任务。任务存储在 SQLite 中，按可配置的计划运行。Claude 可在会话中通过 `POST /api/tasks` 自我注册任务。

```
/skill name:goodmorning         → 立即执行
Claude 调用 POST /api/tasks     → 注册定期任务
SchedulerCog（30 秒主循环）      → 自动触发到期任务
```

### CI/CD 自动化

通过 Discord webhook 从 GitHub Actions 触发 Claude Code 任务。Claude 自主运行——读取代码、更新文档、创建 PR、启用自动合并。

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**实际案例：** 每次推送到 `main`，Claude 分析差异，更新英文 + 日文文档，创建双语 PR，并启用自动合并。零人工干预。

### 会话同步

已经在直接使用 Claude Code CLI？通过 `/sync-sessions` 将现有终端会话同步到 Discord 线程。它会回填最近的对话消息，让你无需丢失上下文即可从手机继续 CLI 会话。

### AI 休息室（AI Lounge）

一个共享的"休息室"频道，所有并发会话在此通报自己、阅读彼此的更新，并在破坏性操作前进行协调。

每个会话都会自动接收休息室上下文，作为临时的系统/开发者指令注入（Claude 使用 `--append-system-prompt`，Codex 使用 `developer_instructions`），而非作为对话历史的一部分。这可防止上下文跨回合累积——否则在长时间运行的会话中会导致"Prompt is too long"错误。注入的上下文包含其他会话的最近消息，以及"在做任何破坏性操作前先检查"的规则。

```bash
# 会话在开始前发布意图：
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "在 feature/oauth 上开始 auth 重构 — worktree-A", "label": "功能开发"}'

# 读取最近的休息室消息（也会自动注入每个会话）：
curl "$CCDB_API_URL/api/lounge"
```

休息室频道同时也是人类可见的活动流——在 Discord 中打开它，一眼即可看到每个活跃的 Claude 会话当前正在做什么。

### 跨会话可观测性

一条休息室笔记只能告诉某个会话"另一个线程存在"。下面这两个只读端点让它可以真正去查看——这样两个从同一任务出发的会话就能发现重叠，而不是各自埋头猛冲。

```bash
# 还有谁在线，他们在哪里工作，他们最后通报了什么？
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# 读取那个线程的实际对话
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions` 合并三个来源：`sessions` 表（created_at、工作目录、后端）、内存注册表（每个活跃会话*此刻*正在做什么），以及每个线程最新的休息室笔记。当某个会话有一个回合正在进行时，它会以 `"state": "running"` 出现——包括那些根本没有向休息室发过任何消息的会话，而这恰恰是最需要它的时刻。没有正在执行回合的已保存对话会显示为 `"state": "history"`；这表示它是可恢复的历史记录，并不表示代理正在等待工作或用户输入。会话本身没有 Discord token，因此由 Bot 执行读取，端点则保持在 localhost 控制平面上。

### 资源占用（Resource Claims）

可观测性告诉会话冲突*已经发生*。而占用可以从源头预防——无需读取、无需协商、无需 LLM 往返。会话占用它即将处理的对象；下一个请求同一对象的会话在做任何工作之前就会被拒绝。

```bash
# 开始前：占用它
curl -X POST "$CCDB_API_URL/api/claims" \
  -H "Content-Type: application/json" \
  -d '{"resource": "repo:ccdb#issue-123", "thread_id": "'$DISCORD_THREAD_ID'", "note": "fixing the parser"}'
# 201 {"status": "acquired", ...}

# 第二个会话请求同一资源时：
# 409 {"status": "held", "claim": {"thread_id": ..., "note": "fixing the parser",
#      "holder_state": "running", "holder_thread_name": "..."}}

# 完成后
curl -X DELETE "$CCDB_API_URL/api/claims?resource=repo:ccdb%23issue-123&thread_id=$DISCORD_THREAD_ID"
```

占用是**建议性的**——在 git 或文件系统层面没有任何强制机制——并且每个占用都带有 TTL（默认 2 小时，最长 24 小时），因此一个已死掉的会话不会永久锁住某个资源。409 响应体会报告持有者是否仍在运行，调用方据此决定是等待、转做其他事，还是用 `force=true` 接管。资源名称是自由格式并会被规范化（大小写和空白），因此 `repo:ccdb` 和 `Repo: CCDB` 是同一个占用。

休息室提示会告诉每个会话在开始前占用、在完成后释放。

### 会话间转发（Session-to-Session Relay）

可观测性让会话看见同伴；占用让它们互不干扰。而当两个会话已经发生碰撞时，它们需要真正对话——并且其中一个需要停下来。

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message` 会忽略任何 Bot 写入的内容——正是这道防护阻止了 Bot 自言自语——因此转发改走这个端点，方式与 `/api/spawn` 相同。

- **`mode: "queue"`**（默认）等待接收方当前回合结束。
- **`mode: "interrupt"`** 会对进行中的回合发送 SIGINT，因此"立即停止"能在几秒内送达。它可能让接收方丢失尚未提交的工作，因此仅保留给真正的冲突使用。
- 转发的文本在到达 Claude 之前会**发布到线程中**，因此围观的人类能看到整个 AI 之间的交流。转发绝不是暗箱通道。
- 每条消息都被**包裹在一个标记中**，标明发送线程并声明它不是来自人类——一条未标记的指令会被当作所有者亲自撰写而照办。

真正的风险是死循环（两个会话互相回复会烧掉 token 并无休止地打断彼此），因此有一道防护限制每条链路：**最多 2 跳**、每个线程对之间 60 秒冷却、每个发送方每 10 分钟最多 5 次转发，且不允许自发。被拒绝时返回 429，并附上原因。

休息室提示还给会话一条决胜规则，让对话收敛而不是以互相客气告终：谁有提交或 PR 就胜过仍在调查的一方；否则较早启动的会话继续；平局归线程 ID 较小者。退让的一方先推送自己的分支，并交接它所了解到的内容。

### 自动碰撞检测

休息室和占用都依赖会话*主动说出*某件事。而这个机制能捕捉到无人通报的重叠，其依据是会话*实际做了什么*：如果两个活跃会话在 15 分钟内写入了同一个文件，那么无论谁提没提，它们都在做同一件事。

`EventProcessor` 记录每次写入型工具调用（`Write`、`Edit`、`MultiEdit`、`NotebookEdit`）的路径；`CollisionWatchCog` 每分钟比较各活跃会话之间的这些集合一次。

> 为什么用文件路径而不是工作目录：在单用户主机上，每个会话往往都从同一个家目录启动，因此 `working_dir` 相等会把每一对会话都标记出来，毫无意义。而共享同一个*被编辑的文件*几乎绝不是巧合。读取被刻意忽略——两个会话读取同一文件很正常，会淹没信号。

发现重叠时，监视器会发布：

- 在 **AI 休息室**中发一行，它会以零 token 成本注入每个会话的下一个回合，且不打断任何东西；以及
- 在**每个发生碰撞的线程**中发一条消息，指明对方、共享的文件，以及能解决它的端点。

它绝不会转发进一个运行中的会话——仅凭怀疑就抢占一个回合，代价会比碰撞本身更大。是否升级由会话自己决定，使用上面的转发端点。每一对会话最多每 30 分钟通报一次，因为每分钟重复一次的警告，是所有人都学会忽略的警告。

自动启用；在两个会话真正重叠之前，它始终保持休眠。

### 程序化会话创建

从脚本、GitHub Actions 或其他 Claude 会话创建新的 Claude Code 会话，无需 Discord 消息交互。

```bash
# 从另一个 Claude 会话或 CI 脚本：
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "对仓库运行安全扫描", "thread_name": "Security Scan"}'
# 线程创建后立即返回；Claude 在后台运行
```

**延迟启动 (`auto_start=false`)** — 创建线程并发布一条种子消息，但不立即启动 Claude。只有当用户回复时 Claude 才启动，并自动接收种子消息作为上下文。

```bash
# 发布一条通知；用户回复时 Claude 才启动
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "早上好！这是你的每日摘要：...",
    "thread_name": "Morning Briefing",
    "auto_start": false
  }'
```

这适用于通知型工作流（如每日简报、CI 告警），你希望预先展示信息，让用户决定是否与 Claude 互动。

Claude 子进程会以环境变量形式接收 `DISCORD_THREAD_ID`，因此一个运行中的会话可以派生子会话来并行工作。

### 带结果检索的认证外部摄取 (`/api/ingest`)

`POST /api/ingest` 是面向不可信外部客户端（浏览器扩展、移动快捷方式、webhook）的**认证、附件感知的派生接口**。与 `/api/spawn`（受信任、localhost）不同，它需要一个专用的 `ingest_token`（通过 `CCDB_INGEST_TOKEN` 设置；独立于 `api_secret`），并可携带 base64 文件附件写入磁盘，供派生的会话读取。它会创建一个真实的 Discord 线程，因此整个交互始终可观察。

该会话是**交互式的**（一个真实的 Discord 线程，你可以持续回复）——但你仍可以程序化地取回它的最终答案。当配置了结果检索（通过 `setup_bridge()` 自动连接）时，响应中包含一个 `result_id`，`GET /api/ingest/{result_id}` 可轮询会话的最终回复。相同的最终回复也会作为 `ccdb-answer.md` 附加到 Discord 线程，因此集成方可以把该附件当作规范的答案载荷。这就是往返模式：发布线程 + 附件 → 等待 → 读取答案文件或轮询结果 → 写回你自己的系统（如 Teams 线程），同时 Discord 保留历史记录。

```bash
# 发布任务（可选附件）；立即返回
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "总结此线程并起草回复",
       "attachments": [{"filename": "thread.txt", "data": "<base64>"}]}'
# → {"status": "spawned", "thread_id": "…", "result_id": "ab12…", "attachments_saved": 1}

# 轮询最终回复
curl "$CCDB_API_URL/api/ingest/ab12…" -H "Authorization: Bearer $CCDB_INGEST_TOKEN"
# → {"status": "done", "result": "…", "error": null, "thread_id": "…", "thread_name": "…"}
```

该端点为可选启用：未配置 `ingest_token` 时，`POST` 返回 `503`。结果检索不可用时，`POST` 仅省略 `result_id`，`GET /api/ingest/{id}` 返回 `503`——派生行为在其他方面不变。请求体和附件**不会**持久化到结果存储中（只存储状态、最终文本和线程 ID）；结果上限为 200 条。

#### 已验证的附件投递 (`attachments_manifest`)

在客户端一侧丢失的附件过去是不可见的：ccdb 保存它拿到的内容，并报告一个无人能核对的数量，于是会话会表现得好像它拥有一个从未收到的文件。发送一份**清单（manifest）**，ccdb 就会**验证**投递，而不是假定它成功——为你在上游发现的每个附件列出一条记录，用 `status` 告诉 ccdb 它的字节是否包含在本次请求中：

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "起草一份回复",
       "attachments": [{"filename": "bundle.zip", "data": "<base64>"}],
       "attachments_manifest": [
         {"name": "shot.png",  "status": "embedded", "sha256": "…", "message": "返信 11"},
         {"name": "debug.log", "status": "linked", "url": "https://…", "message": "返信 12",
          "reason": "SharePoint download returned 403"}
       ]}'
# → {"status": "spawned", …, "attachments": {"verified": true, "complete": false,
#      "missing": [], "not_delivered": [{"name": "debug.log", "message": "返信 12", …}]}}
```

| `status` | 含义 |
|---|---|
| `embedded`（默认） | 字节包含在本次请求中——ccdb 期望找到与之匹配的文件 |
| `linked` | 只拿到了一个 URL（没有主机权限、认证墙等） |
| `skipped` | 有意未发送（体积上限、被过滤掉） |
| `failed` | 抓取或下载失败 |

ccdb 将每条 `embedded` 记录与实际送达的文件进行匹配，顺序为：**优先 sha256**，然后是文件名精确匹配，然后是打包器在名称冲突时添加的 `4_image.png` 索引前缀，最后是文件大小——并且每个文件最多被消费一次，因此两个都叫 `image.png` 的附件不可能同时认领唯一送达的那个文件。任何无法核实的内容都会通过四种方式暴露出来：**会话提示词顶部**的 ⚠️ 区块（列出缺失文件，并要求会话不要编造其内容；当丢失发生在最新消息上时会另有提示）、与文件写在一起的 `ATTACHMENTS-REPORT.md` 账本、上面响应中的 `attachments` 结论，以及日志中的一条 `WARNING`。提供 `message` 还会让提示词中的路径列表按上游消息分组，并将最新的一组标记为应当优先阅读。

设置 `CCDB_INGEST_REQUIRE_COMPLETE=1`（或 `ingest_require_complete=True`）可在附件丢失时以 `409` **拒绝**该次摄取，而不是基于不完整的证据启动会话——适用于附件本身*就是*请求内容的场景。完全不发送清单则一切照旧：该次摄取会被报告为 `verified: false`，而绝不会被报告为已验证且完整。

### 启动恢复

如果 Bot 在会话进行中重启，被中断的 Claude 会话会在 Bot 重新上线时自动恢复。会话通过三种方式被标记为待恢复：

- **自动（升级重启）** — `AutoUpgradeCog` 会在包升级重启之前对所有活跃会话拍快照，并自动标记它们。
- **自动（任意关闭）** — 每当 Bot 通过任何机制关闭（`systemctl stop`、`bot.close()`、SIGTERM 等）时，`ClaudeChatCog.cog_unload()` 都会标记所有进行中的会话。
- **手动** — 任何会话都可以直接调用 `POST /api/mark-resume`。

### 后端切换 — 按需使用 Claude / Codex

ccdb 3.0 引入了三个斜杠命令，用来改变下一个会话由哪个 AI 处理，且无需重启 Bot：

- `/backend [name] [scope]` — 显示或切换后端。`name` 为 `claude` 或 `codex`。`scope` 为 `thread`（仅当前线程）或 `global`（服务器级默认值）。省略 `scope` 时，命令会自动判定：在线程中则作用于该线程，否则设置全局默认值。
- `/model [name] [scope]` — 显示或切换**当前**后端所用的模型。每个后端记住各自的模型偏好，因此来回切换后端不会破坏你偏好的模型。让某后端的模型保持未设置，即可沿用该 CLI 自身的默认值（例如 Codex 使用 `~/.codex/config.toml` 中的 `model`，因此 ccdb 跟踪的是控制台默认值，而不锁定某个版本）。
- `/effort [level] [scope]` — 显示或切换当前后端所用的**推理强度**。有效级别因后端而异：Claude 接受 `low/medium/high/max`；Codex 接受 `low/medium/high/xhigh/max/ultra`（映射到 CLI 的 `model_reasoning_effort`）。保持未设置即沿用 CLI 默认值。

这三个命令都会通过 `SettingsRepository` 持久化到 SQLite，因此选择在 Bot 重启后仍然有效。不带参数调用它们时，会打印当前全局默认值以及任何线程级覆盖。

**已有会话的线程会怎样？** 会话 ID 在两个 CLI 之间不可互通——把 Codex 的 rollout ID 交给 `claude --resume`（或把 Claude 的 UUID 交给 `codex exec resume`）会在 CLI 层失败。ccdb 记录了每个会话 ID 由哪个后端生成，因此切换绝不会让线程陷入无主状态：

- **线程级切换** — 存储的会话 ID 会被丢弃，以便下一条消息在新后端中重新开始，*除非*该记录已知属于你切换**到**的后端。因此切回去是重新接续该线程早先对话的有效方式。
- **全局切换** — 每个线程的记录被刻意保持不动。如果某个线程仍持有另一后端的会话 ID，下一条消息会开启一个全新会话，并发布一行说明原因的提示，而不是恢复。

在 ccdb 开始跟踪后端归属之前写入的记录没有存储后端。全局切换会像以往一样恢复它们；线程级切换则清除它们，而不是冒着恢复出错的风险。

视觉提示，让你永远不会忘记自己在和谁对话：

- **Claude 会话**以一个蓝紫色（blurple）embed 开场，标题为"🤖 Claude Code session started"。
- **Codex 会话**以一个 OpenAI 青绿色 embed 开场，标题为"🌀 OpenAI Codex session started"。
- 完成 embed 会在通常的时长 / 费用 / token / 上下文指标旁边，前置一个 `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` 标签。（当某后端的模型保持为 CLI 默认值时，该标签只显示后端名称。）

具体示例：

```text
/backend codex                        # 全局 → codex（后续新会话使用 codex）
/model gpt-5-codex                    # 全局 → codex 使用 gpt-5-codex
/effort xhigh                          # 全局 → codex 以 xhigh 强度推理
                                       # …打开一个线程，发送一条消息…
/backend claude scope:thread          # 仅当前线程 → 切回 claude
/model opus scope:thread              # 仅当前线程 → claude/opus
/effort max scope:thread              # 仅当前线程 → claude 以 max 强度推理
                                       # 其他线程保持全局的 codex 默认值
```

幕后原理：

- `BackendFactory` — 在启动时捕获静态配置（各后端的命令路径、权限模式、工作目录、允许的工具、超时、append-system-prompt、effort、api_port、api_secret），并按需构建一个全新的 `ClaudeRunner` 或 `CodexRunner`。`api_port` 由 `setup_bridge` 在 REST API 服务器启动后自动连接，因此工厂构建的 runner 总能把 `CCDB_API_URL` 注入其子进程环境。
- `BackendSettings` — 对 `SettingsRepository` 的薄封装，以**线程 > 全局 > 环境变量**的优先级解析活跃后端，并持久化来自斜杠命令的写入。
- `SessionBackend` 协议 — 两个 runner 都满足的抽象接口。内部管线（cog、embed、view、调度器、webhook 触发器）接收的是 `SessionBackend`，而非某个具体的 runner 类。

**每个后端在哪里认证？** Claude Code 通过 `claude` CLI 的 `claude login` 使用你现有的 Claude Pro/Max 订阅。Codex 通过 `codex` CLI 的 `codex login` 使用你现有的 ChatGPT Plus/Pro/Business 订阅。ccdb 从不接触原始 API 密钥——它只是调用被选中的那个 CLI。

---

## 功能列表

### 交互式聊天

#### 🔗 会话基础
- **纯聊天模式** — 当 `CHAT_ONLY_CHANNEL_IDS` 包含某频道时，只显示 Claude 的文本回复；工具 embed、思维块、会话开始/完成 embed 和待办列表都被隐藏。权限请求和 `AskUserQuestion` 始终显示。适合有非技术用户围观的公开频道。
- **线程 = 会话** — Discord 线程与 Claude Code 会话 1:1 映射
- **目标跟踪** — `/goal <条件>` 设置一个完成条件；Claude 持续工作直到满足条件。省略条件即查询状态；传入 `clear` 即取消
- **会话持久化** — 通过 `--resume` 跨消息继续对话
- **Codex 会话自动恢复恢复机制** — 如果一个已恢复的 Codex 会话在产生输出前反复丢失其 WebSocket，ccdb 会以一份有界限、仅文本的先前对话转录启动一个替代会话；图片和工具载荷被排除在外
- **并发会话** — 多个并行会话，限制可配置
- **停止但不清除** — `/stop` 暂停会话，同时保留它以供恢复
- **会话中断** — 向活跃线程发送新消息会向运行中的会话发送 SIGINT，并以新指令重新开始；无需手动 `/stop`
- **自动重命名线程** — 当 `THREAD_AUTO_RENAME=true` 时，每个新线程都会自动重命名为一个由 Claude 从首条消息生成的标题（后台任务，绝不延迟会话启动）

#### 📡 实时反馈
- **实时状态** — 表情反应：🧠 思考中，🛠️ 读取文件，💻 编辑中，🌐 网络搜索
- **流式文本** — Claude 工作时中间的助手文本实时显示
- **工具结果 embed** — 实时的工具调用结果，已用时间立即显示（0 秒）并每 5 秒递增；单行输出内联显示，多行输出折叠在展开按钮之后
- **扩展思维** — 推理以剧透标签 embed 显示（点击展开）
- **线程仪表板** — 实时固定 embed，显示哪些线程活跃、哪些在等待；需要输入时 @提及 所有者

#### 🤝 人机协作
- **交互式问题** — `AskUserQuestion` 渲染为 Discord 按钮或下拉菜单；会话以你的回答恢复；按钮在 Bot 重启后仍然有效；需要输入时 @提及 请求者
- **计划模式** — 当 Claude 调用 `ExitPlanMode` 时，Discord embed 会显示完整计划及批准/取消按钮；只有批准后 Claude 才继续；提示时 @提及 请求者；5 分钟超时自动取消
- **工具权限请求** — 当 Claude 需要权限执行某工具时，Discord 显示带工具名称和输入的允许/拒绝按钮；@提及 请求者；2 分钟后自动拒绝
- **MCP Elicitation** — MCP 服务器可通过 Discord 请求用户输入（表单模式：最多 5 个来自 JSON schema 的 Modal 字段；URL 模式：URL 按钮 + 完成确认）；@提及 请求者；5 分钟超时
- **TodoWrite 实时进度** — 当 Claude 调用 `TodoWrite` 时，发布一个 Discord embed 并在每次更新时原地编辑；显示 ✅ 已完成、🔄 进行中（带 `activeForm` 标签）、⬜ 待处理项

#### 📊 可观测性
- **Token 用量** — 会话完成 embed 中显示缓存命中率和 token 数量
- **上下文用量** — 会话完成 embed 中显示上下文窗口百分比（输入 + 缓存 token，不含输出）和距自动压缩的剩余容量；超过 83.5% 时 ⚠️ 警告
- **压缩检测** — 上下文压缩发生时在线程内通知（触发类型 + 压缩前的 token 数）
- **硬停滞通知** — 在无活动（扩展思维或上下文压缩）后发一条线程消息；Claude 恢复时自动重置。阈值因模型而异：标准模型 30 秒，Opus 120 秒（其思考停顿更长）
- **超时通知** — 超时时显示带已用时间和恢复指南的 embed
- **StatusLine 显示** — 当 Claude 配置了 `statusLine`（通过 `/statusline-setup`）时，每次会话后在 Discord 中显示当前状态，作为简洁、始终可见的指示器
- **API 提供方指示器** — 每次会话后，一行 `🔗 API: <provider>` 显示 CLI 实际使用的端点（`Anthropic API (direct)`、`AWS Bedrock`、`Google Vertex AI`、`Azure AI Foundry`，或自定义 base URL），由真实子进程环境推导得出，因此 CLI 环境覆盖也会被反映。始终显示——即使没有配置 `statusLine`。
- **线程收件箱** — 当 `THREAD_INBOX_ENABLED=true` 时，仪表板显示一个持久的 📬 收件箱区：每次会话结束后，Claude 通过一次轻量的 `claude -p` 调用对最终消息分类（`waiting` / `done` / `ambiguous`）；等待你回复的线程在 Bot 重启后仍然保留，并会一直呈现直到你回应

#### 🔌 输入与技能
- **附件支持** — 文本文件自动追加到提示（最多 5 个文件，每个 200 KB / 合计 500 KB；超大文件会被截断并附带提示，而非跳过）；图片通过 `--input-format stream-json` 以 Discord CDN URL 发送（最多 4 × 5 MB）；被 Discord 自动转为文件附件（无 `content_type`）的长粘贴消息，通过基于扩展名的检测处理
- **按需文件发送** — 让 Claude "发给我"或"附上"某个文件，它会把路径写入 `.ccdb-attachments`；Bot 读取该文件，并在会话完成时将其作为 Discord 附件发送。本地指令也可以要求把重要的书面交付物保存为 Markdown 并附上。
- **技能执行** — `/skill` 命令，带自动补全、可选参数、线程内恢复；已安装插件中的技能也会被自动发现
- **热重载** — 添加到 `~/.claude/skills/` 的新技能会被自动识别（60 秒刷新，无需重启）

### 并发与协调
- **Worktree 指令自动注入** — 每个会话在触碰任何文件前都被提示使用 `git worktree`
- **自动 worktree 清理** — 会话 worktree（`wt-{thread_id}`）在会话结束时和 Bot 启动时自动移除；脏 worktree 永不自动移除（安全不变量）
- **活跃会话注册表** — 内存注册表；每个会话看到其他会话正在做什么
- **AI 休息室** — 共享的"休息室"频道；上下文作为后端专属的系统/开发者指令注入（临时性，绝不在历史中累积），因此长时间会话绝不会触发"Prompt is too long"；会话发布意图、阅读彼此状态，并在破坏性操作前检查；人类将其视为实时活动流
- **跨会话可观测性** — `GET /api/sessions` 列出每个会话（活跃与已存储），带其状态、工作目录和最新休息室笔记；`GET /api/threads/{thread_id}/messages` 读取另一个线程的对话。只读，因此会话可以在编辑前查看——包括那些从未向休息室发消息的会话
- **资源占用** — `POST /api/claims` 在工作开始前预占一个仓库、issue 或文件；请求同一资源的第二个会话会收到 409，并附上持有者的线程、备注和实时状态。建议性且受 TTL 约束（默认 2 小时，最长 24 小时），因此一个已死的会话不会永久锁住资源
- **会话间转发** — `POST /api/threads/{thread_id}/message` 让一个会话在已发生碰撞时对另一个会话说话；`queue` 等待接收方的回合，`interrupt` 对其发送 SIGINT。每次转发都发布到线程中（绝非暗箱通道），并包裹在标记中以免被误认为人类，同时受跳数/冷却/速率限制约束，因此两个会话不会陷入死循环
- **自动碰撞检测** — `CollisionWatchCog` 每分钟比较各活跃会话实际写入的文件（从 `Write`/`Edit`/`MultiEdit`/`NotebookEdit` 记录）；在 15 分钟内写入同一文件的两个会话会在 AI 休息室和两个线程中被通报。它能捕捉无人通报的重叠；每对会话每 30 分钟一次告警，且绝不打断运行中的回合
- **协调频道** — `COORDINATION_CHANNEL_ID` 环境变量用作 AI 休息室频道的默认回退（不含单独的 Bot 端生命周期事件）

### 定时任务
- **SchedulerCog** — 基于 SQLite 的定期任务执行器，带 30 秒主循环
- **自我注册** — Claude 在聊天会话中通过 `POST /api/tasks` 注册任务
- **无需修改代码** — 运行时添加、删除或修改任务
- **启用/禁用** — 无需删除即可暂停任务（`PATCH /api/tasks/{id}`）

### CI/CD 自动化
- **Webhook 触发** — 从 GitHub Actions 或任何 CI/CD 系统触发 Claude Code 任务
- **自动升级** — 上游包发布时自动更新 Bot
- **DrainAware 重启** — 重启前等待活跃会话完成
- **自动恢复标记** — 任何关闭时活跃会话都会自动被标记为待恢复（通过 `AutoUpgradeCog` 的升级重启，或通过 `ClaudeChatCog.cog_unload()` 的任何其他关闭）；重启时 Claude 报告其先前状态，并在恢复任何实现工作前与用户再次确认
- **重启审批** — 可选的升级确认关卡；通过升级线程中的 ✅ 反应或发布到父频道的按钮批准；随着新消息到来，按钮会在底部重新发布自己以保持可见
- **手动升级触发** — `/upgrade` 斜杠命令让授权用户直接从 Discord 触发升级流水线（通过 `slash_command_enabled=True` 选择启用）

### 会话管理
- **内置帮助** — `/help` 显示所有可用斜杠命令和基本用法（临时消息，仅调用者可见）
- **会话同步** — 将 CLI 会话导入为 Discord 线程（`/sync-sessions`）；`/sync-settings` 查看或更改同步偏好（线程样式、时间窗口、最少结果数）
- **会话列表** — `/sessions`，可按来源（Discord / CLI / 全部）和时间窗口过滤
- **会话恢复** — `/resume` 显示最近会话的下拉菜单（最多 25 个）并在新线程中恢复所选会话；可选的 `query` 参数用于关键词搜索（匹配摘要和工作目录）；可选的 `filter=orphaned` 只显示来自已删除线程的会话；可从任何频道或线程使用——始终在配置的主频道中创建新线程
- **恢复信息** — `/resume-info` 显示在终端中继续当前会话的 CLI 命令（仅线程内）
- **清除会话** — `/clear` 重置当前线程的 Claude Code 会话，重新开始而不创建新线程
- **启动恢复** — 被中断的会话在任何 Bot 重启后自动重启；`AutoUpgradeCog`（升级重启）和 `ClaudeChatCog.cog_unload()`（所有其他关闭）会自动标记它们，或手动使用 `POST /api/mark-resume`
- **程序化派生** — `POST /api/spawn` 从任何脚本或 Claude 子进程创建新的 Discord 线程 + Claude 会话；线程创建后立即返回非阻塞的 201
- **线程 ID 注入** — `DISCORD_THREAD_ID` 环境变量被传递给每个 Claude 子进程，使会话能够通过 `$CCDB_API_URL/api/spawn` 派生子会话
- **StatusLine 显示** — 如果你的 Claude Code `settings.json` 配置了 `statusLine`，其输出会在每次会话响应后显示在 Discord 中
- **Worktree 管理** — `/worktree-list` 显示所有活跃会话 worktree 及其干净/脏状态；`/worktree-cleanup` 移除孤立的干净 worktree（支持 `dry_run` 预览）
- **运行时模型切换** — `/model-show` 显示当前全局模型和每个线程的会话模型；`/model-set` 无需重启即可为所有新会话更改模型
- **运行时工具权限** — `/tools-show` 显示当前允许的工具；`/tools-set` 打开下拉菜单切换工具开/关；`/tools-reset` 恢复到 `.env` 默认值——全部无需重启
- **上下文用量** — `/context` 显示带可视化进度条的上下文窗口百分比；接近 83.5% 自动压缩阈值时 ⚠️ 警告；临时消息（仅调用者可见）
- **速率限制用量** — `/usage` 显示 Claude API 速率限制利用率，带百分比条以及 5 小时和 7 天窗口的重置倒计时；利用率 ≥ 80% 时 ⚠️ 标记
- **对话回退** — `/rewind` 显示过去用户回合的下拉菜单，并在所选点截断会话 JSONL，移除该消息及其之后的一切，让会话从该回合之前的确切状态恢复；保留 Claude 创建的所有工作文件；当会话跑偏时很有用
- **对话分叉** — `/fork` 将当前线程分支为一个新线程，通过 `--fork-session` 从相同会话状态继续，创建一个真正独立的会话副本；让你探索不同方向而不影响原线程

### 安全性
- **无 shell 注入** — 仅使用 `asyncio.create_subprocess_exec`，绝不使用 `shell=True`
- **会话 ID 验证** — 传递给 `--resume` 前进行严格正则验证
- **标志注入防护** — 所有提示前使用 `--` 分隔符
- **密钥隔离** — Bot token 从子进程环境中移除
- **用户授权** — `allowed_user_ids` 限制谁能调用 Claude
- **日志注入防护** — 用户提供的 API 值在写入日志前会被净化（移除换行符）

---

## 快速开始 — 5 分钟在 Discord 中运行 Claude 或 Codex

**前提条件：**

- Python 3.12+
- 以下至少一项：
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — 已安装并认证（`claude login`）。推荐 Anthropic Pro/Max 订阅用户使用。
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` 然后 `codex login`。使用你现有的 ChatGPT Plus/Pro/Business 订阅。
- 两者可同时安装。运行时用 `/backend` 在它们之间切换（见[后端切换](#后端切换--按需使用-claude--codex)）。

**平台支持：** 主要在 **Linux** 上开发和测试。macOS 和 Windows 受支持且通过 CI，但获得的真实世界测试较少——欢迎提交 bug 报告。

### 第一步 — 创建 Discord Bot（一次性，约 2 分钟）

1. 前往 [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. 导航到 **Bot** → 在 Privileged Gateway Intents 下启用 **Message Content Intent**
3. 复制 Bot **Token**
4. 前往 **OAuth2 → URL Generator**：作用域 `bot` + `applications.commands`，权限：Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. 打开生成的 URL → 将 Bot 邀请到你的服务器

### 第二步 — 运行设置向导

无需克隆或编辑 `.env`——向导会为你完成一切：

```bash
# 使用 uvx（无需安装）：
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# 或克隆后：
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

向导将：
1. 对照 Discord API 验证你的 Bot token
2. **自动列出可用频道**——只需选一个数字（无需复制 ID）
3. 询问你的工作目录和模型偏好
4. 写入 `.env` 并提议立即启动 Bot

```
╔══════════════════════════════════════════════════════╗
║          ccdb setup — interactive wizard             ║
╚══════════════════════════════════════════════════════╝

Step 1 — Claude Code CLI
  ✅  claude found

Step 2 — Discord Bot Token
  Bot Token: [paste here]
  Validating token… ✅  Logged in as MyBot#1234

Step 3 — Discord Channel ID
  Fetching channels via Discord API… ✅  Found 5 text channel(s)

   1. #general        (My Server)
   2. #claude-code    (My Server)
   3. #dev            (My Server)
   ...

  Select channel [1-5]: 2
  ✅  #claude-code (123456789012345678)

  ...

  ✅  Written: .env
  Start the bot now? [Y/n]: y
```

### 启动 / 停止

```bash
ccdb start    # 启动 Bot（读取当前目录的 .env）
ccdb start --env /path/to/.env   # 自定义 .env 位置
```

在配置的频道发送消息——Claude 将在新线程中回复。

### 作为 systemd 服务运行（生产环境）

对于生产部署，在 systemd 下运行 Bot，使其开机自启并在失败时自动重启。

仓库附带了一个可直接改用的模板（`discord-bot.service`）和一个预启动脚本（`scripts/pre-start.sh`）。复制并自定义它们：

```bash
# 1. 编辑服务文件——将 /home/ebi 和 User=ebi 替换为你的路径/用户
sudo cp discord-bot.service /etc/systemd/system/mybot.service
sudo nano /etc/systemd/system/mybot.service

# 2. 启用并启动
sudo systemctl daemon-reload
sudo systemctl enable mybot.service
sudo systemctl start mybot.service

# 3. 检查状态
sudo systemctl status mybot.service
journalctl -u mybot.service -f
```

**`scripts/pre-start.sh` 做什么**（作为 `ExecStartPre` 在 Bot 进程之前运行）：

1. **`git pull --ff-only`** — 从 `origin main` 拉取最新代码
2. **`uv sync`** — 使依赖与 `uv.lock` 保持同步
3. **导入验证** — 验证 `claude_discord.main` 能干净地导入
4. **自动回滚** — 如果导入失败，回退到上一个提交并重试；在失败或成功时发布一条 Discord webhook 通知
5. **Worktree 清理** — 移除崩溃会话遗留的陈旧 git worktree

该脚本动态检测仓库根目录（对 `$0` 使用 `readlink -f`），因此无论用户把仓库克隆到哪里都能工作——脚本本身无需编辑路径。它还会从 `PATH` 自动发现 `uv` 二进制文件；如有需要可通过 `CCDB_UV_BIN` 环境变量覆盖。

脚本需要 `.env` 中的 `DISCORD_WEBHOOK_URL` 变量以发送失败通知（可选——没有它脚本也能工作）。

#### 工具链 PATH — 在 `.env` 中设置

systemd 以一个最小的默认 `PATH`（通常是 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`）启动单元，且从不 source `~/.bashrc` 或 `~/.profile`。Bot 继承那个 `PATH`，它派生的每个 Claude/Codex 会话也一样——会话以 Bot 的环境（减去被剥离的密钥）运行。

结果令人困惑：在你的终端里能用的构建，在 Discord 会话中却失败，或者悄无声息地对着一个较旧的系统级二进制文件运行，因为安装在 `~/.local/bin` 或 `~/.npm-global/bin` 下的工具对该服务是不可见的。

由于该服务通过 `EnvironmentFile=` 加载 `.env`，在那里设置 `PATH` 可以一次性修复 Bot 和每个会话：

```bash
# .env — 匹配你交互式 shell 的 PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

重启服务（`sudo systemctl restart mybot.service`），然后从一个 Discord 会话中让 Claude 运行 `which node && node --version` 来确认。

### 自定义 Cog（无需 fork 即可扩展）

将 Python 文件放入一个目录即可添加你自己的功能——无需 fork、无需子类、无需打包：

```bash
ccdb start --cogs-dir ./my-cogs/
# 或：CUSTOM_COGS_DIR=./my-cogs ccdb start
```

目录中的每个 `.py` 文件必须暴露一个 `async def setup(bot, runner, components)`：

```python
from discord.ext import commands

class GreeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = self.bot.get_channel(self.bot.channel_id)
        await channel.send(f"Welcome {member.mention}!")

async def setup(bot, runner, components):
    await bot.add_cog(GreeterCog(bot))
```

以 `_` 为前缀的文件会被跳过。如果某个 Cog 加载失败，其他 Cog 仍会正常加载。

参见 [`examples/ebibot/`](examples/ebibot/)，这是一个完整的真实世界示例，包含提醒、Todoist 看门狗、自动升级和文档同步。

**`examples/ebibot/cogs/` 中的内置示例：**

| Cog | 用途 |
|-----|------|
| `ReminderCog` | 基于 Discord 的提醒调度 |
| `WatchdogCog` | Todoist / 外部服务看门狗 |
| `AutoUpgradeCog` | Webhook 触发的包升级 |
| `DocsSyncCog` | 推送时自动同步文档 |
| `AlertResponderCog` | 通用告警监控——将来自监控系统的告警转发到 Discord 并触发一个 Claude Code 调查会话 |

---

### 最小化 Bot（作为包安装）

如果你已经有一个 discord.py Bot，可以将 ccdb 作为包添加，而不必另起炉灶：

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

创建一个 `bot.py`：

```python
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
from claude_discord import ClaudeRunner, setup_bridge

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
runner = ClaudeRunner(
    command="claude",
    model="sonnet",
    working_dir="/path/to/your/project",
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await setup_bridge(
        bot,
        runner,
        claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),
        allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
    )

asyncio.run(bot.start(os.environ["DISCORD_BOT_TOKEN"]))
```

`setup_bridge()` 自动配置所有 Cog。更新到最新版本：

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### 多频道设置

要将 Bot 部署到多个 Discord 频道，在 `claude_channel_id` 之外（或替代它）传入 `claude_channel_ids`：

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),   # 主频道（线程创建的回退目标）
    claude_channel_ids={
        int(os.environ["DISCORD_CHANNEL_ID"]),
        int(os.environ["DISCORD_CHANNEL_ID_2"]),
    },
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

每个频道都完全独立——任何配置频道中的消息都会派生一个新的 Claude 会话线程，`/skill` 命令也在所有频道中通用。`claude_channel_id` 为向后兼容而保留，并在 `/skill` 命令于配置频道之外被调用时用作线程创建的回退目标。

#### 仅提及频道

要让 Bot **仅在被 @提及 时**在特定频道中响应（对你不希望 Bot 对每条消息都做出反应的共享频道很有用）：

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 222},
    mention_only_channel_ids={222},  # 除非被 @提及，否则 Bot 忽略 #222 中的消息
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

或通过环境变量（逗号分隔的频道 ID）：

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

线程**继承其父频道的策略**。人类在仅提及频道中创建的线程不会启动 Claude 会话——否则任何人只需打开一个线程就能绕过该设置。Claude 只在以下情况参与这样的线程：

- 消息中明确 **@提及** 了 Bot，或
- ccdb **已经拥有该线程**——即 Bot 创建的会话线程，或通过 `/api/spawn` 创建的线程。一旦会话存在，每条回复都会被正常处理，无需提及。

*不*在 `mention_only_channel_ids` 中列出的频道下的线程不受影响，始终被处理。

#### 内联回复频道

要让 Bot 在特定频道中**直接在频道内响应**（不创建线程）（对个人命令频道很有用，那里线程会增加不必要的杂乱）：

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # Bot 在 #333 中内联回复，不创建线程
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

或通过环境变量（逗号分隔的频道 ID）：

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

在内联回复模式下，Claude 的响应直接作为消息发送到频道，而不是派生新线程。会话仍在内部被跟踪，因此频道中的后续消息会继续同一个 Claude 会话。

#### 纯聊天频道

要在特定频道中隐藏技术 UI（工具 embed、思维块、会话开始/完成通知、待办列表）并**只显示 Claude 的文本回复**——对有非技术用户围观的面向公众的频道很有用：

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # #444 中只显示文本；隐藏工具细节
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

或通过环境变量（逗号分隔的频道 ID）：

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

在纯聊天模式下，无论该设置如何，权限请求和 `AskUserQuestion` 提示都**始终显示**——它们需要人类输入且必须可见。

---

## 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DISCORD_BOT_TOKEN` | 你的 Discord Bot token | （必填） |
| `DISCORD_CHANNEL_ID` | Claude 聊天频道 ID | （必填） |
| `CCDB_BACKEND` | 使用的 CLI 后端：`claude`（Claude Code CLI）或 `codex`（OpenAI Codex CLI） | `claude` |
| `CCDB_COMMAND` | CLI 二进制文件的路径或名称（覆盖 `CLAUDE_COMMAND`）。由从 `CCDB_BACKEND` 选出的初始 runner 使用；当 `/backend` 在运行时切换时，由下面两个各后端变量取代。 | _（自动：`claude` 或 `codex`）_ |
| `CCDB_CLAUDE_COMMAND` | Claude CLI 二进制文件的显式路径。每当 `/backend claude` 活跃时由 `BackendFactory` 使用，无论初始 `CCDB_BACKEND` 是什么。回退到 `CLAUDE_COMMAND`，再回退到 `claude`（PATH）。 | （可选） |
| `CCDB_CODEX_COMMAND` | OpenAI Codex CLI 二进制文件的显式路径。在 systemd 下运行 Bot 时必需（默认服务 PATH 不含 `~/.npm-global/bin`）。回退到 `codex`（PATH）。 | （可选） |
| `PATH` | Bot **及其派生的每个 CLI 会话**的二进制搜索路径——会话继承 Bot 的环境。在 systemd 下运行时应在 `.env` 中设置它，systemd 以最小 PATH 启动单元且从不读取 `~/.bashrc` / `~/.profile`。见[工具链 PATH](#工具链-path--在-env-中设置)。 | （继承自父进程） |
| `CCDB_MODEL` | 使用的模型（覆盖 `CLAUDE_MODEL`） | `sonnet` |
| `CCDB_PERMISSION_MODE` | CLI 权限模式（覆盖 `CLAUDE_PERMISSION_MODE`） | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | 跳过所有权限检查——覆盖 `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | CLI 工作目录（覆盖 `CLAUDE_WORKING_DIR`） | 当前目录 |
| `CCDB_ALLOWED_TOOLS` | 允许工具的逗号分隔列表（覆盖 `CLAUDE_ALLOWED_TOOLS`） | （可选） |
| `CCDB_CHANNEL_IDS` | 额外的频道 ID，逗号分隔（覆盖 `CLAUDE_CHANNEL_IDS`） | （可选） |
| `CLAUDE_COMMAND` | Claude CLI 二进制文件的路径或名称（旧名称——推荐 `CCDB_COMMAND`）。用于锁定特定版本（例如 `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`）——有助于避免较新 CLI 版本中的回归。 | `claude` |
| `CLAUDE_MODEL` | 使用的模型（旧名称——推荐 `CCDB_MODEL`） | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | CLI 权限模式（旧名称——推荐 `CCDB_PERMISSION_MODE`） | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | 跳过所有权限检查（旧名称——推荐 `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`） | `false` |
| `CLAUDE_WORKING_DIR` | Claude 的工作目录（旧名称——推荐 `CCDB_WORKING_DIR`） | 当前目录 |
| `MAX_CONCURRENT_SESSIONS` | 跨所有代码路径（聊天、技能、调度器、webhook）的最大并行 Claude CLI 会话数 | `3` |
| `SESSION_TIMEOUT_SECONDS` | 会话不活跃超时 | `300` |
| `DISCORD_OWNER_ID` | Claude 需要输入时 @提及 的用户 ID | （可选） |
| `COORDINATION_CHANNEL_ID` | 用作 AI 休息室频道默认回退的频道 ID | （可选） |
| `MENTION_ONLY_CHANNEL_IDS` | Bot 仅在被 @提及 时才响应的频道 ID，逗号分隔（其下的线程继承该策略） | （可选） |
| `INLINE_REPLY_CHANNEL_IDS` | Bot 内联回复（不创建线程）的频道 ID，逗号分隔 | （可选） |
| `CHAT_ONLY_CHANNEL_IDS` | 纯聊天模式的频道 ID，逗号分隔——只显示 Claude 的文本回复；所有技术 embed（工具、思维、会话信息、待办）都被隐藏 | （可选） |
| `WORKTREE_BASE_DIR` | 扫描会话 worktree 的基础目录（启用自动清理） | （可选） |
| `CLI_SESSIONS_PATH` | 用于 CLI 会话发现的 `~/.claude/projects` 路径（启用 `/sync-sessions`） | （可选） |
| `CUSTOM_COGS_DIR` | 启动时加载的自定义 Cog 文件目录（见[自定义 Cog](#自定义-cog无需-fork-即可扩展)） | （可选） |
| `CLAUDE_ALLOWED_TOOLS` | Claude CLI 允许工具的逗号分隔列表（旧名称——推荐 `CCDB_ALLOWED_TOOLS`） | （可选） |
| `CLAUDE_CHANNEL_IDS` | 多频道设置的额外频道 ID（逗号分隔）（旧名称——推荐 `CCDB_CHANNEL_IDS`） | （可选） |
| `THREAD_INBOX_ENABLED` | 启用持久线程收件箱（通过 `claude -p` 将会话分类为 `waiting`/`done`/`ambiguous`；显示在线程仪表板中） | `false` |
| `THREAD_AUTO_RENAME` | 使用 Claude AI 自动重命名新线程标题——通过后台 `claude -p` 调用从首条用户消息生成简短、描述性的标题（绝不延迟会话启动） | `false` |
| `CCDB_CLI_ENV_FILE` | 一个 `KEY=VALUE` 文件的路径，其变量在每次调用时合并到 CLI 子进程环境中。更改立即生效，无需重启 Bot。对临时 API 路由（如 Azure Foundry）很有用 | （可选） |
| `CCDB_LOG_FILE` | 日志文件路径。设置后，将在默认 stdout 处理器旁边添加一个轮转文件处理器（10 MB × 5 个备份）。对监控和告警很有用。 | （可选） |
| `API_HOST` | REST API 绑定地址 | `127.0.0.1` |
| `API_PORT` | REST API 端口（设置后启用 REST API） | （可选） |
| `CCDB_INGEST_TOKEN` | `POST /api/ingest` 的 Bearer 令牌（独立于 `api_secret`）；未设置时该端点返回 `503` | （可选） |
| `CCDB_INGEST_REQUIRE_COMPLETE` | 设为 `1` 时，若 `attachments_manifest` 证明附件已丢失，则以 `409` 拒绝该次摄取，而不是基于不完整的证据启动会话 | `0` |

### 权限模式 — 在 `-p` 模式下哪些有效

通过 ccdb 使用时，Claude Code CLI 运行在 **`-p`（非交互）模式**下。在此模式下，CLI **无法弹出权限请求**——需要审批的工具会被立即拒绝。这是一个 [CLI 设计约束](https://code.claude.com/docs/en/headless)，而非 ccdb 的限制。

| 模式 | 在 `-p` 模式下的行为 | 建议 |
|------|----------------------|------|
| `default` | ❌ **所有工具被拒绝**——不可用 | 不要使用 |
| `acceptEdits` | ⚠️ Edit/Write 自动批准，Bash 被拒绝（Claude 对文件操作回退到 Write） | 最低可用选项 |
| `bypassPermissions` | ✅ 所有工具批准 | 可用，但更推荐下面的标志 |
| **`auto`** | ✅ **AI 分类的安全性**——安全操作自动批准，危险操作被阻止 | **推荐**——安全性与可用性的最佳平衡 |
| `plan` | ✅ AI 分类（偏向只读）——类似 auto 但更保守 | 适合读取密集的工作流 |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **所有工具批准，无安全检查** | 传统的"yolo"模式——当 auto 模式过于严格时使用 |

**我们的建议：** 设置 `CLAUDE_PERMISSION_MODE=auto`。auto 模式使用一个 AI 分类器自动批准安全操作（文件编辑、本地测试、git push 到工作分支），同时阻止危险操作（强制推送、生产部署、凭据泄露）。这让 Claude 在常规开发工作中拥有完全自主权，而没有 yolo 模式"什么都放行"的风险。

**回退到 yolo 模式：** 如果 auto 模式阻止了你需要的操作，改为设置 `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`。由于 ccdb 通过 `allowed_user_ids` 控制谁能与 Claude 交互，CLI 层的权限检查只会增加摩擦而没有实质安全收益。名称中的"dangerously"反映的是 CLI 的通用警告；在访问已被门控的 ccdb 语境下，它是一个务实的选择。

> **注意：** 当 `CLAUDE_PERMISSION_MODE` 设为 `auto` 或 `plan` 时，`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` 会被自动忽略——这些模式有自己的安全分类器，会被 yolo 标志覆盖。

**若需精细控制**，使用 `CLAUDE_ALLOWED_TOOLS` 允许特定工具而不完全绕过权限：

```env
# 示例：允许文件操作和代码执行，但不允许网络访问
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# 示例：只读模式——Claude 可探索但不可修改
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

常见工具名称：`Bash`、`Read`、`Write`、`Edit`、`Glob`、`Grep`、`WebFetch`、`WebSearch`、`NotebookEdit`。使用此项时设置 `CLAUDE_PERMISSION_MODE=default`（其他模式可能覆盖它）。

**通过 Discord 运行时更改：** 使用 `/tools-set` 在运行时更改允许的工具而无需重启 Bot。该设置会被持久化，并立即对所有新会话生效。使用 `/tools-show` 查看当前配置，或用 `/tools-reset` 恢复到 `.env` 默认值。

> **Discord 中的权限按钮：** 当 `CLAUDE_PERMISSION_MODE=default` 时，Claude 发出 `permission_request` 事件，ccdb 在线程中显示允许/拒绝按钮。stdin 始终保持打开（stream-json 输入模式），以便 Bot 能把响应发回给 Claude。如果你使用 `auto` 或 `plan` 模式，Claude 会自动处理权限而无需用户交互。当 `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`（yolo 模式）时，ccdb 会**立即自动批准**任何 `permission_request` 事件——不显示允许/拒绝按钮。这是针对一个 CLI 回归（v2.1.78+，上游 [#35895](https://github.com/anthropics/claude-code/issues/35895)）的变通方案，该回归导致 `--dangerously-skip-permissions` 无法绕过文件级敏感路径检查。

---

## Discord Bot 设置

1. 在 [Discord 开发者门户](https://discord.com/developers/applications)创建一个新应用
2. 创建一个 Bot 并复制 token
3. 在 Privileged Gateway Intents 下启用 **Message Content Intent**
4. 以这些权限邀请 Bot：
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages（用于反应清理）
   - Read Message History

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
      watchdog.py          # Todoist 过期任务监控
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

**[`examples/ebibot/`](examples/ebibot/)** — 一个基于此框架构建的个人 Discord Bot，就包含在本仓库中。它展示了自定义 Cog 加载器，包含：

- **ReminderCog** — `/remind HH:MM "message"` 斜杠命令 + 30 秒发送循环
- **WatchdogCog** — Todoist 过期任务监控（30 分钟检查，每日去重，按严重程度告警）
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
