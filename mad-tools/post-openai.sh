#!/usr/bin/env bash
# post_openai.sh — POST a messages array to an OpenAI-compatible chat completions endpoint.
# Requires: curl; one of: jq, python3, python
#
# usage: API_BASE_URL=<url> API_KEY=<key> MODEL=<model> post_openai.sh <messages.json>
#
# messages.json format: JSON array of {"role": "<role>", "content": "<text>"} objects.
# Response text is written to stdout. All other output goes to stderr.
#
# Optional envars:
#   MAX_TOKENS=<number>   — passed as max_tokens in the request body
#   TEMPERATURE=<number>  — passed as temperature in the request body
#   THINK=true            — passed as enable_thinking: true in the request body
#   DEBUG_POST=true       — dump the request payload to stderr before sending
#   DEBUG_RESPONSE=true   — dump the raw API response JSON to stderr

THIS_SCRIPT=$(basename "$0")

function usage() {
  [[ -n "${*}" ]] && echo "error: ${*}" 1>&2
  echo "usage: API_BASE_URL=<url> API_KEY=<key> MODEL=<model> ${THIS_SCRIPT} <messages.json>" 1>&2
  exit 1
}

[[ -n "${API_BASE_URL}" ]] || usage "API_BASE_URL must be set"
[[ -n "${API_KEY}" ]]      || usage "API_KEY must be set"
[[ -n "${MODEL}" ]]        || usage "MODEL must be set"
[[ -f "${1}" ]]            || usage "messages file not found: ${1}"

command -v curl &>/dev/null || { echo "error: curl is required" 1>&2; exit 1; }

MESSAGES_FILE="${1}"

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
  resp=$(curl -s "${API_BASE_URL}/models" \
    -H "Authorization: Bearer ${API_KEY}") || { echo "error: curl failed querying models" 1>&2; return 1; }
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

# build_payload <model> <messages_file> [max_tokens] [temperature]
# Writes the JSON payload to a temp file; prints the file path to stdout.
# Caller is responsible for deleting the file.
function build_payload() {
  local model="${1}" msgs_file="${2}" max_tok="${3:-}" temp="${4:-}"
  local ext_params=""
  [[ -n "${max_tok}" ]]              && ext_params+=$(printf '"max_tokens": %s,' "${max_tok}")
  [[ -n "${temp}" ]]                 && ext_params+=$(printf '"temperature": %s,' "${temp}")
  [[ "${THINK:-false}" == "true" ]]  && ext_params+='"reasoning_effort": "high", "chat_template_kwargs": {"enable_thinking": true},'
  local tmp
  tmp=$(mktemp) || { echo "error: mktemp failed" 1>&2; return 1; }
  printf '{"model": "%s", "messages": ' "${model}" > "${tmp}"
  cat "${msgs_file}" >> "${tmp}"
  if [[ -n "${ext_params}" ]]; then
    printf ', %s}' "${ext_params%,}" >> "${tmp}"
  else
    printf '}' >> "${tmp}"
  fi
  echo "${tmp}"
}

# extract_content <json_string>
# Writes the assistant message content to stdout; returns 1 if absent.
function extract_content() {
  local raw="${1}"
  if [[ -n "${JQ}" ]]; then
    "${JQ}" -r '.choices[0].message.content // empty' <<< "${raw}"
  else
    "${PYTHON}" -c '
import sys, json
resp = json.loads(sys.stdin.read())
content = resp["choices"][0]["message"]["content"]
if content: print(content)
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
  pat  = r"model_not_found|invalid_model|model_not_allowed|does not exist|not available|no such model"
  sys.exit(0 if re.search(pat, code + msg, re.I) else 1)
except Exception: sys.exit(1)
' <<< "${raw}"
  fi
}

# do_post <model>
# POSTs with the given model name; writes response JSON to stdout.
function do_post() {
  local model="${1}" payload_file
  payload_file=$(build_payload "${model}" "${MESSAGES_FILE}" "${MAX_TOKENS:-}" "${TEMPERATURE:-}") || {
    echo "error: failed to build request payload" 1>&2; return 1
  }
  [[ "${DEBUG_POST:-false}" == "true" ]] && \
    echo "POST ${API_BASE_URL}/chat/completions payload:" 1>&2 && cat "${payload_file}" 1>&2
  local result
  curl -s -X POST "${API_BASE_URL}/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${API_KEY}" \
    -d "@${payload_file}"
  local rc=$?
  rm -f "${payload_file}"
  [[ ${rc} -eq 0 ]] || { echo "error: curl failed" 1>&2; return 1; }
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
