"""Stage F — the write passes, through the write API and nothing else.

**Every metadata byte is composed by the write API**: values in, canonical bytes
out, each write proven by re-parsing with the production parser before it lands.
Nothing here spells a heading, a marker, a bullet or a frontmatter key. That is
what makes it safe for a later wave to hand a model a *title* and never a
heading.

**Five passes, and the order is forced by the ops' own preconditions.**

1. ``insert-claim-entry``, every entry edge-free — a batch cannot name one of
   its own members as a target, because ids are minted by the write itself.
   Minted ids come back in entry order.
1b. ``insert-work-entry``, one entry per external work a claim block cites —
   :func:`write_works`. It mints nothing: a work's id is derived from its
   citation key. It must precede pass 3, which names those ids as targets.
2. ``set-frontmatter``, one entry per document. Must follow pass 1: the op
   resolves every claim id against the authored register and refuses one that is
   not there.
3. ``add-depends-on``, one batch carrying every edge — :func:`write_edges`.
   Every target exists by now, so no ordering among edges is needed and none is
   imposed. It lands twice over a build's three invocations: the declared pass's
   off-graph edges here, and the discovered pass's dependency attribution
   between claims from there.
4. ``mark-claim-in-leaf``, one entry per id on each document declaring two or
   more. Must follow pass 2: the op refuses a marker whose id is not in the
   document's own declaration.

**The values file is the one thing this module composes, and it is proven.** The
transport is TOML, which is a format with a parser, so a hand-composed file is a
byte-fidelity demand — the class of demand this whole API exists to remove. The
composer therefore declares what each value must parse back as and the file is
put through :mod:`tomllib` before any op sees it; a mismatch stops the run at
the composition rather than at a refusal three layers down. Prose travels in
``'''`` literal blocks on a line of their own, so a title carrying backslashes,
``$``, backticks and closing quotes needs no escape grammar; the one sequence no
literal block can hold — ``'''`` itself — is refused here by name.

**A 7 stops the run**: the values are this stage's, so a refusal is a defect in
what it composed. **An 8 is re-issued identically**, per the contract's own
instruction — re-authoring values that were already correct is how a duplicate
id gets written.
"""

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import kb_index_lib, kb_schema
from ..kb_write import ops
from . import endcap
from .assemble import WORKS_REGISTER, Plan, register_for
from .identify import Identification, ProseClaim
from .report import FACT, PASS, ClaimGraphError, Finding

#: How many times an 8 is re-issued before the run stops. The contract says
#: re-issue the identical call; it does not say forever, and a writer that never
#: yields is a wedge rather than a retry.
RETRY_LIMIT = 3

#: The character set a value may carry to be emitted as a basic TOML string:
#: printable ASCII less the two characters that would need an escape grammar.
#: Paths, kinds, ids and the pending literal are all inside it; prose is not,
#: and travels in a literal block instead.
_BASIC_STRING_SAFE = re.compile(r"^[ -~]*$")


class WriteError(ClaimGraphError):
    """A pass refused, or a value cannot be carried by the transport."""


@dataclass(frozen=True)
class Prose:
    """A value that travels in a literal block rather than a quoted string."""

    text: str


def _emit(value: object) -> tuple[str, object]:
    """One value's TOML text, and what it must parse back as.

    Returning both is what makes the round-trip check below a proof rather than
    a restatement: the expectation is derived from the supplied value here, and
    the parser is what confirms the file carries it.
    """
    if isinstance(value, Prose):
        text = value.text
        if "'''" in text:
            raise WriteError(
                "values-transport",
                f"{text[:60]!r}… cannot be carried by a TOML literal block: it contains ''', the one "
                f"sequence no literal block can hold, and this transport has no escape grammar",
            )
        # The text sits on its own line, so a value ending in a quote cannot abut
        # the closing delimiter. A literal block trims the newline immediately
        # after its opener, so the value comes back with exactly the trailing
        # newline the closer adds — which `render.collapse_prose` takes off again
        # at write time, and which the readback comparison collapses too.
        return f"'''\n{text}\n'''", f"{text}\n"
    if isinstance(value, tuple):
        parts = [_emit(item) for item in value]
        return "[" + ", ".join(text for text, _ in parts) + "]", [back for _, back in parts]
    if isinstance(value, Mapping):
        parts = [(key, _emit(item)) for key, item in value.items()]
        return (
            "{ " + ", ".join(f"{key} = {text}" for key, (text, _) in parts) + " }",
            {key: back for key, (_, back) in parts},
        )
    text = str(value)
    if not _BASIC_STRING_SAFE.match(text) or '"' in text or "\\" in text:
        raise WriteError(
            "values-transport",
            f"{text!r} is not a value this composer emits as a quoted string; only prose travels in a "
            f"literal block, and a path, a kind or an id carrying these characters is not one",
        )
    return f'"{text}"', text


def _compose(entries: Sequence[Mapping[str, object]]) -> str:
    """The values file for a batch, proven by re-parsing it before it is used."""
    lines: list[str] = []
    expected: list[dict[str, object]] = []
    for entry in entries:
        lines.append("[[entry]]")
        back: dict[str, object] = {}
        for key, value in entry.items():
            text, parsed = _emit(value)
            lines.append(f"{key} = {text}")
            back[key] = parsed
        expected.append(back)
        lines.append("")
    text = "\n".join(lines)

    parsed = tomllib.loads(text).get("entry")
    if parsed != expected:
        raise WriteError(
            "values-transport",
            "the composed values file does not parse back as the values it was composed from — this is a "
            "defect in this stage's composer, and nothing was handed to the write API",
        )
    return text


def _call(op, *, kb_root: Path, values_file: Path, **kwargs) -> ops.Result:
    """One op call, with the contract's own retry rule applied to an 8."""
    for attempt in range(RETRY_LIMIT):
        result = op(kb_root=kb_root, values_file=values_file, **kwargs)
        if result.exit_code is not ops.ExitCode.RETRY:
            return result
    raise WriteError(
        "concurrent-writer",
        f"{result.op} returned the retry code {RETRY_LIMIT} times over identical values; another writer "
        f"holds the files this run needs. Report lines: {'; '.join(result.lines())}",
    )


def _landed(result: ops.Result, pass_name: str) -> ops.Result:
    if not result.ok:
        raise WriteError(
            pass_name,
            f"{result.op} exited {int(result.exit_code)} and wrote nothing this stage can proceed from. "
            f"Report lines: {'; '.join(result.lines())}",
        )
    return result


def _values_path(scratch: Path, name: str) -> Path:
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch / f"{name}.toml"


def _prove_mint_order(kb_root: Path, register: str, minted: Sequence[str], titles: Sequence[str]) -> None:
    """The minted ids carry the titles they were minted for, read back by the production parser.

    The whole four-pass structure rests on ``minted[i]`` naming ``entries[i]``.
    That is the op's stated contract, and an off-by-one in it would bind every
    claim to the wrong register entry silently — every id would resolve, every
    gate would pass, and the graph would be wrong. So it is checked against the
    register itself rather than trusted.
    """
    parsed = kb_index_lib.parse_claim_quality_file(kb_root / register, kb_root)
    by_id = {entry.id: entry.title for entry in parsed}
    mismatched = [
        f"{node_id} -> {by_id.get(node_id)!r} (expected {title!r})"
        for node_id, title in zip(minted, titles, strict=True)
        if by_id.get(node_id) != title
    ]
    if mismatched:
        raise WriteError(
            "mint-order",
            f"{register}: {len(mismatched)} minted id(s) do not carry the title they were minted for: "
            f"{mismatched[:3]}",
        )


def write(plan: Plan, *, kb_root: Path, scratch: Path) -> tuple[list[Finding], Mapping[int, str]]:
    """Land the plan through the four passes. Returns the report and the minted ids by entry position."""
    findings: list[Finding] = []
    minted: dict[int, str] = {}

    # Pass 1 — one call per register. A refusal is all-or-nothing over the file
    # it names, so a smaller file localizes the report; `register` is a per-entry
    # field, so this is a choice rather than a precondition.
    for register in plan.registers():
        positions = [position for position, entry in enumerate(plan.entries) if entry.register == register]
        batch = [
            {
                "register": plan.entries[position].register,
                "title": Prose(plan.entries[position].title),
                "rigor": kb_schema.PENDING_LITERAL,
                "rationale": Prose(plan.entries[position].rationale),
            }
            for position in positions
        ]
        path = _values_path(scratch, f"1-insert-{register.replace('/', '_')}")
        path.write_text(_compose(batch), encoding="utf-8")
        result = _landed(_call(ops.insert_claim_entry, kb_root=kb_root, values_file=path, create=True), "pass-1")
        ids = result.minted
        if len(ids) != len(positions):
            raise WriteError("pass-1", f"{register}: {len(positions)} entries in, {len(ids)} ids back")
        _prove_mint_order(kb_root, register, ids, [plan.entries[p].title for p in positions])
        minted.update(zip(positions, ids, strict=True))
        findings.append(Finding(PASS, "pass-1-insert-claim-entry", f"{len(ids)} entries minted into {register}"))

    # Pass 1b — the external works, before any edge can name one. It follows
    # pass 1 for no reason of its own: the two are independent inserts, and this
    # order keeps the report reading in mint order.
    findings += write_works(plan.works, kb_root=kb_root, scratch=scratch)

    # Pass 2 — every document the verifier's walk sees gets a kind, and every
    # leaf kind gets exactly one of the two declarations.
    frontmatter: list[Mapping[str, object]] = []
    for record in plan.documents:
        entry: dict[str, object] = {"document": record.path, "kind": record.kind}
        if record.claims:
            entry["claims"] = tuple(minted[position] for position in record.claims)
        if record.no_claim is not None:
            entry["no-claim"] = Prose(record.no_claim)
        frontmatter.append(entry)
    path = _values_path(scratch, "2-set-frontmatter")
    path.write_text(_compose(frontmatter), encoding="utf-8")
    _landed(_call(ops.set_frontmatter, kb_root=kb_root, values_file=path), "pass-2")
    findings.append(Finding(PASS, "pass-2-set-frontmatter", f"{len(frontmatter)} documents"))

    # Pass 3 — the off-graph edges alone. Dependency attribution *between this
    # corpus's claims* is still the discovered pass's and runs from there; what
    # lands here is the endcap, whose edges are a comparison of two positions
    # stage B already recorded rather than an attribution anybody performed.
    findings += write_edges(
        [(minted[position], work_id) for position, work_id in plan.rests_on],
        kb_root=kb_root,
        scratch=scratch,
        name="pass-3-rests-on",
    )

    # Pass 4 — one marker per id on each document declaring two or more.
    if plan.markers:
        markers = [
            {"document": marker.document, "id": minted[marker.entry], "locator": Prose(marker.locator)}
            for marker in plan.markers
        ]
        path = _values_path(scratch, "4-mark-claim-in-leaf")
        path.write_text(_compose(markers), encoding="utf-8")
        _landed(_call(ops.mark_claim_in_leaf, kb_root=kb_root, values_file=path), "pass-4")
    findings.append(
        Finding(
            PASS,
            "pass-4-mark-claim-in-leaf",
            f"{len(plan.markers)} markers across {len({m.document for m in plan.markers})} documents",
        )
    )
    return findings, minted


def _prose_rationale(claim: ProseClaim) -> str:
    return (
        f"Identified in the prose of {claim.document}, which carries no author-marked claim block; the span "
        f"this entry names is anchored in that document by this claim's Tier-2 marker. Neither dependency "
        f"attribution nor rigor assessment has run over it."
    )


def write_claims(identification: Identification, *, kind: str, kb_root: Path, scratch: Path) -> list[Finding]:
    """C5 — one document's claims landed, through three of the write path's four passes.

    **Per document, not per batch.** A discovery run is order a hundred minutes,
    and a stop at document ninety that discards eighty-nine documents' work is a
    fault rather than a design. Writing here makes ``conform.determination`` the
    checkpoint: a document that got its claims reads ``HOSTS_CLAIMS`` on a
    re-run and is skipped, one still awaiting is retried.

    **A marker is minted for every claim, however few the document declares.**
    That departs from :func:`assemble.assemble`, which mints them only above
    two, and the reason is specific: a block-hosted claim's locator is
    recoverable from the tree forever because ``graph.read`` finds the block by
    title, and **a prose claim's is not**. Without the marker the excerpt exists
    only in this run's scratch, ``ClaimNode.locator`` is ``None``, and the next
    stage describes the claim by path alone.
    ``verify_kb_metadata.check_tier2_coverage`` demands markers above two claims
    and forbids them nowhere, so this is additive and green.

    **A document that anchored nothing is written, and it is written loudly.**
    It lands ``no-claim:`` like any other reason-carrying document — one
    ``set-frontmatter`` call, no op of its own, because what differs is the
    reason and not the write. What differs downstream is everything: the reason
    is :data:`~.identify.UNANCHORED_REASON`, which
    :func:`~.conform.determination` reads as the fifth state, and the finding
    this returns names the document and the count rather than reporting a
    document that states nothing. A silent zero is the failure class this whole
    path exists to remove.
    """
    document = identification.document
    stem = f"cinf-{document.replace('/', '_')}"
    claims = identification.claims
    if bool(claims) == bool(identification.no_claim):
        raise WriteError(
            "identification",
            f"{document}: an identification carries claims or a reason, never both and never neither. "
            f"This one carries {len(claims)} claim(s) and {'a' if identification.no_claim else 'no'} reason",
        )

    minted: tuple[str, ...] = ()
    if claims:
        register = register_for(document)
        batch = [
            {
                "register": register,
                "title": Prose(claim.title),
                "rigor": kb_schema.PENDING_LITERAL,
                "rationale": Prose(_prose_rationale(claim)),
            }
            for claim in claims
        ]
        path = _values_path(scratch, f"{stem}-1-insert-claim-entry")
        path.write_text(_compose(batch), encoding="utf-8")
        result = _landed(_call(ops.insert_claim_entry, kb_root=kb_root, values_file=path, create=True), "c-inf-insert")
        minted = tuple(result.minted)
        if len(minted) != len(claims):
            raise WriteError("c-inf-insert", f"{document}: {len(claims)} entries in, {len(minted)} ids back")
        _prove_mint_order(kb_root, register, minted, [claim.title for claim in claims])

    # `set-frontmatter` replaces the whole block, so the unscanned reason is
    # gone and `claims:` stands in its place in one call — the mutual exclusion
    # `check_tier1_coverage` enforces is satisfied by construction rather than
    # by a delete.
    entry: dict[str, object] = {"document": document, "kind": kind}
    if minted:
        entry["claims"] = minted
    else:
        entry["no-claim"] = Prose(identification.no_claim)
    path = _values_path(scratch, f"{stem}-2-set-frontmatter")
    path.write_text(_compose([entry]), encoding="utf-8")
    _landed(_call(ops.set_frontmatter, kb_root=kb_root, values_file=path), "c-inf-frontmatter")

    if minted:
        markers = [
            {"document": document, "id": node_id, "locator": Prose(claim.excerpt)}
            for node_id, claim in zip(minted, claims, strict=True)
        ]
        path = _values_path(scratch, f"{stem}-4-mark-claim-in-leaf")
        path.write_text(_compose(markers), encoding="utf-8")
        _landed(_call(ops.mark_claim_in_leaf, kb_root=kb_root, values_file=path), "c-inf-mark")

    if minted:
        detail = f"{document}: {len(minted)} claim(s) minted and marked"
    elif identification.anchored_nothing:
        detail = (
            f"{document}: 0 claims — identification named {identification.telemetry.returned} result(s) in "
            f"this document and anchored none of them. Nothing is recorded about what it states; what is "
            f"recorded is that this run could not find where. {identification.telemetry.line()}"
        )
    else:
        detail = f"{document}: states no result, and the reason it gives is recorded"
    return [Finding(FACT, "c-inf-document", detail)]


def write_edges(
    edges: Sequence[tuple[str, str]],
    *,
    kb_root: Path,
    scratch: Path,
    name: str = "pass-3-add-depends-on",
    references: Sequence[tuple[str, str]] = (),
) -> list[Finding]:
    """Pass 3 — every authored edge in one batch, through ``add-depends-on``.

    One values entry per *source*, because the op's transport is keyed that way:
    the entry being written is the source, and its lists name the referents it
    consumes and the ones it merely names. Every target already exists, so no
    ordering among the edges is needed and none is imposed.

    ``references`` are the cross-references the narrowing could not direct
    (:mod:`attribute`). They ride the same op and the same batch because they
    are the same entry's outgoing edges written into the same register entry;
    splitting them into a second call would put one claim's edges in two
    all-or-nothing batches, so a refusal in either would leave the other landed.

    **A work target's applicability is not supplied and that is the value.** The
    op renders the pending literal for a ``depends-on`` table carrying no
    ``applicability``, which is exactly what a build may write: the score is a
    judgement about a paper outside the corpus, and no stage of this build is
    pointed at filling it.

    ``name`` names the pass in the report and its values file, because one batch
    op lands two different things over a build's three invocations: the endcap's
    off-graph edges in the declared pass, and dependency attribution between
    claims in the discovered one.
    """
    if not edges and not references:
        return [Finding(FACT, name, "no edge was authored, so there is no batch to land")]

    depends_by_source: dict[str, list[str]] = {}
    for source, target in edges:
        depends_by_source.setdefault(source, []).append(target)
    references_by_source: dict[str, list[str]] = {}
    for source, target in references:
        references_by_source.setdefault(source, []).append(target)

    batch: list[Mapping[str, object]] = []
    for source in sorted({*depends_by_source, *references_by_source}):
        entry: dict[str, object] = {"id": source}
        if source in depends_by_source:
            entry["depends-on"] = tuple({"id": target} for target in depends_by_source[source])
        if source in references_by_source:
            entry["references"] = tuple({"id": target} for target in references_by_source[source])
        batch.append(entry)
    path = _values_path(scratch, name.removeprefix("pass-"))
    path.write_text(_compose(batch), encoding="utf-8")
    _landed(_call(ops.add_depends_on, kb_root=kb_root, values_file=path), "pass-3")
    return [
        Finding(
            PASS,
            name,
            f"{len(edges)} dependency edges across {len(depends_by_source)} claims, "
            f"{len(references)} references across {len(references_by_source)} claims",
        )
    ]


def write_works(works: Sequence[endcap.CitedWork], *, kb_root: Path, scratch: Path) -> list[Finding]:
    """Pass 1b — one register entry per external work, in one batch.

    **Corpus-wide, in the KB root's own register.** A work three volumes cite is
    one node, so its entry cannot live under a volume; the op refuses a key it
    already holds, which is the mechanical half of that guarantee.

    Every strength written is the pending literal. Nothing in this build derives
    one, nothing here defaults one, and no later stage of the build is pointed
    at it: a number about a paper no reader in this build has seen would be a
    confident wrong answer where a blank is an honest one.
    """
    if not works:
        return [Finding(FACT, "pass-1b-insert-work-entry", "no claim cites work outside the corpus")]
    batch = [
        {
            "register": WORKS_REGISTER,
            "key": work.key,
            "title": Prose(work.title),
            "strength": kb_schema.PENDING_LITERAL,
            "rationale": Prose(endcap.rationale(work)),
        }
        for work in works
    ]
    path = _values_path(scratch, "1b-insert-work-entry")
    path.write_text(_compose(batch), encoding="utf-8")
    _landed(_call(ops.insert_work_entry, kb_root=kb_root, values_file=path, create=True), "pass-1b")
    named = sum(1 for work in works if work.named)
    return [
        Finding(
            PASS,
            "pass-1b-insert-work-entry",
            f"{len(works)} external works into {WORKS_REGISTER}; {named} named by the bibliography, "
            f"{len(works) - named} carrying only the citation key",
        )
    ]
