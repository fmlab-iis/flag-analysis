#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_dimacs_limits batch
pl_init_tmpdir "$ROOT"

mkdir -p results_txt cnf_out

filter_jobs() {
  grep -vE '^\s*$|^\s*#' "$ROOT/jobs.txt"
}

TOTAL=$(filter_jobs | wc -l | tr -d ' ')
if (( BATCH_JOBS > TOTAL )); then
  BATCH_JOBS=$TOTAL
fi

MEMFREE_EXPORT=()
read -r -a MEMFREE_EXPORT <<< "$(pl_memfree_arg "$EXPORT_MEM_MB")"

echo "Running $TOTAL job(s) from jobs.txt (export + parallel solve)..."
pl_print_dimacs_limits batch
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. t=%s  %s\n", ++n, ($3 == "" ? 1 : $3), $2 }'
echo ""

export BATCH_JOBS
filter_jobs | parallel -j "$BATCH_JOBS" --tmpdir "$PARALLEL_TMPDIR" "${MEMFREE_EXPORT[@]}" --colsep '\t' --line-buffer --joblog run_dimacs.log \
  "$ROOT/scripts/run_one_job_dimacs.sh" {1} {2} {3} {#} "$TOTAL"

echo ""
echo "========== Summary =========="
awk -v total="$TOTAL" '
  NR == 1 { next }
  {
    exitval = $7
    status = (exitval == 0 ? "OK    " : "FAIL  ")
    cmd = $0
    sub(/^[^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ /, "", cmd)
    printf "  %s  %s\n", status, cmd
  }
  END {
    print ""
    print "  CNF:     cnf_out/"
    print "  Metrics: results_txt/"
    print "  Log:     run_dimacs.log"
  }
' run_dimacs.log
