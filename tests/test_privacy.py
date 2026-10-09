import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentdown.core.privacy import fingerprint, network_prefix, scrub_note, ua_family


@pytest.mark.parametrize(
    ("note", "must_not_contain"),
    [
        ("contact me at jane.doe+x@example.com please", "jane.doe"),
        ("call +65 9123 4567 now", "9123"),
        ("card 4111 1111 1111 1111 declined", "4111"),
        ("booking ref ABC12345 failed", "ABC12345"),
        ("see https://site.com/x?token=secret", "secret"),
        ("order 1234567", "1234567"),
    ],
)
def test_scrubber_redacts(note: str, must_not_contain: str) -> None:
    out = scrub_note(note)
    assert out is not None and must_not_contain not in out


def test_scrubber_keeps_plain_text_and_limits_length() -> None:
    assert scrub_note("  captcha   loop on login ") == "captcha loop on login"
    assert len(scrub_note("word " * 200) or "") <= 280
    assert scrub_note(None) is None
    assert scrub_note("   ") is None


emails = st.from_regex(r"[a-z]{1,8}(\.[a-z]{1,5})?@[a-z]{1,8}\.(com|sg|org)", fullmatch=True)


@given(st.text(max_size=40), emails, st.text(max_size=40))
def test_scrubber_never_lets_an_email_through(before: str, email: str, after: str) -> None:
    out = scrub_note(f"{before} {email} {after}") or ""
    assert not re.search(r"[\w.+-]+@[\w-]+\.[a-z]+", out)


def test_network_prefix() -> None:
    assert network_prefix("203.0.113.77") == "203.0.113.0/24"
    assert network_prefix("2001:db8:1234:5678::1") == "2001:db8:1234::/48"
    assert network_prefix("::ffff:203.0.113.5") == "203.0.113.0/24"
    assert network_prefix("testclient") == "unknown"
    assert network_prefix(None) == "unknown"


def test_ua_family() -> None:
    assert ua_family("notworking-canary/0.1 (+https://x)") == "notworking_canary"
    assert ua_family("Mozilla/5.0 (Windows NT 10.0) Chrome/130") == "browser"
    assert ua_family("python-httpx/0.28") == "python_httpx"
    assert ua_family("something-new") == "other"
    assert ua_family(None) == "none"


def test_fingerprint_is_salted_and_stable() -> None:
    a = fingerprint("salt1", "203.0.113.0/24", "", "browser")
    assert a == fingerprint("salt1", "203.0.113.0/24", "", "browser")
    assert a != fingerprint("salt2", "203.0.113.0/24", "", "browser")
    assert len(a) == 32 and "203" not in a
