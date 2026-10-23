#!/usr/bin/env bash
# Regenerate docs/openapi.json from karspark-core's routes (no engines, no network). Run after API changes.
set -euo pipefail
cd "$(dirname "$0")/../core"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
KARSPARK_ROOT=.. KARSPARK_DATA="$d" KARSPARK_KEY_PEPPER=$(printf 'x%.0s' {1..40}) uv run --frozen python -c '
import json
from karspark.app import create_app
json.dump(create_app(with_mcp=False).openapi(), open("../docs/openapi.json", "w"), indent=1, sort_keys=True)
print("docs/openapi.json:", end=" ")'
echo ok
