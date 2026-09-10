#!/usr/bin/env bash
set -euo pipefail
: "${MODEL_SLUG:?MODEL_SLUG required}"
: "${MODEL_HF:?MODEL_HF required}"

docker pull ghcr.io/ggml-org/llama.cpp:server
docker rm -f benchmark-llm >/dev/null 2>&1 || true

docker run -d --rm --name benchmark-llm --network host \
  ghcr.io/ggml-org/llama.cpp:server \
  -hf "${MODEL_HF}" \
  --alias "${MODEL_SLUG}" \
  --host 127.0.0.1 --port 8080 \
  -c 4096 -np 1 -t 4 -tb 4 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --reasoning off --no-ui

for i in $(seq 1 360); do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    echo "Model ${MODEL_SLUG} is healthy."
    exit 0
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx benchmark-llm; then
    echo 'Model container exited'
    docker ps -a
    docker logs benchmark-llm 2>&1 | tail -200 || true
    exit 1
  fi
  sleep 2
done
echo 'Model health check timed out'
docker logs benchmark-llm 2>&1 | tail -200 || true
exit 1
