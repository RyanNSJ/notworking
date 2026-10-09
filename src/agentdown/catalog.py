"""The steward-curated catalogue (docs/design.md D55, D58-D62).

`catalog/services.yaml` lists services and their access paths. It is loaded into memory
at startup (it's small) and validated strictly: every description here is served to
agents, so the rules are enforced in code, not just by review.
"""

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from agentdown.core.targets import (
    ParsedTarget,
    TargetError,
    parse_target,
    path_matches_prefix,
    route_id,
)

MAX_DESCRIPTION = 200
MAX_NAME = 100
_SERVICE_ID = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
PATH_TYPES = ("site", "route", "mcp", "skill")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class AccessPath:
    type: str
    id: str
    description: str
    url: str | None = None
    no_longer_working_since: dt.date | None = None


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    aliases: tuple[str, ...]
    paths: tuple[AccessPath, ...]  # sorted alphabetically by id (D60)


@dataclass
class Catalog:
    version: int
    services: tuple[Service, ...]
    # indexes, built in __post_init__
    by_id: dict[str, Service] = field(init=False)
    services_for_path: dict[str, list[Service]] = field(init=False)
    path_types: dict[str, str] = field(init=False)
    routes_by_host: dict[str, list[tuple[str, str]]] = field(init=False)
    names: dict[str, list[Service]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {s.id: s for s in self.services}
        self.services_for_path = defaultdict(list)
        self.path_types = {}
        self.routes_by_host = defaultdict(list)
        self.names = defaultdict(list)
        for s in self.services:
            for key in {s.name.lower(), s.id, *s.aliases}:
                self.names[key].append(s)
            for p in s.paths:
                self.services_for_path[p.id].append(s)
                if self.path_types.setdefault(p.id, p.type) != p.type:
                    raise CatalogError(f"path {p.id} is listed with two different types")
                if p.type == "route":
                    host, _, prefix = p.id.partition("/")
                    if (prefix, p.id) not in self.routes_by_host[host]:
                        self.routes_by_host[host].append((prefix, p.id))
        for routes in self.routes_by_host.values():
            routes.sort(key=lambda r: len(r[0]), reverse=True)  # longest prefix first

    def is_listed(self, path_id: str) -> bool:
        return path_id in self.path_types

    def canonical(self, parsed: ParsedTarget) -> tuple[str, str]:
        """(type, id) for a parsed target, mapping a site URL to a listed route if one matches.

        Routes match on the exact host (www. ignored): a subdomain route like
        food.grab.com/sg/en matches only food.grab.com, and a bare-domain route like
        example.com/booking matches only example.com and www.example.com. A host-only
        route (login.example.com) matches every path on that host.
        """
        if parsed.type == "site":
            for prefix, rid in self.routes_by_host.get(parsed.host, []):
                if not prefix or path_matches_prefix(parsed.path, prefix):
                    return "route", rid
        return parsed.type, parsed.id

    def match_names(self, text: str) -> list[Service]:
        """Case-insensitive match on service names, ids and aliases (D61)."""
        found = self.names.get(" ".join(text.lower().split()), [])
        return sorted({s.id: s for s in found}.values(), key=lambda s: s.id)


def _fail(where: str, msg: str) -> CatalogError:
    return CatalogError(f"catalog: {where}: {msg}")


def _description(where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "needs a description")
    text = " ".join(value.split())
    if len(text) > MAX_DESCRIPTION:
        raise _fail(where, f"description is over {MAX_DESCRIPTION} characters")
    return text


def _path(where: str, raw: object) -> AccessPath:
    if not isinstance(raw, dict):
        raise _fail(where, "each path must be a mapping")
    pid, ptype = raw.get("id"), raw.get("type")
    if not isinstance(pid, str) or not pid:
        raise _fail(where, "path needs an id")
    where = f"{where} path {pid}"
    if ptype is not None and ptype not in PATH_TYPES:
        raise _fail(where, f"type must be one of {', '.join(PATH_TYPES)}")
    unknown = set(raw) - {"id", "type", "description", "url", "no_longer_working_since"}
    if unknown:
        raise _fail(where, f"unknown fields: {', '.join(sorted(unknown))}")

    # The id must already be canonical: normalising it must give it back unchanged.
    try:
        parsed = parse_target(pid, "site" if ptype == "route" else ptype)
    except TargetError as e:
        raise _fail(where, f"id isn't a valid target ({e})") from e
    prefix = parsed.path.strip("/") if parsed.type == "site" else ""
    subdomain = parsed.type == "site" and parsed.host != parsed.id
    if ptype == "route" and not (prefix or subdomain):
        raise _fail(
            where,
            "a route needs a path or a subdomain, like example.com/booking or login.example.com",
        )
    if prefix or subdomain:
        canon_type, canon_id = "route", route_id(parsed.host, prefix)
    else:
        canon_type, canon_id = parsed.type, parsed.id
    if canon_id != pid:
        raise _fail(where, f"id isn't canonical; write it as {canon_id}")
    if ptype is not None and ptype != canon_type:
        raise _fail(where, f"id looks like a {canon_type}, not a {ptype}")

    url = raw.get("url")
    if url is not None:
        if canon_type not in ("site", "route"):
            raise _fail(where, "only site and route paths have a url")
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise _fail(where, "url must be an https:// URL")
        if parse_target(url, "site").id != parsed.id:
            raise _fail(where, "url must be on the path's own domain")
    elif canon_type == "site":
        url = f"https://{pid}/"
    elif canon_type == "route":
        url = f"https://{pid}"

    since = raw.get("no_longer_working_since")
    if since is not None and not isinstance(since, dt.date):
        raise _fail(where, "no_longer_working_since must be a date like 2026-10-08")
    return AccessPath(canon_type, pid, _description(where, raw.get("description")), url, since)


def _service(raw: object) -> Service:
    if not isinstance(raw, dict):
        raise _fail("services", "each service must be a mapping")
    sid = raw.get("id")
    if not isinstance(sid, str) or not _SERVICE_ID.match(sid):
        raise _fail("services", f"bad service id {sid!r} (lowercase letters, digits, . and -)")
    where = f"service {sid}"
    unknown = set(raw) - {"id", "name", "aliases", "paths", "source", "country_focus"}
    if unknown:
        raise _fail(where, f"unknown fields: {', '.join(sorted(unknown))}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise _fail(where, f"needs a name of at most {MAX_NAME} characters")
    aliases = raw.get("aliases") or []
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
        raise _fail(where, "aliases must be a list of strings")
    paths = raw.get("paths")
    if not isinstance(paths, list) or not paths:
        raise _fail(where, "needs at least one path")
    parsed = [_path(where, p) for p in paths]
    if len({p.id for p in parsed}) != len(parsed):
        raise _fail(where, "a path is listed twice")
    return Service(
        id=sid,
        name=" ".join(name.split()),
        aliases=tuple(sorted({" ".join(a.lower().split()) for a in aliases})),
        paths=tuple(sorted(parsed, key=lambda p: p.id)),
    )


def parse_catalog(text: str) -> Catalog:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise CatalogError("catalog: must be a mapping")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise CatalogError("catalog: 'version' must be a positive integer")
    services = [_service(s) for s in (raw.get("services") or [])]
    if len({s.id for s in services}) != len(services):
        raise CatalogError("catalog: a service id is used twice")
    return Catalog(version=version, services=tuple(sorted(services, key=lambda s: s.id)))


def load_catalog(path: str | Path) -> Catalog:
    return parse_catalog(Path(path).read_text(encoding="utf-8"))
