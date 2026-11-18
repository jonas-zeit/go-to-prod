"""Simulates dashboards reading the derived tables.

Usage: python -m scripts.dashboard_load [--long-report SECONDS]

--long-report adds one reader that runs a slow report on acme.sales_by_region,
like the heavy exports some customers run.
"""

import argparse
import threading
import time

import psycopg

from refresh_service.db import connect

SLOW_THRESHOLD_MS = 2000


def read_loop(label: str, query: str, pause_seconds: float) -> None:
    conn = connect()
    while True:
        started = time.monotonic()
        try:
            conn.execute(query).fetchall()
            status = "ok"
        except psycopg.Error as exc:
            status = f"ERROR  {str(exc).splitlines()[0]}"
        elapsed_ms = (time.monotonic() - started) * 1000
        if status == "ok" and elapsed_ms > SLOW_THRESHOLD_MS:
            status = "SLOW"
        print(f"{label:<32} {elapsed_ms:>8.0f} ms  {status}", flush=True)
        time.sleep(pause_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-report", type=int, default=0, metavar="SECONDS")
    args = parser.parse_args()

    tables = connect().execute(
        "select t.schema_name, d.name from derived_tables d"
        " join tenants t on t.id = d.tenant_id order by d.id"
    ).fetchall()
    readers = [
        (
            f"{table['schema_name']}.{table['name']}",
            f"select * from {table['schema_name']}.{table['name']} limit 500",
            1.0,
        )
        for table in tables
    ]
    if args.long_report > 0:
        readers.append(
            (
                "acme.sales_by_region (report)",
                "select count(*), pg_sleep(%d) from acme.sales_by_region"
                % args.long_report,
                1.0,
            )
        )
    for label, query, pause_seconds in readers:
        threading.Thread(
            target=read_loop, args=(label, query, pause_seconds), daemon=True
        ).start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
