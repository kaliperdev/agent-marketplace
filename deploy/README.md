# Hosting the global catalog

The catalog runs on Kaliper prod (`~/catalog`), and client servers read it at
`https://catalog.rytangle.com` through that server's Cloudflare tunnel (public
hostname `catalog.rytangle.com` -> `http://catalog-service:8095`).

## catalog.env (on the server, never in git)

    CATALOG_DB_PASSWORD=<openssl rand -hex 24>
    CATALOG_READ_KEYS=<key for kaliper prod>,<key for sahil's mac>,<key for greendzine>

Each client server puts its own key in its root `.env` as `CATALOG_READ_KEY`,
with `CATALOG_URL=https://catalog.rytangle.com`. Remove a key here to stop that
server reading the catalog.

## Publishing a version

The database listens only on the server. From a laptop:

    ssh -N -L 55434:127.0.0.1:55433 ec2-user@<kaliper prod> &
    CATALOG_DATABASE_URL=postgresql://catalog:<password>@127.0.0.1:55434/catalog \
      uv run catalog import agents/jira/catalog-2.1.0.json
    uv run catalog export jira --version 2.1.0 > published/jira/2.1.0.json   # the record, in git

Upload the agent's package first (`agents/publish-image.sh`).

## Backup

Every published version is also a file under `published/`, so the catalog can
be rebuilt from git. A database dump on top:

    docker exec catalog-catalog-db-1 pg_dump -U catalog catalog | gzip > catalog-$(date +%F).sql.gz
