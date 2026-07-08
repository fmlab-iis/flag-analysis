"""
dimacs_bridge.py

A small, practical bridge for:
  Z3 Bool formula  ->  CNF (Tseitin via Z3)  ->  DIMACS CNF
  CryptoMiniSat / MiniSat model (DIMACS ints) ->  assignment on *your original Z3 vars*

Key idea:
  DIMACS export introduces auxiliary variables (Tseitin). That's fine.
  To interpret a SAT model back in Z3 terms, you MUST keep a mapping:
      DIMACS variable id  <->  Z3 BoolRef

This file provides:
  - build_dimacs(phi): returns (dimacs_str, var2id, id2var, orig_vars)
  - parse_dimacs_model(text): returns set of signed ints from a SAT solver output
  - model_to_assignment(lits, id2var, orig_vars): returns dict {BoolRef: bool} restricted to orig_vars
"""

from __future__ import annotations
from typing import Dict, List, Tuple, Set, Iterable, Optional
import os, re, shutil, subprocess, sys, tempfile, time


from z3 import (
    BoolRef, BoolVal, Not, is_true, is_false, simplify, substitute,
    is_not, is_or, is_and, is_const, Goal, Tactic, Then
)

# ---------------------------
# Symbol collection
# ---------------------------

def _is_bool_var(e: BoolRef) -> bool:
    """
    True iff e looks like a boolean variable (uninterpreted 0-arity Bool).
    This excludes compounds like And(...), Xor(...), etc.
    """
    if not isinstance(e, BoolRef):
        return False
    if not is_const(e):
        return False
    # is_const includes True/False; exclude those
    if is_true(e) or is_false(e):
        return False
    return True


def collect_bool_symbols(expr: BoolRef) -> Set[BoolRef]:
    """Collect boolean variables appearing in expr."""
    seen: Set[BoolRef] = set()
    stack = [expr]
    while stack:
        e = stack.pop()
        if isinstance(e, BoolRef):
            if _is_bool_var(e):
                seen.add(e)
            for c in e.children():
                stack.append(c)
    return seen

def is_user_var(name: str) -> bool:
    return (
        name.startswith("r_") or
        name.startswith("s_") or
        name.startswith("f_")
    )

# ---------------------------
# CNF extraction (via Z3)
# ---------------------------

def _flatten_or(e: BoolRef) -> List[BoolRef]:
    """Return a flat list of literals from an Or tree (or [e] if not Or)."""
    if is_or(e):
        out = []
        for c in e.children():
            out.extend(_flatten_or(c))
        return out
    return [e]


def _is_lit(e: BoolRef) -> bool:
    """A CNF literal: v or Not(v) where v is a boolean var, or True/False."""
    if is_true(e) or is_false(e):
        return True
    if _is_bool_var(e):
        return True
    if is_not(e) and _is_bool_var(e.children()[0]):
        return True
    return False


def _clause_to_lits(cl: BoolRef) -> List[BoolRef]:
    """
    Convert a CNF clause into list of literals.
    Clause can be:
      - Or(lit, lit, ...)
      - lit
    """
    cl = simplify(cl)
    if is_true(cl):
        return [BoolVal(True)]  # tautology clause
    if is_false(cl):
        return [BoolVal(False)]  # empty/unsat clause representation

    if is_or(cl):
        lits = _flatten_or(cl)
    else:
        lits = [cl]

    # sanity check: Z3's tseitin-cnf should give us literals here
    for l in lits:
        if not _is_lit(l):
            raise ValueError(f"Non-literal in CNF clause: {l}")
    return lits


def to_cnf_clauses(
    phi: BoolRef,
    *,
    use_pb2bv: bool = False,
    use_card2bv: bool = False,
) -> List[List[BoolRef]]:
    """
    Use Z3 tactics to produce CNF clauses for `phi`.

    If your formula uses pseudo-Boolean constraints (PbEq/PbLe/PbGe) or
    cardinality constraints (AtMost/AtLeast), Z3 may print DIMACS that
    depends on those higher-level constructs.

    Setting:
      - use_pb2bv=True  enables the `pb2bv` lowering pass (handles PbEq/PbLe/PbGe)
      - use_card2bv=True enables the `card2bv` lowering pass (handles AtMost/AtLeast)

    Returns:
      list of CNF clauses; each clause is a list of literals (BoolRef or Not(BoolRef)).
    """
    g = Goal()
    g.add(phi)

    # Build tactic pipeline.
    steps = [
        "simplify",
        "propagate-values",
        "solve-eqs",
        "elim-uncnstr",
    ]
    if use_pb2bv:
        steps.append("pb2bv")
    if use_card2bv:
        steps.append("card2bv")

    # `bit-blast` is harmless for pure Bool, and helpful if pb/card lowering
    # introduces bit-vectors.
    steps.extend(["bit-blast", "tseitin-cnf"])

    cnf_goal = Then(*steps)(g)

    # cnf_goal is a Goal; turn it into a list of clause expressions
    e = simplify(cnf_goal.as_expr())
    fs = e.children() if is_and(e) else [e]

    clauses: List[List[BoolRef]] = []
    for f in fs:
        f = simplify(f)
        if is_true(f):
            continue
        clauses.append(_clause_to_lits(f))
    return clauses


# ---------------------------
# DIMACS printing
# ---------------------------

def _lit_to_dimacs_int(lit: BoolRef, var2id: Dict[BoolRef, int]) -> int:
    """Convert a literal to signed DIMACS int using var2id."""
    lit = simplify(lit)
    if is_true(lit):
        # Clause is satisfied; caller should typically drop whole clause, but keep safe:
        return 0
    if is_false(lit):
        # False literal: represent impossible literal by 0 sentinel (caller will handle)
        return 0

    if _is_bool_var(lit):
        return var2id[lit]
    if is_not(lit) and _is_bool_var(lit.children()[0]):
        return -var2id[lit.children()[0]]

    raise ValueError(f"Not a DIMACS literal: {lit}")



# ---------------------------
# SAT solver output parsing
# ---------------------------

def parse_dimacs_model(text: str) -> Set[int]:
    """
    Parse CryptoMiniSat-style output.
    Returns a set of signed ints (literals) that are assigned True in the model.
    Typical formats:
      - lines starting with 'v ' followed by ints ending with 0
      - may include 's SATISFIABLE'
    """
    lits: Set[int] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("v") or line.startswith("V"):
            parts = line[1:].strip().split()
            for p in parts:
                try:
                    k = int(p)
                except ValueError:
                    continue
                if k == 0:
                    continue
                lits.add(k)
    return lits


def model_to_assignment(
    lits_true: Set[int],
    id2var: Dict[int, BoolRef],
    orig_vars: Optional[Set[BoolRef]] = None,
    default_false: bool = True,
) -> Dict[BoolRef, bool]:
    """
    Turn DIMACS model literals into an assignment on Z3 BoolRefs.

    - lits_true: set of signed ints (positive means var=True, negative means var=False)
    - id2var: DIMACS id -> BoolRef mapping from build_dimacs
    - orig_vars: if provided, restrict returned assignment to these vars only

    If a variable is missing from the SAT output:
      - default_false=True  -> treat as False (common)
      - default_false=False -> omit it
    """
    # Build a map id -> bool
    id_val: Dict[int, bool] = {}
    for lit in lits_true:
        vid = abs(lit)
        val = (lit > 0)
        # If both +x and -x appear, that's inconsistent solver output; last wins.
        id_val[vid] = val

    out: Dict[BoolRef, bool] = {}
    for vid, var in id2var.items():
        if orig_vars is not None and var not in orig_vars:
            continue
        if vid in id_val:
            out[var] = id_val[vid]
        else:
            if default_false:
                out[var] = False
    return out


# ---------------------------
# Convenience: apply assignment to Z3 formula
# ---------------------------

def eval_z3_bool(expr: BoolRef, assignment: Dict[BoolRef, bool], default_false: bool = True) -> bool:
    """
    Evaluate a Z3 Bool expr under a BoolRef->bool assignment.
    Missing vars default to False if default_false=True.
    """
    syms = collect_bool_symbols(expr)
    subs = []
    for s in syms:
        if s in assignment:
            subs.append((s, BoolVal(assignment[s])))
        elif default_false:
            subs.append((s, BoolVal(False)))
    return is_true(simplify(substitute(expr, subs)))


__all__ = [
    "collect_bool_symbols",
    "to_cnf_clauses",
    "build_dimacs",
    "parse_dimacs_model",
    "model_to_assignment",
    "eval_z3_bool",
]

# dimacs_bridge.py
# ----------------
# Z3 -> DIMACS -> CryptoMiniSat -> back to Z3 vars

import os, re, shutil, subprocess
from typing import Dict, List, Optional, Tuple
from z3 import BoolRef, Goal, Then




# --------------------------------------------------
# Z3 Goal → DIMACS (with var map)
# --------------------------------------------------
def build_dimacs(goal: Goal, use_card2bv: bool):
    t = Then(
        "simplify",
        "propagate-values",
        "solve-eqs",
        "elim-uncnstr",
        "pb2bv",
        "card2bv" if use_card2bv else "skip",
        "bit-blast",
        "tseitin-cnf",
    )
    subgoals = list(t(goal))

    dimacs_files = []
    var_maps = []

    for i, sg in enumerate(subgoals):
        dimacs = sg.dimacs()
        path = f"subgoal_{i}.cnf"
        with open(path, "w") as f:
            f.write(dimacs)

        # extract variable map from comments
        var_map = {}
        for line in dimacs.splitlines():
            if line.startswith("c var"):
                _, _, name, num = line.split()
                var_map[int(num)] = name

        dimacs_files.append(path)
        var_maps.append(var_map)

    return dimacs_files, var_maps


def default_sat_solver_bin() -> str:
    """External SAT solver for exported CNFs (override with DIMACS_SOLVER_BIN)."""
    return os.environ.get("DIMACS_SOLVER_BIN", "minisat")


def resolve_sat_solver_binary(solver_bin: Optional[str] = None) -> str:
    """Resolve MiniSat, CryptoMiniSat, or another DIMACS solver on PATH."""
    name = solver_bin or default_sat_solver_bin()
    if os.path.sep in name:
        if not (os.path.exists(name) and os.access(name, os.X_OK)):
            raise FileNotFoundError(f"SAT solver binary path is not executable: {name}")
        return name

    found = shutil.which(name)
    if found is None:
        fallbacks: Tuple[str, ...] = ()
        if name in ("minisat", "minisat2"):
            fallbacks = (
                "minisat",
                "minisat2",
                "/usr/bin/minisat",
                "/usr/local/bin/minisat",
                "/opt/homebrew/bin/minisat",
                "cryptominisat5",
                "/usr/local/bin/cryptominisat5",
                "/opt/homebrew/bin/cryptominisat5",
            )
        elif name in ("cryptominisat5", "cryptominisat"):
            fallbacks = (
                "cryptominisat5",
                "/opt/homebrew/bin/cryptominisat5",
                "/usr/local/bin/cryptominisat5",
            )
        for cand in fallbacks:
            if os.path.sep in cand:
                if os.path.exists(cand) and os.access(cand, os.X_OK):
                    found = cand
                    break
            else:
                found = shutil.which(cand)
                if found:
                    break

    if found is None:
        raise FileNotFoundError(
            f"SAT solver binary not found: '{name}'.\n"
            "Install MiniSat: sudo apt-get install minisat  (or brew install minisat)\n"
            "Or CryptoMiniSat: sudo apt-get install cryptominisat\n"
            "Override with DIMACS_SOLVER_BIN=minisat or DIMACS_SOLVER_BIN=cryptominisat5"
        )
    return found


def resolve_cryptominisat_binary(cms_bin: str = "cryptominisat5") -> str:
    """Backward-compatible alias for resolve_sat_solver_binary."""
    return resolve_sat_solver_binary(cms_bin)


def default_syndrome_sat_solver_bin() -> str:
    """External SAT solver for syndrome-extraction checks (override with SYNDROME_SAT_SOLVER_BIN)."""
    return os.environ.get("SYNDROME_SAT_SOLVER_BIN", "cryptominisat5")


def z3_expr_is_unsat(
    expr: BoolRef,
    *,
    solver_bin: Optional[str] = None,
    timeout_s: Optional[float] = None,
    use_card2bv: bool = False,
) -> bool:
    """Return True iff expr is UNSAT, via Z3 CNF export + external SAT solver."""
    expr = simplify(expr)
    if is_false(expr):
        return True
    if is_true(expr):
        return False

    goal = Goal()
    goal.add(expr)
    cnf_files, _var_maps = build_dimacs(goal, use_card2bv=use_card2bv)
    if not cnf_files:
        return False

    solve_cnf = cnf_files[0]
    cleanup = list(cnf_files)
    if len(cnf_files) > 1:
        merged_fd, merged_path = tempfile.mkstemp(suffix=".cnf", prefix="syndrome_")
        os.close(merged_fd)
        merge_dimacs_cnfs(cnf_files, merged_path)
        solve_cnf = merged_path
        cleanup.append(merged_path)

    solver_exec = resolve_sat_solver_binary(solver_bin or default_syndrome_sat_solver_bin())
    try:
        status, _, _, _, _ = run_dimacs_solver(
            solve_cnf, solver_exec, timeout_s=timeout_s,
        )
    finally:
        for path in cleanup:
            try:
                os.remove(path)
            except OSError:
                pass
    return status == "unsat"


def run_dimacs_solver(
    cnf_path: str,
    solver_exec: str,
    timeout_s: Optional[float] = None,
    extra_args: Optional[list] = None,
) -> Tuple[str, Optional[List[int]], str, float, Optional[int]]:
    """
    Run an external DIMACS solver (MiniSat, CryptoMiniSat, etc.).

    Returns (status, model_lits, out_str, elapsed_s, peak_rss_bytes) with out_str CLEAN:
      - only 's ...' and 'v ...' lines (drops all 'c ...' stats)
    """
    cmd = [solver_exec]
    if extra_args:
        cmd += list(map(str, extra_args))
    cmd.append(cnf_path)

    timed_cmd = cmd
    use_time_wrapper = sys.platform == "darwin"
    if use_time_wrapper:
        timed_cmd = ["/usr/bin/time", "-l"] + cmd

    start = time.perf_counter()

    try:
        p = subprocess.run(timed_cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        elapsed_s = time.perf_counter() - start
        return "unknown", None, "s UNKNOWN\n", elapsed_s, None

    elapsed_s = time.perf_counter() - start

    raw = (p.stdout or "") + "\n" + (p.stderr or "")
    peak_rss_bytes: Optional[int] = None
    if use_time_wrapper:
        match = re.search(r"(\d+)\s+maximum resident set size", raw)
        if not match:
            match = re.search(r"maximum resident set size\s+(\d+)", raw)
        if not match:
            match = re.search(r"(\d+)\s+peak memory footprint", raw)
        if match:
            peak_rss_bytes = int(match.group(1))
    else:
        try:
            import resource

            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            rss = usage.ru_maxrss
            peak_rss_bytes = int(rss * 1024)
        except Exception:
            peak_rss_bytes = None

    if peak_rss_bytes is not None and peak_rss_bytes <= 0:
        peak_rss_bytes = None

    kept_lines = []
    model_lits: List[int] = []

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("c "):
            continue  # DROP stats/comments
        if line.startswith("s "):
            kept_lines.append(line)
        if line.startswith("v "):
            kept_lines.append(line)
            for tok in line.split()[1:]:
                if tok == "0":
                    continue
                if re.fullmatch(r"-?\d+", tok):
                    model_lits.append(int(tok))

    status = "unknown"
    for ln in kept_lines:
        if ln.startswith("s ") and "UNSAT" in ln:
            status = "unsat"
            break
        if ln.startswith("s ") and "SAT" in ln:
            status = "sat"
            break

    if status == "unsat":
        model_lits = []

    clean_out = "\n".join(kept_lines) + ("\n" if kept_lines else "")
    summary_lines = [f"[stats] solver_wall_time_seconds={elapsed_s:.6f}"]
    if peak_rss_bytes is not None:
        summary_lines.append(f"[stats] solver_peak_rss_bytes={peak_rss_bytes}")
    clean_out += "\n".join(summary_lines) + "\n"
    return status, (model_lits if model_lits else None), clean_out, elapsed_s, peak_rss_bytes


def run_cryptominisat(
    cnf_path: str,
    cms_exec: str,
    timeout_s: Optional[float] = None,
    cms_extra_args: Optional[list] = None,
) -> Tuple[str, Optional[List[int]], str, float, Optional[int]]:
    """Backward-compatible alias for run_dimacs_solver."""
    return run_dimacs_solver(
        cnf_path, cms_exec, timeout_s=timeout_s, extra_args=cms_extra_args,
    )


def _parse_z3_dimacs_varmap(dimacs_text: str) -> Dict[int, str]:
    """Parse Z3's DIMACS comments of the form: 'c <id> <name>' (best-effort)."""
    vmap: Dict[int, str] = {}
    for line in dimacs_text.splitlines():
        line = line.strip()
        if not line.startswith("c"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        # common format: c <int> <name>
        try:
            idx = int(parts[1])
        except ValueError:
            continue
        name = parts[2]
        vmap[idx] = name
    return vmap


def merge_dimacs_cnfs(cnf_paths: List[str], out_path: str) -> Dict[int, object]:
    """
    Merge multiple DIMACS CNF files into one file with unified variable numbering.

    Variables are identified by Z3 name (from ``c <id> <name>`` comments) so the
    same logical variable keeps one id across subgoals.

    Returns:
        merged vmap: {dimacs_int -> z3.BoolRef}
    """
    from z3 import Bool

    global_name_to_id: Dict[str, int] = {}
    merged_clauses: List[List[int]] = []

    def global_id_for_name(name: str) -> int:
        if name not in global_name_to_id:
            global_name_to_id[name] = len(global_name_to_id) + 1
        return global_name_to_id[name]

    def remap_lit(lit: int, id_to_name: Dict[int, str], cnf_tag: str) -> int:
        local = abs(lit)
        name = id_to_name.get(local)
        if name is None:
            name = f"{cnf_tag}_anon_{local}"
        gid = global_id_for_name(name)
        return gid if lit > 0 else -gid

    for sg_idx, cnf_path in enumerate(cnf_paths):
        with open(cnf_path, "r", encoding="utf-8") as f:
            text = f.read()
        id_to_name = _parse_z3_dimacs_varmap(text)
        tag = os.path.splitext(os.path.basename(cnf_path))[0]

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("c") or line.startswith("p cnf"):
                continue
            lits = [int(x) for x in line.split() if x != "0"]
            if not lits:
                continue
            merged_clauses.append([
                remap_lit(lit, id_to_name, f"{tag}_{sg_idx}") for lit in lits
            ])

    num_vars = len(global_name_to_id)
    with open(out_path, "w", encoding="utf-8") as f:
        for name, idx in sorted(global_name_to_id.items(), key=lambda kv: kv[1]):
            f.write(f"c {idx} {name}\n")
        f.write(f"p cnf {num_vars} {len(merged_clauses)}\n")
        for clause in merged_clauses:
            f.write(" ".join(str(x) for x in clause) + " 0\n")

    return {idx: Bool(name) for name, idx in global_name_to_id.items()}


from z3 import Goal, Then, Bool
def build_dimacs(goal: Goal, use_card2bv: bool = True) -> Tuple[List[str], List[Dict[int, object]]]:
    """Convert a Z3 Goal to one-or-more CNF subgoals, write DIMACS files, and return per-subgoal var maps.

    Returns:
      cnf_files: list of written CNF paths
      var_maps:  list of {dimacs_int -> z3.BoolRef} maps (best-effort)
    """
    # tactic pipeline
    t = Then(
        "simplify",
        "propagate-values",
        "solve-eqs",
        "elim-uncnstr",
        "pb2bv",
        "card2bv",
        "bit-blast",
        "tseitin-cnf",
    ) if use_card2bv else Then(
        "simplify",
        "propagate-values",
        "solve-eqs",
        "elim-uncnstr",
        "pb2bv",
        "bit-blast",
        "tseitin-cnf",
    )

    subgoals = list(t(goal))
    if not subgoals:
        return [], []

    cnf_files: List[str] = []
    var_maps: List[Dict[int, object]] = []

    for i, sg in enumerate(subgoals):
        # Z3 Goal supports .dimacs() once in CNF form (best-effort)
        dimacs = sg.dimacs()
        cnf_path = f"uniq_sub{i}.cnf"
        with open(cnf_path, "w") as f:
            f.write(dimacs)
        cnf_files.append(cnf_path)

        # build a best-effort mapping dimacs_int -> Bool(name)
        name_map = _parse_z3_dimacs_varmap(dimacs)
        vmap: Dict[int, object] = {k: Bool(v) for k, v in name_map.items()}
        var_maps.append(vmap)

    return cnf_files, var_maps


def model_to_z3_assignment(model_lits: List[int], var_map: Dict[int, object]) -> Dict[str, bool]:
    """Map DIMACS model literals back to Z3 Bool names using a dimacs->Bool map."""
    out: Dict[str, bool] = {}
    for lit in model_lits:
        v = abs(int(lit))
        if v not in var_map:
            continue
        b = var_map[v]
        try:
            name = b.decl().name()
        except Exception:
            name = str(b)
        out[name] = (lit > 0)
    return out


def pretty_print_z3_assignment(assign: Dict[str, bool], *, only_prefixes: Optional[Tuple[str, ...]] = None) -> str:
    """Pretty print mapped assignment; optionally filter by variable-name prefixes."""
    items = sorted(assign.items(), key=lambda kv: kv[0])
    if only_prefixes:
        items = [(k, v) for (k, v) in items if k.startswith(only_prefixes)]
    return "\n".join(f"{k} = {v}" for k, v in items) + ("\n" if items else "")

def pretty_print_true_z3_vars(model_lits, var_map, *, strip_suffix=True, do_print= False):
    """
    From DIMACS model literals + var_map, print only user vars that are True,
    and return two dicts:
        p1_dict: {base_name: True}
        p2_dict: {base_name: True}

    Assumes:
      - model_to_z3_true_vars(model_lits, var_map) -> iterable[str] or iterable[z3 Bool] names
      - is_user_var(name: str) -> bool  (filters out k!, and!, at-most-..., etc.)

    strip_suffix:
      - If True, store keys without the '_p1'/'_p2' suffix.
      - If False, store full var names as keys.
    """
    true_vars = model_to_z3_true_vars(model_lits, var_map) or []

    p1_dict = {}
    p2_dict = {}

    for v in true_vars:
        # normalize to string name
        name = v.decl().name() if hasattr(v, "decl") else str(v)

        # filter internals
        if not is_user_var(name):
            continue

        if name.endswith("_p1"):
            key = name[:-3] if strip_suffix else name
            p1_dict[key] = True
        elif name.endswith("_p2"):
            key = name[:-3] if strip_suffix else name
            p2_dict[key] = True
        else:
            # if you ever want "other" vars, you can add a third dict here
            pass

    if do_print:
        if not p1_dict and not p2_dict:
            print("(no user variables are True)")
        else:
            if p1_dict:
                print("=== p1 True vars ===")
                for k in sorted(p1_dict):
                    print(k if strip_suffix else f"{k}")
            else:
                print("=== p1 True vars ===")
                print("(none)")

            if p2_dict:
                print("=== p2 True vars ===")
                for k in sorted(p2_dict):
                    print(k if strip_suffix else f"{k}")
            else:
                print("=== p2 True vars ===")
                print("(none)")

    return p1_dict, p2_dict
def model_to_z3_true_vars(model_lits, var_map):
    """
    model_lits : list[int]      # e.g. [1, -2, 3, -4]
    var_map    : dict[int, BoolRef]  # DIMACS id -> Z3 Bool

    Returns:
        dict[str, bool] mapping variable name -> True
        (only includes variables assigned True)
    """
    true_vars = {}

    for lit in model_lits:
        if lit > 0:  # TRUE in DIMACS
            v = var_map.get(lit)
            if v is not None:
                true_vars[v.decl().name()] = True

    return true_vars

