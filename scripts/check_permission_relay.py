#!/usr/bin/env python3
"""Acceptance check: a hook's "ask" reaches ccdb, and the answer decides whether the tool runs.

Drives the real Claude CLI through ClaudeRunner and EventProcessor, in a temporary
directory, with a hook that asks about one harmless command. The answer arrives after the runner's
silence limit, so this also proves a slow answer does not end the turn.
It stops at the surface, so the Discord button itself still needs a person.

    uv run python scripts/check_permission_relay.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from claude_code_core.approvals import ALLOW, DENY
from claude_code_core.frontend import ChoicePrompt
from claude_code_core.memory_surface import MemorySurface
from claude_code_core.runner import ClaudeRunner
from claude_discord.cogs.event_processor import EventProcessor
from claude_discord.cogs.run_config import RunConfig

HOOK = """
import json, sys
command = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
if "ran.marker" in command:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "ask", "permissionDecisionReason": "acceptance check"}}))
"""


class SlowSurface(MemorySurface):
    """Answers after the runner's silence limit has passed."""

    async def prompt_choice(self, prompt: ChoicePrompt) -> tuple[str, ...] | None:
        await asyncio.sleep(10)
        return await super().prompt_choice(prompt)


async def attempt(workdir: Path, answer: str) -> tuple[bool, bool]:
    """Return (was the question asked, did the command run)."""
    marker = workdir / "ran.marker"
    marker.unlink(missing_ok=True)
    settings = workdir / "settings.json"
    runner = ClaudeRunner(
        model="opus", permission_mode="auto", working_dir=str(workdir), timeout_seconds=5
    )
    build_args = runner._build_args
    runner._build_args = lambda prompt, session: [
        *build_args(prompt, session),
        "--settings",
        str(settings),
    ]
    surface = SlowSurface(answers=[[answer]])
    processor = EventProcessor(RunConfig(surface=surface, runner=runner, prompt="check"))

    prompt = f"Run exactly this Bash command once, then stop: touch {marker}"
    async for event in runner.run(prompt):
        await processor.process(event)
        if event.is_complete:
            break
    await processor.finalize()
    asked = any("acceptance check" in p.question for p in surface.prompts)
    return asked, marker.exists()


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        (workdir / "hook.py").write_text(HOOK)
        hook = {"type": "command", "command": f"python3 {workdir / 'hook.py'}"}
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [hook]}]}}
        (workdir / "settings.json").write_text(json.dumps(settings))

        results = {
            "allow": await attempt(workdir, ALLOW) == (True, True),
            "deny": await attempt(workdir, DENY) == (True, False),
        }
    for name, passed in results.items():
        print(f"{name}: {'OK' if passed else 'NG'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
