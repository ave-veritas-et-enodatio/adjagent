# KB maintenance recipes. This file is installed at
# .claude/agents/kb_tools/runner-snippets/ by `just install-defs`, and is
# imported from the consuming project's justfile by one installed line
# (never copied into it):
#
#     import? '.claude/agents/kb_tools/runner-snippets/kb.just'
#
# The line is managed by the installer (run from the consumer root):
#     PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util install-targets
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

# Composite, not sequential: all three verifiers run, and the recipe carries the
# worst outcome. Three plain lines stop at the first failure, so a report
# covered one verifier's faults while the next two stayed invisible until it
# went green — three passes to see an all-red KB whole, each one looking like
# the last problem. One continued line is one shell invocation, which is what lets
# the rc be collected instead of enforced per line. The verifiers label their
# own output ([verify-md-links], [claim-quality], [citations]), so suppressing
# the echo costs the report nothing.
[doc("verify KB link integrity and claim-graph metadata")]
kb-verify:
    @rc=0; \
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.verify_md_links || rc=1; \
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.verify_kb_metadata || rc=1; \
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.verify_citations || rc=1; \
    exit $rc

[doc("refresh mechanically maintained KB metadata")]
kb-refresh:
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.refresh_kb_metadata

[doc("print KB claim-graph stats")]
kb-stats:
    PYTHONPATH="{{ KB_PYTHONPATH }}" python3 -m kb_tools.kb_cmd stats
