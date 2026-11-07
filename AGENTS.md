# MoneyFlow: notes for agents and contributors

## Principles

YAGNI, KISS, DRY, AHA, SRP, Fail Fast; reuse ready solutions; no feature, abstraction or dependency without need; don't hide errors; don't optimise early; run tests after changes; on conflict pick the simplest without losing reliability; follow ub-mode (github.com/unclebob/ub-mode)

## Run the tests and the gates

```
uv sync
uv run pytest -q     # unit tests
./check.sh           # all quality gates, also the CI entry point
```

`./check.sh` stops at the first failure. Order: pytest, import contracts (`uv run lint-imports`), CRAP, duplication, mutation testing on `src/moneyflow/core`.

The ub tools are read from `UB_TOOLS` (default `~/dev/ub-tools`) and are not part of this repo.

## Local Postgres

DB tests need a real Postgres. They fail (never skip) when `MONEYFLOW_TEST_DATABASE_URL` is unset,
and they refuse a database whose name does not end in `_test`. The value must be a `postgresql://` URL,
not a keyword DSN. Each pytest process creates its own `<name>_<pid>_test` copy and drops it at the end;
copies left by a killed worker are dropped at the next session start.

```
docker run -d --name mf-pg -e POSTGRES_PASSWORD=mf -p 5433:5432 postgres:16
docker exec mf-pg createdb -U postgres moneyflow_test
docker exec mf-pg createdb -U postgres moneyflow_dev
export MONEYFLOW_TEST_DATABASE_URL=postgresql://postgres:mf@localhost:5433/moneyflow_test
export MONEYFLOW_DATABASE_URL=postgresql://postgres:mf@localhost:5433/moneyflow_dev
```

The test database and the dev database are separate. If the container exists but is stopped: `docker start mf-pg`.

## Quality gates

Gate thresholds (spec section 9): CRAP < 10 per function; duplication flagged at similarity >= 0.8; mutation testing on `src/moneyflow/core`.

Probe of the ub tools on Python 3.12, run against `~/dev/ub-tools/probe/` (three files: a CC 10 function with no test, two near-identical 8-line functions, `add` with one test; plus `conftest.py` and a `.venv` with pytest and coverage):

| Tool | Command | Key output line | Result |
|---|---|---|---|
| crapper | `~/dev/ub-tools/crapper/crapper --root ~/dev/ub-tools/probe` | `classify  probe  10  0.0%  110.0` and `add  calc  1  100.0%  1.0` | PASS |
| dryer | `~/dev/ub-tools/dryer/dryer --root ~/dev/ub-tools/probe` | `DUPLICATE score=1.00  dup.py:1-8  dup.py:11-18` | PASS |
| mutator | `~/dev/ub-tools/mutator/mutator --root ~/dev/ub-tools/probe calc.py` | `KILLED    calc.py:1 + -> -` | PASS |

No fallback (lizard, jscpd, mutmut) was needed, so none is installed.

Notes from the probe:

- The probe complexity is 10, not the 6 the plan sketched. The gate only needs a function above the threshold, and 110.0 is far above 10.
- crapper installs `coverage` into the project's interpreter with `pip`. A uv-created venv has no `pip`, so `coverage` and `pytest` must be in the dev dependency group (they are). Without them crapper prints `coverage is missing ... will score 0%` and still exits 0.
- crapper `--threshold N` exits 2 when the worst score is higher than N. `check.sh` passes 9.99 so a score of exactly 10 fails.
- dryer exits 0 after printing a report, so `check.sh` fails when its output holds a `DUPLICATE` line.
- mutator exits 3 when a mutant survives and 2 when the baseline tests fail.
- A root `conftest.py` makes crapper and mutator choose pytest. Do not delete it.
- Never leave a `mutants/` or `target/` directory in the tree: crapper walks source dirs.

## Import contracts (`.importlinter`)

1. `moneyflow.core` is independent: it may not import `moneyflow.services`, `.storage`, `.api`, `.adapters`, `.agent`, `tools`, or a listed third-party package (yaml, psycopg, sqlalchemy, alembic, mcp, pytest, and a few common others). The list is explicit, so a new third-party package that core must never import has to be added to it.
2. `moneyflow.services` must not import `moneyflow.api` or `moneyflow.agent`.
3. Nothing under `src/` imports `tools`.

## Commits and releases

Commits on `main` must be conventional (`feat:`, `fix:`, `chore:` ...). semantic-release reads them on every push to `main`: `fix:` cuts a patch, `feat:` a minor, and it updates `CHANGELOG.md` and the version in `pyproject.toml`.

## No real data

No real data in this repo, ever: no real amounts, ids, descriptions or account names. Tests use synthetic fixtures only. The old repo's data is read at run time from `--old-repo` and is never copied in.
