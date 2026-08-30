# AGENTS.md — House Rules

House rules and project-specific traps only. Doc-structure conventions and general engineering ethos live in operator config (`user-config/CLAUDE.md`), not here.

- **Bannered definitions are never hand-edited.** A file carrying the `# !GENERATED!` banner is changed by editing its template (`templates/agents/<name>.md.tmpl` or `templates/commands/<name>.md.tmpl`) or the relevant chunk in `templates/shared-sections.toml`, then running `just generate`. `just check` must exit 0 before any handoff that touched `templates/`, `agents/`, or `commands/`.
- **Chunk single-sourcing.** Text shared by two or more definitions lives in exactly one `[chunks.*]` entry in `shared-sections.toml`. A template never pastes a paraphrase of shared text inline — reference the chunk, or add a variant if the shared text needs a per-agent difference.
- **Emphasis economy.** State each rule once, at its best location. Do not repeat a rule across templates or chunks for emphasis, and do not write cross-agent comparisons into a shared chunk — a chunk read by many definitions should not describe how it differs from another agent's rule (`mocking-threshold`'s per-variant thresholds are the pattern to follow: each variant states only its own number).
- **Maintainer notes go in template headers, not bodies.** Commentary about why a template or chunk is shaped as it is belongs in the `+++ ... +++` TOML header (as a TOML comment) or a comment above the chunk in `shared-sections.toml` — never in the rendered body, which becomes an agent's system prompt.
- **Persona rules.** Ground expertise register in stated decision procedures and concrete tradeoffs. Do not invent degrees, employers, or publication history for a persona.
- **Model pins live in frontmatter.** Each definition's `model:` key is its own; there is no fleet-wide default, and no chunk sets `model:`.
- **`user-config/CLAUDE.md` is edited only by syncing from the live `~/.claude/CLAUDE.md`.** Never hand-edit the published copy directly — diff against the live file and adopt the diff (`user-config/README.md`).
- **`*.bak` files are generator safety copies.** Gitignored, deletable at will; confirm a backup is actually the version you want before restoring from it.
