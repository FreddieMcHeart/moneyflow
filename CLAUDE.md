@AGENTS.md

## For Claude Code

- Before you say a change is done, run `./check.sh`. It runs the same gates as CI. A green `pytest` alone is not enough.
- Commit messages and PR titles must be conventional commits. semantic-release uses them to pick the next version.
- Never add real financial data: no real amounts, account ids, merchant names or descriptions. Tests use synthetic data only.
- `moneyflow.core` stays pure. The import contracts in `.importlinter` enforce this; don't loosen them to make an import work.
- DB tests drop schema `public`. Point `MONEYFLOW_TEST_DATABASE_URL` only at a throwaway database whose name contains `test`.
