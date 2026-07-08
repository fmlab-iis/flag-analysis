#!/usr/bin/env python3
"""Verify flag-raised property (Step 3) for each flag circuit in a protocol config."""

import argparse
import sys
from pathlib import Path

from flag_analysis import read_config, set_quiet
from flag_raised_verification import run_flag_raised_verification


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify flag-raised property for each flag circuit in a protocol config.",
    )
    parser.add_argument("--config", required=True, help="Path to protocol config .txt")
    parser.add_argument(
        "--t",
        type=int,
        default=1,
        help="Max fault sites when --w is not set (default: 1)",
    )
    parser.add_argument(
        "--w",
        type=int,
        default=None,
        help="Max fault sites w for flag check; flag must raise when error weight > w (default: t). Overrides config flag_w if set.",
    )
    parser.add_argument(
        "--metrics-dir",
        default=None,
        help="Directory for flag-raised metrics TXT (default: results_txt or config dir)",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose Z3 output")
    args = parser.parse_args()

    try:
        set_quiet(args.quiet)
        config = read_config(args.config)
        if args.metrics_dir:
            config["metrics_dir"] = str(Path(args.metrics_dir).resolve())
        stats = run_flag_raised_verification(config, t=args.t, w=args.w)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    failed = [row for row in stats if row.get("status") != "pass"]
    if failed:
        if not args.quiet:
            print(
                f"FAILED: {len(failed)}/{len(stats)} flag circuit(s) failed.",
                file=sys.stderr,
            )
            for row in failed:
                print(
                    f"  {row.get('circuit_key')}: {row.get('reason', row.get('status'))}",
                    file=sys.stderr,
                )
        return 1

    if not args.quiet:
        print(f"SUCCESS: all {len(stats)} flag circuit(s) pass flag-raised check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
