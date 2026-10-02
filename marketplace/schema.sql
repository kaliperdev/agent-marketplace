-- The global agent catalog: agents, and every published version of each.
-- Never any client's logins, settings or choices: those live on client servers.
-- Safe to run again: every statement is "if not exists".

create table if not exists agents (
  id         text primary key check (id ~ '^[a-z][a-z0-9_-]*$'),
  -- Catalog order, which is the order the router lists agents in.
  position   integer not null,
  created_at timestamptz not null default now()
);

create table if not exists agent_versions (
  agent_id     text not null references agents(id),
  version      text not null check (version ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'),
  -- Split out so "latest" sorts as numbers: 1.10.0 comes after 1.9.0.
  major        integer not null,
  minor        integer not null,
  patch        integer not null,
  -- The whole catalog entry (format: marketplace/entry.py).
  entry        jsonb not null,
  published_by text not null,
  published_at timestamptz not null default now(),
  primary key (agent_id, version),
  check (entry->>'id' = agent_id and entry->>'version' = version)
);

create index if not exists agent_versions_latest
  on agent_versions (agent_id, major desc, minor desc, patch desc);

-- One row per version NUMBER, however it is written: the last line of defence
-- against "1.0.00" republishing 1.0.0 under a different spelling.
create unique index if not exists agent_versions_one_per_number
  on agent_versions (agent_id, major, minor, patch);
