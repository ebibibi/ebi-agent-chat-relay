# 功能与会话协作

> 若有差异，请以[英文版](../../../README.md)为准。

[中文首页](../README.md) · [返回中文首页](../README.md) · [下一指南](reference.md)

---

## v5 的新变化

版本 5 为中继带来了**自己的 GUI：Ebi AI Console**——一个可在桌面或手机上使用的 Web 界面，
让你一次看到所有代理线程，决定现在该做什么，并直接处理。

- **Focus 视图**（默认）回答"我现在该做什么？"。一个快速模型——你所用后端中最便宜的模型，单次调用且不使用工具——
  会填写优先级、截止日期、下一步、现在能否完成，以及是否受地点限制。人工设置的值始终优先。
- **无需离开界面即可处理**：附带任意文件回复、就地预览文件、用选择器切换后端、模型和 effort、
  调整并发会话上限，以及暂停、恢复或停止正在运行的工作。
- **把委派的工作放在父对话旁边**：可折叠面板显示子任务和孙任务的状态、最新报告和完成数量。
  可以先查看子任务，再回到父对话做决定；直接回复子任务需要明确选择。
- **可选且只做增量**：未设置 `CCDB_CONSOLE_PORT` 时不会启动任何东西；Discord、Teams、后端、REST API
  和调度器均无变化。启用控制台后，自动分诊默认开启；`CCDB_CONSOLE_TRIAGE=0` 可将其关闭。

**版本 5.1 新增 Workspace 集成，以及更多管理对话的方式：**

- **Ebi Workspace 任务**：浏览上下文和项目、阅读概览、添加/编辑/完成任务，并从任务直接启动 AI 工作。
  项目的 `status.md` 和上下文的 `single-actions.md` 仍是唯一的事实来源；修订检查会拒绝过期的编辑。
- **搜索消息**：在可用的对话历史和本地转录中查找文本，显示摘录并链接回匹配的线程，不调用 AI。
- **管理线程家族**：分别配置管理者和子线程的后端/模型/effort，并可多选线程一起完成。完成父线程时会一并完成其后代。
- **桌面和移动端操作**：复制文本文件内容和线程链接，在日语/英语之间切换而不丢失草稿或附件，
  并让移动端输入框始终可用。

详见 [v5.1.0 发布说明](../../releases/v5.1.0.md)、[控制台指南](../../console.md) 和
[ADR-0014](../../adr/0014-publish-the-ebi-ai-console-as-v5.md)（英文）。接下来是**调度器集成**。

## v4 的新变化

版本 4 把两个彼此独立的选择显式分开：**人在哪里对话**，以及**由哪个代理来干活**。
任何受支持的前端都可以搭配任何受支持的后端。

### 前端 × 后端

| | Claude Code | OpenAI Codex | Local | AG-UI | pi |
|---|---:|---:|---:|---:|---:|
| Discord | ✅ | ✅ | ✅ | ✅ | ✅ |
| Microsoft Teams | ✅ | ✅ | ✅ | ✅ | ✅ |

- **Discord** 仍然是零迁移的默认选项。现有部署的启动方式与此前完全相同。
- **Microsoft Teams** 已可用于生产环境：由一个小型公开接收端，加上运行在私有会话主机上、
  仅出站连接的 `ActivityPuller` 构成。设置 `CCDB_FRONTENDS=discord,teams` 即可让
  Discord 与 Teams 在同一个进程中同时运行。
- **AG-UI** 让任一聊天界面都能连接到实现了 Agent–User Interaction Protocol 的 HTTP/SSE
  代理。Claude Code、Codex 以及带防护的本地后端同样保持可用。
- **pi** 运行 [pi](https://github.com/earendil-works/pi) CLI，可通过单个代理接入
  Anthropic、OpenAI、Google、GitHub Copilot 以及 OpenAI 兼容的本地服务器。在 ccdb 使用的
  非交互模式下它没有沙箱，因此在用 `CCDB_PI_ALLOW_UNSANDBOXED=1` 明示"信任宿主自身的隔离"
  之前，它会拒绝启动。

请先阅读[后端指南](../../backends.md)。Teams 请按照完整的
[Microsoft Teams 设置指南](../../teams-setup.md)搭建，然后参考更深入的
[界面行为](../../teams.md)与[中继安全模型](../../teams-relay.md)。

**[English](../../../README.md)** | **[日本語](../../ja/README.md)** | 简体中文 | **[한국어](../../ko/README.md)** | **[Español](../../es/README.md)** | **[Português](../../pt-BR/README.md)** | **[Français](../../fr/README.md)**

> **免责声明：** 本项目与 Anthropic 或 OpenAI 没有任何关联、认可或官方关系。"Claude"和"Claude Code"是 Anthropic, PBC 的商标；"OpenAI"、"Codex"和"ChatGPT"是 OpenAI 的商标。这是一个与 Claude Code CLI 和 OpenAI Codex CLI 交互的独立开源工具。

> **完全由 Claude Code 构建。** 整个代码库——架构、实现、测试、文档——均由 Claude Code 本身编写。人类作者通过自然语言提供需求和方向。详见[本项目的构建方式](architecture.md#本项目的构建方式)。

---

## 核心理念：无冲突并行会话

当你在多个独立的 Discord 线程中向 Claude Code 或 OpenAI Codex 发送任务时，桥接器会自动完成四件事——无论你选择了哪个后端：

1. **并发指令注入** — 每个会话的系统提示都包含强制指令：创建 git worktree，只在其中工作，绝不直接修改主工作目录。

2. **活跃会话注册表** — 每个运行中的会话都知道其他会话的存在。如果两个会话将要操作同一个仓库，它们可以协调而非冲突。

3. **AI 休息室（AI Lounge）** — 注入每个提示的会话间"休息室"。开始前，每个会话读取最近的休息室消息，了解其他会话在做什么，并占用它即将操作的仓库、issue 或文件（见[资源占用](features.md#资源占用resource-claims)），从而在第二个会话重复工作之前就将其挡回。在进行破坏性操作（强制推送、重启 Bot、删除数据库）前，会话会先检查休息室，避免踩到彼此的工作。

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

防护保留**最多2跳**和禁止自发的规则。同一发送方向同一接收方重复发送相同文本（规范化空白后）时，限制60秒；不同内容的补充消息或向其他线程发送消息可立即进行。每对线程在滚动60秒内双向合计最多20条；每个发送方的应急上限为滚动60秒内120条。queue和interrupt共用限额。拒绝时返回429和 `reason`；临时限制还提供 `retry_after_seconds` 和 `Retry-After`。详见[规范](../../guide/features.md#session-to-session-relay)。

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

- `/backend [name] [scope]` — 显示或切换后端。`name` 为 `claude`、`codex`、`local`、`agui`、`pi` 或 `antigravity`。`scope` 为 `thread`（仅当前线程）或 `global`（服务器级默认值）。省略 `scope` 时，命令会自动判定：在线程中则作用于该线程，否则设置全局默认值。
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
