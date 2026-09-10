#!/usr/bin/env bash
set -euo pipefail
mkdir -p orchestra-output

docker exec freshfruit-app bash -lc 'cd /app && git diff -- . || true' \
  > orchestra-output/target-diff.txt 2>&1 || true
docker exec freshfruit-app bash -lc 'cd /app && git status --short || true' \
  > orchestra-output/target-status.txt 2>&1 || true
docker logs freshfruit-db > orchestra-output/postgres.log 2>&1 || true
docker logs benchmark-llm > orchestra-output/model.log 2>&1 || true

cat > orchestra-output/run-metadata.json <<JSON
{
  "github_run_id": "${GITHUB_RUN_ID:-}",
  "github_sha": "${GITHUB_SHA:-}",
  "model_slug": "${MODEL_SLUG:-}",
  "model_label": "${MODEL_LABEL:-}",
  "role": "${ORCHESTRA_ROLE:-}",
  "worker": "${ORCHESTRA_WORKER:-}"
}
JSON
