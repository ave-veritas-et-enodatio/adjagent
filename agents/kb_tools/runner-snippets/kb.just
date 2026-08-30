# KB maintenance targets — IMPORTED live from the consuming project's
# justfile via one installed line (never copied into it):
#
#     import? '.claude/agents/kb_tools/runner-snippets/kb.just'
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --install-targets
# `import?` requires just >= 1.33; the optional form is deliberate: if the
# .claude/agents symlink breaks, the consumer's justfile keeps working and
# only these KB recipes go missing.
#
# Assumed consumer layout: the repo root contains kb-root/ and .claude/agents
# — a symlink to the agent-definition repo's agents/ directory, which holds
# the kb_tools package. Inside an imported file, justfile_directory() resolves
# to the ROOT justfile's directory — the consumer root — so KB_PYTHONPATH
# lands on the consumer's .claude/agents. (source_directory() would resolve
# here, into the agents repo — wrong repo.) The tools are stdlib-only and run
# under the system python3; PYTHONDONTWRITEBYTECODE=1 keeps bytecode from
# being written into the shared agents repo through the symlink.
#
# All variables are KB_-prefixed to avoid collisions. Plain recipe lines, no
# `set shell` — nothing here needs bash.

KB_PYTHONPATH := justfile_directory() / ".claude" / "agents"

[doc("verify KB link integrity and claim-graph metadata")]
verify:
    PYTHONPATH="{{ KB_PYTHONPATH }}" PYTHONDONTWRITEBYTECODE=1 python3 -m kb_tools.verify_md_links
    PYTHONPATH="{{ KB_PYTHONPATH }}" PYTHONDONTWRITEBYTECODE=1 python3 -m kb_tools.verify_kb_metadata

[doc("refresh mechanically maintained KB metadata")]
refresh:
    PYTHONPATH="{{ KB_PYTHONPATH }}" PYTHONDONTWRITEBYTECODE=1 python3 -m kb_tools.refresh_kb_metadata

[doc("print KB claim-graph stats")]
stats:
    PYTHONPATH="{{ KB_PYTHONPATH }}" PYTHONDONTWRITEBYTECODE=1 python3 -m kb_tools.kb_cmd stats
