"""Run the private half of the Teams relay beside the Discord bot.

The Azure-facing receiver is deliberately not part of this module.  This side
opens no port: it polls the verified-activity queue, resolves a Teams surface,
and passes the message to the same session runner Discord uses.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from claude_code_core.attention import HumanActivity
from claude_code_core.attention_repo import AttentionRecorder
from claude_code_core.frontend import ConversationSurface, SessionFrontend, ThreadKey

from .backend_settings import session_is_resumable
from .cogs._run_helper import run_claude_with_config
from .cogs.run_config import RunConfig

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from claude_teams.activity import InboundActivity
    from claude_teams.frontend import TeamsFrontend
    from claude_teams.relay import ActivityPuller

logger = logging.getLogger(__name__)

__all__ = [
    "FrontendRouter",
    "TeamsRuntime",
    "TeamsSessionHost",
    "build_teams_runtime",
    "parse_frontends",
    "teams_activity",
]

_KNOWN_FRONTENDS = frozenset({"discord", "teams"})


def parse_frontends(value: str) -> tuple[str, ...]:
    """Parse ``CCDB_FRONTENDS``, keeping the historical Discord default."""
    if not value.strip():
        return ("discord",)
    names = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    if not names:
        raise ValueError("CCDB_FRONTENDS must select at least one frontend")
    unknown = set(names) - _KNOWN_FRONTENDS
    if unknown:
        names_text = ", ".join(sorted(unknown))
        raise ValueError(f"CCDB_FRONTENDS contains unknown frontend(s): {names_text}")
    if len(names) != len(set(names)):
        raise ValueError("CCDB_FRONTENDS must not contain duplicate frontend names")
    return names


class FrontendRouter:
    """Resolve across several frontends while creating on the primary one."""

    name = "multi"

    def __init__(self, primary: SessionFrontend) -> None:
        self._primary = primary
        self._frontends: dict[str, SessionFrontend] = {primary.name: primary}

    def add(self, frontend: SessionFrontend) -> None:
        if frontend.name in self._frontends:
            raise ValueError(f"frontend {frontend.name!r} is already registered")
        self._frontends[frontend.name] = frontend

    async def start(self) -> None:
        for frontend in self._frontends.values():
            await frontend.start()

    async def close(self) -> None:
        for frontend in reversed(tuple(self._frontends.values())):
            await frontend.close()

    async def resolve_surface(self, thread_key: ThreadKey) -> ConversationSurface | None:
        for frontend in self._frontends.values():
            surface = await frontend.resolve_surface(thread_key)
            if surface is not None:
                return surface
        return None

    async def create_surface(self, *, parent_id: str, title: str) -> ConversationSurface:
        return await self._primary.create_surface(parent_id=parent_id, title=title)


class TeamsSessionHost:
    """Turn trusted relayed activities into ordinary ccdb session turns."""

    def __init__(
        self,
        *,
        app_id: str,
        frontend: TeamsFrontend,
        ledger: Any,
        session_repo: Any,
        backend_factory: Any,
        backend_settings: Any,
        run_session: Callable[[RunConfig], Awaitable[str | None]] = run_claude_with_config,
        lounge_repo: Any = None,
        ask_repo: Any = None,
        usage_repo: Any = None,
        registry: Any = None,
        worktree_manager: Any = None,
        attention_recorder: AttentionRecorder | None = None,
    ) -> None:
        self._app_id = app_id
        self._attention_recorder = attention_recorder
        self._frontend = frontend
        self._ledger = ledger
        self._session_repo = session_repo
        self._factory = backend_factory
        self._settings = backend_settings
        self._run_session = run_session
        self._lounge_repo = lounge_repo
        self._ask_repo = ask_repo
        self._usage_repo = usage_repo
        self._registry = registry
        self._worktree_manager = worktree_manager

    async def handle(self, activity: InboundActivity) -> None:
        """Route one already-verified activity from the relay queue."""
        self._frontend.remember(activity.conversation_id, activity.service_url)

        if activity.type == "invoke":
            self._handle_invoke(activity)
            return
        if not activity.is_message or activity.is_from(self._app_id):
            return
        if activity.conversation_type != "personal" and not activity.mentions_bot(self._app_id):
            return

        prompt = activity.clean_text.strip()
        if not prompt:
            return

        parent_id = activity.channel_id or activity.team_id
        thread_key = await self._ledger.register(
            "teams",
            activity.conversation_id,
            parent_external_id=parent_id,
        )
        surface = await self._frontend.resolve_surface(thread_key)
        if surface is None:
            raise LookupError(f"Teams conversation {activity.conversation_id!r} cannot be resolved")
        await self._record_attention(activity, parent_id=parent_id, text=prompt)

        record = await self._session_repo.get(thread_key)
        backend = await self._settings.current_backend(thread_key)
        model = await self._settings.current_model(backend, thread_key)
        runner = self._factory.build(backend=backend, model=model, thread_id=thread_key)

        session_id = None
        if record is not None and session_is_resumable(record.backend, backend):
            session_id = record.session_id
            if record.working_dir:
                runner.working_dir = record.working_dir

        effort = await self._settings.current_effort(backend, thread_key)
        if effort is not None and hasattr(runner, "effort"):
            runner.effort = effort

        await self._run_session(
            RunConfig(
                surface=surface,
                runner=runner,
                repo=self._session_repo,
                prompt=prompt,
                session_id=session_id,
                lounge_repo=self._lounge_repo,
                ask_repo=self._ask_repo,
                usage_repo=self._usage_repo,
                registry=self._registry,
                worktree_manager=self._worktree_manager,
                backend_settings=self._settings,
                codex_command=self._factory.codex_command,
                claude_command=runner.command,
                session_origin="teams",
            )
        )

    async def _record_attention(
        self, activity: InboundActivity, *, parent_id: str | None, text: str
    ) -> None:
        recorder = self._attention_recorder
        if recorder is None or not recorder.enabled or not activity.id:
            return
        await recorder.record(teams_activity(activity, parent_id=parent_id, text=text))

    def _handle_invoke(self, activity: InboundActivity) -> None:
        if activity.raw.get("name") != "adaptiveCard/action":
            return
        value = activity.raw.get("value")
        action = value.get("action") if isinstance(value, dict) else None
        data = action.get("data") if isinstance(action, dict) else None
        if not self._frontend.interactions.resolve(activity.conversation_id, data):
            logger.info("A relayed Teams card action did not match a live prompt")


def teams_activity(activity: InboundActivity, *, parent_id: str | None, text: str) -> HumanActivity:
    """Describe a Teams message as human activity — metadata only, never the text."""
    conversation = activity.raw.get("conversation")
    title = conversation.get("name") if isinstance(conversation, dict) else None
    attachments = activity.raw.get("attachments")
    # Teams repeats the message body as a text/html attachment; that is not a file.
    files = (
        [a for a in attachments if isinstance(a, dict) and a.get("contentType") != "text/html"]
        if isinstance(attachments, list)
        else []
    )
    raw_time = activity.raw.get("timestamp")
    try:
        occurred_at = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if occurred_at.tzinfo is None:
            raise ValueError("naive timestamp")
    except ValueError:
        occurred_at = datetime.now(UTC)
    return HumanActivity(
        frontend="teams",
        conversation_id=activity.conversation_id,
        parent_id=parent_id,
        thread_title=title if isinstance(title, str) and title else None,
        author_id=activity.from_id,
        occurred_at=occurred_at,
        char_count=len(text),
        attachment_count=len(files),
        message_id=activity.id,
    )


class TeamsRuntime:
    """Resources whose lifetime matches the normal bot process."""

    def __init__(self, *, frontend: TeamsFrontend, puller: ActivityPuller, session: ClientSession):
        self.frontend = frontend
        self._puller = puller
        self._session = session
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        await self._puller.start()
        self._running = True

    async def close(self) -> None:
        await self._puller.close()
        await self._session.close()
        self._running = False


async def build_teams_runtime(
    env: Mapping[str, str],
    *,
    components: Any,
    backend_factory: Any,
    registry: Any = None,
    worktree_manager: Any = None,
) -> TeamsRuntime:
    """Assemble the private-side Teams transport from existing components."""
    from aiohttp import ClientSession

    from claude_teams.config import TeamsConfig
    from claude_teams.connector import BotConnector
    from claude_teams.frontend import TeamsFrontend
    from claude_teams.http import bytes_putter, form_poster, json_poster, json_putter
    from claude_teams.relay import ActivityPuller
    from claude_teams.relay.storage_queue import StorageQueue, aiohttp_request
    from claude_teams.token import OutboundTokenProvider

    config = TeamsConfig.from_env(env)
    if config.app_password is None:
        raise ValueError("CCDB_TEAMS_APP_PASSWORD is required on the private session host")
    queue_url = env.get("CCDB_TEAMS_QUEUE_URL", "").strip()
    if not queue_url:
        raise ValueError("CCDB_TEAMS_QUEUE_URL is required for the Teams frontend")

    session = ClientSession()
    try:
        connector = BotConnector(
            OutboundTokenProvider(
                config.tenant_id,
                config.app_id,
                config.app_password,
                form_poster(session),
            ),
            json_poster(session),
            json_putter(session),
        )
        frontend = TeamsFrontend(
            connector,
            components.frontend_threads,
            default_service_url=config.service_url or None,
        )
        host = TeamsSessionHost(
            app_id=config.app_id,
            frontend=frontend,
            ledger=components.frontend_threads,
            session_repo=components.session_repo,
            backend_factory=backend_factory,
            backend_settings=components.backend_settings,
            lounge_repo=components.lounge_repo,
            ask_repo=components.ask_repo,
            usage_repo=components.usage_repo,
            registry=registry,
            worktree_manager=worktree_manager,
            attention_recorder=getattr(components, "attention_recorder", None),
        )
        queue = StorageQueue(queue_url, aiohttp_request(session))
        puller = ActivityPuller(queue, host.handle, on_service_url=frontend.remember)
        # Keep the upload transport constructed beside the connector. File-consent
        # dispatch is added here once its relay-specific acknowledgement contract
        # can be represented without pretending the private host owns the HTTP reply.
        _ = bytes_putter(session)
        return TeamsRuntime(frontend=frontend, puller=puller, session=session)
    except Exception:
        with contextlib.suppress(Exception):
            await session.close()
        raise
