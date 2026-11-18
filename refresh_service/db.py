import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/refresh"
)


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(Path(__file__).with_name("schema.sql").read_text())
