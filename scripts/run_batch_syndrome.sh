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

echo "Running $TOTAL syndrome-extraction job(s) from $JOBS_FILE"
echo "  Parallel: up to $PARALLEL_JOBS (override: PARALLEL_JOBS=N)"
echo "  Metrics: results_txt/*_syndrome_extraction_metrics.txt"
echo ""
echo "Jobs:"
filter_jobs | awk -F '\t' 'NF >= 2 { printf "  %d. %s\n", ++n, $2 }'
echo ""

LOG_FILE="${LOG_FILE:-run_syndrome.log}"

filter_jobs | parallel -j "$PARALLEL_JOBS" --tmpdir "$PARALLEL_TMPDIR" --colsep '\t' --line-buffer --joblog "$LOG_FILE" \
  "$ROOT/scripts/run_one_syndrome_job.sh" {1} {2} {3} {#} "$TOTAL"

echo ""
echo "========== Summary =========="
fail=0
pass=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  config="$(echo "$line" | awk -F '\t' '{print $2}')"
  name="$(basename "$config" .txt)"
  metrics="results_txt/${name}_syndrome_extraction_metrics.txt"
  if [[ -f "$metrics" ]]; then
    failed_count="$(grep 'Failed circuits:' "$metrics" | tail -1 | sed -n 's/.*Failed circuits: *\([0-9]*\/[0-9]*\).*/\1/p')"
    if [[ "$failed_count" == "0/"* ]]; then
      printf "  PASS  %s\n" "$name"
      pass=$((pass + 1))
    else
      printf "  FAIL  %s  (%s)\n" "$name" "$failed_count"
      fail=$((fail + 1))
    fi
  else
    printf "  ?     %s  (no metrics file)\n" "$name"
    fail=$((fail + 1))
  fi
done < <(filter_jobs)

echo ""
echo "  Passed: $pass / $TOTAL"
echo "  Failed: $fail / $TOTAL"
echo "  Metrics: results_txt/*_syndrome_extraction_metrics.txt"
echo "  Log:     $LOG_FILE"

if (( fail > 0 )); then
  exit 1
fi
