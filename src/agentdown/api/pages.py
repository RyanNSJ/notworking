"""Human pages (M5, D42): the front page with the signal board, the service index, one page
per service, methodology and the dataset. Server-rendered Jinja2; charts are inline SVG.

Like the agent responses, every word on these pages comes from the catalogue, options.yaml,
the templates or fixed strings here. Report notes are never shown.
"""

import csv
import datetime as dt
import io
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from agentdown import detector, publish, store
from agentdown.catalog import AccessPath, Service
from agentdown.detector import ISSUES, LEVELS, MANY, NONE
from agentdown.service import AppState
from agentdown.store import DAY_BUCKETS, DayActivity, PathCounts

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "site" / "templates")
PAGE_CACHE = {"Cache-Control": "public, max-age=60"}
BOARD_SIZE = 10
SEARCH_LIMIT = 50
LOOKUP_DAYS = 7

STATUS_LABEL = {NONE: "no reported issues", ISSUES: "issues reported", MANY: "many issues reported"}
STATUS_CLASS = {NONE: "ok", ISSUES: "warn", MANY: "bad"}


@dataclass
class PathRow:
    path: AccessPath
    counts: PathCounts
    day: DayActivity


@dataclass
class ServiceRow:
    service: Service
    paths: list[PathRow]
    status: str
    buckets: list[int]
    total_24h: int
    top_path: PathRow | None  # the path with the most reports in 24h, if any
    lookups_7d: int = 0
    checks_today: int = 0  # status lookups today (UTC): context only, never a status


def strip_svg(buckets: list[int], status: str, *, height: int, label: str) -> Markup:
    """A 24h bar strip: one bar per 15 minutes. The last hour takes the status colour."""
    width, top = 480, max(4, *buckets)
    bw = width / len(buckets)
    recent = len(buckets) - 4  # the detector's 1h window
    bars = []
    for i, v in enumerate(buckets):
        h = max(1.0, v / top * (height - 2))
        cls = "bar-hot" if v and i >= recent and status != NONE else "bar"
        bars.append(
            f'<rect class="{cls}" x="{i * bw + 0.5:.1f}" y="{height - h:.1f}" '
            f'width="{max(1.0, bw - 1):.1f}" height="{h:.1f}"/>'
        )
    return Markup(
        f'<svg class="strip" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'role="img" aria-label="{escape(label)}">{"".join(bars)}</svg>'
    )


def _rows(
    state: AppState, now: dt.datetime, services: Sequence[Service] | None = None
) -> list[ServiceRow]:
    services = state.catalog.services if services is None else services
    ids = sorted({p.id for s in services for p in s.paths})
    with state.engine.connect() as conn:
        counts = store.path_counts(conn, ids, now)
        days = store.day_activity(conn, ids, now)
        checks = store.service_lookups(conn, now.date())
    rows = []
    for s in services:
        paths = [PathRow(p, counts[p.id], days[p.id]) for p in s.paths]
        status = max((r.counts.status for r in paths), key=LEVELS.index, default=NONE)
        buckets = [sum(r.day.buckets[i] for r in paths) for i in range(DAY_BUCKETS)]
        active = [r for r in paths if r.day.total]
        top = max(active, key=lambda r: (r.day.total, r.path.id)) if active else None
        rows.append(
            ServiceRow(
                s, paths, status, buckets, sum(buckets), top, checks_today=checks.get(s.id, 0)
            )
        )
    return rows


def ranked(rows: list[ServiceRow]) -> list[ServiceRow]:
    """Raised statuses first (most reported first), then the most looked-up services in the
    last 7 days, then alphabetical. Access paths within a service stay alphabetical."""
    return sorted(
        rows,
        key=lambda r: (
            -LEVELS.index(r.status),
            -r.total_24h if r.status != NONE else 0,
            -r.lookups_7d,
            r.service.name.lower(),
        ),
    )


def _page(request: Request, name: str, **context: object) -> HTMLResponse:
    state: AppState = request.app.state
    return templates.TemplateResponse(
        request,
        name,
        {
            "public_url": state.settings.public_url,
            "repo_url": publish.REPO_URL,
            "label": STATUS_LABEL,
            "cls": STATUS_CLASS,
            "strip": strip_svg,
            "site_description": publish.DESCRIPTION,
            **context,
        },
        headers=PAGE_CACHE,
    )


@router.get("/")
def home(request: Request) -> HTMLResponse:
    state: AppState = request.app.state
    now = state.clock.now()
    rows = _rows(state, now)
    with state.engine.connect() as conn:  # the last LOOKUP_DAYS days, today included
        lookups = store.service_lookups(conn, now.date() - dt.timedelta(days=LOOKUP_DAYS - 1))
    for r in rows:
        r.lookups_7d = lookups.get(r.service.id, 0)
    rows = ranked(rows)
    return _page(
        request,
        "home.html",
        board=rows[:BOARD_SIZE],
        total_services=len(rows),
        install=publish.install_commands(state.settings.public_url),
        description=publish.DESCRIPTION,
    )


def matches(service: Service, q: str) -> bool:
    """Case-insensitive substring search over the name, id, aliases and access-path ids."""
    words = [service.name, service.id, *service.aliases, *(p.id for p in service.paths)]
    return any(q in w.lower() for w in words)


@router.get("/services")
def services(request: Request, q: str = "") -> HTMLResponse:
    """Search results and services with reports get charts; the rest are a compact A-Z list."""
    state: AppState = request.app.state
    q = " ".join(q.split())[:100]
    by_name = sorted(state.catalog.services, key=lambda s: s.name.lower())
    if q:
        found = [s for s in by_name if matches(s, q.lower())]
        rows = _rows(state, state.clock.now(), found[:SEARCH_LIMIT])
        return _page(request, "services.html", rows=rows, q=q, total=len(found), rest=[])
    rows = [r for r in _rows(state, state.clock.now(), by_name) if r.total_24h]
    reported = {r.service.id for r in rows}
    rest = [s for s in by_name if s.id not in reported]
    return _page(request, "services.html", rows=rows, q=q, total=len(by_name), rest=rest)


@router.get("/service/{service_id}")
def service_page(request: Request, service_id: str) -> HTMLResponse:
    state: AppState = request.app.state
    service = state.catalog.by_id.get(service_id)
    row = _rows(state, state.clock.now(), [service])[0] if service else None
    if row is None:
        response = _page(request, "not_found.html", asked=service_id)
        response.status_code = 404
        return response
    return _page(request, "service.html", row=row)


@router.get("/methodology")
def methodology(request: Request) -> HTMLResponse:
    return _page(request, "methodology.html", d=detector)


@router.get("/dataset")
def dataset(request: Request) -> HTMLResponse:
    return _page(request, "dataset.html")


@router.get("/privacy")
def privacy(request: Request) -> HTMLResponse:
    return _page(request, "privacy.html")


@router.get("/terms")
def terms(request: Request) -> HTMLResponse:
    return _page(request, "terms.html")


def _csv(rows: list[list[object]], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


def _today(state: AppState) -> dt.datetime:
    now = state.clock.now()
    return dt.datetime.combine(now.date(), dt.time(), tzinfo=dt.UTC)


@router.get("/dataset/daily.csv")
def daily_csv(request: Request) -> Response:
    """One row per listed path per completed UTC day. Aggregates only (CC BY 4.0)."""
    state: AppState = request.app.state
    with state.engine.connect() as conn:
        reports = store.listed_reports(conn, _today(state))
    agg: dict[tuple[str, str, str], dict] = {}
    for ttype, tid, outcome, what_failed, fp, created in reports:
        if outcome != "failed":  # older success reports, from before they were removed
            continue
        day = store.utc(created).date().isoformat()
        a = agg.setdefault((day, ttype, tid), {"failed": 0, "reporters": set(), "what": {}})
        a["failed"] += 1
        a["reporters"].add(fp)  # fingerprints rotate daily, so this is per-day unique
        for w in what_failed or []:
            a["what"][w] = a["what"].get(w, 0) + 1
    rows = []
    for (day, ttype, tid), a in agg.items():
        services = ";".join(s.id for s in state.catalog.services_for_path.get(tid, []))
        what = ";".join(f"{k}={v}" for k, v in sorted(a["what"].items()))
        rows.append([day, services, ttype, tid, a["failed"], len(a["reporters"]), what])
    header = [
        "date", "service_ids", "path_type", "path_id", "failure_reports",
        "unique_failure_reporters", "what_failed",
    ]  # fmt: skip
    return _csv(rows, header, "notworking-daily.csv")


@router.get("/dataset/transitions.csv")
def transitions_csv(request: Request) -> Response:
    """Every status change on a listed path before today, with the detector's inputs."""
    state: AppState = request.app.state
    with state.engine.connect() as conn:
        transitions = store.listed_transitions(conn, _today(state))
    keys = ["n", "reports", "baseline", "p", "prefixes", "agent_types", "top_prefix_share"]
    rows = []
    for created, ttype, tid, old, new, inputs in transitions:
        data = inputs if isinstance(inputs, dict) else json.loads(inputs)
        when = store.utc(created).isoformat(timespec="seconds").replace("+00:00", "Z")
        rows.append([when, ttype, tid, old, new, *(data.get(k) for k in keys)])
    header = ["time", "path_type", "path_id", "from_status", "to_status", *keys]
    return _csv(rows, header, "notworking-transitions.csv")
