"""
DPI Sentinel aggregator — witness registry.

The aggregator's trust in a witness's public key comes ONLY from here:
fetching /pubkey directly from a witness base URL we were configured
(out-of-band, via WITNESS_URLS) to trust — never from anything an
incoming /observations payload claims about itself. See main.py's
POST /observations handler and the design-notes callout in this
milestone's writeup for why that distinction matters.

Registration for each configured witness runs with its own retry/backoff
loop, all concurrently, so one witness that's slow to start (docker
compose gives no startup-order guarantee) doesn't block the others.
"""

import asyncio
import logging
import os
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from models import Rail, Witness, WitnessRailAssignment

logger = logging.getLogger("aggregator.registry")

REGISTRY_MAX_ATTEMPTS = int(os.environ.get("WITNESS_REGISTRY_MAX_ATTEMPTS", "10"))
REGISTRY_BACKOFF_BASE_SECONDS = float(os.environ.get("WITNESS_REGISTRY_BACKOFF_BASE_SECONDS", "1"))
REGISTRY_BACKOFF_CAP_SECONDS = float(os.environ.get("WITNESS_REGISTRY_BACKOFF_CAP_SECONDS", "15"))


async def _fetch_pubkey_with_retry(client: httpx.AsyncClient, base_url: str):
    attempt = 0
    while attempt < REGISTRY_MAX_ATTEMPTS:
        try:
            resp = await client.get(f"{base_url}/pubkey", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return data["witness_id"], data["public_key_hex"], data.get("targets", [])
        except Exception as e:
            attempt += 1
            if attempt >= REGISTRY_MAX_ATTEMPTS:
                break
            delay = min(REGISTRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), REGISTRY_BACKOFF_CAP_SECONDS)
            logger.warning(
                "witness registry: %s not reachable (attempt %d/%d): %s — retrying in %.1fs",
                base_url, attempt, REGISTRY_MAX_ATTEMPTS, e, delay,
            )
            await asyncio.sleep(delay)

    logger.error("witness registry: giving up on %s after %d attempts — it will not be registered", base_url, REGISTRY_MAX_ATTEMPTS)
    return None


def _sync_rail_assignments(db: Session, witness: Witness, targets: list[dict]) -> None:
    """
    Milestone 5 — make WitnessRailAssignment match exactly what this witness
    just declared via /pubkey, right now. Matching is by exact string
    equality against Rail.probe_target — the same rule POST /observations
    uses to route an incoming observation to a rail (see main.py) — so a
    witness's recorded coverage never drifts from what its observations
    will actually be routed to.

    This runs on every aggregator startup (build_registry always calls it
    for every witness it just (re)registered), so it's also how existing
    deployments get backfilled: the first startup after this change derives
    witness-a/b/c's assignments from their current PROBE_TARGETS (upi +
    digilocker for all three, per docker-compose.yml) — no separate
    migration script needed, but this IS the migration step, run
    automatically rather than left implicit.

    Declarative sync, not additive: assignments for rails no longer among
    the witness's declared targets are removed, so un-assigning a rail (by
    editing PROBE_TARGETS and restarting) takes effect, not just assigning
    new ones.
    """
    declared_urls = {t["url"] for t in targets if t.get("url")}
    matched_rail_ids: set[int] = set()
    unmatched_urls = []
    for url in declared_urls:
        rail = db.query(Rail).filter_by(probe_target=url).first()
        if rail:
            matched_rail_ids.add(rail.id)
        else:
            unmatched_urls.append(url)
    if unmatched_urls:
        logger.warning(
            "witness registry: %s declared target(s) %s matching no known rail — "
            "not recorded as assignments (observations for them will also be "
            "rejected by POST /observations; see GET /api/diagnostics/unmatched-targets)",
            witness.slug, unmatched_urls,
        )

    existing_by_rail_id = {
        a.rail_id: a
        for a in db.query(WitnessRailAssignment).filter_by(witness_id=witness.id).all()
    }
    now = datetime.utcnow()
    added_rail_ids = matched_rail_ids - existing_by_rail_id.keys()
    removed_rail_ids = existing_by_rail_id.keys() - matched_rail_ids
    for rail_id in added_rail_ids:
        db.add(WitnessRailAssignment(witness_id=witness.id, rail_id=rail_id, assigned_at=now))
    for rail_id in removed_rail_ids:
        db.delete(existing_by_rail_id[rail_id])

    logger.info(
        "witness registry: %s reachable — rail assignments synced (%d assigned total, "
        "%d added, %d removed)",
        witness.slug, len(matched_rail_ids), len(added_rail_ids), len(removed_rail_ids),
    )


async def build_registry(db: Session, witness_urls: list[str]):
    if not witness_urls:
        logger.warning("WITNESS_URLS is empty — no witnesses will be registered; all /observations will be rejected")
        return

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_fetch_pubkey_with_retry(client, url) for url in witness_urls])

    now = datetime.utcnow()
    registered = 0
    for base_url, result in zip(witness_urls, results):
        if result is None:
            # Unreachable this cycle (see _fetch_pubkey_with_retry) — do NOT touch
            # this witness's row or its WitnessRailAssignment rows. Only a
            # successful /pubkey fetch with an explicit target list may add or
            # remove assignments (_sync_rail_assignments); an inability to ask
            # is never treated as "declared zero targets."
            existing = db.query(Witness).filter_by(base_url=base_url).first()
            if existing:
                preserved = db.query(WitnessRailAssignment).filter_by(witness_id=existing.id).count()
                logger.warning(
                    "witness registry: %s (%s) unreachable this sync cycle — "
                    "leaving its existing %d rail assignment(s) untouched",
                    existing.slug, base_url, preserved,
                )
            else:
                logger.warning(
                    "witness registry: %s unreachable this sync cycle and has no prior "
                    "registration — no rail assignments to preserve or create",
                    base_url,
                )
            continue
        slug, public_key_hex, targets = result
        existing = db.query(Witness).filter_by(slug=slug).first()
        if existing:
            existing.base_url = base_url
            existing.public_key_hex = public_key_hex
            witness = existing
        else:
            witness = Witness(slug=slug, base_url=base_url, public_key_hex=public_key_hex, registered_at=now)
            db.add(witness)
            db.flush()  # assign witness.id before assignments reference it
        _sync_rail_assignments(db, witness, targets)
        registered += 1
    db.commit()
    logger.info("witness registry: registered %d/%d configured witnesses", registered, len(witness_urls))
