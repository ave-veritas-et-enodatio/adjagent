# KB maintenance recipes. This file is installed at
# .claude/agents/kb_tools/runner-snippets/ by `just install-defs`, and is
# imported from the consuming project's justfile by one installed line
# (never copied into it):
#
#     import? '.claude/agents/kb_tools/runner-snippets/kb.just'
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --install-targets
# `import?` requires just >= 1.33; the optional form is deliberate: if
# .claude/agents is absent — not yet installed, or removed — the consumer's
# justfile keeps working and only these KB recipes go missing.
#
# Assumed consumer layout: the repo root contains kb-root/ and .claude/agents
# — the agent-definition repo's agents/ surface as installed by `just
# install-defs`, which holds the kb_tools package. Inside an imported file,
# justfile_directory() resolves to the ROOT justfile's directory — the
# consumer root — so KB_PYTHONPATH lands on the consumer's .claude/agents.
# (source_directory() would instead resolve to this fragment's own directory
# inside the installed tree — the wrong path.) The tools are stdlib-only and
# run under the system python3.
#
# Recipe names carry a `kb-` prefix and variables a `KB_` prefix, so neither
# collides with a project's own verify/refresh/stats. Plain recipe lines, no
# `set shell` — nothing here needs bash.

KB_PYTHONPATH := justfile_directory() / ".claude" / "agents"

[doc("verify KB link integrity and claim-graph metadata")]
kb-verify:
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.verify_md_links
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.verify_kb_metadata

[doc("refresh mechanically maintained KB metadata")]
kb-refresh:
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.refresh_kb_metadata

[doc("print KB claim-graph stats")]
kb-stats:
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.kb_cmd stats
