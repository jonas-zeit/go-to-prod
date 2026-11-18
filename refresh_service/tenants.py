"""Tenant provisioning and tenant database roles.

Usage: python -m refresh_service.tenants NAME    # prints the new API token

Each tenant has a Postgres schema and a login role that can use only that
schema. The worker runs a tenant's definitions on a connection authenticated
as that role, so a definition cannot read anything outside the tenant schema.
"""

import hashlib
import hmac
import os
import re
import secrets
import sys

import psycopg
from psycopg import sql

from refresh_service.db import DATABASE_URL, admin_connect, connect

# Tenant names and derived table names. Leaves room for the "tenant_" prefix
# within the 63 character limit of Postgres identifiers.
IDENTIFIER_PATTERN = r"^[a-z_][a-z0-9_]{0,55}$"

REBUILD_TIMEOUT_SECONDS = int(os.environ.get("REBUILD_TIMEOUT_SECONDS", "300"))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def tenant_role(schema_name: str) -> str:
    return f"tenant_{schema_name}"


def tenant_role_password(role: str) -> str:
    # ponytail: every tenant password derives from one secret, so the database
    # stores none. Move to one secret per tenant in a secret manager if a
    # single tenant's password must be rotated on its own.
    secret = os.environ["TENANT_ROLE_SECRET"]
    return hmac.new(secret.encode(), role.encode(), hashlib.sha256).hexdigest()


def connect_as_tenant(schema_name: str) -> psycopg.Connection:
    role = tenant_role(schema_name)
    return connect(
        DATABASE_URL,
        user=role,
        password=tenant_role_password(role),
        options=f"-c statement_timeout={REBUILD_TIMEOUT_SECONDS}s",
        autocommit=False,
    )


def provision_tenant(
    admin_conn: psycopg.Connection, name: str, token: str | None = None
) -> str:
    """Creates the schema, the login role and the tenant row. Returns the API token."""
    if not re.fullmatch(IDENTIFIER_PATTERN, name):
        raise ValueError(f"invalid tenant name: {name!r}")
    token = token or secrets.token_urlsafe(32)
    role = tenant_role(name)
    schema_id, role_id = sql.Identifier(name), sql.Identifier(role)
    role_exists = admin_conn.execute(
        "select 1 from pg_roles where rolname = %s", (role,)
    ).fetchone()
    statements = [
        sql.SQL("{} role {} login password {}").format(
            sql.SQL("alter" if role_exists else "create"),
            role_id,
            sql.Literal(tenant_role_password(role)),
        ),
        sql.SQL("create schema {}").format(schema_id),
        # "create" lets the role own the derived tables it builds.
        sql.SQL("grant usage, create on schema {} to {}").format(schema_id, role_id),
        # Source tables that the administrator creates later become readable too.
        sql.SQL(
            "alter default privileges in schema {} grant select on tables to {}"
        ).format(schema_id, role_id),
    ]
    with admin_conn.transaction():
        for statement in statements:
            admin_conn.execute(statement)
        admin_conn.execute(
            "insert into tenants (name, schema_name, api_token_hash)"
            " values (%s, %s, %s)",
            (name, name, hash_token(token)),
        )
    return token


if __name__ == "__main__":
    print(provision_tenant(admin_connect(), sys.argv[1]))
