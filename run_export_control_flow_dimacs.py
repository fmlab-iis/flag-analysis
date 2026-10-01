#!/usr/bin/env python3
"""Phase 1 (control-flow): export path constraints (type0 break, type1 pred_syn_diff, type2 skip)."""

import argparse
import sys
from pathlib import Path

from dimacs_export_protocol import export_control_flow_path_constraints
from flag_analysis import load_symplectic_txt, new_clean_circuit_state, read_config, set_quiet
from protocol import load_protocol


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export control-flow path constraints to DIMACS (phase 1).",
    )
    parser.add_argument("--protocol", required=True, help="Path to protocol JSON")
    parser.add_argument("--config", required=True, help="Path to protocol config .txt")
    parser.add_argument("--t", type=int, default=1, help="Max faults per path (default: 1)")
    parser.add_argument(
        "--cnf-dir",
        default=None,
        help="Output directory (default: cnf_out_control_flow/<config_stem>/)",
    )
    parser.add_argument(
        "--initial-data-error",
        action="store_true",
        help="Enable symbolic initial data error on Type-0 (Break) paths only",
    )
    parser.add_argument(
        "--type0-only",
        action="store_true",
        help="Export only Type-0 (Break) paths; skip Type-1/Type-2 export",
    )
    parser.add_argument(
        "--stab-encoding",
        choices=("coset", "dual"),
        default=None,
        help="Stab encoding: dual (default, low-weight Pauli) or coset (legacy 2^m expand). "
        "Without --cnf-dir, dual writes to cnf_out_control_flow_dual/ and coset to cnf_out_control_flow/.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args()

    try:
        set_quiet(args.quiet)
        start_node, protocol = load_protocol(args.protocol)
        config = read_config(args.config)
        if args.quiet:
            config["__quiet__"] = True
        if args.initial_data_error:
            config["initial_data_error"] = 1
        if args.type0_only:
            config["type0_only"] = 1
        if args.stab_encoding:
            config["stab_encoding"] = args.stab_encoding
        config["protocol_path"] = str(Path(args.protocol).resolve())
        gen = load_symplectic_txt(str(config["stab_txt_path"]))
        init_state = new_clean_circuit_state(len(gen[0][0]))
        _, stats = export_control_flow_path_constraints(
            protocol,
            start_node,
            init_state,
            config,
            args.t,
            cnf_dir=args.cnf_dir,
            protocol_path=str(Path(args.protocol).resolve()),
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    exported = sum(1 for row in stats if row.get("status") == "exported")
    skipped = sum(1 for row in stats if row.get("status") == "skipped")
    if not args.quiet:
        print(
            f"Control-flow export complete: {exported} exported, {skipped} type-2 skipped."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
