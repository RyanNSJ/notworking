import datetime as dt
from pathlib import Path

import pytest

from agentdown.catalog import Catalog, CatalogError, load_catalog, parse_catalog
from agentdown.core.targets import parse_target


def test_fixture_loads_sorted_and_indexed(catalog: Catalog) -> None:
    xyz = catalog.by_id["xyz.com"]
    assert [p.id for p in xyz.paths] == sorted(p.id for p in xyz.paths)  # D60
    assert xyz.aliases == ("xyz", "xyz booking")  # lowercased, whitespace collapsed
    assert catalog.path_types["xyz.com/booking"] == "route"
    assert catalog.path_types["io.github.xyz/booking-mcp"] == "mcp"
    retired = next(p for p in xyz.paths if p.id == "clawhub:def/xyz")
    assert retired.no_longer_working_since == dt.date(2026, 9, 1)
    site = next(p for p in xyz.paths if p.id == "xyz.com")
    assert site.url == "https://www.xyz.com/"
    route = next(p for p in xyz.paths if p.id == "xyz.com/booking")
    assert route.url == "https://xyz.com/booking"
    assert {s.id for s in catalog.services_for_path["clawhub:shared/browser"]} == {
        "example.org",
        "other.net",
    }


def test_canonical_maps_urls_to_listed_routes(catalog: Catalog) -> None:
    assert catalog.canonical(parse_target("https://xyz.com/booking/42?x=1")) == (
        "route",
        "xyz.com/booking",
    )
    assert catalog.canonical(parse_target("https://xyz.com/about")) == ("site", "xyz.com")


def test_subdomain_routes_match_only_their_host(catalog: Catalog) -> None:
    def canon(url: str) -> tuple[str, str]:
        return catalog.canonical(parse_target(url))

    assert canon("https://login.xyz.com/any/page?next=/x") == ("route", "login.xyz.com")
    assert canon("https://www.xyz.com/booking/1") == ("route", "xyz.com/booking")  # www ignored
    assert canon("https://m.xyz.com/booking") == ("site", "xyz.com")  # other subdomains: site
    assert canon("https://login.xyz.com.evil.com/") == ("site", "evil.com")


def test_name_matching(catalog: Catalog) -> None:
    assert [s.id for s in catalog.match_names("  XYZ   Booking ")] == ["xyz.com"]
    assert [s.id for s in catalog.match_names("shared name")] == ["example.org", "other.net"]
    assert catalog.match_names("nothing") == []


def test_repo_catalog_is_valid() -> None:
    load_catalog(Path(__file__).parent.parent / "catalog" / "services.yaml")


def _one(path: str, extra_service: str = "") -> str:
    return f"""
version: 1
services:
  - id: s.com
    name: S
{extra_service}
    paths:
{path}
"""


@pytest.mark.parametrize(
    ("path_yaml", "message"),
    [
        ("      - id: s.com\n        description: " + "x" * 201, "over 200"),
        ("      - id: www.s.com\n        description: d", "isn't canonical"),
        ("      - id: S.com\n        description: d", "isn't canonical"),
        ("      - id: s.com/booking/\n        type: route\n        description: d", "canonical"),
        ("      - id: www.s.com/booking\n        type: route\n        description: d", "canonical"),
        ("      - id: Login.S.com\n        type: route\n        description: d", "canonical"),
        ("      - id: s.com\n        type: route\n        description: d", "needs a path"),
        ("      - id: s.com\n        type: mcp\n        description: d", "valid target"),
        ("      - id: s.com\n        description: d\n        url: http://s.com/", "https"),
        ("      - id: s.com\n        description: d\n        url: https://evil.com/", "own domain"),
        (
            "      - id: clawhub:a/b\n        description: d\n        url: https://s.com/",
            "only site",
        ),
        (
            "      - id: s.com\n        description: d\n        no_longer_working_since: soon",
            "date",
        ),
        ("      - id: s.com\n        description: ''", "needs a description"),
        ("      - id: s.com\n        description: d\n        colour: red", "unknown fields"),
        (
            "      - id: s.com\n        description: d\n      - id: s.com\n        description: d",
            "twice",
        ),
    ],
)
def test_invalid_paths_are_rejected(path_yaml: str, message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        parse_catalog(_one(path_yaml))


def test_subdomain_route_ids_are_valid() -> None:
    text = _one(
        "      - {id: food.s.com/sg/en, description: d}\n"
        "      - {id: login.s.com, type: route, description: d, url: 'https://login.s.com/in'}"
    )
    paths = {p.id: p for p in parse_catalog(text).services[0].paths}
    assert paths["food.s.com/sg/en"].type == "route"
    assert paths["food.s.com/sg/en"].url == "https://food.s.com/sg/en"
    assert paths["login.s.com"].url == "https://login.s.com/in"


def test_empty_catalogue_is_valid() -> None:
    assert parse_catalog("version: 1\nservices: []\n").services == ()


def test_same_path_with_two_types_is_rejected() -> None:
    text = """
version: 1
services:
  - id: a.com
    name: A
    paths:
      - {id: a.com/x, type: route, description: d}
  - id: b.com
    name: B
    paths:
      - {id: a.com, description: d}
      - {id: a.com/x, type: route, description: d}
"""
    parse_catalog(text)  # same type twice is fine
    with pytest.raises(CatalogError, match="valid target|two different types|looks like"):
        parse_catalog(
            text.replace("{id: a.com, description: d}", "{id: a.com, type: mcp, description: d}")
        )
