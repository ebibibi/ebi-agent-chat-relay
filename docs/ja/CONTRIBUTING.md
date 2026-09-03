> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../../CONTRIBUTING.md) takes precedence.
> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。
> 内容に相違がある場合は、[英語版](../../CONTRIBUTING.md)が優先されます。

# claude-code-discord-bridge へのコントリビューション

コントリビューションに興味を持っていただきありがとうございます！このプロジェクトは Claude Code によって構築されており、人間と AI エージェント両方からのコントリビューションを歓迎します。

## ブランチワークフロー

シンプルな PR ベースのワークフローである **GitHub Flow** を使用しています:

```
main（常にリリース可能）
  ├── feature/add-xxx   → PR → CI 通過 → レビュー → マージ
  ├── fix/issue-123     → PR → CI 通過 → レビュー → マージ
  └── （main への直接プッシュは禁止）
```

### 手順

1. リポジトリを **Fork**（書き込み権限がある場合はブランチを作成）
2. `main` から**ブランチを作成**:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **変更を加える** — コードを書き、テストを追加
4. ブランチを **Push** して `main` に対して **PR を開く**
5. **CI が自動実行** — Python 3.12/3.13 でテスト + lint、CodeQL セキュリティスキャン
6. CI が通過しレビューされたら、`main` に**マージ**

### ブランチ命名

- `feature/description` — 新機能
- `fix/description` または `fix/issue-123` — バグ修正
- `docs/description` — ドキュメントのみ
- `refactor/description` — 動作変更なしのコード整理

## 開発環境のセットアップ

```bash
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv sync --dev
make setup   # git hooks を登録（クローン後に一度だけ実行）
```

> **`make setup` は必須です** — 新しくクローンするたびに実行してください。`.githooks/` の pre-commit hook を有効化し、ステージされた Python ファイルの自動フォーマットと lint を行います。
> 実行しないと hook が動作せず、不正なコードがローカルで通過してしまいます（CI では検出されますが、予期せぬビルド失敗に驚くことになります）。
>
> `make check-setup` をいつでも実行して、環境が正常かどうか確認できます。

## テストの実行

```bash
uv run pytest tests/ -v --cov=claude_discord
```

PR を提出する前にすべてのテストが通過している必要があります。

## 稼働中のボットで動作確認する（dev worktree モード）

ユニットテストだけでは Discord 上の挙動をすべて検証できません。このリポジトリからボットを
起動している場合、ボットの読み込み先を worktree に向けることで、本体ツリーではなく自分の
ブランチを読ませられます。再インストールも editable install の切り替えも不要です:

```bash
git worktree add ../wt-my-feature -b feature/my-feature
cd ../wt-my-feature
make dev-on    # ~/.ccdb-dev-worktree を書き込んでボットを再起動
# ... Discord 上で変更を実際に操作して確認 ...
make dev-off   # マーカーを削除して本体ツリーに戻して再起動
make drift     # ボットは origin/main の最新コードを動かしているか？
```

`make dev-on` は worktree のパスを `~/.ccdb-dev-worktree` に書き込みます。
`scripts/pre-start.sh` が仕込む import フックがそのマーカーを読み、`claude_discord` /
`claude_code_core` の import を worktree へ横取りします。`dev-on` / `dev-off` は
`discord-bot` という systemd ユニットを再起動するので、デプロイ構成が異なる場合は
Makefile を調整してください。

**dev モードには有効期限がありません。** `make dev-on` を切り忘れると、サイドブランチが
そのまま本番に居座り続け、マージした PR は「デプロイされたように見えて実際は動いていない」
状態になります。`make drift`（`scripts/check-deploy-drift.sh`）はそれを検出します:
worktree とブランチ名を示し、そのコミットが `origin/main` の祖先かどうかを判定し、
結果として動いていないマージ済みコミット数を数え、dev モードが何日続いているかを報告します。
比較対象はリモート参照で、古くなっている可能性のあるローカル `main` は使いません。
`pre-start.sh` は起動のたびに同じレポートを出力します。

**本体ツリーモードもまた古くなります。** dev worktree の質問にだけ答えて他はすべて `OK` を
返すのでは、「そのために書かれたケースだけを報告し、残りは素通しするガード」になってしまいます。
チェックアウトを更新するのは `pre-start.sh` だけなので、再起動していないボットは起動前に
マージされたコードを提供し続けます。しかもこれは dev モードとまったく同じように無言です。
ツリーは `main` と表示され、`git pull` は成功し続けます。そこで `make drift` は
**実行中のプロセス**と `origin/main` も比較します。誠実な指標は「チェックアウトが遅れているか」
ではなく（再起動を伴わない pull は何も変えません）「ボットの起動後にどのコミットが着地したか」で、
これはサービスの `ActiveEnterTimestamp` から求めます。そのうえで報告するのは、動いていない
コミットのうち **最も古いもの** が何日待たされているかです。最新コミットの経過日数ではなく、
実際の待ち時間の長さがこれにあたります。再起動は意図的にまとめて行うものなので
（実行中のセッションは再起動を生き延びません）、初日から報告はしつつ、失敗扱いにするのは
`CCDB_STALE_DAYS`（デフォルト `3`）を超えてからです。systemd がないマシンや、ユニットを
読み取れない場合は、無言で通過させずにその旨を報告します。ユニット名は `CCDB_SERVICE` で
上書きできます（デフォルト `discord-bot`）。

終了コード: `0` 最新のコードが動作中、または dev モードだがマージ済みコードを動かしている、
`1` ドリフト（未マージのコードで dev モード、またはマージ済みコミットがしきい値を超えて
未デプロイ）、`2` dev モードが設定されているが使用不能でマーカーの参照先が存在しない
（フックは無言で本体ツリーにフォールバックします）。

本体ツリーへ戻す前に `.env` を確認してください。自分のブランチだけが解釈する値は、
本体ツリーではデフォルトにフォールバックし、エラーを出さないまま挙動が変わります。

## コードスタイル

- **フォーマッター**: `ruff format`
- **リンター**: `ruff check`
- **型ヒント**: すべての関数シグネチャに必須
- **Python**: 3.12+（モダンな構文のために `from __future__ import annotations` を使用）

```bash
uv run ruff check claude_discord/
uv run ruff format claude_discord/
```

## Discord スレッドの作成

`create_thread()` の呼び出しでは、必ず共有の自動アーカイブ期間を渡してください:

```python
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

thread = await channel.create_thread(
    name=name,
    auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
)
```

Discord の自動アーカイブ期間はスレッド作成時に確定し、既定値は短めです。アーカイブされたスレッドは
チャンネルのスレッド一覧から消えるため、利用者がまだ作業中だと思っている会話が削除されたように
見えてしまいます。`tests/test_thread_policy.py` は `claude_discord/` と、`examples/ebibot/cogs/` の
サンプル Cog の両方を走査し、キーワードを付け忘れた呼び出しや、定数を使わず数値をハードコードした
呼び出しがあると失敗します。これは Discord の挙動に関するルールであって、どのパッケージに書かれた
呼び出しかは関係ありません。カスタム Cog も同じ規則に従います。

## 個人情報を出荷するソースに残さない

このリポジトリは public であり、`examples/ebibot/` は実在するインスタンスの設定そのものです。つまり、
個人的な事情が紛れ込むのはまさにここです。「なぜこの Cog が存在するのか」を docstring に書くのはごく
自然なことであり、そして自然に書こうとすると、その運用をしている人物の名前を書いてしまいます。

`tests/test_no_personal_identifiers.py` は `claude_discord/`、`claude_code_core/`、`claude_teams/`、
`examples/` を走査し、出荷されるソースが実在の人物名を含んでいると失敗します。このチェックは意図的に
狭く作ってあります — 対象は「名前」であって「話題」ではありません。「日本語を書くな」のような広い
ルールや、誰かが使っていそうなツール名の一覧は誤検知を生み、誤検知は抑制され、抑制されたガードは
もはやガードではないからです。

ある機能がどうしても特定個人の運用ルールを必要とする場合は、それをコードに埋め込むのではなく、
リポジトリの外を指し示してください。`ThreadCompletionCog` がその参照実装です。Cog 側には汎用部分
（バッチ化、セッションと transcript の解決、マニフェストの生成）だけを残し、インスタンス固有の指示は
`THREAD_COMPLETION_PROMPT_FILE` が指すファイルから読み込みます。パスが読めない場合は、処理を落とすの
ではなく汎用プロンプトにフォールバックします。

## プロジェクト構造

- `claude_code_core/` — バックエンド非依存コアライブラリ: `SessionBackend` プロトコル、`ClaudeRunner`、`CodexRunner`、`create_backend()` ファクトリー、パーサー、型定義、SQLite モデル
- `claude_discord/claude/` — `claude_code_core` からの後方互換性のための再エクスポート
- `claude_discord/cogs/` — Discord.py の Cog（chat、skill コマンド、webhook トリガー、自動アップグレード）
- `claude_discord/database/` — SQLite セッションおよび通知の永続化
- `claude_discord/discord_ui/` — Discord UI コンポーネント（status、chunker、embeds）
- `claude_discord/ext/` — オプション拡張（REST API サーバー — aiohttp が必要）
- `tests/` — pytest テストスイート

## 変更の提出

1. リポジトリを Fork してフィーチャーブランチを作成
2. 新機能のテストを書く
3. プッシュ前にローカルで実行:
   ```bash
   uv run ruff check claude_discord/
   uv run ruff format --check claude_discord/
   uv run pytest tests/ -v
   ```
4. 何を・なぜという明確な説明を付けて PR を提出
5. CI が自動実行 — すべてのチェックが通過する必要があります

## バージョニング

このプロジェクトは自動バージョニングを採用しているため、**通常のコントリビューションではバージョンを手動で変更する必要はありません。**

- **自動パッチバンプ**: `main` にマージされた PR ごとにパッチバージョンが自動的にインクリメントされます（例: `1.3.0` → `1.3.1`）。リリースタグは作成されず、バージョン変更は直接 `main` にコミットされます。
- **手動マイナー/メジャーリリース**: `1.4.0` などのマイナー/メジャーリリースを切る場合は、`pyproject.toml` と `CHANGELOG.md` を手動で更新し、PR タイトルに `[release]` を含めます。これによりパッチバンプなしで現在のバージョンが GitHub Release としてタグ付け・公開されます。

## 新しい Cog の追加

1. `claude_discord/cogs/your_cog.py` を作成
2. Claude CLI 実行には `_run_helper.run_claude_with_config(RunConfig(...))` を使用
   （旧 `run_claude_in_thread()` shim も引き続き使えるが、新規コードは `run_claude_with_config` を優先）
3. `claude_discord/cogs/__init__.py` からエクスポート
4. `claude_discord/__init__.py` のパブリック API に追加
5. `tests/test_your_cog.py` にテストを書く

## AI 生成コードについて

このプロジェクトは Claude Code によって書かれました。コントリビューションに Claude Code や他の AI ツールを使うのは全く問題ありません — コードが動作し、テストされており、意味を成すことを確認してください。
