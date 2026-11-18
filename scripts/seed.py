"""Resets the database and creates demo tenants, source data and derived tables."""

import os

from refresh_service.db import connect, init_schema
from refresh_service.rebuild import rebuild

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


def seed(tenants: list[tuple[str, int]] = TENANTS) -> None:
    conn = connect()
    init_schema(conn)
    conn.execute("truncate jobs, derived_tables, tenants restart identity cascade")
    for name, rows in tenants:
        conn.execute(f"drop schema if exists {name} cascade")
        conn.execute(f"create schema {name}")
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
            "insert into tenants (name, schema_name, api_token)"
            " values (%s, %s, %s) returning id",
            (name, name, f"{name}-token"),
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
        rebuild(conn, table)
    print(f"built {len(tables)} derived tables")


if __name__ == "__main__":
    seed()
