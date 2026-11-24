"""Authenticated goals routes (``/console/api/goals``); the server owns auth and CSRF.

The goals live in one Markdown note in the Ebi Workspace vault (``goals.py``). These routes
read it with the threads the board already knows, and write a person's edits back through
the vault's revision-checked save, so an edit made in Obsidian meanwhile is never lost.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from . import goal_brief
from . import goals as gl
from .vault import VaultConflictError, VaultError
from .work_repo import WorkItemError, thread_item_id
from .workspace import WorkspaceError
from .workspace_api import WorkspaceRoutes

if TYPE_CHECKING:
    from .server import ConsoleServer

logger = logging.getLogger(__name__)

#: Declared deliverables shown per goal; the family panel keeps the full list.
MAX_DELIVERABLES = 8


def _error(message: str, status: int, **extra: Any) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


class GoalRoutes:
    def __init__(self, server: ConsoleServer) -> None:
        self.server = server

    # -- reading --------------------------------------------------------------------------
    def _read_note(self, path: str) -> dict[str, Any]:
        return self.server.vault.note(path)

    def _projects(self, entries: list[dict[str, Any]], wanted: set[str]) -> dict[str, Any]:
        """Every Workspace path's kind and name; milestones only for the paths on screen."""
        out: dict[str, dict[str, Any]] = {}
        root = self.server.workspace.root
        for entry in entries:
            path = entry["path"]
            info: dict[str, Any] = {"kind": entry.get("kind"), "name": path.rsplit("/", 1)[-1]}
            if path in wanted and entry.get("kind") == "project" and root is not None:
                status = root / "02_Contexts" / path / "status.md"
                try:
                    if not status.is_symlink():
                        info["milestones"] = gl.milestones(status.read_text(encoding="utf-8"))
                except (OSError, UnicodeError):
                    info["milestones"] = None
            out[path] = info
        return out

    def _compose(self, items: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
        workspace = self.server.workspace
        entries = workspace.snapshot()["entries"]
        items = workspace.bind_items(items, entries)
        note = self._read_note(config["note"])
        goals = gl.parse_goals(note["text"])
        project_paths = {e["path"] for e in entries if e.get("kind") == "project"}
        index = self.server.note_index

        def resolve(target: str, source: str) -> str | None:
            return index.resolve(target, source)["path"]

        for goal in goals:
            goal["projects"] = gl.resolve_projects(goal, resolve, note["path"], project_paths)
        linked: set[str] = {p for g in goals for p in g["projects"]}
        bound: set[str] = {str(i["workspace_path"]) for i in items if i.get("workspace_path")}
        projects = self._projects(entries, linked | {p for p in bound if p in project_paths})
        screen = gl.build(goals, items, projects)
        active = sum(1 for g in goals if g["state"] == gl.STATE_ACTIVE)
        return {
            "available": True,
            "note": {k: note[k] for k in ("path", "revision", "modified")},
            "limit": config["limit"],
            "active": active,
            **screen,
        }

    async def _deliverables(self, items: list[dict[str, Any]], ids: list[str]) -> list[dict]:
        repo = getattr(self.server.api, "situation_repo", None)
        if repo is None:
            return []
        by_id = {i["id"]: i for i in items}
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for item_id in ids:
            thread = (by_id.get(item_id) or {}).get("thread_id")
            if not thread:
                continue
            try:
                written = await repo.get(int(thread))
            except Exception:
                logger.exception("console: goals: situation of %s unreadable", thread)
                continue
            for entry in (written.deliverables if written else None) or []:
                key = entry.get("url") or entry.get("file") or entry.get("title")
                if not key or key in seen or entry.get("superseded"):
                    continue
                seen.add(key)
                out.append({k: entry.get(k) for k in ("title", "url", "kind", "file")})
        return out[:MAX_DELIVERABLES]

    async def _doing(self, items: list[dict[str, Any]], ids: list[str]) -> dict[str, str]:
        """The first "doing" line of each listed thread's situation, by item id."""
        repo = getattr(self.server.api, "situation_repo", None)
        if repo is None:
            return {}
        by_id = {i["id"]: i for i in items}
        out: dict[str, str] = {}
        for item_id in ids:
            thread = (by_id.get(item_id) or {}).get("thread_id")
            if not thread:
                continue
            try:
                written = await repo.get(int(thread))
            except Exception:
                continue
            if written and written.doing:
                out[item_id] = str(written.doing[0])[:200]
        return out

    async def read(self, request: web.Request) -> web.Response:
        if self.server.workspace.root is None:
            return web.json_response({"available": False, "reason": "workspace"})
        config = await asyncio.to_thread(gl.load_config)
        if not config["note"]:
            return web.json_response({"available": False, "reason": "config"})
        items = await self.server._board_items()
        try:
            data = await asyncio.to_thread(self._compose, items, config)
        except VaultError:
            return web.json_response({"available": False, "reason": "note", "path": config["note"]})
        except (WorkspaceError, OSError, UnicodeError):
            logger.exception("console: goals unreadable")
            return _error("Could not read the goals", 500)
        groups = [g["items"] for g in data["goals"]] + [p["items"] for p in data["outside"]]
        for holder, ids in zip(data["goals"] + data["outside"], groups, strict=True):
            holder["deliverables"] = await self._deliverables(items, ids)
        data["doing"] = await self._doing(items, [i for ids in groups for i in ids])
        return web.json_response(data)

    # -- writing --------------------------------------------------------------------------
    async def change(self, request: web.Request) -> web.Response:
        """One edit to the goals note: ``revision`` plus the action ``goals.edit`` takes."""
        body, err = await self.server._json(request)
        if err is not None:
            return err
        config = await asyncio.to_thread(gl.load_config)
        if self.server.workspace.root is None or not config["note"]:
            return _error("No goals note is configured", 400)
        action = dict(body)
        revision = action.pop("revision", None)
        if action.get("op") in ("link", "unlink") and "project" in action:
            project = action.pop("project")
            entries = await asyncio.to_thread(self.server.workspace.snapshot)
            known = {e["path"] for e in entries["entries"] if e.get("kind") == "project"}
            if project not in known:
                return _error("Unknown Workspace project", 400)
            action["link"] = f"02_Contexts/{project}/status"
        vault = self.server.vault
        path = config["note"]

        def write() -> tuple[dict[str, Any], str | None, str | None]:
            note = vault.note(path)
            if note["revision"] != revision:
                raise VaultConflictError(note)
            before = {g["id"]: g for g in gl.parse_goals(note["text"])}
            text = gl.edit(note["text"], action)
            saved = vault.save(path, text, revision)
            renamed = None
            if action.get("op") == "update" and "title" in action:
                renamed = gl.goal_id(str(action["title"]))
            return (
                saved,
                (action.get("id") if renamed and action.get("id") in before else None),
                renamed,
            )

        try:
            saved, old_id, new_id = await asyncio.to_thread(write)
        except VaultConflictError as exc:
            return _error("The goals note changed since it was read", 409, current=exc.current)
        except gl.GoalError as exc:
            return _error(str(exc), 400)
        except (VaultError, OSError, UnicodeError):
            return _error("Could not save the goals note", 400)
        if old_id and new_id and old_id != new_id:
            await self._retag(old_id, new_id)
        await asyncio.to_thread(WorkspaceRoutes(self.server)._log_edit, saved["path"])
        self.server.workspace_plan.invalidate()
        return web.json_response({"note": {k: saved[k] for k in ("path", "revision", "modified")}})

    async def _retag(self, old: str, new: str) -> None:
        """Keep the threads a person put under a goal there when the goal is renamed."""
        for item in await self.server.work_repo.list_all():
            if f"{gl.TAG_PREFIX}{old}" not in item.tags:
                continue
            try:
                await self.server.work_repo.update(
                    item.id, {"tags": gl.with_goal_tag(item.tags, new)}
                )
            except WorkItemError as exc:
                logger.warning("console: goal tag of %s not moved: %s", item.id, exc)

    async def config(self, request: web.Request) -> web.Response:
        """Choose the goals note (``note``: vault-relative path) and the optional ``limit``."""
        body, err = await self.server._json(request)
        if err is not None:
            return err
        limit = body.get("limit")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            return _error("limit must be a positive integer or null", 400)
        try:
            note = gl.clean_note_path(body.get("note"))
            await asyncio.to_thread(self.server.vault.note, note)
            await asyncio.to_thread(gl.save_config, note, limit)
        except gl.GoalError as exc:
            return _error(str(exc), 400)
        except VaultError:
            return _error("No such note in the vault", 400)
        except OSError:
            return _error("Could not save the goals configuration", 500)
        return web.json_response({"note": note, "limit": limit})

    # -- the agent's goal context ----------------------------------------------------------
    #: How far up the family a goal is inherited.
    MAX_PARENT_HOPS = 5

    async def brief(self, item_id: str) -> str | None:
        """``[GOAL CONTEXT]`` for the item's next turn, or None when it serves no goal.

        The goal is found as on the Goals screen: the person's choice (tag), else the item's
        Workspace project linked to a goal, else its parent's goal. Never raises: a missing
        or unreadable goals note leaves the turn without the block.
        """
        try:
            return await self._brief(item_id)
        except Exception:
            logger.exception("console: goal context for %s not built", item_id)
            return None

    async def _parent_of(self, item_id: str, lineage: dict[int, int]) -> str | None:
        overlay = await self.server.work_repo.get(item_id)
        if overlay is not None and overlay.parent_id:
            return overlay.parent_id
        if item_id.startswith("t") and item_id[1:].isdigit():
            up = lineage.get(int(item_id[1:]))
            return thread_item_id(up) if up else None
        return None

    async def _brief(self, item_id: str) -> str | None:
        server = self.server
        if server.workspace.root is None:
            return None
        config = await asyncio.to_thread(gl.load_config)
        if not config["note"]:
            return None
        goals, note, projects = await asyncio.to_thread(self._goal_model, config["note"])
        if not goals:
            return None
        lineage = await server._lineage()
        current: str | None = item_id
        goal: dict[str, Any] | None = None
        for _ in range(self.MAX_PARENT_HOPS + 1):
            if current is None:
                break
            overlay = await server.work_repo.get(current)
            choice = gl.chosen_goal({"tags": list(overlay.tags) if overlay else []})
            if choice == "none":
                return None
            goal = next((g for g in goals if g["id"] == choice), None)
            path = overlay.workspace_path if overlay else None
            if goal is None and path:
                goal = next(
                    (g for g in goals if any(gl._under(path, p) for p in g["projects"])), None
                )
            if goal is not None:
                break
            current = await self._parent_of(current, lineage)
        if goal is None:
            return None
        parent = await self._parent_of(item_id, lineage)
        return goal_brief.brief(
            goal,
            note_file=str(server.workspace.root / note["path"]),
            section=goal_brief.section_text(note["text"], goal),
            projects=[projects[p] for p in goal["projects"] if p in projects],
            parent=await self._family_doing(parent),
        )

    def _goal_model(self, note_path: str) -> tuple[list[dict], dict[str, Any], dict[str, dict]]:
        """Goals with resolved projects, the note, and each linked project's folder and
        milestones (the Workspace adapter part)."""
        workspace = self.server.workspace
        note = self._read_note(note_path)
        goals = gl.parse_goals(note["text"])
        entries = workspace.snapshot()["entries"]
        known = {e["path"] for e in entries if e.get("kind") == "project"}
        index = self.server.note_index

        def resolve(target: str, source: str) -> str | None:
            return index.resolve(target, source)["path"]

        for goal in goals:
            goal["projects"] = gl.resolve_projects(goal, resolve, note["path"], known)
        linked: set[str] = {p for g in goals for p in g["projects"]}
        info = self._projects(entries, linked)
        root = workspace.root
        projects = {
            p: {
                "path": p,
                "directory": str(root / "02_Contexts" / p) if root else p,
                "milestones": info.get(p, {}).get("milestones"),
            }
            for p in linked
        }
        return goals, note, projects

    async def _family_doing(self, item_id: str | None) -> str | None:
        """The first "doing" line of a thread's situation: where its family stands."""
        repo = getattr(self.server.api, "situation_repo", None)
        if repo is None or not item_id or not item_id.startswith("t") or not item_id[1:].isdigit():
            return None
        try:
            written = await repo.get(int(item_id[1:]))
        except Exception:
            # The family's state is garnish; the goal itself still goes out.
            logger.exception("console: situation of %s unreadable", item_id)
            return None
        doing = getattr(written, "doing", None) if written else None
        return str(doing[0])[:300] if isinstance(doing, list) and doing else None
