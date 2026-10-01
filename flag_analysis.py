# flag_analysis.py
# Minimal Pauli-flow utilities focused on the FLAG qubit.
# Tested with Qiskit 2.x (with compatibility shims).
# Requires: pip install qiskit z3-solver

from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Any
from pathlib  import Path

PROJECT_ROOT = Path(__file__).resolve().parent
_QUIET = False


def set_quiet(quiet: bool) -> None:
    global _QUIET
    _QUIET = quiet


def _resolve_project_path(path: str | Path) -> Path:
    """Resolve a relative path from cwd or the project root."""
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    for candidate in (Path.cwd() / p, PROJECT_ROOT / p):
        if candidate.exists():
            return candidate.resolve()
    return (PROJECT_ROOT / p).resolve()


from dimacs_bridge import (
    build_dimacs,
    merge_dimacs_cnfs,
    default_sat_solver_bin,
    default_syndrome_sat_solver_bin,
    resolve_sat_solver_binary,
    resolve_cryptominisat_binary,
    run_dimacs_solver,
    run_cryptominisat,
    z3_expr_is_unsat,
    model_to_z3_assignment,
    pretty_print_z3_assignment,
    pretty_print_true_z3_vars,
    is_user_var,
)

from circuit_op import *
from protocol import *

from qiskit import QuantumCircuit


from z3 import BoolVal, Xor, Bool,simplify,substitute, And, Not,Or, PbLe, PbEq, AtMost,ForAll, Implies, Exists, PbGe, AtLeast

from z3 import Solver, unsat, sat, unknown, is_true

import ast
from pathlib import Path

def _looks_like_path(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return (
        value.endswith((".qasm", ".txt", ".json", ".cnf"))
        or "/" in value
        or "\\" in value
    )


def _resolve_config_path(value, base_dir: Path):
    if isinstance(value, str) and _looks_like_path(value):
        p = Path(value)
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        return str(p)
    return value


def read_config(path="config.txt"):
    config_file = _resolve_project_path(path)
    if not config_file.exists():
        raise FileNotFoundError(f"Missing config file: {path}")

    config_dir = config_file.parent
    config = {}
    for line in config_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            try:
                value = ast.literal_eval(value)
            except Exception:
                pass  # keep as string

            value = _resolve_config_path(value, config_dir)
            config[key] = value

            # Preserve origin so downstream reporting can save artifacts next to this config.
            config["__config_path__"] = str(config_file)
            config["__config_dir__"] = str(config_dir)

    return config




# ---------------------------
# Pauli-flow data structures
# ---------------------------

@dataclass
class QubitXZ:
    x: object  # z3 BoolRef (or BoolVal)
    z: object

@dataclass
class CircuitXZ:
    qubits: List[QubitXZ]


# ---------------------------
# Boolean helpers
# ---------------------------

def bfalse(): return BoolVal(False)
def bxor(a, b): return Xor(a, b)

# ---------- Qiskit 2.2 regmap helper ----------
def _regmap_indices(qc: QuantumCircuit):
    """Return dict: global_qubit_index -> (qreg_name, local_index)."""
    regmap = {}
    idx = 0
    for qreg in qc.qregs:
        for j in range(qreg.size):
            regmap[idx] = (qreg.name, j)
            idx += 1
    return regmap


def _format_qubit_label(qc: QuantumCircuit, qidx: int) -> str:
    reg, loc = _regmap_indices(qc)[qidx]
    return f"{reg}[{loc}]"


def _format_gate_qubits(qc: QuantumCircuit, qidxs: list[int]) -> str:
    return ", ".join(_format_qubit_label(qc, q) for q in qidxs)


def describe_gate_at_index(qasm_path: str, gate_index: int) -> str:
    """Return a human-readable description of gate `gate_index` in a QASM file."""
    qc = load_qasm(qasm_path)
    if gate_index < 0 or gate_index >= len(qc.data):
        raise IndexError(
            f"gate_index {gate_index} out of range (len={len(qc.data)})"
        )
    inst = qc.data[gate_index]
    name = inst.operation.name
    qidxs = [_qiskit_qubit_index(qc, q) for q in inst.qubits]
    return f"Gate index {gate_index}: {name} on {_format_gate_qubits(qc, qidxs)}"


def _gate_qubit_labels(qc: QuantumCircuit, gate_index: int) -> str:
    return _format_gate_qubits(
        qc,
        [_qiskit_qubit_index(qc, q) for q in qc.data[gate_index].qubits],
    )

def xor_list(lst):
    acc = BoolVal(False)
    for v in lst:
        acc = Xor(acc, v)
    return acc


# ---------------------------
# Group detection by register name
# ---------------------------

def detect_qubit_groups(qc: QuantumCircuit) -> Dict[str, List[int]]:
    """
    Group qubits by their register names for Qiskit 2.2 style.
    Each qreg has a name (like 'ancX', 'ancZ', 'flagX', 'flagZ', 'q'),
    and qubits are numbered globally across all registers.
    """
    groups = {'data': [], 'ancX': [], 'ancZ': [], 'flagX': [], 'flagZ': []}

    # Build mapping: global index → (register name, local index)
    idx = 0
    regmap = {}
    for qreg in qc.qregs:
        for j in range(qreg.size):
            regmap[idx] = qreg.name
            idx += 1

    # Classify each qubit index
    for i, reg in regmap.items():
        reg_l = reg.lower()
        if   reg_l.startswith("ancx"):  groups["ancX"].append(i)
        elif reg_l.startswith("ancz"):  groups["ancZ"].append(i)
        elif reg_l.startswith("flagx"): groups["flagX"].append(i)
        elif reg_l.startswith("flagz"): groups["flagZ"].append(i)
        else: groups["data"].append(i)

    return groups


def data_only_groups_from_state_dict(state_dict):
    # state_dict["data"] is a list of qubits (already extracted)
    n = len(state_dict.get("data", []))
    return {"data": list(range(n)), "ancX": [], "ancZ": [], "flagX": [], "flagZ": []}

# ---------------------------
# Split circuit 
# ---------------------------
from qiskit import QuantumCircuit, QuantumRegister



# ---------------------------
# Tools for state
# ---------------------------
def state_to_raw_expr_dict(state: CircuitXZ, groups: dict):
    """
    Convert CircuitXZ + groups into:
        {
          "data":  [QubitXZ, ...],
          "ancX":  [QubitXZ, ...],
          "ancZ":  [QubitXZ, ...],
          "flagX": [QubitXZ, ...],
          "flagZ": [QubitXZ, ...],
        }
    with list order matching groups[gname].
    """
    out = {}
    for gname in ["data", "ancX", "ancZ", "flagX", "flagZ"]:
        idxs = groups.get(gname, [])
        out[gname] = [state.qubits[phys_idx] for phys_idx in idxs]
    return out
def raw_expr_dict_to_state(raw: dict) -> CircuitXZ:
    """
    Reverse of state_to_raw_expr_dict.
    Input:  raw[g][i] = QubitXZ object for group g at index i.
    Output: a CircuitXZ with qubits arranged in correct global positions.
    """
    # 1) find the maximum qubit index to determine circuit size
    max_idx = -1
    for gdict in raw.values():
        for i in gdict.keys():
            if i > max_idx:
                max_idx = i

    # 2) create empty CircuitXZ
    new_state = new_clean_circuit_state(max_idx + 1)

    # 3) fill in the qubits from raw dict
    for gdict in raw.values():
        for i, qubit in gdict.items():
            new_state.qubits[i].x = qubit.x
            new_state.qubits[i].z = qubit.z

    return new_state

# ---------------------------
# Clifford update rules
# ---------------------------

def apply_h(state: CircuitXZ, q: int) -> None:
    """Hadamard on qubit q: (x,z) <- (z,x)."""
    state.qubits[q].x, state.qubits[q].z = state.qubits[q].z, state.qubits[q].x

def apply_s(state: CircuitXZ, q: int) -> None:
    """Phase S on qubit q: (x,z) <- (x, x xor z)."""
    x, z = state.qubits[q].x, state.qubits[q].z
    state.qubits[q].z = bxor(x, z)

def apply_sdg(state: CircuitXZ, q: int) -> None:
    """S† on qubit q: (x,z) <- (x, z xor x).  (inverse of S)"""
    x, z = state.qubits[q].x, state.qubits[q].z
    state.qubits[q].z = bxor(z, x)

def apply_cnot(state: CircuitXZ, ctrl: int, targ: int) -> None:
    """
    CNOT(c->t):
      x_c' = x_c
      z_c' = z_c xor z_t
      x_t' = x_t xor x_c
      z_t' = z_t
    """
    xc, zc = state.qubits[ctrl].x, state.qubits[ctrl].z
    xt, zt = state.qubits[targ].x, state.qubits[targ].z
    state.qubits[ctrl].x = xc
    state.qubits[ctrl].z = bxor(zc, zt)
    state.qubits[targ].x = bxor(xt, xc)
    state.qubits[targ].z = zt


def apply_cz(state: CircuitXZ, ctrl: int, targ: int) -> None:
    """
    CZ(c->t):
      x_c' = x_c
      z_c' = z_c  xor x_t
      x_t' = x_t 
      z_t' = z_t xor x_c 
    """
    xc, zc = state.qubits[ctrl].x, state.qubits[ctrl].z
    xt, zt = state.qubits[targ].x, state.qubits[targ].z
    state.qubits[ctrl].x = xc
    state.qubits[ctrl].z = bxor(xt ,  zc)
    state.qubits[targ].x = xt
    state.qubits[targ].z =  bxor(xc, zt)


def apply_cy(state: CircuitXZ, ctrl: int, targ: int) -> None:
    """
    CY(c->t):
      x_c' = x_c
      z_c' = z_c  xor x_t xor z_t
      x_t' = x_t xor x_c
      z_t' = z_t xor x_c 
    """
    xc, zc = state.qubits[ctrl].x, state.qubits[ctrl].z
    xt, zt = state.qubits[targ].x, state.qubits[targ].z
    state.qubits[ctrl].x = xc
    state.qubits[ctrl].z = bxor(bxor(xt ,  zc), zt)
    state.qubits[targ].x = bxor(xt, xc)
    state.qubits[targ].z =  bxor(xc, zt)


def apply_notnot(state: CircuitXZ, ctrl: int, targ: int) -> None:
    """
    NOTNOT(c->t):
      x_c' = x_c xor z_t
      z_c' = z_c 
      x_t' = x_t  xor  z_c
      z_t' = z_t
    """
    xc, zc = state.qubits[ctrl].x, state.qubits[ctrl].z
    xt, zt = state.qubits[targ].x, state.qubits[targ].z
    state.qubits[ctrl].x = bxor(xc, zt)
    state.qubits[ctrl].z = zc
    state.qubits[targ].x = bxor(xt, zc)
    state.qubits[targ].z = zt


# ---------------------------
# Fault injection on FLAG
# ---------------------------

def inject_flag_error(state: CircuitXZ, flag_idx: int, kind: str) -> None:
    """
    Insert a Pauli error on the flag qubit at the *current time*.
    kind ∈ {'I','X','Z','Y'}.
    """
    k = kind.upper()
    if k == 'I':
        return
    if k in ('X', 'Y'):
        state.qubits[flag_idx].x = bxor(state.qubits[flag_idx].x, BoolVal(True))
    if k in ('Z', 'Y'):
        state.qubits[flag_idx].z = bxor(state.qubits[flag_idx].z, BoolVal(True))

def inject_flag_symbolic_one_axis(state, fidx: int, axis="z", prefix="ferr"):
    """
    Add a symbolic error variable on a flag qubit, restricted to X *or* Z axis.

    axis: "x" or "z"
    """
    var = Bool(f"{prefix}{fidx}_{axis}")

    if axis == "x":
        # Only X part of error
        state.qubits[fidx].x = Xor(state.qubits[fidx].x, var)
    elif axis == "z":
        # Only Z part of error
        state.qubits[fidx].z = Xor(state.qubits[fidx].z, var)
    else:
        raise ValueError("axis must be 'x' or 'z'")

    return var

def inject_symbolic_one_axis_many(state, idxs, axis="z", prefix="ferr"):
    """
    Inject one symbolic Boolean on the chosen axis for EACH qubit in `idxs`.
    axis: 'x' or 'z'
    Returns: list of created Bool vars (same order as idxs).
    """
    if axis not in ("x", "z"):
        raise ValueError("axis must be 'x' or 'z'")
    vars_created = []
    for i in idxs:
        v = Bool(f"{prefix}{i}_{axis}")
        if axis == "x":
            state.qubits[i].x = Xor(state.qubits[i].x, v)
        else:
            state.qubits[i].z = Xor(state.qubits[i].z, v)
        vars_created.append(v)
    return vars_created

def inject_on_ancillas(state, anc_idxs, axis="z", prefix="ancFault"):
    """Inject symbolic faults on ancillas only (one axis)."""
    return inject_symbolic_one_axis_many(state, anc_idxs, axis=axis, prefix=prefix)

def inject_on_flags(state, flag_idxs, axis="z", prefix="flagFault"):
    """Inject symbolic faults on flags only (one axis)."""
    return inject_symbolic_one_axis_many(state, flag_idxs, axis=axis, prefix=prefix)

def _inject_1q_fault_after(state, q, fault_kind=None, prefix="f"):
    """
    One-qubit Pauli on wire q after a 1q gate (or on ONE wire of a 2q gate):
      fault_kind: None -> symbolic; 'I'|'X'|'Z'|'Y' -> concrete.
    Returns dict {'fx','fz','act'}.
    """
    if fault_kind is None:
        fx = Bool(f"{prefix}_x"); fz = Bool(f"{prefix}_z")
    else:
        k = fault_kind.upper()
        fx = BoolVal(k in ("X","Y")); fz = BoolVal(k in ("Z","Y"))
    state.qubits[q].x = Xor(state.qubits[q].x, fx)
    state.qubits[q].z = Xor(state.qubits[q].z, fz)
    return { "vars":{ "fx": fx, "fz": fz} , "act": Or(fx, fz)}

def _inject_2q_fault_after(state, q0: int, q1: int, fault_kind=None, prefix="f"):
    """
    Gate-agnostic 2-qubit Pauli injection on wires (q0, q1) *after* a 2q gate.
    """
    if fault_kind is None:
        fx0 = Bool(f"{prefix}_x0"); fz0 = Bool(f"{prefix}_z0")
        fx1 = Bool(f"{prefix}_x1"); fz1 = Bool(f"{prefix}_z1")
    else:
        k0, k1 = fault_kind
        k0 = k0.upper(); k1 = k1.upper()
        fx0 = BoolVal(k0 in ("X","Y")); fz0 = BoolVal(k0 in ("Z","Y"))
        fx1 = BoolVal(k1 in ("X","Y")); fz1 = BoolVal(k1 in ("Z","Y"))

    # Apply Pauli update
    state.qubits[q0].x = Xor(state.qubits[q0].x, fx0)
    state.qubits[q0].z = Xor(state.qubits[q0].z, fz0)
    state.qubits[q1].x = Xor(state.qubits[q1].x, fx1)
    state.qubits[q1].z = Xor(state.qubits[q1].z, fz1)

    # Activity bits
    act0 = Or(fx0, fz0)
    act1 = Or(fx1, fz1)
    act  = Or(act0, act1)

    # NEW return structure (your requirement)
    info = {
        "vars": {
            "fx0": fx0, "fz0": fz0,
            "fx1": fx1, "fz1": fz1,
        },
        "act0": act0,
        "act1": act1,
        "act": act,
    }

    return info


# ---------------------------
# QASM → Pauli-flow
# ---------------------------

def _qiskit_qubit_index(qc: QuantumCircuit, qobj) -> int:
    """
    Robustly get integer index from a Qiskit Qubit object in 1.x/2.x.
    """
    # find_bit returns a BitLocations with .index
    return qc.find_bit(qobj).index

def apply_qasm_gate_into_state(state: CircuitXZ, name: str, qidxs: List[int]) -> None:
    """Apply supported (Clifford) gates to Pauli-flow state."""
    if name == 'h':
        apply_h(state, qidxs[0])
    elif name == 's':
        apply_s(state, qidxs[0])
    elif name in ('sdg', 'sxdg'):  # sdg is the usual name; include alias just in case
        apply_sdg(state, qidxs[0])
    elif name in ('cx', 'cnot'):
        apply_cnot(state, qidxs[0], qidxs[1])

    elif name in ('notnot'):  # notnot is a common alias for ccx
        apply_notnot(state, qidxs[0], qidxs[1])

    elif name in ('cz'):
        apply_cz(state, qidxs[0], qidxs[1])
    
    elif name in ('cy'):
        apply_cy(state, qidxs[0], qidxs[1])

    elif name in ('id', 'barrier', 'reset', 'measure'):
        # ignored here; measurement is read via final Z/X bits directly
        pass
    else:
        raise NotImplementedError(f"Unsupported gate in Pauli-flow: {name}")



# ---------------------------
# build states from QASM
# ---------------------------

from circuit_op import *

def new_clean_circuit_state(n_qubits: int) -> CircuitXZ:
    """Start with no errors anywhere: X=0, Z=0 per qubit."""
    return CircuitXZ([QubitXZ(bfalse(), bfalse()) for _ in range(n_qubits)])


def new_variable_circuit_state(qc: QuantumCircuit) -> CircuitXZ:
    """
    Initialize each qubit with named Bool variables based on qreg name + local index.
    Produces variables like: q0_x, q0_z, ancX1_x, ancX1_z, flagZ2_x, ...
    """
    regmap = _regmap_indices(qc)
    qubits: List[QubitXZ] = []
    for i in range(qc.num_qubits):
        regname, j = regmap[i]
        prefix = f"{regname}{j}"
        qubits.append(QubitXZ(x=Bool(f"{prefix}_x"), z=Bool(f"{prefix}_z")))
    return CircuitXZ(qubits)

def build_state_from_qasm(qasm_path: str) -> Tuple[CircuitXZ, QuantumCircuit]:
    """Load QASM and walk the gates to produce final Pauli-flow state (no faults)."""
    qc = load_qasm(qasm_path)
    st = new_clean_circuit_state(qc.num_qubits)
    for instr, qargs, _ in qc.data:
        name = instr.name
        qidxs = [_qiskit_qubit_index(qc, q) for q in qargs]
        apply_qasm_gate_into_state(st, name, qidxs)
    return st, qc

def build_variable_state_from_qasm(qasm_path: str) -> Tuple[CircuitXZ, QuantumCircuit, Dict[str, object]]:
    """
    Load QASM, build variable-initialized Pauli-flow state, propagate gates,
    and return (state, qc, varenv) where varenv maps variable names to z3 Bools.
    """
    qc = load_qasm(qasm_path)

    # 1) variable-initialized state
    state = new_variable_circuit_state(qc)

    # 2) also return a varenv for easy substitutions/evaluation
    regmap = _regmap_indices(qc)
    varenv: Dict[str, object] = {}
    for i in range(qc.num_qubits):
        regname, j = regmap[i]
        prefix = f"{regname}{j}"
        varenv[f"{prefix}_x"] = state.qubits[i].x
        varenv[f"{prefix}_z"] = state.qubits[i].z

    # 3) walk the circuit (Clifford updates)
    for instr, qargs, _ in qc.data:
        name = instr.name
        qidxs = [_qiskit_qubit_index(qc, q) for q in qargs]
        apply_qasm_gate_into_state(state, name, qidxs)

    return state, qc, varenv

def build_state_with_fault_after_gate(qasm_path: str, gate_index: int, fault_mode="either", fault_kind=None):
    """
    Run circuit ideally; inject a fault right AFTER gate #gate_index only.
    fault_mode: '1q' | '2q' | 'either'  (for CNOTs)
    fault_kind:
      - None                -> symbolic
      - 'I'|'X'|'Z'|'Y'     -> for 1q gates, or for CNOT in mode='1q' (applied to one wire)
      - (kc,kt)             -> for CNOT in mode='2q'/'either' (concrete per-wire)
    Returns: (state, qc, site_info, groups)
    """
    qc = load_qasm(qasm_path)
    state = new_clean_circuit_state(qc.num_qubits)
    groups = detect_qubit_groups(qc)
    site_info = None

    for i, (instr, qargs, _) in enumerate(qc.data):
        name = instr.name
        qidxs = [_qiskit_qubit_index(qc, q) for q in qargs]

        if name in ("h","s","sdg"):
            apply_qasm_gate_into_state(state, name, qidxs)
            if i == gate_index:
                info = _inject_1q_fault_after(
                    state, qidxs[0],
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"f_site{i}"
                )
                site_info = {
                    "gate_index": i,
                    "gate_name": name,
                    "qubits": (qidxs[0],),
                    "vars": info["vars"],
                    "act": info["act"],
                    "fault_mode": "1q",
                }

        elif name in ("cx","cnot", "notnot", "cz", "cy"):
            c, t = qidxs
            if (name == "cx"  or name == "cnot"):apply_cnot(state, c, t)
            elif (name == "notnot"): apply_notnot(state, c, t)
            elif (name == "cz"): apply_cz(state, c, t)
            elif (name == "cy"): apply_cy(state, c, t)
            if i == gate_index:
                info = _inject_2q_fault_after(
                    state, c, t, 
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"f_gate{i}"
                )
                site_info = {
                    "gate_index": i,
                    "gate_name": "cx",
                    "qubits": (c, t),
                    "vars": info["vars"],
                    "act0": info["act0"],
                    "act1": info["act1"],
                    "act": info["act"],
                    "fault_mode": fault_mode,
                }

        elif name in ("barrier","id","reset","measure"):
            if i == gate_index:
                site_info = {
                    "gate_index": i, "gate_name": name,
                    "qubits": tuple(qidxs),
                    "vars": {}, "act": BoolVal(False), "fault_mode": "none"
                }
        else:
            raise NotImplementedError(f"Unsupported gate: {name}")

    if site_info is None:
        raise IndexError(f"gate_index {gate_index} out of range (len={len(qc.data)})")

    return state, qc, site_info, groups


def build_state_with_faults_after_gates(qasm_path: str, gate_indices: list, fault_mode="either", fault_kind=None):
    """
    Run circuit ideally; inject faults right AFTER all gates in `gate_indices`.
    
    fault_mode: '1q' | '2q' | 'either'  (for 2-qubit gates)
    fault_kind:
      - None                -> symbolic
      - 'I'|'X'|'Z'|'Y'     -> for 1q gates, or for CNOT in mode='1q' (applied to one wire)
      - (kc,kt)             -> for 2q gates (concrete per-wire)
    
    Returns:
      state, qc, sites_info, groups
      where `sites_info` is a list of site_info dicts (one per injected fault)
    """
    qc = load_qasm(qasm_path)
    state = new_clean_circuit_state(qc.num_qubits)
    groups = detect_qubit_groups(qc)
    # groups debug print suppressed
    sites_info = []

    gate_indices = set(gate_indices)  # so we can check membership quickly

    for i, (instr, qargs, _) in enumerate(qc.data):
        name = instr.name
        qidxs = [_qiskit_qubit_index(qc, q) for q in qargs]
        # per-gate debug print suppressed
        if name in ("h","s","sdg"):
            apply_qasm_gate_into_state(state, name, qidxs)
            if i in gate_indices:
                info = _inject_1q_fault_after(
                    state, qidxs[0],
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"f_site{i}"
                )
                sites_info.append({
                    "gate_index": i,
                    "gate_name": name,
                    "qubits": (qidxs[0],),
                    "vars": info["vars"],
                    "act": info["act"],
                    "fault_mode": "1q",
                })

        elif name in ("cx","cnot","notnot","cz","cy"):
            c, t = qidxs
            if name in ("cx","cnot"):
                apply_cnot(state, c, t)
            elif name == "notnot":
                apply_notnot(state, c, t)
            elif name == "cz":
                apply_cz(state, c, t)
            elif name == "cy":
                apply_cy(state, c, t)
            if i in gate_indices:
                info = _inject_2q_fault_after(
                    state, c, t,
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"faulty_gate{i}"
                )
                sites_info.append({
                    "gate_index": i,
                    "gate_name": name,
                    "qubits": (c, t),
                    "vars": info["vars"],
                    "act0": info["act0"],
                    "act1": info["act1"],
                    "act": info["act"],
                    "fault_mode": fault_mode,
                })

        elif name in ("barrier","id","reset","measure"):
            
            if i in gate_indices:
                sites_info.append({
                    "gate_index": i, "gate_name": name,
                    "qubits": tuple(qidxs),
                    "vars": {}, "act": BoolVal(False), "fault_mode": "none"
                })
        
        else:
            raise NotImplementedError(f"Unsupported gate: {name}")

        #_reset_qubit_x(state, groups.get("ancX", []))

        #_reset_qubit_z(state, groups.get("ancZ", []))

        #_reset_qubit_x(state, groups.get("flagX", []))

        #_reset_qubit_z(state, groups.get("flagZ", []))
      
    if not sites_info:
        raise IndexError(f"gate_indices {gate_indices} produced no injections (len={len(qc.data)})")

    return state, qc, sites_info, groups


def symbolic_propagate_with_resets(
    qc: QuantumCircuit,
    init_state: CircuitXZ,
    *,
    track_steps: bool = False,
    reset_groups=("ancX", "ancZ", "flagX", "flagZ"),
    reset_at_start: bool = True,
    reset_on_barrier: bool = True,
    reset_on_measure: bool = False,
):
    """
    Propagate a CircuitXZ `init_state` through QASM at `qasm_path` with Pauli-flow
    updates, and optionally reset ancilla/flag groups to clean (X=Z=False):

      - `reset_groups`: tuple of group names to reset (default all anc/flag).
      - `reset_at_start`: reset selected groups before the first gate.
      - `reset_on_barrier`: reset selected groups whenever a 'barrier' is seen.
      - `reset_on_measure`: reset a qubit if it is measured *and* belongs to selected groups.

    Returns:
        final_state
        or (final_state, snapshots) if track_steps=True, where snapshots is
        a list of (gate_index, name, qidxs, deep_copied_state).
    """
    #qc = _load_qasm(qasm_path)

    # --- consistency check ---
    n_circ = qc.num_qubits
    n_state = len(init_state.qubits)
    

    # copy so we don't mutate caller's state
    state = deepcopy(init_state)

    # Build groups from register names
    groups = detect_qubit_groups(qc)   # expects keys: 'data','ancX','ancZ','flagX','flagZ'
    group_idxs = {g: groups.get(g, []) for g in ("data","ancX","ancZ","flagX","flagZ")}

    # groups debug print suppressed

    # Which indices are selected for bulk resets?
    selected_reset_idxs = []
    for g in reset_groups:
        selected_reset_idxs.extend(group_idxs.get(g, []))
    selected_reset_idxs = sorted(set(selected_reset_idxs))

    # Qubit → global index (Qiskit 2.x)
    def _qidx(qbit):
        return qc.find_bit(qbit).index

    #print("Selected qubits for resets:", selected_reset_idxs)
    # optional initial reset
    if reset_at_start and selected_reset_idxs:
        #print("Performing initial reset on selected qubits.")
        _reset_qubits(state, selected_reset_idxs)

    snapshots = []

    for i, (instr, qargs, cargs) in enumerate(qc.data):
        name = instr.name.lower()
        qidxs = [_qidx(q) for q in qargs]

        # per-gate debug print suppressed

        if name in ('id', 'reset'):
            continue

        if name == 'barrier':
            
            if track_steps:
                snapshots.append((i, name, tuple(qidxs), deepcopy(state)))
            continue

        if name == 'measure':
            if reset_on_measure:
                to_reset = [q for q in qidxs if q in selected_reset_idxs]
                if to_reset:
                    _reset_qubits(state, to_reset)
            if track_steps:
                snapshots.append((i, name, tuple(qidxs), deepcopy(state)))
            continue

        # Apply the actual Clifford update
        apply_qasm_gate_into_state(state, name, qidxs)

        if track_steps:
            snapshots.append((i, name, tuple(qidxs), deepcopy(state)))

        
        #_reset_qubit_x(state, groups.get("ancX", []))

        #_reset_qubit_z(state, groups.get("ancZ", []))

        #_reset_qubit_x(state, groups.get("flagX", []))

        #_reset_qubit_z(state, groups.get("flagZ", []))

    return (state, snapshots) if track_steps else state


###this reads state as a . dict 
def symbolic_execution_of_state(qasm_path: str, 
                                input_state: CircuitXZ,
                                round: int, 
                                *,
                                fault_gate:list = None,
                                track_steps: bool = False,
                                reset_groups=("ancX", "ancZ", "flagX", "flagZ"),
                                fault_inject : bool = True,
                                fault_inject_on_anc_and_flag : bool = False,
                                fault_mode = "either",
                                fault_kind = None,
                                initial_data_error: bool = False,
                                ):
    
    qc = load_qasm(qasm_path)

    state = new_clean_circuit_state(qc.num_qubits)
    groups = detect_qubit_groups(qc)
    group_idxs = {g: groups.get(g, []) for g in ("data","ancX","ancZ","flagX","flagZ")}

    if initial_data_error:
        for i in groups["data"]:
            state.qubits[i].x = Bool(f"r_{round}_dataInit_{i}_x")
            state.qubits[i].z = Bool(f"r_{round}_dataInit_{i}_z")
    else:
        for i in groups["data"]:
            state.qubits[i].x = input_state.qubits[i].x
            state.qubits[i].z = input_state.qubits[i].z
    ###get all the  gate indices
    if fault_gate is None:
        fault_gate_indices = []
    else:
        fault_gate_indices = get_gate_only_indices(qc)

    


    #reset qubits in selected groups 
    selected_reset_idxs = []
    for g in reset_groups:
        selected_reset_idxs.extend(group_idxs.get(g, []))
    selected_reset_idxs = sorted(set(selected_reset_idxs))

    #print("groups detected:", group_idxs)
    # optional initial reset
    if selected_reset_idxs:
        #print("Performing initial reset on selected qubits.")
        _reset_qubits(state, selected_reset_idxs)

    # Qubit → global index (Qiskit 2.x)
    def _qidx(qbit):
        return qc.find_bit(qbit).index
    
    snapshots = []
    sites_info = []

    if initial_data_error:
        for i in groups["data"]:
            vx = state.qubits[i].x
            vz = state.qubits[i].z
            sites_info.append({
                "gate_index": -1,
                "gate_name": f"r_{round}_dataInit",
                "qubits": (i,),
                "vars": {"fx": vx, "fz": vz},
                "act": Or(vx, vz),
                "fault_mode": "init",
            })

    ###run gates and inject faults if needed
    for i, (instr, qargs, _) in enumerate(qc.data):
        name = instr.name
        qidxs = [_qiskit_qubit_index(qc, q) for q in qargs]
        if track_steps:
            print(
                f"Gate index {i}: {name} on {_format_gate_qubits(qc, qidxs)}"
            )
        if name in ("h","s","sdg"):
            apply_qasm_gate_into_state(state, name, qidxs)
            if i in fault_gate_indices and fault_inject:
                info = _inject_1q_fault_after(
                    state, qidxs[0],
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"r_{round}_f_site{i}"
                )
                sites_info.append({
                    "gate_index": i,
                    "gate_name": name,
                    "qubits": (qidxs[0],),
                    "vars": info["vars"],
                    "act": info["act"],
                    "fault_mode": "1q",
                })

        elif name in ("cx","cnot","notnot","cz","cy"):
            c, t = qidxs
            if name in ("cx","cnot"):
                apply_cnot(state, c, t)
            elif name == "notnot":
                apply_notnot(state, c, t)
            elif name == "cz":
                 apply_cz(state, c, t)
            elif name == "cy":
                apply_cy(state, c, t)

               
            if i in  fault_gate_indices and fault_inject:
                info = _inject_2q_fault_after(
                    state, c, t,
                    fault_kind=None if fault_kind is None else fault_kind,
                    prefix=f"r_{round}_faulty_gate{i}"
                )
                sites_info.append({
                    "gate_index": i,
                    "gate_name": name,
                    "qubits": (c, t),
                    "vars": info["vars"],
                    "act0": info["act0"],
                    "act1": info["act1"],
                    "act": info["act"],
                    "fault_mode": fault_mode,
                })

        elif name in ("barrier","id","reset","measure"):
            continue
            if i in fault_gate_indices:
                sites_info.append({
                    "gate_index": i, "gate_name": name,
                    "qubits": tuple(qidxs),
                    "vars": {}, "act": BoolVal(False), "fault_mode": "none"
                })
        
        
        else:
            raise NotImplementedError(f"Unsupported gate: {name}")
        if track_steps:
            snapshots.append((i, name, tuple(qidxs), deepcopy(state)))
    
    
    if fault_inject_on_anc_and_flag :
      
        # groups already computed above
        ancX_idxs = group_idxs.get("ancX", [])
        ancZ_idxs = group_idxs.get("ancZ", [])
        flagX_idxs = group_idxs.get("flagX", [])
        flagZ_idxs = group_idxs.get("flagZ", [])

        # synthetic “gate indices” after the last real gate
        base_idx = len(qc.data)

        # 1) flagZ (measured in Z) – inject Z faults
        flagZ_vars = inject_on_flags(state, flagZ_idxs, axis="z", prefix=f"r_{round}_flagZFault_")
        for q, v in zip(flagZ_idxs, flagZ_vars):
            sites_info.append({
                "gate_index": base_idx,
                "gate_name": f"r_{round}_post_flagZ_inject",
                "qubits": (q,),
                "vars": {"v": v},   # <-- only the variable
                "act": v,           # <-- act is exactly that var
                "fault_mode": "1q",
            })

        # 2) flagX (measured in X) – inject X faults
        flagX_vars = inject_on_flags(state, flagX_idxs, axis="x", prefix=f"r_{round}_flagXFault_")
        for q, v in zip(flagX_idxs, flagX_vars):
            sites_info.append({
                "gate_index": base_idx + 1,
                "gate_name": f"r_{round}_post_flagX_inject",
                "qubits": (q,),
                "vars": {"v": v},
                "act": v,
                "fault_mode": "1q",
            })

        # 3) ancZ (Z basis) – inject X faults
        ancZ_vars = inject_on_ancillas(state, ancZ_idxs, axis="x", prefix=f"r_{round}_ancZFault_")
        for q, v in zip(ancZ_idxs, ancZ_vars):
            sites_info.append({
                "gate_index": base_idx + 2,
                "gate_name": f"r_{round}_post_ancZ_inject",
                "qubits": (q,),
                "vars": {"v": v},
                "act": v,
                "fault_mode": "1q",
            })

        # 4) ancX (X basis) – inject Z faults
        ancX_vars = inject_on_ancillas(state, ancX_idxs, axis="z", prefix=f"r_{round}_ancXFault_")
        for q, v in zip(ancX_idxs, ancX_vars):
            sites_info.append({
                "gate_index": base_idx + 3,
                "gate_name": f"r_{round}_post_ancX_inject",
                "qubits": (q,),
                "vars": {"v": v},
                "act": v,
                "fault_mode": "1q",
            })
        

    return  (state,sites_info, snapshots) if track_steps else (state ,sites_info)
     

####-----------------------
# proof for uniqness
####
def last_ancilla_formulas(path, config, detect =False):
    """
    Returns:
      gens: list[(Sx,Sz)]  (symplectic generators used for syndrome check)
      syn_measured: list[z3.BoolRef]  (measured syndrome bits at the last ancilla step)

    Syndrome convention (matches your proof_path):
      syn_measured = [a.z for a in ancX] + [a.x for a in ancZ]
    """
    def flatten(xs):
        return [q for g in xs for q in (g if isinstance(g, list) else [g])]

    

    def parse_pauli_instruction(instr: str):
        # 'XIXZZ_s IYXXY_f' -> [('XIXZZ','s'), ('IYXXY','f')]
        out = []
        for tok in str(instr).split():
            if "_" not in tok:
                continue
            p, tag = tok.split("_", 1)
            out.append((p, tag))
        return out

    for step in reversed(path):
        st = step.get("state", {})
        ancX = flatten(st.get("ancX", []))
        ancZ = flatten(st.get("ancZ", []))
        if not ancX and not ancZ:
            continue

        # measured syndrome bits (z3 formulas)
        syn_measured = [a.z for a in ancX] + [a.x for a in ancZ]

        instr = step.get("instruction", None)

        if instr in ("flag_syndrome", "raw_syndrome") or not(detect):
            gens = load_symplectic_txt(config["stab_txt_path"])
            return gens, syn_measured

        gens = []
        for pstr, tag in parse_pauli_instruction(instr):
            
            gens.append(pauli_string_to_symplectic(pstr))

        if not gens:
            raise ValueError(f"Last ancilla step has unsupported instruction: {instr}")

        return gens, syn_measured

    raise ValueError("No ancilla syndrome found in path")
def stabilizer_syndrome_from_data(E_x, E_z, gens):
    """
    E_x, E_z: list[z3 BoolRef] for data qubits
    gens: list[(Sx, Sz)] where Sx,Sz are lists[int 0/1] same length as E_x
    returns: list[z3 BoolRef] syndrome bits, one per generator
    """
    syn = []
    n = len(E_x)
    for (Sx, Sz) in gens:
        bit = BoolVal(False)
        for i in range(n):
            term = BoolVal(False)
            if Sx[i]:
                term = Xor(term, E_z[i])
            if Sz[i]:
                term = Xor(term, E_x[i])
            bit = Xor(bit, term)
        syn.append(simplify(bit))
    return syn

def uniqness_proof(vars :list,at_most_t_faults: list,condition :list ,  gen_syn_z3 :list, data_qubits : list,stab_txt_path: str, log_txt_path:str):

    """
    Prove that for a given set of faults, there is a unique syndrome pattern.
    Args:
      vars: list of z3 Bool variables representing faults
      faults: list of fault expressions (z3 Bool formulas) for data qubits
      gen_syn_z3: list of stabilizer generator expressions (z3 Bool formulas)
      data_qubit: list of data qubit indices
      stab: list of stabilizers from the code (list of (Sx, Sz) tuples)

      """
    

    E_x = [ data_qubit.x for data_qubit in data_qubits]
    E_z = [ data_qubit.z for data_qubit in data_qubits]

    


    #condition_not_in_stab, gsel_1 = error_not_in_stabilizer(E_x, E_z, stab_txt_path, prefix = "gsel_1")
    
    
    

    ren_1 = make_renamer_from_symbols(vars, "_p1")
    ren_2 = make_renamer_from_symbols(vars, "_p2")

    #condition_not_in_stab_1 = primed_copy(condition_not_in_stab, ren_1)
    #condition_not_in_stab_2 = primed_copy(condition_not_in_stab, ren_2)

   
    condition_1 = primed_copy(condition, ren_1)
    condition_2 = primed_copy(condition, ren_2)
    E_x_1 = primed_copy(E_x, ren_1)
    E_z_1 = primed_copy(E_z, ren_1)
    E_x_2 = primed_copy(E_x, ren_2)
    E_z_2 = primed_copy(E_z, ren_2)
    at_most_t_faults_1 = primed_copy(at_most_t_faults, ren_1)
    at_most_t_faults_2 = primed_copy(at_most_t_faults, ren_2)
    gen_syn_z3_1 = primed_copy(gen_syn_z3, ren_1)
    gen_syn_z3_2 = primed_copy(gen_syn_z3, ren_2)

    #stab_eq , gsel = exists_stab_equiv(E_x_1, E_z_1, E_x_2, E_z_2, stab_txt_path)

    condition_E_1_neq_E_2 = []
    for (ex1, ez1, ex2, ez2) in zip(E_x_1, E_z_1, E_x_2, E_z_2):
        neq = Or(ex1 != ex2, ez1 != ez2)
        condition_E_1_neq_E_2.append(neq)
    condition_E_1_neq_E_2_formula = Or( *condition_E_1_neq_E_2 )
    
    E_1_add_E_2_X = [Xor(ex1, ex2) for (ex1, ex2) in zip(E_x_1, E_x_2)] 
    E_1_add_E_2_Z = [Xor(ez1, ez2) for (ez1, ez2) in zip(E_z_1, E_z_2)]
    
    condition_pauli_not_in_stab= pauli_not_in_stabilizer(E_1_add_E_2_X,E_1_add_E_2_Z, stab_txt_path, log_txt_path)
    condition_E_1_not_in_stab =pauli_not_in_stabilizer(E_x_1,E_z_1, stab_txt_path, log_txt_path)
    condition_E_2_not_in_stab =pauli_not_in_stabilizer(E_x_2,E_z_2, stab_txt_path, log_txt_path)
   
   
    same_syn =  And( *[x == y for x, y in zip(gen_syn_z3_1, gen_syn_z3_2)] )
    s = Solver()
    s.set("timeout", 3600000) 
    #s.add(And(*condition_not_in_stab_1))
    #s.add(And(*condition_not_in_stab_2))
    s.add(same_syn)
    #s.add(Not(Exists(gsel, stab_eq)))
    s.add(And( *condition_1))
    s.add(And( *condition_2))
    s.add(at_most_t_faults_1)
    s.add(at_most_t_faults_2)
    s.add(condition_E_1_neq_E_2_formula)
    s.add(condition_pauli_not_in_stab)
    s.add(condition_E_1_not_in_stab)
    s.add(condition_E_2_not_in_stab)
    #s.add(condition_not_in_stab)
    print("Result:")

    if s.check() == unknown:
        print("Solver timed out")
        print(s.reason_unknown())
        return False
    if s.check() == unsat :
       
        print("Success: every error maps to different generalised syndrome")

        return True 
    if s.check() == sat:
        print("Failure: there exists two different errors that map to the same generalised syndrome")

        m = s.model()

        p1_dict = {}
        p2_dict = {}
        other_dict = {}

        for d in m.decls():
            val = m[d]
            if not is_true(val):
                continue

            name = d.name()

            if name.endswith("_p1"):
                base = name[:-3]   # strip "_p1"
                p1_dict[base] = True

            elif name.endswith("_p2"):
                base = name[:-3]   # strip "_p2"
                p2_dict[base] = True

            else:
                other_dict[name] = True

        print("\n--- p1 dict ---")
        print(p1_dict)

        print("\n--- p2 dict ---")
        print(p2_dict)

        if other_dict:
            print("\n--- other dict ---")
            print(other_dict)

        return False
    
import subprocess
import re
from z3 import *
import os
import shutil

def uniqueness_build_goal(vars, at_most_t_faults, condition, gen_syn_z3,
                          data_qubits, stab_txt_path, log_txt_path,
                          witness_mode: str = "type1"):
    # same construction as your uniqness_proof, but into a Goal
    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]

    ren_1 = make_renamer_from_symbols(vars, "_p1")
    ren_2 = make_renamer_from_symbols(vars, "_p2")

    condition_1 = primed_copy(condition, ren_1)
    condition_2 = primed_copy(condition, ren_2)

    E_x_1 = primed_copy(E_x, ren_1)
    E_z_1 = primed_copy(E_z, ren_1)
    E_x_2 = primed_copy(E_x, ren_2)
    E_z_2 = primed_copy(E_z, ren_2)

    at_most_t_faults_1 = primed_copy(at_most_t_faults, ren_1)
    at_most_t_faults_2 = primed_copy(at_most_t_faults, ren_2)

    gen_syn_z3_1 = primed_copy(gen_syn_z3, ren_1)
    gen_syn_z3_2 = primed_copy(gen_syn_z3, ren_2)
    '''
    print("len gen_syn_z3_1:", len(gen_syn_z3_1))
    print("eval gen_syn_z3_1:", eval_with_values(gen_syn_z3_1, {'r_0_faulty_gate42_z1_p1': True, 'r_0_faulty_gate54_z0_p1': True, 'r_0_faulty_gate42_z0_p1': True}))
    print("eval gen_syn_z3_2:", eval_with_values(gen_syn_z3_2, {'r_3_faulty_gate11_z1_p2': True, 'r_3_faulty_gate11_z0_p2': True, 'r_3_faulty_gate31_z1_p2': True}))
    '''
    # E1 != E2
    neq_terms = [Or(ex1 != ex2, ez1 != ez2)
                 for ex1, ez1, ex2, ez2 in zip(E_x_1, E_z_1, E_x_2, E_z_2)]
    condition_E_1_neq_E_2_formula = Or(*neq_terms)

    
    # XOR diffs (will become CNF later)
    E_1_add_E_2_X = [Xor(ex1, ex2) for ex1, ex2 in zip(E_x_1, E_x_2)]
    E_1_add_E_2_Z = [Xor(ez1, ez2) for ez1, ez2 in zip(E_z_1, E_z_2)]

    condition_pauli_not_in_stab = pauli_not_in_stabilizer(
        E_1_add_E_2_X, E_1_add_E_2_Z, stab_txt_path, log_txt_path
    )
    condition_E_1_not_in_stab = pauli_not_in_stabilizer(
        E_x_1, E_z_1, stab_txt_path, log_txt_path
    )
    condition_E_2_not_in_stab = pauli_not_in_stabilizer(
        E_x_2, E_z_2, stab_txt_path, log_txt_path
    )

    same_syn = And(*[x == y for x, y in zip(gen_syn_z3_1, gen_syn_z3_2)])

    # Predicted syndromes from stabilizers for each of the two candidate errors.
    stab_gens = load_symplectic_txt(stab_txt_path)
    pred_syn_1 = stabilizer_syndrome_from_data(E_x_1, E_z_1, stab_gens)
    pred_syn_2 = stabilizer_syndrome_from_data(E_x_2, E_z_2, stab_gens)
    pred_syn_diff = Or(*[a != b for a, b in zip(pred_syn_1, pred_syn_2)]) if pred_syn_1 else BoolVal(False)

    if witness_mode == "type1":
        # Type 1: require E1*E2 to be outside stabilizer.
        # pred_syn_diff implies this already, so we keep the direct condition.
        witness_condition = condition_pauli_not_in_stab
    elif witness_mode == "type2":
        # Type 2: same gen_syn and E1*E2 logical operator.
        # Syndrome consistency is enforced by 'condition' from caller.
        witness_condition = condition_pauli_not_in_stab
    else:
        raise ValueError(f"Unknown witness_mode: {witness_mode}")

    g = Goal()
    g.add(same_syn)
    g.add(And(*condition_1))
    g.add(And(*condition_2))
    g.add(at_most_t_faults_1)
    g.add(at_most_t_faults_2)
    g.add(condition_E_1_neq_E_2_formula)
    g.add(witness_condition)
    g.add(condition_E_1_not_in_stab)
    g.add(condition_E_2_not_in_stab)
    return g


def control_flow_build_goal(
    vars,
    at_most_t_faults,
    condition,
    data_qubits,
    gen_syn_z3,
    stab_txt_path: str,
):
    """
    Control-flow type-1 goal: SAT counterexample has same gen_syn but pred_syn(E1) != pred_syn(E2).
    UNSAT = no two ≤t-fault patterns with equal measured syndrome and differing pred_syn.
    """
    if not gen_syn_z3:
        raise ValueError("control_flow_build_goal requires non-empty gen_syn_z3")

    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]

    ren_1 = make_renamer_from_symbols(vars, "_p1")
    ren_2 = make_renamer_from_symbols(vars, "_p2")

    condition_1 = primed_copy(condition, ren_1)
    condition_2 = primed_copy(condition, ren_2)

    E_x_1 = primed_copy(E_x, ren_1)
    E_z_1 = primed_copy(E_z, ren_1)
    E_x_2 = primed_copy(E_x, ren_2)
    E_z_2 = primed_copy(E_z, ren_2)

    at_most_t_faults_1 = primed_copy(at_most_t_faults, ren_1)
    at_most_t_faults_2 = primed_copy(at_most_t_faults, ren_2)

    gen_syn_z3_1 = primed_copy(gen_syn_z3, ren_1)
    gen_syn_z3_2 = primed_copy(gen_syn_z3, ren_2)
    same_syn = And(*[x == y for x, y in zip(gen_syn_z3_1, gen_syn_z3_2)])

    stab_gens = load_symplectic_txt(stab_txt_path)
    pred_syn_1 = stabilizer_syndrome_from_data(E_x_1, E_z_1, stab_gens)
    pred_syn_2 = stabilizer_syndrome_from_data(E_x_2, E_z_2, stab_gens)
    if pred_syn_1:
        pred_syn_diff = Or(*[a != b for a, b in zip(pred_syn_1, pred_syn_2)])
    else:
        pred_syn_diff = BoolVal(False)

    g = Goal()
    g.add(And(*condition_1))
    g.add(And(*condition_2))
    g.add(at_most_t_faults_1)
    g.add(at_most_t_faults_2)
    g.add(same_syn)
    g.add(pred_syn_diff)
    return g


def control_flow_export_dimacs(
    vars,
    at_most_t_faults,
    condition,
    data_qubits,
    stab_txt_path: str,
    cnf_dir,
    path_tag: str,
    gen_syn_z3,
    use_card2bv: bool = True,
):
    """Export control-flow type-1 (same gen_syn, pred_syn_diff) constraints to DIMACS."""
    cnf_dir = Path(cnf_dir)
    cnf_dir.mkdir(parents=True, exist_ok=True)

    g = control_flow_build_goal(
        vars, at_most_t_faults, condition, data_qubits, gen_syn_z3, stab_txt_path,
    )

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    fault_var_names = sorted({_z3_bool_name(v) for v in vars})
    gen_syn_var_names = [_z3_bool_name(v) for v in gen_syn_z3]
    meta = {
        "path_tag": path_tag,
        "witness_mode": "control_flow_type1",
        "verify_pipeline": "control_flow",
        "fault_var_names": fault_var_names,
        "gen_syn_var_names": gen_syn_var_names,
        "num_fault_vars": len(fault_var_names),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "num_subgoals": len(cnf_files),
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
    }


def break_acts_from_path(path):
    """Split path site acts into initial-data vs circuit-fault acts."""
    data_acts = []
    gate_fault_acts = []
    for step in path:
        for info in step.get("site_info") or []:
            act = info.get("act")
            if act is None:
                continue
            if info.get("fault_mode") == "init":
                data_acts.append(act)
            else:
                gate_fault_acts.append(act)
    return data_acts, gate_fault_acts


def config_flag_enabled(config: dict, key: str, default: bool = False) -> bool:
    """Parse config truthy values: bool/int or '1'/'true'/'yes'/'on'."""
    if not config or key not in config:
        return default
    v = config[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return default


def break_weight_build_goal(
    at_most_t_faults,
    condition,
    data_qubits,
    stab_txt_path: str,
    t: int,
    gsel_prefix: str = "bw_gsel",
    *,
    gate_fault_acts=None,
    compare_residual_to_gate_faults: bool = False,
):
    """
    Break-path counterexample goal (prove UNSAT):
      path_conditions ∧ PbLe(budget_acts, t)
      ∧ ∀ gsel : stab_equiv_weight(E, gsel) > threshold

    threshold:
      - legacy: fixed t  (PbGe(weight, t+1))
      - compare_residual_to_gate_faults: (# circuit faults)
        encoded as ∀k∈0..t: (Σ gate_faults = k) ⇒ wt ≥ k+1
    """
    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]
    Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path, prefix=gsel_prefix)

    from itertools import product

    gate_fault_acts = list(gate_fault_acts or [])
    forall_constraints = []
    for bits in product((False, True), repeat=len(gsel)):
        subs = [(gsel[j], BoolVal(bits[j])) for j in range(len(gsel))]
        b_fixed = [
            Or(simplify(substitute(Epx[i], subs)), simplify(substitute(Epz[i], subs)))
            for i in range(len(Epx))
        ]
        weight_lits = [(bi, 1) for bi in b_fixed]
        if compare_residual_to_gate_faults:
            # Require wt(E_coset) > (# circuit faults) for every coset.
            if not gate_fault_acts:
                forall_constraints.append(PbGe(weight_lits, 1))
            else:
                fault_lits = [(f, 1) for f in gate_fault_acts]
                for k in range(t + 1):
                    forall_constraints.append(
                        Implies(PbEq(fault_lits, k), PbGe(weight_lits, k + 1))
                    )
        else:
            # Require weight > t for EVERY stabilizer coset (min weight > t).
            # Do NOT use Or(Not(pattern_match(gsel)), ...): with free gsel that
            # encodes Exists gsel instead of ForAll gsel.
            forall_constraints.append(PbGe(weight_lits, t + 1))

    g = Goal()
    if condition:
        g.add(And(*condition))
    if at_most_t_faults:
        constraints = at_most_t_faults if isinstance(at_most_t_faults, list) else [at_most_t_faults]
        for c in constraints:
            g.add(c)
    for c in forall_constraints:
        g.add(c)
    return g, gsel


def break_weight_export_dimacs(
    vars,
    at_most_t_faults,
    condition,
    data_qubits,
    stab_txt_path: str,
    t: int,
    cnf_dir,
    path_tag: str,
    gsel_prefix: Optional[str] = None,
    use_card2bv: bool = True,
    *,
    gate_fault_acts=None,
    compare_residual_to_gate_faults: bool = False,
):
    """Build Break-path goal, convert to DIMACS, write path_tag.cnf + sidecars."""
    cnf_dir = Path(cnf_dir)
    cnf_dir.mkdir(parents=True, exist_ok=True)
    prefix = gsel_prefix or f"{path_tag}_gsel"

    g, gsel = break_weight_build_goal(
        at_most_t_faults, condition, data_qubits, stab_txt_path, t, gsel_prefix=prefix,
        gate_fault_acts=gate_fault_acts,
        compare_residual_to_gate_faults=compare_residual_to_gate_faults,
    )

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    fault_var_names = sorted({_z3_bool_name(v) for v in vars})
    gsel_var_names = [_z3_bool_name(v) for v in gsel]
    meta = {
        "path_tag": path_tag,
        "witness_mode": "break",
        "fault_var_names": fault_var_names,
        "gsel_var_names": gsel_var_names,
        "gen_syn_var_names": [],
        "num_fault_vars": len(fault_var_names),
        "t": t,
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "num_subgoals": len(cnf_files),
        "compare_residual_to_gate_faults": bool(compare_residual_to_gate_faults),
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
    }


def break_weight_build_goal_dual(
    at_most_t_faults,
    condition,
    data_qubits,
    stab_txt_path: str,
    t: int,
    gsel_prefix: str = "bw_gsel",
    *,
    log_txt_path: Optional[str] = None,
    gate_fault_acts=None,
    compare_residual_to_gate_faults: bool = False,
):
    """Break goal via dual Pauli enum + commute membership (stab ∪ logical).

    E⊕P ∉ ⟨S⟩ iff anticommutes with some stabilizer or logical (user criterion).
    gsel_prefix ignored (API parity). Returns (goal, []).
    """
    from stab_dual_encoding import forall_min_weight_constraints

    del gsel_prefix
    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]
    gate_fault_acts = list(gate_fault_acts or [])
    n = len(E_x)
    log_path = log_txt_path

    def _cs(min_w: int):
        return forall_min_weight_constraints(
            E_x, E_z, stab_txt_path, min_w, log_txt_path=log_path,
        )

    forall_constraints = []
    num_pauli_constraints = 0
    if compare_residual_to_gate_faults:
        if not gate_fault_acts:
            cs = _cs(1)
            forall_constraints.extend(cs)
            num_pauli_constraints += len(cs)
        else:
            fault_lits = [(f, 1) for f in gate_fault_acts]
            for k in range(t + 1):
                cs = _cs(k + 1)
                num_pauli_constraints += len(cs)
                if cs:
                    forall_constraints.append(Implies(PbEq(fault_lits, k), And(*cs)))
                else:
                    forall_constraints.append(Implies(PbEq(fault_lits, k), BoolVal(True)))
    else:
        cs = _cs(t + 1)
        forall_constraints.extend(cs)
        num_pauli_constraints = len(cs)

    g = Goal()
    if condition:
        g.add(And(*condition))
    if at_most_t_faults:
        constraints = at_most_t_faults if isinstance(at_most_t_faults, list) else [at_most_t_faults]
        for c in constraints:
            g.add(c)
    for c in forall_constraints:
        g.add(c)
    g._dual_num_pauli_constraints = num_pauli_constraints  # type: ignore[attr-defined]
    g._dual_n = n  # type: ignore[attr-defined]
    return g, []


def break_weight_export_dimacs_dual(
    vars,
    at_most_t_faults,
    condition,
    data_qubits,
    stab_txt_path: str,
    t: int,
    cnf_dir,
    path_tag: str,
    gsel_prefix: Optional[str] = None,
    use_card2bv: bool = True,
    *,
    log_txt_path: Optional[str] = None,
    gate_fault_acts=None,
    compare_residual_to_gate_faults: bool = False,
):
    """Export Break goal with dual stab encoding; does not overwrite coset CNFs."""
    cnf_dir = Path(cnf_dir).resolve()
    cnf_dir.mkdir(parents=True, exist_ok=True)
    prefix = gsel_prefix or f"{path_tag}_gsel"

    g, gsel = break_weight_build_goal_dual(
        at_most_t_faults, condition, data_qubits, stab_txt_path, t, gsel_prefix=prefix,
        log_txt_path=log_txt_path,
        gate_fault_acts=gate_fault_acts,
        compare_residual_to_gate_faults=compare_residual_to_gate_faults,
    )
    num_pauli = int(getattr(g, "_dual_num_pauli_constraints", 0))

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    fault_var_names = sorted({_z3_bool_name(v) for v in vars})
    gsel_var_names = [_z3_bool_name(v) for v in gsel]
    meta = {
        "path_tag": path_tag,
        "witness_mode": "break",
        "stab_encoding": "dual_pauli",
        "dual_membership": "commute_stab_and_logical",
        "num_pauli_constraints": num_pauli,
        "fault_var_names": fault_var_names,
        "gsel_var_names": gsel_var_names,
        "gen_syn_var_names": [],
        "num_fault_vars": len(fault_var_names),
        "t": t,
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "log_txt_path": str(log_txt_path) if log_txt_path else None,
        "num_subgoals": len(cnf_files),
        "compare_residual_to_gate_faults": bool(compare_residual_to_gate_faults),
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
        "stab_encoding": "dual_pauli",
        "num_pauli_constraints": num_pauli,
    }


def _break_weight_counterexample_from_lits(lits, var_map):
    """Extract true fault + gsel vars from a SAT model for witness_mode break."""
    assign = model_to_z3_assignment(lits, var_map)
    user_true = {k: v for k, v in assign.items() if v and is_user_var(k)}
    gsel = {k: v for k, v in user_true.items() if "_gsel" in k}
    faults = {k: v for k, v in user_true.items() if k not in gsel}
    return {"faults": faults, "gsel": gsel, "assignment": user_true}


def uniqueness_build_goal_unified(
    vars,
    at_most_t_faults,
    condition,
    gen_syn_z3,
    data_qubits,
    stab_txt_path,
    log_txt_path,
):
    """
    Unified uniqueness goal: same_syn on all measured gen_syn bits,
    E1 != E2, and E1·E2 is a nontrivial logical operator (commutes with all
    stabilizers and anticommutes with at least one logical).
    No syn_constraint in path conditions (caller responsibility).
    """
    E_x = [dq.x for dq in data_qubits]
    E_z = [dq.z for dq in data_qubits]

    ren_1 = make_renamer_from_symbols(vars, "_p1")
    ren_2 = make_renamer_from_symbols(vars, "_p2")

    condition_1 = primed_copy(condition, ren_1)
    condition_2 = primed_copy(condition, ren_2)

    E_x_1 = primed_copy(E_x, ren_1)
    E_z_1 = primed_copy(E_z, ren_1)
    E_x_2 = primed_copy(E_x, ren_2)
    E_z_2 = primed_copy(E_z, ren_2)

    at_most_t_faults_1 = primed_copy(at_most_t_faults, ren_1)
    at_most_t_faults_2 = primed_copy(at_most_t_faults, ren_2)

    gen_syn_z3_1 = primed_copy(gen_syn_z3, ren_1)
    gen_syn_z3_2 = primed_copy(gen_syn_z3, ren_2)

    neq_terms = [Or(ex1 != ex2, ez1 != ez2)
                 for ex1, ez1, ex2, ez2 in zip(E_x_1, E_z_1, E_x_2, E_z_2)]
    condition_E_1_neq_E_2_formula = Or(*neq_terms)

    E_1_add_E_2_X = [Xor(ex1, ex2) for ex1, ex2 in zip(E_x_1, E_x_2)]
    E_1_add_E_2_Z = [Xor(ez1, ez2) for ez1, ez2 in zip(E_z_1, E_z_2)]

    condition_E_product_logical = pauli_is_nontrivial_logical_operator(
        E_1_add_E_2_X, E_1_add_E_2_Z, stab_txt_path, log_txt_path
    )

    same_syn = And(*[x == y for x, y in zip(gen_syn_z3_1, gen_syn_z3_2)])

    g = Goal()
    g.add(same_syn)
    g.add(And(*condition_1))
    g.add(And(*condition_2))
    g.add(at_most_t_faults_1)
    g.add(at_most_t_faults_2)
    g.add(condition_E_1_neq_E_2_formula)
    g.add(condition_E_product_logical)
    return g


def uniqueness_export_dimacs_unified(
    vars,
    at_most_t_faults,
    condition,
    gen_syn_z3,
    data_qubits,
    stab_txt_path,
    log_txt_path,
    cnf_dir,
    path_tag: str,
    use_card2bv: bool = True,
    num_meas_syn_bits: int = 0,
):
    """Export unified uniqueness constraints to DIMACS (no solver)."""
    cnf_dir = Path(cnf_dir)
    cnf_dir.mkdir(parents=True, exist_ok=True)

    g = uniqueness_build_goal_unified(
        vars, at_most_t_faults, condition, gen_syn_z3,
        data_qubits, stab_txt_path, log_txt_path,
    )

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    fault_var_names = sorted({_z3_bool_name(v) for v in vars})
    gen_syn_var_names = [_z3_bool_name(v) for v in gen_syn_z3]
    meta = {
        "path_tag": path_tag,
        "witness_mode": "unified",
        "verify_pipeline": "unified",
        "fault_var_names": fault_var_names,
        "gen_syn_var_names": gen_syn_var_names,
        "num_meas_syn_bits": num_meas_syn_bits,
        "num_fault_vars": len(fault_var_names),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "log_txt_path": str(log_txt_path),
        "num_subgoals": len(cnf_files),
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
    }


def uniqueness_solve_with_cryptominisat_unified(
    vars,
    at_most_t_faults,
    condition,
    gen_syn_z3,
    data_qubits,
    stab_txt_path,
    log_txt_path,
    out_cnf: str = "uniq.cnf",
    cms_bin: str | None = None,
    cms_extra_args: list = None,
    use_card2bv: bool = True,
    timeout_s: Optional[float] = None,
    keep_cnf_files: bool = True,
    verbose: bool = True,
    cms_retries: int = 8,
    num_meas_syn_bits: int = 0,
):
    """Inline unified uniqueness solve via export-to-temp + external SAT solver (MiniSat by default)."""
    import tempfile

    path_tag = Path(out_cnf).stem if out_cnf.endswith(".cnf") else (out_cnf or "uniq")
    with tempfile.TemporaryDirectory(prefix="unified_solve_") as td:
        uniqueness_export_dimacs_unified(
            vars,
            at_most_t_faults,
            condition,
            gen_syn_z3,
            data_qubits,
            stab_txt_path,
            log_txt_path,
            td,
            path_tag,
            use_card2bv=use_card2bv,
            num_meas_syn_bits=num_meas_syn_bits,
        )
        st, counterexample, stats = uniqueness_solve_from_export(
            td,
            path_tag,
            cms_bin=cms_bin,
            cms_extra_args=cms_extra_args,
            timeout_s=timeout_s,
            cms_retries=cms_retries,
            verbose=verbose,
        )

    out_lines = [
        f"[stats] total_solver_time_seconds={stats.get('solver_runtime_seconds', 0.0):.6f}",
        f"[stats] total_clauses={stats.get('total_clauses', 0)}",
        f"[stats] total_dimacs_vars={stats.get('total_dimacs_vars', 0)}",
        f"[stats] peak_solver_rss_bytes={stats.get('peak_solver_rss_bytes', 0)}",
    ]
    out = "\n".join(out_lines) + "\n"
    return st, None, out, counterexample


def _verify_witness_same_syn(gen_syn_z3, p1, p2, fault_vars, default_false=True):
    """Check that extracted p1/p2 satisfy the same_syn constraint."""
    if not gen_syn_z3:
        return True
    ren_1 = make_renamer_from_symbols(fault_vars, "_p1")
    ren_2 = make_renamer_from_symbols(fault_vars, "_p2")
    gs1 = primed_copy(gen_syn_z3, ren_1)
    gs2 = primed_copy(gen_syn_z3, ren_2)
    same_syn = And(*[a == b for a, b in zip(gs1, gs2)])
    assignment = (
        {f"{k}_p1": v for k, v in p1.items()}
        | {f"{k}_p2": v for k, v in p2.items()}
    )
    return bool(eval_with_values(same_syn, assignment, default_false=default_false))


def _counterexample_from_z3_model(model):
    """Extract p1/p2 fault dicts from a Z3 model over primed variables."""
    p1, p2 = {}, {}
    for d in model.decls():
        if not is_true(model[d]):
            continue
        name = d.name()
        if not is_user_var(name):
            continue
        if name.endswith("_p1"):
            p1[name[:-3]] = True
        elif name.endswith("_p2"):
            p2[name[:-3]] = True
    return p1, p2


def _z3_sat_witness(goal, timeout_ms=3600000):
    """Direct Z3 SAT on the uniqueness goal; returns (p1, p2) or None."""
    s = Solver()
    s.set("timeout", timeout_ms)
    for a in goal:
        s.add(a)
    if s.check() != sat:
        return None
    p1, p2 = _counterexample_from_z3_model(s.model())
    if not p1 or not p2:
        return None
    return p1, p2


import subprocess
import re
from z3 import Then  # make sure Goal/Then exist in your imports


##-----------------------
# solve uniqueness constraints with external SAT solver (MiniSat by default)
####
import json
import os, re, shutil, subprocess
from typing import Any, Dict, List, Tuple, Optional








import json


def _z3_bool_name(expr) -> str:
    try:
        return expr.decl().name()
    except Exception:
        return str(expr)


def _count_cnf_stats(cnf_path: str):
    total_clauses = 0
    total_dimacs_vars = 0
    try:
        with open(cnf_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("p cnf "):
                    parts = line.split()
                    if len(parts) >= 4:
                        total_dimacs_vars = int(parts[2])
                        total_clauses = int(parts[3])
                    break
    except (OSError, ValueError):
        pass
    return total_clauses, total_dimacs_vars


def uniqueness_export_dimacs(
    vars,
    at_most_t_faults,
    condition,
    gen_syn_z3,
    data_qubits,
    stab_txt_path,
    log_txt_path,
    cnf_dir,
    path_tag: str,
    witness_mode: str = "type1",
    use_card2bv: bool = True,
):
    """Build Z3 goal, convert to merged DIMACS, write path_tag.cnf + sidecar JSON."""
    cnf_dir = Path(cnf_dir)
    cnf_dir.mkdir(parents=True, exist_ok=True)

    g = uniqueness_build_goal(
        vars, at_most_t_faults, condition, gen_syn_z3,
        data_qubits, stab_txt_path, log_txt_path, witness_mode=witness_mode,
    )

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    fault_var_names = sorted({_z3_bool_name(v) for v in vars})
    gen_syn_var_names = [_z3_bool_name(v) for v in gen_syn_z3]
    meta = {
        "path_tag": path_tag,
        "witness_mode": witness_mode,
        "fault_var_names": fault_var_names,
        "gen_syn_var_names": gen_syn_var_names,
        "num_fault_vars": len(fault_var_names),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "log_txt_path": str(log_txt_path),
        "num_subgoals": len(cnf_files),
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
    }


def _load_export_sidecars(cnf_dir, path_tag: str):
    cnf_dir = Path(cnf_dir)
    cnf_path = cnf_dir / f"{path_tag}.cnf"
    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    if not cnf_path.is_file():
        raise FileNotFoundError(f"Missing CNF: {cnf_path}")
    if not var_map_path.is_file():
        raise FileNotFoundError(f"Missing var map: {var_map_path}")
    if not meta_path.is_file():
        raise FileNotFoundError(f"Missing meta: {meta_path}")

    var_map_raw = json.loads(var_map_path.read_text(encoding="utf-8"))
    var_map = {int(k): Bool(v) for k, v in var_map_raw.items()}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return cnf_path, var_map, meta


def uniqueness_solve_from_export(
    cnf_dir,
    path_tag: str,
    cms_bin: str | None = None,
    cms_extra_args=None,
    timeout_s=None,
    cms_retries: int = 8,
    verbose: bool = False,
):
    """Solve a previously exported path CNF. Returns (status, counterexample, stats)."""
    cnf_dir = Path(cnf_dir)
    cnf_path, var_map, meta = _load_export_sidecars(cnf_dir, path_tag)
    cms_exec = resolve_sat_solver_binary(cms_bin or default_sat_solver_bin())

    witness_mode = meta.get("witness_mode", "type1")
    gen_syn_z3 = [Bool(n) for n in meta.get("gen_syn_var_names", [])]
    fault_vars = [Bool(n) for n in meta.get("fault_var_names", [])]

    total_solver_time_s = 0.0
    peak_solver_rss_bytes = None
    counterexample = None
    st = "unknown"

    for _attempt in range(max(1, cms_retries)):
        st, lits, _out, elapsed_s, rss_bytes = run_dimacs_solver(
            str(cnf_path), cms_exec, timeout_s=timeout_s, extra_args=cms_extra_args,
        )
        total_solver_time_s += elapsed_s
        if rss_bytes is not None:
            peak_solver_rss_bytes = (
                rss_bytes if peak_solver_rss_bytes is None else max(peak_solver_rss_bytes, rss_bytes)
            )

        if st == "unsat":
            break
        if st == "unknown":
            continue
        if st == "sat" and lits:
            if witness_mode == "break":
                counterexample = _break_weight_counterexample_from_lits(lits, var_map)
                break
            if witness_mode == "flag_raised":
                counterexample = _flag_raised_counterexample_from_lits(lits, var_map)
                break
            p1, p2 = pretty_print_true_z3_vars(lits, var_map, do_print=False)
            if witness_mode == "control_flow_type1" or _verify_witness_same_syn(
                gen_syn_z3, p1, p2, fault_vars,
            ):
                counterexample = {"p1": p1, "p2": p2}
                break
            st = "unknown"

    total_clauses = meta.get("total_clauses", 0)
    total_dimacs_vars = meta.get("total_dimacs_vars", 0)
    if not total_clauses:
        total_clauses, total_dimacs_vars = _count_cnf_stats(str(cnf_path))

    stats = {
        "solver_runtime_seconds": total_solver_time_s,
        "peak_solver_rss_bytes": peak_solver_rss_bytes or 0,
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "sat_query_count": meta.get("num_subgoals", 1),
        "num_fault_vars": meta.get("num_fault_vars", len(fault_vars)),
        "status": st,
    }
    if verbose and st == "unsat":
        print("UNSAT")
    elif verbose and st == "sat" and counterexample:
        print("SAT counterexample found")

    return st, counterexample, stats


def uniqueness_solve_with_cryptominisat(
    vars,
    at_most_t_faults,
    condition,
    gen_syn_z3,
    data_qubits,
    stab_txt_path,
    log_txt_path,
    out_cnf: str = "uniq.cnf",
    cms_bin: str | None = None,
    cms_extra_args: list = None,
    use_card2bv: bool = True,
    timeout_s: Optional[float] = None,
    keep_cnf_files: bool = True,
    witness_mode: str = "type1",
    verbose: bool = True,
    cms_retries: int = 8,
):
    """
    Z3 -> CNF (DIMACS) -> external SAT solver (MiniSat by default).

        IMPORTANT (matches caller contract):
            returns (status, model_lits, solver_output, counterexample)
        status in {"sat","unsat","unknown"}
        model_lits: list[int] or None
        solver_output: str
                counterexample: dict or None

    Semantics for this *uniqueness* check:
      - SAT   => counterexample exists (uniqueness FAIL)
      - UNSAT => no counterexample (uniqueness HOLDS)

    If Z3 tactic pipeline creates multiple subgoals, the whole formula is
    the conjunction of subgoals. Therefore:
      - if ANY subgoal is UNSAT => whole is UNSAT
      - else if ANY is UNKNOWN  => whole is UNKNOWN
      - else ALL are SAT        => whole is SAT
    """

    # 1) Build the Z3 goal
    g = uniqueness_build_goal(
        vars, at_most_t_faults, condition, gen_syn_z3,
        data_qubits, stab_txt_path, log_txt_path, witness_mode=witness_mode
    )

    # 2) Convert to CNF (possibly multiple subgoals)
    cnf_files, var_maps = build_dimacs(g, use_card2bv)

    # Rename CNFs if caller passed a base name
    base = out_cnf[:-4] if out_cnf.endswith(".cnf") else out_cnf
    renamed = []
    for i, p in enumerate(cnf_files):
        newp = f"{base}_sub{i}.cnf"
        if p != newp:
            try:
                os.replace(p, newp)
                p = newp
            except OSError:
                # if rename fails, just keep original
                pass
        renamed.append(p)
    cnf_files = renamed

    total_clauses = 0
    total_dimacs_vars = 0
    for cnf in cnf_files:
        try:
            with open(cnf, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("p cnf "):
                        parts = line.split()
                        if len(parts) >= 4:
                            total_dimacs_vars += int(parts[2])
                            total_clauses += int(parts[3])
                        break
        except (OSError, ValueError):
            pass

    # 3) Find external SAT solver (MiniSat by default)
    cms_exec = resolve_sat_solver_binary(cms_bin or default_sat_solver_bin())

    # 4) Merge subgoals into one CNF when needed (avoids invalid partial models)
    solve_cnf = cnf_files[0]
    solve_vmap = var_maps[0]
    merged_cnf = None
    if len(cnf_files) > 1:
        merged_cnf = f"{base}_merged.cnf"
        solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
        solve_cnf = merged_cnf
        total_clauses = 0
        total_dimacs_vars = len(solve_vmap)
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

    outputs = []
    outputs.append(
        f"[info] num_subgoals={len(cnf_files)} merged={len(cnf_files) > 1} "
        f"use_card2bv={use_card2bv} timeout_s={timeout_s}\n"
    )

    total_solver_time_s = 0.0
    peak_solver_rss_bytes = None
    counterexample = None

    def finalize(status: str, model_lits):
        summary_lines = [f"[stats] total_solver_time_seconds={total_solver_time_s:.6f}"]
        summary_lines.append(f"[stats] total_clauses={total_clauses}")
        summary_lines.append(f"[stats] total_dimacs_vars={total_dimacs_vars}")
        if peak_solver_rss_bytes is not None:
            summary_lines.append(f"[stats] peak_solver_rss_bytes={peak_solver_rss_bytes}")
        outputs.append("\n" + "\n".join(summary_lines) + "\n")
        if not keep_cnf_files:
            for p in cnf_files + ([merged_cnf] if merged_cnf else []):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return status, model_lits, "".join(outputs), counterexample

    collected_model_lits = None
    st = "unknown"
    for attempt in range(max(1, cms_retries)):
        st, lits, out, elapsed_s, rss_bytes = run_dimacs_solver(
            solve_cnf, cms_exec, timeout_s=timeout_s, extra_args=cms_extra_args,
        )
        total_solver_time_s += elapsed_s
        if rss_bytes is not None:
            peak_solver_rss_bytes = (
                rss_bytes if peak_solver_rss_bytes is None else max(peak_solver_rss_bytes, rss_bytes)
            )

        outputs.append(
            f"\n===== Solve attempt {attempt + 1} : {st.upper()}  ({solve_cnf}) =====\n"
        )
        outputs.append(out)

        if st == "unsat":
            if verbose:
                print("UNSAT")
                print("Success: uniqueness holds")
            return finalize("unsat", None)

        if st == "unknown":
            continue

        if st == "sat" and lits:
            collected_model_lits = lits
            p1, p2 = pretty_print_true_z3_vars(lits, solve_vmap)
            if _verify_witness_same_syn(gen_syn_z3, p1, p2, vars):
                counterexample = {"p1": p1, "p2": p2}
                if verbose:
                    print("p1 true vars:", p1)
                    print("p2 true vars:", p2)
                return finalize("sat", collected_model_lits)

            outputs.append(
                f"[warn] attempt {attempt + 1}: SAT model failed same_syn verification; retrying\n"
            )

    # CMS inconclusive or witness never verified — fall back to direct Z3
    outputs.append("[info] falling back to Z3 solver for witness extraction\n")
    z3_witness = _z3_sat_witness(g)
    if z3_witness is not None:
        p1, p2 = z3_witness
        if _verify_witness_same_syn(gen_syn_z3, p1, p2, vars):
            counterexample = {"p1": p1, "p2": p2}
            outputs.append("[info] Z3 fallback witness verified same_syn\n")
            if verbose:
                print("p1 true vars (Z3 fallback):", p1)
                print("p2 true vars (Z3 fallback):", p2)
            return finalize("sat", collected_model_lits)

    outputs.append("[warn] no verified witness found; treating as unknown\n")
    if verbose:
        print("WARNING: no verified counterexample; treating as unknown")
    return finalize("unknown", collected_model_lits)
    

from copy import deepcopy
def symbolic_propagate_state_checked(qasm_path: str, init_state, *, track_steps=False):
    """
    Propagate a CircuitXZ `init_state` through a Qiskit QuantumCircuit `qc`
    using the Pauli-flow update rules in `apply_qasm_gate_into_state`.

    - Verifies qubit-count match (circuit vs. state).
    - Ignores non-evolution ops: barrier/reset/measure/id.
    - If track_steps=True, also returns a list of (gate_index, name, qidxs, state_snapshot).

    Returns:
        final_state                    if track_steps == False
        (final_state, step_snapshots)  if track_steps == True
    """
    qc = load_qasm(qasm_path)
    # --- consistency check ---
    n_circ = qc.num_qubits
    n_state = len(init_state.qubits)
    if n_circ != n_state:
        raise ValueError(f"Qubit count mismatch: circuit={n_circ}, state={n_state}")

    # we won’t mutate caller’s state
    state = deepcopy(init_state)

    # Qiskit 2.x: map Qubit -> global index
    def _qidx(qbit):
        return qc.find_bit(qbit).index

    snapshots = []  # (i, name, qidxs, deepcopy(state))

    
    # Walk gates
    for i, (instr, qargs, _cargs) in enumerate(qc.data):
        name = instr.name.lower()
        qidxs = [_qidx(q) for q in qargs]

        if name in ('barrier', 'reset', 'measure', 'id'):
            # no evolution needed
            continue

        # delegate the actual Clifford update to your centralized function
        apply_qasm_gate_into_state(state, name, qidxs)

        if track_steps:
            snapshots.append((i, name, tuple(qidxs), deepcopy(state)))
        
        print(f"After gate {i}: {name} on qubits {qidxs}")
        print("state:")
        print(state.qubits[1].z)

    return (state, snapshots) if track_steps else state


def _reset_qubits(state: CircuitXZ, idxs):
    """Set (x,z) = (False, False) for each qubit index in idxs."""
    for i in idxs:
        state.qubits[i].x = BoolVal(False)
        state.qubits[i].z = BoolVal(False)

def _reset_qubit_x(state: CircuitXZ, idxs):
    """Set x = False for x part qubit index idx."""
    for i in idxs:
        state.qubits[i].x = BoolVal(False)

def _reset_qubit_z(state: CircuitXZ, idxs):
    """Set z = False for z part qubit index idx."""
    for i in idxs:
        state.qubits[i].z = BoolVal(False)



def build_stab_equiv_errors(E_x, E_z, stab_txt_path, prefix="g"):
    """
    Construct stabilizer-equivalent errors.

    Args:
      E_x, E_z: lists of z3 Bool formulas for data qubits
      stab_txt_path: path to stabilizer .txt file
      prefix: name prefix for generator selector variables (default "g")

    Returns:
      (Epx, Epz, gsel)
        - Epx, Epz: new error expressions after applying all possible generator products
        - gsel: list of selector Bool variables, one per generator
    """
    # Load stabilizers from file
    gens = load_symplectic_txt(stab_txt_path)
    m = len(gens)     # number of generators
    n = len(E_x)      # number of data qubits

    # Create selector vars g0..g{m-1}
    gsel = [Bool(f"{prefix}{j}") for j in range(m)]

    # Collect which generators flip which qubit components
    addX = [[] for _ in range(n)]
    addZ = [[] for _ in range(n)]
    for j, (Sx, Sz) in enumerate(gens):
        gj = gsel[j]
        for i in range(n):
            if Sx[i]: addX[i].append(gj)   # Z on generator anticommutes with X error
            if Sz[i]: addZ[i].append(gj)   # X on generator anticommutes with Z error

    # Apply XOR modifications to each data qubit
    Epx, Epz = [], []
    for i in range(n):
        xi = E_x[i]
        for t in addX[i]:
            xi = Xor(xi, t)
        zi = E_z[i]
        for t in addZ[i]:
            zi = Xor(zi, t)
        Epx.append(xi)
        Epz.append(zi)

    return Epx, Epz, gsel
# ---------------------------
# Outputs we care about
# ---------------------------

def ancillas_Z(state: CircuitXZ, anc_idxs: List[int]):
    """Syndrome bits if ancillas are measured in Z basis (flips if X on ancilla)."""
    return [state.qubits[i].x for i in anc_idxs]

def ancillas_X(state: CircuitXZ, anc_idxs: List[int]):
    """Syndrome bits if ancillas are measured in X basis (flips if Z on ancilla)."""
    return [state.qubits[i].z for i in anc_idxs]

def flags_Z(state: CircuitXZ, flag_idxs: List[int]):
    """Flag measured in Z basis (flips if X on flag)."""
    return [state.qubits[i].x for i in flag_idxs]

def flags_X(state: CircuitXZ, flag_idxs: List[int]):
    """Flag measured in X basis (flips if Z on flag)."""
    return [state.qubits[i].z for i in flag_idxs]

def data_qubits(state: CircuitXZ, data_idxs: List[int]):
    """
    Return the residual Pauli error components (x,z) for each data qubit.
    Example:
      (False, True)  -> Z error
      (True, False)  -> X error
      (True, True)   -> Y error
      (False, False) -> no error
    """
    return [(state.qubits[i].x, state.qubits[i].z) for i in data_idxs]

def data_error_weight_literals(state, data_idxs):
    """
    Return a list of literals indicating whether each data qubit carries
    any non-trivial Pauli error (X or Z).
    Useful for counting error weight with PB constraints.
    """
    return [Or(state.qubits[i].x, state.qubits[i].z) for i in data_idxs]


def eval_under(boolexpr, assignment: dict, varenv: dict):
    """
    Evaluate a z3 Bool expression under a *partial assignment*.
    assignment: dict like {"q3_x": True, "ancX0_z": False}
    - Only these vars are substituted.
    - Any var not listed stays symbolic (instead of defaulting to False).
    """
    subs = []
    for name, val in assignment.items():
        if name in varenv:
            subs.append((varenv[name], BoolVal(val)))
    return simplify(substitute(boolexpr, subs))

def evaluate_state_from_raw(state_raw: dict, assignment: dict, default_false: bool = True):
    """
    Evaluate a raw expr dict (output of state_to_raw_expr_dict) under a variable assignment.

    Args:
        state_raw: {"data":[QubitXZ,...], "ancX":[...], ...}
        assignment: dict like {"r_0_faulty_gate3_x0": True, "flagZFault_5_z": False}
        default_false: if True, unassigned Bool symbols default to False.

    Returns:
        A dict with the same structure, but each qubit becomes a pair of booleans:
            {"data":[(x_bool,z_bool), ...], "ancX":[...], ...}
    """
    evaluated = {}
    for gname, qubits in state_raw.items():
        # extract all x/z expressions
        xs = [q.x for q in qubits]
        zs = [q.z for q in qubits]

        # reuse your existing evaluator
        xs_val = eval_with_values(xs, assignment, default_false=default_false)
        zs_val = eval_with_values(zs, assignment, default_false=default_false)

        evaluated[gname] = list(zip(xs_val, zs_val))

    return evaluated


def qubits_to_pauli_string(state_eval):
    """
    state_eval:
      {"data":[{"x":..,"z":..}, ...], "flagX":[[{"x":..,"z":..},...], ...], ...}

    returns:
      {"data":"IXYZ...", "flagX":["IX..", "ZI.."], ...}  # preserves grouping
    """
    def pauli(x, z):
        if x and z: return "Y"
        if x:       return "X"
        if z:       return "Z"
        return "I"

    out = {}
    for group, qubits in state_eval.items():
        if not qubits:
            out[group] = "" if group == "data" else []
            continue

        # list-of-lists -> list of strings
        if isinstance(qubits[0], list):
            out[group] = ["".join(pauli(q["x"], q["z"]) for q in sub) for sub in qubits]
        # flat list -> single string
        else:
            out[group] = "".join(pauli(q["x"], q["z"]) for q in qubits)

    return out

def project_data_only(expr, varenv: dict):
    """
    Substitute anc/flag variables to False, keep all data variables symbolic.
    """
    subs = []
    for name, sym in varenv.items():
        if name.startswith(("ancX","ancZ","flagX","flagZ")):
            subs.append((sym, BoolVal(False)))
    return simplify(substitute(expr, subs))
# ---------------------------
# High-level helper:
#   What happens if the flag has an X/Z/Y error just before measurement?
# ---------------------------

def analyze_flag_errors_multi(
    qasm_path: str,
    anc_idxs: List[int],
    flag_idxs: List[int],
    flag_error_kinds: Dict[int, str],  # mapping: flag_idx -> 'I'|'X'|'Z'|'Y'
):
    """
    Build the Pauli-flow state from QASM, inject specified Pauli errors
    on one or more flag qubits (just before measurement), and return:
      - syn_flips:  List[BoolExpr]  (Z on each ancilla in anc_idxs)
      - flag_flips: List[BoolExpr]  (X-basis flip formula for each flag in flag_idxs)
    """
    state, _ = build_state_from_qasm(qasm_path)

    # Inject errors on the chosen flag qubits
    for fidx, kind in flag_error_kinds.items():
        inject_flag_error(state, fidx, kind)

    syn_flips  = ancillas_Z(state, anc_idxs)
    flag_flips = flags_X(state, flag_idxs)  # X-basis measurement assumed
    return syn_flips, flag_flips

# ---------------------------
# Stabilizers:
#  
# ---------------------------
def load_symplectic_txt(path: str):
    """
    Each line has 'XXXXXXX ZZZZZZZ' (0/1).
    Returns list of (Sx, Sz) where each is a list of 0/1.
    """
    gens = []
    with open(path, 'r') as f:
        for ln in f:
            if not ln.strip():
                continue
            xs, zs = ln.split()
            Sx = [int(c) for c in xs.strip()]
            Sz = [int(c) for c in zs.strip()]
            gens.append((Sx, Sz))
    return gens

def pauli_string_to_symplectic(pstr: str):
    """
    Convert Pauli string like 'XIXZZ' into (Sx, Sz)
    """
    Sx, Sz = [], []
    for p in pstr:
        if p == "I":
            Sx.append(0); Sz.append(0)
        elif p == "X":
            Sx.append(1); Sz.append(0)
        elif p == "Z":
            Sx.append(0); Sz.append(1)
        elif p == "Y":
            Sx.append(1); Sz.append(1)
        else:
            raise ValueError(f"Unknown Pauli {p}")
    return Sx, Sz

def anticomm_formula(Sx, Sz, varenv):
    """
    Build z3 Bool formula:
      ⊕_i ( E_x[i]*S_z[i]  ⊕  E_z[i]*S_x[i] )
    where error vars are q{i}_x, q{i}_z (or data{i}_x/z).
    """
    acc = BoolVal(False)
    for i in range(len(Sx)):
        if Sz[i]:  # stabilizer has Z → anticommutes with X error
            acc = Xor(acc, varenv.get(f"q{i}_x", varenv.get(f"data{i}_x")))
        if Sx[i]:  # stabilizer has X → anticommutes with Z error
            #print("Adding Z term for qubit", i)
            acc = Xor(acc, varenv.get(f"q{i}_z", varenv.get(f"data{i}_z")))
        #print(f"Step {i}: acc = {acc}")
    return acc

# ---------------------------
# Ordered solver check: ancilla[i] ≡ stabilizer[i]
# ---------------------------

def _equiv(a, b) -> bool:
    """True iff a and b are logically equivalent (UNSAT of XOR), via CryptoMiniSat."""
    return z3_expr_is_unsat(Xor(a, b))

def _counterexample(a, b):
    """Return a counterexample model if a ≢ b, else None."""
    s = Solver()
    s.add(Xor(a, b))
    return s.model() if s.check() == sat else None


def check_ancillas_match_symplectic_ordered(qasm_path: str,
                                            stab_txt_path: str | None = None,
                                            order: str = "X-then-Z",
                                            *,
                                            generators: list | None = None):
    """
    Pairwise, ordered equivalence:
      ancilla[i]  ≡  anticommute_formula_from_txt_line[i]

    - Ancilla formulas are taken from the circuit and projected to data-only
      (anc/flag vars set False; data vars symbolic).
    - Stabilizer formulas come from `generators` or the txt (file order preserved).
    - `order` tells how to concatenate ancillas from QASM registers:
         "X-then-Z" (default) means ancX first, then ancZ (both in QASM order).
         "Z-then-X" means ancZ first, then ancX.
      Choose the one that matches the line order in your .txt.
    """
    # Build circuit (symbolic) and detect groups
    state, qc, varenv = build_variable_state_from_qasm(qasm_path)
    groups = detect_qubit_groups(qc)

    # Ancilla flip formulas from circuit → project to data-only
    ancX = [project_data_only(e, varenv) for e in ancillas_X(state, groups["ancX"])]
    ancZ = [project_data_only(e, varenv) for e in ancillas_Z(state, groups["ancZ"])]

    
    
    ancillas = (ancX + ancZ) if order == "X-then-Z" else (ancZ + ancX)

    # Stabilizer anticommute formulas (exact generator order)
    if generators is not None:
        gens = generators
    elif stab_txt_path is not None:
        gens = load_symplectic_txt(stab_txt_path)
    else:
        raise ValueError("check_ancillas_match_symplectic_ordered requires stab_txt_path or generators")
    stabs = [anticomm_formula(Sx, Sz, varenv) for (Sx, Sz) in gens]

    if not _QUIET:
        print(f"Total ancillas considered: {len(ancillas)}")
        for i, (a, s) in enumerate(zip(ancillas, stabs)):
            print(f"Ancilla formula [{i}]:", a)
            print(f"Expected formula (stab.txt) [{i}]:", s)
        if len(ancillas) > len(stabs):
            for i in range(len(stabs), len(ancillas)):
                print(f"Ancilla formula [{i}]:", ancillas[i])
                print(f"Expected formula (stab.txt) [{i}]: (missing — stab.txt has fewer rows)")
        elif len(stabs) > len(ancillas):
            for i in range(len(ancillas), len(stabs)):
                print(f"Ancilla formula [{i}]: (missing — circuit has fewer ancillas)")
                print(f"Expected formula (stab.txt) [{i}]:", stabs[i])

    if len(ancillas) != len(stabs):
        if not _QUIET:
            print(f"[COUNT MISMATCH] ancillas={len(ancillas)} vs stabs={len(stabs)}")
        return {"ok": False, "mismatches": list(range(min(len(ancillas), len(stabs))))}

    # Combine all equivalence checks into a single AND condition
    combined_condition = And(*[Xor(a, s) == False for a, s in zip(ancillas, stabs)])

    # Check if the combined condition is satisfied (CryptoMiniSat on exported CNF)
    if z3_expr_is_unsat(Not(combined_condition)):
        return {"ok": True, "mismatches": []}
    else:
        mismatches = [i for i, (a, s) in enumerate(zip(ancillas, stabs)) if not _equiv(a, s)]
        return {"ok": False, "mismatches": mismatches}


def exists_stab_equiv(E1_x, E1_z, E2_x, E2_z, stab_txt_path, *, prefix="gsel"):
    """
    gens: [(Sx, Sz), ...] each Sx,Sz list[int] length n
    Returns: (constraint, selectors)

    The '*' makes 'prefix' a keyword-only argument.
    """
    gens = load_symplectic_txt(stab_txt_path)
   
    m = len(gens); n = len(E1_x)

    # Prefix controls variable naming
    gsel = [Bool(f"{prefix}_{j}") for j in range(m)]

    addX = [BoolVal(False) for _ in range(n)]
    addZ = [BoolVal(False) for _ in range(n)]

    for j, (Sx, Sz) in enumerate(gens):
        gj = gsel[j]
        for i in range(n):
            if Sx[i]:
                addX[i] = Xor(addX[i], gj)
            if Sz[i]:
                addZ[i] = Xor(addZ[i], gj)

    eqs = []
    for i in range(n):
        eqs.append(E2_x[i] == Xor(E1_x[i], addX[i]))
        eqs.append(E2_z[i] == Xor(E1_z[i], addZ[i]))
    '''
    print("addX:", addX)
    print("addZ:", addZ)

    print("Ex_1 ,E_x_2", simplify(E1_x[0]), simplify(E2_x[0]))
    print("E_z_1 ,E_z_2",simplify(E1_z[0]), simplify(E2_z[0]))

    print("eqs:", simplify(eqs[0]))

    assign = {"r_0_faulty_gate0_z0_p2": True, "r_0_faulty_gate0_x0_p2": True, "r_0_faulty_gate0_z0_p1": True,
    "r_0_faulty_gate0_x1_p1": True, "gsel_0": True}

    for (x,y) in zip(E1_x, E1_z):
        print("E_x_1 eval ", eval_with_values( x, assign), "E_z_1 eval ", eval_with_values( y, assign))
    
    for (x,y) in zip(E2_x, E2_z):
        print("E_x_2 eval ", eval_with_values( x, assign), "E_z_2 eval ", eval_with_values( y, assign))
 
    for eq in eqs:
        print("eq eval ", eval_with_values( eq, assign))
    print("eval ", eval_with_values( And(eqs), assign))
    '''
    return And(eqs), gsel


from z3 import BoolVal, Not

from z3 import BoolVal, Not, Exists

def error_not_in_stabilizer(E_x, E_z, stab_txt_path: str, *, prefix: str = "gsel"):
    """
    Build a constraint saying that the Pauli error (E_x, E_z) is NOT
    equivalent to the identity up to stabilizers.

    Args:
      E_x, E_z : lists of z3 BoolRef, describing the X/Z components of an error
      stab_txt_path : path to the stabilizer file (symplectic txt)
      prefix : optional prefix for the selector variables (default: 'gsel')

    Returns:
      (constraints, gsel)

      constraints : a *list* of one BoolRef, so that you can do
                    And(*condition_not_in_stab_1) after priming.
      gsel        : list of selector BoolRef used inside the Exists(..).

      Semantics:
        not_in_stab  ≡  ¬ ∃ gsel .  (E_x, E_z) == (sum of selected generators)
    """
    n = len(E_x)
    # Identity error (all zero)
    zero_x = [BoolVal(False) for _ in range(n)]
    zero_z = [BoolVal(False) for _ in range(n)]

    # E == I ⊕ (sum of generators)  (i.e. E is in stabilizer group)
    stab_eq, gsel = exists_stab_equiv(
        E1_x=E_x,
        E1_z=E_z,
        E2_x=zero_x,
        E2_z=zero_z,
        stab_txt_path=stab_txt_path,
        prefix=prefix,
    )

    not_in_stab = Not(Exists(gsel, stab_eq))
    # Return as a list so your existing code
    #   condition_not_in_stab_1 = primed_copy(condition_not_in_stab, ren_1)
    #   s.add(And(*condition_not_in_stab_1))
    # still works.
    return [not_in_stab], gsel


def _pauli_anticommutes_with_generator(E_x, E_z, P_x, P_z):
    """True iff Pauli (E_x, E_z) anticommutes with generator (P_x, P_z)."""
    terms = []
    n = len(E_x)
    for j in range(n):
        if P_x[j]:
            terms.append(E_z[j])
        if P_z[j]:
            terms.append(E_x[j])
    if not terms:
        return BoolVal(False)
    return xor_list(terms)


def pauli_commutes_with_all_stabilizers(E_x, E_z, stab_txt_path: str):
    """True iff E commutes with every stabilizer generator."""
    gens = load_symplectic_txt(stab_txt_path)
    if not gens:
        return BoolVal(True)
    return And(*[
        Not(_pauli_anticommutes_with_generator(E_x, E_z, P_x, P_z))
        for P_x, P_z in gens
    ])


def pauli_anticommutes_with_some_logical(E_x, E_z, log_txt_path: str):
    """True iff E anticommutes with at least one logical operator."""
    log_op = load_symplectic_txt(log_txt_path)
    if not log_op:
        return BoolVal(False)
    return Or(*[
        _pauli_anticommutes_with_generator(E_x, E_z, P_x, P_z)
        for P_x, P_z in log_op
    ])


def pauli_is_nontrivial_logical_operator(E_x, E_z, stab_txt_path: str, log_txt_path: str):
    """True iff E commutes with all stabilizers and anticommutes with some logical."""
    return And(
        pauli_commutes_with_all_stabilizers(E_x, E_z, stab_txt_path),
        pauli_anticommutes_with_some_logical(E_x, E_z, log_txt_path),
    )


def pauli_not_in_stabilizer(E_x, E_z, stab_txt_path: str,log_txt_path: str):
    gens = load_symplectic_txt(stab_txt_path)
    log_op = load_symplectic_txt(log_txt_path)

    log_stab = gens + log_op

    m = len(log_stab); n = len(E_x)

    commute = []
    for i, (P_x, P_z) in enumerate(log_stab):
        commute_x = []
        commute_z = []
        for j in range(n):

            if P_x[j]:
                commute_x.append( E_z[j] )
            if P_z[j]:
                commute_z.append( E_x[j] )

        commute_x_z = commute_x + commute_z
        commute.append(xor_list(commute_x_z))

    return Or(commute)
        







from z3 import Solver, ForAll, Exists, Or, Xor, PbLe

def forall_fault_exists_low_weight_per_gate(
    qasm_path: str,
    stab_txt_path: str,
    gate_indices=None,          # e.g. range(10) or [0,1,2]
    fault_mode: str = "2q",     # "2q" | "1q" | "either" (whatever your builder accepts)
    flag_axis: str = "z",       # inject flag error on this axis ("x" or "z")
    flag_prefix: str = "flagErr",
):
    """
    This is for checking 'bad loocation'
    For each gate in `gate_indices`:
      state, qc, site_info, groups = build_state_with_fault_after_gate(...)
      fault_vars = all 'f*' vars from site_info['vars'] (universally quantified)
      E' = stabilizer-equivalent data error
      b  = per-qubit error indicators
      Check:  ∀ fault_vars. ( Or(fault_vars) → ∃ gsel.  sum(b) ≤ 1 )
      And also: Xor(site_info['act'], flag_var)   (your extra constraint)

    Returns: dict {gate_index: {"result": sat/unsat, "num_fault_vars": int, "num_gens": int}}
    """
    results = {}
    unsat_gates = []
    # If user didn't pass indices, default to all gates
    if gate_indices is None:
        # Peek the circuit once to know how many gates
        _, qc, _, _ = build_state_with_fault_after_gate(qasm_path, gate_index=0, fault_mode=fault_mode)
        gate_indices = range(len(qc.data))

    for i in gate_indices:
        # 1) Build state with *symbolic* fault inserted after gate i
        state, qc, site_info, groups = build_state_with_fault_after_gate(
            qasm_path, gate_index=i, fault_mode=fault_mode
        )

        # 2) Collect the fault variables at this site (universally quantified)
        fault_vars = [v for k, v in site_info["vars"].items() if k.startswith("f")]
        if not fault_vars:
            # No fault DOFs at this site (e.g. a barrier/measure) → skip
            results[i] = {"result": "no-fault-vars", "num_fault_vars": 0, "num_gens": 0}
            continue

        # 3) Extract data error (E_x, E_z)
        data_idxs = groups["data"]
        E_x = [state.qubits[j].x for j in data_idxs]
        E_z = [state.qubits[j].z for j in data_idxs]

        

        # 4) Build stabilizer-equivalent errors E' using selector Booleans gsel
        Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path, prefix=f"g")

        # 5) Weight ≤ 1 predicate: sum over per-qubit indicators b_i = Or(E′x_i, E′z_i)
        b = [Or(xi, zi) for xi, zi in zip(Epx, Epz)]

        # 6) ∀ fault_vars: Or(fault_vars) → ∃ gsel: sum(b) ≤ 1
        s = Solver()
        body = Exists(gsel, PbLe([(bi, 1) for bi in b], 1))
        phi  = ForAll(fault_vars, Or(fault_vars) == False)  # placeholder replaced below

        # Rebuild phi cleanly (the line above avoids z3py “no quantifier vars” edge cases if empty)
        phi = ForAll(fault_vars, 
                     Or(  # (¬any_fault) ∨ (∃ gsel: weight ≤ 1)
                        Or([v for v in fault_vars]) == False,
                        body
                     ))

        # 7) Add both the quantified property and your extra XOR constraint
        s.add(phi)
        

        res = s.check()
        results[i] = {
            "result": str(res),
            "num_fault_vars": len(fault_vars),
            "num_gens": len(gsel),
        }
        if str(res) == "unsat":
            unsat_gates.append(i)

    return results, unsat_gates

# ---------------------------
# Rename symbol
# ---------------------------
def primed_copy(exprs: list, rename: dict):
    """Return [ substitute(e, rename) for e in exprs ]."""
    return [substitute(e, [(k, v) for k, v in rename.items()]) for e in exprs]

def make_renamer_from_symbols(symbols: list, suffix= "_p"):
    """
    Given a list of z3 symbols (BoolRef) that appear in E_x/E_z etc.,
    build a rename map sym -> fresh Bool with a suffix.
    """
    ren = {}
    for s in symbols:
        # s.decl().name() gets 'f_gate3_x0' etc.
        ren[s] = Bool(s.decl().name() + suffix)
    return ren

# ---------------------------
# For evaluation
# ---------------------------

from z3 import Bool, BoolVal, substitute, simplify, is_true, Z3_OP_UNINTERPRETED, is_bool, is_app, is_quantifier

def collect_bool_symbols(expr):
    """Recursively collect all uninterpreted Bool symbols in expr."""
    syms = set()
    def _walk(e):
        # Check if this is an application (has .decl() method)
        if is_app(e):
            if is_bool(e) and e.decl().kind() == Z3_OP_UNINTERPRETED and e.num_args() == 0:
                syms.add(e)
            # Recursively walk children for applications
            for ch in e.children():
                _walk(ch)
        elif is_quantifier(e):
            # For quantifiers, walk the body
            _walk(e.body())
        # Note: we skip other expression types (like BoolSort, etc.)
    _walk(expr)
    return syms

def eval_with_values(exprs, assignment, default_false=True):
    """
    Evaluate Z3 Bool expr(s) under a dict of variable→bool values.
    Missing vars default to False if default_false=True.
    Works for a single expr or an iterable of exprs.
    """
    def _eval_one(e):
        syms = collect_bool_symbols(e)
        # Map names of symbols that actually appear in e
        name2sym = {s.decl().name(): s for s in syms}

        subs = []
        # Apply provided assignments *only for symbols that appear in e*
        for name, val in assignment.items():
            if name in name2sym:
                subs.append((name2sym[name], BoolVal(val)))

        # Default any remaining symbols (that appear in e) to False if requested
        if default_false:
            for name, s in name2sym.items():
                if name not in assignment:
                    subs.append((s, BoolVal(False)))

        return is_true(simplify(substitute(e, subs)))

    if isinstance(exprs, (list, tuple, set)):
        return [_eval_one(e) for e in exprs]
    else:
        return _eval_one(exprs)



def eval_state_dict_with_values(state_dict, assignment, default_false=True):
    """
    state_dict:
      {
        "data":  [QubitXZ, ...] or [[QubitXZ, ...], ...],
        "ancX":  ...
      }

    returns:
      {
        "data":  [{"x":bool,"z":bool}, ...] or [[{"x":..,"z":..}, ...], ...],
        ...
      }
    """

    out = {}

    for group, qubits in state_dict.items():

        # case 1: empty
        if not qubits:
            out[group] = []
            continue

        # case 2: list of lists
        if isinstance(qubits[0], list):
            out[group] = []
            for sub in qubits:
                out[group].append([
                    {
                        "x": eval_with_values(q.x, assignment, default_false),
                        "z": eval_with_values(q.z, assignment, default_false),
                    }
                    for q in sub
                ])

        # case 3: flat list
        else:
            out[group] = [
                {
                    "x": eval_with_values(q.x, assignment, default_false),
                    "z": eval_with_values(q.z, assignment, default_false),
                }
                for q in qubits
            ]

    return out


def _unwrap_proof_protocol_paths(path_or_paths):
    """Return all_paths from proof_protocol output, or passthrough a path collection."""
    if path_or_paths is None:
        raise ValueError("path is empty")
    if isinstance(path_or_paths, tuple):
        if len(path_or_paths) == 0:
            raise ValueError("path is empty")
        path_or_paths = path_or_paths[0]
    if path_or_paths is None or len(path_or_paths) == 0:
        raise ValueError("path is empty")
    return path_or_paths


def _is_protocol_step(step):
    return isinstance(step, dict) and "state" in step


def _is_state_dict(state):
    return isinstance(state, dict) and "data" in state and "state" not in state


def _steps_from_state_dicts(states):
    return [
        {"round": i, "node": None, "instruction": None, "state": st}
        for i, st in enumerate(states)
    ]


def _select_protocol_path(path, path_index=0):
    """
    Normalize proof_protocol path input to a list of step dicts.

    Accepts:
      - (all_paths, stats) tuple from proof_protocol()
      - all_paths: list[list[step_dict]]
      - single path: list[step_dict]
    """
    paths = _unwrap_proof_protocol_paths(path)

    if _is_protocol_step(paths[0]):
        return paths, 0

    if path_index < 0 or path_index >= len(paths):
        raise IndexError(
            f"path_index out of range: {path_index}, valid 0..{len(paths)-1}"
        )

    selected = paths[path_index]
    if not isinstance(selected, list) or len(selected) == 0:
        raise TypeError(
            "Expected proof_protocol all_paths (list of step-dict lists), "
            "a single path (list of step dicts), or (all_paths, stats) tuple."
        )

    if _is_protocol_step(selected[0]):
        return selected, path_index

    if _is_state_dict(selected[0]):
        return _steps_from_state_dicts(selected), path_index

    raise TypeError(
        "Expected proof_protocol all_paths (list of step-dict lists), "
        "a single path (list of step dicts), or (all_paths, stats) tuple."
    )


def _flatten_qubit_list(items):
    return [q for g in items for q in (g if isinstance(g, list) else [g])]


def _find_lut_instruction(path):
    for step in reversed(path):
        instr = step.get("instruction")
        if instr and str(instr).startswith("LUT_"):
            return instr
    raise ValueError("No LUT instruction found on path")


def build_gen_syn_z3(path, lut_instr=None):
    """
    Build labeled Z3 expressions for gen_syn_z3 exactly as proof_path does.

    Returns:
        (lut_name, labels, exprs)
    """
    from proof_protocol import parse_lut_instr

    lut = lut_instr or _find_lut_instruction(path)
    gen_syn = parse_lut_instr(lut)
    labels = []
    exprs = []
    for kind, idx in gen_syn:
        if idx >= len(path):
            raise IndexError(
                f"LUT {lut!r} references step {idx}, but path has {len(path)} steps"
            )
        st = path[idx]["state"]
        ancX = _flatten_qubit_list(st.get("ancX", []))
        ancZ = _flatten_qubit_list(st.get("ancZ", []))
        flagX = _flatten_qubit_list(st.get("flagX", []))
        flagZ = _flatten_qubit_list(st.get("flagZ", []))

        if kind == "s":
            for j, a in enumerate(ancX):
                labels.append(f"s_{idx}|ancX[{j}].z")
                exprs.append(a.z)
            for j, a in enumerate(ancZ):
                labels.append(f"s_{idx}|ancZ[{j}].x")
                exprs.append(a.x)
        elif kind == "f":
            for j, q in enumerate(flagX):
                labels.append(f"f_{idx}|flagX[{j}].z")
                exprs.append(q.z)
            for j, q in enumerate(flagZ):
                labels.append(f"f_{idx}|flagZ[{j}].x")
                exprs.append(q.x)
    return lut, labels, exprs


def eval_gen_syn_on_path(path, assignment, path_index=0, default_false=True, lut_instr=None):
    """
    Evaluate the generalized syndrome bits (gen_syn_z3) for one fault assignment.

    Uses the same LUT indexing and measured-bit convention as proof_path().
    """
    selected_path, resolved_index = _select_protocol_path(path, path_index=path_index)
    lut, labels, exprs = build_gen_syn_z3(selected_path, lut_instr=lut_instr)
    values = eval_with_values(exprs, assignment, default_false=default_false)
    bits = list(zip(labels, values))
    return {
        "path_index": resolved_index,
        "lut": lut,
        "bits": bits,
        "vector": [int(v) for _, v in bits],
    }


def print_gen_syn_comparison(path, assignment_a, assignment_b, path_index=0, default_false=True):
    """
    Print LUT gen_syn bit vectors for two assignments side-by-side.
    """
    ga = eval_gen_syn_on_path(path, assignment_a, path_index=path_index, default_false=default_false)
    gb = eval_gen_syn_on_path(path, assignment_b, path_index=path_index, default_false=default_false)
    same = ga["vector"] == gb["vector"]

    print(f"Path {ga['path_index']}  LUT={ga['lut']}")
    print(f"  same gen_syn? {same}")
    for (lbl, va), (_, vb) in zip(ga["bits"], gb["bits"]):
        mark = "" if va == vb else "  <-- differ"
        print(f"  {lbl:<22}  A={int(va)}  B={int(vb)}{mark}")

    return {"a": ga, "b": gb, "same_gen_syn": same}


def eval_path_all_states(path, assignment, default_false=True):
    out = []
    for step in path:
        st = step.get("state", None)
        if st is None:
            continue
        out.append({
            "round": step.get("round"),
            "node": step.get("node"),
            "instruction": step.get("instruction"),
            "state": eval_state_dict_with_values(st, assignment, default_false=default_false),
        })
    return out


def evaluate_two_assignments_on_path(path, assignment_a, assignment_b, path_index=0, default_false=True):
    """
    Evaluate qubit-level Pauli results for two assignments on one selected path.

    Returns a dict with per-step evaluated states and final Pauli strings for both cases.
    """
    selected_path, resolved_index = _select_protocol_path(path, path_index=path_index)

    steps_a = eval_path_all_states(selected_path, assignment_a, default_false=default_false)
    steps_b = eval_path_all_states(selected_path, assignment_b, default_false=default_false)

    final_state_a = eval_state_dict_with_values(selected_path[-1]["state"], assignment_a, default_false=default_false)
    final_state_b = eval_state_dict_with_values(selected_path[-1]["state"], assignment_b, default_false=default_false)

    return {
        "path_index": resolved_index,
        "assignment_a": assignment_a,
        "assignment_b": assignment_b,
        "steps_a": steps_a,
        "steps_b": steps_b,
        "pauli_steps_a": [
            {
                "round": s.get("round"),
                "node": s.get("node"),
                "instruction": s.get("instruction"),
                "pauli": qubits_to_pauli_string(s["state"]),
            }
            for s in steps_a
        ],
        "pauli_steps_b": [
            {
                "round": s.get("round"),
                "node": s.get("node"),
                "instruction": s.get("instruction"),
                "pauli": qubits_to_pauli_string(s["state"]),
            }
            for s in steps_b
        ],
        "final_state_a": final_state_a,
        "final_state_b": final_state_b,
        "final_pauli_a": qubits_to_pauli_string(final_state_a),
        "final_pauli_b": qubits_to_pauli_string(final_state_b),
    }


def _format_assignment(asgmt):
    """Compact display: group by gate name, list affected fault bits."""
    from collections import defaultdict
    groups = defaultdict(list)
    for k in asgmt:
        # e.g. r_0_faulty_gate14_z0  ->  gate=gate14, bit=z0
        parts = k.split("_")
        try:
            gate_idx = next(i for i, p in enumerate(parts) if p.startswith("gate"))
            gate = parts[gate_idx]
            bit = "_".join(parts[gate_idx + 1:])
            groups[gate].append(bit)
        except StopIteration:
            groups[k].append("True")
    return "  ".join(f"{g}[{','.join(bits)}]" for g, bits in groups.items())


def _fmt_pauli(p):
    """Return non-empty Pauli fields as 'field:VALUE' pairs, '-' for empty registers."""
    cols = []
    for field in ("data", "ancX", "ancZ", "flagX", "flagZ"):
        v = p.get(field, [])
        if v and v != []:
            cols.append(f"{field}:{v}")
    return "  ".join(cols) if cols else "(trivial)"


def print_two_assignment_path_report(path, assignment_a, assignment_b, path_index=0, default_false=True):
    """
    Convenience printer for notebook usage. Displays a compact side-by-side
    per-round Pauli comparison table.
    """
    report = evaluate_two_assignments_on_path(
        path,
        assignment_a,
        assignment_b,
        path_index=path_index,
        default_false=default_false,
    )

    W = 62
    bar = "─" * W

    print(f"╔{'═' * W}╗")
    print(f"║  Path Index: {report['path_index']:<{W - 14}}║")
    print(f"╚{'═' * W}╝")

    print(f"\n  Fault A:  {_format_assignment(report['assignment_a'])}")
    print(f"  Fault B:  {_format_assignment(report['assignment_b'])}")

    steps_a = report["pauli_steps_a"]
    steps_b = report["pauli_steps_b"]
    n = max(len(steps_a), len(steps_b))

    FIELDS = ("data", "ancX", "ancZ", "flagX", "flagZ")

    for i in range(n):
        sa = steps_a[i] if i < len(steps_a) else None
        sb = steps_b[i] if i < len(steps_b) else None
        node = (sa or sb)["node"]
        instr = (sa or sb)["instruction"]
        rnd = (sa or sb)["round"]

        print(f"\n{bar}")
        print(f"  Round {rnd}  |  {instr}  @  {node}")
        print(bar)

        # collect non-trivial fields across both steps
        active_fields = [
            f for f in FIELDS
            if (sa and sa["pauli"].get(f) and sa["pauli"][f] != [])
            or (sb and sb["pauli"].get(f) and sb["pauli"][f] != [])
        ]

        header_parts = ["     "] + [f"{f:<8}" for f in active_fields]
        print("  " + "  ".join(header_parts))

        for lbl, step in (("[A]", sa), ("[B]", sb)):
            if step is None:
                continue
            p = step["pauli"]
            vals = [p.get(f, []) or "-" for f in active_fields]
            row_parts = [f"{lbl:<5}"] + [f"{str(v):<8}" for v in vals]
            print("  " + "  ".join(row_parts))

    fa = report["final_pauli_a"].get("data", "")
    fb = report["final_pauli_b"].get("data", "")
    same = (fa == fb)
    verdict = "SAME" if same else "DIFFER  <-- inequivalent data errors"

    print(f"\n{'═' * W}")
    print(f"  FINAL DATA PAULI")
    print(f"  [A]  {fa}")
    print(f"  [B]  {fb}")
    print(f"  {'✓' if same else '⚠'}  data: {verdict}")
    print(f"{'═' * W}")

    return report


def evaluate_two_assignments_on_paths(paths, assignment_a, assignment_b, path_indices=None, default_false=True):
    """
    Evaluate two assignments on multiple paths.

    Args:
        paths: output of proof_protocol(...)
        assignment_a, assignment_b: dict[str, bool]
        path_indices: list[int] of target paths; if None, evaluate all paths.

    Returns:
        list of per-path reports from evaluate_two_assignments_on_path(...)
    """
    paths = _unwrap_proof_protocol_paths(paths)
    if _is_protocol_step(paths[0]):
        paths = [paths]

    if path_indices is None:
        path_indices = list(range(len(paths)))

    reports = []
    for idx in path_indices:
        reports.append(
            evaluate_two_assignments_on_path(
                paths,
                assignment_a,
                assignment_b,
                path_index=idx,
                default_false=default_false,
            )
        )
    return reports


def print_two_assignments_on_paths_report(paths, assignment_a, assignment_b, path_indices=None, default_false=True):
    """
    Print round-by-round qubit Pauli results for two assignments across multiple paths.
    Useful for inspecting failed path indices.
    """
    reports = evaluate_two_assignments_on_paths(
        paths,
        assignment_a,
        assignment_b,
        path_indices=path_indices,
        default_false=default_false,
    )

    for rep in reports:
        print("\n" + "=" * 72)
        print(f"Path {rep['path_index']}")
        print("=" * 72)

        print("\n--- Assignment A ---")
        print(rep["assignment_a"])
        for s in rep["pauli_steps_a"]:
            p = s["pauli"]
            print(f"round={s['round']}, node={s['node']}, instr={s['instruction']}")
            print(f"  data: {p.get('data', '')}")
            if p.get("ancX", ""):
                print(f"  ancX: {p['ancX']}")
            if p.get("ancZ", ""):
                print(f"  ancZ: {p['ancZ']}")
            if p.get("flagX", []):
                print(f"  flagX: {p['flagX']}")
            if p.get("flagZ", []):
                print(f"  flagZ: {p['flagZ']}")

        print("\n--- Assignment B ---")
        print(rep["assignment_b"])
        for s in rep["pauli_steps_b"]:
            p = s["pauli"]
            print(f"round={s['round']}, node={s['node']}, instr={s['instruction']}")
            print(f"  data: {p.get('data', '')}")
            if p.get("ancX", ""):
                print(f"  ancX: {p['ancX']}")
            if p.get("ancZ", ""):
                print(f"  ancZ: {p['ancZ']}")
            if p.get("flagX", []):
                print(f"  flagX: {p['flagX']}")
            if p.get("flagZ", []):
                print(f"  flagZ: {p['flagZ']}")

        print("\nFinal A:", rep["final_pauli_a"])
        print("Final B:", rep["final_pauli_b"])
        print("Same final data:", rep["final_pauli_a"].get("data", "") == rep["final_pauli_b"].get("data", ""))

    return reports
# ---------------------------
# prove for eacg stage
# ---------------------------
def _data_qubit_preservation_eqs(state, groups, varenv, regmap):
    """Return z3 equalities: output data Pauli == input symbolic Pauli."""
    eqs = []
    for idx in groups["data"]:
        regname, j = regmap[idx]
        prefix = f"{regname}{j}"
        in_x = varenv[f"{prefix}_x"]
        in_z = varenv[f"{prefix}_z"]
        out_x = project_data_only(state.qubits[idx].x, varenv)
        out_z = project_data_only(state.qubits[idx].z, varenv)
        eqs.append(out_x == in_x)
        eqs.append(out_z == in_z)
    return eqs


def verify_syndrome_extraction(
    qasm_path: str,
    stab_txt_path: str | None = None,
    *,
    order: str = "X-then-Z",
    generators: list | None = None,
) -> Dict:
    """
    Check that a QASM circuit is a valid syndrome extractor (no faults):
      1) measured ancillas match stabilizer rows (ordered)
      2) data qubits are unchanged (input Pauli == output Pauli)

    Use full stab.txt via stab_txt_path, or pass explicit generators for partial extraction.
    """
    report = check_ancillas_match_symplectic_ordered(
        qasm_path,
        stab_txt_path,
        order=order,
        generators=generators,
    )
    ancilla_ok = bool(report["ok"])
    mismatches = list(report.get("mismatches", []))

    data_preserved = False
    data_mismatches: List[int] = []
    if ancilla_ok:
        state, qc, varenv = build_variable_state_from_qasm(qasm_path)
        groups = detect_qubit_groups(qc)
        regmap = _regmap_indices(qc)
        data_eqs = _data_qubit_preservation_eqs(state, groups, varenv, regmap)
        if z3_expr_is_unsat(Not(And(*data_eqs))):
            data_preserved = True
        else:
            for idx in groups["data"]:
                regname, j = regmap[idx]
                prefix = f"{regname}{j}"
                in_x = varenv[f"{prefix}_x"]
                in_z = varenv[f"{prefix}_z"]
                out_x = project_data_only(state.qubits[idx].x, varenv)
                out_z = project_data_only(state.qubits[idx].z, varenv)
                if not _equiv(out_x, in_x) or not _equiv(out_z, in_z):
                    data_mismatches.append(idx)

    ok = ancilla_ok and data_preserved
    return {
        "ok": ok,
        "ancilla_ok": ancilla_ok,
        "data_preserved": data_preserved,
        "mismatches": mismatches,
        "data_mismatches": data_mismatches,
    }


def prove_syndrome_extractions(qasm_path: str, stab_txt_path: str):
    result = verify_syndrome_extraction(qasm_path, stab_txt_path, order="X-then-Z")

    print("Result of ordered ancilla vs stabilizer check:")
    if result["ancilla_ok"]:
        print("Success : ancilla measurements match stabilizers in order.")
    else:
        for mi in result["mismatches"]:
            print(f"  Mismatch at stabilizer index {mi}")
        return False

    print("Result of data-qubit preservation check:")
    if result["data_preserved"]:
        print("UNSAT: output data qubits match input data qubits.")
        return True

    print("SAT: counterexample — some data qubit input != output.")
    if result["data_mismatches"]:
        print("  Data qubits with input != output at indices:", result["data_mismatches"])
    return False


def find_bad_locations(qasm_path: str, stab_txt_path: str,num_gates: int):
    results, unsat_gates = forall_fault_exists_low_weight_per_gate(
        qasm_path,
        stab_txt_path,
        fault_mode="2q",
        flag_axis="z",
        flag_prefix="flagErr",
    )

    bad_locations_dict = [] # List to store bad locations for the current circuit
    qc = load_qasm(qasm_path)

    skip_gates = {"barrier", "barier", "measure", "reset", "id"}

    for i in range(num_gates):  # Iterate over gates in the subcircuit

        inst = qc.data[i]
        gate_name = inst.operation.name
        qubits = _gate_qubit_labels(qc, i)

        if gate_name in skip_gates:
            print(f"Gate index {i}: {gate_name} on {qubits} (skipped)")
            continue  # Skip non-unitary gates

        state, qc, site_info, groups = build_state_with_fault_after_gate(
            qasm_path,
            gate_index=i,
            fault_mode="2q"
        )
        
        # Extract fault variables
        fault_var = [v for k, v in site_info["vars"].items() if k.startswith("f")]

        # Extract qubit groups
        data_idxs = groups["data"]
        ancz_idxs = groups["ancZ"]
        flagx_idxs = groups["flagX"]
        ancx_idxs = groups["ancX"]
        flagz_idxs = groups["flagZ"]

        # Extract error components
        E_x = [state.qubits[i].x for i in data_idxs]
        E_z = [state.qubits[i].z for i in data_idxs]

        
        # Build stabilizer-equivalent errors
        Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path)

        # Build per-qubit error indicators
        b = [Or(xi, zi) for xi, zi in zip(Epx, Epz)]

        # Create a Z3 solver
        
  
        s = Solver()
        #s.add(ForAll(fault_var, Implies(Or(fault_var), Exists(gsel, PbLe([(bi, 1) for bi in b], 1)))))
        s.add(ForAll(gsel, PbGe([(bi, 1) for bi in b], 2)  ) )
        s.add(Or(fault_var))  # At most one fault
   
        # Check satisfiability
        if s.check() == sat:
            print(f"Gate index {i}: Bad location — {gate_name} on {qubits}")
        else:
            print(f"Gate index {i}: Safe — {gate_name} on {qubits}")
        if s.check() == sat:
            #print("Bad location found at gate index:", i)
            #print("qc.instructions ", qc.data[i].name, qc.data[i].qubits)
        
            # Store bad locations and gate numbers for the current subcircuit
            bad_locations_dict.append(i)
        
    # Update the gate count for the next subcircuit

    # Print the results
    if bad_locations_dict != []:

        print("Success : index of bad locations :")
        print(bad_locations_dict)
    else :print("There is no bad locaiton")

    

    return bad_locations_dict
"""

def find_bad_locations(qasm_path: str, stab_txt_path: str,num_gates: int):
    results, unsat_gates = forall_fault_exists_low_weight_per_gate(
        qasm_path,
        stab_txt_path,
        fault_mode="2q",
        flag_axis="z",
        flag_prefix="flagErr",
    )

    bad_locations_dict = [] # List to store bad locations for the current circuit
    qc = QuantumCircuit.from_qasm_file(qasm_path)



    for i in range(num_gates):  # Iterate over gates in the subcircuit
        
       
        if qc.data[i].name  in ["barier", "measure", "reset"]:
            print(f"Gate index {i}: " , qc.data[i].name )
            continue  # Skip non-unitary gates

        else:
            bad_locations_dict.append(i)

    # Print the results
    if bad_locations_dict != []:

        print("Success : index of bad locations :")
        print(bad_locations_dict)
    else :print("There is no bad locaiton")

    return bad_locations_dict
"""
def _flag_raised_counterexample_from_lits(lits, var_map):
    """Extract true fault vars from a SAT model for witness_mode flag_raised."""
    assign = model_to_z3_assignment(lits, var_map)
    faults = {k: v for k, v in assign.items() if v and k.startswith("faulty_")}
    return {"faults": faults, "assignment": assign}


def flag_raised_build_goal(
    E_x,
    E_z,
    F,
    acts,
    stab_txt_path: str,
    min_weight: int,
    fault_w: int,
    gsel_prefix: str = "fr_gsel",
):
    """
    Flag-raised counterexample goal (prove UNSAT):
      ∀ gsel : stab_equiv_weight(E, gsel) ≥ min_weight
      ∧ no flag raised
      ∧ at most fault_w fault sites

    The universal quantifier over gsel is expanded to 2^m disjunctive clauses
    so the goal is quantifier-free and CNF-exportable.
    """
    from itertools import product

    Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path, prefix=gsel_prefix)

    forall_constraints = []
    for bits in product((False, True), repeat=len(gsel)):
        subs = [(gsel[j], BoolVal(bits[j])) for j in range(len(gsel))]
        b_fixed = [
            Or(simplify(substitute(Epx[i], subs)), simplify(substitute(Epz[i], subs)))
            for i in range(len(Epx))
        ]
        forall_constraints.append(PbGe([(bi, 1) for bi in b_fixed], min_weight))

    g = Goal()
    for c in forall_constraints:
        g.add(c)
    if F:
        g.add(Not(Or(*F)))
    else:
        g.add(BoolVal(True))
    g.add(AtMost(*acts, fault_w))
    return g, gsel


def flag_raised_export_dimacs(
    E_x,
    E_z,
    F,
    acts,
    fault_var_names: List[str],
    stab_txt_path: str,
    min_weight: int,
    fault_w: int,
    cnf_dir,
    path_tag: str,
    gsel_prefix: Optional[str] = None,
    use_card2bv: bool = True,
):
    """Build flag-raised goal, convert to DIMACS, write path_tag.cnf + sidecars."""
    cnf_dir = Path(cnf_dir)
    cnf_dir.mkdir(parents=True, exist_ok=True)
    prefix = gsel_prefix or f"{path_tag}_gsel"

    g, gsel = flag_raised_build_goal(
        E_x, E_z, F, acts, stab_txt_path, min_weight, fault_w, gsel_prefix=prefix,
    )

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "path_tag": path_tag,
        "witness_mode": "flag_raised",
        "verify_pipeline": "flag_raised",
        "fault_var_names": fault_var_names,
        "num_fault_vars": len(fault_var_names),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "num_subgoals": len(cnf_files),
        "min_weight": min_weight,
        "fault_w": fault_w,
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
    }


def flag_raised_build_goal_dual(
    E_x,
    E_z,
    F,
    acts,
    stab_txt_path: str,
    min_weight: int,
    fault_w: int,
    gsel_prefix: str = "fr_gsel",
    *,
    log_txt_path: Optional[str] = None,
):
    """Flag-raised goal via dual Pauli enum + commute membership (stab ∪ logical)."""
    from stab_dual_encoding import forall_min_weight_constraints

    del gsel_prefix
    cs = forall_min_weight_constraints(
        E_x, E_z, stab_txt_path, min_weight, log_txt_path=log_txt_path,
    )
    g = Goal()
    for c in cs:
        g.add(c)
    if F:
        g.add(Not(Or(*F)))
    else:
        g.add(BoolVal(True))
    g.add(AtMost(*acts, fault_w))
    g._dual_num_pauli_constraints = len(cs)  # type: ignore[attr-defined]
    return g, []


def flag_raised_export_dimacs_dual(
    E_x,
    E_z,
    F,
    acts,
    fault_var_names: List[str],
    stab_txt_path: str,
    min_weight: int,
    fault_w: int,
    cnf_dir,
    path_tag: str,
    gsel_prefix: Optional[str] = None,
    use_card2bv: bool = True,
    *,
    log_txt_path: Optional[str] = None,
):
    """Export flag-raised goal with dual stab encoding."""
    cnf_dir = Path(cnf_dir).resolve()
    cnf_dir.mkdir(parents=True, exist_ok=True)
    prefix = gsel_prefix or f"{path_tag}_gsel"

    g, gsel = flag_raised_build_goal_dual(
        E_x, E_z, F, acts, stab_txt_path, min_weight, fault_w, gsel_prefix=prefix,
        log_txt_path=log_txt_path,
    )
    num_pauli = int(getattr(g, "_dual_num_pauli_constraints", 0))
    del gsel

    old_cwd = os.getcwd()
    os.chdir(cnf_dir)
    temp_files = []
    cnf_files = []
    solve_vmap = {}
    merged_cnf = None
    try:
        cnf_files, var_maps = build_dimacs(g, use_card2bv)
        if not cnf_files:
            raise RuntimeError(f"No CNF subgoals produced for {path_tag}")

        solve_cnf = cnf_files[0]
        solve_vmap = var_maps[0]
        if len(cnf_files) > 1:
            merged_cnf = f"{path_tag}_merged.cnf"
            solve_vmap = merge_dimacs_cnfs(cnf_files, merged_cnf)
            solve_cnf = merged_cnf

        final_cnf = cnf_dir / f"{path_tag}.cnf"
        solve_path = Path(solve_cnf)
        if not solve_path.is_absolute():
            solve_path = cnf_dir / solve_path
        if solve_path.resolve() != final_cnf.resolve():
            shutil.copy2(solve_path, final_cnf)

        temp_files = list(cnf_files)
        if merged_cnf and merged_cnf not in temp_files:
            temp_files.append(merged_cnf)
    finally:
        os.chdir(old_cwd)
        for p in temp_files:
            try:
                fp = cnf_dir / p if not os.path.isabs(p) else Path(p)
                if fp.exists() and fp.name != f"{path_tag}.cnf":
                    fp.unlink()
            except OSError:
                pass

    final_cnf = cnf_dir / f"{path_tag}.cnf"
    total_clauses, total_dimacs_vars = _count_cnf_stats(str(final_cnf))

    var_map_path = cnf_dir / f"{path_tag}_var_map.json"
    var_map_path.write_text(
        json.dumps({str(k): _z3_bool_name(v) for k, v in solve_vmap.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "path_tag": path_tag,
        "witness_mode": "flag_raised",
        "verify_pipeline": "flag_raised",
        "stab_encoding": "dual_pauli",
        "dual_membership": "commute_stab_and_logical",
        "num_pauli_constraints": num_pauli,
        "fault_var_names": fault_var_names,
        "num_fault_vars": len(fault_var_names),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "stab_txt_path": str(stab_txt_path),
        "log_txt_path": str(log_txt_path) if log_txt_path else None,
        "num_subgoals": len(cnf_files),
        "min_weight": min_weight,
        "fault_w": fault_w,
    }
    meta_path = cnf_dir / f"{path_tag}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    return {
        "path_tag": path_tag,
        "cnf_path": str(final_cnf),
        "total_clauses": total_clauses,
        "total_dimacs_vars": total_dimacs_vars,
        "num_fault_vars": len(fault_var_names),
        "sat_query_count": len(cnf_files),
        "stab_encoding": "dual_pauli",
        "num_pauli_constraints": num_pauli,
    }


def flag_raised_solve_with_cryptominisat(
    E_x,
    E_z,
    F,
    acts,
    fault_var_names: List[str],
    stab_txt_path: str,
    min_weight: int,
    fault_w: int,
    *,
    log_txt_path: Optional[str] = None,
    cms_bin: str | None = None,
    cms_extra_args: list | None = None,
    use_card2bv: bool = True,
    timeout_s: Optional[float] = None,
    cms_retries: int = 8,
    verbose: bool = True,
) -> Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]:
    """Export flag-raised goal to DIMACS and solve with external SAT solver."""
    import tempfile
    import os as _os

    path_tag = "flag_raised"
    encoding = (_os.environ.get("STAB_ENCODING") or "dual").strip().lower()
    if encoding != "coset":
        with tempfile.TemporaryDirectory(prefix="flag_raised_solve_") as td:
            flag_raised_export_dimacs_dual(
                E_x,
                E_z,
                F,
                acts,
                fault_var_names,
                stab_txt_path,
                min_weight,
                fault_w,
                td,
                path_tag,
                use_card2bv=use_card2bv,
                log_txt_path=log_txt_path,
            )
            st, counterexample, stats = uniqueness_solve_from_export(
                td,
                path_tag,
                cms_bin=cms_bin,
                cms_extra_args=cms_extra_args,
                timeout_s=timeout_s,
                cms_retries=cms_retries,
                verbose=verbose,
            )
        return st, counterexample, stats

    with tempfile.TemporaryDirectory(prefix="flag_raised_solve_") as td:
        flag_raised_export_dimacs(
            E_x,
            E_z,
            F,
            acts,
            fault_var_names,
            stab_txt_path,
            min_weight,
            fault_w,
            td,
            path_tag,
            use_card2bv=use_card2bv,
        )
        st, counterexample, stats = uniqueness_solve_from_export(
            td,
            path_tag,
            cms_bin=cms_bin,
            cms_extra_args=cms_extra_args,
            timeout_s=timeout_s,
            cms_retries=cms_retries,
            verbose=verbose,
        )
    return st, counterexample, stats


def check_flag_raised(
    qasm_path: str,
    stab_txt_path: str,
    num_gates: int,
    bad_locations_dict: List[int],
    *,
    t=1,
    w: Optional[int] = None,
    return_stats: bool = False,
    cms_bin: str | None = None,
    log_txt_path: Optional[str] = None,
):
    # w: max fault sites; flag must raise when stabilizer error weight > w (i.e. weight >= w+1).
    fault_w = w if w is not None else t
    min_weight = fault_w + 1
    state, qc, sites_info, groups = build_state_with_faults_after_gates(qasm_path ,bad_locations_dict, fault_mode="2q")    
    # Detailed symbolic state dump suppressed
    #print(sites_info)

    data_idxs = groups["data"]
    ancz_idxs = groups["ancZ"]
    flagx_idxs = groups["flagX"]
    ancx_idxs = groups["ancX"]
    flagz_idxs = groups["flagZ"]


    fault_var = [[v for k,v in s["vars"].items() if k.startswith("f")] for s in sites_info]
    #print("Injected fault variables:", fault_var)


    all_fault_vars = [f for sublist in fault_var for f in sublist]
    #print("All fault variables:", all_fault_vars)
    acts = [s["act"] for s in sites_info]
    
    #print("Data qubits:", data_idxs)
    #print("Ancilla qubits (Z-basis):", anc_idxs)
    #print("Flag qubits (X-basis):", flag_idxs)

    E_x = [state.qubits[i].x for i in data_idxs]
    E_z = [state.qubits[i].z for i in data_idxs]


    F = []
    if groups["flagX"] != []: F.extend([state.qubits[i].z for i in groups["flagX"]])
    if groups["flagZ"] != []: F.extend([state.qubits[i].x for i in groups["flagZ"]])
    
    if  groups["flagX"] == [] and groups["flagZ"] == [] : print("No flag qubits found.")


    

    Epx, Epz, gsel = build_stab_equiv_errors(E_x, E_z, stab_txt_path)
    fault_var_names = sorted({_z3_bool_name(v) for sublist in fault_var for v in sublist})

    st, counterexample, solve_stats = flag_raised_solve_with_cryptominisat(
        E_x,
        E_z,
        F,
        acts,
        fault_var_names,
        stab_txt_path,
        min_weight,
        fault_w,
        log_txt_path=log_txt_path,
        cms_bin=cms_bin or default_syndrome_sat_solver_bin(),
        verbose=not _QUIET,
    )
    sat_stats = {
        "sat_query_count": solve_stats.get("sat_query_count", 1),
        "sat_time_s": solve_stats.get("solver_runtime_seconds", 0.0),
        "dimacs_vars": solve_stats.get("total_dimacs_vars", 0),
        "total_clauses": solve_stats.get("total_clauses", 0),
        "peak_solver_rss_bytes": solve_stats.get("peak_solver_rss_bytes", 0),
    }

    if st == "unsat":
        if not _QUIET:
            print("Result:")
            print("Success : when high-weight error happens, at least one of the flag qubits raised")
        return (True, sat_stats) if return_stats else True
    if st == "sat" and counterexample:
        if not _QUIET:
            print("Result:")
            print("Failure : there exists a high-weight error where none of the flag qubits raised ")
            print("Counterexample model:")
            for name in sorted(counterexample.get("faults", {})):
                print(f"{name} = True")
        return (False, sat_stats) if return_stats else False
    if return_stats:
        return False, sat_stats
    return False

def check_generalised_syndrome_uniqueness(
    qasm_path: str,
    stab_txt_path: str,
    bad_locations : List[int]
    ):
    clean_qc = remove_flag_gates(qasm_path, save_path=None)
    #print("Original gates:", len(clean_qc.data))
    print("Bad locations being checked:", bad_locations)
    print("Processing the flag circuit")
    state, qc, sites_info, groups = build_state_with_faults_after_gates( qasm_path,bad_locations, fault_mode="2q")   
    
    
    print("#######################################")
    #print(sites_info)
    data_idxs = groups["data"]
    ancz_idxs = groups["ancZ"]
    flagx_idxs = groups["flagX"]
    ancx_idxs = groups["ancX"]
    flagz_idxs = groups["flagZ"]

    

    after_flag_state_X = [state.qubits[i].x for i in data_idxs]
    after_flag_state_Z = [state.qubits[i].z for i in data_idxs]

    fault_var = [[v for k,v in s["vars"].items() if k.startswith("f")] for s in sites_info]
    gate_fault_constr = [Or(f) for f in fault_var if f != []]



    flag_err_var =  []

    flag_err_var.extend(inject_on_flags(state, flagx_idxs, axis="z", prefix="flagErr"))


    flag_err_var.extend(inject_on_flags(state, flagz_idxs, axis="x", prefix="flagErr"))
    #print("flagz_idxs", flagz_idxs)
    #print("flag_err_var", flag_err_var)
    anc_err_var = [] 

    anc_err_var.extend(inject_on_flags(state, ancx_idxs, axis="z", prefix="ancErr"))
    anc_err_var.extend(inject_on_flags(state, ancz_idxs, axis="x", prefix="ancErr"))

    A = [state.qubits[i].z for i in ancx_idxs] + [state.qubits[i].x for i in ancz_idxs]
    F = [state.qubits[i].z for i in flagx_idxs] + [state.qubits[i].x for i in flagz_idxs]


    
    after_raw_state, snap = symbolic_propagate_with_resets( clean_qc ,state, track_steps= True)

    raw_anc_err_var = []

    raw_anc_err_var.extend(inject_on_flags(state, ancx_idxs, axis="z", prefix="raw_ancErr"))
    raw_anc_err_var.extend(inject_on_flags(state, ancz_idxs, axis="x", prefix="raw_ancErr"))

    all_fault = gate_fault_constr+ flag_err_var + anc_err_var+ raw_anc_err_var

    one_fault_constr = [ And (PbGe( [(f,1) for f in all_fault], 1), PbLe( [(f,1) for f in all_fault], 1))]



    var = [sub for sub in fault_var for sub in sub] + flag_err_var + anc_err_var + raw_anc_err_var

    ren_1 = make_renamer_from_symbols(var, "_p1")
    ren_2 = make_renamer_from_symbols(var, "_p2")

    one_fault_constr_p1 = primed_copy(one_fault_constr, ren_1)
    one_fault_constr_p2 = primed_copy(one_fault_constr, ren_2)

    #print("one_fault_constr_p1", one_fault_constr_p1)
    #print("one_fault_constr_p2", one_fault_constr_p2)

    A_1 = primed_copy(A, ren_1)
    A_2 = primed_copy(A, ren_2)
    F_1 = primed_copy(F, ren_1)
    F_2 = primed_copy(F, ren_2)

   
    

    

    E_x = [after_raw_state.qubits[i].x for i in data_idxs]
    E_z = [after_raw_state.qubits[i].z for i in data_idxs]

    raw_A = [after_raw_state.qubits[i].z for i in ancx_idxs] + [after_raw_state.qubits[i].x for i in ancz_idxs]

    E_x_1 = primed_copy(E_x, ren_1)
    E_z_1 = primed_copy(E_z, ren_1)
    E_x_2 = primed_copy(E_x, ren_2)
    E_z_2 = primed_copy(E_z, ren_2)

    raw_A_1 = primed_copy(raw_A, ren_1)
    raw_A_2 = primed_copy(raw_A, ren_2)

    gen_syn_1 = A_1 + F_1 + raw_A_1
    gen_syn_2 = A_2 + F_2 + raw_A_2
    
    
    stab_eq , gsel = exists_stab_equiv(E_x_1, E_z_1, E_x_2, E_z_2, stab_txt_path)




    same_syn =  And( *[x == y for x, y in zip(gen_syn_1, gen_syn_2)] )

    s = Solver()    
    s.add(same_syn, Not(Exists(gsel, stab_eq)))
    s.add(one_fault_constr_p1)
    s.add(one_fault_constr_p2)


    print("Result:")
    if s.check() == unsat :
       
        print("Success: every error maps to different generalised syndrome")

        return True 

    if s.check() == sat:
        print("Failure: there exists two different errors that map to the same generalised syndrome")
        print("The model that would cause different errors map to the same generalised syndrome:")
        for d in s.model().decls(): 
    
            val = s.model()[d]
            if str(val)  == "True": 
                print(f"{d.name()} = {val}")

        return False
    



def main():
    config = read_config()
    qasm_path = Path(config["qasm_path"])
    stab_txt_path = Path(config["stab_txt_path"])

    qc = QuantumCircuit.from_qasm_file(str(qasm_path))
    num_gates = sum(1 for inst in qc.data )
    #print(f"Number of gates in the circuit: {num_gates}")

    print("Step 1: Proving the circuit is etracts syndrome correctly when no fault in the circuit")
    step_1 = prove_syndrome_extractions(str(qasm_path), str(stab_txt_path))

    print("\nStep 2: Finding bad locations in the circuit")

    bad_location = find_bad_locations(str(qasm_path), str(stab_txt_path),num_gates)

    
    print("\nStep 3: Checking if flag is raised when high weight error occurs")
    step_3 = check_flag_raised(str(qasm_path), str(stab_txt_path),num_gates,bad_location)

    print("\nStep 4: Checking two non-degenerate error dont map to same generalised syndrome")
    step_4 = check_generalised_syndrome_uniqueness(str(qasm_path), str(stab_txt_path),bad_location)

    if step_1 and step_3 and step_4 :
        print("\nOverall Result: The flag circuit passes all the checks!")
        return True
    else :
        print("\nOverall Result: The flag circuit fails one or more checks.")
        return False


    


if __name__ == "__main__":
    main()
