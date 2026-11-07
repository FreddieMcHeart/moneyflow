# MoneyFlow

[![ci](https://github.com/FreddieMcHeart/moneyflow/actions/workflows/ci.yml/badge.svg)](https://github.com/FreddieMcHeart/moneyflow/actions/workflows/ci.yml)
[![release](https://github.com/FreddieMcHeart/moneyflow/actions/workflows/release.yml/badge.svg)](https://github.com/FreddieMcHeart/moneyflow/actions/workflows/release.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Personal finance ledger. It has a pure core, Postgres storage and read-only MCP tools.

- `moneyflow.core`: pure ledger build (ingest, categorize, arithmetic). It does no I/O.
- `moneyflow.storage`: Postgres via SQLAlchemy, with Alembic migrations.
- `moneyflow.services`: rebuilds the stored ledger and runs queries.
- `moneyflow.api`: an MCP server over stdio that exposes read-only tools.

Status: early (0.x). The API and the schema can change between minor versions.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Postgres 16. Docker works.

## Quick start

```
git clone https://github.com/FreddieMcHeart/moneyflow.git
cd moneyflow
uv sync

docker run -d --name mf-pg -e POSTGRES_PASSWORD=mf -p 5433:5432 postgres:16
docker exec mf-pg createdb -U postgres moneyflow_dev
export MONEYFLOW_DATABASE_URL=postgresql://postgres:mf@localhost:5433/moneyflow_dev

uv run alembic upgrade head
uv run python -m moneyflow rebuild --db "$MONEYFLOW_DATABASE_URL"
uv run python -m moneyflow mcp --db "$MONEYFLOW_DATABASE_URL"
```

## Local environment

A Postgres and a Metabase in containers, filled from the old repo, with three dashboards. Everything it stores lives under `.local/` (gitignored).

Prerequisites: `docker` (or `podman`; set `MF_COMPOSE="podman compose"`) and [uv](https://docs.astral.sh/uv/).

```
cp .env.example .env     # optional: `./local.sh up` creates .env with random passwords if it is missing
./local.sh up
```

1. In `.env`, set `MF_OLD_REPO` to the path of the old repo (it is only read, never copied or changed). `up` refuses while it is unset.
2. `./local.sh up` starts the containers, migrates, imports and rebuilds, and provisions Metabase. Run it again at any time: the import is incremental and prints inserted / updated / deleted / unchanged counts per table.

| Command | What it does |
|---|---|
| `./local.sh up` | start everything, migrate, import, rebuild, provision Metabase |
| `./local.sh refresh` | import and rebuild only (containers must be running) |
| `./local.sh status` | containers, schema revision, row counts, Metabase health |
| `./local.sh stop` | stop the containers; data stays in `.local/` |

- **URLs.** Postgres on `localhost:5434` (database `moneyflow_dev`), Metabase on `http://localhost:3000`. Both ports are set in `.env` (`MF_PG_PORT`, `MF_MB_PORT`).
- **Metabase first login.** Use `MB_ADMIN_EMAIL` and `MB_ADMIN_PASSWORD` from `.env`.
- **Dashboards.** The JSON definitions are in `deploy/metabase/dashboards/` (Month, Categories by cycle, Transactions). They are native SQL, upserted by name into the collection "MoneyFlow (provisioned)". Edits made in the Metabase UI inside that collection are overwritten by the next `up`; change the JSON instead.
- **Reset.** There is no reset command: `./local.sh stop`, then delete `.local/` by hand.

### Which Postgres

| Postgres | Port | Databases | Used for |
|---|---|---|---|
| `mf-pg` (the `docker run` in [AGENTS.md](AGENTS.md)) | 5433 | `moneyflow_test` | the test suite only |
| local environment (`./local.sh up`) | 5434 | `moneyflow_dev`, `metabase` | development data and Metabase |

Tests never use `moneyflow_dev`. `mf-pg` stays as it is; nothing here deletes it. The Quick start above uses `mf-pg` for a throwaway dev database; with the local environment, point `MONEYFLOW_DATABASE_URL` at port 5434 instead.

### Metabase licence

Metabase is released under the AGPL. This repo only references the image `metabase/metabase` by tag in `deploy/compose.yml`; the image is not shipped or copied here, and this repo stays MIT.

## Use from Claude Code

The MCP server is read-only and talks to the local database. Take `POSTGRES_PASSWORD` from `.env`:

```
claude mcp add moneyflow -- uv run --directory /path/to/moneyflow python -m moneyflow mcp --db postgresql://postgres:<POSTGRES_PASSWORD>@localhost:5434/moneyflow_dev
```

Everything after `--` is the server command. Use `-s user` or `-s project` before the name to change the scope (default `local`).

## Tests and quality gates

```
uv run pytest -q
./check.sh
```

`./check.sh` is also what CI runs. It covers tests, import contracts, CRAP, duplication and mutation testing. To set up the test database, see [AGENTS.md](AGENTS.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Releases are automatic: semantic-release reads conventional commits on `main` and publishes a GitHub release and [CHANGELOG.md](CHANGELOG.md).

## Security

See [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
