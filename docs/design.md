# NotWorking design

NotWorking is Downdetector for AI agents. When a website, URL route, MCP server or skill fails
for an agent, one call tells it whether other agents are reporting the same problem, lists the
service's other known access paths, and takes an anonymous report. This page summarises how it
works and the decisions the code refers to (the `D` numbers in comments).

## The surface

- `GET /v1/status?target=…`: the status of any target, with the service's access paths when
  the target is listed.
- `POST /v1/report`: report that an access path failed (or worked).
- An MCP server at `/mcp` with the same two calls as tools, listed in the MCP Registry.
- The `notworking` skill and plugins for Claude Code, Codex, Copilot CLI, Gemini CLI and pi.
- A badge, human pages (front page, services, one page per service, methodology) and an open
  dataset (CC BY 4.0).

## Principles

- **Statuses describe recent reports, not whether something works.** Like Downdetector.
- **Every word an agent reads comes from a steward.** Descriptions, option text and fixed
  strings are reviewed in pull requests. Report notes are never shown to anyone.
- **No raw IP addresses anywhere, logs included.** Reporters are identified only by salted
  fingerprints of their network that rotate daily.
- **Curation never gates reporting or checking.** Any target can be reported and checked; the
  catalogue adds names, descriptions and alternative paths.
- **Deterministic and explainable.** Statuses come from a Poisson test and diversity rules,
  not ML, and every change is logged with its inputs.

## Architecture

One small VM runs the app (FastAPI, SQLite) behind Caddy, with Litestream replicating the
database to Cloudflare R2. A background loop in the app runs the detector every 5 minutes.
Pushes to `main` deploy automatically after CI passes. See `CLAUDE.md` for the code map.

## Decisions referenced in the code

| ID | Decision |
|---|---|
| D16 | Status ladder per access path: `no_reported_issues`, `issues_reported`, `many_issues_reported`, all from reports. |
| D19 | A raised status steps down only after 6 calm 5-minute checks in a row (30 minutes). |
| D20 | `issues_reported`: at least 3 distinct reporters in the last hour, and a Poisson chance under 1 in 1,000 against the path's 7-day baseline (floor 0.5 per hour). |
| D21 | Reporter and network fingerprints are salted hashes of the IPv4 /24 or IPv6 /48 and a coarse user-agent family. Salts rotate daily and are deleted after 2 days. The client IP is trusted only from our own proxy. |
| D24 | Reports on unlisted targets are stored and counted. |
| D25 | A report `signature` field is accepted but carries no weight until verification exists. |
| D27 | Report notes are scrubbed and stored, and never shown to agents or on pages. |
| D34 | The MCP server: streamable HTTP at `/mcp`, tools `check_status` and `report`, with option values generated into the tool descriptions. |
| D42 | Human pages are server-rendered, with inline SVG charts and a basic search on the services page. |
| D43 | The skill is published on ClawHub and skills.sh, with a bundled fallback copy of the report options. |
| D46 | Discoverability: the MCP Registry, a server card at `/mcp/server-card`, `llms.txt`, OpenAPI and the skill hubs. |
| D47 | `target` accepts anything (a URL, an access-path id, a name); the server infers the type and normalises it. |
| D48 | The badge: a flat SVG, `notworking \| <status>`, with colours that meet WCAG AA. |
| D51 | Our canary reports as `agent_type: notworking_canary`, with no extra weight. |
| D54 | Deploys: CI builds the image, pushes it to GHCR and restarts the stack over SSH. |
| D55 | The catalogue (`catalog/services.yaml`) lists services, their access paths, descriptions and aliases. A target in it is listed. |
| D56 | Statuses are about recent reports; we never claim a path works or doesn't. |
| D57 | Rate limits: one report per reporter per target per 10 minutes, and 60 per reporter per hour. Over that, 429 with `Retry-After`. |
| D58 | The service view: looking up any path returns the whole service, every access path with its status and counts, the report options and a request to report. |
| D59 | Agent-facing text is written by stewards and reviewed. Descriptions say what a path is, in at most 200 characters, and never tell an agent to use it. |
| D60 | Access paths are listed alphabetically, never ranked. Stewards can mark a path as no longer working since a date. |
| D61 | Lookups match exactly after normalising first, then by service name or alias. |
| D62 | A report counts against one access path. |
| D64 | Daily usage counters for lookups and reports (counts only), including misses as normalised ids. |
| D65 | `agentdown stats` prints a usage summary for stewards. |
| D66 | Every status response carries `as_of` and an `about` link to the methodology page. |
| D67 | A route can be a whole subdomain (like `mail.google.com`) or a domain plus a path prefix. Routes match on the exact host. |
| D68 | The product is NotWorking at notworking.io. Internal code names stay `agentdown`. |
| D71 | Any parseable target can be reported and checked. An unlisted target returns its own counts and status with `listed: false`, and stays off the pages and dataset. |
| D72 | The catalogue is curated by stewards: service grouping, descriptions and alternative access paths. A service may have only its website as a known path, or MCP servers and skills too. |
