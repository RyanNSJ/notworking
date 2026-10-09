"""The M4 detector (D16, D19, D20): Poisson + baseline, diversity rules, hysteresis."""

import datetime as dt
from collections.abc import Callable
from typing import cast

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from agentdown import detector, jobs
from agentdown.core.clock import FixedClock
from agentdown.detector import ISSUES, MANY, NONE
from agentdown.service import AppState
from agentdown.store import FailureRow
from tests.conftest import START

FAIL = {"outcome": "failed", "what_failed": ["captcha"]}


def rows(
    n: int, *, prefixes: int | None = None, agent_types: int = 2, at=START
) -> list[FailureRow]:
    """n distinct reporters at `at`, spread over `prefixes` networks (default: one each)."""
    p = prefixes or n
    kinds = ["claude", "chatgpt", "openclaw"][:agent_types]
    return [FailureRow(1, f"r{i}", f"p{i % p}", kinds[i % len(kinds)], at) for i in range(n)]


# ---- pure parts ------------------------------------------------------------------


def test_poisson_tail_known_values() -> None:
    assert detector.poisson_tail(0, 0.5) == 1.0
    assert abs(detector.poisson_tail(1, 0.5) - (1 - 0.6065306597)) < 1e-9
    assert 1.7e-4 < detector.poisson_tail(5, 0.5) < 1.8e-4
    assert 0 < detector.poisson_tail(40, 0.5) < 1e-40  # no underflow to 1 - 1


@given(st.integers(0, 60), st.floats(0.5, 50))
def test_poisson_tail_is_a_probability_that_falls_with_n(n: int, lam: float) -> None:
    p = detector.poisson_tail(n, lam)
    assert 0 <= p <= 1
    assert detector.poisson_tail(n + 1, lam) <= p


def test_quiet_paths_need_five_reporters() -> None:
    assert detector.classify(rows(4), detector.BASELINE_FLOOR)[0] == NONE
    assert detector.classify(rows(5), detector.BASELINE_FLOOR)[0] == ISSUES


def test_baseline_raises_the_bar_for_busy_paths() -> None:
    window_start = START - dt.timedelta(hours=1)
    history = [
        FailureRow(1, f"h{h}-{i}", f"p{i}", None, window_start - dt.timedelta(hours=h, minutes=1))
        for h in range(168)
        for i in range(3)
    ]
    lam = detector.baseline(history, window_start)
    assert abs(lam - 3.0) < 1e-9
    assert detector.classify(rows(5), lam)[0] == NONE  # 5 is normal for this path
    assert detector.classify(rows(12), lam)[0] == ISSUES


def test_many_needs_volume_and_diversity() -> None:
    lam = detector.BASELINE_FLOOR
    assert detector.classify(rows(16), lam)[0] == MANY
    assert detector.classify(rows(14), lam)[0] == ISSUES  # volume
    assert detector.classify(rows(20, prefixes=4), lam)[0] == ISSUES  # too few networks
    assert detector.classify(rows(20, agent_types=1), lam)[0] == ISSUES  # one agent type
    lopsided = rows(16) + [FailureRow(1, f"x{i}", "p0", "claude", START) for i in range(6)]
    assert detector.classify(lopsided, lam)[0] == ISSUES  # one network over 30%


def test_hysteresis_steps_down_after_six_calm_runs() -> None:
    cur = detector.step(None, ISSUES, START)
    for i in range(5):
        cur = detector.step(cur, NONE, START + dt.timedelta(minutes=5 * (i + 1)))
        assert cur.status == ISSUES
    cur = detector.step(cur, NONE, START + dt.timedelta(minutes=30))
    assert cur.status == NONE and cur.calm_runs == 0
    assert detector.step(cur, MANY, START).status == MANY  # up at once


# ---- end to end over HTTP ----------------------------------------------------------


def tick(app: FastAPI) -> int:
    return jobs.tick(cast(AppState, app.state))


def site_status(client: TestClient) -> str:
    body = client.get("/v1/status", params={"target": "xyz.com"}).json()
    return next(p for p in body["access_paths"] if p["id"] == "xyz.com")["status"]


def transitions(app: FastAPI) -> list[tuple[str, str]]:
    with app.state.engine.connect() as conn:
        q = "SELECT from_status, to_status FROM status_transitions ORDER BY pk"
        return [tuple(r) for r in conn.execute(sa.text(q)).all()]


def test_reports_raise_and_calm_lowers_the_status(
    client: TestClient,
    client_from: Callable[[str], TestClient],
    app: FastAPI,
    clock: FixedClock,
) -> None:
    for i in range(5):
        client_from(f"198.51.{100 + i}.1").post("/v1/report", json={"target": "xyz.com", **FAIL})
    assert site_status(client) == NONE  # until the detector runs
    assert tick(app) == 1
    assert site_status(client) == ISSUES
    with app.state.engine.connect() as conn:
        inputs = conn.execute(sa.text("SELECT inputs FROM status_transitions")).scalar_one()
    assert '"n": 5' in inputs and '"baseline": 0.5' in inputs

    clock.advance(dt.timedelta(minutes=61))  # the reports leave the window
    for _ in range(5):
        tick(app)
        clock.advance(dt.timedelta(minutes=5))
    assert site_status(client) == ISSUES
    tick(app)
    assert site_status(client) == NONE
    assert transitions(app) == [(NONE, ISSUES), (ISSUES, NONE)]


def test_one_network_cannot_reach_many(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    """D21 invariant: 20 'reporters' from one /24 (varied User-Agents) stay at issues."""
    agents = ["claude", "chatgpt", "curl/8", "python-httpx/1", "node", "Go-http-client/2"]
    same_net = client_from("198.51.100.1")
    for i, ua in enumerate(agents):
        kind = "claude" if i % 2 else "chatgpt"
        same_net.post(
            "/v1/report",
            json={"target": "xyz.com", **FAIL, "agent_type": kind},
            headers={"user-agent": ua},
        )
    tick(app)
    assert site_status(client) == ISSUES


def test_many_issues_from_many_networks(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    for i in range(16):
        client_from(f"198.51.{100 + i}.1").post(
            "/v1/report",
            json={"target": "xyz.com", **FAIL, "agent_type": "claude" if i % 2 else "chatgpt"},
        )
    tick(app)
    assert site_status(client) == MANY


def test_tick_prunes_old_salts(client: TestClient, app: FastAPI, clock: FixedClock) -> None:
    client.post("/v1/report", json={"target": "xyz.com", **FAIL})
    clock.advance(dt.timedelta(days=3))
    tick(app)
    with app.state.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM salts")).scalar() == 0
