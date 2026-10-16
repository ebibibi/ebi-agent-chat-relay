"""Authenticated Workspace routes; the server owns authentication and CSRF checks."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .vault import VaultError, wikilinks
from .workspace import WorkspaceConflictError, WorkspaceError

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
