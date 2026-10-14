"""Tests for local Claude transcript body search (token-free grep over .jsonl)."""

from __future__ import annotations

import json

from claude_code_core.transcript_search import (
    TranscriptHit,
    make_snippet,
    scan_file_for_query,
    search_transcripts,
)

_SID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_SID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _write_transcript(path, blocks: list[tuple[str, str]]) -> None:
    """Write a .jsonl transcript. blocks = list of (role, text)."""
    with open(path, "w", encoding="utf-8") as f:
        for role, text in blocks:
            f.write(
                json.dumps(
                    {
                        "type": role,
                        "message": {"role": role, "content": [{"type": "text", "text": text}]},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def test_make_snippet_centres_on_match() -> None:
    text = "x" * 200 + "NEEDLE" + "y" * 200
    snip = make_snippet(text, "needle", width=20)
    assert "NEEDLE" in snip
    assert snip.startswith("…") and snip.endswith("…")
    assert len(snip) < 80


def test_make_snippet_no_match_returns_head() -> None:
    snip = make_snippet("hello world", "zzz", width=20)
    assert snip.startswith("hello")


def test_scan_file_finds_japanese_body(tmp_path) -> None:
    p = tmp_path / f"{_SID_A}.jsonl"
    _write_transcript(
        p, [("user", "SharePointの書き込みが失敗する件を調べたい"), ("assistant", "了解です")]
    )
    snip = scan_file_for_query(str(p), "書き込みが失敗")
    assert snip is not None
    assert "書き込みが失敗" in snip


def test_scan_file_no_match_returns_none(tmp_path) -> None:
    p = tmp_path / f"{_SID_A}.jsonl"
    _write_transcript(p, [("user", "hello")])
    assert scan_file_for_query(str(p), "nonexistent") is None


async def test_search_transcripts_returns_hits(tmp_path) -> None:
    _write_transcript(tmp_path / f"{_SID_A}.jsonl", [("user", "deploy the JAIX dashboard tonight")])
    _write_transcript(tmp_path / f"{_SID_B}.jsonl", [("user", "unrelated conversation")])
    hits = await search_transcripts(str(tmp_path), "JAIX", limit=10)
    assert [h.session_id for h in hits] == [_SID_A]
    assert isinstance(hits[0], TranscriptHit)
    assert "JAIX" in hits[0].snippet


async def test_search_transcripts_case_insensitive(tmp_path) -> None:
    _write_transcript(tmp_path / f"{_SID_A}.jsonl", [("user", "The Substack Sync Broke")])
    hits = await search_transcripts(str(tmp_path), "substack", limit=10)
    assert len(hits) == 1


async def test_search_transcripts_respects_limit(tmp_path) -> None:
    for i in range(5):
        sid = f"{i}{_SID_A[1:]}"
        _write_transcript(tmp_path / f"{sid}.jsonl", [("user", "common keyword here")])
    hits = await search_transcripts(str(tmp_path), "common keyword", limit=2)
    assert len(hits) == 2


async def test_search_transcripts_missing_root_is_empty() -> None:
    assert await search_transcripts("/no/such/dir", "x", limit=5) == []


async def test_search_transcripts_blank_query_is_empty(tmp_path) -> None:
    _write_transcript(tmp_path / f"{_SID_A}.jsonl", [("user", "hello")])
    assert await search_transcripts(str(tmp_path), "   ", limit=5) == []


async def test_search_transcripts_python_fallback(tmp_path) -> None:
    """With grep disabled, the pure-Python scan path must still find matches."""
    _write_transcript(tmp_path / f"{_SID_A}.jsonl", [("user", "find me without grep")])
    hits = await search_transcripts(str(tmp_path), "without grep", limit=10, use_grep=False)
    assert [h.session_id for h in hits] == [_SID_A]


async def test_cancelled_search_reaps_the_prefilter_process(monkeypatch) -> None:
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from claude_code_core.transcript_search import _grep_candidate_files

    process = MagicMock()
    process.returncode = None
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    process.wait = AsyncMock(return_value=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    import pytest

    with pytest.raises(asyncio.CancelledError):
        await _grep_candidate_files("/transcripts", "query")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()
