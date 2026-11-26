"""The text the Console's 📄 button puts into a reply box: "summarize this thread as HTML".

Like the 🌙 night template (:mod:`.night`), the button only fills the box; the person edits and
sends. A deployment replaces the built-in text with its own file
(``CCDB_HTML_BRIEF_TEMPLATE_FILE``), for example to name its own style rules or checker.
``{{project}}`` in the text becomes the thread's Ebi Workspace project in the browser.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_ENV = "CCDB_HTML_BRIEF_TEMPLATE_FILE"
MAX_TEMPLATE_CHARS = 4000

TEMPLATES = {
    "ja": (
        "このスレッドの直近のやり取りを、あとで読み返せる1枚のHTMLにまとめてください。\n\n"
        "範囲：［直近の話題すべて／特定の話題だけ］\n"
        "読み手：［自分の振り返り用／チームに共有する］\n"
        "保存先：［プロジェクト「{{project}}」の決まった場所／このスレッドの作業フォルダ］\n\n"
        "- 会話に出ていない事実は足さない。"
        "決まったこと・決まっていないこと・次にやることを分けて書く\n"
        "- JavaScript と外部のCSS・画像・フォントは使わない"
        "（Console のプレビューでは動かない・読まれない）\n"
        "- 表はスマホの幅でも読めるようにする\n"
        "- できたHTMLを添付ファイルとして送る\n"
    ),
    "en": (
        "Summarize the recent conversation in this thread as one HTML page to read later.\n\n"
        "Scope: [everything recent / one topic]\n"
        "Reader: [my own review / the team]\n"
        "Save to: [the usual place in project {{project}} / this thread's working folder]\n\n"
        "- Add no facts the conversation does not contain; separate what was decided, what is "
        "open and what comes next\n"
        "- No JavaScript and no outside CSS, images or fonts (the Console preview neither runs "
        "nor loads them)\n"
        "- Keep tables readable at phone width\n"
        "- Attach the finished HTML file\n"
    ),
}


def html_brief_template(lang: str = "ja", env: Mapping[str, str] | None = None) -> str:
    """The 📄 text: the deployment's file when it is readable, else built in."""
    env = os.environ if env is None else env
    path = (env.get(TEMPLATE_ENV) or "").strip()
    if path:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            logger.warning(
                "html brief: %s=%s is not readable; using the built-in template", TEMPLATE_ENV, path
            )
        else:
            if text:
                return text[:MAX_TEMPLATE_CHARS] + "\n"
    return TEMPLATES.get(lang, TEMPLATES["en"])
