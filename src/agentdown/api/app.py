"""FastAPI application factory."""

import asyncio
from contextlib import asynccontextmanager
from typing import cast

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError

from agentdown import __version__, jobs, publish, store
from agentdown.api.mcp import build_mcp
from agentdown.api.pages import router as pages_router
from agentdown.api.v1 import router as v1_router
from agentdown.catalog import Catalog, load_catalog
from agentdown.core.clock import Clock, SystemClock
from agentdown.core.options import load_options
from agentdown.db.engine import make_engine
from agentdown.service import AppState, problems_result
from agentdown.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    clock: Clock | None = None,
    catalog: Catalog | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    # Fail fast on an invalid catalogue: its text is served to agents.
    catalog = catalog or load_catalog(settings.catalog_path)
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with engine.begin() as conn:
            store.sync_listed(conn, catalog, clock.now())
        jobs_task = (
            asyncio.create_task(jobs.run_forever(cast(AppState, app.state)))
            if settings.run_jobs
            else None
        )
        # A mounted app's own lifespan never runs, so the MCP session manager runs here.
        async with mcp_server.session_manager.run():
            yield
        if jobs_task:
            jobs_task.cancel()
        engine.dispose()

    app = FastAPI(
        title="NotWorking",
        version=__version__,
        description=publish.DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.catalog = catalog
    app.state.engine = engine
    app.include_router(v1_router)
    app.include_router(pages_router)
    mcp_server, mcp_app = build_mcp(cast(AppState, app.state))  # attributes set just above

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt() -> PlainTextResponse:
        return PlainTextResponse(
            publish.llms_txt(settings.public_url), headers={"Cache-Control": "public, max-age=3600"}
        )

    @app.get("/mcp/server-card", include_in_schema=False)
    def server_card() -> JSONResponse:
        return JSONResponse(publish.server_json(settings.public_url))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {
                "field": ".".join(str(p) for p in e.get("loc", ())[1:]) or "body",
                "message": str(e.get("msg", "invalid")),
            }
            for e in exc.errors()
        ]
        result = problems_result(load_options(), problems)
        return JSONResponse(result.body, status_code=result.code)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse({"status": "error", "db": "unreachable"}, status_code=503)
        return JSONResponse({"status": "ok", "version": __version__})

    # Last: the MCP app serves /mcp. Routes above take precedence; anything else is a 404.
    app.mount("/", mcp_app)
    return app
