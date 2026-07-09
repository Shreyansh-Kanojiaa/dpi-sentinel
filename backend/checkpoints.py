"""
DPI Sentinel aggregator — Merkle checkpoints + external git anchoring
(Milestone 3).

A checkpoint freezes a contiguous batch of LogEntry rows into a single
signed Merkle root. It's created when EITHER 50 new entries have piled up
since the last checkpoint OR an hour has passed with at least one new entry
— whichever comes first. The root is signed with the aggregator's own key
(identity.py) and then written into an external git repo.

Why git and not just the DB: the DB is under the operator's control. If the
operator edits an old row AND recomputes every downstream hash AND updates
the stored merkle_root, the database alone would look internally
consistent — the tamper-evidence collapses because the same party holds all
the evidence. Pushing each signed root to an outside git remote puts one
copy somewhere the operator can't quietly rewrite (commit history is public
and timestamped). verify_log.py then recomputes the root from current DB
rows and compares it to the git-committed root; if the operator rewrote
history, those two disagree and the DB is provably the one that changed.
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

import merkle
from identity import get_signing_key, public_key_hex
from models import Checkpoint, LogEntry

logger = logging.getLogger("aggregator.checkpoints")

CHECKPOINT_MAX_ENTRIES = int(os.environ.get("CHECKPOINT_MAX_ENTRIES", "50"))
CHECKPOINT_MAX_AGE_SECONDS = float(os.environ.get("CHECKPOINT_MAX_AGE_SECONDS", "3600"))
CHECKPOINT_REPO_PATH = os.environ.get("CHECKPOINT_REPO_PATH", "").strip()


def _batch_entries(db: Session, seq_start: int) -> list[LogEntry]:
    return (
        db.query(LogEntry)
        .filter(LogEntry.sequence_number >= seq_start)
        .order_by(LogEntry.sequence_number.asc())
        .all()
    )


def _should_checkpoint(new_count: int, oldest_uncheckpointed_at: datetime | None) -> bool:
    if new_count == 0:
        return False
    if new_count >= CHECKPOINT_MAX_ENTRIES:
        return True
    if oldest_uncheckpointed_at is not None:
        age = (datetime.utcnow() - oldest_uncheckpointed_at).total_seconds()
        if age >= CHECKPOINT_MAX_AGE_SECONDS:
            return True
    return False


def maybe_create_checkpoint(db: Session) -> Checkpoint | None:
    """
    Create a checkpoint if the size-or-age trigger is met. Returns the new
    Checkpoint, or None if nothing was due. Safe to call on a fixed tick.
    """
    last_ckpt = db.query(Checkpoint).order_by(Checkpoint.seq_end.desc()).first()
    seq_start = (last_ckpt.seq_end + 1) if last_ckpt else 1

    batch = _batch_entries(db, seq_start)
    if not batch:
        return None

    oldest_at = batch[0].created_at
    if not _should_checkpoint(len(batch), oldest_at):
        return None

    return _create_checkpoint(db, batch)


def _create_checkpoint(db: Session, batch: list[LogEntry]) -> Checkpoint:
    leaf_hashes = [e.entry_hash for e in batch]
    root = merkle.compute_root(leaf_hashes)

    # Sign the raw root bytes with the aggregator's identity. This is the
    # "as-of" attestation: the log looked exactly like this up to seq_end.
    signing_key = get_signing_key()
    signature = signing_key.sign(bytes.fromhex(root)).signature.hex()

    ckpt = Checkpoint(
        seq_start=batch[0].sequence_number,
        seq_end=batch[-1].sequence_number,
        entry_count=len(batch),
        merkle_root=root,
        timestamp=datetime.utcnow(),
        aggregator_public_key_hex=public_key_hex(),
        aggregator_signature=signature,
        git_committed=False,
    )
    db.add(ckpt)
    db.commit()
    db.refresh(ckpt)

    logger.info(
        "checkpoint #%d created: seq %d-%d (%d entries) root=%s",
        ckpt.id, ckpt.seq_start, ckpt.seq_end, ckpt.entry_count, root[:16],
    )

    _anchor_to_git(db, ckpt, leaf_hashes)
    return ckpt


def checkpoint_file_dict(ckpt: Checkpoint, leaf_hashes: list[str]) -> dict:
    """The exact JSON written to git. Self-contained: it carries the leaf
    hashes so an auditor can rebuild the root from the file alone, plus the
    aggregator signature so they can confirm who attested to it."""
    return {
        "seq_start": ckpt.seq_start,
        "seq_end": ckpt.seq_end,
        "entry_count": ckpt.entry_count,
        "merkle_root": ckpt.merkle_root,
        "timestamp": ckpt.timestamp.replace(tzinfo=timezone.utc).isoformat(),
        "aggregator_public_key_hex": ckpt.aggregator_public_key_hex,
        "aggregator_signature": ckpt.aggregator_signature,
        "entry_hashes": leaf_hashes,
    }


def get_inclusion_proof(db: Session, entry_id: int) -> dict | None:
    """
    Build a Merkle inclusion proof for one LogEntry: the sibling hashes that,
    combined with the entry's own hash, recompute the checkpoint's published
    root. Returns None if the entry doesn't exist or isn't inside any
    checkpoint yet (it will be, once the next checkpoint covers its range).

    The point: with just the entry + this proof (a handful of hashes), anyone
    can confirm the entry belongs to the signed root WITHOUT downloading the
    whole log — the proof is log(n) sibling hashes, not the n leaves.
    """
    entry = db.query(LogEntry).filter_by(id=entry_id).first()
    if entry is None:
        return None

    ckpt = (
        db.query(Checkpoint)
        .filter(Checkpoint.seq_start <= entry.sequence_number, Checkpoint.seq_end >= entry.sequence_number)
        .order_by(Checkpoint.seq_end.desc())
        .first()
    )
    if ckpt is None:
        return None

    batch = (
        db.query(LogEntry)
        .filter(LogEntry.sequence_number >= ckpt.seq_start, LogEntry.sequence_number <= ckpt.seq_end)
        .order_by(LogEntry.sequence_number.asc())
        .all()
    )
    leaf_hashes = [e.entry_hash for e in batch]
    index = entry.sequence_number - ckpt.seq_start
    proof = merkle.inclusion_proof(leaf_hashes, index)

    return {
        "entry_id": entry.id,
        "sequence_number": entry.sequence_number,
        "entry_type": entry.entry_type,
        "entry_hash": entry.entry_hash,
        "leaf_index": index,
        "checkpoint": {
            "id": ckpt.id,
            "seq_start": ckpt.seq_start,
            "seq_end": ckpt.seq_end,
            "merkle_root": ckpt.merkle_root,
            "aggregator_public_key_hex": ckpt.aggregator_public_key_hex,
            "aggregator_signature": ckpt.aggregator_signature,
        },
        "proof": proof,
        "verifies": merkle.verify_proof(entry.entry_hash, proof, ckpt.merkle_root),
    }


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _anchor_to_git(db: Session, ckpt: Checkpoint, leaf_hashes: list[str]) -> None:
    """
    Write the checkpoint JSON into CHECKPOINT_REPO_PATH/checkpoints/, commit
    it, and push. Any failure (no repo path, no remote, no network, no auth)
    is logged as a warning and swallowed — the aggregator must never crash
    over a failed anchor, same tolerance as the witness->aggregator POST in
    Milestone 1. A missed push just means this root isn't externally anchored
    yet; the DB checkpoint still stands.
    """
    if not CHECKPOINT_REPO_PATH:
        logger.warning(
            "CHECKPOINT_REPO_PATH not set — checkpoint #%d NOT anchored to git "
            "(DB-only; external tamper-evidence disabled)", ckpt.id,
        )
        return

    repo = Path(CHECKPOINT_REPO_PATH)
    try:
        repo.mkdir(parents=True, exist_ok=True)
        if not (repo / ".git").exists():
            _run_git(repo, "init")
            # Best-effort identity so commits don't fail on a bare container.
            _run_git(repo, "config", "user.email", "aggregator@dpi-sentinel.local")
            _run_git(repo, "config", "user.name", "DPI Sentinel Aggregator")

        ckpt_dir = repo / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        fname = f"checkpoint-{ckpt.seq_start:08d}-{ckpt.seq_end:08d}.json"
        (ckpt_dir / fname).write_text(
            json.dumps(checkpoint_file_dict(ckpt, leaf_hashes), indent=2, sort_keys=True) + "\n"
        )

        _run_git(repo, "add", f"checkpoints/{fname}")
        commit = _run_git(
            repo, "commit", "-m",
            f"checkpoint seq {ckpt.seq_start}-{ckpt.seq_end} root {ckpt.merkle_root[:16]}",
        )
        if commit.returncode != 0:
            logger.warning("git commit failed for checkpoint #%d: %s", ckpt.id, commit.stderr.strip())
            return

        sha = _run_git(repo, "rev-parse", "HEAD").stdout.strip()
        ckpt.git_committed = True
        ckpt.git_commit_sha = sha
        db.commit()

        # Push only if a remote is configured; otherwise commit-only is fine.
        remotes = _run_git(repo, "remote").stdout.split()
        if not remotes:
            logger.warning(
                "checkpoint #%d committed locally (%s) but no git remote configured — not pushed",
                ckpt.id, sha[:10],
            )
            return
        push = _run_git(repo, "push", remotes[0], "HEAD")
        if push.returncode != 0:
            logger.warning(
                "checkpoint #%d committed (%s) but push failed (continuing): %s",
                ckpt.id, sha[:10], push.stderr.strip(),
            )
        else:
            logger.info("checkpoint #%d pushed to %s (%s)", ckpt.id, remotes[0], sha[:10])
    except Exception as e:
        logger.warning("git anchoring failed for checkpoint #%d (continuing): %s", ckpt.id, e)
