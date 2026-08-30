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

Adopt-don't-overwrite — this repo never clobbers an existing config:

```sh
cp -n user-config/CLAUDE.md ~/.claude/CLAUDE.md
```

If you already have a `~/.claude/CLAUDE.md`, diff and merge deliberately:

```sh
diff ~/.claude/CLAUDE.md user-config/CLAUDE.md
```

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
