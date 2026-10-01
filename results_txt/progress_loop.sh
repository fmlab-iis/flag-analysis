#!/usr/bin/env bash
set -u
ROOT=/home/bionicle8699/flag_verification/flag-analysis
cd "$ROOT"
STATE=results_txt/.progress_state.json
WAKE_TS=results_txt/.progress_last_wake_ts
date +%s > "$WAKE_TS"

while true; do
  sleep 60
  NEW=$(./venv/bin/python - <<'PY'
import json, time, subprocess, re
from pathlib import Path

def procs():
    try:
        out = subprocess.check_output(
            "pgrep -af 'run_solve_dimacs|run_flag_raised_protocol|cryptominisat5 |run_one_flag_job|run_cms_watcher' "
            "| grep -v cursorsandbox | grep -v pgrep || true",
            shell=True, text=True,
        )
    except Exception:
        out = ""
    items = []
    for line in out.splitlines():
        if "run_flag_raised" in line:
            m = re.search(r"--config (\S+)", line)
            items.append("flag:" + (Path(m.group(1)).stem if m else "?"))
        elif "run_solve_dimacs" in line:
            m = re.search(r"cnf-dir (\S+)", line)
            items.append("break:" + (m.group(1) if m else "?"))
        elif "cryptominisat5" in line:
            items.append("cms")
        elif "run_one_flag" in line:
            items.append("flagjob")
        elif "run_cms_watcher" in line:
            items.append("watcher")
    return sorted(set(items))

def flag_pipe(p):
    if not p.exists():
        return "missing"
    t = p.read_text()
    if "dimacs_vars" in t or "DIMACS variables" in t or "cryptominisat" in t.lower():
        return "SAT"
    if "z3_vars" in t:
        return "SMT"
    return "?"

stems = [
    "[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config",
    "[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config",
    "[[17,1,5]]_chris_config",
    "chao_du_[[17,1,5]]_config",
    "chao_du_[[19,1,5]]_config",
    "[[25,1,5]]_chris_protocol_config",
]
flags = {s: flag_pipe(Path("results_txt") / f"{s}_flag_raised_metrics.txt") for s in stems}
break_ = {}
for stem in [
    "[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config",
    "[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config",
]:
    for sub in ["cnf_out_control_flow", "cnf_out_control_flow_initdata_d5_fixed"]:
        r = Path(sub) / stem / "path_000_result.json"
        key = f"{sub}|{stem}"
        if r.exists():
            j = json.loads(r.read_text())
            break_[key] = f"{j.get('status')}:{j.get('solver_runtime_seconds')}"
        else:
            break_[key] = "missing"

dones = []
for f in list(Path("results_txt").glob("run_flag*.log")) + list(
    Path("results_txt").glob("run_lai19_*_cms.log")
) + [Path("results_txt/run_cms_watcher.log")]:
    if not f.exists():
        continue
    for ln in f.read_text().splitlines():
        if any(
            k in ln
            for k in (
                "DONE",
                "exit=",
                "RESULT",
                "mid flags",
                "starting Chris",
                "Error",
                "FAIL",
                "compare exit",
            )
        ):
            dones.append(f"{f.name}:{ln.strip()}")

print(
    json.dumps(
        {
            "procs": procs(),
            "flags": flags,
            "break": break_,
            "dones": dones[-40:],
            "ts": time.time(),
        }
    )
)
PY
)
  OLD=$(cat "$STATE" 2>/dev/null || echo "{}")
  LAST=$(cat "$WAKE_TS" 2>/dev/null || echo 0)
  NOW=$(date +%s)
  DECISION=$(LAST="$LAST" NOW="$NOW" ./venv/bin/python - "$OLD" "$NEW" <<'PY'
import json, sys, os
old = json.loads(sys.argv[1])
new = json.loads(sys.argv[2])
reason = None
if old.get("procs") != new.get("procs"):
    reason = "procs"
elif old.get("flags") != new.get("flags"):
    reason = "flags"
elif old.get("break") != new.get("break"):
    reason = "break"
else:
    od = set(old.get("dones") or [])
    nd = set(new.get("dones") or [])
    if nd - od:
        reason = "log"
last = int(float(os.environ.get("LAST", "0") or 0))
now = int(float(os.environ.get("NOW", "0") or 0))
if reason:
    print(f"WAKE|{reason}")
elif (now - last) >= 600:
    print("WAKE|heartbeat")
else:
    print("SKIP")
PY
)
  echo "$NEW" > "$STATE"
  if [[ "$DECISION" == WAKE* ]]; then
    date +%s > "$WAKE_TS"
    echo "AGENT_LOOP_WAKE_flagbreak_progress {\"prompt\":\"Process/progress update (reason=${DECISION#WAKE|}). Read results_txt/.progress_state.json; tell the user what changed: processes started/stopped, Break status, flag SMT/SAT. Be brief.\"}"
  fi
done
