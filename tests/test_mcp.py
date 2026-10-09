"""The MCP transport (D34) and the discoverability files (D43, D46)."""

import json
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentdown import publish
from agentdown.core.options import load_options
from tests.conftest import PUBLIC_URL

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
ROOT = Path(__file__).parent.parent


def rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    r = client.post("/mcp", headers=HEADERS, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def call(client: TestClient, tool: str, **arguments: Any) -> dict[str, Any]:
    return rpc(client, "tools/call", {"name": tool, "arguments": arguments})["result"]


def test_initialize_has_instructions(client: TestClient) -> None:
    result = rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    )["result"]
    assert result["serverInfo"]["name"] == "notworking"
    assert "Downdetector for AI agents" in result["instructions"]


def test_tools_are_described_from_options(client: TestClient) -> None:
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert set(tools) == {"check_status", "report"}
    assert tools["check_status"]["annotations"]["readOnlyHint"] is True
    assert tools["report"]["annotations"]["readOnlyHint"] is False
    options = load_options()
    for o in options.what_failed:  # generated from options.yaml, with descriptions
        assert o.value in tools["report"]["description"]
        assert o.description in tools["report"]["description"]
    schema = json.dumps(tools["report"]["inputSchema"])
    assert '"outcome"' not in schema and '"notworking_canary"' in schema


def test_check_status_matches_http(client: TestClient) -> None:
    via_mcp = call(client, "check_status", target="https://www.xyz.com/booking/1?x=y")
    via_http = client.get("/v1/status", params={"target": "https://www.xyz.com/booking/1?x=y"})
    assert via_mcp["structuredContent"] == via_http.json()
    assert via_mcp["structuredContent"]["you_asked_about"]["id"] == "xyz.com/booking"


def test_report_via_mcp(client: TestClient, migrated_db_url: str, app: FastAPI) -> None:
    result = call(
        client,
        "report",
        target="clawhub:abc/xyz-booking",
        what_failed=["captcha"],
        note="ping me at a@b.com",
    )["structuredContent"]
    assert result["accepted"] is True
    path = next(p for p in result["access_paths"] if p["id"] == "clawhub:abc/xyz-booking")
    assert path["failure_reports"] == 1
    # Same IP and User-Agent over HTTP is the same reporter, so it's rate-limited:
    # the client IP reaches the fingerprint through the MCP transport too.
    r = client.post(
        "/v1/report",
        json={"target": "clawhub:abc/xyz-booking", "outcome": "failed", "what_failed": ["captcha"]},
    )
    assert r.status_code == 429
    with app.state.engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM reports")).all()
    stored = " ".join(str(v) for row in rows for v in row)
    assert "203.0.113" not in stored and "a@b.com" not in stored


def test_mcp_validation_errors_are_reported(client: TestClient) -> None:
    result = call(client, "report", target="xyz.com", what_failed=["broken"])
    assert result.get("isError") is True
    missing = call(client, "report", target="xyz.com")["structuredContent"]
    assert missing["error"] == "invalid_request"
    assert {p["field"] for p in missing["problems"]} == {"what_failed"}


# ---- discoverability -------------------------------------------------------------


def test_llms_txt_and_server_card(client: TestClient) -> None:
    text = client.get("/llms.txt").text
    assert f"{PUBLIC_URL}/mcp" in text and f"{PUBLIC_URL}/v1/status" in text
    assert "`captcha`" in text
    card = client.get("/mcp/server-card").json()
    assert card["name"] == publish.REGISTRY_NAME
    assert card["remotes"] == [{"type": "streamable-http", "url": f"{PUBLIC_URL}/mcp"}]


def test_home_page(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert f"{PUBLIC_URL}/mcp" in r.text and "{" not in r.text.split("</style>")[1]


def test_llms_txt_example_matches_the_real_response(client: TestClient) -> None:
    text = client.get("/llms.txt").text
    example = json.loads(re.findall(r"```json\n(.*?)```", text, re.S)[0])
    real = client.get("/v1/status", params={"target": "xyz.com"}).json()
    assert set(example) - {"...": 0}.keys() <= set(real)
    real_path_keys = {k for p in real["access_paths"] for k in p} | {"breakdown", "last_report_at"}
    assert {k for p in example["access_paths"] for k in p} <= real_path_keys
    assert set(example["please_report"]) - {"..."} <= set(real["please_report"])


def test_report_examples_match_real_responses(client: TestClient) -> None:
    """Every JSON example in the skill and llms.txt parses, and its fields really exist."""
    body = {"target": "xyz.com", "outcome": "failed", "what_failed": ["captcha"]}
    accepted = client.post("/v1/report", json=body).json()
    limited = client.post("/v1/report", json=body).json()
    skill = (ROOT / "skills/notworking/SKILL.md").read_text(encoding="utf-8")
    for text in [skill, client.get("/llms.txt").text]:
        blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S)]
        assert len(blocks) == 3
        for example in blocks:
            real = accepted if "accepted" in example else limited if "error" in example else None
            if real is not None:
                assert set(example) <= set(real)


def test_openapi_documents_both_calls(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/status" in paths and "/v1/report" in paths


def test_server_json_fits_registry_limits() -> None:
    card = publish.server_json(publish.PUBLISH_BASE_URL)
    assert 1 <= len(str(card["description"])) <= 100
    assert len(str(card["title"])) <= 100
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", str(card["name"]))


def test_generated_files_are_in_sync() -> None:
    """Run `uv run agentdown generate` after changing options.yaml or publish.py."""
    for rel, content in publish.generated_files().items():
        assert (ROOT / rel).read_text(encoding="utf-8") == content, f"{rel} is out of date"


def test_skill_has_frontmatter_and_no_unfilled_placeholders() -> None:
    text = (ROOT / "skills/notworking/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: notworking\ndescription: ")
    assert publish.PUBLISH_BASE_URL in text
    assert "{" + "base_url}" not in text and "{" + "values}" not in text
