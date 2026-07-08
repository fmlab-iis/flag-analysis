"""Export protocol path constraints to DIMACS (phase 1 of parallel solve pipeline)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from z3 import And, BoolVal, PbLe

from flag_analysis import (
    break_weight_export_dimacs,
    control_flow_export_dimacs,
    data_only_groups_from_state_dict,
    detect_qubit_groups,
    get_gate_only_indices,
    load_qasm,
    last_ancilla_formulas,
    stabilizer_syndrome_from_data,
    state_to_raw_expr_dict,
    symbolic_execution_of_state,
    uniqueness_export_dimacs,
    uniqueness_export_dimacs_unified,
)
from proof_protocol import (
    classify_full_path,
    condition_to_z3,
    parse_lut_instr,
)


@dataclass
class PathConstraint:
    vars: List[Any]
    at_most_t_faults: List[Any]
    condition: List[Any]
    gen_syn_z3: List[Any]
    data_qubits: List[Any]
    stab_txt_path: str
    log_txt_path: str
    witness_mode: str
    num_fault_vars: int
    num_fault_sites: int
    num_meas_syn_bits: int = 0


def build_path_constraint(
    path: List[dict],
    t: int,
    gen_syn: list,
    all_condition: list,
    stab_txt_path: str,
    log_txt_path: str,
    verify_mode: str = "type1",
) -> PathConstraint:
    vars = [v for step in path for info in step["site_info"] for v in info["vars"].values()]
    faults = [info["act"] for step in path for info in step["site_info"]]
    num_fault_vars = len({str(v) for v in vars})
    num_fault_sites = len(faults)

    gen_syn_z3 = []
    for kind, idx in gen_syn:
        ancX = path[idx]["state"]["ancX"]
        ancZ = path[idx]["state"]["ancZ"]
        flagX = path[idx]["state"]["flagX"]
        flagZ = path[idx]["state"]["flagZ"]

        ancX = [q for g in ancX for q in (g if isinstance(g, list) else [g])]
        ancZ = [q for g in ancZ for q in (g if isinstance(g, list) else [g])]
        flagX = [q for g in flagX for q in (g if isinstance(g, list) else [g])]
        flagZ = [q for g in flagZ for q in (g if isinstance(g, list) else [g])]

        if kind == "s":
            gen_syn_z3 += [a.z for a in ancX] + [a.x for a in ancZ]
        elif kind == "f":
            gen_syn_z3 += [q.z for q in flagX] + [q.x for q in flagZ]

    at_most_t_faults = [PbLe([(f, 1) for f in faults], t)] if faults else [BoolVal(True)]

    return PathConstraint(
        vars=vars,
        at_most_t_faults=at_most_t_faults,
        condition=all_condition,
        gen_syn_z3=gen_syn_z3,
        data_qubits=path[-1]["state"]["data"],
        stab_txt_path=stab_txt_path,
        log_txt_path=log_txt_path,
        witness_mode=verify_mode,
        num_fault_vars=num_fault_vars,
        num_fault_sites=num_fault_sites,
    )


def _count_path_gates_excluding_barrier(path_steps: List[Dict[str, Any]], cfg: Dict[str, Any]) -> int:
    gate_count = 0
    for step in path_steps:
        instr = step.get("instruction")
        if not instr or instr not in cfg:
            continue
        try:
            qc = load_qasm(cfg[instr])
            gate_count += len(get_gate_only_indices(qc))
        except Exception:
            continue
    return gate_count


def _resolve_unified_cnf_dir(config: Dict[str, Any], cnf_dir: Optional[str]) -> Path:
    if cnf_dir:
        return Path(cnf_dir).resolve()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "export"
    return (Path("cnf_out_unified") / stem).resolve()


def find_lut_instr_in_path(full_path: List[dict]) -> Optional[str]:
    instr = full_path[-1].get("instruction") if full_path else None
    if instr and str(instr).startswith("LUT_"):
        return instr
    for prev_step in reversed(full_path[:-1]):
        prev_instr = prev_step.get("instruction")
        if prev_instr and str(prev_instr).startswith("LUT_"):
            return prev_instr
    return None


def _flatten_register_qubits(regs: list) -> List[Any]:
    flat: List[Any] = []
    for g in regs:
        if isinstance(g, list):
            flat.extend(g)
        else:
            flat.append(g)
    return flat


def _measured_bits_from_step_state(state: Dict[str, Any]) -> List[Any]:
    """One round: ancX.z + ancZ.x + flagX.z + flagZ.x."""
    ancX = _flatten_register_qubits(state.get("ancX", []))
    ancZ = _flatten_register_qubits(state.get("ancZ", []))
    flagX = _flatten_register_qubits(state.get("flagX", []))
    flagZ = _flatten_register_qubits(state.get("flagZ", []))
    return (
        [q.z for q in ancX]
        + [q.x for q in ancZ]
        + [q.z for q in flagX]
        + [q.x for q in flagZ]
    )


def _all_measured_outcomes_z3(path: List[dict]) -> List[Any]:
    """Concatenate measured bits from every path step (all rounds, no LUT)."""
    bits: List[Any] = []
    for step in path:
        state = step.get("state")
        if state is None:
            continue
        bits.extend(_measured_bits_from_step_state(state))
    if not bits:
        raise ValueError("no measured outcomes on path")
    return bits


def _lut_gen_syn_z3(path: List[dict], lut_pairs: list) -> List[Any]:
    gen_syn_z3: List[Any] = []
    for kind, idx in lut_pairs:
        ancX = path[idx]["state"]["ancX"]
        ancZ = path[idx]["state"]["ancZ"]
        flagX = path[idx]["state"]["flagX"]
        flagZ = path[idx]["state"]["flagZ"]

        ancX = [q for g in ancX for q in (g if isinstance(g, list) else [g])]
        ancZ = [q for g in ancZ for q in (g if isinstance(g, list) else [g])]
        flagX = [q for g in flagX for q in (g if isinstance(g, list) else [g])]
        flagZ = [q for g in flagZ for q in (g if isinstance(g, list) else [g])]

        if kind == "s":
            gen_syn_z3 += [a.z for a in ancX] + [a.x for a in ancZ]
        elif kind == "f":
            gen_syn_z3 += [q.z for q in flagX] + [q.x for q in flagZ]
    return gen_syn_z3


def build_unified_path_constraint(
    path: List[dict],
    t: int,
    config: Dict[str, Any],
) -> PathConstraint:
    """
    Unified path constraint: gen_syn_z3 = all measured outcomes on every round.
    Branch conditions only (no syn_constraint, no LUT, no pred_syn).
    Raises ValueError if the path has no measured bits.
    """
    gen_syn_z3 = _all_measured_outcomes_z3(path)

    all_condition = [s["condition"] for s in path if s["condition"] is not None]
    stab_txt_path = config["stab_txt_path"]
    log_txt_path = config["log_txt_path"]

    vars = [v for step in path for info in step["site_info"] for v in info["vars"].values()]
    faults = [info["act"] for step in path for info in step["site_info"]]
    at_most_t_faults = [PbLe([(f, 1) for f in faults], t)] if faults else [BoolVal(True)]

    constraint = PathConstraint(
        vars=vars,
        at_most_t_faults=at_most_t_faults,
        condition=all_condition,
        gen_syn_z3=gen_syn_z3,
        data_qubits=path[-1]["state"]["data"],
        stab_txt_path=stab_txt_path,
        log_txt_path=log_txt_path,
        witness_mode="unified",
        num_fault_vars=len({str(v) for v in vars}),
        num_fault_sites=len(faults),
        num_meas_syn_bits=len(gen_syn_z3),
    )
    return constraint


def _resolve_cnf_dir(config: Dict[str, Any], cnf_dir: Optional[str]) -> Path:
    if cnf_dir:
        return Path(cnf_dir).resolve()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "export"
    return (Path("cnf_out") / stem).resolve()


def aggregate_job_solve_stats(path_query_stats: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-job totals and min/max DIMACS size across solved paths."""
    if not path_query_stats:
        return {
            "path_count": 0,
            "total_solver_runtime_s": 0.0,
            "total_sat_query_count": 0,
            "sat_path_count": 0,
            "min_dimacs_vars": 0,
            "max_dimacs_vars": 0,
            "min_clauses": 0,
            "max_clauses": 0,
        }
    vars_list = [int(row.get("total_dimacs_vars", 0) or 0) for row in path_query_stats]
    clause_list = [int(row.get("total_clauses", 0) or 0) for row in path_query_stats]
    return {
        "path_count": len(path_query_stats),
        "total_solver_runtime_s": sum(
            float(row.get("solver_runtime_seconds", 0.0) or 0.0)
            for row in path_query_stats
        ),
        "total_sat_query_count": sum(
            int(row.get("sat_query_count", 0) or 0) for row in path_query_stats
        ),
        "sat_path_count": sum(
            1 for row in path_query_stats if row.get("status") == "sat"
        ),
        "min_dimacs_vars": min(vars_list),
        "max_dimacs_vars": max(vars_list),
        "min_clauses": min(clause_list),
        "max_clauses": max(clause_list),
    }


def format_job_aggregate_lines(agg: Dict[str, Any], *, wall_time_s: float | None = None) -> List[str]:
    lines = ["Job aggregate (across solved paths):"]
    if wall_time_s is not None:
        lines.append(f"  Wall time (export + solve): {wall_time_s:.6f} s")
    lines.append(
        f"  Solver time (sum of paths): {agg['total_solver_runtime_s']:.6f} s"
    )
    lines.append(f"  SAT solver calls (sum sat_query_count): {agg['total_sat_query_count']}")
    lines.append(f"  Paths with SAT result: {agg['sat_path_count']}/{agg['path_count']}")
    lines.append(
        f"  DIMACS variables (min / max): {agg['min_dimacs_vars']} / {agg['max_dimacs_vars']}"
    )
    lines.append(f"  Clauses (min / max): {agg['min_clauses']} / {agg['max_clauses']}")
    return lines


def format_proof_metrics_report(
    path_query_stats: List[Dict[str, Any]],
    t: int,
    total_paths: int,
    *,
    wall_time_s: float | None = None,
    verify_pipeline: str | None = None,
) -> str:
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(f"Max faults per path (t): {t}")
    lines.append(f"Total number of paths: {total_paths}")
    lines.append("Per-path SAT metrics:")
    lines.append(
        "  path_idx | type | last_instr              | status | gate_count | runtime_s | "
        "peak_rss_mb | fault_vars | dimacs_vars | total_clauses | sat_query_count"
    )
    for row in sorted(path_query_stats, key=lambda r: r["path_index"]):
        peak_rss_bytes = row.get("peak_solver_rss_bytes", 0) or 0
        peak_rss_mb = peak_rss_bytes / (1024 * 1024)
        lines.append(
            "  "
            f"{row['path_index']:>7} | "
            f"{row['path_type']:>4} | "
            f"{row.get('last_instr', ''):<23} | "
            f"{row['status']:<7} | "
            f"{row.get('gate_count', 0):>10} | "
            f"{row.get('solver_runtime_seconds', 0.0):>9.6f} | "
            f"{peak_rss_mb:>11.3f} | "
            f"{row.get('num_fault_vars', 0):>10} | "
            f"{row.get('total_dimacs_vars', 0):>11} | "
            f"{row.get('total_clauses', 0):>13} | "
            f"{row.get('sat_query_count', 0):>15}"
        )
    total_runtime_s = sum(row.get("solver_runtime_seconds", 0.0) or 0.0 for row in path_query_stats)
    total_sat_paths = sum(1 for row in path_query_stats if row.get("status") == "sat")
    break_sat_paths = sum(
        1 for row in path_query_stats
        if row.get("path_type") == 0 and row.get("status") == "sat"
    )
    break_total = sum(1 for row in path_query_stats if row.get("path_type") == 0)
    max_fault_vars = max((row.get("num_fault_vars", 0) or 0 for row in path_query_stats), default=0)
    max_dimacs_vars = max((row.get("total_dimacs_vars", 0) or 0 for row in path_query_stats), default=0)
    max_peak_rss_bytes = max((row.get("peak_solver_rss_bytes", 0) or 0 for row in path_query_stats), default=0)
    agg = aggregate_job_solve_stats(path_query_stats)
    lines.extend(format_job_aggregate_lines(agg, wall_time_s=wall_time_s))
    lines.append(f"Total runtime (sum of all paths): {total_runtime_s:.6f} s")
    solved_paths = len(path_query_stats)
    lines.append(f"Failed paths (SAT): {total_sat_paths}/{solved_paths}")
    lines.append(f"Total SAT paths: {total_sat_paths}/{total_paths}")
    if break_total:
        lines.append(f"Break-weight SAT (fail): {break_sat_paths}/{break_total}")
    if verify_pipeline == "control_flow":
        pred_syn_total = sum(1 for row in path_query_stats if row.get("path_type") == 1)
        pred_syn_fail = sum(
            1 for row in path_query_stats
            if row.get("path_type") == 1 and row.get("status") == "sat"
        )
        if pred_syn_total:
            lines.append(f"pred_syn fail (type 1): {pred_syn_fail}/{pred_syn_total}")
    lines.append(f"Max fault variables (across paths): {max_fault_vars}")
    lines.append(f"Max DIMACS variables (across paths): {max_dimacs_vars}")
    lines.append(f"Max peak RSS (across paths): {max_peak_rss_bytes / (1024 * 1024):.3f} MB")
    lines.append("=" * 80)
    return "\n".join(lines) + "\n"


def export_path_constraints(
    protocol,
    start_node: str,
    init_state,
    config: Dict,
    t: int,
    cnf_dir: Optional[str] = None,
    protocol_path: Optional[str] = None,
) -> Tuple[List[List[Dict]], List[Dict[str, Any]]]:
    """
    Traverse protocol paths and export uniqueness constraints as DIMACS per path.
    Does not call the SAT solver (phase 2 handles that).
    """
    quiet = bool(config.get("__quiet__", False))
    all_paths: List[List[Dict]] = []
    path_query_stats: List[Dict[str, Any]] = []
    exported_paths: List[Dict[str, Any]] = []

    out_dir = _resolve_cnf_dir(config, cnf_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def dfs(round_idx: int, node_id: str, cur_state, cur_groups, cur_path: List[Dict]):
        node = protocol[node_id]
        instr = node.instructions[0] if node.instructions else None
        state_after = cur_state
        site_info = []
        groups = cur_groups

        if instr is not None and node.branches:
            if instr not in config:
                raise KeyError(f"Instruction '{instr}' not found in config")
            qasm_path = config[instr]
            qc = load_qasm(qasm_path)
            gate_list = get_gate_only_indices(qc)
            groups = detect_qubit_groups(qc)
            state_after, site_info = symbolic_execution_of_state(
                qasm_path, cur_state, round_idx, fault_gate=gate_list, track_steps=False,
            )

        if groups is not None:
            state_dict = state_to_raw_expr_dict(state_after, groups)
        elif instr is None or instr == "Break" or (instr and instr.startswith("LUT_")):
            state_dict = state_to_raw_expr_dict(state_after, groups)
        else:
            state_dict = state_to_raw_expr_dict(state_after, groups)

        if f"{instr}_flag_group" in config:
            with open(config[f"{instr}_flag_group"], "r", encoding="utf-8") as f:
                flag_group = json.load(f)
            state_dict["flagX"] = [[state_dict.copy()["flagX"][i] for i in g] for g in flag_group["flagX"]]
            state_dict["flagZ"] = [[state_dict.copy()["flagZ"][i] for i in g] for g in flag_group["flagZ"]]

        if not node.branches:
            step = {
                "round": round_idx,
                "node": node_id,
                "next": None,
                "instruction": instr,
                "condition": None,
                "state": state_dict,
                "site_info": site_info,
            }
            full_path = cur_path + [step]
            all_paths.append(full_path)
            path_idx = len(all_paths) - 1
            path_gate_count = _count_path_gates_excluding_barrier(full_path, config)
            path_type, last_instr = classify_full_path(full_path)

            if path_type == 0:
                faults = [info["act"] for step in full_path for info in step["site_info"]]
                if not faults:
                    path_query_stats.append({
                        "path_index": path_idx,
                        "path_type": path_type,
                        "last_instr": last_instr,
                        "status": "not_verified",
                        "gate_count": path_gate_count,
                        "solver_runtime_seconds": 0.0,
                        "peak_solver_rss_bytes": 0,
                        "total_clauses": 0,
                        "sat_query_count": 0,
                    })
                    return

                all_condition = [s["condition"] for s in full_path if s["condition"] is not None]
                vars_list = [
                    v for step in full_path for info in step["site_info"]
                    for v in info["vars"].values()
                ]
                at_most_t_faults = [PbLe([(f, 1) for f in faults], t)]
                path_tag = f"path_{path_idx:03d}"
                export_stats = break_weight_export_dimacs(
                    vars_list,
                    at_most_t_faults,
                    all_condition,
                    full_path[-1]["state"]["data"],
                    config["stab_txt_path"],
                    t,
                    out_dir,
                    path_tag,
                    gsel_prefix=f"{path_tag}_gsel",
                )
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "exported",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "num_fault_vars": export_stats["num_fault_vars"],
                    "total_dimacs_vars": export_stats["total_dimacs_vars"],
                    "total_clauses": export_stats["total_clauses"],
                    "sat_query_count": export_stats["sat_query_count"],
                    "path_tag": path_tag,
                })
                exported_paths.append({
                    "path_index": path_idx,
                    "path_tag": path_tag,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "gate_count": path_gate_count,
                })
                if not quiet:
                    print(
                        f"Exported path {path_idx}: type {path_type} (break weight) -> "
                        f"{path_tag}.cnf ({export_stats['total_clauses']} clauses)"
                    )
                return

            lut_instr = None
            if instr and instr.startswith("LUT_"):
                lut_instr = instr
            else:
                for prev_step in reversed(full_path[:-1]):
                    prev_instr = prev_step.get("instruction")
                    if prev_instr and prev_instr.startswith("LUT_"):
                        lut_instr = prev_instr
                        break

            gen_syn = parse_lut_instr(lut_instr) if lut_instr else []
            all_condition = [s["condition"] for s in full_path if s["condition"] is not None]

            if path_type == 2:
                E_x = [dq.x for dq in full_path[-1]["state"]["data"]]
                E_z = [dq.z for dq in full_path[-1]["state"]["data"]]
                gens, syn_measured = last_ancilla_formulas(full_path, config)
                pred_syn = stabilizer_syndrome_from_data(E_x, E_z, gens)
                syn_constraint = And(*[s_m == s_p for s_m, s_p in zip(syn_measured, pred_syn)])
                all_condition = all_condition + [syn_constraint]
                verify_mode = "type2"
            elif instr and instr.startswith("LUT_"):
                verify_mode = "type1"
            else:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "not_verified",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            constraint = build_path_constraint(
                full_path, t, gen_syn, all_condition,
                config["stab_txt_path"], config["log_txt_path"], verify_mode=verify_mode,
            )
            path_tag = f"path_{path_idx:03d}"
            export_stats = uniqueness_export_dimacs(
                constraint.vars,
                constraint.at_most_t_faults,
                constraint.condition,
                constraint.gen_syn_z3,
                constraint.data_qubits,
                constraint.stab_txt_path,
                constraint.log_txt_path,
                out_dir,
                path_tag,
                witness_mode=constraint.witness_mode,
            )

            row = {
                "path_index": path_idx,
                "path_type": path_type,
                "last_instr": last_instr,
                "status": "exported",
                "gate_count": path_gate_count,
                "solver_runtime_seconds": 0.0,
                "peak_solver_rss_bytes": 0,
                "num_fault_vars": export_stats["num_fault_vars"],
                "total_dimacs_vars": export_stats["total_dimacs_vars"],
                "total_clauses": export_stats["total_clauses"],
                "sat_query_count": export_stats["sat_query_count"],
                "path_tag": path_tag,
            }
            path_query_stats.append(row)
            exported_paths.append({
                "path_index": path_idx,
                "path_tag": path_tag,
                "path_type": path_type,
                "last_instr": last_instr,
                "gate_count": path_gate_count,
            })

            if not quiet:
                print(
                    f"Exported path {path_idx}: type {path_type} -> "
                    f"{path_tag}.cnf ({export_stats['total_clauses']} clauses)"
                )
            return

        for br in node.branches:
            cond_dict = br.condition.to_dict() if br.condition is not None else None
            full_state = [s["state"] for s in cur_path] + [state_dict]
            z3_condition = condition_to_z3(cond_dict, full_state, groups)
            step = {
                "round": round_idx,
                "node": node_id,
                "next": br.target,
                "instruction": instr,
                "condition": z3_condition,
                "state": state_dict,
                "site_info": site_info,
            }
            next_groups = data_only_groups_from_state_dict(state_dict)
            dfs(round_idx + 1, br.target, state_after, next_groups, cur_path + [step])

    dfs(0, start_node, init_state, None, [])

    cfg_path = config.get("__config_path__")
    config_stem = Path(cfg_path).stem if cfg_path else out_dir.name
    manifest = {
        "config_stem": config_stem,
        "protocol": protocol_path or config.get("protocol_path", ""),
        "t": t,
        "cnf_dir": str(out_dir),
        "total_paths": len(all_paths),
        "exported_paths": len(exported_paths),
        "paths": exported_paths,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if not quiet:
        print(f"Exported {len(exported_paths)} DIMACS file(s) to {out_dir}")

    return all_paths, path_query_stats


def export_unified_path_constraints(
    protocol,
    start_node: str,
    init_state,
    config: Dict,
    t: int,
    cnf_dir: Optional[str] = None,
    protocol_path: Optional[str] = None,
    *,
    collect_only: bool = False,
) -> Tuple[List[List[Dict]], List[Dict[str, Any]]]:
    """
    Unified export: all path types use all-round measured gen_syn + logical-product witness.
    Does not call the SAT solver (phase 2 handles that).
    """
    quiet = bool(config.get("__quiet__", False))
    all_paths: List[List[Dict]] = []
    path_query_stats: List[Dict[str, Any]] = []
    exported_paths: List[Dict[str, Any]] = []

    out_dir = _resolve_unified_cnf_dir(config, cnf_dir)
    if not collect_only:
        out_dir.mkdir(parents=True, exist_ok=True)

    def dfs(round_idx: int, node_id: str, cur_state, cur_groups, cur_path: List[Dict]):
        node = protocol[node_id]
        instr = node.instructions[0] if node.instructions else None
        state_after = cur_state
        site_info = []
        groups = cur_groups

        if instr is not None and node.branches:
            if instr not in config:
                raise KeyError(f"Instruction '{instr}' not found in config")
            qasm_path = config[instr]
            qc = load_qasm(qasm_path)
            gate_list = get_gate_only_indices(qc)
            groups = detect_qubit_groups(qc)
            state_after, site_info = symbolic_execution_of_state(
                qasm_path, cur_state, round_idx, fault_gate=gate_list, track_steps=False,
            )

        if groups is not None:
            state_dict = state_to_raw_expr_dict(state_after, groups)
        elif instr is None or instr == "Break" or (instr and instr.startswith("LUT_")):
            state_dict = state_to_raw_expr_dict(state_after, groups)
        else:
            state_dict = state_to_raw_expr_dict(state_after, groups)

        if f"{instr}_flag_group" in config:
            with open(config[f"{instr}_flag_group"], "r", encoding="utf-8") as f:
                flag_group = json.load(f)
            state_dict["flagX"] = [[state_dict.copy()["flagX"][i] for i in g] for g in flag_group["flagX"]]
            state_dict["flagZ"] = [[state_dict.copy()["flagZ"][i] for i in g] for g in flag_group["flagZ"]]

        if not node.branches:
            step = {
                "round": round_idx,
                "node": node_id,
                "next": None,
                "instruction": instr,
                "condition": None,
                "state": state_dict,
                "site_info": site_info,
            }
            full_path = cur_path + [step]
            all_paths.append(full_path)
            path_idx = len(all_paths) - 1
            path_gate_count = _count_path_gates_excluding_barrier(full_path, config)
            path_type, last_instr = classify_full_path(full_path)

            faults = [info["act"] for step in full_path for info in step["site_info"]]
            if not faults:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "not_verified",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            if collect_only:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "collected",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            try:
                constraint = build_unified_path_constraint(full_path, t, config)
            except ValueError as exc:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "not_verified",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                    "error": str(exc),
                })
                return

            path_tag = f"path_{path_idx:03d}"
            export_stats = uniqueness_export_dimacs_unified(
                constraint.vars,
                constraint.at_most_t_faults,
                constraint.condition,
                constraint.gen_syn_z3,
                constraint.data_qubits,
                constraint.stab_txt_path,
                constraint.log_txt_path,
                out_dir,
                path_tag,
                num_meas_syn_bits=constraint.num_meas_syn_bits,
            )

            row = {
                "path_index": path_idx,
                "path_type": path_type,
                "last_instr": last_instr,
                "status": "exported",
                "gate_count": path_gate_count,
                "solver_runtime_seconds": 0.0,
                "peak_solver_rss_bytes": 0,
                "num_fault_vars": export_stats["num_fault_vars"],
                "total_dimacs_vars": export_stats["total_dimacs_vars"],
                "total_clauses": export_stats["total_clauses"],
                "sat_query_count": export_stats["sat_query_count"],
                "path_tag": path_tag,
                "num_meas_syn_bits": constraint.num_meas_syn_bits,
            }
            path_query_stats.append(row)
            exported_paths.append({
                "path_index": path_idx,
                "path_tag": path_tag,
                "path_type": path_type,
                "last_instr": last_instr,
                "gate_count": path_gate_count,
            })

            if not quiet:
                print(
                    f"Exported unified path {path_idx}: type {path_type} -> {path_tag}.cnf "
                    f"({export_stats['total_clauses']} clauses, "
                    f"gen_syn={constraint.num_meas_syn_bits} measured bits)"
                )
            return

        for br in node.branches:
            cond_dict = br.condition.to_dict() if br.condition is not None else None
            full_state = [s["state"] for s in cur_path] + [state_dict]
            z3_condition = condition_to_z3(cond_dict, full_state, groups)
            step = {
                "round": round_idx,
                "node": node_id,
                "next": br.target,
                "instruction": instr,
                "condition": z3_condition,
                "state": state_dict,
                "site_info": site_info,
            }
            next_groups = data_only_groups_from_state_dict(state_dict)
            dfs(round_idx + 1, br.target, state_after, next_groups, cur_path + [step])

    dfs(0, start_node, init_state, None, [])

    cfg_path = config.get("__config_path__")
    config_stem = Path(cfg_path).stem if cfg_path else out_dir.name
    manifest = {
        "config_stem": config_stem,
        "protocol": protocol_path or config.get("protocol_path", ""),
        "t": t,
        "cnf_dir": str(out_dir),
        "verify_pipeline": "unified",
        "total_paths": len(all_paths),
        "exported_paths": len(exported_paths),
        "paths": exported_paths,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if not quiet:
        print(f"Exported {len(exported_paths)} unified DIMACS file(s) to {out_dir}")

    return all_paths, path_query_stats


def _resolve_control_flow_cnf_dir(config: Dict[str, Any], cnf_dir: Optional[str]) -> Path:
    if cnf_dir:
        return Path(cnf_dir).resolve()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "export"
    return (Path("cnf_out_control_flow") / stem).resolve()


def export_control_flow_path_constraints(
    protocol,
    start_node: str,
    init_state,
    config: Dict,
    t: int,
    cnf_dir: Optional[str] = None,
    protocol_path: Optional[str] = None,
    *,
    collect_only: bool = False,
) -> Tuple[List[List[Dict]], List[Dict[str, Any]]]:
    """
    Control-flow export: type 0 break-weight, type 1 pred_syn_diff, type 2 skipped.
    Does not call the SAT solver (phase 2 handles that).
    """
    quiet = bool(config.get("__quiet__", False))
    all_paths: List[List[Dict]] = []
    path_query_stats: List[Dict[str, Any]] = []
    exported_paths: List[Dict[str, Any]] = []

    out_dir = _resolve_control_flow_cnf_dir(config, cnf_dir)
    if not collect_only:
        out_dir.mkdir(parents=True, exist_ok=True)

    def dfs(round_idx: int, node_id: str, cur_state, cur_groups, cur_path: List[Dict]):
        node = protocol[node_id]
        instr = node.instructions[0] if node.instructions else None
        state_after = cur_state
        site_info = []
        groups = cur_groups

        if instr is not None and node.branches:
            if instr not in config:
                raise KeyError(f"Instruction '{instr}' not found in config")
            qasm_path = config[instr]
            qc = load_qasm(qasm_path)
            gate_list = get_gate_only_indices(qc)
            groups = detect_qubit_groups(qc)
            state_after, site_info = symbolic_execution_of_state(
                qasm_path, cur_state, round_idx, fault_gate=gate_list, track_steps=False,
            )

        if groups is not None:
            state_dict = state_to_raw_expr_dict(state_after, groups)
        elif instr is None or instr == "Break" or (instr and instr.startswith("LUT_")):
            state_dict = state_to_raw_expr_dict(state_after, groups)
        else:
            state_dict = state_to_raw_expr_dict(state_after, groups)

        if f"{instr}_flag_group" in config:
            with open(config[f"{instr}_flag_group"], "r", encoding="utf-8") as f:
                flag_group = json.load(f)
            state_dict["flagX"] = [[state_dict.copy()["flagX"][i] for i in g] for g in flag_group["flagX"]]
            state_dict["flagZ"] = [[state_dict.copy()["flagZ"][i] for i in g] for g in flag_group["flagZ"]]

        if not node.branches:
            step = {
                "round": round_idx,
                "node": node_id,
                "next": None,
                "instruction": instr,
                "condition": None,
                "state": state_dict,
                "site_info": site_info,
            }
            full_path = cur_path + [step]
            all_paths.append(full_path)
            path_idx = len(all_paths) - 1
            path_gate_count = _count_path_gates_excluding_barrier(full_path, config)
            path_type, last_instr = classify_full_path(full_path)

            if path_type == 2:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "skipped",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            faults = [info["act"] for step in full_path for info in step["site_info"]]
            if not faults:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "not_verified",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            if collect_only:
                path_query_stats.append({
                    "path_index": path_idx,
                    "path_type": path_type,
                    "last_instr": last_instr,
                    "status": "collected",
                    "gate_count": path_gate_count,
                    "solver_runtime_seconds": 0.0,
                    "peak_solver_rss_bytes": 0,
                    "total_clauses": 0,
                    "sat_query_count": 0,
                })
                return

            all_condition = [s["condition"] for s in full_path if s["condition"] is not None]
            vars_list = [
                v for step in full_path for info in step["site_info"]
                for v in info["vars"].values()
            ]
            at_most_t_faults = [PbLe([(f, 1) for f in faults], t)]
            path_tag = f"path_{path_idx:03d}"

            if path_type == 0:
                export_stats = break_weight_export_dimacs(
                    vars_list,
                    at_most_t_faults,
                    all_condition,
                    full_path[-1]["state"]["data"],
                    config["stab_txt_path"],
                    t,
                    out_dir,
                    path_tag,
                    gsel_prefix=f"{path_tag}_gsel",
                )
                if not quiet:
                    print(
                        f"Exported control-flow path {path_idx}: type 0 (break weight) -> "
                        f"{path_tag}.cnf ({export_stats['total_clauses']} clauses)"
                    )
            else:
                lut_instr = find_lut_instr_in_path(full_path)
                if not lut_instr:
                    path_query_stats.append({
                        "path_index": path_idx,
                        "path_type": path_type,
                        "last_instr": last_instr,
                        "status": "not_verified",
                        "gate_count": path_gate_count,
                        "solver_runtime_seconds": 0.0,
                        "peak_solver_rss_bytes": 0,
                        "total_clauses": 0,
                        "sat_query_count": 0,
                    })
                    return

                gen_syn = parse_lut_instr(lut_instr)
                constraint = build_path_constraint(
                    full_path,
                    t,
                    gen_syn,
                    all_condition,
                    config["stab_txt_path"],
                    config["log_txt_path"],
                    verify_mode="type1",
                )
                export_stats = control_flow_export_dimacs(
                    constraint.vars,
                    constraint.at_most_t_faults,
                    constraint.condition,
                    constraint.data_qubits,
                    constraint.stab_txt_path,
                    out_dir,
                    path_tag,
                    constraint.gen_syn_z3,
                )
                if not quiet:
                    print(
                        f"Exported control-flow path {path_idx}: type 1 (same gen_syn, pred_syn_diff) -> "
                        f"{path_tag}.cnf ({export_stats['total_clauses']} clauses, "
                        f"gen_syn={len(constraint.gen_syn_z3)} bits)"
                    )

            row = {
                "path_index": path_idx,
                "path_type": path_type,
                "last_instr": last_instr,
                "status": "exported",
                "gate_count": path_gate_count,
                "solver_runtime_seconds": 0.0,
                "peak_solver_rss_bytes": 0,
                "num_fault_vars": export_stats["num_fault_vars"],
                "total_dimacs_vars": export_stats["total_dimacs_vars"],
                "total_clauses": export_stats["total_clauses"],
                "sat_query_count": export_stats["sat_query_count"],
                "path_tag": path_tag,
            }
            path_query_stats.append(row)
            exported_paths.append({
                "path_index": path_idx,
                "path_tag": path_tag,
                "path_type": path_type,
                "last_instr": last_instr,
                "gate_count": path_gate_count,
            })
            return

        for br in node.branches:
            cond_dict = br.condition.to_dict() if br.condition is not None else None
            full_state = [s["state"] for s in cur_path] + [state_dict]
            z3_condition = condition_to_z3(cond_dict, full_state, groups)
            step = {
                "round": round_idx,
                "node": node_id,
                "next": br.target,
                "instruction": instr,
                "condition": z3_condition,
                "state": state_dict,
                "site_info": site_info,
            }
            next_groups = data_only_groups_from_state_dict(state_dict)
            dfs(round_idx + 1, br.target, state_after, next_groups, cur_path + [step])

    dfs(0, start_node, init_state, None, [])

    cfg_path = config.get("__config_path__")
    config_stem = Path(cfg_path).stem if cfg_path else out_dir.name
    manifest = {
        "config_stem": config_stem,
        "protocol": protocol_path or config.get("protocol_path", ""),
        "t": t,
        "cnf_dir": str(out_dir),
        "verify_pipeline": "control_flow",
        "total_paths": len(all_paths),
        "exported_paths": len(exported_paths),
        "paths": exported_paths,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if not quiet:
        print(f"Exported {len(exported_paths)} control-flow DIMACS file(s) to {out_dir}")

    return all_paths, path_query_stats

