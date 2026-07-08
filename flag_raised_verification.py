"""Per-circuit flag-raised checks (Step 3) for protocol config QASM files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from circuit_op import load_qasm
from flag_analysis import (
    check_flag_raised,
    detect_qubit_groups,
    set_quiet,
)
from dimacs_bridge import default_syndrome_sat_solver_bin
from syndrome_extraction_verification import CONFIG_SKIP_KEYS, _peak_rss_bytes_during


def _is_flag_group_key(key: str) -> bool:
    return key.endswith("_flag_group")


def list_flag_circuits(config: Dict[str, Any]) -> List[Tuple[str, Path]]:
    """Return (circuit_key, qasm_path) for flag_syndrome and *_flag entries."""
    circuits: List[Tuple[str, Path]] = []
    for key, value in sorted(config.items()):
        if key in CONFIG_SKIP_KEYS or key.startswith("__"):
            continue
        if _is_flag_group_key(key):
            continue
        if key != "flag_syndrome" and not key.endswith("_flag"):
            continue
        path = Path(str(value))
        if path.suffix.lower() == ".qasm":
            circuits.append((key, path))
    return circuits


def _resolve_flag_metrics_path(config: Dict[str, Any]) -> Path:
    metrics_dir = config.get("metrics_dir")
    if metrics_dir:
        base_dir = Path(metrics_dir)
    elif config.get("__config_dir__"):
        base_dir = Path(config["__config_dir__"])
    else:
        stab_path = config.get("stab_txt_path")
        base_dir = Path(stab_path).parent if stab_path else Path.cwd()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "flag_raised"
    return base_dir / f"{stem}_flag_raised_metrics.txt"


def _aggregate_flag_sat_stats(circuit_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-config totals and min/max DIMACS size across flag circuits."""
    solved = [
        row
        for row in circuit_stats
        if row.get("status") in ("pass", "fail") and row.get("sat_query_count", 0)
    ]
    if not solved:
        return {
            "total_sat_query_count": 0,
            "total_sat_time_s": 0.0,
            "min_dimacs_vars": 0,
            "max_dimacs_vars": 0,
            "min_clauses": 0,
            "max_clauses": 0,
        }
    vars_list = [int(row.get("dimacs_vars", 0) or 0) for row in solved]
    clause_list = [int(row.get("total_clauses", 0) or 0) for row in solved]
    return {
        "total_sat_query_count": sum(int(row.get("sat_query_count", 0) or 0) for row in solved),
        "total_sat_time_s": sum(float(row.get("sat_time_s", 0.0) or 0.0) for row in solved),
        "min_dimacs_vars": min(vars_list),
        "max_dimacs_vars": max(vars_list),
        "min_clauses": min(clause_list),
        "max_clauses": max(clause_list),
    }


def write_flag_raised_metrics_report(
    circuit_stats: List[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    t: int = 1,
    w: int | None = None,
) -> Path:
    min_weight = (w if w is not None else t) + 1
    report_path = _resolve_flag_metrics_path(config)
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("Flag-raised verification (Step 3)")
    fault_w = w if w is not None else t
    lines.append(
        f"Parameters: w={fault_w} (max fault sites), min error weight={min_weight} (weight > w)"
    )
    lines.append(
        "Property: faults may occur at any gate (at most w sites); if error weight > w then at least one flag qubit is raised"
    )
    lines.append(
        f"Solver: Z3 export + {default_syndrome_sat_solver_bin()} (SYNDROME_SAT_SOLVER_BIN)"
    )
    lines.append("Per-circuit metrics:")
    lines.append(
        "  circuit_key           | status  | fault_sites | runtime_s | peak_rss_mb | "
        "sat_query_count | sat_time_s | dimacs_vars | total_clauses | reason"
    )
    for row in circuit_stats:
        peak_bytes = row.get("peak_rss_bytes", 0) or 0
        peak_mb = peak_bytes / (1024 * 1024)
        fault_sites = row.get("fault_sites", [])
        if isinstance(fault_sites, list) and fault_sites:
            sites_str = f"all ({len(fault_sites)})"
        else:
            sites_str = str(fault_sites) if fault_sites else "[]"
        lines.append(
            "  "
            f"{row.get('circuit_key', ''):<21} | "
            f"{row.get('status', ''):<7} | "
            f"{sites_str:<11} | "
            f"{row.get('runtime_s', 0.0) or 0.0:>9.6f} | "
            f"{peak_mb:>11.3f} | "
            f"{row.get('sat_query_count', 0):>15} | "
            f"{row.get('sat_time_s', 0.0) or 0.0:>10.6f} | "
            f"{row.get('dimacs_vars', 0):>11} | "
            f"{row.get('total_clauses', 0):>13} | "
            f"{row.get('reason', '')}"
        )
    fail_count = sum(1 for r in circuit_stats if r.get("status") != "pass")
    total_runtime = sum(r.get("runtime_s", 0.0) or 0.0 for r in circuit_stats)
    max_peak_rss_bytes = max(
        (row.get("peak_rss_bytes", 0) or 0 for row in circuit_stats),
        default=0,
    )
    lines.append(f"Total runtime: {total_runtime:.6f} s")
    lines.append(
        f"Max peak RSS (across circuits): {max_peak_rss_bytes / (1024 * 1024):.3f} MB"
    )
    sat_agg = _aggregate_flag_sat_stats(circuit_stats)
    lines.append(
        f"  SAT solver calls (sum sat_query_count): {sat_agg['total_sat_query_count']}"
    )
    lines.append(f"  SAT solver time (sum): {sat_agg['total_sat_time_s']:.6f} s")
    lines.append(
        "  DIMACS variables (min / max): "
        f"{sat_agg['min_dimacs_vars']} / {sat_agg['max_dimacs_vars']}"
    )
    lines.append(
        f"  Clauses (min / max): {sat_agg['min_clauses']} / {sat_agg['max_clauses']}"
    )
    lines.append(f"Failed circuits: {fail_count}/{len(circuit_stats)}")
    lines.append("=" * 80)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def _circuit_has_flag_qubits(qasm_path: Path) -> bool:
    qc = load_qasm(str(qasm_path))
    groups = detect_qubit_groups(qc)
    return bool(groups.get("flagX")) or bool(groups.get("flagZ"))


def _run_one_flag_circuit(
    qasm_path: Path,
    stab_path: str,
    *,
    t: int,
    w: int | None,
) -> Tuple[bool, List[int], Dict[str, Any]]:
    qc = load_qasm(str(qasm_path))
    num_gates = len(qc.data)
    fault_sites = list(range(num_gates))
    ok, sat_stats = check_flag_raised(
        str(qasm_path),
        stab_path,
        num_gates,
        fault_sites,
        t=t,
        w=w,
        return_stats=True,
    )
    return ok, fault_sites, sat_stats


def _resolve_flag_w(config: Dict[str, Any], t: int, w: int | None) -> int | None:
    """Optional w override (max fault sites for flag check); default uses t from jobs/CLI."""
    if w is not None:
        return w
    cfg_w = config.get("flag_w")
    if cfg_w is not None and str(cfg_w).strip() != "":
        return int(cfg_w)
    return None


def run_flag_raised_verification(
    config: Dict[str, Any],
    *,
    t: int = 1,
    w: int | None = None,
) -> List[Dict[str, Any]]:
    w = _resolve_flag_w(config, t, w)
    stab_path = config.get("stab_txt_path")
    if not stab_path:
        raise KeyError("stab_txt_path missing from config")

    flag_circuits = list_flag_circuits(config)
    if not flag_circuits:
        stats = [
            {
                "circuit_key": "(none)",
                "qasm_path": "",
                "status": "missing",
                "fault_sites": [],
                "runtime_s": 0.0,
                "peak_rss_bytes": 0,
                "reason": "no flag_syndrome or *_flag circuits in config",
            }
        ]
        write_flag_raised_metrics_report(stats, config, t=t, w=w)
        return stats

    stats: List[Dict[str, Any]] = []
    for circuit_key, qasm_path in flag_circuits:
        row: Dict[str, Any] = {
            "circuit_key": circuit_key,
            "qasm_path": str(qasm_path),
        }
        if not qasm_path.is_file():
            row.update(
                status="missing",
                fault_sites=[],
                runtime_s=0.0,
                peak_rss_bytes=0,
                reason="qasm file not found",
            )
            stats.append(row)
            continue

        if not _circuit_has_flag_qubits(qasm_path):
            row.update(
                status="no_flags",
                fault_sites=[],
                runtime_s=0.0,
                peak_rss_bytes=0,
                reason="no flagX/flagZ qubits in circuit",
            )
            stats.append(row)
            continue

        t0 = time.perf_counter()
        try:
            (ok, fault_sites, sat_stats), peak_rss_bytes = _peak_rss_bytes_during(
                _run_one_flag_circuit,
                qasm_path,
                str(stab_path),
                t=t,
                w=w,
            )
        except Exception as exc:
            row.update(
                status="error",
                fault_sites=[],
                runtime_s=time.perf_counter() - t0,
                peak_rss_bytes=0,
                sat_query_count=0,
                sat_time_s=0.0,
                dimacs_vars=0,
                total_clauses=0,
                reason=str(exc),
            )
            stats.append(row)
            continue

        runtime_s = time.perf_counter() - t0
        base_stats = {
            "fault_sites": fault_sites,
            "runtime_s": runtime_s,
            "peak_rss_bytes": peak_rss_bytes,
            "sat_query_count": sat_stats.get("sat_query_count", 0),
            "sat_time_s": sat_stats.get("sat_time_s", 0.0),
            "dimacs_vars": sat_stats.get("dimacs_vars", 0),
            "total_clauses": sat_stats.get("total_clauses", 0),
        }
        if ok:
            row.update(status="pass", reason="", **base_stats)
        else:
            row.update(
                status="fail",
                reason="high-weight error with no flag raised",
                **base_stats,
            )
        stats.append(row)

    write_flag_raised_metrics_report(stats, config, t=t, w=w)
    return stats
