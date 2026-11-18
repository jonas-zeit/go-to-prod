"""Points the service at its own database, refresh_test, on the Compose Postgres.

The tests delete all tenants. A separate database keeps them away from the demo
data and from the Compose workers, which poll the "refresh" database.
"""

import os

SERVER = "localhost:5433"
os.environ["ADMIN_DATABASE_URL"] = f"postgresql://postgres:postgres@{SERVER}/refresh_test"
os.environ["DATABASE_URL"] = (
    f"postgresql://refresh_service:refresh_service@{SERVER}/refresh_test"
)
# Postgres roles are shared by all databases of a server, so the tests must
# derive the same tenant passwords as the Compose stack.
os.environ["TENANT_ROLE_SECRET"] = "demo-secret"

import psycopg  # noqa: E402
import pytest  # noqa: E402

from refresh_service.db import admin_connect, connect  # noqa: E402
from scripts.seed import seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def test_database():
    with psycopg.connect(
        f"postgresql://postgres:postgres@{SERVER}/postgres", autocommit=True
    ) as admin:
        if not admin.execute(
            "select 1 from pg_database where datname = 'refresh_test'"
        ).fetchone():
            admin.execute("create database refresh_test")


@pytest.fixture
def conn():
    seed([("acme", 1000), ("globex", 100)], reset=True)
    with connect() as conn:
        yield conn


@pytest.fixture
def admin(conn):
    """Administrator connection, to set up and inspect tenant data."""
    with admin_connect() as admin:
        yield admin
