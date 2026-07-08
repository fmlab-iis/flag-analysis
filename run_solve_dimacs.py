#!/usr/bin/env python3
"""Phase 2: solve exported DIMACS paths and write per-path + protocol reports."""

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dimacs_export_protocol import (
    aggregate_job_solve_stats,
    format_job_aggregate_lines,
    format_proof_metrics_report,
)
from flag_analysis import uniqueness_solve_from_export


def _load_manifest(cnf_dir: Path) -> Dict[str, Any]:
    manifest_path = cnf_dir / "manifest.json"
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    tags = sorted(p.stem for p in cnf_dir.glob("path_*.cnf"))
    return {
        "config_stem": cnf_dir.name,
        "t": 1,
        "exported_paths": len(tags),
        "paths": [{"path_index": i, "path_tag": t} for i, t in enumerate(tags)],
    }


def _write_path_report(
    cnf_dir: Path,
    path_tag: str,
    path_index: int,
    total: int,
    row: Dict[str, Any],
    counterexample: Optional[Dict],
    *,
    verify_pipeline: Optional[str] = None,
) -> None:
    peak_mb = (row.get("peak_solver_rss_bytes", 0) or 0) / (1024 * 1024)
    status = row.get("status", "unknown")
    path_type = row.get("path_type", "?")
    pipeline = verify_pipeline or ""
    unified = pipeline == "unified"
    control_flow = pipeline == "control_flow"
    if status == "unsat":
        if path_type == 0 and not unified:
            result_line = "result: break weight bound holds (no counterexample)"
        elif control_flow and path_type == 1:
            result_line = "result: pred_syn consistent (no counterexample)"
        else:
            result_line = "result: uniqueness holds (no counterexample)"
    elif status == "sat":
        if path_type == 0 and not unified:
            result_line = "result: break weight counterexample found (FAIL)"
        elif control_flow and path_type == 1:
            result_line = "result: pred_syn mismatch found (FAIL)"
        else:
            result_line = "result: counterexample found (uniqueness FAIL)"
    else:
        result_line = f"result: {status}"

    lines = [
        f"Path {path_index} / {total}",
        f"  type: {row.get('path_type', '?')}  last_instr: {row.get('last_instr', '')}",
        f"  status: {status.upper()}",
        f"  runtime: {row.get('solver_runtime_seconds', 0.0):.6f} s  peak RSS: {peak_mb:.3f} MB",
        f"  dimacs_vars: {row.get('total_dimacs_vars', 0)}  clauses: {row.get('total_clauses', 0)}",
        f"  {result_line}",
    ]
    if counterexample:
        if path_type == 0 and not unified:
            lines.append(f"  faults: {counterexample.get('faults', {})}")
            lines.append(f"  gsel: {counterexample.get('gsel', {})}")
        else:
            lines.append(f"  p1: {counterexample.get('p1', {})}")
            lines.append(f"  p2: {counterexample.get('p2', {})}")

    report_path = cnf_dir / f"{path_tag}_report.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_one_path(
    cnf_dir: Path,
    path_tag: str,
    job_id: int,
    total: int,
    manifest: Dict[str, Any],
    cms_retries: int,
) -> Dict[str, Any]:
    path_info = next(
        (p for p in manifest.get("paths", []) if p.get("path_tag") == path_tag),
        {"path_index": job_id - 1, "path_type": "?", "last_instr": "", "gate_count": 0},
    )
    path_index = path_info.get("path_index", job_id - 1)

    status, counterexample, stats = uniqueness_solve_from_export(
        cnf_dir, path_tag, cms_retries=cms_retries,
    )

    row = {
        "path_index": path_index,
        "path_type": path_info.get("path_type", "?"),
        "last_instr": path_info.get("last_instr", ""),
        "status": status,
        "gate_count": path_info.get("gate_count", 0),
        "solver_runtime_seconds": stats.get("solver_runtime_seconds", 0.0),
        "peak_solver_rss_bytes": stats.get("peak_solver_rss_bytes", 0),
        "num_fault_vars": stats.get("num_fault_vars", 0),
        "total_dimacs_vars": stats.get("total_dimacs_vars", 0),
        "total_clauses": stats.get("total_clauses", 0),
        "sat_query_count": stats.get("sat_query_count", 0),
        "path_tag": path_tag,
    }

    result_path = cnf_dir / f"{path_tag}_result.json"
    lock_path = cnf_dir / ".solve_progress.lock"
    config_stem = manifest.get("config_stem", cnf_dir.name)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        result_path.write_text(
            json.dumps({"status": status, "counterexample": counterexample, **stats}, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_path_report(
            cnf_dir, path_tag, path_index, total, row, counterexample,
            verify_pipeline=manifest.get("verify_pipeline"),
        )

        solved = len(list(cnf_dir.glob("path_*_result.json")))
        progress = {
            "solved": solved,
            "total": total,
            "last_path": path_tag,
            "last_status": status,
        }
        (cnf_dir / "solve_progress.json").write_text(json.dumps(progress, indent=2) + "\n")
        print(f"{config_stem}: {solved}/{total} paths", flush=True)

    return row


def summarize(cnf_dir: Path, metrics_dir: Optional[Path]) -> int:
    manifest = _load_manifest(cnf_dir)
    t = manifest.get("t", 1)
    config_stem = manifest.get("config_stem", cnf_dir.name)

    rows: List[Dict[str, Any]] = []
    for result_file in sorted(cnf_dir.glob("path_*_result.json")):
        path_tag = result_file.name.replace("_result.json", "")
        data = json.loads(result_file.read_text(encoding="utf-8"))
        path_info = next(
            (p for p in manifest.get("paths", []) if p.get("path_tag") == path_tag),
            {},
        )
        rows.append({
            "path_index": path_info.get("path_index", 0),
            "path_type": path_info.get("path_type", "?"),
            "last_instr": path_info.get("last_instr", ""),
            "status": data.get("status", "unknown"),
            "gate_count": path_info.get("gate_count", 0),
            "solver_runtime_seconds": data.get("solver_runtime_seconds", 0.0),
            "peak_solver_rss_bytes": data.get("peak_solver_rss_bytes", 0),
            "num_fault_vars": data.get("num_fault_vars", 0),
            "total_dimacs_vars": data.get("total_dimacs_vars", 0),
            "total_clauses": data.get("total_clauses", 0),
            "sat_query_count": data.get("sat_query_count", 0),
        })

    agg = aggregate_job_solve_stats(rows)
    timing_path = cnf_dir / "job_timing.json"
    wall_time_s = None
    if timing_path.is_file():
        try:
            wall_time_s = float(json.loads(timing_path.read_text(encoding="utf-8")).get("wall_time_seconds"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            wall_time_s = None

    pipeline = manifest.get("verify_pipeline")
    report = format_proof_metrics_report(
        rows, t, manifest.get("total_paths", len(rows)), wall_time_s=wall_time_s,
        verify_pipeline=pipeline,
    )
    if pipeline == "unified":
        report = (
            "Verify pipeline: unified (all measured gen_syn + logical-product witness)\n"
            + report
        )
    elif pipeline == "control_flow":
        report = (
            "Verify pipeline: control_flow (type0 break-weight; type1 same gen_syn + pred_syn_diff; type2 skipped)\n"
            + report
        )
    protocol_report = cnf_dir / "protocol_report.txt"
    protocol_report.write_text(report, encoding="utf-8")

    if metrics_dir is None:
        metrics_dir = Path("results_txt")
    metrics_dir.mkdir(parents=True, exist_ok=True)
    if pipeline == "unified":
        metrics_suffix = "_unified_proof_metrics"
    elif pipeline == "control_flow":
        metrics_suffix = "_control_flow_proof_metrics"
    else:
        metrics_suffix = "_proof_metrics"
    metrics_path = metrics_dir / f"{config_stem}{metrics_suffix}.txt"
    metrics_path.write_text(report, encoding="utf-8")

    solved = len(rows)
    total = manifest.get("exported_paths", solved)
    unsat = sum(1 for r in rows if r.get("status") == "unsat")
    sat = sum(1 for r in rows if r.get("status") == "sat")
    unknown = sum(1 for r in rows if r.get("status") == "unknown")
    pred_syn_fail = sum(
        1 for r in rows if r.get("path_type") == 1 and r.get("status") == "sat"
    )
    pred_syn_total = sum(1 for r in rows if r.get("path_type") == 1)
    break_fail = sum(
        1 for r in rows if r.get("path_type") == 0 and r.get("status") == "sat"
    )
    break_total = sum(1 for r in rows if r.get("path_type") == 0)

    job_summary = {
        "config_stem": config_stem,
        "cnf_dir": str(cnf_dir),
        "t": t,
        "wall_time_seconds": wall_time_s,
        **agg,
        "fail_path_count": sat,
        "unsat_path_count": unsat,
        "sat_path_count_result": sat,
        "unknown_path_count": unknown,
        "exported_paths": total,
    }
    if pipeline == "control_flow":
        job_summary["pred_syn_fail_count"] = pred_syn_fail
        job_summary["pred_syn_path_count"] = pred_syn_total
        job_summary["break_fail_count"] = break_fail
        job_summary["break_path_count"] = break_total
    (cnf_dir / "job_aggregate.json").write_text(
        json.dumps(job_summary, indent=2) + "\n",
        encoding="utf-8",
    )
    batch_summary_path = metrics_dir / f"{config_stem}_job_summary.json"
    batch_summary_path.write_text(json.dumps(job_summary, indent=2) + "\n", encoding="utf-8")

    if os.environ.get("DIMACS_VERBOSE"):
        print(f"========== Summary: {config_stem} ==========")
        print(f"  Solved: {solved}/{total} paths")
        print(f"  UNSAT: {unsat}  SAT: {sat}  unknown: {unknown}")
        print(f"  Protocol report: {protocol_report}")
        print(f"  Metrics: {metrics_path}")

    return 1 if sat > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Solve exported DIMACS paths (phase 2).")
    parser.add_argument("--cnf-dir", required=True, help="Config CNF directory")
    parser.add_argument("--path-tag", default=None, help="Solve/parse one path only")
    parser.add_argument("--parse-only", action="store_true", help="Used by run_one_path.sh")
    parser.add_argument("--summarize", action="store_true", help="Aggregate all path results")
    parser.add_argument("--job-id", type=int, default=1)
    parser.add_argument("--total", type=int, default=1)
    parser.add_argument("--cms-retries", type=int, default=8)
    parser.add_argument("--metrics-dir", default="results_txt")
    args = parser.parse_args()

    cnf_dir = Path(args.cnf_dir).resolve()
    if not cnf_dir.is_dir():
        print(f"Error: not a directory: {cnf_dir}", file=sys.stderr)
        return 1

    if args.summarize:
        return summarize(cnf_dir, Path(args.metrics_dir) if args.metrics_dir else None)

    if args.path_tag:
        manifest = _load_manifest(cnf_dir)
        parse_one_path(
            cnf_dir, args.path_tag, args.job_id, args.total, manifest, args.cms_retries,
        )
        return 0

    print("Error: specify --path-tag --parse-only or --summarize", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
