import pytest

from agentdown import networks
from agentdown.networks import SOURCES, Owners, prefixes


def loaded(**lists: list[str]) -> Owners:
    return Owners({**{label: [] for label in SOURCES}, **lists}, missing=set())


def test_prefixes_reads_every_published_format() -> None:
    google = {
        "creationTime": "2026-10-16",
        "prefixes": [{"ipv4Prefix": "66.249.64.0/27"}, {"ipv6Prefix": "2001:4860:4801:10::/64"}],
    }
    aws = {
        "syncToken": "1",
        "prefixes": [{"ip_prefix": "3.5.140.0/22", "region": "ap-southeast-1"}],
        "ipv6_prefixes": [{"ipv6_prefix": "2600:1f00::/24"}],
    }
    assert prefixes(google) == ["66.249.64.0/27", "2001:4860:4801:10::/64"]
    assert prefixes(aws) == ["3.5.140.0/22", "2600:1f00::/24"]


def test_first_listed_owner_wins_inside_a_cloud_range() -> None:
    owners = loaded(
        perplexity=["44.208.221.197/32"], aws=["44.192.0.0/11"], anthropic=["160.79.104.0/21"]
    )
    assert owners.label("44.208.221.197") == "perplexity"  # Perplexity runs on AWS
    assert owners.label("44.208.221.198") == "aws"
    assert owners.label("::ffff:160.79.105.1") == "anthropic"
    assert owners.label("203.0.113.9") == "other"
    assert owners.label("not an ip") == "unknown"


def test_unknown_until_every_list_has_loaded() -> None:
    assert Owners().label("203.0.113.9") == "unknown"


def test_a_failed_refresh_keeps_the_previous_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    def first_fetch(url: str) -> list[str]:
        if "amazonaws" in url:
            raise OSError("unreachable")
        return ["198.51.100.0/24"] if "chatgpt-user" in url else []

    monkeypatch.setattr(networks, "fetch", first_fetch)
    first = Owners().refreshed()
    assert first.missing == {"aws"} and first.label("198.51.100.7") == "openai"
    assert first.label("203.0.113.9") == "unknown"  # might be AWS: its list never loaded

    monkeypatch.setattr(networks, "fetch", lambda url: ["203.0.113.0/24"] * ("amazonaws" in url))
    second = first.refreshed()
    assert second.missing == set() and second.label("203.0.113.9") == "aws"
    assert second.label("160.79.104.10") == "anthropic"  # the fixed range

    def all_down(url: str) -> list[str]:
        raise OSError("unreachable")

    monkeypatch.setattr(networks, "fetch", all_down)
    assert second.refreshed().label("203.0.113.9") == "aws"  # kept from the last good fetch
