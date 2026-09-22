> **Note:** This is an auto-translated version of the original English documentation.
> If there are any discrepancies, the [English version](../merge-events.md) takes precedence.
> **注意:** これは英語のオリジナルドキュメントを自動翻訳したものです。
> 内容に相違がある場合は、[英語版](../merge-events.md)が優先されます。

# ワークフローイベントを発火させるマージ

GitHub のある挙動が、このリポジトリの CI 自動化の大部分を決定しています。

> `GITHUB_TOKEN` によるものと記録されたマージは、`push` または `pull_request`
> イベントを発火させません。実ユーザーによるものと記録されたマージは発火させます。

GitHub はワークフローが自身をトリガーするのを防ぐため、`GITHUB_TOKEN` によって
発生したイベントを抑止します。`repository_dispatch` は明示的に例外とされているため、
`auto-approve.yml` から `auto-version-bump.yml` へ送信できる唯一のシグナルです。

## 実測結果

2026-09-22 に、1 日で `main` へマージされた 16 件を調べました。

| マージ実行者 | リンクされた Issue のクローズ | `main` の `push` イベント |
|--------------|--------------------------------|----------------------------|
| `ebibibi`（7 件） | あり（マージから 1〜2 秒後） | あり（マージから 2〜3 秒後） |
| `github-actions`（9 件） | 一度もなし | 一度もなし |

リポジトリ所有者によるものと記録された 7 件のマージから、`main` の `push` イベントによる
CI がちょうど 7 回実行されました。`github-actions` によるものと記録された 9 件からは
一度も実行されませんでした。この対応関係は 1 対 1 で、サンプル内に例外はありませんでした。

この結果から導かれる次の 2 点は誤解しやすく、実際にどちらも誤解されていました。

- **`issues: write` を付与しても、リンクされた Issue のクローズは復活しません。**
  auto-merge は後から GitHub によって完了され、auto-merge を有効化した job の権限は
  引き継ぎません。#743 で実測したところ、権限は付与されていましたが、マージは
  `github-actions` によって完了され、リンクされた 2 件の Issue は 8 秒後も open のままでした。
  その後、`auto-approve.yml` がそれらを直接クローズしました。
- **`GITHUB_TOKEN` で作成した pull request でも、同じ問題が一段早く発生します。**
  `pull_request` イベントが発火しないため required checks が実行されず、マージできません。
  PR ベースの version bump はこの理由で 2 回取り消され、その後 #725 で実ユーザーとして
  PR を作成する方式になりました。

## この挙動に依存する箇所

| 場所 | この挙動への対応 |
|------|------------------|
| `auto-approve.yml` | 反応できるイベントがないためマージ完了をポーリングします。upgrade と docs-sync の webhook を送信し、`pr-merged` を dispatch し、PR にリンクされた Issue を直接クローズします |
| `auto-version-bump.yml` | bump PR 自体の checks が実行されるよう、`ADMIN_PAT` を使って branch、Issue、pull request を作成します |
| `require-linked-issue.yml` | bot は自身の PR がクローズすべき Issue を作成できないため、bot author を対象外にします |

ポーリングはこの仕組みの弱点です。待機時間の上限を決めるには runner の混雑度が必要ですが、
job からはそれを把握できません。#752 を参照してください。

## 変更前に理解しておくべき影響

自動マージを実ユーザーによるものとして記録させれば、リンクされた Issue のクローズが復活し、
ポーリングも不要になります。その一方で、`ci.yml` は pull request に加えて `main` への `push`
でもトリガーされるため、マージのたびに実行されるようになります。容量 1 の self-hosted pool では
負荷がほぼ倍増し、そもそもポーリングがタイムアウトする原因だった混雑をさらに悪化させます。
