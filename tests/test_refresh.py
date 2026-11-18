import threading

from fastapi.testclient import TestClient

from refresh_service.api import app
from refresh_service.db import admin_connect, connect
from refresh_service.worker import claim, run_once

ACME = {"Authorization": "Bearer acme-token"}
GLOBEX = {"Authorization": "Bearer globex-token"}

client = TestClient(app)


def create_table(headers: dict, name: str, definition: str, mode: str = "full") -> int:
    response = client.post(
        "/tables",
        headers=headers,
        json={"name": name, "definition": definition, "mode": mode},
    )
    assert response.status_code == 200, response.text
    return response.json()["table_id"]


def refresh(headers: dict, table_id: int) -> int:
    response = client.post(f"/tables/{table_id}/refresh", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def job(headers: dict, job_id: int) -> dict:
    return client.get(f"/jobs/{job_id}", headers=headers).json()


def refresh_and_run(conn, headers: dict, table_id: int) -> dict:
    job_id = refresh(headers, table_id)
    assert run_once(conn) is True
    return job(headers, job_id)


def test_refresh_rebuilds_table(conn, admin):
    admin.execute("delete from acme.orders where region = 'apac'")

    job_id = refresh(ACME, 1)
    assert job(ACME, job_id)["status"] == "queued"

    assert run_once(conn) is True
    assert job(ACME, job_id)["status"] == "done"
    regions = admin.execute("select region from acme.sales_by_region").fetchall()
    assert sorted(row["region"] for row in regions) == ["amer", "emea"]


def test_create_table_and_refresh(conn, admin):
    table_id = create_table(
        ACME, "big_orders", "select * from acme.orders where amount > 400"
    )
    assert refresh_and_run(conn, ACME, table_id)["status"] == "done"
    count = admin.execute("select count(*) as n from acme.big_orders").fetchone()
    assert count["n"] > 0


def test_unknown_or_missing_token_is_rejected(conn):
    for headers in ({"Authorization": "Bearer x"}, {"Authorization": "acme-token"}, {}):
        assert client.post("/tables/1/refresh", headers=headers).status_code == 401


def test_tenant_cannot_use_another_tenants_table_or_job(conn):
    job_id = refresh(ACME, 1)
    assert client.post("/tables/1/refresh", headers=GLOBEX).status_code == 404
    assert client.get(f"/jobs/{job_id}", headers=GLOBEX).status_code == 404


def test_definition_cannot_read_outside_the_tenant_schema(conn):
    definitions = {
        "other_tenant": "select count(*) as n from acme.orders",
        "metadata": "select name, api_token_hash from tenants",
        "role_change": "select set_config('role', 'postgres', true),"
        " query_to_xml('select count(*) from acme.orders', false, true, '') as x",
    }
    for name, definition in definitions.items():
        result = refresh_and_run(conn, GLOBEX, create_table(GLOBEX, name, definition))
        assert result["status"] == "failed", name
        assert "permission denied" in result["error"], name


def test_definition_with_several_statements_fails(conn, admin):
    admin.execute("create table globex.canary as select 1 as x")
    table_id = create_table(GLOBEX, "t1", "select 1 as x; drop table globex.canary")
    result = refresh_and_run(conn, GLOBEX, table_id)
    assert result["status"] == "failed"
    assert admin.execute("select to_regclass('globex.canary') as t").fetchone()["t"]


def test_invalid_or_used_table_names_are_rejected(conn):
    def status(name: str, mode: str = "full") -> int:
        body = {"name": name, "definition": "select 1 as x", "mode": mode}
        return client.post("/tables", headers=ACME, json=body).status_code

    assert status("t as select 1; drop table acme.orders; --") == 422
    assert status("ok_name", mode="replace") == 422
    assert status("orders") == 409  # a source table
    assert status("sales_by_region") == 409  # an existing derived table
    assert status("ok_name") == 200
    assert status("ok_name") == 409  # registered but not built yet


def test_failed_job_reports_the_error_and_keeps_the_old_rows(conn, admin):
    admin.execute("alter table acme.orders rename column region to area")
    result = refresh_and_run(conn, ACME, 1)
    assert result["status"] == "failed"
    assert "region" in result["error"]
    rows = admin.execute("select count(*) as n from acme.sales_by_region").fetchone()
    assert rows["n"] == 3


def test_two_workers_never_claim_the_same_job(conn, admin):
    admin.execute(
        "insert into derived_tables (tenant_id, name, definition)"
        " select 1, 't' || i, 'select 1 as x' from generate_series(1, 40) i"
    )
    admin.execute("insert into jobs (table_id) select id from derived_tables")
    queued = admin.execute("select count(*) as n from jobs").fetchone()["n"]
    claimed: list[int] = []
    start = threading.Barrier(4)

    def work() -> None:
        with connect() as worker_conn:
            start.wait()
            while (claimed_job := claim(worker_conn)) is not None:
                claimed.append(claimed_job["id"])

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == len(set(claimed)) == queued


def test_one_queued_and_one_running_job_per_table(conn):
    first = refresh(ACME, 1)
    assert refresh(ACME, 1) == first  # the queued job covers both requests

    with connect() as other_worker:
        assert claim(other_worker)["id"] == first
        second = refresh(ACME, 1)
        assert second != first  # the running job may have read older source rows
        assert claim(conn) is None  # not while the first job runs on this table
        assert job(ACME, second)["status"] == "queued"


def test_job_of_a_dead_worker_runs_again(conn):
    job_id = refresh(ACME, 1)
    dead_worker = connect()
    assert claim(dead_worker)["id"] == job_id

    assert run_once(conn) is False  # the worker is alive and holds the job lock
    assert job(ACME, job_id)["status"] == "running"

    dead_worker.close()
    assert run_once(conn) is True
    assert job(ACME, job_id)["status"] == "done"


def test_append_copies_each_row_once(conn, admin):
    def counts() -> dict:
        return admin.execute(
            "select count(*) as n, count(distinct id) as ids from acme.recent_events"
        ).fetchone()

    assert refresh_and_run(conn, ACME, 3)["status"] == "done"
    assert counts() == {"n": 1000, "ids": 1000}

    admin.execute("insert into acme.events values (5000, 'click', now())")
    for _ in range(2):
        assert refresh_and_run(conn, ACME, 3)["status"] == "done"
    assert counts() == {"n": 1001, "ids": 1001}


def test_refresh_does_not_wait_for_a_reader_or_replace_the_table(conn, admin):
    oid = "select 'acme.sales_by_region'::regclass::oid as oid"
    before = admin.execute(oid).fetchone()
    admin.execute("delete from acme.orders where region = 'apac'")

    # An open transaction that has read the table, like a long report. It would
    # block drop table, truncate and rename until it ends.
    with admin_connect() as reader, reader.transaction():
        read = "select count(*) as n from acme.sales_by_region"
        assert reader.execute(read).fetchone()["n"] == 3

        assert refresh_and_run(conn, ACME, 1)["status"] == "done"
        assert reader.execute(read).fetchone()["n"] == 2
    assert admin.execute(oid).fetchone() == before
