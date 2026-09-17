"""Tests for the explicit escalation channel.

The interesting assertions are all about refusal: the value of this module is
that it will not send anything when its isolation is not intact.
"""

from __future__ import annotations

import pytest

from claude_code_core.escalation import (
    ConsultChannel,
    Escalation,
    IsolationError,
    verify_isolation,
)
from claude_code_core.privacy import (
    AnonymizationRules,
    Anonymizer,
    AnswerabilityVerdict,
    AuditLog,
    InspectionPolicy,
    InspectionResult,
    MappingStore,
    PrivacyGateway,
    Suspect,
)

RULES = {"terms": [{"value": "Contoso", "category": "org"}], "builtins": []}


def make_gateway(policy=InspectionPolicy.OFF, inspection=None, audit_path=None):
    class _FakeInspector:
        model = "fake"

        async def inspect(self, text: str) -> InspectionResult:
            assert inspection is not None
            return inspection

    return PrivacyGateway(
        anonymizer=Anonymizer(rules=AnonymizationRules.from_dict(RULES), store=MappingStore()),
        inspector=_FakeInspector() if inspection is not None else None,  # type: ignore[arg-type]
        policy=policy,
        audit=AuditLog(audit_path),
    )


class TestIsolationContract:
    def test_a_correct_spawn_has_no_problems(self, tmp_path):
        args = ConsultChannel().build_args("hello")
        assert verify_isolation(args, tmp_path) == []

    def test_missing_setting_sources_is_caught(self, tmp_path):
        args = [a for a in ConsultChannel().build_args("hi") if a != "--setting-sources"]
        problems = verify_isolation(args, tmp_path)
        assert any("setting-sources" in p for p in problems)

    def test_non_empty_setting_sources_is_caught(self, tmp_path):
        args = ConsultChannel().build_args("hi")
        args[args.index("--setting-sources") + 1] = "user"
        assert any("setting-sources" in p for p in verify_isolation(args, tmp_path))

    def test_missing_separator_is_caught(self, tmp_path):
        args = [a for a in ConsultChannel().build_args("hi") if a != "--"]
        assert any("separator" in p for p in verify_isolation(args, tmp_path))

    def test_a_tool_left_enabled_is_caught(self, tmp_path):
        args = ConsultChannel().build_args("hi")
        args[args.index("--tools") + 1] = "Bash"
        problems = verify_isolation(args, tmp_path)
        assert any("--tools is not empty" in p for p in problems)

    def test_a_missing_tools_flag_is_caught(self, tmp_path):
        args = ConsultChannel().build_args("hi")
        index = args.index("--tools")
        del args[index : index + 2]
        assert any("--tools is missing" in p for p in verify_isolation(args, tmp_path))

    def test_a_non_empty_working_directory_is_caught(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("customer notes", encoding="utf-8")
        assert any(
            "not empty" in p for p in verify_isolation(ConsultChannel().build_args("hi"), tmp_path)
        )

    def test_a_missing_working_directory_is_caught(self, tmp_path):
        problems = verify_isolation(ConsultChannel().build_args("hi"), tmp_path / "absent")
        assert any("does not exist" in p for p in problems)

    def test_no_tool_name_is_enumerated_anywhere_in_the_argv(self):
        """The regression from #645: a hand-written tool name that went stale.

        `--disallowedTools SlashCommand` made CLI 2.1.273 warn that the rule
        "matches no known tool". The fix is not to correct the name — any
        enumeration has to be re-checked against every CLI release — so the
        invariant is that the argv before the prompt names no tool at all.
        """
        args = ConsultChannel().build_args("hi")
        before_prompt = args[: args.index("--")]
        assert "SlashCommand" not in before_prompt
        for tool in ("Bash", "Read", "WebFetch", "Task", "ToolSearch", "Skill"):
            assert tool not in before_prompt
        assert before_prompt[before_prompt.index("--tools") + 1] == ""

    @pytest.mark.parametrize(
        "flag", ["--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools"]
    )
    def test_a_tool_selection_override_is_caught(self, tmp_path, flag):
        """Fail closed on either direction the empty allow list can be undone."""
        args = ConsultChannel().build_args("hi")
        args[args.index("--") : args.index("--")] = [flag, "Bash"]
        assert any(flag in p for p in verify_isolation(args, tmp_path))

    def test_prompt_is_the_last_argument(self):
        args = ConsultChannel().build_args("the question")
        assert args[-1] == "the question"
        assert args[-2] == "--"


class TestChannelRefusal:
    async def test_broken_isolation_sends_nothing(self, monkeypatch):
        channel = ConsultChannel()
        monkeypatch.setattr(channel, "build_args", lambda prompt: ["claude", "-p", "--", prompt])

        spawned: list[object] = []

        async def _no_spawn(*args: object, **kwargs: object):
            spawned.append(args)

        monkeypatch.setattr("asyncio.create_subprocess_exec", _no_spawn)

        with pytest.raises(IsolationError, match="Nothing was sent"):
            await channel.ask("a question")
        assert spawned == []


class _FakeChannel(ConsultChannel):
    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer
        self.received: list[str] = []

    async def ask(self, prompt: str) -> str:
        self.received.append(prompt)
        return self.answer


class TestEscalation:
    async def test_question_goes_out_anonymized_and_answer_comes_back_restored(self):
        gateway = make_gateway()
        # Mint the alias first so the fake answer can use it.
        alias = gateway.anonymizer.anonymize("Contoso").replacements[0].alias
        channel = _FakeChannel(f"You should check {alias}'s tenant settings.")
        result = await Escalation(gateway=gateway, channel=channel).consult(
            "Contoso のテナント設定で困っています"
        )

        assert result.allowed
        assert "Contoso" not in channel.received[0]
        assert "Contoso" in result.answer
        assert result.substitutions == 1

    async def test_a_blocked_question_never_reaches_the_channel(self):
        gateway = make_gateway(
            policy=InspectionPolicy.BLOCK,
            inspection=InspectionResult(suspects=(Suspect(value="Fabrikam"),)),
        )
        channel = _FakeChannel("should not happen")
        result = await Escalation(gateway=gateway, channel=channel).consult("Fabrikam の件")

        assert result.blocked
        assert channel.received == []
        assert "Fabrikam" in (result.reason or "")

    async def test_the_audit_records_the_question_and_the_answer(self, tmp_path):
        import json

        path = tmp_path / "audit.jsonl"
        gateway = make_gateway(audit_path=path)
        channel = _FakeChannel("an answer")
        await Escalation(gateway=gateway, channel=channel).consult("Contoso", thread_id=7)

        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events = [r["event"] for r in records]
        assert events == ["outbound", "consult_answer"]
        assert records[0]["kind"] == "consult"
        assert records[0]["thread_id"] == 7
        assert "Contoso" not in records[0]["text"]


class _FakeJudge:
    """Records what it was asked to judge and returns a canned verdict."""

    model = "fake-judge"

    def __init__(self, verdict: AnswerabilityVerdict) -> None:
        self.verdict = verdict
        self.seen: list[str] = []

    async def judge(self, text: str) -> AnswerabilityVerdict:
        self.seen.append(text)
        return self.verdict


class TestAnswerabilityGate:
    """Anonymizing the subject of a question can leave nothing to answer.

    Asking for the merits of `org-002` is not a bug in the isolation or in the
    replacement — both worked. It is a question that stopped being a question,
    and the cheapest place to notice is before the external call.
    """

    def _gateway(self):
        return make_gateway()

    async def test_an_unanswerable_question_never_reaches_the_channel(self):
        channel = _FakeChannel("should not happen")
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, reason="org-001 の実体が必要"))
        result = await Escalation(gateway=self._gateway(), channel=channel, judge=judge).consult(
            "Contoso の良い点と悪い点"
        )

        assert result.blocked
        assert channel.received == [], "nothing may be sent once the judge objects"
        assert "org-001 の実体が必要" in (result.reason or "")

    async def test_the_reason_says_how_to_proceed(self):
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, reason="identity required"))
        result = await Escalation(
            gateway=self._gateway(), channel=_FakeChannel("x"), judge=judge
        ).consult("Contoso の評判")

        reason = result.reason or ""
        assert "force" in reason, "a false positive must have a documented way past it"

    async def test_the_reason_does_not_double_its_punctuation(self):
        """The judge writes a sentence; here it is a clause inside one."""
        judge = _FakeJudge(
            AnswerabilityVerdict(answerable=False, reason="org-001 の実体が必要です。")
        )
        result = await Escalation(
            gateway=self._gateway(), channel=_FakeChannel("x"), judge=judge
        ).consult("Contoso の評判")

        assert "。." not in (result.reason or "")
        assert ".." not in (result.reason or "")

    async def test_the_judge_sees_the_anonymized_text_never_the_original(self):
        """The judge is a model too. It gets the same redacted text as the vendor."""
        judge = _FakeJudge(AnswerabilityVerdict(answerable=True))
        await Escalation(gateway=self._gateway(), channel=_FakeChannel("ok"), judge=judge).consult(
            "Contoso のテナント設定"
        )

        assert judge.seen, "the judge should have been consulted"
        assert "Contoso" not in judge.seen[0]
        assert "org-001" in judge.seen[0]

    async def test_force_skips_the_judge_entirely(self):
        channel = _FakeChannel("an answer")
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, reason="no"))
        result = await Escalation(gateway=self._gateway(), channel=channel, judge=judge).consult(
            "Contoso の評判", force=True
        )

        assert result.allowed
        assert judge.seen == [], "force must not even pay for the judgement"
        assert channel.received

    async def test_nothing_replaced_means_nothing_to_judge(self):
        """A question with no substitutions cannot have been broken by them.

        This is the common case for technical questions, and it must cost no
        local call at all.
        """
        channel = _FakeChannel("an answer")
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, reason="never asked"))
        result = await Escalation(gateway=self._gateway(), channel=channel, judge=judge).consult(
            "条件付きアクセスの切り分け手順を教えて"
        )

        assert result.allowed
        assert judge.seen == []
        assert channel.received

    async def test_an_unavailable_judge_still_sends(self):
        """Fail open — the opposite of the leak inspector, deliberately."""
        channel = _FakeChannel("an answer")
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, available=False, error="timeout"))
        result = await Escalation(gateway=self._gateway(), channel=channel, judge=judge).consult(
            "Contoso の評判"
        )

        assert result.allowed
        assert channel.received

    async def test_no_judge_configured_changes_nothing(self):
        channel = _FakeChannel("an answer")
        result = await Escalation(gateway=self._gateway(), channel=channel).consult("Contoso")
        assert result.allowed
        assert channel.received

    async def test_a_leak_block_wins_over_the_judge(self):
        """Safety first: never spend a judgement on text that cannot be sent."""
        gateway = make_gateway(
            policy=InspectionPolicy.BLOCK,
            inspection=InspectionResult(suspects=(Suspect(value="Fabrikam"),)),
        )
        judge = _FakeJudge(AnswerabilityVerdict(answerable=True))
        result = await Escalation(gateway=gateway, channel=_FakeChannel("x"), judge=judge).consult(
            "Fabrikam の件"
        )

        assert result.blocked
        assert judge.seen == []

    async def test_the_block_is_audited(self, tmp_path):
        import json

        path = tmp_path / "audit.jsonl"
        judge = _FakeJudge(AnswerabilityVerdict(answerable=False, reason="identity required"))
        await Escalation(
            gateway=make_gateway(audit_path=path), channel=_FakeChannel("x"), judge=judge
        ).consult("Contoso の評判", thread_id=9)

        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events = [r["event"] for r in records]
        assert "consult_unanswerable" in events
        record = records[events.index("consult_unanswerable")]
        assert record["thread_id"] == 9
        assert "Contoso" not in json.dumps(record, ensure_ascii=False)


class TestToolIsolationIsAnAllowList:
    """Naming the tools to forbid cannot hold: the tool set keeps growing.

    Measured 2026-08-17 with the deny list alone, the consult still had
    ToolSearch (the entry point to every MCP tool — Gmail, Calendar, Azure),
    Skill and Workflow; extending the list by hand then left CronCreate,
    RemoteTrigger and DesignSync. `--tools ""` is the allow-list form and was
    measured to actually stop Bash from running.
    """

    def test_build_args_disables_every_tool(self):
        args = ConsultChannel().build_args("hi")
        assert "--tools" in args
        assert args[args.index("--tools") + 1] == ""

    def test_build_args_ignores_configured_mcp_servers(self):
        assert "--strict-mcp-config" in ConsultChannel().build_args("hi")

    def test_missing_tools_flag_is_refused(self, tmp_path):
        args = [a for a in ConsultChannel().build_args("hi") if a != "--tools"]
        problems = verify_isolation(args, tmp_path)
        assert any("--tools" in p for p in problems)

    def test_non_empty_tools_flag_is_refused(self, tmp_path):
        args = ConsultChannel().build_args("hi")
        args[args.index("--tools") + 1] = "Bash"
        problems = verify_isolation(args, tmp_path)
        assert any("--tools" in p for p in problems)

    def test_missing_strict_mcp_config_is_refused(self, tmp_path):
        args = [a for a in ConsultChannel().build_args("hi") if a != "--strict-mcp-config"]
        problems = verify_isolation(args, tmp_path)
        assert any("mcp" in p.lower() for p in problems)

    def test_the_prompt_still_survives_the_extra_flags(self):
        args = ConsultChannel().build_args("the question")
        assert args[-1] == "the question"
        assert args[-2] == "--"
