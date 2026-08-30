# SPEC — Agent Definition Repository

This repository is the source of a Claude Code agent/command definition set, consumed by other projects. This document states the observable contract — what the repository guarantees, independent of how the guarantee is implemented. See ARCHITECTURE.md for mechanism.

## Deployed Surfaces

The repository exposes exactly two deployed surfaces, consumed by symlink:

```sh
ln -s <repo>/agents    .claude/agents
ln -s <repo>/commands  .claude/commands
```

A consuming project sees the linked contents as if they lived at `.claude/agents/` and `.claude/commands/`. Every path written inside a definition body — a file the agent is told to `Read`, a script it invokes — is written against `.claude/agents/...`, not against the repository's own path, since that is the path the definition resolves from inside a consuming session.

Every file directly under `agents/` matching `*.md` and carrying frontmatter with dispatch keys (`name`, `description`, `model`) is a dispatchable agent definition. One file is a deliberate exception: `agents/mad-participant-contract.md` carries a frontmatter block empty of those keys — structurally present, but undispatchable (see Guest-Extraction Contract). Non-`.md` entries under `agents/` (`mad-review-topics/`, `mad-design-topics/`, `liaison_tools/`, `kb_tools/`) are supporting material read by definitions, not definitions themselves.

## Generated-Definition Integrity

A subset of definitions in the deployed surfaces (`agents/`, `commands/`) are generated rather than hand-authored. For every such definition:

- Its content is byte-identical to what its declared template currently renders.
- Every banner claiming a definition is generated names a template that exists and declares that definition. A banner naming a missing template, or naming a template that does not declare it, is a contract violation.

A definition without a generated banner is hand-maintained and outside this guarantee.

## Write Safety

Regenerating definitions never destroys hand-maintained or already-correct content. For any generation target:

| Target state | Outcome |
|---|---|
| does not exist | created |
| exists, carries a generated banner, render differs | prior content copied aside as a numbered backup, then overwritten |
| exists, carries a generated banner, render identical | untouched |
| exists, no generated banner | refused — never written |

A target is written only when it is provably the tool's own output. A hand-maintained file that happens to share a name with a template's declared output is never silently overwritten.

## Guest-Extraction Contract

Any definition's body — the content after its frontmatter — must be extractable frontmatter-free for use as an external (non-Claude) model's system prompt. The observable property every `agents/*.md` must hold is mechanical: extraction succeeds and yields the complete body (`agents/liaison_tools/extract-agent-body.sh` exits zero, drops exactly the frontmatter block, loses no body content).

`agents/mad-participant-contract.md` is written specifically for this extraction: its frontmatter is deliberately empty of dispatch keys so it cannot itself be dispatched as an agent — it exists only to be extracted and relayed as a guest model's system prompt.

## Operator Baseline

The repository publishes a recommended operator-level configuration file, byte-syncable against a user's live `~/.claude/CLAUDE.md`: the published copy and the live file are compared by direct diff, and updates flow explicitly in either direction (publish a local improvement; adopt a published update) — never by symlink, so a write to this repository can never silently rewrite live operator configuration.
