"""
DPI Sentinel witness — probing loop.

Performs a real async HTTP GET against one target on a fixed interval
(called once per PROBE_TARGETS entry by main.py's probe_loop), builds a
signed observation, and POSTs it to the aggregator. Connection failures
are logged and swallowed so the loop keeps running.

WATCH ITEM (not yet root-caused, don't file away after one occurrence):
during the multi-target-witness fix's done-check, all three witness
containers logged a `timeout` on their very first probe tick after a
restart, then went clean from the second tick onward — plausible as
container-network/DNS cold-start noise, but "multiple witnesses failing in
lockstep" was exactly the signature that earlier turned out to be shared-
infrastructure noise (not independent disagreement) in a different
incident. If this lockstep-timeout pattern (a) recurs on future restarts,
or (b) ever happens mid-run rather than only at t=0, that stops being
explainable as startup noise and needs real investigation — a timeout is
recorded in `error` below either way, so it's queryable via ProbeResult
rows if/when that investigation happens.
"""

import logging
from datetime import datetime, timezone

import httpx
from nacl.signing import SigningKey

from signing import sign_observation

logger = logging.getLogger("witness.prober")

PROBE_TIMEOUT_SECONDS = 5


async def probe_once(client: httpx.AsyncClient, target: str) -> dict:
    reachable = False
    http_status = None
    error = None

    start = datetime.now(timezone.utc)
    try:
        resp = await client.get(target, timeout=PROBE_TIMEOUT_SECONDS)
        http_status = resp.status_code
        reachable = resp.status_code < 500
    except httpx.TimeoutException:
        error = "timeout"
    except httpx.ConnectError as e:
        error = f"connect_error: {str(e)[:120]}"
    except Exception as e:
        error = f"error: {str(e)[:120]}"
    end = datetime.now(timezone.utc)

    latency_ms = round((end - start).total_seconds() * 1000, 1) if reachable else None

    return {
        "timestamp": start.isoformat(),
        "target": target,
        "reachable": reachable,
        "http_status": http_status,
        "latency_ms": latency_ms,
        "error": error,
    }


async def report_observation(client: httpx.AsyncClient, aggregator_url: str, signed_observation: dict):
    url = f"{aggregator_url}/observations"
    try:
        resp = await client.post(url, json=signed_observation, timeout=PROBE_TIMEOUT_SECONDS)
        if resp.status_code >= 400:
            logger.warning("aggregator rejected observation: %s -> %s", url, resp.status_code)
        else:
            logger.info("reported observation to aggregator: %s", url)
    except httpx.ConnectError as e:
        logger.warning("could not reach aggregator at %s (connect_error: %s) — is it running yet?", url, str(e)[:200])
    except httpx.TimeoutException:
        logger.warning("timed out reporting to aggregator at %s", url)
    except Exception as e:
        logger.warning("unexpected error reporting to aggregator at %s: %s", url, e)


async def probe_and_report(
    client: httpx.AsyncClient,
    witness_id: str,
    target: str,
    aggregator_url: str,
    signing_key: SigningKey,
) -> dict:
    result = await probe_once(client, target)

    observation = {
        "witness_id": witness_id,
        "timestamp": result["timestamp"],
        "target": result["target"],
        "reachable": result["reachable"],
        "http_status": result["http_status"],
        "latency_ms": result["latency_ms"],
        "error": result["error"],
    }

    logger.info(
        "probe target=%s reachable=%s status=%s latency_ms=%s error=%s",
        target, result["reachable"], result["http_status"], result["latency_ms"], result["error"],
    )

    signed = sign_observation(observation, signing_key)
    await report_observation(client, aggregator_url, signed)
    return result
