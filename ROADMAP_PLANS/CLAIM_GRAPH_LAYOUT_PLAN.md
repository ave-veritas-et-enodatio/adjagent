# CLAIM_GRAPH_LAYOUT_PLAN.md — the sheet is built; this is what it looks like

**Paths** are repository-root-relative. The renderer lives at `kb_tools/kb_graph/`.

**Seat.** **PC** python-coder. Nothing here is a contract-doc change unless a row says so.

**Bold marks a decision the row itself must make and record.** It is not emphasis.

**What travels with a dispatched row**: this header, the Standing rules, the Baseline
section, the row, and the rows it is blocked on. The Order section does not travel.

## What produced this

The claim-graph SVG renderer is built and shipped: one `kb_util` subcommand, dummy-node
routing, crossing reduction, coordinate assignment, an orphan grid, container-scaled
output, and render-only suppression of a `references` edge a path already implies. That
ladder is complete and its plan retires with this document's authoring.

**What is left is not feature work. It is that the sheets are wider than the graphs in
them.** Two phases of the layout algorithm are running the cheaper of two known options,
and the cost has been measured rather than estimated.

## The measured baseline — every row grades against these

**Every figure in this section is invalidated and none of it has been retaken**: the 15,034px
sheet, the spread and the `viewBox` table were measured over trees whose claim graph attributed
claims to indexes, which `5f90b53` fixed and a corpus rebuild carried through, so a row re-measures
its own baseline before grading against it.

**The spread nothing closes.** On a 126-claim sheet — 122 strokes, 9 layers, the widest
holding 29 boxes — those 29 boxes occupy **6,960px** and pack into **7,408px** at the
current gutter. The sheet is **15,034px**. So **7,626px of it is spread the priority
method opened and no constant reaches.** That is the number this plan exists to move.

**The renderer's own trade, stated.** The priority method only ever spreads a layer: on
`mini-kb` it bought **81% shorter edges** at the cost of a sheet **19% wider than the grid
drew it** (5,048 → 5,997).

**Where the sheets stand now**, each under `just render-claim-graph <repo>` over a copy of
a built KB:

| KB | `viewBox` | ratio |
|---|---|---|
| `2609.00183v1` — 32 nodes, 138 strokes, 7 layers | `6960 × 620` | 11.2 : 1 |
| `2609.00269v1` — 21 nodes, 154 strokes, 4 layers | `4864 × 356` | 13.7 : 1 |
| `2609.00108v1` — 23 nodes, 34 strokes, 6 layers | `2192 × 532` | 4.1 : 1 |
| `2609.09855v1` — 135 claims, 114 `depends` | `24800 × 884` | 28.1 : 1 |
| `mini-kb` — the committed 32-node fixture with a golden | `2048 × 444` | 4.6 : 1 |

## Standing rules — on every row, not restated per row

1. **Geometry is a pure function of the graph, never of record order.**
   `test_shuffling_the_loaded_records_changes_no_geometry` passes, and it is not
   "byte-identical across runs" — a sweep iterating a dict built in record order also
   passes that. Every tie is broken by a stated rule, never by a container's iteration.
2. **Layout lives in `layout.py` and nowhere else.** A swap, a barycentre or a
   try-both-and-keep-the-better found in `model.py`, `style.py`, `svg.py` or `ops.py` is a
   crossed boundary rather than an optimisation. `svg.py` computes no coordinate: a
   comparison between two node positions in it is the same boundary from the other side.
3. **`layout.py` knows no colour, no font, no tag name, no URL.** If it imports `xml`, the
   boundary has been crossed.
4. **Stdlib only.** No third-party runtime dependency, no vendored layout library, no DOT,
   no Graphviz.
5. **The golden is regenerated through `write_mini_kb_golden`, never hand-edited.**
   No runner target does this today — that gap is R3.
6. **A row reports numbers with the command that produced them**, before and after, on
   `mini-kb` and on at least two of the real KBs above. `viewBox`, crossing count and
   total edge length are the three; a row that moves one and not the others says so.
7. **The method, its parameters, its round cap and its convergence condition are recorded
   in `layout.py`'s own docstring**, not here.
8. `just test` green at handoff. Scratch under `.claude-temp/`. Never `/tmp`.
   `kb-testing/test-data/transient/` is read-only — render a copy.

## Rows

| Row | What | Blocked on | Done when |
|---|---|---|---|
| **R1** | **Brandes–Köpf coordinate assignment**, replacing the priority method in Sugiyama's phase 4. It is the method that *compacts* — the priority method only ever spreads a layer, which is the whole of the 7,626px above. Larger than what it replaces and known to be so. **Its four candidate alignments and how a row combines them is this row's to rule**, with the choice and its reasoning in `layout.py`'s docstring | — | **The spread figure re-measured on the same 126-claim sheet**, before and after, with the command. Crossings do not rise on `mini-kb` or on the corpus sheets — coordinates may not undo the ordering phase's work. Total edge length reported both ways: the priority method bought 81% shorter edges and a row that gives that back has traded the wrong way. Golden regenerated, determinism test green |
| **R2** | **Network simplex layering**, replacing longest-path in Sugiyama's phase 2. Longest-path pushes every node as high as it can go; network simplex minimises total edge length, **so it produces fewer long edges to route — which is upstream of everything R1 does**, because a spanning edge costs a full `COLUMN_PITCH` in every layer it crosses and width is O(edges), not O(nodes) | R1 | Dummy-node count reported before and after — that is the mechanism, and it should fall. `viewBox` and total edge length on `mini-kb` and two corpus sheets. Crossings do not rise. Determinism test green, golden regenerated |
| **R3** | **A runner target regenerates the `mini-kb` golden.** Every ladder row so far has invoked `write_mini_kb_golden` through the venv python by hand, and standing rule 5 forbids hand-editing the golden while naming no way to regenerate it. Small, and it unblocks the two rows above from improvising | — | A target exists, the rows above use it, and no row's evidence names a bare interpreter invocation |
| **R4** | **Two docstring defects in landed code**, neither blocking anything: `kb_graph/__init__.py`'s module map documents `model`, `layout` and `style` and omits `svg.py`, which exists in the package; and `kb_index_lib.build_claims_records`'s docstring says `claims.jsonl` holds four node types while the function also emits a `support` record per entry in `state.supports` — five written, four documented | — | Both corrected. No behaviour changes |

## Out of scope

**Dummy-column pricing** — charging a dummy less than a full `COLUMN_PITCH`. It is real
headroom and it interacts with both rows above, so it wants their numbers first rather
than a third variable moving at the same time.

Anything about which edges are drawn. L8 ruled that render-only suppression is the
renderer's business and **the claim graph is never reduced in service of a rendering
limitation** — a reference the corpus states is true whether or not a picture can hold it.
No row here touches `kb_claimgraph/` or changes what `.index/` records.

## Order

**R3 first** — it is small and the other rows need it to produce evidence without
improvising a command line.

**R1 before R2**, though the dependency is evidential rather than technical: R1's
compaction is measured against the current layering, and moving both at once leaves no way
to attribute the change. R2 then re-measures on R1's baseline.

**R4 any time.** It shares no file with the others.
