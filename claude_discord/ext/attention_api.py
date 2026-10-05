"""``GET /api/attention`` — the operator's estimated attention, by day or thread.

Query parameters (all optional):

- ``from`` / ``to``: local days ``YYYY-MM-DD``, inclusive. Default: the last
  7 days ending today. At most :data:`MAX_RANGE_DAYS` days per request.
- ``group_by``: ``day`` (default) or ``thread``.
- ``author``: restrict to one author id (Discord user id / Teams ``from.id``).

The response always carries ``estimate: true`` and the parameters used.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from aiohttp import web

from claude_code_core.attention import GROUP_BY_CHOICES, GROUP_BY_DAY, AttentionParams
from claude_code_core.attention_repo import HumanActivityRepository, load_report

__all__ = ["MAX_RANGE_DAYS", "handle_attention", "parse_range"]

DEFAULT_RANGE_DAYS = 7
MAX_RANGE_DAYS = 366
_MAX_AUTHOR_LENGTH = 128


class RangeError(ValueError):
    """A query parameter that cannot describe a day range."""


def parse_range(raw_from: str | None, raw_to: str | None, today: date) -> tuple[date, date]:
    """Resolve ``from``/``to`` into an inclusive day range, applying defaults and limits."""
    end = _parse_day(raw_to, "to") if raw_to else today
    start = _parse_day(raw_from, "from") if raw_from else end - timedelta(DEFAULT_RANGE_DAYS - 1)
    if end < start:
        raise RangeError("'to' must not be before 'from'")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise RangeError(f"the range may cover at most {MAX_RANGE_DAYS} days")
    return start, end


def _parse_day(raw: str, name: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RangeError(f"'{name}' must be a date in YYYY-MM-DD form") from exc


async def handle_attention(
    request: web.Request,
    repo: HumanActivityRepository | None,
    params: AttentionParams | None,
) -> web.Response:
    if repo is None:
        return web.json_response(
            {"error": "Attention metering not configured (attention_repo is None)"}, status=503
        )
    params = params or AttentionParams()
    query = request.rel_url.query

    group_by = query.get("group_by", GROUP_BY_DAY)
    if group_by not in GROUP_BY_CHOICES:
        return web.json_response(
            {"error": f"group_by must be one of: {', '.join(GROUP_BY_CHOICES)}"}, status=400
        )
    author = query.get("author") or None
    if author is not None and len(author) > _MAX_AUTHOR_LENGTH:
        return web.json_response({"error": "author is too long"}, status=400)

    today = datetime.now(params.tz).date() if params.tz else datetime.now().astimezone().date()
    try:
        start, end = parse_range(query.get("from"), query.get("to"), today)
    except RangeError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    report = await load_report(
        repo, params, start=start, end=end, group_by=group_by, author_id=author
    )
    return web.json_response(report)
