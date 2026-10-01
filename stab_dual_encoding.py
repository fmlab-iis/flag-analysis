"""Dual stab-equivalence encoding: low-weight Pauli enum + commute membership.

Encodes  ∀g: wt(E ⊕ S(g)) ≥ min_weight
as       ⋀_{P: wt(P) < min_weight} (E ⊕ P ∉ ⟨S⟩)

Membership in ⟨S⟩ uses the commute characterization:
  v ∈ ⟨S⟩  ⟺  v commutes with all stabilizers AND all logical operators
  v ∉ ⟨S⟩  ⟺  anticommutes with at least one of (stab ∪ logical)

(Same idea as pauli_not_in_stabilizer in flag_analysis.py.)
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from math import comb
from typing import Iterable, List, Sequence, Tuple

from z3 import BoolRef, BoolVal, Or, Xor

SymplecticGen = Tuple[List[int], List[int]]
PauliXZ = Tuple[Tuple[int, ...], Tuple[int, ...]]


def paulis_weight_leq(n: int, max_weight: int) -> Iterable[PauliXZ]:
    """All n-qubit Paulis with Hamming weight (non-I count) in 0..max_weight."""
    if max_weight < 0:
        return
    yield (tuple([0] * n), tuple([0] * n))
    if max_weight == 0:
        return
    non_i = ((1, 0), (0, 1), (1, 1))
    for w in range(1, max_weight + 1):
        for support in combinations(range(n), w):
            choices = [0] * w
            while True:
                Px = [0] * n
                Pz = [0] * n
                for k, q in enumerate(support):
                    x, z = non_i[choices[k]]
                    Px[q] = x
                    Pz[q] = z
                yield (tuple(Px), tuple(Pz))
                i = 0
                while i < w:
                    choices[i] += 1
                    if choices[i] < 3:
                        break
                    choices[i] = 0
                    i += 1
                if i == w:
                    break


def _xor_bits(bits: List[BoolRef]) -> BoolRef:
    if not bits:
        return BoolVal(False)
    acc = bits[0]
    for b in bits[1:]:
        acc = Xor(acc, b)
    return acc


def _v_bit_x(E_x: Sequence[BoolRef], Px: Sequence[int], j: int) -> BoolRef:
    return E_x[j] if not Px[j] else Xor(E_x[j], BoolVal(True))


def _v_bit_z(E_z: Sequence[BoolRef], Pz: Sequence[int], j: int) -> BoolRef:
    return E_z[j] if not Pz[j] else Xor(E_z[j], BoolVal(True))


def symplectic_product_v_gen(
    E_x: Sequence[BoolRef],
    E_z: Sequence[BoolRef],
    Px: Sequence[int],
    Pz: Sequence[int],
    Sx: Sequence[int],
    Sz: Sequence[int],
) -> BoolRef:
    """Symplectic product ⟨E⊕P, S⟩ = ⊕_j (v_x Sz + v_z Sx) over GF(2)."""
    bits: List[BoolRef] = []
    n = len(E_x)
    for j in range(n):
        if Sz[j]:
            bits.append(_v_bit_x(E_x, Px, j))
        if Sx[j]:
            bits.append(_v_bit_z(E_z, Pz, j))
    return _xor_bits(bits)


def not_in_stabilizer_commute(
    E_x: Sequence[BoolRef],
    E_z: Sequence[BoolRef],
    Px: Sequence[int],
    Pz: Sequence[int],
    check_ops: Sequence[SymplecticGen],
) -> BoolRef:
    """E⊕P ∉ ⟨S⟩ via: anticommutes with at least one of stab ∪ logical.

    Matches pauli_not_in_stabilizer(E⊕P, stab, log): Or over symplectic products.
    """
    if not check_ops:
        # No checks → only I is "in ⟨S⟩"; require E⊕P ≠ 0
        diffs = [
            Or(_v_bit_x(E_x, Px, i), _v_bit_z(E_z, Pz, i))
            for i in range(len(E_x))
        ]
        return Or(*diffs) if diffs else BoolVal(False)
    return Or(
        *[
            symplectic_product_v_gen(E_x, E_z, Px, Pz, Sx, Sz)
            for Sx, Sz in check_ops
        ]
    )


# Back-compat alias used by older call sites / probes
def not_stab_equiv_constraint(
    E_x: Sequence[BoolRef],
    E_z: Sequence[BoolRef],
    Px: Sequence[int],
    Pz: Sequence[int],
    check_ops: Sequence[SymplecticGen],
) -> BoolRef:
    return not_in_stabilizer_commute(E_x, E_z, Px, Pz, check_ops)


@lru_cache(maxsize=32)
def _check_ops_for_paths(
    stab_txt_path: str, log_txt_path: str
) -> Tuple[Tuple[Tuple[Tuple[int, ...], Tuple[int, ...]], ...], int]:
    from flag_analysis import load_symplectic_txt

    gens = load_symplectic_txt(stab_txt_path)
    logs = load_symplectic_txt(log_txt_path) if log_txt_path else []
    n = len(gens[0][0]) if gens else (len(logs[0][0]) if logs else 0)
    ops = tuple(
        (tuple(Sx), tuple(Sz))
        for Sx, Sz in (list(gens) + list(logs))
    )
    return ops, n


def forall_min_weight_constraints(
    E_x: Sequence[BoolRef],
    E_z: Sequence[BoolRef],
    stab_txt_path: str,
    min_weight: int,
    log_txt_path: str | None = None,
) -> List[BoolRef]:
    """Constraints ensuring min stab-coset weight ≥ min_weight.

    For each Pauli P with wt(P) ≤ min_weight-1, assert E⊕P ∉ ⟨S⟩ using
    commute-with-(stabilizers ∪ logicals).
    """
    if min_weight <= 0:
        return []
    if not log_txt_path:
        raise ValueError(
            "dual commute membership requires log_txt_path "
            "(stabilizers ∪ logicals)"
        )
    ops_t, n = _check_ops_for_paths(str(stab_txt_path), str(log_txt_path))
    if len(E_x) != n:
        raise ValueError(f"E length {len(E_x)} != code length {n}")
    check_ops: List[SymplecticGen] = [
        (list(Sx), list(Sz)) for Sx, Sz in ops_t
    ]
    max_p_wt = min_weight - 1
    out: List[BoolRef] = []
    for Px, Pz in paulis_weight_leq(n, max_p_wt):
        out.append(not_in_stabilizer_commute(E_x, E_z, Px, Pz, check_ops))
    return out


def count_paulis_weight_leq(n: int, max_weight: int) -> int:
    """Number of n-qubit Paulis with weight ≤ max_weight (including I)."""
    if max_weight < 0:
        return 0
    total = 1
    for w in range(1, max_weight + 1):
        total += comb(n, w) * (3**w)
    return total
