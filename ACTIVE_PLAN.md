# Active Plan

Two workstreams. **Part 1 (C-inf) precedes Part 2 (`rests-on`)** — the first live inference run
halted inside C-inf, and no inference-based refinement lands until it holds.

---

# Part 1 — C-inf identifies claims by quotation, not by address

## 0. What the first live run measured

Two papers ran with inference enabled (`kb-testing/justfile`, `just kb-driver-arxiv-corpus`).

**It works.** `2609.09855v1` minted 13 in-prose claims across two documents — anchored inline, listed
in frontmatter, edges deliberately absent.

**It halted.** A third document's answer failed to parse twice, identically: a prose block opened as
`2` and closed as `3`. Nine well-formed claims were discarded whole, and the second attempt carried a
correction *restating the rule* and made the same slip in the same place.

**It drifts.** The same document, asked twice:

```
attempt A starts: S4  S6  S10  S12  S13  S15  S19  S22  S24
attempt B starts: S4  S6  S10       S13       S19  S22
```

B's starts are a subset of A's. The two never conflict about where a result begins; A subdivides
three of B's spans. The free variable is granularity, which the ask anchors nowhere.

**Every failure is the model maintaining a pointer.** `"locator": "S10-S11"` points into the
document; `"title-block": 1` and `KB-CLAIMGRAPH-PROSE 2` point into its own answer. None is a
judgement. And a wrong address is undetectable: `S10-S11` where the seat meant `S10-S12` is a
well-formed record aimed at the wrong span, and no downstream check can tell.

`kb_tools/CONVENTIONS.md:41` records this failure class once already, from a 176-document build. The
remedy it prescribes was applied to `fragments/envelope-contract.tmpl` and not to `identify.tmpl`,
whose example still demonstrates only block `1` — where both numbers are `1` and the rule is
invisible.

## 1. The answer shape

**The model quotes; the tool addresses.** One ask per document. The answer is a flat sequence of
self-contained blocks in any order, with no numbering and no cross-references:

```
<<<KB-CLAIMGRAPH-CLAIM
quote: While probabilities are often said to have a direct connection to degrees of belief, we argue
label: S4
title: Probability does not depend on degrees of belief
KB-CLAIMGRAPH-CLAIM
```

A document stating no result returns **zero** claim blocks and exactly one reason block:

```
<<<KB-CLAIMGRAPH-NO-CLAIM
This section restates notation introduced earlier and states no result of its own.
KB-CLAIMGRAPH-NO-CLAIM
```

Zero claim blocks *alone* is not an answer — it is indistinguishable from a failed one. The reason
block is what makes "states none" a positive assertion, and it feeds `conform.AUTHORED_NO_CLAIM`
exactly as the current `no-claim` field does.

- **`quote`** is the opening of the sentence where the result begins, in the document's own words. It
  is a lookup key and never content: once it resolves, it is discarded and the tool cuts the bytes.
- **`label`** is a cross-check, not the address. Resolution runs off the quote; the label breaks ties
  and catches divergence.
- **`title`** is the seat's own words — the one thing it composes.

Every field is a single line, and that is founded rather than asserted: `label.render` emits one
sentence per line with wraps collapsed, so a quoted sentence cannot contain a newline.

### 1.1 Ruling — a third transport class

`kb_driver/envelope.py` states two: **JSON** for structure, and **raw delimited blocks keyed by
index** for anything a seat composes or quotes, so *"no escape sequence exists anywhere in the
transport."* The claim block is a third: **a raw delimited block carrying a closed, fixed set of
single-line fields.**

It is not a departure from the data-language rule. Author bytes already travel raw; what this drops
is the *index*, which is the pointer that failed. It answers `envelope.py`'s two stated objections to
grammars on their own terms: a `quote:` / `label:` / `title:` form has priors as strong as JSON's,
and its failures name themselves — *"the block has no `label:` line"* is as actionable as a decode
error. No field nests, quotes or balances, so the grammar's only failure is a missing or misspelled
field name, and that failure is local to one claim.

**JSON with positional pairing was considered and rejected**: it is the same pointer with the number
erased, and it converts a detectable mismatch into a silent one — a title attached to the wrong
quote, well-formed and unreadable as an error.

`envelope.py`'s module docstring is rewritten to record the third class beside the other two (§6).

### 1.2 Extent is mechanical

A claim runs from its resolved start sentence to **the next resolved start in the same paragraph, or
the paragraph's end**, whichever comes first. Starts partition a paragraph; they never nest.
`label.py` already guarantees no span crosses a paragraph break.

**Extent-for-meaning and locator-for-marking are separate.** The semantic extent is the rule above.
The locator handed to `mark-claim-in-leaf` must additionally be *unique in the document*, and
`Render.widen` / `identify._unique_slice` remain the lever for that. They are not retired by this
plan; they stop serving a seat-named range and start serving marker findability alone.

### 1.3 Why this is not a reversal of `label.py`'s ruling

That module records the prior call: the ask *"used to ask for the author's words back, which made a
model the transport for bytes it did not compose; this module removes that demand."* The ruling
stands. No model-typed byte reaches the KB — the quote resolves to a label and the tool cuts, as
now. What changes is that the address is **verified** instead of **trusted**. State this in the
commit; on its face it reads as a reversal of a decision made for good reason.

## 2. Resolution

One entry point, no caller-selectable mode. A caller-selected matching mode recreates exactly the
failure `ops.excerpt_lines`' docstring exists to close — *"a pre-check that disagrees with the op it
pre-checks is worse than no pre-check."*

Escalation is internal and deterministic for every caller:

| tier | search | on hit |
|---|---|---|
| 1 | strict — blockquote-stripped, whitespace-collapsed (today's terms) | resolve |
| 2 | folded — markup stripped structurally, then lowercased, punctuation to space, whitespace collapsed; digits kept | resolve, flagged as folded |

Tier 2 runs **only** when tier 1 returns zero. Exact always wins when available, so folding can only
add resolutions strict would have missed, and any ambiguity folding introduces is attributable to it.

### 2.1 The public surface — settled

`resolve_excerpt` is **the single implementation and is public**. It returns the hits and the tier
that produced them, each hit carrying a body line index **and a character offset into the joined
haystack**.

`excerpt_lines(document, excerpt) -> tuple[int, ...]` keeps its exact signature and **delegates**,
returning the resolver's hits with the tier dropped — including tier-2 hits. Its three callers and
ten test references do not change.

The consequence is intended and must be written down rather than discovered: `mark-claim-in-leaf`
now binds an excerpt that differs from the document only by markup, typography, case or punctuation.
`_locate_excerpt`'s refusal text (*"wrapping does not matter but wording does"*) becomes false and is
rewritten. `identify.py:469`'s post-placement check may now be satisfied by a folded hit; that is
correct — it asks whether the marker is findable and unique, and folded-findable is findable.

### 2.2 Hit counting and the label cross-check

On the hit count: **0** → re-ask A; **1** → accept; **>1** → settled without a call when the seat's
`label` names exactly one of the hits, else re-ask B.

**Which label names a hit** is a decision, not a lift. `ask.py:401` is `_fence_labels`, a
fence-membership filter, and is not the mapping. The mapping is many-to-many — several sentences
share a physical line and one sentence may span several — so it resolves by **offset**, not by line:
the label naming a hit is the sentence whose character span contains the hit's offset. This is why
§2.1 requires the offset in the hit.

### 2.3 Why folding is required now

Measured over 713 candidate sentences (≥5 words) in 27 claim-hosting documents of the two built KBs:

| | markup | typography |
|---|---|---|
| whole sentence | 31.8% | 16.5% |
| first 10 words | 12.9% | 7.3% |

`collapse_prose` normalises whitespace only, and the haystack retains `<span class="citation"
data-cites="…">` wrappers. A seat quoting such a sentence will omit the span and strict matching then
fails outright. Quoting the opening more than halves exposure but still leaves roughly one candidate
in six with a dirty anchor.

### 2.4 Why aggressive folding is safe

Measured over the 843 sentences in those same 27 documents:

| canonical length | collisions | what they are |
|---|---|---|
| 1 word | 15 | `proof`, `definition`, `proposition` — structural labels |
| 3 words | 1 | a repeated hypothesis line |
| 12 words | 1 | genuinely identical repeated sentence |
| 21 words | 1 | genuinely identical repeated sentence |

Folding never merged two distinct prose sentences in 843. The one-word collisions are boilerplate
that cannot be a claim opening and are removed by §2.5. The two long ones are actually duplicate
text, where no quote-based scheme can disambiguate and the `label` cross-check is the tiebreaker —
a ~0.2% residue.

### 2.5 Candidate exclusion — a filter on hits, in `kb_claimgraph`

Excluded from being a claim opening: canonical forms of ≤3 words, and sentences whose canonical form
is empty.

The empty case reaches **markup-only lines** — HTML wrappers, a bare blockquote prefix,
punctuation-only lines, an empty link. It does **not** reach mathematics, and must not: a LaTeX
control word is an alphanumeric run, so `\qquad` folds to `qquad` and `\begin{align}` to `begin
align`. Folding those away would stop a quotation carrying display maths from resolving at tier 2 —
a worse failure than the one this exclusion prevents. Fence interiors are removed instead by the ≤3-
word rule for short lines and by the fence extents `identify` already holds for long ones. (The
234-of-1860 measurement across all 42 documents of the two KBs counts both populations together and
so overstates what the empty test alone removes.)

**It composes as a filter over returned hits, applied in `kb_claimgraph` after the resolver returns
and before the count in §2.2 is taken.** No policy enters `kb_write`, and the resolver stays a pure
matcher over a joined string.

## 3. Re-asks carry information the first ask did not have

`ask.py` documents today's retry as *"re-issued **identically**"* with the previous answer's
mechanical failure appended — which for a format error is the rule restated. It went out twice
tonight and returned the same slip both times.

**Cadence: one call per unresolved claim**, not one per document. Isolating the ask is what raises
the odds on each, and the population is its own measurement (§3.2).

| trigger | what the re-ask carries |
|---|---|
| **A** — quote resolves nowhere | the nearest window the search came closest to, with its label, plus the labelled neighbourhood of the claimed `label` |
| **B** — quote resolves in several places and `label` matches none | the colliding candidates with their labels |
| **C** — quote resolves, `label` disagrees, and the resolved sentence is not the labelled one | what stands at the claimed label, beside what the quote matched |

**Near-miss auto-accept is about the label, not the tier.** Accept silently when the resolved
sentence *is* the labelled one — whether tier 1 or tier 2 produced it. A tier-2 match whose label
disagrees still raises trigger C; folding is why that population exists and must not swallow it.

**Every menu carries an explicit "none of these" answer**, and an answer of "none of these" ends that
claim as unresolved rather than re-asking. A forced choice over a narrowed window gets confidently
answered even when the right sentence lies outside it.

### 3.1 Budget

Today's `PARSE_RETRY_BUDGET` / `CHECK_RETRY_BUDGET` / `CALL_BUDGET` are 1/1/3 **per document** and
must re-base. The replacement: **one re-ask per unresolved claim, under a per-document ceiling on
total re-ask calls.** When the ceiling trips, every still-unresolved claim is recorded unresolved
(§4) — the two compose with no special case.

### 3.2 Telemetry — the datapoint this cadence buys

The stage records and the report emits, per document: claims returned by the first ask, resolved on
the first ask, re-asked, resolved on re-ask, recorded unresolved. If first-ask resolution is
routinely poor and per-claim re-asks routinely succeed, that is the evidence for asking per claim
from the start, and it costs nothing to collect.

### 3.3 The re-ask is a new template, not an edited fragment

`fragments/ask-correction.tmpl` is **shared**: `identify.tmpl:77` and `depends.tmpl:35` both fill
`@!correction!@`, and `prompt_templates.ALTERNATIVE_SLOTS` registers `"correction": (None,
"ask-correction")` as a closed pair. Editing it for C-inf changes stage D's re-ask and trips the
byte-exactness checks in `test_kb_claimgraph_cinf.py` and `test_kb_claimgraph_pass2.py`.

`ask.py` already rules the case: *"Where the question itself differs, there is an **alternate
template**"* — the precedent is `depends-cycle-reask.tmpl`. So C-inf's re-ask is a registered
alternate template, and its registration in `prompt_templates.ALTERNATIVE_SLOTS` /
`FRAGMENT_SLOTS` is part of this change; `tests/test_kb_driver_prompt_templates.py` checks the
correspondence both ways.

## 4. An unresolvable claim costs that claim

**The partial case already works.** `write_claims` writes per claim, and a document landing even one
claim takes `conform.HOSTS_CLAIMS` and leaves `AWAITING`. Four of five resolving costs nothing today
beyond not raising.

**The all-failed document is the whole of the change.** With nothing written it stays `AWAITING`,
and `discover._still_awaiting` — C6's exit condition — halts the run.

This needs a **fifth `conform.Determination`**: identification ran and anchored nothing. It is
distinct from `AUTHORED_NO_CLAIM`, which is the falsehood `identify.py`'s ruling refuses to write —
*"recording that on the strength of a model failing to point into a document writes a falsehood into
the graph."* That ruling is preserved, not overturned: we are not recording that the document states
nothing, we are recording that we could not anchor what it stated.

Follow the existing precedent rather than adding a frontmatter key: `assemble.UNSCANNED_REASON` is
already a reserved reason literal *"compared by identity, and that literal is load-bearing."* The new
state is that pattern.

Required with it:

- `conform.Determination` gains the member; its class docstring (*"The other three each say the
  question is answered"*) is rewritten.
- `conform.determination()` recognises the reserved literal.
- `discover._still_awaiting` exempts it, so C6's exit condition no longer halts.
- `write.py` gains the op that writes it.
- `identify.py`'s failure path stops raising for this case; its module docstring's *"The one
  asymmetry on failure"* paragraph is rewritten.

**The loudness condition is binding.** A document ending the build with zero claims must be a
prominent finding naming the document and the count — not a line in a log. A silent zero is the
failure class this whole plan exists to remove; adding a state that exits `AWAITING` without a loud
report would install it deliberately.

It is carried at `FACT`, as a dedicated run-level finding naming every affected document plus a
per-document zero-claim detail. The alternative — a fourth `kb_util` status, a warning that does not
gate — was weighed and deferred: `FAIL` gates and would reinstate the halt this section removes, and
whether `FACT` reads as prominent enough is answerable by using it rather than by argument. Revisit
once there is experience of a real build carrying one.

## 5. Change per module, and the interface between the two coders

The work splits on file ownership with no overlap. **Coder A owns the matcher; coder B owns the
stage.** B codes against the interface A delivers, frozen here:

```
resolve_excerpt(document: str, excerpt: str) -> Resolution
    Resolution.hits: tuple[Hit, ...]      # Hit.line: int (0-based body line)
                                          # Hit.offset: int (into the joined haystack)
    Resolution.tier: int                  # 1 strict, 2 folded
label.labels_at(render: Render, offset: int) -> str
    The label of the sentence whose character span contains offset.
```

### Coder A — `kb_tools/kb_write/ops.py`, `kb_tools/kb_write/render.py`, `kb_tools/kb_claimgraph/label.py`

- `resolve_excerpt` as above: the single implementation, public, tier-escalating per §2.
- `excerpt_lines` keeps its signature and delegates (§2.1).
- Fold is **search-side only**, on the precedent already in the file: *"Stripping is search-side
  only: the line count driving the returned indices is taken from `str.splitlines` before any
  stripping."* Returned indices still point at real physical lines.
- **Do not touch `render.collapse_prose`.** It is applied at write time so stored text equals
  readback — *"what makes the write API's readback comparison well-posed."*
- Promote the nearest-window diagnostic. `_nearest_text` returns `haystack[start:start+len(needle)]`
  — text with no position — and cannot name the sentences a trigger-A re-ask must show. Expose the
  same difflib result carrying its offset.
- Rewrite `_locate_excerpt`'s refusal text (§2.1).
- `label.labels_at` per the interface; the render itself is unchanged.

### Coder B — `kb_tools/kb_claimgraph/{ask,identify,discover,conform,write,assemble}.py`, `kb_tools/kb_driver/envelope.py`

- **`envelope.py`**: parser for the fixed-field raw block — each block independent, malformed blocks
  reported individually rather than failing the answer. `extract_prose_blocks` is **unchanged**; the
  driver's WAVE envelope still uses it, and stage D's declaration (`Level("depends", …, prose=())`)
  is an empty prose vocabulary that must keep *refusing* a prose block. Stage D uses no prose blocks;
  any statement that it does is wrong.
- **`ask.py`**: claim-block and no-claim-block names, markers and marker slots. C-inf's `identify`
  level re-declared for the new shape — note `envelope.Level` models `keys` / `json` / `prose` and
  cannot express this shape today, so `Level` / `check_levels` grow the case. Re-ask policy per §3,
  with the alternate template registered per §3.3.
- **`identify.py`**: candidates arrive as `(quote, label, title)`. Resolution per §2, extent per
  §1.2, exclusion per §2.5. Of `check_answer`'s seven checks, state per check whether it survives;
  two need an explicit answer — locator uniqueness (kept, served by widening per §1.2) and the
  collision check (two starts in one wrapped paragraph now routinely share a physical line, so it
  compares labels, not lines).
- **`conform.py`, `discover.py`, `write.py`, `assemble.py`**: the fifth `Determination` and
  everything in §4.
- Telemetry per §3.2.

### Prompt-engineer, not a coder

`kb_driver/prompt-templates/identify.tmpl` rewritten for the new shape, plus the new C-inf re-ask
template. Both are durable model-facing artifacts and get prompt-engineer authorship and review
before first use. The current `identify.tmpl` demonstrates block `1` only, which is the documented
cause of the failure this plan removes: **any example must demonstrate the format at more than one
instance.**

## 6. Documents this invalidates

Carried as its own section because landing without it leaves the contract stating the opposite.

**`kb_tools/ARCHITECTURE.md` — tech-writer owns this file.** The `ask.py` row (*"**The seat quotes
nothing**: it returns a title it composed and a locator, and the tool cuts the bytes"*), the
`identify.py` row (the check order this plan reorders), the `label.py` row, and the Claim Graph
section's *"Discovery's checks are weak by construction"* paragraph all become false on landing.

**Docstrings that state the opposite and are rewritten in this change:**

- `ask.py` module docstring — *"It never quotes: the seat picks a label and the tool cuts the bytes"*
  and *"Nothing either seat composes travels inside the JSON"*
- `identify.py` module docstring — the *"one asymmetry on failure"* paragraph (§4) and the check
  order
- `identify.check_answer` docstring
- `envelope.py` module docstring — the two-transport-class paragraph gains the third (§1.1)
- `conform.Determination` class docstring (§4)
- `ops._locate_excerpt` refusal text (§2.1)

**`kb_tools/CONVENTIONS.md`** gains the retry rule this run demonstrated, beside the prompt-template
scar already at line 41: *a re-ask carries information the first ask did not have; a re-ask that only
restates the requirement is a spent call.*

## 7. Acceptance criteria and the instruments that check them

`kb_tools/tests/test_kb_claimgraph_cinf.py` is the instrument for 3–10: it is the only surface that
drives C-inf against a fixed `SeatAsk`. Criteria 1–2 are checked by the existing `kb_write` tests.

1. `excerpt_lines`' signature and strict-tier results are unchanged; its three callers and ten test
   references are untouched.
2. Exactly one matching implementation exists, and `render.collapse_prose` is byte-for-byte
   unchanged.
3. A quote differing from the document only by markup, typography, case or punctuation resolves and
   reports tier 2.
4. A quote resolving to several sentences is settled by `label` without a call when `label` names one
   of them, and raises re-ask B otherwise.
5. A tier-2 match whose label disagrees raises trigger C rather than being auto-accepted.
6. A malformed or unresolvable claim block costs that claim only: the document keeps its other
   claims, and a document losing *all* of them takes the fifth `Determination` rather than halting
   C6 or taking `AUTHORED_NO_CLAIM`.
7. A document ending with zero claims produces a prominent finding naming it.
8. No answer field is a number the model must keep consistent with another number.
9. Every re-ask prompt contains at least one fact computed after the previous answer, and every
   re-ask offering a choice offers "none of these".
10. Claim extents partition their paragraph and never nest or cross a paragraph break.
11. `just test` is green — assert it from the run, not from reasoning. The count rises; do not pin it.
12. **Driver-level, and not the implementing agent's to assert**: `2609.09855v1` completes
    claims-discovered end to end including `relation-to-rational-degrees-of-belief.md`. The target is
    `just kb-driver-arxiv-corpus` in `kb-testing/justfile` (`ARXIV_LIVE_IDS := "2609.09855v1
    2609.10318v1"`), run by the main session after both coders land.

## 8. Out of scope

- **The driver's WAVE envelope and stage D's answer contract.** The same numbered-block mechanism
  carries the driver's envelope and has the same reason to fail; that is a known exposure, recorded
  rather than unexamined. C-inf works first.
- **Edges from discovered claims.** C-inf identifies; it does not connect.

  Measured after the first complete run, on `2609.09855v1` — 129 claims, 123 of them prose-
  discovered, 121 `depends-on` edges. Discovered claims are **already full graph citizens**: 117
  edges have one as their source and 106 as their target. What is missing is not attribution for
  them as a class. It is that **40 claims carry no edge at all, and every one is prose-discovered**,
  for two reasons that compound:

  1. **The candidate set comes from cross-references.** `attribute.narrow` builds candidates from
     `inventory.anchors`, and states its own rule: *"a source that ends with no candidate is absent
     rather than asked an empty question."* The stage reports the count out loud. A prose claim
     resting on a definition carries no `\ref` to it — definitions are not labelled environments —
     so its candidate set is empty, it is never asked, and it can carry no edge by construction. 15
     of the 129 were never asked.
  2. **A definition is not a node to point at.** Of the orphans that *were* asked, at least 9 have
     D-inf naming a definition as the antecedent — for `clm-5gpeol`, *"derives from one thing only:
     the definition of calibration … which is not a"* candidate. `FrameworkNode` is exactly this
     kind, solidity 1.0 by construction, and `parse_framework_nodes` mints invariants and axioms
     from `### INVARIANT-XX:` headings and `- Axiom N:` bullets in `invariants.md`, falling back to
     `CLAUDE.md`. A single-domain corpus has neither, and no pass mints a framework node from the
     paper's own definitions. The edge type exists with nothing on its far end.

  Not every orphan is a defect: 11 of the 40 have D-inf stating explicitly, after reading the leaf,
  that nothing supports them. Comparison, introduction and example sections state positions rather
  than derive results, and the orphans cluster there — 14 in
  `comparison-with-conventional-interpretations.md` alone.

  So the work is two changes, and the second is worthless without the first: widen the candidate set
  past `\ref` anchors, and give definitional dependence a node to land on.
- **The phase-5 cap-exhausted barrier** and `installed/CLAUDE.md.tmpl`'s unconditional
  `invariants.md` read instruction — separate defects from the same run, separate work.

## 9. Two things easy to get wrong

1. **A quote is a key, never content.** If an implementation ever writes the quote rather than the
   cut span, `label.py`'s ruling has been reversed in fact while §1.3 claims it wasn't.
2. **The corpus evidence is two papers** — 843 sentences, 27 documents. `just stage-arxiv-corpus`
   (`kb-testing/justfile`) widens it, and should be run before §2.4's thresholds are treated as
   corpus-wide.

---

# Part 2 — `rests-on` joins the `min` branch

## 0. Verdict on the shape

The direction is right and the code supports it cheaply: `rests-on` edges already sit on
`ClaimEntry.depends_on` beside `depends` edges, already carry a parsed `fraction`, and the work's
`strength` is already parsed into `KbState.works`. The change is a few lines inside one loop plus a
parameter threaded through six call sites.

**One substantive disagreement, on what enters the `min`.** The `supports` row is the obvious
analogue and its arithmetic is `sup_solidity × fraction`. Transplanted to a `min` gate that is
**backwards**: `strength × fraction` makes the gate *harsher* as applicability *falls*, so a work
judged sound-but-irrelevant (`strength 0.9`, `fraction 0.0`) would gate the claim to `0.0` —
annihilating a claim on the strength of a judgement that the work bears nothing on it. SPEC already
rules on that value's meaning (Claim-Graph Nodes and Edges: *"A zero on-point fraction is a value,
not an absence. It says the supporting work is sound on its own terms and bears nothing on this
claim"*). Under a lift, multiplying discounts toward zero and that is correct; under a gate, a
discount must run toward **1.0**, and every arithmetic that does so (`1 − f(1−s)`, `max(s, 1−f)`)
treats ordinal bands as probabilities — the exact thing SPEC's dep-gate rationale refuses — and
produces a `min` operand written on no disk anywhere, which the "no new derived field" constraint
forbids from being written.

So the rule below makes `fraction` decide **whether** a pairing gates and `strength` decide **how
hard**. Consequence accepted and stated rather than hidden: applicability `0.3` and `1.0` gate
identically. If graded applicability is wanted, that is a new derived scalar and the constraint has
to move first.

## 1. Invariants

1. **One computation.** `kb_index_lib.compute_solidity_full` remains the sole solidity rule. No
   second path, no second reading of a work's `strength`. `refresh` and `verify` continue to reach
   it through the existing single call each.
2. **Nothing is derived, defaulted or inferred.** `ExternalWork.strength` and a `rests-on` edge's
   `fraction` stay hand-authored. No refresh write-back, no annotation sync, no index field, no
   work-level scalar, no roll-up.
3. **Auditability of the `min`.** Every operand of a rendered `[= min(…)]` is a number a reader can
   find written on disk. The work's `strength` qualifies (its register entry's `- strength:` line);
   any computed blend of `strength` and `fraction` does not.
4. **Pending is the safe direction.** Every state short of a person having supplied both values —
   pending `fraction`, absent `fraction`, pending `strength`, a work id with no register entry —
   yields a pending claim derivation, never a gate of `1.0` and never a crash.
5. **Terminality is unchanged.** A `work-` node emits no edge and is not a node of
   `_dependency_order`. The cycle check is untouched.
6. **Claims only.** The gate lives in the claim branch. `_sup_solidity_from_deps` and
   `min_dependency_solidity` are not touched.

## 2. The rule

In `compute_solidity_full`'s per-claim loop, alongside the existing `relation == "depends"`
gathering, for each edge with `relation == "rests-on"`:

| `edge.fraction` | `work.strength` | Contribution |
|---|---|---|
| `0.0` | anything (incl. pending) | **none** — the pairing bears nothing on this claim, so it gates nothing |
| numeric `> 0` | numeric | append `strength` to `dep_finals` |
| numeric `> 0` | pending (`None`), or the work has no register entry | **pending** — set `pending = True`, break |
| `PENDING_FRACTION` | anything | **pending** |
| `None` (no `(applicability …)` on the bullet) | anything | **pending** — identical to `PENDING_FRACTION` |

Read `fraction` first: `f == 0.0` short-circuits before `strength` is consulted, because a work
judged irrelevant to this claim needs no judgement of its own.

**Justification against the `supports` analogue.** A `supports` edge is a *lift* into
`local_quality`, where `sup_solidity × fraction` discounts a contribution toward zero and a pending
fraction is *excluded* (never poisons). A `rests-on` edge is a *gate* into the `min`, where
`depends` is its analogue, not `supports` — and a `depends` target's pendingness poisons.
`rests-on` inherits `depends`' poisoning, and `fraction` does the only job a relevance weight can do
inside a `min`: decide membership.

**Consequences to state, not fix:**

- `strength 0.0` with a positive `fraction` gates the claim to `0.0`. This is the property the
  rejected `max` shape could not deliver — citing a work judged spurious must be able to hurt.
- The experimental `max` branch still rescues a claim whose derivation went pending through a work,
  exactly as it rescues one that went pending through a `depends` target. Do not special-case it.
- A claim with pending `confidence` is already skipped before the dep loop; a scored work does not
  make it scorable. Unchanged.
- The rendered trace needs **no format change**: work gate terms land in `dep_finals`, so `min_dep`
  and `render_solidity_trace` carry them for free. A reader auditing `[= min(0.85, 0.40)]` may have
  to look up `0.40` in the works register rather than on the bullet. That one hop is the price of
  invariant 2 — **do not** add a `(strength X)` annotation to the work bullet.

## 3. Change per module

### `kb_tools/kb_index_lib.py` — the whole behavioural change

- **`compute_solidity_full(claim_entries, experiments=(), supports=(), works=())`** — add the fourth
  parameter, defaulted to `()`. Build `strength_of = {w.id: w.strength for w in works}` once. Apply
  the §2 table inside the existing dep loop. Distinguish "work absent from `works`" from "work
  present with `strength is None`" only in intent, not in outcome — both are pending.
  (`_assert_work_node_coverage` fires later in the same run and names the real defect; refresh's
  `_refresh_solidity` runs before `_emit_jsonl_indexes`, so the computation must tolerate the
  dangling target rather than raise.)
- **`compute_solidity` / `compute_support_solidity`** — add the same `works=()` parameter and pass
  through. Uniform signature is cheaper than explaining an asymmetry.
- **`build_claims_records`** and the support-record builder — pass `state.works`.
- **Docstrings that now state the opposite and must be rewritten:** `compute_solidity_full`'s
  paragraph beginning *"**A `rests-on` edge enters nothing here, by construction.**"*;
  `DependsOnEdge`'s `"rests-on"` bullet (*"recorded, propagating nothing"*, *"there is no target
  solidity to gate on and none is manufactured"*); `ExternalWork` (*"It is not a solidity and does
  not enter one"*).
- **Do not touch** `_dependency_order`, `_sup_solidity_from_deps`, `min_dependency_solidity`,
  `render_solidity_trace`, `render_min_trace`, or any record builder's field set.

### `kb_tools/refresh_kb_metadata.py`

Pass `state.works`. Nothing else. The annotation sync is keyed on `"(solidity" in probe` plus a
claim-id token in the bullet head, so work bullets are untouched — verify this holds rather than
assuming it.

### `kb_tools/verify_kb_metadata.py`

Pass `state.works` at both call sites. Both loops that walk `entry.depends_on` already `continue` on
`target_kind != "claim"`; leave them. The per-edge integrity check for `rests-on` is unchanged.

### `kb_tools/kb_claimgraph/attribute.py`

`check_acyclic` calls `compute_solidity(_synthetic_entries(...))` over claim-to-claim pairs. The
`works=()` default keeps it correct. **No change** — and this is the property that makes the default
load-bearing rather than cosmetic.

### `kb_claimgraph/` (endcap, assemble, write), `kb_write/`, `kb_cmd/`, `kb_graph/`

**No change.** The build still authors both scores as `*pending*`. `kb_cmd` and `kb_graph` read
`.index/` and neither recomputes.

## 4. Contract-document changes

**`kb_tools/SPEC.md` — Claim-Graph Nodes and Edges:**

1. The edge-class table's `rests-on` row, contribution cell: currently `**none** — recorded, not
   propagated`. Replace with the gate statement.
2. The paragraph beginning *"An off-graph dependency is recorded, not propagated: a `rests-on` edge
   enters no solidity computation at all."* — wrong end to end, including its reasoning. Rewrite:
   the propagation is the point; a claim resting on unjudged outside work genuinely has unknown
   solidity, the contagion marks a judgement that is owed, and supplying the two values clears it.
   Keep and re-aim *"What the edge is for is telling a claim that depends on nothing from one whose
   warrant is outside reach"*. Record that a `max`/`strengthens` shape was rejected as directionally
   wrong.
3. The paragraph *"A zero on-point fraction is a value, not an absence"* — extend it to settle the
   `rests-on` case: zero applicability removes the pairing from the gate.

**`kb_tools/SPEC.md` — Derived Metadata, Defined:**

4. The `strength (external works)` bullet: strike *"enters no computation"*; keep *"not a solidity,
   sits on no build band"*. Add that it enters the dependency gate of every claim whose pairing with
   it is scored non-zero.
5. The `solidity` bullet: make the cone's reach explicit — it now ends at the corpus boundary rather
   than inside it.

**`kb_tools/ARCHITECTURE.md`:**

6. *"No number is authored anywhere"* — still literally true; add the operative consequence, that a
   build's `*pending*` applicability is now why a citing claim's solidity is pending, so the scoring
   pass owes two values per pairing.
7. The **Score the graph** paragraph: add what supplying the endcap's two values now does.

**Out of scope but contradicted — flag, do not edit.**
`templates/agents/kb-maintainer.md.tmpl` (*"It gates nothing, so recording it costs the claim no
solidity"*) and `templates/shared-chunks.toml` (*"that edge enters no solidity computation either —
a claim resting on outside work scores exactly as it would with no such edge"*). Both become false
on landing and need a follow-on change under the same authorization.

## 5. Blast radius, measured

**Today: zero claims change solidity, in every corpus that exists.**

| Corpus | Claims | `rests-on` edges | Works | Claims changing |
|---|---|---|---|---|
| seven-volume corpus | 26 | 1 | 1 | **0** |
| arXiv maths, 4 of 10 with built trees | — | 8 | 8 | **0** |
| hand-scored comparison KB (same seven volumes) | 26 | 0 | 0 | **0** |

Every `rests-on` edge carries `fraction: "*pending*"`, and every claim sourcing one already has
`confidence: null`. Pending claims are already omitted from the solidity map, so the new gate finds
nothing to change.

**This is the plan's sharpest risk, not a reassurance.** No corpus-level integration target
covers this change, and one would stay green whether the change is right, wrong, or absent. Unit
tests over synthetic entries are the only signal, and §7 treats them accordingly.

**If the endcap's trigger widens to prose citations:** the seven-volume corpus's tree carried 238
citation spans across 71 documents against a 121-entry bibliography; 14 of 26 claims sat in a citing
document.
Clearing the contagion then costs *(distinct works × one `strength`) + (pairings × one
`applicability`)* — of order a hundred hand-authored judgements before any claim in that corpus
carries a numeric solidity again. That is what the widening decision actually costs.

## 6. Acyclicity — confirmed against the code

`_dependency_order` builds `nodes = set(entries) | set(sups)`; works are in neither. `add_edge`
guards on both ends being in `nodes`, so a `rests-on` edge adds no ordering constraint.
`ExternalWork` emits no edges and `parse_work_entries` reads no `- depends-on:` list from a work
entry by construction. **A work cannot appear on a cycle, and the cycle check needs no change.**
`test_an_off_graph_edge_closes_no_cycle_and_orders_nothing` already pins this and survives verbatim.

## 7. Acceptance criteria

1. `compute_solidity_full` is the only place a work's `strength` reaches a solidity.
2. A claim with numeric `confidence` and a `rests-on` edge whose `fraction` is pending or absent, or
   whose work's `strength` is pending or has no register entry, has **pending** solidity.
3. A claim with a `rests-on` edge at `fraction 0.0` scores **identically to the same claim with no
   such edge**, whatever the work's `strength`.
4. A claim gated by a work at `strength 0.0` and positive applicability carries solidity `0.0`.
5. `refresh` remains idempotent and `verify` green over a KB carrying scored works; `refresh` writes
   no byte on a work bullet or entry.
6. `refresh` does not raise on a `rests-on` edge naming a work with no register entry; the run
   reaches `_emit_jsonl_indexes` and fails there with `ExternalWorkParseError` naming the key.
7. Every operand of the rendered trace is readable off disk. No operand is a computed blend.
8. No new field in any `.index/*.jsonl` record shape, no new derived line in any register.
9. Acyclicity behaviour byte-for-byte unchanged.

**The test that catches this going wrong**, replacing `TestAnOffGraphDependencyIsRecordedNotPropagated`:
a table-driven case over one claim at `confidence 0.8` with one `rests-on` edge, varying
`(fraction, strength)`: `(0.0, *) → 0.8`; `(pending, 0.9) → None`; `(None, 0.9) → None`;
`(1.0, pending) → None`; `(1.0, 0.4) → 0.4`; `(0.3, 0.4) → 0.4`; `(1.0, 0.0) → 0.0`. Plus a work id
absent from `works` → `None`; a `rests-on` gate beside a weaker `depends` gate → the `depends` value;
and a pending-through-work claim with a run experiment at `0.9` → `0.9`.

## 8. Traps

1. **The `supports` row is the wrong analogue** (§0). Expect a reviewer to propose `strength ×
   fraction`; the answer is the `fraction 0.0` case.
2. **`fraction` has three falsy-adjacent states** — `None`, `PENDING_FRACTION`, `0.0`. `if not
   edge.fraction` collapses all three. Compare explicitly, and read `fraction` before `strength`.
3. **`strength` pending and `strength` absent are the same `None`.** No disambiguation exists and
   none is needed — but do not write code assuming one does.
4. **Refresh runs the computation before the work-coverage guard.** The computation must survive a
   dangling work target. Do not add a raise inside `compute_solidity_full`.
5. **`min_dependency_solidity` is a second implementation of the dep-gate min**, for the support
   trace only. Leaving it alone is correct; do not "fix" it by adding work handling.
6. **`test_a_support_resting_on_outside_work_scores_as_if_it_did_not`** stays, but its docstring must
   be rewritten: the support branch is inert because a `rests-on` edge whose source is not a claim is
   *illegal*, not merely unhandled.
7. **`works=()` is load-bearing, not cosmetic.** `attribute.check_acyclic` relies on it.
8. **Contradicting agent-facing text is already committed.** See §4.
