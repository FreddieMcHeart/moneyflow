#!/usr/bin/env bash
# Quality gates, in order. Stops at the first failure. Also the CI entry point.
# ub tools (crapper, dryer, mutator) live outside the repo: UB_TOOLS, default ~/dev/ub-tools.
set -euo pipefail
cd "$(dirname "$0")"
UB="${UB_TOOLS:-$HOME/dev/ub-tools}"
ROOT="$PWD"

echo "== gate 1/5: pytest"
uv run pytest -q

echo "== gate 2/5: import contracts"
uv run lint-imports

echo "== gate 3/5: CRAP < 10 per function"
# --threshold exits 2 when the worst score is HIGHER than N; 9.99 makes a score of exactly 10 fail.
"$UB/crapper/crapper" --root "$ROOT" --threshold 9.99 src

echo "== gate 4/5: duplication, similarity >= 0.8"
dup_out="$("$UB/dryer/dryer" --root "$ROOT" --threshold 0.8 src)"
echo "$dup_out"
if grep -q '^DUPLICATE' <<<"$dup_out"; then
    echo "FAIL duplication gate: dryer reported DUPLICATE candidates" >&2
    exit 1
fi

echo "== gate 5/5: mutation testing on src/moneyflow/core"
"$UB/mutator/mutator" --root "$ROOT" src/moneyflow/core

echo "== all gates passed"
