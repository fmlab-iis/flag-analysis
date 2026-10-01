#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_dimacs_limits batch
pl_init_tmpdir "$ROOT"

JOBS_FILE="${1:-$ROOT/jobs_type0.txt}"

mkdir -p results_txt cnf_out_control_flow_initdata

PYTHON="$ROOT/venv/bin/python"

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "ERROR: jobs file not found: $JOBS_FILE" >&2
  exit 1
fi

filter_jobs() {
  grep -vE '^\s*$|^\s*#' "$JOBS_FILE"
}

TOTAL=$(filter_jobs | wc -l | tr -d ' ')
if (( TOTAL == 0 )); then
  echo "ERROR: no jobs in $JOBS_FILE" >&2
  exit 1
fi
if (( BATCH_JOBS > TOTAL )); then
  BATCH_JOBS=$TOTAL
fi

MEMFREE_EXPORT=()
read -r -a MEMFREE_EXPORT <<< "$(pl_memfree_arg "$EXPORT_MEM_MB")"

echo "Running $TOTAL Type-0 initdata job(s) from $JOBS_FILE ..."
pl_print_dimacs_limits batch
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. t=%s  %s\n", ++n, ($3 == "" ? 1 : $3), $2 }'
echo ""

export BATCH_JOBS
filter_jobs | parallel -j "$BATCH_JOBS" --tmpdir "$PARALLEL_TMPDIR" "${MEMFREE_EXPORT[@]}" --colsep '\t' --line-buffer \
  --joblog run_control_flow_initdata.log \
  "$ROOT/scripts/run_one_job_control_flow_initdata.sh" {1} {2} {3} {#} "$TOTAL"

echo ""
"$PYTHON" summarize_jobs_control_flow.py --root "$ROOT" --jobs-file "$JOBS_FILE" \
  --cnf-subdir cnf_out_control_flow_initdata \
  --out results_txt/jobs_control_flow_initdata_summary.txt \
  --json-out results_txt/jobs_control_flow_initdata_summary.json || true

echo ""
"$PYTHON" compare_type0_break.py --root "$ROOT" --jobs-file "$JOBS_FILE" || true

echo ""
echo "========== Summary =========="
echo "  CNF:         cnf_out_control_flow_initdata/"
echo "  Comparison:  results_txt/type0_break_comparison.txt"
echo "  Log:         run_control_flow_initdata.log"
