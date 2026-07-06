#!/usr/bin/env python3
"""Verify every QASM circuit in a protocol config is a valid syndrome extractor."""

import argparse
import sys
from pathlib import Path

from flag_analysis import read_config, set_quiet
from syndrome_extraction_verification import run_syndrome_extraction_verification


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify syndrome extraction for each circuit in a protocol config.",
    )
    parser.add_argument("--config", required=True, help="Path to protocol config .txt")
    parser.add_argument(
        "--metrics-dir",
        default=None,
        help="Directory for syndrome extraction metrics TXT (default: results_txt or config dir)",
    )
    parser.add_argument(
        "--order",
        default="X-then-Z",
        choices=("X-then-Z", "Z-then-X"),
        help="How to concatenate ancX/ancZ vs stab.txt row order",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose output")
    args = parser.parse_args()

    try:
        set_quiet(args.quiet)
        config = read_config(args.config)
        if args.metrics_dir:
            config["metrics_dir"] = str(Path(args.metrics_dir).resolve())
        stats = run_syndrome_extraction_verification(config, order=args.order)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    failed = [row for row in stats if row.get("status") != "pass"]
    if failed:
        if not args.quiet:
            print(
                f"FAILED: {len(failed)}/{len(stats)} circuit(s) failed syndrome extraction.",
                file=sys.stderr,
            )
            for row in failed:
                print(
                    f"  {row.get('circuit_key')}: {row.get('reason', row.get('status'))}",
                    file=sys.stderr,
                )
        return 1

    if not args.quiet:
        print(f"SUCCESS: all {len(stats)} circuit(s) pass syndrome extraction.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
