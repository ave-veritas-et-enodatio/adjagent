# CONVENTIONS – kb_tools

House rules and traps only, for someone working *inside* `kb_tools/`. The
contract and its mechanism live in SPEC.md and ARCHITECTURE.md — this file
never restates them, only points at them. Unqualified, those names mean this
directory's copies; the repository-root documents are named as root.

- **Never run these tools ad-hoc.** Use the consuming project's runner
  target (`kb-verify` / `kb-refresh` / `kb-stats`, or the `kb_util` build
  ops through the runner include) — see SPEC.md, Runner Targets, for why
  ad-hoc invocation isn't sanctioned.
- **`pandoc.py` is the only module that names the `pandoc` binary.** It is
  the single enumerated stdlib-only exception (SPEC.md, Corpus Invariants),
  and the property that makes it enumerable is that nothing else in the
  package builds an argv for it or imports it — enforced by
  `tests/test_pandoc_monopoly.py`. A second call site is a defect: it is
  a second place that knows how LaTeX gets read, and the seam exists so
  that replacing the reader is one module's work rather than a sweep.
- **Adding a second stdlib exception is a design decision, not an
  implementer's.** SPEC.md enumerates exactly one (the `pandoc` binary,
  reached through `pandoc.py`); proposing another goes to the architect, not
  into a patch.
- **A `kb_claimgraph` stage never exits on a model's opinion.** Every stage
  exits on a comparison between two artifacts or a subprocess return code
  (`gate.py`, ARCHITECTURE.md's Claim Graph section) — a new stage does not
  get to add a third kind of exit. This is a scar, not a preference: an
  earlier pipeline ran two review loops until a reviewer stopped finding
  problems, against reviewer instructions that made problems inexhaustible —
  one resolving severity ambiguity toward the more severe, another demanding
  a tool be named wherever none existed — while the loop that actually
  finished a build exited on a subprocess return code. A stage that asks a
  model whether the work is good enough is reintroducing that failure, not a
  smaller version of it.
- **No prompt reaches a model from a heredoc or an in-code string.** Every
  prompt is a template under `kb_driver/prompt-templates/`, rendered with
  its slots filled — the seat briefs and the inference asks alike. A prompt
  assembled from string literals is a model-facing artifact that does not
  look like one: it never lands in a diff as prose, it is never read end to
  end, and it never goes to the prompt-engineer, because nothing about it
  says "document". This is a scar. `kb_claimgraph`'s claim-identification
  ask carried an answer format whose central rule — that a prose block's
  two markers carry one number — was stated nowhere in it and demonstrated
  only at block 1, where the two numbers are both `1` and the rule is
  invisible; the seat got it wrong on roughly one answer in ten, always at
  an interior block, and stopped a 176-document build twice. The same ask
  requested a `no-claim` reason as "one sentence saying so", which nothing
  checks beyond non-emptiness and which is written into a document's
  frontmatter permanently. Neither had been read as a prompt by anyone.
- **`prompt-templates/` holds what is dispatched; `prompt-templates/fragments/`
  holds what is spliced.** Which one a new file is, is settled by where it goes
  and by nothing else — no prefix, no suffix, no list to read. Every file under
  `fragments/` is registered in exactly one of `prompt_templates`' two
  vocabularies, `FRAGMENT_SLOTS` (the composer resolves it because a template
  named its slot) or `ALTERNATIVE_NAMES` (a caller picked it), and
  `tests/test_kb_driver_prompt_templates.py` checks that correspondence both
  ways — so a fragment nobody registered fails rather than sitting unreachable.
- **A caller-selected alternative ends without a newline; the template it fills
  supplies every line break around it.** `kb_claimgraph`'s two asks pick between
  `fragments/ask-correction.tmpl`, `fragments/identify-display-maths.tmpl` and
  `fragments/identify-no-display-maths.tmpl`, and each is spliced into a slot
  sitting inside a line rather than appended as a block of lines. An editor that
  adds the customary final newline moves the prompt's bytes and nothing about
  the file says so; the byte-exactness checks in
  `tests/test_kb_claimgraph_cinf.py` and `tests/test_kb_claimgraph_pass2.py`
  are what report it.
- **A register holds four entry kinds, and `RegisterEntry.kind` is where you
  learn which.** `clm`, `sup`, `exp` and — since the off-graph endcap —
  `work`, whose id is `work-` plus a citation key rather than a hash body, so a
  pattern spelling `[a-z0-9]{6}` does not match one. Anything keying on that
  field, or on `locate_register_entries`, meets all four; a branch handling
  three silently drops the fourth, which is how a work entry becomes a lost
  entry the census reports and nobody expected. The one asymmetry to know: a
  `work-` entry carries no `- solidity:` line and no depends-on list, because
  the node is terminal and nothing derives a value for it.
- **Keep no prose copy of an op's key vocabulary.** The vocabulary is
  closed, total, and per-op, stated once in `values.OP_FIELDS`
  (SPEC.md, The Write API's Contract). A second statement of it — in a doc,
  a docstring, or a comment — drifts; point at `values.OP_FIELDS` or the
  refusal message it produces instead of transcribing it.
- **Never spell the node-kind list.** It is `kb_schema.NODE_KINDS`, and the
  same rule applies to it in code as to `OP_FIELDS` in prose. A site that
  *iterates* — a census, a breakdown, a union, an ordering — reads the tuple
  and names no kind; a site that *branches* keeps naming kinds, because that
  is what a branch is, and wraps its table in `kb_schema.kind_table`, which
  refuses one that is not total over the vocabulary at the moment it is
  defined. A count derived from the list ("all four node types") is the same
  copy in a shorter form. This is a scar: `work` arrived as a fifth kind with
  no constant to arrive in, so every site spelled its own list and each drifted
  alone — a census whose total counted the works and whose breakdown never
  named them, and a union returning four of five that drew every `rests-on`
  target as a ghost id with the record sitting in `claims.jsonl` the whole
  time. Note which question you are asking: `kb_schema.ID_KINDS` is what gets
  *minted* and excludes `work` deliberately; this is what exists.

- **A test's placement is decided by what it drives, not by convenience.**
  A test that exercises a `kb_tools` function or module in isolation belongs
  in `kb_tools/tests/` and runs under `test` (`just test`). A test that
  drives a corpus through the pipeline — a `kb-driver` build, a staged
  multi-document corpus, anything reaching a `kb-testing/` runner target —
  is an integration test and belongs in `kb-testing/`, never in
  `kb_tools/tests/`. Two written forms coexist there: the justfile recipes
  that already drive staged corpora end to end, and a pytest tree at
  `kb-testing/tests/` for integration tests better expressed that way — the
  choice between the two is only how the test is written, never what kind
  of test it is. Integration tests run from `integration-test`
  (`kb-testing/justfile`), not from root `test`, which stays the fast unit
  run. Integration test data is staged into gitignored
  `kb-testing/test-data/transient/` and never committed; a test finding no
  staged data skips, which is normal, not a failure. This is a scar: four
  integration tests had settled into `kb_tools/tests/` with nothing written
  down to say they were in the wrong place.

## Tooling-Repo Targets

The agent-definition repo's own justfile; never installed into consumers:

- `test` — the `kb_tools/tests/` suite (auto-provisions `.venv` + `pytest`;
  a dev dependency, never imported at runtime).
- `format-python` — `black` (line-length 120) + `isort` over every python
  surface in the repo at once: `kb_tools`, `liaison_tools`, `tests`, and
  `gen-defs.py`. There is no `kb_tools`-only invocation, so a run after
  editing here also reformats anything left unformatted elsewhere; check the
  diff before committing.
