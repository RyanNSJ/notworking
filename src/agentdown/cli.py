"""`agentdown` command-line entrypoint."""

import argparse

import uvicorn

from agentdown.core.clock import SystemClock
from agentdown.db import engine as db
from agentdown.settings import get_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentdown")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    serve = sub.add_parser("serve", help="apply migrations, then run the API server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    stats = sub.add_parser("stats", help="print a usage summary for stewards")
    stats.add_argument("--days", type=int, default=7)
    sub.add_parser("check-catalog", help="validate catalog/services.yaml and exit")
    gen = sub.add_parser("generate", help="write the skill and server.json for publishing")
    gen.add_argument("--base-url", default=None, help="defaults to publish.PUBLISH_BASE_URL")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command == "migrate":
        db.upgrade(settings.database_url)
    elif args.command == "serve":
        db.upgrade(settings.database_url)
        uvicorn.run(
            "agentdown.api.app:create_app",
            factory=True,
            host=args.host or settings.host,
            port=args.port or settings.port,
            proxy_headers=True,
            forwarded_allow_ips=settings.forwarded_allow_ips,
            access_log=False,  # access logs contain client IPs; no raw IPs anywhere
            server_header=False,
        )
    elif args.command == "stats":
        from agentdown.stats import render_stats

        engine = db.make_engine(settings.database_url)
        with engine.connect() as conn:
            print(render_stats(conn, SystemClock().now(), args.days))
        engine.dispose()
    elif args.command == "check-catalog":
        from agentdown.catalog import load_catalog

        catalog = load_catalog(settings.catalog_path)
        paths = sum(len(s.paths) for s in catalog.services)
        print(f"catalog ok: {len(catalog.services)} services, {paths} access paths")
    elif args.command == "generate":
        from pathlib import Path

        from agentdown import publish

        for rel in publish.write_generated(Path.cwd(), args.base_url or publish.PUBLISH_BASE_URL):
            print(f"wrote {rel}")


if __name__ == "__main__":
    main()
