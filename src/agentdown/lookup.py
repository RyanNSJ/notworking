"""Resolve what an agent asked about, and build the agent-facing responses (D58-D61, D66).

Shared by the HTTP API and (in M3) the MCP tools. Every string in these responses comes
from the catalogue, options.yaml or the fixed text below: never from reports (D59).
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

from agentdown.catalog import AccessPath, Catalog, Service
from agentdown.core.options import Options
from agentdown.core.targets import TargetError, parse_target
from agentdown.store import PathCounts

NOTICE = (
    "NotWorking lists known access paths and recent reports from agents. "
    "It does not test, vet or endorse them."
)
PLEASE_REPORT = (
    "If one of these access paths failed for you, please report it so other agents know. "
    "Use the id of the path you used. Reports are anonymous."
)
NOT_LISTED = (
    "This target isn't in the NotWorking catalogue, so there's no description or list of other "
    "access paths for it. The counts are reports from agents about this exact target. If it "
    "failed for you, please report it so other agents know."
)


@dataclass
class Resolution:
    kind: Literal["service", "ambiguous", "not_listed", "no_match"]
    asked: tuple[str, str] | None = None  # (type, canonical id) of what the agent named
    service: Service | None = None
    candidates: list[Service] = field(default_factory=list)
    reason: str = ""


def resolve(
    catalog: Catalog, raw: str, type_hint: str | None = None, *, by_name: bool = True
) -> Resolution:
    """Exact listed id, then normalisation (D47), then service names and aliases (D61).

    Raises TargetError only when `by_name` is False and the input can't be parsed.
    """
    text = raw.strip()
    exact = text if catalog.is_listed(text) else text.lower()
    if catalog.is_listed(exact) and type_hint in (None, catalog.path_types[exact]):
        return _for_path(catalog, (catalog.path_types[exact], exact))
    if exact in catalog.by_id and type_hint is None:
        return Resolution("service", service=catalog.by_id[exact])
    try:
        parsed = parse_target(text, "site" if type_hint == "route" else type_hint)
    except TargetError as e:
        if not by_name:
            raise
        matches = catalog.match_names(text)
        if len(matches) == 1:
            return Resolution("service", service=matches[0])
        if matches:
            return Resolution("ambiguous", candidates=matches[:5])
        return Resolution("no_match", reason=str(e))
    asked = catalog.canonical(parsed)
    if catalog.is_listed(asked[1]):
        return _for_path(catalog, asked)
    if asked[0] == "site" and asked[1] in catalog.by_id:
        return Resolution("service", service=catalog.by_id[asked[1]])
    return Resolution("not_listed", asked=asked)


def _for_path(catalog: Catalog, asked: tuple[str, str]) -> Resolution:
    services = catalog.services_for_path[asked[1]]
    if len(services) == 1:
        return Resolution("service", asked=asked, service=services[0])
    return Resolution("ambiguous", asked=asked, candidates=services[:5])


# ---- response builders -----------------------------------------------------


def _iso(value: dt.datetime | dt.date) -> str:
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return value.isoformat()


def _path_entry(path: AccessPath, counts: PathCounts) -> dict[str, object]:
    entry: dict[str, object] = {"type": path.type, "id": path.id}
    if path.url:
        entry["url"] = path.url
    entry["description"] = path.description
    if path.no_longer_working_since:
        entry["no_longer_working_since"] = _iso(path.no_longer_working_since)
    entry["status"] = counts.status
    entry["failure_reports"] = counts.failure_reports
    entry["unique_reporters"] = counts.unique_reporters
    if counts.failure_reports:
        entry["breakdown"] = counts.breakdown
        if counts.last_report_at:
            entry["last_report_at"] = _iso(counts.last_report_at)
    return entry


def _common(options: Options, public_url: str, now: dt.datetime, example_target: str):
    return {
        "please_report": {
            "message": PLEASE_REPORT,
            "method": "POST",
            "url": f"{public_url}/v1/report",
            "example": {"target": example_target, "what_failed": ["captcha"]},
        },
        "what_failed_options": options.what_failed_payload(),
        "agent_type_options": list(options.agent_type),
        "notice": NOTICE,
        "as_of": _iso(now),
        "about": f"{public_url}/methodology",
    }


def service_view(
    res: Resolution,
    counts: dict[str, PathCounts],
    options: Options,
    public_url: str,
    now: dt.datetime,
) -> dict[str, object]:
    service = res.service
    assert service is not None
    # Looked up by service name or id: there's no single path to point at.
    asked = res.asked or ("service", service.id)
    example = res.asked[1] if res.asked else service.paths[0].id
    return {
        "service": {"id": service.id, "name": service.name},
        "you_asked_about": {"type": asked[0], "id": asked[1]},
        "listed": True,
        "window": "1h",
        "access_paths": [_path_entry(p, counts.get(p.id, PathCounts())) for p in service.paths],
        **_common(options, public_url, now, example),
    }


def not_listed_view(
    res: Resolution, counts: PathCounts, options: Options, public_url: str, now: dt.datetime
) -> dict[str, object]:
    """An unlisted target (D71): its own counts and status, but no service or other paths.

    The only id in it is the one the asking agent sent, so no reporter text reaches it."""
    assert res.asked is not None
    entry: dict[str, object] = {
        "status": counts.status,
        "failure_reports": counts.failure_reports,
        "unique_reporters": counts.unique_reporters,
    }
    if counts.failure_reports:
        entry["breakdown"] = counts.breakdown
        if counts.last_report_at:
            entry["last_report_at"] = _iso(counts.last_report_at)
    return {
        "you_asked_about": {"type": res.asked[0], "id": res.asked[1]},
        "listed": False,
        "window": "1h",
        **entry,
        "message": NOT_LISTED,
        **_common(options, public_url, now, res.asked[1]),
    }


def candidates_view(res: Resolution, public_url: str, now: dt.datetime) -> dict[str, object]:
    body: dict[str, object] = {
        "match": "ambiguous",
        "message": "Several services match. Look up one of these service ids for its access paths.",
        "candidates": [{"id": s.id, "name": s.name} for s in res.candidates],
        "as_of": _iso(now),
        "about": f"{public_url}/methodology",
    }
    if res.asked:
        body["you_asked_about"] = {"type": res.asked[0], "id": res.asked[1]}
    return body
