# Hosting the global catalog

The catalog runs on Kaliper prod (`~/catalog`), and client servers read it at
`https://catalog.rytangle.com` through its own Cloudflare tunnel,
`rytangle-catalog` (published application `catalog.rytangle.com` ->
`http://catalog-service:8095`). It shares nothing with the bot on the same
machine: redeploying or removing the bot does not touch it. To move it, copy
`~/catalog` and its data to another machine; the address follows the tunnel.

## catalog.env (on the server, never in git)

    CATALOG_DB_PASSWORD=<openssl rand -hex 24>
    CATALOG_READ_KEYS=<key for kaliper prod>,<key for sahil's mac>,<key for greendzine>
    TUNNEL_TOKEN=<the rytangle-catalog tunnel's token>

Each client server puts its own key in its root `.env` as `CATALOG_READ_KEY`,
with `CATALOG_URL=https://catalog.rytangle.com`. Remove a key here to stop that
server reading the catalog.

## Publishing a version

The database listens only on the server. From a laptop, after uploading the
agent's package (`agents/publish-image.sh`):

    deploy/publish-to-hosted.sh agents/jira/catalog-2.1.0.json
    git add published/jira/2.1.0.json && git commit -m "publish jira 2.1.0"

It opens an SSH tunnel for the length of the command, publishes the entry, and
saves the published record under `published/`. `deploy/publish-to-hosted.sh
--list` shows what the hosted catalog holds. It reads `HOSTED_CATALOG_SSH`,
`HOSTED_CATALOG_SSH_KEY` and `HOSTED_CATALOG_DATABASE_URL` from this repo's
git-ignored `.env` (the password is `CATALOG_DB_PASSWORD` in the server's
`~/catalog/catalog.env`).

## Backup

Every published version is also a file under `published/`, so the catalog can
be rebuilt from git. On top of that, `catalog-backup` writes a dump a day to
`~/catalog/backups/` and keeps 14 days. To restore one into an empty database:

    gunzip -c backups/catalog-<date>.sql.gz | docker exec -i catalog-catalog-db-1 psql -U catalog -d catalog
