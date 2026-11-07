#!/bin/sh
cd "$(dirname "$0")" && exec uv run python -m tools.local "$@"
