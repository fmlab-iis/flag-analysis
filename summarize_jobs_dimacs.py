#!/usr/bin/env python3
"""Aggregate per-job DIMACS solve stats for all entries in jobs.txt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dimacs_export_protocol import aggregate_job_solve_stats
from run_solve_dimacs import _load_manifest


def _read_jobs(jobs_file: Path) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    for line in jobs_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        jobs.append(
            {
                "protocol": parts[0].strip(),
                "config": parts[1].strip(),
                "t": parts[2].strip() if len(parts) > 2 and parts[2].strip() else "1",
            }
        )
    return jobs


def _load_job_rows(cnf_dir: Path) -> List[Dict[str, Any]]:
    if not cnf_dir.is_dir():
        return []
    manifest = _load_manifest(cnf_dir)
    rows: List[Dict[str, Any]] = []
    for result_file in sorted(cnf_dir.glob("path_*_result.json")):
        path_tag = result_file.name.replace("_result.json", "")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        path_info = next(
            (p for p in manifest.get("paths", []) if p.get("path_tag") == path_tag),
            {},
        )
        rows.append(
            {
                "path_index": path_info.get("path_index", 0),
                "path_type": path_info.get("path_type", "?"),
                "last_instr": path_info.get("last_instr", ""),
                "status": data.get("status", "unknown"),
                "solver_runtime_seconds": data.get("solver_runtime_seconds", 0.0),
                "total_dimacs_vars": data.get("total_dimacs_vars", 0),
                "total_clauses": data.get("total_clauses", 0),
                "sat_query_count": data.get("sat_query_count", 0),
            }
        )
    return rows


def _load_wall_time(cnf_dir: Path) -> Optional[float]:
    timing_path = cnf_dir / "job_timing.json"
    if not timing_path.is_file():
        return None
    try:
        value = json.loads(timing_path.read_text(encoding="utf-8")).get("wall_time_seconds")
        return float(value) if value is not None else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def summarize_jobs(root: Path, jobs_file: Path) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for job in _read_jobs(jobs_file):
        config = job["config"]
        stem = Path(config).stem
        cnf_dir = root / "cnf_out" / stem
        rows = _load_job_rows(cnf_dir)
        agg = aggregate_job_solve_stats(rows)
        wall_time_s = _load_wall_time(cnf_dir)
        summary = {
            "config": config,
            "config_stem": stem,
            "t": job["t"],
            "cnf_dir": str(cnf_dir),
            "wall_time_seconds": wall_time_s,
            **agg,
            "status": "ok" if rows else "missing",
        }
        summaries.append(summary)
    return summaries


def _format_table(summaries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("=" * 120)
    lines.append("jobs.txt DIMACS batch summary (per config)")
    lines.append(
        "  config_stem | paths | wall_s | solver_s | sat_calls | sat_paths | "
        "vars_min | vars_max | clauses_min | clauses_max | status"
    )
    for row in summaries:
        wall = row.get("wall_time_seconds")
        wall_s = f"{wall:.3f}" if wall is not None else "n/a"
        lines.append(
            "  "
            f"{row.get('config_stem', ''):<40} | "
            f"{row.get('path_count', 0):>5} | "
            f"{wall_s:>6} | "
            f"{row.get('total_solver_runtime_s', 0.0):>8.3f} | "
            f"{row.get('total_sat_query_count', 0):>9} | "
            f"{row.get('sat_path_count', 0):>9} | "
            f"{row.get('min_dimacs_vars', 0):>8} | "
            f"{row.get('max_dimacs_vars', 0):>8} | "
            f"{row.get('min_clauses', 0):>11} | "
            f"{row.get('max_clauses', 0):>11} | "
            f"{row.get('status', '')}"
        )
    lines.append("=" * 120)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize DIMACS stats for jobs.txt")
    parser.add_argument("--jobs-file", default="jobs.txt", help="Tab-separated jobs file")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument(
        "--out",
        default="results_txt/jobs_dimacs_summary.txt",
        help="Write human-readable table here",
    )
    parser.add_argument(
        "--json-out",
        default="results_txt/jobs_dimacs_summary.json",
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

    summaries = summarize_jobs(root, jobs_file)
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
