"""`agentdown stats`: a plain-text usage summary for stewards (docs/design.md D64, D65, D24)."""

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from agentdown.db.schema import reports, targets, usage_daily
from agentdown.store import OVER_CAP

LISTING_MIN_REPORTERS = 10  # D24
LISTING_MIN_PREFIXES = 3


def _totals(conn: Connection, since: dt.date, event: str, limit: int) -> list[tuple[str, int]]:
    total = sa.func.sum(usage_daily.c.count).label("n")
    rows = conn.execute(
        sa.select(usage_daily.c.subject, total)
        .where(usage_daily.c.day >= since, usage_daily.c.event == event)
        .group_by(usage_daily.c.subject)
        .order_by(total.desc(), usage_daily.c.subject)
        .limit(limit)
    ).all()
    return [(subject, int(n)) for subject, n in rows]


def _sum(conn: Connection, since: dt.date, event: str) -> int:
    return int(
        conn.execute(
            sa.select(sa.func.coalesce(sa.func.sum(usage_daily.c.count), 0)).where(
                usage_daily.c.day >= since, usage_daily.c.event == event
            )
        ).scalar_one()
    )


def listing_candidates(conn: Connection, now: dt.datetime) -> list[tuple[str, str, int, int]]:
    """Unlisted targets past the D24 threshold (last 7 days): (type, id, reporters, prefixes)."""
    reporters = sa.func.count(sa.distinct(reports.c.reporter_fp)).label("reporters")
    prefixes = sa.func.count(sa.distinct(reports.c.prefix_fp)).label("prefixes")
    rows = conn.execute(
        sa.select(targets.c.type, targets.c.target_id, reporters, prefixes)
        .join(targets, targets.c.pk == reports.c.target_pk)
        .where(targets.c.listed.is_(False), reports.c.created_at > now - dt.timedelta(days=7))
        .group_by(targets.c.type, targets.c.target_id)
        .having(reporters >= LISTING_MIN_REPORTERS, prefixes >= LISTING_MIN_PREFIXES)
        .order_by(reporters.desc())
    ).all()
    return [(t, i, int(r), int(p)) for t, i, r, p in rows]


def unlisted_activity(
    conn: Connection, now: dt.datetime, days: int, limit: int = 50
) -> list[tuple[str, str, int, int, int, int]]:
    """Unlisted targets agents reported or looked up in the window, canary excluded:
    (type, id, reports, reporters, networks, lookups), busiest first."""
    since = now - dt.timedelta(days=days)
    rows = conn.execute(
        sa.select(
            targets.c.type,
            targets.c.target_id,
            sa.func.count(),
            sa.func.count(sa.distinct(reports.c.reporter_fp)),
            sa.func.count(sa.distinct(reports.c.prefix_fp)),
        )
        .join(targets, targets.c.pk == reports.c.target_pk)
        .where(
            targets.c.listed.is_(False),
            reports.c.created_at > since,
            sa.func.coalesce(reports.c.agent_type, "") != "notworking_canary",
        )
        .group_by(targets.c.type, targets.c.target_id)
    ).all()
    found = {tid: [ttype, tid, int(n), int(r), int(p), 0] for ttype, tid, n, r, p in rows}
    misses = _totals(conn, now.date() - dt.timedelta(days=days - 1), "lookup_miss", 10_000)
    for subject, n in misses:
        if subject not in ("unparseable", OVER_CAP):
            found.setdefault(subject, ["", subject, 0, 0, 0, 0])[5] += n
    ranked = sorted(found.values(), key=lambda f: (-f[3], -f[2], -f[5], f[1]))
    return [tuple(f) for f in ranked[:limit]]


def render_stats(conn: Connection, now: dt.datetime, days: int = 7) -> str:
    since = now.date() - dt.timedelta(days=days - 1)
    lookups = _sum(conn, since, "lookup_service")
    reports_n = _sum(conn, since, "report")
    lines = [f"NotWorking usage, {since.isoformat()} to {now.date().isoformat()} ({days} days)", ""]
    ratio = f"{reports_n / lookups:.2f}" if lookups else "n/a"
    lines.append(f"Service lookups: {lookups}   Reports: {reports_n}   Reports per lookup: {ratio}")

    def section(title: str, rows: list[tuple[str, int]]) -> None:
        lines.extend(["", title])
        lines.extend(f"  {n:>6}  {subject}" for subject, n in rows)
        if not rows:
            lines.append("  (none)")

    section("Lookups by service", _totals(conn, since, "lookup_service", 20))
    section("Lookups by access path", _totals(conn, since, "lookup_path", 20))
    section("Reports by target", _totals(conn, since, "report", 20))

    lines.extend(["", "Unlisted targets agents used (reports exclude the canary)"])
    lines.append("  reports  reporters  networks  lookups  target")
    active = unlisted_activity(conn, now, days)
    lines.extend(
        f"  {n:>7}  {r:>9}  {p:>8}  {k:>7}  {f'{t}:' if t else ''}{i}"
        for t, i, n, r, p, k in active
    )
    if not active:
        lines.append("  (none)")

    lines.extend(["", "Unlisted targets past the listing threshold (last 7 days, D24)"])
    found = listing_candidates(conn, now)
    lines.extend(f"  {r:>4} reporters, {p:>3} networks  {t}:{i}" for t, i, r, p in found)
    if not found:
        lines.append("  (none)")
    return "\n".join(lines)
