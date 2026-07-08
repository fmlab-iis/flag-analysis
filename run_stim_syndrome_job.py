#!/usr/bin/env python3
"""Run stim_syndrome invariance checks for all QASM circuits in a protocol config."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set

from flag_analysis import read_config
from syndrome_extraction_verification import _peak_rss_bytes_during

_SKIP_CONFIG_KEYS = frozenset({"stab_txt_path", "log_txt_path"})


def _check_stim_imports() -> None:
    missing: List[str] = []
    for mod in ("stim", "cirq", "stimcirq", "stim_syndrome"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise ImportError(
            "Missing packages for stim_syndrome batch: "
            + ", ".join(missing)
            + ". Install with: pip install -r requirements-stim.txt "
            "and set PYTHONPATH to the parent of stim_syndrome/."
        )


def _qasm_paths_from_config(config: Dict[str, Any]) -> List[Path]:
    paths: Set[Path] = set()
    for key, value in config.items():
        if key.startswith("__") or key in _SKIP_CONFIG_KEYS:
            continue
        if isinstance(value, str) and value.endswith(".qasm"):
            paths.add(Path(value))
    return sorted(paths)


def _circuit_row(
    config: Dict[str, Any],
    config_key: str,
    circuit_path: Path,
    *,
    state_check: bool,
) -> Dict[str, Any]:
    from stim_syndrome.cli import build_spec
    from stim_syndrome.io import load_qasm_circuit
    from stim_syndrome.verify import check_invariance

    row: Dict[str, Any] = {
        "config_key": config_key,
        "circuit": str(circuit_path),
        "circuit_name": circuit_path.name,
    }
    try:
        if not circuit_path.is_file():
            raise FileNotFoundError(f"Missing QASM file: {circuit_path}")
        spec = build_spec(
            Path(config["stab_txt_path"]),
            Path(config["log_txt_path"]),
            circuit_path,
        )
        stim_circuit, _, _ = load_qasm_circuit(circuit_path)
        report = check_invariance(spec, stim_circuit, state_check=state_check)
        row["status"] = "PASS" if report.ok else "FAIL"
        row["stabilizer_span_ok"] = report.stabilizer_span_ok
        row["logical_results"] = dict(report.logical_results)
        row["rank_before"] = report.rank_before
        row["rank_after"] = report.rank_after
        if report.generator_failures:
            row["generator_failures"] = [
                {
                    "index": f.index,
                    "label": f.label,
                    "before": f.before,
                    "after": f.after,
                    "in_g_before": f.in_g_before,
                    "in_g_after": f.in_g_after,
                }
                for f in report.generator_failures
            ]
        if report.state_check_ok is not None:
            row["state_check_ok"] = report.state_check_ok
    except Exception as exc:
        row["status"] = "ERROR"
        row["error"] = str(exc)
    return row


def _config_key_for_qasm(config: Dict[str, Any], circuit_path: Path) -> str:
    target = str(circuit_path.resolve())
    for key, value in config.items():
        if key.startswith("__") or key in _SKIP_CONFIG_KEYS:
            continue
        if isinstance(value, str) and value.endswith(".qasm"):
            if str(Path(value).resolve()) == target:
                return key
    return circuit_path.stem


def run_job(
    root: Path,
    config_path: str,
    *,
    state_check: bool = False,
) -> Dict[str, Any]:
    _check_stim_imports()

    config = read_config(config_path)
    stab_path = Path(config.get("stab_txt_path", ""))
    log_path = Path(config.get("log_txt_path", ""))

    if not stab_path.is_file():
        raise FileNotFoundError(f"stab_txt_path missing or not found: {stab_path}")
    if not log_path.is_file():
        raise FileNotFoundError(f"log_txt_path missing or not found: {log_path}")

    circuits = _qasm_paths_from_config(config)
    if not circuits:
        raise ValueError(f"No .qasm paths found in config: {config_path}")

    t0 = time.perf_counter()
    circuit_rows: List[Dict[str, Any]] = []
    peak_rss_bytes = 0
    for circuit_path in circuits:
        key = _config_key_for_qasm(config, circuit_path)
        circuit_t0 = time.perf_counter()
        row, circuit_peak_rss_bytes = _peak_rss_bytes_during(
            _circuit_row,
            config,
            key,
            circuit_path,
            state_check=state_check,
        )
        row["runtime_s"] = time.perf_counter() - circuit_t0
        row["peak_rss_bytes"] = circuit_peak_rss_bytes
        peak_rss_bytes = max(peak_rss_bytes, circuit_peak_rss_bytes)
        circuit_rows.append(row)

    n_pass = sum(1 for r in circuit_rows if r.get("status") == "PASS")
    n_fail = sum(1 for r in circuit_rows if r.get("status") == "FAIL")
    n_error = sum(1 for r in circuit_rows if r.get("status") == "ERROR")
    wall_time = time.perf_counter() - t0
    total_runtime_s = sum(r.get("runtime_s", 0.0) or 0.0 for r in circuit_rows)

    job_status = "PASS" if n_fail == 0 and n_error == 0 else "FAIL"
    if n_error and n_fail == 0 and n_pass == 0:
        job_status = "ERROR"

    return {
        "verify_pipeline": "stim_syndrome",
        "config": config_path,
        "config_stem": Path(config_path).stem,
        "stab_txt_path": str(stab_path),
        "log_txt_path": str(log_path),
        "circuits_total": len(circuit_rows),
        "pass_count": n_pass,
        "fail_count": n_fail,
        "error_count": n_error,
        "job_status": job_status,
        "wall_time_seconds": wall_time,
        "total_runtime_s": total_runtime_s,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_mb": peak_rss_bytes / (1024 * 1024),
        "state_check": state_check,
        "circuits": circuit_rows,
    }


def _format_metrics(result: Dict[str, Any]) -> str:
    peak_mb = float(result.get("peak_rss_mb", 0.0) or 0.0)
    lines = [
        f"verify_pipeline: {result['verify_pipeline']}",
        f"config: {result['config']}",
        f"stab_txt_path: {result['stab_txt_path']}",
        f"log_txt_path: {result['log_txt_path']}",
        f"circuits_total: {result['circuits_total']}",
        f"pass: {result['pass_count']}  fail: {result['fail_count']}  error: {result['error_count']}",
        f"wall_time_seconds: {result['wall_time_seconds']:.3f}",
        f"total_runtime_s: {result.get('total_runtime_s', result['wall_time_seconds']):.6f}",
        f"peak_rss_mb: {peak_mb:.3f}",
        f"job_status: {result['job_status']}",
        "",
        "  status | config_key                     | circuit                         | runtime_s | peak_rss_mb",
    ]
    for row in result["circuits"]:
        status = row.get("status", "?")
        name = row.get("circuit_name", Path(row.get("circuit", "")).name)
        key = row.get("config_key", "")
        runtime_s = row.get("runtime_s", 0.0) or 0.0
        circuit_peak_mb = (row.get("peak_rss_bytes", 0) or 0) / (1024 * 1024)
        lines.append(
            f"  {status:5} | {key:30} | {name:31} | {runtime_s:9.6f} | {circuit_peak_mb:11.3f}"
        )
        if status == "ERROR":
            lines.append(f"         {row.get('error', '')}")
        elif status == "FAIL" and row.get("generator_failures"):
            for f in row["generator_failures"][:3]:
                lines.append(f"         [{f['index']}] {f['label']}: {f['before']} -> {f.get('after', '')}")
    lines.append("")
    lines.append(f"Overall: {result['job_status']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run stim_syndrome on one jobs.txt config")
    parser.add_argument("--root", default=".", help="Project root")
    parser.add_argument("--config", required=True, help="Protocol config .txt path")
    parser.add_argument("--state-check", action="store_true", help="Enable TableauSimulator cross-check")
    parser.add_argument(
        "--metrics-out",
        help="Human-readable metrics file (default: results_txt/{stem}_stim_syndrome_metrics.txt)",
    )
    parser.add_argument(
        "--json-out",
        help="JSON result file (default: results_txt/{stem}_stim_syndrome.json)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    config_path = args.config
    if not Path(config_path).is_absolute():
        config_path = str((root / config_path).resolve())

    try:
        result = run_job(root, config_path, state_check=args.state_check)
    except Exception as exc:
        stem = Path(config_path).stem
        err_result = {
            "verify_pipeline": "stim_syndrome",
            "config": config_path,
            "config_stem": stem,
            "job_status": "ERROR",
            "error": str(exc),
            "circuits_total": 0,
            "pass_count": 0,
            "fail_count": 0,
            "error_count": 1,
            "circuits": [],
        }
        metrics_out = Path(args.metrics_out) if args.metrics_out else root / "results_txt" / f"{stem}_stim_syndrome_metrics.txt"
        json_out = Path(args.json_out) if args.json_out else root / "results_txt" / f"{stem}_stim_syndrome.json"
        if not metrics_out.is_absolute():
            metrics_out = root / metrics_out
        if not json_out.is_absolute():
            json_out = root / json_out
        metrics_out.parent.mkdir(parents=True, exist_ok=True)
        metrics_out.write_text(
            f"verify_pipeline: stim_syndrome\nconfig: {config_path}\njob_status: ERROR\n\n{exc}\n",
            encoding="utf-8",
        )
        json_out.write_text(json.dumps(err_result, indent=2) + "\n", encoding="utf-8")
        print(f"ERROR  {stem}: {exc}", file=sys.stderr)
        return 1

    stem = result["config_stem"]
    metrics_out = Path(args.metrics_out) if args.metrics_out else root / "results_txt" / f"{stem}_stim_syndrome_metrics.txt"
    json_out = Path(args.json_out) if args.json_out else root / "results_txt" / f"{stem}_stim_syndrome.json"
    if not metrics_out.is_absolute():
        metrics_out = root / metrics_out
    if not json_out.is_absolute():
        json_out = root / json_out
    metrics_out.parent.mkdir(parents=True, exist_ok=True)
    metrics_out.write_text(_format_metrics(result), encoding="utf-8")
    json_out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    if result["job_status"] == "PASS":
        print(
            f"DONE   {stem}  pass={result['pass_count']}/{result['circuits_total']} "
            f"({result['wall_time_seconds']:.2f}s, peak={result.get('peak_rss_mb', 0.0):.1f} MB)"
        )
        return 0
    print(
        f"FAIL   {stem}  pass={result['pass_count']} fail={result['fail_count']} "
        f"error={result['error_count']}/{result['circuits_total']} "
        f"({result['wall_time_seconds']:.2f}s, peak={result.get('peak_rss_mb', 0.0):.1f} MB)",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
