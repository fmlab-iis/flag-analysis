#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_tmpdir "$ROOT"

mkdir -p results_txt

filter_jobs() {
  grep -vE '^\s*$|^\s*#' "$ROOT/jobs.txt"
}

TOTAL=$(filter_jobs | wc -l | tr -d ' ')
PARALLEL_JOBS="${PARALLEL_JOBS:-$(nproc)}"
if (( PARALLEL_JOBS > TOTAL )); then
  PARALLEL_JOBS=$TOTAL
fi

echo "Running $TOTAL job(s) from jobs.txt (up to $PARALLEL_JOBS in parallel, $(nproc) cores available)..."
echo "  Override: PARALLEL_JOBS=N bash scripts/run_batch.sh"
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. t=%s  %s\n", ++n, ($3 == "" ? 1 : $3), $2 }'
echo ""

filter_jobs | parallel -j "$PARALLEL_JOBS" --tmpdir "$PARALLEL_TMPDIR" --colsep '\t' --line-buffer --joblog run.log \
  "$ROOT/scripts/run_one_job.sh" {1} {2} {3} {#} "$TOTAL"

echo ""
echo "========== Summary =========="
awk -v total="$TOTAL" '
  NR == 1 { next }
  {
    status = "DONE  "
    cmd = $0
    sub(/^[^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ /, "", cmd)
    printf "  %s  %s\n", status, cmd
  }
  END {
    print ""
    print "  Metrics: results_txt/"
    print "  Log:     run.log"
  }
' run.log
