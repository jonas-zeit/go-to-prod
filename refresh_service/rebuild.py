from psycopg import sql

from refresh_service.tenants import connect_as_tenant


def rebuild(table: dict) -> None:
    """Rebuilds one derived table on a connection authenticated as the tenant role.

    All statements run in one transaction. Readers see the previous rows until
    the commit, and no statement takes a lock that blocks readers.
    """
    target = sql.Identifier(table["schema_name"], table["name"])
    # The definition is tenant-supplied SQL. It is safe to inline only because
    # this connection can do nothing outside the tenant schema.
    source = sql.SQL("({}) as source").format(sql.SQL(table["definition"]))
    statements = [
        sql.SQL("create table if not exists {} as select * from {} with no data"),
    ]
    if table["mode"] == "append":
        # The watermark is the newest row already copied, read in the same
        # statement, so a repeated job copies nothing twice.
        statements.append(
            sql.SQL(
                "insert into {0} select * from {1} where source.created_at >"
                " coalesce((select max(created_at) from {0}), '-infinity')"
            )
        )
    else:
        # Not drop/create or truncate: those wait for running reads and make
        # every new read wait behind them.
        statements.append(sql.SQL("delete from {}"))
        statements.append(sql.SQL("insert into {} select * from {}"))
    with connect_as_tenant(table["schema_name"]) as conn:
        for statement in statements:
            # prepare=True sends a single prepared statement. Postgres rejects
            # a definition that contains more than one statement.
            conn.execute(statement.format(target, source), prepare=True)
