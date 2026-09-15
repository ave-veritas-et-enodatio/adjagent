# agents task recipes.

# Recipe bodies are bash, not just's default `sh`. Stating it makes the
# contract explicit rather than inherited from whatever /bin/sh happens to be
# on the host. `-u` (just's own default, kept) makes an unset variable a
# failure instead of an empty string. `-o pipefail` makes a pipeline fail
# when any stage does, not just its last.
set shell := ["bash", "-cuo", "pipefail"]
# Without this, a variadic parameter reaches a recipe body only through
# `{{args}}` textual interpolation, which just renders as a bare
# space-joined string — a multi-word value like `-k "a and b"` arrives as
# four separate shell words, indistinguishable from four short ones. With
# it, recipe parameter values are also passed as the shell's own positional
# parameters ($1, $2, ...), so `"$@"` (or a slice of it) carries each
# argument through with its original word boundaries intact.
set positional-arguments := true

# consumed in python.just
PROJECT_ROOT := justfile_directory()
import 'python.just'

GEN := "gen-defs.py"
AGENTS_DIR := "agents"
COMMANDS_DIR := "commands"

default:
    @just --list

# The one deployment shape: copies both deployed
# surfaces into <target>/.claude/ via gen-defs.py install, minus test suites
# and caches, stamping each copied file with an !INSTALLED! banner carrying the
# hash of the content below it. Every flag forwards verbatim to
# gen-defs.py — --family, --model-tier-map, --model-pin-map, --verbose, and
# whatever it adds next: gen-defs.py's argparse is the single source of flag
# truth, so an unknown spelling fails there — stripping `--` to
# make the params positional would make the command line worse, not better. The
# one flag this recipe intercepts is `--subdir=X`, because it addresses this
# recipe's own target-composition, not gen-defs.py: read only when it is the
# *first* flag, stripped before the rest forwards. Absent, subdir defaults
# to `.claude`; `--subdir=` (empty value) installs directly into <target>.
[doc("install both surfaces into <target>/<subdir>/ (<target> is the first non-flag argument, in any position; subdir defaults to .claude; pass --subdir= as the first flag to install into <target> directly) — every other --* flag forwards verbatim to gen-defs.py (--family, --model-tier-map, --model-pin-map, --verbose, ...)")]
install target *args:
    #!/usr/bin/env bash
    set -euo pipefail
    # `just` binds the first positional to `target` whatever it looks like, so
    # a caller leading with flags would have a flag taken as the path. The
    # target is the first argument that is not a `--` flag, wherever it sits;
    # everything else keeps its order and forwards.
    positional=("{{target}}" {{args}})
    target=""
    subdir=".claude"
    args=()
    for arg in ${positional[@]+"${positional[@]}"}; do
        if [[ -z "${target}" && "${arg}" != --* ]]; then
            target="${arg}"
        elif [[ "${#args[@]}" -eq 0 && "${arg}" == --subdir=* ]]; then
            subdir="${arg#--subdir=}"
        else
            args+=("${arg}")
        fi
    done
    if [[ -z "${target}" ]]; then
        printf '%s\n' "error: no target project root given — every argument is a flag" >&2
        exit 1
    fi
    # A consuming project that does not exist is an operator mistake — a typo'd
    # path would otherwise be silently populated as a new project root.
    if [[ ! -d "${target}" ]]; then
        printf '%s\n' "error: target project root '${target}' does not exist — create the project first" >&2
        exit 1
    fi
    root="${target}${subdir:+/${subdir}}"
    mkdir -p "${root}"
    # ${arr[@]+...} guard: expanding an empty array trips `set -u` on the
    # bash 3.2 that macOS ships at /bin/bash.
    python3 "{{justfile_directory() / GEN}}" install "${root}" ${args[@]+"${args[@]}"}

# The live ~/.claude/CLAUDE.md is a real file, deliberately not a symlink:
# updates flow only by explicit act, never silently. This recipe is the repo ->
# home direction, and it is a thin invocation on purpose — every protection the
# integration carries (base recovery from git history, the four cases, the
# report before the write, the bail path) lives in gen-defs.py, which is the
# one place anything in this repository writes into space an operator owns. The
# reverse flow (home -> repo) stays a manual diff-and-adopt; this recipe never
# reads live changes back. A live file no published revision merges cleanly
# against leaves the recipe nonzero with ~/.claude/CLAUDE.md untouched.
[doc("merge user-config/INSTALLED_CLAUDE.md into ~/.claude/CLAUDE.md (classified and reported before it writes; nonzero and untouched when no clean merge exists)")]
install-claude-md:
    python3 "{{justfile_directory() / GEN}}" install-claude-md "${HOME}/.claude/CLAUDE.md"


##
##
## mmmm                        mmmmm                  "
## #   "m  mmm   m   m         #   "#  mmm    mmm   mmm    mmmm    mmm    mmm
## #    # #"  #  "m m"         #mmmm" #"  #  #"  "    #    #" "#  #"  #  #   "
## #    # #""""   #m#          #   "m #""""  #        #    #   #  #""""   """m
## #mmm"  "#mm"    #           #    " "#mm"  "#mm"  mm#mm  ##m#"  "#mm"  "mmm"
##                                                          #
##                                                          "

# This repository's own render tree: gitignored, never committed, and the
# conventional place to put the product for inspection or a PR diff. It names
# a build product rather than a project root.
RENDERED := "rendered"

# The project's scratch space, gitignored: throwaway trees nobody inspects
# twice. The tuning rungs render there rather than into RENDERED, which holds
# the default-triple product an operator reads and diffs — a rung landing there
# would leave it tuned to something else until the next `just check`.
SCRATCH := ".claude-temp"


# The full install shape, not a bare render: rendered/ holds what a consuming
# project would receive, which is what makes it inspectable and PR-diffable.
# `--subdir=` is fixed here, ahead of any caller-supplied args, so the render
# always lands directly under rendered/ with no .claude nest — `install`'s
# leading-flag rule means a caller-supplied --subdir would only apply if it
# came first, and this recipe does not expose that seam.
[doc("render the full install product into rendered/ (no .claude nest) — every --* flag forwards verbatim to gen-defs.py via install (--family, --model-tier-map, --model-pin-map, --verbose, ...)")]
generate *args:
    @mkdir -p "{{RENDERED}}"
    "{{just_executable()}}" --justfile "{{justfile()}}" install "{{RENDERED}}" --subdir= {{args}}

# The eventual shape — `check` also asserting the
# Guest-Extraction Contract over the render — is not landed here.
#
# check does NOT render its own subject. It diffs rendered/ — a baseline
# `just generate` produced earlier — against what the templates render right
# now, and prints exactly what differs and where. That diff is the point: the
# working sequence is generate a baseline, edit a template or a
# shared-chunks.toml chunk, run check, and read which definitions changed and
# how — a chunk edit that reaches forty definitions when you meant four is the
# failure this is for. Folding `generate` into this recipe would overwrite the
# baseline before the diff ever ran, so the report would always read clean —
# which is why that line was removed rather than kept ahead of the check pass.
# `--no-diff` suppresses the very report this recipe exists to produce; reach
# for it only when the exit code alone is wanted.
#
# Every flag forwards verbatim to the check pass: a tree rendered under a
# given family/tier-map/pin-map must be checked under the identical set — the
# banner claims it, and a mismatch reports MISTUNED.
[doc("diff rendered/ (a baseline from `just generate`) against what the templates render now, naming exactly what changed and where — every --* flag forwards verbatim to gen-defs.py (--family, --model-tier-map, --model-pin-map, --verbose, ...)")]
check *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -d "{{RENDERED}}" ]]; then
        printf '%s\n' "error: {{RENDERED}}/ does not exist — run 'just generate' first, then 'just check'" >&2
        exit 1
    fi
    python3 "{{justfile_directory() / GEN}}" check "{{RENDERED}}" {{args}}

# The two tuning rungs share one shape, and each is a pair of recipes rather
# than a command line so neither rung is ever run from correctly-recalled
# flags. `_render-rung` installs the full product into its own scratch tree
# under the rung's fixed tuning; `_check-rung` diffs that tree against what
# the templates render right now, under the IDENTICAL flags — a tree rendered
# under one triple and checked under another reports MISTUNED by
# construction, which is the one thing a rung must not be measuring. The pair
# mirrors the top-level generate/check split for the same reason: folding the
# render into the check would overwrite the very baseline the diff runs
# against, so the report would always read clean. Re-running `_render-rung`
# renders over its own prior output, which is provably this tool's own, so
# nothing is backed up and nothing here is ever deleted.
_render-rung name *args:
    #!/usr/bin/env bash
    set -euo pipefail
    out="{{justfile_directory() / SCRATCH / name}}"
    mkdir -p "${out}"
    "{{just_executable()}}" --justfile "{{justfile()}}" install "${out}" --subdir= {{args}}

_check-rung name render_recipe *args:
    #!/usr/bin/env bash
    set -euo pipefail
    out="{{justfile_directory() / SCRATCH / name}}"
    if [[ ! -d "${out}" ]]; then
        printf '%s\n' "error: ${out}/ does not exist — run 'just {{render_recipe}}' first, then 'just {{name}}'" >&2
        exit 1
    fi
    python3 "{{justfile_directory() / GEN}}" check "${out}" {{args}}

# The floor: every tier mapped onto the smallest model, tuning and pin alike.
# What it exercises is the `all=` merge reaching every tier a real definition
# sits at — not the `lowest` tier, which no pin site carries.
[doc("install the floor rung (both maps all=haiku) into .claude-temp/check-floor")]
generate-floor: (_render-rung "check-floor" "--model-tier-map=all=haiku" "--model-pin-map=all=haiku")
[doc("diff the floor rung against what the templates render now — run `just generate-floor` first")]
check-floor: (_check-rung "check-floor" "generate-floor" "--model-tier-map=all=haiku" "--model-pin-map=all=haiku")

# The stock rung: a family whose five tiers name real members that the family
# declares no overlay overrides for, so every tier renders stock and says so.
[doc("install the stock rung (family gemma-4) into .claude-temp/check-stock")]
generate-stock: (_render-rung "check-stock" "--family=gemma-4")
[doc("diff the stock rung against what the templates render now — run `just generate-stock` first")]
check-stock: (_check-rung "check-stock" "generate-stock" "--family=gemma-4")

# The shipped packages are imported as top-level packages (`kb_tools`,
# `liaison_tools`), and their sources sit at the repository root — so the
# repository root IS the import path. The consumer-side invocation is
# unaffected: there the same packages arrive under .claude/agents/ and are
# imported with PYTHONPATH=.claude/agents.
# `surface` is always positional parameter $1 (positional-arguments, set
# above), so the pytest arguments that follow it start at $2 — `"${@:2}"`
# forwards them to pytest as the separate words `just` received them as,
# not as a re-split string, so `-k "a and b"` survives as one argument.
[doc("run tooling python tests: no argument runs all three (kb_tools + liaison_tools + gen-defs); a surface argument (kb_tools, liaison_tools, gen-defs) runs only that one; any further arguments forward to pytest verbatim (flags, -k, a file::test path, ...)")]
test surface="" *pytest_args: venv
    PYTHONPATH="{{justfile_directory()}}" PYTHONDONTWRITEBYTECODE=1 "{{VENV_PYTHON}}" -m pytest {{ \
      if surface == "" { "kb_tools/tests liaison_tools/tests tests" } \
      else if surface == "kb_tools" { "kb_tools/tests" } \
      else if surface == "liaison_tools" { "liaison_tools/tests" } \
      else if surface == "gen-defs" { "tests" } \
      else { error("unknown test surface '" + surface + "' — valid values: kb_tools, liaison_tools, gen-defs; a leading flag binds here instead — pass it with an explicit empty surface: just test \"\" " + surface) } \
    }} "${@:2}"


FLAKE8_IGNORE := "E122,E201,E202,E203,E225,E226,E228,E261,E265,E302,E303,E501,E704,E731,W291,W293,W391,W503"
# `*paths` (positional-arguments, set above) arrives as "$@" with each path
# its own word, so a path containing a space still reaches every tool as one
# argument. No paths given reproduces today's four-surface, tool-by-tool
# argument order exactly — the order flake8 was already called with differs
# from black/isort's, and that difference is preserved rather than
# normalized away.
[doc("black-format, isort, flake8 the shipped python packages and the generator, or the given path(s) in place of all four")]
format-python *paths: venv
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$#" -eq 0 ]]; then
        black_isort_paths=(kb_tools liaison_tools tests gen-defs.py dupe_sweep.py)
        flake8_paths=(kb_tools liaison_tools gen-defs.py dupe_sweep.py tests)
    else
        black_isort_paths=("$@")
        flake8_paths=("$@")
    fi
    "{{VENV_PYTHON}}" -m black --line-length=120 "${black_isort_paths[@]}"
    "{{VENV_PYTHON}}" -m isort --profile black --line-length 120 "${black_isort_paths[@]}"
    "{{VENV_PYTHON}}" -m flake8 --ignore="{{FLAKE8_IGNORE}}" "${flake8_paths[@]}"


# The two duplication sweeps. One idea in two places is invisible inside either
# of them — nothing in a file reports a fact about two files — so it has been
# found here by adversarial multi-model review or by accident, and both are too
# expensive to be the standing instrument. These are the standing instrument.
#
# They EMIT CANDIDATES AND NEVER VERDICTS, and exit zero with findings on
# purpose: whether two sites are one idea is a judgment, and a gate here would
# be claiming it. Read the output, or hand it to a seat that will.
#
# Stdlib only under the system python3, like every other tool here, with
# PYTHONPATH pointed at this tree because the sweep is imported as a top-level
# module. `--rev` sweeps a past tree, which is how the known-answer run is made.
[doc("list duplicated-prose candidates across templates/ — chunk bodies and template inline prose; trailing args forward to the sweep (--rev, --min-words, --coverage)")]
sweep-prose *args:
    PYTHONPATH="{{justfile_directory()}}" PYTHONDONTWRITEBYTECODE=1 python3 -m dupe_sweep prose "$@"

[doc("list one-idea-two-places candidates across kb_tools/ — structural twins, repeated constants, repeated docstring and comment rules; trailing args forward to the sweep (--rev, --min-words, --coverage)")]
sweep-python *args:
    PYTHONPATH="{{justfile_directory()}}" PYTHONDONTWRITEBYTECODE=1 python3 -m dupe_sweep python "$@"


# The claim-graph sheet for one built KB. A recipe rather than a command line:
# the op takes no root override — it resolves the repository from the working
# directory, the way every tool in this toolchain does — so naming the KB means
# standing in it, and a naked invocation is a cd plus a PYTHONPATH recalled
# correctly every time.
#
# Stdlib only, under the system python3 the way a consumer runs it, but with
# PYTHONPATH pointed at THIS tree rather than the consumer's installed
# .claude/agents: the renderer under test is the working copy.
#
# `"${@:2}"` and not `"$@"`: with positional-arguments set, $1 is `repo` itself,
# so passing the whole list would hand the op its own target as a flag.
[doc("render one built KB's claim graph to <repo>/kb-root/claim-graph.svg (repo is a consuming repository root); trailing args forward verbatim to the op (--out, --domain)")]
render-claim-graph repo *args:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -d "{{repo}}" ]]; then
        printf 'error: %s is not a directory — name a consuming repository root, the one holding kb-root/\n' "{{repo}}" >&2
        exit 1
    fi
    root="$(cd "{{repo}}" && pwd)"
    cd "${root}"
    PYTHONPATH="{{justfile_directory()}}" PYTHONDONTWRITEBYTECODE=1 \
        python3 -m kb_tools.kb_util render-claim-graph "${@:2}"
