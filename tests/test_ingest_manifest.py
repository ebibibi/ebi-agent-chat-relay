"""Tests for attachment-delivery verification (``ext/ingest_manifest.py``).

The bug these pin: an ingest client that lost an attachment on the way — a
download that failed, a size cap, a screenshot that never rendered — produced an
ingest indistinguishable from a complete one, and the session answered as if it
had everything. The manifest makes the shortfall provable; these tests hold the
matching rules and the "say it loudly" behaviour in place.
"""

from __future__ import annotations

from pathlib import Path

from claude_discord.ext import ingest_manifest as im


def _write(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestParseManifest:
    def test_absent_manifest_is_not_an_error(self) -> None:
        entries, err = im.parse_manifest(None)
        assert entries == []
        assert err is None

    def test_accepts_a_bare_list(self) -> None:
        entries, err = im.parse_manifest([{"name": "a.png"}])
        assert err is None
        assert entries[0].name == "a.png"
        # Default status is "the bytes are in this request".
        assert entries[0].claims_bytes

    def test_accepts_a_wrapper_object(self) -> None:
        entries, err = im.parse_manifest({"entries": [{"name": "a.png"}]})
        assert err is None and len(entries) == 1

    def test_rejects_a_non_list(self) -> None:
        entries, err = im.parse_manifest("nope")
        assert entries == [] and err is not None

    def test_rejects_an_oversized_manifest(self) -> None:
        _, err = im.parse_manifest([{"name": "x"}] * (im.MAX_MANIFEST_ENTRIES + 1))
        assert err is not None

    def test_skips_malformed_entries_without_failing_the_ingest(self) -> None:
        entries, err = im.parse_manifest([{"name": "ok.png"}, "garbage", 42])
        assert err is None
        assert [e.name for e in entries] == ["ok.png"]

    def test_unknown_status_is_treated_as_failed_not_delivered(self) -> None:
        # A status ccdb doesn't understand must never be read as "arrived fine".
        entries, _ = im.parse_manifest([{"name": "a.png", "status": "probably-fine"}])
        assert entries[0].status == im.STATUS_FAILED
        assert not entries[0].claims_bytes

    def test_strips_newlines_from_client_fields(self) -> None:
        entries, _ = im.parse_manifest([{"name": "a\nb.png", "reason": "x\r\ny"}])
        assert "\n" not in entries[0].name
        assert entries[0].reason is not None and "\n" not in entries[0].reason


class TestReconcile:
    def test_no_manifest_means_unverified_not_complete(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "a.png", b"a")])
        result = im.reconcile([], files)
        assert result.verified is False
        assert result.unlisted and result.delivered == 1

    def test_sha256_wins_over_a_matching_name(self, tmp_path: Path) -> None:
        # Same name, different bytes: the hash must decide which file is which,
        # otherwise the wrong path is handed to the session.
        right = _write(tmp_path, "shot.png", b"correct-bytes")
        decoy = _write(tmp_path, "shot_2.png", b"other")
        files = im.hash_files([decoy, right])
        sha = next(f.sha256 for f in files if f.path == right)
        entries, _ = im.parse_manifest([{"name": "shot.png", "sha256": sha.upper()}])
        result = im.reconcile(entries, files)
        assert result.matched[0][1].path == right

    def test_matches_an_index_prefixed_name(self, tmp_path: Path) -> None:
        # Bundlers rename collisions image.png -> 4_image.png.
        files = im.hash_files([_write(tmp_path, "4_image.png", b"x")])
        entries, _ = im.parse_manifest([{"name": "image.png"}])
        result = im.reconcile(entries, files)
        assert result.is_complete and result.matched[0][1].name == "4_image.png"

    def test_does_not_match_an_unrelated_name_ending_the_same_way(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "screenshot-image.png", b"x")])
        entries, _ = im.parse_manifest([{"name": "image.png"}])
        result = im.reconcile(entries, files)
        assert result.missing and result.unlisted

    def test_two_entries_cannot_share_one_file(self, tmp_path: Path) -> None:
        # The original silent loss: two attachments named image.png, one file on
        # disk, and a naive name match calls it complete.
        files = im.hash_files([_write(tmp_path, "image.png", b"x")])
        entries, _ = im.parse_manifest([{"name": "image.png"}, {"name": "image.png"}])
        result = im.reconcile(entries, files)
        assert len(result.matched) == 1
        assert len(result.missing) == 1
        assert not result.is_complete

    def test_linked_and_skipped_count_as_not_delivered(self, tmp_path: Path) -> None:
        entries, _ = im.parse_manifest(
            [
                {"name": "log.txt", "status": "linked", "url": "https://sp/log.txt"},
                {"name": "big.zip", "status": "skipped", "reason": "size cap"},
            ]
        )
        result = im.reconcile(entries, [])
        assert result.expected == 0  # neither claimed to carry bytes
        assert len(result.not_delivered) == 2
        assert not result.is_complete

    def test_complete_delivery_reports_complete(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "a.png", b"a"), _write(tmp_path, "b.png", b"b")])
        entries, _ = im.parse_manifest([{"name": "a.png"}, {"name": "b.png"}])
        result = im.reconcile(entries, files)
        assert result.is_complete and result.expected == 2 and not result.unlisted

    def test_size_is_the_last_resort_match(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "renamed-by-teams.png", b"12345")])
        entries, _ = im.parse_manifest([{"name": "original.png", "size": 5}])
        result = im.reconcile(entries, files)
        assert result.is_complete


class TestPromptWarning:
    def test_complete_delivery_adds_no_warning(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "a.png", b"a")])
        entries, _ = im.parse_manifest([{"name": "a.png"}])
        assert im.render_prompt_warning(im.reconcile(entries, files)) is None

    def test_unverified_delivery_adds_no_warning(self) -> None:
        # No manifest → nothing proven → no false alarm.
        assert im.render_prompt_warning(im.reconcile([], [])) is None

    def test_missing_attachment_is_named_and_the_session_is_told_not_to_guess(
        self,
    ) -> None:
        entries, _ = im.parse_manifest(
            [{"name": "ACMEdebug.log", "status": "linked", "message": "返信 12"}]
        )
        warning = im.render_prompt_warning(im.reconcile(entries, []))
        assert warning is not None
        assert "ACMEdebug.log" in warning
        assert "推測" in warning

    def test_newest_message_losing_an_attachment_is_called_out(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "old.png", b"o")])
        entries, _ = im.parse_manifest(
            [
                {"name": "old.png", "message": "返信 1"},
                {"name": "latest.log", "status": "failed", "message": "返信 2"},
            ]
        )
        warning = im.render_prompt_warning(im.reconcile(entries, files))
        assert warning is not None and "返信 2" in warning

    def test_no_newest_callout_when_only_an_older_message_lost_a_file(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "new.png", b"n")])
        entries, _ = im.parse_manifest(
            [
                {"name": "old.log", "status": "failed", "message": "返信 1"},
                {"name": "new.png", "message": "返信 2"},
            ]
        )
        warning = im.render_prompt_warning(im.reconcile(entries, files))
        assert warning is not None
        assert "最新メッセージ「返信 1」" not in warning


class TestAttachmentSection:
    def test_unverified_falls_back_to_a_flat_path_list(self, tmp_path: Path) -> None:
        paths = [tmp_path / "a.png", tmp_path / "b.png"]
        section = im.render_attachment_section(im.reconcile([], []), paths)
        assert str(paths[0]) in section and str(paths[1]) in section

    def test_groups_by_message_and_flags_the_newest(self, tmp_path: Path) -> None:
        a = _write(tmp_path, "a.png", b"a")
        b = _write(tmp_path, "b.png", b"b")
        files = im.hash_files([a, b])
        entries, _ = im.parse_manifest(
            [{"name": "a.png", "message": "元投稿"}, {"name": "b.png", "message": "返信 2"}]
        )
        section = im.render_attachment_section(im.reconcile(entries, files), [a, b])
        assert "■ 元投稿" in section
        assert "最新メッセージ" in section
        assert section.index("元投稿") < section.index("返信 2")


class TestReport:
    def test_report_states_the_verdict_and_lists_losses(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "a.png", b"a")])
        entries, _ = im.parse_manifest(
            [{"name": "a.png"}, {"name": "gone.log", "status": "failed", "reason": "403"}]
        )
        report = im.render_report(im.reconcile(entries, files))
        assert "欠落あり" in report and "gone.log" in report and "403" in report

    def test_unverified_report_says_so_rather_than_claiming_success(self) -> None:
        report = im.render_report(im.reconcile([], []))
        assert "検証できません" in report


class TestResponseSummary:
    def test_summary_exposes_what_the_client_must_resend(self, tmp_path: Path) -> None:
        files = im.hash_files([_write(tmp_path, "a.png", b"a")])
        entries, _ = im.parse_manifest(
            [
                {"name": "a.png"},
                {"name": "b.png"},
                {"name": "c.log", "status": "linked", "url": "https://x/c.log"},
            ]
        )
        summary = im.summarize_for_response(im.reconcile(entries, files))
        assert summary["verified"] is True
        assert summary["complete"] is False
        assert [m["name"] for m in summary["missing"]] == ["b.png"]  # type: ignore[index]
        assert [m["name"] for m in summary["not_delivered"]] == ["c.log"]  # type: ignore[index]

    def test_unverified_summary_reports_none_not_true(self) -> None:
        summary = im.summarize_for_response(im.reconcile([], []))
        assert summary["verified"] is False
        assert summary["complete"] is None
