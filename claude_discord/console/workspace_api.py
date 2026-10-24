"""Authenticated Workspace routes; the server owns authentication and CSRF checks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .thread_notes import changed_notes
from .vault import VaultConflictError, VaultError, wikilinks
from .workspace import WorkspaceConflictError, WorkspaceError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .server import ConsoleServer


class WorkspaceRoutes:
    def __init__(self, server: ConsoleServer) -> None:
        self.server = server

    async def read(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.server.workspace.snapshot))

    async def plan(self, request: web.Request) -> web.Response:
        """Workspace next actions ranked by the plugin's goodmorning scoring (Focus view)."""
        return web.json_response(await asyncio.to_thread(self.server.workspace_plan.read))

    async def context(self, request: web.Request) -> web.Response:
        """One entry's current state, next actions, waiting-for and latest log."""
        try:
            return web.json_response(
                await asyncio.to_thread(
                    self.server.workspace.context, request.query.get("path", "")
                )
            )
        except (WorkspaceError, OSError, UnicodeError):
            return web.json_response({"error": "Unknown or unreadable Workspace entry"}, status=400)

    async def note(self, request: web.Request) -> web.Response:
        """Any Markdown note in the vault the owner did not exclude, with its wikilinks
        resolved (``links``: target → vault path, or null when nothing matches)."""
        index = self.server.note_index

        def read() -> dict[str, Any]:
            note = self.server.vault.note(request.query.get("path", ""))
            note["links"] = {
                target: index.resolve(target, note["path"])["path"]
                for target in wikilinks(note["text"])
            }
            return note

        try:
            return web.json_response(await asyncio.to_thread(read))
        except (VaultError, OSError):
            return web.json_response({"error": "Unknown note"}, status=404)

    async def save_note(self, request: web.Request) -> web.Response:
        """Save an edited note (``path``, ``revision``, ``text``; ``log``: record it too).

        A stale revision answers 409 with the note as it is now, so the browser can show
        the difference. ``log`` adds a line to the enclosing Workspace entry's log.md and
        the daily note, like a task change does.
        """
        body, err = await self.server._json(request)
        if err is not None:
            return err
        vault = self.server.vault
        try:
            saved = await asyncio.to_thread(
                vault.save, body.get("path"), body.get("text"), body.get("revision")
            )
        except VaultConflictError as exc:
            return web.json_response({"error": str(exc), "current": exc.current}, status=409)
        except (VaultError, OSError):
            return web.json_response({"error": "Could not save the note"}, status=400)
        self.server.workspace_plan.invalidate()
        if body.get("log") is True:
            await asyncio.to_thread(self._log_edit, saved["path"])
        return web.json_response(saved)

    def _log_edit(self, rel: str) -> None:
        """Record an edit in the log of the Workspace entry the note sits in, if any."""
        parts = rel.split("/")
        if len(parts) < 3 or parts[0] != "02_Contexts":
            return
        workspace = self.server.workspace
        for end in range(len(parts) - 1, 1, -1):
            entry = "/".join(parts[1:end])
            try:
                workspace.entry(entry)
            except (WorkspaceError, OSError, UnicodeError):
                continue
            try:
                workspace._record(entry, f"ノート編集: {parts[-1]}")
            except (WorkspaceError, OSError, UnicodeError) as exc:
                # The note itself is saved; a missing log line must not fail the save.
                logger.warning("console: note edit not logged for %s: %s", entry, exc)
            return

    async def thread_notes(self, request: web.Request) -> web.Response:
        """Vault notes the item's session wrote, with diffs since the thread started."""
        item_id = request.match_info["item_id"]
        repo = self.server.api.session_repo
        if self.server.workspace.root is None or repo is None:
            return web.json_response({"notes": []})
        if not item_id.startswith("t") or not item_id[1:].isdigit():
            return web.json_response({"notes": []})
        key = int(item_id[1:])
        record = await repo.get(key)
        if record is None:
            return web.json_response({"notes": []})
        notes = await asyncio.to_thread(
            changed_notes,
            self.server.vault,
            record.session_id,
            record.backend,
            record.created_at,
            await self.server.session_home(key, record.backend),
        )
        return web.json_response({"notes": notes, "since": record.created_at})

    async def backlinks(self, request: web.Request) -> web.Response:
        """Notes linking to one note (slower than a read, so asked for separately)."""
        index = self.server.note_index

        def read() -> list[str]:
            path = self.server.vault.note(request.query.get("path", ""))["path"]
            return index.backlinks(path)

        try:
            return web.json_response({"backlinks": await asyncio.to_thread(read)})
        except (VaultError, OSError):
            return web.json_response({"error": "Unknown note"}, status=404)

    async def resolve(self, request: web.Request) -> web.Response:
        """Where a ``[[link]]`` written outside any note (e.g. in a reply) points."""
        link = request.query.get("link", "")[:500]
        index = self.server.note_index
        found = await asyncio.to_thread(index.resolve, link, "")
        return web.json_response(found, status=200 if found["path"] else 404)

    async def search(self, request: web.Request) -> web.Response:
        """Full-text search over visible notes (``q``: space-separated terms, all required)."""
        query = request.query.get("q", "")[:200]
        index = self.server.note_index
        return web.json_response({"hits": await asyncio.to_thread(index.search_text, query)})

    async def notes(self, request: web.Request) -> web.Response:
        """Note paths matching every term of ``q`` (the quick switcher)."""
        query = request.query.get("q", "")[:200]
        index = self.server.note_index
        return web.json_response({"notes": await asyncio.to_thread(index.search_names, query)})

    async def file(self, request: web.Request) -> web.StreamResponse:
        """An image or PDF a note embeds, served as an inert download-safe file."""
        try:
            path, content_type = await asyncio.to_thread(
                self.server.vault.file, request.query.get("path", "")
            )
        except (VaultError, OSError):
            return web.json_response({"error": "Unknown file"}, status=404)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": content_type,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=300",
            },
        )

    async def excludes(self, request: web.Request) -> web.Response:
        """Read (GET) or replace (PUT, body ``{"text": …}``) the owner's exclusion list."""
        vault = self.server.vault
        if request.method == "PUT":
            body, err = await self.server._json(request)
            if err is not None:
                return err
            try:
                await asyncio.to_thread(vault.save_excludes, body.get("text"))
            except VaultError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except OSError:
                return web.json_response({"error": "Could not save the list"}, status=500)
        text = await asyncio.to_thread(vault.exclude_text)
        return web.json_response(
            {"text": text, "patterns": vault.patterns(), "file": str(vault.excludes_path)}
        )

    async def document(self, request: web.Request) -> web.Response:
        try:
            content = await asyncio.to_thread(
                self.server.workspace.document,
                request.query.get("path", ""),
                request.query.get("name", "overview.md"),
            )
            return web.json_response({"text": content})
        except (WorkspaceError, OSError, UnicodeError):
            return web.json_response(
                {"error": "Unknown or unreadable Workspace document"}, status=400
            )

    async def change(self, request: web.Request) -> web.Response:
        body, err = await self.server._json(request)
        if err is not None:
            return err
        try:
            path = body.get("path", "")
            revision = body.get("revision", "")
            if request.method == "POST":
                await asyncio.to_thread(
                    self.server.workspace.add_task, path, body.get("text", ""), revision
                )
            else:
                await asyncio.to_thread(
                    self.server.workspace.update_task,
                    path,
                    body.get("line", 0),
                    revision,
                    text=body.get("text"),
                    done=body.get("done"),
                )
            self.server.workspace_plan.invalidate()
            return web.json_response(await asyncio.to_thread(self.server.workspace.entry, path))
        except WorkspaceConflictError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except WorkspaceError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except (OSError, UnicodeError):
            return web.json_response({"error": "Could not update Workspace files"}, status=500)

    async def run(self, request: web.Request) -> web.Response:
        from .server import IDENTITY

        body, err = await self.server._json(request)
        if err is not None:
            return err
        try:
            entry = await asyncio.to_thread(self.server.workspace.entry, body.get("path", ""))
            if entry["revision"] != body.get("revision"):
                raise WorkspaceConflictError("The task changed. Refresh and try again.")
            task = next((t for t in entry["tasks"] if t["line"] == body.get("line")), None)
            if task is None or task["done"]:
                raise WorkspaceError("Select an open task")
            item = await self.server.work_repo.create_capture(
                {
                    "title": task["text"][:200],
                    "note": task["text"],
                    "workspace_path": entry["path"],
                    # Started from that task: the person chose where it belongs.
                    "workspace_source": "pinned",
                    "workspace_reason": "task",
                }
            )
            return await self.server._start_guarded(item.id, {}, request[IDENTITY])
        except WorkspaceConflictError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except WorkspaceError as exc:
            return web.json_response({"error": str(exc)}, status=400)
