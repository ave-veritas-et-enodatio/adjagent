"""The single composer of KB metadata bytes.

Every metadata byte the write API emits is composed here and nowhere else. The
failure this API exists to remove is not a bad value: a correctly minted id,
a correct title and a correct rationale still lose the node outright if the
id marker sits on the wrong side of its ``##`` heading. Layout is not a thing
inference should be asked to get right, so layout lives in one module with no
inputs but values.

**Purity, binding.** This module reads no files,
knows no paths, and holds no validation. It is values → text, total and pure.
It imports :mod:`kb_tools.kb_schema` and the standard library's :mod:`re` and
:mod:`dataclasses`, and nothing else — no :mod:`pathlib`, no :mod:`os`, no
:mod:`kb_index_lib`. ``test_kb_write_render.py`` asserts the import set
structurally, because a negative constraint with no instrument is a wish.

Well-formedness (grammar, closed vocabularies, inherited value domains) is
``values.py``'s; existence and uniqueness are ``ops.py``'s against the store.
Nothing here refuses, substitutes, defaults, or repairs. **The one
permitted transform is whitespace-run collapse inside a single-paragraph prose
field** (:func:`collapse_prose`) — the parser's own ``_normalize_text``
semantics applied at write time, so what is stored is what the reader returns.

**Derived fields are written at their placeholder identity and never at a
value.** ``solidity``, the ``(solidity …)`` bullet annotation, and the
leaf-references footer are ``refresh``'s; this module emits the exact bytes
``refresh`` recognizes so that the first refresh over a freshly inserted entry
either fills the slot or leaves it byte-identical. The placeholders are
required rather than optional: refresh *replaces* a ``- solidity:`` line and a
``(solidity …)`` annotation and does not insert either, so an entry rendered
without those slots would never receive a value.

**The grammar this output must satisfy** is ``kb_index_lib``'s parsers, and the
load-bearing details are these:

* A register entry is ``## <title>`` **then** ``<!-- id: … -->``. The parser
  binds a marker to the *preceding* ``## `` heading; a marker above its heading
  binds to the previous entry's title or to nothing at all, which is exactly
  how an id goes missing with zero verifier output.
* ``- rationale:`` is **single-paragraph by grammar**. The fold breaks on a
  blank line, and on a continuation line beginning with another known key, both
  *before* normalization runs. Collapsing the value to one physical line
  defeats the second class outright. The first class — a value carrying a blank
  line — is refused by ``values.py``, never collapsed here.
* ``- no-edge:`` is not in either fold's key list, so it is rendered **above**
  ``- depends-on:`` / ``- rationale:`` / ``- strengthen-by:``; placed after any
  of them it is silently swallowed into that field's value.
* A support entry's ``- supports:`` staging block is read by TWO graders and
  satisfies both. ``kb_index_lib.parse_support_quality_entries`` already carries
  ``supports`` in both of its fold-break key lists (``:1159``, ``:1174``), so
  the block terminates that entry's ``rationale`` and ``depends-on`` folds
  correctly wherever it sits — which is also the clearest evidence in the tree
  that the staging home was designed for rather than tolerated. The staging
  reader (``kb_index_lib.parse_register_staged_supports``) is the binding
  constraint: it ends the block at the next sibling ``- `` bullet, so the pairs
  must follow their opener with **nothing between them**.
* It is rendered **directly under the rigor line** for two reasons that are not
  aesthetic. There it is always closed by an explicit sibling — ``- solidity:``
  is emitted unconditionally — rather than running to the end of the entry and
  relying on the next entry's id marker to close it, which is what a block
  rendered last would do. And it sits above every derived line, so no update
  op's replaced span and no ``refresh`` rewrite ever crosses it.
* A depends-on bullet's target lives in its *head* — the text before the first
  ``  — `` (a real em-dash, U+2014) or the first `` (``. A hyphen where the
  em-dash belongs extends the head over the title and mints phantom edges from
  every id in it.
"""

import re
from dataclasses import dataclass

from kb_tools import kb_schema

# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------

#: The authored/derived "not yet assessed" literal, re-exported from
#: :mod:`kb_tools.kb_schema` — the same object every reader compares against.
PENDING_LITERAL = kb_schema.PENDING_LITERAL

#: The em-dash separating a depends-on bullet's target head from its title.
#: A real U+2014 with a space on each side — the parser cuts the head here, and
#: a hyphen does not cut at all.
EM_DASH_SEPARATOR = " — "

# ---------------------------------------------------------------------------
# Derived-field placeholders
# ---------------------------------------------------------------------------
#
# These three are the identity value of a field this API never computes, and
# they are `kb_schema`'s objects rather than copies of them. A local literal
# pinned by test to refresh's own handling is equality maintained by hand,
# which is what the ordinary drift begins as. `refresh` reads the same three
# objects, so the placeholder this module writes into a fresh entry and the one
# refresh recognizes are the same bytes by construction.
# `test_kb_write_render.py` still asserts recognition by refresh's *matchers*,
# which are separate objects and could still diverge from what they match.

#: The canonical ``- solidity:`` slot on a freshly inserted entry. Refresh
#: replaces this line; it never inserts one, so the slot is required.
SOLIDITY_PENDING_LINE = kb_schema.SOLIDITY_PENDING_LINE

#: The canonical ``(solidity …)`` annotation on a claim-target depends-on
#: bullet. Refresh substitutes into an existing annotation and never adds one,
#: so this too is required rather than decorative.
SOLIDITY_ANNOTATION_PENDING = kb_schema.SOLIDITY_ANNOTATION_PENDING

#: The canonical leaf-references footer for an entry no leaf cites yet — which
#: is every entry at the moment it is inserted. The object
#: ``kb_index_lib.render_leaf_references`` returns for an empty citing set, so a
#: refresh over a freshly inserted entry leaves it untouched.
LEAF_REFERENCES_PENDING_FOOTER = kb_schema.LEAF_REFERENCES_PENDING_FOOTER

# ---------------------------------------------------------------------------
# Frontmatter block delimiters
# ---------------------------------------------------------------------------
#
# The one composer of the block opener. A second writer re-spelling it inline —
# `refresh_kb_metadata`'s field splice is the one that can — is exactly what
# this constant exists to prevent, and the constraint needs an instrument rather
# than a statement.

#: Opens a leaf's ``<!-- kb-frontmatter … -->`` metadata block.
FRONTMATTER_OPENER = "<!-- kb-frontmatter"

#: Closes it. Matched by ``kb_index_lib.FRONTMATTER_RE``, which tolerates
#: leading whitespace on this line; the writer emits it at column zero.
FRONTMATTER_CLOSER = "-->"

# ---------------------------------------------------------------------------
# Grammar helpers (kb_schema is the only source of id grammar)
# ---------------------------------------------------------------------------

_CLAIM_ID_RE = re.compile(rf"^{kb_schema.id_body('clm')}$")
_WORK_ID_RE = re.compile(rf"^{kb_schema.WORK_ID_RE}$")
_WHITESPACE_RUN_RE = re.compile(r"\s+")

#: Two spaces — the sub-bullet indent under a structured ``- <field>:`` line.
_BULLET_INDENT = "  "


def collapse_prose(text: str) -> str:
    """Collapse every whitespace run in ``text`` to a single space, and strip.

    The parser's own ``_normalize_text`` semantics
    (``kb_index_lib.py:697-699``), applied at write time so that what is stored
    is what the reader will return. Applying it here — rather than letting the
    reader apply it on the way back — is what makes the write API's readback
    comparison well-posed: the supplied value and the parsed value are the same
    string, not two spellings of one.

    This is the module's only transform, and it is not a repair. A value that
    cannot survive the grammar — a rationale carrying a blank line, whose
    remainder the fold discards before normalization ever runs — is refused by
    ``values.py``. This function may assume single-paragraph input.
    """
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()


def is_claim_id(token: str) -> bool:
    """True if ``token`` is a well-formed ``clm-`` node id.

    Grammar only — this answers "is this shaped like a claim id", never "does
    this claim exist", which is ``ops.py``'s question against the store. The
    renderer needs the answer because a claim-target depends-on bullet carries
    the ``(solidity …)`` annotation and a framework-target bullet must not: on
    a framework bullet the parser reads the first parenthetical as the edge's
    *context*, so an annotation there would silently become prose.
    """
    return bool(_CLAIM_ID_RE.match(token))


def is_work_id(token: str) -> bool:
    """True if ``token`` is a well-formed external-work id.

    Grammar only, like :func:`is_claim_id` beside it. The renderer needs the
    answer for the same reason: a work-target depends-on bullet carries an
    ``(applicability …)`` annotation where a claim bullet carries
    ``(solidity …)`` and a framework bullet carries its context, and the parser
    reads whichever it finds there.
    """
    return bool(_WORK_ID_RE.match(token))


def _format_score(value: float | None) -> str:
    """Render an authored ``confidence`` / ``quality`` / fraction value.

    ``None`` renders as :data:`PENDING_LITERAL`. A number renders through
    Python's shortest round-tripping float repr, so the stored text parses back
    to the identical value. Fixed-width formatting is deliberately not used: it
    would silently round an authored ``0.875`` to ``0.88``, which is a
    near-miss coercion and forbidden.
    """
    return PENDING_LITERAL if value is None else f"{value}"


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependsOnTarget:
    """One depends-on bullet's values.

    ``target`` is a ``clm-`` id, a framework token (``INVARIANT-XX``,
    ``Axiom N``) or a ``work-`` id. ``title`` is the referent's register title,
    supplied by the caller — ``ops.py`` has already resolved the referent, so it
    holds the title; this module resolves nothing. ``context`` is the authored
    note about why the dependency is taken.

    ``applicability`` is the on-point fraction of a **work** target's pairing
    with this entry — how much of the cited work bears on this result — and is
    ``None`` for the authored ``*pending*`` literal, which is what a build
    writes and what only a person replaces. It is ignored on every other target
    kind, which carry no such quantity: a claim target's paren is the derived
    solidity annotation and a framework target's is its context.
    """

    target: str
    title: str | None = None
    context: str | None = None
    applicability: float | None = None


@dataclass(frozen=True)
class ExperimentDecl:
    """An ``exp-id:`` / ``status:`` / ``strengthens:`` block in leaf frontmatter."""

    exp_id: str
    status: str
    strengthens: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class SupportDecl:
    """A ``sup-id:`` / ``supports:`` block in leaf frontmatter.

    A beneficiary's on-point fraction is a float in [0, 1] or ``None`` for the
    authored ``*pending*`` literal — distinct on disk from a depends edge's
    null, and a legal authored value.
    """

    sup_id: str
    supports: tuple[tuple[str, float | None], ...] = ()


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def render_id_marker(node_id: str) -> str:
    """Render a register entry's canonical id marker: ``<!-- id: clm-xxxxxx -->``."""
    return f"<!-- id: {node_id} -->"


def render_tier2_marker(node_id: str) -> str:
    """Render a leaf-body Tier-2 marker: ``<!-- claim-quality: clm-xxxxxx -->``.

    The marker body carries the id alone. The reader accepts trailing text and
    harvests every id it finds, but a marker naming one id is the only shape
    whose meaning is unambiguous to a human diffing it.
    """
    return f"<!-- claim-quality: {node_id} -->"


# ---------------------------------------------------------------------------
# Structured bullets and lines
# ---------------------------------------------------------------------------


def render_depends_on_bullet(target: DependsOnTarget) -> str:
    """Render one ``- depends-on:`` sub-bullet.

    Claim target::

        - clm-aa1111 — Foundation Claim A (solidity *pending*) [context]

    Framework target — no ``(solidity …)`` annotation, context in parens, which
    is where the parser reads a framework edge's context from::

        - INVARIANT-S2 (context)

    External-work target — the off-graph endcap. Its paren carries the pairing's
    applicability rather than a solidity or a context, because the work has no
    solidity on this corpus's ladder and the quantity that varies per pairing is
    the applicability::

        - work-nobody2026 — Nobody (2026) Nothing (applicability *pending*)

    The separator is a real em-dash (U+2014) with a space on each side. The
    parser cuts the bullet head at the first `` — `` or `` (`` and scans only
    the head for target tokens; a hyphen does not cut, so the head runs on over
    the title and every ``clm-`` shaped token in it becomes a phantom edge.
    """
    parts = [f"{_BULLET_INDENT}- {target.target}"]
    if is_claim_id(target.target) or is_work_id(target.target):
        if target.title:
            parts.append(f"{EM_DASH_SEPARATOR}{collapse_prose(target.title)}")
        if is_work_id(target.target):
            parts.append(f" {render_applicability_annotation(_format_score(target.applicability))}")
        else:
            parts.append(f" {SOLIDITY_ANNOTATION_PENDING}")
        if target.context:
            parts.append(f" [{collapse_prose(target.context)}]")
    elif target.context:
        parts.append(f" ({collapse_prose(target.context)})")
    return "".join(parts)


def render_references_bullet(target: DependsOnTarget) -> str:
    """Render one ``- references:`` sub-bullet::

        - clm-aa1111 — Foundation Claim A [context]

    The shape is a claim depends-on bullet's with the parenthetical gone. There
    is no ``(solidity …)`` annotation because nothing gates on this edge and
    ``refresh`` derives nothing for it, and no ``(applicability …)`` because the
    pairing carries no score at all — the fact recorded is that the source's own
    text names the target, and that is the whole of it.

    ``target.applicability`` is ignored here, as it is on every non-work depends
    target: the values layer refuses one before this is reached.
    """
    parts = [f"{_BULLET_INDENT}- {target.target}"]
    if target.title:
        parts.append(f"{EM_DASH_SEPARATOR}{collapse_prose(target.title)}")
    if target.context:
        parts.append(f" [{collapse_prose(target.context)}]")
    return "".join(parts)


def render_applicability_annotation(value_text: str) -> str:
    """Compose the ``(applicability …)`` annotation on a work-target bullet.

    ``value_text`` is the already-formatted fraction or :data:`PENDING_LITERAL`
    — the same "values in, text out" contract :func:`render_solidity_annotation`
    keeps beside it. Unlike that one, the value is **authored**: nothing derives
    an applicability, and a build writes the pending literal here and leaves it.
    """
    return f"(applicability {value_text})"


def render_strengthen_by_bullet(text: str) -> str:
    """Render one ``- strengthen-by:`` sub-bullet — collapsed to one line.

    The reader folds continuation lines into the preceding item, so a
    multi-line item is representable; collapsing it anyway keeps one item on
    one physical line, which is what makes the item count readable from the
    bytes.
    """
    return f"{_BULLET_INDENT}- {collapse_prose(text)}"


def render_no_edge_line(reason: str) -> str:
    """Render the entry-level ``- no-edge: <reason>`` foreign-domain exemption.

    Placement is load-bearing and is :func:`render_claim_entry`'s: this line is
    in no fold's key list, so below ``- depends-on:`` it is appended to the last
    dependency bullet, and below ``- rationale:`` or ``- strengthen-by:`` it is
    absorbed into that field's text. Above all three it is skipped by the entry
    parser and seen by the citation checker, which is the intent.
    """
    return f"- no-edge: {collapse_prose(reason)}"


def render_solidity_line(*, value_text: str | None, status_phrase: str | None = None, trace: str = "") -> str:
    """Compose an entry's derived ``- solidity:`` line.

    **This module composes the line; it never computes the number.** Every input
    is a value the caller already has: ``value_text`` is the scalar already
    formatted by the toolchain's own 2-dp formatter, ``status_phrase`` is the
    build-band phrase for that value, and ``trace`` is the arithmetic suffix the
    computation itself rendered from its own branch record. Passing them in
    rather than deriving them is what keeps this module free of the solidity
    machinery while still leaving the *format* with one implementation.

    ``value_text`` of ``None`` is the no-computable-solidity case — an entry
    whose base is ``*pending*``, or one a pending dependency blocks. It returns
    :data:`SOLIDITY_PENDING_LINE` itself, so the line refresh writes for an
    unassessable entry and the line the write API writes into a fresh one are
    the same object and not merely equal strings. Any phrase and trace are
    dropped with it: there is no value for an arithmetic suffix to annotate.

    A non-``None`` ``value_text`` requires a ``status_phrase``: the two co-vary
    at the source (``build_status_phrase`` is ``None`` on exactly the values
    ``format_solidity`` renders as the pending literal), so they are not two
    independent inputs a caller could get out of step.

    ``refresh`` is this function's only caller with a real value. The write API
    never supplies one, because writing a derived value is not something it may
    do.
    """
    if value_text is None:
        return SOLIDITY_PENDING_LINE
    return f"- solidity: {value_text} ({status_phrase}){trace}"


def render_solidity_annotation(value_text: str) -> str:
    """Compose the ``(solidity …)`` annotation on a claim-target depends-on bullet.

    ``value_text`` is the already-formatted target solidity, or
    :data:`PENDING_LITERAL` for a target with no computable one — the same
    "values in, text out" contract as :func:`render_solidity_line`, and the
    reason a caller needs no branch of its own.
    """
    return f"(solidity {value_text})"


def render_id_list_field(field: str, ids: tuple[str, ...] | list[str]) -> str:
    """Compose a frontmatter ``<field>: [id, id]`` line — the canonical shape.

    The inline list is one of the three shapes the reader accepts and the only
    one written, so a block this module composes and a field ``refresh``
    rewrites in place are the same shape rather than two. It occupies one
    physical line, so the reader's multi-line span rule never has to be
    exercised on our output.

    No indent is applied: where the line sits is the splice's business, and the
    splice preserves the indent of the line it replaces
    (``store.replace_or_insert_frontmatter_field``).
    """
    return f"{field}: [{', '.join(ids)}]"


def wrap_frontmatter(body: str) -> str:
    """Wrap a rendered frontmatter body in its block delimiters.

    The single composer of the block opener. ``body`` carries no trailing
    newline; the delimiters supply the line breaks, so a body and its wrapper
    cannot disagree about how many blank lines sit between them.
    """
    return f"{FRONTMATTER_OPENER}\n{body}\n{FRONTMATTER_CLOSER}"


def render_strengthens_pair_line(claim_id: str, strength: float) -> str:
    """Render one ``strengthens:`` pair line in an experiment's frontmatter block."""
    return f"{_BULLET_INDENT}- {claim_id}: {strength}"


def render_supports_pair_line(claim_id: str, fraction: float | None) -> str:
    """Render one ``supports:`` pair line, in either of the fan-out's two homes.

    One line for the hosting document's ``sup-id:`` block
    (:func:`render_frontmatter_block`) and for the register entry's staging
    block (:func:`render_support_entry`) alike: both are read by the same pair
    pattern (``kb_index_lib._SUPPORTS_PAIR_RE``), so one renderer is the only
    honest number of renderers.

    ``fraction`` of ``None`` renders the authored ``*pending*`` literal — an
    intended-but-unassessed beneficiary edge, which contributes nothing to the
    beneficiary's lift and is not a poison.
    """
    return f"{_BULLET_INDENT}- {claim_id}: {_format_score(fraction)}"


def render_citation(*, excerpt: str, kb_path: str, anchor: str) -> str:
    """Render the sanctioned authority-citation form.

    ``["<excerpt>"](<kb-relative-path>#<anchor>)`` — the excerpt quoted inside
    the link text, whitespace-collapsed to the single line the checker compares
    (it normalizes whitespace before matching, and rejects an embedded
    newline). No length bound is applied: the inherited 240-character bound is
    the verifier's, checked in ``values.py`` against its contract, and this
    program authors no bound of its own.
    """
    return f'["{collapse_prose(excerpt)}"]({kb_path}#{anchor})'


# ---------------------------------------------------------------------------
# Register entries
# ---------------------------------------------------------------------------


def _render_entry(
    *,
    node_id: str,
    title: str,
    score_field: str,
    score: float | None,
    rationale: str,
    depends_on: tuple[DependsOnTarget, ...],
    strengthen_by: tuple[str, ...],
    supports: tuple[tuple[str, float | None], ...],
    no_edge: str | None,
) -> str:
    """Compose one register entry. Claim and support share every rule but the
    score field name, ``strengthen-by``, and the ``supports:`` staging block.

    Field order is the toolchain's own canonical order (``score``, ``supports``,
    ``depends-on``, ``solidity``, ``rationale``, ``strengthen-by``), and it is
    not free: each folding field is terminated by the next line matching the
    reader's key list, so ``solidity`` bounds ``depends-on`` and ``rationale``
    bounds nothing but its own collapse. The staging block leads so that an
    unconditional ``- solidity:`` always closes it and no derived line ever sits
    above it — see the module docstring.
    """
    lines = [
        f"## {collapse_prose(title)}",
        render_id_marker(node_id),
        "",
        LEAF_REFERENCES_PENDING_FOOTER,
        "",
        "### Quality",
        f"- {score_field}: {_format_score(score)}",
    ]
    if supports:
        lines.append("- supports:")
        lines.extend(render_supports_pair_line(claim_id, fraction) for claim_id, fraction in supports)
    if no_edge:
        lines.append(render_no_edge_line(no_edge))
    if depends_on:
        lines.append("- depends-on:")
        lines.extend(render_depends_on_bullet(target) for target in depends_on)
    lines.append(SOLIDITY_PENDING_LINE)
    lines.append(f"- rationale: {collapse_prose(rationale)}")
    if strengthen_by:
        lines.append("- strengthen-by:")
        lines.extend(render_strengthen_by_bullet(item) for item in strengthen_by)
    return "\n".join(lines)


def render_claim_entry(
    *,
    node_id: str,
    title: str,
    confidence: float | None,
    rationale: str,
    depends_on: tuple[DependsOnTarget, ...] = (),
    strengthen_by: tuple[str, ...] = (),
    no_edge: str | None = None,
) -> str:
    """Render a canonical ``clm-`` register entry, heading above marker.

    Returns the entry text with no trailing newline and no entry separator:
    where an entry sits among its siblings, and the ``---`` rule between them,
    is the splice's business and so ``store.py``'s.

    An entry with no dependencies renders no ``- depends-on:`` section at all
    rather than a placeholder bullet; both parse to zero edges, and the absent
    section is the shape the toolchain's own register fixtures carry.
    """
    return _render_entry(
        node_id=node_id,
        title=title,
        score_field="confidence",
        score=confidence,
        rationale=rationale,
        depends_on=depends_on,
        strengthen_by=strengthen_by,
        supports=(),
        no_edge=no_edge,
    )


def render_support_entry(
    *,
    node_id: str,
    title: str,
    quality: float | None,
    rationale: str,
    depends_on: tuple[DependsOnTarget, ...] = (),
    supports: tuple[tuple[str, float | None], ...] = (),
    no_edge: str | None = None,
) -> str:
    """Render a canonical ``sup-`` register entry, with its fan-out staged.

    Identical to a claim entry but for ``- quality:`` in place of
    ``- confidence:``, the absence of ``strengthen-by``, and the optional
    ``supports:`` block.

    **A fan-out has two authored homes and this is the earlier one.** The
    canonical home — the only one the claim graph reads — is the hosting
    document's ``sup-id:`` frontmatter block
    (:func:`render_frontmatter_block`), parallel to an experiment's
    ``strengthens:``. But a ``sup-`` id is minted a full stage before the leaf
    that will host it exists, so the pairs are staged in the register entry
    until that leaf is written, and ``kb_index_lib`` reads them there:
    :func:`kb_index_lib.parse_register_staged_supports` is the grammar, and
    ``scan_authored_support_edges`` — which the warrant-attribution
    postcondition is stated over — unions both homes. A ``- supports:`` block
    written into a register is therefore read, not decoration.

    ``supports`` is a beneficiary claim id and its on-point fraction, ``None``
    for the authored ``*pending*`` literal.
    """
    return _render_entry(
        node_id=node_id,
        title=title,
        score_field="quality",
        score=quality,
        rationale=rationale,
        depends_on=depends_on,
        strengthen_by=(),
        supports=supports,
        no_edge=no_edge,
    )


def render_work_entry(*, node_id: str, title: str, strength: float | None, rationale: str) -> str:
    """Render a canonical ``work-`` register entry — the off-graph endcap's node.

    It shares the skeleton every register entry carries — ``## `` heading,
    canonical marker, ``### Quality`` — so the census, the entry locator and
    every splice in this package serve it with no branch of their own. What it
    omits is what does not apply to a work this corpus does not contain:

    * **no ``- solidity:`` line**, because nothing derives one. A work's
      standing is authored whole and does not sit on the build-band ladder;
      an unconditional pending slot here would be a derived field nothing ever
      fills, reported forever as unassessed.
    * **no ``- depends-on:`` section**, because the node is terminal. Whatever
      the cited work rests on is outside this corpus too.
    * **no leaf-references footer**, because no leaf hosts a work: an external
      work is cited by the claims that rest on it, not contained by a document.

    ``strength`` is the work's standing, ``None`` for the ``*pending*`` literal.
    """
    return "\n".join(
        [
            f"## {collapse_prose(title)}",
            render_id_marker(node_id),
            "",
            "### Quality",
            f"- strength: {_format_score(strength)}",
            f"- rationale: {collapse_prose(rationale)}",
        ]
    )


# ---------------------------------------------------------------------------
# Leaf frontmatter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontmatterValues:
    """The authored half of a leaf's ``<!-- kb-frontmatter … -->`` block.

    Derived fields — ``subtree-claims`` and ``subtree-experiments`` — are
    absent by construction. They are refresh's roll-ups, inserted anchored
    after ``kind:``; rendering them here would write a derived value this API
    never computes.
    """

    kind: str
    path_stable: str | None = None
    claims: tuple[str, ...] = ()
    no_claim: str | None = None
    experiments: tuple[str, ...] = ()
    experiment_nodes: tuple[ExperimentDecl, ...] = ()
    support_nodes: tuple[SupportDecl, ...] = ()


def render_frontmatter_block(values: FrontmatterValues) -> str:
    """Render a leaf's kb-frontmatter block.

    **The canonical output shape is the inline list** — ``claims: [clm-aaaaaa,
    clm-bbbbbb]`` — one of the three the reader accepts (inline, bracket-
    wrapped across lines, and YAML bullets). It is chosen because it is the
    shape refresh's own field writer emits when it replaces or inserts a list
    field, so a block this module writes and a block refresh rewrites are the
    same shape rather than two; and because it occupies one physical line, so
    the reader's multi-line span rule never has to be exercised on our output.
    The other two shapes stay readable and are never written.

    ``no-claim``'s reason and ``path-stable``'s label are emitted
    double-quoted. The reader strips exactly one outer quote pair before typing
    the value, so the quoted form round-trips any single-line text — including
    one that would otherwise type as the boolean ``true`` or as a bracketed
    list. Both are free-text document attributes the reader types generically
    (``kb_index_lib._frontmatter_value``); neither carries a vocabulary or a
    length this API could inherit, so it authors neither.

    Field order is ``kind``, ``path-stable``, the primary field (``claims`` or
    ``no-claim``), the additive ``experiments`` reference list, then one block
    per hosted experiment and support node. Repeated ``exp-id:`` / ``sup-id:``
    keys each open their own fan-out block: a container hosts any number of any
    combination of node bodies. ``path-stable`` follows ``kind`` because the two
    describe the document rather than its contents; ``refresh``'s roll-ups are
    inserted after ``kind:`` and so land between them, which no reader minds —
    the block is a mapping, not a sequence.
    """
    lines = [f"kind: {values.kind}"]
    if values.path_stable is not None:
        lines.append(f'path-stable: "{collapse_prose(values.path_stable)}"')
    if values.claims:
        lines.append(render_id_list_field("claims", values.claims))
    if values.no_claim is not None:
        lines.append(f'no-claim: "{collapse_prose(values.no_claim)}"')
    if values.experiments:
        lines.append(render_id_list_field("experiments", values.experiments))
    for exp in values.experiment_nodes:
        lines.append(f"exp-id: {exp.exp_id}")
        lines.append(f"status: {exp.status}")
        if exp.strengthens:
            lines.append("strengthens:")
            lines.extend(render_strengthens_pair_line(cid, strength) for cid, strength in exp.strengthens)
    for sup in values.support_nodes:
        lines.append(f"sup-id: {sup.sup_id}")
        if sup.supports:
            lines.append("supports:")
            lines.extend(render_supports_pair_line(cid, fraction) for cid, fraction in sup.supports)
    return wrap_frontmatter("\n".join(lines))
