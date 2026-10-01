#!/usr/bin/env bash
# Re-solve unfinished d=5 Lai Type-0 ([[19]]) with 2000s timeout.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
LOG="$ROOT/run_d5_lai19_timeout2000.log"
TIMEOUT=2000

{
  echo "START $(date -Is) timeout=${TIMEOUT}s"

  jobs=(
    "cnf_out_control_flow/[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config|baseline"
    "cnf_out_control_flow/[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config|baseline"
    "cnf_out_control_flow_initdata_d5_fixed/[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config|initdata"
    "cnf_out_control_flow_initdata_d5_fixed/[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config|initdata"
  )

  for entry in "${jobs[@]}"; do
    cnf_dir="${entry%%|*}"
    kind="${entry##*|}"
    if [[ ! -f "$cnf_dir/path_000.cnf" ]]; then
      echo "SKIP missing CNF: $cnf_dir"
      continue
    fi
    echo "==== SOLVE $kind $cnf_dir $(date -Is) ===="
    set +e
    "$PYTHON" run_solve_dimacs.py \
      --cnf-dir "$cnf_dir" \
      --path-tag path_000 \
      --parse-only \
      --job-id 1 \
      --total 1 \
      --cms-retries 1 \
      --timeout "$TIMEOUT"
    rc=$?
    set -e
    echo "solve_rc=$rc $kind $cnf_dir"
    if [[ -f "$cnf_dir/path_000_result.json" ]]; then
      "$PYTHON" - "$cnf_dir/path_000_result.json" <<'PY'
import json, sys
r=json.load(open(sys.argv[1]))
print(f"RESULT status={r.get('status')} runtime_s={r.get('solver_runtime_seconds')}")
cex=(r.get('counterexample') or {})
faults=cex.get('faults') or cex.get('assignment') or {}
true={k:v for k,v in faults.items() if v}
if true:
    print(f"true_faults={true}")
PY
    fi
  done

  echo "==== COMPARE $(date -Is) ===="
  "$PYTHON" compare_type0_break.py \
    --jobs-file jobs_type0_d5.txt \
    --initdata-subdir cnf_out_control_flow_initdata_d5_fixed \
    --out results_txt/type0_break_comparison_d5.txt \
    --json-out results_txt/type0_break_comparison_d5.json
  echo "END $(date -Is)"
} 2>&1 | tee -a "$LOG"
