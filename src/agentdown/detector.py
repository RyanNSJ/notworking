"""Spike detection (docs/design.md D16, D19, D20): one status per listed access path.

Runs every 5 minutes. For each path it counts distinct failure reporters in the last hour
(n) and compares n with that path's own baseline (lambda: the mean distinct reporters per
hour over the 7 days before the window, floored). Deterministic and explainable: every
status change is logged with the inputs below. No ML.
"""

import datetime as dt
import math
from collections import Counter, defaultdict

from sqlalchemy.engine import Connection

from agentdown import store
from agentdown.store import WINDOW, CurrentStatus, FailureRow

NONE, ISSUES, MANY = "no_reported_issues", "issues_reported", "many_issues_reported"
LEVELS = [NONE, ISSUES, MANY]

BASELINE_SPAN = dt.timedelta(days=7)
BASELINE_FLOOR = 0.5  # expected reporters per hour when a path has little history
ISSUES_MIN_N, ISSUES_P = 3, 1e-3
MANY_MIN_N, MANY_P = 15, 1e-6
MANY_MIN_PREFIXES = 5
MANY_MIN_AGENT_TYPES = 2  # self-reported, so prefixes do the real work
MANY_MAX_PREFIX_SHARE = 0.3
CALM_RUNS_TO_STEP_DOWN = 6  # 6 x 5 minutes = 30 minutes (D19)


def poisson_tail(n: int, lam: float) -> float:
    """P(X >= n) for X ~ Poisson(lam). Sums whichever tail is small, for precision."""
    if n <= 0:
        return 1.0
    if n <= lam:
        term, lower = math.exp(-lam), 0.0
        for k in range(n):
            lower += term
            term *= lam / (k + 1)
        return max(0.0, 1.0 - lower)
    total, k = 0.0, n
    term = math.exp(-lam + n * math.log(lam) - math.lgamma(n + 1))
    while term > 1e-300 and (total == 0 or term > total * 1e-17):
        total += term
        k += 1
        term *= lam / k
    return min(total, 1.0)


def baseline(rows: list[FailureRow], window_start: dt.datetime) -> float:
    """Mean distinct reporters per hour over BASELINE_SPAN before the window, floored."""
    hours = BASELINE_SPAN / WINDOW
    seen = {
        (r.reporter_fp, int((window_start - r.created_at) / WINDOW))
        for r in rows
        if window_start - BASELINE_SPAN < r.created_at <= window_start
    }
    return max(BASELINE_FLOOR, len(seen) / hours)


def classify(window: list[FailureRow], lam: float) -> tuple[str, dict[str, object]]:
    """The status these window reports justify, and the inputs that decided it."""
    n = len({r.reporter_fp for r in window})
    p = poisson_tail(n, lam)
    prefixes = Counter(r.prefix_fp for r in window)
    agent_types = {r.agent_type for r in window if r.agent_type}
    top_share = max(prefixes.values()) / len(window) if window else 0.0
    inputs: dict[str, object] = {
        "n": n,
        "reports": len(window),
        "baseline": round(lam, 4),
        "p": p,
        "prefixes": len(prefixes),
        "agent_types": len(agent_types),
        "top_prefix_share": round(top_share, 4),
    }
    status = NONE
    if n >= ISSUES_MIN_N and p < ISSUES_P:
        status = ISSUES
        if (
            n >= MANY_MIN_N
            and p < MANY_P
            and len(prefixes) >= MANY_MIN_PREFIXES
            and len(agent_types) >= MANY_MIN_AGENT_TYPES
            and top_share <= MANY_MAX_PREFIX_SHARE
        ):
            status = MANY
    return status, inputs


def step(current: CurrentStatus | None, computed: str, now: dt.datetime) -> CurrentStatus:
    """Hysteresis (D19): go up at once; step down only after 6 calm runs in a row."""
    if current is None:
        return CurrentStatus(computed, now, 0)
    if LEVELS.index(computed) >= LEVELS.index(current.status):
        since = current.since if computed == current.status else now
        return CurrentStatus(computed, since, 0)
    calm = current.calm_runs + 1
    if calm >= CALM_RUNS_TO_STEP_DOWN:
        return CurrentStatus(computed, now, 0)
    return CurrentStatus(current.status, current.since, calm)


def run(conn: Connection, now: dt.datetime) -> int:
    """Evaluate every path with recent failures or a raised status. Returns transitions."""
    window_start = now - WINDOW
    # shortcut: loads 7 days of failure rows into memory; fine at our volume, move the
    # baseline into SQL (or a daily rollup) if reports grow past a few hundred thousand.
    rows = store.failures_since(conn, window_start - BASELINE_SPAN)
    by_target: dict[int, list[FailureRow]] = defaultdict(list)
    for r in rows:
        by_target[r.target_pk].append(r)
    currents = store.current_statuses(conn)
    active = {pk for pk, rs in by_target.items() if any(r.created_at > window_start for r in rs)}
    active |= {pk for pk, c in currents.items() if c.status != NONE}

    changes = 0
    for tpk in sorted(active):
        target_rows = by_target.get(tpk, [])
        window = [r for r in target_rows if r.created_at > window_start]
        computed, inputs = classify(window, baseline(target_rows, window_start))
        before = currents.get(tpk)
        after = step(before, computed, now)
        store.save_status(conn, tpk, after, inputs, now)
        old = before.status if before else NONE
        if after.status != old:
            store.log_transition(conn, tpk, old, after.status, inputs, now)
            changes += 1
    return changes
