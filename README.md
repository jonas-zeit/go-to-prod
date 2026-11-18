# refresh-service

Keeps derived tables up to date. A derived table is a SQL definition over a
tenant's source tables. Clients ask for a refresh over HTTP, a worker rebuilds
the table, and dashboards read the result. Refreshes take a few seconds.

## Layout

- `refresh_service/api.py`: HTTP API (create a derived table, request a refresh, read a job)
- `refresh_service/worker.py`: claims queued jobs and runs them
- `refresh_service/rebuild.py`: rebuilds one derived table (`full` or `append` mode)
- `refresh_service/tenants.py`: creates tenants and their database roles
- `refresh_service/schema.sql`: metadata tables (`tenants`, `derived_tables`, `jobs`)
- `scripts/seed.py`: creates three demo tenants
- `scripts/dashboard_load.py`: simulates dashboards reading the derived tables

## Run the demo

    docker compose up --build -d          # Postgres on :5433, API on :8000, 2 workers
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    export TENANT_ROLE_SECRET=demo-secret # same value as in docker-compose.yml
    python -m scripts.seed                # prints the API tokens
    python -m scripts.dashboard_load      # leave running in a second terminal

`scripts.seed` refuses to run when the database already has tenants. Pass
`--reset` to delete them first.

Request a refresh:

    curl -X POST localhost:8000/tables/1/refresh -H "Authorization: Bearer acme-token"
    curl localhost:8000/jobs/1 -H "Authorization: Bearer acme-token"

Worker logs: `docker compose logs -f worker`

## Test

    pytest

The tests need the Compose Postgres. They create and use their own database,
`refresh_test`, and do not touch the demo data.

## How it behaves

### Tenants

Each tenant has a Postgres schema, an API token and a Postgres login role
named `tenant_<name>`. Create a tenant with:

    python -m refresh_service.tenants NAME    # prints the API token once

The database stores only a SHA-256 hash of the token.

The API returns 404 for a table or a job of another tenant.

The worker runs a definition on a separate connection that is authenticated as
the tenant's role. That role can read and write only the tenant's schema, so a
definition cannot read other tenants or the metadata tables. The worker derives
the role's password from `TENANT_ROLE_SECRET` and the role name. The database
stores no tenant passwords.

### Jobs

A job is `queued`, `running`, `done` or `failed`. A failed job has the Postgres
error in `error`.

Each table has at most one queued job and one running job. A refresh request
for a table that already has a queued job returns that job. A request that
arrives while a job is running gets a new job, because the running job may have
read the source before the caller's change.

Any number of workers can run. A worker holds a Postgres advisory lock for the
job it runs. When a worker dies, Postgres releases the lock and another worker
puts the job back in the queue. On SIGTERM a worker finishes its job and exits.

### Rebuilds

A rebuild runs in one transaction. Dashboards see the previous rows until the
commit. A rebuild does not block readers and does not wait for them.

- `full` deletes all rows and inserts the result of the definition.
- `append` inserts the rows of the definition whose `created_at` is newer than
  the newest `created_at` already in the derived table.

A rebuild never drops the table, so grants, indexes and dependent views stay.

## Known limits

- **Append can miss late rows.** Append does not copy a row that becomes
  visible after a refresh with a `created_at` equal to or older than the newest
  copied row. This happens when a transaction on the source stays open across a
  refresh. Use `full` mode when that matters.
- **Changed columns fail the job.** When the columns of a definition change,
  for example after a change to a source table under `select *`, the job fails
  and the old rows stay. Drop the derived table by hand, then refresh.
- **A definition has 300 seconds.** Change it with `REBUILD_TIMEOUT_SECONDS`.
- **Tenant roles can read the system catalogs.** A definition can see the
  names of other schemas and tables. It cannot read their rows.
- **No command rotates `TENANT_ROLE_SECRET`.** After changing the secret, set
  the new password of each `tenant_<name>` role with `alter role`.
- **Full rebuilds leave dead rows** until autovacuum removes them. Keep
  autovacuum enabled, which is the Postgres default.

## Production: Kubernetes and managed Postgres

The Compose file is a demo. Its passwords and its secret are public.

### Configuration

| Variable | Used by | Purpose |
|---|---|---|
| `DATABASE_URL` | API, workers | Connection as the `refresh_service` role. |
| `TENANT_ROLE_SECRET` | workers, provisioning | Secret from which tenant role passwords derive. |
| `ADMIN_DATABASE_URL` | provisioning only | Connection as an administrator. |
| `REBUILD_TIMEOUT_SECONDS` | workers | Time limit of one definition, 300 by default. |

Store the first three in a Kubernetes Secret. The API does not need
`TENANT_ROLE_SECRET` or `ADMIN_DATABASE_URL`.

### Database setup

1. As an administrator, run `refresh_service/schema.sql` once. It is safe to
   run again. It creates the metadata tables and the `refresh_service` role.
2. Set the password of the role: `alter role refresh_service password '...'`.
3. Create tenants with `python -m refresh_service.tenants NAME`, for example
   from a Kubernetes Job that has `ADMIN_DATABASE_URL` and `TENANT_ROLE_SECRET`.

On managed Postgres the administrator is not a superuser. It needs the
`CREATEROLE` attribute and the `CREATE` privilege on the database. The
`refresh_service` role must not be a superuser.

Tenant roles are login roles. If the server hosts other databases, run
`revoke connect on database <other> from public` for each of them.

Tenant roles get `select` on tables that the provisioning administrator creates
in the tenant schema. If another role creates the source tables, grant
`select` on them to `tenant_<name>`.

Each tenant role owns its derived tables. Grant `select` on them to the role
that the dashboards use. The grant stays across refreshes.

### Workloads

- **API**: a Deployment running
  `uvicorn refresh_service.api:app --host 0.0.0.0 --port 8000`. Use
  `GET /healthz` as readiness and liveness probe. It checks the database
  connection.
- **Workers**: a Deployment running `python -m refresh_service.worker` with two
  or more replicas. Set `terminationGracePeriodSeconds` above the duration of
  your longest refresh, so a worker can finish its job on shutdown. A job that
  is cut off runs again on another worker.

### Connection pooler

Workers must connect directly to Postgres, or through a pooler in session
mode. A pooler in transaction mode, such as PgBouncer with
`pool_mode = transaction`, breaks the advisory lock that marks a job as owned
by a live worker. The API can use a pooler in any mode.
