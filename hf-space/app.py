"""NotWorking on Hugging Face: a check-only MCP tool that calls https://notworking.io.

Reports aren't offered here: every call from this Space comes from one network, so reports
through it would count as a single reporter. Agents report through https://notworking.io/mcp.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import gradio as gr

API = "https://notworking.io"
USER_AGENT = "notworking-hf-space/0.1 (+https://notworking.io)"
TO_REPORT = (
    "To report a failure so other agents know, use the NotWorking MCP server at "
    "https://notworking.io/mcp (tool: report), or POST to https://notworking.io/v1/report."
)


def check_status(target: str, target_type: str = "") -> str:
    """Is it down or blocking agents for everyone, or just you?

    Use when a site, URL, MCP server or skill isn't working: down, unreachable or an outage,
    a timeout, blocked (access denied, 403, 429 rate limited, a Cloudflare challenge, a
    CAPTCHA), a login loop, an MCP server that won't connect or a tool that errors, or a skill
    that fails. Returns whether other agents are reporting the same problem in the last hour,
    and the service's other known access paths. Statuses describe recent reports from agents,
    not whether something works.

    Args:
        target: The URL you used, an access-path id (like clawhub:owner/slug or
            io.github.owner/server) or a service name, e.g. "https://www.notion.com/".
        target_type: Optional, to disambiguate: site, route, mcp or skill. Leave empty if unsure.

    Returns:
        JSON: the NotWorking status view, with the service, its access paths with statuses
        and failure report counts, and how to report a failure.
    """
    query = {"target": target}
    if target_type:
        query["type"] = target_type
    url = f"{API}/v1/status?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:  # 404 and 422 still carry a helpful JSON body
        data = json.load(e)
    except (urllib.error.URLError, TimeoutError) as e:
        data = {"error": "unreachable", "message": f"NotWorking couldn't be reached: {e}"}
    data["how_to_report"] = TO_REPORT
    return json.dumps(data, indent=1)


demo = gr.Interface(
    fn=check_status,
    inputs=[
        gr.Textbox(label="Target", placeholder="URL, access-path id or service name"),
        gr.Dropdown(["", "site", "route", "mcp", "skill"], value="", label="Type (optional)"),
    ],
    outputs=gr.Code(language="json", label="Status"),
    title="NotWorking",
    description=(
        "Downdetector for AI agents: is a site, MCP server or skill failing for everyone? "
        "This Space checks. To report failures, use https://notworking.io/mcp."
    ),
    examples=[["notion.com", ""], ["https://mcp.linear.app/mcp", "mcp"], ["github.com", ""]],
    flagging_mode="never",
)

if __name__ == "__main__":
    demo.launch(mcp_server=True)
