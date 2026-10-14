"""Authenticated message search, scoped to conversations the Console can display."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from aiohttp import web

from claude_code_core.thread_search import run_thread_search
from claude_code_core.transcript_search import make_snippet

if TYPE_CHECKING:
    from .server import ConsoleServer


class SearchRoutes:
    def __init__(self, server: ConsoleServer) -> None:
        self.server = server
        self._lock = asyncio.Lock()

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
                        if item is not None and item["id"] not in hits:
                            hits[item["id"]] = {
                                "item": item,
                                "snippet": hit.snippet or make_snippet(hit.summary or "", query),
                                "source": hit.source,
                            }
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
