"""The canary (docs/design.md D51): one ordinary reporter that checks listed access paths.

Runs from GitHub Actions. It reads only catalog/services.yaml, never the database, and talks
to NotWorking over its public API. It reports failures only, as `agent_type:
notworking_canary`, with no extra weight. It never runs skill code, never completes a
transaction, refuses private and internal addresses, and makes at most one request per
second per host. Without `--report` it's a dry run: results go to a JSONL file only.
"""

import datetime as dt
import hashlib
import ipaddress
import json
import socket
import threading
import time
import urllib.parse
import urllib.robotparser
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from agentdown import __version__
from agentdown.catalog import AccessPath, Catalog, Service

USER_AGENT = f"notworking-canary/{__version__} (+https://notworking.io/methodology)"
REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
TIMEOUT = 20
MAX_REDIRECTS = 5
MAX_BODY = 512 * 1024
REAL_PAGE_BYTES = 100 * 1024  # challenge pages seen so far: 2-30 KB; real pages: far more
HOST_GAP = 1.0  # seconds between requests to one host
ROTATION_DAYS = 30  # each website-only service is checked once in this many days
MAX_REPORTS = 90  # per run: at 72 s apart, that's under 2 hours and the job's time limit

# Markers of a block or challenge page, by vendor. Checked on any response.
CHALLENGE_MARKERS = [
    # Not "/cdn-cgi/challenge-platform": Cloudflare adds that script to ordinary pages too.
    ("cloudflare", b"_cf_chl_opt"),
    ("cloudflare", b"Just a moment..."),
    ("cloudflare", b"Attention Required! | Cloudflare"),
    ("datadome", b"captcha-delivery.com"),
    ("perimeterx", b"px-captcha"),
    ("perimeterx", b"Access to this page has been denied"),
    ("akamai", b"Reference&#32;&#35;"),
    ("akamai", b"errors.edgesuite.net"),
    ("imperva", b"_Incapsula_Resource"),
    ("imperva", b"Incapsula incident"),
    ("aws-waf", b"awswaf"),
    ("vercel", b"Vercel Security Checkpoint"),
]
CAPTCHA_MARKERS = [b"g-recaptcha", b"h-captcha", b"cf-turnstile", b"hcaptcha.com/1/api.js"]


@dataclass
class Result:
    service_id: str
    path_id: str
    type: str
    variant: str
    ok: bool
    what_failed: str | None = None
    status: int | None = None
    detail: str = ""
    ms: int = 0


@dataclass
class Fetched:
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""


# ---- selection ---------------------------------------------------------------


def _order(service_id: str) -> int:
    return int.from_bytes(hashlib.sha256(service_id.encode()).digest()[:8], "big")


def is_daily(service: Service) -> bool:
    """Services with MCP servers or skills are checked every day; the rest in rotation."""
    return any(p.type in ("mcp", "skill") for p in service.paths)


def select(
    catalog: Catalog, which: str, sample: int | None = None, day: dt.date | None = None
) -> list[Service]:
    """`daily`, `websites`, or `scheduled`: the daily set plus that day's share of websites,
    so each website is checked about once every ROTATION_DAYS days."""
    if which == "scheduled":
        slot = (day or dt.datetime.now(dt.UTC).date()).toordinal() % ROTATION_DAYS
        pool = [s for s in catalog.services if is_daily(s) or _order(s.id) % ROTATION_DAYS == slot]
    else:
        pool = [s for s in catalog.services if is_daily(s) == (which == "daily")]
    pool.sort(key=lambda s: _order(s.id))
    return pool[:sample] if sample else pool


# ---- safety and politeness -----------------------------------------------------


def host_problem(host: str, resolve: Callable[..., Any] = socket.getaddrinfo) -> str | None:
    """Why the canary won't connect to a host, or None if every address it has is public."""
    try:
        addrs = {info[4][0] for info in resolve(host, None)}
    except OSError:
        return f"DNS lookup failed for {host}"
    if not addrs or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs):
        return f"refused: {host} resolves to a private or internal address"
    return None


class Pacer:
    """At most one request per HOST_GAP seconds to any one host, across threads."""

    def __init__(self) -> None:
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + HOST_GAP
        time.sleep(max(0.0, start - now))


# ---- classification (D52) -------------------------------------------------------


def classify_site(f: Fetched) -> tuple[bool, str | None, str]:
    """(ok, what_failed, detail) for a fetched page."""
    if f.status is None:
        return False, "site_unreachable", f.error
    body = f.body[:MAX_BODY]
    vendor = next((v for v, m in CHALLENGE_MARKERS if m in body), None)
    if f.status == 200 and len(body) > REAL_PAGE_BYTES:
        vendor = None  # challenge pages are small; a big 200 is the real page with a script
    if f.headers.get("cf-mitigated", "").lower() == "challenge":
        vendor = "cloudflare"
    blocked = f.status in (403, 429) or (f.status == 503 and vendor) or vendor
    if blocked:
        kind = "captcha" if any(m in body for m in CAPTCHA_MARKERS) else "bot_block"
        return False, kind, f"{f.status} {vendor or 'blocked'}"
    if f.status >= 500:
        return False, "site_unreachable", f"{f.status}"
    if f.status == 401:
        return False, "login_auth", "401"
    if f.status in (404, 410):
        return False, "specific_page", f"{f.status}"
    if f.status >= 400:
        return False, "bot_block", f"{f.status}"
    return True, None, f"{f.status}"


# ---- fetching ---------------------------------------------------------------------


class Fetcher:
    """HTTP for the canary: curl_cffi, manual redirects with the address check on each hop."""

    def __init__(self, pacer: Pacer) -> None:
        from curl_cffi import requests as cr  # only the canary needs it (dependency group)

        self._cr = cr
        self._pacer = pacer
        self._local = threading.local()

    def _session(self, variant: str):
        sessions = self._local.__dict__.setdefault("sessions", {})
        if variant not in sessions:
            if variant == "chrome":
                sessions[variant] = self._cr.Session(impersonate="chrome")
            else:
                sessions[variant] = self._cr.Session(headers={"User-Agent": USER_AGENT})
        return sessions[variant]

    def request(self, method: str, url: str, variant: str = "declared", **kw: Any) -> Fetched:
        for _ in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(url).hostname or ""
            if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
                return Fetched(None, error=f"refused: not a web URL ({url[:80]})")
            problem = host_problem(host)
            if problem:
                return Fetched(None, error=problem)
            self._pacer.wait(host)
            try:
                r = self._session(variant).request(
                    method, url, allow_redirects=False, timeout=TIMEOUT, **kw
                )
            except Exception as e:  # DNS, connect, TLS, timeout
                return Fetched(None, error=type(e).__name__ + ": " + str(e)[:160])
            headers = {k.lower(): v for k, v in r.headers.items()}
            if r.status_code in (301, 302, 303, 307, 308) and "location" in headers:
                url = urllib.parse.urljoin(url, headers["location"])
                if r.status_code == 303:
                    method, kw = "GET", {}
                continue
            return Fetched(r.status_code, headers, r.content[:MAX_BODY])
        return Fetched(None, error="too many redirects")


# ---- checks -----------------------------------------------------------------------


def _timed(fn: Callable[[], tuple[bool, str | None, int | None, str]]) -> tuple:
    start = time.monotonic()
    ok, what, status, detail = fn()
    return ok, what, status, detail, int((time.monotonic() - start) * 1000)


def check_site(fx: Fetcher, url: str, variants: Iterable[str]) -> list[tuple]:
    out = []
    parts = urllib.parse.urlsplit(url)
    for variant in variants:

        def run(variant: str = variant) -> tuple[bool, str | None, int | None, str]:
            if variant == "declared":
                robots = fx.request("GET", f"https://{parts.netloc}/robots.txt", "declared")
                if robots.status == 200:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(robots.body.decode("utf-8", "replace").splitlines())
                    if not rp.can_fetch(USER_AGENT, url):
                        # Honoured, not reported: an agent wouldn't be stopped by it (D75).
                        return True, None, 200, "skipped: robots.txt disallows our user-agent"
            f = fx.request("GET", url, variant)
            ok, what, detail = classify_site(f)
            return ok, what, f.status, detail

        out.append((variant, *_timed(run)))
    return out


def mcp_remote(fx: Fetcher, name: str) -> str | None:
    f = fx.request("GET", f"{REGISTRY}/{urllib.parse.quote(name, safe='')}/versions/latest")
    if f.status != 200:
        return None
    remotes = [  # a {placeholder} URL is filled in per customer
        r for r in json.loads(f.body)["server"].get("remotes", []) if "{" not in r.get("url", "{")
    ]
    http = [r for r in remotes if r.get("type") == "streamable-http"] or remotes
    return http[0]["url"] if http else None


def check_mcp(fx: Fetcher, name: str) -> list[tuple]:
    def run() -> tuple[bool, str | None, int | None, str]:
        url = mcp_remote(fx, name)
        if not url:
            return False, "mcp_error", None, "no remote URL in the MCP Registry"
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "notworking-canary", "version": __version__},
            },
        }
        f = fx.request(
            "POST",
            url,
            json=body,
            headers={"Accept": "application/json, text/event-stream"},
        )
        if f.status is None:
            return False, "mcp_error", None, f.error
        if f.status == 401:  # OAuth sign-in or an API key the Registry entry asks for
            return True, None, 401, "sign-in required (reachable)"
        if f.status == 200 and (b'"result"' in f.body or "mcp-session-id" in f.headers):
            return True, None, 200, "initialized"
        return False, "mcp_error", f.status, f"initialize returned {f.status}"

    return [("declared", *_timed(run))]


def check_skill(fx: Fetcher, skill_id: str) -> list[tuple]:
    def run() -> tuple[bool, str | None, int | None, str]:
        hub, _, rest = skill_id.partition(":")
        if hub == "clawhub":
            owner, slug = rest.split("/", 1)
            q = urllib.parse.urlencode({"owner": owner, "path": "SKILL.md"})
            f = fx.request("GET", f"https://clawhub.ai/api/v1/skills/{slug}/file?{q}")
            ok = f.status == 200 and f.body.lstrip().startswith(b"---") and b"name:" in f.body
            return (
                ok,
                None if ok else "skill_failed",
                f.status,
                "SKILL.md parsed" if ok else (f.error or f"SKILL.md {f.status}"),
            )
        f = fx.request("GET", f"https://skills.sh/{rest}")
        ok = f.status == 200 and b"First Seen" in f.body
        return (
            ok,
            None if ok else "skill_failed",
            f.status,
            "listed" if ok else (f.error or "listing not found"),
        )

    return [("declared", *_timed(run))]


def check_path(fx: Fetcher, service: Service, path: AccessPath, chrome: bool) -> list[Result]:
    if path.type in ("site", "route"):
        url = path.url or f"https://{path.id}/"
        rows = check_site(fx, url, ["declared", "chrome"] if chrome else ["declared"])
    elif path.type == "mcp":
        rows = check_mcp(fx, path.id)
    else:
        rows = check_skill(fx, path.id)
    return [
        Result(service.id, path.id, path.type, v, ok, what, status, detail, ms)
        for v, ok, what, status, detail, ms in rows
    ]


# ---- reporting and the run ---------------------------------------------------------


def failures(results: list[Result]) -> dict[str, list[str]]:
    """One report per failed path: every distinct what_failed seen across its variants."""
    out: dict[str, list[str]] = {}
    for r in results:
        if not r.ok and r.what_failed and r.what_failed not in out.setdefault(r.path_id, []):
            out[r.path_id].append(r.what_failed)
    return out


def report(fx: Fetcher, api: str, failed: dict[str, list[str]], gap: float) -> Counter:
    """POST each failure to the public API, paced to stay under the per-reporter limit."""
    codes: Counter = Counter()
    for i, (path_id, what) in enumerate(sorted(failed.items())[:MAX_REPORTS]):
        if i:
            time.sleep(gap)
        body = {"target": path_id, "what_failed": what, "agent_type": "notworking_canary"}
        f = fx.request("POST", f"{api}/v1/report", json=body)
        codes[f.status or 0] += 1
    return codes


def run(
    catalog: Catalog,
    which: str,
    sample: int | None,
    out_path: str,
    *,
    do_report: bool = False,
    api: str = "https://notworking.io",
    workers: int = 16,
) -> dict[str, Any]:
    services = select(catalog, which, sample)
    jobs = [(s, p) for s in services for p in s.paths if not p.no_longer_working_since]
    fx = Fetcher(Pacer())
    start = time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        # The Chrome variant only for services with MCP servers or skills (politeness).
        nested = pool.map(lambda sp: check_path(fx, sp[0], sp[1], is_daily(sp[0])), jobs)
        results = [r for rs in nested for r in rs]
    elapsed = time.monotonic() - start
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r)) + "\n")
    failed = failures(results)
    summary: dict[str, Any] = {
        "set": which,
        "services": len(services),
        "paths": len(jobs),
        "checks": len(results),
        "seconds": round(elapsed, 1),
        "failed_paths": len(failed),
        "by_type": dict(Counter(f"{r.type}:{'ok' if r.ok else r.what_failed}" for r in results)),
        "by_variant": dict(Counter(f"{r.variant}:{'ok' if r.ok else 'failed'}" for r in results)),
        "reported": None,
    }
    if do_report:
        summary["reported"] = dict(report(fx, api, failed, gap=72.0))  # about 50 an hour
    return summary


def report_problems(summary: dict[str, Any]) -> dict[int, int]:
    """Report responses other than accepted (202) or rate-limited (429). Any of these means
    reporting itself is broken, so the run fails and GitHub emails the maintainer."""
    return {
        int(code): n
        for code, n in (summary.get("reported") or {}).items()
        if int(code) not in (202, 429)
    }
