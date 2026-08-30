#!/usr/bin/env bash
# Thin launcher: delegates to post-openai.py. The python implementation owns
# all behavior; see that file for env-var contract and output format —
# including the optional USAGE_STATS_FILE token-usage side channel (one JSON
# line appended per successful call; never on stdout). Unit tests live in
# tests/test_post_openai.py (run via `just test`).
THIS_DIR=$(cd "$(dirname "$0")" && pwd)
PYTHON=$(command -v python3 2>/dev/null) || \
  PYTHON=$(command -v python 2>/dev/null) || \
  { echo "neither python3 nor python found. python3 required (sorry)." 1>&2; exit 1; }
exec "${PYTHON}" "${THIS_DIR}/post-openai.py" "$@"
