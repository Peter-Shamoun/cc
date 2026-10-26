#!/usr/bin/env bash
# Local CI gate: runs every step of web/ci/web.yml (jobs `web` and `api`) on this machine, in the same order.
# It is the real gate until the workflow is installed under .github/workflows (docs/frontend_ci.md).
#
#   npm run ci                                 # from web/
#   bash web/ci/run_local.sh [preview-port]    # from anywhere
#
# Env:
#   CI_ONLY=web|api      run one job only
#   FAIL_FAST=1          stop at the first failure
#   SKIP_LIGHTHOUSE=1    skip the Lighthouse budget
#   SKIP_SLOW3G=1        skip the slow-3G first-paint budget (CI_SLOW3G_PORT, default 5241)
#   LH_RUNS=n            Lighthouse runs per form factor (default 1 locally; CI uses 3)
#   CI_API_PORT          port for the API header check (default 8139)
#   CC_PYTHON            Python with api deps (default: /workspace/cc_fe/venvA, ../.venv, python3)
#   CI_SUMMARY           markdown summary path (default test-results/ci-local.md)
#
# Shared-box rules (BRIEF): vitest --maxWorkers=2, at most one server at a time (preview, then API), every
# server is killed on exit, and heavy steps wait for >= 3 GB available memory. Demo mode only: ScriptedLLM,
# synthetic data, no paid API calls, no secrets.
set -uo pipefail
WEB="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$WEB/.." && pwd)"
PORT="${1:-5239}"
API_PORT="${CI_API_PORT:-8139}"
PY="${CC_PYTHON:-}"
for cand in /workspace/cc_fe/venvA/bin/python "$ROOT/.venv/bin/python" python3; do
  [ -n "$PY" ] && break
  { [ -x "$cand" ] || command -v "$cand" >/dev/null 2>&1; } && PY="$cand"
done
LOGS="$WEB/test-results/ci-local"
SUMMARY="${CI_SUMMARY:-$WEB/test-results/ci-local.md}"
mkdir -p "$LOGS"
rm -f "$LOGS"/*.log

SERVER_PID=""
stop_server() {
  [ -n "$SERVER_PID" ] && { kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null; }
  SERVER_PID=""
  # Backstop: anything still bound to our ports from this checkout (never other teams' servers).
  pkill -f "$WEB/node_modules/.bin/vite preview --outDir dist-mock --port $PORT" 2>/dev/null
  pkill -f -- "-m api --port $API_PORT" 2>/dev/null
  return 0
}
trap stop_server EXIT INT TERM

mem_gate() {  # BRIEF: wait while available memory is under 3 GB
  for _ in $(seq 20); do
    avail=$(free -g | awk '/^Mem:/{print $7}')
    [ "${avail:-0}" -ge 3 ] && return 0
    echo "     waiting: ${avail} GB available (< 3 GB)"; sleep 30
  done
  return 1
}

declare -a NAMES STATUS SECS
FAILED=0
N=0
# step <name> <command...>: runs in a subshell, logs to test-results/ci-local/NN-name.log
step() {
  local name="$1"; shift
  N=$((N + 1))
  local log; log="$LOGS/$(printf '%02d' "$N")-$(echo "$name" | tr -c 'a-zA-Z0-9\n' '-' | tr 'A-Z' 'a-z').log"
  local t0=$SECONDS st
  if [ "$FAILED" -gt 0 ] && [ -n "${FAIL_FAST:-}" ]; then st=skip
  elif ( "$@" ) >"$log" 2>&1; then st=ok
  else st=FAIL; FAILED=$((FAILED + 1)); fi
  NAMES+=("$name"); STATUS+=("$st"); SECS+=($((SECONDS - t0)))
  printf '%-4s %-58s %4ss\n' "$st" "$name" "$((SECONDS - t0))"
  [ "$st" = FAIL ] && { echo "     log: $log"; tail -n 15 "$log" | sed 's/^/     | /'; }
  return 0
}

wait_url() { for _ in $(seq 60); do curl -sf -o /dev/null "$1" && return 0; sleep 1; done; return 1; }

start_preview() {
  curl -s -o /dev/null "http://127.0.0.1:$PORT/" && { echo "port $PORT already in use; pass another: npm run ci -- <port>" >&2; return 1; }
  # exec: $! is the server itself, so stop_server really stops it (npx/subshell PIDs orphan the child).
  ( cd "$WEB" && CC_PREVIEW=1 exec "$WEB/node_modules/.bin/vite" preview --outDir dist-mock --port "$PORT" --strictPort ) >"$LOGS/preview-server.log" 2>&1 &
  SERVER_PID=$!
  wait_url "http://127.0.0.1:$PORT/"
}
start_api() {
  curl -s -o /dev/null "http://127.0.0.1:$API_PORT/" && { echo "port $API_PORT already in use; set CI_API_PORT" >&2; return 1; }
  ( cd "$ROOT" && CC_API_MODE=demo CC_API_LLM=scripted exec "$PY" -m api --port "$API_PORT" ) >"$LOGS/api-server.log" 2>&1 &
  SERVER_PID=$!
  wait_url "http://127.0.0.1:$API_PORT/api/health"
}

# Wrapped in a function so bash parses the whole file before running (safe if the file changes mid-run).
main() {
  echo "Local CI on $(git -C "$ROOT" rev-parse --abbrev-ref HEAD)@$(git -C "$ROOT" rev-parse --short HEAD), python=$PY"
  START=$SECONDS
  if [ "${CI_ONLY:-}" != api ]; then
      cd "$WEB"
      step "web: licences + static safety checks" node scripts/license_audit.mjs
      step "web: oxlint (0 warnings)" npm run -s lint
      step "web: npm audit" npm run -s audit
      step "web: typecheck (tsc -b)" npx tsc -b
      mem_gate
      step "web: vitest (CSP, PHI-sink, licence tests incl.)" npx vitest run --maxWorkers=2
      step "web: build" npx vite build
      step "web: bundle-size budget" node scripts/bundle_budget.mjs
      step "web: mock build (dist-mock)" env CC_PREVIEW=1 npx vite build --mode mock --outDir dist-mock
      step "web: Subresource Integrity (dist, dist-mock)" node scripts/sri_check.mjs dist dist-mock
      if start_preview; then
        step "web: CSP check (every fixture, headless Chrome)" env CSP_MODE=mock node scripts/csp_check.mjs "http://127.0.0.1:$PORT/" "$LOGS/shots"
        step "web: response-header check (preview)" node scripts/header_check.mjs "http://127.0.0.1:$PORT/"
        if [ -z "${SKIP_LIGHTHOUSE:-}" ]; then
          mem_gate
          step "web: Lighthouse budget (LH_RUNS=${LH_RUNS:-1})" env LH_RUNS="${LH_RUNS:-1}" LH_OUT="$LOGS/lighthouse" node scripts/lighthouse_budget.mjs "http://127.0.0.1:$PORT/"
        fi
      else
        step "web: vite preview did not start" false
      fi
      stop_server
      if [ -z "${SKIP_SLOW3G:-}" ]; then
        # Own gzip static server (one server at a time: preview is already stopped). Not in web.yml yet: local gate only.
        mem_gate
        step "web: slow-3G first paint (FCP <= 2.5 s, composer)" node scripts/slow3g_budget.mjs dist-mock "${CI_SLOW3G_PORT:-5241}"
      fi
      # tsc -b rewrites the build-info file; keep the tree clean
      git -C "$ROOT" checkout -q -- web/tsconfig.tsbuildinfo 2>/dev/null || true
  fi
  if [ "${CI_ONLY:-}" != web ]; then
    cd "$ROOT"
    mem_gate
    step "api: pytest (demo mode, ScriptedLLM)" env CC_API_MODE=demo CC_API_LLM=scripted PYTHONPATH=. "$PY" -m pytest api/tests -q -p no:cacheprovider
    if "$PY" -m pip_audit --version >/dev/null 2>&1; then
      step "api: pip-audit (api/requirements.txt)" "$PY" -m pip_audit -r api/requirements.txt
    elif command -v uvx >/dev/null 2>&1; then
      step "api: pip-audit (api/requirements.txt, via uvx)" uvx pip-audit -r api/requirements.txt
    else
      step "api: pip-audit not installed (pip install pip-audit)" false
    fi
    if start_api; then
      step "api: response headers (python -m api)" node web/scripts/header_check.mjs none --api "http://127.0.0.1:$API_PORT"
    else
      step "api: python -m api did not start" false
    fi
    stop_server
  fi

  TOTAL=$((SECONDS - START))
  {
    echo "| step | result | seconds |"
    echo "|---|---|---|"
    for i in "${!NAMES[@]}"; do echo "| ${NAMES[$i]} | ${STATUS[$i]} | ${SECS[$i]} |"; done
    echo
    echo "$( [ "$FAILED" -eq 0 ] && echo PASS || echo "FAIL ($FAILED)" ) in ${TOTAL}s on $(git -C "$ROOT" rev-parse --abbrev-ref HEAD)@$(git -C "$ROOT" rev-parse --short HEAD), $(date '+%Y-%m-%d %H:%M %Z')"
  } >"$SUMMARY"
  echo
  [ "$FAILED" -eq 0 ] && echo "Local CI: PASS (${TOTAL}s). Summary: $SUMMARY" || echo "Local CI: FAIL, $FAILED step(s). Logs: $LOGS"
  return $(( FAILED > 0 ))
}
main "$@"
