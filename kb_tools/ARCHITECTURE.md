# ARCHITECTURE – kb_tools

How the toolchain and the agent set that drives it satisfy SPEC.md: the
module layout, the CLI's op set, the build pipeline's stage table and
ledger, and the query surface. Cites SPEC's requirements rather than
restating them.

## Module Inventory

Single-source modules (the anti-drift discipline — one definition, many
consumers):

| Module | Role |
|---|---|
| `kb_util.py` | Repo/KB path construction (the consuming repo's root is discovered by walking up from the cwd to `.git` with `kb-root/` beside it — never from `__file__`, which points wherever this toolchain happens to have been installed rather than at the repo being worked on; the KB directory name is single-sourced here, not hardwired across tools) **and** maintenance-command hints (`just`/`make` `kb-refresh`/`kb-verify`, detected from the root's runner file) for remediation output + tests. Also the build front-end CLI, one subcommand per op — `preflight` / `graph-init` / `install-targets` / `uninstall-targets` / `show-confirmation` / `show-status` / `show-stage-status` / `start-build` / `advance-step` / `show-run-lock` / `validate-build` / `insert-claim-entry` / `insert-support-entry` / `insert-experiment-entry` / `insert-work-entry` / `set-work-strength` / `set-applicability` / `set-rigor` / `set-rationale` / `add-depends-on` / `set-frontmatter` / `mark-claim-in-leaf` / `set-on-point-fraction` / `render-citation`; see Runner Targets and the Build Ledger, below, and The Write API (`kb_write/`), below. One CLI by ruling: the validator, the reading verbs, the build-opening read, every member of `kb_write.ops.OPS` and the one read-only metadata op in `READ_OPS` are members of this op set, not second entry points. Each subcommand declares its own options, so a companion belonging to one op cannot be spelled on another. `show-run-lock` reports the driver's run lock — live, stale or absent, with the holder named — for callers outside `kb_driver/`; the judgement is `runlog.lock_state`'s and the op restates none of it, writes nothing, clears nothing, and answers on stdout rather than through its exit status. (`survey-sources`, `validate-skeleton` and `render-manifest` fronted the deleted LaTeX-reading survey and are gone with it — kb_survey, Retained, above.) |
| `kb_pipeline.py` | The build pipeline's state machine: the ordered stage table (the single source of the stage vocabulary), the git-commit-trail ledger, the checklist render, and the build's opening gate. Driven through `kb_util`'s ledger ops (`show-status` / `show-stage-status` / `start-build` / `advance-step`) and the read that stands in front of the opening gate (`show-confirmation`). |
| `kb_schema.py` | The build-band ladder, the `clm`/`exp`/`sup` id grammar (`[a-z0-9]{6}` body), the collision-checked id minter, and the one id grammar that is **not** minted — `work-` plus a citation key, derived by `work_id` from the key rather than drawn, which is why `work` sits outside `ID_KINDS` (every consumer of that tuple is asking about minting). Also the **node-kind vocabulary**, `NODE_KINDS` — every value a `node_type` takes, which answers the other question: what exists, rather than what gets minted. A census, a breakdown, a union or an ordering iterates it; a site that *branches* per kind keeps naming kinds and wraps its table in `kind_table`, which refuses one that is not total over the vocabulary at the moment it is defined. `FRAMEWORK_KINDS` is the bedrock subset and `node_kind_plural` the one statement of a kind's report label. The definition of each vocabulary, not a copy of one. |
| `kb_links.py` | Low-level Markdown link-scanning primitives (code-span neutralization, inline-link regex, file crawl, skip-dir rules) shared by the link checker and the query CLI's reverse-find. Primitives only; classification/gating stays in the checker. `blank_fenced_lines` is the toolchain's one fence scanner, and it reads a fence the way SPEC.md point 9 spells one: any indentation, **any depth of blockquote marker**, a close on the same character at least as long, carrying nothing after it but emphasis markers. Each condition is a measured failure, and the last two are the same document twice — a display-maths fence inside a labelled blockquote (point 12) that no column-anchored scanner opens, so `[T_{P,Q}g](z)` is read as an inline link to a file named `z`, and that same fence's close written `` ```* `` because the emphasised theorem statement around it terminates on the delimiter's own line. A fence opened inside a blockquote closes with the blockquote — fenced content takes no lazy continuation, so an unprefixed line is outside both — which is what bounds an unclosed quoted fence to its block instead of blanking the file to its end and reporting the silence as a clean scan. `strip_code` additionally blanks **inline** maths, which the gfm writer spells `` $`…`$ `` and hard-wraps mid-span, so a continuation line's `\bigl[…\bigr](\xi)` is not read as a link to a file named `\xi`; the `$` required at both ends is what makes a multi-line blank safe where a bare code span is not — an odd backtick in prose cannot open one and take real broken links with it. |
| `pandoc.py` | The one module that knows how LaTeX is read: `to_ast` / `to_markdown` / `from_ast` / `version`, and nothing else in the package names the `pandoc` binary or builds an argv for it. It owns one three-way decision: `--citeproc` and one `--bibliography` per file where any bibliography stands, and `CITATION_KEYS_ONLY_FLAG` where none does — one condition, three flags, so a caller cannot set two and land back in silent deletion. `bibliographies` is a **set of files and not a choice among them**: pandoc takes the flag repeatedly and merges what it is given, and citeproc renders only the entries a source cites, so an uncited extra is inert. The order is the caller's and is preserved, a key two files define resolving to the first. Filters precede `--citeproc` in the argv and pandoc applies them in order, which is what lets the filter mark a citation before citeproc resolves it (measured against pandoc 3.11, not read off the manual). The single enumerated exception to SPEC.md's stdlib-only invariant, and a different kind of one than the vendored parser it replaced — a system binary this module assumes present rather than a copy the repository ships (SPEC.md, Corpus Invariants). `PandocMissingError` is what a caller gets instead of a bare `FileNotFoundError`. `PandocBibliographyError` is the second named failure and the only one a caller may continue past — a `--bibliography` the reader could not parse, discriminated on pandoc's own exit code (25, against an unparseable source's 64) rather than on message text, so widening the recovery to a real failure takes an exit code and not a reworded string; the code says *a* file failed and not which, so pandoc's own complaint is the only thing naming it. `PandocIncludeError` is the third, and the one failure raised at **exit 0**: an `\input` target the reader could not load takes its file's content out of the parse entirely, so it reaches neither side of either partition check and a silent run produces a paper without its body (SPEC.md point 8, the known hole). It is matched on pandoc's own sentence because no exit code and no artifact records it — the stderr line is the only evidence there is — and `_UNLOADABLE_INCLUDE_RE` is narrowed to that sentence plus a source position of its own shape, the position's filename optional because this module's sources arrive on stdin and pandoc names no file for one. It carries every unloaded target with the line that named it. See The Document Graph (`kb_docgraph/`), below. |

Pipeline scripts:

| Script | Role |
|---|---|
| `kb_index_lib.py` | Core library: leaf/register parsing + the shared `compute_solidity` / subtree-aggregate / leaf-reference computations. Also **the** KB document walk (`kb_files` / `document_texts`) and `UNSCANNED_REASON`, both here because `kb_pipeline` and `kb_claimgraph` each need them and may not import each other (What the Stage Table Declares, below). Both refresh and verify call these so the two can never dual-compute and drift. Also `scan_authored_ids(kb_root) -> {id: IdRecord(kind, register_path, hosting_leaf)}` — the authored-id inventory, read from registers and leaf frontmatter and **never** from `.index/`, so it is correct on a KB whose derived index is empty or stale — and `anchor_section(text, anchor)`, the code-stripped body of the section an anchor names, which the citation gate and the citation composer both read a cited document through. |
| `refresh_kb_metadata.py` | The refresh target — regenerates derived metadata (solidity write-backs, subtree aggregates, leaf-reference footers), the `.index/*.jsonl`, and then the claim-graph sheet from the index it just wrote (The Claim-Graph Sheet, below). Idempotent: same canonical state → byte-identical output, and always a full rebuild rather than an incremental one — the freshness gate is this same rebuild run in dry-run and diffed against disk, so an incremental path would leave verify rebuilding by a route refresh does not take (future intent: see ROADMAP.md). |
| `verify_kb_metadata.py` | Claim-graph integrity gate — frontmatter coverage, id uniqueness, orphan/referential integrity, bidirectional coverage, acyclicity, and freshness (runs the build in dry-run, diffs against on-disk; any diff = stale = hard fail). The claim-graph sheet joins that freshness gate on the same terms: `check_claim_graph_fresh` re-composes the sheet the on-disk `.index/` renders and byte-compares it, absent and hand-edited being one drift. |
| `verify_md_links.py` | Repo-wide dead-link gate — **every** `.md` file must have zero dead links. |
| `verify_citations.py` | Citation-grammar gate over authored files (grammar in SPEC.md, Citation Grammar): channel exclusivity, referent existence, excerpt match + length bound, durable targets, and edge-backed foreign-domain references. Leaf bodies exempt; `.index/` out of scope. |
| `kb_cmd/` | Read-side query package over `.index/` (`index.py` loads JSONL into dataclasses and exposes question-shaped lookups; `cli.py` is the shell wrapper). See Query Surface, below. |
| `kb_graph/` | The claim-graph SVG renderer, in four staged modules: `model.py` assembles the node table, the drawn edge set — deduped, then a `references` edge dropped wherever the edges left standing already reach its target, a greedy transitive reduction that never touches `depends` or `rests-on` — the defects decidable without geometry and each node's domain attribution, reading `.index/` only through `kb_cmd.index`'s dataclasses and taking `depends-on.jsonl` as its sole edge source; `layout.py` places geometry — layering, in-layer ordering, coordinate assignment, edge routing, and hop detection with hop-side selection — in exact integer arithmetic. **A node of degree zero over the corpus graph is not layered at all**: longest-path layering has nothing to ask of a node with no premises and no dependents, so every one of them landed on layer 0 and a KB built without the pass that authors edges drew as a single row a quarter of a metre wide. Such a node leaves the hierarchy and is drawn in a grid block below the baseline, `style.ORPHAN_ROW` to a row, in the same ascending-id key a layer's occupants start from — and degree is the corpus's, never the drawn sheet's, or a node whose one edge leaves a selected domain would be an orphan on that sheet and a premise on the full one. An edge spanning N layers routes as a chain of N−1 dummies, one per intervening layer, each taking a real column of its own layer on the in-layer ordering key, and draws as N segments of one polyline; `PlacedEdge` carries `points` with `start`, `end` and `segments` as derived properties, and crossing detection runs pairwise over segments rather than whole edges, since two chains can cross more than once. The `viewBox` unions every drawn box with every stroke vertex, because a chain can bend to the right of the widest box in its own layer. In-layer order is no longer id-ascending alone: crossing reduction is an adopted phase of the algorithm, an iterated sweep over the dummy-bearing layers. **A column index is no longer a coordinate either**: coordinate assignment is an adopted phase too, by the priority method — each occupant pulled toward the barycentre of its neighbours in the adjacent layer, a dummy outranking every real node so a long edge's chain straightens, and no occupant passing its neighbours in the order the sweep chose or closing the gutter between two boxes. Both phases' heuristics and parameters are recorded in this module's own docstring rather than here; a hop's bulge is not, being the emitter's, which orders the arc's two endpoints by screen x so the bridge reads the same way on an edge the graph drew right to left. Geometry stays a pure function of the graph's own records, independent of the order they were loaded in; **A `references` stroke that reaches layout at all is drawn distinctly from a dependency, and it is the one class using the weight channel** — the ones a remaining path already implies never get this far, dropped by `model.py`'s reduction before geometry runs. It asserts nothing about strength, so it takes no rung of the ramp and not the pending neutral either — that neutral means *on the ladder, unscored*, which a pending `depends` edge is and this is not — but `style.REFERENCE_EDGE_COLOUR`, off the ladder for `FRAMEWORK_FILL`'s reason read at the other end, at `style.REFERENCE_STROKE_WIDTH`, half the weight of every other class. Two channels for one fact, deliberately: the colour is what reads at a glance and the weight is the half that survives greyscale and a screenshot in a review. It also contributes **no premise pair** (`layout.NON_PREMISE_RELATIONS`), so it orders no layer and joins no cycle — a mutually-referencing pair draws as two ordinary strokes rather than as two back edges, which is the drawn form of the class carrying no acyclicity constraint. `style.py` holds every presentation constant and gates nothing; `svg.py` is the only module composing markup, built as an element tree and serialized by it. Nothing in the package refuses: a defective graph is the picture's subject, so a cycle, a ghost id or a `relation` conflict comes back as data for the drawing and the report lines, and no property of the graph — not a cycle, not zero edges, not zero nodes — is a precondition. `ops.py` is the op: `render` writes one sheet and returns its exit code, report and path; `compose` stops a step short at the markup, which is what lets `verify` ask what the index would render without writing anything. Fronted by `kb_util`'s `render-claim-graph` subcommand, and called by `refresh` and `verify` (The Claim-Graph Sheet, below). |
| `kb_write/` | The metadata write API: the ops every authored metadata byte reaches a KB file through — values in, canonical bytes out, each write proven by readback before it lands. Contract in SPEC.md, The Write API's Contract; module roles below. |
| `kb_docgraph/` | LaTeX volumes in, the KB's navigable Markdown tree out — one pandoc parse per volume root, zipped against that parse's own whole-volume rendering, split into documents, written and gated. The current front end for everything SPEC.md's Topography graph guarantees. See The Document Graph (`kb_docgraph/`), below. |
| `kb_claimgraph/` | The KB's navigable Markdown tree in, the claim-graph metadata spine authored into it — the conformance gate, the claim-site inventory, claim identification, dependency attribution, assembly, the write passes and the refresh/verify gate, over two passes: the declared graph mechanically, then the discovered graph's edges additively over it. Reads the tree as its **sole input** and writes every metadata byte through `kb_write`. See The Claim Graph (`kb_claimgraph/`), below. |
| `kb_survey/` | Three stdlib-only modules retained from the toolchain's pre-pandoc LaTeX reader: the manifest schema, a second (unused-by-`kb_docgraph`) skeleton derivation, and the tree validator `kb_docgraph` itself calls. Nothing here reads LaTeX any more. See kb_survey, Retained, below. |
| `kb_driver/` | The build sequencer — walks a step table itself rather than briefing an agent to walk it. It sequences a KB build end to end: the head, where the two front ends below produce the tree and the claim graph over it, and the tail, where that product is validated, reviewed and documented. Contract in SPEC.md, The Driver's Contract; module roles below. |
| `inference/` | One headless `claude` call, for any tool here that wants one: spawn, stream capture, the two bounds, the process-group kill, and the **one** classification of how a call ended (`Outcome`). Both callers read that classification through it — `kb_driver`, which spawns a seat per step, and `kb_claimgraph.ask`, which asks one a question — so a defect fixed in how a call is graded is fixed for both at once, which two implementations of it were not: `SPAWN_FAILURE` and `RESULT_ERROR` existed on the driver's side alone, and on the other a missing binary read as a defect in the tool and a call the CLI ended at exit 0 while marking the result errored was graded `OK` with its error text validated as the answer. **What is here is what both callers share, and no more**: retry, a re-ask, where a bound's value comes from, one-call-at-a-time, and boundary checks carrying an application's exit codes are the caller's, because a tool asking a seat something is not a build and has no barriers, no ledger and no cards. A second `init` is the same line drawn through one fact — the count is here, what it *means* is `call.py`'s, since one call is one turn for the driver's step model and for nothing else. Two entry points one level apart: `invoke`, argv and prompt file in, the whole `CallResult` back; `call_claude` / `seat.ask_seat`, named parameters in, the response text and the outcome back. The prompt crosses the `Invoker` seam as a **file**, which is what lets a substituted invoker tell one call from the next by what the caller composed — `replay.py`'s scenarios answer per step off the brief's own filename. Stdlib only, and it configures no logging handler: a run attaches its own to `inference.LOGGER_NAME` (`runlog.configure`). |
| `installed/` | Artifacts written **into** a consuming KB, never rendered here: `CLAUDE.md.tmpl` and `CONVENTIONS.md.tmpl`, stamped into `<kb-root>/` by `kb_pipeline.stamp_readiness_docs`, `phase-3a`'s pre-commit hook, each only if absent. `CLAUDE.md.tmpl`'s `{scope-pin}` slot is filled there from the build's recorded charter (SPEC.md, Project Scoping). Neither is the source of the file of the same name beside this table. |
| `tests/` | Unit tests over synthetic fixtures (the project's test target). |

### The Write API (`kb_write/`)

`kb_write/` is where SPEC.md's write-API contract (values in, canonical
bytes out — see The Write API's Contract) is composed. Consumers reach it
through one `kb_util` subcommand per member of `ops.OPS`, and its read-only
op through one more (see the CLI table above).

| Module | Role |
|---|---|
| `render.py` | The single composer of metadata bytes: register entries (claim, support and external work), the kb-frontmatter block, id and Tier-2 markers, structured bullets, prose collapse, derived-field placeholders, and the citation form. Values in, text out — total, pure, path-free, and holding no validation. |
| `values.py` | The values-file grammar (TOML) and the closed per-op key vocabulary: parse, domain-check, and refuse with a message naming the field and the line. It answers "is this a well-formed value?" and nothing else — it resolves no id and opens no file under `kb-root/`. Every numeric bound it enforces is inherited from the contract that already states it; this package authors none of its own. |
| `store.py` | The read-modify-write path over authored Markdown: path containment inside `kb-root/`, the register census, locate/splice, the temp beside the target, the readback proof on the temp, the concurrency check, and the atomic replace. Returns written / refused / retry as data; the exit codes are `ops`'. |
| `ops.py` | Op semantics: the validation ladder in order, mint fusion, the report lines, and the outcome-to-exit-code mapping — one function per op, plus the two registries a surface binds: `OPS`, the ops that write, and `READ_OPS`, the ops that read to verify and print. Surface-independent by construction (no `argparse`, no `sys.argv`, no `sys.exit`), so what an op means does not change when its surface does. One non-op name is public: `excerpt_lines`, the matching `mark-claim-in-leaf` resolves a locator by, exported so a caller composing one can ask the op's own question before spending a write. A caller that re-implemented it would be the checker that re-derives what the emitter derived, and a pre-check disagreeing with the op it pre-checks is worse than none. |

### The Document Graph (`kb_docgraph/`)

LaTeX volumes in, the KB's navigable Markdown tree out — run from its own
command line (`python3 -m kb_tools.kb_docgraph --source <root> [--source ...]
[--bibliography <path> ...] --kb-root <dir>`), and by `kb_driver` at the
`document-graph` stage, which composes that same line from the run's own
sources (The Driver, below). It is the front end for everything SPEC.md's
Topography graph guarantees, and produces no claim-graph artifact of any
kind — no `.index/`, no frontmatter, no id or claim-quality marker.

Four stages, each mechanical, plus the checks that make the mechanical claims
enforceable rather than aspirational:

| Module | Role |
|---|---|
| `convert.py` | Stage 1: one volume root, a source pre-pass (`strip_environment_declarations`, over `\newenvironment` — it must run *before* pandoc parses, because pandoc expands a declared environment during parsing and by the time any filter runs the author's name for it is gone), a second source reading (`theorem_display_names`, over `\newtheorem` — the preamble is consumed, so the internal-name→display-name mapping reaches the filter only if the caller reads it off the source; a babel translation macro resolves to its stem, and any other display name carrying a backslash is dropped rather than put on a label line), then two pandoc calls under one shared Lua filter and the same mapping — `to_ast` for the index, `to_markdown` (`-s`, so the metadata channel survives) for the content. Both get the mapping because check A compares the AST's own text against the rendering's, and a label word on one side only reads as a drop. The scan is the volume root's own text, so a preamble reached by `\input` yields no mapping and every block falls back to its internal name. The working directory is set to the volume's own on every call, because pandoc resolves `\input` against the invoking process's directory rather than the source file's. **One pandoc failure is recoverable here and the rest are stops**: a `--bibliography` the reader cannot parse is the input SPEC.md point 10 says a corpus may not have, so both conversions are re-run with none — both, because stage 2 zips them and check A compares one's text against the other's — and the files travel out on `Volume.unreadable_bibliographies` for `build.py` to report. The retry drops the **whole** set rather than a guess at the offender, pandoc's exit code naming none of them. The catch is on `pandoc.PandocBibliographyError` and never on the base type, so an unparseable source still stops. `pandoc.PandocIncludeError` is caught too and is not a recovery: it is re-raised as itself carrying the volume root, because the source travels to pandoc on stdin and its complaint names a line with no file, so a run over seven roots would otherwise report the loss against none of them. **Neither source pre-pass refuses, and the standing rule is why**: both read author markup held to no fixed standard, so a form the scan cannot read is an ordinary input rather than an exception — nothing mechanical is provably wrong and the unit that cannot be read can be named. `strip_environment_declarations` is brace-balanced and comment-aware, a `%` ending a line inside a declaration as it does anywhere else: LyX writes `\newenvironment{elabeling}[2][]%` and puts the body groups on the lines beneath, which a whitespace-only scan stops at and calls malformed. A declaration it still cannot read is left where it was written and travels out on `Volume.unreadable_declarations` for `build.py` to report on its own `declaration-read` line — a `FACT` naming the line of the volume root each one opens on, and stated in a zero form too, because a tree whose blocks lost their names reads exactly like a corpus that distinguished no block — and the scan carries on from the end of that keyword. What that costs is the environment's own blocks: pandoc expands them as the author defined them, so their content reaches the tree and neither partition check goes false, but they arrive as ordinary content rather than as labelled blockquotes carrying a name (SPEC.md point 12). |
| `outline.py` | Stage 2: the AST's `Header` sequence zipped against the rendering's own headings — asserted aligned, in count, level and text, before either is used — builds the tree. Level and label come from the AST (a heading's `\label` has nowhere to land in gfm); text comes from the rendering, never re-rendered from an AST slice. Frontmatter is parsed and classified here too (`CONTENT_KEYS`/`APPARATUS_KEYS`): `abstract` and `title` are content, `address`/`author`/`date`/`bibliography` are apparatus and dropped. The list enumerates and SPEC.md point 13's criterion classifies: `address` is `amsart`'s author affiliation and the only one of that class's seven other byline macros pandoc 3.11 lifts into `meta` at all, so the rest are absent because nothing emits them rather than because the criterion is unsure of them. `split_bibliography` lifts citeproc's reference list out of the rendering *before* the split at headings and `_references_document` makes it `<volume>/references.md`, the volume index's last child (SPEC.md point 10); the marker is the Div's own `id="refs"` plus its `csl-bib-body` class, the labels it declares move to it with its content, and the words stay on both sides of check B because this is a relocation, not an elision. `_lift_own_prose` is the sibling relocation: a section's own prose ahead of its first subsection becomes a first child leaf titled by `OWN_PROSE_TITLE`, splitting the section's labels the same way — see The Derived Skeleton, below, for both. |
| `judge.py` | The heuristic seam: `judge(tree) -> Verdict` accepts every input today; `recut(tree, verdict) -> Tree` is reached only from the reject branch and raises if invoked. See The Derived Skeleton's judgment seam, below. |
| `partition.py` | The build's own checks: the two checks below (The Partition Checks), the maths-survival count, the anchor-landing check, and the image-asset report. |
| `walk.py` | Reads pandoc's JSON AST. Word-granular — every word a `Str`, every gap a `Space` — so everything here walks rather than phrase-matches: the `Header` sequence, every cross-reference-bearing identifier (a `Div`/`Figure`/`Span` id, an equation's in-source `\label`), `meta` walked *recursively by node type* rather than enumerated by container (`author` is a `MetaList`; a walk that handles only `MetaBlocks`/`MetaInlines` skips it without a word), and every `Math` element's LaTeX. **A `Header` inside a `BlockQuote` is not in that sequence** (SPEC.md point 12): the filter has already reshaped an author-distinguished block into one, and an author's `\paragraph{Step 1.}` inside a proof is a step of that proof. `text.headings` leaves the same heading quoted where it sits, so the two sequences stage 2 zips are one rule read on two representations rather than two rules that agreed until a proof carried a heading. Only the heading reads differently inside a quote — a `Span` id still reaches `anchored_labels` from there, which is exactly where point 12 puts `<span id="thm:bif">` — and the heading's own label maps to the enclosing section, so a `\ref` to it lands on the document the block reached. `math_texts` takes the content metadata keys from its caller and walks no others: `blocks` in full plus `outline.CONTENT_KEYS`, which is where point 9 meets point 13 (an abstract's maths is counted, a byline's `^{1}` affiliation marker is not, having no document to reach). The classification stays in `outline.py`; a second reading of it here is a second thing to move when point 13's list moves. |
| `text.py` | Turns AST text and rendered Markdown into comparable token streams (`plain_tokens` / `markdown_tokens`, fence-aware, undoing what the gfm writer did to a rendering — tags, escapes, wrapped autolinks, character references, and a whole `<math>` element dropped the way a `` $`…`$ `` span is, the AST spelling that element once where the tag pass alone would leave both its glyphs and its annotation behind as text, and an inline code span **padded rather than dropped** — `walk`'s runs break at a `Code` and hold none of its content, while the writer spells the span flush against its neighbour, so restoring the boundary keeps the words on this side too, which matters because check B compares this stream against itself and a dropped span would hide a splitter that lost one) and finds headings in rendered text (`headings`, wrapped continuation lines folded back in) — the one normalization the splitter and the partition checks share. Its three fence scans read one delimiter rule (`_delimiter`): any indentation, because point 9's fence opens at a list item's content column; a close on the opening marker alone, because an author's `~~~` spacing is equation content; and `fenced_blocks` giving the item's columns back, because point 9's verbatim LaTeX does not carry them. **The writer has a second spelling of verbatim and `markdown_tokens` reads that too**: an attribute-less `CodeBlock` — every `\begin{verbatim}` — is written indented rather than fenced, and is measured against its *container's* content column, because pandoc writes `1.  ` and an enumerate item's second paragraph otherwise sits at the same four columns a top-level code block does. Read as code, its cross-reference runs would break; read as prose, its `_` is deleted as an emphasis mark and every identifier carrying one is reported lost. |
| `build.py` | Stage 3: writes the tree and its copied image assets, runs both partition checks plus the maths/anchor/asset checks, `kb_survey.validate.validate_build` (below) and the repo-wide dead-link gate, and returns a `Report` whose `failed` property is the whole verdict. A failure stops; there is no fix loop, because every check compares one mechanical product against another and a red gate is a defect in this package or its input, never something a seat repairs. |
| `authored_blocks.lua` | The one Lua filter both pandoc calls run under. Wraps every `Cite` in `<span class="citation" data-cites="…">` — pandoc's own markup for one — leaving the `Cite` inside so citeproc still resolves it and still emits the `refs` Div the references leaf is cut from; where `pandoc.CITATION_KEYS_ONLY_FLAG` says no citeproc will run, the span carries the keys as its own visible text instead. That flag is read in a first `Meta` pass and **removed**, because it is an invocation argument the tool passed itself rather than the document's metadata, and `outline.py` classifies every metadata key it finds against a closed list. Also reshapes an author-declared Div into a labelled blockquote, carrying the identifier forward on a `Span` so a `\label` survives to the label-to-node map — and, downstream of that map, as the definition end of SPEC.md's cross-reference join (Corpus Invariants), which is what a reference resolves against; drops the `titlepage` Div outright. **Identifying such a Div is structural and naming it is not.** Pandoc classes an unrecognised environment with the declaration's *internal* name, so `\newtheorem{claimbox}{Result}` yields a Div classed `claimbox`, and a filter keyed on a list of declarations would find neither an environment nobody declared nor amsthm's `proof`; but that class is an arbitrary handle, and point 12's label line carries the *display* name. The mapping arrives on `pandoc.THEOREM_NAMES_FLAG` — read in the same first `Meta` pass, JSON-decoded, and removed for the same reason — because pandoc consumes the preamble and no `\newtheorem` reaches a filter in any form; a class the mapping does not answer falls back to itself. **Lifts a display equation out of the `Emph` around it**, the words either side keeping their emphasis and an emphasised group left holding no word emitted unwrapped. A `\newtheorem` environment's statement is italic, so the reader returns the whole statement — display equations included — as one `Emph`, and where an equation opens it the gfm writer puts that `Emph`'s delimiter against the fence's own backticks: `*``` math`, which opens no fence for any reader, leaves the closing delimiter to open one, and reads every heading after it as fenced. SPEC.md point 9 says display maths reaches a document as a fenced block holding the LaTeX verbatim, so the emphasis is what gives way. `Emph` is where this has been measured; another inline wrapper carrying display maths would reach stage 2's alignment check rather than pass quietly. |
| `__main__.py` | The CLI: `--source` (repeatable, each a volume root — never a file reached by `\input`), `--bibliography` (repeatable, none legal — the reader merges what it is given and renders only cited entries, so the caller passes the set it has rather than choosing), `--kb-root`. Prints the report's lines; exits 1 on any `FAIL`. |

#### The Derived Skeleton (`outline.py`, `judge.py`)

`outline.build_tree` places every heading at exactly
one KB path, ranked per SPEC.md's "normalisation is ranking, not
subtraction": a volume's tree levels are the *rank* of each heading level
among the levels that volume actually uses, so `{1,4}` becomes tree levels
`{1,2}` rather than a tree carrying two empty interior levels. `entry-point.md`
lists every volume; each volume owns a directory named from its own title (or
its filename stem, where it declares none); a heading with descendants is
`<slug>/index.md`, one without is `<slug>.md`; a volume's own lead-in material,
and every section's own prose ahead of its first subsection, becomes a first
child leaf of that node under a supplied `Overview` heading
(`outline.OWN_PROSE_TITLE`), so an index node's own extent is relocated rather
than dropped and no index carries source of its own — the index keeps its own
heading, so every index reads the same whether or not its section wrote a
lead-in. A node with nothing below its heading has nothing to lift and gets no
leaf, the answer `_references_document` already gives an empty bibliography.
`OWN_PROSE_TITLE` is accounted in `_supplied_titles` once per leaf
`_lift_own_prose` emits: a supplied title is listed exactly as many times as a
segment carries it, which is what keeps the lift a relocation rather than a
word the split invented — check B's word totals move by one word per leaf
emitted, on both sides at once, and the check gates on the two differences
being empty rather than on the totals matching. The lift also splits a
section's labels: a heading's own identifier (`outline.header_labels`) stays
with the heading and so with the index, while a label the lead-in prose
declares on its own account — a Div or Span id, an equation's in-source
`\label` — follows the prose to the leaf; in a built tree, `#preliminaries`
stays on `preliminaries/index.md` while `thm:mainrect`, declared in the
Introduction's lead-in, resolves to `introduction/overview.md`. Path segments
are slugged from titles, collisions
resolved by sibling ordinal, and the slugger reserves `kb_index_lib`'s
`EXCLUDE_NAMES` / `EXCLUDE_DIRS` and the `index` stem, so a derived path can
never be one leaf discovery would not see. `DepthError` stops the build when
the realized tree depth disagrees with the volume's own count of distinct
heading levels — a fault in the derivation, never a finding about the corpus.

**The judgment seam.** `judge(tree) -> Verdict` is where "is this decomposition
good enough, or does it need help?" will be asked — a volume that is one
undivided wall of text, a leaf far larger than its siblings, a hierarchy one
level deep. It is stubbed: every volume is accepted today. `recut(tree,
verdict) -> Tree` is reached from the reject branch and from nowhere else, and
raises if it is ever invoked — there is no inference call, no prompt, and no
data format for a response; populating those is unbuilt work. Judging a
decomposition undesirable is never done by truncating it: the tree carries the
author's hierarchy at whatever depth they wrote it.

#### The Partition Checks (`partition.py`)

**Two checks, because there are two things that can lose content.**
Check A (`check_ast_against_markdown`) compares the AST's blocks and `meta`'s
content-bearing entries against the whole-volume rendering, matching by
contiguous word run rather than set membership — an abstract restates the
body's own vocabulary, so token overlap would score a dropped abstract as
present. It catches what *pandoc* drops. Check B
(`check_markdown_against_tree`) compares that rendering against the tree,
Markdown on both sides, a near-total partition; it catches what *this
package's splitter* drops. The elision either check exempts is a closed list —
`outline.APPARATUS_KEYS` plus the `titlepage` Div the filter drops, and nothing
else, a literal a diff can show rather than a judgment call. Neither check sees
what pandoc drops *before* parsing — `tikzpicture` is the confirmed instance,
absent from the AST entirely at exit 0 with no warning — so a build is not
self-certifying against a reader-level drop the AST itself never recorded.
**A reader-level drop the reader does announce is stopped where it is
announced, never checked for here**: an include pandoc could not load is
`pandoc.PandocIncludeError` at exit 0, raised off the stderr line that is the
only record such a file leaves anywhere. Both checks would pass over it —
`\input{1.introduction}` eight times built a 108-word tree from an eight-section
paper — because the content is missing from both sides of both comparisons.

**Maths, citations and figures**, in the order SPEC.md states them: every
`Math` element reaches a document by literal containment (`check_math`), read
in each of the three forms the writer carries it in — an inline code span, a
fence, and a caption's MathML annotation, the last unescaped first because it
is XML text and its LaTeX arrives written in character references. The source
side of that count is `blocks` plus `outline.CONTENT_KEYS`, never the whole of
`meta`: point 13 elides the byline before any document exists, so its `^{1}`
affiliation markers have none to reach and asking point 9's question over them
reports the elision as a drop;
citations *survive* because the Lua filter wraps every `Cite` in pandoc's own
citation markup before citeproc sees it, and *resolve* where an explicit
`--bibliography` is passed. The two used to be one thing: without `--citeproc`
every `\citet{}` reached the gfm writer as a `RawInline` it had nowhere to put
and the citation was silently gone, so a bib-less corpus built a tree with no
citations in it and exited clean. Now a bibliography is optional (SPEC.md point
10) and its absence costs resolution and the references leaf, not the citation. **A bibliography that
cannot be read costs the same and is reported** — `build`'s own `bibliography-read` line, a `FACT`
naming every file the volume was offered, because the degraded tree carries the same citation markup
as a resolved one and a
silent fallback would read as citations that resolved; figures are tabled (`ROADMAP.md`, the
inline-drawn-figures item) — an external image that is on disk is copied and embedded as a
self-linking image, one that is not is reported (`check_assets`) rather than a
build stop, and an inline-drawn figure (`tikzpicture`) is not yet recoverable
at all, pandoc having dropped it before the AST exists for any rule to select.

### The Claim Graph (`kb_claimgraph/`)

The tree in, the claim-graph metadata spine authored into it — run from inside
the consuming repo
(`PYTHONPATH=.claude/agents python3 -m kb_tools.kb_claimgraph [--pass 1|2]
[--scope block-hosted|full]`), and by `kb_driver` at the three stages that
follow the spine seed, one per invocation (The Driver, above). It takes no root
override: the repository is walked up to from the working directory, the way
every other tool here resolves one.

**Three invocations, and they are separate because inference is the part that
needs repeating.** `--pass 1 --scope block-hosted` — the default — authors the
*declared* graph, what the author marked explicitly, and is mechanical end to
end. `--pass 1 --scope full` is **claim discovery**: it reads the documents that
run left awaiting and mints a claim node per result they state in prose.
`--pass 2` authors the *discovered* graph's dependency edges over the claims
that then exist. Each extends its predecessor's output additively rather than
rebuilding it, so a failed or improved inference costs none of the mechanical
work and can be re-run against a different model without rebuilding the tree.
The two inferential invocations are ordered — discovery mints nodes, stage D
authors edges over a graph that by then includes them — and neither authors what
the other does.

**The discovered graph is a lower bound by construction, in both directions.**
A result the author stated in a way discovery's ask does not recognise is
invisible, and a dependency the author never cross-referenced is invisible to
stage D's candidate enumeration and to a model answering one bounded pair at a
time. No run can claim completeness for either the nodes or the edges it
authors; that is a property of the input and of the method, not a defect to be
closed.

**Discovery's checks are weak by construction, and the asymmetry is deliberate.**
What is checkable is *location*, and only half of what once was: the bytes a
record points at are the tool's own slice of its document, so verbatimness is
obviated rather than checked, and what remains is that the slice appears exactly
once — widened one sentence at a time within its paragraph until it does, and
failing only where the whole paragraph is still not unique. Nothing bounds
fabrication of claimhood, and nothing bounds how many records one document may
yield. The ask therefore biases toward recording, because a wrong claim is a
register entry anchored at a span a reader can compare against the document and
a missed claim is invisible to every gate, count and roll-up in the build. That
visibility is a person's, not a gate's.

**Each invocation has its own re-entrancy guard, and they ask different
questions.** Pass 1's is over the tree: point 14's cleanliness check refuses any
of the artifacts it writes, because a second run would mint a second set of ids.
Discovery and pass 2 mint nothing into a document that already carries a
*determination*, so their guard is per document — `conform.determination` — and
the tree they must accept is one carrying frontmatter everywhere. Discovery
writes per document rather than per batch, which makes that guard its
checkpoint: a run of it costs an ask per document and a stopped run must not
cost the documents already done.

**The tree is its sole input** — no `.tex`, no AST, no bibliography — which is
what makes SPEC.md's Document-Tree Contract the replaceable seam it is stated
to be. **`graph-init` runs before it** (Initialising the Claim-Graph Spine,
below): the derived-index directory and the runner include line are that verb's
writes, and a run finding either absent exits **3** naming it rather than
seeding half a spine itself.

| Module | Role |
|---|---|
| `tree.py` | The readings every stage shares: the document set with its down-link and up-link relations, the blockquote-prefix strip a scan over an author-distinguished block runs first, and the two readings of what `kb_write` inserts, both read off `kb_write.render` rather than re-typed: `METADATA_OPENERS`, what each artifact opens with, and `strip_markers`, which takes an appended marker back off a line of authored content. Also the `kind:` derivation — three values off path shape alone: `entry-point.md` by name, a document with children an `index`, one without a `leaf`. |
| `conform.py` | **Stage A**, the conformance gate over the Document-Tree Contract. Writes nothing and stops on the first failed assertion naming the point. Ordered by dependency rather than by the contract's numbering: point 7 first, because every relation below it is derived from links and a dead link is a phantom child; point 14 last, because a fresh tree passes everything above it. Point 14's cleanliness check is pass 1's **double-run guard**. Its one unchecked clause is point 14's `.index/` ban, which describes what `kb_docgraph` leaves behind and not what `graph-init` legitimately creates between the two stages. `pass_two_gate` is pass 2's entry condition: the same structural checks, and in place of the cleanliness check a per-document partition by `determination` — awaiting, hosting claims, or carrying an authored reason no later pass may overturn. |
| `inventory.py` | **Stage B**, the claim-site inventory: labelled blockquotes with their name, identifier, title-and-locator span off the display line, and extent (point 12); display-maths fences and the `\label` tokens in them (point 9); rewritten cross-references with their resolved target, the author's own label off the anchor's third attribute, and which block they sit inside where they sit inside one (point 7) — the label because an `eqref` resolves to its document with an empty fragment, point 9 leaving an equation's `\label` inside the maths fence rather than as an addressable id, so it is the only thing naming which equation (717 of 717 `eqref` anchors over the staged corpus carry no fragment); and the citations the tree renders recognisably (point 10). **A citation reports which of point 10's three states it ended in, and the discriminator is the span rather than the build.** `Citation.state` is a `CitationState` — `resolved`, `unanswered`, `key-only` — read off the span's own two halves: `data-cites` holds the keys and the span's text holds what became of them, so a span whose text is exactly `(key1; key2)` was offered no bibliography, and where citeproc ran a key marked `**key?**` is one it could not answer while its neighbours in the same group resolved. That makes the state **per key**, which a multi-work `\citep` needs and a build-wide condition cannot give; "the tree renders no reference list" is *not* the test for "no bibliography was offered", since a bibliography answering none of the cited keys emits none either and that is the other state. The reference list's entries are `Work` records rather than citations — the list is the volume's and carries each work once — which is what stops a one-work corpus being reported as two. `build.py`'s `stage-B-citations` line carries one count per state (`inline-resolved` / `inline-unanswered` / `inline-key-only`, every state present so an unreached one reads zero) plus `reference-list` for the works. **The two tables hold display names, which is what point 12's label line carries.** A `\newtheorem` internal handle is arbitrary — a survey of 35 arXiv papers found 43 of them, 36 outside any table, the tail aliases (`Cor`, `Lem`, `Pro`), starred variants (`lem*`) and other languages (`Teo`, `oss`) — while the display names concentrate onto thirteen words; `claimbox` is classified as the `Result` it declares itself to be, and needs no entry of its own. Where no display name was declared the label carries the internal one (amsthm's `proof`) and the tables carry that word too. Matching folds case, the label carrying the author's own capitalisation. The tables are still closed, and a name outside them is **recorded rather than refused**: the block is admitted as not-claim-bearing — `Block.claim_bearing` reads `CLAIM_BEARING` alone, so an unclassified name is outside it by construction — and the name is counted into `Census.unclassified`, which `build.py` reports on its own `stage-B-unclassified` line. What the closed table guards against is silence, not omission: nothing classifies an unknown name as claim-bearing, so no claim enters the graph on a guess, and a named counted omission is the census line a reader greps where a halted build reports one bit. `NOT_CLAIM_BEARING` has no branch reading it and is not obviated — subtracting it is what separates a name somebody classified as not stating a result from a name nobody has classified, and only the second is reported. This is what keeps the stage usable off the corpus it was calibrated on: the survey's residue is some fifteen one-offs (`Setup`, `Axiom`, `Fact`, `Hypothesis`) and it skews away from results, so an unclassified name defaulting to not-claim-bearing is both safe and usually right. A blockquote with no label line is ordinary quoted prose, not an unclassified block. A Tier-2 marker an earlier pass appended sits on the end of a quoted line, so it neither ends a blockquote nor joins the content read off it, which is what lets pass 2 re-scan a tree pass 1 has written markers into. **A block's title and its locator are one span of its display line, and point 12's optional argument is not required to supply it.** Where the author wrote one the span is `**Theorem 2** (The governance bifurcation).` and the title is the argument; where they wrote none — the norm outside corpora written with tooling that prompts for a title, and two of three unguided arXiv `math.*` papers stopped here before it was read — the span is the environment's printed word and its number, `**Lemma 3**.`, and the title is that with its emphasis markers off. Nothing composes a title from the block's content: a bare *Lemma 3* is a poorer heading in `ask.py`'s enumeration than an author's sentence, and it is the one the page carries. **A span that runs inside another block's display line grows through its own content until it does not**, because `\newtheorem*` renders every block of an unnumbered environment under the same word and a locator matching two blocks binds a claim to the wrong site silently; growth is decided against the document's other display lines rather than against the spans already handed out, so both of two alike blocks grow and neither depends on which comes first. The title grows with the span, a register entry being bound back to its site by title in `graph.read`. **The span is one span read for two purposes, and only the locator is bytes**: `Block.display` is matched against the document and nothing normalises it, while `Block.title` is read by a person — a register heading, an entry in `ask.py`'s enumeration — so `_readable` takes point 10's citation markup off it and keeps what that markup renders. An author titling a block `[\citet{gibbons2020}]` titled it with a `<span class="citation" data-cites="…">`, which is the reader's markup rather than the author's words; the substitution runs through `CITATION_SPAN_RE`, the same reading of that markup the citation inventory is built on. **Two shapes stay unreadable, and each costs its own block rather than the build**: a display line not opening with the printed name in bold at all — an environment declared in a class file rather than the volume root, so the reader prints no name and no number and the content begins with the author's sentence — and one running inside another block's to its last word, where no span names one block rather than the other. Such a block carries no title and no locator, `Block.claim_bearing` is false for it on that second condition as well as on the name, it enters the graph as no claim, and it is counted into `Census.unreadable` — reported per document on `build.py`'s own `stage-B-unreadable` line, which is what keeps the absence from being silent. This is the same trade the unclassified name takes one sentence above: a halt is right where two mechanical artifacts disagree or where a loss would reach no check at all, and here neither holds — one authored block is unreadable, and stopping charges every other document of the tree for it. **A sixth reading says which claim each proof establishes** (`Inventory.proofs`), and it is a reading rather than a consumer's derivation because two passes direct a reference by it — stage D a `\ref`, the off-graph endcap a `\cite` — and a binding derived twice could disagree about the corpus with no third artifact to settle it. It binds *blocks* to blocks, so it is usable before the first id is minted, which is what lets the endcap run in the declared pass; it runs after the per-document loop because a proof may name its subject in another document and therefore needs every document's blocks first. The two arms and everything the binding misses are in the `attribute.py` row, which is where the ruling that a proof establishes the claim it belongs to is argued and measured. |
| `label.py` | The sentence-labelled render C-inf's ask shows, and the map from a label back to the document. One sentence per line with the wraps collapsed, the paragraph breaks kept, each line opening with `S<n>` — and **no label for the up-link or the frontmatter block**, which is what retires the navigation exclusion rather than checking it: what the render does not offer, a locator cannot name. A paragraph here is exactly a run of lines a slice can span, because `ops.excerpt_lines` joins each body line's collapsed text with single spaces and a blank line puts two into its haystack. A display-maths fence is not special — measured over the survey corpus, only 6 of 74 carry a blank line before them and 59 open mid-sentence, so a fence sits inside its own paragraph and a span crossing one is findable; the render only leaves its lines unsegmented. `Span.excerpt` is the tool's own slice, and `widen` is the sequence of wider spans within one paragraph. |
| `identify.py` | **Stage C.** `block_claims` is C-mech, wholly mechanical: title, locator and host are all read off one display line, titled or not (the `inventory.py` row). `check_block_coverage` is the comparison that says every block is named exactly once. `infer_claims` is C-inf: one ask per document through `ask.ModelIdentifier`, then `check_answer`, whose per-record checks run in a **load-bearing order** — the locator names labels the render emitted and names them in order; the span lies in one paragraph and is not wholly inside a maths fence (before the next, because it is what establishes the paragraph the next works inside); the slice appears exactly once under `ops.excerpt_lines`, **widened one sentence at a time within its paragraph until it does** (before the rest, because it is what settles which line the record lands on); the line is outside every claim-bearing block; the title is one line and unique in the document; and — the one check over the records *together* — no record's marker would land inside a later record's span. **Verbatimness is obviated rather than checked**: the bytes are the tool's own slice, so no comparison is left to make. **Uniqueness is not**, and widening is how a repeated slice is fixed without an ask — the seat has no instrument for making its own quotation unique and the tool does. The collision check is what a wrapped paragraph named twice costs: a marker goes on the end of the first line of its own span and `mark-claim-in-leaf` resolves each locator against the document the previous marker left, so the second span is unfindable by the time it is marked, and the op's refusal would land after the register entries and the frontmatter — leaving the document reading as hosting claims it carries no marker for, out of every re-run's scope. One re-ask against a report naming each failure, then a stop — except that a *reason* failing twice takes `SUBSTITUTED_NO_CLAIM_REASON` and the run continues, because a missing sentence loses prose nobody had while a fabricated locator loses a claim that exists. **An answer that did not parse spends a re-ask of its own**, against the parse refusal's own message, and a second one stops the stage naming the document: it arrived, so it costs a re-ask — but `PARSE_RETRY_BUDGET` and not `CHECK_RETRY_BUDGET`, because a document whose first answer was malformed still has every check ahead of it and a shared allowance left it none for them. Each is one and `CALL_BUDGET` is their sum plus the first ask, so a seat alternating between the two classes stops at the third call. |
| `graph.py` | The authored claim graph read back off the tree: each document's `claims:` joined to its domain register's entries, and each claim bound to its site — by block title where a block carries it, and otherwise by `marker_locator`, the line that claim's own Tier-2 marker sits on. That second arm is a prose claim's only route back to a span. Nothing is re-derived; the only thing computed is the join. |
| `attribute.py` | **Stage D.** The mechanical narrowing, then one bounded selection per source claim over what it left open. Each end of an anchor is attributed to a claim where a rule settles it: the **source** end through the claim-bearing block it sits inside, through the claim the *proof* it sits inside establishes, or through a document hosting exactly one claim; the **target** end through the fragment naming a block's own source label, through an `eqref` whose equation sits inside a claim-bearing block, or through a document hosting exactly one claim. Where both ends are settled **and the source end was settled by proof containment**, the pair is an edge and no question is asked about it — a proof establishes what it belongs to, so containment supplies the direction a bare cross-reference does not carry. `_fragment_claim` is this package's whole reading of SPEC.md's cross-reference join (Corpus Invariants), asked at the join's definition end: the fragment against the identifier the target block declares, never against how an author spells a label. **Three narrowings were reconsidered and reversed, and the reasoning they replaced is kept in `attribute.py`'s docstring rather than deleted.** They dropped every `eqref`, every reference whose fragment named a section, and any direction a proof's containment would have supplied, each on the ground that a wrong edge is worse than a missing one. What inverted that is what an edge is on this path — a draft a later inference pass corrects, against a claim recorded as resting on nothing, which is a wrong answer nobody is prompted to revisit — while the line that does not move is being provably wrong inside a mechanical constraint, which the two checks below hold. Measured: 39% of cross-references in the staged corpus's claim-bearing maths papers sit inside a proof; 232 of 791 claim-reaching references in an earlier survey named an equation inside a claim body; and 104 of 159 resolving `ref` anchors over the seven-volume corpus's tree are section references. Over the ten staged maths papers carrying a built tree, the narrowing settles **102 edges with no model asked** — 79 by identifier, 20 by equation, 3 by sole claim — and takes the ask from 66 sources / 197 pairs to 57 / 138. One argument survives unanswered and is paid: a section reference into a **multi-claim** document offers every claim that document hosts, so the enumeration is k×m in exactly the place discovery multiplies m; that case stays ambiguous and stays the ask's. **A proof binds to its claim two ways**, in order: the anchors of the italic run its content opens with, which is where the reader renders `\begin{proof}[Proof of Theorem \ref{…}]` and which binds across documents; otherwise the block *immediately* above it, where that block states a claim. **That binding is stage B's** (`Inventory.proofs`, the `inventory.py` row) because it is about blocks and lines and is knowable before any id exists; what `_proofs` adds here is the one step that needs minted ids — mapping each subject block to the claim it carries, through the same `_claim_of` join `graph.read` made, so a subject block no register entry carries drops out and the proof binds to nothing. Reaching past a block that states none is what inverts an edge — on `math.DG` 2609.10523v1 a result stated in ordinary prose sits between two proofs, and reaching past both bound the second to the lemma above them and closed a cycle with the first — so the binding stops at the block above and every case it misses stays an open pair rather than a directed edge. A further narrowing was considered and refused, and is unchanged: shared containment in a directory contributes no candidate on its own, because filing is a placement decision and not a dependency relation. **A pair the narrowing cannot direct is recorded as a `references` edge rather than discarded** (SPEC.md, Claim-Graph Nodes and Edges). `narrow` returns every open pair on `Attribution.references` alongside the `Question` enumerating it, and the two are not alternatives: the pair is still put to a model, because what the narrowing could not settle is the *direction of dependence* and not whether the source's text names the target. `references_beyond` is the difference the pipeline writes — the selection's edges subtracted, so a pair raised to a dependency is upgraded rather than carrying two records onto one drawn stroke. Measured on the seven-volume corpus's tree at the declared graph, this is **23 pairs over 14 claims** that a `--no-inference` build previously dropped entirely against the 4 dependency edges it kept. The class is under no acyclicity constraint and that corpus exercises the case — six mutually-referencing pairs, each of which would be a cycle were this a `depends` edge. Two comparisons bound the *dependency* half: `check_membership` (set membership over the ids offered) and `check_acyclic` (`kb_index_lib.compute_solidity` raising `SolidityCycleError`, before anything is written), which runs over `depends` alone. **The acyclicity check runs twice, and the first run has no re-ask behind it**: the mechanical edges are checked alone before any question is asked, and there is nothing to re-ask about edges no model selected — so `narrow` breaks that ring itself rather than stopping, demoting **every** edge on it to `references` (`cycle_edges`: an edge is on a cycle exactly when its target reaches its source, which is a property of the edge set and not of the order it arrived in, so two runs over one corpus demote the same edges with no tie-break key to agree on; what remains is the graph's condensation and is acyclic by construction). A ring is not evidence that the corpus reasons circularly — proofs citing one another for symmetry, and one argument carried across several statements, both read as direction-bearing to containment — and it is evidence about nothing but its own members, so it may cost its own edges and no others. A minimum feedback set was refused: it leaves the ring's other members asserted as dependencies on a discrimination nothing in the corpus supports, and on a 2-cycle it is a coin flip reported as a finding; demoting the whole ring gives up only the direction the cycle disproves and keeps every relationship, the demoted edge being recorded rather than dropped. A demoted pair is **not** reopened as a question, so a run with a model and a run without demote alike. Measured over a 50-paper `--no-inference` arXiv sweep, three papers carried such a ring — two 4-cycles and a 2-cycle — and each produced no graph at all before this. What remains under the check's own name is the post-condition: a cycle surviving the demotion is a defect in it, not a finding about the corpus. The whole set is then checked again with the selections in it. `depends.py`'s `stage-D-attribute` line carries the demotion count and names each demoted edge, with a zero form, since a demoted edge is an ordinary `references` edge once written and nothing else records which they were. One re-ask against a report naming the offending ids or — where the answer did not parse — the parse refusal's own message, then a stop naming the source claim. The cycle is not a report: it travels as a path on `Selector.select`'s `cycle` argument and is asked through a template of its own (`ask.py`, below), spent on that re-ask's first call alone, so a malformed answer to it falls back to the ordinary ask under the refusal. The parse and the membership check hold separate allowances (`PARSE_RETRY_BUDGET`, `CHECK_RETRY_BUDGET`, one apiece, bounded together by `CALL_BUDGET`), so an answer that did not parse leaves the check that never ran its own re-ask. |
| `ask.py` | Both inferences. `SeatAsk` is the injected seam — `kb_tools.inference.ask_seat` in production, a fixed answer under test — so nothing in this package knows a subprocess exists. Stage D's ask is a selection from an enumerated set (`compose_prompt` / `parse_answer` / `ModelSelector`); C-inf's is the one open-ended reading in the build (`compose_identify_prompt` / `parse_identify_answer` / `ModelIdentifier`), and its prompt carries one document as `label.render` shows it, the labels its maths fences occupy, and no other document and no other document's answer. **Neither prompt is written in this module.** Both are templates under `kb_driver/prompt-templates/` — `depends.tmpl`, `identify.tmpl` — filled through `prompt_templates.render`, and what varies takes one of two forms chosen by what the condition does to the ask. A specific tailored under an unchanged question is a **substitution**: the alternatives are files under `prompt-templates/fragments/` (`identify-display-maths` / `identify-no-display-maths` for `@!display-maths!@`, and `ask-correction` for `@!correction!@` on a re-ask of either stage, against the `None` a first ask names, which fills it with nothing). This module hands the composer the **name** it chose and the composer resolves the file, so no prose and no path leaves here — a caller holding a fragment's prose would be prose reaching a model from application code, which routing makes unrepresentable rather than discouraged. A question that differs is an **alternate template**: `depends-cycle-reask.tmpl`, stage D's acyclicity re-ask, reached through `compose_cycle_prompt` and the `cycle` argument on `Selector.select`. No template carries a conditional and no prose is assembled in Python. The marker literals travel as composer constants — `MARKER_SLOTS`, this package's analogue of `steps.CONSTANT_SLOTS` — because the parses match answers against those same constants and a template spelling one would be a second definition; they are constants and not per-call values, so they reach a template through the composer's pool and never through a caller's slots. **The seat quotes nothing**: it returns a title it composed and a locator, and the tool cuts the bytes. Each parse reads its marked block back through `kb_driver.envelope`'s record toolkit, closed and total in both directions and at every level, with every composed value in a numbered prose block beside the JSON; `LEVELS` declares which keys carry which, and `envelope.check_levels` proves the partition at import. Stage D declares an **empty** prose set, which is a check rather than a formality — a prose block in its answer is refused rather than ignored. A call whose `Outcome` is retryable is re-issued identically up to `TRANSPORT_ATTEMPTS`; one that is not stops the stage on the first return. **An answer that arrived and did not parse is neither**: it is `report.AnswerFormatError`, and the stage that holds the re-asks spends its parse allowance on it, never the one its mechanical checks are owed. That type is declared in `report.py` rather than here because this module imports from both stages that catch it, so the seam stays one-directional and every check still runs against a fixed `SeatAsk`. Both consumers take `SEAT` as a constructor parameter, so a run can move the seat without a code change, and each ask's prompt and captured stream land under the run's scratch workspace. |
| `assemble.py` | **Stage E.** No new fact enters: the register batches, the frontmatter records, the marker records and the endcap's works and edges, with claims named by position in the entry list because ids do not exist until the first write pass mints them. A work needs no such indirection, its id being derived from its key. `UNSCANNED_REASON` is written here and compared by identity in `conform.determination`. |
| `endcap.py` | The off-graph endcap's join, in stage E's service: which external works this corpus's claims cite, and which claims rest on them. Reads nothing new — `Citation.line` compared against a run of lines a claim owns, all of it already stage B's — and asks nobody anything. **A claim owns two such runs**: its own block, and the body of the proof establishing it (`Inventory.proofs`, which this module consumes rather than re-deriving). The second is where the warrants are — measured over 25 built arXiv KBs, 8 citations sit inside claim blocks against 68 inside proofs, and the block population is almost entirely the theorem environment's *optional argument*, an author attributing the whole statement rather than the step. Containment is asked of the proof's lines while the pairing is recorded under the subject's site, because the head arm binds across documents. A claim citing one work in both its statement and its proof rests on it once. The node's identity is the citation key, so a work three volumes cite is one node; it is tied to the bibliography and not to the per-volume `references.md` leaves, which are citers of it. Where the bibliography answered the key, the reference list's own rendered text titles the node; where none did, the key does, and the rationale says which. Neither of the two scores is defaulted, heuristic or inferred (SPEC.md, Claim-Graph Nodes and Edges). |
| `write.py` | **Stage F**, the passes of SPEC.md's write-API contract, in the order the ops' preconditions force. The endcap's two passes sit inside that order: the works are inserted before any edge can name one, and their `rests-on` edges go through the same `add-depends-on` batch stage D's do — one op, two things it lands over a build's three invocations, told apart by the report name. The values file is the one thing composed here, and it is put through `tomllib` against what the composer declared it must parse back as before any op sees it; prose travels in `'''` literal blocks. A `7` stops the run, an `8` is re-issued identically. Mint order is proved against the register through the production parser rather than trusted. `write_edges` is pass 3, `add-depends-on`, one batch — the declared pass's off-graph edges and pass 2's attribution alike, the dependency edges and the `references` edges of one source travelling in the same values entry so a refusal cannot land half of one claim's edges. `write_claims` is discovery's, and it is **one document per call** rather than one batch per run: the insert, the `set-frontmatter` that replaces the awaiting reason with a `claims:` list in one act, and a marker for **every** claim however few the document declares — a block-hosted claim's locator is recoverable from the tree forever and a prose claim's is not. |
| `gate.py` | **Stage G**, the runner's `kb-refresh` then `kb-verify`, by target name. Green or stop, on the return code alone — there is no fix loop and no seat to run one. |
| `build.py` | Pass 1's pipeline, mechanical end to end. |
| `discover.py` | Claim discovery's pipeline: `pass_two_gate`'s partition, the inventory and the assertion that no document in scope hosts a block, then per awaiting document the ask, the checks and `write_claims`, and finally its own exit condition — **no document in scope still reads awaiting** over the tree the run just wrote — before the same stage G. |
| `depends.py` | Pass 2's pipeline: the entry condition, the inventory, the authored graph, stage D, `write_edges`, and the same stage G. |
| `report.py` | The uniform `[claimgraph] STATUS name detail` line, the `Report` every pipeline produces, and the stop that becomes a line. Also `AnswerFormatError`, the one stop type declared away from the code that raises it: `ask.py` raises it and `identify`/`attribute` catch it, and `ask.py` imports from both of them. |

**No number is authored anywhere.** Every register entry's rigor is
`kb_schema.PENDING_LITERAL`, as is every external work's `strength` and every
`rests-on` edge's applicability — and those two are pending permanently as far
as any build is concerned, nothing deriving them and no stage pointed at them; `solidity`, the bullet annotations, the roll-ups
and the leaf-reference footers are all `refresh`'s (SPEC.md, Derived Metadata,
Defined). Those two pendings now carry a consequence a build's reader sees: a
`rests-on` edge gates, so a claim citing outside work has a pending `solidity`
until both are supplied, and the scoring pass owes one `strength` per work plus
one applicability per pairing before any such claim carries a number again
(SPEC.md, Claim-Graph Nodes and Edges).

**Three properties of this pass need an integration target over a real corpus,
and none exists yet — they are unproven.** Such a target builds a document tree
from a multi-volume corpus, stands up a throwaway installed consumer repository,
seeds it with `graph-init` and runs pass 1, the endcap included, over a corpus
carrying at least one citation inside a claim block. What it must establish:
that `kb-refresh` and `kb-verify` exit 0 over the result, that every line gained
is a frontmatter line and every line rewritten is the same line with a Tier-2
marker appended, and that a second pass-1 run refuses at the conformance gate
naming point 14 with `kb-root/` byte-identical. **Neither inferential invocation
belongs in that target**,
because a run of either spends inference and the target must be green without a
model reachable. They are exercised instead by
`kb_tools/tests/test_kb_claimgraph_cinf.py` and
`kb_tools/tests/test_kb_claimgraph_pass2.py`, each of which stands up the same
shape of consumer and drives every stage against a fixed `ask.SeatAsk`. Neither
asserts anything about two runs agreeing: what a fixed seam establishes is that
the mechanism holds answers fixed, never that a model would give the same ones
twice.

### kb_survey, Retained

Three stdlib-only modules survive from the toolchain's pre-pandoc LaTeX
reader — `manifest.py`, `skeleton.py`, `validate.py` — unchanged by this cut.
Everything that read LaTeX through the vendored parser
(`_pylatexenc.py`, `expand.py`, `compose.py`, `strip.py`, `harvest.py`,
`leaf.py`) is deleted, with the survey ops that fronted them
(`survey-sources`, `render-manifest`) and the golden fixtures that pinned
their output.

| Module | Role |
|---|---|
| `manifest.py` | The schema — record types, the closed `FlagCode` enum, the `mf:` id prefix, reader and writer, atomic write, `volume_slug`. Unchanged; nothing currently *writes* one (below). |
| `skeleton.py` | `derive(manifest)`: manifest in, KB tree out, by the same ranking rule `kb_docgraph.outline` now applies independently and without it. `partition_defects` is its audit; `sections_needing_recut` is a judgment seam of its own, parallel to — and, with `p1.judge` gone, currently uncalled beside — `kb_docgraph.judge` above. |
| `validate.py` | `validate_build(paths, kb_root)`: the checks over a built tree, manifest-agnostic in its own function signature. `paths` is the path list the caller independently holds, or `None` where it holds none — `kb_docgraph.build` supplies the documents it just wrote, which is what makes the tree-diff check a real comparison there, and `kb_util validate-build`, handed a tree and nothing that says what should have been built, passes `None` and the check reports itself inapplicable rather than passing. A tree compared against a walk of itself cannot fail, and a `PASS` line for it would claim a coverage the run does not have. |

**Nothing in the current toolchain produces the manifest `skeleton.derive`
still expects, and nothing calls for one.** `kb_docgraph` writes a tree
directly and emits no `kb_survey.manifest.Manifest`-shaped file; nothing adapts
its output into one, and no live call site asks for one — the driver's domain
partition is walked off the built tree instead (The Driver, below).
`kb_driver/ledger.py`'s `validate_build` adapter needs none and is latent for
its own reason, which its docstring gives: "no current driver row calls
this... latent until a caller needs it again."

Both modules are kept because they are stdlib-only, import no parser, and —
the acceptance
surface a future in-house replacement for `kb_docgraph` would still have to
satisfy, not because either sits on a live path from source to KB today.

### The Driver (`kb_driver/`)

`kb_driver/` sequences the stages, dispatches each seat, checks what came
back, and records the ledger, per SPEC.md's driver contract. Every brief it
composes is forbidden from naming a stage id or the record verb — enforced
by a lint over the templates. Its step table runs nine stages, in two halves:

- **The head**, where the build produces what it will then be judged on —
  `document-graph` (the tree, from the run's own `--source` list),
  `spine-seed` (`graph-init` over that tree), `claims-declared`,
  `claims-discovered` and `depends-attributed` (the three `kb_claimgraph`
  invocations, in that order).
- **The tail**, over the product — `phase-3a` (validation gate),
  `overview-drafted` (the overview document written) and `phase-5` (the review
  cycle over it) — after `start`, which opens the build.

**Where a stage boundary falls is decided by what must not be repeated, and the
step table is where that is checked.** A stage is as small as the most expensive
thing in it (SPEC.md, The Driver's Contract), so every row with
`steps.Step.spends_inference` stands immediately in front of its stage's ledger
row and nothing failable stands between the two — which is why the tail is two
stages rather than one, the draft and the review being a model call each. The
property is asserted over `steps.STEPS` in `test_kb_driver_steps.py` rather than
re-argued at each insertion, and a stage that grew a second such row fails there.
Its consequence for a resume is the discard rule: position comes from the ledger
alone, so an artifact no boundary accounts for is re-earned rather than adopted,
which costs one row's work and never more.

**Every head row is a tool row.** Each invokes a module and reads an exit code,
so none briefs a seat, none writes a brief, and the only barrier the head raises
is `spine-seed.runner-choice` — a repository carrying neither runner file cannot
be told where the include line goes, and no other row can answer that either. A
red front end is not a finding any seat addresses: every check either tool makes
compares one mechanical product against another, so a red one is a defect in the
tool or in its input, and the run stops.

**The head's order is forced at every step, and the stage boundaries are part
of the mechanism.** The seed refuses a `kb-root/` with no tree in it; the
declared pass refuses a `kb-root/` with no spine; discovery reads what the
declared pass left awaiting; stage D authors edges over the claims discovery
minted. The seed is a stage of its own rather than a row of `start` because its
preflight refuses a dirty worktree, and the tree the stage before it has just
written is exactly that — the `document-graph` boundary commit is what clears
it.

**No row is conditional, and a launch is the only thing a guard has to
recognise.** There is one kind of build and one kind of continuation
(SPEC.md, The Driver's Contract): an invocation that finds `start`
unrecorded is opening a build, and one that finds it recorded is resuming,
re-derived from the ledger every time (`Runner._recorded`) and configured
nowhere — `config.RunSection` carries no mode field, and `[run] build_mode`
is refused at load as a retired key (`config.RETIRED_RUN_KEYS`). What keeps `dg.build` from re-deriving the tree over
the documents a spine is stamped into is therefore the ledger — a recorded stage
is not re-walked — plus one guard for the case the ledger cannot speak to: a
`kb-root/` this build did not write. `pre.kb-root` is that guard, the last row of
`start` before the first write, reading `kb_util.kb_root_state`: `absent` and
`spine-only` proceed (`.index/` is derived space with no authored byte to lose),
`populated` is exit 14 naming the state and the tree. It sits in `start` rather
than beside `dg.build` because that is what makes it a *launch* guard: a resume
skips the whole stage, so a build interrupted between `dg.build` and its record
re-derives its own half-written tree rather than being refused entry to it.

**`--no-inference` drops rows and bounds nothing.** `steps.Step.spends_inference`
is the derived union of the two routes a row can cost a model call by — a
`seat` this driver dispatches to, or `spends_own_inference`, a model spawned
inside a tool it invokes — and `steps.applies` refuses every such row under the
flag. Every other row runs for real, every stage is walked, and every stage is
recorded, so the product is a real KB built without those rows rather than a
walk that stopped. **Derived from the rows, never from a stage id**: a stage
that gains or loses an inference-spending row moves the answer without an edit,
and a constant naming `claims-declared` would read identically today and be
silently wrong on the first insertion after it.

**The two routes stay distinguishable, and `--dry-run` is why.** That flag
swaps the `Invoker` for `replay.py` and reaches only what this driver
dispatches; `claims-discovered` and `depends-attributed` spend theirs inside
`kb_claimgraph`'s own `ask.SeatAsk` seam (`ask.ModelIdentifier` and
`ask.ModelSelector`), which no invoker of this driver replaces. So a build under
`--dry-run` alone still reaches those two for real wherever it walks them; the
walk that does not reach them is one resuming past their stages, which is why
the suite that drives the whole table under it enters against a ledger whose
head is already recorded, and why `kb-testing`'s `kb-driver-dryrun` recipe,
whose consumer is a launch, passes `--no-inference` beside it.

**What the build says about it is a boundary commit's body.** `run._stage_note`
supplies `advance-step`'s `--note` for a stage that lost rows and the empty
string for every other, naming the rows off `steps.inference_rows` rather than
counting them. It is the only possible statement: the KB itself cannot
distinguish a document a step never read from one it read and found nothing in,
so a reader counting awaiting documents learns nothing. Both mode flags are
rendered back into `config.invocation`, against the one bound that is not — a
resume line dropping `--no-inference` would spend the calls the build was told
to do without, and one dropping `--dry-run` would spend real ones.

**The record states the build and excuses nothing by itself.** `run._record_stage`
passes `--no-inference` through `ledger.record_stage` to `kb_util advance-step`
on **every** stage record, not only the ones that lost rows, because which
coverage units that excuses is `kb_pipeline`'s classification and not this
layer's to anticipate (The Build Pipeline's Coverage Checks, below).

| Module | Role |
|---|---|
| `steps.py` | The ordered step table — the only place that says what happens next. Rows carry the call unit, seat, template, declared artifacts, parses, barriers, and capped series. Stage order and cap values are imported from `kb_pipeline`, never restated. |
| `run.py` | The sequencer: stage iteration, the capped loops, resume, and exit selection. **It carries no resumption state.** `run.RUNNER_ATTRIBUTES` is the closed set of attributes a `Runner` may hold and states the criterion that admits one — the stage boundary: a resume re-enters at one, so a value a row writes is read stale by a later invocation exactly where its reader runs in a different stage, and an attribute is admissible only where the invocation fixes it, where every reader runs in the writer's own stage (`_seq`, `_rounds`), or where the walk re-derives it before any row reads it (`_recorded`, from the ledger). Closure is checked at construction and at every stage transition; which ground a name stands on is stated in the registry, because no check can read it. Anything else is re-derived at the point of use — `seed.graph-init` asks `kb_util.detected_runner` whether this repository carries a runner file, rather than taking `pre.preflight`'s answer two stages back, which a resume skipping `start` left at its constructor default and which is why `spine-seed.runner-choice` could not fire on a resume at all. A `--decide` answer no barrier asked for is reported rather than dropped, by `run._report_unconsumed` through `runlog.notify` — stderr and `run.log`, the values in the message text, `notify` being the stderr counterpart of `relay` so a notice about the invocation stays off the stream a session pastes from. Position comes from the ledger, and so does a findings loop's round count — not by reading one back, but because there is none to read: the ledger's entries are the stage vocabulary, a stage is recorded once, and its entry is written after its last round, so a stage the walk reaches has no round recorded and the loop opens at `run.FIRST_ROUND`. How many rounds it then ran is an attribute of that stage's own boundary commit (`run._stage_note`), which is the whole record of them. A findings filename still carries its round, so two rounds' findings are two files, but no counter is derived from a name — which is what keeps a file a dying process left from reading as a completed round and spending a fix cap of one on a crash. |
| `call.py` | This driver's **policy** over one call — retry, the one re-ask, contract validation, the absoluteness of every path the call carries in either direction, one call at a time, the spawn boundary (exit 15), and the three persistence routes. The mechanism under it — spawn, stream capture, silence watchdog, kill/reap, and the classification of how the call ended — is `kb_tools.inference`'s, shared with the tool path and holding none of the above (Module Inventory, above). The driver-persists route writes through `kb_survey.manifest.write_text_atomic` — a temp beside the target and a rename, the toolchain's one such writer rather than a second copy of it — because every reader of a declared artifact asks presence and non-emptiness and nothing else, so a file left half-written under its final name reads as work that finished. |
| `prompt_templates.py` + `prompt-templates/*.tmpl` | Prompt composition: strict slot fill in both directions, shared fragments injected by the composer, and the template lint (the enforcement mechanism SPEC.md's driver contract names). A slot is `@!slot-name!@` — the marker grammar `gen-defs.py` renders the agent definitions with, delimiters and name class alike, so one syntax serves both model-facing surfaces and a brace in a body is only a brace. The name is strict kebab (`prompt_templates.SLOT_NAME`), which is what lets a write op's slot be the op token verbatim: `@!insert-claim-entry!@` expands to an invocation ending in the string it is spelled with. **A namespace routes the slot, and the name never does**: `@!dyn.<name>!@` is filled from the caller's per-call data and from nowhere else, `@!<name>!@` from the composer's own sources — the constants pool, a fragment, a caller-selected alternative, the calling row — and from nowhere else. `prompt_templates.DYNAMIC_PREFIX` sits outside the name class rather than widening it, so a namespace is never mistaken for a name; the composer keys every mapping by the spelling, which is why the two cannot shadow one another and why each direction's failure names the mistake — a `dyn.` slot the caller supplied no value for, or a composer slot it supplied one for — instead of leaving an unfilled slot and an unused value to be read together. The templates are edited by agents holding no repository context, and a hole indistinguishable from prose invites the helpful edit that inlines the literal it stands for, which is the divergence the slot exists to prevent. Bodies are the prompt engineer's. **The directory layout is the declaration**: `prompt-templates/` holds exactly what something dispatches and `prompt-templates/fragments/` exactly what something splices into one, so a reader tells the two apart by where a file sits rather than by decoding its name. Every file under `fragments/` is registered in exactly one of two vocabularies — `FRAGMENT_SLOTS`, which the composer resolves because a template named the slot, and `ALTERNATIVE_SLOTS`, which registers per slot the choices a caller may name — and the correspondence is checked in both directions. An alternative resolves the way a fragment does, one level deep with its own slots lifted into the required set and nesting refused; what a caller hands in is the chosen name, or `None` where the slot registers the empty fill, never the chosen body's prose. `template_paths` walks the whole tree, fragments included, because the lint and the model-facing sweeps are questions about prose and a fragment's prose reaches a model as a template's does; nothing is made dispatchable by appearing there, a row naming only its own `template`. The templates are the shelf; a *composed* brief lands under the run directory's own `briefs/`, and the claim-graph asks' composed prompts under that build's workspace. |
| `envelope.py` | The formats read back from inference — the wave envelope (members, `gaps`, deviations), the `VERDICT` line, and the design document's `SCOPE` line — and the record toolkit they and `kb_claimgraph.ask` are built from: marker-pair extraction, the decode, the closed-and-total key check, and the **prose blocks beside the JSON**. Nothing a seat composes travels inside the JSON: structure cannot carry a backslash and words can, and a corpus of mathematics makes that the ordinary case rather than the edge — `\sigma` is not a JSON escape and fails loudly, `\beta` is one and decodes to a backspace, failing later and silently. Each composed value travels in a numbered `KB-DRIVER-PROSE` block taken verbatim between its delimiters, so no escape grammar exists in the transport at all; `members.status` is the literal `ok` or such a block, which moves where the phrase travels without closing it into a vocabulary. Every level declares which keys carry structure and which carry prose (`LEVELS`), and `check_levels` proves the two a partition of the key vocabulary at import. Parse only, never quality. `envelope_block` composes what `parse_envelope` reads, so a caller stating an envelope does not restate the format. |
| `barriers.py` / `baton.py` | The typed barrier registry (`barriers.REGISTRY` — one entry per `(stage, kind)` pair, each with its admissible answers) and the relay card every terminating invocation prints. Two of the exit codes are reachable both from a raised barrier and from a stage that failed mechanically, and only the first has a question or an answer; `BatonContext.pair` is what tells them apart, and the second gets a card that asks nothing and offers no `--decide` resume. **`BatonContext.mode` is the second such selector, and the mode-scoped ladders are why one is needed**: `0` is the code both modes reach, and a run's `0` is a finished build where a watch's is a live one whose recorded set just grew, so the watch table's own card for it directs another poll rather than a completion report — a code with no entry in a mode's table reads the shared one, and the mode a card was rendered under is the mode function's statement rather than a guess from the code (`watch.mode`). The stage-record refusal is on the other side of the same distinction: `kb_pipeline` exit 6 says a stage's own declared output was absent at record time, which no brief and no seat is party to, so it carries `baton.EXIT_COVERAGE` and a card that resumes once that output stands rather than `EXIT_CONTRACT`'s, whose subject is a dispatched call that could not produce its declared shape. Whichever card is rendered, the failing op's own report lines ride its detail (`ledger._failure_detail`) — a card naming only the return code is one an operator can only act on by re-running the stage by hand. |
| `ledger.py` | Subprocess adapter over the sanctioned `kb_util` ops — the ledger verbs, the validator (`validate_build`; latent, per its own docstring — no current row calls it, see kb_survey, Retained, above), the metadata write ops `write_op` fronts — and the `kb-refresh` / `kb-verify` targets, plus the one place a tool exit becomes a driver exit. |
| `config.py` / `runlog.py` / `watch.py` | The run overlay (TOML), the run directory and its logs, and the poll-a-backgrounded-run mode. |
| `replay.py` | The `--dry-run` invoker: synthetic stream-json through the real capture and parse path. Installed rather than test-only, so a consumer can smoke-test the state machine without spending inference on the calls this driver dispatches. It replaces those and no others, so the walk it is honest alone over is one resuming past the two claim-graph rows, whose model it cannot reach. |

**CLI surface** — `PYTHONPATH=.claude/agents python3 -m kb_tools.kb_driver <mode>`,
from the consuming repo's root:

```sh
# Run the build from wherever the ledger says it is.
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_driver run \
    --source <path> [--source <path> ...] \
    [--permission-mode <mode>] [--config <path.toml>] \
    [--decide <stage>.<kind>=<answer>[:<note>]] ... \
    [--run-dir <dir>] [--no-inference] [--dry-run] [--through <stage>]

# Poll a backgrounded run until it advances, ends, or the timeout expires.
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_driver watch [--run-dir <dir>] [--timeout <s>] [--poll <s>]
```

**A launch composes nothing.** The one `[run]` field with no default —
`sources` — is a flag, so a build starts from a command line and no authored
file; every other field of `config.DriverConfig` has a default. Six of them —
`permission_mode`, the barrier answers, the run directory, the two mode flags
`no_inference` and `dry_run`, and the walk bound `through` — carry a flag as well; the rest are
reachable only through `--config`, which stays supported for a run that needs
one. `[run] bibliography` is one of those: the document graph
takes bibliographies and the launch line carries only sources, so where the key
names none `run.bibliographies_beside` passes **every** `.bib` sitting in the
directories the sources sit in, sorted — the reader merges them and renders only
cited entries, so several is an ordinary corpus and none is too, and the key
exists for a run that wants the set narrowed to one file rather than for an
ambiguity to be resolved. Where both are given the flag wins for the field it names,
which is the precedence `--decide` already has over the config's `[barriers.*]`
tables — one rule for both doors — and a repeated `--source` replaces the
configured list rather than extending it. A flag's value is validated by the
key's own config check, so both doors refuse in the same words at load
(exit 13). Every relay card's resume line is rendered from what the run was
actually given (`config.invocation`), so a flags-only run is handed its own
flags back rather than a `--config` it never used.

**The bound is the exception the resume line makes, and deliberately.**
`--through` bounds one invocation rather than specifying the build, so
`config.invocation` does not render it: a card that handed it back would name a
run that stops in the same place forever, and resuming past a bound is what
resuming is for. It takes a stage id or the stage's own display name —
`phase-3a` and `validation gate` name one stage — resolved to an id at load by
`kb_pipeline.resolve_stage`, so nothing downstream deals in two spellings and an
unknown name is refused (exit 13) carrying the whole vocabulary in walk order.
Reaching it is exit 18 (`baton.EXIT_BOUNDED`), whose card resumes without the
bound. It composes with both mode flags and none of the three implies another;
the refusal that once paired `--through` with `--no-inference` is gone with the
bound that made some stages unreachable.

**The permission mode is defaulted and announced, not asked for.**
`config.DEFAULT_PERMISSION_MODE` is `bypassPermissions` — the only mode a run
has been driven to completion under, and the one that cannot wedge a headless
`claude` on a prompt nobody can answer (SPEC.md, The Driver's Contract); what a
build may do is bounded by the environment it runs in rather than by this
field. `--permission-mode` narrows it for a run that wants it narrowed. The
announcement SPEC.md requires is an INFO line in `cli._mode_run`, beside `run
started` and before the sequencer is entered, carrying the effective mode and
the flag **in the message text** rather than the record's context: the console
tee prints messages alone, so a value in the context would reach the run log
and not the operator.

`--decide` is repeatable, takes precedence over the config's `[barriers.*]`
tables for its pair, and is consumed once per process; an answer whose
barrier was never raised is reported rather than silently dropped.
`--run-dir` overrides `[log] run_dir` and should point **outside** the
consuming repo, because a restage wipes `.claude-temp/` and the run
directory is the evidence. It is settled at load like every other flag, so
`config.log.run_dir` is the effective parent and `config.invocation` renders
it back wherever it is not the default — both commands a card offers name it,
the resume through the invocation and the watch through a field of its own,
since a run whose evidence sits elsewhere is one a bare `watch` reads a stale
`LATEST` for and reports as terminated.

**A run's report is written on every exit, the ones nobody planned included.**
`run.write_report` puts `cadence.jsonl` and `exit.json` in the run directory on
the way out of every ending — a barrier, a red gate, a boundary check, a
terminating signal (`runlog.terminating_signals`), an exception no handler
names — because `exit.json` is the one channel a backgrounded session has for
learning how a run ended, and the endings whose own card calls that directory
the bug report were the ones leaving none in it. A signal exits `128 + n`, the
shell's convention rather than a rung of either ladder: nothing in the build
decided that ending, so the fallback card is the true one for it and a
borrowed code would state a verdict no stage reached.

**The charter is optional.** `pre.charter` resolves once whether one stands at
`[run] charter_file`, and that one answer reaches every consumer: the `start`
record names a charter only where there is one. It reaches the build through
`start-build --charter` and through no brief slot at all — the briefs that
quoted it went with the front end that had them — so it is outside the
path-slot vocabulary below rather than an optional member of it. A build with
no charter is an ordinary build whose scope is the sources it was given.

**Every path a brief hands a seat is absolute and is there, and it is the same
rule the declared artifacts already take.** `call.CallRequest`'s `outputs` are
absolute because whose cwd a relative one resolves against is an ambiguity a
contract check must not have; a path travelling the other way, in a slot, is
the same ambiguity asked of a model instead of a check. `steps.PATH_SLOTS`
names every slot of the driver's vocabulary that carries one, and
`call.Caller._check` refuses the call before it is made — relative, absent, or
carrying the named absence where the slot admits none. Measured: `phase-5`'s
review, handed `kb-root/README.md`, searched for a directory of that name
rather than resolving it, reviewed a different repository's knowledge base and
returned three critical findings about it, stopping a build at seven of the
then eight stages. **The remedy is the removed ambiguity and not a prohibition on the
consequence** — a brief instructing a seat not to look outside the tree is
prose against a model above temperature zero, where a path with one legal
reading is a fact, and a precondition fires before a model is involved at all.

**Requiredness is the slot's, and it decides one thing: whether the named
absence is an answer.** `steps.REQUIRED_PATH_SLOTS` holds the three whose
subject an earlier stage has already produced — `kb-root`, the tree the head
wrote; `readme-path`, what `ov.docs` assembles in the stage immediately before
the review; `conventions-path`, what `phase-3a`'s `stamp_readiness_docs` seeds
two stages earlier — so an absent one is that stage having failed quietly.
`steps.OPTIONAL_PATH_SLOTS` holds `remediation-source-path`, which a first
review round genuinely has none of and which renders as `steps.NOTHING`.
Existence is asked of both: a dangling path is never the right thing to state,
and where a build may not have the subject the slot renders the absence rather
than a path to nothing. **The inputs a build may legitimately lack are not in
this vocabulary at all** — a bibliography (SPEC.md point 10) and the charter
reach `kb_docgraph`'s and `start-build`'s command lines, not a brief.

The vocabulary is keyed by slot rather than by row, so it reaches the slots of
a template no row dispatches yet, and `run.Runner._brief_path` is its one
composer — `_repo_relative` is the log line's, the barrier record's and the
relay card's, for a reader already standing in the repository.

Two path-bearing values stay relative, each with its base stated where it is
read. `steps.CONSTANT_SLOTS`' `layout-paths` is repo-relative and
`fragments/scratch-layout.tmpl` says so in the line above it. The document path
in `kb_claimgraph.ask`'s two asks (`@!dyn.document!@`, and the `stated in`
clause of a claim line) is kb-root-relative and names the document whose body
is inlined in the same prompt: it is a citation, not a path to open, and those
asks show one document deliberately.

**The driver runs the tree's production and authors none of it.** The tree is
`kb_docgraph`'s whole product, computed from the sources the run was launched
with; what the driver contributes is the invocation, the exit-code reading and
the boundary commit. No seat is dispatched, no invariants list, navigation spec
or acceptance criteria is written by anybody, and no row proposes a
decomposition — SPEC.md's "the build derives the tree and grades none of it"
describes this shape exactly, with the judgement seam living inside
`kb_docgraph.judge` rather than in a row here (The Derived Skeleton, above).

**Nothing in the tail repairs a gate.** `phase-3a` runs `kb-refresh` then
`kb-verify` and either records or stops (`run._p3a_gate`): each of the three
verifiers compares one mechanically-produced artifact against another, so a red
one is a defect in a tool or in what was authored, and a wave sent to rewrite
the KB over it would be repairing mechanical output a mechanism checks. The
verifier's whole report reaches the run log at INFO; the card names the target
that failed and carries the lines of its report that name the failure. The one
capped cycle left is `phase-5`'s, over documents a seat wrote.

**That cycle turns on `critical` and on nothing else.** `run._review_loop`'s
condition is `verdict.critical > 0`, so a review clean of critical findings
passes on its first pass and spends no fix round, and a warning and a note travel
past the gate unrepaired (SPEC.md, The Driver's Contract). The condition that
summed `critical + warning` is what stopped a live build of arXiv `2609.10318v1`
at seven of the then eight stages: its reviewer returned `critical=0 warning=1 note=0`
and then `critical=0 warning=1 note=2`, never once calling anything critical,
and the lone warning spent the fix round and then escalated. **The cap
keeps its work all the same**: what `PHASE_5_FIX_CAP` bounds is the
review→fix→re-review loop a *critical* finding opens, and a reviewer whose
instructions make problems inexhaustible can reopen that indefinitely — the scar
CONVENTIONS.md records under "a `kb_claimgraph` stage never exits on a model's
opinion". It is still one, so a critical finding buys one repair attempt and then
`phase-5.cap-exhausted`, whose `stop` answer ends the run at the barrier with the
stage unrecorded, as every stopping answer in `barriers.REGISTRY` does.

**A fix round is judged where it composes, not at the boundary.**
`run._assemble_overview` holds both the bytes the round composed and the bytes
standing in `README.md` when it composed them, and a round that produces the
second answered its review with nothing: exit 17, the round refused before the
re-review that would spend the cap on a document nobody moved. The boundary
cannot make that call — `_check_meta_docs` runs once, after the last round, by
which point the overview differs from `HEAD` because the draft created it, and a
no-op fix and a real one are the same observation from there. The draft itself is
exempt: re-composing what stands is what `ov.docs` does on a resume past a lost
boundary, where the answer on disk is work no boundary accounts for and earning
it again byte for byte is the discard working.

**What stopped halting is reported, by `run._report_round`.** One line per round,
at INFO, carrying `critical`, `warning` and `note` and naming the findings
artifact — and stated in both directions, the zero form included, because a build
silent about a reviewer's warnings reads exactly like one whose reviewer raised
none. That is the shape `kb_docgraph.build._declarations` and
`kb_claimgraph.build`'s `stage-B-unreadable` line already take. The whole
statement is in the message text rather than in the record's context, for the
same reason the permission-mode announcement carries its value in its words: the
console tee prints messages alone, so a count in the context would reach the run
log and not the operator.

**The driver runs the claim graph's production and grades none of it.** Minting
a `clm-`/`exp-`/`sup-` id, writing a `depends` edge and placing a Tier-2 marker
are `kb_claimgraph`'s, across the three stages that invoke it; every value any
of them writes is the unscored literal, which is the state SPEC.md's "the build
authors the claim graph and grades none of it" requires and the state
`kb-claim-scorer` later reads. No row renders a leaf's prose: leaf bodies are
computed from their own extents by `kb_docgraph`, and no stage asks a seat to
read a source extent or to transcribe one.

**The self-graded-work guard rides a property no head row needs.**
`run._check_minted_grades` guards rows whose *seat* authors register entries;
the head's minting is a tool's, checked by that tool's own gate and by
`depends-attributed`'s verify coverage, so no head row declares
`writes_register` either.

**The self-graded-work guard is dormant, not removed.** `run._check_minted_grades`
still runs behind every row declaring `steps.Step.writes_register` — the
mechanism behind SPEC.md's "the build authors the claim graph and grades none
of it" — but no row in the current table sets that property, the minting rows
it used to guard (`phase-2.5`, `phase-2.6`) being gone with the rest of the
front end. `run.py`'s own module docstring says so plainly: "no row in the
current table declares it, but the guard stands ready for one that does."

**The entry-point word budget no longer exists as a check.**
`steps.ENTRY_POINT_WORD_BUDGET`, `steps.ENTRY_POINT_WORD_CEILING` and
`run._check_entry_point_size` belonged to `p3.entry-point` alone and are gone
with it. `kb_docgraph` writes `entry-point.md` — a bare per-volume list — as
part of every build, but enforces no size budget over it: SPEC.md's "the entry
point is the one document with a size budget" currently names a document
nothing in the toolchain sizes.

Two suites cover the walk, and they enter from the two states an invocation can
find. `kb_tools/tests/test_kb_driver_dryrun.py` drives the tail through the
shipped entry point against a throwaway consumer whose tree is already built and
green and whose head stages are already recorded — a resume — with `replay.py`
standing in for the model. `kb_tools/tests/test_kb_driver_head.py` drives the
head against a consumer holding its LaTeX sources and no `kb-root/` at all — a
launch, made the way the launch line specifies one — and stops before
`claims-discovered`,
because that stage's model is not the driver's to replace. That is the same
place `--no-inference` stops such a build, arrived at by the same property
rather than by agreement.

### The Derived Index (`<kb-root>/.index/`)

`refresh` materializes a fixed set of JSONL files, one record per line,
each sorted by a per-file key so a new node or edge is a single inserted
`git diff` line. The record shapes and field orders are `kb_index_lib`'s
emitters — their own definition, with no prose second view — and the
freshness contract is `verify_kb_metadata`'s dry-run diff. This section is
the map.

| File | Builder | Sort key | Holds |
|---|---|---|---|
| `claims.jsonl` | `build_claims_records` | `(node_type, id)` | every graph node, type-tagged by `node_type` over the node-kind vocabulary, `kb_schema.NODE_KINDS` |
| `depends-on.jsonl` | `build_depends_on_records` | `(source, target, context)`, a null context sorting as `""` | every edge of all four classes (SPEC.md, Claim-Graph Nodes and Edges); all eight keys on every record, only the class's own ones non-null |
| `strengthen-by.jsonl` | `build_strengthen_by_records` | `(claim_id, item_idx)` | a claim's open-work bullets, verbatim plus the ids they mention — no edge, traversed by nothing |
| `cites.jsonl` | `build_cites_records` | `(claim_id, leaf_path)` | the leaf→claim hosting edges |
| `supported-by.jsonl` | `build_supported_by_records` | `(claim_id, sup_id)` | the reverse view of the `supports` edges, carrying each one's realized lift — a convenience index, never consulted for solidity |
| `subtree-aggregates.jsonl` | `build_subtree_aggregate_records` | `node_path` | per index / entry-point roll-ups, from the same `compute_subtree_aggregates` the frontmatter refresh and the verify check read |

`depends-on.jsonl` is the whole edge substrate: `supported-by.jsonl` is a
reverse view of records already in it, `cites.jsonl` and
`strengthen-by.jsonl` carry no `DependsOnEdge` at all, and `kb_graph` takes
`depends-on.jsonl` as its only edge source. Solidity is not computed from
this tree — refresh and verify both call `compute_solidity_full` against the
parsed authored state, upstream of every file here. Per-edge integrity is
checked in `verify_kb_metadata` line by line and relation-aware — for each
the admissible source and target node types, the `target_kind` match, and
which of `strength` / `fraction` must be null and which must carry a value
in range. A `rests-on` edge's row of that check is claim → work, `strength`
null, `fraction` in [0, 1] or the pending literal. A `supports` edge is additionally checked at its two authored
ends: `FanOutRecord` pairs the register's staged fraction against the
hosting leaf's declared one, and a disagreement is not refresh-fixable,
both ends being authored. No such pairing exists for any other class.

### The Claim-Graph Sheet (`<kb-root>/claim-graph.svg`)

One derived artifact more, and derived from the table above rather than from
the authored layer: `kb_graph` reads `.index/` and draws it. It is kept fresh
the way `.index/` itself is — **`refresh` mints it, `verify` checks it** — and
that is one write site and one check site rather than a hook on every
invocation that moves the graph, because `refresh` is what rewrites the index
the sheet is a view of.

`refresh` renders it as its last phase, after the index is on disk, through
`kb_graph.ops.render`. `verify` composes the sheet the on-disk index would
render, through `kb_graph.ops.compose`, and byte-compares; a mismatch and an
absent sheet are one refresh-fixable failure, beside the index freshness
failure it reads like. The check is skipped on two conditions, neither of them
about the graph: an index already reported missing or malformed has no sheet to
render against, and an `--index-dir` pointing away from the KB's own is a run
asking about a synthetic index rather than about this KB's sheet.

**The render's only precondition is that the index loads.** No property of the
graph is one — a cycle, a ghost id, zero edges, one node, zero nodes: each
draws and exits 0. The failure that rules out is dependency-order — *no picture
until something else has been done* — and it is why the render sits inside
`refresh` rather than after the `kb-refresh`-then-`kb-verify` gate a
claim-graph pass ends on: a build that halts at that gate is the one a reader
most needs a current picture of.

`verify` stays read-only across this: `compose` returns bytes, and the
directory it composes against is the KB root because that is where the sheet is
read from and what every node hyperlink in it resolves relative to.

The comparison rests on determinism (SPEC.md, Corpus Invariants) — same graph,
byte-identical sheet — which the gate makes load-bearing. Perceptual locality
is *not* part of it: a small graph change may rearrange the sheet freely.

## The Agent Set

The toolchain is the mechanism; the agents are the workflow (the rule that
editing must go through them is SPEC.md, Agent-Mediated Editing). Roles, by
lifecycle stage:

**Create** — `kb_docgraph` (The Document Graph (`kb_docgraph/`), above) turns a
canonical corpus into the KB's tree, and `kb_claimgraph` (The Claim Graph
(`kb_claimgraph/`), above) authors the claim-graph spine over it: two mechanical
CLIs, not agents, with no seat dispatched at any point in either. `kb_driver`
runs both — as the five stages of the build's head — and then sequences the
validation and meta-documentation tail over what they produced: nine stages,
`start` through `phase-5` (SPEC.md, The Driver's Contract; The Driver, above).
The orchestrator is a program, not a seat: it walks a static step table,
dispatches each row to the seat that row names, and stops at a barrier rather
than asking. `/kb-build` prints the command line that starts it and does
nothing else. **No stage remediates a gate's findings**: `phase-3a` runs the
verifiers and either records or stops, because what each of them faults is
mechanically-produced content a mechanism already checks. One pair of seats is
dispatched in the whole run:
- `tech-writer` + `tech-writer-reviewer` — write `README.md` at
  `overview-drafted` and review it, with `CONVENTIONS.md`, at `phase-5`.

Nothing in the build reviews KB content. Adversarial review of leaf fidelity
and of navigability was a hedge against agent-authored leaves; the head is
mechanical end to end, so its output is checked by the verifiers rather than
read by a seat.

No current seat reads the source corpus directly or reports the
notation/concepts/anomalies a walk over its markup would not — `kb-latex-specialist`,
the source specialist this toolchain used to ship, is deleted along with the
survey pipeline it served (kb_survey, Retained, above).

**Use** — `kb-docent` (read-only) guides navigation through the topography
graph, manages session state, and executes topic switches. Loaded via the
`/kb-start` and `/kb-next` commands; it is the sanctioned way to browse the
KB.

**Maintain** — `kb-maintainer` is the write side: add/edit leaves, wire leaf
frontmatter and claim-graph ids/edges, migrate finished work into canonical
leaves, and run the refresh→verify loop to green. Parallel-safe by file
ownership.

**Score the graph** — `kb-claim-scorer` supplies the inputs the tooling cannot
derive: hand-authored `confidence` / `quality` (local rigor), a `supports`
edge's on-point fraction, and the off-graph endcap's two — an external work's
`strength` and a `rests-on` pairing's applicability, which reach the KB through
`set-work-strength` and `set-applicability`. Supplying that pair is what clears
a citing claim's `solidity` from `*pending*`: a positive applicability puts the
work's `strength` into the claim's dependency `min`, a zero one takes the
pairing out of it, and either left unscored keeps the claim — and everything
downstream of it — pending. It reads a leaf and takes its
dependencies as given; everything downstream (`solidity`, bands, aggregates) is
then mechanical. **This is not a build stage** and never was one this seat ran
inside: a build used to mint and attribute at `*pending*` and stop there
(SPEC.md, The Driver's Contract), leaving scoring to enter separately through
the maintenance path's write ops — `set-rigor` and `set-on-point-fraction` —
which is still how this seat's output lands. What has changed is upstream of
it: no *driver* stage mints or attributes anything for it to score (The Driver,
above). `kb_claimgraph` does, mechanically and undispatched, for the claims an
author marked — every one of them at `*pending*`, which is exactly the state
this seat reads.

**Extend the toolchain** — `python-coder` for changes to `kb_tools/` itself
(stdlib-only per SPEC.md's Corpus Invariants, but for the one external
`pandoc` dependency).

## Query Surface

`kb_cmd/index.py` loads the JSONL once per process into plain dataclasses
and exposes forward/inverse dependency, open-work, citation, subtree,
filter, and lookup queries. It is a normal package —
`from kb_tools import kb_cmd` (or `from kb_tools.kb_cmd import load`) for
programmatic access, or run the CLI as `python -m kb_tools.kb_cmd`:

```sh
PYTHONPATH=<repo> python3 -m kb_tools.kb_cmd <cmd>
# deps <id> [-i] | gated-on <id> | cited-by <id> | solidity-below <n>
# weak-points | subtree <path> | show <id> | stats     (--json for jq)
```

**The load drops nothing.** Every `node_type` discriminator in
`kb_schema.NODE_KINDS` has its own dataclass, but for the `invariant`/`axiom`
pair, which share `FrameworkNode` — so a support's `quality`/`solidity`, an
experiment's `status` and an external work's `strength` survive the load, and
none is given the framework pair's bedrock semantics. The builder table is
proven total over the vocabulary at import (`kb_schema.kind_table`), and a
discriminator outside it is refused naming the unknown value and the known set,
never typed as framework. A support's `build_band` is **not**
on its record (the emitter writes `quality` and `solidity` only): the query
side derives it through `kb_index_lib.derive_build_band`, the same mapping
the build side bands a claim with. A `DependsOnEdge` carries the record's
`relation`, `strength` and `fraction`, so a `supports` edge is
distinguishable from a `depends` edge and a pending fraction arrives as
`kb_schema.PENDING_LITERAL` — compare against that constant, never a
typed-out string.

**`stats` is a census.** It carries one node count per kind in
`kb_schema.NODE_KINDS`, in that vocabulary's own order and keyed by
`kb_schema.node_kind_plural`, and they partition `claims.jsonl`: every record
is counted in exactly one bucket, and the buckets sum to the file's record
count. A bucket is present whether or not the KB populates it, and a kind added
to the vocabulary reaches this census — and the `[index]` breakdown
`kb-verify` prints — by that alone. The census used to name its buckets by
hand: the total counted the external works and no breakdown ever named them.

At KB scales in the low hundreds of nodes, full load + query is sub-10 ms
cold; no caching layer.

## Runner Targets and the Build Ledger

A consuming project's runner gains the KB targets by **include, not copy
into the runner file**: exactly one installed line pulls in a fragment that
ships with this toolchain, at its installed location under `.claude/agents/`
— `-include .claude/agents/kb_tools/runner-snippets/kb.mk` in a Makefile,
or `import? '.claude/agents/kb_tools/runner-snippets/kb.just'` in a
justfile (`import?` requires just ≥ 1.33). The non-fatal include forms are
deliberate: a project whose `.claude/agents/` has not been installed (or
has been removed) degrades to missing KB targets, never a broken runner.
The line is managed mechanically by the installer, run from the consumer
root:

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util install-targets
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util uninstall-targets
```

(An existing justfile wins over a Makefile; `--runner just|make` forces the
choice, and on install creates the runner file when it doesn't exist. `--runner`
is declared on `graph-init`, `install-targets` and `uninstall-targets`
and on no other op, so naming it elsewhere is a usage error rather than a
silently ignored flag. The op that seeds parts company with the two that
only install here: given a repo carrying neither runner file and no
`--runner`, a seed creates the default runner's file —
`kb_util.DEFAULT_RUNNER`, a Makefile today — while `install-targets`
refuses.)

### Initialising the Claim-Graph Spine (`graph-init`)

The whole claim-graph metadata seed is one mechanical command, run from
anywhere inside the consuming repo:

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util graph-init [--runner just|make]
```

**The document tree is the precondition, not the obstacle.** `kb_docgraph`
(The Document Graph, above) writes the tree first and this verb initialises
claim-graph metadata over it, so a `kb-root/` with no tree in it is what has
nothing to attach to. `kb_util.document_tree_present` is the test —
`entry-point.md` at the KB root with at least one volume directory beside it,
the shape that front end writes — and it is a precondition check rather than
the conformance gate, which reads the whole of SPEC.md's Document-Tree
Contract and belongs to the claim-graph builder. Its absence prints an
advisement naming `kb_docgraph`'s own command line and exits **3**
(`kb_util.EXIT_NO_DOCUMENT_TREE`) having written nothing: the environment is
sound, so this is neither a preflight failure nor a repair anyone performs
here, and its own code is what routes a caller to the front end instead.

It is self-guarding: it opens by running the preflight suite (below) — same
code, same report lines, printed as part of its own output — and any
`FAIL` stops it with nothing seeded (exit 2). Ordering the two verbs in
prose is therefore unnecessary; an agent that skips `preflight` entirely
still cannot mis-seed.

Seeding creates `kb-root/.index/` and installs the runner include line,
then runs refresh and the two `kb-verify` gates through the modules those
targets wrap — the targets do not exist until the include line is in.
Every step no-ops when its work is already done, so re-running is safe and
a fully-initialised spine reports itself as such; the exit code is 1 if
refresh or verify fails. Which runner file the include line lands in is
decided by the probe order stated above.

**The fused `kb-verify` gate excludes one check, named and scoped to this
verb alone.** SPEC.md's Document-Tree Contract point 14 has `kb_docgraph`
produce no frontmatter block, so `verify_kb_metadata`'s frontmatter-presence
check would fail by construction on the tree this seed runs over, before any
claim-graph stage has stamped one — an assertion about phase 2's output,
asked before phase 2 runs. `graph_init_kb` calls
`verify_kb_metadata.main` with `--skip-frontmatter-presence`, an explicit,
single-named flag bound to that one check; every other metadata check stays
live, because each is keyed on a classified document (`kind: leaf`)
and nothing classifies as one on a tree with no frontmatter
at all, so each passes vacuously rather than needing its own exclusion. Every
other caller of `verify_kb_metadata` — the runner's `kb-verify` target,
`phase-3a`'s gate — keeps frontmatter-presence as a real check; the exclusion
narrows what this one fused pass claims, from "this KB is complete" to "this
spine is correctly installed over the tree that is there," and is not a
weakening of the check itself.

The seed is not undoable and needs none: it stands alone, its work stays
wherever it got to, and re-running it is a no-op over what already landed. The
boundary commit that follows it is what makes it durable — the seed's own
writes are uncommitted until the `spine-seed` record sweeps them up (Driving
the Build Ledger, below).

**The seed writes no format contract, and a KB holds no copy of one.** The
write API renders every metadata byte, so there is nothing a per-build
contract copy could say that a seat is free to act on. A project's own scope
is pinned in `<kb-root>/CLAUDE.md` instead — the file carrying the rest of
the KB's per-project orientation; who writes it and when is in SPEC.md,
Project Scoping. Nothing gates on a file's mere presence.

Because preflight gates on a clean worktree, a seed's own output must be
committed before `graph-init` is run again in the same repo.

The repo root is always derived from the working directory — no tool here
takes a root override; run from inside the target repo.

### Checking the Environment (`preflight`)

The mechanical form of a build's environment checks, one report line per
item in a uniform `[preflight] STATUS name detail` format:

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util preflight
```

`PASS`/`FAIL` items gate (exit 0 iff none failed), and every `FAIL` names
its restoring action: the resolved git root, both docent commands under
`.claude/commands/`, `.claude-temp/`'s gitignore coverage, and a clean
worktree. `FACT` items never gate: the kb-root tri-state (`absent` |
`spine-only` | `populated`, named once as `kb_util.KB_ROOT_STATES`) — reported
plainly and acted on nowhere here, the one place it decides anything being the
driver's `pre.kb-root` launch guard (The Driver, above) — the detected runner
file, and whether `.claude-temp/kb-build/` exists.

Creating `.claude-temp/` when it is missing is the only side effect and is
not a check; the worktree is read before that mkdir, so preflight can never
fail on its own writes. It never edits gitignore rules and never commits —
those remedies are named, not performed. **A non-empty `.claude-temp/` is
not evidence of a prior build**: a consumer may have filled it with
unrelated scratch and never run one. Only `kb-root/`, `kb-build:`-prefixed
commits, and `.claude-temp/kb-build/` indicate build state.

### The Build's Opening Gate (`show-confirmation`)

One read in front of the gate, and **one route through it**: `show-confirmation`
renders the whole confirmation and writes nothing, and what the user's answer
releases is `start-build`, which records the `start` boundary (Driving the
Build Ledger, below). The spine seed is not part of opening a build — it is
`spine-seed`'s, through `graph-init`, two stages later and over the tree
`document-graph` has by then written. No op performs the two together. That
matters because a route stated twice is a route that drifts: `kb_driver` walks
this one, row for row (`pre.*` → `start.record` → `dg.*` → `seed.*`, The
Driver, above), and a second door it never walked would be the one nothing
exercised.

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util \
    show-confirmation --source <path> [--source <path> ...] [--charter-values <file>]
```

**`show-confirmation` prints the message, not material for one.** One
block carrying the resolved root, each source resolved, the KB root, the
kb-root tri-state with what it means for an invocation opening a build (a
`populated` tree is one such an invocation is refused over, stated here where a
reader can still act on it rather than met as a refusal on the next command; a
build already under way resumes into the same tree, and the checklist below is
what says which of the two this repository is in), the charter quoted back a line at a time under
`[charter]`, the stage checklist, which stages stop for the user (read off
`Stage.user_gate`, so a gate that moves takes the sentence with it), every
per-project fact still unsettled with the default that holds if it goes
unanswered, and the `preflight` report. A
caller relays it unchanged; selecting from it or re-ordering it is making
the judgements the op exists to remove. Exit **0** there is something to
confirm, **1** a blocking item stands in the way — a preflight `FAIL`, or a
source that is not there — **2** the root will not resolve. Its only write
anywhere is the scratch directory `preflight` creates.

**The charter this read quotes back travels as text, in a values file.**
`--charter-values` is the metadata ops' transport under a name of its own —
`[[entry]]` tables with prose in a `'''` literal block, so a charter's
backslashes and quotes need no escape grammar and never pass through a
shell — over a vocabulary of one key:

```toml
[[entry]]
charter = '''
<the charter's own words>
'''
```

Exactly one `[[entry]]` is read, one build having one charter; a second
entry, a second key, or a charter saying nothing is refused naming the
offence, and nothing is written. This flag carries *words*, and is the one
that writes nothing: the charter this read quotes back is quoted for the user
to check, and what puts it on disk is whoever answers. `start-build`'s
`--charter` is the other end — a path to a charter that already stands, and
`kb_pipeline.CHARTER_RELPATH` is where the build keeps one (tracked, at the
repo root, outside both the scratch tree a restage wipes and the `kb-root/`
refresh and the verifiers walk). Write the values file itself under
`.claude-temp/`: it is the call's input, and an uncommitted file anywhere
else fails the clean-worktree check the seed runs.

**A charter is not required to open a build, and the absence is recorded
rather than merely permitted.** SPEC.md (The Driver's Contract) makes the
charter optional — a build given none runs on the sources it was given — so
`start-build` takes `--charter` optionally and refuses nothing when it is
left off. What it does not do is record silently: the `start` boundary's body
names the charter where one stands and states
`kb_pipeline.NO_CHARTER_BODY` where none does. An empty body would be
indistinguishable from a caller that dropped the argument, and the boundary
commit is the only durable place the two can be told apart — so a charter
written somewhere the build never looked is found by reading the ledger
instead of being inferred from silence.

### Driving the Build Ledger (`show-status` / `start-build` / `advance-step` / `show-stage-status`)

The build pipeline's state machine — four top-level ops, each carrying only
the options it takes; the first and last read, the two in the middle record.
Three of the four are `kb_driver`'s own: `ledger.py` drives `show-status`,
`start-build` and `advance-step` as its subprocess adapter (Module Inventory,
above). `show-stage-status` answers no row in the driver at all — it is a
diagnostic read for whoever is inspecting the ledger from outside a run,
since no coordinator process consumes it (SPEC.md, The Driver's Contract):

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util \
    show-status
    start-build       [--charter <repo-relative-path>]
    advance-step      --stage <stage-id> [--note <text>]
    show-stage-status [--stage <stage-id>]
```

`--charter` is `start-build`'s alone, and is optional: a build carrying no
charter records without one, and the boundary commit's body — whose whole
content here is what the build was told about its charter — then carries
`kb_pipeline.NO_CHARTER_BODY` rather than standing empty (The Build's Opening
Gate, above, for why the absence is stated). The coverage unit tells
the three conditions apart: a *read* holds no record argument and reports the
unit missing for that reason (below), a record naming no charter is a vacuous
unit rather than a failed one, and a record naming a path is checked on disk.
`--stage` is declared on the two ops
that take a stage — required by `advance-step`, optional on the coverage
read below — and on no other, so a record command missing its argument is
a usage error rather than a run that proceeds on a default.

**The stage table lives in `kb_pipeline.STAGES` and only there** — ids,
display names, and each stage's **action card**. No agent definition
enumerates stages or their obligations; both are read out of the render.

Every render is one block, in this order:

```
[kb-build] status: <state> (<n> of <len(STAGES)> stages recorded)
[kb-build] note: ...                     (only when kb-root/ is unseeded)
[x] start      build started
[*] phase-3a   validation gate
[ ] phase-5    meta-documentation
[card] next action — phase-3a (validation gate):
[card] · <obligation>
[kb-build] next: ...                     (baton; start-build only)
[kb-build] Any message to the user opens with the checklist above, verbatim, in the message body.
```

The three `[x]`/`[*]`/`[ ]` lines above are the **checklist block**, shown
truncated: the real render carries one line per `kb_pipeline.STAGES` entry.
It is contiguous and is the only thing matching
`^\[([x* ])\] (\S+)` — `[x]` recorded, `[*]` in progress (the first
unrecorded stage, once the build has started), `[ ]` undone. Every other
line carries a word in its brackets, so a parser lifts the checklist
without knowing the rest.

The **action card** is the current stage's procedural obligations,
rendered at the moment of action: the `start` card before the build
starts, the `[*]` stage's card while it runs, none once complete. Card and
`[*]` marker come from one `current_stage()` decision and so cannot
disagree. Refusals render it too — a caller that asked for the wrong stage
still leaves knowing what the right one requires.

Card obligations that would go stale as strings are **generated**: the
record step expands to the full sanctioned command that records the
card's own stage — `advance-step --stage <id>`, or `start-build [--charter
<path>]` on the `start` card, the one boundary that takes a charter path
instead of a stage id — so neither op appears anywhere else an agent reads;
phase-3a's gate line renders through the runner detection, so a justfile
consumer is told `just kb-refresh` and a Makefile consumer `make
kb-refresh` — `phase-3a` is the only verify gate the current stage table
carries; and every loop-cap line renders from the cap constants in
`kb_pipeline.py`, so a card can never advertise a cap the run does not use.

The **contract line** closes every render, unconditionally.

**Baton lines** name the next command: `preflight` and `graph-init` on their
standalone runs alone — neither prints one where the call it sits inside
has already run that same suite or that same seed — plus
`show-confirmation` and `start-build`. `advance-step` needs none: the next
stage's card is its baton.

**The ledger is the git commit trail** — there is no pipeline metadata
file (SPEC.md, The Driver's Contract: resumption's state lives in the
working tree). A boundary is a commit whose subject is
`kb-build: <stage-id> | <display name>`, with an optional body paragraph
(the charter path on `start`, the `--note` text elsewhere); `show-status`
reads them back with `git log --grep`. Recording sweeps with `git add -A`
(gitignore keeps scratch out) and falls back to `--allow-empty` when no
tracked file changed — the boundary is the point, not the diff.
Each stage's sweep is also what leaves the worktree clean for the next stage's
tool, which the head depends on: `graph-init`'s preflight refuses a dirty
worktree, and the tree the stage before it wrote is exactly that until the
boundary commit lands.

Recording is **stage-addressed and declarative**. Re-recording a recorded
stage is exit 0 with a banner above the checklist and no commit; an
out-of-order stage is refused (exit 4) with the checklist, correcting a
caller's wrong world-model rather than obeying it. `start-build` on an
already-started build refuses with exit 5.

Exit 6 is a failed **postcondition**. Every stage carries
one, and they check that the stage's observable artifacts *exist* — the
charter file (`start`), `README.md` (`overview-drafted` and
`phase-5` alike, one check guarding both boundaries).
`CONVENTIONS.md` is not among them: `phase-3a`'s readiness stamp writes it, so
a boundary check for it after that stage is satisfied by work neither
meta-documentation stage did, and a contract no run can fail distinguishes
nothing.
`phase-3a` is the exception in depth: its
postcondition runs all three verify gates rather than looking for a file.

**Existence is not quality.** No postcondition reads a file's contents or
judges them; that is the verify gates' job. A
refusal names what is missing and then renders the stage's card, because
the card is the corrective feedback.

#### What the Stage Table Declares

**One question — does this pass run, and what must be true for it to — used to
be answered in four places that could not see each other.** `kb_pipeline` is
the only module both sides can share (it imports stdlib, `kb_util` and
`kb_index_lib`, and `kb_driver` imports it throughout), so the stage table
there declares all of it and both consumers read the declaration:

| Declared on `Stage` | Read by |
|---|---|
| the stage id | everything; the vocabulary was already single-sourced |
| `work_is_inference` | `_excused`, for which coverage units a build spending none has nothing left to assert |
| `claimgraph_invocation` | `kb_driver.run._claim_graph` composes the flags from it; `kb_claimgraph.__main__` resolves the same flags back to a stage |
| `precondition_of(stage)` | the stage whose own work must already stand — the stage before it, so the order is stated once |
| the coverage split (`CoverageUnit.asserts_own_work`) | `_excused`, below |

**`steps.stages_without_own_inference` is gone, and its guarantee is not.**
That function derived the old walk bound at runtime, and its docstring ended
"Do not 'simplify' it back" — the failure it names is binding, its mechanism
was not. The durable requirement is that *an inference-spending row inserted
anywhere must not silently escape*, and what carries it now is: one runtime
source (`Stage.work_is_inference`, which `_excused` reads and nothing
re-derives), plus a test that performs the derivation itself over `steps.STEPS`
and compares to the declaration **in both directions**
(`test_kb_driver_steps.py`). A test that only asked whether the declaration is
self-consistent would pass on a table that had stopped describing the rows, and
would look identical in review.

**The claim-graph invocation table is the one that existed nowhere.**
`--pass 1 --scope block-hosted`, `--pass 1 --scope full` and `--pass 2` are
`claims-declared`, `claims-discovered` and `depends-attributed`;
`ClaimgraphInvocation` is that pairing, composed by `.flags` and resolved by
`claimgraph_stage`. The driver holds no pass number of its own and the tool
branches on a stage id rather than on a number, so a build cannot invoke a pass
the tool would run as a different stage. The flags themselves are unchanged —
see ROADMAP.md for what the surface would become.

**`kb_pipeline` imports nothing from `kb_claimgraph`, and that is now
structural.** Two of its coverage checks used to reach into that package from
inside a function body — `UNSCANNED_REASON` and the document walk. That was
safe only while nothing imported the stage table back; the moment
`kb_claimgraph` reads it, hoisting either import closes a cycle, and nothing in
the code said so. Both moved to `kb_index_lib`: `UNSCANNED_REASON`, beside the
frontmatter parser that reads it, and `document_texts` / `kb_files`, the one KB
document walk the verifiers, the claim-graph builder and the coverage checks
now all share. `test_kb_pipeline.py` asserts the absent import directly.

#### The Build Pipeline's Coverage Checks

**Every coverage unit is one of two kinds, and it says which.**
`CoverageUnit.asserts_own_work` has no default, so a check cannot forget to
classify what it is asking. A unit asserting *this stage did its work* has
nothing to assert of a build that dropped the work and reports as vacuous; a
unit asserting *the state handed across this boundary is valid for what comes
next* is never excused, because whatever path reached the boundary the next
stage is entitled to its contract.

**The classification is the unit's rather than the stage's, and
`_check_verify_gates` is why.** That one check is `depends-attributed`'s
postcondition and `phase-3a`'s alike, and `depends-attributed` is a stage whose
work a no-inference build drops — so a per-stage answer would excuse a validity
gate at the head's own exit. Declared on the unit, one check carries one answer
to every boundary it guards.

**Excusing takes two conditions and both are the tool's.**
`kb_pipeline._excused` turns a unit vacuous only where `CheckContext.no_inference`
holds *and* `Stage.work_is_inference` does. The second is what keeps a build
that spent no model call from excusing the stages whose work was never a model
call: it derived its tree and seeded its spine for real, and those units stand.
`kb_util advance-step --no-inference` is the whole of what a caller supplies —
one statement about the build, naming no unit and waiving no check — so a
record cannot reach into the classification.

The consequence the owner confirmed: `kb-refresh`/`kb-verify` runs at both
boundaries that declare a validity unit, `depends-attributed`'s included, over a
KB that stage may have left unchanged. Refresh is idempotent absent claim-value
changes and verify is cheap, so asking twice costs nothing beside a validity
gate silently skipped.

`show-status` is read-only, exits 0 in all three world-states, and names
which holds (`not started` / `in progress` / `complete`). A
present-but-incomplete ledger is what says an invocation is continuing a
build rather than opening one — the driver's whole reading of the
distinction. It anchors on the git root
alone, because the ledger lives in the commit trail rather than in the KB:
a build being opened renders its all-undone checklist at confirmation time,
*before* anything has created `kb-root/`, and the render carries a note
saying the spine is unseeded. **The two writing ops anchor there too**, for the
same reason: a build's opening stages are recorded before anything has created
`kb-root/`, the tree being the `document-graph` stage's own product.

**`show-stage-status` asks what remains inside a stage** instead of
reconstructing it. It prints a `FACT` line naming the stage and how many
units it declares, then one `COVERED` or `MISSING` line per unit carrying
the path that unit is satisfied from — the section-to-path pairing no
other verb renders, which is where an operator debugging a stalled build
reads the artifact path a check is missing, rather than reconstructing it
from the stage table by hand. The `MISSING` lines are
the same lines an `advance-step` refusal renders for the units it names:
one computation, two callers. It writes nothing, records nothing, renders
neither checklist nor card (that render is as long as the corpus has
units, and `show-status`' is what gets quoted verbatim into a user
message), and exits **0** in every stage state — recorded, in flight, not
yet reached, or `UNDECLARED`, which is a stage whose declaring artifact is
missing and whose one `FACT` line names the call that restores it. Its
only nonzero code is 2, for an unresolvable git root; refusing is
`advance-step`'s job. Without `--stage` it reports the stage in flight,
through the `current_stage()` decision behind the checklist's `[*]`
marker; on a complete build it says every stage is recorded rather than
falling back to the last one. One stage's unit is argument-derived —
`start`'s charter — and a read reports it as missing because the argument
rides the record, never as a claim about the tree.

### Consumer-Side Targets

Defined by the included fragment (stdlib-only, run under the system
`python3` — no venv):

- `kb-verify` — the green gate: `verify_md_links.py`, `verify_kb_metadata.py`,
  then `verify_citations.py`.
- `kb-refresh` — regenerate derived metadata + `.index/` from authored sources,
  then the claim-graph sheet from that index.
- `kb-stats` — claim-graph dashboard via the query CLI.

A register that loses an entry, or binds a marker to the wrong heading,
fails `verify` — the same binding the write API refuses an edit into.
