#!/usr/bin/env bash
set -u -o pipefail
# post_openai.sh — POST a messages array to an OpenAI-compatible chat completions endpoint
# using SSE streaming. Reassembles delta content / tool_calls and emits the same stdout
# contract as the prior non-streaming version (text body, or "TOOL_CALLS\n<json>").
#
# Requires: curl, python3 (stdlib only — no third-party packages)
#
# usage: API_BASE_URL=<url> API_KEY_CURL_CFG=<auth-header-curl-config> MODEL=<model> post_openai.sh <messages.json>
# optional envar param: MAX_TOKENS=<max-response-token-count> (default: 32768)
# API_KEY_CURL_CFG format: header = "Authorization: Bearer the-api-key-here"
#
# messages.json format: JSON array of {"role": "<role>", "content": "<text>"} objects.
# Response text is written to stdout. All other output goes to stderr.
#
# Optional envars:
#   DEBUG_POST=true       — dump the request payload to stderr before sending
#   DEBUG_RESPONSE=true   — dump the raw SSE stream + reassembled output to stderr
#   POST_OPENAI_TEST=1    — self-test mode: read SSE fixture from $1 instead of curling.
#                           No auth, no network, no envvar checks beyond MAX_TOKENS.

THIS_SCRIPT=$(basename "$0")

DEFAULT_MAX_TOKENS=$((1024 * 32))

function usage() {
  [[ -n "${*}" ]] && echo "error: ${*}" 1>&2
  echo "usage: API_BASE_URL=<url> API_KEY_CURL_CFG=<curl-auth-header-config-file> MODEL=<model> ${THIS_SCRIPT} <messages.json>" 1>&2
  echo "optional envar param MAX_TOKENS=<max-response-token-count> (default: ${DEFAULT_MAX_TOKENS})"
  echo 'API_KEY_CURL_CFG *exact* file format:' 1>&2
  echo 'header = "Authorization: Bearer your-api-key-here"' 1>&2
  exit 1
}

# Resolve required tools early — used by both production and test paths.
command -v curl &>/dev/null || { echo "error: curl is required" 1>&2; exit 1; }
PYTHON=$(command -v python3 2>/dev/null)
[[ -x "${PYTHON}" ]] || { echo "error: python3 is required for JSON handling" 1>&2; exit 1; }

MAX_TOKENS=${MAX_TOKENS:-${DEFAULT_MAX_TOKENS}}

# reassemble_stream <chunks_file>
# Reads a file containing one JSON chunk per line (already de-SSE'd; "[DONE]" marker
# lines and pre-stream errors removed) and prints either:
#   <reassembled-content>
# or, if tool_calls deltas were present:
#   TOOL_CALLS
#   <json-array>
#
# A single python invocation processes the whole file to amortize fork cost.
function reassemble_stream() {
  local chunks_file="${1}"
  "${PYTHON}" -c '
import sys, json
content_parts = []
tc_map = {}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    choices = obj.get("choices") or []
    if not choices:
        continue
    delta = choices[0].get("delta") or {}
    c = delta.get("content")
    if isinstance(c, str):
        content_parts.append(c)
    for tc in delta.get("tool_calls") or []:
        idx = tc.get("index")
        if idx is None:
            continue
        key = str(idx)
        slot = tc_map.setdefault(key, {
            "id": None,
            "type": "function",
            "function": {"name": None, "arguments": ""},
        })
        if tc.get("id"):
            slot["id"] = tc["id"]
        if tc.get("type"):
            slot["type"] = tc["type"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if isinstance(fn.get("arguments"), str):
            slot["function"]["arguments"] += fn["arguments"]
if tc_map:
    ordered = [tc_map[k] for k in sorted(tc_map.keys(), key=lambda x: int(x))]
    sys.stdout.write("TOOL_CALLS\n")
    sys.stdout.write(json.dumps(ordered))
else:
    sys.stdout.write("".join(content_parts))
' < "${chunks_file}"
}

# demux_sse <chunks_file>
# Reads SSE bytes from stdin, writes one JSON object per line to <chunks_file>.
# Strips "data: " prefix, blank lines, comment lines (":..."), and "[DONE]".
# If a "data:" payload contains an "error" object, prints it verbatim to stderr
# and returns 2. If [DONE] is seen, sets DONE_SEEN=1 in the calling shell via
# a sentinel file. Returns 0 if at least one data event was processed and no
# error was seen.
function demux_sse() {
  local chunks_file="${1}"
  local done_marker="${chunks_file}.done"
  local error_marker="${chunks_file}.error"
  local got_data_marker="${chunks_file}.gotdata"
  : > "${chunks_file}"
  rm -f "${done_marker}" "${error_marker}" "${got_data_marker}"

  while IFS= read -r line; do
    # Strip a single trailing CR (some servers send CRLF).
    line="${line%$'\r'}"
    [[ -z "${line}" ]] && continue
    [[ "${line:0:1}" == ":" ]] && continue
    [[ "${line}" != "data:"* ]] && continue
    local payload="${line#data:}"
    # Tolerate "data:foo" as well as "data: foo".
    payload="${payload# }"
    if [[ "${payload}" == "[DONE]" ]]; then
      : > "${done_marker}"
      break
    fi
    : > "${got_data_marker}"
    # Detect mid-stream error events: a chunk carrying a top-level "error" key.
    # Use cheap textual prefilter; only run python if "error" appears.
    if [[ "${payload}" == *'"error"'* ]]; then
      local is_err
      is_err=$("${PYTHON}" -c '
import sys, json
try:
    o = json.loads(sys.stdin.read())
    print("1" if o.get("error") is not None else "0")
except Exception:
    print("0")
' <<< "${payload}" 2>/dev/null || echo "0")
      if [[ "${is_err}" == "1" ]]; then
        echo "error: mid-stream error event:" 1>&2
        echo "${payload}" 1>&2
        : > "${error_marker}"
        # Drain the rest silently; the caller will detect the error marker.
        cat > /dev/null
        return 2
      fi
    fi
    printf '%s\n' "${payload}" >> "${chunks_file}"
  done

  [[ -f "${error_marker}" ]] && return 2
  [[ -f "${got_data_marker}" ]] || return 1
  return 0
}

# ---------------------------------------------------------------------------
# Self-test path: parse a fixture file and emit reassembled output. No curl,
# no auth, no network. Exits 0 on success.
# ---------------------------------------------------------------------------
if [[ "${POST_OPENAI_TEST:-}" == "1" ]]; then
  fixture="${1:-}"
  [[ -n "${fixture}" && -f "${fixture}" ]] || {
    echo "error: POST_OPENAI_TEST=1 requires a fixture file path as \$1" 1>&2
    exit 1
  }
  testdir=$(mktemp -d) || { echo "error: mktemp failed" 1>&2; exit 1; }
  trap "rm -rf '${testdir}'" EXIT
  chunks="${testdir}/chunks.jsonl"
  demux_sse "${chunks}" < "${fixture}"
  demux_rc=$?
  if [[ ${demux_rc} -eq 2 ]]; then
    exit 1
  fi
  if [[ ${demux_rc} -eq 1 ]]; then
    echo "error: fixture contained no data events" 1>&2
    exit 1
  fi
  reassembled=$(reassemble_stream "${chunks}")
  if [[ "${DEBUG_RESPONSE:-false}" == "true" ]]; then
    echo "--- chunks ---" 1>&2
    cat "${chunks}" 1>&2
    echo "--- reassembled ---" 1>&2
    echo "${reassembled}" 1>&2
  fi
  if [[ -z "${reassembled}" ]]; then
    echo "error: empty reassembled output" 1>&2
    exit 1
  fi
  # Match the production stdout contract: TOOL_CALLS payloads end without
  # trailing newline; content payloads get a single trailing newline via echo.
  if [[ "${reassembled}" == "TOOL_CALLS"* ]]; then
    printf '%s' "${reassembled}"
    printf '\n'
  else
    echo "${reassembled}"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Production path
# ---------------------------------------------------------------------------

[[ -n "${API_BASE_URL:-}" ]]      || usage "API_BASE_URL must be set"
[[ -n "${API_KEY_CURL_CFG:-}" ]]  || usage "API_KEY_CURL_CFG must be set"
[[ -f "${API_KEY_CURL_CFG}" ]]    || usage "API_KEY_CURL_CFG not found: ${API_KEY_CURL_CFG}"
{
  [[ $(wc -l < "${API_KEY_CURL_CFG}" | awk '{print $1}') -le 1 ]] && \
  grep '^header = "Authorization: Bearer [a-zA-Z0-9_+/.:=-]*"$' < "${API_KEY_CURL_CFG}" > /dev/null
} || usage "API_KEY_CURL_CFG has invalid format: ${API_KEY_CURL_CFG}"
[[ -n "${MODEL:-}" ]]               || usage "MODEL must be set"
[[ -f "${MESSAGES_FILE:=${1:-}}" ]] || usage "messages file not found: ${MESSAGES_FILE}"

# list_models
# Writes newline-separated model IDs to stdout.
function list_models() {
  local resp
  resp=$(curl -K - < "${API_KEY_CURL_CFG}" -s "${API_BASE_URL}/models") || {
    echo "error: curl failed querying models" 1>&2
    return 1
  }
  "${PYTHON}" -c '
import sys, json
print("\n".join(m["id"] for m in json.loads(sys.stdin.read())["data"]))
' <<< "${resp}"
}

# resolve_model <name>
# Resolves MODEL to an exact model ID. Accepts exact match or unambiguous substring.
function resolve_model() {
  local name="${1}" models matches count
  models=$(list_models) || return 1

  if echo "${models}" | grep -qxF "${name}"; then
    echo "${name}"
    return 0
  fi

  matches=$(echo "${models}" | grep -F "${name}")
  count=$(echo "${matches}" | grep -c .)
  if [[ ${count} -eq 1 ]]; then
    echo "${matches}"
    return 0
  elif [[ ${count} -eq 0 ]]; then
    echo "error: no model matching '${name}'. available models:" 1>&2
    echo "${models}" 1>&2
    return 1
  else
    echo "error: '${name}' is ambiguous — ${count} candidates:" 1>&2
    echo "${matches}" 1>&2
    return 1
  fi
}

# is_model_error_text <stderr_text>
# Returns 0 if the captured pre-stream error text looks like a model-name error.
# Operates on plain text (the raw HTTP body curl produced when the request was
# rejected before SSE began).
function is_model_error_text() {
  local raw="${1}"
  # Try JSON parse first; fall back to substring match.
  "${PYTHON}" -c '
import sys, json, re
try:
  body = json.loads(sys.stdin.read())
  err  = body.get("error") or body.get("detail") or {}
  code = err.get("code", "") or ""
  msg  = err.get("message", "") or ""
  code_pat = r"model_not_found|invalid_model|model_not_allowed"
  msg_pat  = r"does not exist|not available|no such model"
  sys.exit(0 if re.search(code_pat, code, re.I) or re.search(msg_pat, msg, re.I) else 1)
except Exception:
  sys.exit(1)
' <<< "${raw}" && return 0
  # Last-resort textual scan.
  echo "${raw}" | grep -Eqi 'model_not_found|invalid_model|model_not_allowed|does not exist|not available|no such model'
}

# do_post_streaming <model> <out_chunks_file> <out_raw_file>
# POSTs a streaming request. On success, writes one JSON chunk per line to
# <out_chunks_file>. On failure, writes the raw body (or partial stream) to
# <out_raw_file> and returns non-zero. Possible return values:
#   0  — clean stream, [DONE] received, at least one data event
#   1  — pre-stream HTTP error or curl connect failure
#   2  — mid-stream SSE "error" event
#   3  — connection drop before [DONE]
function do_post_streaming() {
  local model="${1}" chunks_file="${2}" raw_file="${3}"
  local tmpdir payload_file curl_rc demux_rc
  tmpdir=$(dirname "${chunks_file}")
  payload_file="${tmpdir}/payload.json"

  printf '{"model": "%s", "max_tokens": %s, "top_p": 1.0, "temperature": 0.1, "stream": true, "messages": ' \
    "${model}" "${MAX_TOKENS}" > "${payload_file}"
  cat "${MESSAGES_FILE}" >> "${payload_file}"
  printf '}' >> "${payload_file}"

  [[ "${DEBUG_POST:-false}" == "true" ]] && \
    { echo "POST ${API_BASE_URL}/chat/completions payload:" 1>&2; cat "${payload_file}" 1>&2; }

  : > "${raw_file}"
  : > "${chunks_file}"

  # Pipeline: curl -> tee (raw archive) -> demux_sse (chunks).
  # We need both curl's exit code and demux's. Use PIPESTATUS.
  set +o pipefail
  curl -N --no-buffer --max-time 600 --connect-timeout 30 \
       -K - < "${API_KEY_CURL_CFG}" -s -S \
       -X POST "${API_BASE_URL}/chat/completions" \
       -H "Content-Type: application/json" \
       -H "Accept: text/event-stream" \
       -d "@${payload_file}" \
    | tee "${raw_file}" \
    | demux_sse "${chunks_file}"
  local pipestatus=("${PIPESTATUS[@]}")
  set -o pipefail
  curl_rc=${pipestatus[0]}
  demux_rc=${pipestatus[2]}

  if [[ ${demux_rc} -eq 2 ]]; then
    return 2
  fi

  if [[ ${curl_rc} -ne 0 ]]; then
    if [[ ${demux_rc} -eq 0 ]]; then
      # Curl errored mid-stream after [DONE] was processed — treat as success.
      return 0
    fi
    if [[ -s "${chunks_file}" ]]; then
      echo "error: stream terminated before [DONE] (curl exit ${curl_rc}, partial chunks captured)" 1>&2
      return 3
    fi
    echo "error: curl failed (exit ${curl_rc}) before any SSE data" 1>&2
    if [[ -s "${raw_file}" ]]; then
      echo "--- raw body ---" 1>&2
      cat "${raw_file}" 1>&2
    fi
    return 1
  fi

  # curl succeeded.
  if [[ ${demux_rc} -eq 1 ]]; then
    # No SSE data events at all — server returned a non-SSE body (e.g. JSON error).
    echo "error: no SSE data events received" 1>&2
    if [[ -s "${raw_file}" ]]; then
      echo "--- raw body ---" 1>&2
      cat "${raw_file}" 1>&2
    fi
    return 1
  fi

  return 0
}

# Top-level orchestration: stream once; on pre-stream error that looks like a
# model-name problem, retry with substring resolution.
TMPDIR_SESSION=$(mktemp -d) || { echo "error: mktemp failed" 1>&2; exit 1; }
trap "rm -rf '${TMPDIR_SESSION}'" EXIT
chunks_file="${TMPDIR_SESSION}/chunks.jsonl"
raw_file="${TMPDIR_SESSION}/raw.sse"

do_post_streaming "${MODEL}" "${chunks_file}" "${raw_file}"
rc=$?

if [[ ${rc} -eq 1 ]] && [[ -s "${raw_file}" ]] && is_model_error_text "$(cat "${raw_file}")"; then
  echo "warning: model '${MODEL}' not found — attempting substring resolution" 1>&2
  RESOLVED=$(resolve_model "${MODEL}") || {
    echo "error: update MODEL to a valid model name" 1>&2; exit 1
  }
  echo "warning: resolved '${MODEL}' → '${RESOLVED}' — update MODEL to avoid this fallback" 1>&2
  do_post_streaming "${RESOLVED}" "${chunks_file}" "${raw_file}"
  rc=$?
fi

if [[ ${rc} -ne 0 ]]; then
  exit 1
fi

if [[ "${DEBUG_RESPONSE:-false}" == "true" ]]; then
  echo "--- raw SSE stream ---" 1>&2
  cat "${raw_file}" 1>&2
  echo "--- chunks ---" 1>&2
  cat "${chunks_file}" 1>&2
fi

reassembled=$(reassemble_stream "${chunks_file}")

if [[ "${DEBUG_RESPONSE:-false}" == "true" ]]; then
  echo "--- reassembled ---" 1>&2
  echo "${reassembled}" 1>&2
fi

if [[ -z "${reassembled}" ]]; then
  echo "error: no content in stream — raw response follows" 1>&2
  cat "${raw_file}" 1>&2
  exit 1
fi

# Match the prior contract: text content gets a trailing newline (echo);
# TOOL_CALLS output already has its embedded newline between marker and JSON.
if [[ "${reassembled}" == "TOOL_CALLS"* ]]; then
  printf '%s' "${reassembled}"
  printf '\n'
else
  echo "${reassembled}"
fi
