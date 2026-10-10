"""Relay Console — triage agent work by priority, tree and due date.

The console is an API-first surface on its own listener. It overlays the
threads ccdb already runs with what a human needs to manage them (priority,
due date, snooze, project, parent) and derives everything else — whose move it
is, whether a turn is running, who spawned whom — from state ccdb already
keeps. Agents change nothing to appear here.

See ``docs/console.md``.
"""
