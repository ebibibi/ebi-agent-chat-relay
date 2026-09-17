"""Every repository a deployment needs, built without touching a frontend.

``setup_bridge`` used to open the database and construct ten repositories in
the middle of wiring Discord cogs. The two jobs are unrelated: nothing here
knows what a thread looks like, and a Teams deployment needs exactly the same
stores with none of the Discord wiring around them.

Separating them also makes the deployment-isolation story checkable in one
place. Every repository below shares a single SQLite file, so that path is
where two deployments would silently start sharing sessions, claims and
lounge history — the thing ``DataLayout`` exists to prevent.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .database.ask_repo import PendingAskRepository
from .database.claims_repo import ClaimRepository
from .database.frontend_thread_repo import FrontendThreadRepository
from .database.ingest_repo import IngestResultRepository
from .database.lineage_repo import ThreadLineageRepository
from .database.lounge_repo import LoungeRepository
from .database.models import init_db
from .database.repository import SessionRepository, UsageStatsRepository
from .database.resume_repo import PendingResumeRepository
from .database.settings_repo import SettingsRepository
from .database.summary_repo import ThreadSummaryRepository

logger = logging.getLogger(__name__)

__all__ = ["SessionStores", "build_session_stores"]


@dataclass(frozen=True)
class SessionStores:
    """The repositories that back a running deployment, frontend-agnostic."""

    db_path: str
    sessions: SessionRepository
    settings: SettingsRepository
    asks: PendingAskRepository
    lounge: LoungeRepository
    claims: ClaimRepository
    resumes: PendingResumeRepository
    usage: UsageStatsRepository
    ingest: IngestResultRepository
    summaries: ThreadSummaryRepository
    frontend_threads: FrontendThreadRepository
    lineage: ThreadLineageRepository


async def build_session_stores(session_db_path: str) -> SessionStores:
    """Open (creating if needed) the session database and build every repository.

    Args:
        session_db_path: The one file all of these share.

    Returns:
        The stores, ready to use. Repositories that need their own schema have
        already initialised it, so a caller never has to remember which ones do.
    """
    os.makedirs(os.path.dirname(session_db_path) or ".", exist_ok=True)
    await init_db(session_db_path)

    ingest = IngestResultRepository(session_db_path)
    await ingest.init_db()
    summaries = ThreadSummaryRepository(session_db_path)
    await summaries.init_db()

    # Adopt every thread this deployment already has, so the ledger answers for
    # all of its conversations rather than only the ones opened from now on.
    # Idempotent: a no-op on every run after the first.
    frontend_threads = FrontendThreadRepository(session_db_path)
    await frontend_threads.backfill_discord()

    logger.info("Session DB initialized: %s", session_db_path)
    return SessionStores(
        db_path=session_db_path,
        sessions=SessionRepository(session_db_path),
        settings=SettingsRepository(session_db_path),
        asks=PendingAskRepository(session_db_path),
        lounge=LoungeRepository(session_db_path),
        claims=ClaimRepository(session_db_path),
        resumes=PendingResumeRepository(session_db_path),
        usage=UsageStatsRepository(session_db_path),
        ingest=ingest,
        summaries=summaries,
        frontend_threads=frontend_threads,
        lineage=ThreadLineageRepository(session_db_path),
    )
