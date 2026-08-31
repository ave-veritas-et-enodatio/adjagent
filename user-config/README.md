# user-config — recommended global settings

Recommended user-level configuration for working with this agent set. Agent
definitions travel with this repo; the operator-level working rules they
assume — sandbox discipline, scratch policy, the authorization gate, task
automation, dispatch preferences — normally live invisibly in each user's
home config. This directory publishes that baseline so it travels too.

## Contents

- `CLAUDE.md` — recommended `~/.claude/CLAUDE.md`: cross-project working
  preferences loaded by Claude Code at the user level in every project.

## Install

Adopt-don't-clobber — this repo never overwrites an existing config silently. The
supported path is the recipe, run from this repo's root:

```sh
just install-user-config
```

It installs `~/.claude/CLAUDE.md` from `user-config/CLAUDE.md`, mechanizing that same
adopt-don't-clobber judgment call:

- **No `~/.claude/CLAUDE.md` yet**: installs it fresh.
- **Live file matches the published one**: no-op.
- **Live file differs**: shows the diff, saves the live file beside itself as a
  numbered `CLAUDE.md.NN.bak`, then installs the published version.

The recipe only ever installs repo → home; it never reads live changes back.

## Keeping in sync

The live file at `~/.claude/CLAUDE.md` is a real file, deliberately NOT a
symlink into this repo — a symlink would let anything that writes this repo
(including dispatched agents) silently rewrite live operator config, and
would force every future personal addition to be published. Updates flow by
explicit act in either direction:

- **repo → home**: adopt a baseline update after reading the diff.
- **home → repo**: publish an improvement by copying it here and committing.

Asking an agent to "diff my ~/.claude/CLAUDE.md against user-config/ and
show me what changed" is the whole sync procedure.
