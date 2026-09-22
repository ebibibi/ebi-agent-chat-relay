> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../merge-events.md) takes precedence.
> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。
> 内容に相違がある場合は、[英語版](../merge-events.md)が優先されます。

# ワークフローイベントを発火させるマージ

GitHub のある挙動が、このリポジトリの CI 自動化の大部分を決定しています。

> `GITHUB_TOKEN` によるものと記録されたマージは、`push` または `pull_request`
> イベントを発火させません。実ユーザーによるものと記録されたマージは発火させます。

GitHub はワークフローが自身をトリガーするのを防ぐため、`GITHUB_TOKEN` によって
発生したイベントを抑止します。fine-grained または classic PAT は所有者として動作するため、
通常のイベントが発火します。

## 実測結果

2026-09-22 に、1 日で `main` へマージされた 16 件を調べました。

| マージ実行者 | リンクされた Issue のクローズ | `main` の `push` イベント |
|--------------|--------------------------------|----------------------------|
| `ebibibi`（7 件） | あり（マージから 1〜2 秒後） | あり（マージから 2〜3 秒後） |
| `github-actions`（9 件） | 一度もなし | 一度もなし |

リポジトリ所有者によるものと記録された 7 件のマージから、`main` の `push` イベントによる
CI がちょうど 7 回実行されました。`github-actions` によるものと記録された 9 件からは
一度も実行されませんでした。この対応関係は 1 対 1 で、サンプル内に例外はありませんでした。

PR #758 では、手動マージではなく auto-merge の経路そのものを検証しました。自動化が最初に
`GITHUB_TOKEN` で auto-merge を有効化し、その後、所有者が所有者トークンで同じ
`gh pr merge --auto --squash` を実行しました。required checks の完了後、GitHub はマージを
`ebibibi` 名義として記録し、1 秒後に #757 を閉じ、3 秒後に `main` の push workflow を
発火させました。最後に auto-merge を有効化したトークンが、最終的なマージ主体になります。

次の 2 点は誤解しやすく、実際にどちらも誤解されていました。

- **bot 名義のマージに `issues: write` を付与しても、リンクされた Issue のクローズは
  復活しません。** auto-merge は後から GitHub によって完了され、有効化した job の権限を
  引き継ぎません。#743 では権限があっても Issue は open のままで、workflow が明示的に
  閉じるまで変化しませんでした。
- **`GITHUB_TOKEN` で作成した pull request でも、同じ問題が一段早く発生します。**
  `pull_request` イベントが発火しないため required checks が実行されず、マージできません。
  PR ベースの version bump はこの理由で 2 回取り消され、その後 #725 で実ユーザーとして
  PR を作成する方式になりました。

## 現在の設計

| 場所 | このルールの使い方 |
|------|--------------------|
| `auto-approve.yml` | `GITHUB_TOKEN` で承認し、`ADMIN_PAT` で所有者 PR の auto-merge を有効化します。完了待ちはしません |
| `post-merge.yml` | 発生した `main` push に反応し、commit からマージ済み PR を特定して upgrade/docs-sync webhook と `pr-merged` dispatch を送ります |
| `auto-version-bump.yml` | bump PR 自体の checks が実行されるよう、`ADMIN_PAT` で branch、Issue、pull request を作成します |
| `ci.yml`、`codeql.yml` | PR と定期実行で動き、マージ後の `main` push では重ねて実行しません |
| GitHub | マージがユーザー名義なので、`Closes #N` で指定された Issue を閉じます |

Dependabot は意図的に bot 名義のままです。その lock file 更新では bot を再起動せず、追加の
version bump も作りません。変更は次回の自然な再起動時に反映されます。

## ポーリングをイベント駆動へ置き換えた理由

ポーリング job は required checks がキューでどれだけ待つかを推測しなければなりません。
以前の待ち時間は 15 分から 40 分へ延長されましたが、PR #749 は長い方の期限が切れた約 1 分後に
マージされました。待機が終わると、その後ろにある処理はすべて失われます。`push` イベントなら
時間を推測する必要はなく、マージ後にのみ存在し、直ちに consumer を起動します。

ユーザー名義のマージでは通常、`main` で CI と CodeQL も再実行され、容量 1 の self-hosted pool の
負荷が倍になります。これらの高コストな検査は PR と定期実行だけにし、軽量な post-merge の
制御処理は `ubuntu-latest` で実行します。
