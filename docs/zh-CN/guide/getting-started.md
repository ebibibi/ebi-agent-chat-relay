# 快速开始与配置

> 若有差异，请以[英文版](../../../README.md)为准。

[中文首页](../README.md) · [上一指南](reference.md) · [下一指南](automation.md)

---

## 快速开始 — 5 分钟在 Discord 中运行 Claude 或 Codex

**前提条件：**

- Python 3.12+
- 以下至少一项：
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — 已安装并认证（`claude login`）。推荐 Anthropic Pro/Max 订阅用户使用。
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` 然后 `codex login`。使用你现有的 ChatGPT Plus/Pro/Business 订阅。
- 两者可同时安装。运行时用 `/backend` 在它们之间切换（见[后端切换](features.md#后端切换--按需使用-claude--codex)）。

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

参见 [`examples/ebibot/`](../../../examples/ebibot/)，这是一个完整的真实世界示例，包含提醒、Todoist 看门狗、自动升级和文档同步。

**`examples/ebibot/cogs/` 中的内置示例：**

| Cog | 用途 |
|-----|------|
| `ReminderCog` | 基于 Discord 的提醒调度 |
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
| `CUSTOM_COGS_DIR` | 启动时加载的自定义 Cog 文件目录（见[自定义 Cog](getting-started.md#自定义-cog无需-fork-即可扩展)） | （可选） |
| `CLAUDE_ALLOWED_TOOLS` | Claude CLI 允许工具的逗号分隔列表（旧名称——推荐 `CCDB_ALLOWED_TOOLS`） | （可选） |
| `CLAUDE_CHANNEL_IDS` | 多频道设置的额外频道 ID（逗号分隔）（旧名称——推荐 `CCDB_CHANNEL_IDS`） | （可选） |
| `THREAD_INBOX_ENABLED` | 启用持久线程收件箱（通过 `claude -p` 将会话分类为 `waiting`/`done`/`ambiguous`；显示在线程仪表板中） | `false` |
| `THREAD_AUTO_RENAME` | 使用 Claude AI 自动重命名新线程标题——通过后台 `claude -p` 调用从首条用户消息生成简短、描述性的标题（绝不延迟会话启动），并在主题明显改变后重新命名线程（每 15 分钟最多一次；保留族谱标记） | `false` |
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
