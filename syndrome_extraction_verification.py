"""Per-circuit syndrome extraction checks for protocol config QASM files."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, TypeVar

from flag_analysis import load_symplectic_txt, pauli_string_to_symplectic, verify_syndrome_extraction
from dimacs_bridge import default_syndrome_sat_solver_bin

T = TypeVar("T")

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
X_Z_SUBSET_KEYS = frozenset({"raw_syndrome_x", "raw_syndrome_z"})
_PAULI_CHARS = frozenset("IXYZ")


def _read_vm_rss_kb() -> int:
    """Current resident set size in KiB (Linux /proc or getrusage fallback)."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss) if sys.platform == "darwin" else int(rss)


def _peak_rss_bytes_during(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Tuple[T, int]:
    """Return (fn result, peak RSS in bytes) while fn runs (poll /proc VmRSS)."""
    peak_kb = [_read_vm_rss_kb()]
    stop = threading.Event()

    def _poll() -> None:
        while not stop.is_set():
            peak_kb[0] = max(peak_kb[0], _read_vm_rss_kb())
            stop.wait(0.001)

    poll_thread = threading.Thread(target=_poll, daemon=True)
    poll_thread.start()
    try:
        result = fn(*args, **kwargs)
    finally:
        stop.set()
        poll_thread.join(timeout=1.0)
    return result, peak_kb[0] * 1024


def _split_stab_x_z_generators(stab_txt_path: str) -> Tuple[List, List]:
    """Split stab.txt rows into X-type (nonzero Sx) and Z-type (nonzero Sz) generators."""
    x_gens: List = []
    z_gens: List = []
    for sx, sz in load_symplectic_txt(stab_txt_path):
        if any(sx):
            x_gens.append((sx, sz))
        if any(sz):
            z_gens.append((sx, sz))
    return x_gens, z_gens


def _expected_qubit_count(stab_txt_path: str) -> int:
    gens = load_symplectic_txt(stab_txt_path)
    if not gens:
        return 0
    return len(gens[0][0])


def _is_pauli_string(name: str, *, n_qubits: int | None = None) -> bool:
    if not name or not all(ch in _PAULI_CHARS for ch in name):
        return False
    if n_qubits is not None and len(name) != n_qubits:
        return False
    return True


def pauli_names_from_circuit_key(
    circuit_key: str,
    *,
    n_qubits: int | None = None,
) -> List[str] | None:
    """
    Detect stabilizer Pauli name(s) for partial syndrome extraction.

    - flag_syndrome / raw_syndrome / raw_syndrome_x / raw_syndrome_z -> None
      (handled via full stab or X/Z subsets in stabilizer_generators_for_circuit)
    - IXZZX_flag / XIXZZ_raw -> single Pauli from prefix
    """
    if circuit_key in FULL_SYNDROME_KEYS or circuit_key in X_Z_SUBSET_KEYS:
        return None
    if circuit_key.endswith("_flag"):
        pauli = circuit_key[: -len("_flag")]
        if _is_pauli_string(pauli, n_qubits=n_qubits):
            return [pauli]
    if circuit_key.endswith("_raw"):
        pauli = circuit_key[: -len("_raw")]
        if _is_pauli_string(pauli, n_qubits=n_qubits):
            return [pauli]
    if _is_pauli_string(circuit_key, n_qubits=n_qubits):
        return [circuit_key]
    return None


def stabilizer_generators_for_circuit(
    circuit_key: str,
    stab_txt_path: str | None = None,
) -> Tuple[str, List | None]:
    """
    Return (mode, generators) where mode is full / partial / x_subset / z_subset.
    generators is None for full stab.txt mode.
    """
    if circuit_key == "raw_syndrome_x":
        if not stab_txt_path:
            raise ValueError("stab_txt_path required for raw_syndrome_x")
        x_gens, _ = _split_stab_x_z_generators(stab_txt_path)
        return "x_subset", x_gens
    if circuit_key == "raw_syndrome_z":
        if not stab_txt_path:
            raise ValueError("stab_txt_path required for raw_syndrome_z")
        _, z_gens = _split_stab_x_z_generators(stab_txt_path)
        return "z_subset", z_gens

    n_qubits = _expected_qubit_count(stab_txt_path) if stab_txt_path else None
    pauli_names = pauli_names_from_circuit_key(circuit_key, n_qubits=n_qubits)
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
        f"Solver: Z3 export + {default_syndrome_sat_solver_bin()} (SYNDROME_SAT_SOLVER_BIN)"
    )
    lines.append(
        "Full: flag_syndrome/raw_syndrome vs stab.txt; "
        "Partial: <PAULI>_flag/_raw vs named Pauli; "
        "raw_syndrome_x/z vs X/Z rows of stab.txt"
    )
    lines.append("Pass => ok; fail => ancilla mismatch or data not preserved")
    lines.append("Per-circuit metrics:")
    lines.append(
        "  circuit_key           | mode    | status  | ancilla | data | runtime_s | peak_rss_mb | reason"
    )
    for row in circuit_stats:
        peak_bytes = row.get("peak_rss_bytes", 0) or 0
        peak_mb = peak_bytes / (1024 * 1024)
        lines.append(
            "  "
            f"{row.get('circuit_key', ''):<21} | "
            f"{row.get('mode', ''):<7} | "
            f"{row.get('status', ''):<7} | "
            f"{str(row.get('ancilla_ok', '')):<7} | "
            f"{str(row.get('data_preserved', '')):<4} | "
            f"{row.get('runtime_s', 0.0) or 0.0:>9.6f} | "
            f"{peak_mb:>11.3f} | "
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
                peak_rss_bytes=0,
                reason="qasm file not found",
            )
            stats.append(row)
            continue

        t0 = time.perf_counter()
        mode, generators = stabilizer_generators_for_circuit(
            circuit_key, str(stab_path),
        )
        row["mode"] = mode
        if mode == "partial":
            row["pauli"] = pauli_names_from_circuit_key(
                circuit_key, n_qubits=_expected_qubit_count(str(stab_path)),
            )
        try:
            if generators is None:
                result, peak_rss_bytes = _peak_rss_bytes_during(
                    verify_syndrome_extraction,
                    str(qasm_path),
                    str(stab_path),
                    order=order,
                )
            else:
                result, peak_rss_bytes = _peak_rss_bytes_during(
                    verify_syndrome_extraction,
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
                peak_rss_bytes=0,
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
                peak_rss_bytes=peak_rss_bytes,
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
                peak_rss_bytes=peak_rss_bytes,
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
                peak_rss_bytes=peak_rss_bytes,
                reason=reason,
            )
        stats.append(row)

    write_syndrome_extraction_metrics_report(stats, config)
    return stats
