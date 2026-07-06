#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "[$(date '+%H:%M:%S')] ERROR  venv not found. Run: bash scripts/setup_server.sh"
  exit 1
fi

_protocol="$1"
config="$2"
_t="${3:-1}"
job_id="${4:-?}"
total="${5:-?}"
name="$(basename "$config" .txt)"

if [[ -z "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) empty job line"
  exit 0
fi

if [[ ! -f "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) missing config: $config"
  exit 0
fi

echo "[$(date '+%H:%M:%S')] START  ($job_id/$total) syndrome $name"

set +e
"$PYTHON" run_syndrome_extraction_protocol.py \
  --config "$config" \
  --metrics-dir results_txt \
  --quiet
rc=$?
set -e

echo "[$(date '+%H:%M:%S')] DONE   ($job_id/$total) syndrome $name (exit $rc)"

exit "$rc"
