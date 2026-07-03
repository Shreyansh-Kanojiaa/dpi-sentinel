"""
DPI Sentinel witness — standalone service.

Each witness instance has its own Ed25519 identity, independently probes
PROBE_TARGET on a fixed interval, signs what it observed, and reports it
to a central aggregator (built in a later milestone). No witness can
alter another witness's report or forge one on its behalf, because each
signs with a private key that never leaves its own container.

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
PROBE_TARGET = os.environ.get("PROBE_TARGET", "https://www.npci.org.in")
AGGREGATOR_URL = os.environ.get("AGGREGATOR_URL", "http://aggregator:8420")
PROBE_INTERVAL_SECONDS = float(os.environ.get("PROBE_INTERVAL_SECONDS", "8"))
KEY_PATH = os.environ.get("KEY_PATH", "./witness.key")

signing_key = load_or_create_signing_key(KEY_PATH)
public_key_hex = signing_key.verify_key.encode().hex()

state = {"last_probe_at": None}


async def probe_loop(client: httpx.AsyncClient):
    while True:
        try:
            result = await probe_and_report(client, WITNESS_ID, PROBE_TARGET, AGGREGATOR_URL, signing_key)
            state["last_probe_at"] = result["timestamp"]
        except Exception:
            logger.exception("probe_loop iteration failed unexpectedly")
        await asyncio.sleep(PROBE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "starting witness id=%s target=%s aggregator=%s interval=%ss pubkey=%s",
        WITNESS_ID, PROBE_TARGET, AGGREGATOR_URL, PROBE_INTERVAL_SECONDS, public_key_hex,
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
    return {"witness_id": WITNESS_ID, "public_key_hex": public_key_hex}


@app.get("/health")
def health():
    return {"status": "ok", "witness_id": WITNESS_ID, "last_probe_at": state["last_probe_at"]}
