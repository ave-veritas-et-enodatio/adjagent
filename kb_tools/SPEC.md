# SPEC – kb_tools

`kb_tools/` is a portable, stdlib-only Python toolchain — paired with a set of
specialized agents — for turning a canonical authored corpus into a
**knowledge base (KB)**: a navigable, verbatim Markdown distillation of the
source that also carries a queryable *claim-graph metadata spine*. The
tooling **builds, validates, and queries** that spine; the agents **create,
navigate, and maintain** the KB on top of it (roles and mechanism in
ARCHITECTURE.md, The Agent Set). This document states the observable
contract — what a KB *is* and what the toolchain *guarantees*, independent of
how. See ARCHITECTURE.md for mechanism.

The toolchain authors no content and is corpus-agnostic. Python 3.11+,
standard-library only (Corpus Invariants, below).

## What a KB Is

A KB is a directory of Markdown that mirrors a canonical authored corpus and
layers structured, machine-checkable metadata over it. Two orthogonal graphs
run through it, and this toolchain builds and checks both:

1. **Topography graph** (navigation) — a hyperlink tree: `entry-point.md` →
   domain `index.md` → subtopic `index.md` → leaf. Every non-root document
   opens with an up-link to its parent. A document's `kind`
   (`entry-point` | `index` | `leaf`) is its structural-position label only;
   it does **not** encode claim-graph flavor.
2. **Claim graph** (the metadata spine) — an acyclic graph whose nodes are
   the corpus's formal results and whose edges record how they depend on and
   reinforce one another. Node kinds and edge classes are enumerated in
   Claim-Graph Nodes and Edges, below. Nodes are declared additively in leaf
   frontmatter; a leaf is a **container** hosting any combination of node
   bodies. The format contract is executable, not prose: the write API
   (`kb_write/`; ARCHITECTURE.md, The Write API (`kb_write/`)) renders every
   metadata byte and `kb_index_lib` parses it back, and no KB carries a local
   copy of a contract. Which kinds and classes a given KB populates, and
   against which corpus, is pinned in `<kb-root>/CLAUDE.md` — see Project
   Scoping, below.

**A leaf's body is verbatim.** This is what lets the KB stand in for the
source: the derivation described next is mechanical the whole way, so no
leaf body diverges from the corpus for anything to resolve.

### Leaf Bodies Are Derived

**In build production, a leaf's body is a function of the extent it is derived
from, computed. No inference writes one there.** The translation from the
corpus's own markup to a leaf's Markdown is mechanical the whole way — the
maths carried through byte-exact, the citations resolved out of the corpus's
bibliography, the cross-references resolved to the documents the derived tree
places them in — so the body is the same body on every build of the same
source, and a *difference* between two builds' leaves would be a defect with no
upstream to charge it to. This is what makes the verbatim property above a
guarantee, on this path, rather than an instruction: a translation nobody
performs cannot paraphrase.

**Hand-authoring a leaf is a different origin, not the inversion of the
computed case.** Adding and editing leaves, and migrating finished work into
canonical ones, is `kb-maintainer`'s ordinary job (ARCHITECTURE.md, The Agent
Set, the `kb-maintainer` row). A leaf the build produced is a mechanical
rendering of one extent of the corpus, and repairing it means repairing it
against that extent. A leaf a maintainer authors — migrating `session/` work,
say — was never a rendering of anything, so it is the KB's own text from the
start: there is no upstream extent for it to derive from or be checked
against. What is the same across both origins: a leaf's metadata still
reaches the file only through the write API, and every derived field on it is
still derived, never authored, whichever origin wrote the body beside it.

What is *said about* a leaf is inference's still: which registered claims it
establishes, or the reason it establishes none. That declaration is metadata and
reaches the file the way all metadata does, through the write API — as do a
multi-claim leaf's in-body markers, which the build places from the leaf's own
words (ARCHITECTURE.md, The Leaf-Body Renderer and The Driver).

### The Skeleton Is Derived

**The topography graph's shape is a function of the source, computed, down to
the leaves. No inference authors it.** The corpus's own segmentation decides the
tree: a source section holding no subsections is one KB document, a leaf; a
section that holds subsections is an index, and the prose it owns ahead of them
becomes a leaf of its own beneath it — so a document with children carries no
source of its own. The hierarchy above them is the hierarchy the source
declares. A KB built twice from one corpus has one tree, and the tree can be
recomputed from the corpus at any point in a build rather than read back from
something a stage wrote down.

Two properties follow, and they are what the toolchain guarantees rather than
asks for:

The extent a document is derived from is **one whole-volume rendering**,
produced by a single parse of the volume root, and never the source file as it
sits on disk. Notation, theorem numbering, citation rendering and the
bibliography are resolved once, across the whole document, by that one parse —
never per document: slicing raw or independently re-rendered source instead
would let each slice compute its own numbering, and the defect would be a
plausible-looking *Theorem 1* in every leaf. A document's body is a cut of
that one rendering, never a re-render of a slice (ARCHITECTURE.md, The
Document Graph (`kb_docgraph/`)).

- **Every heading the source declares reaches exactly one document, and every
  document is a contiguous cut of the volume's own rendering, never a
  re-render.** This is a partition, and it is checked twice on every build
  against the two different things that can lose content: the volume's
  rendering against the reader's own parse, which catches what the *reader*
  drops, and the tree against that rendering, which catches what the
  *splitter* drops (ARCHITECTURE.md, The Document Graph (`kb_docgraph/`)). A
  source file reached from two volume roots is converted once per root — each
  root is its own parse — so its material stands under each root's own tree,
  which is the same answer a corpus of overlapping volumes always got, arrived
  at because each root is independent rather than because anything scoped it
  that way.
- **Every document's place is legal by construction.** Its parent exists, it
  falls under exactly one domain, and its path is one leaf discovery can see.

What inference still decides about the tree is what is *said* about it — the
invariants and where they sit, the navigation spec, the acceptance criteria —
and, at one named point, whether a derived placement should be re-cut. That
judgement stage exists and is exercised on every build; today its heuristic
names no section and the skeleton passes through unaltered (ARCHITECTURE.md, The
Derived Skeleton).

**Derived-layer direction.** Within the KB, the **`.index/*.jsonl` artifacts**
and every derived metadata field (`solidity`, `build_status`, `build_band`,
`subtree-claims`, leaf-reference footers) are always derived from the
authored Markdown, never the reverse — on any disagreement the derived layer
is rebuilt, unconditionally (Derived Metadata, Defined, below).

### The Document-Tree Contract

The Topography graph is not authored directly: it is produced by a build
stage that reads a canonical LaTeX corpus, and the claim graph is built over
that stage's output as its **sole input** — never over the corpus, never over
an intermediate the stage happened to keep. Any stage that produces a tree
conforming to what follows may replace the current front end (`kb_docgraph`;
ARCHITECTURE.md, The Document Graph (`kb_docgraph/`)) without the claim-graph
builder, or anything else downstream, noticing. That is what makes the
following normative rather than descriptive; the numbering is fixed and is
what other documents and code cite by point number.

1. `entry-point.md` at the root lists every top-level volume (this section's
   "domain," above) with a link.
2. **The hierarchy is the author's, at whatever depth they wrote it.** Every
   heading level the source declares becomes a level of the tree; there is no
   cutoff and no maximum depth. A heading inside an author-distinguished block
   is the exception, and it is point 12's rather than a cutoff: it is that
   block's content and no level of anything. A node with descendants is `<slug>/index.md`;
   one without is `<slug>.md`. **Normalisation is ranking, not subtraction**:
   a volume's tree levels are the *rank* of each heading level among the
   distinct levels that volume actually uses — `{1,4}` becomes tree levels
   `{1,2}`, `{1,2,4}` becomes `{1,2,3}`, `{1,3}` becomes `{1,2}`. Ranking is
   per volume; levels need not agree between volumes. Judging a decomposition
   undesirable (The Derived Skeleton, above) is never done by truncating it.
3. Every non-root document opens with an up-link to its parent index, on line
   1: `[↑ Parent Title](relpath)`, carrying `kb_index_lib.UPLINK_MARKER`.
4. Every index links each of its children, in the source's own document
   order — never sorted.
5. The down-link spine is acyclic and total: every document is reachable from
   `entry-point.md` by down-links alone, and every non-root document has
   exactly one parent.
6. **Author cross-references are a second edge set, and may contain cycles.**
   Two passages pointing at each other as *see also* is the author's
   argument, not a defect. Nothing checks this graph for acyclicity — only
   the spine (point 5) is checked. A consumer that took cross-references as
   dependency edges would import the author's cycles into a graph that must
   stay acyclic.

   **That is an obligation to check, not a prohibition on deriving an edge.**
   A consumer may derive a dependency edge from a cross-reference where
   something other than the reference itself supplies the direction — a
   reference inside a proof runs from the claim that proof establishes, the
   containment being part of the markup — provided it proves the result
   acyclic before it writes it, and never stores a cycle it finds as a
   dependency graph. Where those edges go instead, and why a cycle costs them
   and nothing else, is the `references` class's (Claim-Graph Nodes and Edges,
   below). What is forbidden is importing
   this edge set unchecked, which is what would make the acyclicity the claim
   graph guarantees a property of the source rather than of the consumer.
7. Every internal link resolves, and every rewritten anchor lands on the node
   that held the label it targets. A label that cannot be resolved renders as
   the raw label text rather than as a broken link — a legitimate contract
   outcome, not a defect, and the same visibility standard point 10 sets for
   an unresolvable citation. An equation's `\label` is recoverable
   specifically because point 9 guarantees it survives verbatim inside the
   maths fence; nothing else in the tree carries it.

   **A cross-reference's rendered form is part of the contract**, as point 12's
   labelled block is: a consumer keys on the shape directly. The form, the
   identifier a reference resolves against, and the rule joining the two are
   stated together in Corpus Invariants, below — the reference is only half of a
   join, and half a contract is what let both halves go unstated.
8. **The tree is a partition of the source's content** — the partition
   already stated above (The Skeleton Is Derived) checked to the following
   specification, because there are two different things that can lose
   content. **Check A** compares the source's own parse against the
   whole-volume rendering and catches what the *reader* drops: every
   block-level element and every content-bearing metadata entry — the
   metadata walked recursively, not enumerated by container type, so a
   container a future metadata field arrives wrapped in is not silently
   skipped — must reach the rendering, matched by contiguous run rather than
   token-set overlap, because token overlap scores a dropped abstract as
   present when it restates the body's own vocabulary. **Check B** compares
   that rendering against the tree and catches what the *splitter* drops:
   every part of the volume's rendering reaches exactly one document, none
   dropped, none duplicated, Markdown compared against Markdown. A heading
   reaching both its own document and its parent's child list, and a
   parent's title reaching its children's up-links, are navigation and are
   exempt from check B — they are not duplication.

   **The elision either check exempts is closed** —
   `kb_docgraph.outline.APPARATUS_KEYS` plus the title-page marker (point
   13) — **and nothing else.** Widening it is a specification change, not a
   code change. This closure is what makes points 9 through 11 enforceable
   rather than aspirational.

   **Known hole: neither check sees what the reader drops before its own
   parse exists.** Content discarded at that stage is absent from both sides
   of the comparison and reads as clean (ARCHITECTURE.md, The Partition
   Checks, for the confirmed instance). A build is therefore not
   self-certifying against a reader-level drop, and a corpus exercising a
   construct this pipeline has not yet seen needs a probe before its tree is
   trusted.

   **Where the reader says what it dropped, the build stops on it — that half
   of the hole is closed.** A source file the reader was told to include and
   could not load takes its whole content out of the parse, so no check above
   can bite on it and the reader's own complaint is the only evidence that the
   file was ever named. **A build meeting one fails, naming every file that did
   not load and the line of the source that named it**, rather than producing a
   paper without its body. It is the reader's report that is acted on and not
   its silence: a construct the reader drops without saying so is still the hole
   stated above.
9. **Mathematics survives**: every mathematical element in the source reaches
   a document, none degraded to text, and the count matches — that is the
   enforceable claim.

   **Maths inside presentation apparatus is outside it, and point 13 is the
   reason rather than an exemption bought here.** Apparatus enters no document
   at all, so an affiliation marker's `^{1}` on a byline has no document to
   reach and nothing was lost when it did not: this guarantee is about content
   reaching a document, and where point 13 has already ruled there is no
   content, it has nothing to bite on. So the metadata this claim ranges over
   is exactly what point 13 calls content — the volume's title and its
   abstract — and an abstract's maths is checked in full, an abstract being
   where a paper states its result. What keeps that from being a hole a later
   change widens is that the two lists are one list: point 13's split is
   closed, and a key moved across it moves this claim's reach with it, in the
   same specification change.

   **Display maths is a fenced block holding the LaTeX verbatim** —
   `\begin{equation}`, `\qquad`, internal line breaks, and `\label`, all
   included. That is a guarantee, not an observation: point 7's equation-label
   recovery depends on it, and a future change that normalised the fence's
   contents would break that recovery silently were this not stated.

   **The fence's opening delimiter is literally `` ``` math ``, with a space
   between the backticks and the word.** The info string is `math`; the
   spelling is not `` ```math ``, and a recogniser keyed on the unspaced form
   matches nothing.

   **A fence may sit inside a blockquote, prefixed `> `, and it may sit
   indented at a list item's own content column** — an author's display maths
   inside a labelled block (point 12), and an author's display maths inside an
   `enumerate`. A fence scanner that assumes column zero misreads the first,
   and one that allows the three spaces CommonMark grants a *top-level* fence
   misreads the second: a single `1.  ` marker puts its item's content four
   columns in and a nested one puts it further. The failure is not local: an
   unclosed fence swallows the content that follows it. **Those columns are the
   item's and not the maths'**, so the LaTeX the fence holds verbatim is what
   remains after they come back off. **A fence closes on its own delimiter and
   on nothing else**, an author's `~~~` spacing inside a display equation being
   equation content rather than a tilde fence.

   **One exception, stated so it is not mistaken for a violation: maths
   inside a figure caption is emitted as MathML, with the LaTeX preserved in
   an `annotation` element.** Nothing is lost; only the form differs from
   maths everywhere else. "None degraded to text" is the criterion, and
   MathML carrying its own LaTeX annotation is not degradation to text. **A
   caption is raw HTML, so that annotation is XML text**: its LaTeX arrives
   with `<`, `>`, `&` and `'` written as character references, and a consumer
   comparing it against the source's own spelling resolves them first.
10. **Every citation survives, carrying its own key.** A citation reaches the
    Markdown wrapped in the reader's own citation markup —
    `<span class="citation" data-cites="key1 key2">` — whatever became of it
    inside: resolved to a readable author-year reference, rendered `(**key?**)`
    because the bibliography did not answer it, or rendered as its own keys
    because no bibliography was offered. **One recognisable form across all
    three**, so a consumer finds every citation without knowing which state it
    is in, and no state is silently absent. For warrant attribution the
    difference between a document proving a result and one citing whoever
    proved it is the whole question, so a dropped citation is a claim-graph
    defect and not a cosmetic one.

    **A bibliography is an input a corpus may not have, and its absence is not
    a failure.** Many sources ship a pre-generated reference list or inline
    entries and no bibliography file at all. Given one, citations resolve and
    the volume gains the reference list of the next paragraph; given none,
    citations still reach the tree by their keys and the volume gets no
    reference list — which is the same outcome the next paragraph already
    admits for a volume the processor emits none for. What is lost is
    resolution, never the citation.

    **A corpus may ship several, and that is not a choice for anyone to make.**
    Every bibliography a build is given is read as one collection, and only the
    entries a source cites are rendered — so a file carrying works this corpus
    does not cite reaches no document, and a source declaring two files as one
    bibliography is an ordinary corpus rather than a conflict. Where one key is
    defined twice the first file given answers it, and the order the files are
    given in is fixed by the build rather than by a directory read, so two
    builds of one corpus resolve alike.

    **A bibliography the reader cannot parse is the same input as none**, and
    takes the same degradation rather than stopping the build: the volume is
    converted as though it had been offered none. It is **reported as a
    finding naming the file**, which absence is not, because the two are the
    same outcome arrived at from different places — one is what the corpus is,
    the other is a file the run was handed and could not use, and a silent
    fallback would leave a reader of the build's own output believing the
    citations resolved. Citation
    processing appends apparatus with no source counterpart — a references
    block, one bibliography entry per work, and inline formatting markup
    around an author's name — so a check asserting a strict bijection
    between source and rendering fails on the apparatus, and a text
    comparison that does not strip markup reads the formatting as a false
    drop.

    **The reference list is one document per volume, at
    `<volume>/references.md`, titled `References`.** It is a leaf under the
    volume index like any other — up-linked, listed among the index's children
    last, and inside the partition. Its placement is stated here because the
    citation processor appends the block at the end of the content stream, and
    a tree that split at headings alone would leave it inside whatever section
    the source happened to end with: deterministic, incidental, and moved by
    the author's next closing section. Warrant attribution reads this list to
    tell a document proving a result from one citing whoever proved it, so it
    is addressable from the tree's shape rather than found by searching every
    document for the block. **This is a relocation, not an elision** — point 13
    is not widened by it, and a volume the processor emits no reference list
    for gets no such document rather than an empty one.
11. **Figures — tabled.** None of what follows gates conformance; a claim
    graph built from text is enough to prove the pipeline lives (ROADMAP.md,
    the inline-drawn-figures item).

    An external image present on disk is copied into the tree and embedded
    as a self-linking image, `[![](<image>)](<image>)`, so it renders in the
    document and its target is the image itself; sizing is deferred. A
    source that names an image not shipped alongside it is a reported
    finding, not a build stop — the document simply lacks that figure,
    loudly, in the report.

    An inline-drawn figure (a `tikzpicture` and its kin) is inlined as
    source, the way mathematics is: a fenced block whose info string names
    the drawing environment it came from (`` ```tikz ``). Naming the
    environment in the output is not the same as keying identification on
    it — what marks a block as raw source to inline is structural, not the
    environment's name, and the name is read off the block only once it is
    already identified. A caption is text in either case and is never
    dropped.

    This is the target the contract will hold the stage to once figures are
    taken up; it is not yet enforced.
12. **Author-distinguished blocks render as labelled blockquotes, carrying the
    name that says what the block is** — the display name where the source
    declared one, and the environment's own name where it did not. This is how
    a later stage finds claim sites *and classifies them* without re-reading the
    source, and it is why the name must survive rather than whatever wrapper the
    declaration expands to. The rendered form is part of the contract, not an
    implementation detail — a consumer keys on this shape directly:

    ```
    > **<block-name>**
    >
    > <the block's content>
    ```

    Content and label are separated by a blank quoted line.

    **A heading inside such a block is that block's content, not a heading of
    the document.** An author writing `\paragraph{Step 1.}` inside a proof has
    named a step of that proof, and splitting the tree there would tear the
    block into pieces, hang half a proof off the outline, and leave neither half
    in the rendered form above — so the block reaches one document whole, its
    own internal headings quoted along with the rest of it. This is what point
    2's "every heading level the source declares becomes a level of the tree"
    ranges over: the document's headings, not every `#` in the rendering. A
    label on one of those headings names the document the block landed in and
    nothing narrower, a heading's label surviving as no identifier anywhere
    (Corpus Invariants, the definition end).

    Where the source
    carried a `\label`, the identifier wraps the name and is addressable:
    `> <span id="thm:bif">**Theorem**</span>`. That identifier is the definition
    end of the cross-reference join (Corpus Invariants, below) — a block that
    stopped carrying it would still render, and every reference to it would
    quietly become a reference to the whole document.

    **Which of the two strings a `\newtheorem` declares is the name, and why.**
    A declaration names an environment twice: an internal handle the source's
    own markup uses, and a display name saying what the thing is.
    `\newtheorem{claimbox}{Result}` renders the label line `Result`, not
    `claimbox`, and the content beneath it begins `**Result 1**` — the same
    word, carrying a number this line alone has. The handle is arbitrary and
    private to the source: across surveyed corpora it is aliased (`Cor`, `Pro`),
    starred (`lem*`) and written in other languages (`Teo`, `oss`), so a
    consumer classifying on it is classifying on nothing, and no table of
    handles closes. The name is as the source spells it, capitalisation
    included; a consumer matching it folds case. **Where a declaration gives its
    display name as a translation macro** — `\protect\theoremname`, babel's
    standard vocabulary — the label carries the word that macro stands for,
    never the macro.

    **Where no display name was declared, the environment's own name is what
    the label carries.** An author's `\newenvironment` declares one string, and
    `amsthm`'s `proof` is declared by nobody; both render under the only name
    they have. A consumer therefore meets both kinds on one line and needs no
    way to tell them apart.

    **The environment's optional argument survives, on the content's first
    line**:
    `\begin{theorem}[The governance bifurcation]` renders `**Theorem 2** (The
    governance bifurcation).` beneath the label — a consumer wanting the
    author's own title for a block reads it there rather than inferring one.

    **The optional argument is preserved where it was written and is not
    required.** Most blocks carry none — `\begin{lemma}` untitled is the norm
    outside corpora written with tooling that prompts for a title — and such a
    block's first line renders the environment's printed word and its number
    alone: `**Lemma 3**.` That word and number are what the block carries, and
    what a consumer wanting a title for it reads there — the clause above
    holding in the other direction too, so where the author titled nothing there
    is nothing to read and still nothing to infer. An unnumbered environment
    (`\newtheorem*`) renders the word without a number, so several blocks of one
    document can present the identical line; what tells them apart is the
    author's own content beneath, and nothing else on the page does.

    **These blocks are a minority affordance.** Most leaves carry none. A
    consumer that needs every claim site in a document cannot rely on this
    marking to find them all.
13. **Presentation apparatus is elided.** Title pages, cover matter and the
    like do not bear on the logical construction of an argument or proof, and
    are dropped; a title page announces itself structurally, so eliding it is
    a named-marker test rather than a heuristic about what leading content
    looks like. An abstract is not apparatus — it states the document's
    claims — and is retained, with whatever else precedes the volume's first
    heading, in a leaf beneath the volume index; the index keeps its own
    heading. Document metadata
    splits by the same criterion, and **the criterion classifies while the list
    only enumerates the keys it has already been applied to**: the volume's
    title and its abstract are content; a byline, the author's institutional
    address, a draft date and the bibliography path are apparatus. **The list
    stays closed even so.** A key nobody has classified stops the build naming
    itself, because a metadata channel that silently reaches no document is how
    author-supplied content is dropped with nothing said about it — so what the
    criterion settles is how the *next* key is classified here, never that the
    code may classify one on its own.

    **This elision reaches point 9's count as well, and reaches it here rather
    than there.** An apparatus key's maths — the `^{1}` markers an author hangs
    off a byline — reaches no document because the key does not, so a check
    asking point 9's question over it reports the elision as a drop and the two
    points read as contradicting each other. They do not: this one runs first,
    and what it removes is gone before there is a document for the other to ask
    about. Point 9 therefore ranges over the content half of this split and no
    further, and a key reclassified here changes that reach in the same act.

    The bibliography path in particular is not source content at all — it is
    an invocation argument the tool passed itself — so retaining it would
    land a host-specific path in the volume index; the only attribution a
    claim graph needs is which work proved a cited result, which is point
    10's job, not the volume byline's. A volume whose source declares no
    title is named by its filename stem.
14. **No claim-graph artifacts.** No `.index/` directory, no frontmatter
    block, no `claims:` key, no `<!-- id: -->` or `<!-- claim-quality: -->`
    marker anywhere in the tree. A document carries body text, a heading, an
    up-link, and — for an index — a child list, and nothing else.

Mechanism for checking each of these lives in ARCHITECTURE.md, The Document
Graph (`kb_docgraph/`) and The Partition Checks; this section states what
must hold, not how it is verified.

### Claim-Graph Nodes and Edges

**Node kinds.** The minted kinds are `kb_schema.ID_KINDS` — `clm-` (claim),
`exp-` (experiment), `sup-` (support) — each id a prefix plus a
collision-checked `[a-z0-9]{6}` body. **Framework nodes are not minted.**
They are authored headings in `kb-root/invariants.md`: a `### INVARIANT-*`
heading is an `invariant` node under its own label, and each `- Axiom N:`
bullet beneath the `INVARIANT-S2` heading is an `axiom` node under the id
`axiom-<N>`. Framework nodes are solidity-1.0 by definition and carry no
scoring fields. Claim nodes are the universal core; the other three kinds
are populated per project (Project Scoping, below).

**The external work is the fifth kind, and it is not minted either.** A
`work-` node stands for a work the corpus **cites and does not contain** — the
off-graph endcap. Its identity is the citation key, so its id is `work-` plus
that key and nothing mints one: a key is what a citation carries, and a build
resolves every citation against one collection however many files that
collection was assembled from, so a key names one work corpus-wide by
construction. **It is tied to the bibliography and not
to the per-volume `references.md` leaves**: a work three volumes cite is one
node, because its standing is a property of the work, and those leaves are
*citers* of it rather than homes for it. A `work-` node is authored as a
register entry like a `clm-` or a `sup-`, in the KB root's own register so that
one file holds the corpus's works; it carries a hand-authored `strength` — the
work's standing, foundational at 1 through spurious at 0 — and nothing else. It
is **terminal**: it emits no edge, because whatever the cited work rests on is
outside this corpus too.

**A work survives a corpus with no bibliography.** A citation carries its key
whatever became of it (point 10), so a key nothing answered still stands up a
node — titled with the key itself, which is honestly the whole of what the
corpus says about the work. Where a bibliography did answer, the node is titled
with that work's own rendered reference-list text.

**Bidirectional coverage does not reach it, and that is stated rather than
implied.** Every `clm-` register entry must be back-linked by a leaf's
frontmatter; no leaf can ever name a `work-` entry, because the node is a paper
this corpus does not contain rather than a result one of its documents states.
Framework nodes stand outside that relation for the same reason, so this is one
more kind outside it and not a schema without precedent. What holds in its place
is the other direction: every `rests-on` edge's target has an entry.

**Edge classes.** Five, discriminated by the `relation` field on
`kb_index_lib.DependsOnEdge`. Every edge of every class is materialized in
`.index/depends-on.jsonl`.

| `relation` | Authored at | Source → target | Also carries | Contribution to solidity |
|---|---|---|---|---|
| `depends` | the source's `- depends-on:` bullets in its register entry | claim or support → claim, invariant, or axiom | — | the **target** gates the **source**: its final solidity enters the source's `min`, a framework target contributing 1.0 |
| `strengthens` | the experiment leaf's `strengthens:` frontmatter block | experiment → claim | `strength`, in [0, 1] | the **source** lifts the **target**: `max` of the strengths over *run* experiments is the claim's experimental solidity |
| `supports` | the support leaf's `supports:` frontmatter block, and staged in the `sup-` register entry | support → claim | on-point `fraction`, in [0, 1] or the literal `*pending*` | the **source** lifts the **target**: `sup_solidity × fraction` enters the claim's local quality, and is still dep-gated |
| `rests-on` | the source's `- depends-on:` bullets in its register entry, where the target is a `work-` id | claim → external work | applicability `fraction`, in [0, 1] or the literal `*pending*`; `strength` is null, a work's standing being a property of the node and not of each pairing | the **target** gates the **source**, on a positive applicability: the work's own `strength` enters the source's `min`. A zero applicability takes the pairing out of the `min`; either value `*pending*` leaves the source pending (below) |
| `references` | the source's `- references:` bullets in its register entry | claim → claim | — | **none.** It enters no solidity computation, gates nothing, and is under no acyclicity constraint |

**`references` records what the corpus states and computes nothing from it.** An
author who writes one result's identifier inside another's statement — *a second
certificate, distinct from the `V` of Theorem 2*, *not derivable from the bare
cap-table dynamics (Proposition 8(iii))* — has stated a relationship between two
claims in formal notation. Most such references are not dependencies, and
recording only the dependencies discards the rest to avoid a wrong edge, which
leaves the graph asserting the two claims are unrelated: a different wrong
answer, arrived at silently. So the relationship is recorded as what it is.
Three properties are what keep the class from being a weak `depends`, and each
is a requirement rather than an observation:

- **Direction comes from the markup, not from judgement.** The source is the
  claim whose text carries the reference; the target is the claim the reference
  resolves to. Nothing infers it and nothing is asked about it.
- **It is under no acyclicity constraint.** Two claims naming each other is the
  author's argument, not the defect a dependency cycle is — point 6's own
  reading of a cross-reference set that may contain cycles. `check_acyclic`
  continues to run over `depends` alone, and a corpus whose claims name each
  other mutually builds.

  **This is also where a ring of derived dependency edges lands.** Where a
  build directs edges by the containment rule point 6 admits and the directed
  set closes a cycle, every edge on that cycle is recorded in this class
  instead, and the build carries on. The cycle proves those edges cannot all be
  dependencies; it proves nothing about any other edge the corpus yielded, so
  producing no graph for that document spends a whole paper on one ring and
  records every claim in it as resting on nothing. The relationship each
  demoted edge states is kept — the author did write one claim's identifier
  inside another's proof — and only the direction claim is given up, which is
  the half the cycle disproves. The whole ring goes rather than a minimum
  feedback set: a ring is exactly where no evidence separates its members, so
  keeping some would assert a discrimination nothing in the corpus supports.
  **A build reports every demotion, naming the edges**, a corpus whose derived
  edges are acyclic and one that lost a ring being otherwise the same passing
  build.
- **Nothing downstream may treat it as a weak dependency.** It contributes to
  no solidity, lifts nothing, gates nothing, and `compute_solidity_full` does
  not see one: the class is carried on a field of its own
  (`kb_index_lib.ClaimEntry.references`) rather than among the edges that
  compute, so the guarantee is structural and not a branch each consumer
  remembers to write.

**Recording a reference is not answering the dependency question.** Where a pair
is both recorded here and put to inference, the ask still happens; a build that
suppressed it because it had already written something down would trade the
answer for the note.

**`depends` and `strengthens` differ temporally, not computationally.** Both
resolve in the single pass of `kb_index_lib.compute_solidity_full`; neither
has a computation of its own. What separates them is *when* the cited work
stands relative to the claim: a `depends` target is prior standing the claim
is derived from, and a `strengthens` source is later evidence raising a claim
already stated. They are distinct types because that relation differs — the
branch each feeds follows from the temporal difference rather than defining
it.

**`strengthens` is a push edge; `strengthened by` is a pull edge, and the two
are not halves of one relationship.** A claim's `- strengthen-by:` bullets
are its own open-work list — what *would* raise it — authored at the claim
end, kept as free text, and materialized in `.index/strengthen-by.jsonl` as
untraversed bookkeeping: no `DependsOnEdge`, no contribution to any
solidity. A `strengthens` edge is authored at the experiment end and names
the claim it lifts. Neither implies the other. An item on a claim's list that
no experiment has answered is the normal state, and an experiment
strengthening a claim whose list never named it is equally normal; **no gate
pairs the two, and one direction standing alone is not a bookkeeping
defect.**

**An off-graph dependency propagates, and that propagation is the point: a
`rests-on` edge joins the same dependency gate a `depends` edge does.**
`compute_solidity_full` takes one `min` over a claim's local quality, its claim
deps' finals, and — for each pairing scored above zero — the target work's own
`strength`. So a claim resting on outside work is no better founded than that
work is, which is the fact the graph exists to carry. The two authored values
divide the job: applicability decides *whether* the pairing gates, `strength`
decides *how hard*. Both `*pending*` until a person supplies them, and while
either is, the citing claim's derivation is pending — a claim resting on
unjudged outside work genuinely has unknown solidity, so the contagion is not a
defect to route around but a judgement the corpus owes, and supplying the two
values clears it. What the edge is *for* is telling a claim that depends on
nothing from one whose warrant is outside reach: those are the same zero in an
edge count and are not the same epistemic state.

**The lift shape was considered and rejected as directionally wrong.** Reading
the pairing as `strengthens`-like — a `max` over `strength`, or the `supports`
row's `strength × fraction` — inverts under a gate. The product makes the gate
*harsher* as applicability *falls*, so a work judged sound and irrelevant
(`strength 0.9`, applicability `0.0`) would drive the claim to zero; and a
`max` cannot let a citation hurt, which is the one thing a spurious-work
judgement must be able to do. Every arithmetic that discounts toward `1.0`
instead — `1 − f(1−s)`, `max(s, 1 − f)` — treats ordinal bands as
probabilities, which the `min` gate exists to refuse, and puts an operand
written on no disk anywhere inside a rendered trace. The accepted consequence
of the shape chosen: applicability `0.3` and `1.0` gate identically. Grading
that would take a derived scalar, which the endcap does not have.

**Neither of the endcap's two scores is derived, and nothing may be pointed at
filling them.** The work's `strength` and the pairing's applicability are
hand-authored, both `*pending*` until a person replaces them, and no inference
pass, heuristic or default numeric value stands behind either. The failure mode
here is not a wrong number but a confident number about a paper no reader of the
build has encountered, which is worse than a blank.

**A zero on-point fraction is a value, not an absence.** It says the supporting
work is sound on its own terms and bears nothing on this claim — a relevance
asserted and found wanting. That is a distinct fact from no edge at all, and
from the `*pending*` literal, so the domain admits it: the alternative records
the judgement by deleting the edge, which is to say it does not record it. On a
`rests-on` pairing that settles the gate too: zero applicability removes the
pairing from the citing claim's `min` entirely, so the claim scores exactly as
it would with no such edge, and the work's `strength` is never consulted —
there is nothing to ask of a work the reader has already judged to bear
nothing here, pending `strength` included.

**`supports` is the one class recorded at both ends by design.** A support's
beneficiary fan-out is staged in the `sup-` register entry's `### Quality`
block *and* declared in the hosting leaf's `supports:` frontmatter, because a
`sup-` id is minted a full stage before the leaf that will carry it. The leaf
is canonical — the graph is built from that end alone — and where both ends
are live they must agree; a disagreement is a `verify` failure that refresh
cannot fix, both ends being authored. This property is `supports`' alone and
generalizes to no other class.

**Out of scope, by design — the reference KB's extra vocabulary.** The
reference KB at `kb-testing/test-data/transient/ave-kb/` carries `def-`
(definition) and `ilk-` (interlock-mechanism) node prefixes and an
`interlocks` edge relation; a reader meeting those ids there is meeting types
this toolchain does not mint or admit. `kb_schema.ID_KINDS` mints neither
prefix and `DependsOnEdge.relation` admits no such class, and their absence
is a boundary, not a gap. A definition node is terminal — it carries no
scoring fields and emits no edges — so it contributes nothing to either
graph this toolchain builds, checks, and scores; it is a term-adjudication
ledger for a particular authoring workflow, not a graph primitive. The
interlock mechanism's substance is a corpus-specific argument about one
document's calibration constants, and importing the type would import that
corpus's semantics into a toolchain that authors no content and stays
corpus-agnostic (above). Either type is added only against a demonstrated
need arising in a corpus this toolchain serves, never in anticipation of
one.

### Derived Metadata, Defined

- `confidence` (claims) / `quality` (supports) — **hand-authored** local
  rigor: how well the cited leaf's own derivation establishes the result,
  with everything it depends on taken as given. The grading scale ships in
  the scoring seats' own definitions, which is where a value is settled.
- `strength` (external works) — **hand-authored**, and a different question
  from local rigor rather than a third spelling of it: it grades a work this
  corpus does not contain and nobody here wrote, so there is no derivation to
  read. It is not a solidity and sits on no build band, but it does enter a
  computation: it is the gate term in the dependency `min` of every claim whose
  pairing with the work is scored above zero, and while it is `*pending*` every
  one of those claims is pending.
- `solidity` — **derived, never authored**: the weakest link in the
  dependency cone, lifted by the two lift classes above. That cone **ends at
  the corpus boundary rather than inside it**: a claim's `rests-on` edges reach
  the works it cites, so the weakest link may be an external work's `strength`
  and not any node this corpus contains. `*pending*`
  propagates NaN-style through the DAG — a pending dependency forces a
  pending result regardless of local confidence.
  `kb_index_lib.compute_solidity_full` is the rule in full; refresh and
  verify both call it, so the two cannot dual-compute and drift.
- `build_band` / `build_status` — **derived** from `solidity` via the
  build-band ladder, `kb_schema.BUILD_BAND_LADDER`.
- `subtree-claims` / leaf-reference footers — **derived** roll-ups /
  reverse-citation maps.

The dependency gate is a `min` and not a product down the chain,
deliberately: solidity must be refactor-invariant, so splitting one
derivation step into two same-quality steps cannot lower it, and a deep
clean chain cannot decay toward zero as an artifact of how finely it was
subdivided. The grades are ordinal bands, not independent probabilities to
be multiplied. A support's on-point fraction is one edge weight rather than
a chain, and so stays multiplicative.

Hand-editing a derived field is a `refresh-fixable` verify failure — the
derived-layer direction rule above is what `kb-verify`'s freshness gate
enforces mechanically (ARCHITECTURE.md, The Derived Index).

### The Claim-Graph Sheet

**A KB carries a picture of its claim graph at `<kb-root>/claim-graph.svg`, and
a stale one is a tool failure rather than a user error.** The sheet is derived
from `.index/` alone and authored by nobody: it draws every node and every
edge, with one exception — a `references` edge whose source already reaches
its target through the edges the sheet draws is left undrawn, because a reader
loses nothing a picture cannot show by walking the rest of it instead. That
exception is a boundary on the drawing and nowhere else: the claim graph
itself is never reduced to fit a rendering, `.index/` carries every
`references` record regardless of what the sheet does with it, and no
consumer of the index sees fewer edges than it always has. So the sheet is
kept fresh by the pair that already owns that index: **`refresh` mints it and
`verify` checks it**, an absent sheet and a hand-edited one being the one
refresh-fixable failure. The picture is how a reader understands the graph at
all, and an artifact someone must remember to regenerate is wrong exactly when
it is trusted.

**Nothing about the graph gates the drawing.** A cycle, an edge naming an id no
record carries, an isolated node, a whole disconnected component, no edges at
all, one node, none: each is a true picture of the KB that produced it and each
is drawn. The earliest point a sheet can exist is the first `refresh` over an
index, and that is where it starts existing — a spine with ten claims and
nothing attributed yet is precisely when the picture of ten unconnected claims
is worth having.

The comparison the check makes is what promotes determinism (Corpus
Invariants, below) from a property to a gate. **Perceptual locality is not part
of it**: a one-edge change may rearrange the sheet completely, and where the
rearrangement reads better that is a feature. Cross-run comparison is the
machine's job, over the records, not the picture's.

## Agent-Mediated Editing

KB navigation and editing go **through** the agent set (the project contract
mandates it), because the agents carry the verbatim and derived-field
discipline that raw file edits would silently violate.
The roster, and which agent owns which lifecycle stage, is in ARCHITECTURE.md,
The Agent Set.

## The Write API's Contract

Every metadata byte the toolchain puts into a KB is composed by the write
API (`kb_write/`; mechanism and module roles in ARCHITECTURE.md). A
consumer — human or agent — supplies **values** (a title, a rigor score, a
rationale, an edge target, a path); the tool renders the format, proves what
it composed by re-parsing it with the production parser, and replaces the
file atomically. No op accepts a preformatted heading, marker, or bullet:
layout is not an input. A leaf's prose is not metadata and stays its
author's — this API owns the metadata in the file and nothing else.

**The values file carries the values; the command line carries the op.**
Every write op takes `--values <file>` — a TOML file of `[[entry]]` tables
the caller writes with an ordinary editor, prose fields travelling in it
rather than through a shell. The op is never named inside the file: a second
source for which op is running is refused as an unknown key. **That refusal
is also how an op's key vocabulary is read.** The vocabulary is closed,
total and per-op, stated once in `values.OP_FIELDS`: a key the op does not
take is refused with the offending key, its line, and the complete set the
op does take; a required key left out is refused by name.

One file may carry a batch, and a batch is all-or-nothing — every edit is
prepared and proven before any of them replaces a live file, so a refusal
anywhere writes nothing anywhere.

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util <write-op> --values <path.toml>
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util insert-claim-entry --values <path.toml> [--create]
```

**Three outcomes, three exit codes, and they are not interchangeable:**

| Code | Meaning | Caller obligation |
|---|---|---|
| 0 | Written and proven. | — |
| 7 | Refused — the values are wrong and nothing was written; the report names the offending field and its line. | Fix the values, then call again. |
| 8 | Retry unchanged — the values were right and a concurrent writer moved the file. | Re-issue the identical call. Re-asking for values that were already correct is how a duplicate id gets written. |

The proof runs on a temp, so a live file never holds bytes nobody read back,
and after a 7 or an 8 there is nothing on disk to restore. Report lines are
the uniform `[kb-write] STATUS name detail` shape, every failure detail
carrying a `restore:` clause naming the corrective call.

**An insert mints its own id and prints it; there is no separate mint step.**
`insert-claim-entry`, `insert-support-entry` and `insert-experiment-entry`
draw the id and write its canonical entry in one act, so an id without its
entry is not a state the tool can produce, and an entry for an id the tool
did not mint is not one it will accept. An insert op takes no id; read the
minted one off stdout.

**A register that does not exist yet is created only under `--create`**,
declared on the two register inserts and on no other op. Without it, a path
naming no existing register is a refusal naming the path, so a typo cannot
succeed into a fresh empty file. `insert-experiment-entry` takes no
`--create` at all: an experiment's host is authored prose this tool never
brings into being.

**No write op writes a derived field.** An insert renders `solidity`, the
`(solidity …)` bullet annotation and the leaf-references footer at their
canonical placeholder identity — the exact bytes `refresh` recognizes and
replaces — and an update op rewrites only the field it names, leaving every
derived line byte-untouched. These ops author, `refresh` derives, and
`kb-verify`'s freshness gate is what says the two agree.

**One op reads instead of writing: `render-citation`.** Over the same
`--values` transport it prints the sanctioned authority-citation string —
`["<excerpt>"](<path>#<anchor>)` — after reading the cited document and
confirming the excerpt appears verbatim in the section the anchor names.
Writing nothing, it has two outcomes rather than three: **0** with the
citation on stdout, one line per entry, and **7** with nothing on stdout and
the reason on stderr. Its values are the `excerpt`, the `cited-document` it
is quoted from, the `anchor` naming that document's section, and the
`citing-document` the citation will be written into — the last because a
link target resolves relative to the file the link sits in, so where the
citation will land decides how its target is spelled. Where in that document
it goes stays the author's, and the citation gate still checks the placed
result: this op moves the excerpt check to the moment of writing, it does not
replace the gate.

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util render-citation --values <path.toml>
```

## The Driver's Contract

`kb_driver/` runs a KB build end to end: it sequences the stages, dispatches
each seat, checks what came back, records the ledger, and stops at a
decision instead of taking one. **No inference performs sequencing, loop
counting, recording, or display** — those belong to the toolchain, enforced
mechanically (ARCHITECTURE.md, The Driver). **A barrier is an *exit*, never
a prompt**: the driver never blocks on a human, so the answer arrives on the
next invocation through `--decide` or a config table.

**The build is driver-controlled end to end, and there is no coordinator
seat.** One process sequences a build: it dispatches every seat itself, reads
every tool's exit code itself, and records every boundary itself. Nothing in
this contract, and no artifact this toolchain produces, admits a second
controller — human or agent — standing between an invocation and the
driver's own step table; a build has exactly one process directing it, from
`start` through its last stage.

**A build has exactly two modes, fresh and resume, and resume is never
configured.** Which one an invocation performs is read off the ledger
itself — recorded stages mean resume, none mean fresh — re-derived on every
invocation rather than chosen on a command line or in a config table.
Revising a finished KB is not a third mode: it is the maintenance path's
work, a maintainer and a human cooperating through the write API, and no
driver invocation expresses it. The one thing a fresh launch must be guarded
against is destroying what a prior build already wrote: an invocation
finding `kb-root/` already `populated` (`kb_util.kb_root_state`) refuses,
naming what it found; finding it `absent`, or `spine-only` — holding only
the derived index, with no authored byte to lose — it proceeds.

**A build may spend no inference and still be a finished build.**
`--no-inference` drops every step that would cost a model call and the walk
carries on past it, so the run produces a real KB built without them rather
than a resumable stop. It is not a bound and not a substitution: the ledger
ops, the runner targets, the coverage checks, the barriers and every write
still happen for real, and every stage is still walked and still recorded.
`--dry-run` is the different thing it is often confused with — it replaces the
calls the driver dispatches with synthetic ones so the state machine can be
smoke-tested, and it makes no claim about a model spawned inside a tool the
driver invokes. `--through <stage>` is the one bound, naming the last stage to
walk by stage id or by the stage's own display name. **A bounded run is not a
failed one**: it leaves the ledger standing where it stopped, and the next
invocation resumes there rather than redoing what landed.

**Expensive work that succeeded is never discarded, and that is what decides
where a stage boundary falls.** A step that spends inference is followed by its
own recorded boundary, with no step that can fail standing between the two. The
shape this forbids is cheap work, then expensive work that succeeds, then a cheap
step whose failure throws that result away: the resume re-spends what had already
been earned, and nothing in the ledger ever showed that it was. So a stage is as
small as the most expensive thing in it that must not be repeated — where one
stage would hold two such steps, each takes a boundary of its own. The
classification this rests on already exists and already decides what
`--no-inference` drops; it decides this too, and a step tagged as spending
inference without a boundary behind it is a defect in the stage table rather than
a risk to be managed at run time.

**This is what makes resumption free of indeterminate state rather than merely
tolerant of it.** Every boundary is a commit, every write lands only on success,
and a resume returns to the last boundary and continues — so there is no state
where work is present in the tree and absent from the ledger for a later
invocation to reconcile, diagnose, or ask a human about. An output that no
boundary accounts for is discarded rather than trusted, which is safe precisely
because the rule above guarantees nothing expensive is ever in that position.

**Resumption's state has exactly one home, and it is the working tree —
never scratch.** `.claude-temp/` is disposable: wiped wholesale, without
warning, by the tooling that manages it. A run's own evidence — its log, the
calls it made, the briefs it composed, the barriers it raised — may
legitimately live there, because nothing resumes from it; a person reads it
after the fact, and its loss costs a diagnosis, never a rebuild. Where
resumption needs a home no other artifact already supplies, the ledger is
that home: a boundary is a commit, and a commit is in the working tree by
construction.

**The build states what it did without.** A stage's ledger boundary is
recorded either way, and where a step was dropped the boundary names it: what
that step would have authored is absent, and whatever the step before it wrote
stands. That is the only place it can be said — a document a step never read is
indistinguishable, in the KB itself, from one it read and found nothing in — so
a later reader is told rather than left to infer it from a count.

**A stage boundary asks two different questions, and only one of them is ever
excused.** A coverage check either asserts *this stage did its work*, which a
build that dropped the work has nothing to assert and which reports as vacuous;
or it asserts *the state handed across this boundary is valid for what comes
next*, which **no** path to the boundary excuses — however the state got there,
it must satisfy the contract the next stage is entitled to. The classification
belongs to the check and not to the stage, because one check may guard more
than one boundary and a per-stage answer would let two stages disagree about
it. A record says only what the build was; which checks that excuses is the
toolchain's own classification and is not nameable from a command line.

**A review's severities are the reviewer's ruling, and a gate over one reads
them rather than counting what they sort.** Where a stage ends on a seat's
review, what stops the build is a **critical** finding and nothing else. A
warning and a note are reported and the build carries on, the documents standing
as written with those findings outstanding against them. A gate summing open
findings discards the judgement it asked for: a note observing that a filename
goes unmentioned in a document would halt a build exactly as hard as a defect
does, which is the opposite of what a reviewer asked to decide what is critical
has ruled about it. A critical finding is the one a repair round is spent on,
and a review clean of them passes on its first pass.

**A severity that no longer stops must still be seen, and that is the other half
of the same guarantee.** The stage states each round's counts, one per severity,
and names the findings artifact so a reader can go look — in both directions,
a round that raised no warning and no note saying so in as many words. Whether
to promote a severity to a stop is a decision made later from the record of what
those findings actually were, so a silent carry-past would spend the evidence
that decision runs on; and an absence that reads like success is the failure
this states the zero form against.

**The build authors the claim graph and grades none of it.** Every rigor value
and every on-point fraction a build writes is the `*pending*` literal, and a
finished build leaves them there. Structure is the build's — the register
entry, the `depends` edge, the warrant attribution — and a *grade* is a
judgment about work that already exists, reaching the graph through the
maintenance tooling's own write ops on a later pass. `*pending*` is a legal
value everywhere the format admits one (Claim-Graph Nodes and Edges, above), so
no stage boundary is gated on a number and nothing in the build refuses one
left unscored. What the build does refuse is the converse: a seat that grades
its own minted work stops the stage.

**The entry point is the one document with a size budget.** It is read at the
start of a reading session and stays resident, so its length is paid on every
turn of that session. The build states the budget to the seat that writes it —
as a **word** count, and as the number to write to — and asks that seat to
measure nothing, a model estimating words far better than it counts tokens,
which it cannot do at all. What the build refuses is a runaway: a document
several times the budget stops the stage, and one merely over it is accepted,
because failing a seat for the error of writing to a number by eye would be
gating on arithmetic no inference performs. No other document carries a size
rule.

**The build derives the tree and grades none of it.** The skeleton is computed
from the surveyed sources (The Skeleton Is Derived, above) before any seat is
asked about the taxonomy, and a build that cannot derive a partition from its own
manifest stops rather than asking a seat to repair one — the failure is in the
tool, and no revision of any document answers it. A seat is handed the tree; no
seat proposes one.

**The build renders the leaves and copies nothing through inference.** Every leaf
body is computed from its own extent (Leaf Bodies Are Derived, above) and written
by the driver, so no stage asks a seat to read a source extent and no stage asks
one to transcribe it. What a seat is asked for is judgement about material that
already exists — the structural and conceptual reading a survey produces, the
claims a written leaf establishes, the summaries above it, the reviews — and the
build carries no stage whose product is a copy of the corpus in a second place.

**A build is launched, never composed.** One invocation opens a build, and
nothing is authored before it: the run's sources — the only field with no
default — are arguments to that invocation, and a charter is optional, a build
given none running on the sources it was given. The permission mode every
dispatched call runs under is defaulted rather than asked for, because the
driver is headless and a mode that gates a tool the build needs stops it with
nobody to answer; an invocation may override it, and **a run states the mode it
took, and the flag that changes it, before its first stage**. A config file
remains available for a run that sets more than the arguments carry, and where
both are given the argument wins for the field it names.

Exit codes are two mode-scoped ladders, enumerated in
`baton.RUN_MODE_EXIT_CODES` and `baton.WATCH_MODE_EXIT_CODES`. Every
terminating invocation prints a relay card naming what to ask and what to
run next, and an unrecognized code prints the fallback card rather than
being interpreted.

## Runner Targets

Per project policy, **do not** run these tools ad-hoc — use the consuming
project's runner target. The tools detect the runner and name it in
remediation hints. Every operational entry point below — seeding a KB,
checking the environment, opening a build, driving the build ledger, and
the consumer-facing `kb-verify` / `kb-refresh` / `kb-stats` targets — is
sanctioned only through the runner include and the `kb_util` CLI; mechanism
in ARCHITECTURE.md, Runner Targets and the Build Ledger.

## Citation Grammar

`invariants.md`, `CLAUDE.md` and `CONVENTIONS.md` are authored but are not
leaves; all three are in `EXCLUDE_NAMES`. Only `invariants.md` is the
framework-node source (Claim-Graph Nodes and Edges, above) — the two
orientation docs are **not** an invariant channel (Project Scoping, below,
for who writes them).

**Citation grammar** (`verify_citations.py`, part of `kb-verify`). A claim id
may appear only in a sanctioned channel — leaf frontmatter, a register's
structured `- <field>:` bullets, a `<!-- id: -->` or
`<!-- claim-quality: -->` marker, or a markdown link to a KB path. An
authority is cited as `["<excerpt>"](<kb-path>#<anchor>)`, the excerpt
quoted verbatim (whitespace-normalized), one line, at most 240 characters.

Scope is authored content: **leaf bodies are out of scope** (converted corpus
prose — an id or invariant name inside one is the source's own word, not a
citation this KB makes; a leaf's own obligations live in frontmatter and
markers, which are authored) and **`.index/` is out of scope** (derived
space — nothing under it is authored). Every check is
fence-aware: an example inside a fence is documentation. The one exemption
for prose is a **declaration section** of the framework source — an
invariant's own `### INVARIANT-*` section may name its siblings; the same
file's prose outside those sections may not.

## Corpus Invariants

- **Stdlib-only for Python, with one enumerated exception that is not a
  Python dependency at all.** No third-party Python package and no required
  virtualenv in committed tooling. `pytest` (+ `black`/`isort` for
  formatting) live only in the dev `.venv` the test/format targets
  provision; nothing under `kb_tools/` imports them at runtime.

  The single exception is **the `pandoc` binary** — the reader
  `kb_docgraph` converts LaTeX sources through, reached exclusively via
  `kb_tools/pandoc.py` (ARCHITECTURE.md, Module Inventory). It is a
  different *kind* of exception than the one it replaced, not a renamed
  copy of it: the vendored LaTeX parser this toolchain used to read sources
  with shipped inside the repository, so a consumer's clone was always
  sufficient on its own. Pandoc is not vendored, not pip-installed, and not
  demand-installed — it is a system binary this toolchain assumes is
  already present, so "a consumer clones, installs, and it works" is no
  longer the guarantee this exception carries. What it carries instead is a
  named failure: `kb_tools.pandoc.PandocMissingError`, raised in place of the
  bare `FileNotFoundError` an absent binary would otherwise produce mid-build,
  naming the install page rather than a stack trace. Demand-install,
  vendoring pandoc, and the GPL redistribution question a vendored copy would
  raise are none of them designed here — prototype scope, left open rather
  than assumed closed. (Changing that scope is a design decision, not an
  implementer's — see CONVENTIONS.md.)
- **Cross-reference resolution is a join, and both of its ends are specified
  here.** A claim graph's dependency candidates are found by matching a
  reference against the thing it names, and the two values matched are put there
  by two different parts of the reader. Neither end is an implementation detail,
  and both are observable in the delivered documents rather than in any
  intermediate.

  **The reference end.** A rewritten cross-reference (point 7) is an HTML anchor
  carrying three attributes, in this order:

  ```
  <a href="<path>#<fragment>" data-reference-type="<type>" data-reference="<label>">
  ```

  `<type>` is the referencing macro's own kind — `ref` and `eqref` are distinct,
  and a consumer that must not treat an equation as a node tells them apart
  here. `<label>` is the author's own `\label` as the source spelled it, and it
  rides every anchor whether or not the fragment does. **An anchor may be
  hard-wrapped between its attributes, and inside a labelled block (point 12)
  the continuation line carries that block's quote prefix** — so a recogniser
  anchored to a single line, or one reading the raw bytes rather than the
  quote-stripped ones, matches nothing.

  **The definition end.** Where the source labelled something the tree can
  address, the label survives *on that thing*, as an identifier the document
  declares: point 12's `<span id="thm:bif">` wrapping a labelled block's name,
  and point 9's `\label` verbatim inside the maths fence. A heading's label has
  nowhere to land in the rendering and survives as no identifier.

  **The join.** A reference's `<fragment>` is the label where the label survived
  as an identifier, and the target document's own heading anchor where it did
  not — so **`<fragment>` equals `<label>` exactly when the target document
  addresses `<label>`**, and that equivalence is the property a consumer uses. A
  reference whose two agree names an element of the target; one whose two differ
  names the target document itself. This is what lets a citation of a result be
  told from a navigational *see Section 4* without reading anybody's labelling
  convention: `\label{bifurcation}` on a theorem is as legal as
  `\label{thm:bif}`, and a prefix decides nothing.

  **Both ends fail silently, which is why they are stated rather than
  observed.** A changed reference form yields zero anchors, an edgeless claim
  graph, and a green build. A label that stopped reaching its block turns every
  reference to it into a reference to the whole document, and the graph goes
  quietly noisier rather than wrong. Nothing in the toolchain reports either
  today; this is the statement such a check would be written against.
- **Determinism.** `refresh` against a fixed canonical state is
  byte-identical (no timestamps, random ids, or environment-dependent paths
  in records). Mint-time randomness never enters the rebuild.
- **Single-source / anti-drift.** Paths, the KB directory name, command
  names, the build-band ladder, the id grammar, the package version
  (`__version__` in `__init__.py`, reported by every CLI's `--version`), and
  every shared computation have exactly one definition; consumers import it.
  A change flows through one constant, not scattered literals.
- **Derived-vs-authored split.** Derived Metadata, Defined, above.
- **Verbatim leaves + derived-layer direction.** What a KB Is, above.

## Project Scoping

The format fully specifies every node kind and edge class in Claim-Graph
Nodes and Edges, above, but **which of them a given KB populates is a
project decision, not a property of the toolchain.** Claim nodes are the
universal core. A KB populates:

- **framework nodes** only if its `kb-root/invariants.md` declares
  invariant/axiom headings (else claim chains terminate on dependency-free
  foundational claims, and out-of-graph modeling assumptions are noted in
  rationales as prose);
- **experiment nodes** only if it documents physical experiments the
  authors design and control (a simulation feeds derivation confidence, not
  experimental solidity; a re-analysis of outside data is a `sup-` or a
  `clm-` citation, never an `exp-`);
- **support nodes** only if it carries non-physical analytical support that
  lifts a claim without gating it.

A KB that exercises only claim nodes still conforms; the unused record
schemas simply have zero instances, and verify/refresh stay green either
way. The concrete node population, scope, and canonical source for a given
consuming project's KB live in that project's own orientation docs and
`<kb-root>/CLAUDE.md`; run the project's stats target for live node/edge
counts.

**Who writes the pin.** The pin is charter prose, not metadata: no write op
composes it and no op takes it as a value. **The build run writes it** into
`<kb-root>/CLAUDE.md`, from what the project's charter states — never an agent
in the KB set, which authors neither orientation doc. A build given no charter
has no scope statement to write, and the document says so in as many words
rather than carrying a blank section: a KB nobody pinned and one whose pin went
missing are different facts, and the build is the only thing that can tell them
apart.

**When it is written, and why not earlier.** At the validation gate, with the
rest of the KB's readiness documents — not when the build opens. The pin's home
is a file inside `kb-root/`, and until the document graph writes the tree that
directory holds nothing outside `.index/`; any write into it before then turns
`kb_util.kb_root_state` from `spine-only` into `populated`, which is the
reading a fresh build is refused on. So a build-open write would carry the pin
by making the build that performed it refusable — the one outcome the pin is
not worth. By the gate the tree is populated already, the stamp cannot change
that answer, and every stage after the gate runs against a KB that carries its
pin.

The run is the shell-resident driver process a person starts in a terminal,
and it is the only session there is. `/kb-build` prints the command line that
starts one and stops; it holds nothing and writes nothing. An earlier
revision of this section assigned the pin to "the session holding
`/kb-build`", which named a session that does not exist — the equivalent of a
voicemail greeting giving another number. Agent-assisted launch and
management of a run is later work, and the straightforward case has to work
first: the primary process in a user's terminal.

**One file arrives once, carrying both halves.** `kb_tools/installed/CLAUDE.md.tmpl`
holds the KB's standing orientation and a slot for the pin, and the stamp fills
the slot as it writes the file — so there is no path on which the document
lands asserting a pin it does not carry. The stamp stays only-if-absent: against
a `CLAUDE.md` a project authored for itself it writes nothing and reports
`present, left as authored`, that project's pin being its own to state.
