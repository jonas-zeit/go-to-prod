from collections.abc import Iterator

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from refresh_service.db import connect

app = FastAPI(title="refresh-service")


class CreateTableRequest(BaseModel):
    name: str
    definition: str
    mode: str = "full"


def get_conn() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        yield conn


def get_tenant(
    authorization: str = Header(), conn: psycopg.Connection = Depends(get_conn)
) -> dict:
    token = authorization.removeprefix("Bearer ")
    tenant = conn.execute(
        "select * from tenants where api_token = %s", (token,)
    ).fetchone()
    if tenant is None:
        raise HTTPException(status_code=401, detail="unknown token")
    return tenant


@app.get("/healthz")
def healthz(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    conn.execute("select 1")
    return {"status": "ok"}


@app.post("/tables")
def create_table(
    body: CreateTableRequest,
    tenant: dict = Depends(get_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    row = conn.execute(
        "insert into derived_tables (tenant_id, name, definition, mode)"
        " values (%s, %s, %s, %s) returning id",
        (tenant["id"], body.name, body.definition, body.mode),
    ).fetchone()
    return {"table_id": row["id"]}


@app.post("/tables/{table_id}/refresh")
def refresh_table(
    table_id: int,
    tenant: dict = Depends(get_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    table = conn.execute(
        "select id from derived_tables where id = %s", (table_id,)
    ).fetchone()
    if table is None:
        raise HTTPException(status_code=404, detail="table not found")
    # A queued job has not read the source yet, so it also covers this request.
    # The loop covers a worker claiming that job between the two statements.
    for _ in range(3):
        job = conn.execute(
            "insert into jobs (table_id) values (%s)"
            " on conflict (table_id) where status = 'queued' do nothing"
            " returning id",
            (table_id,),
        ).fetchone() or conn.execute(
            "select id from jobs where table_id = %s and status = 'queued'",
            (table_id,),
        ).fetchone()
        if job is not None:
            return {"job_id": job["id"]}
    raise HTTPException(status_code=503, detail="could not queue the refresh, retry")


@app.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    tenant: dict = Depends(get_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    job = conn.execute("select * from jobs where id = %s", (job_id,)).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
