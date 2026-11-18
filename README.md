# refresh-service

Keeps derived tables up to date. A derived table is a SQL definition over a
tenant's source tables. Clients ask for a refresh over HTTP, a worker rebuilds
the table, and dashboards read the result. Refreshes take a few seconds.

Status: prototype. Demoed end to end, not yet in production.

## Layout

- `refresh_service/api.py`: HTTP API (create a derived table, request a refresh, read a job)
- `refresh_service/worker.py`: polls for queued jobs and runs them
- `refresh_service/rebuild.py`: rebuilds one derived table (`full` or `append` mode)
- `refresh_service/schema.sql`: metadata tables (`tenants`, `derived_tables`, `jobs`)
- `scripts/seed.py`: resets the database with three demo tenants
- `scripts/dashboard_load.py`: simulates dashboards reading the derived tables

Each tenant has its own Postgres schema holding its source and derived tables.

## Run

    docker compose up --build -d          # Postgres on :5433, API on :8000, 2 workers
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m scripts.seed                # prints the API tokens
    python -m scripts.dashboard_load      # leave running in a second terminal

Request a refresh:

    curl -X POST localhost:8000/tables/1/refresh -H "Authorization: Bearer acme-token"
    curl localhost:8000/jobs/1 -H "Authorization: Bearer acme-token"

Worker logs: `docker compose logs -f worker`

## Test

    pytest          # resets the database
