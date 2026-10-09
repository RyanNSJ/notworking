"""The canary's pure parts (D51, D52): no network, no curl_cffi."""

import datetime as dt
import socket

from agentdown import canary
from agentdown.canary import Fetched, Result
from agentdown.catalog import Catalog


def test_classify_site_maps_responses_to_what_failed() -> None:
    def c(status, body=b"", headers=None):
        return canary.classify_site(Fetched(status, headers or {}, body))[:2]

    assert c(200, b"<html>Welcome</html>") == (True, None)
    assert c(None) == (False, "site_unreachable")  # DNS, connect, timeout
    assert c(502) == (False, "site_unreachable")
    assert c(403) == (False, "bot_block")
    assert c(429) == (False, "bot_block")
    assert c(503, b"<title>Just a moment...</title>") == (False, "bot_block")
    assert c(200, b"<script>window._cf_chl_opt={}</script>") == (False, "bot_block")
    # Cloudflare's background script on an ordinary page isn't a block (found in a real run).
    assert c(
        200, b"<title>Moz</title><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"
    ) == (True, None)
    assert c(200, b"", {"cf-mitigated": "challenge"}) == (False, "bot_block")
    assert c(403, b"captcha-delivery.com ... g-recaptcha") == (False, "captcha")
    assert c(404) == (False, "specific_page")
    assert c(401) == (False, "login_auth")
    # A normal page that embeds reCAPTCHA (say, on a login form) isn't a failure.
    assert c(200, b"<form><div class='g-recaptcha'></div></form>") == (True, None)
    # A large 200 page with a vendor's marker is the real page (challenge pages are small).
    assert c(200, b"awswaf" + b"x" * 200_000) == (True, None)
    assert c(200, b"<title>Human Verification</title> awswaf") == (False, "bot_block")


def test_select_splits_daily_and_websites_deterministically(catalog: Catalog) -> None:
    daily = canary.select(catalog, "daily")
    websites = canary.select(catalog, "websites")
    assert {s.id for s in daily} | {s.id for s in websites} == set(catalog.by_id)
    assert not {s.id for s in daily} & {s.id for s in websites}
    assert all(canary.is_daily(s) for s in daily)
    assert canary.select(catalog, "daily") == daily  # same order every run
    assert len(canary.select(catalog, "daily", sample=1)) == 1


def test_scheduled_rotates_every_website_once_per_cycle(catalog: Catalog) -> None:
    start = dt.date(2026, 10, 1)
    days = [canary.select(catalog, "scheduled", day=start + dt.timedelta(d)) for d in range(30)]
    daily = {s.id for s in canary.select(catalog, "daily")}
    assert all(daily <= {s.id for s in day} for day in days)  # the daily set every day
    seen = [s.id for day in days for s in day if s.id not in daily]
    assert sorted(seen) == sorted(s.id for s in canary.select(catalog, "websites"))  # each once


def test_host_problem_refuses_private_addresses_and_names_dns_failures() -> None:
    def fake(ips):
        return lambda host, port: [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in ips]

    assert canary.host_problem("ok", fake(["93.184.216.34"])) is None
    for ip in ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"]:
        assert "private" in (canary.host_problem("x", fake([ip])) or ""), ip
    assert canary.host_problem("mixed", fake(["93.184.216.34", "10.0.0.1"]))

    def boom(host, port):
        raise OSError("no such host")

    assert canary.host_problem("nope", boom) == "DNS lookup failed for nope"


def test_failures_groups_one_report_per_path() -> None:
    results = [
        Result("s", "a.com", "site", "declared", False, "bot_block"),
        Result("s", "a.com", "site", "chrome", False, "captcha"),
        Result("s", "a.com", "site", "chrome", False, "captcha"),
        Result("s", "b.com", "site", "declared", True),
    ]
    assert canary.failures(results) == {"a.com": ["bot_block", "captcha"]}


def test_report_problems_flag_anything_but_accepted_or_rate_limited() -> None:
    assert canary.report_problems({"reported": None}) == {}  # a dry run
    assert canary.report_problems({"reported": {202: 9, 429: 1}}) == {}
    assert canary.report_problems({"reported": {"202": 3, "500": 2, "0": 1}}) == {500: 2, 0: 1}
