"""
DPI Sentinel aggregator — visibility for observations whose target didn't
match any known Rail (a fix following the DigiLocker participation bug).

POST /observations already REJECTS this case loudly at the HTTP layer (a
400, not a silent 200-and-drop) and logs a WARNING with the offending
witness_id and target — see main.py's routing-match block. What was
missing wasn't rejection, it was VISIBILITY: a process log line only
surfaces if someone is already tailing logs at the right moment, which is
exactly the "I'd only notice if I already suspected a problem" gap this
closes. This module keeps a small in-memory record so a mismatch shows up
in something you'd actually look at — GET /api/diagnostics/unmatched-targets
— rather than requiring you to already be watching container logs.

In-memory, not persisted, chosen for the same reason as certificates.py's
rate limiter: the aggregator is a single process, so a lock + a bounded
deque is fully correct and needs no new dependency. Tradeoff accepted: the
count and recent list reset on restart, and this wouldn't be shared across
multiple aggregator replicas — irrelevant at this project's current scale.
"""

import threading
from collections import deque
from datetime import datetime

_LOCK = threading.Lock()
_MAX_RECENT = 50

_total_rejected = 0
_recent: deque = deque(maxlen=_MAX_RECENT)


def record_unmatched_target(witness_id: str, target: str) -> None:
    """Called from POST /observations right where the existing rejection
    already happens — this only adds visibility, it doesn't change whether
    or how the observation is rejected."""
    global _total_rejected
    with _LOCK:
        _total_rejected += 1
        _recent.append({
            "witness_id": witness_id,
            "target": target,
            "rejected_at": datetime.utcnow().isoformat(),
        })


def snapshot() -> dict:
    """Total count since process start (a restart resets this — see module
    docstring) plus the most recent MAX_RECENT rejections, newest last."""
    with _LOCK:
        return {
            "total_rejected_since_start": _total_rejected,
            "recent": list(_recent),
        }
