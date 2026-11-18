import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# The API and the workers connect as the refresh_service role.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://refresh_service:refresh_service@localhost:5433/refresh",
)
# Only the provisioning and seed scripts connect as an administrator.
ADMIN_DATABASE_URL = os.environ.get(
    "ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/refresh"
)


def connect(url: str = DATABASE_URL, **kwargs) -> psycopg.Connection:
    # ponytail: one connection per API request, add psycopg_pool when
    # connection setup shows up in request latency.
    kwargs.setdefault("autocommit", True)
    return psycopg.connect(url, row_factory=dict_row, **kwargs)


def admin_connect() -> psycopg.Connection:
    return connect(ADMIN_DATABASE_URL)


def init_schema(admin_conn: psycopg.Connection) -> None:
    admin_conn.execute(Path(__file__).with_name("schema.sql").read_text())
