#!/usr/bin/env python3
"""Aggregate per-job stim_syndrome stats for all entries in jobs.txt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from summarize_jobs_dimacs import _read_jobs


def _load_job_json(root: Path, stem: str) -> Dict[str, Any]:
    json_path = root / "results_txt" / f"{stem}_stim_syndrome.json"
    if not json_path.is_file():
        return {}
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def summarize_jobs(root: Path, jobs_file: Path) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for job in _read_jobs(jobs_file):
        config = job["config"]
        stem = Path(config).stem
        data = _load_job_json(root, stem)
        if not data:
            summaries.append(
                {
                    "config": config,
                    "config_stem": stem,
                    "t": job["t"],
                    "circuits_total": 0,
                    "pass_count": 0,
                    "fail_count": 0,
                    "error_count": 0,
                    "wall_time_seconds": None,
                    "total_runtime_s": None,
                    "peak_rss_bytes": None,
                    "peak_rss_mb": None,
                    "job_status": "missing",
                    "verification": "missing",
                }
            )
            continue
        job_status = data.get("job_status", "FAIL")
        summaries.append(
            {
                "config": config,
                "config_stem": stem,
                "t": job["t"],
                "circuits_total": data.get("circuits_total", 0),
                "pass_count": data.get("pass_count", 0),
                "fail_count": data.get("fail_count", 0),
                "error_count": data.get("error_count", 0),
                "wall_time_seconds": data.get("wall_time_seconds"),
                "total_runtime_s": data.get("total_runtime_s"),
                "peak_rss_bytes": data.get("peak_rss_bytes"),
                "peak_rss_mb": data.get("peak_rss_mb"),
                "job_status": job_status,
                "verification": "pass" if job_status == "PASS" else job_status.lower(),
            }
        )
    return summaries


def _format_table(summaries: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("=" * 120)
    lines.append("jobs.txt stim_syndrome batch summary (per config)")
    lines.append(
        "  config_stem | circuits | pass | fail | error | runtime_s | peak_mb | verify | status"
    )
    for row in summaries:
        runtime = row.get("total_runtime_s")
        if runtime is None:
            runtime = row.get("wall_time_seconds")
        runtime_s = f"{runtime:.3f}" if runtime is not None else "n/a"
        peak = row.get("peak_rss_mb")
        peak_s = f"{peak:.3f}" if peak is not None else "n/a"
        lines.append(
            "  "
            f"{row.get('config_stem', ''):<40} | "
            f"{row.get('circuits_total', 0):>8} | "
            f"{row.get('pass_count', 0):>4} | "
            f"{row.get('fail_count', 0):>4} | "
            f"{row.get('error_count', 0):>5} | "
            f"{runtime_s:>9} | "
            f"{peak_s:>7} | "
            f"{row.get('verification', ''):>6} | "
            f"{row.get('job_status', '')}"
        )
    lines.append("=" * 120)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize stim_syndrome stats for jobs.txt")
    parser.add_argument("--jobs-file", default="jobs.txt", help="Tab-separated jobs file")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument(
        "--out",
        default="results_txt/jobs_stim_syndrome_summary.txt",
        help="Write human-readable table here",
    )
    parser.add_argument(
        "--json-out",
        default="results_txt/jobs_stim_syndrome_summary.json",
        help="Write machine-readable summary here",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Exit 0 even if some jobs are missing or failed",
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

    if args.allow_missing:
        return 0
    bad = [s for s in summaries if s.get("job_status") != "PASS"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
