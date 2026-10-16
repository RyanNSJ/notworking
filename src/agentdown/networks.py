"""Who owns a client's network, from the IP range lists owners publish (lookup log only).

Only the label is stored; the client IP is used in memory only (D21).
"""

import ipaddress
import json
import logging
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "notworking (+https://notworking.io)"
_GOOGLE = "https://developers.google.com/static/search/apis/ipranges/"
# Checked in this order, first match wins: AI fetchers run inside clouds (Perplexity's on AWS).
SOURCES: dict[str, list[str]] = {
    "anthropic": [],  # no list; see FIXED
    "openai": [
        "https://openai.com/chatgpt-user.json",
        "https://openai.com/searchbot.json",
        "https://openai.com/gptbot.json",
    ],
    "perplexity": [
        "https://www.perplexity.com/perplexity-user.json",
        "https://www.perplexity.com/perplexitybot.json",
    ],
    "google": [
        f"{_GOOGLE}googlebot.json",
        f"{_GOOGLE}special-crawlers.json",
        f"{_GOOGLE}user-triggered-fetchers.json",
        f"{_GOOGLE}user-triggered-fetchers-google.json",
    ],
    "bing": ["https://www.bing.com/toolbox/bingbot.json"],
    "aws": ["https://ip-ranges.amazonaws.com/ip-ranges.json"],
    "google_cloud": ["https://www.gstatic.com/ipranges/cloud.json"],
}
# shortcut: Anthropic's outbound range from platform.claude.com/docs/en/api/ip-addresses (no
# JSON list); update by hand if that page changes.
FIXED: dict[str, list[str]] = {"anthropic": ["160.79.104.0/21"]}

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def prefixes(doc: Any) -> list[str]:
    """Every string under a key ending in "prefix" (ipv4Prefix, ipv6Prefix, ip_prefix, ...)."""
    if isinstance(doc, dict):
        return [
            p
            for k, v in doc.items()
            for p in ([v] if isinstance(v, str) and k.lower().endswith("prefix") else prefixes(v))
        ]
    if isinstance(doc, list):
        return [p for v in doc for p in prefixes(v)]
    return []


def fetch(url: str) -> list[str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return prefixes(json.load(response))


class Owners:
    """IP -> owner label. One dict per prefix length, so a lookup is a few dozen dict hits
    rather than a scan of ~20k ranges."""

    def __init__(
        self, lists: dict[str, list[str]] | None = None, missing: set[str] | None = None
    ) -> None:
        self.lists = lists or {}
        self.missing = set(SOURCES) if missing is None else missing  # never loaded
        self._rank = {label: i for i, label in enumerate(SOURCES)}
        self._by_len: dict[tuple[int, int], dict[Network, str]] = {}
        for label, found in self.lists.items():
            for p in found:
                try:
                    net = ipaddress.ip_network(p, strict=False)
                except ValueError:
                    continue
                table = self._by_len.setdefault((net.version, net.prefixlen), {})
                if self._rank.get(label, 99) < self._rank.get(table.get(net, ""), 99):
                    table[net] = label

    def label(self, ip: str | None) -> str:
        """The first owner whose list has `ip`; "other" if none does, "unknown" if a list
        hasn't loaded yet or the IP doesn't parse."""
        try:
            addr = ipaddress.ip_address(ip or "")
        except ValueError:
            return "unknown"
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        hits = [
            table[net]
            for (version, length), table in self._by_len.items()
            if version == addr.version
            and (net := ipaddress.ip_network((addr, length), strict=False)) in table
        ]
        if hits:
            return min(hits, key=lambda h: self._rank.get(h, 99))
        return "unknown" if self.missing else "other"

    def refreshed(self) -> "Owners":
        """Fetch every list again. A label whose fetch fails keeps its previous ranges."""
        lists: dict[str, list[str]] = {}
        missing: set[str] = set()
        for label, urls in SOURCES.items():
            try:
                lists[label] = FIXED.get(label, []) + [p for url in urls for p in fetch(url)]
            except Exception:  # network, HTTP or JSON error
                log.warning("network owner list failed: %s", label)
                lists[label] = self.lists.get(label, [])
                if label in self.missing:
                    missing.add(label)
        return Owners(lists, missing)
