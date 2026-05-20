#!/usr/bin/env bash
set -u -o pipefail
# msg-util.sh — manage the OpenAI-compatible messages JSON array used by the
# guest liaison (mad-guest-liaison and guest-liaison). Provides deterministic
# initialization and append operations so the liaison never has to improvise
# JSON manipulation inline.
#
# Modes:
#   init   --system-prompt=<text> --instructions=<text> <messages.json>
#     Creates <messages.json> as a 2-element array: one system turn holding
#     <text> and one user turn holding <text>. Overwrites if the file exists.
#
#   append --role=<user|agent> <messages.json> <content-file>
#     Appends one turn whose content is the full contents of <content-file>.
#     Role 'agent' maps to API role 'assistant'; 'user' passes through.
#
# Design notes:
# - init takes its two texts on the command line — both are small fixed inputs.
# - append reads message bodies from a file to avoid shell arg-length limits
#   and quoting hazards when turns carry large or multi-line content.
# - All JSON construction goes through python3/python, never
#   string concatenation, so escaping is always correct.

THIS_SCRIPT=$(basename "$0")

function usage() {
  [[ -n "${*}" ]] && echo "error: ${*}" 1>&2
  cat <<USAGE 1>&2
usage:
  ${THIS_SCRIPT} init   --system-prompt=<system-prompt-file> --instructions=<intstructions-file> <messages.json>
  ${THIS_SCRIPT} append --role=<user|agent> <messages.json> <content-file>
USAGE
  exit 1
}

PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
[[ "${PYTHON}" ]] || \
  { echo "error: python3 required for JSON handling" 1>&2; exit 1; }

# map_role <input>
# Maps the user-facing role name to the OpenAI API role. Writes result to stdout.
function map_role() {
  case "${1}" in
    user)  echo "user" ;;
    agent) echo "assistant" ;;
    *)     echo "error: role must be 'user' or 'agent' (got '${1}')" 1>&2; return 1 ;;
  esac
}

# mode_init <args...>
function mode_init() {
  local sys_prompt="" instructions="" msgs_file=""
  local have_sys=0 have_instr=0
  while [[ $# -gt 0 ]]; do
    case "${1}" in
      --system-prompt=*) sys_prompt="${1#*=}";;
      --instructions=*)  instructions="${1#*=}";;
      --system-prompt|--instructions) usage "${1} must use '=<value>' form" ;;
      -*) usage "unknown option: ${1}" ;;
      *)
        [[ -z "${msgs_file}" ]] || usage "unexpected positional: ${1}"
        msgs_file="${1}"
        ;;
    esac
    shift
  done

  [[ -n "${msgs_file}"  ]] || usage "init requires <messages.json>"
  [[ -n "${sys_prompt}" ]] || usage "init requires --system-prompt=<system-prompt-file>"
  [[ -n "${instructions}" ]] || usage "init requires --instructions=<instructions-file>"
  [[ -f "${sys_prompt}" ]] || usage "sys-prompt file ${sys_prompt} does not exist."
  [[ -f "${instructions}" ]] || usage "instructions file ${instructions} does not exist."

  local tmp
  tmp=$(mktemp) || { echo "error: mktemp failed" 1>&2; exit 1; }
  trap "rm -f '${tmp}'" EXIT

  SYS="${sys_prompt}" USR="${instructions}" "${PYTHON}" - > "${tmp}" << 'PY' || { echo "error: python init failed" 1>&2; exit 1; }
import json, os, sys
from pathlib import Path
json.dump(
  [
    {"role": "system", "content": Path(os.environ["SYS"]).read_text()},
    {"role": "user",   "content": Path(os.environ["USR"]).read_text()},
  ],
  sys.stdout, indent=2,
)
PY
  fi

  mv "${tmp}" "${msgs_file}"
  trap - EXIT
}

# mode_append <args...>
function mode_append() {
  local role_in="" msgs_file="" content_file=""
  while [[ $# -gt 0 ]]; do
    case "${1}" in
      --role=*) role_in="${1#*=}" ;;
      --role)   usage "--role must use '=<value>' form" ;;
      -*)       usage "unknown option: ${1}" ;;
      *)
        if   [[ -z "${msgs_file}"    ]]; then msgs_file="${1}"
        elif [[ -z "${content_file}" ]]; then content_file="${1}"
        else usage "unexpected positional: ${1}"
        fi
        ;;
    esac
    shift
  done

  [[ -n "${role_in}"      ]] || usage "append requires --role=<user|agent>"
  [[ -n "${msgs_file}"    ]] || usage "append requires <messages.json>"
  [[ -n "${content_file}" ]] || usage "append requires <content-file>"
  [[ -f "${msgs_file}"    ]] || { echo "error: messages file not found: ${msgs_file}" 1>&2; exit 1; }
  [[ -f "${content_file}" ]] || { echo "error: content file not found: ${content_file}" 1>&2; exit 1; }

  local role
  role=$(map_role "${role_in}") || exit 1

  local tmp
  tmp=$(mktemp) || { echo "error: mktemp failed" 1>&2; exit 1; }
  trap "rm -f '${tmp}'" EXIT

  MSGS="${msgs_file}" ROLE="${role}" CF="${content_file}" "${PYTHON}" - > "${tmp}" <<'PY' || { echo "error: python append failed" 1>&2; exit 1; }
import json, os, sys
with open(os.environ["MSGS"]) as f:
    msgs = json.load(f)
with open(os.environ["CF"]) as f:
    content = f.read()
msgs.append({"role": os.environ["ROLE"], "content": content})
json.dump(msgs, sys.stdout, indent=2)
PY

  mv "${tmp}" "${msgs_file}"
  trap - EXIT
}

[[ $# -ge 1 ]] || usage "missing mode"
mode="${1}"; shift
case "${mode}" in
  init)   mode_init   "$@" ;;
  append) mode_append "$@" ;;
  -h|--help|help) usage ;;
  *)      usage "unknown mode: ${mode}" ;;
esac
