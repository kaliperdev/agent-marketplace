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

-- A published version never changes: client servers have already copied it.
-- Edited in a database client (it happened twice, in TablePlus), it would
-- silently differ from those copies. Publish a new version instead.
create or replace function agent_versions_unchanging() returns trigger language plpgsql as $$
begin
  raise exception 'published versions cannot be edited: publish a new version instead (catalog import)';
end
$$;
drop trigger if exists agent_versions_no_edits on agent_versions;
create trigger agent_versions_no_edits before update or delete on agent_versions
  for each row execute function agent_versions_unchanging();
-- A deleted row would let the same version be published again with other content.
drop trigger if exists agent_versions_no_truncate on agent_versions;
create trigger agent_versions_no_truncate before truncate on agent_versions
  for each statement execute function agent_versions_unchanging();

-- Every attempt to publish, including the ones the catalog refused or ignored:
-- agent_versions only ever holds what was accepted.
create table if not exists publish_attempts (
  id       bigint generated always as identity primary key,
  agent_id text not null,
  version  text not null,
  -- published | unchanged | refused (failed a check) | older (a newer version exists)
  outcome  text not null check (outcome in ('published', 'unchanged', 'refused', 'older')),
  reason   text,
  by       text not null,
  at       timestamptz not null default now()
);
