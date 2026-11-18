"""Creates demo tenants, source data and derived tables.

Usage: python -m scripts.seed [--reset]

--reset deletes all existing tenants and their schemas first.
"""

import os
import sys

from psycopg import sql

from refresh_service.db import admin_connect, init_schema
from refresh_service.rebuild import rebuild
from refresh_service.tenants import provision_tenant

TENANTS = [
    ("acme", int(os.environ.get("SEED_ROWS", 3_000_000))),
    ("globex", 200_000),
    ("initech", 200_000),
]

DERIVED_TABLES = [
    (
        "sales_by_region",
        "full",
        "select region, count(*) as orders, sum(amount) as revenue"
        " from {schema}.orders group by region",
    ),
    (
        "orders_daily",
        "full",
        "select created_at::date as day, count(*) as orders, sum(amount) as revenue"
        " from {schema}.orders group by 1",
    ),
    ("recent_events", "append", "select id, kind, created_at from {schema}.events"),
]


def seed(tenants: list[tuple[str, int]] = TENANTS, reset: bool = False) -> None:
    conn = admin_connect()
    init_schema(conn)
    existing = conn.execute("select schema_name from tenants").fetchall()
    if existing and not reset:
        raise SystemExit("the database already has tenants, pass --reset to delete them")
    for tenant in existing:
        conn.execute(
            sql.SQL("drop schema if exists {} cascade").format(
                sql.Identifier(tenant["schema_name"])
            )
        )
    conn.execute("truncate jobs, derived_tables, tenants restart identity cascade")
    for name, rows in tenants:
        # Demo tokens are predictable on purpose. provision_tenant generates a
        # random token when none is given.
        provision_tenant(conn, name, token=f"{name}-token")
        conn.execute(
            f"create table {name}.orders as"
            " select i as id,"
            " (array['emea', 'amer', 'apac'])[1 + mod(i, 3)] as region,"
            " (random() * 500)::numeric(10, 2) as amount,"
            " now() - mod(i, 365) * interval '1 day' as created_at"
            " from generate_series(1, %s) as i",
            (rows,),
        )
        conn.execute(
            f"create table {name}.events as"
            " select i as id, 'login' as kind,"
            " now() - i * interval '1 minute' as created_at"
            " from generate_series(1, 1000) as i"
        )
        tenant = conn.execute(
            "select id from tenants where name = %s", (name,)
        ).fetchone()
        for table_name, mode, definition in DERIVED_TABLES:
            conn.execute(
                "insert into derived_tables (tenant_id, name, definition, mode)"
                " values (%s, %s, %s, %s)",
                (tenant["id"], table_name, definition.format(schema=name), mode),
            )
        print(f"seeded {name} ({rows} orders), token: {name}-token")

    tables = conn.execute(
        "select d.*, t.schema_name from derived_tables d"
        " join tenants t on t.id = d.tenant_id order by d.id"
    ).fetchall()
    for table in tables:
        rebuild(table)
    print(f"built {len(tables)} derived tables")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv[1:])
