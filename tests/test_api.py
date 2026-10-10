import datetime as dt
from collections.abc import Callable
from typing import cast

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentdown import jobs, store
from agentdown.catalog import parse_catalog
from agentdown.core.clock import FixedClock
from agentdown.db import engine as db
from agentdown.lookup import NOTICE, PLEASE_REPORT, resolve
from agentdown.service import AppState
from agentdown.stats import render_stats
from tests.conftest import PUBLIC_URL

FAIL = {"outcome": "failed", "what_failed": ["captcha"]}


def status(client: TestClient, target: str, **params: str):
    return client.get("/v1/status", params={"target": target, **params})


def report(client: TestClient, target: str, **body):
    return client.post("/v1/report", json={"target": target, **(body or FAIL)})


# ---- lookups -----------------------------------------------------------------


def test_service_view_shape(client: TestClient) -> None:
    r = status(client, "https://www.xyz.com/booking/42?email=a@b.com")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=60"
    body = r.json()
    assert body["service"] == {"id": "xyz.com", "name": "XYZ Booking"}
    assert body["you_asked_about"] == {"type": "route", "id": "xyz.com/booking"}
    ids = [p["id"] for p in body["access_paths"]]
    assert ids == sorted(ids)  # alphabetical (D60)
    assert all(p["status"] == "no_reported_issues" for p in body["access_paths"])
    retired = next(p for p in body["access_paths"] if p["id"] == "clawhub:def/xyz")
    assert retired["no_longer_working_since"] == "2026-09-01"
    assert body["please_report"]["message"] == PLEASE_REPORT
    assert body["please_report"]["url"] == f"{PUBLIC_URL}/v1/report"
    assert body["please_report"]["example"]["target"] == "xyz.com/booking"
    assert {o["value"] for o in body["what_failed_options"]} >= {"captcha", "bot_block"}
    assert all(o["description"] for o in body["what_failed_options"])
    assert body["notice"] == NOTICE
    assert body["as_of"] == "2026-10-08T12:00:00Z"
    assert body["about"] == f"{PUBLIC_URL}/methodology"
    assert "email" not in r.text and "a@b.com" not in r.text


def test_lookup_by_path_id_name_alias_and_service_id(client: TestClient) -> None:
    assert status(client, "clawhub:abc/xyz-booking").json()["you_asked_about"]["type"] == "skill"
    assert status(client, "io.github.xyz/booking-mcp").json()["service"]["id"] == "xyz.com"
    for name in ("XYZ Booking", "xyz", "  xyz   BOOKING "):
        body = status(client, name).json()
        assert body["service"]["id"] == "xyz.com"
        assert body["you_asked_about"] == {"type": "service", "id": "xyz.com"}


def test_ambiguous_lookups_return_candidates(client: TestClient) -> None:
    by_name = status(client, "shared name").json()
    assert by_name["match"] == "ambiguous"
    assert [c["id"] for c in by_name["candidates"]] == ["example.org", "other.net"]
    shared_path = status(client, "clawhub:shared/browser").json()
    assert shared_path["match"] == "ambiguous"
    assert shared_path["you_asked_about"] == {"type": "skill", "id": "clawhub:shared/browser"}


def test_not_listed_and_no_match(client: TestClient) -> None:
    body = status(client, "https://unknown-shop.sg/cart?id=9").json()
    assert body["listed"] is False and body["status"] == "no_reported_issues"
    assert body["failure_reports"] == 0
    assert body["you_asked_about"] == {"type": "site", "id": "unknown-shop.sg"}
    assert "access_paths" not in body and "service" not in body and "please_report" in body
    r = status(client, "no such thing")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


def test_malformed_targets_are_422_not_500(client: TestClient) -> None:
    assert status(client, "[x").status_code in (404, 422)
    assert status(client, "http://[abc").status_code in (404, 422)
    r = client.post("/v1/report", json={"target": "https://[example.com]/", **FAIL})
    assert r.status_code == 422


def test_listed_ids_keep_their_catalogue_type(client: TestClient, app: FastAPI) -> None:
    """A wrong type hint can't create a second row for a listed path (review finding)."""
    r = client.post("/v1/report", json={"target": "xyz.com/booking", "type": "mcp", **FAIL})
    assert r.status_code == 202 and r.json()["you_asked_about"]["type"] == "route"
    with app.state.engine.connect() as conn:
        types = conn.execute(
            sa.text("SELECT type FROM targets WHERE target_id = 'xyz.com/booking'")
        ).scalars()
        assert list(types) == ["route"]


def test_a_url_on_a_service_domain_can_be_reported() -> None:
    """D71: brave.com is a service id that lists only search.brave.com; a report on a URL at
    brave.com itself is an unlisted site, not 'that's a service' (review finding)."""
    catalog = parse_catalog(
        "version: 1\nservices:\n  - id: brave.com\n    name: Brave Search\n    paths:\n"
        "      - id: search.brave.com\n        type: route\n        description: Search pages.\n"
    )
    res = resolve(catalog, "https://brave.com/download", by_name=False)
    assert res.kind == "not_listed" and res.asked == ("site", "brave.com")
    assert resolve(catalog, "https://brave.com/download").kind == "service"  # a lookup


def test_bad_type_param(client: TestClient) -> None:
    r = status(client, "xyz.com", type="website")
    assert r.status_code == 422
    assert r.json()["valid_values"]["type"] == ["site", "route", "mcp", "skill"]


def test_summary_and_24h_context(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI, clock: FixedClock
) -> None:
    def view() -> tuple[str, dict]:
        body = status(client, "xyz.com").json()
        return body["summary"], next(p for p in body["access_paths"] if p["id"] == "xyz.com")

    assert view()[0] == (
        "No other agents reported failures for XYZ Booking in the last 24 hours. "
        "If it failed for you, please report it so other agents know."
    )
    assert "summary" not in report(client, "xyz.com").json()  # it would ask for a report
    text, path = view()
    assert text.startswith("1 failure report on xyz.com in the last hour, not enough to raise")
    assert path["failure_reports"] == 1 and path["failure_reports_24h"] == 1

    clock.advance(dt.timedelta(hours=2))  # out of the status window, still in the 24h context
    text, path = view()
    assert text.startswith(
        "No failure reports for XYZ Booking in the last hour; 1 failure report in the last 24 "
        "hours, most recently on xyz.com at 2026-10-08T12:00:00Z."
    )
    assert path["failure_reports"] == 0 and "breakdown" not in path
    assert path["failure_reports_24h"] == 1 and path["last_report_at"] == "2026-10-08T12:00:00Z"

    for i in range(5):  # five networks: the status rises
        report(client_from(f"198.51.{100 + i}.1"), "xyz.com/booking")
    jobs.tick(cast(AppState, app.state))
    assert view()[0].startswith("Agents are reporting failures on xyz.com/booking in the last hour")

    clock.advance(dt.timedelta(days=2))
    unlisted = status(client, "not-listed.com").json()
    assert unlisted["summary"].startswith("No other agents reported failures for not-listed.com")
    assert unlisted["failure_reports_24h"] == 0
    assert unlisted["please_report"]["message"].startswith("If this target failed for you")


# ---- reports -------------------------------------------------------------------


def test_report_returns_service_view_and_counts(client: TestClient) -> None:
    r = report(client, "clawhub:abc/xyz-booking", outcome="failed", what_failed=["skill_failed"])
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    path = next(p for p in body["access_paths"] if p["id"] == "clawhub:abc/xyz-booking")
    assert path["failure_reports"] == 1 and path["unique_reporters"] == 1
    assert path["breakdown"] == {"skill_failed": 1}
    assert path["last_report_at"] == "2026-10-08T12:00:00Z"
    other = next(p for p in body["access_paths"] if p["id"] == "xyz.com")
    assert "breakdown" not in other and other["failure_reports"] == 0


def test_only_failures_are_reported(client: TestClient) -> None:
    body = {"target": "xyz.com", "what_failed": ["captcha"]}
    assert client.post("/v1/report", json=body).status_code == 202  # outcome isn't needed
    r = client.post("/v1/report", json={"target": "xyz.com", "outcome": "success"})
    # Only the outcome problem: listing what_failed too would invite a false failure.
    assert r.status_code == 422 and {p["field"] for p in r.json()["problems"]} == {"outcome"}


def test_counts_expire_after_the_window(client: TestClient, clock: FixedClock) -> None:
    report(client, "xyz.com")
    clock.advance(dt.timedelta(minutes=61))
    site = next(p for p in status(client, "xyz.com").json()["access_paths"] if p["id"] == "xyz.com")
    assert site["failure_reports"] == 0


def test_rate_limit_per_target(client: TestClient, clock: FixedClock) -> None:
    assert report(client, "xyz.com").status_code == 202
    r = report(client, "xyz.com")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "600"
    clock.advance(dt.timedelta(minutes=4))
    assert report(client, "xyz.com").headers["retry-after"] == "360"
    assert report(client, "example.org").status_code == 202  # other targets are fine
    clock.advance(dt.timedelta(minutes=6))
    assert report(client, "xyz.com").status_code == 202


def test_rate_limit_per_hour(client: TestClient, clock: FixedClock) -> None:
    for i in range(60):
        assert report(client, f"site{i}.com").status_code == 202
        clock.advance(dt.timedelta(seconds=10))
    r = report(client, "site60.com")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) == 3600 - 600


def test_limited_reporter_creates_no_target_rows(
    client: TestClient, clock: FixedClock, app: FastAPI
) -> None:
    for i in range(60):
        report(client, f"site{i}.com")
    with app.state.engine.connect() as conn:
        before = conn.execute(sa.text("SELECT count(*) FROM targets")).scalar()
    assert all(report(client, f"new{i}.com").status_code == 429 for i in range(5))
    with app.state.engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM targets")).scalar() == before


def test_lookup_misses_are_capped_per_day(
    client: TestClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "MISS_SUBJECTS_PER_DAY", 3)
    for i in range(6):
        status(client, f"rand{i}.com")
    status(client, "rand0.com")  # already counted today: still its own row
    with app.state.engine.connect() as conn:
        rows = dict(
            conn.execute(
                sa.text("SELECT subject, count FROM usage_daily WHERE event = 'lookup_miss'")
            ).all()
        )
    assert rows == {"rand0.com": 2, "rand1.com": 1, "rand2.com": 1, store.OVER_CAP: 3}


def test_unlisted_targets_get_counts_and_a_status(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    """D71: anyone asking about an unlisted target sees its reports, without a service view."""
    r = report(client, "https://unknown-shop.sg/checkout?card=4111")
    assert r.status_code == 202
    body = r.json()
    assert body["listed"] is False and body["failure_reports"] == 1
    assert "access_paths" not in body and "4111" not in r.text
    for i in range(4):
        report(client_from(f"198.51.{100 + i}.1"), "unknown-shop.sg")
    jobs.tick(cast(AppState, app.state))  # the detector covers unlisted targets too
    assert status(client, "unknown-shop.sg").json()["status"] == "issues_reported"
    assert "unknown-shop.sg" not in client.get("/").text  # pages stay listed-only
    with app.state.engine.connect() as conn:
        listed = conn.execute(
            sa.text("SELECT listed FROM targets WHERE target_id = 'unknown-shop.sg'")
        ).scalar_one()
    assert listed in (0, False)


# ---- validation (422) -----------------------------------------------------------


def _problems(r) -> set[str]:
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == "invalid_request"
    assert {o["value"] for o in body["valid_values"]["what_failed"]} >= {"captcha"}
    assert "notworking_canary" in body["valid_values"]["agent_type"]
    return {p["field"] for p in body["problems"]}


def test_report_validation(client: TestClient) -> None:
    def post(**body):
        return client.post("/v1/report", json={"target": "xyz.com", **body})

    assert _problems(post(outcome="failed")) == {"what_failed"}
    assert _problems(post(outcome="success", what_failed=["captcha"])) == {"outcome"}
    assert _problems(post(outcome="broken", what_failed=["captcha"])) == {"outcome"}
    assert _problems(post(outcome="failed", what_failed=["meh"])) == {"what_failed"}
    assert _problems(post(**FAIL, agent_type="robot")) == {"agent_type"}
    assert _problems(post(**FAIL, country="XX")) == {"country"}
    assert _problems(post(**FAIL, note="x" * 281)) == {"note"}
    assert _problems(post(**FAIL, colour="red")) == {"colour"}
    assert _problems(client.post("/v1/report", json={"outcome": "failed"})) == {"target"}
    assert _problems(client.post("/v1/report", json={"target": "a b c", **FAIL})) == {"target"}
    assert post(**FAIL, country="sg", agent_type="claude").status_code == 202


def test_report_against_a_bare_service_name_is_rejected(client: TestClient) -> None:
    assert _problems(report(client, "XYZ Booking")) == {"target"}


# ---- privacy and safety invariants ----------------------------------------------


def test_no_ip_stored_and_notes_never_served(client: TestClient, migrated_db_url: str) -> None:
    marker = "zebra-crossing-banana"
    r = report(client, "xyz.com", **FAIL, note=f"{marker} email me at x@y.com")
    assert r.status_code == 202 and marker not in r.text
    assert marker not in status(client, "xyz.com").text

    engine = db.make_engine(migrated_db_url)
    with engine.connect() as conn:
        dump = []
        for table in sa.inspect(engine).get_table_names():
            dump += [str(v) for row in conn.execute(sa.text(f"SELECT * FROM {table}")) for v in row]
        note = conn.execute(sa.text("SELECT note_scrubbed FROM reports")).scalar_one()
    engine.dispose()
    text = " ".join(dump)
    assert "203.0.113" not in text  # the client IP, or its /24
    assert "x@y.com" not in text and "[email]" in note


# ---- usage counters and stats (D64, D65) ------------------------------------------


def test_usage_counters_and_stats(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI, clock: FixedClock
) -> None:
    status(client, "xyz")
    status(client, "clawhub:abc/xyz-booking")
    status(client, "https://not-listed.com/x?y=1")
    status(client, "no such thing")
    report(client, "xyz.com")
    for i in range(10):  # 10 reporters across 10 networks -> past the D24 threshold
        report(client_from(f"198.51.{100 + i}.1"), "popular-unlisted.com")

    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT event, subject, count FROM usage_daily")).all()
        out = render_stats(conn, clock.now())
    counts = {(e, s): n for e, s, n in rows}
    assert counts[("lookup_service", "xyz.com")] == 2
    assert counts[("lookup_path", "clawhub:abc/xyz-booking")] == 1
    assert counts[("lookup_miss", "not-listed.com")] == 1
    assert counts[("lookup_miss", "unparseable")] == 1
    assert counts[("report", "xyz.com")] == 1
    assert ("lookup_service", "popular-unlisted.com") not in counts  # reports aren't lookups
    assert "Reports per lookup: 5.50" in out
    assert "site:popular-unlisted.com" in out
    unlisted = out.split("Unlisted targets agents used")[1].split("\n\n")[0].splitlines()
    assert "       10         10        10        0  site:popular-unlisted.com" in unlisted
    assert "        0          0         0        1  not-listed.com" in unlisted
    assert not any("unparseable" in line or line.endswith(" xyz.com") for line in unlisted)


def test_an_mcp_endpoint_url_finds_its_service(client: TestClient) -> None:
    """Agents know the URL they connect to, not the Registry name (found in a real session)."""
    body = status(client, "https://www.xyz.com/mcp", type="mcp").json()
    assert body["service"]["id"] == "xyz.com"
    assert any(p["id"] == "io.github.xyz/booking-mcp" for p in body["access_paths"])
