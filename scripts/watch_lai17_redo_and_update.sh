#!/usr/bin/env bash
# Wait for LAI17 Break Kissat + flag w=1 redos, then refresh metrics/tables.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
LOG="$ROOT/results_txt/run_lai17_redo_watcher.log"
{
  echo "WATCHER_START $(date -Is)"

  # Wait until no LAI17 kissat / solve_dimacs / flag processes remain
  while pgrep -f 'kissat .*\[\[17,1,5\]\]' >/dev/null \
     || pgrep -f 'run_solve_dimacs.py --cnf-dir .*\[\[17,1,5\]\]' >/dev/null \
     || pgrep -f 'run_flag_raised_protocol.py --config .*\[\[17,1,5\]\].*_T' >/dev/null \
     || pgrep -f 'run_d5_lai17_kissat_notimeout' >/dev/null; do
    echo "still running $(date -Is)"
    sleep 60
  done
  echo "JOBS_DONE $(date -Is)"

  echo "==== BREAK results ===="
  for d in \
    'cnf_out_control_flow/[[17,1,5]]_[1,1,1,1,1,...]_T_config' \
    'cnf_out_control_flow/[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config' \
    'cnf_out_control_flow_initdata_d5_fixed/[[17,1,5]]_[1,1,1,1,1,...]_T_config' \
    'cnf_out_control_flow_initdata_d5_fixed/[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config'
  do
    "$PYTHON" - "$d/path_000_result.json" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])
print(p)
if not p.exists():
    print('  MISSING'); raise SystemExit
r=json.loads(p.read_text())
print(f"  status={r.get('status')} runtime_s={r.get('solver_runtime_seconds')} vars={r.get('total_dimacs_vars')}")
PY
  done

  echo "==== SUMMARIZE baseline Break ===="
  for cnf_dir in \
    'cnf_out_control_flow/[[17,1,5]]_[1,1,1,1,1,...]_T_config' \
    'cnf_out_control_flow/[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config'
  do
    "$PYTHON" run_solve_dimacs.py --cnf-dir "$cnf_dir" --summarize --metrics-dir results_txt
  done

  echo "==== COMPARE type0 ===="
  "$PYTHON" compare_type0_break.py \
    --jobs-file jobs_type0_d5.txt \
    --initdata-subdir cnf_out_control_flow_initdata_d5_fixed \
    --out results_txt/type0_break_comparison_d5.txt \
    --json-out results_txt/type0_break_comparison_d5.json

  echo "==== FLAG metrics w= ===="
  for f in \
    'results_txt/[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config_flag_raised_metrics.txt' \
    'results_txt/[[17,1,5]]_[1,1,1,1,1,...]_T_config_flag_raised_metrics.txt'
  do
    echo "-- $f"
    rg -n 'Parameters: w=|Total runtime:|SAT solver time|Max peak|Failed circuits|dimacs_vars|DIMACS variables|cryptominisat|Solver:' "$f" || true
  done

  echo "==== REBUILD flag table ===="
  "$PYTHON" scripts/generate_flag_metrics_table.py || true

  echo "==== UPDATE combined LAI17 cells from new sources ===="
  "$PYTHON" - <<'PY'
import re
from pathlib import Path

root = Path('.')
rt = root / 'results_txt'

def parse_cf(stem):
    t = (rt / f'{stem}_control_flow_proof_metrics.txt').read_text()
    bugs = int(re.search(r'Paths with SAT result:\s*(\d+)', t).group(1))
    sat = float(re.search(r'Solver time \(sum of paths\):\s*([\d.]+)', t).group(1))
    peak = float(re.search(r'Max peak RSS \(across paths\):\s*([\d.]+)', t).group(1))
    calls = int(re.search(r'SAT solver calls \(sum sat_query_count\):\s*(\d+)', t).group(1))
    vmin, vmax = map(int, re.search(r'DIMACS variables \(min / max\):\s*(\d+)\s*/\s*(\d+)', t).groups())
    cmin, cmax = map(int, re.search(r'Clauses \(min / max\):\s*(\d+)\s*/\s*(\d+)', t).groups())
    return dict(bugs=bugs, sat=sat, peak=peak, calls=calls, vmin=vmin, vmax=vmax, cmin=cmin, cmax=cmax)

def parse_flag(stem):
    t = (rt / f'{stem}_flag_raised_metrics.txt').read_text()
    w = int(re.search(r'Parameters: w=(\d+)', t).group(1))
    wall = float(re.search(r'Total runtime:\s*([\d.]+)', t).group(1))
    sat = float(re.search(r'SAT solver time \(sum\):\s*([\d.]+)', t).group(1))
    peak = float(re.search(r'Max peak RSS \(across circuits\):\s*([\d.]+)', t).group(1))
    calls = int(re.search(r'SAT solver calls \(sum sat_query_count\):\s*(\d+)', t).group(1))
    failed = int(re.search(r'Failed circuits:\s*(\d+)', t).group(1))
    vmin = vmax = int(re.search(r'DIMACS variables \(min / max\):\s*(\d+)\s*/\s*(\d+)', t).group(1))
    cmin = cmax = int(re.search(r'Clauses \(min / max\):\s*(\d+)\s*/\s*(\d+)', t).group(1))
    return dict(w=w, wall=wall, sat=sat, peak=peak, calls=calls, failed=failed, vmin=vmin, vmax=vmax, cmin=cmin, cmax=cmax)

def fmt_time(x):
    return f'{x:.1f}' if x >= 10 else f'{x:.3f}'

def fmt_peak(x):
    return f'{x:.1f}'

exports = {
    '[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config': 1012.6,
    '[[17,1,5]]_[1,1,1,1,1,...]_T_config': 1201.2,
}
synd = {
    '[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config': 0.254,
    '[[17,1,5]]_[1,1,1,1,1,...]_T_config': 0.261,
}

stems = [
    '[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config',
    '[[17,1,5]]_[1,1,1,1,1,...]_T_config',
]
vals = {}
for stem in stems:
    cf = parse_cf(stem)
    fl = parse_flag(stem)
    assert fl['w'] == 1, f"{stem} flag w={fl['w']} expected 1"
    peak = max(cf['peak'], fl['peak'])
    calls = cf['calls'] + fl['calls'] + 2  # + syndrome circuits
    sat_time = cf['sat'] + fl['sat']
    vmin = min(cf['vmin'], fl['vmin']); vmax = max(cf['vmax'], fl['vmax'])
    cmin = min(cf['cmin'], fl['cmin']); cmax = max(cf['cmax'], fl['cmax'])
    cpu = exports[stem] + cf['sat'] + fl['wall'] + synd[stem]
    vals[stem] = dict(
        bugs=cf['bugs'], flag_bugs=fl['failed'],
        peak=peak, calls=calls, sat_time=sat_time, cpu=cpu,
        vmin=vmin, vmax=vmax, cmin=cmin, cmax=cmax,
        cf=cf, fl=fl,
    )
    print(stem)
    print(f"  CF bugs={cf['bugs']} sat={cf['sat']:.1f} peak={cf['peak']:.1f}")
    print(f"  Flag w={fl['w']} wall={fl['wall']:.1f} sat={fl['sat']:.1f} peak={fl['peak']:.1f} fail={fl['failed']}")
    print(f"  Combined peak={peak:.1f} sat={sat_time:.1f} cpu={cpu:.1f} vars={vmin}--{vmax}")

# Patch control_flow_metrics_table.tex cols 13-14 (0-based index 12,13 in 18-col rows)
cf_tex = (rt / 'control_flow_metrics_table.tex').read_text()
# Build replacements from known current pattern for LAI17 pair in CF table
# Safer: regenerate the two CF columns numerically into the existing lines via python split

def patch_18col_row(text, row_prefix, idx_a, idx_b, a, b):
    lines = text.splitlines(True)
    out = []
    for line in lines:
        if line.startswith(row_prefix):
            # keep leading label through first &
            parts = line.rstrip('\n').split('&')
            # parts[0] is label, parts[1..] are cells; last may have \\
            cells = [p.strip() for p in parts[1:]]
            if cells:
                cells[-1] = cells[-1].replace('\\', '').strip()
            cells[idx_a] = a
            cells[idx_b] = b
            # rebuild
            rebuilt = parts[0] + ' & ' + ' & '.join(cells) + ' \\\\\n'
            out.append(rebuilt)
        else:
            out.append(line)
    return ''.join(out)

s222 = vals['[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config']
s111 = vals['[[17,1,5]]_[1,1,1,1,1,...]_T_config']

cf_tex = patch_18col_row(cf_tex, 'Control flow', 12, 13,
    ('1 bug' if s222['bugs'] else '0'),
    ('1 bug' if s111['bugs'] else '0'))
cf_tex = patch_18col_row(cf_tex, 'Peak Mem (MB)', 12, 13, fmt_peak(s222['cf']['peak']), fmt_peak(s111['cf']['peak']))
cf_tex = patch_18col_row(cf_tex, 'SAT Calls', 12, 13, str(s222['cf']['calls']), str(s111['cf']['calls']))
cf_tex = patch_18col_row(cf_tex, 'Total SAT Time (s)', 12, 13, fmt_time(s222['cf']['sat']), fmt_time(s111['cf']['sat']))
cf_tex = patch_18col_row(cf_tex, r'\#Vars', 12, 13, f"{s222['cf']['vmin']}--{s222['cf']['vmax']}", f"{s111['cf']['vmin']}--{s111['cf']['vmax']}")
cf_tex = patch_18col_row(cf_tex, r'\#Clauses', 12, 13, f"{s222['cf']['cmin']}--{s222['cf']['cmax']}", f"{s111['cf']['cmin']}--{s111['cf']['cmax']}")
(rt / 'control_flow_metrics_table.tex').write_text(cf_tex)
print('updated control_flow_metrics_table.tex')

# combined_results_table.tex part2: cols are 7 wide; LAI17 are cols 2 and 3 (1-based) => indices 1,2
comb = (rt / 'combined_results_table.tex').read_text()

def patch_part2_row(text, row_prefix, idx_a, idx_b, a, b):
    # Only patch inside part 2 tabular (after second begin{tabular})
    parts = text.split('\\begin{tabular}{l ccccccc}', 1)
    if len(parts) != 2:
        raise SystemExit('part2 tabular not found')
    head, rest = parts
    tab, tail = rest.split('\\end{tabular}', 1)
    lines = tab.splitlines(True)
    new_lines = []
    for line in lines:
        if line.startswith(row_prefix) or (row_prefix.startswith('FTEC') and line.startswith(row_prefix)):
            # Control flow bugs / Flag bugs / Peak Memory etc.
            pass
        if line.lstrip().startswith(row_prefix) or line.startswith(row_prefix):
            segs = line.rstrip('\n').split('&')
            cells = [p.strip() for p in segs[1:]]
            if cells:
                cells[-1] = cells[-1].replace('\\', '').strip()
            cells[idx_a] = a
            cells[idx_b] = b
            new_lines.append(segs[0] + ' & ' + ' & '.join(cells) + ' \\\\\n')
        else:
            new_lines.append(line)
    return head + '\\begin{tabular}{l ccccccc}' + ''.join(new_lines) + '\\end{tabular}' + tail

# row prefixes in part2
comb = patch_part2_row(comb, 'Control flow bugs (path)', 1, 2, str(s222['bugs']), str(s111['bugs']))
comb = patch_part2_row(comb, 'Flag bugs (circuit)', 1, 2, str(s222['flag_bugs']), str(s111['flag_bugs']))
comb = patch_part2_row(comb, 'Peak Memory (MB)', 1, 2, fmt_peak(s222['peak']), fmt_peak(s111['peak']))
comb = patch_part2_row(comb, 'Total SAT Calls', 1, 2, str(s222['calls']), str(s111['calls']))
comb = patch_part2_row(comb, 'Total SAT Time (s)', 1, 2, fmt_time(s222['sat_time']), fmt_time(s111['sat_time']))
# There are two Total CPU Time rows (protocol + FTEC); patch only protocol block by doing first occurrence carefully
# Simpler: replace protocol SAT Solver Total CPU Time line which comes before FTEC section in part2
def patch_first_matching(text, marker_before, row_prefix, idx_a, idx_b, a, b):
    i = text.find(marker_before)
    if i < 0:
        raise SystemExit(f'marker not found {marker_before}')
    # find part2 SAT Solver section after Code & $[[19
    j = text.find('Code & $[[19,1,5]]$', i)
    k = text.find('\\multicolumn{8}{c}{\\textbf{FTEC}}', j)
    block = text[j:k]
    lines = block.splitlines(True)
    new = []
    for line in lines:
        if line.startswith(row_prefix):
            segs = line.rstrip('\n').split('&')
            cells = [p.strip() for p in segs[1:]]
            if cells:
                cells[-1] = cells[-1].replace('\\', '').strip()
            cells[idx_a] = a
            cells[idx_b] = b
            new.append(segs[0] + ' & ' + ' & '.join(cells) + ' \\\\\n')
        else:
            new.append(line)
    return text[:j] + ''.join(new) + text[k:]

comb = patch_first_matching(comb, '% ==================== PART 2', 'Total CPU Time (s)', 1, 2, fmt_time(s222['cpu']), fmt_time(s111['cpu']))
comb = patch_part2_row(comb, r'\#Vars', 1, 2, f"{s222['vmin']}--{s222['vmax']}", f"{s111['vmin']}--{s111['vmax']}")
# #Vars appears in protocol and FTEC; patch_part2_row patches ALL matching lines — bad for FTEC.
# Re-read file approach: only patch protocol #Vars/#Clauses by marker
comb_text = (rt / 'combined_results_table.tex').read_text() if False else comb

# Re-do #Vars/#Clauses only in protocol SAT section of part2
def patch_protocol_sat_rows(text, updates):
    j = text.find('Code & $[[19,1,5]]$')
    k = text.find('\\multicolumn{8}{c}{\\textbf{FTEC}}', j)
    block = text[j:k]
    lines = block.splitlines(True)
    new = []
    for line in lines:
        matched = False
        for row_prefix, idx_a, idx_b, a, b in updates:
            if line.startswith(row_prefix):
                segs = line.rstrip('\n').split('&')
                cells = [p.strip() for p in segs[1:]]
                if cells:
                    cells[-1] = cells[-1].replace('\\', '').strip()
                cells[idx_a] = a
                cells[idx_b] = b
                new.append(segs[0] + ' & ' + ' & '.join(cells) + ' \\\\\n')
                matched = True
                break
        if not matched:
            new.append(line)
    return text[:j] + ''.join(new) + text[k:]

comb = patch_protocol_sat_rows(comb, [
    (r'\#Vars', 1, 2, f"{s222['vmin']}--{s222['vmax']}", f"{s111['vmin']}--{s111['vmax']}"),
    (r'\#Clauses', 1, 2, f"{s222['cmin']}--{s222['cmax']}", f"{s111['cmin']}--{s111['cmax']}"),
    ('Peak Memory (MB)', 1, 2, fmt_peak(s222['peak']), fmt_peak(s111['peak'])),
    ('Total SAT Calls', 1, 2, str(s222['calls']), str(s111['calls'])),
    ('Total SAT Time (s)', 1, 2, fmt_time(s222['sat_time']), fmt_time(s111['sat_time'])),
    ('Total CPU Time (s)', 1, 2, fmt_time(s222['cpu']), fmt_time(s111['cpu'])),
])
# also bugs rows are before SAT section
comb = patch_protocol_sat_rows(comb, [])  # no-op safeguard
# bugs are before Stim; patch via full part2 from Code to FTEC including verification rows
j = comb.find('Code & $[[19,1,5]]$')
k = comb.find('\\multicolumn{8}{c}{\\textbf{FTEC}}', j)
block = comb[j:k]
lines = block.splitlines(True)
new=[]
for line in lines:
    if line.startswith('Control flow bugs (path)'):
        segs=line.rstrip('\n').split('&'); cells=[p.strip() for p in segs[1:]]; cells[-1]=cells[-1].replace('\\','').strip()
        cells[1]=str(s222['bugs']); cells[2]=str(s111['bugs'])
        new.append(segs[0]+' & '+' & '.join(cells)+' \\\\\n')
    elif line.startswith('Flag bugs (circuit)'):
        segs=line.rstrip('\n').split('&'); cells=[p.strip() for p in segs[1:]]; cells[-1]=cells[-1].replace('\\','').strip()
        cells[1]=str(s222['flag_bugs']); cells[2]=str(s111['flag_bugs'])
        new.append(segs[0]+' & '+' & '.join(cells)+' \\\\\n')
    else:
        new.append(line)
comb = comb[:j] + ''.join(new) + comb[k:]

(rt / 'combined_results_table.tex').write_text(comb)
print('updated combined_results_table.tex')

# protocol_cpu_time_estimates: CF_SAT indices 12,13 (0-based) and flag totals; LAI d=5 line
est = (rt / 'protocol_cpu_time_estimates.txt').read_text()
# replace CF_SAT second line LAI17 values
est = re.sub(
    r'(CF_SAT:\n  [^\n]+\n  )([0-9.]+), ([0-9.]+), ([0-9.]+), ([0-9.]+),',
    lambda m: f"{m.group(1)}{m.group(2)}, {m.group(3)}, {s222['cf']['sat']:.1f}, {s111['cf']['sat']:.1f},",
    est,
    count=1,
)
# flag_Total_runtime LAI17 pair on second data line positions 3,4 of that line (indices 12,13 overall)
# second line currently: 5155.051, 5017.433, 1096.848, 3449.083, ...
est = re.sub(
    r'(flag_Total_runtime:\n  [^\n]+\n  )([0-9.]+), ([0-9.]+), ([0-9.]+), ([0-9.]+),',
    lambda m: f"{m.group(1)}{m.group(2)}, {m.group(3)}, {s222['fl']['wall']:.3f}, {s111['fl']['wall']:.3f},",
    est,
    count=1,
)
est = re.sub(
    r'(LAI d=5:\n  )([0-9.]+), ([0-9.]+), ([0-9.]+), ([0-9.]+)',
    lambda m: f"{m.group(1)}{m.group(2)}, {m.group(3)}, {s222['cpu']:.1f}, {s111['cpu']:.1f}",
    est,
    count=1,
)
(rt / 'protocol_cpu_time_estimates.txt').write_text(est)
print('updated protocol_cpu_time_estimates.txt')
print('DONE_TABLE_UPDATE')
PY

  echo "WATCHER_END $(date -Is)"
} 2>&1 | tee -a "$LOG"
