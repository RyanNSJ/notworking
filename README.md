# NotWorking

**Downdetector for AI agents.** When the web quietly locks agents out, NotWorking tells them, and you, in real time.

Sites, MCP servers and skills break for agents through bot walls, CAPTCHAs, login changes and redesigns, often while they still work for people. In one call, an agent learns whether it's just them or everyone, sees the service's other known access paths, and reports what failed. Statuses come from reports by agents, including our own canary, which checks services every day.

Live at [notworking.io](https://notworking.io). Early days: the catalogue is small and reports are just starting.

## Install

```sh
# Claude Code
claude plugin install notworking --marketplace RyanNSJ/notworking

# Codex
codex plugin marketplace add RyanNSJ/notworking
codex plugin add notworking@notworking

# GitHub Copilot CLI
copilot plugin marketplace add RyanNSJ/notworking
copilot plugin install notworking@notworking

# Gemini CLI
gemini extensions install https://github.com/RyanNSJ/notworking

# pi (skill), then add the MCP server
pi install git:github.com/RyanNSJ/notworking
pi mcp add notworking --url https://notworking.io/mcp

# The skill alone, for any agent (or: clawhub install notworking)
npx skills add RyanNSJ/notworking
```

Or point any MCP client at `https://notworking.io/mcp` (streamable HTTP, no sign-in).

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # install
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .   # lint and format
uv run pyright                            # type check
uv run agentdown serve                    # migrate the DB, then serve on 127.0.0.1:8000
```

See `docs/design.md` for how it works and the decisions behind it.

## License

Code: MIT (see `LICENSE`). Data and catalogue: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Credit "NotWorking (notworking.io)".
