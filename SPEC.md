# SPEC – Adjagent

This repository is the source of a drift-proof, model-tunable generation system that renders Claude Code agent and command definitions to order; the multi-capability agent/command set it ships, consumed by other projects, is its flagship rendering. This document states the observable contract — what the repository guarantees, independent of how the guarantee is implemented. See ARCHITECTURE.md for mechanism.

## Deployed Surfaces

The repository exposes exactly two deployed surfaces, `agents/` and `commands/` — the rendered-definition trees `generate` and `check` govern. They are names in the product, not directories in this repository: no render is kept here. Every definition is produced to order into the output root an invocation names, so there is no checked-in copy for a render to be served from, compared against, or drift away from. A consuming project receives the full install product — the two surfaces together with the shipped packages delivered alongside them by explicit mapping (below) — by **install**: one deployment shape, and no other:

```sh
just install <path-to-consuming-project> [--family=NAME] [--model-tier-map=SPEC] [--model-pin-map=SPEC] [--verbose]
```

An install delivers the surfaces entire, into the project's `.claude/`: every definition, rendered by that same invocation — together with the shipped packages those definitions read (below), delivered to their own frozen destinations by explicit mapping rather than as part of either surface. What is not part of the product does not travel: test suites and their fixtures, caches, `.DS_Store`, generator safety copies, and this repository's own project documentation. That last class is a filename vocabulary — the contract-doc precedence chain (`SPEC.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`) plus `ROADMAP.md`, `AGENTS.md` and `CLAUDE.md` — matched at any depth in a shipped package's source. A shipped package's contract states what it must do for someone changing it *here*; a consuming project receives the code and not the project it came from, so no installed prose may assume a reader who can open one. `README.md` is not in that class: it is the one such name a package writes for its consumer, and a package that grows one ships it. The optional tuning arguments — a model family, and overrides to either of the two maps that resolve a definition's tier (the tier map, which the family file declares; the pin map, which the generator supplies and no family owns) — render the generated definitions under that tuning (see Generation System, below).

An installed tree is an **artifact**, not a working copy. This repository is the source of truth, and a re-install always overwrites — local edits to installed files are never preserved, by design. The fix for a wrong installed definition is a change made here and a re-install. The overwrite replaces without destroying: an installed file that is not provably this tool's own output is set aside as a numbered `.bak` beside itself before being replaced, and those safety copies are the consumer's to delete.

Every installed file states its own origin and records the hash of the content beneath that statement — the generated banner (Generated-Definition Integrity) on a definition rendered here, an installed banner on a file copied here. The banner is written in whatever comment syntax the file's type admits, and never where it changes how the file is read: out of a definition's prompt body, out of a script's executable content, out of the first body line a frontmatter-less command is described by, and never in a form that would present a shipped package's own documentation as a definition. Where a file's whole content is itself the payload — consumed verbatim as prompt text, or written out verbatim as a consuming project's own file — there is no place a banner does not change how the file is read, and the file is installed unstamped whatever comment syntax its type would admit: the shipped `.tmpl` templates are that case. A filetype admitting no comment is likewise installed unbannered. Third-party source vendored into a shipped package's `_vendor/` directory is a third class: it ships byte-verbatim and is never bannered whatever comment syntax its filetype would admit, because a banner would carry this repository's provenance claim and edit-here pointer over code it did not write. The verbose install report names every file it leaves unstamped. The recorded hash is what makes Write Safety below decidable for an installed tree exactly as it is for a regenerated one.

Every path written inside a definition body — a file the agent is told to `Read`, a script it invokes — is written against `.claude/agents/...`, not against the repository's own path: that is where an installed definition actually lives, and the path it must resolve from inside a consuming session.

Every file directly under `agents/` matching `*.md` and carrying frontmatter with dispatch keys (`name`, `description`, `model`) is a dispatchable agent definition — the rule holds without exception. What sits under `agents/` besides those definitions is generated too, and structurally outside the dispatch namespace: `mad/participant-contract.md` and the design referee's methodology topic set, `mad/design-topics/`. Each additionally carries a frontmatter block empty of dispatch keys, an independent second guard that keeps it undispatchable on its own terms.

The repository's fourth file class is a **shipped package**: plain code, never rendered and so never carrying a `!GENERATED!` banner in this repository, its tests beside it, installed by explicit mapping (`gen-defs.py`'s `SHIPPED_PACKAGES` table) to a frozen `.claude/agents/…` destination — frozen because an installed consumer's own artifacts, such as a shipped KB's runner snippets, name that destination directly and cannot be asked to notice a source-side rearrangement. Its installed copy is stamped like any other install emission whose filetype admits a comment: the `!INSTALLED!` banner, not `!GENERATED!` — a shipped package is source taken as-is, never a render, exactly as `kb_tools/`'s installed `.py` files are stamped today. `kb_tools/` and `liaison_tools/` are the shipped packages, sourced at the repository top level and installed to `.claude/agents/kb_tools/` and `.claude/agents/liaison_tools/` respectively — sibling to the two deployed surfaces in the install product, never part of either.

## Generation System

The mechanism behind every definition in the deployed surfaces guarantees three observable properties, independent of how they are implemented (see ARCHITECTURE.md):

- **Single-sourcing.** Text shared across two or more definitions has exactly one source. Wherever rendered output shares text across definitions, that text is identical by construction — not by maintenance discipline that could silently drift apart.
- **Additive-only model tuning.** A family's tuning file may extend a definition's base text at declared anchor points; it can never replace, suppress, or fork that text. A definition whose anchors the loaded family does not fill is byte-identical to its base render.
- **Tier-keyed tuning, with recorded provenance.** A definition declares a tier at its pin site — one of `highest`, `high`, `medium`, `low`, `lowest`, ordered by capability — and two maps, each covering all five tiers in every render, resolve it: the tier map names the family member whose overrides the definition is tuned against, and the pin map supplies the model pin the definition renders with. The two are independent, so what a definition is tuned for and what it dispatches on may diverge; that is deliberate, because tuning and dispatch capacity are separately chosen. Divergence is disclosed rather than prevented — every generated definition's banner records the family file and both maps in force at its render, so both answers are readable from the definition alone. A definition with no pin site declares no tier and stands outside this resolution; family-wide tuning text still fills its anchors. A family file whose `[family.*.models.*]` tables name a member no tier reaches — in neither its own `[tiers]` nor the effective tier map — fails to load.

An install that requests a tuning other than the default (Deployed Surfaces, above) renders the generated definitions under it; Generated-Definition Integrity, below, holds for the tuning the install was given exactly as it holds for the default one.

## Generated-Definition Integrity

Every definition in the deployed surfaces (`agents/`, `commands/`) is generated rather than hand-authored — the partition is total, because a definition no template declares is never rendered and so never reaches the product at all. For every definition:

- Its content is byte-identical to what its declared template currently renders.
- Every banner claiming a definition is generated names a template that exists and declares that definition. A banner naming a missing template, or naming a template that does not declare it, is a contract violation.
- Every banner records the hash of the definition body beneath it, so a modification is detectable from the file alone — without the template, the chunk source, or the generator.

A file without a generated banner is not a definition this repository produced, and is outside this guarantee — Write Safety, below, is what governs meeting one at a generation target.

## Write Safety

Regenerating definitions never destroys hand-maintained or already-correct content. For any generation target:

| Target state | Outcome |
|---|---|
| does not exist | created |
| generated banner, render identical | untouched |
| generated banner, body matches the banner's hash, render differs | overwritten in place, no backup kept |
| generated banner, body hash absent or mismatched, render differs | prior content copied aside as a numbered backup, then overwritten |
| no generated banner | refused — never written |

A target is written only when it is provably the tool's own output. A hand-maintained file that happens to share a name with a template's declared output is never silently overwritten.

An install renders the definitions and copies the shipped packages, and reads the same table with one row changed: a target carrying no banner is backed up and overwritten rather than refused, because an installed tree is an artifact and not a place hand-maintained content may live.

The banner's body hash is what separates the two overwrite rows. Content that hashes to its own banner's claim is reproducible from the template at will, so preserving a copy of it protects nothing; content that does not — because someone edited it, or because it predates the hash — is unreproducible and is preserved before being replaced.

## Guest-Extraction Contract

Any definition's body — the content after its frontmatter — must be usable frontmatter-free as an external (non-Claude) model's system prompt. The obligation attaches to definitions, not to depth: the dispatchable definitions directly under `agents/`, plus the participant contract at `agents/mad/participant-contract.md`, must hold the property mechanically — dropping every line through the one that closes the frontmatter block yields the body entire, losing no content and needing no knowledge of what the block holds. The property is guaranteed where a definition is built, not where it is read: a definition that would not extract fails at generation rather than reaching a guest model truncated.

`agents/mad/participant-contract.md` is written specifically for this extraction: it sits outside the dispatch namespace, and its frontmatter is additionally empty of dispatch keys, so neither route can dispatch it as an agent — it exists only to be extracted and relayed as a guest model's system prompt.

## Operator Baseline

The repository publishes a recommended operator-level configuration file for a user's live `~/.claude/CLAUDE.md`. Updates flow explicitly in either direction and the two directions differ. **Publishing a local improvement is a manual act**: diff, copy across, commit. **Adopting a published update is a merge, never an overwrite** — the operator's own sections and edits survive it, and content the merge did not write into is returned byte-identical, whitespace included, because the live file is the operator's and its formatting is not this toolchain's to change; a write that changes the live file's bytes also keeps a single rolling backup of what preceded it beside the file, overwritten by the next such write and left alone by one that changes nothing. A merge it cannot make cleanly leaves the live file untouched and puts the baseline beside it for the operator to integrate by hand.

Neither direction is a symlink, so a write to this repository can never silently rewrite live operator configuration.
