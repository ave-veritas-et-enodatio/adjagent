#!/usr/bin/env bash
# Thin launcher: delegates to post-openai.py. The python implementation owns
# all behavior; see that file for env-var contract, output format, and
# self-test mode (POST_OPENAI_TEST=1).
THIS_DIR=$(cd "$(dirname "$0")" && pwd)
PYTHON=$(command -v python3 2>/dev/null) || \
  PYTHON=$(command -v python 2>/dev/null) || \
  { echo "neither python3 nor python found. python3 required (sorry)." 1>&2; exit 1; }
exec "${PYTHON}" "${THIS_DIR}/post-openai.py" "$@"
