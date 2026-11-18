import psycopg


def rebuild(conn: psycopg.Connection, table: dict) -> None:
    target = f"{table['schema_name']}.{table['name']}"
    if table["mode"] == "append":
        append(conn, table, target)
    else:
        conn.execute(f"drop table if exists {target}")
        conn.execute(f"create table {target} as {table['definition']}")
    conn.execute(
        "update derived_tables set last_refreshed_at = now() where id = %s",
        (table["id"],),
    )


def append(conn: psycopg.Connection, table: dict, target: str) -> None:
    source = f"({table['definition']}) as source"
    conn.execute(
        f"create table if not exists {target} as select * from {source} with no data"
    )
    conn.execute(
        f"insert into {target} select * from {source}"
        " where source.created_at > coalesce(%s::timestamptz, '-infinity')",
        (table["last_refreshed_at"],),
    )
