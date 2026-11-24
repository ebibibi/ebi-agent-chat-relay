"""What an agent working toward a goal is told on every turn (``[GOAL CONTEXT]``).

Built from the goal model in ``goals.py``; the Workspace parts (project folders and
milestones) are passed in by the caller, so this module reads no files itself.
"""

from __future__ import annotations

from typing import Any

from .goals import SECTIONS, STATE_DONE, STATE_PAUSED, _block_lines, structure

BRIEF_HEADER = "[GOAL CONTEXT]"
#: How much of the goal's own section the brief quotes; the agent can read the rest.
BRIEF_SECTION_CHARS = 900


def section_text(text: str, goal: dict[str, Any], sections: tuple[str, ...] = SECTIONS) -> str:
    """The lines under the goal's heading, without the heading."""
    parsed = structure(text, sections)
    for block in parsed.blocks:
        if block.start + 1 == goal["line"]:
            return "\n".join(_block_lines(parsed.lines, block)[1:]).strip()
    return ""


def brief(
    goal: dict[str, Any],
    *,
    note_file: str,
    section: str,
    projects: list[dict[str, Any]],
    parent: str | None = None,
) -> str:
    """What an agent working toward *goal* is told on every turn: the goal, where it is
    written down, its projects and, for a child, what the parent is doing.

    *projects* are ``{"path", "directory", "milestones"}`` with absolute directories.
    """
    state = {STATE_PAUSED: "paused", STATE_DONE: "done"}.get(goal["state"], "active")
    lines = [
        BRIEF_HEADER,
        f'This conversation is work toward the goal "{goal["title"]}" ({state}'
        + (f", due {goal['due']}" if goal.get("due") else "")
        + ").",
    ]
    if goal.get("done_when"):
        lines.append(f"Done when: {goal['done_when']}")
    if goal.get("why"):
        lines.append(f"Why: {goal['why']}")
    heading = goal.get("heading") or goal["title"]
    lines.append(
        f'The goal is written in {note_file} under the heading "{heading}"; '
        "read that section when the request leaves something open."
    )
    if section:
        excerpt = (
            section if len(section) <= BRIEF_SECTION_CHARS else section[:BRIEF_SECTION_CHARS] + " …"
        )
        lines.append("Goal notes (excerpt):\n" + excerpt)
    for project in projects:
        line = f"Linked project {project['path']}: {project['directory']}/overview.md and status.md"
        m = project.get("milestones") or {}
        if m.get("total"):
            line += f" (milestones {m['done']}/{m['total']}"
            if m.get("next"):
                line += f", next: {m['next']['name']}" + (
                    f" {m['next']['date']}" if m["next"].get("date") else ""
                )
            line += ")"
        lines.append(line + ".")
    if parent:
        lines.append(f"The parent conversation is currently: {parent}")
    lines.append(
        "Interpret the request in the light of this goal, say how your work moves it forward, "
        "and do not ask what the work is about when the goal answers it."
    )
    return "\n".join(lines) + "\n"
