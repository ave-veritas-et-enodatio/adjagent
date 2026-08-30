# KB maintenance targets — INCLUDED live from the consuming project's
# Makefile via one installed line (never copied into it):
#
#     -include .claude/agents/kb_tools/runner-snippets/kb.mk
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --install-targets
# The non-fatal `-include` form is deliberate: if the .claude/agents symlink
# breaks, the consumer's Makefile keeps working and only these KB targets go
# missing.
#
# Assumed consumer layout: the repo root (where make runs) contains kb-root/
# and .claude/agents — a symlink to the agent-definition repo's agents/
# directory, which holds the kb_tools package. The tools are stdlib-only and
# run under the system python3; PYTHONDONTWRITEBYTECODE=1 keeps bytecode from
# being written into the shared agents repo through the symlink.
#
# All variables are KB_-prefixed to avoid collisions. Plain POSIX recipe
# lines — no SHELL override; existing recipes keep their own shell.

KB_PY_ENV := PYTHONPATH=$(CURDIR)/.claude/agents PYTHONDONTWRITEBYTECODE=1

.PHONY: verify refresh stats

verify:
	$(KB_PY_ENV) python3 -m kb_tools.verify_md_links
	$(KB_PY_ENV) python3 -m kb_tools.verify_kb_metadata

refresh:
	$(KB_PY_ENV) python3 -m kb_tools.refresh_kb_metadata

stats:
	$(KB_PY_ENV) python3 -m kb_tools.kb_cmd stats
