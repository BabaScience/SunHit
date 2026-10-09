#!/usr/bin/env sh
# Fuse the samples into the surface model, publish it to the object store, load footprints into PostGIS.
set -e
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths as written
REGION=${1:-infra/regions/rome.json}
docker compose --profile jobs run --rm -e PYTHONUNBUFFERED=1 dsm-builder \
  sunhit-dsm-build --region "$REGION" --data-dir data/samples
