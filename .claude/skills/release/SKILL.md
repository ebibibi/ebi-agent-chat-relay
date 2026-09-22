---
name: release
description: ccdb のマイナー/メジャーリリース手順。「v1.4.0としてリリースして」等と言われたときに使う。
allowed-tools: Bash, Read, Edit
---

# ccdb リリース手順

## バージョン体系

```
v1.3.0  ← 手動マイナーリリース（このスキルの対象）
v1.3.1  ← PR マージごとに自動インクリメント（普段は何もしない）
v1.3.2  ← 同上
...
v1.4.0  ← 手動マイナーリリース（このスキルの対象）
```

- **自動パッチバンプ**: 普通の PR をマージするたびに `.1` ずつ自動インクリメント。操作不要
- **手動リリース**: マイナー/メジャーの区切りを付けたいときに使う（このスキル）

---

## 手動リリース手順（「v1.4.0としてリリースして」）

### Step 1: リポジトリに入る

```bash
cd /home/ebi/claude-code-discord-bridge
git checkout main && git pull
```

### Step 2: ブランチを作る

```bash
git checkout -b release/v1.4.0   # バージョンは指定されたものに変える
```

### Step 3: pyproject.toml のバージョンを更新

`pyproject.toml` の `version = "1.3.x"` を指定バージョンに書き換える:

```toml
version = "1.4.0"
```

### Step 4: CHANGELOG.md を更新

1. `## [Unreleased]` セクションを `## [1.4.0] - YYYY-MM-DD` に変更（今日の日付）
2. その上に新しい空の `## [Unreleased]` セクションを追加

```markdown
## [Unreleased]

## [1.4.0] - 2026-02-22   ← 今日の日付

### Added
...（既存の Unreleased 内容がここに来る）
```

### Step 5: PR を作成（タイトルに **必ず** `[release]` を含める）

```bash
PATH="/home/ebi/.local/bin:$PATH" git add pyproject.toml CHANGELOG.md
PATH="/home/ebi/.local/bin:$PATH" git commit -m "release: v1.4.0 [release]"
git push -u origin release/v1.4.0

gh pr create \
  --repo ebibibi/ebi-agent-chat-relay \
  --base main \
  --title "release: v1.4.0 [release]" \
  --body "Release v1.4.0

## Changes
See CHANGELOG.md for details."
```

**⚠️ 重要**: PR タイトルに `[release]` を含めること。これがないと自動パッチバンプが走って v1.4.1 になってしまう。

### Step 6: 確認（数分後）

auto-approve により PR が自動マージされ、タグ `v1.4.0` と GitHub Release が作成される:

```bash
gh release view v1.4.0 --repo ebibibi/ebi-agent-chat-relay
```

---

## 仕組み（裏側）

```
PR マージ（auto-approve.yml）
  └── repository_dispatch: pr-merged
        └── auto-version-bump.yml
              ├── [release] あり → 現在バージョンでタグ & Release（バンプなし）
              └── [release] なし → patch++ の bump PR を自動で開く（タグ・Release なし）
                    └── auto-approve.yml が承認 → CI 通過後に自動マージ
```

- docs-sync PR（翻訳・ドキュメント更新）はバンプも Release も発生しない
- patch-bump は `auto/bump-vX.Y.Z` ブランチからの PR。`issue-driven-main`
  ルールセットは bypass_actors が空で、管理者の PAT でも main へ直接 push
  できないため（Issue #725）
- bump PR は `require-linked-issue` を通すために専用 Issue を自動作成して
  `Closes #N` で紐付ける。PR も Issue も ADMIN_PAT（= ebibibi）が作る。
  GITHUB_TOKEN で作ると pull_request イベントが発火せず、必須チェックが
  「Expected」のまま永久に埋まらない（過去2回この理由で revert されている）
- bump PR が既に開いているあいだの追加マージは、その1件に集約される
- bump PR のマージでは dispatch を送らない（無限ループ防止）
- リリース時にはえびログ（Discord）に自動通知

---

## よくある失敗

| 状況 | 原因 | 対処 |
|------|------|------|
| v1.4.1 になってしまった | PR タイトルに `[release]` がなかった | タグを削除して再度 release PR を作る |
| タグが既に存在するエラー | 同じバージョンでタグを作ろうとした | タグを削除: `gh api repos/ebibibi/ebi-agent-chat-relay/git/refs/tags/v1.4.0 --method DELETE` |
