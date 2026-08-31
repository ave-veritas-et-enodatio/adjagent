# KB maintenance targets. This file is installed at
# .claude/agents/kb_tools/runner-snippets/ by `just install-defs`, and is
# included from the consuming project's Makefile by one installed line
# (never copied into it):
#
#     -include .claude/agents/kb_tools/runner-snippets/kb.mk
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --install-targets
# The non-fatal `-include` form is deliberate: if .claude/agents is absent —
# not yet installed, or removed — the consumer's Makefile keeps working and
# only these KB targets go missing.
#
# Assumed consumer layout: the repo root (where make runs) contains kb-root/
# and .claude/agents — the agent-definition repo's agents/ surface as
# installed by `just install-defs`, which holds the kb_tools package. The
# tools are stdlib-only and run under the system python3.
#
# Target names carry a `kb-` prefix and variables a `KB_` prefix, so neither
# collides with a project's own verify/refresh/stats. Plain POSIX recipe
# lines — no SHELL override; existing recipes keep their own shell.

KB_PY_ENV := PYTHONPATH=$(CURDIR)/.claude/agents

.PHONY: kb-verify kb-refresh kb-stats

kb-verify:
	$(KB_PY_ENV) python3 -m kb_tools.verify_md_links
	$(KB_PY_ENV) python3 -m kb_tools.verify_kb_metadata

kb-refresh:
	$(KB_PY_ENV) python3 -m kb_tools.refresh_kb_metadata

kb-stats:
	$(KB_PY_ENV) python3 -m kb_tools.kb_cmd stats
