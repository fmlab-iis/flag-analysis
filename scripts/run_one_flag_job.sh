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
_w="${4:-}"
job_id="${5:-?}"
total="${6:-?}"
name="$(basename "$config" .txt)"

if [[ -z "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) empty job line"
  exit 0
fi

if [[ ! -f "$config" ]]; then
  echo "[$(date '+%H:%M:%S')] SKIP   ($job_id/$total) missing config: $config"
  exit 0
fi

echo "[$(date '+%H:%M:%S')] START  ($job_id/$total) flag-raised t=$_t w=${_w:-$_t} $name"

_w_args=()
if [[ -n "$_w" ]]; then
  _w_args=(--w "$_w")
fi

set +e
"$PYTHON" run_flag_raised_protocol.py \
  --config "$config" \
  --t "$_t" \
  "${_w_args[@]}" \
  --metrics-dir results_txt \
  --quiet
rc=$?
set -e

echo "[$(date '+%H:%M:%S')] DONE   ($job_id/$total) flag-raised t=$_t w=${_w:-$_t} $name (exit $rc)"

exit "$rc"
