"""Filesystem side of the Teams thread sync — one message, one file.

The vault is the single source of truth. There is no database row and no
client-side marker recording what has been synced: the answer to "what do you
already have?" is computed by looking at the files, every time. That is what
makes the protocol self-healing — delete a message file by hand and the next
sync fetches it again; a half-finished sync simply leaves less on disk and the
next one completes it.

Layout under the sync root (default ``{working_dir}/teams``, beside ``ingest``)::

    orgs.json            team GUID → company name (hand-editable, authoritative)
    {company}/           present once a thread's company is known
    {company}/{title-slug}--{root_mid}/
        thread.json          identity, coverage, pending attachments
        chain.jsonl          append-only order + revision journal
        README.md            how an agent should read this folder
        messages/{mid}.md    one message
        messages/{mid}/...   that message's attachments
        _history/{mid}.{hash8}.md   superseded versions of an edited message
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from . import teams_sync
from .teams_sync import IncomingMessage, StoredMessage, ThreadRef

logger = logging.getLogger(__name__)

DEFAULT_SYNC_SUBDIR = "teams"
ORGS_FILE = "orgs.json"
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def default_vault_root(working_dir: str | Path | None = None) -> Path:
    """Where synced threads live.

    ``CCDB_TEAMS_VAULT_ROOT`` overrides it; otherwise ``{working_dir}/teams``,
    the sibling of the ``ingest/`` directory attachments already land in. The
    default has to be somewhere every deployment already has — pointing it at a
    note-taking vault would bake one operator's filing system into the
    framework, and create a directory on machines that have no such vault.
    Somewhere personal is exactly what the environment variable is for.
    """
    override = os.getenv("CCDB_TEAMS_VAULT_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(working_dir or os.getcwd()) / DEFAULT_SYNC_SUBDIR


class TeamsVaultStore:
    """Reads and writes one thread folder per Teams thread."""

    def __init__(
        self, root: Path | str | None = None, working_dir: str | Path | None = None
    ) -> None:
        self.root = Path(root).expanduser() if root else default_vault_root(working_dir)

    # -- paths ----------------------------------------------------------

    def _contained(self, base: Path, path: Path) -> Path | None:
        """Return ``path`` only if it really resolves inside ``base``.

        mids and attachment names come from the network. They are pattern-checked
        upstream, but the guarantee is re-established here, next to the write
        that depends on it: a sanitiser three call frames away protects nothing
        that a later refactor cannot quietly remove.
        """
        try:
            root = os.path.realpath(str(base))
            resolved = os.path.realpath(str(path))
        except OSError:
            return None
        if resolved != root and not resolved.startswith(root + os.sep):
            return None
        return Path(resolved)

    # -- company (org) folders -------------------------------------------

    def read_orgs(self) -> dict[str, str]:
        """The team GUID → company map, lowercased keys. Missing file = empty."""
        data = self._read_json(self.root / ORGS_FILE) or {}
        raw = data.get("teams")
        if not isinstance(raw, dict):
            return {}
        return {
            str(k).strip().lower(): str(v).strip()
            for k, v in raw.items()
            if str(k).strip() and str(v).strip()
        }

    def org_for(self, ref: ThreadRef) -> str:
        """The company this thread files under, learning the label once.

        ``orgs.json`` wins over the label the client sent: the file is the
        operator's filing decision, edited by hand, and a client that could
        override it would undo every correction on the next sync. A team the file
        does not know yet is recorded from the client's label, so labelling one
        conversation files every later thread of that company by itself.
        """
        mapped = self.read_orgs().get(ref.team, "")
        if mapped:
            return mapped
        if ref.org:
            self._remember_org(ref.team, ref.org)
        return ref.org

    def _remember_org(self, team: str, org: str) -> None:
        path = self.root / ORGS_FILE
        data = self._read_json(path) or {}
        teams = data.get("teams")
        if not isinstance(teams, dict):
            teams = {}
        teams[team] = org
        data["teams"] = teams
        data.setdefault(
            "_comment",
            "team GUID → 会社名。同期フォルダの第1階層になる。手で直した内容が優先される。",
        )
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            # Filing is a convenience; failing to record it must not lose the sync.
            logger.warning("Could not record the company for team %s: %s", team, exc)

    # -- paths -----------------------------------------------------------

    def _thread_dir_candidates(self) -> list[Path]:
        """Every directory that could be a thread folder: root, then one level in.

        Two levels is the whole tree — companies do not nest. Descending further
        would walk ``messages/`` and ``_history/`` of every thread on every sync.
        """
        if not self.root.is_dir():
            return []
        out: list[Path] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            out.append(entry)
            if (entry / "thread.json").exists():
                continue  # a thread folder, not a company folder
            out.extend(
                child
                for child in sorted(entry.iterdir())
                if child.is_dir() and not child.name.startswith(".")
            )
        return out

    def find_thread_dir(self, ref: ThreadRef) -> Path | None:
        """Locate an existing folder for this thread by reading thread.json.

        Identity lives in the file, not in the folder name or its place in the
        tree, so a folder the user renamed in Obsidian — or dragged into a
        company folder — is still found. That is what makes filing the vault by
        hand a safe migration: a thread that is not found is not "missing", it is
        re-created empty and the entire history uploads again, silently, because
        every message looks new. There is deliberately no index file: an index
        would be a second ledger that can drift from the directory it describes.

        A conversation-scoped ref matches on the team alone (see
        ``ThreadRef.identity_scope``). That is only safe because such a team is
        derived from one Teams conversation id and therefore never holds a second
        thread — matching a real channel GUID this way would pull every thread of
        that team into one folder. The scope is the client's claim, so the check
        stays on the ref rather than on the shape of the GUID.
        """
        for entry in self._thread_dir_candidates():
            meta = self._read_json(entry / "thread.json")
            if not meta:
                continue
            if str(meta.get("team", "")).lower() != ref.team:
                continue
            if ref.conversation_scoped or str(meta.get("root_mid", "")) == ref.root_mid:
                return entry
        return None

    def ensure_thread_dir(self, ref: ThreadRef) -> Path:
        """Find the thread's folder, creating it under its company on first sync.

        An existing folder is never moved. Re-filing is the user's call — Teams
        renames a group chat the moment someone joins it, and a folder that walks
        around the vault on its own would break every wikilink pointing into it.
        """
        existing = self.find_thread_dir(ref)
        if existing is not None:
            return existing
        parent = self.root
        org_dir = teams_sync.org_dirname(self.org_for(ref))
        if org_dir:
            contained = self._contained(self.root, self.root / org_dir)
            if contained is None:
                raise ValueError("company directory escapes the vault root")
            parent = contained
        base = teams_sync.thread_dirname(ref.title, ref.root_mid)
        candidate = parent / base
        # The name embeds the root mid, so a clash means an unrelated leftover
        # directory. Take the next free suffix rather than writing into it.
        for n in range(2, 100):
            if self._contained(self.root, candidate) is None:
                raise ValueError("thread directory escapes the vault root")
            if not candidate.exists():
                break
            candidate = parent / f"{base}_{n}"
        candidate.mkdir(parents=True, exist_ok=True)
        (candidate / "messages").mkdir(exist_ok=True)
        return candidate

    # -- reading --------------------------------------------------------

    def load_stored(self, thread_dir: Path) -> dict[str, StoredMessage]:
        """What this folder currently holds, keyed by mid.

        Built from ``chain.jsonl`` (last revision per mid) but confirmed against
        the actual files: a chain entry whose message file is gone is not
        reported as stored, so the message comes back on the next sync.
        """
        stored: dict[str, StoredMessage] = {}
        for entry in self.read_chain(thread_dir):
            mid = str(entry.get("mid") or "")
            if not mid:
                continue
            stored[mid] = StoredMessage(mid=mid, hash=str(entry.get("hash") or ""))
        confirmed: dict[str, StoredMessage] = {}
        for mid, rec in stored.items():
            note = self._message_path(thread_dir, mid)
            if note is None or not note.exists():
                continue
            confirmed[mid] = StoredMessage(
                mid=mid,
                hash=rec.hash,
                attachments_present=self._attachments_present(thread_dir, mid),
            )
        return confirmed

    def read_chain(self, thread_dir: Path) -> list[dict]:
        """Every chain line, oldest first. Malformed lines are skipped, not fatal."""
        path = thread_dir / "chain.jsonl"
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed chain line in %s", path.name)
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def latest_chain(self, thread_dir: Path) -> dict[str, dict]:
        """Last revision per mid (a later line for the same mid supersedes)."""
        latest: dict[str, dict] = {}
        for entry in self.read_chain(thread_dir):
            mid = str(entry.get("mid") or "")
            if mid:
                latest[mid] = entry
        return latest

    def read_meta(self, thread_dir: Path) -> dict:
        return self._read_json(thread_dir / "thread.json") or {}

    def _read_json(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _message_path(self, thread_dir: Path, mid: str) -> Path | None:
        return self._contained(thread_dir, thread_dir / "messages" / f"{mid}.md")

    def _attachment_dir(self, thread_dir: Path, mid: str) -> Path | None:
        return self._contained(thread_dir, thread_dir / "messages" / mid)

    def _attachments_present(self, thread_dir: Path, mid: str) -> frozenset[str]:
        folder = self._attachment_dir(thread_dir, mid)
        if folder is None or not folder.is_dir():
            return frozenset()
        return frozenset(p.name for p in folder.iterdir() if p.is_file())

    # -- writing --------------------------------------------------------

    def save_message(
        self,
        thread_dir: Path,
        msg: IncomingMessage,
        ref: ThreadRef,
        *,
        prev_mid: str | None,
        now: str | None = None,
    ) -> dict:
        """Write one message, rotating any previous version into ``_history``.

        Returns a report ``{mid, action, pending, attachments_saved}``. Nothing
        is ever deleted: an edit moves the old body aside, a Teams-side deletion
        only flips a flag.
        """
        now = now or datetime.now().astimezone().isoformat(timespec="seconds")
        note = self._message_path(thread_dir, msg.mid)
        if note is None:
            raise ValueError(f"message path escapes the thread directory: {msg.mid}")
        note.parent.mkdir(parents=True, exist_ok=True)

        previous = self.latest_chain(thread_dir).get(msg.mid)
        edited = previous is not None and teams_sync.is_edit(
            str(previous.get("hash") or ""), msg.hash
        )
        rev = int(previous.get("rev", 1)) + 1 if previous else 1
        first_synced = now
        if note.exists():
            if edited and previous is not None:
                self._archive_version(thread_dir, msg.mid, str(previous.get("hash") or ""), note)
            first_synced = self._existing_first_synced(note) or now

        saved, pending, unavailable = self._save_attachments(thread_dir, msg)
        declared = {teams_sync.safe_attachment_name(a.name) for a in msg.attachments}
        kept = tuple(sorted(self._attachments_present(thread_dir, msg.mid) - declared))
        note.write_text(
            teams_sync.render_message(
                msg,
                ref,
                prev_mid=prev_mid,
                edited=edited or bool(previous and previous.get("edited")),
                first_synced_at=first_synced,
                last_synced_at=now,
                pending=pending,
                unavailable=unavailable,
                kept_attachments=kept,
            ),
            encoding="utf-8",
        )
        self._append_chain(
            thread_dir,
            teams_sync.chain_entry(msg, prev_mid=prev_mid, rev=rev, synced_at=now),
        )
        return {
            "mid": msg.mid,
            "action": "updated" if previous else "created",
            "edited": edited,
            "attachments_saved": saved,
            "pending": pending,
            "unavailable": unavailable,
        }

    def _existing_first_synced(self, note: Path) -> str | None:
        """Preserve the original sync time across a rewrite."""
        try:
            for line in note.read_text(encoding="utf-8").splitlines()[:30]:
                if line.startswith("first_synced_at:"):
                    return line.split(":", 1)[1].strip().strip('"')
        except OSError:
            return None
        return None

    def _archive_version(self, thread_dir: Path, mid: str, old_hash: str, note: Path) -> None:
        history = self._contained(thread_dir, thread_dir / "_history")
        if history is None:
            return
        history.mkdir(exist_ok=True)
        stamp = (old_hash.split(":")[-1] or "prev")[:8]
        target = self._contained(thread_dir, history / f"{mid}.{stamp}.md")
        if target is None:
            return
        for n in range(2, 100):
            if not target.exists():
                break
            target = history / f"{mid}.{stamp}_{n}.md"
        try:
            target.write_text(note.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not archive previous version of %s: %s", mid, exc)

    def _save_attachments(
        self, thread_dir: Path, msg: IncomingMessage
    ) -> tuple[int, list[dict], list[dict]]:
        """Write bytes; separate terminal source gaps from retryable failures.

        An attachment that cannot be stored is never dropped silently — it is
        returned as pending so it surfaces in thread.json, in the README and in
        the next plan's want list.
        """
        import base64
        import binascii

        saved = 0
        pending: list[dict] = []
        unavailable: list[dict] = []
        for i, att in enumerate(msg.attachments):
            name = teams_sync.safe_attachment_name(att.name, i)
            if att.status == "unavailable":
                unavailable.append(
                    {
                        "mid": msg.mid,
                        "name": name,
                        "reason": att.reason or "取得元で利用できません",
                        "url": att.url,
                    }
                )
                continue
            if att.status != "embedded" or not att.data_b64:
                pending.append(
                    {
                        "mid": msg.mid,
                        "name": name,
                        "reason": att.reason or f"status={att.status}",
                        "url": att.url,
                    }
                )
                continue
            try:
                blob = base64.b64decode(att.data_b64, validate=True)
            except (binascii.Error, ValueError):
                pending.append({"mid": msg.mid, "name": name, "reason": "base64が不正"})
                continue
            if len(blob) > _MAX_ATTACHMENT_BYTES:
                pending.append(
                    {
                        "mid": msg.mid,
                        "name": name,
                        "reason": f"サイズ上限超過 ({len(blob)} bytes)",
                        "url": att.url,
                    }
                )
                continue
            folder = self._attachment_dir(thread_dir, msg.mid)
            if folder is None:
                pending.append({"mid": msg.mid, "name": name, "reason": "保存先が不正"})
                continue
            folder.mkdir(parents=True, exist_ok=True)
            target = self._contained(thread_dir, folder / name)
            if target is None:
                pending.append({"mid": msg.mid, "name": name, "reason": "保存先が不正"})
                continue
            try:
                target.write_bytes(blob)
            except OSError as exc:
                pending.append({"mid": msg.mid, "name": name, "reason": f"書き込み失敗: {exc}"})
                continue
            saved += 1
        return saved, pending, unavailable

    def _append_chain(self, thread_dir: Path, entry: dict) -> None:
        path = thread_dir / "chain.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _root_mid_for(ref: ThreadRef, previous: dict) -> str:
        """The root a conversation-scoped chat keeps as chunks arrive out of order.

        A client working backwards through a long chat reports a different
        "oldest mid I have seen" on every flush, and a later chunk covering only
        recent messages reports a *newer* one. Taking the last value would make
        the root walk forward and backward with the scroll position; keeping the
        oldest ever seen means it converges on the real beginning of the chat and
        stays there. The folder name is left alone either way — renaming it would
        break every wikilink pointing into the vault.
        """
        if not ref.conversation_scoped:
            return ref.root_mid
        stored = str(previous.get("root_mid") or "").strip()
        if not stored.isdigit():
            return ref.root_mid
        return stored if int(stored) <= int(ref.root_mid) else ref.root_mid

    def write_meta(
        self,
        thread_dir: Path,
        ref: ThreadRef,
        *,
        pending: list[dict],
        unavailable: list[dict] | None = None,
        coverage: dict,
        now: str | None = None,
    ) -> dict:
        """Refresh thread.json and README.md from what is actually on disk."""
        now = now or datetime.now().astimezone().isoformat(timespec="seconds")
        chain = self.read_chain(thread_dir)
        latest = self.latest_chain(thread_dir)
        previous = self.read_meta(thread_dir)
        unavailable = unavailable or []
        saved_total = sum(len(self._attachments_present(thread_dir, mid)) for mid in latest)
        scan = coverage.get("attachment_scan") or {}
        previous_summary = previous.get("attachment_summary") or {}
        ignored = int(scan.get("ignored", previous_summary.get("ignored", 0)) or 0)
        detected = int(
            scan.get(
                "detected",
                previous_summary.get(
                    "detected", saved_total + len(unavailable) + len(pending) + ignored
                ),
            )
            or 0
        )
        attachment_summary = {
            "detected": detected,
            "saved": saved_total,
            "unavailable": len(unavailable),
            "ignored": ignored,
            "pending": len(pending),
        }
        authors = [str(e.get("author") or "") for e in latest.values()]
        meta = {
            "team": ref.team,
            "org": self.org_for(ref) or previous.get("org", ""),
            "root_mid": self._root_mid_for(ref, previous),
            "title": ref.title,
            "url": ref.url or previous.get("url", ""),
            "participants": sorted({a for a in authors if a}),
            "message_count": len(latest),
            "coverage": {**(previous.get("coverage") or {}), **coverage},
            "pending_attachments": pending,
            "unavailable_attachments": unavailable,
            "attachment_summary": attachment_summary,
            "first_synced_at": previous.get("first_synced_at") or now,
            "last_synced_at": now,
        }
        (thread_dir / "thread.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (thread_dir / "README.md").write_text(
            teams_sync.render_readme(meta, chain), encoding="utf-8"
        )
        return meta

    def merge_pending(
        self,
        thread_dir: Path,
        fresh: list[dict],
        *,
        pushed: list[IncomingMessage] | None = None,
        unavailable: list[dict] | None = None,
    ) -> list[dict]:
        """Carry forward unresolved gaps, drop resolved or obsolete ones.

        A file that failed to arrive stays listed until its bytes actually
        appear. Recomputing the list from scratch each sync would clear entries
        for messages that were not part of this run. For messages that *were*
        pushed, however, the incoming attachment inventory is authoritative:
        pending names it no longer declares came from an older client and must
        not survive forever.
        """
        declared_by_mid = {
            msg.mid: {teams_sync.safe_attachment_name(att.name) for att in msg.attachments}
            for msg in pushed or []
        }
        merged: dict[tuple[str, str], dict] = {}
        unavailable_keys = {
            (str(item.get("mid") or ""), str(item.get("name") or "")) for item in unavailable or []
        }
        for item in (self.read_meta(thread_dir).get("pending_attachments") or []) + fresh:
            mid = str(item.get("mid") or "")
            name = str(item.get("name") or "")
            if not mid or not name:
                continue
            if (mid, name) in unavailable_keys:
                continue
            if mid in declared_by_mid and name not in declared_by_mid[mid]:
                continue
            if name in self._attachments_present(thread_dir, mid):
                continue
            candidate = {**item, "mid": mid, "name": name}
            key = (mid, name)
            current = merged.get(key)
            candidate_priority = (
                bool(candidate.get("url")),
                bool(candidate.get("reason")),
            )
            current_priority = (
                bool(current and current.get("url")),
                bool(current and current.get("reason")),
            )
            if current is None or candidate_priority >= current_priority:
                merged[key] = candidate
        return sorted(merged.values(), key=lambda i: (i["mid"], i["name"]))

    def merge_unavailable(
        self,
        thread_dir: Path,
        fresh: list[dict],
        *,
        pushed: list[IncomingMessage] | None = None,
    ) -> list[dict]:
        """Carry forward terminal source gaps until bytes arrive or inventory changes."""
        declared_by_mid = {
            msg.mid: {teams_sync.safe_attachment_name(att.name) for att in msg.attachments}
            for msg in pushed or []
        }
        fresh_keys = {(str(item.get("mid") or ""), str(item.get("name") or "")) for item in fresh}
        merged: dict[tuple[str, str], dict] = {}
        for item in (self.read_meta(thread_dir).get("unavailable_attachments") or []) + fresh:
            mid = str(item.get("mid") or "")
            name = str(item.get("name") or "")
            if not mid or not name:
                continue
            if mid in declared_by_mid and name not in declared_by_mid[mid]:
                continue
            if name in self._attachments_present(thread_dir, mid):
                continue
            if mid in declared_by_mid and (mid, name) not in fresh_keys:
                continue
            merged[(mid, name)] = {**item, "mid": mid, "name": name}
        return sorted(merged.values(), key=lambda i: (i["mid"], i["name"]))
