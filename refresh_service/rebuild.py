import psycopg
from psycopg import sql


def rebuild(conn: psycopg.Connection, table: dict) -> None:
    """Rebuilds one derived table.

    All statements run in one transaction. Readers see the previous rows until
    the commit, and no statement takes a lock that blocks readers.
    """
    target = sql.Identifier(table["schema_name"], table["name"])
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
    with conn.transaction():
        for statement in statements:
            conn.execute(statement.format(target, source))
