#!/usr/bin/env bash
# Export+solve Type-0 only for d=5 initdata jobs; then compare solver runtimes.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
JOBS="$ROOT/jobs_type0_d5.txt"
OUT_ROOT="${OUT_ROOT:-$ROOT/cnf_out_control_flow_initdata_d5_fixed}"
LOG="${LOG:-$ROOT/run_d5_type0_initdata_fixed.log}"

mkdir -p "$OUT_ROOT"
echo "START $(date -Is)" | tee -a "$LOG"

while IFS=$'\t' read -r proto cfg t || [[ -n "${proto:-}" ]]; do
  [[ -z "${proto:-}" || "${proto:0:1}" == "#" ]] && continue
  name="$(basename "$cfg" .txt)"
  cnf_dir="$OUT_ROOT/$name"
  echo "==== EXPORT TYPE0 $name t=$t $(date -Is) ====" | tee -a "$LOG"
  set +e
  "$PYTHON" "$ROOT/run_export_control_flow_dimacs.py" \
    --protocol "$proto" \
    --config "$cfg" \
    --t "$t" \
    --cnf-dir "$cnf_dir" \
    --initial-data-error \
    --type0-only \
    --quiet
  erc=$?
  set -e
  echo "export_rc=$erc $name" | tee -a "$LOG"
  if (( erc != 0 )); then
    echo "FAIL_EXPORT $name" | tee -a "$LOG"
    continue
  fi

  set +e
  "$PYTHON" - "$cnf_dir" <<'PY' | tee -a "$LOG"
import json, subprocess, sys
from pathlib import Path
cnf_dir = Path(sys.argv[1])
man = json.loads((cnf_dir / "manifest.json").read_text(encoding="utf-8"))
tags = [p["path_tag"] for p in man.get("paths", []) if p.get("path_type") == 0]
print("type0_tags", tags)
rc = 0
for tag in tags:
    r = subprocess.run(
        [
            "./venv/bin/python",
            "run_solve_dimacs.py",
            "--cnf-dir",
            str(cnf_dir),
            "--path-tag",
            tag,
            "--parse-only",
            "--job-id",
            "1",
            "--total",
            "1",
        ],
        cwd=str(Path.cwd()),
    )
    print("solve", tag, "rc", r.returncode)
    rc |= r.returncode
    rj = cnf_dir / f"{tag}_result.json"
    if rj.exists():
        d = json.loads(rj.read_text(encoding="utf-8"))
        print(
            f"RESULT {tag} status={d.get('status')} "
            f"runtime_s={d.get('solver_runtime_seconds')}"
        )
sys.exit(rc)
PY
  set -e
  echo "DONE $name $(date -Is)" | tee -a "$LOG"
done < "$JOBS"

echo "==== COMPARE $(date -Is) ====" | tee -a "$LOG"
"$PYTHON" "$ROOT/compare_type0_break.py" \
  --jobs-file "$JOBS" \
  --initdata-subdir "$(basename "$OUT_ROOT")" \
  --out results_txt/type0_break_comparison_d5.txt \
  --json-out results_txt/type0_break_comparison_d5.json \
  | tee -a "$LOG"
echo "END $(date -Is)" | tee -a "$LOG"
