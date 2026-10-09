"""Privacy helpers (docs/design.md D21, D27). Nothing here stores anything; callers must never
store or log the raw IP or User-Agent, only the outputs of these functions."""

import hashlib
import ipaddress
import re

MAX_NOTE = 280

_SCRUB = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"(?i)\b(?:https?://|www\.)\S+"), "[url]"),
    (re.compile(r"\+?\d[\d\s().-]{6,}\d"), "[number]"),  # phones, card numbers, ids with digits
    (re.compile(r"\d{5,}"), "[number]"),
    (re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{6,}\b"), "[id]"),
]


def scrub_note(note: str | None) -> str | None:
    """Redact anything that looks like contact details, numbers, ids or links."""
    if note is None:
        return None
    text = " ".join(note.split())
    for pattern, replacement in _SCRUB:
        text = pattern.sub(replacement, text)
    text = text[:MAX_NOTE].strip()
    return text or None


def network_prefix(ip: str | None) -> str:
    """The reporter's network: IPv4 /24 or IPv6 /48. Unparseable input -> 'unknown'."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


_UA_FAMILIES = [
    ("notworking-canary", "notworking_canary"),
    ("claude", "claude"),
    ("chatgpt", "openai"),
    ("openai", "openai"),
    ("python-httpx", "python_httpx"),
    ("python-requests", "python_requests"),
    ("python-urllib", "python_urllib"),
    ("aiohttp", "python_aiohttp"),
    ("curl", "curl"),
    ("node", "node"),
    ("axios", "node"),
    ("go-http-client", "go"),
    ("mozilla", "browser"),
]


def ua_family(user_agent: str | None) -> str:
    """A coarse family for the User-Agent. The raw header is never stored."""
    ua = (user_agent or "").lower()
    for token, family in _UA_FAMILIES:
        if token in ua:
            return family
    return "other" if ua else "none"


def fingerprint(salt: str, *parts: str) -> str:
    return hashlib.sha256("\x1f".join((salt, *parts)).encode()).hexdigest()[:32]
