#!/usr/bin/env python3
"""Run low-weight path verification only (no uniqueness proof)."""

import argparse
import sys
from pathlib import Path

from flag_analysis import load_symplectic_txt, new_clean_circuit_state, read_config, set_quiet
from low_weight_verification import run_low_weight_verification
from protocol import load_protocol


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run single-fault low-weight verification (no uniqueness proof).",
    )
    parser.add_argument("--protocol", required=True, help="Path to protocol JSON")
    parser.add_argument("--config", required=True, help="Path to protocol config .txt")
    parser.add_argument(
        "--metrics-dir",
        default=None,
        help="Directory for low-weight metrics TXT (default: next to config file)",
    )
    parser.add_argument(
        "--unified",
        action="store_true",
        help="Use unified-style path traversal (all non-Break paths)",
    )
    parser.add_argument(
        "--cnf-dir",
        default=None,
        help="Ignored unless --unified; optional unified CNF directory override",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args()

    try:
        set_quiet(args.quiet)
        start_node, protocol = load_protocol(args.protocol)
        config = read_config(args.config)
        if args.metrics_dir:
            config["metrics_dir"] = str(Path(args.metrics_dir).resolve())
        if args.cnf_dir:
            config["unified_cnf_dir"] = str(Path(args.cnf_dir).resolve())
        if args.quiet:
            config["__quiet__"] = True
        gen = load_symplectic_txt(str(config["stab_txt_path"]))
        init_state = new_clean_circuit_state(len(gen[0][0]))
        _, stats = run_low_weight_verification(
            protocol,
            start_node,
            init_state,
            config,
            unified=args.unified,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    sat_low_weight = [row for row in stats if row.get("low_weight_status") == "sat"]
    if sat_low_weight:
        if not args.quiet:
            print(
                f"FAILED: {len(sat_low_weight)} low-weight path(s) returned SAT.",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        lw_verified = [
            row for row in stats if row.get("low_weight_status") == "unsat"
        ]
        skipped = [
            row for row in stats if row.get("low_weight_status") == "skipped"
        ]
        print(f"SUCCESS: all {len(lw_verified)} low-weight path(s) are UNSAT.")
        if skipped:
            print(f"Skipped {len(skipped)} path(s) (Break / no LUT / no faults).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
