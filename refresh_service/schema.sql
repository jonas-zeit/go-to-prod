create table if not exists tenants (
  id serial primary key,
  name text not null unique,
  schema_name text not null unique,
  api_token text not null unique
);

create table if not exists derived_tables (
  id serial primary key,
  tenant_id int not null references tenants (id),
  name text not null,
  definition text not null,
  mode text not null default 'full',
  last_refreshed_at timestamptz
);

create table if not exists jobs (
  id serial primary key,
  table_id int not null references derived_tables (id),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'done', 'failed')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  error text
);

-- At most one queued and one running job per derived table.
create unique index if not exists jobs_one_queued_per_table
  on jobs (table_id) where status = 'queued';
create unique index if not exists jobs_one_running_per_table
  on jobs (table_id) where status = 'running';
