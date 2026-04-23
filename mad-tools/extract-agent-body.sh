#!/usr/bin/env bash
set -u -o pipefail
# extract-agent-body.sh — print the body of a Claude agent markdown file with
# its YAML frontmatter stripped. Frontmatter must be delimited by a '---' line
# at the very top of the file and a subsequent '---' line that closes it.
#
# usage: extract-agent-body.sh <agent-md-file>
#
# The body (every line after the closing '---') is written to stdout with
# content and line endings preserved. Exits non-zero and prints a diagnostic
# to stderr if the file lacks a complete frontmatter block — silently emitting
# an empty body on malformed input would let the caller proceed with a broken
# system prompt.

THIS_SCRIPT=$(basename "$0")

function usage() {
  [[ -n "${*}" ]] && echo "error: ${*}" 1>&2
  echo "usage: ${THIS_SCRIPT} <agent-md-file>" 1>&2
  exit 1
}

[[ $# -eq 1 ]]  || usage "expected exactly one argument"
[[ -f "${1}" ]] || usage "file not found: ${1}"

FILE="${1}"

awk -v file="${FILE}" '
  BEGIN { state = 0 }  # 0 = before opening, 1 = in frontmatter, 2 = in body

  NR == 1 && $0 !~ /^---[[:space:]]*$/ {
    print "error: " file ": first line is not frontmatter delimiter (---)" > "/dev/stderr"
    err = 1
    exit 2
  }
  NR == 1 { state = 1; next }

  state == 1 && /^---[[:space:]]*$/ { state = 2; next }
  state == 2 { print }

  END {
    if (err) exit 2
    if (state != 2) {
      print "error: " file ": missing closing frontmatter delimiter (---)" > "/dev/stderr"
      exit 2
    }
  }
' "${FILE}"
