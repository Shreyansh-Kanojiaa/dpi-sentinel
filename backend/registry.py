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

from models import Witness

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
            return data["witness_id"], data["public_key_hex"]
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
            continue
        slug, public_key_hex = result
        existing = db.query(Witness).filter_by(slug=slug).first()
        if existing:
            existing.base_url = base_url
            existing.public_key_hex = public_key_hex
        else:
            db.add(Witness(slug=slug, base_url=base_url, public_key_hex=public_key_hex, registered_at=now))
        registered += 1
    db.commit()
    logger.info("witness registry: registered %d/%d configured witnesses", registered, len(witness_urls))
