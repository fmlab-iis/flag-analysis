#!/usr/bin/env python3
"""Probe LAI19 Type-0 Break with Z3 ForAll(gsel, ...) instead of 2^m expand."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from z3 import (
    And,
    ForAll,
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
    build_stab_equiv_errors,
    load_symplectic_txt,
    new_clean_circuit_state,
    read_config,
    set_quiet,
)
from proof_protocol import prepare_type0_path
from protocol import load_protocol


def _break_smt_check(
    break_path,
    stab_txt_path: str,
    t: int,
    *,
    timeout_ms: int | None,
) -> tuple[str, float]:
    data_acts, gate_fault_acts = break_acts_from_path(break_path)
    faults = data_acts + gate_fault_acts
    if not faults:
        return "skip", 0.0

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
    elapsed = time.perf_counter() - t0
    if r == sat:
        return "sat", elapsed
    if r == unsat:
        return "unsat", elapsed
    return "unknown", elapsed


def run_one(
    protocol_path: str,
    config_path: str,
    t: int,
    *,
    initial_data_error: bool,
    timeout_ms: int | None,
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
    init_state = new_clean_circuit_state(len(gen[0][0]))

    t_enum0 = time.perf_counter()
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
    enum_s = time.perf_counter() - t_enum0

    type0 = [
        (i, p)
        for i, p in enumerate(all_paths)
        if stats[i].get("path_type") == 0 or (
            p and (p[-1].get("instruction") or "").lower().startswith("break")
            or (p[-1].get("instruction") == "Break")
        )
    ]
    # Prefer stats path_type when present
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
        status, solve_s = _break_smt_check(
            break_path, config["stab_txt_path"], t, timeout_ms=timeout_ms,
        )
        results.append(
            {
                "path_index": path_idx,
                "status": status,
                "solve_s": solve_s,
                "last_instr": full_path[-1].get("instruction"),
            }
        )

    return {
        "config": config_path,
        "initial_data_error": initial_data_error,
        "enum_s": enum_s,
        "n_paths": len(all_paths),
        "n_type0": len(type0),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout-s", type=float, default=600.0, help="Z3 timeout per path (0=none)")
    ap.add_argument("--out", default="results_txt/lai19_break_smt_probe.json")
    args = ap.parse_args()
    timeout_ms = None if args.timeout_s <= 0 else int(args.timeout_s * 1000)

    jobs = [
        (
            "./protocols/d_5_lai_protocol.json",
            "./[[19,1,5]]_[1,1,1,...]_T/[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config.txt",
            False,
            "baseline_111",
        ),
        (
            "./protocols/d_5_lai_protocol.json",
            "./[[19,1,5]]_[2,2,2,1,1,1]_T/[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config.txt",
            False,
            "baseline_222",
        ),
        (
            "./protocols/d_5_lai_protocol.json",
            "./[[19,1,5]]_[1,1,1,...]_T/[[19,1,5]]_[1,1,1,...]_lai_d_5_protocol_config.txt",
            True,
            "initdata_111",
        ),
        (
            "./protocols/d_5_lai_protocol.json",
            "./[[19,1,5]]_[2,2,2,1,1,1]_T/[[19,1,5]]_[2,2,2,1,1,1]_T_lai_d_5_protocol_config.txt",
            True,
            "initdata_222",
        ),
    ]

    all_out = []
    for protocol, config, initdata, tag in jobs:
        print(f"\n==== {tag} initdata={initdata} ====", flush=True)
        t0 = time.perf_counter()
        try:
            row = run_one(
                protocol, config, t=2,
                initial_data_error=initdata,
                timeout_ms=timeout_ms,
            )
            row["tag"] = tag
            row["wall_s"] = time.perf_counter() - t0
            print(json.dumps(row, indent=2), flush=True)
            all_out.append(row)
        except Exception as exc:
            err = {"tag": tag, "error": str(exc)}
            print(err, flush=True)
            all_out.append(err)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_out, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
