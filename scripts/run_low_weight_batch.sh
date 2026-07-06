#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_tmpdir "$ROOT"

JOBS_FILE="${JOBS_FILE:-$ROOT/jobs.txt}"
mkdir -p results_txt

filter_jobs() {
  grep -vE '^\s*$|^\s*#' "$JOBS_FILE"
}

if [[ ! -f "$JOBS_FILE" ]]; then
  echo "Jobs file not found: $JOBS_FILE"
  exit 1
fi

TOTAL=$(filter_jobs | wc -l | tr -d ' ')
PARALLEL_JOBS="${PARALLEL_JOBS:-$(nproc)}"
if (( PARALLEL_JOBS > TOTAL )); then
  PARALLEL_JOBS=$TOTAL
fi

MODE="standard"
if [[ "${LOW_WEIGHT_UNIFIED:-0}" == "1" ]]; then
  MODE="unified"
fi

echo "Running $TOTAL low-weight job(s) from $JOBS_FILE"
echo "  Mode: $MODE path traversal (set LOW_WEIGHT_UNIFIED=1 for unified)"
echo "  Parallel: up to $PARALLEL_JOBS (override: PARALLEL_JOBS=N)"
echo "  Metrics: results_txt/*_low_weight_metrics.txt"
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. %s\n", ++n, $2 }'
echo ""

LOG_FILE="${LOG_FILE:-run_low_weight.log}"

filter_jobs | parallel -j "$PARALLEL_JOBS" --tmpdir "$PARALLEL_TMPDIR" --colsep '\t' --line-buffer --joblog "$LOG_FILE" \
  "$ROOT/scripts/run_one_low_weight_job.sh" {1} {2} {3} {#} "$TOTAL"

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
    print "  Metrics: results_txt/*_low_weight_metrics.txt"
    print "  Log:     " ENVIRON["LOG_FILE"]
  }
' "$LOG_FILE"
