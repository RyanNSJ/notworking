import datetime as dt
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentdown.api.app import create_app
from agentdown.catalog import Catalog, load_catalog
from agentdown.core.clock import FixedClock
from agentdown.db import engine as db
from agentdown.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
START = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.UTC)
PUBLIC_URL = "https://agentdown.test"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def migrated_db_url(db_url: str) -> str:
    db.upgrade(db_url)
    return db_url


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(FIXTURES / "catalog.yaml")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def app(migrated_db_url: str, catalog: Catalog, clock: FixedClock) -> FastAPI:
    return create_app(
        Settings(database_url=migrated_db_url, public_url=PUBLIC_URL, run_jobs=False),
        clock=clock,
        catalog=catalog,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, client=("203.0.113.10", 50000)) as c:
        yield c


@pytest.fixture
def client_from(app: FastAPI, client: TestClient) -> Callable[[str], TestClient]:
    """Clients with different source IPs, i.e. different reporters.

    They share the app started by `client`: the app's lifespan (catalogue sync, MCP
    session manager) must run only once, as in production.
    """
    del client  # only needed so the lifespan is running

    def make(ip: str) -> TestClient:
        return TestClient(app, client=(ip, 50000))

    return make
