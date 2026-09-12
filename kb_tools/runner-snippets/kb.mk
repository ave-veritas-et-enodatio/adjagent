# KB maintenance targets. This file is installed at
# .claude/agents/kb_tools/runner-snippets/ by `just install-defs`, and is
# included from the consuming project's Makefile by one installed line
# (never copied into it):
#
#     -include .claude/agents/kb_tools/runner-snippets/kb.mk
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util install-targets
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

# Composite, not sequential: all three verifiers run, and the target carries the
# worst outcome. Three plain lines stop at the first failure, so a report
# covered one verifier's faults while the next two stayed invisible until it
# went green — three passes to see an all-red KB whole, each one looking like
# the last problem. One continued line is one shell invocation, which is what lets
# the rc be collected instead of enforced per line. The verifiers label their
# own output ([verify-md-links], [claim-quality], [citations]), so suppressing
# the echo costs the report nothing.
kb-verify:
	@rc=0; \
	$(KB_PY_ENV) python3 -m kb_tools.verify_md_links || rc=1; \
	$(KB_PY_ENV) python3 -m kb_tools.verify_kb_metadata || rc=1; \
	$(KB_PY_ENV) python3 -m kb_tools.verify_citations || rc=1; \
	exit $$rc

kb-refresh:
	$(KB_PY_ENV) python3 -m kb_tools.refresh_kb_metadata

kb-stats:
	$(KB_PY_ENV) python3 -m kb_tools.kb_cmd stats
