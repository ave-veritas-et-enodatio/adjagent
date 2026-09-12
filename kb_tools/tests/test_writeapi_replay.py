"""The 2026-09-02 regression, replayed.

The claim under test: **the 23/23 assertion passes, every title binds to its own
id, and ``clm-07687j`` is present.** That is the program's whole claim as one
executable arithmetic, and it is asserted twice here, at two different depths,
because one layer cannot carry both halves of it.

**Why two layers.** The test must feed the same values — 23 ids, titles,
confidences, rationales, depends-on sets — through the API. No op accepts an
id for an insert: ops mint. The ids are therefore replayable as
*test data* but not as *op inputs*, and the two readings are split rather than
one of them being quietly dropped:

* :class:`TestByteLevelReplay` drives ``render`` + ``store`` directly with the
  **recorded** ids. Here an id is a fixture value, not an agent-supplied one, and
  what the layer proves is that the renderer/store pair reproduces a correct
  register for the exact 2026-09-02 population: 23 claim markers, 23 claim
  records, every title bound to its own id, ``clm-07687j`` present and parseable.
  This is where the named id lives, because it is the only layer in which that
  literal id can appear on disk at all.
* :class:`TestEndToEndReplay` drives ``ops`` with the same titles, rigors,
  rationales and depends-on sets, minting fresh ids. Identity of ids is
  unavailable by construction, so the graph is compared **up to the renaming**:
  the depends-on edge set read back, translated through the mint map, must equal
  the recorded one. Isomorphism is the strongest statement available,
  and it is the statement that matters — what was lost on 2026-09-02 was a node
  and its in-edges, not a particular six characters.

**The values are extracted, never transcribed.** Hand-copying 23 titles,
confidences and rationales into this file would re-import, into the test, the
byte-fidelity demand the API exists to remove — and a transcription error would
show up as a passing test over a corpus nobody replayed. :func:`_load_recorded`
instead performs the *inverse of the defect* — it transposes each ``<!-- id: … -->``
marker back below its own ``## `` heading, which is the one-line difference
between what the agent wrote and what the reader needed — and then reads the
values out with the **production parsers**. So the intended values are defined by
the toolchain's own reader over repaired bytes, not by this file's opinion of
them.

**What the corpus actually carries**, faithfully replayed rather than tidied:
``part4``'s rationale opens with a stray ``|`` (a YAML block scalar the register
author wrote and the reader does not strip) and its depends-on bullets are prose
citations that resolve to zero edges. Both are replayed exactly as the reader
returns them. A replay that silently improved its input would be proving
something about a corpus that never existed.

**Not re-asserted here.** ``test_writeapi_regression_fixture.py`` already holds
the frozen bytes to their sha256s and asserts that they still parse to 18 records
— the 18 half of this arithmetic. This file asserts the 23 half and nothing about
the fixture's own integrity.

**No test asserts a report string's wording** — only status tokens, exit
codes, named identities and counts. Every count here is a census of the frozen
corpus, not a bound this program authored.
"""

import re
import tempfile
import unittest
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from kb_tools import kb_index_lib
from kb_tools.kb_write import ops, render, store

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "writeapi-regression"

# The reader's own marker grammar, anchored to a whole line. The transposition
# below must move exactly the markers `parse_claim_quality_file` binds and no
# other token, so the pattern is the parser's rather than a second spelling of
# it — the same reach `store.py` makes into these compiled patterns, for the same
# reason: one reader per grammar.
_MARKER_LINE = re.compile(rf"^\s*{kb_index_lib._CANONICAL_ANY_ID_RE.pattern}\s*$")

#: The 2026-09-02 population, as PROVENANCE.md's census records it: 23 canonical
#: markers across the frozen registers, every one of them a claim entry. The
#: support count is asserted at zero rather than dropped — these registers
#: declare none, and a fixture that grew one must move this number rather than
#: slip past a test that stopped looking. These are facts about frozen bytes,
#: re-derived by :func:`_load_recorded` and asserted against here so a change in
#: either the corpus or the extraction is a failure rather than a quietly
#: different replay.
_CLAIM_MARKERS = 23
_SUPPORT_MARKERS = 0

#: The entry this regression names. Its only references are ``depends-on``
#: bullets inside its own register, which the reader drops silently under
#: ``diagnostic_stream=None``, so nothing about its loss reaches a verifier from
#: these bytes — the one no downstream check could have caught.
_THE_SILENT_ID = "clm-07687j"


# ---------------------------------------------------------------------------
# The recorded population, read back out of the frozen bytes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recorded:
    """One 2026-09-02 entry, as the production parser returns it once repaired.

    ``rigor`` is the one concept behind the on-disk ``confidence:`` (claim) and
    ``quality:`` (support) fields, named as ``store`` and ``values`` name it so
    the replay never picks a field name. ``depends_on`` carries target ids only:
    the corpus authored no edge context, which :func:`_load_recorded` asserts
    rather than assumes.
    """

    node_id: str
    register: str
    kind: str
    title: str
    rigor: float
    rationale: str
    depends_on: tuple[str, ...]


def _register_path(source: Path) -> str:
    """The kb-root-relative path a flattened fixture file was frozen from.

    This flattened ``kb-root/<domain>/claim-quality.md`` to
    ``<domain>-claim-quality.md`` because six files of one name cannot share a
    directory (PROVENANCE.md). The replay restores the original shape, which is
    the shape both ``scan_authored_ids`` and the register census expect.
    """
    return f"{source.name.removesuffix('-claim-quality.md')}/claim-quality.md"


def _repaired(text: str) -> str:
    """Put every canonical marker back **below** its own ``## `` heading.

    The exact inverse of the 2026-09-02 defect, and a two-line transposition
    rather than a re-authoring: no title, score, rationale or bullet is touched.
    That is what lets the production parsers — not this file — say what the
    intended values were. A marker not followed by a heading is left where it
    is; :func:`_load_recorded` then fails on it rather than dropping it, since
    every marker must yield exactly one record for the census to mean anything.
    """
    lines = text.splitlines()
    out = list(lines)
    for i, line in enumerate(lines):
        if _MARKER_LINE.match(line) and i + 1 < len(lines) and lines[i + 1].startswith("## "):
            out[i], out[i + 1] = out[i + 1], out[i]
    return "\n".join(out) + "\n"


def _marker_ids(text: str) -> list[str]:
    """Every canonical marker id, in document order."""
    return [match.group(1) for line in text.splitlines() if (match := _MARKER_LINE.match(line))]


def _load_recorded() -> tuple[Recorded, ...]:
    """The intended values of all six frozen registers, in document order.

    The repaired copies are written to a throwaway directory and parsed there:
    the fixture tree is read-only material and is never written, and
    ``parse_claim_quality_file`` takes a path rather than text.
    """
    out: list[Recorded] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        for source in sorted(_FIXTURE.glob("*-claim-quality.md")):
            rel = _register_path(source)
            path = root / rel
            path.parent.mkdir(parents=True)
            text = _repaired(source.read_text(encoding="utf-8"))
            path.write_text(text, encoding="utf-8")

            claims = {entry.id: entry for entry in kb_index_lib.parse_claim_quality_file(path, root)}
            supports = kb_index_lib.parse_support_quality_entries(path, root)
            for node_id in _marker_ids(text):
                if node_id in claims:
                    entry = claims[node_id]
                    fields = ("clm", entry.title, entry.confidence, entry.rationale, entry.depends_on)
                elif node_id in supports:
                    raw = supports[node_id]
                    fields = ("sup", raw["title"], raw["quality"], raw["rationale"], raw["depends_on"])
                else:
                    # A marker the repaired parse still loses. Loud, because a
                    # replay of 22 of 23 entries would pass every count below
                    # only after those counts had been adjusted to match it.
                    raise ValueError(f"{source.name}: {node_id} yields no record even with its marker repaired")
                kind, title, rigor, rationale, edges = fields
                out.append(
                    Recorded(
                        node_id=node_id,
                        register=rel,
                        kind=kind,
                        title=title,
                        rigor=rigor,
                        rationale=rationale,
                        depends_on=tuple(edge.target for edge in edges),
                    )
                )
    _assert_corpus_shape(out)
    return tuple(out)


def _assert_corpus_shape(recorded: Sequence[Recorded]) -> None:
    """The properties of the frozen corpus the replay's emitters rely on.

    Stated here, at the one place the corpus is read, so a corpus that ever
    stopped having them fails with the reason rather than surfacing three layers
    down as a TOML parse error or an unresolvable dependency.
    """
    known = {entry.node_id for entry in recorded}
    for entry in recorded:
        if entry.rigor is None:
            raise ValueError(f"{entry.node_id}: rigor is *pending*; the values emitter renders numbers only")
        if "'''" in entry.title or "'''" in entry.rationale:
            raise ValueError(f"{entry.node_id}: prose carries ''' and cannot ride a TOML literal block")
        unresolved = sorted(set(entry.depends_on) - known)
        if unresolved:
            raise ValueError(f"{entry.node_id}: depends on {unresolved}, which the frozen corpus does not declare")


def _registers(recorded: Sequence[Recorded]) -> list[str]:
    """The kb-root-relative register paths the population occupies, in order."""
    return list(dict.fromkeys(entry.register for entry in recorded))


# ---------------------------------------------------------------------------
# Readback helpers, shared by both layers
# ---------------------------------------------------------------------------


def _seed_registers(kb_root: Path, registers: Iterable[str]) -> None:
    """Create each register as its title line alone — an empty, consistent store.

    The header, rather than an absent file, so neither layer has to route
    through the creation acknowledgment: what is being replayed is the write of
    23 entries, not the birth of five files.
    """
    for rel in registers:
        path = kb_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.parent.name} Claim Quality Register\n", encoding="utf-8")


def _censuses(kb_root: Path, registers: Iterable[str]) -> list[tuple[str, store.Census]]:
    return [(rel, store.take_census(kb_root / rel, kb_root)) for rel in registers]


def _read_back(kb_root: Path, registers: Iterable[str]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``id -> (title, depends-on targets)`` over the whole written population.

    Read through the two production parsers — the same pair that lost six nodes
    on 2026-09-02 — because what the replay must prove is what *that* reader
    sees, not what the bytes look like to a human.
    """
    seen: dict[str, tuple[str, tuple[str, ...]]] = {}
    for rel in registers:
        path = kb_root / rel
        for entry in kb_index_lib.parse_claim_quality_file(path, kb_root):
            seen[entry.id] = (entry.title, tuple(edge.target for edge in entry.depends_on))
        for node_id, raw in kb_index_lib.parse_support_quality_entries(path, kb_root).items():
            seen[node_id] = (raw["title"], tuple(edge.target for edge in raw["depends_on"]))
    return seen


def _edges(readback: dict[str, tuple[str, tuple[str, ...]]]) -> set[tuple[str, str]]:
    return {(node_id, target) for node_id, (_title, targets) in readback.items() for target in targets}


def _recorded_edges(recorded: Sequence[Recorded]) -> set[tuple[str, str]]:
    return {(entry.node_id, target) for entry in recorded for target in entry.depends_on}


# ---------------------------------------------------------------------------
# Layer 1 — render + store, driven with the recorded ids
# ---------------------------------------------------------------------------


def _splice_all(entries: Sequence[str]) -> Callable[[str], str]:
    """Append every rendered entry to a register, in order.

    Written here rather than borrowed from ``ops``: this layer's whole point is
    that ``ops`` is not in the picture, so the splice is composed from
    ``store``'s own primitive and nothing else.
    """

    def splice(document: str) -> str:
        for entry in entries:
            document = store.insert_entry(document, entry)
        return document

    return splice


def _render(entry: Recorded, titles: dict[str, str]) -> str:
    """Render one recorded entry with its recorded id.

    The dependency bullets carry their referents' titles, which is what a real
    insert does (``ops`` reads the title off the resolved referent) and what
    exercises the em-dash head the parser cuts a target out of. The title is not
    compared on readback — the edge is — so passing it is about replaying the
    corpus's bytes rather than about the assertion.
    """
    depends_on = tuple(render.DependsOnTarget(target=target, title=titles.get(target)) for target in entry.depends_on)
    if entry.kind == "clm":
        return render.render_claim_entry(
            node_id=entry.node_id,
            title=entry.title,
            confidence=entry.rigor,
            rationale=entry.rationale,
            depends_on=depends_on,
        )
    return render.render_support_entry(
        node_id=entry.node_id,
        title=entry.title,
        quality=entry.rigor,
        rationale=entry.rationale,
        depends_on=depends_on,
    )


class TestByteLevelReplay(unittest.TestCase):
    """The 2026-09-02 population, re-composed under its own ids and proven.

    One :func:`store.apply_edits` call carries every register, so the whole
    23-entry population lands in one proven act or none of it does — which
    is the shape the failure would have had if the API had existed: 23 nodes
    written together, or a refusal naming the one that would not read back.

    ``setUpClass`` performs the replay; every test below is an assertion about
    it. The written tree is read-only afterwards, so sharing it across the class
    costs nothing and keeps the replay attributable to one act.
    """

    @classmethod
    def setUpClass(cls) -> None:
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        # Resolved: a system temp directory is a symlink on macOS, and an
        # unresolved root makes `resolve_target`'s containment test vacuous.
        cls.kb_root = Path(tmp.name).resolve() / "kb-root"
        cls.recorded = _load_recorded()
        cls.registers = _registers(cls.recorded)
        _seed_registers(cls.kb_root, cls.registers)

        titles = {entry.node_id: entry.title for entry in cls.recorded}
        edits = []
        for rel in cls.registers:
            group = [entry for entry in cls.recorded if entry.register == rel]
            edits.append(
                store.Edit(
                    path=rel,
                    splice=_splice_all([_render(entry, titles) for entry in group]),
                    expect=tuple(
                        store.ExpectedEntry(
                            node_id=entry.node_id,
                            title=entry.title,
                            rigor=entry.rigor,
                            rationale=entry.rationale,
                            depends_on=tuple(store.ExpectedEdge(target) for target in entry.depends_on),
                        )
                        for entry in group
                    ),
                    claim_delta=sum(1 for entry in group if entry.kind == "clm"),
                    support_delta=sum(1 for entry in group if entry.kind == "sup"),
                )
            )
        cls.outcome = store.apply_edits(kb_root=cls.kb_root, edits=edits)

    def test_the_extraction_recovered_every_authored_marker(self):
        # This layer's own instrument, guarded before anything is concluded from
        # it: a replay that silently recovered 22 of 23 entries would satisfy
        # every count below once those counts matched the shortfall.
        kinds = [entry.kind for entry in self.recorded]
        self.assertEqual(kinds.count("clm"), _CLAIM_MARKERS)
        self.assertEqual(kinds.count("sup"), _SUPPORT_MARKERS)
        self.assertIn(_THE_SILENT_ID, {entry.node_id for entry in self.recorded})

    def test_the_whole_population_lands_in_one_proven_act(self):
        self.assertEqual(self.outcome.status, store.Status.WRITTEN, self.outcome.detail)
        self.assertEqual(sorted(self.outcome.written), sorted(self.registers))

    def test_twenty_three_markers_and_twenty_three_records(self):
        """The claim under test, as arithmetic.

        On 2026-09-02 this register set was 18 ≠ 23 and nothing noticed. Every
        marker is counted against the parser that would have lost it, per kind,
        and every register must balance on its own as well as in the total — a
        sum that happened to come out right over an unbalanced pair would be the
        same silence in a different shape.
        """
        markers = records = support_markers = support_records = 0
        for rel, census in _censuses(self.kb_root, self.registers):
            self.assertTrue(census.consistent, f"{rel}: {census.describe()}")
            markers += census.claim_markers
            records += census.claim_records
            support_markers += census.support_markers
            support_records += census.support_records
        self.assertEqual((markers, records), (_CLAIM_MARKERS, _CLAIM_MARKERS))
        self.assertEqual((support_markers, support_records), (_SUPPORT_MARKERS, _SUPPORT_MARKERS))

    def test_every_title_binds_to_its_own_id(self):
        # The off-by-one is what a marker above its heading produces, and it is
        # invisible in a count: 23 records each carrying its neighbour's title
        # censuses clean. The whole map is compared, so a single shifted binding
        # fails here.
        readback = _read_back(self.kb_root, self.registers)
        self.assertEqual(
            {node_id: title for node_id, (title, _edges) in readback.items()},
            {entry.node_id: entry.title for entry in self.recorded},
        )

    def test_the_silent_id_is_present_and_parseable(self):
        readback = _read_back(self.kb_root, self.registers)
        self.assertIn(_THE_SILENT_ID, readback)
        recorded = next(entry for entry in self.recorded if entry.node_id == _THE_SILENT_ID)
        title, targets = readback[_THE_SILENT_ID]
        self.assertEqual(title, recorded.title)
        self.assertEqual(targets, recorded.depends_on)
        # Its own register's entry, read field by field: presence alone would be
        # satisfied by a heading with nothing under it.
        entry = next(
            record
            for record in kb_index_lib.parse_claim_quality_file(self.kb_root / recorded.register, self.kb_root)
            if record.id == _THE_SILENT_ID
        )
        self.assertEqual(entry.confidence, recorded.rigor)
        self.assertEqual(entry.rationale, recorded.rationale)

    def test_the_recorded_depends_on_graph_is_reproduced(self):
        # The witnesses that were the silent id's only trace: the depends-on
        # bullets naming it from inside its own register. They are edges again,
        # not dropped prose.
        edges = _edges(_read_back(self.kb_root, self.registers))
        self.assertEqual(edges, _recorded_edges(self.recorded))
        self.assertIn(_THE_SILENT_ID, {target for _source, target in edges})


# ---------------------------------------------------------------------------
# Layer 2 — the op surface, with fresh ids
# ---------------------------------------------------------------------------


def _invocations(recorded: Sequence[Recorded]) -> list[tuple[str, tuple[Recorded, ...]]]:
    """Group the population into ``(op, entries)`` calls in dependency order.

    A values file is planned against the store as it stands *before* the call,
    so an entry may only name referents an earlier call already minted.
    The layering is Kahn's over the recorded graph; the two node kinds are split
    because they are two ops, and a support may depend on a claim while no claim
    depends on a support, so claims lead each layer.

    A cycle is raised rather than worked around: the claim graph is acyclic by
    invariant, and a corpus that had stopped being so would make every ordering
    here arbitrary.
    """
    remaining = {entry.node_id: entry for entry in recorded}
    calls: list[tuple[str, tuple[Recorded, ...]]] = []
    while remaining:
        ready = [entry for entry in remaining.values() if not set(entry.depends_on) & set(remaining)]
        if not ready:
            raise ValueError(f"the recorded depends-on graph has a cycle among {sorted(remaining)}")
        for kind, op in (("clm", "insert-claim-entry"), ("sup", "insert-support-entry")):
            group = tuple(entry for entry in ready if entry.kind == kind)
            if group:
                calls.append((op, group))
        for entry in ready:
            del remaining[entry.node_id]
    return calls


def _literal_block(text: str) -> str:
    """A TOML multi-line literal — the prose transport, with no escape grammar.

    This corpus's rationales carry ``$``, backslashes, backticks and
    apostrophes, which is the whole reason the literal block was chosen:
    asking a writer to escape them would relocate the byte-fidelity demand one layer out
    instead of removing it. The text sits on its own line, so a value ending in
    a quote cannot abut the closing delimiter; the newline the parser then
    leaves on the value is collapsed away by the renderer and by the readback
    comparison alike. ``_assert_corpus_shape`` has already refused a value
    carrying ``'''``, the one sequence no literal block can hold.
    """
    return f"'''\n{text}\n'''"


def _values_file(path: Path, entries: Sequence[Recorded], minted: dict[str, str]) -> Path:
    """Write one op's values file for ``entries``, in the values grammar.

    Dependency targets are written as the ids the **mint** produced, never as
    the recorded ones: the recorded graph is replayed by shape, and the only ids
    that exist in the replayed KB are the ones this API drew.
    """
    blocks = []
    for entry in entries:
        lines = [
            "[[entry]]",
            f"register = '{entry.register}'",
            f"title = {_literal_block(entry.title)}",
            f"rigor = {entry.rigor!r}",
            f"rationale = {_literal_block(entry.rationale)}",
        ]
        for target in entry.depends_on:
            lines += ["  [[entry.depends-on]]", f"  id = '{minted[target]}'"]
        blocks.append("\n".join(lines))
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


class TestEndToEndReplay(unittest.TestCase):
    """The same values through the op surface, with ids the tool minted.

    The recorded ids never reach an op: they are a lookup key for the values and
    a translation table for the graph, and nothing else. The reason: an
    insert takes values and returns the id it drew, so identity of ids is not
    a property this layer *can* assert, and isomorphism is what it asserts
    instead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        work = Path(tmp.name).resolve()
        cls.kb_root = work / "kb-root"
        cls.recorded = _load_recorded()
        cls.registers = _registers(cls.recorded)
        _seed_registers(cls.kb_root, cls.registers)

        cls.minted: dict[str, str] = {}
        cls.results: list[ops.Result] = []
        for number, (op, group) in enumerate(_invocations(cls.recorded), 1):
            values_file = _values_file(work / f"values-{number:02d}.toml", group, cls.minted)
            result = ops.OPS[op].run(kb_root=cls.kb_root, values_file=values_file)
            cls.results.append(result)
            if result.exit_code is not ops.ExitCode.WRITTEN:
                # Stop at the first failure: every later call names referents
                # this one would have minted, so continuing would bury the
                # cause under a cascade of unresolvable-target refusals.
                break
            cls.minted.update(zip((entry.node_id for entry in group), result.minted))

    def test_every_insert_wrote(self):
        for result in self.results:
            with self.subTest(op=result.op):
                self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(len(self.minted), len(self.recorded))

    def test_the_mint_supplied_every_id(self):
        # Visible in the replay itself: this test holds all 23 recorded ids
        # and still could not write one, because no insert op has a channel that
        # takes one. The ids on disk are the tool's own draws.
        fresh_ids = set(self.minted.values())
        self.assertEqual(len(fresh_ids), len(self.recorded))
        self.assertEqual(fresh_ids & {entry.node_id for entry in self.recorded}, set())
        for recorded_id, fresh in self.minted.items():
            # The kind survives the renaming even though the body does not: the
            # op mints for its own kind and takes no say from the values.
            self.assertEqual(fresh.split("-")[0], recorded_id.split("-")[0])

    def test_twenty_three_markers_and_twenty_three_records(self):
        """The claim under test again, now over ids the API minted."""
        markers = records = support_markers = support_records = 0
        for rel, census in _censuses(self.kb_root, self.registers):
            self.assertTrue(census.consistent, f"{rel}: {census.describe()}")
            markers += census.claim_markers
            records += census.claim_records
            support_markers += census.support_markers
            support_records += census.support_records
        self.assertEqual((markers, records), (_CLAIM_MARKERS, _CLAIM_MARKERS))
        self.assertEqual((support_markers, support_records), (_SUPPORT_MARKERS, _SUPPORT_MARKERS))

    def test_every_title_binds_to_its_own_id(self):
        readback = _read_back(self.kb_root, self.registers)
        self.assertEqual(
            {node_id: title for node_id, (title, _edges) in readback.items()},
            {self.minted[entry.node_id]: entry.title for entry in self.recorded},
        )

    def test_each_entry_landed_in_its_recorded_register(self):
        # Isomorphism of the graph would survive every entry landing in one
        # register; the population's distribution across the registers is part
        # of what was replayed, so it is asserted rather than implied.
        for rel in self.registers:
            with self.subTest(register=rel):
                landed = set(_read_back(self.kb_root, [rel]))
                self.assertEqual(
                    landed, {self.minted[entry.node_id] for entry in self.recorded if entry.register == rel}
                )

    def test_the_depends_on_graph_is_isomorphic_under_the_renaming(self):
        # Every edge, translated back through the mint map and compared as a
        # set. Identity of ids is unavailable here; this is the whole of what
        # remains, and it is what was actually lost on 2026-09-02.
        back = {fresh: recorded_id for recorded_id, fresh in self.minted.items()}
        edges = {(back[source], back[target]) for source, target in _edges(_read_back(self.kb_root, self.registers))}
        self.assertEqual(edges, _recorded_edges(self.recorded))

    def test_the_silent_id_arrives_with_the_dependents_that_were_its_only_witnesses(self):
        # `clm-07687j` reaches no verifier because the only references to it are
        # depends-on bullets in its own register, which the reader drops in
        # silence. Its counterpart here is a resolved referent: the
        # bullets are validated values now, so the node cannot be absent and the
        # edges present, nor the edges absent and nobody told.
        fresh = self.minted[_THE_SILENT_ID]
        readback = _read_back(self.kb_root, self.registers)
        self.assertIn(fresh, readback)

        recorded = next(entry for entry in self.recorded if entry.node_id == _THE_SILENT_ID)
        self.assertEqual(readback[fresh][0], recorded.title)
        dependents = {source for source, target in _edges(readback) if target == fresh}
        self.assertEqual(
            dependents,
            {self.minted[entry.node_id] for entry in self.recorded if _THE_SILENT_ID in entry.depends_on},
        )
        self.assertTrue(dependents, "the corpus records no dependent, so this test asserts nothing")


if __name__ == "__main__":
    unittest.main()
