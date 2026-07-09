"""
DPI Sentinel aggregator — Merkle tree (Milestone 3).

Deliberately hand-rolled, no external Merkle library: the tree construction
IS the thing being demonstrated, so it should be readable end to end.

Conventions (must match everywhere — checkpoint creation, inclusion proofs,
and verify_log.py):
  - Leaves are the hex `entry_hash` strings of a contiguous LogEntry batch,
    in ascending sequence order.
  - Two nodes are combined by concatenating their raw bytes (bytes.fromhex)
    and taking sha256, returned as hex:  parent = sha256(L_bytes + R_bytes).
  - If a level has an odd number of nodes, the last node is duplicated
    (hashed with itself) to form its parent. This is the common Bitcoin-style
    convention; it keeps the tree binary without inventing a padding value.
  - A single-leaf batch has that leaf as its own root.
"""

import hashlib


def _combine(left_hex: str, right_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(left_hex) + bytes.fromhex(right_hex)).hexdigest()


def build_levels(leaf_hashes: list[str]) -> list[list[str]]:
    """
    Build every level of the tree, bottom (leaves) to top (root).
    levels[0] == leaves, levels[-1] == [root]. Returned so both the root and
    inclusion proofs can be read off the same structure.
    """
    if not leaf_hashes:
        raise ValueError("cannot build a Merkle tree over zero leaves")

    levels = [list(leaf_hashes)]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        nxt = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]  # duplicate last if odd
            nxt.append(_combine(left, right))
        levels.append(nxt)
    return levels


def compute_root(leaf_hashes: list[str]) -> str:
    return build_levels(leaf_hashes)[-1][0]


def inclusion_proof(leaf_hashes: list[str], index: int) -> list[dict]:
    """
    Sibling hashes needed to recompute the root from leaf `index`, bottom to
    top. Each step is {"sibling": hex, "position": "left"|"right"} where
    `position` is the side the SIBLING sits on (so the verifier knows the
    concatenation order). With just the leaf and this list — not the whole
    log — anyone can rebuild the path to the root.
    """
    if not (0 <= index < len(leaf_hashes)):
        raise IndexError("leaf index out of range for this batch")

    levels = build_levels(leaf_hashes)
    proof = []
    idx = index
    for level in levels[:-1]:  # every level except the root
        if idx % 2 == 0:
            sibling_idx = idx + 1 if idx + 1 < len(level) else idx  # odd -> self-paired
            position = "right"
        else:
            sibling_idx = idx - 1
            position = "left"
        proof.append({"sibling": level[sibling_idx], "position": position})
        idx //= 2
    return proof


def verify_proof(leaf_hash: str, proof: list[dict], expected_root: str) -> bool:
    """Recompute the root from a leaf + proof and compare. This is exactly
    what a third party (or the Milestone 4 verify page) would run."""
    computed = leaf_hash
    for step in proof:
        if step["position"] == "right":
            computed = _combine(computed, step["sibling"])
        else:
            computed = _combine(step["sibling"], computed)
    return computed == expected_root
