"""
DPI Sentinel witness — standalone service.

Each witness instance has its own Ed25519 identity, independently probes
one or more configured targets on a fixed interval, signs what it observed
PER TARGET, and reports each observation to a central aggregator. No
witness can alter another witness's report or forge one on its behalf,
because each signs with a private key that never leaves its own container.

Run with:
    uvicorn main:app --host 0.0.0.0 --port 8500
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from identity import load_or_create_signing_key
from prober import probe_and_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("witness.main")

WITNESS_ID = os.environ.get("WITNESS_ID", "witness-dev")
AGGREGATOR_URL = os.environ.get("AGGREGATOR_URL", "http://aggregator:8420")
PROBE_INTERVAL_SECONDS = float(os.environ.get("PROBE_INTERVAL_SECONDS", "8"))
KEY_PATH = os.environ.get("KEY_PATH", "./witness.key")


def parse_probe_targets(raw: str) -> list[tuple[str, str]]:
    """
    Parse PROBE_TARGETS="label:url,label:url,..." into [(label, url), ...],
    preserving order. `label` is a human-readable tag used only in this
    witness's own logs — it is NOT sent to the aggregator and plays no part
    in rail routing (see the module-level note below on why).

    Splits each entry on the FIRST colon only, so a URL's own "https://"
    colon doesn't break parsing (label.split(":", 1) -> [label, url]).

    Fails loudly (raises ValueError) on anything malformed — an empty
    config, a missing label or URL, or a URL without an http(s) scheme —
    rather than silently starting a witness that probes nothing. This is
    deliberately a hard failure at import time: better a container that
    won't start than one that starts but generates zero observations,
    which would only be noticed via an aggregator-side quorum gap much
    later.
    """
    if not raw.strip():
        raise ValueError(
            "PROBE_TARGETS is empty — a witness must have at least one "
            'target configured, e.g. PROBE_TARGETS="upi:https://www.npci.org.in"'
        )

    targets: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f'malformed PROBE_TARGETS entry {entry!r} — expected "label:url" '
                f'(e.g. "upi:https://www.npci.org.in")'
            )
        label, url = entry.split(":", 1)
        label, url = label.strip(), url.strip()
        if not label or not url:
            raise ValueError(f"malformed PROBE_TARGETS entry {entry!r} — empty label or url")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"malformed PROBE_TARGETS entry {entry!r} — url must start with http:// or https://")
        targets.append((label, url))

    if not targets:
        raise ValueError("PROBE_TARGETS parsed to zero targets — check the format")
    return targets


# A witness may cover any subset of rails via PROBE_TARGETS (Milestone 5).
# Earlier this had to be every rail, because the aggregator's participation
# math divided by the total registered witness count, a GLOBAL denominator —
# a rail watched by a strict subset of witnesses could never clear quorum.
# That's fixed now: registry.py records each witness's declared targets
# (below, surfaced via GET /pubkey) as WitnessRailAssignment rows, and
# quorum.py divides by witnesses ASSIGNED to a rail, not all registered
# witnesses. This file doesn't need to know that — it just declares what it
# probes, same as before.
PROBE_TARGETS = parse_probe_targets(os.environ.get("PROBE_TARGETS", ""))

signing_key = load_or_create_signing_key(KEY_PATH)
public_key_hex = signing_key.verify_key.encode().hex()

state = {"last_probe_at": None}


async def probe_loop(client: httpx.AsyncClient):
    while True:
        # All configured targets are probed within the same tick, concurrently
        # (not staggered) — with PROBE_INTERVAL_SECONDS as the cycle time for
        # the whole batch, not per-target. Each target gets its own independent
        # probe_and_report() call: its own HTTP probe, its own timestamp, its
        # own signature, its own POST to /observations — exactly the same
        # single-target code path as before, just invoked once per target.
        # return_exceptions=True preserves the pre-existing tolerance (a
        # failure probing/reporting one target must never take down the
        # others or crash the loop).
        results = await asyncio.gather(
            *[
                probe_and_report(client, WITNESS_ID, target_url, AGGREGATOR_URL, signing_key)
                for _, target_url in PROBE_TARGETS
            ],
            return_exceptions=True,
        )
        for (label, target_url), result in zip(PROBE_TARGETS, results):
            if isinstance(result, Exception):
                logger.exception("probe_loop iteration failed for target label=%s url=%s", label, target_url, exc_info=result)
            else:
                state["last_probe_at"] = result["timestamp"]
        await asyncio.sleep(PROBE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    targets_desc = ", ".join(f"{label}={url}" for label, url in PROBE_TARGETS)
    logger.info(
        "starting witness id=%s targets=[%s] aggregator=%s interval=%ss pubkey=%s",
        WITNESS_ID, targets_desc, AGGREGATOR_URL, PROBE_INTERVAL_SECONDS, public_key_hex,
    )
    client = httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": f"DPI-Sentinel-Witness/0.1 ({WITNESS_ID})"},
    )
    task = asyncio.create_task(probe_loop(client))

    yield

    task.cancel()
    await client.aclose()


app = FastAPI(title=f"DPI Sentinel Witness ({WITNESS_ID})", lifespan=lifespan)


@app.get("/pubkey")
def get_pubkey():
    # Milestone 5: targets are included here, not just at /health, because
    # this is the endpoint the aggregator's registry.py fetches at startup
    # to build BOTH the trusted-key registry AND (new) the
    # WitnessRailAssignment rows — registration and rail-coverage
    # declaration happen in the same out-of-band fetch.
    return {
        "witness_id": WITNESS_ID,
        "public_key_hex": public_key_hex,
        "targets": [{"label": label, "url": url} for label, url in PROBE_TARGETS],
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "witness_id": WITNESS_ID,
        "targets": [{"label": label, "url": url} for label, url in PROBE_TARGETS],
        "last_probe_at": state["last_probe_at"],
    }
