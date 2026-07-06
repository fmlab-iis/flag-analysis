#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

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
cnf_dir="cnf_out_control_flow/$name"

if [[ -z "$protocol" || -z "$config" ]]; then
  exit 0
fi

if [[ ! -f "$protocol" ]]; then
  echo "SKIP   missing protocol: $protocol" >&2
  exit 0
fi

if [[ ! -f "$config" ]]; then
  echo "SKIP   missing config: $config" >&2
  exit 0
fi

set +e
"$PYTHON" run_export_control_flow_dimacs.py \
  --protocol "$protocol" \
  --config "$config" \
  --t "$t" \
  --cnf-dir "$cnf_dir" \
  --quiet
export_rc=$?

if (( export_rc != 0 )); then
  echo "FAIL   control-flow export failed: $name" >&2
  exit "$export_rc"
fi

bash "$ROOT/scripts/run_solve_paths.sh" "$cnf_dir"
solve_rc=$?
set -e

if (( solve_rc == 0 )); then
  echo "DONE   $name (job ${job_id}/${total})"
else
  echo "FAIL   $name (job ${job_id}/${total})" >&2
fi

exit "$solve_rc"
