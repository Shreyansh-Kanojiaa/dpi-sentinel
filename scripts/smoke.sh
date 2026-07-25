#!/usr/bin/env bash
#
# End-to-end smoke test of the detection-to-certificate pipeline.
#
# Runs the same path the demo does, and asserts on it:
#   demo rail operational -> stop demo-target -> quorum declares degraded
#   -> Evidence Certificate issued -> certificate verifies (all 3 checks)
#   -> start demo-target -> incident resolved -> hash chain still verifies
#
# Assertions are deliberately limited to the "demo" rail, which is entirely
# self-contained (an nginx container inside the compose network). UPI and
# DigiLocker probe the real public internet, so their status depends on the
# network the test runs from; asserting on them would make this fail for
# reasons that have nothing to do with the code. They are reported at the
# end for information only.
#
# Usable two ways:
#   ./scripts/smoke.sh          # local pre-demo check, leaves the stack up
#   CI=1 ./scripts/smoke.sh     # tears the stack down and removes volumes
#
set -euo pipefail

API="${API:-http://localhost:8420}"
COMPOSE="docker compose"
STEP=0

log()  { STEP=$((STEP + 1)); printf '\n[%d] %s\n' "$STEP" "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
die()  { printf '  FAIL %s\n' "$*" >&2; dump_diag; exit 1; }

dump_diag() {
  printf '\n--- aggregator logs (last 40) ---\n' >&2
  $COMPOSE logs --tail 40 aggregator >&2 2>/dev/null || true
}

cleanup() {
  if [ "${CI:-}" = "1" ]; then
    printf '\nTearing down (CI=1)\n'
    $COMPOSE down -v >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# wait_for <timeout-seconds> <description> <shell-condition...>
wait_for() {
  local timeout=$1 desc=$2; shift 2
  local deadline=$(( SECONDS + timeout ))
  until "$@" >/dev/null 2>&1; do
    [ $SECONDS -lt $deadline ] || die "timed out after ${timeout}s waiting for: $desc"
    sleep 3
  done
  ok "$desc"
}

# rail_status <slug> — prints the rail's current quorum status
rail_status() {
  curl -sf --max-time 5 "$API/api/rails" | python3 -c "
import sys, json
rails = json.load(sys.stdin)
match = [r for r in rails if r['slug'] == '$1']
print(match[0]['status'] if match else 'missing')
"
}

rail_is() { [ "$(rail_status "$1")" = "$2" ]; }

log "Building and starting the stack"
$COMPOSE up -d --build
ok "containers up"

log "Waiting for the aggregator to serve (it blocks on witness registration)"
wait_for 180 "aggregator responding on $API" \
  bash -c "curl -sf --max-time 5 '$API/api/rails' | grep -q '\"slug\"'"

log "Waiting for the demo rail to reach quorum"
wait_for 120 "demo rail operational" rail_is demo operational

log "Stopping demo-target: a real HTTP server goes away"
$COMPOSE stop demo-target >/dev/null
wait_for 120 "demo rail degraded" rail_is demo degraded

log "Checking WHY it was declared degraded"
curl -sf "$API/api/incidents" | python3 -c "
import sys, json
live = [i for i in json.load(sys.stdin)
        if not i['is_historical'] and i['severity'] == 'degraded'
        and 'Demo Rail' in i['title']]
assert live, 'no live degraded incident for the demo rail'
q = live[0].get('quorum_snapshot') or {}
assert q, 'incident carries no quorum_snapshot receipt'
assigned, reporting = q.get('assigned_count'), q.get('reporting_count')
assert assigned == 3, f'expected 3 assigned witnesses, got {assigned}'
# The point of the whole design: participation held (every assigned witness
# still reported), so it was AGREEMENT that drove the call, not silence.
assert reporting == assigned, f'participation dropped: {reporting}/{assigned} reporting'
print(f'  ok   quorum receipt present: {reporting}/{assigned} reporting, '
      f\"agreement {q.get('agreement_fraction')}\")
" || die "quorum snapshot did not show an agreement-driven detection"

log "Requesting an Evidence Certificate inside the incident window"
TS=$(curl -sf "$API/api/incidents" | python3 -c "
import sys, json
print([i for i in json.load(sys.stdin)
       if not i['is_historical'] and i['severity'] == 'degraded'][0]['started_at'])")
curl -sf -X POST "$API/api/certificates" \
  -H 'Content-Type: application/json' \
  -d "{\"rail_slug\":\"demo\",\"claimed_timestamp\":\"$TS\",\"claimed_transaction_ref\":\"CI-SMOKE\"}" \
  -o /tmp/cert.json || die "certificate issuance failed"
python3 -c "
import json
c = json.load(open('/tmp/cert.json'))
assert 'certificate' in c and 'signature' in c, 'malformed certificate bundle'
n = len(c['certificate'].get('log_evidence', []))
assert n, 'certificate cites no log entries'
print(f'  ok   certificate issued, {n} log entries cited')
" || die "certificate bundle was not well-formed"

log "Verifying that certificate back through the API"
curl -sf -X POST "$API/api/verify" -H 'Content-Type: application/json' -d @/tmp/cert.json \
  | python3 -c "
import sys, json
v = json.load(sys.stdin)
for name, r in v['checks'].items():
    print(f\"  {'ok  ' if r['passed'] else 'FAIL'} {name}: {r['passed']}\")
assert v['valid'], f\"certificate did not verify: {v.get('failed_checks')}\"
" || die "issued certificate failed verification"

log "Re-fetching that certificate by id (what a scanned QR code does)"
CID=$(python3 -c "import json; print(json.load(open('/tmp/cert.json'))['certificate']['certificate_id'])")
# Run inside the container: the check imports backend/signing.py, which needs
# PyNaCl, and only the aggregator image is guaranteed to have it.
$COMPOSE exec -T aggregator python - "$CID" < scripts/check_cert_roundtrip.py \
  || die "certificate did not survive the fetch-by-id round trip"
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/certificates/definitelynotarealcertificateid")
[ "$code" = "404" ] && ok "unknown certificate id returns 404" || die "unknown id returned $code, expected 404"

log "Restarting demo-target: the incident should resolve on its own"
$COMPOSE start demo-target >/dev/null
wait_for 120 "demo rail operational again" rail_is demo operational
curl -sf "$API/api/incidents" | python3 -c "
import sys, json
live = [i for i in json.load(sys.stdin) if not i['is_historical'] and 'Demo Rail' in i['title']]
assert live[0]['status'] == 'resolved', f\"incident still {live[0]['status']}\"
print('  ok   incident resolved by recovery, not by hand')
" || die "incident did not resolve after recovery"

log "Verifying the tamper-evident log end to end"
$COMPOSE exec -T aggregator python verify_log.py > /tmp/verify_log.out 2>&1 \
  || { tail -20 /tmp/verify_log.out >&2; die "log verification reported tampering"; }
tail -1 /tmp/verify_log.out

log "Real-rail status (informational only, depends on this network)"
curl -sf "$API/api/rails" | python3 -c "
import sys, json
for r in json.load(sys.stdin):
    if r['slug'] != 'demo':
        print(f\"  {r['slug']}: {r['status']} ({r.get('witness_coverage')})\")
"

printf '\nSMOKE TEST PASSED\n'
