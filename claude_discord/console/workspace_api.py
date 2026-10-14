"""Authenticated Workspace routes; the server owns authentication and CSRF checks."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web

from .workspace import WorkspaceConflictError, WorkspaceError

if TYPE_CHECKING:
    from .server import ConsoleServer


class WorkspaceRoutes:
    def __init__(self, server: ConsoleServer) -> None:
        self.server = server

    async def read(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.server.workspace.snapshot))

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
                }
            )
            return await self.server._start_guarded(item.id, {}, request[IDENTITY])
        except WorkspaceConflictError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        except WorkspaceError as exc:
            return web.json_response({"error": str(exc)}, status=400)
