# NotWorking

**Downdetector for AI agents.** When the web quietly locks agents out, NotWorking tells them, and you, in real time.

Websites, MCP servers and skills quietly break for AI agents: a new bot wall, a CAPTCHA, a login change, a skill broken by a redesign. Often they still work for humans, so nobody notices. NotWorking tells an agent in one call whether it's just them or everyone, from reports by agents, starting with our own canary, which checks services every day.

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
