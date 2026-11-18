from collections.abc import Iterator
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from refresh_service.db import connect
from refresh_service.tenants import IDENTIFIER_PATTERN, hash_token

app = FastAPI(title="refresh-service")


class CreateTableRequest(BaseModel):
    name: str = Field(pattern=IDENTIFIER_PATTERN)
    definition: str = Field(min_length=1)
    mode: Literal["full", "append"] = "full"


def get_conn() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        yield conn


def get_tenant(
    authorization: str | None = Header(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    tenant = None
    if scheme == "Bearer" and token:
        tenant = conn.execute(
            "select id, name, schema_name from tenants where api_token_hash = %s",
            (hash_token(token),),
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
    # A rebuild deletes the rows of the table with this name, so the name must
    # not belong to a source table.
    existing = conn.execute(
        "select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = %s and c.relname = %s",
        (tenant["schema_name"], body.name),
    ).fetchone()
    if existing is not None:
        raise HTTPException(status_code=409, detail="name is already in use")
    try:
        row = conn.execute(
            "insert into derived_tables (tenant_id, name, definition, mode)"
            " values (%s, %s, %s, %s) returning id",
            (tenant["id"], body.name, body.definition, body.mode),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="name is already in use")
    return {"table_id": row["id"]}


@app.post("/tables/{table_id}/refresh")
def refresh_table(
    table_id: int,
    tenant: dict = Depends(get_tenant),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict:
    table = conn.execute(
        "select id from derived_tables where id = %s and tenant_id = %s",
        (table_id, tenant["id"]),
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
    job = conn.execute(
        "select j.* from jobs j join derived_tables d on d.id = j.table_id"
        " where j.id = %s and d.tenant_id = %s",
        (job_id, tenant["id"]),
    ).fetchone()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
