"""
Run this on YOUR OWN network (not in a restricted sandbox) before the demo
to confirm each rail's probe target is actually reachable.

    python verify_targets.py

If a target fails, edit PROBE_TARGET_OVERRIDES in rails_config.py with a
working alternative (e.g. a specific page, a regional mirror, or — for a
fully honest fallback — keep it as-is and let the dashboard show that rail
as "synthetic only" for the demo, which is itself an honest finding worth
mentioning to judges: "even finding a stable public surface to monitor was
part of the problem we're surfacing.")
"""

import time
import httpx
from rails_config import RAILS_SEED, PROBE_TARGET_OVERRIDES

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def main():
    print("DPI Sentinel — probe target verification\n" + "=" * 50)
    for spec in RAILS_SEED:
        target = PROBE_TARGET_OVERRIDES.get(spec["slug"], spec["probe_target"])
        print(f"\n[{spec['name']}] -> {target}")
        try:
            start = time.perf_counter()
            r = httpx.get(target, timeout=6, follow_redirects=True, headers={"User-Agent": UA})
            ms = (time.perf_counter() - start) * 1000
            status = "OK" if r.status_code < 500 else "DEGRADED"
            print(f"  status={r.status_code}  latency={ms:.0f}ms  -> {status}")
        except Exception as e:
            print(f"  FAILED: {e}")
            print(f"  -> fix: set PROBE_TARGET_OVERRIDES['{spec['slug']}'] in rails_config.py")


if __name__ == "__main__":
    main()
