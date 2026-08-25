"""Tests for the example EbiBot Todoist watchdog."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from examples.ebibot.cogs.watchdog import WatchdogCog


def test_fetch_overdue_tasks_retries_an_empty_response() -> None:
    cog = WatchdogCog(MagicMock())
    task = {"id": "task-1", "content": "Follow up"}

    with patch(
        "examples.ebibot.cogs.watchdog.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout='[{"id":"task-1","content":"Follow up"}]', stderr=""
            ),
        ],
    ) as run:
        assert cog._fetch_overdue_tasks() == [task]

    assert run.call_count == 2
