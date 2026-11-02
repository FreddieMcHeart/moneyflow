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
