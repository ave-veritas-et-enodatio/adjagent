# ROADMAP – liaison_tools

Future intent only. Not part of the contract-doc precedence chain (SPEC > ARCHITECTURE > CONVENTIONS > code); not handed to coding dispatches.

1. **This package's `CONVENTIONS.md` sits deep in a shipped package, which raises the same opencode shadowing concern the multi-harness portability item in the root `ROADMAP.md` raises for `kb_tools/CONVENTIONS.md`.** Related, not owned here: that item names four sites today and `liaison_tools/` is not among them; whether it should be is its call, since the portability mechanism it proposes is project-wide.

2. **Restore a `python`-fallback launcher if an environment turns up that needs one.** `post-openai.sh` was retired: all three tools now run off `#!/usr/bin/env python3` alone, matching SPEC.md's "a `python3` on `PATH` and nothing else". The case it covered — `python` aliased to a Python 3 interpreter with no `python3` on `PATH` — is real but unobserved here; add the launcher back for all three tools, not one, if it appears.
