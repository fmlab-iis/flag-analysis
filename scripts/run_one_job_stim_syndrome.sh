#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="/home/bionicle8699:${PYTHONPATH:-}"

PYTHON="$ROOT/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR  venv not found. Run: bash scripts/setup_server.sh" >&2
  exit 1
fi

protocol="$1"
config="$2"
t="${3:-1}"
job_id="${4:-?}"
total="${5:-?}"
name="$(basename "$config" .txt)"

if [[ -z "$protocol" || -z "$config" ]]; then
  exit 0
fi

if [[ ! -f "$config" ]]; then
  echo "SKIP   missing config: $config" >&2
  exit 0
fi

STATE_CHECK_FLAG=()
if [[ "${STIM_STATE_CHECK:-0}" == "1" ]]; then
  STATE_CHECK_FLAG=(--state-check)
fi

set +e
"$PYTHON" run_stim_syndrome_job.py \
  --root "$ROOT" \
  --config "$config" \
  "${STATE_CHECK_FLAG[@]}"
rc=$?
set -e

if (( rc == 0 )); then
  echo "DONE   $name  stim_syndrome (job ${job_id}/${total})"
else
  echo "FAIL   $name  stim_syndrome (job ${job_id}/${total})" >&2
fi

exit "$rc"
