#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "[$(date '+%H:%M:%S')] ERROR  venv not found. Run: bash scripts/setup_server.sh"
  exit 1
fi

protocol="$1"
config="$2"
_t="${3:-1}"
job_id="${4:-?}"
total="${5:-?}"
name="$(basename "$config" .txt)"

if [[ -z "$protocol" || -z "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) empty job line"
  exit 0
fi

if [[ ! -f "$protocol" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) missing protocol: $protocol"
  exit 0
fi

if [[ ! -f "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) missing config: $config"
  exit 0
fi

unified_flag=()
if [[ "${LOW_WEIGHT_UNIFIED:-0}" == "1" ]]; then
  unified_flag=(--unified)
fi

echo "[$(date '+%H:%M:%S')] START  ($job_id/$total) low-weight $name"

set +e
"$PYTHON" run_low_weight_protocol.py \
  --protocol "$protocol" \
  --config "$config" \
  --metrics-dir results_txt \
  "${unified_flag[@]}" \
  --quiet
rc=$?
set -e

echo "[$(date '+%H:%M:%S')] DONE   ($job_id/$total) low-weight $name"

exit "$rc"
