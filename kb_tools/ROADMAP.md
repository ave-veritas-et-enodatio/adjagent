# ROADMAP – kb_tools

Future intent only. Not part of the contract-doc precedence chain; not
handed to coding dispatches.

## Policy direction

Standing constraints on how the items below get decided. These do not
retire; an item that conflicts with one of them is the defect.

- **The lowest tier is the target configuration, not a stress test.** The
  agent set must do full knowledge-base work — dissection and
  exploration/maintenance alike — driven by a bottom-tier model on a
  stock harness. Capability above that floor is headroom, never a
  requirement. A decision that works only on a frontier model has not
  solved the problem.

- **Local compute is a first-class deployment, not a fallback.** Nothing
  in the pipeline may assume network egress, a hosted endpoint, or a
  provider-specific capability.

- **The floor has two names.** The build process targets local inference
  on the **`gemma-4`** family; the shipped agent set targets **`haiku`**
  in the Claude Code context. `just generate-stock` renders at the
  `gemma-4` rung, so "does this survive the target tier" is a run.

- **No artifact crosses between producer and reviewer.** A knowledge base
  is never an interchange format: it is built locally, from a source
  document, by whoever is reading. This is why no schema-compatibility
  guarantee spans installs, and why the format stays free to change.

- **Per-node only.** Solidity and confidence exist per claim. No
  aggregate, no corpus-level score, no single number that could stand in
  for reading the map.

## Items

1. **Full-vs-incremental refresh** — revisit only if rebuild time becomes
   the constraint. Why `refresh` is always a full rebuild, and what an
   incremental path would cost the freshness gate, is in ARCHITECTURE.md's
   module inventory (`refresh_kb_metadata.py`). Nothing moves until rebuild
   time actually binds.

2. **Pre-split invariants parsing, deprecation.** A KB built before
   `kb-root/invariants.md` split off as the framework-node source still
   parses `### INVARIANT-*` headings and `- Axiom N:` bullets from
   `CLAUDE.md`. `verify_kb_metadata.py` carries a deprecation note for this
   compatibility path, live until the headings move.

3. **Claim-graph SVG renderer in kb_util** — important for end-users and development alike (owner, 2026-09-02): a subcommand rendering the claim graph to SVG. Nodes carry the claim id and slug and are **hyperlinks to the claim's canonical definition in the KB leaves — never to the LaTeX** (owner, 2026-09-03: the KB is designed to become the canonical source, demoting LaTeX to a rendered artifact after initial conversion and a validation/verification/comfort-level transition by the author; the index's `canonical_path`/`canonical_anchor` already point into `kb-root/`, and the SVG `<a>` wraps each node with them); edges color-coded by the **strength of the supporting connection** (solidity/on-point values from the register — the same numbers the aggregation pipeline computes, displayed rather than restated). Development value is immediate: tonight's ghost-id/orphan class of defect would be *visible* — a disconnected subgraph or dangling edge reads at a glance in a picture where it hides in a JSONL diff; the run-2 graph-isolation defect (a whole volume's claims unlinked) would have been one look. Reads only `.index/` JSONL — a pure derived view, no new data. Three human-instrument roles (owner, 2026-09-02): at-a-glance connectivity — *whether the concepts connect, instead of reading that they do/don't in a summary*; strength coloring showing *where the work is needed and whether weakness is localized or structural*; and **cross-run comparison** — whether graphs from different runs or after an agent-definition tweak are *substantially the same, denser, or different*. The third imposes a hard design requirement: **layout is deterministic and stable** — same graph → byte-identical SVG, and a small graph delta → a locally different picture, never a global rearrangement (rules out seeded force-directed layouts; ordering and placement derive from stable keys). **Layout ruled (owner, 2026-09-03): hand-rolled, stdlib-only — no vendored layout lib, no DOT** ("SVG is highly regular and easy to dump out… hand-rolled & simple with some useful helper functions goes a long, long way"; revisitable). The principle: **topo-sorted bottom-to-top** — most basic premises at the bottom, most derived at the top (layer = longest path from the axioms, so height reads as derivation depth), id-keyed ordering within layers, straight segments; determinism-by-stable-keys over optimization, since global optimizers are exactly what turns a small delta into a global rearrangement. Crossings are **denoted, not avoided**: a `drawHopOver` utility renders the schematic-style bridge glyph where segments intersect (closed-form on straight segments; which edge hops decided by stable key), converting crossing-avoidance from a layout problem into a rendering primitive. One-corpus caveat, owner-stated: generality unproven — the design pass previews the simple resolution against synthetic stress graphs (dense diamonds, wide fans, long chains) before anyone rules "mess." **Two edges, two strokes**: `depends` and `supports` are distinct edges established from different directions, not one relationship recorded twice — `depends` runs claim-or-support → claim/invariant/axiom and the target gates the source, while `supports` runs support → claim and the source lifts the target, carrying an on-point fraction (SPEC's edge table); the renderer draws both, never collapsing them into a single stroke, since doing so would erase the direction distinction the graph exists to show. A relationship recorded at only one end is not, by itself, a bookkeeping defect: SPEC states of `strengthens` that one direction standing alone is not a bookkeeping defect, and `supports`' own both-ends property is a staging artifact rather than a double-bookkeeping check — a `sup-` id is minted a full stage before the leaf that will carry it, so the register entry stages the beneficiary fan-out and the hosting leaf declares it, with the leaf canonical and the graph built from that end alone; this property belongs to `supports` alone and generalizes to no other class. Process when started: architect + coder, no MAD pass (ruled 2026-09-03). Born a `kb_util` subcommand. Design and increment ladder are planned separately; this entry retires when that ladder completes.

4. **Generality corpus — a few dozen arXiv papers, varied in topic and construction** (owner, 2026-09-04). Getting `/kb-build` through the seven-volume corpus used for early development end to end is a big step and explicitly **not the last one**: every design decision the pipeline carries was taken against a single document, so a clean run over that corpus measures that document, not the process. The instrument is a sampling of real arXiv sources chosen for *structural* variety — single-file and `\input`-chained, section-dense and section-sparse, appendix-heavy, theorem-dense, shared-preamble — not for topical breadth alone. **First known blocker, concrete today**: `kb_survey.manifest.volume_slug` takes `PurePosixPath(source).stem`, discarding the directory before slugging. An arXiv sampling is overwhelmingly `main.tex`, so every entry slugs to `main`. It fails loudly rather than silently — `_volume_members` carries a `runlog.require` guarding "two source volumes share one slug, so one survey would overwrite the other" — but it refuses, and the directory is the only distinguishing component and is discarded by construction, so the slug scheme changes before that corpus runs at all. Other assumptions shaped by that earlier corpus this new one is expected to stress: section counts in the single digits (STAGE_STATUS_PLAN's D1 = B makes the section the phase-0 unit), domain attribution by first path component, and `\input` chains — rare in the seven-volume corpus, normal in arXiv, and already the reason STAGE_STATUS_PLAN's D4 could not attribute a section to its entry volume. The renderer entry above carries the same one-corpus caveat scoped to layout only; this entry is the pipeline-wide case.

5. **The badly structured paper — a finer boundary set, not a different mechanism.** The section-boundary leaf-cut design rests on one property: *the model picks from a boundary set the tool enumerated, and authors none of it.* Sections are the obvious source for that set, not the only possible one. A monolith with no useful sectioning would otherwise collapse to one leaf — the case full transcription handled, at the price of trusting a model to move bytes faithfully.

    Keep the property, widen the source. A document with thin declared structure gets a finer index: paragraph breaks are blank lines `latexwalker` already sees, and `\paragraph` heads where they exist. The model still returns ranges over an index it did not invent; the partition check is unchanged; nothing is transcribed.

    **Oversize sections get a mechanical split**, at a sentence break, with the pieces titled `(forced-split I/N)`. That keeps the split visible in the index the model reads and in any leaf title derived from it, so a forced boundary is never mistaken for an authored one.

    **And the judgment may be rarer than assumed.** Heuristics can take most leaf splits straight from the original authoring, farming out a call only where a section falls outside acceptable shape properties. Then inference is the exception path rather than the stage, which is the strongest form of this design and may be cheaper than the current one on a well-structured corpus.

    Sizing: item 4's generality corpus is chosen for structural variety and names section-sparse explicitly, so that is where this gets exercised. Open question: whether a paragraph-granular index stays small enough to reason over for a long paper.

    **If it injects levels, it owes the injected nodes a summary** (owner, 2026-09-09). A node the author never wrote has no carved text to carry, so the stage that breaks up a wall of text also synthesizes navigation prose for what it created. That authoring runs as the write / review / repair one pass, with a subject-matter expert on both ends — selection automatic, or declared, something like `--discipline=[math|engineering|general]`; undecided. Version 1.0 or 2.0 work, not the demo.

    Recorded here because it is the one prospective stage that could have wanted the retired review wave, and it does not: an SME judging whether a summary misleads is not an accuracy or structure review of a mechanical dissection, and no shape of this needs those seats.

6. **The envelope contract is one definition, not two.** `fragments/envelope-contract.tmpl` hand-types the JSON skeleton a seat returns — `step`, `members`, `name`, `status`, `gaps`, `deviations` — with `@!step-id!@` its only substitution, while `envelope.py` states the same vocabulary independently as `ENVELOPE_KEYS`, `MEMBER_KEYS` and `DEVIATION_KEYS`, which `check_keys` refuses against in both directions. Two definitions of one format, no mechanical link between them.

    `envelope.py`'s own module docstring names this hazard and claims to have closed it for `DEVIATION_KINDS` and `SCOPE_LINE_CONTRACT` — *"a restatement would be a second definition"* — while the container that wraps them stays a restatement. Both artifacts survive the driver rebuild into `kb_tools/seat_contracts/`, so the duplication travels with them and whoever picks them up inherits it silently.

    Fix: render the skeleton from the parser's own constants into a slot, and delete the hand-typed JSON.

7. **Pre-populated values files and a `--check` flag on the write ops — a technique in case we need it.** Not planned work. It applies to values files a model composes directly, which is the claim mint: the one place a model authors ids and values rather than transcribing them.

    Two halves. **A pre-populated artifact**: the driver emits the values file with one `[[entry]]` per id, ids already filled and value fields carrying the unscored literal, so the model replaces values and touches nothing structural — it never types an id and so cannot fabricate or transpose one. The residual gap, a model adding or deleting a row, is caught by comparing the returned id set against the set emitted: a set comparison, not a parse. **A `--check` flag on the write op**: `kb_write/ops.py` runs Census → render → temp → prove → replace, and a check is that pipeline stopping before replace. Same code over the same bytes, so checker and writer cannot drift and a clean check is a guarantee rather than an approximation. A flag rather than a separate verb, because `OP_FIELDS` is closed and per-op, so a flag inherits that op's key vocabulary where a new verb would have to be told it.

    **The rationale is behavioral, not efficiency** — stated plainly because the arithmetic does not favor it. A refused write already leaves nothing on disk, and a check-fix cycle costs a full round trip exactly as a submit-refuse cycle does. The claim is that check-diagnose-fix is a pattern models treat as normal iteration where repeated submit-refusal reads as accumulating failure. Checks would need their own cap, set high enough not to bind during normal iteration; hitting it means the model is stuck on the format rather than sloppy with it — a different diagnosis and a different remedy from a submit failure.

    **Revisit condition:** a run where a model burns its retry budget composing a values file.

8. **Inline-drawn figures survive the document-graph build — a source pre-pass.**

    Pandoc's LaTeX reader **discards `tikzpicture` at parse time**: not as a `RawBlock`, not as a `Div`, absent from the AST entirely, with or without `\usepackage{tikz}`, at exit 0 with no warning. A genuinely unknown environment becomes a `Div` classed with its own name; this one pandoc recognises and deliberately drops.

    Two consequences make this a real item rather than polish. **No downstream rule can recover it** — content the reader discarded is not in the AST for anything to select, so `kb_tools/SPEC.md`'s Document-Tree Contract point 11's structural identification has nothing to identify. And **the partition check cannot see the loss**: its reference is the AST, so a drop upstream of the AST is missing from both sides of the comparison and reads as clean. A build over a figure-heavy manuscript loses every drawing and reports success.

    The mechanism is therefore a **pre-pass over source text**, before pandoc parses — the same moment as the `\newenvironment` strip, for the same reason. It rewrites drawing environments into a form pandoc preserves; only then can point 11's fenced-source treatment apply. What it keys on is the open question, and a name list is the wrong shape: `tikzpicture` and `circuitikz` are both known members, and a rule keyed to the first silently drops the second.

    Needs a corpus that contains figures. The tracked fixture has none; the out-of-tree AVE manuscript carries `tikzpicture`, `circuitikz` and images at weight.

    **Tabled deliberately** (owner, 2026-09-07): soluble but messy, and it is not the thing that needs proving. The claim graph built from text is — the document tree exists to be its sole input, and figures bear on neither.

9. **Harden the document-tree contract where phase 2 found it thin.** Three amendments the claim-graph plan proposed against `kb_tools/SPEC.md`'s Document-Tree Contract, carried here because that plan retires and these are phase-1 contract work rather than phase-2's. Two were landed during that plan's life — point 12's recognition form and point 9's fence spelling — and these three were not. **The reference list's placement has since landed**; the two below it are open.

    **Point 7 promises what nothing checks, and this is the one with teeth.** The contract says every internal link resolves and every rewritten anchor lands on the node that held its label, and cites `verify_md_links` as covering the first. It does not: `kb_links.LINK_RE` matches `[text](target)` and nothing else, while cross-references render as `<a href="…" data-reference-type="ref|eqref">`. The tree carries **243 of those against 323 Markdown links**, so the whole class point 7 is *about* is unchecked on every build. All 243 resolved when hand-verified once; nothing would report it if they stopped. Point 7 should state the rendered anchor form, and a check should cover it.

    **The reference list lands wherever the source position fell — landed.** It went to the last content document of each volume, `notation.md` in five of seven, purely by accident of where the citation processor appended the block. It is now lifted out of the content stream before the split and emitted as `<volume>/references.md`, a leaf under the volume index titled `References`, stated at `kb_tools/SPEC.md` point 10. The volume *index* was this item's proposal and is superseded: the index body is the abstract's home under point 13, and burying a bibliography beneath it damages both. A volume the citation processor emits no reference list for gets no such document.

    **A closing maths fence can carry trailing markup.** One in the corpus reads ` ```* ` — an emphasis closer landing on the fence line — which is not a closing fence under CommonMark, so an exact-delimiter reader finds the fence unclosed and swallows the blocks after it. Point 9 states the *opening* spelling for exactly this reason and owes the same for the close. `kb_claimgraph` already matches the close on the delimiter alone; the contract has not caught up.

    Neither of the two still open blocks the claim graph. They are the difference between a contract that holds because it is checked and one that holds because nobody has broken it yet.

10. **One source per rating ladder, read by every consumer.** Two ladders are hand-copied today, and neither is pinned. `kb_schema.BUILD_BAND_LADDER` — five thresholds over computed solidity, with the status phrases a reader acts on — is transcribed as a markdown table in `[chunks.kb-solidity]` in `templates/shared-chunks.toml`, thresholds and phrases both — it sat inline in `kb-docent.md.tmpl` until the scoring semantics were chunked, which makes the alarm *more* worth building rather than less: the table is now the one statement every seat that explains a score expands, so a drift reaches all of them at once. The rigor grades — seven discrete values a person assigns to a derivation — exist **only** as prose, in `[chunks.rigor-scale]`, with no code-side definition at all; the write ops accept any number in `[0, 1]`. **Ruled 2026-09-08: the duplication stands for the prototype, and a test carries the alarm.** The test suite asserts that the two statements of the band ladder are identical — `kb-docent.md.tmpl`'s table against `kb_schema.BUILD_BAND_LADDER`, thresholds and phrases both. It is not single-sourcing and does not pretend to be; it converts silent drift into a failing test, which is the property that was actually missing.

    **Why pinning rather than single-sourcing, which the direction settles.** True single-sourcing needs one side to read the other, and neither direction is affordable. `kb_tools` cannot read `shared-chunks.toml`: `gen-defs.py` is project space and never installs, so an installed `kb_tools` would import something absent. The inverse is architecturally sound — a generator may read its inputs — but `gen-defs.py` is stdlib-only and renders 151 definitions, most unrelated to this toolchain; importing a shipped package to render them would make the whole agent set's build depend on this one importing cleanly, which is the coupling the surface boundary otherwise keeps out. Both directions cost more than the duplication does. `shared-chunks.toml` already handles unreachable-by-chunk duplication with a maintainer comment saying *check by hand*; where the other side is a constant rather than prose, a test is strictly stronger than the comment and costs little more.

    **Not a consumer of this ladder, ruled 2026-09-08:** `applied-mathematician`. That seat judges argument strength for any formal system, kb or not, so a grade scale is its own to own. The two aligning is fortunate rather than structural, and if they ever diverge the kb-side dispatch tells the mathematician to use the kb ladder instead of its own. That seat carried this rubric once and lost it deliberately — `e35cc91`, when the scoring seat split off — so its absence there is a decision already taken, not a gap to close.

    **Open, and decide it first: one ladder or two.** They share a range and are different quantities. Rigor's `0.9` means *derived end-to-end* — how the argument was made. A band's `0.85–1.00` means *ok to build on* — what to do with the result. A claim with no dependencies has solidity equal to its rigor, which is why they look mergeable; merging picks one vocabulary and loses the other.

    **The substitution runs from the templates toward the code, not the reverse.** `gen-defs.py` imports no `kb_tools` and has no mechanism to render a Python value into a definition; giving it one couples the generator to a shipped package, which the surface boundary exists to prevent. Cheaper and boundary-preserving: the ladder lives in `shared-chunks.toml` as the single source, definitions expand the chunk, and `kb_tools` carries a test asserting its constants match it. Templates own the text; code asserts agreement.

    **Not gating.** `[chunks.rigor-scale]` stays as it is, with its one consumer, as the seam this builds on.

11. **A re-launch over a finished build still exits 0 rather than stopping loudly.** The destructive half of this item has landed: there is no build-mode setting, an invocation that finds `start` unrecorded is opening a build, and `pre.kb-root` refuses one over a populated `kb-root/`. What is left is the case an empty ledger cannot describe — a build whose ledger is *complete*, re-launched. Every stage is recorded, so the walk skips all of them and reports success, which is right for a repeat invocation and wrong for someone who meant to rebuild: the driver cannot tell a safe rebuild from one over work the KB has since gained, and the honest answer is to refuse and name an explicit override rather than to report a build it did not run.

    **What makes that case unresolvable is worth stating, because it is the deeper gap:** nothing records which phase a KB is in. `kb_tools/SPEC.md` now states that the first hand-authored leaf body or hand-minted claim inverts canonicality permanently, and that fact lives only in git history — no frontmatter key, no `kb-root/CLAUDE.md` field, no `.index/` artifact carries it. A driver that could read the phase could distinguish a safe rebuild from a destructive one; lacking that, refusing is the only honest answer.

12. **One-hop cross-volume links.** A multi-volume corpus cites its own siblings by citation key — the seven-volume corpus used for early development carried `\citep{mertensPart2}` ten times and `\citep{mertensPart4}` six. Today that reaches the KB as author-year prose where a bibliography resolves it and as nothing where one does not, so a reader resolving "Part 2" goes key → `references.md` → author-year entry → title → match a volume title by eye. The standing quality eval measured the consequence on the mechanical baseline: **zero cross-domain links anywhere** in a five-part mutually-citing corpus, with that textual route recorded as the only path that works.

    **The key is enough, and it costs no bibliography.** `\citep{mertensPart2}` carries `citationId: mertensPart2` in pandoc's `Cite` node, which reaches the Lua filter whether the corpus ships a `.bib`, a `.bbl`, or neither — verified directly against pandoc. So recognition is available on exactly the corpora where a bibliography is least likely to be, which is the arXiv population item 4 names.

    **Which keys name sibling volumes is a corpus declaration, not a toolchain guess** — the same posture as the claim-bearing environment vocabulary and the label-prefix taxonomy. A `.bib`'s `title` field makes auto-detection possible as a convenience; a `.bbl` degrades it to a substring match against formatted prose; neither is required where the corpus states which keys are its own.

    What it delivers is better than what a resolving bibliography gives today: a direct link to the cited volume in place of a hop a reader performs by hand. It also splits citations into **internal** — an edge candidate the graph can hold — and **external**, which is unverifiable by construction and wants an off-graph endcap instead. That split is this item's material; the endcap is its own.

13. **Recover a bibliography from biblatex's `.bbl`.** arXiv guarantees a paper carries one of the two forms — submission is blocked when the `.bbl` is absent and a needed `.bib` is missing — but the two `.bbl` flavours are not the same artifact. **bibtex** emits `\begin{thebibliography}` with `\bibitem` entries: plain LaTeX, spliceable into a staged copy of the source by string substitution, which is the handled case. **biblatex** emits its own internal format — `\entry{key}{type}{}` blocks of `\field{name}{value}` pairs — which nothing but biblatex renders, so splicing it produces macro soup rather than a reference list.

    **The recovery is easier than the case that already works, not harder.** A biblatex `.bbl` is *more* machine-readable than bibtex's: `\field{title}{…}` is an explicit field where bibtex's output is prose already formatted to a style. A scraper collecting `\entry` / `\field` pairs reconstructs either a `.bib` or a plain `thebibliography` for the same string substitution. Names are the fiddly part — nested, with hashes and initials — but title and year, which is what volume-matching (item 12) needs, are flat.

    **Deferred rather than dismissed, for one reason:** that format description has not been checked against real biblatex output. The generality corpus (item 4) produces such files by the dozen, which is the cheapest possible confirmation and makes this work worth doing *after* the sweep rather than before it.

    Until then a biblatex `.bbl` degrades rather than fails: the citation filter carries the keys, no reference list is produced — which point 10 already admits, a volume the processor emits no reference list for getting no such document — and the build records which toolchain the paper used. Note the population skews old: since November 2025 arXiv processes `.bib` files itself, so recent submissions increasingly carry the form that needs none of this.

14. **Stage rewind as a first-class driver operation** (`--resume-from-phase <name>`). The build is
    resumable after an *interruption* — pick up at the first unrecorded stage — but there is no way
    to say "that stage's output is wrong, I have fixed the cause, run it again." Twice in one night
    a live build stopped at the `phase-5` cap barrier on a defect stamped from a packaged template,
    and clearing it meant rebuilding from scratch or rewinding the ledger by hand.

    Git already supplies the mechanism: the ledger *is* one commit per stage, so `reset --hard`
    un-records a stage and reverts its tracked output atomically. Because a rewind is contiguous
    from HEAD it cannot leave a stage recorded whose predecessor is not — the downstream
    invalidation the ledger's ordering does not otherwise model becomes unrepresentable rather than
    handled. What is missing is the safe front end.

    - **Correlate stages to commits by tag, not by parsing the commit message.** The driver writes
      `kb-build: <stage> | <title>`; parsing it back makes a human-facing message into a machine
      format, and a reword then breaks the rewind silently. A tag written in the same operation
      (`kb-build/<stage>`) is a machine reference that declares itself.
    - **The untracked half is the real work, and it is a schema question.** `git reset` covers what
      is committed; the operation is only correct if it also clears each rewound stage's scratch.
      Nothing declares that today — `phase-5` keeps its round counter as *filenames* under
      `.claude-temp/kb-build/review/`, so a rewind leaving them behind resumes with the fix budget
      already spent, and that is known only because a review happened to find it. A `scratch=` field
      on the `Stage` rows in `kb_pipeline.STAGES`, beside the existing `card=`, makes the rewind
      complete by construction rather than by recall, and makes "what does this stage keep outside
      its commit" an answerable question.

      **The stronger reason for that field is not rewind.** Control-flow state is living in a
      directory whose entire contract is that it can be deleted. Two instances are known: the
      phase-5 round counter above, and the repository concurrency lock,
      anchored inside `.claude-temp/` — the directory a
      routine restage wipes — so a restage landing mid-run removes the lock and a second driver can
      start against the same repository and ledger. Neither was found by looking for the class;
      each turned up in an unrelated investigation. So the population is not two, it is *two we
      tripped over*, and nothing in the system can report the real number. The rule worth landing
      with the field: **state that decides control flow either goes in the commit or is declared.**
      Scratch that survives a reset is scratch that can lie about what has already happened.
    - Guards: refuse on a dirty worktree, and refuse a stage that is not recorded.
    - **Sequencing: the prerequisite this item named has landed.** A `Runner` no longer carries
      resumption state in an instance attribute — a rewind-and-rerun *is* a resuming process, so
      this command would have fired that reversion routinely rather than occasionally. What stands
      in its place is `run.RUNNER_ATTRIBUTES`, the closed attribute set and the criterion that
      admits one, checked at construction and at every stage transition; a rewind command added
      later inherits the guard rather than the defect.

    Until it exists the manual path is two steps, and the second is the one that gets forgotten:
    `git reset --hard <stage-commit>^`, then remove that stage's scratch by hand.

15. **A labelled, referenced displayed equation is a claim-bearing node.** **Ruled: mint it.** Measured on the seven-volume corpus: 40 labelled displayed equations, referenced by **80 `\eqref`** occurrences — a third of the corpus's 243 cross-reference anchors — and not one can produce an edge, because no node exists for them to point at. The corpus states those results, displays them, labels them so they can be cited, and then cites them; the graph records none of it.

    **The reasoning is the one already written for conjectures** (`kb_claimgraph/inventory.py`): *"`conjecture` **is** claim-bearing, and that is a ruling rather than a default: a conjecture is a result the corpus states and does not prove."* If stated-but-unproven earns a node, stated-displayed-derived-and-cited cannot be excluded on the same reasoning. A conjecture is the weakest result in a paper and it gets a node; the equations it is about get nothing.

    **The objection, and its answer.** Not every labelled equation is a *result* — some are definitions, some are intermediate algebra labelled for convenience three lines later. That is an argument about which ones, and the discriminator costs nothing: **an equation nobody references is scaffolding; one referenced from another claim is load-bearing by demonstration.** Let the citation decide, the way the reverse-citation map already decides what is referenced. Minting only referenced equations also bounds the node count to what the author's own cross-references justify.

    **The governing principle is `kb_tools/AGENTS.md`'s second bullet**: a result withheld because some fraction would be wrong does not leave the graph cautious, it leaves it asserting the dependent rests on nothing — a different wrong answer, arrived at silently, that no later pass is prompted to revisit. A mislabelled node is correctable by a human or by an inference pass; a missing one is forgotten.

    **What remains open is the node kind, not the decision.** `kb_schema`'s `NODE_KINDS` is a closed vocabulary and `kind_table` refuses a branch that is not total over it, so this is a real widening rather than a relaxation: whether a minted equation is a `clm-` like any other, or its own kind with its own solidity semantics, wants deciding before the code is written.

    **And the reference's own location is evidence of which kind it is** — the same containment logic stage D already uses for direction, pointed at typing instead. An `\eqref` from inside a proof body is a premise being *used*, so that equation is a result. One from inside a definition environment is the thing being defined. One in a restating sentence is neither. The kind therefore need not be guessed from the equation's own text, which is where a model would be needed: it is read off where the corpus refers to it, mechanically, and an equation cited from several places votes several times. That also gives the ambiguous case a principled resolution rather than a coin flip, and leaves only the genuinely unreferenced-but-labelled equation outside the scheme — which is the one this item already declines to mint.

16. **A definition is a node, and a claim that uses a defined term is a candidate for depending on it.** Two halves of one defect, and they land together or not at all. Measured on `2609.09855v1`: stage D-inf names a definition as the antecedent for at least **9** orphaned claims — *"derives from one thing only: the definition of calibration … which is not a"* candidate — and there is no node for that answer to be. Separately, **15 of 129** claims reach `narrow` with an empty candidate set and are therefore never asked about at all.

    **The node half needs no new kind, no new prefix and no new id grammar.** A definition *is* what a framework node already models: bedrock, solidity 1.0 by construction, terminal, carrying no scoring fields. `parse_framework_nodes` mints them today from `invariants.md`'s `### INVARIANT-XX` headings and `- Axiom N:` bullets; it gains a third source, the corpus's own `\begin{definition}` blocks. SPEC's out-of-scope note concerns AVE-KB's `def-`/`ilk-` **vocabulary import** and states its own admission condition — *"added only against a demonstrated need arising in a corpus this toolchain serves"* — which the 9 orphans are. Minting an existing kind from a new source is not the import SPEC declined.

    **Answer the terminal objection in place**: SPEC says a definition node *"carries no scoring fields and emits no edges — so it contributes nothing to either graph."* True of emitting, irrelevant to receiving. `work-` is already terminal and receive-only, and exists precisely to be pointed at.

    **The candidate half is scoped by region, which is the whole discriminator and is why it is not a proximity heuristic.** A claim resting on a definition carries no `\ref` to it, because definitions are not labelled the way claims are. The rule: a claim whose **statement block** uses a defined term becomes a candidate for depending on it. A theorem statement is terse — authors do not pad a proposition with decorative vocabulary, so every term in one is load-bearing — where the same word scattered through surrounding prose carries no signal at all: searching a paper *about* calibration for "calibration" finds it everywhere and discriminates nothing. Scope the match to the claim's own statement block and nothing else. A second discriminator comes free and needs no constant: a term appearing in most claim statements is a topic word, one appearing in three of forty is a dependency signal, so rarity *within this corpus's claim statements* weights the match.

    **Out of scope**: matching on any word of a definition's body; matching outside the claim statement; any proximity or same-document rule; and any rule that authors a `depends` edge directly. A match produces a **candidate**, never an edge — it feeds `attribute.narrow`'s existing settle-or-ask path, and `_source_end`'s rule is not relaxed. **The node half alone is worthless** and makes the orphan picture worse, adding nodes nothing points at.
