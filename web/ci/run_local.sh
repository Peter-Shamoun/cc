#!/usr/bin/env bash
# Runs the web/ CI job (web/ci/web.yml) locally. Usage: bash web/ci/run_local.sh [preview-port]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-5239}"
node scripts/license_audit.mjs >/dev/null && echo "ok   licences"
npm run -s lint >/dev/null && echo "ok   lint"
npm run -s audit >/dev/null && echo "ok   npm audit"
npx tsc -b && echo "ok   typecheck"
npx vitest run --maxWorkers=2
npx vite build >/dev/null && echo "ok   build"
node scripts/bundle_budget.mjs
CC_PREVIEW=1 npx vite build --mode mock --outDir dist-mock >/dev/null
CC_PREVIEW=1 npx vite preview --outDir dist-mock --port "$PORT" --strictPort >/dev/null 2>&1 &
PREVIEW=$!
trap 'kill $PREVIEW 2>/dev/null || true' EXIT
for _ in $(seq 60); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/" && break; sleep 1; done
CSP_MODE=mock node scripts/csp_check.mjs "http://127.0.0.1:$PORT/"
node scripts/header_check.mjs "http://127.0.0.1:$PORT/"
# Lighthouse budget (scripts/perf_budget.json). One run per form factor locally (CI uses the median of 3); SKIP_LIGHTHOUSE=1 skips it.
[ -n "${SKIP_LIGHTHOUSE:-}" ] || LH_RUNS="${LH_RUNS:-1}" node scripts/lighthouse_budget.mjs "http://127.0.0.1:$PORT/"
