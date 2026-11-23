"""What a thread family produced: declared in the situation record, collected from messages."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from claude_discord.console import deliverables as module
from claude_discord.console.deliverables import (
    DeliverableCollector,
    bot_files,
    console_files,
    family_of,
    merge,
)
from claude_discord.database.models import init_db
from claude_discord.database.situation_repo import (
    MAX_ENTRIES,
    SituationError,
    ThreadSituationRepository,
    parse_situation,
)
from tests.test_console_conversations import AUTH
from tests.test_console_conversations import env as conversation_env

env = conversation_env
BOT = 99
PARENT, CHILD, GRANDCHILD, OTHER = 100, 200, 300, 400
TOKEN = "0123456789abcdef0123456789abcdef"


@pytest.fixture
async def repo(tmp_path) -> ThreadSituationRepository:
    path = str(tmp_path / "situations.db")
    await init_db(path)
    return ThreadSituationRepository(path)


def _attachment(name: str, host: str = "cdn.discordapp.com", kind: str = "video/mp4"):
    return SimpleNamespace(
        filename=name, url=f"https://{host}/attachments/1/2/{name}?ex=1", size=42, content_type=kind
    )


def _message(author: int, *attachments, at: str = "2026-11-23T01:00:00", webhook=None):
    return SimpleNamespace(
        author=SimpleNamespace(id=author),
        webhook_id=webhook,
        attachments=list(attachments),
        created_at=datetime.fromisoformat(at).replace(tzinfo=UTC),
    )


# -- declaring --------------------------------------------------------------------------


def test_a_deliverable_is_stored_clean() -> None:
    body = {
        "deliverables": [
            {"title": " PR #12 ", "url": "https://github.com/o/r/pull/12", "kind": "pr"},
            {"title": "Report", "url": "/#note/02_Contexts/x/report.md", "note": "v2"},
            {"title": "Prototype v1", "file": "short_v1.mp4", "superseded": True},
        ]
    }
    assert parse_situation(body)["deliverables"] == [
        {"title": "PR #12", "url": "https://github.com/o/r/pull/12", "kind": "pr"},
        {"title": "Report", "url": "/#note/02_Contexts/x/report.md", "note": "v2"},
        {"title": "Prototype v1", "file": "short_v1.mp4", "superseded": True},
    ]


@pytest.mark.parametrize(
    "entry",
    [
        "PR #12",
        {"url": "https://example.com"},
        {"title": "No address"},
        {"title": "x", "url": "javascript:alert(1)"},
        {"title": "x", "url": "data:text/html,hi"},
        {"title": "x", "url": "//evil.example/"},
        {"title": "x", "url": "https://a.example/ b"},
        {"title": "x", "url": "https://a.example", "kind": "movie"},
        {"title": "x", "url": "https://a.example", "superseded": "yes"},
        {"title": "x", "url": "https://a.example", "replaced": True},
        {"title": "x" * 201, "url": "https://a.example"},
        {"title": 3, "url": "https://a.example"},
    ],
)
def test_a_deliverable_that_would_mislead_or_endanger_the_panel_is_refused(entry) -> None:
    with pytest.raises(SituationError):
        parse_situation({"deliverables": [entry]})


def test_too_many_deliverables_or_not_a_list_is_refused() -> None:
    many = [{"title": f"{i}", "url": "https://a.example"} for i in range(MAX_ENTRIES + 1)]
    with pytest.raises(SituationError):
        parse_situation({"deliverables": many})
    with pytest.raises(SituationError):
        parse_situation({"deliverables": {"title": "x"}})


async def test_deliverables_are_kept_apart_from_the_other_fields(repo) -> None:
    await repo.update(PARENT, {"decided": ["Ship"]})
    stored = await repo.update(
        PARENT, {"deliverables": [{"title": "PR", "url": "https://github.com/o/r/pull/1"}]}
    )
    assert stored.decided == ["Ship"]
    [entry] = stored.as_dict()["deliverables"]
    assert entry["at"]
    assert {k: v for k, v in entry.items() if k != "at"} == {
        "title": "PR",
        "url": "https://github.com/o/r/pull/1",
    }
    assert (await repo.update(PARENT, {"doing": ["x"]})).deliverables == stored.deliverables
    assert [r.thread_id for r in await repo.get_many([PARENT, CHILD, PARENT])] == [PARENT]


async def test_an_older_database_gains_the_column(tmp_path) -> None:
    import aiosqlite

    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE thread_situation (thread_id INTEGER PRIMARY KEY, "
            "asks TEXT NOT NULL DEFAULT '[]', doing TEXT NOT NULL DEFAULT '[]', "
            "decided TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO thread_situation VALUES (1, '[]', '[\"old\"]', '[]', '2026-01-01')"
        )
        await db.commit()
    await init_db(path)
    record = await ThreadSituationRepository(path).get(1)
    assert record is not None
    assert record.doing == ["old"]
    assert record.deliverables == []


# -- collecting ---------------------------------------------------------------------------


def test_only_the_relays_own_attachments_on_discord_are_collected() -> None:
    messages = [
        _message(BOT, _attachment("short_v2.mp4"), at="2026-11-23T02:00:00"),
        _message(BOT, _attachment("evil.mp4", host="evil.example")),
        _message(7, _attachment("from_a_person.png", kind="image/png")),
        _message(BOT, _attachment("hook.png"), webhook=5),
        _message(BOT, _attachment("report.md", kind="text/markdown")),
    ]
    found = bot_files(messages, thread_id=CHILD, bot_id=BOT)
    assert [f["name"] for f in found] == ["short_v2.mp4", "report.md"]
    assert found[0] == {
        "source": "file",
        "thread_id": str(CHILD),
        "name": "short_v2.mp4",
        "url": "https://cdn.discordapp.com/attachments/1/2/short_v2.mp4?ex=1",
        "content_type": "video/mp4",
        "size": 42,
        "at": "2026-11-23T02:00:00+00:00",
    }
    assert bot_files(messages, thread_id=CHILD, bot_id=None) == []


def test_a_console_conversation_lists_only_its_own_files() -> None:
    own = f"/console/api/files/{CHILD}/{TOKEN}/plan.md"
    foreign = f"/console/api/files/{OTHER}/{TOKEN}/secret.md"
    messages = [
        SimpleNamespace(
            content=f"📎 Files:\n- [plan.md]({own})\n- [s]({foreign})", created_at="t2"
        ),
        SimpleNamespace(content=f"See [plan.md]({own})", created_at="t1"),
    ]
    assert console_files(CHILD, messages) == [
        {
            "source": "file",
            "thread_id": str(CHILD),
            "name": "plan.md",
            "url": own,
            "content_type": None,
            "size": None,
            "at": "t2",
        }
    ]


def test_a_family_is_its_root_and_every_descendant() -> None:
    parents = {CHILD: PARENT, GRANDCHILD: CHILD, OTHER: 999}
    assert family_of(GRANDCHILD, parents) == [PARENT, CHILD, GRANDCHILD]
    assert family_of(PARENT, parents) == [PARENT, CHILD, GRANDCHILD]
    assert family_of(555, parents) == [555]
    # A loop cannot hang the walk.
    assert sorted(family_of(1, {1: 2, 2: 1})) == [1, 2]


def test_declarations_title_and_supersede_the_files_they_name() -> None:
    files = [
        {"source": "file", "thread_id": "200", "name": "short_v1.mp4", "at": "2026-11-22"},
        {"source": "file", "thread_id": "200", "name": "short_v2.mp4", "at": "2026-11-23"},
    ]
    record = SimpleNamespace(
        thread_id=PARENT,
        updated_at="2026-11-23T05:00:00",
        deliverables=[
            {"title": "PR #5", "url": "https://github.com/o/r/pull/5", "kind": "pr"},
            {"title": "Prototype v1", "file": "short_v1.mp4", "superseded": True},
            {"title": "Typo", "file": "nope.mp4"},
        ],
    )
    merged = merge(files, [record])
    assert [e.get("title") or e["name"] for e in merged] == [
        "PR #5",
        "Typo",
        "short_v2.mp4",
        "Prototype v1",
    ]
    v1 = merged[-1]
    assert v1["superseded"] is True and v1["source"] == "file"
    assert merged[1]["missing"] is True
    assert merged[0]["thread_id"] == str(PARENT)
    assert files[0].get("title") is None, "the input is not changed"


class _Thread:
    def __init__(self, messages, last: int) -> None:
        self.messages = messages
        self.last_message_id = last
        self.reads = 0

    async def _iter(self):
        for m in self.messages:
            yield m

    def history(self, limit: int):
        self.reads += 1
        return self._iter()


async def test_the_collector_reads_each_thread_once_until_it_moves(repo) -> None:
    child = _Thread([_message(BOT, _attachment("short_v1.mp4"))], last=1)
    threads = {CHILD: child}
    await repo.update(PARENT, {"deliverables": [{"title": "Plan", "url": "/#note/plan.md"}]})

    async def lineage():
        return {CHILD: PARENT}

    async def chat_thread(thread_id):
        if thread_id == PARENT:
            raise RuntimeError("Discord is down")
        return threads.get(thread_id)

    collector = DeliverableCollector(
        lineage=lineage,
        conversations=lambda: None,
        chat_thread=chat_thread,
        bot_id=lambda: BOT,
        records=repo.get_many,
    )
    first = await collector.for_thread(CHILD)
    assert first["threads"] == [str(PARENT), str(CHILD)]
    assert first["unread"] == [str(PARENT)]
    assert {e.get("title") or e["name"] for e in first["deliverables"]} == {"Plan", "short_v1.mp4"}
    await collector.for_thread(PARENT)
    assert child.reads == 1, "cached while nothing new was posted"
    child.messages.insert(0, _message(BOT, _attachment("short_v2.mp4"), at="2026-11-24T00:00:00"))
    child.last_message_id = 2
    names = [e.get("name") for e in (await collector.for_thread(CHILD))["deliverables"]]
    assert child.reads == 2
    assert "short_v2.mp4" in names


async def test_a_cached_scan_expires(repo, monkeypatch) -> None:
    child = _Thread([], last=1)

    async def lineage():
        return {}

    async def chat_thread(thread_id):
        return child

    collector = DeliverableCollector(
        lineage=lineage,
        conversations=lambda: None,
        chat_thread=chat_thread,
        bot_id=lambda: BOT,
        records=repo.get_many,
    )
    await collector.for_thread(CHILD)
    monkeypatch.setattr(module, "CACHE_SECONDS", -1)
    collector._cache.clear()
    await collector.for_thread(CHILD)
    await collector.for_thread(CHILD)
    assert child.reads == 3


# -- the Console endpoint -----------------------------------------------------------------


async def test_the_console_lists_a_conversations_files_and_declarations(env, repo) -> None:
    created = await env.conversations.create(CHILD, "child")
    assert created is not None
    url = f"/console/api/files/{CHILD}/{TOKEN}/report.md"
    await env.conversations.append(
        CHILD, author="AI", is_bot=True, content=f"📎 Files:\n- [report.md]({url})"
    )
    await repo.update(
        CHILD, {"deliverables": [{"title": "Release", "url": "https://github.com/o/r/releases/1"}]}
    )
    env.console.api.situation_repo = repo
    path = f"/console/api/items/t{CHILD}/deliverables"
    assert (await env.client.get(path)).status == 401
    body = await (await env.client.get(path, headers=AUTH)).json()
    assert body["threads"] == [str(CHILD)]
    assert {e.get("title") or e["name"] for e in body["deliverables"]} == {"Release", "report.md"}
    capture = await env.client.get("/console/api/items/cabc/deliverables", headers=AUTH)
    assert (await capture.json())["deliverables"] == []


# -- when each entry was declared (#1548) ---------------------------------------------------


async def test_each_deliverable_keeps_the_time_it_was_first_declared(repo) -> None:
    first = {"title": "PR #1", "url": "https://github.com/o/r/pull/1"}
    stored = await repo.update(PARENT, {"deliverables": [first]})
    born = stored.deliverables[0]["at"]
    assert born
    # The agent reads the record back and resends it, with "at", plus a new entry.
    again = await repo.update(
        PARENT,
        {
            "deliverables": parse_situation(
                {"deliverables": [{"title": "Video", "file": "v.mp4"}, *stored.deliverables]}
            )["deliverables"]
        },
    )
    by_title = {e["title"]: e["at"] for e in again.deliverables}
    assert by_title["PR #1"] == born
    assert by_title["Video"] >= born


def test_a_sent_time_is_ignored() -> None:
    entry = {"title": "x", "url": "https://a.example", "at": "1999-01-01"}
    assert "at" not in parse_situation({"deliverables": [entry]})["deliverables"][0]


def test_declared_entries_sort_by_their_own_time() -> None:
    record = SimpleNamespace(
        thread_id=PARENT,
        updated_at="2026-11-23T09:00:00+00:00",
        deliverables=[
            {"title": "old PR", "url": "https://a.example/1", "at": "2026-11-20T00:00:00+00:00"},
            {"title": "legacy", "url": "https://a.example/2"},
        ],
    )
    files = [
        {"source": "file", "thread_id": "200", "name": "v.mp4", "at": "2026-11-22T00:00:00+00:00"}
    ]
    merged = merge(files, [record])
    assert [e.get("title") or e["name"] for e in merged] == ["legacy", "v.mp4", "old PR"]
