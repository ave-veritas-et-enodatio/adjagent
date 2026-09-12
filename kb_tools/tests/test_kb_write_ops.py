"""The write-op registry's contract, as tests.

Four proofs, and the classes below carry them.

**Every refusal fixture exits 7 naming its own identity.** A check failing
for the wrong reason is a failed check, so each fixture asserts the exact name
the report line carries, not merely that something was refused. The control for
all of them is :class:`TestEveryOpWrites`, which drives the whole registry to a
clean write against the same fixture KB — without it a refusal below would be
evidence only that the harness is broken.

**The duplicate-id fixture is caught by ``scan_duplicate_register_ids`` and
demonstrably missed by the inventory.** :class:`TestDuplicateIsInvisible`
asks both functions the same question over the same KB and shows the inventory
answering "clean" — not by reasoning about the code, but by reading its return.
That is the whole reason two functions exist and not one.

**A minted id is provably absent from both scans before use and present after**,
and the draw genuinely re-rolls on a collision: :class:`TestMintFusion` forces
the first draw to land on an id the inventory already holds and asserts the
second is what gets written.

**No op path exists that writes an entry for an unminted id, asserted
structurally.** :class:`TestNoPathWritesAnUnmintedEntry` does not sample op
bodies; it enumerates. An op's inputs are exactly its own parameters plus the
closed values vocabulary of its row in :data:`values.OP_FIELDS`, and the test
walks **every** member of :data:`ops.OPS` asserting that no minting op's two
channels admit a node id, that every edit which raises a record count belongs to
a minting op and carries only ids minted in that call, and that removing the
draw stops every minting op dead. A future op that took an id for insert fails
here rather than shipping.

**No test asserts a report string's wording** — only the status token, the
named identity, the presence of a ``restore:`` clause, and the exit code.

**Two more rows carry the same shape.**
:class:`TestHostedDeclarationsAreProven` injects a renderer that drops each
class of hosted node declaration, and :class:`TestEveryLeafWriteIsProven` walks
the registry so that no future field can join the uncompared set in silence.
:class:`TestAnUndecodableKbFileIsEnvironmentUnfit` plants a file the
store walk cannot decode and asserts the ladder holds — exit 2, the file named,
never a traceback and never an rc outside the three the ladder defines.
"""

import ast
import dataclasses
import inspect
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kb_tools import kb_index_lib, kb_schema, kb_util, verify_citations
from kb_tools.kb_write import ops, render, store, values

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_REGRESSION = _FIXTURES / "writeapi-regression"
_MINI_KB = _FIXTURES / "mini-kb"

_LOCATOR = "a sentence worth locating"
_BODY = f"# A Document\n\nThe anchor result is established here, in {_LOCATOR}.\n"


# ---------------------------------------------------------------------------
# Fixture corpus — built through the renderer, never typed
#
# Typing a register by hand here would reintroduce, in the test harness, the
# exact byte-fidelity demand the API exists to remove: a fixture whose marker
# sat above its heading would make these tests pass against a broken corpus.
# ---------------------------------------------------------------------------


def _register(*entries: str) -> str:
    document = "# Part 1 Claim Quality\n"
    for entry in entries:
        document = store.insert_entry(document, entry)
    return document


def _document(fm: render.FrontmatterValues, body: str = _BODY) -> str:
    return f"[↑ Up](index.md)\n\n{render.render_frontmatter_block(fm)}\n\n{body}"


def _pandoc_blocks(markdown: str) -> list[dict]:
    """``markdown``'s top-level blocks, as the renderer reads them.

    Where a marker *lands* is a byte question its caller asserts directly;
    whether the document still holds the block structure SPEC.md's point 12
    describes is a question only a Markdown reader answers, and leaving the
    author's prose parsing as it did is this package's job. The binary is
    assumed present, the way ``test_pandoc.py`` assumes it.
    """
    completed = subprocess.run(
        ["pandoc", "-f", "gfm", "-t", "json"],
        input=markdown,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(completed.stdout)["blocks"]


class _KbCase(unittest.TestCase):
    """A throwaway KB the whole op surface can be driven against.

    ``part1/claim-quality.md`` holds two claims and a support; ``part1/leaf.md``
    hosts that support's beneficiary fan-out; ``part1/plain.md`` hosts nothing,
    so a block-replacing op can be run on it without restating a declaration;
    ``part1/orphan.md`` cites a claim that no register keys, which is the
    pre-existing defect one refusal fixture needs a subject for.

    The values file lives **outside** the KB, because it is an input to the tool
    and not KB content.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolved: a system temp directory is a symlink on macOS, and an
        # unresolved root makes every containment assertion vacuous.
        self.work = Path(self._tmp.name).resolve()
        self.kb_root = self.work / "kb-root"
        (self.kb_root / "part1").mkdir(parents=True)

        self.rel = "part1/claim-quality.md"
        self.register = self.kb_root / self.rel
        self.register.write_text(
            _register(
                render.render_claim_entry(
                    node_id="clm-aa1111", title="Anchor Claim", confidence=0.9, rationale="The anchor."
                ),
                render.render_claim_entry(
                    node_id="clm-bb2222",
                    title="Second Claim",
                    confidence=0.6,
                    rationale="The second.",
                    depends_on=(render.DependsOnTarget(target="clm-aa1111", title="Anchor Claim"),),
                ),
                render.render_support_entry(
                    node_id="sup-cc3333", title="A Support", quality=0.8, rationale="Analytical support."
                ),
                # The off-graph endcap, both ends of it: a work outside the
                # corpus and a claim resting on it. It is a claim of its own
                # rather than a bullet on one of the two above, because those
                # two carry the dependency shapes other tests here assert —
                # one with no section at all, one with a single claim target.
                render.render_work_entry(
                    node_id="work-nobody2026",
                    title="Nobody. 2026. Nothing.",
                    strength=None,
                    rationale="Cited from inside a claim block.",
                ),
                render.render_claim_entry(
                    node_id="clm-ee5555",
                    title="A Citing Claim",
                    confidence=0.5,
                    rationale="Rests on work outside the corpus.",
                    depends_on=(
                        render.DependsOnTarget(
                            target="work-nobody2026", title="Nobody. 2026. Nothing.", applicability=None
                        ),
                    ),
                ),
            ),
            encoding="utf-8",
        )
        (self.kb_root / "part1/leaf.md").write_text(
            _document(
                render.FrontmatterValues(
                    kind="leaf",
                    claims=("clm-aa1111", "clm-ee5555"),
                    support_nodes=(render.SupportDecl(sup_id="sup-cc3333", supports=(("clm-bb2222", 0.5),)),),
                )
            ),
            encoding="utf-8",
        )
        (self.kb_root / "part1/plain.md").write_text(
            _document(render.FrontmatterValues(kind="leaf", claims=("clm-aa1111",))), encoding="utf-8"
        )
        (self.kb_root / "part1/orphan.md").write_text(
            _document(render.FrontmatterValues(kind="leaf", claims=("clm-dd4444",))), encoding="utf-8"
        )

    # -- harness ----------------------------------------------------------

    def values_file(self, text: str, *, name: str = "values.toml") -> Path:
        path = self.work / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_op(self, op: str, text: str, **kwargs) -> ops.Result:
        return ops.OPS[op].run(kb_root=self.kb_root, values_file=self.values_file(text), **kwargs)

    def valid_values(self, op: str) -> str:
        """A values file that writes, for every op in the registry.

        One per row, so the registry can be walked exhaustively — a new op with
        no entry here fails the walk rather than being silently skipped.
        """
        return {
            "insert-claim-entry": """
                [[entry]]
                register = "part1/claim-quality.md"
                title = "A Third Claim"
                rigor = 0.7
                rationale = "A third rationale."
                """,
            "insert-support-entry": """
                [[entry]]
                register = "part1/claim-quality.md"
                title = "Another Support"
                rigor = 0.8
                rationale = "Another analytical support."
                """,
            "insert-experiment-entry": """
                [[entry]]
                document = "part1/plain.md"
                status = "run"
                  [[entry.strengthens]]
                  id = "clm-aa1111"
                  strength = 0.8
                """,
            "insert-work-entry": """
                [[entry]]
                register = "part1/claim-quality.md"
                key = "somebody2027"
                title = "Somebody. 2027. Something."
                strength = "*pending*"
                rationale = "Cited from inside a claim block as somebody2027."
                """,
            "set-work-strength": """
                [[entry]]
                id = "work-nobody2026"
                strength = 0.4
                """,
            "set-applicability": """
                [[entry]]
                id = "clm-ee5555"
                work = "work-nobody2026"
                applicability = 0.25
                """,
            "set-rigor": """
                [[entry]]
                id = "clm-bb2222"
                rigor = 0.55
                """,
            "set-rationale": """
                [[entry]]
                id = "clm-bb2222"
                rationale = "A replacement rationale."
                """,
            "add-depends-on": """
                [[entry]]
                id = "clm-bb2222"
                  [[entry.depends-on]]
                  id = "INVARIANT-S2"
                  context = "the labelling convention"
                """,
            "set-frontmatter": """
                [[entry]]
                document = "part1/plain.md"
                kind = "leaf"
                claims = ["clm-aa1111", "clm-bb2222"]
                """,
            "mark-claim-in-leaf": f"""
                [[entry]]
                document = "part1/plain.md"
                id = "clm-aa1111"
                locator = "{_LOCATOR}"
                """,
            "set-on-point-fraction": """
                [[entry]]
                id = "sup-cc3333"
                claim = "clm-bb2222"
                fraction = 0.25
                """,
        }[op]

    # -- assertions -------------------------------------------------------

    def assert_refused(self, result: ops.Result, name: str) -> None:
        """Exit 7, one FAIL line, that line naming ``name`` and a corrective call."""
        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED)
        self.assertEqual(result.written, ())
        failures = [item for item in result.report if item.status == kb_util.FAIL]
        self.assertEqual([item.name for item in failures], [name], result.lines())
        self.assertIn("restore:", failures[0].detail)

    def assert_nothing_written(self, before: dict[Path, bytes]) -> None:
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, f"{path} was modified by a refused op")

    def snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in sorted(self.kb_root.rglob("*.md"))}


# ---------------------------------------------------------------------------
# The control: every op in the registry writes
# ---------------------------------------------------------------------------


class TestEveryOpWrites(_KbCase):
    """The whole registry, driven to exit 0 against one KB.

    This is the control the refusal suite needs, and it is also what keeps
    :meth:`_KbCase.valid_values` honest: an op whose row went stale fails here
    rather than making its refusal fixtures pass for the wrong reason.
    """

    def test_each_op_writes_its_file_and_reports_it(self):
        for name, op in ops.OPS.items():
            with self.subTest(op=name):
                self.setUp()
                result = self.run_op(name, self.valid_values(name))
                self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
                self.assertTrue(result.written, result.lines())
                passes = [item for item in result.report if item.status == kb_util.PASS]
                self.assertEqual([item.name for item in passes], list(result.written))

    def test_a_minting_op_reports_and_returns_the_id_it_minted(self):
        for name, op in ops.OPS.items():
            if op.mints is None:
                continue
            with self.subTest(op=name):
                self.setUp()
                result = self.run_op(name, self.valid_values(name))
                self.assertEqual(len(result.minted), 1, result.lines())
                self.assertTrue(result.minted[0].startswith(f"{op.mints}-"))
                facts = [item for item in result.report if item.status == kb_util.FACT]
                self.assertIn(result.minted[0], " ".join(item.detail for item in facts))

    def test_a_batch_of_entries_into_one_file_lands_together(self):
        # One file may carry a batch, and a scoring wave's batch is
        # ordinarily bound for ONE register. Two edits naming one path would be
        # refused by the store as a duplicate target, so the ops layer folds
        # them into a single edit — this is what keeps a batch a batch.
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "Batch Claim One"
            rigor = 0.7
            rationale = "The first of a batch."

            [[entry]]
            register = "part1/claim-quality.md"
            title = "Batch Claim Two"
            rigor = 0.4
            rationale = "The second of a batch."
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(len(result.minted), 2)
        self.assertEqual(result.written, (self.rel,))
        records = {e.id: e.title for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)}
        self.assertEqual([records[i] for i in result.minted], ["Batch Claim One", "Batch Claim Two"])
        self.assertTrue(store.take_census(self.register, self.kb_root).consistent)

    def test_a_batch_of_updates_to_one_register_lands_together(self):
        result = self.run_op(
            "set-rigor",
            """
            [[entry]]
            id = "clm-aa1111"
            rigor = 0.11

            [[entry]]
            id = "clm-bb2222"
            rigor = 0.22
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        records = {e.id: e.confidence for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)}
        self.assertEqual((records["clm-aa1111"], records["clm-bb2222"]), (0.11, 0.22))

    def test_a_batch_of_markers_locates_each_against_the_document_it_lands_in(self):
        # Each marker is resolved against the document the one before it landed
        # in, so a batch marking two claims in one leaf ends each claim's own
        # line rather than binding one of them to the other's paragraph.
        doc = self.kb_root / "part1/two-claims.md"
        doc.write_text(
            _document(
                render.FrontmatterValues(kind="leaf", claims=("clm-aa1111", "clm-bb2222")),
                body="# Two Results\n\nThe first result, the anchor.\n\nThe second result, the follower.\n",
            ),
            encoding="utf-8",
        )
        result = self.run_op(
            "mark-claim-in-leaf",
            """
            [[entry]]
            document = "part1/two-claims.md"
            id = "clm-aa1111"
            locator = "the anchor"

            [[entry]]
            document = "part1/two-claims.md"
            id = "clm-bb2222"
            locator = "the follower"
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        lines = doc.read_text(encoding="utf-8").splitlines()
        for node_id, anchor in (("clm-aa1111", "the anchor"), ("clm-bb2222", "the follower")):
            marked = [line for line in lines if line.endswith(render.render_tier2_marker(node_id))]
            self.assertEqual(len(marked), 1, lines)
            self.assertIn(anchor, marked[0])
        self.assertEqual(
            kb_index_lib.parse_leaf(doc, self.kb_root).tier2_marked, frozenset({"clm-aa1111", "clm-bb2222"})
        )

    def test_a_batch_that_refuses_one_entry_writes_none(self):
        before = self.snapshot()
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "A Good Batch Member"
            rigor = 0.7
            rationale = "Would have been written."

            [[entry]]
            register = "part1/claim-quality.md"
            title = "A Bad Batch Member"
            rigor = 0.7
            rationale = "Names a dependency that does not exist."
              [[entry.depends-on]]
              id = "clm-999999"
            """,
        )
        self.assert_refused(result, "depends-on.id=clm-999999")
        self.assertEqual(result.minted, ())
        self.assert_nothing_written(before)

    def test_report_lines_carry_the_toolchains_uniform_shape(self):
        result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        for line in result.lines():
            self.assertTrue(line.startswith(f"[{ops.REPORT_TAG}] "), line)
            self.assertIn(line.split()[1], {kb_util.PASS, kb_util.FAIL, kb_util.FACT}, line)

    def test_an_update_op_leaves_a_computed_solidity_byte_untouched(self):
        # The consequential case, end to end: the op rewrites the one
        # authored line it names and refresh's computed value survives, so the
        # next refresh reports no drift and verify stays green.
        computed = "- solidity: 0.54 (use as input only, don't build deeper) [= min(0.60, 0.90)]"
        text = self.register.read_text(encoding="utf-8")
        head, _, tail = text.partition("## Second Claim")
        self.register.write_text(
            head + "## Second Claim" + tail.replace(render.SOLIDITY_PENDING_LINE, computed, 1), encoding="utf-8"
        )

        result = self.run_op("set-rigor", self.valid_values("set-rigor"))
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = self.register.read_text(encoding="utf-8")
        self.assertIn(computed, after)
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-bb2222"
        )
        self.assertEqual(record.confidence, 0.55)
        self.assertEqual(record.solidity, 0.54)

    def test_add_depends_on_appends_rather_than_rewriting_the_list(self):
        # The existing bullet — and the derived annotation on it — is still
        # there, byte for byte, with the new one below it.
        existing = "  - clm-aa1111 — Anchor Claim (solidity *pending*)"
        self.assertIn(existing, self.register.read_text(encoding="utf-8"))
        result = self.run_op("add-depends-on", self.valid_values("add-depends-on"))
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = self.register.read_text(encoding="utf-8")
        self.assertIn(existing, after)
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-bb2222"
        )
        self.assertEqual([edge.target for edge in record.depends_on], ["clm-aa1111", "INVARIANT-S2"])

    def test_add_depends_on_opens_a_section_on_an_entry_that_has_none(self):
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "clm-aa1111"\n  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-aa1111"
        )
        self.assertEqual([edge.target for edge in record.depends_on], ["INVARIANT-S2"])

    def test_add_depends_on_writes_references_into_a_list_of_their_own(self):
        # One op, two lists: the reference lands in `- references:` above
        # `- solidity:`, carries no derived annotation, and reaches the record's
        # own field rather than the one every solidity consumer reads.
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "clm-bb2222"\n  [[entry.references]]\n  id = "clm-aa1111"\n'
            '  context = "distinct from the anchor"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = self.register.read_text(encoding="utf-8")
        self.assertIn("- references:\n  - clm-aa1111 — Anchor Claim [distinct from the anchor]\n", after)
        self.assertNotIn("clm-aa1111 — Anchor Claim [distinct from the anchor] (solidity", after)
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-bb2222"
        )
        self.assertEqual([edge.target for edge in record.references], ["clm-aa1111"])
        self.assertEqual([edge.relation for edge in record.references], ["references"])
        self.assertNotIn("clm-aa1111", [edge.target for edge in record.depends_on if edge.relation == "references"])

    def test_add_depends_on_writes_both_lists_in_one_call(self):
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "clm-aa1111"\n  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
            '  [[entry.references]]\n  id = "clm-bb2222"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-aa1111"
        )
        self.assertEqual([edge.target for edge in record.depends_on], ["INVARIANT-S2"])
        self.assertEqual([edge.target for edge in record.references], ["clm-bb2222"])
        # Canonical field order survives whichever section was opened first.
        text = self.register.read_text(encoding="utf-8")
        start = text.index("<!-- id: clm-aa1111 -->")
        block = text[start : text.index("\n---", start)]
        self.assertLess(block.index("- depends-on:"), block.index("- references:"))
        self.assertLess(block.index("- references:"), block.index("- solidity:"))

    def test_add_depends_on_refuses_a_call_naming_no_edge_at_all(self):
        result = self.run_op("add-depends-on", '[[entry]]\nid = "clm-aa1111"\n')
        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED, result.lines())
        self.assertIn("neither list names one", "\n".join(result.lines()))

    def test_add_depends_on_refuses_a_reference_authored_on_a_support(self):
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "sup-cc3333"\n  [[entry.references]]\n  id = "clm-aa1111"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED, result.lines())
        self.assertIn("one claim of this corpus naming another", "\n".join(result.lines()))

    def test_a_reference_the_entry_already_holds_is_written_once(self):
        supplied = '[[entry]]\nid = "clm-bb2222"\n  [[entry.references]]\n  id = "clm-aa1111"\n'
        self.assertEqual(self.run_op("add-depends-on", supplied).exit_code, ops.ExitCode.WRITTEN)
        result = self.run_op("add-depends-on", supplied)
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-bb2222"
        )
        self.assertEqual([edge.target for edge in record.references], ["clm-aa1111"])

    def test_the_rigor_field_is_read_off_the_ids_kind(self):
        # The op picks `confidence:` or `quality:`; the caller never names it,
        # so the caller can never name the wrong one.
        self.run_op("set-rigor", '[[entry]]\nid = "clm-bb2222"\nrigor = 0.4\n')
        self.run_op("set-rigor", '[[entry]]\nid = "sup-cc3333"\nrigor = 0.3\n')
        text = self.register.read_text(encoding="utf-8")
        self.assertIn("- confidence: 0.4", text)
        self.assertIn("- quality: 0.3", text)

    def test_an_experiment_is_born_with_its_declaration(self):
        result = self.run_op("insert-experiment-entry", self.valid_values("insert-experiment-entry"))
        exp_id = result.minted[0]
        nodes = kb_index_lib.parse_experiment_leaf(self.kb_root / "part1/plain.md", self.kb_root)
        self.assertEqual([n.id for n in nodes], [exp_id])
        self.assertEqual(nodes[0].status, "run")
        self.assertEqual(nodes[0].strengthens, (("clm-aa1111", 0.8),))
        # The fresh id now resolves, so set-frontmatter's
        # refusal of an unresolvable exp-id is correct behaviour rather than a
        # hole in the surface.
        self.assertIn(exp_id, kb_index_lib.scan_authored_ids(self.kb_root))


# ---------------------------------------------------------------------------
# One op's idempotence, because a re-run is the ordinary case
# ---------------------------------------------------------------------------


class TestAddDependsOnIsIdempotent(_KbCase):
    """A dependency graph is a set of edges, so adding one twice adds it once.

    Claim discovery makes a dependency pass long — hours over a corpus — which
    makes re-running it after an interruption the ordinary case rather than the
    exceptional one. Refusing the duplicate would make such a run unresumable;
    appending it a second time doubles every edge the first run wrote. So it is
    a no-op, and the count is reported rather than swallowed.
    """

    _ADD_INVARIANT = '[[entry]]\nid = "clm-bb2222"\n  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'

    def facts(self, result: ops.Result) -> list[tuple[str, str]]:
        return [(item.status, item.name) for item in result.report if item.status == kb_util.FACT]

    def targets(self, node_id: str = "clm-bb2222") -> list[str]:
        record = next(e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == node_id)
        return [edge.target for edge in record.depends_on]

    def test_the_same_edge_twice_is_one_edge_and_a_reported_duplicate(self):
        first = self.run_op("add-depends-on", self._ADD_INVARIANT)
        self.assertEqual(first.exit_code, ops.ExitCode.WRITTEN, first.lines())
        self.assertEqual(self.facts(first), [])

        second = self.run_op("add-depends-on", self._ADD_INVARIANT)
        self.assertEqual(second.exit_code, ops.ExitCode.WRITTEN, second.lines())
        self.assertEqual(self.facts(second), [(kb_util.FACT, "clm-bb2222")])
        self.assertEqual(self.targets(), ["clm-aa1111", "INVARIANT-S2"])

    def test_a_wholly_duplicate_call_leaves_the_register_byte_identical(self):
        self.run_op("add-depends-on", self._ADD_INVARIANT)
        written = self.register.read_bytes()
        result = self.run_op("add-depends-on", self._ADD_INVARIANT)
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(self.register.read_bytes(), written)

    def test_a_framework_target_is_one_edge_in_either_spelling(self):
        # The values file spells the bullet's token, `Axiom 1`; the reader keys
        # that edge under `axiom-1`. Comparing the two spellings directly finds
        # no match and writes the bullet again.
        axiom = '[[entry]]\nid = "clm-bb2222"\n  [[entry.depends-on]]\n  id = "Axiom 1"\n'
        self.run_op("add-depends-on", axiom)
        second = self.run_op("add-depends-on", axiom)
        self.assertEqual(second.exit_code, ops.ExitCode.WRITTEN, second.lines())
        self.assertEqual(self.facts(second), [(kb_util.FACT, "clm-bb2222")])
        self.assertEqual(self.targets(), ["clm-aa1111", "axiom-1"])

    def test_an_interrupted_pass_resumed_converges_on_the_clean_runs_register(self):
        both = (
            '[[entry]]\nid = "clm-bb2222"\n'
            '  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
            '  [[entry.depends-on]]\n  id = "Axiom 1"\n'
        )
        self.run_op("add-depends-on", both)
        clean = self.register.read_bytes()

        # The same pass, interrupted after its first edge and re-run whole —
        # which is what a caller with no record of what landed must do.
        self.setUp()
        self.run_op("add-depends-on", self._ADD_INVARIANT)
        resumed = self.run_op("add-depends-on", both)
        self.assertEqual(resumed.exit_code, ops.ExitCode.WRITTEN, resumed.lines())
        self.assertEqual(self.facts(resumed), [(kb_util.FACT, "clm-bb2222")])
        self.assertEqual(self.register.read_bytes(), clean)
        self.assertEqual(self.targets(), ["clm-aa1111", "INVARIANT-S2", "axiom-1"])

    def test_one_target_named_twice_in_one_entry_is_one_edge(self):
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "clm-bb2222"\n'
            '  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
            '  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(self.facts(result), [(kb_util.FACT, "clm-bb2222")])
        self.assertEqual(self.targets(), ["clm-aa1111", "INVARIANT-S2"])

    def test_a_new_edge_beside_a_duplicate_is_still_written(self):
        self.run_op("add-depends-on", self._ADD_INVARIANT)
        result = self.run_op(
            "add-depends-on",
            '[[entry]]\nid = "clm-bb2222"\n'
            '  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
            '  [[entry.depends-on]]\n  id = "clm-aa1111"\n'
            '  [[entry.depends-on]]\n  id = "Axiom 1"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        # clm-aa1111 was already on the entry before any of this ran.
        self.assertEqual(self.targets(), ["clm-aa1111", "INVARIANT-S2", "axiom-1"])


# ---------------------------------------------------------------------------
# The refusal fixtures — each class once, each naming its own identity
# ---------------------------------------------------------------------------


class TestRefusalFixtures(_KbCase):
    """Every refusal class, through the real ops, on a real KB."""

    def test_an_unresolvable_depends_on_target(self):
        before = self.snapshot()
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "A Claim With A Bad Dependency"
            rigor = 0.7
            rationale = "Depends on nothing that exists."
              [[entry.depends-on]]
              id = "clm-999999"
            """,
        )
        self.assert_refused(result, "depends-on.id=clm-999999")
        self.assert_nothing_written(before)

    def test_a_claims_member_with_no_register_entry(self):
        # clm-dd4444 is cited by part1/orphan.md and keyed by no register, so
        # the inventory holds it while nothing canonical declares it. Today
        # that is a silent drop.
        self.assertIn("clm-dd4444", kb_index_lib.scan_authored_ids(self.kb_root))
        result = self.run_op(
            "set-frontmatter",
            '[[entry]]\ndocument = "part1/plain.md"\nkind = "leaf"\nclaims = ["clm-dd4444"]\n',
        )
        self.assert_refused(result, "claims=clm-dd4444")

    def test_a_marker_id_absent_from_the_documents_own_claims(self):
        result = self.run_op(
            "mark-claim-in-leaf",
            f'[[entry]]\ndocument = "part1/plain.md"\nid = "clm-bb2222"\nlocator = "{_LOCATOR}"\n',
        )
        self.assert_refused(result, "id=clm-bb2222")

    def test_a_locator_matching_nothing_in_the_document_body(self):
        # The other half of the placement pair: nowhere and more-than-one-place
        # are both refusals, because either one would put the marker somewhere
        # nobody chose. Its twin is this class's ambiguity fixture.
        before = self.snapshot()
        result = self.run_op(
            "mark-claim-in-leaf",
            '[[entry]]\ndocument = "part1/plain.md"\nid = "clm-aa1111"\n'
            'locator = "no sentence resembling this one appears in the document"\n',
        )
        self.assert_refused(result, "locator")
        self.assert_nothing_written(before)

    def test_a_duplicate_register_id(self):
        _plant_duplicate(self.kb_root)
        before = self.snapshot()
        result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assert_refused(result, "clm-aa1111")
        self.assert_nothing_written(before)

    def test_a_rigor_outside_its_inherited_domain(self):
        # Delegated to values.py, asserted end to end: today `confidence: 47`
        # parses as 47.0 and nothing checks it.
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "An Over-Confident Claim"
            rigor = 47
            rationale = "Out of domain."
            """,
        )
        self.assert_refused(result, "entry[1].rigor")

    def test_an_on_point_fraction_above_one(self):
        result = self.run_op(
            "set-on-point-fraction",
            '[[entry]]\nid = "sup-cc3333"\nclaim = "clm-bb2222"\nfraction = 1.1\n',
        )
        self.assert_refused(result, "entry[1].fraction")

    def test_a_support_id_in_a_claims_list(self):
        result = self.run_op(
            "set-frontmatter",
            '[[entry]]\ndocument = "part1/plain.md"\nkind = "leaf"\nclaims = ["sup-cc3333"]\n',
        )
        self.assert_refused(result, "entry[1].claims[1]")

    def test_a_malformed_values_file(self):
        result = self.run_op("insert-claim-entry", '[[entry]\nregister = "part1/claim-quality.md"\n')
        self.assert_refused(result, "entry")

    def test_a_target_path_outside_kb_root(self):
        outside = self.work / "escape.md"
        outside.write_text("untouched\n", encoding="utf-8")
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "../escape.md"
            title = "An Escaping Claim"
            rigor = 0.7
            rationale = "Should never be written."
            """,
        )
        self.assert_refused(result, "register")
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_a_target_path_reaching_outside_via_symlink(self):
        outside = self.work / "secret.md"
        outside.write_text("untouched\n", encoding="utf-8")
        (self.kb_root / "part1/link.md").symlink_to(outside)
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/link.md"
            title = "A Symlinked Claim"
            rigor = 0.7
            rationale = "Should never be written."
            """,
        )
        self.assert_refused(result, "register")
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_a_register_whose_census_already_fails(self):
        # The frozen 2026-09-02 corpus carries the defect in its own bytes:
        # every marker sits above its heading, so the first binds to nothing
        # and the register is one record short of its markers.
        (self.kb_root / "part2").mkdir()
        broken = self.kb_root / "part2/claim-quality.md"
        shutil.copyfile(_REGRESSION / "part2-claim-quality.md", broken)
        self.assertFalse(store.take_census(broken, self.kb_root).consistent)
        before = self.snapshot()
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part2/claim-quality.md"
            title = "A Claim Into A Losing Register"
            rigor = 0.7
            rationale = "Refused before anything is composed."
            """,
        )
        self.assert_refused(result, "part2/claim-quality.md")
        self.assert_nothing_written(before)

    def test_a_values_file_that_is_not_utf8(self):
        path = self.work / "values.toml"
        path.write_bytes(b'[[entry]]\nregister = "part1/\xff\xfe.md"\n')
        result = ops.insert_claim_entry(kb_root=self.kb_root, values_file=path)
        self.assert_refused(result, str(path))

    def test_a_nonexistent_register_without_the_create_intent(self):
        # A typo'd path can no longer succeed into a fresh register.
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part9/claim-quality.md"
            title = "A Claim Into Nowhere"
            rigor = 0.7
            rationale = "Creation is never implicit."
            """,
        )
        self.assert_refused(result, "part9/claim-quality.md")
        self.assertFalse((self.kb_root / "part9").exists())

    def test_the_same_insert_succeeds_once_creation_is_asked_for(self):
        # The control: the refusal above is the missing acknowledgment,
        # not a broken path.
        (self.kb_root / "part9").mkdir()
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part9/claim-quality.md"
            title = "A Claim Into A Fresh Register"
            rigor = 0.7
            rationale = "Creation was asked for."
            """,
            create=True,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertTrue(store.take_census(self.kb_root / "part9/claim-quality.md", self.kb_root).consistent)

    def test_an_ambiguous_locator_is_refused_rather_than_placed_arbitrarily(self):
        doc = self.kb_root / "part1/plain.md"
        doc.write_text(doc.read_text(encoding="utf-8") + f"\nAnd again, {_LOCATOR}.\n", encoding="utf-8")
        result = self.run_op("mark-claim-in-leaf", self.valid_values("mark-claim-in-leaf"))
        self.assert_refused(result, "locator")

    def test_a_frontmatter_replace_that_would_drop_a_hosted_declaration(self):
        # part1/leaf.md originates sup-cc3333. Replacing its block without
        # restating the declaration would destroy the node, so the op names it
        # instead of absorbing the loss.
        result = self.run_op(
            "set-frontmatter",
            '[[entry]]\ndocument = "part1/leaf.md"\nkind = "leaf"\nclaims = ["clm-aa1111"]\n',
        )
        self.assert_refused(result, "part1/leaf.md")

    def test_a_broken_frontmatter_renderer_refuses_and_leaves_no_temp(self):
        # The readback-failure injection on the leaf side. ``store``'s proof is
        # register-shaped, so a document write is proven by the op itself,
        # through the production leaf parser, inside the splice. With a renderer
        # that drops the claims list the write must refuse, the live file must
        # be byte-unchanged, and no temp may survive.
        doc = self.kb_root / "part1/plain.md"
        before = doc.read_bytes()
        empty = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf"))
        with mock.patch.object(ops.render, "render_frontmatter_block", lambda _values: empty):
            result = self.run_op("set-frontmatter", self.valid_values("set-frontmatter"))
        self.assert_refused(result, "part1/plain.md")
        self.assertEqual(doc.read_bytes(), before)
        self.assertEqual([p.name for p in doc.parent.iterdir() if p.name.endswith(store.TEMP_SUFFIX)], [])

    def test_an_unresolvable_root_is_an_environment_failure_not_a_refusal(self):
        # Exit 2 is a different bit from exit 7: nothing about the values can
        # fix an absent KB, and re-authoring them would be the wrong response.
        result = ops.insert_claim_entry(
            kb_root=self.work / "no-such-kb",
            values_file=self.values_file(self.valid_values("insert-claim-entry")),
        )
        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT)


# ---------------------------------------------------------------------------
# `_locate_excerpt` matches inside a blockquote
#
# Every author-distinguished block (theorem, proof, remark) in a built KB tree
# is a blockquote pandoc hard-wraps, median 12 lines and up to 55. The text
# below is drawn from a built leaf
# (`the-firm-scale-bifurcation/index.md`) to keep the wrapping realistic.
# ---------------------------------------------------------------------------


class TestLocateExcerptInsideBlockquotes(unittest.TestCase):
    """Direct coverage of `_locate_excerpt`'s matching and line arithmetic.

    `_insert_marker` splices at the returned index straight into the document
    it read (`store.splice_lines`), so the index must name a real physical
    line of that document — stripping blockquote markers is a search-side
    transform on a throwaway copy, never a rewrite of what gets indexed or
    returned.
    """

    _THEOREM = (
        "\n".join(
            [
                "# A Document",
                "",
                "The setup precedes the result directly.",
                r"> **Theorem 2** (The governance bifurcation). *For $`0 < \beta < 1`$ the",
                r"> origin is a boundary equilibrium that attracts from $`v > 0`$ but has",
                r"> an admissible unstable direction in $`v < 0`$; it is only metastable,",
                r"> and coexists with the stable zombie $`s^*`$ (a bistable regime).*",
            ]
        )
        + "\n"
    )

    def test_a_quoted_single_line_locates_at_its_real_line(self):
        at = ops._locate_excerpt(self._THEOREM, "it is only metastable")
        lines = self._THEOREM.splitlines()
        self.assertEqual(lines[at], r"> an admissible unstable direction in $`v < 0`$; it is only metastable,")

    def test_a_quoted_excerpt_spanning_a_hard_wrap_locates_at_its_first_line(self):
        # "the" ends one physical line and "origin ..." begins the next; a
        # locator straddling that wrap must resolve to the first of the two.
        at = ops._locate_excerpt(self._THEOREM, "the origin is a boundary equilibrium that attracts")
        lines = self._THEOREM.splitlines()
        self.assertEqual(lines[at], r"> **Theorem 2** (The governance bifurcation). *For $`0 < \beta < 1`$ the")

    def test_a_nested_quote_locates_at_its_real_line(self):
        document = (
            "\n".join(
                [
                    "# A Document",
                    "",
                    "> An outer remark introduces the setting.",
                    ">",
                    "> > Within this nested aside, the origin is a boundary equilibrium",
                    "> > once again, illustrating the same mechanism at one remove.",
                ]
            )
            + "\n"
        )
        at = ops._locate_excerpt(document, "Within this nested aside, the origin is a boundary equilibrium once again")
        lines = document.splitlines()
        self.assertEqual(lines[at], "> > Within this nested aside, the origin is a boundary equilibrium")

    def test_an_excerpt_spanning_the_quoted_unquoted_boundary_locates_at_its_first_line(self):
        # No blank line separates the paragraph from the blockquote here —
        # unlike pandoc's own output — precisely to exercise the transition a
        # leading blockquote marker used to sit in the middle of.
        at = ops._locate_excerpt(self._THEOREM, "the result directly. **Theorem 2**")
        lines = self._THEOREM.splitlines()
        self.assertEqual(lines[at], "The setup precedes the result directly.")

    def test_an_absent_locator_still_refuses(self):
        with self.assertRaises(ops._Refused):
            ops._locate_excerpt(self._THEOREM, "nothing resembling this sentence appears anywhere here")

    def test_a_locator_matching_once_plain_and_once_quoted_still_refuses(self):
        # Before blockquote-stripping, only the plain paragraph matched (one
        # hit) because the leading "> " broke the quoted copy's match. Now
        # both match, and two hits must still refuse rather than pick one.
        document = (
            "\n".join(
                [
                    "# A Document",
                    "",
                    "The lemma states that the origin is asymptotically stable outright.",
                    "",
                    "> The lemma states that the origin is asymptotically stable outright,",
                    "> restated here for the quoted record.",
                ]
            )
            + "\n"
        )
        with self.assertRaises(ops._Refused):
            ops._locate_excerpt(document, "the lemma states that the origin is asymptotically stable outright")


# ---------------------------------------------------------------------------
# `resolve_excerpt` — the one matcher, and its two tiers
#
# The document below carries the two things measured across the built KBs that
# break a strict match on a sentence a seat quoted correctly: a citation the
# converter wrapped in its own markup, and a hard wrap in the middle of the
# sentence.
# ---------------------------------------------------------------------------


class TestResolveExcerptEscalatesToAFoldedSearch(unittest.TestCase):
    """Exact wins where it is available; folding only adds what strict missed."""

    _DOCUMENT = (
        "\n".join(
            [
                "# A Document",
                "",
                'The measurement <span class="citation" data-cites="ashby64">(Ashby 1964)</span> settles the',
                "question of whether the regime is stable under perturbation.",
                "",
                "A second paragraph restates nothing.",
            ]
        )
        + "\n"
    )

    def _resolved(self, excerpt):
        return ops.resolve_excerpt(self._DOCUMENT, excerpt)

    def test_a_verbatim_quote_resolves_strictly(self):
        resolution = self._resolved("question of whether the regime is stable")
        self.assertEqual(resolution.tier, ops.STRICT_TIER)
        self.assertEqual(tuple(hit.line for hit in resolution.hits), (3,))

    def test_a_strict_hit_offset_names_the_quoted_characters(self):
        excerpt = "question of whether the regime is stable"
        (hit,) = self._resolved(excerpt).hits
        self.assertTrue(self._DOCUMENT[hit.offset :].startswith(excerpt))

    def test_a_quote_omitting_the_converters_citation_markup_resolves_folded(self):
        # The seat reads the sentence and writes what a reader sees; the span
        # wrapper is in the haystack and in no quotation of it.
        resolution = self._resolved("The measurement (Ashby 1964) settles the question of whether the regime")
        self.assertEqual(resolution.tier, ops.FOLDED_TIER)
        self.assertEqual(tuple(hit.line for hit in resolution.hits), (2,))

    def test_a_folded_hit_offset_names_the_first_character_of_the_sentence(self):
        (hit,) = self._resolved("the measurement (ashby 1964) SETTLES the question").hits
        self.assertTrue(self._DOCUMENT[hit.offset :].startswith("The measurement"))

    def test_case_typography_and_punctuation_stop_separating_a_quote_from_its_sentence(self):
        for excerpt in (
            "QUESTION OF WHETHER THE REGIME",
            "question—of—whether—the—regime",
            "question of whether, the regime",
        ):
            with self.subTest(excerpt=excerpt):
                resolution = self._resolved(excerpt)
                self.assertEqual(resolution.tier, ops.FOLDED_TIER)
                self.assertEqual(tuple(hit.line for hit in resolution.hits), (3,))

    def test_folding_never_carries_a_quote_across_a_paragraph_break(self):
        # `label.py` guarantees no span crosses one by relying on the blank
        # line's two spaces in the joined body. A fold that collapsed them would
        # retire that guarantee with nothing reporting it.
        crossing = "stable under perturbation. A second paragraph restates nothing."
        self.assertEqual(self._resolved(crossing).hits, ())

    def test_an_absent_quote_resolves_nowhere_at_either_tier(self):
        resolution = self._resolved("nothing resembling this sentence appears anywhere here")
        self.assertEqual(resolution.hits, ())
        self.assertEqual(resolution.tier, ops.FOLDED_TIER)

    def test_excerpt_lines_is_the_resolver_with_the_tier_dropped(self):
        for excerpt in (
            "question of whether the regime is stable",
            "The measurement (Ashby 1964) settles the question of whether the regime",
            "nothing resembling this sentence appears anywhere here",
        ):
            with self.subTest(excerpt=excerpt):
                self.assertEqual(
                    ops.excerpt_lines(self._DOCUMENT, excerpt),
                    tuple(hit.line for hit in self._resolved(excerpt).hits),
                )

    def test_a_line_index_still_names_a_physical_line_of_the_document(self):
        lines = self._DOCUMENT.splitlines()
        (hit,) = self._resolved("the measurement (ashby 1964) settles").hits
        self.assertEqual(lines[hit.line], self._DOCUMENT.splitlines()[2])
        self.assertIn("<span", lines[hit.line])


class TestCanonicalFormIsTheFoldTierTwoSearches(unittest.TestCase):
    """The reduction a caller counts words over is the one the matcher resolves by."""

    def test_a_line_carrying_no_words_canonicalises_to_nothing(self):
        for text in (
            '<span class="citation" data-cites="ashby64"></span>',
            "[](../index.md)",
            "> > ",
            "**—**",
            "$$ \\, $$",
        ):
            with self.subTest(text=text):
                self.assertEqual(ops.canonical_form(text), "")

    def test_a_latex_control_word_is_a_word_and_the_length_rule_is_what_excludes_it(self):
        # The fold is a SEARCH fold: `\qquad` has to survive as `qquad` or a
        # quotation carrying display maths stops resolving. So a fence line is
        # not emptied by canonicalisation — it is caught by the same short-form
        # rule that catches `Proof.`, and by the fence extents `identify` holds.
        self.assertEqual(ops.canonical_form("``` math"), "math")
        self.assertEqual(ops.canonical_form("\\begin{align}"), "begin align")
        self.assertLessEqual(len(ops.canonical_form("$$\\qquad\\qquad$$").split()), 3)

    def test_structural_boilerplate_canonicalises_to_its_own_few_words(self):
        self.assertEqual(ops.canonical_form("**Proof.**"), "proof")
        self.assertEqual(len(ops.canonical_form("*Definition 3.*").split()), 2)

    def test_markup_typography_and_case_are_gone_and_digits_are_kept(self):
        self.assertEqual(
            ops.canonical_form("The <em>rate</em> settles at $2$ — see [Fig. 4](../figs.md), *op. cit.*"),
            "the rate settles at 2 see fig 4 op cit",
        )

    def test_it_is_the_same_fold_the_folded_tier_resolves_by(self):
        # Not a pinned string: the claim is that the two agree, so the matcher
        # is asked to resolve exactly what this function says the text is.
        document = "# D\n\nThe <b>rate</b> settles at $2$ — see Fig. 4.\n"
        canonical = ops.canonical_form("The rate settles at 2, see Fig 4")
        resolution = ops.resolve_excerpt(document, canonical)

        self.assertEqual(resolution.tier, ops.FOLDED_TIER)
        self.assertEqual(tuple(hit.line for hit in resolution.hits), (2,))

    def test_a_paragraph_break_survives_it(self):
        # The guarantee `label.py` leans on: the barrier is still two spaces, so
        # a caller reads the result with `split` and never by counting spaces.
        self.assertEqual(ops.canonical_form("ends here.  Begins there."), "ends here  begins there")
        self.assertEqual(ops.canonical_form("ends here.  Begins there.").split(), ["ends", "here", "begins", "there"])


class TestNearestExcerptCarriesItsPosition(unittest.TestCase):
    """The trigger-A diagnostic: the window the search came closest to, placed."""

    _DOCUMENT = (
        "\n".join(
            [
                "# A Document",
                "",
                "The origin is a boundary equilibrium that attracts from above.",
                "",
                "Notation introduced earlier is restated here and nothing else.",
            ]
        )
        + "\n"
    )

    def test_a_near_miss_names_the_line_it_sits_on(self):
        near = ops.nearest_excerpt(self._DOCUMENT, "The origin is a boundary equilibrium that repels from above.")
        self.assertIsNotNone(near)
        self.assertEqual(near.line, 2)
        self.assertTrue(self._DOCUMENT[near.offset :].startswith("The origin"))

    def test_nothing_comparable_is_reported_as_nothing(self):
        self.assertIsNone(ops.nearest_excerpt(self._DOCUMENT, "a wholly unrelated string of words entirely"))


class TestMarkClaimInLeafAppendsAnInlineMarker(_KbCase):
    """A marker joins the block it marks instead of splitting it.

    Proven through the real op, and asserted on the **rendered structure**
    rather than on the bytes alone: a marker on a line of its own is legal
    Markdown that parses into a different document. It is block-level there, so
    it cuts a paragraph in half (``Para, RawBlock, Para`` — one of the author's
    sentences broken in two on the page) and it ends a blockquote it lands
    inside, detaching a point-12 environment label from the statement it labels.
    Byte assertions cannot see either; a parse can, so both are made.
    """

    #: A paragraph pandoc has wrapped, with the claim's own words opening it —
    #: the ordinary shape of a claim identified in prose, and the one a
    #: block-level marker splits.
    _PARAGRAPH = (
        "\n".join(
            [
                "# A Document",
                "",
                "The friction persists under",
                "governance suppression, and the argument continues past it.",
            ]
        )
        + "\n"
    )

    #: The point-12 shape, as a built tree carries it: an identified label line,
    #: a blank quoted line, then the statement with its optional-argument title,
    #: hard-wrapped the way pandoc wraps one.
    _STATEMENT = r"> **Lemma 5** (A $`\beta`$-independent fast certificate). *The escape"
    _LABELLED_BLOCK = (
        "\n".join(
            [
                "# A Document",
                "",
                "The setup precedes the result directly.",
                "",
                '> <span id="lem:vfast">**lemma**</span>',
                ">",
                _STATEMENT,
                r"> time is bounded above by a constant independent of $`\beta`$.*",
            ]
        )
        + "\n"
    )

    _NESTED = (
        "\n".join(
            [
                "# A Document",
                "",
                "> An outer remark introduces the setting.",
                ">",
                "> > Within this nested aside the same mechanism appears once more,",
                "> > at one remove from the argument that needed it.",
            ]
        )
        + "\n"
    )

    #: Two trailing spaces are Markdown's hard line break, and the corpus's
    #: front end emits them wherever the source broke a line.
    _HARD_BREAK = "# A Document\n\nOccupancy: banked unconditionally.  \nConnectivity: defensible cheaply.\n"

    def mark(self, body: str, locator: str, *, name: str = "quoted.md") -> str:
        """Write a leaf carrying ``body``, mark ``clm-aa1111`` at ``locator``, return the result.

        The metadata assertion is the production reader's — what the write was
        for is that this id comes back marked — so every test below is free to
        be about where the marker sits.
        """
        doc = self.kb_root / "part1" / name
        self.before = _document(render.FrontmatterValues(kind="leaf", claims=("clm-aa1111",)), body=body)
        doc.write_text(self.before, encoding="utf-8")
        result = self.run_op(
            "mark-claim-in-leaf",
            f'[[entry]]\ndocument = "part1/{name}"\nid = "clm-aa1111"\nlocator = "{locator}"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(kb_index_lib.parse_leaf(doc, self.kb_root).tier2_marked, frozenset({"clm-aa1111"}))
        return doc.read_text(encoding="utf-8")

    def _marked_line(self, marked: str) -> str:
        """The one line of ``marked`` that differs from what was written, asserted unique."""
        before, after = self.before.splitlines(), marked.splitlines()
        self.assertEqual(len(before), len(after), "a marker changed the document's line count")
        differing = [line for line, was in zip(after, before) if line != was]
        self.assertEqual(len(differing), 1, differing)
        return differing[0]

    def test_a_marked_paragraph_is_still_one_paragraph(self):
        # The case the placement exists for: a locator mid-paragraph, which is
        # where a claim identified in prose lands as a matter of course.
        marked = self.mark(self._PARAGRAPH, "The friction persists under", name="prose.md")
        self.assertEqual(
            self._marked_line(marked),
            f"The friction persists under {render.render_tier2_marker('clm-aa1111')}",
        )

        # The whole sentence in one block, with the marker inside it. Split, it
        # would be two blocks with the marker's RawBlock wedged between them.
        prose = [block for block in _pandoc_blocks(marked) if "governance" in json.dumps(block)]
        self.assertEqual([block["t"] for block in prose], ["Para"])
        self.assertIn("claim-quality: clm-aa1111", json.dumps(prose[0]))

    def test_a_marked_labelled_block_still_parses_as_one_blockquote(self):
        marked = self.mark(self._LABELLED_BLOCK, "The escape time is bounded above by a constant")
        self.assertEqual(
            self._marked_line(marked),
            f"{self._STATEMENT} {render.render_tier2_marker('clm-aa1111')}",
        )

        blocks = _pandoc_blocks(marked)
        quotes = [block for block in blocks if block["t"] == "BlockQuote"]
        self.assertEqual(len(quotes), 1, [block["t"] for block in blocks])
        self.assertIn("claim-quality: clm-aa1111", json.dumps(quotes[0]))

    def test_an_excerpt_spanning_a_quoted_hard_wrap_is_marked_and_proven(self):
        marked = self.mark(self._LABELLED_BLOCK, r"fast certificate). *The escape time is bounded")
        self.assertEqual(
            self._marked_line(marked),
            f"{self._STATEMENT} {render.render_tier2_marker('clm-aa1111')}",
        )

    def test_a_marker_inside_a_nested_quote_leaves_both_levels_standing(self):
        marked = self.mark(self._NESTED, "Within this nested aside the same mechanism appears once more")
        self.assertTrue(self._marked_line(marked).endswith(render.render_tier2_marker("clm-aa1111")))

        outer = [block for block in _pandoc_blocks(marked) if block["t"] == "BlockQuote"]
        self.assertEqual(len(outer), 1)
        inner = [block for block in outer[0]["c"] if block["t"] == "BlockQuote"]
        self.assertEqual(len(inner), 1, json.dumps(outer[0]))
        self.assertIn("claim-quality: clm-aa1111", json.dumps(inner[0]))

    def test_a_hard_line_break_on_the_located_line_survives(self):
        # The marker goes in front of the line's trailing whitespace, because
        # those two spaces are the author's line break and not padding.
        marked = self.mark(self._HARD_BREAK, "Occupancy: banked unconditionally.", name="broken.md")
        self.assertEqual(
            self._marked_line(marked),
            f"Occupancy: banked unconditionally. {render.render_tier2_marker('clm-aa1111')}  ",
        )

        prose = [block for block in _pandoc_blocks(marked) if "Occupancy:" in json.dumps(block)]
        self.assertEqual([block["t"] for block in prose], ["Para"])
        self.assertIn('"LineBreak"', json.dumps(prose[0]))


# ---------------------------------------------------------------------------
# The readback covers the declarations that originate nodes
# ---------------------------------------------------------------------------


class TestHostedDeclarationsAreProven(_KbCase):
    """A dropped ``exp-id:`` / ``sup-id:`` block is a refused write, not a PASS.

    A hosted declaration is the only thing in a frontmatter block that
    *originates* a node, so losing one destroys the node rather than mislaying
    an attribute. Each test writes the declaration first — the control, without
    which a refusal below would be evidence that the values file was wrong
    rather than that the readback works — and then re-runs the identical values
    through a renderer that drops the block.
    """

    def _dropping(self, **emptied):
        """The real renderer with the named record fields emptied on the way in."""
        real = render.render_frontmatter_block
        return lambda supplied: real(dataclasses.replace(supplied, **emptied))

    def _assert_untouched(self, doc: Path, before: bytes) -> None:
        self.assertEqual(doc.read_bytes(), before)
        self.assertEqual([p.name for p in doc.parent.iterdir() if p.name.endswith(store.TEMP_SUFFIX)], [])

    def test_a_dropped_support_declaration_refuses_and_the_live_file_stands(self):
        doc = self.kb_root / "part1/leaf.md"
        text = """
            [[entry]]
            document = "part1/leaf.md"
            kind = "leaf"
            claims = ["clm-aa1111"]
              [[entry.support-node]]
              sup-id = "sup-cc3333"
                [[entry.support-node.supports]]
                id = "clm-bb2222"
                fraction = 0.5
            """
        control = self.run_op("set-frontmatter", text)
        self.assertEqual(control.exit_code, ops.ExitCode.WRITTEN, control.lines())
        self.assertEqual([n.id for n in kb_index_lib.parse_support_leaf(doc, self.kb_root)], ["sup-cc3333"])

        before = doc.read_bytes()
        with mock.patch.object(ops.render, "render_frontmatter_block", self._dropping(support_nodes=())):
            result = self.run_op("set-frontmatter", text)
        self.assert_refused(result, "part1/leaf.md")
        self._assert_untouched(doc, before)

    def test_a_dropped_experiment_declaration_refuses_and_the_live_file_stands(self):
        # An experiment's declaration is its whole canonical existence: there is
        # no register entry behind it to make the loss partial.
        born = self.run_op("insert-experiment-entry", self.valid_values("insert-experiment-entry"))
        self.assertEqual(born.exit_code, ops.ExitCode.WRITTEN, born.lines())
        (exp_id,) = born.minted
        doc = self.kb_root / "part1/plain.md"
        text = f"""
            [[entry]]
            document = "part1/plain.md"
            kind = "leaf"
            claims = ["clm-aa1111"]
              [[entry.experiment-node]]
              exp-id = "{exp_id}"
              status = "run"
                [[entry.experiment-node.strengthens]]
                id = "clm-aa1111"
                strength = 0.8
            """
        control = self.run_op("set-frontmatter", text)
        self.assertEqual(control.exit_code, ops.ExitCode.WRITTEN, control.lines())
        self.assertEqual([n.id for n in kb_index_lib.parse_experiment_leaf(doc, self.kb_root)], [exp_id])

        before = doc.read_bytes()
        with mock.patch.object(ops.render, "render_frontmatter_block", self._dropping(experiment_nodes=())):
            result = self.run_op("set-frontmatter", text)
        self.assert_refused(result, "part1/plain.md")
        self._assert_untouched(doc, before)

    def test_a_mis_rendered_pair_inside_a_kept_declaration_is_caught_too(self):
        # The block survives and the node with it; one beneficiary's fraction
        # does not. Nothing about the census or the id would notice.
        text = """
            [[entry]]
            document = "part1/leaf.md"
            kind = "leaf"
            claims = ["clm-aa1111"]
              [[entry.support-node]]
              sup-id = "sup-cc3333"
                [[entry.support-node.supports]]
                id = "clm-bb2222"
                fraction = 0.5
            """
        self.assertEqual(self.run_op("set-frontmatter", text).exit_code, ops.ExitCode.WRITTEN)
        doc = self.kb_root / "part1/leaf.md"
        before = doc.read_bytes()
        blanked = self._dropping(
            support_nodes=(render.SupportDecl(sup_id="sup-cc3333", supports=(("clm-bb2222", 0.25),)),)
        )
        with mock.patch.object(ops.render, "render_frontmatter_block", blanked):
            result = self.run_op("set-frontmatter", text)
        self.assert_refused(result, "part1/leaf.md")
        self._assert_untouched(doc, before)


class TestDerivedRollUpsSurviveTheReplace(_KbCase):
    """A block replace carries the derived roll-ups over, empty ones included.

    ``subtree-claims`` / ``subtree-experiments`` are refresh's to write and no
    values file's to supply, so ``set-frontmatter`` reads them off the document
    it is replacing and puts them back. The empty roll-up is the case a
    truthiness test drops in silence: ``subtree-claims: []`` is a walk that
    found nothing and an absent key is a walk that never ran, and the verifier
    reads both as the empty union — so nothing downstream reports the loss.
    """

    def _stamp(self, doc: Path, *, claims: tuple[str, ...], experiments: tuple[str, ...]) -> None:
        text = doc.read_text(encoding="utf-8")
        text = store.replace_or_insert_frontmatter_field(
            text, field="subtree-claims", ids=list(claims), anchor_prefix="kind:"
        )
        text = store.replace_or_insert_frontmatter_field(
            text, field="subtree-experiments", ids=list(experiments), anchor_prefix="subtree-claims:"
        )
        doc.write_text(text, encoding="utf-8")

    def test_a_derived_roll_up_is_carried_over_whatever_its_length(self):
        for claims, experiments in (((), ()), (("clm-aa1111",), ("exp-gg7777",))):
            with self.subTest(claims=claims):
                self.setUp()
                doc = self.kb_root / "part1/plain.md"
                self._stamp(doc, claims=claims, experiments=experiments)

                result = self.run_op("set-frontmatter", self.valid_values("set-frontmatter"))
                self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())

                fields = kb_index_lib.parse_frontmatter(doc.read_text(encoding="utf-8"))
                self.assertEqual(fields["subtree-claims"], list(claims))
                self.assertEqual(fields["subtree-experiments"], list(experiments))


class TestEveryLeafWriteIsProven(_KbCase):
    """The structural half: what proves a leaf write cannot rot.

    ``store``'s proof is register-shaped, so an op that writes a document is
    proven by nothing unless it brings its own prover — and a prover that
    compares five of a record's seven fields is a check that passes over the two
    that matter. Neither gap is visible in any single test, so both are asserted
    over the registry: every planned edit carries a proof of some kind, the ops
    that carry their own are exactly the ones declared in
    :data:`ops.LEAF_PROOF_COVERAGE`, and each declaration partitions its op's
    values vocabulary. A field added tomorrow lands in neither half and fails
    here — the same anti-rot shape as ``store.COMPARED_FIELDS``' partition test.
    """

    def _intents(self, op: str) -> list:
        """The intents one op plans for its valid values, captured at the fold.

        A fresh KB per op, as :class:`TestEveryOpWrites` takes one: several ops
        target ``part1/plain.md``, and one of them leaves a hosted declaration
        behind that the next one's values do not restate — a refusal about the
        order of a walk rather than about the op under it.
        """
        self.setUp()
        captured: list = []
        real = ops._batch

        def spy(intents, *, kb_root):
            captured.extend(intents)
            return real(intents, kb_root=kb_root)

        with mock.patch.object(ops, "_batch", spy):
            result = self.run_op(op, self.valid_values(op))
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        return captured

    def test_every_planned_edit_carries_a_proof_of_some_kind(self):
        """Three kinds now, and the third is the newest.

        An update op's expectation is derived from the baseline rather than
        stated at plan time, so ``intent.expect`` is empty for one and the
        proof travels as ``expect_current``. Both are counted here; an intent
        carrying neither, and no prover of its own, is written by nothing that
        reads it back.
        """
        for name in ops.OPS:
            with self.subTest(op=name):
                intents = self._intents(name)
                self.assertTrue(intents, f"{name} planned nothing")
                for intent in intents:
                    self.assertTrue(
                        intent.expect or intent.expect_current is not None or intent.prove is not None,
                        f"{name} plans an edit to {intent.path} that neither store nor the op proves",
                    )

    def test_a_deferred_expectation_actually_reaches_the_edit_it_proves(self):
        """The teeth on the clause above: ``expect_current`` is not a promise.

        An intent that carries one and an ``Edit`` whose ``expect`` is still
        empty when ``store`` proves it would be an op with NO readback at all —
        and it would pass the walk above, because the walk sees the intent
        rather than what the fold did with it. So the edit is driven through its
        own splice here, exactly as ``store`` drives it, and the expectation is
        required to have arrived.
        """
        for name in ops.OPS:
            with self.subTest(op=name):
                intents = self._intents(name)
                deferring = [i for i in intents if i.expect_current is not None]
                if not deferring:
                    continue
                # `_intents` left `self.kb_root` pointing at the tree these
                # intents were planned against; re-seeding it here would leave
                # every captured target addressing a KB that no longer exists.
                edits = ops._batch(deferring, kb_root=self.kb_root)
                for edit in edits:
                    self.assertEqual(list(edit.expect), [], "an expectation arrived before the splice ran")
                    edit.splice((self.kb_root / edit.path).read_text(encoding="utf-8"))
                    self.assertEqual(
                        len(edit.expect),
                        len([i for i in deferring if i.path == edit.path]),
                        f"{name}: the splice ran and the edit's expectation is still short",
                    )

    def test_the_ops_that_carry_their_own_prover_are_exactly_the_declared_ones(self):
        proving = {name for name in ops.OPS if any(i.prove is not None for i in self._intents(name))}
        self.assertEqual(proving, set(ops.LEAF_PROOF_COVERAGE))

    def test_each_declared_coverage_partitions_that_ops_values_vocabulary(self):
        for name, coverage in ops.LEAF_PROOF_COVERAGE.items():
            with self.subTest(op=name):
                vocabulary = {field.name for field in values.OP_FIELDS[name]}
                self.assertEqual(coverage.compared | set(coverage.excluded), vocabulary)
                self.assertEqual(coverage.compared & set(coverage.excluded), set())
                for key, reason in coverage.excluded.items():
                    self.assertTrue(reason.strip(), f"{name}.{key} is excluded from the proof with no reason")

    def test_the_frontmatter_proof_covers_the_rendered_record_whole(self):
        # The prover loops over this map, so a rendered field missing from it is
        # a field written and never read back.
        declared = {field.name for field in dataclasses.fields(render.FrontmatterValues)}
        self.assertEqual(set(ops._FRONTMATTER_PROVEN_FIELDS), declared)


# ---------------------------------------------------------------------------
# A KB file that will not decode is the environment, located
# ---------------------------------------------------------------------------


class TestAnUndecodableKbFileIsEnvironmentUnfit(_KbCase):
    """One latin-1 byte under kb-root, and the ladder still holds.

    The store-wide scans read the whole store before the target has been
    resolved, so an undecodable file anywhere decides the outcome of a write
    into a clean file elsewhere. That is a fact about the KB and not about the
    caller's values, so it exits 2 — "unreadable tree" — rather than a
    refusal that would send a caller back to re-author values that were right.
    """

    _LATIN1 = "Café pédagogique\n".encode("latin-1")

    def test_a_register_the_scan_cannot_decode_stops_every_write_op(self):
        (self.kb_root / "part2").mkdir()
        (self.kb_root / "part2/claim-quality.md").write_bytes(b"# Part 2\n\n## " + self._LATIN1)
        before = self.snapshot()
        for name in ops.OPS:
            with self.subTest(op=name):
                result = self.run_op(name, self.valid_values(name))
                self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())
                self.assertEqual(result.written, ())
                failures = [item for item in result.report if item.status == kb_util.FAIL]
                self.assertEqual([item.name for item in failures], ["part2/claim-quality.md"], result.lines())
                self.assertIn("restore:", failures[0].detail)
        self.assert_nothing_written(before)

    def test_a_leaf_the_inventory_scan_cannot_decode_is_named_too(self):
        # The second of the two scans: the register walk passes this file by,
        # and `scan_authored_ids` reads every leaf.
        (self.kb_root / "part1/bad.md").write_bytes(b"<!-- kb-frontmatter\nkind: leaf\n-->\n\n" + self._LATIN1)
        result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())
        self.assertEqual([item.name for item in result.report], ["part1/bad.md"])

    def test_a_non_utf8_values_file_is_still_the_callers_own_refusal(self):
        # The two decode failures are different bits and stay so, even with both
        # present at once: the values file is read before the store is, and a
        # values file is the caller's to fix (7) where a KB file is
        # not (2).
        (self.kb_root / "part2").mkdir()
        (self.kb_root / "part2/claim-quality.md").write_bytes(b"# Part 2\n\n## " + self._LATIN1)
        path = self.work / "values.toml"
        path.write_bytes(b'[[entry]]\nregister = "part1/\xff\xfe.md"\n')
        result = ops.insert_claim_entry(kb_root=self.kb_root, values_file=path)
        self.assert_refused(result, str(path))


# ---------------------------------------------------------------------------
# The fan-out's two homes, through the op surface
# ---------------------------------------------------------------------------


class TestAWorkEntrysRationaleIsEditable(_KbCase):
    """``set-rationale`` over the entry kind whose prose had no second writer.

    A work's rationale is composed mechanically at mint time, and the
    convention a built KB ships asks a cited authority to carry a quoted
    excerpt linked to a durable path — a judgement that cannot exist when the
    entry is minted. The guarantee asserted here is the one
    ``set-work-strength`` already makes in the other direction: the field named
    moves and nothing else in the register does.
    """

    #: The sanctioned authority-citation form, spelled as ``verify_citations``
    #: reads one, and the whole reason this op reaches a work at all. What it
    #: has to survive here is the collapse and the readback; the gates that
    #: check the excerpt against its target run over a real KB in
    #: ``test_writeapi_integration.py``.
    CITATION = '["the anchor result is established here"](../common/leaf-single.md#anchor-result-restated)'

    def cited_work(self) -> kb_index_lib.ExternalWork:
        entries = kb_index_lib.parse_work_entries(self.register, self.kb_root)
        return next(entry for entry in entries if entry.id == "work-nobody2026")

    def test_the_rationale_line_is_the_only_line_that_moves(self):
        before = self.register.read_text(encoding="utf-8").splitlines()
        result = self.run_op(
            "set-rationale",
            f"[[entry]]\nid = \"work-nobody2026\"\nrationale = '''Judged against {self.CITATION}.'''\n",
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = self.register.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(before), len(after), "the edit changed the register's line count")
        moved = [(n, old, new) for n, (old, new) in enumerate(zip(before, after)) if old != new]
        self.assertEqual(len(moved), 1, f"more than the rationale moved: {moved}")
        self.assertTrue(moved[0][2].startswith("- rationale:"), moved[0])

    def test_the_record_reads_back_with_its_other_two_fields_intact(self):
        before = self.cited_work()
        result = self.run_op(
            "set-rationale",
            f"[[entry]]\nid = \"work-nobody2026\"\nrationale = '''Judged against {self.CITATION}.'''\n",
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = self.cited_work()
        self.assertEqual(after.rationale, f"Judged against {self.CITATION}.")
        self.assertNotEqual(after.rationale, before.rationale)
        self.assertEqual(after.title, before.title)
        self.assertEqual(after.strength, before.strength)

    def test_a_work_with_no_entry_is_refused(self):
        # The kind is admitted; the referent still has to exist. Resolution is
        # ops' against the authored inventory, as it is for every other id.
        before = self.snapshot()
        result = self.run_op(
            "set-rationale", '[[entry]]\nid = "work-nobodyatall2099"\nrationale = "Nothing stands here."\n'
        )
        self.assert_refused(result, "id=work-nobodyatall2099")
        self.assert_nothing_written(before)


class TestStagedFanOut(_KbCase):
    """``insert-support-entry`` can author the pre-leaf home of a fan-out.

    A ``sup-`` id is minted at phase-2.6 and the document that will host it is
    not written until phase-3, so for one whole stage the register entry is the
    only place its beneficiaries can be recorded — and phase-2.6's own
    postcondition reads them there.
    """

    def _staged(self) -> ops.Result:
        return self.run_op(
            "insert-support-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "A Staged Support"
            rigor = 0.8
            rationale = "Derives the beneficiary in full."
              [[entry.supports]]
              id = "clm-aa1111"
              fraction = 0.5
              [[entry.supports]]
              id = "clm-bb2222"
              fraction = "*pending*"
            """,
        )

    def test_the_pairs_reach_the_scan_the_postcondition_reads(self):
        result = self._staged()
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        sup_id = result.minted[0]
        edges = kb_index_lib.scan_authored_support_edges(self.kb_root)
        self.assertEqual(edges[sup_id], (("clm-aa1111", 0.5), ("clm-bb2222", kb_index_lib.PENDING_FRACTION)))

    def test_the_staged_entry_still_reads_back_as_a_whole_record(self):
        # The block sits inside the `### Quality` section the entry parser
        # reads, so "the pairs landed" is only half the question.
        result = self._staged()
        sup_id = result.minted[0]
        raw = kb_index_lib.parse_support_quality_entries(self.register, self.kb_root)[sup_id]
        self.assertEqual(raw["quality"], 0.8)
        self.assertEqual(raw["rationale"], "Derives the beneficiary in full.")
        self.assertEqual(raw["depends_on"], ())
        self.assertTrue(store.take_census(self.register, self.kb_root).consistent)

    def test_an_unresolvable_beneficiary_is_refused(self):
        # A staged beneficiary is an id-valued field like any other.
        before = self.snapshot()
        result = self.run_op(
            "insert-support-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "A Support Lifting Nothing Real"
            rigor = 0.8
            rationale = "Names a claim that does not exist."
              [[entry.supports]]
              id = "clm-999999"
              fraction = 0.5
            """,
        )
        self.assert_refused(result, "supports.id=clm-999999")
        self.assert_nothing_written(before)

    def test_an_update_to_a_staged_entry_leaves_the_block_alone(self):
        # The other direction: `set-rigor` rewrites one physical line, and the
        # staged pairs are part of "nothing else moved". An expectation that
        # did not carry them would refuse this write outright.
        sup_id = self._staged().minted[0]
        result = self.run_op("set-rigor", f'[[entry]]\nid = "{sup_id}"\nrigor = 0.42\n')
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        raw = kb_index_lib.parse_support_quality_entries(self.register, self.kb_root)[sup_id]
        self.assertEqual(raw["quality"], 0.42)
        self.assertEqual(
            kb_index_lib.scan_authored_support_edges(self.kb_root)[sup_id],
            (("clm-aa1111", 0.5), ("clm-bb2222", kb_index_lib.PENDING_FRACTION)),
        )


class TestOnPointFractionFindsItsHome(_KbCase):
    """The op rewrites the pair where it is authored, and refuses where it is not."""

    def _stage_a_support(self) -> str:
        """Mint an unhosted support staging one pending pair; return its id."""
        result = self.run_op(
            "insert-support-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "An Unhosted Support"
            rigor = 0.8
            rationale = "Its hosting document does not exist yet."
              [[entry.supports]]
              id = "clm-aa1111"
              fraction = "*pending*"
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        sup_id = result.minted[0]
        self.assertIsNone(kb_index_lib.scan_authored_ids(self.kb_root)[sup_id].hosting_leaf)
        return sup_id

    def test_a_hosted_support_is_still_updated_in_its_document(self):
        # sup-cc3333 is declared in part1/leaf.md. The canonical home wins
        # wherever it exists: it is the one the claim graph reads.
        register_before = self.register.read_bytes()
        result = self.run_op(
            "set-on-point-fraction",
            '[[entry]]\nid = "sup-cc3333"\nclaim = "clm-bb2222"\nfraction = 0.25\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(result.written, ("part1/leaf.md",))
        self.assertEqual(self.register.read_bytes(), register_before)
        node = kb_index_lib.parse_support_leaf(self.kb_root / "part1/leaf.md", self.kb_root)[0]
        self.assertEqual(dict(node.supports)["clm-bb2222"], 0.25)

    def test_a_zero_fraction_is_written_and_proven_like_any_other(self):
        # Zero is a judgement about the edge, not the absence of one: the op
        # composes it, the readback proof reads it, and the pair stays.
        result = self.run_op(
            "set-on-point-fraction",
            '[[entry]]\nid = "sup-cc3333"\nclaim = "clm-bb2222"\nfraction = 0.0\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        node = kb_index_lib.parse_support_leaf(self.kb_root / "part1/leaf.md", self.kb_root)[0]
        self.assertEqual(dict(node.supports)["clm-bb2222"], 0.0)

    def test_an_unhosted_support_is_updated_in_the_register(self):
        # Without this, phase-2.6's own obligation, "no fraction left
        # *pending*", would be unsatisfiable: the pairs it had to score were
        # all in the home the op would not write.
        sup_id = self._stage_a_support()
        result = self.run_op(
            "set-on-point-fraction",
            f'[[entry]]\nid = "{sup_id}"\nclaim = "clm-aa1111"\nfraction = 0.75\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(result.written, (self.rel,))
        self.assertEqual(kb_index_lib.scan_authored_support_edges(self.kb_root)[sup_id], (("clm-aa1111", 0.75),))

    def test_the_staged_update_moves_one_pair_and_nothing_else(self):
        sup_id = self._stage_a_support()
        self.run_op(
            "add-depends-on",
            f'[[entry]]\nid = "{sup_id}"\n  [[entry.depends-on]]\n  id = "clm-bb2222"\n',
        )
        before = kb_index_lib.parse_support_quality_entries(self.register, self.kb_root)[sup_id]
        result = self.run_op(
            "set-on-point-fraction",
            f'[[entry]]\nid = "{sup_id}"\nclaim = "clm-aa1111"\nfraction = 0.75\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        after = kb_index_lib.parse_support_quality_entries(self.register, self.kb_root)[sup_id]
        for field in ("quality", "rationale", "title", "solidity"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual([e.target for e in after["depends_on"]], ["clm-bb2222"])

    def test_a_pair_in_neither_home_is_still_refused(self):
        # The refusal survives where it is correct: this op re-scores an edge
        # that exists, it does not author one.
        sup_id = self._stage_a_support()
        before = self.snapshot()
        result = self.run_op(
            "set-on-point-fraction",
            f'[[entry]]\nid = "{sup_id}"\nclaim = "clm-bb2222"\nfraction = 0.75\n',
        )
        self.assert_refused(result, f"id={sup_id}")
        self.assert_nothing_written(before)


# ---------------------------------------------------------------------------
# The plan window: an expectation is derived from the bytes it is proven
# against, so a writer landing before the baseline read is absorbed and one
# landing after it is CONTENDED. Neither is ever reported as a bad value.
# ---------------------------------------------------------------------------


class TestTheExpectationComesFromTheBaseline(_KbCase):
    """The concurrent-write plan window.

    The interleaving is forced at the one seam that reproduces it: a concurrent
    writer that lands **after** the op has planned and **before** ``store`` reads
    the baseline it splices. Wrapping :func:`store.apply_edits` puts the mutation
    in exactly that gap.
    """

    def land_a_writer_in_the_plan_window(self, mutate) -> None:
        """Run ``mutate(text) -> text`` over the register between plan and baseline."""
        real = store.apply_edits

        def interleaved(**kwargs):
            self.register.write_text(mutate(self.register.read_text(encoding="utf-8")), encoding="utf-8")
            return real(**kwargs)

        patch = mock.patch.object(store, "apply_edits", interleaved)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_concurrent_edge_on_the_same_entry_no_longer_refuses_the_rigor_write(self):
        # The review's own shape: a slow `set-rigor` and an `add-depends-on`
        # landing on the same entry. Both values were right; both writes belong.
        self.run_op("add-depends-on", '[[entry]]\nid = "clm-aa1111"\n  [[entry.depends-on]]\n  id = "Axiom 1"\n')
        self.land_a_writer_in_the_plan_window(
            lambda text: text.replace(
                "- rationale: The anchor.", "- depends-on:\n  - clm-bb2222 — Second Claim\n- rationale: The anchor."
            )
        )
        result = self.run_op("set-rigor", '[[entry]]\nid = "clm-aa1111"\nrigor = 0.42\n')

        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-aa1111"
        )
        # This op's field landed...
        self.assertEqual(record.confidence, 0.42)
        # ...and the intervening writer's edge is still there, unoverwritten.
        self.assertIn("clm-bb2222", [edge.target for edge in record.depends_on])

    def test_a_concurrent_rigor_change_no_longer_refuses_the_edge_write(self):
        self.land_a_writer_in_the_plan_window(lambda text: text.replace("- confidence: 0.6", "- confidence: 0.31"))
        result = self.run_op(
            "add-depends-on", '[[entry]]\nid = "clm-bb2222"\n  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
        )

        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root) if e.id == "clm-bb2222"
        )
        self.assertEqual(record.confidence, 0.31)
        self.assertIn("invariant-s2", [edge.target.lower() for edge in record.depends_on])

    def test_a_concurrent_claims_addition_no_longer_refuses_the_marker(self):
        """The same window, on the leaf side: mark-claim's membership read."""
        leaf = self.kb_root / "part1/plain.md"
        real = store.apply_edits

        def interleaved(**kwargs):
            leaf.write_text(
                leaf.read_text(encoding="utf-8").replace("claims: [clm-aa1111]", "claims: [clm-aa1111, clm-bb2222]"),
                encoding="utf-8",
            )
            return real(**kwargs)

        with mock.patch.object(store, "apply_edits", interleaved):
            result = self.run_op(
                "mark-claim-in-leaf",
                f'[[entry]]\ndocument = "part1/plain.md"\nid = "clm-bb2222"\nlocator = "{_LOCATOR}"\n',
            )

        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertIn("clm-bb2222", kb_index_lib.parse_leaf(leaf, self.kb_root).tier2_marked)

    def test_a_membership_that_is_absent_from_the_baseline_is_still_refused(self):
        """The refusal survives where it is correct — it only moved reads."""
        before = self.snapshot()
        result = self.run_op(
            "mark-claim-in-leaf",
            f'[[entry]]\ndocument = "part1/plain.md"\nid = "clm-bb2222"\nlocator = "{_LOCATOR}"\n',
        )
        self.assert_refused(result, "id=clm-bb2222")
        self.assert_nothing_written(before)

    def test_a_file_that_moves_after_the_baseline_read_is_still_retry(self):
        """The other side of the same seam: the retry window still answers 8.

        Closing the plan window must not close the retry window — a writer
        landing between ``store``'s baseline read and its replace is a
        *retry*, and collapsing that into a success would be the silent
        overwrite the lock exists to stop.
        """
        real = store._still_matches
        with mock.patch.object(
            store, "_still_matches", lambda path, baseline: False if path == self.register else real(path, baseline)
        ):
            result = self.run_op("set-rigor", '[[entry]]\nid = "clm-bb2222"\nrigor = 0.55\n')
        self.assertEqual(result.exit_code, ops.ExitCode.RETRY, result.lines())


# ---------------------------------------------------------------------------
# Two spellings of one file are one edit, not a duplicate-target refusal
# ---------------------------------------------------------------------------


class TestOneFileSpelledTwoWaysIsOneEdit(_KbCase):
    """Two spellings of one path fold into one edit.

    ``store`` claims a target by its RESOLVED subject. Folding the batch by
    the caller's spelling instead would turn one file into two edits — and
    ``store`` refuses two edits on one file, because both would splice from
    the same baseline. A values file that spells its register two ways must
    not trip that ``duplicate-target`` refusal.
    """

    def test_two_spellings_of_one_register_fold_into_one_edit(self):
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "Spelled Plainly"
            rigor = 0.7
            rationale = "The first spelling."
            [[entry]]
            register = "part1/../part1/claim-quality.md"
            title = "Spelled Round About"
            rigor = 0.7
            rationale = "The second spelling."
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        # One edit, so one written path — and it is the spelling the caller
        # wrote first, not the resolved absolute one.
        self.assertEqual(result.written, (self.rel,))
        self.assertEqual(len(result.minted), 2)
        titles = {e.id: e.title for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)}
        self.assertEqual(
            sorted(titles[node_id] for node_id in result.minted), ["Spelled Plainly", "Spelled Round About"]
        )

    def test_two_edits_on_genuinely_different_files_are_still_two(self):
        """The fold must not collapse what is actually two targets."""
        (self.kb_root / "part2").mkdir()
        (self.kb_root / "part2/claim-quality.md").write_text("# Part 2 Claim Quality\n", encoding="utf-8")
        result = self.run_op(
            "insert-claim-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "In Part One"
            rigor = 0.7
            rationale = "One."
            [[entry]]
            register = "part2/claim-quality.md"
            title = "In Part Two"
            rigor = 0.7
            rationale = "Two."
            """,
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(sorted(result.written), [self.rel, "part2/claim-quality.md"])


# ---------------------------------------------------------------------------
# The leaf-side half — every splice this module composes cuts with
# store.splice_lines, so a metadata edit keeps the bytes it did not name
# ---------------------------------------------------------------------------


class TestASpliceKeepsTheBytesItDidNotName(_KbCase):
    """The four splices ``ops`` composes itself, on ``store.splice_lines``.

    A splice that rebuilds its document as
    ``"\\n".join(document.splitlines())`` reads as a no-op and rewrites
    every line terminator in the file — including the author's body prose,
    which this API leaves untouched.

    The measurement is a byte count, not a line count: the parsers normalize
    whitespace, so a readback cannot see this and neither can the retry check.
    """

    #: A break `str.splitlines` splits on and `"\n".join` turns into a newline,
    #: sitting in body prose no op names. Its count is preserved EXACTLY: no
    #: splice emits one, so any change to it is the whole-document rewrite.
    FORM_FEED = "\x0c"

    def crlf(self, path: Path) -> None:
        """Rewrite a fixture file in CRLF, its prose carrying an exotic break."""
        text = path.read_text(encoding="utf-8")
        if "# A Document" in text:
            text += f"\nA closing note{self.FORM_FEED}with a form feed in it.\n"
        path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    def assert_terminators_survive(self, path: Path, before: bytes) -> None:
        """Every LF is still part of a CRLF, and no exotic break became one.

        Stated as "no bare LF" rather than as an exact CR count, because an op
        that inserts a line legitimately adds the terminator that line needs —
        what it may not do is convert the ones already there. Before this row
        one `set-rigor` over a CRLF register deleted all 123 of its CR bytes,
        and one `set-frontmatter` turned a leaf's form feed into a newline in
        the author's prose.
        """
        after = path.read_bytes()
        self.assertEqual(after.count(b"\n"), after.count(b"\r\n"), f"{path.name} gained a bare LF")
        self.assertGreaterEqual(after.count(b"\r"), before.count(b"\r"), f"{path.name} lost a CR")
        self.assertEqual(
            after.count(self.FORM_FEED.encode()),
            before.count(self.FORM_FEED.encode()),
            f"{path.name}'s form feed count changed — a break in body prose was rewritten",
        )

    def test_set_rigor_over_a_crlf_register_keeps_every_cr(self):
        self.crlf(self.register)
        before = self.register.read_bytes()
        result = self.run_op("set-rigor", '[[entry]]\nid = "clm-bb2222"\nrigor = 0.55\n')
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assert_terminators_survive(self.register, before)

    def test_add_depends_on_over_a_crlf_register_keeps_every_cr(self):
        self.crlf(self.register)
        before = self.register.read_bytes()
        result = self.run_op(
            "add-depends-on", '[[entry]]\nid = "clm-bb2222"\n  [[entry.depends-on]]\n  id = "INVARIANT-S2"\n'
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assert_terminators_survive(self.register, before)

    def test_a_staged_pair_rewrite_over_a_crlf_register_keeps_every_cr(self):
        result = self.run_op(
            "insert-support-entry",
            """
            [[entry]]
            register = "part1/claim-quality.md"
            title = "An Unhosted Support"
            rigor = 0.8
            rationale = "Not hosted yet."
              [[entry.supports]]
              id = "clm-aa1111"
              fraction = "*pending*"
            """,
        )
        sup_id = result.minted[0]
        self.crlf(self.register)
        before = self.register.read_bytes()
        result = self.run_op(
            "set-on-point-fraction", f'[[entry]]\nid = "{sup_id}"\nclaim = "clm-aa1111"\nfraction = 0.75\n'
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assert_terminators_survive(self.register, before)
        self.assertEqual(kb_index_lib.scan_authored_support_edges(self.kb_root)[sup_id], (("clm-aa1111", 0.75),))

    def test_a_marker_insert_leaves_the_bodys_own_breaks_alone(self):
        leaf = self.kb_root / "part1/plain.md"
        self.crlf(leaf)
        before = leaf.read_bytes()
        result = self.run_op(
            "mark-claim-in-leaf",
            f'[[entry]]\ndocument = "part1/plain.md"\nid = "clm-aa1111"\nlocator = "{_LOCATOR}"\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assert_terminators_survive(leaf, before)
        self.assertIn("clm-aa1111", kb_index_lib.parse_leaf(leaf, self.kb_root).tier2_marked)

    def test_a_first_frontmatter_block_leaves_the_bodys_own_breaks_alone(self):
        bare = self.kb_root / "part1/bare.md"
        bare.write_text(f"[Up](index.md)\n\n{_BODY}", encoding="utf-8")
        self.crlf(bare)
        before = bare.read_bytes()
        result = self.run_op(
            "set-frontmatter",
            '[[entry]]\ndocument = "part1/bare.md"\nkind = "leaf"\nclaims = ["clm-aa1111"]\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assert_terminators_survive(bare, before)


class TestAFirstBlockDoesNotDoubleTheBlankLine(_KbCase):
    """A first inserted block must not double the blank line around it.

    An unconditional separator on each side of the block would double the
    blank line in the ordinary leaf shape — an up-link, a blank line, then the
    body — harmless to every reader but still a body edit by an op that owns
    only the metadata.
    """

    def test_the_ordinary_leaf_shape_gains_exactly_one_blank_line(self):
        bare = self.kb_root / "part1/bare.md"
        bare.write_text(f"[Up](index.md)\n\n{_BODY}", encoding="utf-8")
        result = self.run_op(
            "set-frontmatter",
            '[[entry]]\ndocument = "part1/bare.md"\nkind = "leaf"\nclaims = ["clm-aa1111"]\n',
        )
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())

        lines = bare.read_text(encoding="utf-8").splitlines()
        closer = max(i for i, line in enumerate(lines) if line.strip() == "-->")
        self.assertEqual(lines[closer + 1], "")
        self.assertNotEqual(lines[closer + 2], "", "the block's separator was emitted twice")
        # The up-link's own separator is not doubled on the other side either.
        self.assertEqual(lines[0], "[Up](index.md)")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].startswith("<!--"), lines[:4])


# ---------------------------------------------------------------------------
# The proof temp is written through store's marked seam
# ---------------------------------------------------------------------------


class TestTheProofTempIsSweepable(_KbCase):
    """The proof temp must carry the sweep's mark.

    ``store.PROOF_MARK`` in the temp's name is what tells a sweep a temp
    beside the target belongs to a process that died holding it — an unmarked
    temp is permanent litter, and it is the mark rather than the sweep that
    this module owns.
    """

    def test_the_candidate_is_written_through_the_marked_seam(self):
        seen: list[str] = []
        real = store.proof_temp

        def recording(target, text):
            context = real(target, text)
            seen.append(target.name)
            return context

        with mock.patch.object(store, "proof_temp", recording):
            result = self.run_op("set-frontmatter", self.valid_values("set-frontmatter"))
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        self.assertEqual(seen, ["plain.md"])

    def test_the_temp_carries_the_proof_mark_and_does_not_survive(self):
        names: list[str] = []

        def prover(path: Path, kb_root: Path) -> list[str]:
            names.append(path.name)
            return ["forced"]

        with mock.patch.object(ops, "_marker_prover", lambda node_id: prover):
            result = self.run_op("mark-claim-in-leaf", self.valid_values("mark-claim-in-leaf"))

        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED, result.lines())
        self.assertEqual(len(names), 1, names)
        self.assertIn(store.PROOF_MARK, names[0])
        self.assertTrue(names[0].endswith(store.TEMP_SUFFIX))
        self.assertEqual(list((self.kb_root / "part1").glob(f"*{store.TEMP_SUFFIX}")), [])


# ---------------------------------------------------------------------------
# A batch the environment interrupts names both halves
# ---------------------------------------------------------------------------


class TestAnInterruptedBatchNamesWhatLanded(_KbCase):
    """An interrupted batch names the failed target, not the KB root, and
    reports what had already landed."""

    def test_the_failed_target_is_named_and_the_committed_ones_are_reported(self):
        (self.kb_root / "part2").mkdir()
        (self.kb_root / "part2/claim-quality.md").write_text("# Part 2 Claim Quality\n", encoding="utf-8")
        real = ops.store.apply_edits

        def interrupted(**kwargs):
            raise store.BatchInterrupted(
                written=("part1/claim-quality.md",),
                failed="part2/claim-quality.md",
                cause=OSError("Read-only file system"),
            )

        with mock.patch.object(store, "apply_edits", interrupted):
            result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))

        # Exit 2: every entry was proven before the first replace, so this is
        # the environment failing and not a verdict on the values.
        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())
        failures = [item for item in result.report if item.status == kb_util.FAIL]
        self.assertEqual([item.name for item in failures], ["part2/claim-quality.md"], result.lines())
        self.assertIn("restore:", failures[0].detail)
        # ...and the accounting for what already landed survives the trip
        # through ops.
        self.assertEqual(result.written, ("part1/claim-quality.md",))
        self.assertEqual(result.minted, tuple(result.minted))
        self.assertTrue(result.minted, "an id this op drew and wrote must still be reported")
        self.assertIn("part1/claim-quality.md", " ".join(result.lines()))
        del real


# ---------------------------------------------------------------------------
# The two questions, and the one the inventory cannot answer
# ---------------------------------------------------------------------------


def _plant_duplicate(kb_root: Path) -> None:
    """Key ``clm-aa1111`` from a second canonical register entry."""
    (kb_root / "part2").mkdir(exist_ok=True)
    (kb_root / "part2/claim-quality.md").write_text(
        _register(
            render.render_claim_entry(
                node_id="clm-aa1111", title="Anchor Claim, Restated", confidence=0.5, rationale="A second keyer."
            )
        ),
        encoding="utf-8",
    )


class TestDuplicateIsInvisible(_KbCase):
    """The duplicate is reported by one scan and missed by the other.

    Not argued from the code — asked of both functions over the same bytes.
    ``scan_authored_ids`` returns a map keyed by id and keeps the first keyer,
    so a collision reads as a clean single entry; that is why a second
    function exists and why it is asked **first**, before any reading is taken
    over the collapsed answer.
    """

    def test_the_inventory_reports_the_duplicated_id_as_one_clean_entry(self):
        _plant_duplicate(self.kb_root)
        inventory = kb_index_lib.scan_authored_ids(self.kb_root)
        self.assertIn("clm-aa1111", inventory)
        self.assertEqual(inventory["clm-aa1111"].register_path, self.rel)
        # One register named, one entry implied — and there are two on disk.
        self.assertEqual(len([r for r in inventory.values() if r.register_path == "part2/claim-quality.md"]), 0)

    def test_the_duplicate_scan_reports_both_registers(self):
        _plant_duplicate(self.kb_root)
        duplicates = kb_index_lib.scan_duplicate_register_ids(self.kb_root)
        self.assertEqual(duplicates["clm-aa1111"], (self.rel, "part2/claim-quality.md"))

    def test_every_op_refuses_while_the_collision_stands(self):
        # Asked first means asked by all of them: an update taken over a
        # damaged inventory is as wrong as a mint against one.
        _plant_duplicate(self.kb_root)
        for name in ops.OPS:
            with self.subTest(op=name):
                result = self.run_op(name, self.valid_values(name))
                self.assert_refused(result, "clm-aa1111")

    def test_the_refusal_precedes_the_inventory_read(self):
        # The order, asserted rather than assumed: if the inventory
        # were taken first the op would refuse for the same reason but from a
        # map already collapsed, which is the state the rule exists to avoid.
        _plant_duplicate(self.kb_root)
        with mock.patch.object(ops.kb_index_lib, "scan_authored_ids", side_effect=AssertionError("asked too early")):
            result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assert_refused(result, "clm-aa1111")


# ---------------------------------------------------------------------------
# Ids are born with their entries
# ---------------------------------------------------------------------------


class TestMintFusion(_KbCase):
    """The minted id is fresh against both scans, and the draw proves it."""

    def test_a_minted_id_is_absent_from_both_scans_before_and_present_after(self):
        before_inventory = kb_index_lib.scan_authored_ids(self.kb_root)
        before_duplicates = kb_index_lib.scan_duplicate_register_ids(self.kb_root)

        result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
        minted = result.minted[0]

        self.assertNotIn(minted, before_inventory)
        self.assertNotIn(minted, before_duplicates)

        after_inventory = kb_index_lib.scan_authored_ids(self.kb_root)
        after_duplicates = kb_index_lib.scan_duplicate_register_ids(self.kb_root)
        self.assertIn(minted, after_inventory)
        self.assertEqual(after_inventory[minted].register_path, self.rel)
        # Present once: an id that arrived as a duplicate would satisfy the
        # inventory clause above and still be the defect the mint exists to
        # avoid, so "present after" is asserted against both scans.
        self.assertNotIn(minted, after_duplicates)

    def test_the_draw_re_rolls_off_an_id_the_inventory_already_holds(self):
        # The collision check, with teeth: the first draw is forced onto
        # `clm-aa1111`, which the fixture register already keys, and the id
        # that reaches the file is the second one.
        chars = list("aa1111" + "zz9999")
        with mock.patch.object(kb_schema.secrets, "choice", lambda _alphabet: chars.pop(0)):
            result = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assertEqual(result.minted, ("clm-zz9999",))
        self.assertEqual(chars, [])
        self.assertIn("clm-zz9999", kb_index_lib.scan_authored_ids(self.kb_root))

    def test_the_exclusion_set_covers_the_whole_inventory(self):
        # Every kind, not only the one being minted: an id is unique across the
        # graph, so drawing a `clm-` body that a `sup-` already carries would
        # still be a collision.
        before = set(kb_index_lib.scan_authored_ids(self.kb_root))
        seen: dict[str, set[str]] = {}
        real = kb_schema.mint_id

        def spy(prefix, *, existing=()):
            seen["existing"] = set(existing)
            return real(prefix, existing=existing)

        with mock.patch.object(ops.kb_schema, "mint_id", spy):
            self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))
        self.assertLessEqual(before, seen["existing"])
        self.assertIn("sup-cc3333", seen["existing"])


class TestNoPathWritesAnUnmintedEntry(_KbCase):
    """Asserted structurally over the whole registry.

    An op's inputs are exhaustively enumerable — its own parameters, and the
    closed vocabulary of its row in ``values.OP_FIELDS`` — so "no reachable path
    writes an entry for an id the tool did not mint" is decidable rather than
    reviewable. These three tests decide it from three directions: the input
    channels admit no id, every record-creating edit carries only ids minted in
    that call, and removing the draw stops exactly the minting ops.
    """

    #: The values-module checkers that accept a node id. A minting op's
    #: vocabulary may not carry one at top level; nested id values (a
    #: `depends-on` target, a `strengthens` beneficiary) name *other* nodes and
    #: are a different question entirely.
    _ID_CHECKERS = (
        values.check_claim_id,
        values.check_support_id,
        values.check_experiment_id,
        values.check_entry_id,
        values.check_rationale_id,
    )

    def test_a_minting_ops_input_channels_admit_no_node_id(self):
        for name, op in ops.OPS.items():
            if op.mints is None:
                continue
            with self.subTest(op=name):
                parameters = set(inspect.signature(op.run).parameters)
                # The enumeration this proof rests on: these are all of them.
                self.assertLessEqual(parameters, {"kb_root", "values_file", "create"})
                vocabulary = values.OP_FIELDS[name]
                self.assertEqual([f.name for f in vocabulary if f.check in self._ID_CHECKERS], [])
                self.assertNotIn("id", {f.name for f in vocabulary})

    def test_only_a_minting_op_raises_a_record_count_and_only_with_fresh_ids(self):
        for name, op in ops.OPS.items():
            with self.subTest(op=name):
                self.setUp()
                inventory = set(kb_index_lib.scan_authored_ids(self.kb_root))
                captured: list[store.Edit] = []

                def spy(*, kb_root, edits):
                    captured.extend(edits)
                    return store.Outcome(status=store.Status.WRITTEN, written=tuple(e.path for e in edits))

                with mock.patch.object(ops.store, "apply_edits", spy):
                    result = self.run_op(name, self.valid_values(name))
                self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())

                fresh = set(result.minted)
                self.assertEqual(bool(fresh), op.mints is not None)
                for node_id in fresh:
                    self.assertTrue(node_id.startswith(f"{op.mints}-"))
                    self.assertNotIn(node_id, inventory)
                for edit in captured:
                    if edit.claim_delta + edit.support_delta > 0:
                        self.assertIsNotNone(op.mints, "a non-minting op raised a record count")
                        for expected in edit.expect:
                            self.assertIn(expected.node_id, fresh)
                    elif edit.work_delta > 0:
                        # The one insert that draws nothing. An external work's
                        # id is a function of its citation key, so there is no
                        # draw for the clause above to check; what stands in its
                        # place is that the entry is new, which is the property
                        # the draw was protecting — one node per work.
                        self.assertIsNone(op.mints)
                        for expected in edit.expect:
                            self.assertNotIn(expected.node_id, inventory)
                    else:
                        for expected in edit.expect:
                            self.assertIn(expected.node_id, inventory)

    def test_removing_the_draw_stops_every_minting_op_and_no_other(self):
        for name, op in ops.OPS.items():
            with self.subTest(op=name):
                self.setUp()
                with mock.patch.object(ops.kb_schema, "mint_id", side_effect=AssertionError("no draw")):
                    if op.mints is None:
                        result = self.run_op(name, self.valid_values(name))
                        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())
                    else:
                        with self.assertRaises(AssertionError):
                            self.run_op(name, self.valid_values(name))

    def test_the_registry_declares_creation_on_the_ops_that_take_it(self):
        # Unspellable elsewhere: a surface reads this rather
        # than deciding for itself which ops admit `--create`.
        creating = {name for name, op in ops.OPS.items() if op.creates_register}
        self.assertEqual(creating, {"insert-claim-entry", "insert-support-entry", "insert-work-entry"})
        for name, op in ops.OPS.items():
            with self.subTest(op=name):
                takes_create = "create" in inspect.signature(op.run).parameters
                self.assertEqual(takes_create, op.creates_register)


# ---------------------------------------------------------------------------
# Outcomes and codes
# ---------------------------------------------------------------------------


class TestOutcomesMapToCodes(_KbCase):
    """Three answers, three codes, and the one that must not be re-authored."""

    def test_the_mapping_is_total_over_the_stores_outcomes(self):
        self.assertEqual(set(ops.EXIT_FOR_STATUS), set(store.Status))
        self.assertEqual(len(set(ops.EXIT_FOR_STATUS.values())), len(store.Status))
        self.assertEqual(ops.EXIT_FOR_STATUS[store.Status.REFUSED], 7)
        self.assertEqual(ops.EXIT_FOR_STATUS[store.Status.RETRY], 8)

    def test_a_contended_file_exits_8_and_writes_nothing(self):
        # The forced interleaving, driven through a real op: a second writer
        # completes inside the first's splice, which is the seam between the
        # first writer's read and its replace. Retry, not refused — the values
        # were correct, and re-asking a model for values that were already
        # right is how a duplicate id gets written.
        second: list[ops.Result] = []
        real = ops._insert_all

        def hooked(entries):
            splice = real(entries)

            def wrapped(text: str) -> str:
                candidate = splice(text)
                if not second:
                    with mock.patch.object(ops, "_insert_all", real):
                        second.append(
                            ops.insert_claim_entry(
                                kb_root=self.kb_root,
                                values_file=self.values_file(
                                    """
                                    [[entry]]
                                    register = "part1/claim-quality.md"
                                    title = "Writer B Claim"
                                    rigor = 0.5
                                    rationale = "The interleaved writer."
                                    """,
                                    name="values-b.toml",
                                ),
                            )
                        )
                return candidate

            return wrapped

        with mock.patch.object(ops, "_insert_all", hooked):
            first = self.run_op("insert-claim-entry", self.valid_values("insert-claim-entry"))

        self.assertEqual(second[0].exit_code, ops.ExitCode.WRITTEN, second[0].lines())
        self.assertEqual(first.exit_code, ops.ExitCode.RETRY, first.lines())
        self.assertEqual(first.written, ())
        self.assertEqual(first.minted, ())
        failure = next(item for item in first.report if item.status == kb_util.FAIL)
        self.assertEqual(failure.name, self.rel)
        self.assertIn("restore:", failure.detail)
        # Exactly one new entry landed, and the register still censuses clean.
        ids = [e.id for e in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)]
        self.assertEqual(ids[:2], ["clm-aa1111", "clm-bb2222"])
        self.assertEqual(len(ids), 4)
        self.assertTrue(store.take_census(self.register, self.kb_root).consistent)


# ---------------------------------------------------------------------------
# Surface independence, asserted structurally
# ---------------------------------------------------------------------------


def _module_ast() -> ast.Module:
    return ast.parse(Path(ops.__file__).read_text(encoding="utf-8"))


def _emitted_strings(tree: ast.Module) -> list[str]:
    """Every string literal in the module that is not a docstring.

    The scan is over the parse rather than over the text because the module's
    own prose *describes* the machinery it excludes — a text grep would fail on
    the sentence saying the token is absent, which is a guard that reports its
    own documentation. Comments fall out for free: they are not in the tree.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


class TestOpsAreSurfaceIndependent(unittest.TestCase):
    """No argv, no argparse, no exit call, no stage id anywhere in the module.

    A negative constraint with no instrument is a wish. These read
    ``ops.py``'s own parse: the import set, the call set, and the string
    literals it emits.
    """

    def test_the_module_imports_no_surface_or_process_machinery(self):
        tree = _module_ast()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("argparse", "sys", "subprocess"):
            self.assertNotIn(forbidden, imported)
        # `sys` is not imported, so `sys.exit` is unspellable; the bare
        # builtins are the other way to leave a process, and they are absent.
        calls = {
            node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(calls & {"exit", "quit"}, set())

    def test_the_module_emits_no_stage_id_and_no_record_verb(self):
        # The same prohibition `kb_survey` carries, and the same one the
        # brief lint enforces on templates.
        emitted = " ".join(_emitted_strings(_module_ast()))
        for token in ("advance-step", "start-build", "phase-0", "phase-1", "phase-2", "phase-3", "phase-4"):
            self.assertNotIn(token, emitted, f"{token} reached the op-semantics module's output")

    def test_no_op_hands_a_caller_a_literal_kb_util_command_line(self):
        # Every sanctioned invocation renders from one constant.
        # A `restore:` clause here names the op and the action, so an
        # invocation-shape change is one code change rather than nine report
        # strings.
        emitted = " ".join(_emitted_strings(_module_ast()))
        for token in ("python3 -m", "kb_tools.kb_util", "PYTHONPATH="):
            self.assertNotIn(token, emitted)

    def test_every_op_of_the_surface_has_a_closed_values_vocabulary(self):
        # The two tables are the surface an agent writes against and the
        # surface this package checks; a row in one and not the other is drift
        # between them.
        self.assertLessEqual(set(ops.OPS), set(values.OP_FIELDS))
        # `render-citation` is the only vocabulary row with no op here: it is
        # read-only.
        self.assertLessEqual(set(ops.READ_OPS), set(values.OP_FIELDS))
        # The two registries partition the vocabulary: a values row with no op
        # is a surface an agent can write against and nothing can run, and an
        # op with no row is an op with no checked inputs.
        self.assertEqual(set(ops.OPS) | set(ops.READ_OPS), set(values.OP_FIELDS))
        self.assertEqual(set(ops.OPS) & set(ops.READ_OPS), set())

    def test_the_read_registry_holds_no_writing_or_minting_op(self):
        # `READ_OPS` is what a surface binds when it wants an op that reads.
        # The partition walks `OPS`; this is the clause that keeps a future
        # member of the sibling registry from quietly acquiring a write.
        for name, op in ops.READ_OPS.items():
            with self.subTest(op=name):
                self.assertIsNone(op.mints)
                self.assertFalse(op.creates_register)


# ---------------------------------------------------------------------------
# The read-only op
#
# Its refusals are the citation gate's own checks, asked at authoring time, so
# each fixture below is paired with the gate's verdict on the same corpus
# wherever the two could drift: an op that thought a fenced heading was a
# section, or that a fenced clause was quotable, would print citations the gate
# rejects. Behavioural agreement is asserted rather than call sequence — what
# matters is that the two answer the same question the same way, not how the
# answer is routed.
#
# The end-to-end half — a printed string passing the
# gate's own CLI unmodified — lives in `test_kb_util.py`, where the shipped
# entry point is.
# ---------------------------------------------------------------------------

_CITED_DOC = """\
<!-- kb-frontmatter
kind: index
-->

# Part 1 Claim Quality

## Second Claim

The load-bearing clause lives here, stated plainly.

Only inside a fence, and so quotable from nowhere:

```
the moon is made of cheese
```

```markdown
## Fake Section

invented authority text
```

## Twice Over

The repeated clause appears here. And the repeated clause appears here.
"""


class TestRenderCitation(unittest.TestCase):
    """The read-only row: verify against the KB, print the form, touch nothing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self.kb_root = self.work / "kb-root"
        (self.kb_root / "part1" / "sub").mkdir(parents=True)
        (self.kb_root / "part1" / "claim-quality.md").write_text(_CITED_DOC, encoding="utf-8")
        (self.kb_root / "index.md").write_text(_document(render.FrontmatterValues(kind="index")), encoding="utf-8")

    # -- harness ----------------------------------------------------------

    def run_op(self, **overrides: str) -> ops.Result:
        supplied = {
            "excerpt": "The load-bearing clause lives here",
            "cited-document": "part1/claim-quality.md",
            "anchor": "second-claim",
            "citing-document": "part1/index.md",
            **overrides,
        }
        body = "[[entry]]\n" + "".join(f'{key} = """{value}"""\n' for key, value in supplied.items())
        path = self.work / "values.toml"
        path.write_text(body, encoding="utf-8")
        return ops.READ_OPS["render-citation"].run(kb_root=self.kb_root, values_file=path)

    def snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in sorted(self.kb_root.rglob("*")) if path.is_file()}

    def assert_refused(self, result: ops.Result, name: str) -> None:
        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED, result.lines())
        self.assertEqual(result.printed, ())
        failures = [item for item in result.report if item.status == kb_util.FAIL]
        self.assertEqual([item.name for item in failures], [name], result.lines())
        self.assertIn("restore:", failures[0].detail)

    # -- the printing half -------------------------------------------------

    def test_a_matching_excerpt_prints_the_citation_and_writes_nothing(self):
        before = self.snapshot()

        result = self.run_op()

        self.assertEqual(result.exit_code, ops.ExitCode.PRINTED, result.lines())
        self.assertEqual(result.printed, ('["The load-bearing clause lives here"](claim-quality.md#second-claim)',))
        self.assertEqual(result.written, ())
        self.assertEqual(result.minted, ())
        self.assertEqual(self.snapshot(), before)

    def test_a_cited_document_that_will_not_decode_is_a_located_refusal(self):
        """The last unguarded read on this op's path.

        **Exit 7, where the store walk's undecodable file is exit 2**, and the
        difference is who chose the file: the walk reaches a file no value names,
        and this one the caller named in ``cited-document``. Citing a document
        that will not decode is a value to correct.
        """
        (self.kb_root / "part1" / "claim-quality.md").write_bytes(
            _CITED_DOC.replace("clause lives here", "clause lives h\xe8re").encode("latin-1")
        )
        before = self.snapshot()

        result = self.run_op()

        self.assert_refused(result, "cited-document")
        self.assertEqual(self.snapshot(), before)

    def test_a_cited_document_that_cannot_be_opened_is_the_environment_named(self):
        """The same read's other failure, and it is exit 2 rather than 7: the
        values named a document that exists, so there is nothing to correct.
        Named specifically, as ``cited-document``, rather than the whole KB
        root."""
        cited = self.kb_root / "part1" / "claim-quality.md"
        real = Path.read_text

        def refuse(path, *args, **kwargs):
            if path == cited:
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", refuse):
            result = self.run_op()

        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())
        failures = [item for item in result.report if item.status == kb_util.FAIL]
        self.assertEqual([item.name for item in failures], ["cited-document"], result.lines())
        self.assertIn("restore:", failures[0].detail)

    def test_the_printed_string_is_the_renderers_own_composition(self):
        # The form is `render.py`'s and is composed nowhere else: this op
        # verifies and prints, it does not spell a citation.
        result = self.run_op()

        self.assertEqual(
            result.printed[0],
            render.render_citation(
                excerpt="The load-bearing clause lives here", kb_path="claim-quality.md", anchor="second-claim"
            ),
        )

    def test_the_target_is_spelled_relative_to_the_citing_document(self):
        """The whole reason the citing document is a value.

        Every reader of a citation — the gate and the link checker alike —
        resolves its target against the file the link is written in, so the one
        correct spelling depends on where the citation lands. A kb-root
        -relative spelling would be right only for a citation written at the
        top of the tree.
        """
        for citing, target in (
            ("part1/index.md", "claim-quality.md"),
            ("index.md", "part1/claim-quality.md"),
            ("part1/sub/index.md", "../claim-quality.md"),
        ):
            with self.subTest(citing=citing):
                result = self.run_op(**{"citing-document": citing})
                self.assertEqual(result.exit_code, ops.ExitCode.PRINTED, result.lines())
                self.assertIn(f"]({target}#second-claim)", result.printed[0])

    def test_the_excerpt_is_matched_with_whitespace_collapsed(self):
        result = self.run_op(excerpt="The load-bearing\n   clause lives here")

        self.assertEqual(result.exit_code, ops.ExitCode.PRINTED, result.lines())
        self.assertEqual(result.printed, ('["The load-bearing clause lives here"](claim-quality.md#second-claim)',))

    def test_an_excerpt_occurring_twice_is_printed_rather_than_called_ambiguous(self):
        """Mirroring the gate's rules means adding none of our own.

        ``verify_citations`` asks whether the excerpt appears in the cited
        section and asks nothing further, so a clause occurring twice there is
        a citation it passes. Refusing it here would refuse a citation the
        corpus accepts — and `mark-claim-in-leaf`'s ambiguity refusal answers a
        different question, where to PUT a marker, which has one answer or none.
        """
        result = self.run_op(excerpt="The repeated clause appears here", anchor="twice-over")

        self.assertEqual(result.exit_code, ops.ExitCode.PRINTED, result.lines())
        self.assertIn("#twice-over)", result.printed[0])
        self.assertEqual(
            verify_citations._normalize("The repeated clause appears here")
            in verify_citations._normalize(kb_index_lib.anchor_section(_CITED_DOC, "twice-over")),
            True,
        )

    def test_a_batch_prints_one_line_per_entry_in_order(self):
        path = self.work / "batch.toml"
        path.write_text(
            "[[entry]]\n"
            'excerpt = "The load-bearing clause lives here"\n'
            'cited-document = "part1/claim-quality.md"\n'
            'anchor = "second-claim"\n'
            'citing-document = "part1/index.md"\n'
            "\n[[entry]]\n"
            'excerpt = "The repeated clause appears here"\n'
            'cited-document = "part1/claim-quality.md"\n'
            'anchor = "twice-over"\n'
            'citing-document = "index.md"\n',
            encoding="utf-8",
        )

        result = ops.READ_OPS["render-citation"].run(kb_root=self.kb_root, values_file=path)

        self.assertEqual(result.exit_code, ops.ExitCode.PRINTED, result.lines())
        self.assertEqual(
            result.printed,
            (
                '["The load-bearing clause lives here"](claim-quality.md#second-claim)',
                '["The repeated clause appears here"](part1/claim-quality.md#twice-over)',
            ),
        )

    # -- the refusing half -------------------------------------------------

    def test_a_non_matching_excerpt_refuses_and_names_the_nearest_text(self):
        before = self.snapshot()

        result = self.run_op(excerpt="The load-bearing clause was never written")

        self.assert_refused(result, "excerpt")
        self.assertIn("The load-bearing clause lives here", result.report[0].detail)
        self.assertEqual(self.snapshot(), before)

    def test_an_excerpt_with_no_near_miss_refuses_stating_its_absence(self):
        result = self.run_op(excerpt="Nothing whatsoever resembling the section")

        self.assert_refused(result, "excerpt")
        self.assertNotIn("nearest", result.report[0].detail)

    def test_an_over_long_excerpt_refuses_at_the_inherited_bound(self):
        # The bound is `verify_citations`' own, checked in `values.py` against
        # its contract; the op authors none of its own.
        result = self.run_op(excerpt="y" * (verify_citations.EXCERPT_MAX_CHARS + 1))

        self.assertEqual(result.exit_code, ops.ExitCode.REFUSED, result.lines())
        self.assertEqual(result.printed, ())
        self.assertEqual([item.name for item in result.report], ["entry[1].excerpt"], result.lines())

    def test_an_anchor_naming_no_section_refuses(self):
        result = self.run_op(anchor="no-such-section")

        self.assert_refused(result, "anchor")

    def test_a_heading_that_exists_only_in_a_fence_is_not_a_section(self):
        """The gate reads the cited document fence-stripped, and so does this op.

        The pairing is the point: the op refuses the anchor, and a citation
        naming it fails the gate. Two readings of "what is a section" would
        make this op print citations the corpus then rejects.
        """
        result = self.run_op(anchor="fake-section", excerpt="invented authority text")

        self.assert_refused(result, "anchor")
        self.assertIsNone(kb_index_lib.anchor_section(_CITED_DOC, "fake-section"))

    def test_a_clause_that_exists_only_in_a_fence_is_not_quotable(self):
        # The discriminator: the clause IS in the cited document's bytes, and
        # in the cited section's line range. Only the fence stripping can be
        # what refuses it, so this cannot be passing as an ordinary non-match.
        self.assertIn("the moon is made of cheese", _CITED_DOC)

        result = self.run_op(excerpt="the moon is made of cheese")

        self.assert_refused(result, "excerpt")

    def test_a_cited_document_that_does_not_exist_refuses(self):
        result = self.run_op(**{"cited-document": "part1/nowhere.md"})

        self.assert_refused(result, "cited-document")

    def test_a_cited_document_outside_the_kb_refuses_as_a_non_durable_target(self):
        outside = self.work / "notes.md"
        outside.write_text("# Notes\n\n## Second Claim\n\nThe load-bearing clause lives here.\n", encoding="utf-8")

        result = self.run_op(**{"cited-document": "../notes.md"})

        self.assert_refused(result, "cited-document")

    def test_a_citing_document_in_a_directory_that_does_not_exist_refuses(self):
        # The file itself may be unwritten — the ordinary call composes a
        # citation for prose being written now — but a directory typo would
        # silently change the target's spelling.
        result = self.run_op(**{"citing-document": "part9/index.md"})

        self.assert_refused(result, "citing-document")

    def test_a_citing_document_outside_the_kb_refuses(self):
        result = self.run_op(**{"citing-document": "../elsewhere/index.md"})

        self.assert_refused(result, "citing-document")

    def test_an_excerpt_whose_brackets_hide_the_citation_from_the_gate_refuses(self):
        """A citation no gate can *see* is worse than one a gate rejects.

        ``kb_links.LINK_RE`` is the primitive every link-reading gate shares,
        and an unbalanced bracket in the link text is enough to make the whole
        citation invisible to it — reading as verified authority while nothing
        ever checked it.
        """
        cited = self.kb_root / "part1" / "claim-quality.md"
        cited.write_text(_CITED_DOC.replace("stated plainly.", "stated plainly] here."), encoding="utf-8")

        result = self.run_op(excerpt="lives here, stated plainly] here")

        self.assert_refused(result, "excerpt")
        # The discriminator, since an excerpt refusal has one name and two
        # causes: the same corpus and a bracket-free quotation of the same
        # sentence prints, so what was refused above is the composed link's
        # invisibility and not a failed match.
        self.assertEqual(self.run_op(excerpt="lives here, stated plainly").exit_code, ops.ExitCode.PRINTED)

    def test_an_unresolvable_kb_root_is_an_environment_failure(self):
        path = self.work / "values.toml"
        path.write_text("[[entry]]\n", encoding="utf-8")

        result = ops.READ_OPS["render-citation"].run(kb_root=self.work / "nope", values_file=path)

        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())

    def test_a_missing_values_file_is_an_environment_failure(self):
        result = ops.READ_OPS["render-citation"].run(kb_root=self.kb_root, values_file=self.work / "absent.toml")

        self.assertEqual(result.exit_code, ops.ExitCode.ENVIRONMENT, result.lines())


class TestTheGatesNormalizerIsTheRenderersOwn(unittest.TestCase):
    """The excerpt comparison this op makes is the comparison the gate makes.

    The section finder is shared by construction — one object,
    ``kb_index_lib.anchor_section``, called by both — and the normalizer is
    pinned here instead, over inputs that separate the two if they ever drift:
    a value that normalized differently would let the op accept a quotation the
    gate then rejects, which is the whole failure this op exists to prevent.
    """

    def test_the_two_normalizers_agree(self):
        for text in (
            "plain text",
            "  leading and trailing  ",
            "internal   runs\tof\twhitespace",
            "a line\nand another",
            "an em — dash and “curled quotes”",
            "",
            "\n\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(verify_citations._normalize(text), render.collapse_prose(text))


# ---------------------------------------------------------------------------
# A realistic corpus
# ---------------------------------------------------------------------------


class TestAgainstTheMiniKb(unittest.TestCase):
    """One insert against a copy of the shipped mini-KB fixture.

    The fixture is read-only material: it is copied into the test's own
    tmpdir and the copy is written, never the tree under ``fixtures/``. Its
    value here is that it is a corpus this program did not compose — hand-built
    registers, framework dependencies, co-hosted supports — so an op that only
    worked against its own renderer's output would fail.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = Path(self._tmp.name).resolve()
        self.kb_root = self.work / "kb-root"
        shutil.copytree(_MINI_KB, self.kb_root)

    def test_an_insert_into_the_hand_built_register_writes_and_censuses_clean(self):
        values_file = self.work / "values.toml"
        values_file.write_text(
            """
            [[entry]]
            register = "common/claim-quality.md"
            title = "A Claim Inserted By The Write API"
            rigor = 0.65
            rationale = "Inserted into a register this program did not compose."
              [[entry.depends-on]]
              id = "clm-aa1111"
              context = "the anchor claim"
              [[entry.depends-on]]
              id = "INVARIANT-S2"
            """,
            encoding="utf-8",
        )
        result = ops.insert_claim_entry(kb_root=self.kb_root, values_file=values_file)
        self.assertEqual(result.exit_code, ops.ExitCode.WRITTEN, result.lines())

        register = self.kb_root / "common/claim-quality.md"
        self.assertTrue(store.take_census(register, self.kb_root).consistent)
        record = next(
            e for e in kb_index_lib.parse_claim_quality_file(register, self.kb_root) if e.id == result.minted[0]
        )
        self.assertEqual([edge.target for edge in record.depends_on], ["clm-aa1111", "INVARIANT-S2"])
        # The title on the bullet is read off the referent's own entry, not
        # retyped by the caller.
        self.assertIn("clm-aa1111 — Foundation Claim A — No Dependencies", register.read_text(encoding="utf-8"))
        self.assertEqual(kb_index_lib.scan_duplicate_register_ids(self.kb_root), {})


if __name__ == "__main__":
    unittest.main()
