#!/usr/bin/env python3
"""Compare Type-0 (Break) SAT results: baseline vs initial_data_error runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from summarize_jobs_dimacs import _read_jobs


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _type0_rows(cnf_dir: Path) -> List[Dict[str, Any]]:
    if not cnf_dir.is_dir():
        return []
    manifest = _load_json(cnf_dir / "manifest.json")
    rows: List[Dict[str, Any]] = []
    for path_info in manifest.get("paths", []):
        if path_info.get("path_type") != 0:
            continue
        tag = path_info.get("path_tag", "")
        result = _load_json(cnf_dir / f"{tag}_result.json")
        meta = _load_json(cnf_dir / f"{tag}_meta.json")
        rows.append(
            {
                "path_index": path_info.get("path_index"),
                "path_tag": tag,
                "last_instr": path_info.get("last_instr", ""),
                "gate_count": path_info.get("gate_count", 0),
                "status": result.get("status", "missing"),
                "solver_runtime_seconds": result.get("solver_runtime_seconds", 0.0),
                "num_fault_vars": meta.get("num_fault_vars", result.get("num_fault_vars", 0)),
                "total_dimacs_vars": result.get(
                    "total_dimacs_vars", meta.get("total_dimacs_vars", 0)
                ),
                "total_clauses": result.get("total_clauses", meta.get("total_clauses", 0)),
                "compare_residual_to_gate_faults": meta.get(
                    "compare_residual_to_gate_faults"
                ),
                "n_data_init_vars": sum(
                    1
                    for n in meta.get("fault_var_names", [])
                    if "dataInit" in str(n)
                ),
            }
        )
    return rows


def compare_jobs(
    root: Path,
    jobs_file: Path,
    *,
    baseline_subdir: str = "cnf_out_control_flow",
    initdata_subdir: str = "cnf_out_control_flow_initdata",
) -> List[Dict[str, Any]]:
    comparisons: List[Dict[str, Any]] = []
    for job in _read_jobs(jobs_file):
        stem = Path(job["config"]).stem
        base_rows = {
            r["path_tag"]: r for r in _type0_rows(root / baseline_subdir / stem)
        }
        init_rows = {
            r["path_tag"]: r for r in _type0_rows(root / initdata_subdir / stem)
        }
        tags = sorted(set(base_rows) | set(init_rows))
        if not tags:
            comparisons.append(
                {
                    "config_stem": stem,
                    "t": job["t"],
                    "path_tag": None,
                    "baseline_status": "no_type0",
                    "initdata_status": "no_type0",
                    "changed": False,
                }
            )
            continue
        for tag in tags:
            b = base_rows.get(tag, {})
            n = init_rows.get(tag, {})
            b_status = b.get("status", "missing")
            n_status = n.get("status", "missing")
            comparisons.append(
                {
                    "config_stem": stem,
                    "t": job["t"],
                    "path_index": b.get("path_index", n.get("path_index")),
                    "path_tag": tag,
                    "last_instr": b.get("last_instr") or n.get("last_instr", ""),
                    "baseline_status": b_status,
                    "initdata_status": n_status,
                    "baseline_fault_vars": b.get("num_fault_vars"),
                    "initdata_fault_vars": n.get("num_fault_vars"),
                    "initdata_data_init_vars": n.get("n_data_init_vars", 0),
                    "baseline_runtime_s": b.get("solver_runtime_seconds"),
                    "initdata_runtime_s": n.get("solver_runtime_seconds"),
                    "compare_residual_to_gate_faults": n.get(
                        "compare_residual_to_gate_faults"
                    ),
                    "changed": b_status != n_status,
                }
            )
    return comparisons


def _fmt_runtime(val: Any) -> str:
    if val is None:
        return "-"
    try:
        return f"{float(val):.3f}"
    except (TypeError, ValueError):
        return str(val)


def _format_table(rows: List[Dict[str, Any]]) -> str:
    lines = [
        "=" * 140,
        "Type-0 Break comparison: baseline (old) vs initial_data_error",
        "  stem | path | baseline | initdata | base_s | init_s | ratio | base_vars | init_vars | dataInit | changed",
    ]
    flips = 0
    for r in rows:
        if r.get("path_tag") is None:
            lines.append(f"  {r['config_stem']:<42} | (no Type-0 path)")
            continue
        changed = "YES" if r.get("changed") else "no"
        if r.get("changed"):
            flips += 1
        br = r.get("baseline_runtime_s")
        ir = r.get("initdata_runtime_s")
        ratio = "-"
        try:
            if br is not None and ir is not None and float(br) > 0:
                ratio = f"{float(ir) / float(br):.3f}x"
        except (TypeError, ValueError):
            ratio = "-"
        lines.append(
            "  "
            f"{r.get('config_stem', ''):<42} | "
            f"{r.get('path_tag', ''):<8} | "
            f"{str(r.get('baseline_status', '')):<8} | "
            f"{str(r.get('initdata_status', '')):<8} | "
            f"{_fmt_runtime(br):>8} | "
            f"{_fmt_runtime(ir):>8} | "
            f"{ratio:>7} | "
            f"{str(r.get('baseline_fault_vars', '')):>9} | "
            f"{str(r.get('initdata_fault_vars', '')):>9} | "
            f"{str(r.get('initdata_data_init_vars', '')):>8} | "
            f"{changed}"
        )
    lines.append("-" * 140)
    lines.append(f"Type-0 paths with status flip: {flips}")
    lines.append("=" * 140)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Type-0 break results baseline vs initial_data_error",
    )
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--jobs-file", default="jobs_type0.txt")
    parser.add_argument("--baseline-subdir", default="cnf_out_control_flow")
    parser.add_argument("--initdata-subdir", default="cnf_out_control_flow_initdata")
    parser.add_argument(
        "--out",
        default="results_txt/type0_break_comparison.txt",
    )
    parser.add_argument(
        "--json-out",
        default="results_txt/type0_break_comparison.json",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    jobs_file = Path(args.jobs_file)
    if not jobs_file.is_absolute():
        jobs_file = (root / jobs_file).resolve()
    if not jobs_file.is_file():
        print(f"Jobs file not found: {jobs_file}", file=sys.stderr)
        return 1

    rows = compare_jobs(
        root,
        jobs_file,
        baseline_subdir=args.baseline_subdir,
        initdata_subdir=args.initdata_subdir,
    )
    table = _format_table(rows)
    print(table, end="")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(table, encoding="utf-8")

    json_path = Path(args.json_out)
    if not json_path.is_absolute():
        json_path = root / json_path
    json_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
