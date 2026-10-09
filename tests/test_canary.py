"""The canary's pure parts (D51, D52): no network, no curl_cffi."""

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
    assert c(200, b"<script src='/cdn-cgi/challenge-platform/x'></script>") == (False, "bot_block")
    assert c(200, b"", {"cf-mitigated": "challenge"}) == (False, "bot_block")
    assert c(403, b"captcha-delivery.com ... g-recaptcha") == (False, "captcha")
    assert c(404) == (False, "specific_page")
    assert c(401) == (False, "login_auth")
    # A normal page that embeds reCAPTCHA (say, on a login form) isn't a failure.
    assert c(200, b"<form><div class='g-recaptcha'></div></form>") == (True, None)


def test_select_splits_daily_and_websites_deterministically(catalog: Catalog) -> None:
    daily = canary.select(catalog, "daily")
    websites = canary.select(catalog, "websites")
    assert {s.id for s in daily} | {s.id for s in websites} == set(catalog.by_id)
    assert not {s.id for s in daily} & {s.id for s in websites}
    assert all(canary.is_daily(s) for s in daily)
    assert canary.select(catalog, "daily") == daily  # same order every run
    assert len(canary.select(catalog, "daily", sample=1)) == 1


def test_public_host_refuses_private_and_internal_addresses() -> None:
    def fake(ips):
        return lambda host, port: [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in ips]

    assert canary.public_host("ok", fake(["93.184.216.34"]))
    for ip in ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"]:
        assert not canary.public_host("x", fake([ip])), ip
    assert not canary.public_host("mixed", fake(["93.184.216.34", "10.0.0.1"]))

    def boom(host, port):
        raise OSError("no such host")

    assert not canary.public_host("nope", boom)


def test_failures_groups_one_report_per_path() -> None:
    results = [
        Result("s", "a.com", "site", "declared", False, "bot_block"),
        Result("s", "a.com", "site", "chrome", False, "captcha"),
        Result("s", "a.com", "site", "chrome", False, "captcha"),
        Result("s", "b.com", "site", "declared", True),
    ]
    assert canary.failures(results) == {"a.com": ["bot_block", "captcha"]}
