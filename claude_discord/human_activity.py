"""Re-export of :mod:`claude_code_core.human_activity` (the restart quiet rule's clock)."""

from claude_code_core.human_activity import (
    last_human_activity,
    last_human_source,
    note_human_activity,
    reset_human_activity,
)

__all__ = [
    "last_human_activity",
    "last_human_source",
    "note_human_activity",
    "reset_human_activity",
]
