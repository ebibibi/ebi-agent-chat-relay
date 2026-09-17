"""Explicit escalation: ask a strong external model one self-contained question.

This is the other half of the local-first design. A thread does its work on a
model you control; when it needs research, a second opinion or a plan, exactly
one anonymized question goes out and exactly one answer comes back.

What makes the claim checkable is that the external CLI runs with **no context
of its own**. That is not a documented procedure — it is verified immediately
before every spawn, because a procedure that can be skipped when someone is in
a hurry always eventually is:

1. ``--setting-sources ""`` — no CLAUDE.md, skills or memory.
2. An **empty** temporary directory as cwd — nothing local to read.
3. ``--tools ""`` — the CLI's own "no built-in tools" mode, so a tool added
   by a later release is off too; no shell to escape the directory with.
4. ``--`` before the prompt — without it the variadic tool list eats the
   prompt and the CLI dies with "Input must be provided...".

Point 1 is not paranoia. Measured with cwd=/home/ebi and every tool already
disabled, changing *only* ``--setting-sources``: with it, the model reported
that a term unique to the project's CLAUDE.md was absent from its context;
without it, present. A naive escalation ships the whole file — customer names
included — no matter how carefully the question was anonymized.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .privacy.answerability import AnswerabilityJudge, AnswerabilityVerdict
from .privacy.gateway import GuardOutcome, PrivacyGateway

logger = logging.getLogger(__name__)

__all__ = [
    "ConsultChannel",
    "ConsultOutcome",
    "Escalation",
    "IsolationError",
    "verify_isolation",
]


class IsolationError(RuntimeError):
    """Raised when a consult would run without its isolation intact."""


# Tool-selection flags that must never appear beside ``--tools ""``. An allow
# override widens the empty list; a deny list re-introduces hand-maintained tool
# names, which is what broke this route once already.
_FORBIDDEN_TOOL_FLAGS: tuple[str, ...] = (
    "--allowedTools",
    "--allowed-tools",
    "--disallowedTools",
    "--disallowed-tools",
)

# Secrets and control-plane handles that have no business in a consult.
_STRIPPED_ENV_KEYS = frozenset(
    {
        "CLAUDECODE",
        "DISCORD_BOT_TOKEN",
        "DISCORD_TOKEN",
        "API_SECRET_KEY",
        "CCDB_API_URL",
        "CCDB_API_SECRET",
        "CCDB_CLI_ENV_FILE",
    }
)


def verify_isolation(args: list[str], cwd: str | Path) -> list[str]:
    """Return the isolation guarantees that are NOT in place.

    An empty list means the four properties in the module docstring all hold
    for this exact spawn. Checking the argv we are about to use — rather than
    trusting the code that built it — is what makes this a route and not a
    procedure.
    """
    problems: list[str] = []

    if "--setting-sources" in args:
        index = args.index("--setting-sources")
        if index + 1 >= len(args) or args[index + 1] != "":
            problems.append("--setting-sources is set to something other than empty")
    else:
        problems.append("--setting-sources is missing (CLAUDE.md and skills would be sent)")

    if "--tools" in args:
        index = args.index("--tools")
        if index + 1 >= len(args) or args[index + 1] != "":
            problems.append("--tools is not empty (tools would be available)")
    else:
        problems.append("--tools is missing (every built-in tool would be allowed)")

    if "--strict-mcp-config" not in args:
        problems.append("--strict-mcp-config is missing (configured MCP servers would be sent)")

    if "--" not in args:
        problems.append("-- separator is missing (the tool list would swallow the prompt)")

    for flag in _FORBIDDEN_TOOL_FLAGS:
        if flag in args:
            problems.append(f'{flag} is present (only --tools "" may select tools)')

    path = Path(cwd)
    if not path.is_dir():
        problems.append(f"working directory {path} does not exist")
    elif any(path.iterdir()):
        problems.append(f"working directory {path} is not empty")

    return problems


@dataclass(frozen=True)
class ConsultOutcome:
    """The result of one escalation."""

    allowed: bool
    question_sent: str = ""
    answer: str = ""
    reason: str | None = None
    warning: str | None = None
    substitutions: int = 0

    @property
    def blocked(self) -> bool:
        return not self.allowed


@dataclass
class ConsultChannel:
    """Runs one hardened, single-shot, text-only CLI call."""

    command: str = "claude"
    model: str = "sonnet"
    timeout_seconds: int = 300

    def build_args(self, prompt: str) -> list[str]:
        return [
            self.command,
            "-p",
            "--model",
            self.model,
            "--setting-sources",
            "",
            # Allow list, and only an allow list. A deny list cannot be the
            # primary layer: measured 2026-08-17 it still left ToolSearch (the
            # entry point to every MCP tool), Skill and Workflow, and extending
            # it by hand then left CronCreate, RemoteTrigger and DesignSync.
            # It cannot be a second layer either, because it goes stale in the
            # other direction: measured on CLI 2.1.273 a name the CLI has since
            # dropped makes it warn that the rule "matches no known tool", so
            # the list has to track renames the allow list is immune to.
            "--tools",
            "",
            # Without this the consult inherits the operator's configured MCP
            # servers — Gmail, Calendar, cloud APIs — none of which belong in
            # a one-question, text-in-text-out escalation.
            "--strict-mcp-config",
            "--",
            prompt,
        ]

    async def ask(self, prompt: str) -> str:
        """Ask once and return the plain-text answer."""
        workdir = tempfile.mkdtemp(prefix="ccdb-consult-")
        try:
            args = self.build_args(prompt)
            problems = verify_isolation(args, workdir)
            if problems:
                raise IsolationError(
                    "Refusing to escalate: " + "; ".join(problems) + ". Nothing was sent."
                )
            env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_KEYS}
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise
            if process.returncode:
                detail = stderr.decode("utf-8", errors="replace").strip()[:200]
                raise RuntimeError(f"Consult CLI exited with {process.returncode}: {detail}")
            return stdout.decode("utf-8", errors="replace").strip()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


@dataclass
class Escalation:
    """Anonymize a question, ask an external model, restore the answer."""

    gateway: PrivacyGateway
    channel: ConsultChannel = field(default_factory=ConsultChannel)
    judge: AnswerabilityJudge | None = None

    async def consult(
        self, question: str, *, force: bool = False, **context: object
    ) -> ConsultOutcome:
        """Send one question out, under the gateway's policy.

        Two gates, in this order. The leak check decides whether the text *may*
        go; the answerability check decides whether it is *worth* sending. Order
        matters: never spend a judgement on text that cannot leave anyway.
        """
        outcome = await self.gateway.guard(question, kind="consult", **context)
        if not outcome.allowed:
            return ConsultOutcome(allowed=False, reason=outcome.reason)

        verdict = await self._judge_answerability(outcome, force=force)
        if verdict is not None and verdict.blocks:
            reason = _unanswerable_reason(verdict)
            self.gateway.audit.record(
                "consult_unanswerable",
                judge=verdict.model,
                judge_reason=verdict.reason,
                substitutions=outcome.result.total_substitutions,
                **context,
            )
            logger.info("Escalation withheld as unanswerable: %s", verdict.reason)
            return ConsultOutcome(allowed=False, reason=reason)

        answer = await self.channel.ask(outcome.text)
        restored = self.gateway.restore(answer)
        self.gateway.audit.record(
            "consult_answer",
            model=self.channel.model,
            answer_chars=len(answer),
            **context,
        )
        return ConsultOutcome(
            allowed=True,
            question_sent=outcome.text,
            answer=restored,
            warning=outcome.warning,
            substitutions=outcome.result.total_substitutions,
        )

    async def _judge_answerability(
        self, outcome: GuardOutcome, *, force: bool
    ) -> AnswerabilityVerdict | None:
        """Ask the local judge, or return ``None`` when there is nothing to ask.

        Skipped when nothing was replaced: anonymization cannot have broken a
        question it did not touch. That is the common case for technical
        questions, so the usual `/ask` pays no extra latency at all.
        """
        if force or self.judge is None:
            return None
        if outcome.result.total_substitutions == 0:
            return None
        # The judge is a model too, and gets the redacted text — the same one
        # the vendor would receive, never the original question.
        return await self.judge.judge(outcome.text)


def _unanswerable_reason(verdict: AnswerabilityVerdict) -> str:
    """Explain the refusal in terms of the fix, not the mechanism."""
    detail = verdict.reason or "the answer would depend on who the placeholders really are"
    # The judge writes a sentence; this is a clause inside one. Measured: models
    # end the reason with a full stop, giving "…cons.. Either describe…".
    detail = detail.rstrip(" 。．.")
    return (
        "Not sent: after anonymization this question is about placeholders, and "
        f"answering it needs the identity that was removed — {detail}. "
        "Either describe them generically (industry, size, role) and ask again, "
        "ask about the general case instead, or pass force: true to send it as is."
    )
