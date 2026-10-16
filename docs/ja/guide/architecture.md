# アーキテクチャとプロジェクトの背景

> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。内容に相違がある場合は英語版が優先されます。

← [README](../README.md) に戻る · [できること](features.md) · [機能リファレンス](reference.md) · [はじめかた](getting-started.md) · [自動化・スケジューラー・REST API](automation.md) · **アーキテクチャとプロジェクトの背景**

## アーキテクチャ

```
claude_code_core/          # バックエンド非依存のコアライブラリ
  backend.py               # SessionBackend プロトコル + create_backend() ファクトリー
  codex_runner.py          # OpenAI Codex CLI バックエンド
  runner.py                # Claude CLI subprocess マネージャー
  parser.py                # stream-json イベントパーサー
  types.py                 # SDK メッセージの型定義
  models.py                # SQLite スキーマ
  session_repo.py          # セッション CRUD
  thread_search.py         # /search のオーケストレーション — サマリー + 本文をマージしスレッド単位で重複排除
  transcript_search.py     # ~/.claude/projects トランスクリプトの grep/スキャン + スニペット抽出。
                           # find_transcript() は作業ディレクトリを知らなくても単一セッションのファイルを特定する
  lounge_repo.py           # AI Lounge メッセージ CRUD
  rewind.py                # セッションリワインドヘルパー
claude_discord/
  main.py                  # スタンドアロンエントリーポイント（setup_bridge + カスタム Cog ローダー）
  cli.py                   # CLI エントリーポイント（ccdb setup/start コマンド）
  setup.py                 # setup_bridge() — 1 行で Cog を配線
  cog_loader.py            # 動的カスタム Cog ローダー（CUSTOM_COGS_DIR）
  bot.py                   # Discord Bot クラス
  protocols.py             # 共有プロトコル（DrainAware）
  frontend.py              # DiscordFrontend — resolve/create a conversation by key
  stores.py                # build_session_stores() — every repo, no frontend
  concurrency.py           # Worktree 指示 + アクティブセッションレジストリ
  collision.py             # ファイル書き込み追跡 + 衝突判定ルール（純粋関数・時刻注入）
  lounge.py                # AI Lounge プロンプトビルダー
  session_view.py          # GET /api/sessions 用のクロスセッションビュー（純粋なマージロジック）
  relay.py                 # RelayGuard + リレープロンプトのラッパー（ホップ／クールダウン／レート制限）
  session_slots.py         # 操作できるセッション枠キュー（優先 / 後回し / 一時停止 + 自動再開）
  session_sync.py          # CLI セッションの検出とインポート
  worktree.py              # WorktreeManager — git worktree の安全なライフサイクル管理
  cogs/
    claude_chat.py         # インタラクティブチャット（スレッド作成、メッセージ処理）
    skill_command.py       # /skill スラッシュコマンド（オートコンプリート付き）
    session_manage.py      # /sessions, /search, /sync-sessions, /resume, /resume-info, /sync-settings
    session_queue.py       # /queue — セッション枠キューの確認と操作
    session_sync.py        # sync-sessions のスレッド作成・メッセージ投稿ロジック
    prompt_builder.py      # build_prompt_and_images() — 純粋関数、Cog/Bot 状態に非依存
    scheduler.py           # 定期 Claude Code タスク実行エンジン
    webhook_trigger.py     # Webhook → Claude Code タスク実行（CI/CD）
    auto_upgrade.py        # Webhook → パッケージアップグレード + DrainAware 再起動
    collision_watch.py     # 同一ファイルへ書き込むセッションを通知（60 秒ループ）
    event_processor.py     # EventProcessor — stream-json イベントのステートマシン
    run_config.py          # RunConfig データクラス — CLI 実行パラメーターをまとめる
    _run_helper.py         # 薄いオーケストレーション層（run_claude_with_config + shim）
  claude/
    runner.py              # claude_code_core からの ClaudeRunner 再エクスポート
    parser.py              # claude_code_core からの parse_line 再エクスポート
    types.py               # claude_code_core からの型定義再エクスポート
  database/
    models.py              # SQLite スキーマ
    repository.py          # セッション CRUD
    task_repo.py           # スケジュールタスク CRUD
    ask_repo.py            # 保留中 AskUserQuestion CRUD
    notification_repo.py   # スケジュール通知 CRUD
    lounge_repo.py         # AI Lounge メッセージ CRUD
    claims_repo.py         # アドバイザリなリソースクレーム CRUD（TTL 付き）
    resume_repo.py         # スタートアップリジューム CRUD（Bot 再起動をまたいだ保留リジューム）
    settings_repo.py       # ギルドごとの設定
    frontend_thread_repo.py  # ThreadKey → 会話の実際の場所
    inbox_repo.py          # スレッドインボックス CRUD（THREAD_INBOX_ENABLED）
  discord_ui/
    status.py              # 絵文字リアクションステータスマネージャー（デバウンス付き）
    chunker.py             # フェンス・テーブル対応メッセージ分割
    embeds.py              # Discord embed ビルダー
    views.py               # 停止ボタンと共有 UI コンポーネント
    prompt_views.py        # ChoiceView / FormModal — renders the protocol's prompts
    mentions.py            # user_mention_kwargs() — Claude が入力待ちのときリクエスターに通知
    ask_bus.py             # AskUserQuestion 通信用イベントバス
    ask_view.py            # AskUserQuestion 用 Discord ボタン / Select Menu
    ask_handler.py         # collect_ask_answers() — AskUserQuestion UI + DB ライフサイクル
    streaming_manager.py   # StreamingMessageManager — デバウンス付きインプレース編集
    tool_timer.py          # LiveToolTimer — 長時間ツール実行の経過時間カウンター
    thread_dashboard.py    # スレッドのセッション状態を表示する live ピン embed
    slot_views.py          # 待機メッセージと /queue 用のキュー操作ボタン・メニュー（⏫ / ⏬ / ⏸）
    file_sender.py         # .ccdb-attachments 経由のファイル配信
    inbox_classifier.py    # classify() — セッションにラベルを付ける軽量 claude -p 呼び出し
    thread_renamer.py      # suggest_title() — スレッド自動リネーム用バックグラウンド claude -p 呼び出し
  ext/
    api_server.py          # REST API サーバー（オプション、aiohttp が必要）
    ingest_manifest.py     # attachments_manifest と実際に届いたファイルの突合
    teams_sync.py          # /api/teams/sync（plan + push）の have/want ネゴシエーション
    teams_store.py         # TeamsVaultStore — 1 メッセージ 1 ファイル、chain.jsonl、_history/
  utils/
    logger.py              # ロギング設定
examples/
  ebibot/                  # 実例: カスタム Cog を使った個人 Bot
    cogs/
      reminder.py          # /remind スラッシュコマンド + スケジュール通知
      auto_upgrade.py      # GitHub webhook 経由の自己更新
      docs_sync.py         # push 時のドキュメント自動翻訳
```

### 設計思想

- **CLI スポーン、API ではない** — `claude -p --output-format stream-json` を呼び出し、Claude Code の全機能（CLAUDE.md、スキル、ツール、メモリ）を利用。Claude Pro/Max サブスクリプションで動作 — API キー不要、トークン課金なし
- **並行処理ファースト** — 複数の同時セッションが例外ではなく期待値。すべてのセッションに worktree 指示を注入し、レジストリと AI Lounge が残りを処理
- **Discord を接着剤として** — Discord が UI、スレッディング、リアクション、Webhook、永続的な通知を提供。カスタムフロントエンド不要
- **フレームワーク、アプリケーションではない** — パッケージとしてインストールし、既存の Bot に Cog を追加し、コードで設定
- **ゼロコード拡張性** — ソースを変更せずにスケジュールタスクと Webhook トリガーを追加
- **シンプルさによるセキュリティ** — 約 8000 行の監査可能な Python。subprocess exec のみ、シェル展開なし

## テスト

```bash
uv run pytest tests/ -v --cov=claude_discord
```

1690 件以上のテストがパーサー、チャンカー、リポジトリ、ランナー、ストリーミング、Webhook トリガー、自動アップグレード（`/upgrade` スラッシュコマンド、スレッド内実行、承認ボタン含む）、REST API、AskUserQuestion UI、スレッドダッシュボード、スケジュールタスク、セッション同期、AI Lounge、クロスセッション可観測性、リソースクレーム、セッション間リレー、スタートアップリジューム、モデル切り替え、コンパクト検出、TodoWrite 進捗 embed、カスタム Cog ローダー、許可／Elicitation／Plan Mode イベントパース、スレッドインボックス分類、スレッドごとのロック動作、SessionBackend プロトコル、CodexRunner、バックエンドファクトリー、バックエンドをまたぐセッション所有権をカバーしています。

## このプロジェクトの構築方法

**このコードベースは [Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — Anthropic の AI コーディングエージェント — によって、[@ebibibi](https://github.com/ebibibi) の指揮のもとで開発されています。人間の著者は要件を定義し、PR をレビューし、すべての変更を承認します — 実装は Claude Code が行います。

つまり:

- **実装は AI 生成** — アーキテクチャ、コード、テスト、ドキュメント
- **PR レベルでの人間によるレビューが行われています** — すべての変更は GitHub のプルリクエストと CI を経てマージされます
- **バグレポートと PR を歓迎します** — Claude Code を使って対応することになるでしょう
- **これは人間が指揮し AI が実装するオープンソースソフトウェアの実例です**

このプロジェクトは 2026-02-18 に開始され、Claude Code との反復的な会話を通じて進化し続けています。

## 実例

**[`examples/ebibot/`](../../../examples/ebibot)** — このフレームワーク上に構築された個人 Discord Bot がこのリポジトリに同梱されています。カスタム Cog ローダーを以下のもので実演しています:

- **ReminderCog** — `/remind HH:MM "メッセージ"` スラッシュコマンド + 30 秒送信ループ
- **AutoUpgradeCog** — GitHub webhook + systemctl restart による自己更新
- **DocsSyncCog** — push 時の Webhook 経由でドキュメントを自動翻訳
- **AlertResponderCog** — 汎用アラート監視 Cog。設定可能なソースを監視し、重要度付き通知を Discord に投稿
- **JobFailureTriageCog** — スケジューラージョブの失敗 embed を拾ってトリアージセッションを開始
- **ThreadCompletionCog** — スレッドの削除は作業完了の合図。削除をまとめて 1 件の作業記録にする。スレッドのメッセージはすでに消えているため、記録はセッションの transcript から組み立てる。何をどこに記録するかは Cog ではなく外部のプロンプトファイル（`THREAD_COMPLETION_PROMPT_FILE`）が決める。記録は `/thread-completion on` を実行するまで無効 — 環境変数はスイッチを「用意する」だけで、入れるかどうかは決めない

実行方法: `ccdb start --cogs-dir examples/ebibot/cogs/`

> EbiBot のカスタム Cog は以前は[別のリポジトリ](https://github.com/ebibibi/discord-bot)で管理されていましたが、現在はここに同梱されています。これにより Claude Code がフレームワークとカスタマイズの両方について完全なコンテキストを持てるようになり、機能の重複実装を防ぎます。

## インスパイアされたプロジェクト

- [OpenClaw](https://github.com/openclaw/openclaw) — 絵文字ステータスリアクション、メッセージデバウンシング、フェンス対応チャンキング
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — CLI スポーン + stream-json アプローチ
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — パーミッション制御パターン
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — スレッドごとの会話モデル

---

[できること](features.md) · [機能リファレンス](reference.md) · [はじめかた](getting-started.md) · [自動化・スケジューラー・REST API](automation.md) · **アーキテクチャとプロジェクトの背景**
