"""Per-circuit syndrome extraction checks for protocol config QASM files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from flag_analysis import pauli_string_to_symplectic, verify_syndrome_extraction

CONFIG_SKIP_KEYS = frozenset(
    {
        "stab_txt_path",
        "log_txt_path",
        "metrics_dir",
        "unified_cnf_dir",
        "__config_path__",
        "__config_dir__",
        "__quiet__",
    }
)

FULL_SYNDROME_KEYS = frozenset({"flag_syndrome", "raw_syndrome"})
_PAULI_CHARS = frozenset("IXYZ")


def _is_pauli_string(name: str) -> bool:
    return bool(name) and all(ch in _PAULI_CHARS for ch in name)


def pauli_names_from_circuit_key(circuit_key: str) -> List[str] | None:
    """
    Detect stabilizer Pauli name(s) for partial syndrome extraction.

    - flag_syndrome / raw_syndrome -> None (use full stab.txt)
    - IXZZX_flag / XIXZZ_raw -> single Pauli from prefix
    - bare IXZZX key -> single Pauli if name is all I/X/Y/Z
    """
    if circuit_key in FULL_SYNDROME_KEYS:
        return None
    if circuit_key.endswith("_flag"):
        pauli = circuit_key[: -len("_flag")]
        if _is_pauli_string(pauli):
            return [pauli]
    if circuit_key.endswith("_raw"):
        pauli = circuit_key[: -len("_raw")]
        if _is_pauli_string(pauli):
            return [pauli]
    if _is_pauli_string(circuit_key):
        return [circuit_key]
    return None


def stabilizer_generators_for_circuit(circuit_key: str) -> Tuple[str, List | None]:
    """
    Return (mode, generators) where mode is 'full' or 'partial'.
    generators is None for full stab.txt mode.
    """
    pauli_names = pauli_names_from_circuit_key(circuit_key)
    if pauli_names is None:
        return "full", None
    gens = [pauli_string_to_symplectic(name) for name in pauli_names]
    return "partial", gens


def list_config_circuits(config: Dict[str, Any]) -> List[Tuple[str, Path]]:
    """Return (circuit_key, qasm_path) for every .qasm entry in a protocol config."""
    circuits: List[Tuple[str, Path]] = []
    for key, value in sorted(config.items()):
        if key in CONFIG_SKIP_KEYS or key.startswith("__"):
            continue
        path = Path(str(value))
        if path.suffix.lower() == ".qasm":
            circuits.append((key, path))
    return circuits


def _resolve_syndrome_metrics_path(config: Dict[str, Any]) -> Path:
    metrics_dir = config.get("metrics_dir")
    if metrics_dir:
        base_dir = Path(metrics_dir)
    elif config.get("__config_dir__"):
        base_dir = Path(config["__config_dir__"])
    else:
        stab_path = config.get("stab_txt_path")
        base_dir = Path(stab_path).parent if stab_path else Path.cwd()
    cfg_path = config.get("__config_path__")
    stem = Path(cfg_path).stem if cfg_path else "syndrome_extraction"
    return base_dir / f"{stem}_syndrome_extraction_metrics.txt"


def write_syndrome_extraction_metrics_report(
    circuit_stats: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Path:
    report_path = _resolve_syndrome_metrics_path(config)
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append("Syndrome extraction verification (no circuit faults)")
    lines.append(
        "Property: ancillas match stabilizer(s); data qubits preserved"
    )
    lines.append(
        "Full: flag_syndrome/raw_syndrome vs stab.txt; "
        "Partial: <PAULI>_flag/_raw vs named Pauli"
    )
    lines.append("Pass => ok; fail => ancilla mismatch or data not preserved")
    lines.append("Per-circuit metrics:")
    lines.append(
        "  circuit_key           | mode    | status  | ancilla | data | runtime_s | reason"
    )
    for row in circuit_stats:
        lines.append(
            "  "
            f"{row.get('circuit_key', ''):<21} | "
            f"{row.get('mode', ''):<7} | "
            f"{row.get('status', ''):<7} | "
            f"{str(row.get('ancilla_ok', '')):<7} | "
            f"{str(row.get('data_preserved', '')):<4} | "
            f"{row.get('runtime_s', 0.0) or 0.0:>9.6f} | "
            f"{row.get('reason', '')}"
        )
    fail_count = sum(1 for r in circuit_stats if r.get("status") != "pass")
    total_runtime = sum(r.get("runtime_s", 0.0) or 0.0 for r in circuit_stats)
    lines.append(f"Total runtime: {total_runtime:.6f} s")
    lines.append(f"Failed circuits: {fail_count}/{len(circuit_stats)}")
    lines.append("=" * 80)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run_syndrome_extraction_verification(
    config: Dict[str, Any],
    *,
    order: str = "X-then-Z",
) -> List[Dict[str, Any]]:
    stab_path = config.get("stab_txt_path")
    if not stab_path:
        raise KeyError("stab_txt_path missing from config")

    stats: List[Dict[str, Any]] = []
    for circuit_key, qasm_path in list_config_circuits(config):
        row: Dict[str, Any] = {
            "circuit_key": circuit_key,
            "qasm_path": str(qasm_path),
        }
        if not qasm_path.is_file():
            row.update(
                status="missing",
                ancilla_ok=False,
                data_preserved=False,
                runtime_s=0.0,
                reason="qasm file not found",
            )
            stats.append(row)
            continue

        t0 = time.perf_counter()
        mode, generators = stabilizer_generators_for_circuit(circuit_key)
        row["mode"] = mode
        if mode == "partial":
            row["pauli"] = pauli_names_from_circuit_key(circuit_key)
        try:
            if generators is None:
                result = verify_syndrome_extraction(
                    str(qasm_path),
                    str(stab_path),
                    order=order,
                )
            else:
                result = verify_syndrome_extraction(
                    str(qasm_path),
                    order=order,
                    generators=generators,
                )
        except Exception as exc:
            row.update(
                status="error",
                ancilla_ok=False,
                data_preserved=False,
                runtime_s=time.perf_counter() - t0,
                reason=str(exc),
            )
            stats.append(row)
            continue

        runtime_s = time.perf_counter() - t0
        if result["ok"]:
            row.update(
                status="pass",
                ancilla_ok=True,
                data_preserved=True,
                runtime_s=runtime_s,
                reason="",
            )
        elif not result["ancilla_ok"]:
            mismatches = result.get("mismatches", [])
            reason = (
                f"ancilla mismatch at indices {mismatches}"
                if mismatches
                else "ancilla count/order mismatch"
            )
            row.update(
                status="fail",
                ancilla_ok=False,
                data_preserved=False,
                runtime_s=runtime_s,
                reason=reason,
            )
        else:
            data_mm = result.get("data_mismatches", [])
            reason = (
                f"data not preserved at qubit indices {data_mm}"
                if data_mm
                else "data not preserved"
            )
            row.update(
                status="fail",
                ancilla_ok=True,
                data_preserved=False,
                runtime_s=runtime_s,
                reason=reason,
            )
        stats.append(row)

    write_syndrome_extraction_metrics_report(stats, config)
    return stats
