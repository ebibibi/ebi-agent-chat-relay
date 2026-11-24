"""One card per piece of content on a page (#1564).

Items that share a ``group`` (the video or article they promote) are one card, with a row per
item in the order they were registered. An item without a group is a card of its own, so a page
that never uses groups lists exactly what it listed before.

Which tab a card is on follows its rows: **open** while any row is open (finished rows stay
on it, so the person sees what automation already posted), **done** once nothing is open and
something was done, **skipped** when every row was skipped.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite

from .pages_repo import (
    DEFAULT_ITEM_LIMIT,
    ITEM_COLUMNS,
    MAX_ITEM_LIMIT,
    STATE_DONE,
    STATE_OPEN,
    STATE_SKIPPED,
    Page,
    PageItem,
    PageNotFoundError,
    PageRepository,
    check_state,
    make_item,
)


def _tab(states: set[str]) -> str:
    if STATE_OPEN in states:
        return STATE_OPEN
    return STATE_DONE if STATE_DONE in states else STATE_SKIPPED


def _card(group: str, items: list[PageItem]) -> dict[str, Any]:
    title = next((i.group_title for i in items if i.group_title), "") or next(
        (i.title for i in items if i.title), ""
    )
    return {
        "group": group,
        "title": title,
        "state": _tab({i.state for i in items}),
        "created_at": min(i.created_at for i in items),
        "resolved_at": max(i.resolved_at for i in items),
        "items": [i.as_dict() for i in items],
    }


async def list_cards(
    repo: PageRepository,
    slug: str,
    state: str = STATE_OPEN,
    limit: int = DEFAULT_ITEM_LIMIT,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """The cards on one tab: open ones oldest first, resolved ones latest first.

    *limit* counts cards. Each card carries every row of its group, whatever their state.
    """
    check_state(state)
    page = await repo.get_page(slug)
    if page is None:
        raise PageNotFoundError(f"unknown page: {slug}")
    count = max(1, min(limit, MAX_ITEM_LIMIT))
    groups = await _groups_on_tab(repo, page, state, count)
    order = "created_at, key" if state == STATE_OPEN else "resolved_at DESC, key"
    async with aiosqlite.connect(repo.db_path) as db:
        rows = list(
            await db.execute_fetchall(
                f"SELECT rowid, {ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                " WHERE page_slug = ? AND group_key IN (SELECT value FROM json_each(?))"
                " ORDER BY rowid",
                (slug, json.dumps(groups)),
            )
        )
        rows += await db.execute_fetchall(
            f"SELECT rowid, {ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
            f" WHERE page_slug = ? AND group_key = '' AND state = ? ORDER BY {order} LIMIT ?",
            (slug, state, count),
        )
    grouped: dict[str, list[PageItem]] = {}
    singles: list[tuple[str, list[PageItem]]] = []
    first: dict[str, int] = {}  # the card's first row, to break ties in registration order
    for rowid, *columns in rows:
        item = make_item(tuple(columns), page, now)
        if item.group:
            grouped.setdefault(item.group, []).append(item)
            first.setdefault(item.group, rowid)
        else:
            singles.append((f"\0{rowid}", [item]))
            first[f"\0{rowid}"] = rowid
    keyed = [
        (name, _card("" if name.startswith("\0") else name, items))
        for name, items in [*grouped.items(), *singles]
    ]
    if state == STATE_OPEN:
        keyed.sort(key=lambda pair: (pair[1]["created_at"], first[pair[0]]))
    else:
        keyed.sort(key=lambda pair: (pair[1]["resolved_at"], first[pair[0]]), reverse=True)
    return [card for _, card in keyed[:count]]


async def _groups_on_tab(repo: PageRepository, page: Page, state: str, count: int) -> list[str]:
    """The first *count* groups on a tab, in the order the tab lists them."""
    async with aiosqlite.connect(repo.db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT group_key, GROUP_CONCAT(DISTINCT state), MIN(created_at), MAX(resolved_at)"
            " FROM console_page_items WHERE page_slug = ? AND group_key != ''"
            " GROUP BY group_key",
            (page.slug,),
        )
    on_tab = [row for row in rows if _tab(set(row[1].split(","))) == state]
    if state == STATE_OPEN:
        on_tab.sort(key=lambda row: (row[2], row[0]))
    else:
        on_tab.sort(key=lambda row: (row[3], row[0]), reverse=True)
    return [row[0] for row in on_tab[:count]]
