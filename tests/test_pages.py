"""Human pages, badge and dataset (M5)."""

import csv
import datetime as dt
import io
import re
from collections.abc import Callable
from typing import cast

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentdown import jobs
from agentdown.api.v1 import BADGE, BADGE_LEFT
from agentdown.core.clock import FixedClock
from agentdown.db.schema import checks_hourly
from agentdown.service import AppState
from tests.conftest import PUBLIC_URL

FAIL = {"outcome": "failed", "what_failed": ["bot_block"]}


def spike(client_from: Callable[[str], TestClient], app: FastAPI, target: str, n: int = 5) -> None:
    for i in range(n):
        client_from(f"198.51.{100 + i}.1").post("/v1/report", json={"target": target, **FAIL})
    jobs.tick(cast(AppState, app.state))


def board_names(html: str) -> list[str]:
    return re.findall(r'class="row-name" href="/service/[^"]+">([^<]+)<', html)


def test_home_has_install_and_a_board(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert f"{PUBLIC_URL}/mcp" in r.text and "claude plugin install notworking" in r.text
    assert "Why install it" in r.text and 'a CAPTCHA or "are you human?" check' in r.text
    assert board_names(r.text) == ["Example Org", "Other Net", "XYZ Booking"]  # cold start: A-Z


def test_board_ranks_raised_then_looked_up(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    client_from("192.0.2.1").post("/v1/report", json={"target": "example.org", **FAIL})
    client.get("/v1/status", params={"target": "other.net"})  # a lookup, not a report
    spike(client_from, app, "xyz.com")
    html = client.get("/").text
    # Raised first; then quiet services by lookups (a lone report doesn't lift Example Org).
    assert board_names(html) == ["XYZ Booking", "Other Net", "Example Org"]
    assert "xyz.com &middot; bot_block &middot; 5 in 24h" in html
    assert 'class="bar-hot"' in html  # the last hour is coloured while raised


def test_service_page(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    spike(client_from, app, "clawhub:abc/xyz-booking")
    r = client.get("/service/xyz.com")
    assert r.status_code == 200
    ids = re.findall(r'<div class="pid">(?:<a [^>]+>)?([^<]+?)(?:</a>)? <span', r.text)
    assert ids == sorted(ids) and len(ids) == 6  # alphabetical, every listed path
    assert "Agents are reporting more problems with XYZ Booking than usual." in r.text
    assert "No longer working as of 2026-09-01." in r.text
    assert "/v1/badge?target=xyz.com" in r.text
    assert client.get("/service/nope.example").status_code == 404


def test_privacy_and_terms_pages(client: TestClient) -> None:
    privacy = client.get("/privacy")
    assert privacy.status_code == 200 and "IP addresses" in privacy.text
    assert client.get("/terms").status_code == 200
    assert 'href="/privacy"' in client.get("/").text  # linked from every page's footer


def test_methodology_numbers_come_from_the_detector(client: TestClient) -> None:
    text = client.get("/methodology").text
    assert "less than 1 time in 1,000" in text and "1 in 1,000,000" in text
    assert "none over 30%" in text and "30 minutes" in text


def test_notes_never_appear_on_pages(client: TestClient) -> None:
    marker = "zebra-crossing-banana"
    client.post("/v1/report", json={"target": "xyz.com", **FAIL, "note": marker})
    for page in ["/", "/services", "/service/xyz.com", "/dataset/daily.csv"]:
        assert marker not in client.get(page).text


def test_badges(client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI) -> None:
    def badge(target: str, **params: str) -> str:
        r = client.get("/v1/badge", params={"target": target, **params})
        assert r.headers["content-type"] == "image/svg+xml"
        return r.text

    assert "no reported issues" in badge("xyz.com")
    assert "no reported issues" in badge("unlisted-shop.com")  # unlisted targets too (D71)
    assert ">unknown<" in badge("no such thing")
    spike(client_from, app, "clawhub:abc/xyz-booking")
    assert "issues reported" in badge("clawhub:abc/xyz-booking")
    assert "issues reported" in badge("XYZ Booking")  # a service shows its most raised path
    assert "issues reported" in badge("xyz.com")  # the service id means the whole service
    assert "no reported issues" in badge("xyz.com", type="site")  # the site path is quiet
    assert ">unknown<" in badge("http://[abc")  # malformed, not a 500


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def test_badge_colours_meet_wcag_aa_with_white_text() -> None:
    for color in [BADGE_LEFT, *(c for _, c in BADGE.values())]:
        assert 1.05 / (_luminance(color) + 0.05) >= 4.5, color


def test_dataset_csvs(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI, clock: FixedClock
) -> None:
    spike(client_from, app, "xyz.com")
    client.post("/v1/report", json={"target": "unlisted-shop.com", **FAIL})
    assert list(csv.reader(io.StringIO(client.get("/dataset/daily.csv").text)))[1:] == []  # today

    clock.advance(dt.timedelta(days=1))
    daily = list(csv.DictReader(io.StringIO(client.get("/dataset/daily.csv").text)))
    assert daily == [
        {
            "date": "2026-10-08",
            "service_ids": "xyz.com",
            "path_type": "site",
            "path_id": "xyz.com",
            "failure_reports": "5",
            "unique_failure_reporters": "5",
            "what_failed": "bot_block=5",
        }
    ]  # the unlisted target is not published
    transitions = list(csv.DictReader(io.StringIO(client.get("/dataset/transitions.csv").text)))
    assert [(t["path_id"], t["from_status"], t["to_status"], t["n"]) for t in transitions] == [
        ("xyz.com", "no_reported_issues", "issues_reported", "5")
    ]


def test_services_search(client: TestClient) -> None:
    def names(q: str) -> list[str]:
        return board_names(client.get("/services", params={"q": q}).text)

    quiet = client.get("/services").text
    assert names("") == []  # nothing reported: no charted rows, just the A-Z list
    assert re.findall(r'<li><a href="/service/[^"]+">([^<]+)</a>', quiet) == [
        "Example Org",
        "Other Net",
        "XYZ Booking",
    ]
    assert names("xyz") == ["XYZ Booking"]  # name, id or alias
    assert names("booking-mcp") == ["XYZ Booking"]  # an access-path id
    assert names("SHARED/browser") == ["Example Org", "Other Net"]  # case-insensitive
    assert names("nothing like this") == []
    assert "&lt;script&gt;" in client.get("/services", params={"q": "<script>"}).text  # escaped


def test_services_lists_reported_first(
    client: TestClient, client_from: Callable[[str], TestClient], app: FastAPI
) -> None:
    spike(client_from, app, "other.net", n=1)
    html = client.get("/services").text
    assert board_names(html) == ["Other Net"]  # charted: reported in the last 24h
    rest = re.findall(r'<li><a href="/service/[^"]+">([^<]+)</a>', html)
    assert rest == ["Example Org", "XYZ Booking"]


def test_checks_today_are_shown_as_context(client: TestClient) -> None:
    for _ in range(2):
        client.get("/v1/status", params={"target": "xyz.com"})
    assert "2 checks today" in client.get("/").text
    assert "Agents checked it 2 times today." in client.get("/service/xyz.com").text
    assert "check" not in client.get("/service/other.net").text.split("<main>")[1].split("<h2")[0]


def test_header_counts_checks_and_reports_over_rolling_windows(
    client: TestClient,
    client_from: Callable[[str], TestClient],
    app: FastAPI,
    clock: FixedClock,
) -> None:
    client.get("/v1/status", params={"target": "xyz.com"})
    client.get("/v1/status", params={"target": "unlisted.example"})
    client_from("192.0.2.1").post("/v1/report", json={"target": "example.org", **FAIL})

    def counter() -> str:
        html = client.get("/privacy").text
        return html.split('class="counter">')[1].split("</p>")[0]

    assert counter() == (
        "Last 24h: checks 2 &middot; reports 1 &nbsp;|&nbsp; "
        "Last 7 days: checks 2 &middot; reports 1"
    )
    clock.advance(dt.timedelta(hours=23, minutes=59))
    assert "Last 24h: checks 2 &middot; reports 1" in counter()
    clock.advance(dt.timedelta(hours=2))  # past 24h, and past the lookups' hour
    assert "Last 24h: checks 0 &middot; reports 0" in counter()
    assert "Last 7 days: checks 2 &middot; reports 1" in counter()
    clock.advance(dt.timedelta(days=8))
    jobs.tick(cast(AppState, app.state))
    assert "Last 7 days: checks 0 &middot; reports 0" in counter()
    with app.state.engine.connect() as conn:  # old hourly counts are pruned
        assert conn.execute(sa.select(sa.func.count()).select_from(checks_hourly)).scalar() == 0


def test_robots_and_sitemap_for_search_engines(client: TestClient) -> None:
    robots = client.get("/robots.txt").text
    assert "Disallow: /v1/" in robots and f"Sitemap: {PUBLIC_URL}/sitemap.xml" in robots
    sitemap = client.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert f"<loc>{PUBLIC_URL}/service/xyz.com</loc>" in sitemap.text
    assert sitemap.text.count("<url>") == 6 + 3  # the fixed pages plus one per service


def test_service_page_is_findable_and_tells_agents_what_to_do(client: TestClient) -> None:
    html = client.get("/service/xyz.com").text
    assert "<title>Is XYZ Booking down or blocking AI agents? · NotWorking</title>" in html
    assert f"{PUBLIC_URL}/v1/status?target=xyz.com" in html and f"{PUBLIC_URL}/mcp" in html
