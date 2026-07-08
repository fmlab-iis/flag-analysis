#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_dimacs_limits batch
pl_init_tmpdir "$ROOT"

mkdir -p results_txt

export PYTHONPATH="/home/bionicle8699:${PYTHONPATH:-}"
PYTHON="$ROOT/venv/bin/python"

filter_jobs() {
  grep -vE '^\s*$|^\s*#' "$ROOT/jobs.txt"
}

TOTAL=$(filter_jobs | wc -l | tr -d ' ')
if (( BATCH_JOBS > TOTAL )); then
  BATCH_JOBS=$TOTAL
fi

echo "Running $TOTAL job(s) from jobs.txt (stim_syndrome invariance checks)..."
pl_print_dimacs_limits batch
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. t=%s  %s\n", ++n, ($3 == "" ? 1 : $3), $2 }'
echo ""

export BATCH_JOBS
set +e
filter_jobs | parallel -j "$BATCH_JOBS" --tmpdir "$PARALLEL_TMPDIR" --colsep '\t' --line-buffer --joblog run_stim_syndrome.log \
  "$ROOT/scripts/run_one_job_stim_syndrome.sh" {1} {2} {3} {#} "$TOTAL"
parallel_rc=$?
set -e

echo ""
"$PYTHON" summarize_jobs_stim_syndrome.py --root "$ROOT" --jobs-file "$ROOT/jobs.txt" --allow-missing

echo ""
echo "========== Summary =========="
awk -v total="$TOTAL" '
  NR == 1 { next }
  {
    exitval = $7
    status = (exitval == 0 ? "PASS  " : "FAIL  ")
    cmd = $0
    sub(/^[^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ [^ ]+ /, "", cmd)
    printf "  %s  %s\n", status, cmd
  }
  END {
    print ""
    print "  Metrics:     results_txt/*_stim_syndrome_metrics.txt"
    print "  JSON:        results_txt/*_stim_syndrome.json"
    print "  Job summary: results_txt/jobs_stim_syndrome_summary.txt"
    print "  Log:         run_stim_syndrome.log"
  }
' run_stim_syndrome.log

exit "$parallel_rc"
