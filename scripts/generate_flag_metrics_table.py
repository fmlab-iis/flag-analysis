#!/usr/bin/env python3
"""Generate LaTeX table for flag-raised batch metrics (same column order as control-flow table)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Column order matches results_txt/control_flow_metrics_table.tex
TABLE_JOBS: list[tuple[str, str]] = [
    ("[[5,1,3]]_low_depth_protocol_config", "[[5,1,3]]_low_depth"),
    ("[[5,1,3]]_[1,1,1,1]_T_lai_d_3_protocol_config", "[1,1,1,1]^T"),
    ("[[5,1,3]]_[2,2]_lai_3_protocol_config", "[2,2]"),
    ("[[5,1,3]]_[2,2]_T_lai_3_protocol_config", "[2,2]^T"),
    ("[[5,1,3]]_[2,2]_T_fix_2_lai_3_protocol_config", "[2,2]^T fix"),
    ("[[5,1,3]]_origin_config", "Origin"),
    ("low_depth_7_1_3_w_6", "Low-depth w6"),
    ("[[7,1,3]]_low_depth_2_config", "Low-depth_2"),
    ("[[7,1,3]]_lai_3_protocol", "[1,1,1,1]^T"),
    ("[[9,1,3]]_chris_protocol_config", "Seq."),
    ("[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config", "[2,2,2,1,1,1]^T"),
    ("[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config", "[1,1,1,...]^T"),
    ("[[17,1,5]]_[2,2,2,1,1]_T_lai_5_protocol_config", "[2,2,2,1,1]^T"),
    ("[[17,1,5]]_[1,1,1,1,1,...]_T_config", "[1,1,1,1,1,...]^T"),
    ("chao_du_[[17,1,5]]_config", "Par. flag"),
    ("[[17,1,5]]_chris_config", "Seq."),
    ("chao_du_[[19,1,5]]_config", "Par. flag"),
    ("[[25,1,5]]_chris_protocol_config", "Seq."),
]


def _format_time(value: float) -> str:
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.3f}"


def _format_range(min_val: int, max_val: int) -> str:
    if min_val == max_val:
        return str(min_val)
    return f"{min_val}--{max_val}"


def _parse_metrics(stem: str) -> dict[str, str]:
    path = ROOT / "results_txt" / f"{stem}_flag_raised_metrics.txt"
    missing = {
        "fail_total": "?",
        "runtime": "?",
        "peak_mb": "?",
        "sat_calls": "?",
        "sat_time": "?",
        "vars_range": "?",
        "clauses_range": "?",
    }
    if not path.is_file():
        return missing
    text = path.read_text(encoding="utf-8")

    m = re.search(r"Failed circuits:\s*(\d+)/(\d+)", text)
    fail_total = f"{m.group(1)}/{m.group(2)}" if m else "?"

    m2 = re.search(r"Total runtime:\s*([\d.]+)", text)
    runtime = f"{float(m2.group(1)):.3f}" if m2 else "?"

    m3 = re.search(r"Max peak RSS \(across circuits\):\s*([\d.]+)", text)
    peak = f"{float(m3.group(1)):.1f}" if m3 else "?"

    m4 = re.search(r"SAT solver calls \(sum sat_query_count\):\s*(\d+)", text)
    sat_calls = m4.group(1) if m4 else "?"

    m5 = re.search(r"SAT solver time \(sum\):\s*([\d.]+)", text)
    sat_time = _format_time(float(m5.group(1))) if m5 else "?"

    m6 = re.search(r"DIMACS variables \(min / max\):\s*(\d+)\s*/\s*(\d+)", text)
    if not m6:
        m6 = re.search(r"Z3 variables \(min / max\):\s*(\d+)\s*/\s*(\d+)", text)
    vars_range = _format_range(int(m6.group(1)), int(m6.group(2))) if m6 else "?"

    m7 = re.search(r"Clauses \(min / max\):\s*(\d+)\s*/\s*(\d+)", text)
    clauses_range = _format_range(int(m7.group(1)), int(m7.group(2))) if m7 else "?"

    return {
        "fail_total": fail_total,
        "runtime": runtime,
        "peak_mb": peak,
        "sat_calls": sat_calls,
        "sat_time": sat_time,
        "vars_range": vars_range,
        "clauses_range": clauses_range,
    }


def _row_cells(key: str) -> str:
    vals = [_parse_metrics(stem)[key] for stem, _ in TABLE_JOBS]
    return " & ".join(vals)


def build_tex() -> str:
    return r"""\begin{table}[htbp]
\centering
\caption{Experimental results of the flag-raised verification (Step 3). Flag-raised row: number of failed flag circuits over total checked per config (Z3 export + external SAT on \texttt{check\_flag\_raised}; faults at any gate, at most $w$ sites; flag required when error weight $> w$). Time is total flag-check runtime per config. Peak memory, SAT calls, total SAT time, \#vars, and \#clauses are from the flag-raised DIMACS pipeline.}
\label{tab:experiment-results-flag-raised}
\resizebox{\linewidth}{!}{%
\begin{tabular}{l ccccccccc cccccccccc}
\toprule
\textbf{Metric} & \multicolumn{18}{c}{\textbf{Experimental Setups}} \\
\midrule
Code & $[[5,1,3]]$ & $[[5,1,3]]$ & $[[5,1,3]]$ & $[[5,1,3]]$ & $[[5,1,3]]$ & $[[5,1,3]]$ & $[[7,1,3]]$ & $[[7,1,3]]$ & $[[7,1,3]]$ & $[[9,1,3]]$ & $[[19,1,5]]$ & $[[19,1,5]]$ & $[[17,1,5]]$ & $[[17,1,5]]$ & $[[17,1,5]]$ & $[[17,1,5]]$ & $[[19,1,5]]$ & $[[25,1,5]]$ \\
Optimization & Low-depth & $[1,1,1,1]^{T}$ & $[2,2]$ & $[2,2]^{T}$ & $[2,2]^{T}$ fix & Origin & Low-depth & Low-depth$_2$ & $[1,1,1,1]^{T}$ & Seq. & $[2,2,2,1,1,1]^{T}$ & $[1,1,1,\dots]^{T}$ & $[2,2,2,1,1]^{T}$ & $[1,1,1,1,1,\dots]^{T}$ & Par. flag & Seq. & Par. flag & Seq. \\
Protocol & [Bha23] & [LL25] $d{=}3$ & [LL25] $d{=}3$ & [LL25] $d{=}3$ & [LL25] $d{=}3$ & [LL25] $d{=}3$ & [Bha23] & [Bha23] & [LL25] $d{=}3$ & Chris $d{=}3$ & [LL25] $d{=}5$ & [LL25] $d{=}5$ & [LL25] $d{=}5$ & [LL25] $d{=}5$ & [Du24] $d{=}5$ & Chris $d{=}5$ & [Du24] $d{=}5$ & Chris $d{=}5$ \\
\midrule
Flag raised & FLAG_FAIL_ROW \\
\midrule
Time (s) & WALL_TIME_ROW \\
Peak Mem (MB) & PEAK_ROW \\
\midrule
SAT Calls & SAT_CALLS_ROW \\
Total SAT Time (s) & SAT_TIME_SUM_ROW \\
\#Vars & VARS_ROW \\
\#Clauses & CLAUSES_ROW \\
\bottomrule
\end{tabular}
}%
\end{table}
""".replace("FLAG_FAIL_ROW", _row_cells("fail_total")).replace(
        "WALL_TIME_ROW", _row_cells("runtime")
    ).replace("PEAK_ROW", _row_cells("peak_mb")).replace(
        "SAT_CALLS_ROW", _row_cells("sat_calls")
    ).replace("SAT_TIME_SUM_ROW", _row_cells("sat_time")).replace(
        "VARS_ROW", _row_cells("vars_range")
    ).replace("CLAUSES_ROW", _row_cells("clauses_range"))


def main() -> None:
    out = ROOT / "results_txt" / "flag_raised_metrics_table.tex"
    out.write_text(build_tex() + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
