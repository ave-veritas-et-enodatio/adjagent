#!/usr/bin/env bash
set -u -o pipefail
# post_openai.sh — POST a messages array to an OpenAI-compatible chat completions endpoint.
# Requires: curl; one of: jq, python3, python
#
# usage: API_BASE_URL=<url> API_KEY_CURL_CFG=<auth-header-curl-config> MODEL=<model> post_openai.sh <messages.json>
# API_KEY_CURL_CFG format: header = "Authorization: Bearer the-api-key-here"
#
# messages.json format: JSON array of {"role": "<role>", "content": "<text>"} objects.
# Response text is written to stdout. All other output goes to stderr.
#
# Optional envars:
#   DEBUG_POST=true       — dump the request payload to stderr before sending
#   DEBUG_RESPONSE=true   — dump the raw API response JSON to stderr

THIS_SCRIPT=$(basename "$0")

function usage() {
  [[ -n "${*}" ]] && echo "error: ${*}" 1>&2
  echo "usage: API_BASE_URL=<url> API_KEY_CURL_CFG=<curl-auth-header-config-file> MODEL=<model> ${THIS_SCRIPT} <messages.json>" 1>&2
  echo 'API_KEY_CURL_CFG *exact* file format:' 1>&2
  echo 'header = "Authorization: Bearer your-api-key-here"' 1>&2
  exit 1
}

[[ -n "${API_BASE_URL}" ]]        || usage "API_BASE_URL must be set"
[[ -n "${API_KEY_CURL_CFG}" ]]        || usage "API_KEY_CURL_CFG must be set"
[[ -f "${API_KEY_CURL_CFG}" ]]        || usage "API_KEY_CURL_CFG not found: ${API_KEY_CURL_CFG}"
{
  [[ $(wc -l < "${API_KEY_CURL_CFG}" | awk '{print $1}') -le 1 ]] && \
  grep '^header = "Authorization: Bearer [a-zA-Z0-9_+/.:=-]*"$' < "${API_KEY_CURL_CFG}" > /dev/null
} || usage "API_KEY_CURL_CFG has invalid format: ${API_KEY_CURL_CFG}"
[[ -n "${MODEL}" ]]               || usage "MODEL must be set"
[[ -f "${MESSAGES_FILE:=${1}}" ]] || usage "messages file not found: ${MESSAGES_FILE}"

command -v curl &>/dev/null || { echo "error: curl is required" 1>&2; exit 1; }

PYTHON=
# Resolve JSON tool: jq preferred, fall back to python3/python
JQ=$(command -v jq 2>/dev/null)
[[ -x "${JQ}" ]] || PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
[[ -x "${JQ}" || -x "${PYTHON}" ]] || \
  { echo "error: jq or python3 required for JSON handling" 1>&2; exit 1; }

# list_models
# Writes newline-separated model IDs to stdout.
function list_models() {
  local resp
  resp=$(curl -K - < "${API_KEY_CURL_CFG}" -s "${API_BASE_URL}/models") || {
    echo "error: curl failed querying models" 1>&2
    return 1
  }
  if [[ -n "${JQ}" ]]; then
    "${JQ}" -r '.data[].id' <<< "${resp}"
  else
    "${PYTHON}" -c '
import sys, json
print("\n".join(m["id"] for m in json.loads(sys.stdin.read())["data"]))
' <<< "${resp}"
  fi
}

# resolve_model <name>
# Resolves MODEL to an exact model ID. Accepts exact match or unambiguous substring.
# Writes the resolved model ID to stdout; exits 1 on ambiguity or no match.
function resolve_model() {
  local name="${1}" models matches count
  models=$(list_models) || return 1

  # exact match
  if echo "${models}" | grep -qxF "${name}"; then
    echo "${name}"
    return 0
  fi

  # substring match
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


# extract_content <json_string>
# Writes the assistant message content to stdout.
# If content is a structured array, joins text items.
# If no text content but tool_calls present, outputs TOOL_CALLS\n<json>.
function extract_content() {
  local raw="${1}"
  if [[ -n "${JQ}" ]]; then
    "${JQ}" -r '
      .choices[0].message as $msg |
      ($msg.content) as $c |
      if $c == null or $c == "" then
        if ($msg.tool_calls // null) != null then
          "TOOL_CALLS\n" + ($msg.tool_calls | tojson)
        else
          empty
        end
      elif ($c | type) == "array" then
        [ $c[] | select(.type == "text") | .text ] | join("")
      else
        $c
      end
    ' <<< "${raw}"
  else
    "${PYTHON}" -c '
import sys, json
resp = json.loads(sys.stdin.read())
msg = resp.get("choices", [{}])[0].get("message", {})
content = msg.get("content")
if isinstance(content, list):
    text = "".join(item.get("text", "") for item in content if item.get("type") == "text")
    if text:
        print(text, end="")
    else:
        content = None
elif isinstance(content, str) and content:
    print(content, end="")
else:
    content = None
if content is None:
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        print("TOOL_CALLS")
        print(json.dumps(tool_calls), end="")
' <<< "${raw}"
  fi
}

# is_model_error <json_string>
# Returns 0 if the response is a model-related error, 1 otherwise.
# Handles both OpenAI-style {error: {code, message}} and {detail: {code, message}}.
function is_model_error() {
  local raw="${1}"
  if [[ -n "${JQ}" ]]; then
    "${JQ}" -e '
      (.error // .detail // {}) as $e |
      ($e.code    // "" | test("model_not_found|invalid_model|model_not_allowed"; "i")) or
      ($e.message // "" | test("does not exist|not available|no such model"; "i"))
    ' <<< "${raw}" &>/dev/null
  else
    "${PYTHON}" -c '
import sys, json, re
try:
  body = json.loads(sys.stdin.read())
  err  = body.get("error") or body.get("detail") or {}
  code = err.get("code", "")
  msg  = err.get("message", "")
  code_pat = r"model_not_found|invalid_model|model_not_allowed"
  msg_pat  = r"does not exist|not available|no such model"
  sys.exit(0 if re.search(code_pat, code, re.I) or re.search(msg_pat, msg, re.I) else 1)
except Exception: sys.exit(1)
' <<< "${raw}"
  fi
}

# do_post <model>
# POSTs with the given model name; writes response body to stdout.
function do_post() {
  local model="${1}" tmpdir payload_file bodytmp http_code
  tmpdir=$(mktemp -d) || { echo "error: mktemp failed" 1>&2; return 1; }
  trap "rm -rf '${tmpdir}'" EXIT

  payload_file="${tmpdir}/payload.json"
  printf '{"model": "%s", "streaming": false, "messages": ' "${model}" > "${payload_file}"
  cat "${MESSAGES_FILE}" >> "${payload_file}"
  printf '}' >> "${payload_file}"

  [[ "${DEBUG_POST:-false}" == "true" ]] && \
    echo "POST ${API_BASE_URL}/chat/completions payload:" 1>&2 && cat "${payload_file}" 1>&2

  bodytmp="${tmpdir}/body.tmp"
  http_code=$(curl -K - < "${API_KEY_CURL_CFG}" -s -o "${bodytmp}" -w "%{http_code}" \
    -X POST "${API_BASE_URL}/chat/completions" \
    -H "Content-Type: application/json" \
    -d "@${payload_file}") || { echo "error: curl failed" 1>&2; return 1; }

  if [[ "${http_code}" -ge 400 ]]; then
    echo "error: HTTP ${http_code}" 1>&2
    cat "${bodytmp}" 1>&2
    return 1
  fi
  cat "${bodytmp}"
}

resp=$(do_post "${MODEL}") || exit 1

[[ "${DEBUG_RESPONSE:-false}" == "true" ]] && echo "response: ${resp}" 1>&2

# On model error, attempt substring resolution once and retry
if is_model_error "${resp}"; then
  echo "warning: model '${MODEL}' not found — attempting substring resolution" 1>&2
  RESOLVED=$(resolve_model "${MODEL}") || {
    echo "error: update MODEL to a valid model name" 1>&2; exit 1
  }
  echo "warning: resolved '${MODEL}' → '${RESOLVED}' — update MODEL to avoid this fallback" 1>&2
  resp=$(do_post "${RESOLVED}") || exit 1
  [[ "${DEBUG_RESPONSE:-false}" == "true" ]] && echo "response (retry): ${resp}" 1>&2
fi

content=$(extract_content "${resp}" 2>/dev/null)
if [[ -z "${content}" ]]; then
  echo "error: no content in response — raw response follows" 1>&2
  echo "${resp}" 1>&2
  exit 1
fi

echo "${content}"
