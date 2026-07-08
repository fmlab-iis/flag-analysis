"""Per-path single-fault low-weight verification (LUT gen_syn only, no pred_syn)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from z3 import And, BoolVal, Goal, Not, Or, PbEq, Xor

from dimacs_bridge import (
    build_dimacs,
    merge_dimacs_cnfs,
    default_sat_solver_bin,
    resolve_sat_solver_binary,
    run_dimacs_solver,
)
from dimacs_export_protocol import (
    _lut_gen_syn_z3,
    find_lut_instr_in_path,
)
from flag_analysis import pauli_not_in_stabilizer
from proof_protocol import classify_full_path, parse_lut_instr, proof_protocol


def weight_one_paulis(n: int) -> List[Tuple[List[Any], List[Any]]]:
    """Fixed weight-1 Paulis on n qubits: X_i, Z_i, Y_i for each i."""
    paulis: List[Tuple[List[Any], List[Any]]] = []
    for i in range(n):
        wx = [BoolVal(False)] * n
        wz = [BoolVal(False)] * n
        wx[i] = BoolVal(True)
        paulis.append((list(wx), list(wz)))
        wx = [BoolVal(False)] * n
        wz = [BoolVal(False)] * n
        wz[i] = BoolVal(True)
        paulis.append((list(wx), list(wz)))
        wx = [BoolVal(False)] * n
        wz = [BoolVal(False)] * n
        wx[i] = BoolVal(True)
        wz[i] = BoolVal(True)
        paulis.append((list(wx), list(wz)))
    return paulis


def pauli_commutes_with_all_stab_and_logical(
    E_x: List[Any],
    E_z: List[Any],
    stab_txt_path: str,
    log_txt_path: str,
) -> Any:
    """True iff E commutes with every stabilizer generator and logical operator."""
    return Not(pauli_not_in_stabilizer(E_x, E_z, stab_txt_path, log_txt_path))


def low_weight_ok_z3(
    E_x: List[Any],
    E_z: List[Any],
    stab_txt_path: str,
    log_txt_path: str,
) -> Any:
    """
    low_weight_ok :=
        commute(E) ∨ ∃ weight-1 W: commute(E·W)
    """
    n = len(E_x)
    terms = [
        pauli_commutes_with_all_stab_and_logical(E_x, E_z, stab_txt_path, log_txt_path)
    ]
    for w_x, w_z in weight_one_paulis(n):
        p_x = [Xor(ex, wx) for ex, wx in zip(E_x, w_x)]
        p_z = [Xor(ez, wz) for ez, wz in zip(E_z, w_z)]
        terms.append(
            pauli_commutes_with_all_stab_and_logical(p_x, p_z, stab_txt_path, log_txt_path)
        )
    return Or(*terms)


def build_lut_gen_syn_bits(path: List[dict]) -> List[Any]:
    lut_instr = find_lut_instr_in_path(path)
    if not lut_instr:
        return []
    lut_pairs = parse_lut_instr(lut_instr)
    return _lut_gen_syn_z3(path, lut_pairs)


def _path_conditions(path: List[dict]) -> List[Any]:
    return [s["condition"] for s in path if s.get("condition") is not None]


def _path_fault_acts(path: List[dict]) -> List[Any]:
    return [info["act"] for step in path for info in step.get("site_info", [])]


def _solve_goal(goal: Goal, query_tag: str = "low_weight") -> Dict[str, Any]:
    cnf_files, _var_maps = build_dimacs(goal, use_card2bv=True)
    base = query_tag
    renamed: List[str] = []
    for i, p in enumerate(cnf_files):
        newp = f"{base}_sub{i}.cnf"
        if p != newp:
            try:
                os.replace(p, newp)
                p = newp
            except OSError:
                pass
        renamed.append(p)

    solve_cnf = renamed[0]
    if len(renamed) > 1:
        merged = f"{base}_merged.cnf"
        merge_dimacs_cnfs(renamed, merged)
        solve_cnf = merged

    total_clauses = 0
    total_dimacs_vars = 0
    try:
        with open(solve_cnf, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("p cnf "):
                    parts = line.split()
                    if len(parts) >= 4:
                        total_dimacs_vars = int(parts[2])
                        total_clauses = int(parts[3])
                    break
    except (OSError, ValueError):
        pass

    solver_exec = resolve_sat_solver_binary(default_sat_solver_bin())
    status, _lits, _out, elapsed_s, peak_rss = run_dimacs_solver(solve_cnf, solver_exec)

    for p in renamed:
        try:
            os.remove(p)
        except OSError:
            pass
    if len(renamed) > 1:
        try:
            os.remove(solve_cnf)
        except OSError:
            pass

    return {
        "low_weight_solver_status": status,
        "low_weight_solver_runtime_seconds": elapsed_s,
        "low_weight_peak_solver_rss_bytes": peak_rss or 0,
        "low_weight_total_clauses": total_clauses,
        "low_weight_total_dimacs_vars": total_dimacs_vars,
        "low_weight_sat_query_count": 1,
    }


def prove_single_fault_low_weight_path(
    path: List[dict],
    config: Dict[str, Any],
    query_tag: str = "low_weight",
) -> Dict[str, Any]:
    """
    Prove UNSAT of:
      path_conditions ∧ LUT-all-false ∧ exactly_one_fault ∧ Not(low_weight_ok)
    """
    path_type, _ = classify_full_path(path)
    if path_type == 0:
        return {"low_weight_status": "skipped", "low_weight_reason": "break_path"}

    lut_bits = build_lut_gen_syn_bits(path)
    if not lut_bits:
        return {"low_weight_status": "skipped", "low_weight_reason": "no_lut"}

    faults = _path_fault_acts(path)
    if not faults:
        return {"low_weight_status": "skipped", "low_weight_reason": "no_fault_sites"}

    stab_txt_path = str(config["stab_txt_path"])
    log_txt_path = str(config["log_txt_path"])
    E_x = [dq.x for dq in path[-1]["state"]["data"]]
    E_z = [dq.z for dq in path[-1]["state"]["data"]]

    goal = Goal()
    conds = _path_conditions(path)
    if conds:
        goal.add(And(*conds))
    goal.add(And(*[bit == BoolVal(False) for bit in lut_bits]))
    goal.add(PbEq([(f, 1) for f in faults], 1))
    goal.add(Not(low_weight_ok_z3(E_x, E_z, stab_txt_path, log_txt_path)))

    with tempfile.TemporaryDirectory(prefix="low_weight_") as tmp:
        prev = os.getcwd()
        try:
            os.chdir(tmp)
            stats = _solve_goal(goal, query_tag=query_tag)
        finally:
            os.chdir(prev)

    stats["low_weight_status"] = stats.pop("low_weight_solver_status")
    return stats


def verify_low_weight_all_paths(
    all_paths: List[List[dict]],
    path_query_stats: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    quiet = bool(config.get("__quiet__", False))
    updated: List[Dict[str, Any]] = []

    for row in path_query_stats:
        row = dict(row)
        path_idx = row.get("path_index")
        if path_idx is None or path_idx < 0 or path_idx >= len(all_paths):
            row.setdefault("low_weight_status", "skipped")
            row.setdefault("low_weight_reason", "missing_path")
            updated.append(row)
            continue

        path = all_paths[path_idx]
        tag = f"low_weight_path_{path_idx:03d}"
        lw_stats = prove_single_fault_low_weight_path(path, config, query_tag=tag)
        row.update(lw_stats)
        if not quiet and row.get("low_weight_status") not in (None, "skipped"):
            print(
                f"Low-weight path {path_idx}: {row.get('low_weight_status', '').upper()}"
            )
        updated.append(row)

    return updated


def _resolve_low_weight_metrics_path(config: Dict[str, Any]) -> Path:
    metrics_dir = config.get("metrics_dir")
    if metrics_dir:
        base_dir = Path(metrics_dir)
    elif config.get("__config_dir__"):
        base_dir = Path(config["__config_dir__"])
    else:
        stab_path = config.get("stab_txt_path")
        base_dir = Path(stab_path).parent if stab_path else Path.cwd()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "low_weight"
    return base_dir / f"{stem}_low_weight_metrics.txt"


def write_low_weight_metrics_report(
    path_query_stats: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Path:
    report_path = _resolve_low_weight_metrics_path(config)
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("Low-weight verification (LUT s/f all false, exactly 1 fault)")
    lines.append("Property: low_weight_ok = commute(E) ∨ ∃ weight-1 W: commute(E·W)")
    lines.append("Counterexample UNSAT => pass; SAT => fail")
    lines.append("Per-path metrics:")
    lines.append(
        "  path_idx | type | last_instr              | low_weight | runtime_s | "
        "peak_rss_mb | dimacs_vars | total_clauses | reason"
    )
    for row in sorted(path_query_stats, key=lambda r: r.get("path_index", 0)):
        peak_rss_bytes = row.get("low_weight_peak_solver_rss_bytes", 0) or 0
        peak_rss_mb = peak_rss_bytes / (1024 * 1024)
        lines.append(
            "  "
            f"{row.get('path_index', 0):>7} | "
            f"{row.get('path_type', ''):>4} | "
            f"{row.get('last_instr', ''):<23} | "
            f"{row.get('low_weight_status', ''):<10} | "
            f"{row.get('low_weight_solver_runtime_seconds', 0.0) or 0.0:>9.6f} | "
            f"{peak_rss_mb:>11.3f} | "
            f"{row.get('low_weight_total_dimacs_vars', 0) or 0:>11} | "
            f"{row.get('low_weight_total_clauses', 0) or 0:>13} | "
            f"{row.get('low_weight_reason', '')}"
        )
    sat_count = sum(1 for r in path_query_stats if r.get("low_weight_status") == "sat")
    # Denominator matches Paths column and Step 4: all traversed paths (including Break).
    total_paths = len(path_query_stats)
    total_runtime = sum(
        r.get("low_weight_solver_runtime_seconds", 0.0) or 0.0
        for r in path_query_stats
        if r.get("low_weight_status") not in ("skipped", None)
    )
    lines.append(f"Total low-weight runtime: {total_runtime:.6f} s")
    lines.append(f"Low-weight SAT (fail) paths: {sat_count}/{total_paths}")
    lines.append("=" * 80)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def collect_protocol_paths(
    protocol,
    start_node: str,
    init_state,
    config: Dict[str, Any],
    t: int,
):
    """Traverse protocol paths without running uniqueness proofs."""
    return proof_protocol(
        protocol,
        start_node,
        init_state,
        config,
        t,
        verify_uniqueness=False,
    )


def collect_unified_protocol_paths(
    protocol,
    start_node: str,
    init_state,
    config: Dict[str, Any],
    t: int,
):
    """Traverse unified-style paths without CNF export or uniqueness solve."""
    from dimacs_export_protocol import export_unified_path_constraints

    return export_unified_path_constraints(
        protocol,
        start_node,
        init_state,
        config,
        t,
        cnf_dir=config.get("unified_cnf_dir"),
        protocol_path=config.get("protocol_path"),
        collect_only=True,
    )


def run_low_weight_verification(
    protocol,
    start_node: str,
    init_state,
    config: Dict[str, Any],
    *,
    unified: bool = False,
) -> Tuple[List[List[dict]], List[Dict[str, Any]]]:
    """Collect paths and run low-weight checks only (no uniqueness proof)."""
    if unified:
        all_paths, stats = collect_unified_protocol_paths(
            protocol, start_node, init_state, config, t=1
        )
    else:
        all_paths, stats = collect_protocol_paths(
            protocol, start_node, init_state, config, t=1
        )
    stats = verify_low_weight_all_paths(all_paths, stats, config)
    write_low_weight_metrics_report(stats, config)
    return all_paths, stats
