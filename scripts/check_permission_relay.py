#!/usr/bin/env python3
"""Acceptance check: a hook's "ask" reaches ccdb, and the answer decides whether the tool runs.

Drives the real Claude CLI through ClaudeRunner, in a temporary directory, with a
hook that asks about one harmless command. The answer arrives after the runner's
silence limit, so this also proves a slow answer does not end the turn.
It does not cover the Discord button itself.

    uv run python scripts/check_permission_relay.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from claude_code_core.approvals import ALLOW, DENY, permission_result
from claude_code_core.runner import ClaudeRunner

HOOK = """
import json, sys
command = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
if "ran.marker" in command:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "ask", "permissionDecisionReason": "acceptance check"}}))
"""


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

    asked = False
    answers = []
    prompt = f"Run exactly this Bash command once, then stop: touch {marker}"
    async for event in runner.run(prompt):
        request = event.permission_request
        if request is not None and request.reason == "acceptance check":
            asked = True

            async def reply(request_id: str = request.request_id) -> None:
                await asyncio.sleep(10)
                await runner.inject_tool_result(request_id, permission_result((answer,)))

            answers.append(asyncio.create_task(reply()))
        if event.is_complete:
            break
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
