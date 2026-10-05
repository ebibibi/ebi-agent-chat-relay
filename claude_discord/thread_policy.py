"""How long Discord keeps a conversation thread visible.

Discord archives an inactive thread automatically, which removes it from the
channel's thread list — the conversation still exists, but the user has to open
"Show archived threads" to find it again. The window is chosen per thread at
creation time and only accepts 60, 1440, 4320 or 10080 minutes.

We always ask for the maximum. Threads here are conversations the user comes
back to, and several are deliberately kept open as a to-do list; a thread that
vanishes from the sidebar an hour after the last reply reads as lost work.
"""

# Discord's maximum auto-archive window (7 days, in minutes).
THREAD_AUTO_ARCHIVE_MINUTES = 10080

# What a thread opened by a message is called before any rename: the message's
# opening text, which is what Discord shows in the sidebar.
DEFAULT_THREAD_NAME = "Claude Chat"
THREAD_NAME_LIMIT = 100


def initial_thread_name(content: str | None) -> str:
    """The name ccdb gives a thread it opens from a message with *content*."""
    return (content or "")[:THREAD_NAME_LIMIT] or DEFAULT_THREAD_NAME
