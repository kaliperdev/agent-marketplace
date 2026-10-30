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

### On merge (the usual way)

A merge to main does the same by itself (`.github/workflows/publish.yml`): it
publishes every `catalog-*.json` the merge added or changed, with its guide
screenshots, then commits the records under `published/`. A failed publish shows
as a failed run on the commit. It does not upload packages, so upload one before
merging its entry.

It signs in with its own key, one that can open the tunnel and nothing else, and
its secrets live in the `hosted-catalog` environment, which only `main` can use:
no branch or pull request job can read them. Set it up once:

1. Make the key: `ssh-keygen -t ed25519 -N "" -C catalog-publish-github -f catalog_publish`
2. Add this line to `~/.ssh/authorized_keys` on Kaliper prod (the public key from
   `catalog_publish.pub` at the end):

       restrict,port-forwarding,permitopen="127.0.0.1:55433",command="/bin/false" ssh-ed25519 AAAA... catalog-publish-github

3. In the repo's Settings › Environments, create `hosted-catalog`, limited to the
   `main` branch, with secrets `HOSTED_CATALOG_SSH` and `HOSTED_CATALOG_DATABASE_URL`
   (as in `.env`), `HOSTED_CATALOG_SSH_KEY` (the whole `catalog_publish` file) and
   `HOSTED_CATALOG_KNOWN_HOSTS` (the output of `ssh-keyscan -t ed25519 <kaliper prod>`).
4. A second key, for committing the records: add it to this repo as a deploy key
   with write access ("publish records"), put its private half in the
   environment as `PUBLISH_DEPLOY_KEY`, and let deploy keys bypass the `main`
   ruleset. (On a personal account GitHub lets only deploy keys, not the Actions
   app, bypass a ruleset.) Keep it the repo's only deploy key.
5. Delete the key files.

## Updating the service

A merge to main that changes `marketplace/`, the `Dockerfile` or the lockfile
builds and pushes `ghcr.io/kaliperdev/catalog-service:0.1.<n>`
(`.github/workflows/release.yml`; the run's log names the version). Prod keeps
running its old tag until it is told to move. On Kaliper prod, in `~/catalog`,
put the new tag on the `catalog-service` image in `hosted-catalog.yml` and run:

    docker compose -f hosted-catalog.yml --env-file catalog.env up -d

The database is not touched. To go back, put the old tag in and run it again.

## Backup

Every published version is also a file under `published/`, so the catalog can
be rebuilt from git. On top of that, `catalog-backup` writes a dump a day to
`~/catalog/backups/` and keeps 14 days. To restore one into an empty database:

    gunzip -c backups/catalog-<date>.sql.gz | docker exec -i catalog-catalog-db-1 psql -U catalog -d catalog
