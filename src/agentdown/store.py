"""Database access for reports, counts, salts, rate limits and usage counters.

All functions take an open connection and an explicit `now` (UTC), so the caller owns
transactions and tests control time.
"""

import datetime as dt
import secrets
from collections import Counter
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from agentdown.catalog import Catalog
from agentdown.db.schema import (
    checks_hourly,
    reports,
    salts,
    status_current,
    status_transitions,
    targets,
    usage_daily,
)

WINDOW = dt.timedelta(hours=1)
PER_TARGET_GAP = dt.timedelta(minutes=10)  # D57
PER_REPORTER_HOURLY = 60  # D57
MISS_SUBJECTS_PER_DAY = 1000  # beyond this, lookup misses count under one subject
OVER_CAP = "(over daily cap)"
CHECKS_KEPT = dt.timedelta(days=8)


def utc(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _insert(conn: Connection, table: sa.Table):
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


# ---- targets ---------------------------------------------------------------


def sync_listed(conn: Connection, catalog: Catalog, now: dt.datetime) -> None:
    """Mirror the catalogue into `targets.listed`. Rows are kept: reports point at them."""
    conn.execute(targets.update().values(listed=False))
    for path_id, ptype in catalog.path_types.items():
        stmt = _insert(conn, targets).values(
            type=ptype, target_id=path_id, listed=True, created_at=now
        )
        conn.execute(
            stmt.on_conflict_do_update(index_elements=["type", "target_id"], set_={"listed": True})
        )


def find_target_pk(conn: Connection, ttype: str, tid: str) -> int | None:
    return conn.execute(
        sa.select(targets.c.pk).where(targets.c.type == ttype, targets.c.target_id == tid)
    ).scalar()


def target_pk(conn: Connection, ttype: str, tid: str, listed: bool, now: dt.datetime) -> int:
    stmt = _insert(conn, targets).values(type=ttype, target_id=tid, listed=listed, created_at=now)
    conn.execute(stmt.on_conflict_do_nothing(index_elements=["type", "target_id"]))
    return conn.execute(
        sa.select(targets.c.pk).where(targets.c.type == ttype, targets.c.target_id == tid)
    ).scalar_one()


# ---- salts (D21) -----------------------------------------------------------


def daily_salt(conn: Connection, now: dt.datetime) -> str:
    """Today's salt, created on first use. Salts older than yesterday are deleted."""
    today = now.date()
    stmt = _insert(conn, salts).values(day=today, salt=secrets.token_hex(32), created_at=now)
    conn.execute(stmt.on_conflict_do_nothing(index_elements=["day"]))
    delete_old_salts(conn, now)
    return conn.execute(sa.select(salts.c.salt).where(salts.c.day == today)).scalar_one()


def delete_old_salts(conn: Connection, now: dt.datetime) -> None:
    """Delete salts older than yesterday, so old fingerprints can't be reversed (D21)."""
    conn.execute(salts.delete().where(salts.c.day < now.date() - dt.timedelta(days=1)))


# ---- reports ---------------------------------------------------------------


def retry_after(
    conn: Connection, reporter_fp: str, tpk: int | None, now: dt.datetime
) -> int | None:
    """Seconds until this reporter may report again (D57), or None if allowed now.

    `tpk` is None for a target with no row yet, so it has no per-target history."""
    last = conn.execute(
        sa.select(sa.func.max(reports.c.created_at)).where(
            reports.c.reporter_fp == reporter_fp,
            reports.c.target_pk == tpk,
            reports.c.created_at > now - PER_TARGET_GAP,
        )
    ).scalar()
    if last is not None:
        return max(1, int((utc(last) + PER_TARGET_GAP - now).total_seconds()))
    recent = (
        conn.execute(
            sa.select(reports.c.created_at)
            .where(reports.c.reporter_fp == reporter_fp, reports.c.created_at > now - WINDOW)
            .order_by(reports.c.created_at)
        )
        .scalars()
        .all()
    )
    if len(recent) >= PER_REPORTER_HOURLY:
        oldest = utc(recent[len(recent) - PER_REPORTER_HOURLY])
        return max(1, int((oldest + WINDOW - now).total_seconds()))
    return None


def insert_report(conn: Connection, **values: object) -> None:
    conn.execute(reports.insert().values(**values))


@dataclass
class PathCounts:
    failure_reports: int = 0
    unique_reporters: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)
    last_report_at: dt.datetime | None = None
    status: str = "no_reported_issues"  # set by the detector (M4)


def path_counts(conn: Connection, path_ids: list[str], now: dt.datetime) -> dict[str, PathCounts]:
    """Failure counts per path id over the last hour."""
    rows = conn.execute(
        sa.select(
            targets.c.target_id,
            reports.c.reporter_fp,
            reports.c.what_failed,
            reports.c.created_at,
        )
        .join(targets, targets.c.pk == reports.c.target_pk)
        .where(
            targets.c.target_id.in_(path_ids),
            reports.c.outcome == "failed",
            reports.c.created_at > now - WINDOW,
        )
    ).all()
    out = {pid: PathCounts() for pid in path_ids}
    reporters: dict[str, set[str]] = {pid: set() for pid in path_ids}
    breakdowns: dict[str, Counter[str]] = {pid: Counter() for pid in path_ids}
    for tid, fp, what_failed, created in rows:
        c = out[tid]
        c.failure_reports += 1
        reporters[tid].add(fp)
        breakdowns[tid].update(what_failed or [])
        created = utc(created)
        if c.last_report_at is None or created > c.last_report_at:
            c.last_report_at = created
    for pid, c in out.items():
        c.unique_reporters = len(reporters[pid])
        c.breakdown = dict(sorted(breakdowns[pid].items()))
    statuses = conn.execute(
        sa.select(targets.c.target_id, status_current.c.status)
        .join(targets, targets.c.pk == status_current.c.target_pk)
        .where(targets.c.target_id.in_(path_ids))
    ).all()
    for tid, st in statuses:
        out[tid].status = st
    return out


# ---- detector (M4) ---------------------------------------------------------


@dataclass
class FailureRow:
    target_pk: int
    reporter_fp: str
    prefix_fp: str
    agent_type: str | None
    created_at: dt.datetime


def failures_since(conn: Connection, since: dt.datetime) -> list[FailureRow]:
    """Failure reports on every target since `since`; unlisted ones get statuses too (D71)."""
    rows = conn.execute(
        sa.select(
            reports.c.target_pk,
            reports.c.reporter_fp,
            reports.c.prefix_fp,
            reports.c.agent_type,
            reports.c.created_at,
        )
        .join(targets, targets.c.pk == reports.c.target_pk)
        .where(reports.c.outcome == "failed", reports.c.created_at > since)
    ).all()
    return [FailureRow(pk, fp, pfp, at, utc(t)) for pk, fp, pfp, at, t in rows]


@dataclass
class CurrentStatus:
    status: str
    since: dt.datetime
    calm_runs: int


def current_statuses(conn: Connection) -> dict[int, CurrentStatus]:
    rows = conn.execute(
        sa.select(
            status_current.c.target_pk,
            status_current.c.status,
            status_current.c.since,
            status_current.c.calm_runs,
        )
    ).all()
    return {pk: CurrentStatus(st, utc(since), calm) for pk, st, since, calm in rows}


def save_status(
    conn: Connection,
    tpk: int,
    current: CurrentStatus,
    inputs: dict[str, object],
    now: dt.datetime,
) -> None:
    values = {
        "status": current.status,
        "since": current.since,
        "calm_runs": current.calm_runs,
        "n_window": inputs["n"],
        "baseline": inputs["baseline"],
        "breakdown": inputs,
        "updated_at": now,
    }
    stmt = _insert(conn, status_current).values(target_pk=tpk, **values)
    conn.execute(stmt.on_conflict_do_update(index_elements=["target_pk"], set_=values))


def log_transition(
    conn: Connection,
    tpk: int,
    from_status: str,
    to_status: str,
    inputs: dict[str, object],
    now: dt.datetime,
) -> None:
    conn.execute(
        status_transitions.insert().values(
            target_pk=tpk,
            from_status=from_status,
            to_status=to_status,
            inputs=inputs,
            created_at=now,
        )
    )


# ---- usage counters (D64) --------------------------------------------------


def bump_usage(
    conn: Connection, now: dt.datetime, event: str, subject: str, cap: int | None = None
) -> None:
    """Count one event. With `cap`, new subjects past `cap` per day share one row, so
    random lookups can't grow the table without limit."""
    day, subject = now.date(), subject[:512]
    if cap is not None:
        key = (usage_daily.c.day == day) & (usage_daily.c.event == event)
        seen = sa.select(usage_daily.c.subject).where(key & (usage_daily.c.subject == subject))
        if conn.execute(seen).first() is None:
            n = conn.execute(sa.select(sa.func.count()).select_from(usage_daily).where(key))
            if n.scalar_one() >= cap:
                subject = OVER_CAP
    stmt = _insert(conn, usage_daily).values(day=day, event=event, subject=subject, count=1)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["day", "event", "subject"],
            set_={"count": usage_daily.c.count + 1},
        )
    )


# ---- pages and dataset (M5) ------------------------------------------------

BUCKET = dt.timedelta(minutes=15)
DAY_BUCKETS = 96  # 24 hours of 15-minute buckets


@dataclass
class DayActivity:
    buckets: list[int] = field(default_factory=lambda: [0] * DAY_BUCKETS)  # oldest first
    total: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)


def day_activity(conn: Connection, path_ids: list[str], now: dt.datetime) -> dict[str, DayActivity]:
    """Failure reports per path over the last 24 hours, in 15-minute buckets."""
    start = now - BUCKET * DAY_BUCKETS
    rows = conn.execute(
        sa.select(targets.c.target_id, reports.c.what_failed, reports.c.created_at)
        .join(targets, targets.c.pk == reports.c.target_pk)
        .where(
            targets.c.target_id.in_(path_ids),
            reports.c.outcome == "failed",
            reports.c.created_at > start,
        )
    ).all()
    out = {pid: DayActivity() for pid in path_ids}
    tallies: dict[str, Counter[str]] = {pid: Counter() for pid in path_ids}
    for tid, what_failed, created in rows:
        i = min(DAY_BUCKETS - 1, int((utc(created) - start) / BUCKET))
        out[tid].buckets[i] += 1
        out[tid].total += 1
        tallies[tid].update(what_failed or [])
    for pid, a in out.items():
        a.breakdown = dict(tallies[pid].most_common())
    return out


def listed_reports(conn: Connection, before: dt.datetime) -> list[sa.Row]:
    """Every report on a listed target before `before`, for the daily dataset."""
    # shortcut: reads all history into memory; fine at our volume, aggregate in SQL
    # (or keep a daily rollup table) once reports reach the hundreds of thousands.
    return list(
        conn.execute(
            sa.select(
                targets.c.type,
                targets.c.target_id,
                reports.c.outcome,
                reports.c.what_failed,
                reports.c.reporter_fp,
                reports.c.created_at,
            )
            .join(targets, targets.c.pk == reports.c.target_pk)
            .where(targets.c.listed, reports.c.created_at < before)
            .order_by(reports.c.created_at)
        ).all()
    )


def listed_transitions(conn: Connection, before: dt.datetime) -> list[sa.Row]:
    return list(
        conn.execute(
            sa.select(
                status_transitions.c.created_at,
                targets.c.type,
                targets.c.target_id,
                status_transitions.c.from_status,
                status_transitions.c.to_status,
                status_transitions.c.inputs,
            )
            .join(targets, targets.c.pk == status_transitions.c.target_pk)
            .where(targets.c.listed, status_transitions.c.created_at < before)
            .order_by(status_transitions.c.created_at)
        ).all()
    )


def _hour(t: dt.datetime) -> dt.datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def bump_checks(conn: Connection, now: dt.datetime) -> None:
    """Count one status lookup in the current UTC hour."""
    stmt = _insert(conn, checks_hourly).values(hour=_hour(now), count=1)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["hour"], set_={"count": checks_hourly.c.count + 1}
        )
    )


def delete_old_checks(conn: Connection, now: dt.datetime) -> None:
    conn.execute(checks_hourly.delete().where(checks_hourly.c.hour < now - CHECKS_KEPT))


def site_counts(conn: Connection, now: dt.datetime, window: dt.timedelta) -> tuple[int, int]:
    """Status lookups and failure reports in the last `window`. Lookups are counted per hour,
    so they include up to an hour more than `window`."""
    checks = conn.execute(
        sa.select(sa.func.coalesce(sa.func.sum(checks_hourly.c.count), 0)).where(
            checks_hourly.c.hour >= _hour(now - window)
        )
    ).scalar_one()
    failures = conn.execute(
        sa.select(sa.func.count())
        .select_from(reports)
        .where(reports.c.created_at >= now - window, reports.c.outcome == "failed")
    ).scalar_one()
    return int(checks), int(failures)


def service_lookups(conn: Connection, since: dt.date) -> dict[str, int]:
    """Lookups per service id since `since` (D64 counters), for ranking quiet services."""
    total = sa.func.sum(usage_daily.c.count)
    rows = conn.execute(
        sa.select(usage_daily.c.subject, total)
        .where(usage_daily.c.day >= since, usage_daily.c.event == "lookup_service")
        .group_by(usage_daily.c.subject)
    ).all()
    return {subject: int(n) for subject, n in rows}
