"""Target normalisation (docs/design.md D47).

`parse_target` turns whatever an agent passes (a URL, a bare domain, a skill or MCP id)
into a canonical `(type, id)`. It is pure: no DB, no network. Query strings and fragments
are always dropped; for sites, the URL path is returned separately, only so the caller
can match it against listed route prefixes in memory. Raw paths are never stored.

Order of rules, simplest and least ambiguous first:
1. An explicit prefix: `clawhub:`, `skills.sh:`, `mcp:`, or an explicit `type`.
2. A URL with a scheme: a skill-hub listing URL becomes a skill, anything else a site.
3. `io.github.<...>/<name>` becomes an MCP server (the registry's common namespace).
4. Anything else is read as a site. Reverse-DNS MCP names can look like real domains
   (`com.google/...`), so unlisted MCP servers need `mcp:` or `type=mcp`. Listed ids are
   matched exactly before this function is called, so they always resolve correctly.
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import tldextract

# Bundled Public Suffix List snapshot only: never fetch it over the network.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

MAX_INPUT = 2048
_SLUG = r"[a-z0-9][a-z0-9_.-]*"
_SKILL_ID = [
    # ClawHub: slugs are only unique per owner, so the id is owner/slug.
    re.compile(rf"^clawhub:{_SLUG}/{_SLUG}$"),
    # skills.sh: GitHub-sourced <owner>/<repo>/<skill>, or "well-known" <domain>/<skill>.
    re.compile(rf"^skills\.sh:{_SLUG}/{_SLUG}/{_SLUG}$"),
    re.compile(rf"^skills\.sh:{_SLUG}\.[a-z0-9-]+/{_SLUG}$"),
]
_SKILL_HOSTS = {"clawhub.ai", "skills.sh"}
_MCP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9-]+/[A-Za-z0-9][A-Za-z0-9._-]*$")


class TargetError(ValueError):
    """The input can't be understood as a target."""


@dataclass(frozen=True)
class ParsedTarget:
    type: str  # site | route | mcp | skill (route is only produced by catalogue matching)
    id: str
    # Sites only, for in-memory route matching; never stored:
    path: str = ""  # URL path
    host: str = ""  # full host name without a leading "www."


def parse_target(raw: str, type_hint: str | None = None) -> ParsedTarget:
    try:
        return _parse_target(raw, type_hint)
    except TargetError:
        raise
    except ValueError as e:  # urlsplit rejects things like "http://[abc"
        raise TargetError("the target isn't a valid URL or id") from e


def _parse_target(raw: str, type_hint: str | None) -> ParsedTarget:
    s = raw.strip()
    if not s:
        raise TargetError("target is empty")
    if len(s) > MAX_INPUT:
        raise TargetError("target is too long")
    low = s.lower()
    # Other spellings of skill ids seen on the hubs.
    if low.startswith("skills-sh:"):  # ClawHub's prefix for skills.sh entries
        low = "skills.sh:" + low.removeprefix("skills-sh:")
    elif low.startswith("@") and low.count("/") == 1:  # ClawHub's @owner/slug reference
        low = "clawhub:" + low[1:]

    if type_hint == "skill" or low.startswith(("clawhub:", "skills.sh:")):
        return _parse_skill(low)
    if type_hint == "mcp" or low.startswith("mcp:"):
        return _parse_mcp(s[4:] if low.startswith("mcp:") else s)
    if type_hint in (None, "site", "route") and "://" in s:
        host = (urlsplit(s).hostname or "").removeprefix("www.")
        if type_hint is None and host in _SKILL_HOSTS:
            return _parse_skill_url(s, host)
        return _parse_site(s)
    if type_hint is None and low.startswith("io.github."):
        return _parse_mcp(s)
    return _parse_site(s)


def _parse_skill(low: str) -> ParsedTarget:
    if any(pattern.match(low) for pattern in _SKILL_ID):
        return ParsedTarget("skill", low)
    raise TargetError(
        "skill ids look like clawhub:<owner>/<slug>, skills.sh:<owner>/<repo>/<skill> or "
        "skills.sh:<domain>/<skill> (ClawHub's internal clawhub:<id> form isn't accepted)"
    )


def _parse_skill_url(url: str, host: str) -> ParsedTarget:
    parts = [p for p in urlsplit(url).path.lower().split("/") if p]
    if host == "clawhub.ai":
        # https://clawhub.ai/<owner>/skills/<slug> (canonical) or /<owner>/<slug> (redirects)
        if len(parts) >= 3 and parts[1] == "skills":
            return _parse_skill(f"clawhub:{parts[0]}/{parts[2]}")
        if len(parts) == 2:
            return _parse_skill(f"clawhub:{parts[0]}/{parts[1]}")
    elif len(parts) >= 3:  # https://skills.sh/<owner>/<repo>/<skill>
        return _parse_skill(f"skills.sh:{'/'.join(parts[:3])}")
    return _parse_site(url)  # e.g. a hub's own home page


def _parse_mcp(name: str) -> ParsedTarget:
    name = name.strip()
    if not _MCP_NAME.match(name):
        raise TargetError("MCP ids are MCP Registry names, like io.github.<owner>/<server>")
    return ParsedTarget("mcp", name)


def _parse_site(s: str) -> ParsedTarget:
    parts = urlsplit(s if "://" in s else f"https://{s}")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise TargetError("couldn't find a host name in the target")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise TargetError("IP addresses aren't targets; use the site's domain name")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise TargetError("the host name isn't valid") from e
    domain = _EXTRACT(host).top_domain_under_public_suffix
    if not domain:
        raise TargetError("couldn't find a registrable domain (like example.com) in the target")
    return ParsedTarget("site", domain, parts.path or "/", host.removeprefix("www."))


def route_id(host: str, prefix: str) -> str:
    """Canonical route id: `<host>/<prefix>`, or just `<host>` for a whole subdomain.

    `host` is the full host without "www.": the bare domain (example.com/booking) or a
    subdomain (food.grab.com/sg/en, login.example.com).
    """
    prefix = prefix.strip("/")
    return f"{host}/{prefix}" if prefix else host


def path_matches_prefix(path: str, prefix: str) -> bool:
    """True if `path` is `prefix` or below it, on a segment boundary."""
    p, pre = path.rstrip("/"), "/" + prefix.strip("/")
    return p == pre or p.startswith(pre + "/")
