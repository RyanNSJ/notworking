"""HTTP transport for the two operations: `GET /v1/status` and `POST /v1/report`."""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from agentdown import service
from agentdown.service import ReportIn, Result

router = APIRouter(prefix="/v1", tags=["agents"])


def _send(result: Result) -> JSONResponse:
    return JSONResponse(result.body, status_code=result.code, headers=result.headers)


@router.get(
    "/status",
    summary="Is it just me? Look up a service and its access paths",
    description="Pass the URL, access-path id or service name that failed. Returns the service, "
    "every listed access path with recent failure-report counts, the report options with "
    "descriptions, and how to report. Targets not in the catalogue return their own counts and "
    "status with `listed: false`.",
)
def get_status(
    request: Request,
    target: str = Query(..., max_length=2048, description="URL, access-path id or service name"),
    type: str | None = Query(None, description="Optional: site, route, mcp or skill"),
) -> JSONResponse:
    return _send(service.check_status(request.app.state, target, type))


@router.post(
    "/report",
    status_code=202,
    summary="Report that an access path failed for you",
    description="Anonymous. Use the access-path id from the status lookup (or the URL you used). "
    "Never include personal data. Returns the updated service view.",
)
def post_report(request: Request, body: ReportIn) -> JSONResponse:
    ip = request.client.host if request.client else None
    return _send(
        service.submit_report(request.app.state, body, ip, request.headers.get("user-agent"))
    )


# Badge (D48): white text on fills that all meet WCAG AA (4.5:1); a test checks this.
BADGE_LABEL = "notworking"
BADGE = {
    "no_reported_issues": ("no reported issues", "#116329"),
    "issues_reported": ("issues reported", "#7d4e00"),
    "many_issues_reported": ("many issues reported", "#a40e26"),
    "unknown": ("unknown", "#4b5563"),
}
BADGE_LEFT = "#24292f"


def badge_svg(status: str) -> str:
    text, color = BADGE[status]
    lw, rw = 10 + 7 * len(BADGE_LABEL), 10 + 7 * len(text)
    w = lw + rw
    font = 'font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" fill="#fff"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" '
        f'aria-label="{BADGE_LABEL}: {text}"><title>{BADGE_LABEL}: {text}</title>'
        f'<rect width="{lw}" height="20" fill="{BADGE_LEFT}"/>'
        f'<rect x="{lw}" width="{rw}" height="20" fill="{color}"/>'
        f'<text x="{lw / 2}" y="14" text-anchor="middle" {font} '
        f'textLength="{lw - 10}">{BADGE_LABEL}</text>'
        f'<text x="{lw + rw / 2}" y="14" text-anchor="middle" {font} '
        f'textLength="{rw - 10}">{text}</text></svg>'
    )


@router.get(
    "/badge",
    summary="A status badge (SVG) for a service or access path",
    description="Shows the target's status, or for a service its most raised path. A target "
    "that can't be resolved shows `unknown`.",
    response_class=Response,
)
def get_badge(
    request: Request,
    target: str = Query(..., max_length=2048, description="Service, URL or access-path id"),
    type: str | None = Query(None, description="Optional: site, route, mcp or skill"),
) -> Response:
    status = service.badge_status(request.app.state, target, type)
    return Response(
        badge_svg(status),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=60"},
    )
