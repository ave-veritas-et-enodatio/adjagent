# Active Plan — an index's body is never source

**Paths.** Every path is `kb_tools/`-relative unless it begins with a directory. `SPEC.md`,
`ARCHITECTURE.md` and `CONVENTIONS.md` exist at both the repository root and under `kb_tools/`,
and they are different documents; every unqualified citation here means the `kb_tools/` copy.

**Vocabulary.** An **index** is a document that has children — SPEC's own word for it, from the
topography list at point 1. SPEC reserves *container* for a different thing entirely, a leaf
hosting claim-graph nodes (*"a leaf is a **container** hosting any combination of node"*), so no
row here uses that word for a document with children.

**Seats.** **PC** python-coder. **TW** tech-writer. Contract-chain documents take TW; see
standing rule 4.

**In a row's `What` column only**, bold marks a decision that row must make and record; a row with
no bold in its `What` has no open choice in it. Bold elsewhere is ordinary emphasis.

**What travels with a dispatched row**: this header, the whole RULED section, the standing rules,
the row, and the surface-table entries the row names. Not the narrative section, and not Order.

## What produced this

`kb_docgraph/outline.py`'s `build_tree` gives an index the source text its section owns. Two
sites, one shape:

- The volume root's segment is `"# {title}"`, `frontmatter.abstract` and `preamble` joined —
  so a paper's abstract lands in the domain `index.md`.
- Each section node's segment runs from its heading to the next heading at any level, so a
  section's own prose up to its first subsection stays with the section. A section that then
  acquires children keeps it.

The downstream consequence is the `kind:` vocabulary. `kb_claimgraph/assemble.py` computes
`hosts_claim=bool(positions)` and `tree.document_kind` returns `leaf-as-index` for an index
whose body hosts a claim — which is the construct this tree manufactures. No inference is
involved: the positions come from the declared claim graph, so the value arrives deterministically
on every build. An index that hosts claims is not a category; it is this bug's signature.

The distinction the fix turns on: prose in an index is correct, source in an index is not.
A summary index is *supposed* to carry prose — SPEC has a summary lead with Key Results, and
`tests/fixtures/mini-kb`'s two indexes carry exactly that, authored summary and no source.

## RULED — the four decisions this plan executes

Settled. A row that reopens one has misread the plan.

1. **A document that has children carries no source of its own.** The prose a section owns becomes
   a child leaf of that section; the index keeps its up-link, its child list, and whatever
   summary a later stage writes. This is the fix, and it covers both sites — the volume root's
   abstract-and-preamble and every section's own preamble.

2. **The relocation is a relocation and never an elision.** Every word stays in the tree, on both
   sides of `partition.py`'s checks, exactly as `split_bibliography` already does it. A row that
   drops a preamble, truncates one, or summarises one has substituted a different change.

3. **`leaf-as-index` is retired, and after decision 1 it is dead rather than disfavoured.** With no
   index carrying source, `hosts_claim` is False at every call, so the value is unreachable and
   `document_kind` loses the parameter. The vocabulary becomes `entry-point` / `index` / `leaf`.
   Nothing here changes which documents may declare claims: the kind gate on `kb_index_lib`'s
   parsers is correct once indexes hold no source, and a row widening it has misread the plan.

4. **The verbatim doctrine retires.** *"Leaves are verbatim, never editorially altered"* was a
   fidelity guard for when a model transcribed leaf content. Leaves are mechanically converted now
   and a maintainer edits them after the build by design. **Scope: post-build editing only** — a
   site instructing a post-build editor goes, a site stating the build-time guarantee stays. SPEC's
   *Leaf Bodies Are Derived* draws that line already. Independent of decisions 1–3.

   **The pin returns no answer for `verify_citations.py`'s two sites**, which justify a gate's
   reach rather than instruct an editor or state a guarantee. B2 decides those on their own terms
   and does not force them into either bucket.

## Standing rules — on every row, not restated per row

1. Code citations are by symbol, never by line; the surface table was verified at `3d29018`. A row
   re-greps every symbol it is about to change and reports any that no longer resolves rather than
   improvising a referent.
2. **No row introduces a content predicate.** Decision 1 is structural — *does this node have
   children* — and never *does this body look like source*. A row testing a body has rebuilt the
   defect one layer up.
3. **Evidence is a fresh mechanical build**, and it is cheap: `just no-inference-kb-driver-arxiv-paper <id>`
   from `kb-testing/`, about forty seconds, no model calls. A row reports before and after on
   `2609.00183v1` plus one other staged id.
4. **Contract-doc routing.** `SPEC.md`, `ARCHITECTURE.md` and `installed/*.tmpl` are contract-chain
   edits and take the TW seat. So does `templates/shared-chunks.toml` and every
   `templates/agents/*.md.tmpl`: they are model-facing definition text. Per the root
   `CONVENTIONS.md`'s chunk single-sourcing rule the chunk is the edit site — never a rendered
   definition.
5. Bannered files (`# !GENERATED!`) are never hand-edited: change the template, re-render.
6. **`just check` diffs `rendered/` — a baseline from a prior `just generate` — against what the
   templates render now, and the report is the evidence, never the exit code.** After a template or
   chunk edit it exits non-zero by construction; exit 0 is reachable only by regenerating first,
   which overwrites the baseline and makes the check vacuous. A row names the definitions it
   expects to see `DRIFT` on, confirms those and no others, and regenerates only afterwards.
7. Root `just test` green at handoff. Scratch under `.claude-temp/`. Never `/tmp`. Do not commit.
8. `kb-testing/test-data/transient/` is not hand-edited. The staging and build recipes write there;
   a row reaches it through a target and never with an editor.

## The surface

| Where | What it is | Row |
|---|---|---|
| `kb_docgraph/outline.py` — `build_tree` | the two segment compositions: the volume root's `title`/`abstract`/`preamble` join, and the per-section `lines[heading.end:stop]` slice | B1 |
| `kb_docgraph/outline.py` — `_references_document` | the precedent: a synthesized child `Document` appended to `index.children`, outside `ordered`, with its labels moved to it | B1 |
| `kb_docgraph/partition.py` — `check_markdown_against_tree` | decision 2's instrument. `volume = Counter(tree.content_tokens)` is the left side and `content_tokens` is `_supplied_titles` + abstract + body; `assigned` is every node's `segment`. The module docstring states the exemption a row depends on: *"a document's body and its up-link and child list are different fields, and only the body is compared"* | B1 |
| `kb_docgraph/outline.py` — `_place_children`, `_distinct`, `_reserved` | a child with children becomes `index.md`, one without becomes `<slug>.md`; slug collisions already take an ordinal and reserved names are already refused | B1 |
| `kb_docgraph/outline.py` — `ordered`, `_depth_of`, the `DepthError` check | `ordered` is the zip against the AST's headers. A synthesized node answers to no header and is outside it, as `references` is | B1 |
| `SPEC.md` — *The Skeleton Is Derived* | the sentence decision 1 falsifies: *"a source section is one KB document, a section holding no subsections is a leaf."* After B1 a section holding both prose and subsections is an index **plus** a leaf | B1 |
| `SPEC.md` — the Document-Tree Contract | its statement of what an index holds; point 8's closed elision list (`APPARATUS_KEYS` plus the title-page marker), which decision 2 forbids widening | B1 |
| `ARCHITECTURE.md` — the `outline.py` row, *The Derived Skeleton* | its account of how the tree is cut | B1 |
| `kb_claimgraph/tree.py` — `document_kind`, `KIND_LEAF_AS_INDEX`, `DECLARING_KINDS` | the decision site; `hosts_claim` is the parameter decision 3 removes, and `DECLARING_KINDS` collapses to a one-element set when the value goes | B2 |
| `kb_claimgraph/assemble.py` | the one caller passing `hosts_claim=bool(positions)` | B2 |
| `kb_claimgraph/identify.py` — `unmarked_documents` | calls `document_kind` with `hosts_claim=False` already. Its docstring opens *"C-inf's whole surface"*, which is false — it has one consumer, a `stage-C-identify` report line in `build.py`, and C-inf's ask surface is `conform.pass_two_gate` | B2 |
| `kb_index_lib.py` — `parse_leaf`, `parse_experiment_leaf`, `parse_support_leaf` | three `("leaf", "leaf-as-index")` literals, plus four docstrings naming the value | B2 |
| `verify_kb_metadata.py` (**two** literals), `verify_citations.py` — `LEAF_KINDS`, `kb_readme.py` — `_LEAF_KINDS`, `kb_cmd/index.py` — `_LEAF_KINDS` | five more leaf-kind literals | B2 |
| `kb_write/values.py` — `DOCUMENT_KINDS`, `check_kind` | the authored-value vocabulary; `check_kind` refuses anything outside it, which is why a stale tree cannot be re-stamped | B2, B4 |
| `kb_survey/skeleton.py` — `NodeKind` | a second, independent `StrEnum` of the vocabulary, already three-valued. Nothing to change and nothing checks the correspondence — decision 3 is what makes the two agree | B2 |
| `verify_citations.py` — `in_scope_text`, the module docstring's SCOPE paragraph | the body-skip justified by verbatim-ness **in code**. A third `verbatim` in this file, in the `"excerpt"` finding's message, is about an excerpt matching its section and is not a site | B2 |
| `SPEC.md` | the kind-vocabulary line and the structural-position clause — one sentence, *What a KB Is*, topography point 1 | B2 |
| `ARCHITECTURE.md` | the `tree.py` row's `kind:`-derivation sentence; the `graph_init_kb` note's parenthetical `(kind: leaf / leaf-as-index)`. That note's argument — every metadata check passes vacuously because nothing classifies as a leaf on a frontmatter-less tree — survives decision 3 intact, so striking the value is sufficient and no rewrite is required | B2 |
| `installed/README.md.tmpl` | the `leaf-as-index` rule sentence under *What Is Here*, and the per-document-label promise | B2 |
| `templates/agents/kb-maintainer.md.tmpl` | states the `kind:` vocabulary inline — the only tracked retirement site outside `kb_tools/` | B2 |
| fixtures | `tests/fixtures/writeapi-render-golden/frontmatter-hosts.md` opens `kind: leaf-as-index` and is byte-exact; `tests/test_kb_readme.py`, `tests/test_kb_claimgraph.py` and `tests/test_kb_write_render.py` assert on the value by name; `tests/test_kb_write_values.py` couples `DOCUMENT_KINDS` to `verify_citations.LEAF_KINDS` and passes unchanged after retirement, so its survival is not evidence | B2, B4 |
| `SPEC.md` | the verbatim doctrine in four passages: *"Leaves are verbatim"* under *What a KB Is*; the *Leaf Bodies Are Derived* guarantee sentence; the leaf-bodies-exempt clause under *Citations*; the *"Verbatim leaves + canonical direction"* invariants bullet | B3 |
| `installed/CONVENTIONS.md.tmpl` | the whole `## Leaves are verbatim` section — heading and one paragraph | B3 |
| `installed/CLAUDE.md.tmpl` | one clause: *"leaf prose is verbatim source that nobody paraphrases"* | B3 |
| `templates/shared-chunks.toml` chunk `kb-orientation` | one clause in the Topography-graph bullet. **That sentence carries the summary rule too** — *"a summary … leads with Key Results drawn verbatim from below"* — so an unpinned edit strips both; only the leaf clause is in scope. It is the file's only leaf-verbatim site; its other `verbatim` lines are the MAD/liaison relay rule and sweep commentary, all unrelated | B3 |

## Rows

| Row | What | Who | Blocked on | Done when |
|---|---|---|---|---|
| **B1** | A node that has children emits its own segment as a child leaf, in `build_tree`. Both sites: the volume root's abstract-and-preamble and every section's preamble. `_references_document` is the shape to follow — a synthesized `Document` appended to the parent's `children`. **The child's `segment` carries the index's former segment byte-for-byte, heading line included, and `_supplied_titles` is not touched at either site**: that list feeds `content_tokens`, the left side of check B, so adding a constant to it without putting that constant into a segment reports words dropped by the split. The child's `title` field is free — it reaches only the parent's child list and the child's up-link, neither of which check B compares. **This row decides and records: (a) the child's `title`**, a constant or the section's own heading text; **(b) its position in the child list** — `references` is last because last in document order, and a preamble precedes its subsections; **(c) whether a node whose segment is empty after the heading emits nothing**, the answer `_references_document` already gives for an empty bibliography | PC · **TW for `SPEC.md`, `ARCHITECTURE.md`** | — | Fresh mechanical builds per standing rule 3, before and after, on `2609.00183v1` and one other staged id. Every index's body is empty and no document is stamped `leaf-as-index`. The before state on `2609.00183v1` is 24 documents with four of five indexes carrying source — `introduction/index.md` 134 lines and one child, the domain index 13 (the abstract), `splitting-and-singular-strata/index.md` 9, `preliminaries/index.md` 5, and one index at 0 whose prose all sits in its subsections; the after state re-measures that table. `partition.py`'s checks pass with word counts unchanged on both sides — a changed count is an elision, not a relocation. A `\ref` into a relocated preamble resolves to the document that now holds it, demonstrated on a paper that has one. **Also captures the corpus-wide document and index counts before the change** (`just stage-arxiv-corpus`, then `just no-inference-kb-driver-arxiv-corpus`, from `kb-testing/`) and records them in the handoff: that recipe `rm -rf`s its output tree on every invocation, so B4's baseline is this measurement and cannot be retaken. SPEC's *The Skeleton Is Derived* and the Document-Tree Contract state the index/leaf split as implemented, with the changed sentence quoted in the report. Root `just test` green |
| **B2** | Retire `leaf-as-index`. `document_kind` loses `hosts_claim` and `KIND_LEAF_AS_INDEX`, and `DECLARING_KINDS` collapses to one element; `assemble.py` stops computing the argument; the nine leaf-kind literals — `kb_index_lib` (three), `verify_kb_metadata` (two), `verify_citations`, `kb_readme`, `kb_cmd/index`, `kb_write/values` — collapse to the three-value vocabulary. Docstrings naming the value go with it, including `unmarked_documents`' false opening line. The `kb_survey.skeleton.NodeKind` correspondence is stated, not enforced — a row adding a cross-check has widened this one. Also takes `verify_citations.py`'s two verbatim sites, which sit in this row's files and rest on B1's outcome: **decide and state whether the citation gate's scope changes or the body-skip keeps a different justification.** After B1 no index carries source, so the skip predicate stays positional and standing rule 2 leaves no body test available | PC · **TW for `SPEC.md`, `ARCHITECTURE.md`, `installed/README.md.tmpl`, `templates/agents/kb-maintainer.md.tmpl`** | B1 | `git grep -n leaf-as-index` over the tree returns this plan and nothing else. The golden fixture `frontmatter-hosts.md` is regenerated through the write API, never hand-edited. The three tests asserting the value by name are re-pointed or retired deliberately, each with its reason. Per standing rule 6, the `DRIFT` list from `just check` against the pre-edit baseline names the definitions the `kb-maintainer` template reaches and no others. If the citation gate's scope changed, the before/after count of bodies scanned. Root `just test` green |
| **B3** | The verbatim doctrine retires (decision 4). **Seven determinations across four files, and this row decides each against RULED 4's scope pin** — a site instructing a post-build editor goes, a site stating the build-time guarantee stays: `SPEC.md`'s four passages, `installed/CONVENTIONS.md.tmpl`'s whole `## Leaves are verbatim` section, `installed/CLAUDE.md.tmpl`'s one clause, and the `kb-orientation` chunk, whose two consumers are both post-build seats. The code sites are B2's | TW | — (independent of B1–B2; shares `SPEC.md` with both, so never dispatched concurrently with either) | Per determination, its outcome and reason, one line each. Then, before and after, from the repository root: `git grep -c -e 'Leaves are verbatim' -e 'never editorially altered' -e 'leaf prose is verbatim' -e 'BODIES are exempt' -e 'verbatim translation of the source' -e 'leaf bodies are exempt' -e 'Verbatim leaves' -e 'makes the verbatim property'`. At `3d29018` the four files in this row's scope report `SPEC.md` 4, `installed/CONVENTIONS.md.tmpl` 2, `installed/CLAUDE.md.tmpl` 1, `templates/shared-chunks.toml` 1 — the last two patterns are what reach SPEC's *Leaf Bodies Are Derived* sentence and its invariants bullet, which the other six miss. **This plan is an expected residual and is not a site**, as are `verify_citations.py`'s two, which are B2's. `rendered/` is gitignored, so that the renders followed is shown by standing rule 6's `DRIFT` list naming the `kb-orientation` consumers and no others. State what a consuming KB's `CONVENTIONS.md` and `CLAUDE.md` lose |
| **B4** | Every tree built before B1 carries the defect and is rebuilt. Not migrated, not tolerated: `kb_write.values.check_kind` refuses the retired value after B2, so no write op can touch such a tree again, and its claim graph attributes claims to indexes. Restage and rebuild the staged corpus with `just stage-arxiv-corpus` then `just no-inference-kb-driver-arxiv-corpus`, from `kb-testing/` | PC | B1, B2 | Document and index counts over the staged corpus, against the baseline B1's handoff recorded — that recipe `rm -rf`s its output tree on every invocation, so the before figure comes from B1's report and is not re-derivable here. `CLAIM_GRAPH_LAYOUT_PLAN.md`'s measured baseline is invalidated by this row — the 15,034px sheet was measured over a tree that attributed claims to indexes — so this row states that in one line in that plan and does not re-measure it. `tests/fixtures/mini-kb` is untouched: its indexes carry authored summary and no source, so the golden does not move |

## Out of scope

**What a claim is, how claims are discovered, and how the claim graph is built.** B1 changes which
document a claim is attributed to and nothing about the attribution rule.

**The `kind:` field itself.** It stays: the per-file parsers read one file at a time with no tree
in hand, so the field is how a document declares its role without its context, and
`refresh_kb_metadata` discovers roll-up targets by kind and never by filename deliberately. Only
one value leaves the vocabulary.

**Re-measuring the claim-graph sheet.** B4 marks the layout plan's baseline invalid; re-taking it
belongs to that plan's own rows, where the numbers are graded.

## Order

**B1 first** — it is the fix, and every other row is downstream of it. **B2 second**, because the
value is dead only once B1 lands, and its `verify_citations.py` decision reads a post-B1 tree.
**B3 any time**: it needs neither outcome, but it shares `SPEC.md` with both, so it is never
dispatched concurrently with either. **B4 last**, needing B1's shape, B2's vocabulary, and the
baseline B1 recorded.
