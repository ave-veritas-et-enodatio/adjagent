# user-config — recommended global settings

Recommended user-level configuration for working with this agent set. Agent
definitions travel with this repo; the operator-level working rules they
assume — scratch policy, the authorization gate, task automation, dispatch
preferences — normally live invisibly in each user's home config. This
directory publishes that baseline so it travels too.

Machine-local setup is deliberately NOT published. Anything describing one
machine's environment (an auth sandbox, local users/groups, host paths)
belongs in the live `~/.claude/CLAUDE.md` and nowhere else. A publish
(home → repo) leaves such sections behind; an install (repo → home) leaves
them where they are — a section of yours the baseline knows nothing about is
not something an install has an opinion about.

## Contents

- `INSTALLED_CLAUDE.md` — recommended `~/.claude/CLAUDE.md`: cross-project
  working preferences loaded by Claude Code at the user level in every project.

## Install

Adopt-don't-clobber — this repo never overwrites an existing config silently. The
supported path is the recipe, run from this repo's root:

```sh
just install-claude-md
```

It **merges** `user-config/INSTALLED_CLAUDE.md` into `~/.claude/CLAUDE.md`
rather than replacing it. The merge base — the published revision your live
file was last integrated from — is recovered from this repository's git
history, so nothing is kept beside your file and nothing is ever written into
it to mark a region. Which case you are in is classified and reported, section
by section, before the first byte is written:

- **No `~/.claude/CLAUDE.md` yet**: installed fresh.
- **Your file already matches the published baseline**: nothing to do.
- **Your file is an unmodified published revision**: it carried no local edits,
  so the update applies whole.
- **Your file has local edits over a recoverable base**: merged — your own
  sections and your edits stay, the published changes land around them.
- **No shared ancestry** (hand-written, never installed from here): there is
  nothing to align, so your file is kept entire and the baseline is appended
  below it. Hand-edit out the duplication, and anything of ours that
  contradicts what you wrote.
- **Anything else** — no recovered base merges cleanly: your file is left
  untouched, the baseline is written beside it as `incoming.CLAUDE.md`, and the
  recipe exits nonzero. Integrate by hand from that copy, then delete it.

A write that changes your file's bytes keeps one rolling backup beside it —
`backup.CLAUDE.md`, yours to delete — overwritten by the next such write and
left alone by one that changes nothing. What the merge did not write into
still comes back byte-identical, your spacing included.

The recipe only ever installs repo → home; it never reads live changes back.

## Keeping in sync

The live file at `~/.claude/CLAUDE.md` is a real file, deliberately NOT a
symlink into this repo — a symlink would let anything that writes this repo
(including dispatched agents) silently rewrite live operator config, and
would force every future personal addition to be published. Updates flow by
explicit act in either direction, and the two directions are not the same
mechanism:

- **repo → home**: `just install-claude-md`, above. It reports the case before
  it writes, and it never replaces your edits.
- **home → repo**: still a manual diff-and-adopt. Read the live file against
  `INSTALLED_CLAUDE.md`, copy the improvement here, commit it — leaving your
  machine-local sections behind.

Asking an agent to "diff my ~/.claude/CLAUDE.md against user-config/ and
show me what changed" is the whole of that second procedure.
