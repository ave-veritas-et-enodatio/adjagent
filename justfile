# agents task recipes.

# Recipe bodies are bash, not just's default `sh`. Stating it makes the
# contract explicit rather than inherited from whatever /bin/sh happens to be
# on the host. `-u` (just's own default, kept) makes an unset variable a
# failure instead of an empty string. `-o pipefail` makes a pipeline fail
# when any stage does, not just its last.
set shell := ["bash", "-cuo", "pipefail"]

GEN := "templates/gen-agents.py"
AGENTS_DIR := "agents"

default:
    @just --list

# gen-agents.py in check mode (no --generate) is the repo's test: it renders
# every template in memory and diffs against the checked-in definitions,
# exiting nonzero on drift.
[doc("check every definition against its template; exit nonzero on drift")]
check:
    python3 {{GEN}}

[doc("render every template to its definition under agents/")]
generate:
    python3 {{GEN}} --generate

# `--generate` copies an about-to-change target to <name>.md.<NN>.bak beside
# it before overwriting — a safety copy, already gitignored by the repo's
# root *.bak rule, not an artifact anyone commits.
[doc("remove the generator's *.bak safety copies under agents/")]
clean-backups:
    rm -f {{AGENTS_DIR}}/*.bak
