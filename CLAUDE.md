# CLAUDE.md

Guidance for Claude Code sessions working in this repo.

## What this is

**NotWorking (notworking.io): Downdetector for AI agents.** Internal code names (Python package, CLI, repo `agentdown`, directory `sentinel`) stay as they are; everything agents and people see says NotWorking (D68).

> *When the web quietly locks agents out, NotWorking tells them, and you, in real time.*

Sites, MCP servers and skills quietly break for agents through bot walls, CAPTCHAs, login changes and skills broken by redesigns. NotWorking tells an agent in one call whether it's just them or everyone, from reports by agents, starting with our own canary, which checks services every day. It's open source with open data.

**The whole surface:**
- `GET /v1/status?target=…` returns the **service view**: the service, all its listed **access paths** (site, routes, skills, MCP servers) alphabetically, each with a steward-written description and recent report counts, the `what_failed` options with descriptions, and a polite request to report (D58)
- `POST /v1/report` takes a report against one access path
- an MCP server at `/mcp` exposing those two tools, listed in the MCP Registry
- a thin `notworking` skill
- a basic SVG badge
- minimal human pages: one per service, an index and a methodology page
- an open dataset

Nothing else goes into the MVP. Searching across services by task ("book a table anywhere") is post-MVP.

**Status = recent community reports, like Downdetector.** We never claim whether a path works. Statuses are `no_reported_issues`, `issues_reported` and `many_issues_reported`; any target can be reported and checked, and one not in the catalogue comes back with `listed: false` (D71). Our daily canary (GitHub Actions, agent path only) is just one ordinary reporter, using the public API with equal weight. There's no human control path and no `confirmed_down`.

**The catalogue** (`catalog/services.yaml`) is curated by **stewards** (Ryan, maybe trusted maintainers later). It lists services, access paths, descriptions and aliases. A target in it is **listed**. Curation never gates reporting or checking (D71): it decides service grouping, descriptions, alternative paths and what appears on pages and in the dataset. A service is a service (D72): some have only their website as a known path, others also have MCP servers and skills. Stewards' tools and notes for finding services live in `notes/`, never in the repo.

**Docs:**
- `notes/PLAN.md` (local only, git-ignored; the maintainer's copy) holds the product definition (§0), every decision, the build sequence and the decision log. **It's the source of truth.** `D` numbers in code and docs refer to it.
- `docs/design.md` (public) summarises the design and indexes every decision the code refers to. Keep it in step when a decision referenced in code changes.
- `notes/` (local only, git-ignored) also holds the original concept doc and other background. Where it conflicts with `notes/PLAN.md`, the plan wins.

## Working rules

- **Keep it simple.** In any ambiguity, pick the simplest option. There's no deadline, so don't rush.
- **Build only the milestone the user has started.** M0 (scaffold), M1 (infra), M2 (core API + starter catalogue), M3 (MCP, skill, plugin manifests, front page, published), M4 (detector) and M5 (pages, badge, dataset) are done. Don't start a milestone without the user's go.
- Tasks that need Ryan (accounts, secrets, payments) go in `ryan_todo.md` (local only, git-ignored). Keep it short.
- Never commit working or reference files (notes, todos, personal Claude Code config). Stage files by name, not with `git add -A`.
- **Keep public text terse.** Commit messages, pull request titles and descriptions, code comments and public docs say *what* changed, not the reasoning behind it. Reasoning and decisions go in `notes/PLAN.md`.
- **Never describe in public** (commits, pull requests, docs, comments, catalogue files) where catalogue services come from, how they're collected or found, or the catalogue's scope or size targets.
- Secrets live in `.env` locally (gitignored) and in GitHub Actions secrets. Never print, log or commit them.
- When the user makes or changes a decision, record it in the `notes/PLAN.md` decision log (§8), update `docs/design.md` if the code refers to it, and update this file if it changes the surface, an invariant, the stack or a convention.
- Don't re-add superseded designs (`notes/PLAN.md` §3.7) without being asked.

## Hard invariants (never violate)

**Agent safety**
- **Every piece of text an agent reads comes from a steward.** Text in agent-facing responses (HTTP and MCP) comes only from files under steward review: `catalog/services.yaml`, `options.yaml` and fixed strings in code. Report notes and any other reporter-supplied text are **never** served to agents.
- Access paths come only from the catalogue. A report can never create or promote a path. Paths are listed alphabetically by ID, never ranked. Descriptions say what a path is (at most 200 characters) and never tell the agent to use it. Every service view carries the `notice` that we don't test, vet or endorse paths.

**Status integrity**
- Statuses describe recent reports, not whether anything works.
- Every status transition is logged with its inputs. The detector is deterministic and explainable: Poisson + baseline plus the diversity rules, with no ML.
- `many_issues_reported` requires both the volume **and** the diversity rules. One network prefix can never produce it.
- Unlisted targets return only their own counts and status (`listed: false`), never a description, service or alternatives, and never appear on pages, the board or in the dataset.
- The canary gets no special weight or path. It reports through the public `POST /v1/report` as `agent_type: notworking_canary`.

**Privacy**
- No raw IPs anywhere, logs included. Reporters are identified only by salted, daily-rotating fingerprints.
- No personal data and no query strings. Routes map to listed route prefixes only (a route can name a subdomain, D67). Country is ISO-3166 alpha-2 only. Notes are scrubbed before storage.

**Canary safety**
- The canary runs **only** against `catalog/services.yaml`. Nothing triggers a fetch from user input.
- Also refuse private, loopback, link-local and metadata IPs after DNS resolution.
- Never execute downloaded skill code. Download and parse only.
- Never complete a transaction, booking, payment or account action.
- Never impersonate a specific agent product or a person. The only identities are our declared UA (linking to the methodology page) and generic Chrome.
- Fetch only `/`, `/robots.txt`, `/llms.txt` and listed public routes, at most 1 request per second per host, daily.
- Sites can't opt out of reports, by design.

## Stack (locked)

- Python 3.13 managed by uv, FastAPI, SQLAlchemy Core + Alembic, pytest + hypothesis, ruff, pyright.
- SQLite + Litestream (to Cloudflare R2), keeping the SQL Postgres-portable.
- One small VM (Singapore region) running Docker Compose and Caddy, with Cloudflare in front of public GETs once the real domain exists.
- Push-to-deploy: CI passes → the image goes to GHCR → `docker compose pull && up -d` over SSH.
- Build order: M0 scaffold → M1 infra → M2 core API → M3 MCP + skill → M4 detection → M5 pages, badge and dataset → M6 canary reporter.
- Background jobs: a plain asyncio loop in the app (`jobs.py`, D69) runs the 5-minute detector and salt pruning. No scheduler library; the dataset is computed on request (D70).
- Canary: a CLI run by a daily GitHub Actions cron. It uses curl_cffi for the declared-UA and Chrome variants and POSTs to the public API. Anything it can't access is reported as a failure against the target it was accessing. Run detail goes to an Actions artifact for debugging only.
- MCP: Python MCP SDK, streamable HTTP, mounted at `/mcp`.
- Pages: server-rendered Jinja2, design direction A "Signal board" (D70). System fonts only; charts are inline SVG drawn on the server.

## Planned layout

```
src/agentdown/
  api/        v1 status, report, badge; mcp mount; pages
  core/       target resolution + normalise, privacy (scrubber, fingerprint), options, clock
  detector.py baseline, poisson, rules, hysteresis, transitions
  canary/     clients, checks/*, report mapping, runner CLI (talks to the API over HTTP only)
  db/         schema + alembic migrations
  jobs.py     the 5-minute background loop (detector, salt pruning)
  site/       templates (Jinja2; CSS lives in base.html, no static files)
catalog/      services.yaml (steward-curated services + access paths)
skills/       notworking/SKILL.md + options.json (generated by `agentdown generate`)
.claude-plugin/, gemini-extension.json   plugin manifests for Claude Code, Codex, Copilot CLI, Gemini CLI (generated)
server.json   MCP Registry entry (generated; published as io.notworking/notworking with `mcp-publisher`)
tests/        unit, property, e2e (definition of done over HTTP and MCP)
.github/workflows/  ci, canary-daily
```

## Conventions (apply once code exists)

- Use UTC everywhere and inject the clock. Never call `datetime.now()` directly in detector or rate-limit code.
- Option enums live in one versioned data file. The status response, the 422 errors, the MCP tool descriptions and the skill's fallback copy are all generated from it.
- CI makes no live network calls. Canary checks are tested against recorded fixtures.
- The dev machine runs Windows and CI and the VM run Ubuntu, so prefer Python entrypoints over shell scripts.
- Licensing: code MIT, dataset and catalogue CC BY 4.0.

## Commands

```sh
uv sync                                       # install (Python 3.13 via .python-version)
uv run pytest                                 # tests
uv run ruff check . && uv run ruff format --check .
uv run pyright                                # type check (basic)
uv run agentdown serve                        # apply migrations, serve on 127.0.0.1:8000
uv run agentdown migrate                      # apply migrations only
uv run agentdown check-catalog                # validate catalog/services.yaml
uv run agentdown stats                        # usage summary (on the VM: docker compose exec app agentdown stats)
uv run alembic revision --autogenerate -m "…" # new migration after editing db/schema.py
docker build -t agentdown .                   # image (runs `agentdown serve`, DB at /data)
```

- Settings come from `AGENTDOWN_*` env vars (`src/agentdown/settings.py`). The default DB is `./data/agentdown.db`.
- Code map: `core/targets.py` (normalisation, D47), `catalog.py` (catalogue loading and validation), `lookup.py` (resolution and response builders), `service.py` (the two operations, transport-independent), `api/v1.py` (HTTP), `api/mcp.py` (MCP tools, SDK 2.x `MCPServer` mounted at /mcp), `publish.py` (skill, server.json, llms.txt, server card), `store.py` (DB queries), `stats.py`, `detector.py` (statuses: pure `classify`/`baseline`/`step` plus `run`), `jobs.py` (background loop; `tick` is what tests call, and `run_jobs=False` keeps it off in tests), `api/pages.py` (human pages, SVG strips, dataset CSVs; D70), the badge in `api/v1.py`.
- After changing `options.yaml` or `publish.py`, run `uv run agentdown generate` and commit the regenerated `skills/notworking/*`, `server.json`, `.claude-plugin/*` and `gemini-extension.json` (a test enforces this). The public URL baked into them is `PUBLISH_BASE_URL` in `publish.py`.
- **Downstream publishing.** After a change reaches `main`, check what needs republishing and remind Ryan or do it:
  - `skills/notworking/*` changed: republish to ClawHub with the next patch version: `clawhub skill publish skills/notworking --version <x.y.z>` (token from `CLAWHUB_TOKEN` in `.env`, via `clawhub login --token`). It goes live after ClawHub's security scan.
  - `server.json` changed (description, URL, name): bump `version` in `pyproject.toml` first (the Registry won't take the same version twice), regenerate, then `mcp-publisher login dns --domain notworking.io --private-key <MCP_REGISTRY_PRIVATE_KEY>` and `mcp-publisher publish`.
  - Nothing to do for: the plugins (Claude Code, Codex, Copilot CLI), the Gemini extension, pi and `npx skills`, which all install from the GitHub repo; and the MCP tool descriptions, `llms.txt`, the server card and pages, which are served live by the deploy.
- MCP SDK 2.x: `MCPServer` (not FastMCP); the session manager runs in the FastAPI lifespan, so a test app's lifespan must start only once. `GET /mcp` opens an event stream and never returns; probe with POST JSON-RPC.
- All agent-facing text is in `catalog/services.yaml`, `core/options.yaml`, the constants in `lookup.py`, `api/mcp.py` (instructions, tool descriptions) and `publish.py` (skill, llms.txt). Nothing else may reach a response.
- After autogenerating a migration, check it for SQLite-only defaults (e.g. booleans must use `sa.false()`, not `text('0')`). `tests/test_migrations.py` fails if `schema.py` and the migrations drift apart.
- The repo is `github.com/RyanNSJ/notworking` (public; renamed from `agentdown`, so the image is still `ghcr.io/ryannsj/agentdown`). CI (`.github/workflows/ci.yml`) runs lint, types and tests, plus a Docker build and smoke test.

## Infrastructure (M1)

- **Live at** `https://notworking.io` (`/healthz`; DNS on Cloudflare, A record DNS-only so client IPs reach Caddy). DigitalOcean droplet `agentdown-1`: sgp1, Ubuntu 24.04, 1 GB RAM. `deploy/provision.py` (idempotent) creates it from `deploy/cloud-init.yaml`.
- **SSH:** user `ryan` (sudo, Ryan's key) and user `deploy` (docker group only, key `~/.ssh/agentdown_deploy_ed25519`, also stored as a GitHub secret). Root and password logins are disabled. The host key is pinned in `~/.ssh/agentdown_known_hosts` and the `DEPLOY_KNOWN_HOSTS` secret.
- **Deploy:** every push to `main` that passes CI builds the image, pushes it to `ghcr.io/ryannsj/agentdown`, copies `deploy/{docker-compose.yml,Caddyfile,litestream.yml}` to `/opt/agentdown`, writes `.env` from GitHub secrets, runs `docker compose up -d` and checks `/healthz`.
- **Stack:** `init` (chowns the volume) → `restore` (Litestream restores from R2 if the DB is missing) → `app`, then `litestream` (continuous replication to R2 bucket `agentdown-backups`) and `caddy` (auto-TLS). There are no access logs in Caddy or Uvicorn, because they would record client IPs, and Caddy's error logs pass through a filter that deletes IP and header fields. Caddy caps request bodies at 64 KB and container logs at 10 MB x 3. The app's IP-based fingerprints rely on Caddy replacing client-sent `X-Forwarded-For`; turning on Cloudflare's proxy needs `trusted_proxies` first.
- **Restore** = delete the `agentdown_data` volume and run `docker compose up -d`. Drill-tested on 2026-10-08.
- **Monitoring:** the DigitalOcean uptime check `agentdown-healthz` emails Ryan's account address if the service is down for 2 minutes.
- **Tokens in `.env`:** `GITHUB_PAT` (fine-grained, this repo only), `DIGITALOCEAN_TOKEN` (90-day expiry), and `R2_*`.
