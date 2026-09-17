"""Tests for the example EbiBot Todoist watchdog."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from examples.ebibot.cogs import watchdog as watchdog_module
from examples.ebibot.cogs.watchdog import _FETCH_ATTEMPTS, WatchdogCog


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


def test_fetch_overdue_tasks_reports_an_error_when_every_attempt_is_empty() -> None:
    """Exhausted retries must fail loudly, never as a silently skipped check."""
    cog = WatchdogCog(MagicMock())
    empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch(
            "examples.ebibot.cogs.watchdog.subprocess.run",
            side_effect=[empty] * _FETCH_ATTEMPTS,
        ) as run,
        patch.object(watchdog_module.logger, "error") as log_error,
    ):
        assert cog._fetch_overdue_tasks() == []

    assert run.call_count == _FETCH_ATTEMPTS
    log_error.assert_called_once()
    assert "after %d attempts" in log_error.call_args.args[0]
