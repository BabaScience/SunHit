#!/usr/bin/env sh
# Download the open-data samples for a region (default: Rome) into data/samples/<region>/raw.
set -e
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths as written
REGION=${1:-infra/regions/rome.json}
docker compose --profile jobs run --rm -e PYTHONUNBUFFERED=1 --no-deps dsm-builder \
  sunhit-fetch-samples --region "$REGION" --data-dir data/samples
