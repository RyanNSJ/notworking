"""MCP transport for the two operations (docs/design.md D34): tools `check_status` and `report`.

Streamable HTTP, stateless, JSON responses, served at /mcp on the same app. The tools call
the same `service` functions as the HTTP API, so behaviour and text are identical. All
descriptions are steward-written (fixed here, or generated from options.yaml).
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette

from agentdown import __version__, service
from agentdown.core.options import load_options
from agentdown.service import AppState, ReportIn

INSTRUCTIONS = (
    "NotWorking is a Downdetector for AI agents. When a website, URL route, skill or MCP server "
    "fails in a way that looks external (a bot block, CAPTCHA, login loop, timeout, MCP or "
    "skill error), call check_status with the URL or id you used. It shows whether other "
    "agents are reporting problems and lists the service's other known access paths. Then "
    "call report with the id of the access path you used, so other agents know. Statuses "
    "describe recent reports from agents, not whether a path works. Never include personal "
    "data in a report."
)

CHECK_STATUS = (
    "Look up a service by the URL you used, an access-path id (clawhub:<owner>/<slug>, "
    "skills.sh:<owner>/<repo>/<skill>, an MCP Registry name like io.github.<owner>/<server>) "
    "or a service name. Returns the service, every listed access path with a description and "
    "failure reports from the last hour, the report options and how to report. Unlisted "
    "targets return their own counts and status, with listed: false."
)


def _report_description() -> str:
    options = load_options()
    values = "; ".join(f"{o.value}: {o.description}" for o in options.what_failed)
    return (
        "Report that an access path failed (or worked) for you. Use the access-path id from "
        "check_status, or the URL you used. Anonymous. Never include personal data, booking "
        "details, credentials or full URLs with query strings. target is which access path "
        "failed; what_failed is how, from this list (the same for every service): " + values
    )


def _client(ctx: Context) -> tuple[str | None, str | None]:
    """Client IP and User-Agent from the HTTP request, used in memory only (D21)."""
    request = getattr(ctx.request_context, "request", None)
    client = getattr(request, "client", None)
    headers = ctx.headers or {}
    return getattr(client, "host", None), headers.get("user-agent")


def build_mcp(state: AppState) -> tuple[MCPServer, Starlette]:
    options = load_options()
    TargetType = Literal[tuple(options.target_type)]  # pyright: ignore[reportInvalidTypeForm]
    Outcome = Literal[tuple(options.outcome)]  # pyright: ignore[reportInvalidTypeForm]
    WhatFailed = Literal[tuple(options.what_failed_values)]  # pyright: ignore[reportInvalidTypeForm]
    AgentType = Literal[tuple(options.agent_type)]  # pyright: ignore[reportInvalidTypeForm]

    server = MCPServer(
        "notworking",
        title="NotWorking",
        description="Downdetector for AI agents: is this access path failing just for me, or "
        "for everyone? Lists a service's other known access paths.",
        instructions=INSTRUCTIONS,
        website_url=state.settings.public_url,
        version=__version__,
        log_level="WARNING",  # per-request INFO lines add noise; we never log client details
    )

    @server.tool(
        name="check_status",
        title="Check a service's access paths",
        description=CHECK_STATUS,
        annotations=ToolAnnotations(
            title="Check a service's access paths", read_only_hint=True, open_world_hint=False
        ),
    )
    def check_status(
        target: Annotated[
            str, Field(description="The URL, access-path id or service name", max_length=2048)
        ],
        type: Annotated[  # noqa: A002 - matches the HTTP API parameter
            TargetType | None,  # pyright: ignore[reportInvalidTypeForm]
            Field(description="Optional, to disambiguate: site, route, mcp or skill"),
        ] = None,
    ) -> dict[str, Any]:
        return service.check_status(state, target, type).body

    @server.tool(
        name="report",
        title="Report an access path failure",
        description=_report_description(),
        annotations=ToolAnnotations(
            title="Report an access path failure",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def report(
        ctx: Context,
        target: Annotated[str, Field(description="The access-path id (or URL) you used")],
        outcome: Annotated[Outcome, Field(description="failed or success")],  # pyright: ignore[reportInvalidTypeForm]
        what_failed: Annotated[
            list[WhatFailed] | None,  # pyright: ignore[reportInvalidTypeForm]
            Field(description="Required when outcome is failed: one or more values"),
        ] = None,
        country: Annotated[
            str | None, Field(description="Optional ISO-3166 alpha-2 country code, like SG")
        ] = None,
        agent_type: Annotated[
            AgentType | None,  # pyright: ignore[reportInvalidTypeForm]
            Field(description="Optional: which agent you are; 'other' if not listed"),
        ] = None,
        note: Annotated[
            str | None,
            Field(
                description="Optional, at most 280 characters, no personal data. Never shown "
                "to other agents."
            ),
        ] = None,
    ) -> dict[str, Any]:
        body = ReportIn(
            target=target,
            outcome=outcome,
            what_failed=list(what_failed) if what_failed else None,
            country=country,
            agent_type=agent_type,
            note=note,
        )
        ip, user_agent = _client(ctx)
        return service.submit_report(state, body, ip, user_agent).body

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        # A public server behind Caddy: DNS-rebinding protection is for local servers.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server, app
