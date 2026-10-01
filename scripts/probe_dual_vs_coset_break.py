#!/usr/bin/env python3
"""Probe dual Pauli stab encoding vs SMT ForAll (and optional coset Z3) on Type-0 Break."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from z3 import (
    And,
    ForAll,
    Goal,
    Implies,
    Or,
    PbEq,
    PbGe,
    PbLe,
    Solver,
    sat,
    unknown,
    unsat,
)

from dimacs_export_protocol import export_control_flow_path_constraints
from flag_analysis import (
    break_acts_from_path,
    break_weight_build_goal,
    break_weight_build_goal_dual,
    break_weight_export_dimacs_dual,
    build_stab_equiv_errors,
    load_symplectic_txt,
    new_clean_circuit_state,
    read_config,
    set_quiet,
)
from proof_protocol import prepare_type0_path
from protocol import load_protocol
from stab_dual_encoding import count_paulis_weight_leq


def _status_name(r) -> str:
    if r == sat:
        return "sat"
    if r == unsat:
        return "unsat"
    return "unknown"


def _break_smt_forall(break_path, stab_txt_path: str, t: int, timeout_ms: int | None):
    data_acts, gate_fault_acts = break_acts_from_path(break_path)
    faults = data_acts + gate_fault_acts
    if not faults:
        return "skip", 0.0, {}

    compare_to_gate = bool(data_acts)
    conditions = [s["condition"] for s in break_path if s["condition"] is not None]
    data_qubits = break_path[-1]["state"]["data"]
    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]
    Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path, prefix="smt_gsel")
    b = [Or(xi, zi) for xi, zi in zip(Epx, Epz)]
    weight_lits = [(bi, 1) for bi in b]

    s = Solver()
    if timeout_ms is not None and timeout_ms > 0:
        s.set("timeout", timeout_ms)
    if conditions:
        s.add(And(*conditions))
    s.add(PbLe([(f, 1) for f in faults], t))

    if compare_to_gate:
        if not gate_fault_acts:
            body = PbGe(weight_lits, 1)
        else:
            fault_lits = [(f, 1) for f in gate_fault_acts]
            body = And(
                *[
                    Implies(PbEq(fault_lits, k), PbGe(weight_lits, k + 1))
                    for k in range(t + 1)
                ]
            )
    else:
        body = PbGe(weight_lits, t + 1)

    s.add(ForAll(gsel, body))
    t0 = time.perf_counter()
    r = s.check()
    return _status_name(r), time.perf_counter() - t0, {"compare_to_gate": compare_to_gate}


def _z3_check_goal(g: Goal, timeout_ms: int | None) -> tuple[str, float]:
    s = Solver()
    if timeout_ms is not None and timeout_ms > 0:
        s.set("timeout", timeout_ms)
    for f in g:
        s.add(f)
    t0 = time.perf_counter()
    r = s.check()
    return _status_name(r), time.perf_counter() - t0


def run_one(
    protocol_path: str,
    config_path: str,
    t: int,
    *,
    initial_data_error: bool,
    timeout_ms: int | None,
    export_dual_cnf: bool,
    check_coset_z3: bool,
    out_cnf_dir: Path | None,
) -> dict:
    set_quiet(True)
    start_node, protocol = load_protocol(protocol_path)
    config = read_config(config_path)
    config["__quiet__"] = True
    config["type0_only"] = 1
    config["protocol_path"] = str(Path(protocol_path).resolve())
    if initial_data_error:
        config["initial_data_error"] = 1

    gen = load_symplectic_txt(str(config["stab_txt_path"]))
    n = len(gen[0][0])
    m = len(gen)
    init_state = new_clean_circuit_state(n)

    all_paths, stats = export_control_flow_path_constraints(
        protocol,
        start_node,
        init_state,
        config,
        t,
        cnf_dir=None,
        protocol_path=str(Path(protocol_path).resolve()),
        collect_only=True,
    )

    type0 = []
    for i, p in enumerate(all_paths):
        pt = stats[i].get("path_type")
        if pt == 0 or (pt is None and (p[-1].get("instruction") == "Break")):
            type0.append((i, p))

    results = []
    for path_idx, full_path in type0:
        break_path = prepare_type0_path(
            full_path, config, init_state, protocol=protocol,
        )
        data_acts, gate_fault_acts = break_acts_from_path(break_path)
        faults = data_acts + gate_fault_acts
        if not faults:
            results.append({"path_index": path_idx, "status": "skip"})
            continue

        compare_to_gate = bool(data_acts)
        conditions = [s["condition"] for s in break_path if s["condition"] is not None]
        data_qubits = break_path[-1]["state"]["data"]
        vars_list = [
            v for step in break_path for info in step["site_info"]
            for v in info["vars"].values()
        ]
        at_most = [PbLe([(f, 1) for f in faults], t)]

        smt_st, smt_s, meta = _break_smt_forall(
            break_path, config["stab_txt_path"], t, timeout_ms,
        )

        g_dual, _ = break_weight_build_goal_dual(
            at_most,
            conditions,
            data_qubits,
            config["stab_txt_path"],
            t,
            log_txt_path=config.get("log_txt_path"),
            gate_fault_acts=gate_fault_acts if compare_to_gate else None,
            compare_residual_to_gate_faults=compare_to_gate,
        )
        dual_st, dual_s = _z3_check_goal(g_dual, timeout_ms)
        num_pauli = int(getattr(g_dual, "_dual_num_pauli_constraints", 0))

        row = {
            "path_index": path_idx,
            "last_instr": full_path[-1].get("instruction"),
            "compare_to_gate": compare_to_gate,
            "smt_forall": {"status": smt_st, "solve_s": smt_s},
            "dual_z3": {"status": dual_st, "solve_s": dual_s, "num_pauli_constraints": num_pauli},
            "status_match_smt_dual": smt_st == dual_st,
        }

        if check_coset_z3:
            g_coset, _ = break_weight_build_goal(
                at_most,
                conditions,
                data_qubits,
                config["stab_txt_path"],
                t,
                gate_fault_acts=gate_fault_acts if compare_to_gate else None,
                compare_residual_to_gate_faults=compare_to_gate,
            )
            coset_st, coset_s = _z3_check_goal(g_coset, timeout_ms)
            row["coset_z3"] = {"status": coset_st, "solve_s": coset_s}
            row["status_match_coset_dual"] = coset_st == dual_st

        if export_dual_cnf and out_cnf_dir is not None:
            path_tag = f"path_{path_idx:03d}"
            exp = break_weight_export_dimacs_dual(
                vars_list,
                at_most,
                conditions,
                data_qubits,
                config["stab_txt_path"],
                t,
                out_cnf_dir,
                path_tag,
                log_txt_path=config.get("log_txt_path"),
                gate_fault_acts=gate_fault_acts if compare_to_gate else None,
                compare_residual_to_gate_faults=compare_to_gate,
            )
            row["dual_cnf"] = {
                "total_dimacs_vars": exp["total_dimacs_vars"],
                "total_clauses": exp["total_clauses"],
                "num_pauli_constraints": exp.get("num_pauli_constraints"),
                "cnf_path": exp["cnf_path"],
            }

        results.append(row)

    return {
        "config": config_path,
        "protocol": protocol_path,
        "t": t,
        "n": n,
        "m": m,
        "coset_expand_size": 2**m,
        "dual_pauli_count_w_le_t": count_paulis_weight_leq(n, t),
        "initial_data_error": initial_data_error,
        "n_type0": len(type0),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--protocol",
        default="protocols/d_3_lai_protocol.json",
        help="Protocol JSON (default: d=3 for fast coset compare)",
    )
    ap.add_argument(
        "--config",
        default="[[5,1,3]]_[1,1,1,1]_T/[[5,1,3]]_[1,1,1,1]_T_lai_d_3_protocol_config.txt",
    )
    ap.add_argument("--t", type=int, default=1)
    ap.add_argument("--initial-data-error", action="store_true")
    ap.add_argument("--timeout-s", type=float, default=120.0)
    ap.add_argument(
        "--check-coset-z3",
        action="store_true",
        help="Also build coset-expand goal and solve with Z3 (ok for small m)",
    )
    ap.add_argument(
        "--export-dual-cnf",
        action="store_true",
        help="Write dual CNFs under --cnf-dir",
    )
    ap.add_argument(
        "--cnf-dir",
        default="cnf_out_control_flow_dual/probe_dual",
        help="Dual CNF output dir when --export-dual-cnf",
    )
    ap.add_argument(
        "--out",
        default="results_txt/dual_encoding_probe.json",
        help="JSON probe report path",
    )
    args = ap.parse_args()

    timeout_ms = None if args.timeout_s <= 0 else int(args.timeout_s * 1000)
    out_cnf = Path(args.cnf_dir) if args.export_dual_cnf else None
    if out_cnf is not None:
        out_cnf.mkdir(parents=True, exist_ok=True)

    report = run_one(
        args.protocol,
        args.config,
        args.t,
        initial_data_error=args.initial_data_error,
        timeout_ms=timeout_ms,
        export_dual_cnf=args.export_dual_cnf,
        check_coset_z3=args.check_coset_z3,
        out_cnf_dir=out_cnf,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    matches = [r.get("status_match_smt_dual") for r in report["results"] if "status_match_smt_dual" in r]
    print(
        f"n={report['n']} m={report['m']} coset=2^{report['m']}={report['coset_expand_size']} "
        f"dual_P(wt≤t)={report['dual_pauli_count_w_le_t']}"
    )
    print(f"type0 paths: {report['n_type0']}; smt↔dual match: {sum(1 for x in matches if x)}/{len(matches)}")
    print(f"wrote {out}")
    return 0 if all(matches) else 2


if __name__ == "__main__":
    raise SystemExit(main())
