#!/usr/bin/env bash
set -euo pipefail
SLUG=${1:?model slug required}
case "$SLUG" in
  qwen35-4b) HF='bartowski/Qwen_Qwen3.5-4B-GGUF:Q4_K_M' ;;
  qwen35-9b) HF='bartowski/Qwen_Qwen3.5-9B-GGUF:Q4_K_M' ;;
  *) echo "unsupported model: $SLUG" >&2; exit 2 ;;
esac
docker pull ghcr.io/ggml-org/llama.cpp:server
docker run -d --rm --name benchmark-llm --network host ghcr.io/ggml-org/llama.cpp:server -hf "$HF" --alias "$SLUG" --host 127.0.0.1 --port 8080 -c 4096 -np 1 -t 4 -tb 4 --cache-type-k q8_0 --cache-type-v q8_0 --reasoning off --no-ui
for i in $(seq 1 300); do
  curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 && { echo MODEL_READY; exit 0; }
  if ! docker ps --format '{{.Names}}' | grep -qx benchmark-llm; then docker logs benchmark-llm || true; exit 1; fi
  sleep 2
done
docker logs benchmark-llm 2>&1 | tail -200 || true
exit 1
