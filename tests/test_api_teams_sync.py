"""Tests for the Teams thread sync API (/api/teams/sync/{plan,push}).

The behaviour that matters here is not "does it write a file" but the three
properties the design rests on:

- **idempotence** — pushing the same thread twice must not duplicate anything,
  because the client keeps no record of what it sent.
- **edits are the same mechanism as new messages** — a changed hash is wanted,
  and the old version survives in ``_history``.
- **nothing goes missing quietly** — an attachment that could not be stored is
  reported, keeps being reported, and is asked for again on the next plan.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext import teams_sync
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.teams_store import TeamsVaultStore

TOKEN = "test-ingest-token"  # noqa: S105 - test fixture, not a real credential
TEAM = "3a2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
ROOT = "1784110000000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def vault() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def db_path() -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
async def client(db_path: str, vault: Path) -> Iterator[TestClient]:
    repo = NotificationRepository(db_path)
    await repo.init_db()
    api = ApiServer(
        repo=repo,
        bot=MagicMock(),
        default_channel_id=1,
        ingest_token=TOKEN,
        teams_vault_root=str(vault),
    )
    async with TestClient(TestServer(api.app)) as c:
        yield c


def thread_body(**over) -> dict:
    body = {
        "thread": {
            "team": TEAM,
            "root_mid": ROOT,
            "title": "MEHJ後のIntune登録に関して",
            "url": "https://teams.cloud.microsoft/x",
        },
        "messages": [],
    }
    body.update(over)
    return body


def msg(mid: str, text: str, digest: str, **over) -> dict:
    out = {
        "mid": mid,
        "hash": digest,
        "text": text,
        "author": "高橋 太郎",
        "timestamp": "2026-07-28T10:12:00+09:00",
    }
    out.update(over)
    return out


# ---------------------------------------------------------------------------
# have / want
# ---------------------------------------------------------------------------


async def test_plan_wants_everything_on_a_first_sync(client: TestClient) -> None:
    resp = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(
            messages=[{"mid": ROOT, "hash": "h1"}, {"mid": "1784885000000", "hash": "h2"}]
        ),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["exists"] is False
    assert body["have"] == 0
    assert body["want_messages"] == [ROOT, "1784885000000"]
    assert body["newest_have_mid"] is None


async def test_plan_after_push_wants_nothing_new(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "最初の投稿", "h1")]),
    )
    resp = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(
            messages=[{"mid": ROOT, "hash": "h1"}, {"mid": "1784885000000", "hash": "h2"}]
        ),
    )
    body = await resp.json()
    assert body["have"] == 1
    assert body["want_messages"] == ["1784885000000"]
    assert body["newest_have_mid"] == ROOT


async def test_changed_hash_is_wanted_again(client: TestClient) -> None:
    """An edit upstream is detected by the same comparison as a new message."""
    await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "before", "h1")])
    )
    resp = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h2"}]),
    )
    assert (await resp.json())["want_messages"] == [ROOT]


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


async def test_push_writes_one_file_per_message(client: TestClient, vault: Path) -> None:
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[msg(ROOT, "ルート投稿", "h1"), msg("1784885000000", "返信です", "h2")]
        ),
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["created"] == 2

    folder = Path(body["folder"])
    assert (folder / "messages" / f"{ROOT}.md").exists()
    assert "返信です" in (folder / "messages" / "1784885000000.md").read_text()
    # mid is fixed width, so the filenames sort into chronological order.
    names = sorted(p.stem for p in (folder / "messages").glob("*.md"))
    assert names == [ROOT, "1784885000000"]


async def test_chain_records_order_and_prev(client: TestClient) -> None:
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[msg("1784885000000", "二番目", "h2"), msg(ROOT, "ルート", "h1")]
        ),
    )
    folder = Path((await resp.json())["folder"])
    chain = [json.loads(line) for line in (folder / "chain.jsonl").read_text().splitlines()]
    assert [c["mid"] for c in chain] == [ROOT, "1784885000000"]
    assert chain[0]["prev"] is None
    assert chain[1]["prev"] == ROOT
    # `next` is deliberately not stored anywhere: it would require rewriting an
    # existing file on every new reply.
    assert "next" not in chain[0]
    front = (folder / "messages" / f"{ROOT}.md").read_text()
    assert "next:" not in front


async def test_pushing_twice_is_idempotent(client: TestClient) -> None:
    body = thread_body(messages=[msg(ROOT, "同じ本文", "h1")])
    first = await client.post("/api/teams/sync/push", headers=AUTH, json=body)
    folder = Path((await first.json())["folder"])
    second = await client.post("/api/teams/sync/push", headers=AUTH, json=body)

    assert (await second.json())["folder"] == str(folder)
    assert len(list(Path(folder / "messages").glob("*.md"))) == 1
    assert sorted(p.name for p in folder.parent.iterdir()) == [folder.name]
    meta = json.loads((folder / "thread.json").read_text())
    assert meta["message_count"] == 1


async def test_edit_archives_the_previous_version(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "旧本文", "h1")])
    )
    resp = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "新本文", "h2")])
    )
    folder = Path((await resp.json())["folder"])
    note = (folder / "messages" / f"{ROOT}.md").read_text()
    assert "新本文" in note
    assert "edited: true" in note
    archived = list((folder / "_history").glob(f"{ROOT}.*.md"))
    assert len(archived) == 1
    assert "旧本文" in archived[0].read_text()
    chain = [json.loads(line) for line in (folder / "chain.jsonl").read_text().splitlines()]
    assert [c["rev"] for c in chain] == [1, 2]


async def test_deleted_message_keeps_its_file(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[msg(ROOT, "このメッセージは削除されました", "h2", deleted=True)]
        ),
    )
    folder = Path((await resp.json())["folder"])
    note = (folder / "messages" / f"{ROOT}.md").read_text()
    assert "deleted: true" in note
    assert list((folder / "_history").glob("*.md"))


# ---------------------------------------------------------------------------
# attachments — the part that has failed silently before
# ---------------------------------------------------------------------------


async def test_embedded_attachment_lands_next_to_its_message(
    client: TestClient,
) -> None:
    payload = base64.b64encode(b"evtx-bytes").decode()
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "取り急ぎ admin.evtx を確認しました",
                    "h1",
                    attachments=[{"name": "admin.evtx", "status": "embedded", "data": payload}],
                )
            ]
        ),
    )
    body = await resp.json()
    folder = Path(body["folder"])
    assert body["attachments_saved"] == 1
    assert body["pending"] == []
    assert (folder / "messages" / ROOT / "admin.evtx").read_bytes() == b"evtx-bytes"


async def test_unfetched_attachment_is_reported_and_asked_for_again(
    client: TestClient,
) -> None:
    """The failure mode that produced a false "✅ 完全" must not recur."""
    push = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "ログを添付します",
                    "h1",
                    attachments=[
                        {"name": "admin.evtx", "status": "failed", "reason": "URLを取得できず"}
                    ],
                )
            ]
        ),
    )
    body = await push.json()
    assert body["attachments_saved"] == 0
    assert body["pending"] == [
        {"mid": ROOT, "name": "admin.evtx", "reason": "URLを取得できず", "url": ""}
    ]

    folder = Path(body["folder"])
    assert "admin.evtx" in (folder / "README.md").read_text()
    assert "⚠️ 添付未取得" in (folder / "messages" / f"{ROOT}.md").read_text()

    # And the next plan asks for it again — a gap that stays visible is a gap
    # that can be closed by pressing the button once more.
    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h1", "attachments": ["admin.evtx"]}]),
    )
    plan_body = await plan.json()
    assert plan_body["want_messages"] == []
    assert plan_body["want_attachments"] == [{"mid": ROOT, "name": "admin.evtx"}]


async def test_pending_clears_once_the_bytes_arrive(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[{"name": "a.log", "status": "failed", "reason": "取得失敗"}],
                )
            ]
        ),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[
                        {
                            "name": "a.log",
                            "status": "embedded",
                            "data": base64.b64encode(b"ok").decode(),
                        }
                    ],
                )
            ]
        ),
    )
    assert (await resp.json())["pending"] == []


async def test_source_unavailable_is_terminal_and_reported_separately(
    client: TestClient,
) -> None:
    """A confirmed source-side 404 is visible, but not retryable pending work."""
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[
                        {
                            "name": "gone.txt",
                            "status": "unavailable",
                            "url": "https://contoso.sharepoint.com/sites/team/gone.txt",
                            "reason": "Authenticated source returned HTTP 404",
                        }
                    ],
                )
            ],
            coverage={
                "full_scan": True,
                "attachment_scan": {"detected": 10, "ignored": 1},
            },
        ),
    )
    body = await resp.json()

    assert body["pending"] == []
    assert body["unavailable"] == [
        {
            "mid": ROOT,
            "name": "gone.txt",
            "reason": "Authenticated source returned HTTP 404",
            "url": "https://contoso.sharepoint.com/sites/team/gone.txt",
        }
    ]
    assert body["attachment_summary"] == {
        "detected": 10,
        "saved": 0,
        "unavailable": 1,
        "ignored": 1,
        "pending": 0,
    }

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h1", "attachments": ["gone.txt"]}]),
    )
    plan_body = await plan.json()
    assert plan_body["want_messages"] == []
    assert plan_body["want_attachments"] == []
    assert plan_body["attachment_summary"] == body["attachment_summary"]
    assert plan_body["message_count"] == 1


async def test_obsolete_pending_clears_when_the_message_inventory_changes(
    client: TestClient,
) -> None:
    """A corrected client inventory is authoritative for the pushed message."""
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[{"name": "1.txt", "status": "failed", "reason": "誤検出"}],
                )
            ]
        ),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h2",
                    attachments=[
                        {
                            "name": "テスト1.txt",
                            "status": "embedded",
                            "data": base64.b64encode(b"ok").decode(),
                        }
                    ],
                )
            ]
        ),
    )
    body = await resp.json()
    assert body["pending"] == []
    meta = json.loads((Path(body["folder"]) / "thread.json").read_text())
    assert meta["pending_attachments"] == []


async def test_partial_push_preserves_pending_for_an_untouched_message(
    client: TestClient,
) -> None:
    """A partial batch must not erase gaps for messages outside that batch."""
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[{"name": "a.log", "status": "failed", "reason": "取得失敗"}],
                )
            ]
        ),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg("1784885000000", "reply", "h2")]),
    )
    assert (await resp.json())["pending"] == [
        {"mid": ROOT, "name": "a.log", "reason": "取得失敗", "url": ""}
    ]


async def test_pending_merge_prefers_a_real_link_over_a_provisional_failure(
    client: TestClient,
) -> None:
    """A late provisional DOM fallback must not hide the actionable SharePoint URL."""
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[
                        {
                            "name": "admin.evtx",
                            "status": "linked",
                            "url": "https://contoso.sharepoint.com/sites/team/admin.evtx",
                            "reason": "HTTP 404",
                        },
                        {
                            "name": "admin.evtx",
                            "status": "failed",
                            "reason": "本文で言及されていますが添付カードを検出できません",
                        },
                    ],
                )
            ]
        ),
    )

    assert (await resp.json())["pending"] == [
        {
            "mid": ROOT,
            "name": "admin.evtx",
            "reason": "HTTP 404",
            "url": "https://contoso.sharepoint.com/sites/team/admin.evtx",
        }
    ]


async def test_plan_requests_a_message_with_obsolete_pending_metadata(
    client: TestClient,
) -> None:
    """Vaults affected before the fix self-heal on the next ordinary retry."""
    payload = base64.b64encode(b"ok").decode()
    push = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h2",
                    attachments=[{"name": "テスト1.txt", "status": "embedded", "data": payload}],
                )
            ]
        ),
    )
    folder = Path((await push.json())["folder"])
    meta_path = folder / "thread.json"
    meta = json.loads(meta_path.read_text())
    meta["pending_attachments"] = [
        {"mid": ROOT, "name": "1.txt", "reason": "old client false positive", "url": ""}
    ]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h2", "attachments": ["テスト1.txt"]}]),
    )
    body = await plan.json()
    assert body["want_messages"] == [ROOT]
    assert body["want_attachments"] == []


# ---------------------------------------------------------------------------
# self-healing + safety
# ---------------------------------------------------------------------------


async def test_a_deleted_file_is_fetched_again(client: TestClient) -> None:
    """The filesystem is the source of truth, not the chain journal."""
    push = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    folder = Path((await push.json())["folder"])
    (folder / "messages" / f"{ROOT}.md").unlink()

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h1"}]),
    )
    assert (await plan.json())["want_messages"] == [ROOT]


async def test_thread_is_found_after_the_folder_is_renamed(client: TestClient, vault: Path) -> None:
    """Identity lives in thread.json, so a hand-renamed folder keeps syncing."""
    push = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    folder = Path((await push.json())["folder"])
    renamed = folder.parent / "胡田が名前を変えたフォルダ"
    folder.rename(renamed)

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h1"}]),
    )
    body = await plan.json()
    assert body["folder"] == str(renamed)
    assert body["want_messages"] == []


async def test_traversal_in_ids_is_refused(client: TestClient) -> None:
    for bad_thread in ({"team": "../../etc", "root_mid": ROOT}, {"team": TEAM, "root_mid": "../x"}):
        resp = await client.post(
            "/api/teams/sync/plan", headers=AUTH, json={"thread": bad_thread, "messages": []}
        )
        assert resp.status == 400


async def test_traversal_in_an_attachment_name_is_flattened(
    client: TestClient, vault: Path
) -> None:
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "x",
                    "h1",
                    attachments=[
                        {
                            "name": "../../../../etc/passwd",
                            "status": "embedded",
                            "data": base64.b64encode(b"nope").decode(),
                        }
                    ],
                )
            ]
        ),
    )
    folder = Path((await resp.json())["folder"])
    written = list((folder / "messages" / ROOT).iterdir())
    assert [p.name for p in written] == ["passwd"]
    assert not (vault.parent / "etc").exists()


async def test_sync_requires_the_ingest_token(client: TestClient) -> None:
    for path in ("/api/teams/sync/plan", "/api/teams/sync/push"):
        assert (await client.post(path, json=thread_body())).status == 401
        bad = {"Authorization": "Bearer wrong"}
        assert (await client.post(path, headers=bad, json=thread_body())).status == 401


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_thread_dirname_is_readable_and_unique() -> None:
    name = teams_sync.thread_dirname("MEHJ後のIntune登録に関して", ROOT)
    assert name == f"MEHJ後のIntune登録に関して--{ROOT}"
    # Path- and Obsidian-hostile characters never reach the filesystem.
    assert "/" not in teams_sync.thread_dirname("a/b:c*d?[e]", ROOT)


def test_yaml_scalars_survive_a_japanese_title_with_a_colon() -> None:
    rendered = teams_sync.render_message(
        teams_sync.IncomingMessage(mid=ROOT, hash="h1", text="本文", author="胡田: テスト"),
        teams_sync.ThreadRef(team=TEAM, root_mid=ROOT, title="件名: あり"),
        prev_mid=None,
        edited=False,
        first_synced_at="t",
        last_synced_at="t",
        pending=[],
    )
    assert 'author: "胡田: テスト"' in rendered
    # A quoted mid stays a string, so it keeps matching its filename.
    assert f'mid: "{ROOT}"' in rendered


def test_store_defaults_beside_the_ingest_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must be somewhere every deployment already has.

    ccdb is a framework: a default pointing at one operator's note vault would
    create directories on machines that have no such vault. Personal locations
    are what the environment variable is for.
    """
    monkeypatch.delenv("CCDB_TEAMS_VAULT_ROOT", raising=False)
    assert TeamsVaultStore(working_dir="/srv/bot").root == Path("/srv/bot/teams")
    monkeypatch.setenv("CCDB_TEAMS_VAULT_ROOT", "/tmp/elsewhere")
    assert TeamsVaultStore(working_dir="/srv/bot").root == Path("/tmp/elsewhere")


async def test_sync_writes_under_the_working_dir_by_default(
    db_path: str, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no override configured, threads land in {working_dir}/teams."""
    monkeypatch.delenv("CCDB_TEAMS_VAULT_ROOT", raising=False)
    repo = NotificationRepository(db_path)
    await repo.init_db()
    api = ApiServer(repo=repo, bot=MagicMock(), ingest_token=TOKEN, working_dir=str(vault))
    async with TestClient(TestServer(api.app)) as c:
        resp = await c.post(
            "/api/teams/sync/push",
            headers=AUTH,
            json=thread_body(messages=[msg(ROOT, "本文", "h1")]),
        )
        folder = Path((await resp.json())["folder"])
    assert folder.parent == vault / "teams"


# ---------------------------------------------------------------------------
# Company (org) folders — one level of grouping above the thread folder
# ---------------------------------------------------------------------------


async def test_thread_lands_in_its_company_folder(client: TestClient, vault: Path) -> None:
    """A labelled thread is filed under the company, not at the vault root."""
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "日本工営"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    folder = Path((await resp.json())["folder"])
    assert folder.parent == vault / "日本工営"
    assert json.loads((folder / "thread.json").read_text())["org"] == "日本工営"


async def test_unlabelled_thread_stays_at_the_root(client: TestClient, vault: Path) -> None:
    """No company means no guess: the thread stays where it always was.

    Inventing a bucket for every deployment that never labels anything would
    nest the whole vault one level deeper for no gain. A thread sitting at the
    root is exactly what "not filed yet" should look like.
    """
    resp = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    assert Path((await resp.json())["folder"]).parent == vault


async def test_a_thread_already_filed_flat_is_reused_not_duplicated(
    client: TestClient, vault: Path
) -> None:
    """The migration case: sync a thread flat, then label it.

    Identity lives in thread.json, so the existing folder must be found wherever
    it sits. Missing it would create a second folder and re-upload the whole
    history — silently, because every message would simply look new.
    """
    first = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    flat = Path((await first.json())["folder"])

    second = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "日本工営"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    assert Path((await second.json())["folder"]) == flat
    assert sorted(p.name for p in vault.iterdir() if p.is_dir()) == [flat.name]


async def test_a_thread_moved_into_a_company_folder_by_hand_keeps_syncing(
    client: TestClient, vault: Path
) -> None:
    """Hand-filing the vault is the supported migration path, so find it there."""
    first = await client.post(
        "/api/teams/sync/push", headers=AUTH, json=thread_body(messages=[msg(ROOT, "本文", "h1")])
    )
    flat = Path((await first.json())["folder"])
    moved = vault / "日本工営" / flat.name
    moved.parent.mkdir()
    flat.rename(moved)

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "h1"}]),
    )
    body = await plan.json()
    assert body["folder"] == str(moved)
    assert body["want_messages"] == []


async def test_the_orgs_file_is_authoritative_over_the_client(
    client: TestClient, vault: Path
) -> None:
    """A hand-edited orgs.json wins.

    The client label is a convenience typed in a popup; the file is the operator's
    filing decision. If the client could override it, correcting a company name
    would last exactly until the next sync.
    """
    (vault / "orgs.json").write_text(
        json.dumps({"teams": {TEAM: "日本工営"}}, ensure_ascii=False), encoding="utf-8"
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "打ち間違えた会社"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    assert Path((await resp.json())["folder"]).parent == vault / "日本工営"


async def test_a_new_company_label_is_remembered_for_the_team(
    client: TestClient, vault: Path
) -> None:
    """Learn once: a team the file does not know is recorded on first use.

    Chats and channels both key off the (derived) team GUID, so labelling one
    conversation files every later one from the same company automatically.
    """
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "日本工営"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    assert json.loads((vault / "orgs.json").read_text())["teams"][TEAM] == "日本工営"

    other = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": "1784110000999", "title": "別件"},
            messages=[msg("1784110000999", "本文", "h9")],
        ),
    )
    assert Path((await other.json())["folder"]).parent == vault / "日本工営"


async def test_a_hostile_company_name_cannot_escape_the_vault(
    client: TestClient, vault: Path
) -> None:
    """The label is free text from the network and becomes a path segment."""
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "../../etc"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    folder = Path((await resp.json())["folder"])
    assert str(folder).startswith(str(vault) + os.sep)
    assert folder.parent.parent == vault


def test_org_dirname_keeps_a_readable_japanese_name() -> None:
    assert teams_sync.org_dirname("日本工営") == "日本工営"
    assert teams_sync.org_dirname("  ") == ""
    assert "/" not in teams_sync.org_dirname("a/b:c")
    assert teams_sync.org_dirname("..") == ""


async def test_readme_names_the_company(client: TestClient) -> None:
    """The folder alone stops being visible once an agent is handed a path."""
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            thread={"team": TEAM, "root_mid": ROOT, "title": "件名", "org": "日本工営"},
            messages=[msg(ROOT, "本文", "h1")],
        ),
    )
    readme = (Path((await resp.json())["folder"]) / "README.md").read_text()
    assert "- 会社: 日本工営" in readme


# ---------------------------------------------------------------------------
# conversation-scoped identity (long chats that cannot be scrolled to the top)
# ---------------------------------------------------------------------------
#
# A chat has no team GUID, so the extension derives one. It used to derive it
# from the chat's *oldest* message id, which meant the identity only existed
# once the whole history had been scrolled. On a long chat Teams becomes
# unstable well before that, so the client threw the partial scan away rather
# than fork the folder — and the longest conversations could never sync at all.
#
# `identity_scope: "conversation"` says the team alone is the identity, so the
# root mid is free to move as the client works backwards through the history.


def chat_body(root_mid: str, *, scope: str = "conversation", **over) -> dict:
    body = thread_body()
    body["thread"] = dict(body["thread"], root_mid=root_mid, identity_scope=scope)
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_conversation_scope_keeps_one_folder_as_the_root_mid_moves(
    client: TestClient, vault: Path
) -> None:
    """The newest chunk lands first; scrolling back must reuse the same folder."""
    newest = await client.post(
        "/api/teams/sync/push",
        json=chat_body("1784800000000", messages=[msg("1784800000000", "new", "h1")]),
        headers=AUTH,
    )
    assert newest.status == 200
    # A later chunk reaches further back, so the oldest mid it can see is older.
    older = await client.post(
        "/api/teams/sync/push",
        json=chat_body("1770000000000", messages=[msg("1770000000000", "old", "h2")]),
        headers=AUTH,
    )
    assert older.status == 200

    folders = [p for p in vault.rglob("thread.json")]
    assert len(folders) == 1, f"a moving root mid forked the vault: {folders}"
    stored = sorted(p.name for p in (folders[0].parent / "messages").glob("*.md"))
    assert stored == ["1770000000000.md", "1784800000000.md"]


@pytest.mark.asyncio
async def test_conversation_scope_records_the_oldest_mid_seen_so_far(
    client: TestClient, vault: Path
) -> None:
    await client.post(
        "/api/teams/sync/push",
        json=chat_body("1784800000000", messages=[msg("1784800000000", "new", "h1")]),
        headers=AUTH,
    )
    await client.post(
        "/api/teams/sync/push",
        json=chat_body("1770000000000", messages=[msg("1770000000000", "old", "h2")]),
        headers=AUTH,
    )
    meta = json.loads(next(vault.rglob("thread.json")).read_text(encoding="utf-8"))
    assert meta["root_mid"] == "1770000000000", "the root must follow the history back"
    # A newer chunk arriving later must not drag the root forward again.
    await client.post(
        "/api/teams/sync/push",
        json=chat_body("1790000000000", messages=[msg("1790000000000", "newer", "h3")]),
        headers=AUTH,
    )
    meta = json.loads(next(vault.rglob("thread.json")).read_text(encoding="utf-8"))
    assert meta["root_mid"] == "1770000000000"


@pytest.mark.asyncio
async def test_a_plan_finds_the_folder_by_conversation_alone(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push",
        json=chat_body("1784800000000", messages=[msg("1784800000000", "new", "h1")]),
        headers=AUTH,
    )
    plan = await client.post("/api/teams/sync/plan", json=chat_body("1770000000000"), headers=AUTH)
    body = await plan.json()
    assert body["exists"] is True, "a different root mid must still find the chat"
    assert body["have"] == 1, "the stored message is already accounted for"


@pytest.mark.asyncio
async def test_without_the_scope_a_different_root_is_still_a_different_thread(
    client: TestClient, vault: Path
) -> None:
    """Channels and pre-upgrade clients keep the old {team}/{root_mid} key.

    A real team GUID holds many threads, so team-only matching there would merge
    unrelated conversations into one folder.
    """
    for root in ("1784800000000", "1770000000000"):
        res = await client.post(
            "/api/teams/sync/push",
            json=chat_body(root, scope="oldest-mid", messages=[msg(root, "x", "h" + root)]),
            headers=AUTH,
        )
        assert res.status == 200
    assert len(list(vault.rglob("thread.json"))) == 2


@pytest.mark.asyncio
async def test_an_unknown_scope_falls_back_to_the_strict_key(
    client: TestClient, vault: Path
) -> None:
    """Never widen matching because of a value this version does not understand."""
    for root in ("1784800000000", "1770000000000"):
        await client.post(
            "/api/teams/sync/push",
            json=chat_body(root, scope="something-new", messages=[msg(root, "x", "h" + root)]),
            headers=AUTH,
        )
    assert len(list(vault.rglob("thread.json"))) == 2


@pytest.mark.asyncio
async def test_plan_advertises_conversation_scope_support(client: TestClient) -> None:
    """The client must be able to tell an upgraded server from an old one.

    Without this it cannot know whether flushing a partial scan is safe, and the
    safe default (never flush) would keep long chats unsyncable forever.
    """
    plan = await client.post("/api/teams/sync/plan", json=thread_body(), headers=AUTH)
    body = await plan.json()
    assert body.get("capabilities", {}).get("conversation_scope") is True


# ---------------------------------------------------------------------------
# re-sync of an unchanged message — #790
# ---------------------------------------------------------------------------


async def test_a_new_hash_scheme_is_not_an_edit(client: TestClient) -> None:
    """A client that changes its hash formula must not archive the whole thread."""
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "同じ本文", "fnv1a:aaaa1111")]),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "同じ本文", "fnv1a2:bbbb2222")]),
    )
    body = await resp.json()
    folder = Path(body["folder"])
    note = (folder / "messages" / f"{ROOT}.md").read_text()
    assert 'hash: "fnv1a2:bbbb2222"' in note
    assert "edited: false" in note
    assert not list((folder / "_history").glob("*.md"))

    plan = await client.post(
        "/api/teams/sync/plan",
        headers=AUTH,
        json=thread_body(messages=[{"mid": ROOT, "hash": "fnv1a2:bbbb2222"}]),
    )
    assert (await plan.json())["want_messages"] == []


async def test_an_edit_within_the_same_scheme_is_still_archived(client: TestClient) -> None:
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "旧本文", "fnv1a2:aaaa1111")]),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "新本文", "fnv1a2:bbbb2222")]),
    )
    folder = Path((await resp.json())["folder"])
    assert "edited: true" in (folder / "messages" / f"{ROOT}.md").read_text()
    assert len(list((folder / "_history").glob(f"{ROOT}.*.md"))) == 1


async def test_a_stored_attachment_stays_linked_when_a_later_push_misses_it(
    client: TestClient,
) -> None:
    """Teams lazy-loads images; a pass that did not see one must not unlink it."""
    payload = base64.b64encode(b"png-bytes").decode()
    await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "スクショです",
                    "h1",
                    attachments=[{"name": "画像.png", "status": "embedded", "data": payload}],
                )
            ]
        ),
    )
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(messages=[msg(ROOT, "スクショです", "h1")]),
    )
    folder = Path((await resp.json())["folder"])
    note = (folder / "messages" / f"{ROOT}.md").read_text()
    assert f"- 📎 [[{ROOT}/画像.png]]" in note
    assert 'attachments: ["画像.png"]' in note
    assert (folder / "messages" / ROOT / "画像.png").read_bytes() == b"png-bytes"


async def test_a_duplicate_attachment_name_renders_once(client: TestClient) -> None:
    resp = await client.post(
        "/api/teams/sync/push",
        headers=AUTH,
        json=thread_body(
            messages=[
                msg(
                    ROOT,
                    "仕様調査を添付します",
                    "h1",
                    attachments=[
                        {"name": "仕様調査.pdf", "status": "unavailable", "reason": "HTTP 404"},
                        {"name": "仕様調査.pdf", "status": "unavailable", "reason": "言及のみ"},
                    ],
                )
            ]
        ),
    )
    folder = Path((await resp.json())["folder"])
    note = (folder / "messages" / f"{ROOT}.md").read_text()
    assert note.count("添付未取得: 仕様調査.pdf") == 1
    assert 'attachments: ["仕様調査.pdf"]' in note
