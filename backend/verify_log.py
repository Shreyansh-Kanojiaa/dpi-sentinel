"""
DPI Sentinel — tamper-evident log verifier (Milestone 3).

Run manually:
    python verify_log.py

Two independent checks, reported separately:

  1. CHAIN INTEGRITY — walk every LogEntry in sequence order, recompute
     entry_hash = sha256(prev_hash + payload) with the SAME canonical
     serializer the aggregator used, and confirm it matches the stored
     value. Also confirm sequence numbers are gapless and each row's
     prev_hash equals the previous row's entry_hash. Reports the exact
     sequence_number where it first breaks, if any.

  2. CHECKPOINT INTEGRITY — for each Checkpoint, recompute the Merkle root
     from the LogEntry batch AS CURRENTLY STORED IN THE DB, then compare it
     against THREE things:
        (a) the DB's own stored merkle_root,
        (b) the aggregator's signature over that root,
        (c) the merkle_root in the git-committed checkpoint file.
     If (a) disagrees with the recomputed root, the log rows were edited
     after the checkpoint was taken. If the DB root and the git root
     disagree, the database was rewritten after it was externally anchored —
     the git copy is the one the operator couldn't quietly change, so the DB
     is provably the tampered side.

Exit code is non-zero if any check fails, so this can gate CI / a cron.
"""

import json
import os
import sys
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import merkle
from log_chain import GENESIS_PREV_HASH, compute_entry_hash
from models import LogEntry, Checkpoint

DB_URL = os.environ.get("DB_URL", "sqlite:///./dpi_sentinel.db")
CHECKPOINT_REPO_PATH = os.environ.get("CHECKPOINT_REPO_PATH", "").strip()

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg):
    print(f"{GREEN}  OK {RESET} {msg}")


def fail(msg):
    print(f"{RED} FAIL{RESET} {msg}")


def warn(msg):
    print(f"{YELLOW} WARN{RESET} {msg}")


def verify_chain(db) -> bool:
    print("\n=== CHAIN INTEGRITY ===")
    entries = db.query(LogEntry).order_by(LogEntry.sequence_number.asc()).all()
    if not entries:
        warn("log is empty — nothing to verify yet")
        return True

    clean = True
    expected_prev = GENESIS_PREV_HASH
    expected_seq = 1

    for e in entries:
        if e.sequence_number != expected_seq:
            fail(f"sequence gap/disorder: expected seq {expected_seq}, found {e.sequence_number}")
            clean = False
            expected_seq = e.sequence_number  # resync so we can keep reporting

        if e.prev_hash != expected_prev:
            fail(
                f"seq {e.sequence_number}: prev_hash does not chain to the previous entry "
                f"(stored prev_hash={e.prev_hash[:16]}…, expected {expected_prev[:16]}…)"
            )
            clean = False

        recomputed = compute_entry_hash(e.prev_hash, e.payload)
        if recomputed != e.entry_hash:
            fail(
                f"seq {e.sequence_number}: entry_hash MISMATCH — this row's payload was tampered with.\n"
                f"        stored entry_hash    = {e.entry_hash}\n"
                f"        recomputed from data = {recomputed}"
            )
            clean = False

        expected_prev = e.entry_hash
        expected_seq = e.sequence_number + 1

    if clean:
        ok(f"all {len(entries)} entries verified — chain intact from genesis to seq {entries[-1].sequence_number}")
    return clean


def _load_git_checkpoint(ckpt: Checkpoint) -> dict | None:
    if not CHECKPOINT_REPO_PATH:
        return None
    fname = f"checkpoint-{ckpt.seq_start:08d}-{ckpt.seq_end:08d}.json"
    path = Path(CHECKPOINT_REPO_PATH) / "checkpoints" / fname
    if not path.exists():
        return None
    return json.loads(path.read_text())


def verify_checkpoints(db) -> bool:
    print("\n=== CHECKPOINT INTEGRITY ===")
    ckpts = db.query(Checkpoint).order_by(Checkpoint.seq_start.asc()).all()
    if not ckpts:
        warn("no checkpoints yet — none due, or the checkpoint job hasn't run")
        return True

    clean = True
    for c in ckpts:
        label = f"checkpoint #{c.id} (seq {c.seq_start}-{c.seq_end})"
        batch = (
            db.query(LogEntry)
            .filter(LogEntry.sequence_number >= c.seq_start, LogEntry.sequence_number <= c.seq_end)
            .order_by(LogEntry.sequence_number.asc())
            .all()
        )
        if len(batch) != c.entry_count:
            fail(f"{label}: batch has {len(batch)} rows in DB but checkpoint claims {c.entry_count} — entries added/removed")
            clean = False
            if not batch:
                continue

        # Rebuild each leaf from CONTENT (prev_hash + payload), not from the
        # stored entry_hash column. If we trusted the stored entry_hash, an
        # attacker who edited a payload but left entry_hash untouched would
        # slip past this check (the tree would rebuild from the old, matching
        # hashes). Deriving the leaf from payload makes the Merkle root
        # directly sensitive to payload tampering — which is the whole point.
        recomputed_root = merkle.compute_root(
            [compute_entry_hash(e.prev_hash, e.payload) for e in batch]
        )

        # (a) DB stored root
        if recomputed_root != c.merkle_root:
            fail(
                f"{label}: recomputed Merkle root != DB stored root — the underlying log rows "
                f"were edited after this checkpoint was taken.\n"
                f"        stored root     = {c.merkle_root}\n"
                f"        recomputed root = {recomputed_root}"
            )
            clean = False
        else:
            ok(f"{label}: recomputed root matches DB stored root")

        # (b) aggregator signature over the stored root
        try:
            VerifyKey(bytes.fromhex(c.aggregator_public_key_hex)).verify(
                bytes.fromhex(c.merkle_root), bytes.fromhex(c.aggregator_signature)
            )
            ok(f"{label}: aggregator signature over root is valid")
        except (BadSignatureError, ValueError):
            fail(f"{label}: aggregator signature does NOT verify against the stored root")
            clean = False

        # (c) git-committed copy
        git_ckpt = _load_git_checkpoint(c)
        if git_ckpt is None:
            if CHECKPOINT_REPO_PATH:
                warn(f"{label}: no git-committed file found (not anchored, or repo path wrong) — DB-only, can't cross-check")
            else:
                warn(f"{label}: CHECKPOINT_REPO_PATH not set — skipping external cross-check")
        else:
            git_root = git_ckpt.get("merkle_root")
            if git_root != recomputed_root:
                fail(
                    f"{label}: recomputed root != GIT-committed root. The database was rewritten "
                    f"AFTER it was externally anchored — git is the copy the operator couldn't "
                    f"silently change, so the DB is the tampered side.\n"
                    f"        git-committed root = {git_root}\n"
                    f"        recomputed root    = {recomputed_root}"
                )
                clean = False
            elif git_root != c.merkle_root:
                fail(
                    f"{label}: git root matches the true recomputed root, but the DB's stored "
                    f"merkle_root was changed to {c.merkle_root[:16]}… — DB stored-root tampering."
                )
                clean = False
            else:
                ok(f"{label}: git-committed root agrees with DB and recomputed root")

    return clean


def main():
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
    db = sessionmaker(bind=engine)()
    try:
        print(f"Verifying log in {DB_URL}")
        if CHECKPOINT_REPO_PATH:
            print(f"Cross-checking against git checkpoints in {CHECKPOINT_REPO_PATH}")
        chain_ok = verify_chain(db)
        ckpt_ok = verify_checkpoints(db)
    finally:
        db.close()

    print()
    if chain_ok and ckpt_ok:
        print(f"{GREEN}RESULT: log verified — no tampering detected.{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}RESULT: verification FAILED — see the mismatches above.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
