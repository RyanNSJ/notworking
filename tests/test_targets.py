import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentdown.core.targets import (
    TargetError,
    parse_target,
    path_matches_prefix,
    route_id,
)


@pytest.mark.parametrize(
    ("raw", "type_", "id_"),
    [
        ("https://www.klook.com/en-SG/activity/123?ref=abc#top", "site", "klook.com"),
        ("klook.com", "site", "klook.com"),
        ("KLOOK.COM/path", "site", "klook.com"),
        ("shop.example.co.uk", "site", "example.co.uk"),
        ("https://xn--bcher-kva.de/", "site", "xn--bcher-kva.de"),
        ("bücher.de", "site", "xn--bcher-kva.de"),
        ("clawhub:Owner/My-Skill", "skill", "clawhub:owner/my-skill"),
        ("skills.sh:owner/repo/skill", "skill", "skills.sh:owner/repo/skill"),
        ("https://clawhub.ai/owner/slug?tab=readme", "skill", "clawhub:owner/slug"),
        ("https://clawhub.ai/Steipete/skills/notion", "skill", "clawhub:steipete/notion"),
        ("@steipete/github", "skill", "clawhub:steipete/github"),
        (
            "skills-sh:stripe/ai/stripe-best-practices",
            "skill",
            "skills.sh:stripe/ai/stripe-best-practices",
        ),
        (
            "skills.sh:docs.stripe.com/stripe-directory",
            "skill",
            "skills.sh:docs.stripe.com/stripe-directory",
        ),
        (
            "https://www.skills.sh/makenotion/skills/notion-cli",
            "skill",
            "skills.sh:makenotion/skills/notion-cli",
        ),
        ("io.github.SeaMCP/shopee", "mcp", "io.github.SeaMCP/shopee"),  # registry names keep case
        ("https://skills.sh/owner/repo/skill", "skill", "skills.sh:owner/repo/skill"),
        ("https://clawhub.ai/", "site", "clawhub.ai"),
        ("io.github.someone/server", "mcp", "io.github.someone/server"),
        ("mcp:com.google/maps", "mcp", "com.google/maps"),
    ],
)
def test_parse(raw: str, type_: str, id_: str) -> None:
    parsed = parse_target(raw)
    assert (parsed.type, parsed.id) == (type_, id_)


def test_site_path_is_kept_for_route_matching_but_never_the_query() -> None:
    parsed = parse_target("https://xyz.com/booking/123?email=a@b.com")
    assert parsed.path == "/booking/123"
    assert "?" not in parsed.id and "@" not in parsed.id


def test_type_hint_overrides_guessing() -> None:
    assert parse_target("com.google/maps", "mcp").type == "mcp"
    assert parse_target("com.google/maps").type == "site"  # brand TLD: ambiguous without a hint


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "localhost",
        "192.168.1.1",
        "http://[::1]/",
        "clawhub:nope",
        "mcp:not a name",
        "x" * 3000,
    ],
)
def test_rejects(raw: str) -> None:
    with pytest.raises(TargetError):
        parse_target(raw)


def test_routes() -> None:
    assert route_id("xyz.com", "/booking/") == "xyz.com/booking"
    assert path_matches_prefix("/booking", "booking")
    assert path_matches_prefix("/booking/123", "/booking")
    assert not path_matches_prefix("/bookings", "/booking")


hosts = st.from_regex(r"[a-z]{1,10}\.(com|org|co\.uk|sg|io)", fullmatch=True)
paths = st.from_regex(r"(/[a-zA-Z0-9_-]{0,8}){0,3}", fullmatch=True)
queries = st.from_regex(r"(\?[a-z]{1,5}=[a-zA-Z0-9@.]{0,10})?(#[a-z]{0,5})?", fullmatch=True)


@given(hosts, paths, queries)
def test_site_ids_are_idempotent_and_never_carry_paths_or_queries(
    host: str, path: str, query: str
) -> None:
    parsed = parse_target(f"https://www.{host}{path}{query}")
    assert parse_target(parsed.id).id == parsed.id
    assert not set("/?#@") & set(parsed.id)
