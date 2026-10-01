#!/usr/bin/env python3
"""Aggregate per-job control-flow solve stats for all entries in jobs.txt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from summarize_jobs_dimacs import _read_jobs, _load_job_rows


def _load_job_aggregate(cnf_dir: Path) -> Dict[str, Any]:
    agg_path = cnf_dir / "job_aggregate.json"
    if not agg_path.is_file():
        return {}
    try:
        return json.loads(agg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _control_flow_fail_counts(cnf_dir: Path, agg: Dict[str, Any]) -> Dict[str, int]:
    """Return fail_path / pred_syn / break counts from aggregate or path results."""
    if agg.get("pred_syn_fail_count") is not None:
        return {
            "fail_path_count": int(agg.get("fail_path_count", agg.get("sat_path_count", 0)) or 0),
            "pred_syn_fail_count": int(agg.get("pred_syn_fail_count", 0) or 0),
            "pred_syn_path_count": int(agg.get("pred_syn_path_count", 0) or 0),
            "break_fail_count": int(agg.get("break_fail_count", 0) or 0),
            "break_path_count": int(agg.get("break_path_count", 0) or 0),
            "path_count": int(agg.get("path_count", 0) or 0),
        }

    rows = _load_job_rows(cnf_dir)
    if not rows:
        return {
            "fail_path_count": 0,
            "pred_syn_fail_count": 0,
            "pred_syn_path_count": 0,
            "break_fail_count": 0,
            "break_path_count": 0,
            "path_count": 0,
        }
    sat_rows = [r for r in rows if r.get("status") == "sat"]
    pred_syn_total = sum(1 for r in rows if r.get("path_type") == 1)
    pred_syn_fail = sum(
        1 for r in rows if r.get("path_type") == 1 and r.get("status") == "sat"
    )
    break_total = sum(1 for r in rows if r.get("path_type") == 0)
    break_fail = sum(
        1 for r in rows if r.get("path_type") == 0 and r.get("status") == "sat"
    )
    return {
        "fail_path_count": len(sat_rows),
        "pred_syn_fail_count": pred_syn_fail,
        "pred_syn_path_count": pred_syn_total,
        "break_fail_count": break_fail,
        "break_path_count": break_total,
        "path_count": len(rows),
    }


def summarize_jobs(
    root: Path,
    jobs_file: Path,
    *,
    cnf_subdir: str = "cnf_out_control_flow",
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for job in _read_jobs(jobs_file):
        config = job["config"]
        stem = Path(config).stem
        cnf_dir = root / cnf_subdir / stem
        agg = _load_job_aggregate(cnf_dir)
        counts = _control_flow_fail_counts(cnf_dir, agg)
        solved = counts["path_count"]
        fail_paths = counts["fail_path_count"]
        summary = {
            "config": config,
            "config_stem": stem,
            "t": job["t"],
            "cnf_dir": str(cnf_dir),
            "solved_paths": solved,
            "fail_path_count": fail_paths,
            "pred_syn_fail_count": counts["pred_syn_fail_count"],
            "pred_syn_path_count": counts["pred_syn_path_count"],
            "break_fail_count": counts["break_fail_count"],
            "break_path_count": counts["break_path_count"],
            "total_solver_runtime_s": agg.get("total_solver_runtime_s", 0.0),
            "wall_time_seconds": agg.get("wall_time_seconds"),
            "status": "ok" if solved else "missing",
            "verification": "pass" if solved and fail_paths == 0 else ("fail" if solved else "missing"),
        }
        summaries.append(summary)
    return summaries


def _format_table(summaries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("=" * 110)
    lines.append("control-flow batch summary (per config)")
    lines.append(
        "  config_stem | solved | fail_paths | pred_syn_fail | break_fail | "
        "solver_s | verify | status"
    )
    for row in summaries:
        pred = row.get("pred_syn_fail_count")
        pred_s = (
            f"{pred}/{row.get('pred_syn_path_count', '?')}"
            if pred is not None and row.get("pred_syn_path_count") is not None
            else "n/a"
        )
        brk = row.get("break_fail_count")
        brk_s = (
            f"{brk}/{row.get('break_path_count', '?')}"
            if brk is not None and row.get("break_path_count") is not None
            else "n/a"
        )
        lines.append(
            "  "
            f"{row.get('config_stem', ''):<40} | "
            f"{row.get('solved_paths', 0):>6} | "
            f"{row.get('fail_path_count', 0):>10} | "
            f"{pred_s:>13} | "
            f"{brk_s:>10} | "
            f"{row.get('total_solver_runtime_s', 0.0):>8.3f} | "
            f"{row.get('verification', ''):>6} | "
            f"{row.get('status', '')}"
        )
    lines.append("=" * 110)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize control-flow stats for jobs.txt")
    parser.add_argument("--jobs-file", default="jobs.txt", help="Tab-separated jobs file")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument(
        "--cnf-subdir",
        default="cnf_out_control_flow",
        help="CNF output subdirectory under root (default: cnf_out_control_flow)",
    )
    parser.add_argument(
        "--out",
        default="results_txt/jobs_control_flow_summary.txt",
        help="Write human-readable table here",
    )
    parser.add_argument(
        "--json-out",
        default="results_txt/jobs_control_flow_summary.json",
        help="Write machine-readable summary here",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    jobs_file = Path(args.jobs_file)
    if not jobs_file.is_absolute():
        jobs_file = (root / jobs_file).resolve()
    if not jobs_file.is_file():
        print(f"Jobs file not found: {jobs_file}", file=sys.stderr)
        return 1

    summaries = summarize_jobs(root, jobs_file, cnf_subdir=args.cnf_subdir)
    table = _format_table(summaries)
    print(table, end="")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(table, encoding="utf-8")

    json_path = Path(args.json_out)
    if not json_path.is_absolute():
        json_path = root / json_path
    json_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
