# agents task recipes.

# Recipe bodies are bash, not just's default `sh`. Stating it makes the
# contract explicit rather than inherited from whatever /bin/sh happens to be
# on the host. `-u` (just's own default, kept) makes an unset variable a
# failure instead of an empty string. `-o pipefail` makes a pipeline fail
# when any stage does, not just its last.
set shell := ["bash", "-cuo", "pipefail"]

GEN := "gen-agents.py"
AGENTS_DIR := "agents"

# Venv layout differs by OS: POSIX puts executables in .venv/bin, Windows in
# .venv/Scripts with .exe suffixes. Resolved here via just's os() builtin.
VENV := justfile_directory() / ".venv"
PYBIN := if os() == "windows" { "Scripts" } else { "bin" }
EXE := if os() == "windows" { ".exe" } else { "" }
VENV_PYTHON := VENV / PYBIN / "python" + EXE

# PYTHONDONTWRITEBYTECODE=1 on every python invocation: agents/ is a deployed
# surface consumed through symlinks — bytecode caches must never be written
# into it from this repo or from consumers.

default:
    @just --list

# gen-agents.py in check mode (no --generate) is the repo's test: it renders
# every template in memory and diffs against the checked-in definitions,
# exiting nonzero on drift.
[doc("check every definition against its template; exit nonzero on drift")]
check:
    python3 {{GEN}}

[doc("render every template to its definition under agents/ or commands/")]
generate:
    python3 {{GEN}} --generate

# `--generate` copies an about-to-change target to <name>.md.<NN>.bak beside
# it before overwriting — a safety copy, already gitignored by the repo's
# root *.bak rule, not an artifact anyone commits.
[doc("remove the generator's *.bak safety copies under agents/")]
clean-backups:
    rm -f {{AGENTS_DIR}}/*.bak

# Lazy: if the venv's python already exists this is a no-op; a half-built
# venv is repaired by deleting .venv and re-running.
[doc("create .venv with pytest/isort/black if its python is missing")]
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -x "{{VENV_PYTHON}}" ]]; then
        PYTHONDONTWRITEBYTECODE=1 python3 -m venv "{{VENV}}"
        PYTHONDONTWRITEBYTECODE=1 "{{VENV_PYTHON}}" -m pip install pip pytest isort black --upgrade
    fi

[doc("run the kb_tools unit tests")]
test: venv
    PYTHONPATH="{{justfile_directory() / AGENTS_DIR}}" PYTHONDONTWRITEBYTECODE=1 "{{VENV_PYTHON}}" -m pytest {{AGENTS_DIR}}/kb_tools/tests

[doc("black-format and isort agents/kb_tools")]
format-python: venv
    PYTHONDONTWRITEBYTECODE=1 "{{VENV / PYBIN / 'black' + EXE}}" --line-length=120 {{AGENTS_DIR}}/kb_tools
    PYTHONDONTWRITEBYTECODE=1 "{{VENV / PYBIN / 'isort' + EXE}}" --profile black --line-length 120 {{AGENTS_DIR}}/kb_tools
