#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=parallel_limits.sh
source "$ROOT/scripts/parallel_limits.sh"
pl_init_dimacs_limits solve
pl_init_tmpdir "$ROOT"

PYTHON="$ROOT/venv/bin/python"
CNF_DIR="${1:?usage: run_solve_paths.sh cnf_out/config_stem}"

if [[ ! -d "$CNF_DIR" ]]; then
  echo "Error: directory not found: $CNF_DIR" >&2
  exit 1
fi

mapfile -t TAGS < <(ls "$CNF_DIR"/path_*.cnf 2>/dev/null | xargs -n1 basename | sed 's|\.cnf||' | sort)
TOTAL=${#TAGS[@]}

if (( TOTAL == 0 )); then
  echo "Error: no path_*.cnf files in $CNF_DIR" >&2
  exit 1
fi

CONFIG_STEM="$(basename "$CNF_DIR")"
if [[ -f "$CNF_DIR/manifest.json" ]]; then
  CONFIG_STEM="$("$PYTHON" -c "import json; print(json.load(open('$CNF_DIR/manifest.json'))['config_stem'])")"
fi

PATH_JOBS="$PATH_JOBS"
if (( PATH_JOBS > TOTAL )); then
  PATH_JOBS=$TOTAL
fi
read -r -a MEMFREE_SOLVER <<< "$(pl_memfree_arg "$SOLVER_MEM_MB")"

if [[ -n "${DIMACS_VERBOSE:-}" ]]; then
  echo "Protocol: $CONFIG_STEM"
  pl_print_dimacs_limits solve
  echo "Solving paths: 0/$TOTAL (${PATH_JOBS} workers)"
  echo ""
fi

printf '%s\n' "${TAGS[@]}" | parallel -j "$PATH_JOBS" --tmpdir "$PARALLEL_TMPDIR" "${MEMFREE_SOLVER[@]}" --line-buffer --joblog "$CNF_DIR/solve.log" \
  "$ROOT/scripts/run_one_path.sh" "$CNF_DIR" {} {#} "$TOTAL"

SAT_COUNT="$("$PYTHON" -c "
import json
import sys
from pathlib import Path

cnf_dir = Path(sys.argv[1])
sat = 0
for result_path in cnf_dir.glob('path_*_result.json'):
    try:
        if json.loads(result_path.read_text(encoding='utf-8')).get('status') == 'sat':
            sat += 1
    except (OSError, json.JSONDecodeError, ValueError):
        pass
print(sat)
" "$CNF_DIR")"
echo "${CONFIG_STEM}: SAT paths ${SAT_COUNT}/${TOTAL}"

echo ""
set +e
"$PYTHON" run_solve_dimacs.py --cnf-dir "$CNF_DIR" --summarize --metrics-dir results_txt
summarize_rc=$?
set -e
exit "$summarize_rc"
