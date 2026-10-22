#!/usr/bin/env bash
# The exact serving command for <service>. Secrets come from the unit's EnvironmentFile.
set -euo pipefail
: "${API_KEY:?API_KEY missing from ~/services/<service>/env}"
exec echo "replace with the real command, passing --api-key \"\$API_KEY\""
