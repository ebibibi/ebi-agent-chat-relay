# 自動化・スケジューラー・REST API

> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。内容に相違がある場合は英語版が優先されます。

← [README](../README.md) に戻る · [できること](features.md) · [機能リファレンス](reference.md) · [はじめかた](getting-started.md) · **自動化・スケジューラー・REST API** · [アーキテクチャとプロジェクトの背景](architecture.md)

## GitHub + Claude Code 自動化

### 例: 自動ドキュメント同期

main へのプッシュごとに Claude Code が:
1. 最新の変更をプルして diff を分析
2. 英語ドキュメントを更新
3. 日本語（または任意の対象言語）に翻訳
4. バイリンガルな要約付き PR を作成
5. 自動マージを有効化 — CI 通過後に PR が自動マージ

**GitHub Actions:**

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

**Bot の設定:**

```python
from claude_discord import WebhookTriggerCog, WebhookTrigger, ClaudeRunner

runner = ClaudeRunner(command="claude", model="sonnet")

triggers = {
    "🔄 docs-sync": WebhookTrigger(
        prompt="変更を分析し、ドキュメントを更新し、バイリンガルな要約付き PR を作成し、自動マージを有効化。",
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

**セキュリティ:** プロンプトはサーバー側で定義。Webhook はどのトリガーを発火するかを選択するだけ — 任意のプロンプトインジェクションはなし。

### 例: オーナー PR の自動承認

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
    steps:
      - name: ワークフロートークンで承認
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: gh pr review "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --approve
      - name: リポジトリ所有者として auto-merge を有効化
        env:
          GH_TOKEN: ${{ secrets.ADMIN_PAT }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --squash
```

`ADMIN_PAT` には必要最小限の権限を持つ所有者トークンを使い、リポジトリの
Actions secret として保管し、リリース資格情報と同様にワークフローを保護する。
auto-merge を有効化した主体が、後で完了するマージの主体になるため、このトークンが
重要になる。`GITHUB_TOKEN` 名義のマージは `push` イベントを発火せず、
`Closes #123` の Issue も閉じないが、所有者名義なら両方とも動く。マージ後の処理は
このジョブ内でポーリングせず、`main` への `push` をトリガーとする別ワークフローに
置く。実測結果とトレードオフは
[ワークフローイベントを発火させるマージ](../merge-events.md) を参照。

## スケジュールタスク

コード変更なし、再デプロイなしで、ランタイムに定期的な Claude Code タスクを登録。

Discord セッション内から Claude がタスクを登録:

```bash
# Claude がセッション内で呼び出す:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "古い依存関係をチェックして見つかったら Issue を開く", "interval_seconds": 604800}'
```

または独自のスクリプトから登録:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "週次セキュリティスキャン", "interval_seconds": 604800}'
```

30 秒マスターループが期限のタスクを検出し、Claude Code セッションを自動起動します。

### タスクをバックエンドとモデルに固定する

タスクは、発火した時点で `/backend` が解決したバックエンドで実行されます。普段はそれで構いません。デプロイを Codex に切り替えれば夜間ジョブも一緒に移ります。ただし、いつもそうとは限りません。安価なダイジェストは人間が何を使っていようと小さなモデルで回すべきですし、あるエージェントのツール語彙を前提に書いたタスクが、黙って別のエージェントへ移るべきではありません。

`backend` はバックエンドを、`model` はそのバックエンド内のモデルを固定します。

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name": "nightly-digest", "prompt": "Summarise today'"'"'s commits",
       "interval_seconds": 86400, "channel_id": 123,
       "backend": "codex", "model": "gpt-5.6-sol"}'
```

- `backend` は `claude`、`codex`、`local`、`agui`、`pi`、`antigravity` のいずれかです。
- `model` は自由記述（`/model set` が受け付けるのと同じ ID）ですが、**バックエンドと一緒に指定したときだけ**有効です。モデル ID は 1 つのバックエンドに属するためです。モデルだけを送ると、その時たまたま有効だったバックエンドで毎晩失敗するタスクを作るのではなく、`400` を返します。
- 両方を省略すれば何も変わりません。既存のタスクと同じく、スレッド／グローバルの設定に従います。
- `PATCH /api/tasks/{id}` で固定を設定・変更でき、`null` で解除できます。モデルを固定したままバックエンドだけを解除する操作も、同じ理由で拒否されます。
- `/effort` は固定**されません**。タスクが最終的に実行されるバックエンドの設定から解決されるため、effort の変更は固定されたタスクにも反映されます。

## 自動アップグレード

新しいリリースが公開されたときに Bot を自動アップグレード:

```python
from claude_discord import AutoUpgradeCog, UpgradeConfig

config = UpgradeConfig(
    package_name="claude-code-discord-bridge",
    trigger_prefix="🔄 bot-upgrade",
    working_dir="/home/user/my-bot",
    restart_command=["sudo", "systemctl", "restart", "my-bot.service"],
    restart_approval=True,       # スレッドの ✅ リアクション、またはチャンネルのボタンで承認
    slash_command_enabled=True,  # /upgrade スラッシュコマンドを有効化（オプトイン、デフォルト False）
)

await bot.add_cog(AutoUpgradeCog(bot, config))
```

#### `/upgrade` スラッシュコマンドによる手動トリガー

`slash_command_enabled=True` の場合、認可されたユーザーが Discord から `/upgrade` を実行することで、Webhook なしで同じアップグレードパイプラインをトリガーできます。テキストチャンネルとスレッドの両方から実行でき（スレッド内から実行した場合は親チャンネルにアップグレードスレッドを作成します）。`upgrade_approval` および `restart_approval` のゲートが適用され、進捗スレッドが作成されます。すでにアップグレードが実行中の場合はエフェメラルで通知します。

再起動前に `AutoUpgradeCog` は以下の手順を実行します:

1. **アクティブセッションのスナップショット** — `_active_runners` を持つ Cog からアクティブなスレッド ID を収集（duck-typed で自動検出）。
2. **ドレイン** — アクティブセッションが自然に完了するまで待機。
3. **リジューム登録** — アクティブなスレッド ID を保留リジュームテーブルに保存。次回起動時に安全優先のプロンプトで再開：Claude は何をしていたかを報告し、実装作業（コード変更・コミット・PR 作成など）を再開する前に必ずユーザーに確認を取ります。これにより、コンテキスト圧縮でタスクの承認状態が消えた後に意図しない操作が実行されるのを防ぎます。
4. **再起動** — 設定した再起動コマンドを実行。

`active_count` プロパティを持つ Cog は自動検出されてドレインされます:

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

セッションのリジューム登録は完全にオプトイン方式です。`setup_bridge()` でセッション DB を初期化している場合（デフォルト）に有効になります。有効な場合、セッションは `--resume` の継続性を保ちながら再開されるため、Claude Code は中断した会話を正確に再開できます。

> **カバレッジ:** `AutoUpgradeCog` はアップグレードによる再起動を担当します。`systemctl stop`、`bot.close()`、SIGTERM など*その他すべて*のシャットダウンに対しては、`ClaudeChatCog.cog_unload()` が第二の自動セーフネットとして機能します。

## REST API

通知とタスク管理のためのオプション REST API。aiohttp が必要:

```bash
uv sync --extra api
```

### エンドポイント

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/api/health` | ヘルスチェック |
| POST | `/api/notify` | 即時通知の送信 |
| POST | `/api/schedule` | 通知のスケジュール |
| GET | `/api/scheduled` | 保留中の通知一覧 |
| DELETE | `/api/scheduled/{id}` | スケジュール済み通知のキャンセル |
| POST | `/api/tasks` | 定期的な Claude Code タスクを登録 |
| GET | `/api/tasks` | 登録済みタスクの一覧 |
| DELETE | `/api/tasks/{id}` | タスクの削除 |
| PATCH | `/api/tasks/{id}` | タスクの更新（有効/無効、スケジュール変更、バックエンド/モデルの固定） |
| POST | `/api/spawn` | 新しい Discord スレッドを作成し Claude Code セッションを起動（非ブロッキング）。`auto_start: false` を指定するとユーザーの最初の返信まで Claude の起動を延期でき、`user_id` を指定するとリクエスト元をスレッドに追加できる |
| POST | `/api/ingest` | 認証済み外部スポーン（ブラウザ拡張機能 / webhook）。base64 添付ファイル対応。結果取得が設定されている場合 `result_id` を返す |
| GET | `/api/ingest/{result_id}` | スポーンされたセッションの最終返信をポーリング（`status`/`result`/`error`/`thread_id`） |
| GET | `/api/ingest/summary` | 長時間続くインジェストスレッドの継続サマリー + `marker` を `key` で読み取り（ingest-token 認証）。クライアントは差分だけをエクスポートできる |
| POST | `/api/ingest/summary` | セッションから更新版の継続サマリー（`result_id` + `summary`）を保存 — localhost コントロールプレーン。ccdb が `marker` をインジェスト行から前進させる |
| DELETE | `/api/ingest/summary` | `key` の保存済みサマリーをクリアし、次回インジェストで完全な再サマリーを強制 |
| POST | `/api/teams/sync/plan` | 上流スレッドのミラーに何が足りないかを問い合わせる — ID + ハッシュを送ると `want_messages`/`want_attachments`/`newest_have_mid` が返る（ingest-token 認証） |
| POST | `/api/teams/sync/push` | plan が要求したメッセージを Vault 配下に 1 件 1 ファイルで保存。添付ファイルと追記専用の `chain.jsonl` を伴う |
| POST | `/api/mark-resume` | 次回 Bot 起動時のスレッド自動リジュームを登録 |
| GET | `/api/lounge` | AI Lounge の最近のメッセージを取得 |
| POST | `/api/lounge` | AI Lounge にメッセージを投稿（`label` オプション）。200 文字を超えると `hint` フィールドを返す |
| GET | `/api/sessions` | すべてのセッション（ライブ・保存済み）を状態・作業ディレクトリ・最新のラウンジメモ付きで一覧（`state=running`、`exclude_thread`、`limit`） |
| GET | `/api/slots` | セッション枠のキュー: `max_slots`、枠を保持しているスレッド（`running`）、開始順に並んだ待機キュー（`waiting`） |
| GET | `/api/restart` | 予約再起動の状態: `reserved`、`phase`（`idle` / `draining` / `restarting`）、実行中・待機中の件数、`last_error` |
| POST | `/api/restart` | 再起動を予約: キューを止め、何も動いていなくなったら再起動し、待機していたものをすべて再実行（202。予約済みなら 200） |
| DELETE | `/api/restart` | 予約を取り消してキューを再開（予約なしは 404、再起動中は 409） |
| POST | `/api/slots/{thread_id}/{action}` | キューを操作 — 待機中のスレッドを `prioritize` / `defer`、実行中のスレッドを `pause`（状態が合わなければ 404、`nothing_waiting` などの競合は 409、上限未設定なら 503） |
| GET | `/api/search` | キーワードから過去のスレッドを検索 — サマリーと作業ディレクトリへの `LIKE` クエリ。`body=1` を付けるとローカルの Claude トランスクリプトも grep（各ヒットに `snippet` と `source` が付く）。各ヒットを Discord `deep_link` 付きで返す（`q` 必須、任意の `origin`、`limit` は最大 50） |
| GET | `/api/threads/{thread_id}/messages` | 他スレッドの会話を古い順に取得（`limit`） |
| POST | `/api/claims` | 作業開始前にリソースを宣言 — 取得成功で 201、取得済みなら保持者情報付きで 409 |
| GET | `/api/claims` | 有効なクレームの一覧（`resource` フィルター任意） |
| DELETE | `/api/claims` | クレームの解放（`resource`、`thread_id`、任意で `force=true`） |
| POST | `/api/waits` | CI 実行中にターンを終える: プローブを登録（`thread_id`、`argv`、`pending_exit_codes` と `done_values` の少なくとも一方、任意で `interval_seconds`・`timeout_seconds`・`label`・`note`・`cwd`）。完了・タイムアウト・5 回連続の失敗でスレッドを再開。作成時 201、同じプローブが既に待機中なら 200、上限超過は 429、無効時は 503。詳細は [docs/waits.md](../../waits.md) |
| GET | `/api/waits` | 有効な wait と直近のプローブ結果の一覧（任意で `thread_id`） |
| DELETE | `/api/waits/{id}` | wait を取り消す（`thread_id` を付けると、そのスレッドが所有する場合のみ） |
| POST | `/api/threads/{thread_id}/message` | あるセッションから別のセッションへメッセージをリレー（`text`、`from_thread`、`mode`、`hop`） |
| POST | `/api/threads/{thread_id}/done` | スレッドを「終了してよい」状態にする（タイトル先頭に `✅`。冪等） |
| POST | `/api/threads/{thread_id}/waiting` | スレッドを「人間の返答待ち」状態にする（タイトル先頭に `❓`。`✅`/`⚠️` を置き換え、次に人間が返信すると外れる。冪等） |
| POST | `/api/threads/{thread_id}/review` | スレッドを「成果物の確認待ち」状態にする（タイトル先頭に `👀`。他の結果マーカーを置き換え、次に人間が返信すると外れる。冪等） |
| POST | `/api/threads/{thread_id}/action` | スレッドを「チャット外での人間の作業待ち（Todo）」状態にする（タイトル先頭に `📋`。他の結果マーカーを置き換え、次に人間が返信すると外れる。冪等） |

4 つのマーク用エンドポイントは `200`（`marked` または `unchanged`）を返します。Discord のレート制限でリネームが待機中の場合は `202`（`queued`）を返し、バックグラウンドで反映されます（再試行は不要です）。

```bash
# 通知の送信（埋め込み形式、デフォルト）
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "ビルド成功！", "title": "CI/CD"}'

# プレーンテキスト通知の送信（埋め込みなし）
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "デプロイ完了！", "format": "text"}'

# Discord Poll の送信
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "投票してください",
    "poll": {
      "question": "次のリリーストラックは？",
      "answers": ["安定版", "ベータ版", "ナイトリー"],
      "duration_hours": 24,
      "allow_multiselect": false
    }
  }'

# 定期タスクの登録
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "デイリースタンドアップ要約", "interval_seconds": 86400}'
```

---

[できること](features.md) · [機能リファレンス](reference.md) · [はじめかた](getting-started.md) · **自動化・スケジューラー・REST API** · [アーキテクチャとプロジェクトの背景](architecture.md)
