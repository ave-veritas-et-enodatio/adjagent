# SPEC — Agent Definition Repository

This repository is the source of a Claude Code agent/command definition set, consumed by other projects. This document states the observable contract — what the repository guarantees, independent of how the guarantee is implemented. See ARCHITECTURE.md for mechanism.

## Deployed Surfaces

The repository exposes exactly two deployed surfaces, `agents/` and `commands/`. A consuming project receives them by **install** — one deployment shape, and no other:

```sh
just install-defs <path-to-consuming-project> [flavor]
```

An install delivers the surfaces entire, into the project's `.claude/`: every definition, generated and hand-maintained alike, together with the supporting material those definitions read — the MAD topic sets, `kb_tools/`, `liaison_tools/`. What is not part of the product does not travel: test suites and their fixtures, caches, and generator safety copies. The optional flavor renders the generated definitions under a model family or model; Generated-Definition Integrity below then holds for the flavor the install was given.

An installed tree is an **artifact**, not a working copy. This repository is the source of truth, and a re-install overwrites unconditionally — local edits to installed files are not preserved, by design. The fix for a wrong installed definition is a change made here and a re-install. Every installed tree carries a provenance stamp at `.claude/agents-install-manifest.json` naming the generator version, the source commit, the flavor, and the install time, so any tree can be traced back to what produced it.

Every path written inside a definition body — a file the agent is told to `Read`, a script it invokes — is written against `.claude/agents/...`, not against the repository's own path: that is where an installed definition actually lives, and the path it must resolve from inside a consuming session.

Every file directly under `agents/` matching `*.md` and carrying frontmatter with dispatch keys (`name`, `description`, `model`) is a dispatchable agent definition — the rule holds without exception. Supporting material — read by definitions, never dispatched as one — lives in subdirectories: `mad/` (the participant contract, plus the two referees' methodology topic sets `mad/review-topics/` and `mad/design-topics/`), `liaison_tools/`, and `kb_tools/`. The participant contract at `agents/mad/participant-contract.md` is therefore structurally outside the dispatch namespace; it additionally carries a frontmatter block empty of dispatch keys, an independent second guard that keeps it undispatchable on its own terms (see Guest-Extraction Contract).

## Generated-Definition Integrity

A subset of definitions in the deployed surfaces (`agents/`, `commands/`) are generated rather than hand-authored. For every such definition:

- Its content is byte-identical to what its declared template currently renders.
- Every banner claiming a definition is generated names a template that exists and declares that definition. A banner naming a missing template, or naming a template that does not declare it, is a contract violation.
- Every banner records the hash of the definition body beneath it, so a modification is detectable from the file alone — without the template, the chunk source, or the generator.

A definition without a generated banner is hand-maintained and outside this guarantee.

## Write Safety

Regenerating definitions never destroys hand-maintained or already-correct content. For any generation target:

| Target state | Outcome |
|---|---|
| does not exist | created |
| generated banner, render identical | untouched |
| generated banner, body matches the banner's hash, render differs | overwritten in place, no backup kept |
| generated banner, body does not match the banner's hash, render differs | prior content copied aside as a numbered backup, then overwritten |
| no generated banner | refused — never written |

A target is written only when it is provably the tool's own output. A hand-maintained file that happens to share a name with a template's declared output is never silently overwritten.

The banner's body hash is what separates the two overwrite rows. Content that hashes to its own banner's claim is reproducible from the template at will, so preserving a copy of it protects nothing; content that does not — because someone edited it, or because it predates the hash — is unreproducible and is preserved before being replaced.

## Guest-Extraction Contract

Any definition's body — the content after its frontmatter — must be extractable frontmatter-free for use as an external (non-Claude) model's system prompt. The obligation attaches to definitions, not to depth: every file under `agents/` opening with a YAML frontmatter block — the dispatchable definitions directly under `agents/`, plus the participant contract at `agents/mad/participant-contract.md` — must hold the property mechanically (`agents/liaison_tools/extract-agent-body.sh` exits zero, drops exactly the frontmatter block, loses no body content). Supporting material carrying no frontmatter — methodology topics, tool documentation — is not a definition, is outside this guarantee, and extraction over it correctly fails rather than yielding a silently truncated prompt.

`agents/mad/participant-contract.md` is written specifically for this extraction: it sits outside the dispatch namespace, and its frontmatter is additionally empty of dispatch keys, so neither route can dispatch it as an agent — it exists only to be extracted and relayed as a guest model's system prompt.

## Operator Baseline

The repository publishes a recommended operator-level configuration file, byte-syncable against a user's live `~/.claude/CLAUDE.md`: the published copy and the live file are compared by direct diff, and updates flow explicitly in either direction (publish a local improvement; adopt a published update) — never by symlink, so a write to this repository can never silently rewrite live operator configuration.
