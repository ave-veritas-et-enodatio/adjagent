# agents task recipes.

# Recipe bodies are bash, not just's default `sh`. Stating it makes the
# contract explicit rather than inherited from whatever /bin/sh happens to be
# on the host. `-u` (just's own default, kept) makes an unset variable a
# failure instead of an empty string. `-o pipefail` makes a pipeline fail
# when any stage does, not just its last.
set shell := ["bash", "-cuo", "pipefail"]

GEN := "gen-defs.py"
AGENTS_DIR := "agents"
COMMANDS_DIR := "commands"

# Venv layout differs by OS: POSIX puts executables in .venv/bin, Windows in
# .venv/Scripts with .exe suffixes. Resolved here via just's os() builtin.
VENV := justfile_directory() / ".venv"
PYBIN := if os() == "windows" { "Scripts" } else { "bin" }
EXE := if os() == "windows" { ".exe" } else { "" }
VENV_PYTHON := VENV / PYBIN / "python" + EXE

# PYTHONDONTWRITEBYTECODE=1 on every python invocation: agents/ is a deployed
# surface, copied wholesale into consuming projects by `install-defs` —
# bytecode caches must never be written into it from this repo (an install
# excludes them, but a cache in the source tree is still noise nobody wants).

default:
    @just --list

# gen-defs.py in check mode (no --generate) is the repo's test: it renders
# every template in memory and diffs against the checked-in definitions,
# exiting nonzero on drift.
[doc("check every definition against its template; exit nonzero on drift")]
check:
    python3 {{GEN}}

[doc("render every template to its definition under agents/ or commands/")]
generate:
    python3 {{GEN}} --generate

# `--generate` copies an about-to-change target to <name>.md.<NN>.bak beside
# it before overwriting, when the extant content is NOT provably its own
# output — a safety copy, already gitignored by the repo's root *.bak rule,
# not an artifact anyone commits. Backups land beside their target at whatever
# depth it sits (agents/mad/ has them too), so this sweeps both surface trees
# rather than one glob level: pattern-scoped, file-only, and named to the two
# deployed surfaces so it can never reach anything else.
[doc("remove the generator's *.bak safety copies under agents/ and commands/")]
clean-backups:
    find {{AGENTS_DIR}} {{COMMANDS_DIR}} -name '*.bak' -type f -delete

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

[doc("run all tooling python tests (kb_tools + liaison_tools + gen-defs)")]
test: venv
    PYTHONPATH="{{justfile_directory() / AGENTS_DIR}}" PYTHONDONTWRITEBYTECODE=1 "{{VENV_PYTHON}}" -m pytest {{AGENTS_DIR}}/kb_tools/tests {{AGENTS_DIR}}/liaison_tools/tests templates/tests

[doc("black-format and isort agents/kb_tools")]
format-python: venv
    PYTHONDONTWRITEBYTECODE=1 "{{VENV / PYBIN / 'black' + EXE}}" --line-length=120 {{AGENTS_DIR}}/kb_tools {{AGENTS_DIR}}/liaison_tools templates/tests gen-defs.py
    PYTHONDONTWRITEBYTECODE=1 "{{VENV / PYBIN / 'isort' + EXE}}" --profile black --line-length 120 {{AGENTS_DIR}}/kb_tools {{AGENTS_DIR}}/liaison_tools templates/tests gen-defs.py

# The live ~/.claude/CLAUDE.md is a real file, deliberately not a symlink
# (see user-config/README.md): updates flow only by explicit act, never
# silently. This recipe is the repo -> home direction. A differing live file
# is shown as a diff, then saved beside itself as CLAUDE.md.<NN>.bak — the
# generator's numbered-backup convention (highest serial + 1, never reused)
# — before being overwritten. The reverse flow (home -> repo) stays a manual
# diff-and-adopt; this recipe never reads live changes back.
[doc("install user-config/CLAUDE.md to ~/.claude/CLAUDE.md (diff + numbered backup when the live file differs)")]
install-user-config:
    #!/usr/bin/env bash
    set -euo pipefail
    src="{{justfile_directory() / 'user-config' / 'CLAUDE.md'}}"
    dest="${HOME}/.claude/CLAUDE.md"
    if [[ ! -f "${dest}" ]]; then
        mkdir -p "${HOME}/.claude"
        cp "${src}" "${dest}"
        printf '%s\n' "installed ${dest} (no prior file)"
    elif cmp -s "${dest}" "${src}"; then
        printf '%s\n' "no-op: ${dest} already matches user-config/CLAUDE.md"
    else
        printf '%s\n' "live ${dest} differs from published user-config/CLAUDE.md:"
        # diff exits 1 when inputs differ — expected on this branch, not a
        # failure; >= 2 is real trouble and still fails the recipe.
        diff_status=0
        diff -u -L "live ${dest}" -L "published user-config/CLAUDE.md" "${dest}" "${src}" || diff_status=${?}
        if [[ "${diff_status}" -ge 2 ]]; then
            exit "${diff_status}"
        fi
        next=0
        for existing in "${dest}".*.bak; do
            [[ -e "${existing}" ]] || continue  # unmatched glob stays literal
            if [[ "${existing}" =~ \.([0-9]+)\.bak$ ]]; then
                n=$((10#${BASH_REMATCH[1]}))
                if [[ "${n}" -ge "${next}" ]]; then
                    next=$((n + 1))
                fi
            fi
        done
        backup="$(printf '%s.%02d.bak' "${dest}" "${next}")"
        cp -p "${dest}" "${backup}"
        cp "${src}" "${dest}"
        printf '%s\n' "installed ${dest} (previous live file backed up as ${backup})"
    fi
    printf '%s\n' "reminder: publishing live improvements back into the repo is a manual diff-and-adopt per user-config/README.md — this recipe only installs, it never reads live changes back."

# The one deployment shape (README.md, "Install"): copies both deployed
# surfaces into <target>/.claude/ via gen-defs.py --install, minus test suites
# and caches, stamping each copied file with an !INSTALLED! banner carrying the
# hash of the content below it. `flavor` disambiguates
# by file existence only: a templates/models/<flavor>*.toml match means
# family, otherwise model — gen-defs.py then resolves the bare name (or errors
# helpfully) itself, and a flavor adds a render pass over the copy.
[doc("install both surfaces into <target>/.claude/ — optional flavor is a model family or model name")]
install-defs target flavor="":
    #!/usr/bin/env bash
    set -euo pipefail
    target="{{target}}"
    flavor="{{flavor}}"
    if [[ ! -d "${target}" ]]; then
        printf '%s\n' "error: target project root '${target}' does not exist — create the project first" >&2
        exit 1
    fi
    mkdir -p "${target}/.claude"
    tuning_args=()
    tuning_desc="no model tuning"
    if [[ -n "${flavor}" ]]; then
        matches=("{{justfile_directory() / 'templates' / 'models'}}/${flavor}"*.toml)
        if [[ -e "${matches[0]}" ]]; then  # unmatched glob stays literal
            tuning_args=(--model-family "${flavor}")
            tuning_desc="model family '${flavor}'"
        else
            tuning_args=(--model "${flavor}")
            tuning_desc="model '${flavor}'"
        fi
    fi
    # ${arr[@]+...} guard: expanding an empty array trips `set -u` on the
    # bash 3.2 that macOS ships at /bin/bash.
    PYTHONDONTWRITEBYTECODE=1 python3 "{{justfile_directory() / GEN}}" --install "${target}/.claude" ${tuning_args[@]+"${tuning_args[@]}"}
    printf '%s\n' "installed the full agents/ and commands/ product into ${target}/.claude/ with ${tuning_desc} — see the install summary above for counts, backups, and the integrity verdict."
