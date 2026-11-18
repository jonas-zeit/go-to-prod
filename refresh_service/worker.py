import logging
import signal
import time

import psycopg

from refresh_service.db import connect
from refresh_service.rebuild import rebuild

POLL_INTERVAL_SECONDS = 1.0
# First key of the advisory lock that a worker holds for the job it runs.
JOB_LOCK = 7331

log = logging.getLogger("refresh_service.worker")


def requeue_orphans(conn: psycopg.Connection) -> None:
    """Puts jobs of dead workers back in the queue.

    A running job whose advisory lock is free belongs to a worker whose
    connection ended. The CTE is materialized so that the lock function runs
    only for running jobs. If the table already has a queued job, that job
    covers the refresh and the orphan is marked failed instead.
    """
    orphans = conn.execute(
        "with running as materialized ("
        "  select id, table_id from jobs where status = 'running'"
        "), orphan as ("
        "  select id, table_id from running"
        "  where pg_try_advisory_xact_lock(%s, id)"
        ")"
        " update jobs j set started_at = null,"
        "  status = case when q.id is null then 'queued' else 'failed' end,"
        "  finished_at = case when q.id is null then null else now() end,"
        "  error = case when q.id is null then null else 'worker lost' end"
        " from orphan o"
        " left join jobs q on q.status = 'queued' and q.table_id = o.table_id"
        " where j.id = o.id returning j.id, j.status",
        (JOB_LOCK,),
    ).fetchall()
    for orphan in orphans:
        log.warning("job %s: worker lost, now %s", orphan["id"], orphan["status"])


def claim(conn: psycopg.Connection) -> dict | None:
    """Marks the oldest runnable job as running and takes its advisory lock.

    "skip locked" keeps two workers from claiming the same job. A job is not
    runnable while another job for the same table is running.
    """
    return conn.execute(
        "update jobs set status = 'running', started_at = now()"
        " where id = ("
        "  select j.id from jobs j where j.status = 'queued'"
        "  and not exists (select 1 from jobs r"
        "   where r.table_id = j.table_id and r.status = 'running')"
        "  order by j.created_at, j.id limit 1 for update skip locked"
        " ) returning id, table_id, pg_advisory_lock(%s, id)",
        (JOB_LOCK,),
    ).fetchone()


def run_once(conn: psycopg.Connection) -> bool:
    requeue_orphans(conn)
    job = claim(conn)
    if job is None:
        return False
    table = conn.execute(
        "select d.*, t.schema_name from derived_tables d"
        " join tenants t on t.id = d.tenant_id where d.id = %s",
        (job["table_id"],),
    ).fetchone()
    log.info("job %s: rebuilding %s.%s", job["id"], table["schema_name"], table["name"])
    try:
        rebuild(conn, table)
    except Exception as exc:
        log.exception("job %s: failed", job["id"])
        conn.execute(
            "update jobs set status = 'failed', finished_at = now(), error = %s"
            " where id = %s",
            (str(exc), job["id"]),
        )
    else:
        conn.execute(
            "update jobs set status = 'done', finished_at = now() where id = %s",
            (job["id"],),
        )
        log.info("job %s: done", job["id"])
    # Unlock only after the final status is stored. If this connection ends
    # earlier, another worker finds the job running and unlocked and requeues it.
    conn.execute("select pg_advisory_unlock(%s, %s)", (JOB_LOCK, job["id"]))
    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    # Finish the current job, then exit.
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker started")
    while not stopping:
        try:
            with connect() as conn:
                while not stopping:
                    if not run_once(conn):
                        time.sleep(POLL_INTERVAL_SECONDS)
        except psycopg.Error:
            # The session ends here, which releases the job lock, so another
            # worker requeues a job that this one left running.
            log.exception("database error, reconnecting")
            time.sleep(POLL_INTERVAL_SECONDS)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
