# writeapi-regression — provenance

The frozen failure corpus for the 2026-09-02 regression replay. These files are
**inputs to tests only**. They are never edited, never re-derived, and never installed into a
consuming project — `INSTALL_EXCLUDED_DIRS` carries `tests`, so every path
under `kb_tools/tests/` is carved out of the install walk.

## Freeze source

| Fact | Value |
|---|---|
| Repository | a transient build fixture repository, read-only to this program |
| Commit | `edf672ac848a5447241fa6f93326b344860f0450` |
| Subject | `kb-build: phase-3 \| distillation` |
| Date | 2026-09-02 18:16:00 -0700 |

Bytes were taken from that commit's **git objects** (`git show <commit>:<path>`),
never from the worktree: the fixture repository hosted running builds and its
restage recipe reset it hard, so a worktree copy could not survive as evidence.
That repository is no longer reachable, which is why the digests below are the
only standing check on these bytes.

## What was taken

Five files, each a `claim-quality.md` register the KB held at that commit. Each
is flattened to `<domain>-claim-quality.md`, because files of one name cannot
share a directory.

| Fixture file | Source path at the commit | Bytes | sha256 |
|---|---|---|---|
| `part1-claim-quality.md` | `kb-root/part1/claim-quality.md` | 1595 | `14c93b02bb891b848959f78dee226051b3836e6d3c9fbb1231a3ec40ff5352f3` |
| `part2-claim-quality.md` | `kb-root/part2/claim-quality.md` | 9702 | `8cef30961e2f47c0cf3ab737cf4d9d3cac5fdd18b2b77c555d0a1cfc784a7179` |
| `part3-claim-quality.md` | `kb-root/part3/claim-quality.md` | 4341 | `6ccc9e11bc9e2e993a5976adb941772aa5cc83c412372eef67f0160234735f9b` |
| `part4-claim-quality.md` | `kb-root/part4/claim-quality.md` | 1402 | `28ac7158485828e494d7268110d9928fc516745c20dc1f61ae11af20dbf59639` |
| `part5-claim-quality.md` | `kb-root/part5/claim-quality.md` | 1080 | `2b397095b08c6eac6070f06f9b523ec7915d90a33c8bb4d4c72f89259b3f10c7` |

One redaction, recorded here because these files are otherwise never edited:
`part4`'s first `depends-on` bullet named the LaTeX file its conjecture was
stated in, and that source is no longer distributable. The filename was removed
and the bullet now reads `` `conj:transcritical` (lines 204–209) ``, matching
the bare-line form its two siblings already use. The digest above is over the
redacted bytes; nothing else in the file changed, and no marker, heading, score
or edge moved.

## The defect they carry

Every entry's `<!-- id: … -->` marker is authored **above** its `##` heading.
`kb_index_lib.parse_claim_quality_file` binds a marker to the *preceding* `##`
heading (`kb_index_lib.py:930-937`), so in each register the first marker has no
preceding heading and emits **no record**, and every marker after it inherits the
previous entry's title — every title in the file is off by one entry. Layout, and
nothing else: every id was real and registered, so no id check, reference check,
or rubric could have fired. That class is closed at three independent points.

The corpus census, reproduced by the guard test beside this file:

| Register | `<!-- id: clm-` markers | Parsed records | Lost |
|---|---|---|---|
| `part1-claim-quality.md` | 2 | 1 | `clm-07687j` |
| `part2-claim-quality.md` | 15 | 14 | `clm-3ig11l` |
| `part3-claim-quality.md` | 4 | 3 | `clm-k970j7` |
| `part4-claim-quality.md` | 1 | 0 | `clm-424074` |
| `part5-claim-quality.md` | 1 | 0 | `clm-gi3glh` |
| **Total** | **23** | **18** | **5** |

`clm-07687j` is the quiet one: its only reference anywhere in this fixture is a
`depends-on` bullet inside its own register, which the reader drops silently
(`kb_index_lib.py:773-780` under `diagnostic_stream=None`), so nothing about its
loss would reach a verifier from these bytes alone. The other four surfaced only
because a leaf happened to cite them.

## What is not here, and why

- **The phase-2.5 mint record.** It belongs to the freeze, but
  `.claude-temp/kb-build/phase25-id-assignments.md` is **not in commit
  `edf672a`** — `.claude-temp/` is gitignored in the fixture repository, so the
  file has no git object to capture. Its only copy was that repository's
  worktree, which its restage recipe wiped on every run. The values the replay
  needs (ids, titles, confidences, rationales, depends-on sets) are all carried
  by the five registers above; what the mint record adds is the independent
  id ↔ LaTeX-`\label` ↔ source-line mapping.
- **A known-good register.** None exists at that commit: all registers carry
  the defect, uniformly. The known-good specimen already in this tree is
  `../mini-kb/claim-quality.md` (hand-built, heading-then-marker), and the
  same-corpus known-good control is what the golden renders produce.
- **Any support (`sup-`) entry.** The registers kept here declare claims only.
  The support-entry path through `render`/`store`/`ops` is therefore exercised
  by the unit and golden suites rather than by this replay.

## Re-creating the freeze

It cannot be re-created: the source repository is gone. Byte-exactness is
checkable only against the table above, which is what the guard test beside this
file does on every run.
