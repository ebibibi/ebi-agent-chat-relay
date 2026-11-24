# Focus navigation check

In the bundled IT-admin scenario, pin two requests and open Focus. The next-move guide
must remain at the top, above the pinned group. Reorder the pins and reload: the guide
stays first and the two pins keep their saved order. Repeat with a narrow phone viewport.
Other lists keep their usual pinned-first layout.

## Every unfinished item in Focus

Scroll Focus to the bottom. The ranking above is unchanged; below it, 📂 その他の未完了,
🤖 AIが作業中, 🏁 完了候補 and 🔍 絞り込みで非表示 each show a count and fold on a tap
(candidates and filtered items start folded). Narrow the time chip to ⚡: the items it drops
are counted under 絞り込みで非表示 and appear on one tap. No unfinished thread is missing and
none is listed twice. Repeat at phone width.

## Decision card cleanup

Open a thread's situation panel or the secretary home and dismiss one obsolete request with
消す. Other cards remain and no reply is sent. With automatic triage enabled, settle a separate
card in ordinary thread conversation: a later explicit human answer should close that card
once the thread is idle. An unrelated message must leave unanswered cards in place.

## Human-action eligibility

Keep a continuing conversation open after acknowledging an answer. It must not become the next
move just because it was updated or its project still has tasks. A distinct unanswered review
should remain eligible after triage. Newly untriaged or stale conversations wait for fresh
assessment; captured tasks continue to work before assessment.

## Put one decision off until later

In the secretary home, press 後回し on one decision. It leaves 今決めること but remains
unanswered in the folded 後回し section. Another card in the same thread must remain active.
Reload, open 後回し and press 戻す: the original card returns without sending a reply.
Repeat at phone width. If every request in a thread is deferred, its next-move recommendation
must disappear until an active request exists again.

## Situation comment input

Open a family's situation panel and choose 別の答え on a decision card. The comment
editor and the panel's reply composer each provide at least six lines. Type Japanese,
leave a selection in the middle, and let other threads update: focus, composition and
the selection must stay in the connected editor while surrounding progress changes.
Repeat on a phone's situation tab and on the secretary home. Removing or resolving the
request must still remove its editor; an explicit click elsewhere must keep its focus.

## Deliverables in the situation panel

Start one of the passkey family's children (全社のパスキー移行を統括して) from the Console and ask it to
write a short Markdown report and attach it. Open the parent: the panel's 📦 成果物 lists the report
labelled with the child, and 👁 プレビュー opens it. Have the parent declare a link with
`deliverables` (see docs/console.md, "📦 Deliverables") and mark an older file `superseded`: the link
appears with ↗ 開く and the old file folds under 置き換え済み. Repeat on a phone's 現在地 tab. A thread on
its own that produced a file shows the panel with only 📦 成果物.
