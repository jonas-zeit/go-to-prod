from fastapi.testclient import TestClient

from refresh_service.api import app
from refresh_service.worker import run_once

ACME = {"Authorization": "Bearer acme-token"}


def test_refresh_rebuilds_table(conn):
    conn.execute("delete from acme.orders where region = 'apac'")
    client = TestClient(app)

    job_id = client.post("/tables/1/refresh", headers=ACME).json()["job_id"]
    assert client.get(f"/jobs/{job_id}", headers=ACME).json()["status"] == "queued"

    assert run_once(conn) is True
    assert client.get(f"/jobs/{job_id}", headers=ACME).json()["status"] == "done"
    regions = conn.execute("select region from acme.sales_by_region").fetchall()
    assert sorted(row["region"] for row in regions) == ["amer", "emea"]


def test_create_table_and_refresh(conn):
    client = TestClient(app)
    table_id = client.post(
        "/tables",
        headers=ACME,
        json={
            "name": "big_orders",
            "definition": "select * from acme.orders where amount > 400",
        },
    ).json()["table_id"]
    client.post(f"/tables/{table_id}/refresh", headers=ACME)

    run_once(conn)
    count = conn.execute("select count(*) as n from acme.big_orders").fetchone()
    assert count["n"] > 0


def test_unknown_token_is_rejected(conn):
    client = TestClient(app)
    response = client.post("/tables/1/refresh", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401
