"""Authenticated message search, scoped to conversations the Console can display."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from claude_code_core.thread_search import run_thread_search
from claude_code_core.transcript_search import make_snippet

from .thread_archive import ThreadArchive

if TYPE_CHECKING:
    from .server import ConsoleServer


class SearchRoutes:
    def __init__(self, server: ConsoleServer, archive: ThreadArchive | None = None) -> None:
        self.server = server
        self.archive = archive if archive is not None else ThreadArchive.from_env()
        self._lock = asyncio.Lock()

    async def archived(self, request: web.Request) -> web.Response:
        """A thread deleted from Discord, read back from its export. Read-only."""
        raw = request.match_info["thread_id"]
        if self.archive is None or not raw.isdigit() or len(raw) > 20:
            return web.json_response({"error": "not archived"}, status=404)
        found = await self.archive.read(int(raw))
        if found is None:
            return web.json_response({"error": "not archived"}, status=404)
        title, messages = found
        return web.json_response({"thread_id": raw, "title": title, "messages": messages})

    @staticmethod
    def _archived_hit(thread_id: int, title: str, snippet: str, when: str | None) -> Any:
        item = {"id": f"a{thread_id}", "title": title, "bucket": "done", "last_activity_at": when}
        return {"item": item, "snippet": snippet, "source": "archive", "archived": True}

    async def search(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        if not query or len(query) > 500:
            return web.json_response({"error": "q must contain 1–500 characters"}, status=400)
        if self._lock.locked():
            return web.json_response({"error": "Search is busy. Try again shortly."}, status=429)
        async with self._lock:
            items = await self.server._board_items()
            board = {str(i["thread_id"]): i for i in items if i.get("thread_id")}
            hits: dict[str, dict[str, Any]] = {}
            for item in items:
                source = "\n".join(
                    str(item.get(k) or "") for k in ("title", "note", "project", "working_dir")
                )
                if query.lower() in source.lower():
                    hits[item["id"]] = {
                        "item": item,
                        "snippet": make_snippet(source, query),
                        "source": "summary",
                    }
            if self.server.conversations is not None:
                for hit in await self.server.conversations.search(query, limit=50):
                    item = board.get(str(hit["thread_key"]))
                    if item is not None:
                        hits[item["id"]] = {
                            "item": item,
                            "snippet": make_snippet(hit["content"], query),
                            "source": "body",
                        }
            partial = False
            missing: dict[int, str] = {}  # deleted from Discord: thread id → snippet
            api = self.server.api
            if api.session_repo is not None and isinstance(api.transcripts_path, str):
                try:
                    async with asyncio.timeout(15):
                        transcripts = await run_thread_search(
                            session_repo=api.session_repo,
                            query=query,
                            limit=50,
                            include_body=True,
                            transcripts_root=api.transcripts_path,
                            body_limit=100,
                        )
                    # Older archived threads may be outside the board's normal archive page.
                    discovered = False
                    for hit in transcripts:
                        if hit.thread_id is None or str(hit.thread_id) in board:
                            continue
                        thread, error = await self.server._thread_for(f"t{hit.thread_id}")
                        if error is None:
                            cache = self.server._search_threads
                            cache[thread.id] = thread
                            while len(cache) > 50:
                                del cache[next(iter(cache))]
                            discovered = True
                    if discovered:
                        board.update(
                            {
                                str(item["thread_id"]): item
                                for item in await self.server._board_items()
                                if item.get("thread_id")
                            }
                        )
                    for hit in transcripts:
                        item = board.get(str(hit.thread_id))
                        if item is None and hit.thread_id is not None:
                            missing.setdefault(
                                int(hit.thread_id),
                                hit.snippet or make_snippet(hit.summary or "", query),
                            )
                        if item is not None and item["id"] not in hits:
                            hits[item["id"]] = {
                                "item": item,
                                "snippet": hit.snippet or make_snippet(hit.summary or "", query),
                                "source": hit.source,
                            }
                except TimeoutError:
                    partial = True
            if self.archive is not None:
                try:
                    async with asyncio.timeout(10):
                        await self._add_archived(query, board, missing, hits)
                except TimeoutError:
                    partial = True
            results = sorted(
                hits.values(),
                key=lambda h: str(h["item"].get("last_activity_at") or ""),
                reverse=True,
            )
            return web.json_response(
                {
                    "query": query,
                    "results": results[:50],
                    "partial": partial,
                    "more": len(results) > 50,
                }
            )

    async def _add_archived(
        self,
        query: str,
        board: dict[str, dict[str, Any]],
        missing: dict[int, str],
        hits: dict[str, dict[str, Any]],
    ) -> None:
        """Add threads deleted from Discord whose export survives."""
        assert self.archive is not None
        for found in await self.archive.search(query, limit=50):
            if str(found.thread_id) not in board:
                hits[f"a{found.thread_id}"] = self._archived_hit(
                    found.thread_id, found.title, found.snippet, found.last_activity_at
                )
        # Matched in a transcript or summary, but not in the export's own messages.
        for thread_id, snippet in missing.items():
            if f"a{thread_id}" in hits:
                continue
            content = await self.archive.read(thread_id)
            if content is not None:
                title, messages = content
                when = str(messages[-1]["created_at"]) if messages else None
                hits[f"a{thread_id}"] = self._archived_hit(thread_id, title, snippet, when)
