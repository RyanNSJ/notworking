"""Discoverability and the skill (docs/design.md D43, D46): everything that names our public URL.

`agentdown generate` writes the committed files (the skill in skills/notworking/, server.json
and the plugin manifests for Claude Code, Codex, Copilot and Gemini) from PUBLISH_BASE_URL.
/llms.txt and /mcp/server-card are served live from the app's public URL.
"""

import json
from pathlib import Path

from agentdown import __version__
from agentdown.core.options import load_options

# The URL baked into published files (D68).
PUBLISH_BASE_URL = "https://notworking.io"
# MCP Registry name, verified by a DNS TXT record on notworking.io.
REGISTRY_NAME = "io.notworking/notworking"
REPO_URL = "https://github.com/RyanNSJ/notworking"
REGISTRY_SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"

# The one long description, used wherever a page or listing describes NotWorking.
DESCRIPTION = (
    "When a site, MCP server or skill fails for an agent, NotWorking says in one call whether "
    "other agents are reporting the same problem, lists the service's other known access "
    "paths, and takes anonymous failure reports."
)


# The MCP Registry caps description at 100 characters (schema 2025-12-11).
REGISTRY_DESCRIPTION = (
    "Downdetector for AI agents: is a site, MCP server or skill failing for everyone? "
    "Check and report."
)


def server_json(base_url: str) -> dict[str, object]:
    """MCP Registry server.json; also served as the server card (provisional, SEP-2127)."""
    return {
        "$schema": REGISTRY_SCHEMA,
        "name": REGISTRY_NAME,
        "title": "NotWorking",
        "description": REGISTRY_DESCRIPTION,
        "repository": {"url": REPO_URL, "source": "github"},
        "version": __version__,
        "websiteUrl": base_url,
        "remotes": [{"type": "streamable-http", "url": f"{base_url}/mcp"}],
    }


def claude_plugin(base_url: str) -> dict[str, object]:
    """.claude-plugin/plugin.json; Codex and Copilot CLI read this location too."""
    return {
        "name": "notworking",
        "description": REGISTRY_DESCRIPTION,
        "author": {"name": "Ryan Ng"},
        "homepage": base_url,
        "repository": REPO_URL,
        "license": "MIT",
        "privacyPolicyUrl": f"{base_url}/privacy",
        "mcpServers": {"notworking": {"type": "http", "url": f"{base_url}/mcp"}},
    }


CLAUDE_MARKETPLACE = {
    "name": "notworking",
    "owner": {"name": "Ryan Ng"},
    "plugins": [{"name": "notworking", "source": "./", "description": REGISTRY_DESCRIPTION}],
}


def copilot_plugin(base_url: str) -> dict[str, object]:
    """.github/plugin/plugin.json for Copilot CLI: the Claude manifest plus a version, which the
    Copilot marketplace checks. (Copilot ignores a plugin's MCP server when it finds an
    Agent Plugins plugin.json at the root, so we don't ship one.)"""
    return {**claude_plugin(base_url), "version": __version__}


def gemini_extension(base_url: str) -> dict[str, object]:
    # httpUrl is streamable HTTP in Gemini CLI (url would mean SSE). Skills load from skills/.
    return {
        "name": "notworking",
        "version": __version__,
        "description": REGISTRY_DESCRIPTION,
        "mcpServers": {"notworking": {"httpUrl": f"{base_url}/mcp"}},
    }


def options_json() -> dict[str, object]:
    """The skill's fallback copy of the what_failed values, for when the API is unreachable."""
    options = load_options()
    return {
        "version": options.version,
        "what_failed": options.what_failed_payload(),
        "agent_type": list(options.agent_type),
        "target_type": list(options.target_type),
    }


LLMS_TEMPLATE = """# NotWorking

> Downdetector for AI agents. When a site, URL route, MCP server or skill fails for an \
agent, NotWorking says in one call whether other agents are reporting the same problem, lists \
the service's other known access paths, and takes anonymous failure reports.

Statuses describe recent reports from agents, not whether a path works. NotWorking lists known \
access paths and recent reports; it does not test, vet or endorse them.

## When to use it

When a site, route, MCP server or skill fails in a way that looks outside your control: a bot \
block or 403/429 error, a CAPTCHA, a login loop or new sign-in step, a timeout, an MCP server \
that won't connect, or a skill that errors. One call answers "is it just me, or everyone?"

## 1. Check the status

```
curl "{base_url}/v1/status?target=https://www.example.com/checkout"
```

`target` can be the URL you used, an access-path id (`clawhub:<owner>/<slug>`, \
`skills.sh:<owner>/<repo>/<skill>`, an MCP Registry name like `io.github.<owner>/<server>`) \
or a service name. Example response, trimmed:

```json
{
  "service": {"id": "example.com", "name": "Example"},
  "you_asked_about": {"type": "site", "id": "example.com"},
  "listed": true,
  "summary": "Agents are reporting failures on example.com in the last hour, ...",
  "window": "1h",
  "access_paths": [
    {"type": "mcp", "id": "com.example/mcp", "description": "Example's hosted MCP server.",
     "status": "no_reported_issues", "failure_reports": 0, "unique_reporters": 0,
     "failure_reports_24h": 0},
    {"type": "site", "id": "example.com", "url": "https://www.example.com/",
     "description": "The Example website.", "status": "issues_reported",
     "failure_reports": 7, "unique_reporters": 6, "breakdown": {"captcha": 5, "bot_block": 2},
     "failure_reports_24h": 9, "last_report_at": "2026-10-09T08:40:00Z"}
  ],
  "please_report": {"method": "POST", "url": "{base_url}/v1/report", "...": "..."},
  "what_failed_options": [{"value": "captcha", "description": "..."}]
}
```

Each access path's `status` is one of:

- `no_reported_issues`: no unusual number of failure reports in the last hour
- `issues_reported`: well above that path's usual level
- `many_issues_reported`: many reports from many independent agents and networks

`failure_reports` and `unique_reporters` cover the last hour. `failure_reports_24h` and \
`last_report_at` cover the last 24 hours, as context only. `summary` says it in one sentence.

A target that isn't in the catalogue comes back with `listed: false`, a `summary` and its own \
`status` and counts, but no `service` or `access_paths`.

## 2. Report what failed

```
curl -X POST "{base_url}/v1/report" -H "Content-Type: application/json" \\
  -d '{"target": "example.com", "what_failed": ["captcha"]}'
```

A report says which access path failed and how:

- `target`: **which** path failed. Use the `id` of the access path you used, from \
`access_paths` in the status response, or the URL you used. Each service has its own paths.
- `what_failed`: **how** it failed. One or more values from the list below. The list is the \
same for every service.
- Optional: `note` for anything else (free text, at most 280 characters, never shown to \
agents), `country` (ISO-3166 alpha-2) and `agent_type`. Never include personal data.

The response is 202 with `"accepted": true` and the updated status view:

```json
{"accepted": true, "service": {"id": "example.com", "name": "Example"},
 "access_paths": [{"id": "example.com", "status": "issues_reported", "failure_reports": 8}]}
```

A 429 means you reported this target recently, so don't retry:

```json
{"error": "rate_limited", "retry_after_seconds": 540}
```

A 422 lists the valid values: fix the request and send it once more.

## what_failed values (the same for every service)

{values}

## MCP and install

- [MCP server]({base_url}/mcp): streamable HTTP, no sign-in, tools `check_status` and \
`report` with the same fields.
- [Server card]({base_url}/mcp/server-card) · [OpenAPI]({base_url}/openapi.json)
- [Plugins and the skill]({base_url}/): Claude Code, Codex, Copilot CLI, Gemini CLI, pi, \
ClawHub and `npx skills add {repo}`.
"""


def llms_txt(base_url: str) -> str:
    options = load_options()
    values = "\n".join(f"- `{o.value}`: {o.description}" for o in options.what_failed)
    repo = REPO_URL.removeprefix("https://github.com/")
    # Plain replace, not str.format: the JSON examples are full of braces.
    return (
        LLMS_TEMPLATE.replace("{base_url}", base_url)
        .replace("{values}", values)
        .replace("{repo}", repo)
    )


# (platform, install command). `{base_url}` and `{repo}` are filled in by install_commands.
INSTALL = [
    ("Claude Code", "claude plugin install notworking --marketplace {repo}"),
    ("Codex", "codex plugin marketplace add {repo}\ncodex plugin add notworking@notworking"),
    (
        "GitHub Copilot CLI",
        "copilot plugin marketplace add {repo}\ncopilot plugin install notworking@notworking",
    ),
    ("Gemini CLI", "gemini extensions install https://github.com/{repo}"),
    ("pi", "pi install git:github.com/{repo}\npi mcp add notworking --url {base_url}/mcp"),
    ("ClawHub", "clawhub install notworking"),
    ("Any agent with skills", "npx skills add {repo}"),
    ("Any MCP client", "{base_url}/mcp  (streamable HTTP, no sign-in)"),
]


def install_commands(base_url: str) -> list[tuple[str, str]]:
    """(platform, command) pairs for the front page."""
    repo = REPO_URL.removeprefix("https://github.com/")
    return [(name, cmd.format(base_url=base_url, repo=repo)) for name, cmd in INSTALL]


SKILL_TEMPLATE = """---
name: notworking
description: Check whether a site, URL, MCP server or skill that isn't working is down or \
blocking AI agents for everyone, or just you, see the service's other known access paths, and \
report the failure. Use right after something outside your control fails, before trying \
workarounds - a site that's down, unreachable or timing out, an outage, a fetch that's blocked \
(access denied, 403, 429 rate limited, a Cloudflare "Just a moment" challenge, a CAPTCHA), a \
login loop, an MCP server that won't connect or whose tools error, or a skill that fails.
---

# NotWorking

NotWorking is Downdetector for AI agents. When a site, URL route, MCP server or skill fails \
for you, it says in one call whether other agents are reporting the same problem, lists the \
service's other known access paths, and takes anonymous failure reports.

Base URL: {base_url}
MCP server (streamable HTTP, tools `check_status` and `report`): {base_url}/mcp

If the NotWorking MCP tools `check_status` and `report` are available, use them instead of the \
HTTP calls below. They take the same fields.

## When to use it

Use it as soon as a site, URL route, MCP server or skill fails in a way that looks \
external, before trying workarounds like other URLs, feeds or tools. One check tells you \
whether other agents see the same problem, which saves debugging something you can't fix:
- a fetch that's blocked or returns an error, a bot block, access-denied page, 403 or 429 \
error, or a challenge page
- a CAPTCHA or "are you human?" check
- a login or sign-in loop, or a new login step
- timeouts or the site being unreachable
- an MCP server that won't connect or whose tools error
- a skill that won't install or errors

Don't use it for problems that are clearly local, such as a typo in a URL or your own \
missing credentials. Checking is fine whenever you're unsure, but only report failures that \
came from the service itself: not ones caused by your own tools, permissions or settings, \
like a fetch tool that refuses a domain before reaching it.

## 1. Check the status

```
GET {base_url}/v1/status?target=<the URL, access-path id or service name>
```

Pass the URL or id you used, for example `https://www.example.com/booking/123`, \
`clawhub:owner/slug` or `io.github.owner/server`. The response lists the service's \
`access_paths`. Each has an `id`, a `description`, a `status` and `failure_reports` from the \
last hour, plus `failure_reports_24h` as context. `summary` says it in one sentence. Example, \
trimmed:

```json
{{
  "service": {{"id": "example.com", "name": "Example"}},
  "you_asked_about": {{"type": "site", "id": "example.com"}},
  "listed": true,
  "summary": "Agents are reporting failures on example.com in the last hour, ...",
  "access_paths": [
    {{"type": "mcp", "id": "com.example/mcp", "description": "Example's hosted MCP server.",
     "status": "no_reported_issues", "failure_reports": 0, "failure_reports_24h": 0}},
    {{"type": "site", "id": "example.com", "description": "The Example website.",
     "status": "issues_reported", "failure_reports": 7, "unique_reporters": 6,
     "breakdown": {{"captcha": 5, "bot_block": 2}}, "failure_reports_24h": 9}}
  ]
}}
```

`status` is one of:
- `no_reported_issues`: no unusual number of failure reports in the last hour
- `issues_reported`: well above that path's usual level
- `many_issues_reported`: many reports from many independent agents and networks

If the target isn't in the catalogue, the response has `listed: false`: you still get its \
counts and status, but no description or other access paths.

Statuses describe recent reports, not whether a path works. NotWorking doesn't test, vet or \
endorse the paths it lists.

## 2. Tell the user plainly

For example: "Other agents are reporting the same problem with this skill (9 reports in the \
last hour, mostly CAPTCHA)", or "No other reports, so this may be specific to this setup". If \
the response lists other access paths, you may mention them as options. The user decides \
whether to try one.

## 3. Report it

Unless the user has asked you not to report, send an anonymous report so other agents know:

```
POST {base_url}/v1/report
Content-Type: application/json

{{"target": "<the access-path id you used>", "what_failed": ["captcha"]}}
```

- `target`: the `id` of the access path you used, from the status response (or the URL).
- `what_failed`: how it failed: one or more of the values below, the same list for every \
service.
- Optional: `country` (the user's ISO-3166 alpha-2 country code, like `SG`) and `agent_type`.
- Optional `note` for anything else (free text, at most 280 characters), which is never shown \
to other agents.
- **Never include personal data**: no names, emails, phone numbers, booking references, \
credentials or full URLs with query strings.
- If you get a 429, you've already reported recently; don't retry.
- A 422 response lists the valid values, so fix the request and send it once more.

An accepted report returns 202 with `"accepted": true` and the updated status view, so you \
can see your report counted:

```json
{{"accepted": true, "service": {{"id": "example.com", "name": "Example"}},
 "access_paths": [{{"id": "example.com", "status": "issues_reported", "failure_reports": 8}}]}}
```

A 429 looks like this:

```json
{{"error": "rate_limited", "retry_after_seconds": 540}}
```

Only report failures. NotWorking doesn't take reports that something worked.

## what_failed values (the same for every service)

{values}

If NotWorking is unreachable, the same options are in `options.json` next to this file.
"""


def skill_md(base_url: str) -> str:
    options = load_options()
    values = "\n".join(f"- `{o.value}`: {o.description}" for o in options.what_failed)
    return SKILL_TEMPLATE.format(base_url=base_url, values=values)


def generated_files(base_url: str = PUBLISH_BASE_URL) -> dict[str, str]:
    """Repo-relative path -> content, for every committed generated file."""
    return {
        "skills/notworking/SKILL.md": skill_md(base_url),
        "skills/notworking/options.json": dump(options_json()),
        "server.json": dump(server_json(base_url)),
        ".claude-plugin/plugin.json": dump(claude_plugin(base_url)),
        ".claude-plugin/marketplace.json": dump(CLAUDE_MARKETPLACE),
        "gemini-extension.json": dump(gemini_extension(base_url)),
        ".github/plugin/plugin.json": dump(copilot_plugin(base_url)),
    }


def dump(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


def write_generated(root: Path, base_url: str = PUBLISH_BASE_URL) -> list[str]:
    written = []
    for rel, content in generated_files(base_url).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(rel)
    return written
