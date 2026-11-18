import time

import psycopg

from refresh_service.db import connect
from refresh_service.rebuild import rebuild

POLL_INTERVAL_SECONDS = 1.0


def run_once(conn: psycopg.Connection) -> bool:
    job = conn.execute(
        "select id, table_id from jobs where status = 'queued'"
        " order by created_at limit 1"
    ).fetchone()
    if job is None:
        return False
    conn.execute(
        "update jobs set status = 'running', started_at = now() where id = %s",
        (job["id"],),
    )
    table = conn.execute(
        "select d.*, t.schema_name from derived_tables d"
        " join tenants t on t.id = d.tenant_id where d.id = %s",
        (job["table_id"],),
    ).fetchone()
    print(f"job {job['id']}: rebuilding {table['schema_name']}.{table['name']}")
    try:
        rebuild(conn, table)
    except Exception as exc:
        print(f"job {job['id']}: failed: {exc}")
    conn.execute(
        "update jobs set status = 'done', finished_at = now() where id = %s",
        (job["id"],),
    )
    return True


def main() -> None:
    conn = connect()
    print("worker started")
    while True:
        if not run_once(conn):
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
