create table if not exists tenants (
  id serial primary key,
  name text not null unique,
  schema_name text not null unique,
  api_token_hash text not null unique
);

create table if not exists derived_tables (
  id serial primary key,
  tenant_id int not null references tenants (id),
  name text not null,
  definition text not null,
  mode text not null default 'full' check (mode in ('full', 'append')),
  last_refreshed_at timestamptz,
  unique (tenant_id, name)
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

-- The API and the workers connect as this role. It has no password here;
-- set one with "alter role refresh_service password ...".
do $$
begin
  if not exists (select from pg_roles where rolname = 'refresh_service') then
    create role refresh_service login;
  end if;
end
$$;

grant select on tenants to refresh_service;
grant select, insert, update on derived_tables, jobs to refresh_service;
grant usage on all sequences in schema public to refresh_service;
