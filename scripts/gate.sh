#!/usr/bin/env sh
# Phase 0 gate: predicted vs measured sun-hit times. Args go to sunhit-gate, e.g.
#   scripts/gate.sh --from 2026-10-01 --to 2026-10-14
#   scripts/gate.sh --labels data/labels.csv --json data/gate.json     (paths under data/)
set -e
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths as written
docker compose run --rm --no-deps -v ./data:/app/data horizon-builder sunhit-gate "$@"
