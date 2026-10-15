"""Cross-backend conversation history handoff tests."""

from __future__ import annotations

import json
from pathlib import Path

from claude_discord.cross_backend_handoff import (
    ConversationHistoryReader,
    build_handoff_prompt,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


class TestConversationHistoryReader:
    def test_reads_claude_user_and_assistant_text_only(self, tmp_path: Path) -> None:
        session_id = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
        _write_jsonl(
            tmp_path / "project" / f"{session_id}.jsonl",
            [
                {"type": "user", "isMeta": True, "message": {"content": "internal"}},
                {"type": "user", "message": {"content": "最初の質問"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "secret reasoning"},
                            {"type": "text", "text": "最初の回答"},
                            {"type": "tool_use", "name": "Bash"},
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "content": "noise"}]},
                },
                # An unanswered tail is excluded: the current Discord message is
                # supplied separately and must not be duplicated.
                {"type": "user", "message": {"content": "未回答の末尾"}},
            ],
        )

        reader = ConversationHistoryReader(claude_sessions_root=tmp_path)

        transcript = reader.read("claude", session_id)

        assert transcript == "User:\n最初の質問\n\nAssistant:\n最初の回答"
        assert "internal" not in transcript
        assert "secret reasoning" not in transcript
        assert "noise" not in transcript
        assert "未回答の末尾" not in transcript

    def test_reads_codex_event_messages_only(self, tmp_path: Path) -> None:
        session_id = "cccccccc-1111-2222-3333-dddddddddddd"
        rollout = (
            tmp_path
            / "sessions"
            / "2026"
            / "08"
            / "05"
            / (f"rollout-2026-08-05T12-00-00-{session_id}.jsonl")
        )
        _write_jsonl(
            rollout,
            [
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "developer", "content": "ignore"},
                },
                {"type": "event_msg", "payload": {"type": "user_message", "message": "依頼"}},
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": "対応したよ"},
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "token_count", "message": "ignore"},
                },
            ],
        )

        reader = ConversationHistoryReader(codex_home=tmp_path)

        assert reader.read("codex", session_id) == "User:\n依頼\n\nAssistant:\n対応したよ"

    def test_reads_current_codex_item_completed_messages(self, tmp_path: Path) -> None:
        session_id = "01a079d0-351b-7a72-8d88-527a380ae9f8"
        rollout = tmp_path / "sessions" / "2026" / "09" / "07" / f"rollout-x-{session_id}.jsonl"
        _write_jsonl(
            rollout,
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "UserMessage",
                            "content": [{"type": "text", "text": "test", "text_elements": []}],
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {"type": "Reasoning", "summary_text": ["ignore"]},
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "AgentMessage",
                            "content": [{"type": "Text", "text": "受信できてるよ"}],
                            "phase": "final_answer",
                        },
                    },
                },
            ],
        )

        reader = ConversationHistoryReader(codex_home=tmp_path)

        assert reader.read("codex", session_id) == "User:\ntest\n\nAssistant:\n受信できてるよ"

    def test_rejects_invalid_session_id_before_path_lookup(self, tmp_path: Path) -> None:
        reader = ConversationHistoryReader(
            claude_sessions_root=tmp_path,
            codex_home=tmp_path,
        )

        assert reader.read("claude", "../../etc/passwd") == ""
        assert reader.read("codex", "not valid") == ""

    def test_bounds_message_and_total_transcript_size(self, tmp_path: Path) -> None:
        session_id = "eeeeeeee-1111-2222-3333-ffffffffffff"
        records: list[dict] = []
        for number in range(20):
            role = "user" if number % 2 == 0 else "assistant"
            records.append(
                {"type": role, "message": {"content": f"message-{number}-" + ("x" * 200)}}
            )
        _write_jsonl(tmp_path / f"{session_id}.jsonl", records)
        reader = ConversationHistoryReader(
            claude_sessions_root=tmp_path,
            max_messages=4,
            max_message_chars=40,
            max_transcript_chars=140,
        )

        transcript = reader.read("claude", session_id)

        assert "message-16" in transcript
        assert "message-19" in transcript
        assert "message-15" not in transcript
        assert len(transcript) <= 140


def test_build_handoff_prompt_marks_history_and_current_message() -> None:
    prompt = build_handoff_prompt(
        source_backend="claude",
        target_backend="codex",
        transcript="User:\nold question\n\nAssistant:\nold answer",
        current_prompt="new question",
    )

    assert "Claude" in prompt
    assert "Codex" in prompt
    assert "old question" in prompt
    assert "old answer" in prompt
    assert prompt.endswith("Current user message:\nnew question")
    assert "do not repeat completed work" in prompt
