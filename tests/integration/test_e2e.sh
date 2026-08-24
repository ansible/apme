#!/usr/bin/env bash
# End-to-end integration test: rebuild containers, start pod, run scan, assert violations.
# Usage: tests/integration/test_e2e.sh [--skip-build] [--skip-teardown]
#
# Exits 0 on success, 1 on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

SKIP_BUILD=0
SKIP_TEARDOWN=0
for arg in "$@"; do
  case "$arg" in
    --skip-build)    SKIP_BUILD=1 ;;
    --skip-teardown) SKIP_TEARDOWN=1 ;;
  esac
done

PASS=0
FAIL=0
log()  { echo "  [INFO] $*"; }
pass() { echo "  [PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL + 1)); }

# ─── Phase 1: Build ────────────────────────────────────────────────────────────
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> Phase 1: Building container images"
  bash containers/podman/build.sh
  log "Images built"
else
  log "Skipping build (--skip-build)"
fi

# ─── Phase 2: Tear down old pod + start fresh ──────────────────────────────────
echo "==> Phase 2: Starting pod"
podman pod rm -f apme-pod 2>/dev/null || true
podman play kube containers/podman/pod.yaml
log "Pod created, waiting for Running status..."

MAX_WAIT=90
for i in $(seq 1 "$MAX_WAIT"); do
  STATUS=$(podman pod list --filter name=apme-pod --format "{{.Status}}" 2>/dev/null || true)
  if [[ "$STATUS" == "Running" ]]; then
    break
  fi
  if [[ $i -eq $MAX_WAIT ]]; then
    fail "Pod did not reach Running within ${MAX_WAIT}s (status: ${STATUS:-none})"
    podman pod logs apme-pod 2>&1 | tail -40
    exit 1
  fi
  sleep 1
done
pass "Pod is Running"

# ─── Phase 3: Health check ─────────────────────────────────────────────────────
echo "==> Phase 3: Health check"
sleep 3  # let services finish starting
HEALTH_OUTPUT=$(podman run --rm --pod apme-pod \
  -e APME_ENGINE_ADDRESS=127.0.0.1:50051 \
  --entrypoint apme apme-cli:latest \
  health-check 2>&1) || true
echo "$HEALTH_OUTPUT"
if echo "$HEALTH_OUTPUT" | grep -q "overall: ok"; then
  pass "Health check passed"
else
  fail "Health check did not report overall: ok"
fi

# ─── Phase 4: Scan test playbook ───────────────────────────────────────────────
echo "==> Phase 4: Scanning test playbook"
TEST_DIR="$ROOT/tests/integration"
SCAN_OUTPUT=$(podman run --rm --pod apme-pod \
  -v "$TEST_DIR":/workspace:ro,Z \
  -w /workspace \
  -e APME_ENGINE_ADDRESS=127.0.0.1:50051 \
  --entrypoint apme apme-cli:latest \
  check --json . 2>/dev/null) || true

echo "$SCAN_OUTPUT" | python3 -m json.tool 2>/dev/null || echo "$SCAN_OUTPUT"

# Extract violation count
COUNT=$(echo "$SCAN_OUTPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))" 2>/dev/null || echo "0")
log "Total violations: $COUNT"

if [[ "$COUNT" -gt 0 ]]; then
  pass "Scan returned $COUNT violation(s)"
else
  fail "Scan returned 0 violations (expected >0)"
fi

# ─── Phase 5: Assert expected rule IDs ─────────────────────────────────────────
echo "==> Phase 5: Asserting expected violations"

assert_rule() {
  local rule_id="$1"
  local desc="$2"
  if echo "$SCAN_OUTPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ids = [v.get('rule_id','') for v in d.get('violations',[])]
sys.exit(0 if '$rule_id' in ids else 1)
" 2>/dev/null; then
    pass "$rule_id: $desc"
  else
    fail "$rule_id: $desc (not found in output)"
  fi
}

# OPA rules
assert_rule "L007" "shell when command suffices"
assert_rule "L010" "ignore_errors without register"
assert_rule "L021" "missing explicit mode on file/copy"
assert_rule "L025" "name not starting uppercase"
assert_rule "R118" "inbound transfer (annotation-based)"

# Native rules
assert_rule "native:L046" "free-form args (native)"

# Modernize rules (plugin introspection via ansible-core)
assert_rule "M001" "FQCN resolution (yum, copy)"

# Ansible validator lint rules (argspec)
assert_rule "L058" "argspec validation - docstring (invalid_param)"
assert_rule "L059" "argspec validation - mock/patch (invalid_param)"

# ─── Phase 6: Assert no duplicates ─────────────────────────────────────────────
echo "==> Phase 6: Checking for duplicate violations"
DUPS=$(echo "$SCAN_OUTPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
seen = set()
dups = []
for v in d.get('violations', []):
    line = v.get('line')
    if isinstance(line, list):
        line = tuple(line)
    key = (v.get('rule_id',''), v.get('file',''), line)
    if key in seen:
        dups.append(key)
    seen.add(key)
print(len(dups))
" 2>/dev/null || echo "?")

if [[ "$DUPS" == "0" ]]; then
  pass "No duplicate violations"
else
  fail "$DUPS duplicate violation(s) found"
fi

# ─── Phase 7: Engine daemon logs ───────────────────────────────────────────────
echo "==> Phase 7: Engine daemon logs"
ENGINE_LOGS=$(podman logs apme-pod-engine 2>&1 || true)
echo "$ENGINE_LOGS" | tail -10

if echo "$ENGINE_LOGS" | grep -q "Opa="; then
  pass "Engine logged OPA violation count"
else
  fail "Engine did not log OPA violation count"
fi

if echo "$ENGINE_LOGS" | grep -q "Native="; then
  pass "Engine logged Native violation count"
else
  fail "Engine did not log Native violation count"
fi

# ─── Phase 8: OPA and Native validator logs ───────────────────────────────────
echo "==> Phase 8: OPA wrapper validator logs"
OPA_LOGS=$(podman logs apme-pod-opa 2>&1 || true)
echo "$OPA_LOGS" | tail -10

if echo "$OPA_LOGS" | grep -q "OPA returned"; then
  pass "OPA wrapper logged violation count"
else
  fail "OPA wrapper did not log violation count"
fi

echo "==> Phase 8b: Native validator logs"
NATIVE_LOGS=$(podman logs apme-pod-native 2>&1 || true)
echo "$NATIVE_LOGS" | tail -10

if echo "$NATIVE_LOGS" | grep -q "Native validator returned"; then
  pass "Native validator logged violation count"
else
  fail "Native validator did not log violation count"
fi

# ─── Phase 9: Ansible validator logs ──────────────────────────────────────────
echo "==> Phase 9: Ansible validator logs"
ANSIBLE_LOGS=$(podman logs apme-pod-ansible 2>&1 || true)
echo "$ANSIBLE_LOGS" | tail -15

if echo "$ANSIBLE_LOGS" | grep -q "introspecting"; then
  pass "Ansible validator ran plugin introspection"
else
  fail "Ansible validator did not run plugin introspection"
fi

if echo "$ANSIBLE_LOGS" | grep -q "argspec"; then
  pass "Ansible validator ran argspec check"
else
  fail "Ansible validator did not run argspec check"
fi

# ─── Teardown ──────────────────────────────────────────────────────────────────
if [[ "$SKIP_TEARDOWN" -eq 0 ]]; then
  echo "==> Teardown: stopping pod"
  podman pod rm -f apme-pod 2>/dev/null || true
  log "Pod removed"
else
  log "Skipping teardown (--skip-teardown)"
fi

# ─── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Integration test: $PASS passed, $FAIL failed"
echo "========================================"
[[ "$FAIL" -eq 0 ]]
