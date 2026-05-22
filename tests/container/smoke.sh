#!/usr/bin/env bash
# Smoke-test the built image: boots, serves /health + /ready, then stops.
set -euo pipefail
IMAGE="${1:?usage: $0 <image>}"
cid=$(docker run -d --rm \
  -e GEMINI_API_KEY="${GEMINI_API_KEY:-smoke}" \
  -e FASTMCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 -e FASTMCP_PORT=8000 \
  -p 8000:8000 "$IMAGE")
trap 'docker kill "$cid" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  curl -fsS localhost:8000/health >/dev/null 2>&1 \
    && curl -fsS localhost:8000/ready >/dev/null 2>&1 && ok=1 && break
  sleep 1
done
[[ "${ok:-}" == 1 ]] || { echo "FAIL: server did not come up"; docker logs "$cid"; exit 1; }
echo "OK: booted and serving /health + /ready"
start=$(date +%s); docker stop --timeout=30 "$cid"; elapsed=$(( $(date +%s) - start ))
(( elapsed > 12 )) && echo "WARN: shutdown took ${elapsed}s — check SIGTERM forwarding" \
  || echo "OK: clean shutdown in ${elapsed}s"
