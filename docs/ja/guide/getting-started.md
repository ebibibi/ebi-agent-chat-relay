# はじめかた

> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。内容に相違がある場合は英語版が優先されます。

← [README](../README.md) に戻る · [できること](features.md) · [機能リファレンス](reference.md) · **はじめかた** · [自動化・スケジューラー・REST API](automation.md) · [アーキテクチャとプロジェクトの背景](architecture.md)

## クイックスタート — 5 分で Discord に Claude または Codex を

**前提条件:**

- Python 3.12+
- 以下のうち少なくとも 1 つ:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — インストールと認証（`claude login`）。Anthropic Pro/Max サブスクライバーに推奨。
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` の後 `codex login`。既存の ChatGPT Plus/Pro/Business サブスクリプションを使用。
- 両方インストールも可能。実行時に `/backend` でいつでも切り替えられます（[バックエンド切り替え](features.md#バックエンド切り替え--claude--codex--local--ag-ui--pi-をオンデマンドで) 参照）。

**対応プラットフォーム:** **Linux** で開発・テストされ、本番運用されています。CI は Linux のみで実行しています。macOS と Windows でも動作する想定です（Windows では Claude・Codex CLI の npm `.cmd` ラッパーを `node` + スクリプトとして解決します）が、実環境でのテストはほとんどありません — バグ報告歓迎。

### Step 1 — Discord Bot の作成（一度だけ、約 2 分）

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. **Bot** に移動 → Privileged Gateway Intents の **Message Content Intent** を有効化
3. Bot の **Token** をコピー
4. **OAuth2 → URL Generator** へ: スコープ `bot` + `applications.commands`、権限: Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. 生成された URL を開いてサーバーに Bot を招待

### Step 2 — セットアップウィザードを実行

クローンや `.env` 編集は不要 — ウィザードがすべて行います:

```bash
# uvx を使う場合（インストール不要）:
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# または、クローン後:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

ウィザードの流れ:
1. Discord API に対して Bot トークンを検証
2. **利用可能なチャンネルを自動一覧表示** — 番号を選ぶだけ（ID のコピー不要）
3. 作業ディレクトリとモデルの選択
4. `.env` を書き込み、すぐに Bot を起動するか確認

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

### 起動 / 停止

```bash
ccdb start    # Bot を起動（カレントディレクトリの .env を読む）
ccdb start --env /path/to/.env   # カスタム .env の場所を指定
```

設定したチャンネルにメッセージを送ると、Claude が新しいスレッドで返信します。

### systemd サービスとして運用（本番環境）

本番環境では systemd 管理下で動かすと、起動時の自動開始と障害時の自動再起動が得られます。

リポジトリにはテンプレートとして `discord-bot.service`（サービスファイル）と `scripts/pre-start.sh`（起動前スクリプト）が同梱されています。コピーしてパスやユーザー名を書き換えてください:

```bash
# 1. サービスファイルを編集 — /home/ebi と User=ebi をご自身のパス/ユーザーに変更
sudo cp discord-bot.service /etc/systemd/system/mybot.service
sudo nano /etc/systemd/system/mybot.service

# 2. 有効化して起動
sudo systemctl daemon-reload
sudo systemctl enable mybot.service
sudo systemctl start mybot.service

# 3. 状態確認
sudo systemctl status mybot.service
journalctl -u mybot.service -f
```

**`scripts/pre-start.sh` の動作**（ボットプロセス起動前に `ExecStartPre` として実行）:

1. **`git pull --ff-only`** — `origin main` から最新コードを取得
2. **`uv sync`** — `uv.lock` に従って依存関係を最新の状態に同期
3. **インポート検証** — `claude_discord.main` が正常にインポートできるか確認
4. **自動ロールバック** — インポートに失敗した場合は前のコミットに戻して再試行。Discord webhook に成功/失敗を通知
5. **Worktree クリーンアップ** — クラッシュしたセッションが残した git worktree を削除

スクリプトはリポジトリのルートを自動検出（`$0` に対する `readlink -f`）するため、どのユーザーがどの場所にクローンしてもパスの書き換えは不要です。`uv` のパスも `PATH` から自動検出します。カスタムパスを使う場合は `CCDB_UV_BIN` 環境変数で指定してください。

`.env` に `DISCORD_WEBHOOK_URL` を設定すると障害通知が届きます（任意）。

#### ツールチェーンの PATH — `.env` に設定する

systemd はユニットを最小限の `PATH`（通常は `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`）で起動し、`~/.bashrc` や `~/.profile` を一切読み込みません。Bot はその `PATH` を引き継ぎ、Bot が起動する Claude / Codex の各セッションも同じ環境（除去されるシークレットを除く）を継承します。

そのため、ターミナルでは成功するビルドが Discord セッションの中では失敗したり、気づかないうちに古いシステム側のバイナリで実行されたりします。`~/.local/bin` や `~/.npm-global/bin` にインストールしたツールがサービスからは見えないためです。

サービスは `EnvironmentFile=` で `.env` を読み込むため、そこに `PATH` を設定すれば Bot と全セッションを一度に修正できます。

```bash
# .env — 対話シェルの PATH に合わせる
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

サービスを再起動し（`sudo systemctl restart mybot.service`）、Discord セッションで Claude に `which node && node --version` を実行させて確認してください。

### カスタム Cog（フォーク不要で機能拡張）

Python ファイルをディレクトリに追加するだけで独自機能を追加できます — フォーク不要、サブクラス不要、パッケージ不要:

```bash
ccdb start --cogs-dir ./my-cogs/
# または: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

ディレクトリ内の各 `.py` ファイルは `async def setup(bot, runner, components)` を公開する必要があります:

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

`_` で始まるファイルはスキップされます。1 つの Cog の読み込みが失敗しても、他は正常に読み込まれます。

リマインダー、自動アップグレード、ドキュメント同期を含む実例は [`examples/ebibot/`](../../../examples/ebibot) を参照してください。

**`examples/ebibot/cogs/` に含まれる組み込みサンプル:**

| Cog | 用途 |
|-----|------|
| `ReminderCog` | Discord ベースのリマインダースケジューリング |
| `AutoUpgradeCog` | Webhook トリガーによるパッケージ自動アップグレード |
| `DocsSyncCog` | プッシュ時の自動ドキュメント同期 |
| `AlertResponderCog` | 汎用アラート監視 — 監視システムからのアラートを Discord に転送し、Claude Code による調査セッションをトリガー |
| `JobFailureTriageCog` | Webhook の embed として投稿されたスケジューラージョブの失敗を自動でトリアージ |
| `ThreadCompletionCog` | 「スレッドを削除した」＝「その作業が完了した」とみなす — 削除をまとめて、残っている transcript から作業記録を書き起こす。`/thread-completion on` を実行するまでは無効 |

---

### ミニマル Bot（パッケージとしてインストール）

すでに discord.py Bot を動かしている場合は、ccdb をパッケージとして追加します:

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

`bot.py` を作成します:

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

`setup_bridge()` はすべての Cog を自動的に配線します。最新版へのアップデート:

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### マルチチャンネル設定

複数の Discord チャンネルに Bot を展開するには、`claude_channel_id` に加えて（または代わりに）`claude_channel_ids` を指定します:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_id=YOUR_CHANNEL_ID,       # プライマリ（スレッド作成のフォールバック）
    claude_channel_ids={
        YOUR_CHANNEL_ID,
        YOUR_CHANNEL_ID_2,
    },
    allowed_user_ids={YOUR_USER_ID},
)
```

設定されたチャンネルはそれぞれ完全に独立して動作します。どのチャンネルへのメッセージも新しい Claude セッションスレッドを起動し、`/skill` コマンドもすべてのチャンネルで機能します。`claude_channel_id` は後方互換性のために残されており、`/skill` コマンドが設定外チャンネルから実行された場合のフォールバック先として使用されます。

#### Bot が反応する場所 — メンション不要チャンネルと @メンション

`claude_channel_ids` は **@メンションが不要**なチャンネルのリストです。そこに投稿されたもの、およびその配下のスレッド内のメッセージは、すべてそのまま Claude に渡ります。ギルド内のそれ以外の場所では、Bot は **@メンションされたときだけ**応答します。

つまりこの設定が記述するのは、ccdb が「どこに存在するか」ではなく「どこで*自由に*発言してよいか」です。誰も気に留めなかったチャンネルは構造的に静かなままで、それでいて Bot は設定を変更しなくてもどこからでも名前で呼び出せます。

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111},          # #111: メンション不要。全メッセージが Claude に渡る
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
# ギルド内のそれ以外の場所: 「@YourBot どう思う?」でセッションが始まる。それ以外は無反応。
```

Claude が起動するのは、次のどちらかを満たす場合です:

- メッセージが**メンション不要チャンネル**（またはその配下のスレッド）にある — こちらはセッションフローです。チャンネルへのメッセージがスレッドを開き、そのスレッド内の返信がセッションを継続します
- そのメッセージで Bot が **@メンション**されている — それ以外のあらゆる場所では *1 通ごと*に必要で、ccdb 自身が開いたスレッドの中でも例外ではありません。スレッドを所有していることは恒久的な同意ではありません。そうしたスレッドでも人間同士の会話は続くものであり、誰も頼んでいない実行はノイズでしかないからです

**メンションには、その場で答えます。** スレッドは作成されません。チャンネルでのメンションはそのチャンネルで、スレッドでのメンションはそのスレッドで応答します。スレッドを切り出してしまうと、答えがそれを促した議論から引き離され、誰も読んでいない場所にセッションが走り続けることになります。そのチャンネル・スレッドに既存のセッションがあれば再開されるため、続けてメンションすれば同じ作業の続きになります。

ダイレクトメッセージがメンション経路で拾われることはありません。また、ユーザー単位のゲート（`allowed_user_ids`）は従来どおり、あらゆる場所で最初に適用されます。

以前の厳格な挙動 — Bot は設定されたチャンネルに*のみ*存在し、それ以外の場所でメンションしても何も起きない — に戻すには、メンション経路をオフにします:

```
CCDB_MENTION_ANYWHERE=false
```

##### 答える前に場の空気を読む

メンションはたいてい、Claude が見ていない会話の途中に飛んできます。ccdb は応答する前に、**まさにそのチャンネルまたはスレッドの直近 7 日間**のトランスクリプトをプロンプトの先頭に付加し、実際に議論されていた内容について答えられるようにします。スレッド全体を読むのは一見自然な発想ですが誤りです — 数か月前から続くスレッドは、大部分が無関係なまま多量のトークンを消費します。そのためウィンドウは 3 つの軸で制限されています: 経過日数、メッセージ件数（200 件）、そして合計サイズ（12,000 文字。**古い方から**切り詰めるため、返信対象となる直近のやり取りは必ず残ります）。

```
CCDB_THREAD_CONTEXT_DAYS=7   # 0 でトランスクリプトを完全に無効化
```

メンション不要チャンネルの内側では、このトランスクリプトが使われるのは ccdb が作成していないスレッドに限られます。自身のセッションスレッドではやり取りをすべて見ているため、再送してもトークンを消費するだけです。

##### メンション専用チャンネル（レガシー）

`mention_only_channel_ids` は、メンション不要チャンネルの集合から特定のチャンネルを除外します。メンション経路が有効な状態では、単にそのチャンネルを*リストに載せない*だけで同じ効果が得られるため、これが役立つのは、親チャンネルをリストに載せたうえで特定の子チャンネルだけを除外したい場合に限られます。

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

#### インライン返信チャンネル

特定のチャンネルで**スレッドを作らずにチャンネル内に直接返信**させることができます（個人コマンドチャンネルなど、スレッドが煩わしい場合に便利）:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # #333 ではスレッドを作らずインライン返信
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

環境変数でも設定可能（カンマ区切りのチャンネル ID）:

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

インライン返信モードでは、Claude の返信は新しいスレッドではなくチャンネル内のメッセージとして直接送信されます。チャンネル自体が 1 つの継続した会話となります。セッションは内部で追跡されるため、その後のメッセージも `/clear` でリセットするまで同じ Claude セッションを再開します。

#### チャットのみチャンネル

特定のチャンネルで技術的な UI（ツール埋め込み、thinking ブロック、セッション開始/完了通知、Todo リスト）を非表示にし、**Claude のテキスト返信のみ**を表示する — 非技術系ユーザーが見ている公開チャンネルに便利です:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # #444 ではテキストのみ表示、ツール詳細は非表示
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

環境変数でも設定可能（カンマ区切りのチャンネル ID）:

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

チャットのみモードでも、パーミッションリクエストと `AskUserQuestion` プロンプトは**常に表示**されます — 人間の入力が必要なため、必ず表示する必要があります。

## 設定

アイドルデッドライン、添付ファイルの再送、認証情報、起動時ロールバックについては [ランタイム復旧とコントロールプレーンの境界](../../runtime-reliability.md) を参照してください。

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `DISCORD_BOT_TOKEN` | Discord Bot トークン | （必須） |
| `DISCORD_CHANNEL_ID` | Claude チャット用チャンネル ID | （必須） |
| `CCDB_BACKEND` | 使用するバックエンド: `claude`、`codex`、`local`、`agui`、または `pi` | `claude` |
| `CCDB_COMMAND` | CLI バイナリのパスまたは名前（`CLAUDE_COMMAND` より優先）。`CCDB_BACKEND` で選択された初期ランナーに使用され、実行時に `/backend` で切り替えた際は以下の 2 つのバックエンド別変数が優先されます。 | _（自動: `claude` or `codex`）_ |
| `CCDB_CLAUDE_COMMAND` | Claude CLI バイナリの明示的なパス。`/backend claude` がアクティブなとき `BackendFactory` が使用（`CCDB_BACKEND` の初期値に依存しない）。`CLAUDE_COMMAND`、次に `claude`（PATH）へのフォールバックあり。 | （オプション） |
| `CCDB_CODEX_COMMAND` | OpenAI Codex CLI バイナリの明示的なパス。systemd 下で Bot を実行する場合に必須（デフォルトのサービス PATH に `~/.npm-global/bin` が含まれない）。`codex`（PATH）へのフォールバックあり。 | （オプション） |
| `CCDB_CODEX_SANDBOX_OVERRIDE` | Codex の `--sandbox` をデプロイ全体で上書きする任意設定: `read-only`、`workspace-write`、`danger-full-access`。未設定なら Codex CLI のデフォルトを使用します。`danger-full-access` は、ホスト側の外側の隔離が信頼でき、かつ OS の namespace 制限によって Codex 自身の sandbox が起動できない場合にのみ使ってください。この設定はスレッド単位では意図的に公開していません。 | （オプション） |
| `CCDB_AGUI_URL` | `/backend agui` で使用する正確な HTTP(S) run endpoint。redirect は拒否されます。 | （`agui` では必須） |
| `CCDB_AGUI_TOKEN` | AG-UI endpoint 用の任意の bearer token。Claude/Codex subprocess の環境から除去されます。 | （オプション） |
| `CCDB_PI_COMMAND` | [pi](https://github.com/earendil-works/pi) CLI バイナリの明示的なパス。`pi`（PATH）へのフォールバックあり。 | （オプション） |
| `CCDB_PI_ALLOW_UNSANDBOXED` | `/backend pi` の起動を許可する場合に `1`。pi は非対話モードで sandbox も承認ループも持たないため、既定では ccdb がターンを拒否します。[pi バックエンド](../../pi-backend.md) 参照。 | `0`（バックエンドは拒否） |
| `CCDB_PI_MODEL` | pi の既定モデル。プロバイダ込みの完全修飾名で指定します（`anthropic/claude-opus-5`、`ollama/gpt-oss:120b`）。未設定の場合は pi 自身の既定モデルが選ばれますが、それはアカウントの認証情報で呼び出せるモデルとは限りません。`/model set` はスレッド単位・全体のいずれでもこの値を上書きします。[pi バックエンド](../../pi-backend.md) 参照。 | （オプション） |
| `CCDB_PI_HOME` | pi の設定ディレクトリが `~/.pi` でない場合に指定します。`/model` のオートコンプリートが読むモデルカタログの探索にのみ使われます。 | `~/.pi` |
| `CONTEXT_LINKS_CONFIG` | `ContextLinksCog` が読むコンテキストリンク JSON ファイルのパス。ファイルが無い場合・壊れている場合は、この機能が黙って無効になります。 | `context_links.json` |
| `CCDB_PUBLIC_API_URL` | `/open/obsidian?vault=…&file=…` を対応する `obsidian://` URI へリダイレクトする HTTPS エンドポイントのベース URL。設定すると Obsidian のコンテキストリンクが embed 内のテキストではなくクリック可能な Discord ボタンとして表示されます。このリダイレクト自体は ccdb が提供するものではありません。 | （オプション） |
| `CCDB_PI_APPROVE_PROJECT` | プロジェクト固有の `.pi/` 設定・skill・extension を pi に読み込ませる場合に `1`。チェックアウトしたリポジトリが、その中で動くエージェントを再設定できないよう既定はオフです。 | `0` |
| `CCDB_PI_CLAUDE_PLUGIN_SKILLS` | 有効化済みでユーザースコープの Claude Code プラグインの skill を `--skill` で pi に渡し、Claude スレッドと同じプラグイン skill を pi スレッドでも使えるようにします。`0` で無効化。 | `1` |
| `PATH` | Bot **と Bot が起動する全 CLI セッション**のバイナリ検索パス（セッションは Bot の環境を継承）。systemd はユニットを最小限の PATH で起動し `~/.bashrc` / `~/.profile` を読まないため、systemd 運用時は `.env` に設定する。[ツールチェーンの PATH](#ツールチェーンの-path--env-に設定する) 参照 | （親プロセスから継承） |
| `CCDB_MODEL` | 使用するモデル（`CLAUDE_MODEL` より優先） | `sonnet` |
| `CCDB_MODEL_DISCOVERY` | `0` にすると、`/model` のオートコンプリートが Anthropic のモデル一覧エンドポイントへ「この認証情報から見えるモデル」を問い合わせるのをやめ（あわせて Codex CLI と pi CLI のローカルモデルカタログの読み取りもやめ）、常に静的な候補リストを使用する。この問い合わせは読み取り専用で、Claude Code CLI 自身の認証情報を再利用し、オフライン時・未認証時・Bedrock/Vertex/Foundry 利用時には自動的にフォールバックする | `1` |
| `CCDB_PERMISSION_MODE` | CLI のパーミッションモード（`CLAUDE_PERMISSION_MODE` より優先） | `acceptEdits` |
| `CCDB_STATUS_LANG` | Codex ステータス行のラベル（`週次` / `クレジット` / `上限到達`）の言語。`en` にすると UI の他の箇所で使われている英語に合わせて `7d` / `credits` / `limit reached` と表示されます。認識できない値は `ja` にフォールバックします。影響するのはこれらのラベルだけで、日本語のプロンプト文やフレーズ照合には影響しません。 | `ja` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | 全パーミッションチェックをスキップ（`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` より優先） | `false` |
| `CCDB_WORKING_DIR` | CLI の作業ディレクトリ（`CLAUDE_WORKING_DIR` より優先） | カレントディレクトリ |
| `CCDB_ALLOWED_TOOLS` | 許可するツールのカンマ区切りリスト（`CLAUDE_ALLOWED_TOOLS` より優先） | （オプション） |
| `CCDB_CHANNEL_IDS` | マルチチャンネル設定用の追加チャンネル ID（`CLAUDE_CHANNEL_IDS` より優先） | （オプション） |
| `CLAUDE_COMMAND` | Claude CLI バイナリのパスまたは名前（旧名 — `CCDB_COMMAND` を推奨）。特定バージョンを固定する場合に使用（例: `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`）— 新バージョンのリグレッション回避に便利。 | `claude` |
| `CLAUDE_MODEL` | 使用するモデル（旧名 — `CCDB_MODEL` を推奨） | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | CLI のパーミッションモード（旧名 — `CCDB_PERMISSION_MODE` を推奨） | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | 全パーミッションチェックをスキップ（旧名 — `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` を推奨） | `false` |
| `CLAUDE_WORKING_DIR` | Claude の作業ディレクトリ（旧名 — `CCDB_WORKING_DIR` を推奨） | カレントディレクトリ |
| `MAX_CONCURRENT_SESSIONS` | 最大並行 Claude CLI セッション数（チャット・スキル・スケジューラ・Webhook の全パスに適用）。[Ebi AI Console](../../console.md#running-work) から変更した上限（1〜32）は保存され、再起動時にはこの値より優先されます | `3` |
| `SESSION_TIMEOUT_SECONDS` | セッションの**アイドル**タイムアウト（秒）。出力が届くたびにリセットされるため、アクティブなストリームはこの値を超えて継続しうる。`0` でデッドラインを明示的に無効化 | `300` |
| `CCDB_PR_COMPLETION_OWNER` | 指定したGitHub所有者の非Draft `session/<thread_id>` PRが残っている場合、同じAIを1回だけ自動継続して完了または具体的なブロッカー報告まで進める。認証済み`gh`が必要。 | （オプション） |
| `DISCORD_OWNER_ID` | Claude が入力待ちのとき @mention する Discord ユーザー ID | （オプション） |
| `COORDINATION_CHANNEL_ID` | AI Lounge チャンネルのデフォルトフォールバック用チャンネル ID | （オプション） |
| `CCDB_MENTION_ANYWHERE` | true のとき、ギルド内のどのチャンネル・スレッドでも @メンションで Claude を呼び出せる。`false` にすると設定されたチャンネルのみを監視 | `true` |
| `CCDB_THREAD_CONTEXT_DAYS` | メンションで Claude が起動したとき、その場のチャンネル・スレッドの履歴を何日分プロンプトに付加するか（`0` で無効） | `7` |
| `MENTION_ONLY_CHANNEL_IDS` | メンション不要チャンネルの集合から除外するチャンネル ID（カンマ区切り。レガシー — 現在はチャンネルをリストに載せないだけで同じ効果が得られる） | （オプション） |
| `INLINE_REPLY_CHANNEL_IDS` | インライン返信チャンネル ID（カンマ区切り、スレッドを作成しない） | （オプション） |
| `CHAT_ONLY_CHANNEL_IDS` | チャットのみモードのチャンネル ID（カンマ区切り）— Claude のテキスト応答のみ表示。ツール embed・思考・セッション情報・Todo はすべて非表示 | （オプション） |
| `WORKTREE_BASE_DIR` | リポジトリ群の親ディレクトリ。セッション Worktree のスキャン対象になる。設定を強く推奨: セッションには必ず `wt-{thread_id}` の作成が指示されるため、未設定だと誰も削除しない | （オプション） |
| `CLI_SESSIONS_PATH` | CLI セッション検出用のパス（`~/.claude/projects`）。`/sync-sessions` の有効化と、トランスクリプトの本文検索（`/search body:True`、`GET /api/search?body=1`）に使用。デフォルトは標準の `~/.claude/projects` なので、Claude Code を実行した環境ならゼロコンフィグで本文検索が使える | （オプション） |
| `CUSTOM_COGS_DIR` | 起動時に読み込むカスタム Cog ファイルを含むディレクトリ（[カスタム Cog](#カスタム-cogフォーク不要で機能拡張) 参照） | （オプション） |
| `CLAUDE_ALLOWED_TOOLS` | Claude CLI に許可するツールのカンマ区切りリスト（旧名 — `CCDB_ALLOWED_TOOLS` を推奨） | （オプション） |
| `CLAUDE_CHANNEL_IDS` | マルチチャンネル設定用の追加チャンネル ID（旧名 — `CCDB_CHANNEL_IDS` を推奨） | （オプション） |
| `CCDB_SPAWN_THREAD_MARKER` | `POST /api/spawn` で作成したスレッドのタイトル先頭に付けるマーカー。エージェントが起動したスレッドをチャンネル一覧で見分けるためのもの。空文字で無効化 | `🤖` |
| `CCDB_SPAWN_PARENT_MARKER` | 子スレッドをスポーンしたスレッド自身のタイトル先頭に付けるマーカー。空文字で無効化 | `🌳` |
| `CCDB_DONE_THREAD_MARKER` | エージェントが作業完了を報告したスレッドのタイトル先頭に付けるマーカー（次に人間が返信すると外れる）。空文字で無効化 | `✅` |
| `CCDB_WAITING_THREAD_MARKER` | ターンが人間の返答待ちで終わったとき（AskUserQuestion が開いている、または `POST /api/threads/{id}/waiting`）にタイトル先頭に付けるマーカー。次に人間が返信すると外れる。空文字で無効化 | `❓` |
| `CCDB_REVIEW_THREAD_MARKER` | ターンが成果物の確認待ちで終わったとき（`POST /api/threads/{id}/review`）にタイトル先頭に付けるマーカー。次に人間が返信すると外れる。空文字で無効化 | `👀` |
| `CCDB_ACTION_THREAD_MARKER` | ターンが人間の作業待ち（Todo）で終わったとき（`POST /api/threads/{id}/action`）にタイトル先頭に付けるマーカー。次に人間が返信すると外れる。空文字で無効化 | `📋` |
| `CCDB_ERROR_THREAD_MARKER` | ターンがエラーで終わったときにタイトル先頭に付けるマーカー。次に人間が返信すると外れる。空文字で無効化 | `⚠️` |
| `CCDB_SCHEDULED_THREAD_MARKER` | スケジュールされたタスクの投稿を待っているスレッドのタイトル先頭に付けるマーカー（発火・無効化・削除で外れる）。空文字で無効化 | `⏰` |
| `CCDB_WAIT_CWD_ROOTS` | wait プローブの `cwd` が配下になければならないディレクトリ（`os.pathsep` 区切り、`realpath` 後に判定） | relay ユーザーのホーム |
| `THREAD_INBOX_ENABLED` | 永続スレッドインボックスを有効化（`claude -p` でセッションを `waiting`/`done`/`ambiguous` に分類し、スレッドダッシュボードに表示） | `false` |
| `THREAD_AUTO_RENAME` | 新しいスレッドのタイトルを Claude AI で自動リネーム — 最初のユーザーメッセージをもとにバックグラウンドの `claude -p` 呼び出しで短く分かりやすいタイトルを生成（セッション開始を遅延させない）。以降も話題が明確に変わったらタイトルを更新する（15分に1回まで・系譜タグは保持） | `false` |
| `CCDB_ACCOUNT_POOLS_FILE` | バックエンドごとにログイン済みの Claude Code / Codex プロファイルディレクトリと、セッションまたはターンごとにどれを使うかの戦略を記述した TOML ファイルのパス。起動時に検証される。未設定ならバックエンドごとに暗黙のログイン1つのまま。[アカウントプール](../../account-pools.md) を参照 | （オプション） |
| `CCDB_CLI_ENV_FILE` | CLI サブプロセス起動時に毎回環境変数へマージする `KEY=VALUE` ファイルのパス。Bot を再起動せずに即座に反映される。一時的な API ルーティング（Azure Foundry への切り替えなど）に便利 | （オプション） |
| `CCDB_LOG_FILE` | ログファイルのパス。設定するとデフォルトの stdout ハンドラに加えてローテーティングファイルハンドラ（10 MB × 5 バックアップ）が追加される。監視・アラートに便利 | （オプション） |
| `API_HOST` | REST API バインドアドレス | `127.0.0.1` |
| `API_PORT` | REST API ポート（設定すると REST API が有効になる） | （オプション） |
| `CCDB_API_SECRET` | コントロールプレーン用のオプションの Bearer シークレット。セッションランナーにも同じ値が渡される。ループバック以外にバインドする場合は必須 | （オプション） |
| `CCDB_CONTROL_PLANE_HOST_GUARD` | コントロールプレーンでローカル以外の Host / Origin と転送ヘッダを拒否する。認証済みプロキシを意図的に使う場合は `0` で無効化 | `1` |
| `CCDB_INGEST_TOKEN` | `POST /api/ingest` 用の Bearer トークン（`api_secret` とは独立）。未設定ならこのエンドポイントは `503` を返す | （オプション） |
| `CCDB_INGEST_REQUIRE_COMPLETE` | `1` を設定すると、`attachments_manifest` によって添付ファイルの欠落が判明したインジェストを、部分的な証拠でセッションを開始せずに `409` で拒否する | `0` |
| `CCDB_TEAMS_VAULT_ROOT` | `POST /api/teams/sync` が上流スレッドをミラーリングする先のディレクトリ（メッセージ 1 件につき 1 ファイル）。`CCDB_INGEST_TOKEN` で保護される | `{working_dir}/teams` |
| `CCDB_CONSOLE_PORT` | [Ebi AI Console](../../console.md) のリスナーのポート（`console` extra が必要）。設定しない限り無効で、認証なしでは動かない（既定はパスキーでのサインイン） | (optional) |
| `CCDB_CONSOLE_HOST` | Ebi AI Console のバインドアドレス。トンネルの背後でループバックのままにする | `127.0.0.1` |
| `CCDB_CONSOLE_ACCESS_TEAM_DOMAIN` | コンソールが JWT を検証する Cloudflare Access のチームドメイン（`myteam.cloudflareaccess.com`） | (optional) |
| `CCDB_CONSOLE_ACCESS_AUD` | コンソールの前段にある Cloudflare Access アプリケーションの AUD タグ | (optional) |
| `CCDB_CONSOLE_ALLOWED_EMAILS` | Access 経由で許可するメールアドレス（カンマ区切り）。Access 使用時は必須で、Access ポリシーを誤って編集してもコンソールは閉じたまま | (optional) |
| `CCDB_CONSOLE_TOKEN` | ローカルクライアント用の Bearer トークン（32 文字以上）。これ単独で使うのはプライベートなネットワーク（tailnet、SSH トンネル、ループバック）に限る | (optional) |
| `CCDB_CONSOLE_PASSKEYS` | `0` でパスキーによるサインインを無効化する（その場合は Access かトークンが必須） | `1` |
| `CCDB_CONSOLE_ORIGIN` | コンソールを利用できるオリジン（カンマ区切り。例: `https://console.example.com`）。プロキシが `Host` を書き換える場合に設定する | (リクエストから取得) |
| `CCDB_CONSOLE_SESSION_DAYS` | コンソールのサインインの有効日数 | `30` |
| `CCDB_CONSOLE_ENROLL` | `1` にすると、パスキーが登録済みでも起動時に新しい使い捨てセットアップコードをログに書き出す（パスキーをすべて失ったときの復旧用） | (optional) |
| `CCDB_CONSOLE_TRIAGE` | `0` でコンソールの[自動トリアージ](../../console.md#automatic-triage)（高速なモデルによる優先度・期日・次の一手・場所の推定）を無効化する | `1` |
| `CCDB_CONSOLE_TRIAGE_MODEL` | Claude バックエンドでのトリアージに使うモデル（`claude --model`） | `haiku` |
| `CCDB_CONSOLE_TRIAGE_CODEX_MODEL` | Codex バックエンドでのトリアージに使うモデル（`codex exec -m`） | (Codex のカタログで最も安いモデル) |
| `EBI_WORKSPACE_NOTES_FOLDER` | 任意の [Console の Workspace 連携](../../console.md#optional-ebi-workspace)に使用する notes-folder の絶対パス。`02_Contexts` が必要です。`CLAUDE_WORKSPACE_NOTES_FOLDER` も利用できます。未設定の場合は `${XDG_CONFIG_HOME:-~/.config}/ebi-workspace/notes-folder`、次に `~/.claude/settings.local.json` の `env` 内の同じ変数を確認します。既定の Vault を推測して有効にすることはありません | （オプション） |

### パーミッションモード — `-p` モードで動作するもの

ccdb を通じて使用する場合、Claude Code CLI は **`-p`（非インタラクティブ）モード** で動作します。このモードでは、CLI は **権限の確認ができない** ため、承認が必要なツールは即座に拒否されます。これは ccdb の制限ではなく、[CLI の設計上の制約](https://code.claude.com/docs/en/headless)です。

| モード | `-p` モードでの動作 | 推奨 |
|------|----------------------|----------------|
| `default` | ❌ **すべてのツールが拒否される** — 使用不可 | 使用しないこと |
| `acceptEdits` | ⚠️ Edit/Write は自動承認、Bash は拒否（Claude はファイル操作に Write にフォールバック） | 最低限動作するオプション |
| `bypassPermissions` | ✅ すべてのツールが承認される | 動作するが、以下を推奨 |
| **`auto`** | ✅ **AI による安全性分類** — 安全な操作は自動承認、危険な操作はブロック | **推奨** — 安全性と使いやすさのベストバランス |
| `plan` | ✅ AI による分類（読み取り優先）— auto と同様だが、より保守的 | 読み取り中心のワークフロー向け |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **すべてのツールが承認される（安全チェックなし）** | レガシー「yolo」モード — auto モードで制限が強すぎる場合に使用 |

**推奨設定:** `CLAUDE_PERMISSION_MODE=auto` を設定してください。auto モードは AI 分類器を使用して、安全な操作（ファイル編集、ローカルテスト、作業ブランチへの git push）を自動承認しつつ、危険な操作（force push、本番デプロイ、認証情報の漏洩）をブロックします。yolo モードの「何でも許可」リスクなしで、通常の開発作業において Claude に完全な自律性を与えられます。

**yolo モードへのフォールバック:** auto モードで必要な操作がブロックされる場合は、代わりに `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` を設定してください。ccdb が `allowed_user_ids` で Claude との対話を制御しているため、CLI レベルのパーミッションチェックは意味のあるセキュリティ上の利点なしに摩擦を生むだけです。名前に含まれる「dangerously（危険）」は CLI の汎用的な警告を反映したものですが、アクセスがすでにゲートされている ccdb の文脈では、実用的な選択肢です。

> **注意:** `CLAUDE_PERMISSION_MODE` を `auto` または `plan` に設定した場合、`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` は自動的に無視されます — これらのモードは独自の安全分類器を持っており、yolo フラグによってオーバーライドされてしまいます。

**細かい制御を行うには**、`CLAUDE_ALLOWED_TOOLS` を使用して、すべてのパーミッションをバイパスせずに特定のツールのみを許可できます：

```env
# 例：ファイル操作とコード実行を許可するが、Web アクセスは不許可
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# 例：読み取り専用モード — Claude は探索できるが変更は不可
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

主なツール名：`Bash`、`Read`、`Write`、`Edit`、`Glob`、`Grep`、`WebFetch`、`WebSearch`、`NotebookEdit`。この設定を使用する場合は `CLAUDE_PERMISSION_MODE=default` にしてください（他のモードではオーバーライドされる場合があります）。

**Discord からの実行時変更：** `/tools-set` を使用すると、Bot を再起動せずに実行時に許可ツールを変更できます。設定は永続化され、以降のすべての新規セッションに即座に反映されます。現在の設定を確認するには `/tools-show`、`.env` デフォルトに戻すには `/tools-reset` を使用してください。

> **Discord のパーミッションボタン：** `CLAUDE_PERMISSION_MODE=default` の場合、Claude は `permission_request` イベントを発行し、ccdb はスレッドに許可／拒否ボタンを表示します。stdin は常時開放（stream-json 入力モード）されているため、Bot が Claude へ応答を返すことができます。`auto` または `plan` モードを使用している場合、Claude はユーザーの操作なしに自動でパーミッションを処理します。`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`（yolo モード）の場合、ccdb は `permission_request` イベントを**即座に自動承認**します — 許可／拒否ボタンは表示されません。これは CLI のリグレッション（v2.1.78+、upstream [#35895](https://github.com/anthropics/claude-code/issues/35895)）の回避策で、`--dangerously-skip-permissions` がファイルレベルの機密パスチェックをバイパスできない問題に対処しています。

## Discord Bot のセットアップ

1. [Discord Developer Portal](https://discord.com/developers/applications) で新しいアプリケーションを作成
2. Bot を作成してトークンをコピー
3. Privileged Gateway Intents で **Message Content Intent** を有効化
4. 以下の権限で Bot をサーバーに招待:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages（リアクション削除のため）
   - Read Message History

## Microsoft Teams

Teams は port ではなく、本番対応の**兄弟フロントエンド**です。`claude_discord` と
`claude_teams` はそれぞれ `claude_code_core.frontend` の共通語彙を実装し、互いを
import しません。両方に同じ conformance contract を実行することで、「Teams 側に機能が
足りない」という問題を利用者が何か月も後になって発見する事態を防ぎます。

```bash
uv sync --extra teams
python -m claude_teams manifest --out dist/teams-app.zip
```

通常の launcher は、`CCDB_FRONTENDS=discord,teams` の設定により Discord と Teams を同時に
実行します。公開レシーバーが Bot Framework token を検証して activity を queue へ追加し、private 側の
`ActivityPuller` が outbound 接続で取得します。その後、Discord と同じ session runner へ各 prompt を
送り、結果を Teams へ投稿します。session host に Teams 向けの inbound listener は不要です。

Teams の体験は Discord をそのまま移植したものではありません。Discord では 15 件に分割される回答も
**1 件**で届き、ツール呼び出しごとに縦に並ぶ embed は、**更新され続ける 1 枚の card**になります。
Teams では会話ごとに 1 時間あたり 1,800 回の操作までしか許されないため、Discord と同じ設計では
長いセッションの操作枠を会話履歴への投稿だけで使い切ってしまいます。

prompt には回答できます。card を押すと invoke が届き、その card を投稿した会話との対応を検証した
うえで、回答を待っているセッションに渡します。回答のない permission request は拒否され、card を
投稿できなかった場合も同様です。誰にも見えなかった prompt を、誰も回答しなかった prompt より
安全なものとして扱うことはありません。

Teams surface は、1 回限りの upload URL を使った personal chat の file consent に対応します。byte を
送る前に、URL の host が Microsoft 自身の domain であることを確認します。channel への file delivery は
未対応で、private queue relay も file-consent invoke をまだ bridge しません。この surface-level の差に
より、conformance contract は 2 回実行されます。personal chat は 18 項目すべてに合格し、channel は
該当する 1 項目だけ不合格になることを test で確認しています。

Discord と異なり、Teams には**公開 HTTPS endpoint**、Entra application、Azure Bot、install 可能な
Teams app package、および公開側と private 側を結ぶ queue が必要です。値は tenant 固有なので、安全かつ
正確な万能 manifest をリポジトリへ check in することはできません。

登録から最初の Teams → agent → Teams round trip までは、
[Teamsセットアップガイド](../../teams-setup.md)に沿って進めてください。機能の詳細は
[Teams surface の動作](../../teams.md)、trust boundary と実測運用は
[relay のセキュリティモデル](../../teams-relay.md)を参照してください。

---

[できること](features.md) · [機能リファレンス](reference.md) · **はじめかた** · [自動化・スケジューラー・REST API](automation.md) · [アーキテクチャとプロジェクトの背景](architecture.md)
