"""The two operations, independent of transport: used by the HTTP API and the MCP tools.

`check_status` and `submit_report` return a `Result` (HTTP-style status code, JSON body,
extra headers). The HTTP layer sends it as-is; the MCP layer returns the body.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from agentdown import store
from agentdown.catalog import Catalog
from agentdown.core.clock import Clock
from agentdown.core.countries import COUNTRY_CODES
from agentdown.core.options import Options, load_options
from agentdown.core.privacy import MAX_NOTE, fingerprint, network_prefix, scrub_note, ua_family
from agentdown.core.targets import TargetError
from agentdown.detector import LEVELS
from agentdown.lookup import (
    Resolution,
    candidates_view,
    not_listed_view,
    resolve,
    service_view,
)
from agentdown.settings import Settings

STATUS_CACHE = "public, max-age=60"


class AppState(Protocol):
    engine: Engine
    catalog: Catalog
    clock: Clock
    settings: Settings


@dataclass
class Result:
    code: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    type: str | None = None
    outcome: str | None = None  # only "failed"; kept so older clients that send it still work
    what_failed: list[str] | None = None
    country: str | None = None
    agent_type: str | None = None
    note: str | None = None
    signature: str | None = None  # accepted, not yet verified or weighted (D25)


def problems_result(options: Options, problems: list[dict[str, str]]) -> Result:
    """422 that tells the agent exactly what's valid, so it can fix the request in one retry."""
    return Result(
        422,
        {
            "error": "invalid_request",
            "problems": problems,
            "valid_values": {
                "what_failed": options.what_failed_payload(),
                "agent_type": list(options.agent_type),
                "type": list(options.target_type),
            },
            "hint": "target is the URL or the access-path id you used, as listed by the "
            "status lookup. what_failed says how it failed: one or more of the values.",
        },
    )


def _view(state: AppState, res: Resolution, now, *, count: bool) -> Result:
    """Build the response for a resolution. `count` records lookup counters (D64)."""
    options = load_options()
    url = state.settings.public_url
    with state.engine.begin() as conn:
        if count:
            store.bump_checks(conn, now)

        def bump(event: str, subject: str) -> None:
            if count:
                cap = store.MISS_SUBJECTS_PER_DAY if event == "lookup_miss" else None
                store.bump_usage(conn, now, event, subject, cap)

        if res.kind == "service":
            assert res.service is not None
            counts = store.path_counts(conn, [p.id for p in res.service.paths], now)
            bump("lookup_service", res.service.id)
            if res.asked:
                bump("lookup_path", res.asked[1])
            return Result(200, service_view(res, counts, options, url, now))
        if res.kind == "ambiguous":
            for s in res.candidates:
                bump("lookup_service", s.id)
            return Result(200, candidates_view(res, url, now))
        if res.kind == "not_listed":
            assert res.asked is not None
            bump("lookup_miss", res.asked[1])
            counts = store.path_counts(conn, [res.asked[1]], now)[res.asked[1]]
            return Result(200, not_listed_view(res, counts, options, url, now))
        bump("lookup_miss", "unparseable")
    return Result(
        404,
        {
            "error": "not_found",
            "message": "Nothing in the catalogue matches that. Try the URL you used, or an "
            "access-path id like clawhub:<owner>/<slug> or io.github.<owner>/<server>.",
            "detail": res.reason,
        },
    )


def check_status(state: AppState, target: str, type_: str | None = None) -> Result:
    options = load_options()
    if type_ is not None and type_ not in options.target_type:
        return problems_result(options, [{"field": "type", "message": "unknown type"}])
    now = state.clock.now()
    result = _view(state, resolve(state.catalog, target, type_), now, count=True)
    if result.code == 200:
        result.headers["Cache-Control"] = STATUS_CACHE
    return result


def _validate(body: ReportIn, options: Options) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []

    def bad(field: str, message: str) -> None:
        problems.append({"field": field, "message": message})

    if body.type is not None and body.type not in options.target_type:
        bad("type", "unknown type")
    if body.outcome not in (None, "failed"):
        # Only this problem: listing what_failed too would invite turning it into a failure.
        bad("outcome", "only failures are reported; don't send a report when it worked")
        return problems
    if not body.what_failed:
        bad("what_failed", "required: one or more values saying how it failed")
    for v in body.what_failed or []:
        if v not in options.what_failed_values:
            bad("what_failed", f"unknown value {v[:40]!r}")
    if body.what_failed and len(set(body.what_failed)) != len(body.what_failed):
        bad("what_failed", "values must not repeat")
    if body.agent_type is not None and body.agent_type not in options.agent_type:
        bad("agent_type", "unknown agent_type; use 'other' if yours isn't listed")
    if body.country is not None and body.country.upper() not in COUNTRY_CODES:
        bad("country", "must be an ISO-3166 alpha-2 code, like SG or US")
    if body.note is not None and len(body.note) > MAX_NOTE:
        bad("note", f"must be at most {MAX_NOTE} characters")
    return problems


def submit_report(
    state: AppState, body: ReportIn, client_ip: str | None, user_agent: str | None
) -> Result:
    """Validate and store a report. The client IP and User-Agent are used in memory only."""
    options = load_options()
    problems = _validate(body, options)
    try:
        res = resolve(state.catalog, body.target, body.type, by_name=False)
    except TargetError as e:
        problems.append({"field": "target", "message": str(e)})
        res = None
    if problems or res is None:
        return problems_result(options, problems)
    if res.asked is None:  # a bare service id: the agent must say which path failed
        return problems_result(
            options,
            [
                {
                    "field": "target",
                    "message": "that's a service, not an access path; report against the id "
                    "of the path you used (see access_paths in the status lookup)",
                }
            ],
        )

    now = state.clock.now()
    ttype, tid = res.asked
    prefix = network_prefix(client_ip)  # D21: never stored or logged
    family = ua_family(user_agent)
    with state.engine.begin() as conn:
        salt = store.daily_salt(conn, now)
        reporter_fp = fingerprint(salt, prefix, "", family)  # signature key id: none yet (D25)
        # Check limits before creating a target row, so a limited reporter can't add rows.
        tpk = store.find_target_pk(conn, ttype, tid)
        wait = store.retry_after(conn, reporter_fp, tpk, now)
        if wait is not None:
            return Result(
                429,
                {
                    "error": "rate_limited",
                    "message": "You've already reported recently. Please wait before reporting "
                    "again; you can still check the status.",
                    "retry_after_seconds": wait,
                },
                {"Retry-After": str(wait)},
            )
        if tpk is None:
            tpk = store.target_pk(conn, ttype, tid, state.catalog.is_listed(tid), now)
        store.insert_report(
            conn,
            target_pk=tpk,
            outcome="failed",
            what_failed=body.what_failed,
            country=body.country.upper() if body.country else None,
            agent_type=body.agent_type,
            note_scrubbed=scrub_note(body.note),
            reporter_fp=reporter_fp,
            prefix_fp=fingerprint(salt, prefix),
            ua_family=family,
            signed=False,
            options_version=options.version,
            created_at=now,
        )
        store.bump_usage(conn, now, "report", tid)
    view = _view(state, res, now, count=False)
    shown = {k: v for k, v in view.body.items() if k != "summary"}  # it asks for a report
    return Result(202, {"accepted": True, **shown})


def badge_status(state: AppState, target: str, type_: str | None = None) -> str:
    """The status a badge shows: the target's, or a service's most raised path (listed or not,
    D71). Anything that can't be resolved to one target shows `unknown`."""
    service = state.catalog.by_id.get(target.strip().lower()) if type_ is None else None
    if service is not None:  # a service id means the whole service, even if it's also a site
        with state.engine.connect() as conn:
            counts = store.path_counts(conn, [p.id for p in service.paths], state.clock.now())
        return max((c.status for c in counts.values()), key=LEVELS.index)
    try:
        res = resolve(state.catalog, target, type_)
    except TargetError:
        return "unknown"
    if res.kind == "service" and res.service is not None:
        ids = [res.asked[1]] if res.asked else [p.id for p in res.service.paths]
    elif res.kind == "not_listed" and res.asked is not None:
        ids = [res.asked[1]]
    else:
        return "unknown"
    with state.engine.connect() as conn:
        counts = store.path_counts(conn, ids, state.clock.now())
    return max((c.status for c in counts.values()), key=LEVELS.index)
