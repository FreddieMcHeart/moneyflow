# Contributing

Thanks for helping. Keep changes small and focused.

## Setup

Follow [AGENTS.md](AGENTS.md): `uv sync`, a local Postgres, and `MONEYFLOW_TEST_DATABASE_URL`.

## Before you open a PR

- Run `./check.sh`. It must pass. CI runs the same script.
- Add or update tests for any behaviour change.
- Don't commit real financial data: no real amounts, account ids, merchant names or descriptions. Use synthetic fixtures.

## Commits and PR titles

Use [Conventional Commits](https://www.conventionalcommits.org/):

- `fix:` makes a patch release
- `feat:` makes a minor release
- `feat!:` or a `BREAKING CHANGE:` footer makes a major release
- `docs:`, `chore:`, `test:`, `refactor:`, `ci:` make no release

semantic-release reads these on `main`. It bumps the version and writes `CHANGELOG.md`, so don't edit either by hand.

## Design rules

The principles and import contracts are in [AGENTS.md](AGENTS.md). In short: keep `moneyflow.core` pure, add no dependency without a need, and don't hide errors.

## Reporting bugs and ideas

Open an issue with the templates. For security problems, see [SECURITY.md](SECURITY.md). Don't open a public issue for those.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
