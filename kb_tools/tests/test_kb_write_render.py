"""The render module's contract, as tests.

Four questions.

**Is every rendered shape byte-stable, and is it the shape we meant?** One
committed golden per shape under ``fixtures/writeapi-render-golden/``, produced
by the renderers in :data:`_GOLDENS` and byte-compared. A golden is the only
instrument that catches a change nobody meant to make: every other test here
asserts a property this seat chose to assert, and a regression outside that set
is invisible to all of them. Each shape is additionally rendered twice and the
two runs compared, because a golden that matched a nondeterministic renderer
once would prove nothing about the next run.

**Is the renderer actually pure?** ``render.py``'s import set is read out of its
AST and checked against a closed allowlist. The negative constraint — reads no
files, knows no paths — is load-bearing (it is what keeps one module the only
composer of metadata bytes), and a negative constraint with no instrument is a
wish.

**Are the derived-field placeholders the ones refresh recognizes?** The
requirement is that an inserted entry carry the *identity* value of each derived
field, so the first refresh over it either fills the slot or leaves it
byte-identical. All three constants live in ``kb_schema``, which
``render`` may import and every other writer already does, so the question is
now answered by **object identity** — asserted with ``assertIs`` below, against
``kb_schema`` on this side and against what the production writers return on the
other. What identity cannot cover is asserted against refresh's own *matchers*,
which are separate objects that could still drift from what they match — the
solidity line against the pattern that *replaces* it, the annotation against
the pattern that *substitutes* it.

**Does the prose collapse defeat the truncation class it exists to defeat?**
A rationale whose text contains a line beginning ``- confidence:`` is
representable: collapsed to one physical line, the reader's key list
(``kb_index_lib.py:985``) no longer matches at a line start. The fixture asserts
the stored text equals the collapsed supplied text through the real parser.

The blank-line class is **not** asserted here. It is refused by ``values.py``,
and its fixture lives in ``test_kb_write_values.py``; this module's renderers
may assume single-paragraph input.
"""

import ast
import tempfile
import unittest
from pathlib import Path

from kb_tools import kb_index_lib, kb_schema, refresh_kb_metadata
from kb_tools.kb_write import render

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "writeapi-render-golden"

# The import allowlist: `render` ->
# `kb_schema` only, plus the stdlib it composes text with. Anything reaching a
# path, a file, or a parser is the boundary being crossed. Names are recorded
# qualified — `from kb_tools import kb_schema` as "kb_tools.kb_schema" — so the
# allowlist pins which member of a package is imported, not merely which
# package: `from kb_tools import kb_index_lib` fails here.
_ALLOWED_IMPORTS = frozenset({"re", "dataclasses.dataclass", "kb_tools.kb_schema"})

# Named so a failure says which boundary was crossed rather than only that one
# was. Not exhaustive by construction — the allowlist above is what gates.
_FILESYSTEM_MODULES = frozenset(
    {"pathlib", "os", "os.path", "io", "shutil", "tempfile", "glob", "fnmatch", "sys", "subprocess"}
)


# ---------------------------------------------------------------------------
# The specimen table — one entry per rendered shape
# ---------------------------------------------------------------------------


def _claim_entry_full() -> str:
    """Every optional field, and every depends-on bullet shape."""
    return render.render_claim_entry(
        node_id="clm-aa1111",
        title="Regime Conservation Laws",
        confidence=0.7,
        rationale=(
            "CES asymptotics applied with an explicit exponent formula and a cited "
            "methodology; scope marked as leading-order with error-term bounds."
        ),
        depends_on=(
            render.DependsOnTarget(
                target="clm-bb2222",
                title="Foundation Claim B",
                context="builds directly on the anchor claim",
            ),
            render.DependsOnTarget(target="clm-cc3333", title="Single-Dependency Claim C"),
            render.DependsOnTarget(target="INVARIANT-S2", context="labelling convention for the d-axis treatment"),
            render.DependsOnTarget(target="Axiom 4"),
        ),
        strengthen_by=(
            "Run an independent derivation to confirm the anchor value.",
            "Discharge the open step so the dependency on clm-bb2222 can be closed.",
        ),
        no_edge="the foreign-domain reference is illustrative, not load-bearing",
    )


def _claim_entry_minimal() -> str:
    """No optional field at all: a pending, dependency-free, unstrengthened entry."""
    return render.render_claim_entry(
        node_id="clm-dd4444",
        title="Unassessed Foundation Result",
        confidence=None,
        rationale="*pending*",
    )


def _support_entry() -> str:
    """A ``sup-`` entry: ``quality:`` in place of ``confidence:``, no strengthen-by."""
    return render.render_support_entry(
        node_id="sup-ee5555",
        title="Free-Standing Analytical Support",
        quality=0.9,
        rationale="Non-physical analytical support; lifts a claim without gating it.",
        depends_on=(render.DependsOnTarget(target="clm-aa1111", title="Regime Conservation Laws"),),
    )


def _support_entry_staged() -> str:
    """A ``sup-`` entry staging its beneficiary fan-out in the register.

    The pre-leaf home: the id is minted a stage before the document that will
    host it exists, so the pairs live here until then. It carries every field
    that could interfere with the block — a ``no-edge:`` line, a dependency
    bullet, a rationale — because the question is not whether the pairs render
    but whether they and the entry's own fields survive one another.
    """
    return render.render_support_entry(
        node_id="sup-ee5555",
        title="Staged Analytical Support",
        quality=0.9,
        rationale="Derives the beneficiary result in full; staged before its hosting leaf exists.",
        depends_on=(render.DependsOnTarget(target="clm-aa1111", title="Regime Conservation Laws"),),
        supports=(("clm-bb2222", 0.5), ("clm-cc3333", None)),
        no_edge="the foreign-domain reference is illustrative, not load-bearing",
    )


def _frontmatter_path_stable() -> str:
    """The ``path-stable:`` document attribute, double-quoted like ``no-claim``."""
    return render.render_frontmatter_block(
        render.FrontmatterValues(
            kind="leaf",
            path_stable="regime conservation — stable reference label",
            claims=("clm-aa1111",),
        )
    )


def _frontmatter_claims() -> str:
    """The primary ``claims:`` shape — the canonical inline list — plus the
    additive ``experiments:`` reference field a non-owning leaf may carry."""
    return render.render_frontmatter_block(
        render.FrontmatterValues(
            kind="leaf",
            claims=("clm-aa1111", "clm-bb2222", "clm-cc3333"),
            experiments=("exp-ff6666",),
        )
    )


def _frontmatter_no_claim() -> str:
    """The ``no-claim:`` branch of the primary field, double-quoted."""
    return render.render_frontmatter_block(
        render.FrontmatterValues(kind="leaf", no_claim="navigation-only leaf — carries no claim-quality entries")
    )


def _frontmatter_hosts() -> str:
    """A container hosting an experiment and two supports.

    No ``experiments:`` reference list: an owning experiment leaf must not also
    reference foreign experiments, which is the one exclusivity that survives
    between these fields.
    """
    return render.render_frontmatter_block(
        render.FrontmatterValues(
            kind="leaf-as-index",
            no_claim="hosts an experiment and two support nodes only",
            experiment_nodes=(
                render.ExperimentDecl(exp_id="exp-gg7777", status="run", strengthens=(("clm-aa1111", 0.8),)),
            ),
            support_nodes=(
                render.SupportDecl(sup_id="sup-hh8888", supports=(("clm-bb2222", 1.0),)),
                render.SupportDecl(sup_id="sup-ii9999", supports=(("clm-bb2222", None), ("clm-cc3333", 0.5))),
            ),
        )
    )


def _markers() -> str:
    """The two comment markers: a register entry's id, and a leaf-body Tier-2."""
    return "\n".join(
        [
            render.render_id_marker("clm-aa1111"),
            render.render_id_marker("sup-ee5555"),
            render.render_tier2_marker("clm-aa1111"),
        ]
    )


def _depends_on_bullets() -> str:
    """Each depends-on bullet shape on its own line, claim and framework."""
    return "\n".join(
        render.render_depends_on_bullet(target)
        for target in (
            render.DependsOnTarget(target="clm-bb2222", title="Foundation Claim B", context="the anchor dependency"),
            render.DependsOnTarget(target="clm-bb2222", title="Foundation Claim B"),
            render.DependsOnTarget(target="clm-bb2222"),
            render.DependsOnTarget(target="INVARIANT-S2", context="labelling convention"),
            render.DependsOnTarget(target="Axiom 4"),
        )
    )


def _no_edge() -> str:
    """The entry-level foreign-domain exemption line."""
    return render.render_no_edge_line("cited for context only; the derivation takes nothing from that domain")


def _citation() -> str:
    """The sanctioned authority-citation string form."""
    return render.render_citation(
        excerpt="the weakest link in the dependency cone",
        kb_path="part3/claim-quality.md",
        anchor="regime-conservation-laws",
    )


#: name -> zero-argument renderer. The name is the golden's filename stem.
_GOLDENS = {
    "claim-entry-full": _claim_entry_full,
    "claim-entry-minimal": _claim_entry_minimal,
    "support-entry": _support_entry,
    "support-entry-staged": _support_entry_staged,
    "frontmatter-claims": _frontmatter_claims,
    "frontmatter-no-claim": _frontmatter_no_claim,
    "frontmatter-path-stable": _frontmatter_path_stable,
    "frontmatter-hosts": _frontmatter_hosts,
    "markers": _markers,
    "depends-on-bullets": _depends_on_bullets,
    "no-edge": _no_edge,
    "citation": _citation,
}


# ---------------------------------------------------------------------------
# Golden renders
# ---------------------------------------------------------------------------


class TestGoldenRenders(unittest.TestCase):
    """Every rendered shape matches its committed specimen, and is stable."""

    maxDiff = None

    def test_every_shape_has_a_committed_golden(self):
        on_disk = {p.stem for p in _FIXTURES.glob("*.md")} - {"README"}
        self.assertEqual(on_disk, set(_GOLDENS), "the golden set and the specimen table disagree")

    def test_golden_byte_compare(self):
        for name, produce in _GOLDENS.items():
            with self.subTest(shape=name):
                golden = (_FIXTURES / f"{name}.md").read_text(encoding="utf-8")
                # The renderers return text with no trailing newline; the golden
                # file carries the one a text file ends with.
                self.assertMultiLineEqual(golden, produce() + "\n")

    def test_byte_stable_across_two_runs(self):
        for name, produce in _GOLDENS.items():
            with self.subTest(shape=name):
                self.assertEqual(produce(), produce())


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------


class TestRendererIsPure(unittest.TestCase):
    """``render.py`` imports no path or filesystem module."""

    @staticmethod
    def _imported_modules() -> set[str]:
        source = Path(render.__file__).read_text(encoding="utf-8")
        names: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                # `from kb_tools import kb_schema` -> "kb_tools.kb_schema";
                # `from x import y` where y is a name, not a module, is still
                # recorded under x.y, which the allowlist accounts for.
                module = node.module or ""
                names.update(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
        return names

    def test_import_set_is_the_allowlist(self):
        self.assertEqual(self._imported_modules(), set(_ALLOWED_IMPORTS))

    def test_no_filesystem_module_is_imported(self):
        crossed = self._imported_modules() & _FILESYSTEM_MODULES
        self.assertEqual(crossed, set(), f"render.py imports a filesystem module: {sorted(crossed)}")

    def test_module_has_no_open_or_path_reference(self):
        source = Path(render.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("open", called)


# ---------------------------------------------------------------------------
# Derived-field placeholders
# ---------------------------------------------------------------------------


class TestDerivedPlaceholdersAreRefreshs(unittest.TestCase):
    """The placeholders render.py writes are the ones refresh reads and replaces.

    ``kb_write`` may not import ``refresh_kb_metadata``, so these can only be
    pinned behaviorally by keeping the constants themselves identical. All
    three now live in ``kb_schema`` — which ``render`` may import and every
    other writer already does — so the pins below are ``assertIs``. Equality
    would pass on two literals someone kept in sync, which is what the ordinary
    drift begins as; identity cannot.

    ``PENDING_LITERAL`` is the one case where identity would be untrustworthy on
    its own — ``"*pending*"`` is short enough for CPython to intern, so two
    independent literals *would* compare identical — and it is asserted against
    ``kb_schema`` as the definition site rather than against a second literal, so
    the assertion means what it says.

    The matcher assertions stay: refresh's ``SOLIDITY_LINE`` and
    ``SOLIDITY_ANNOTATION`` patterns are separate objects, and identity of the
    constants says nothing about whether the pattern still matches them.
    """

    def test_pending_literal_is_the_schema_object(self):
        self.assertIs(render.PENDING_LITERAL, kb_schema.PENDING_LITERAL)
        self.assertIs(kb_index_lib.PENDING_LITERAL, kb_schema.PENDING_LITERAL)

    def test_solidity_line_is_the_schema_object(self):
        self.assertIs(render.SOLIDITY_PENDING_LINE, kb_schema.SOLIDITY_PENDING_LINE)

    def test_solidity_annotation_is_the_schema_object(self):
        self.assertIs(render.SOLIDITY_ANNOTATION_PENDING, kb_schema.SOLIDITY_ANNOTATION_PENDING)

    def test_refresh_writes_the_same_pending_solidity_line(self):
        # Refresh reaches the placeholder through its own line builder, so the
        # pin is taken on what that builder returns rather than on a constant
        # refresh no longer keeps. `None` is the no-computable-solidity case,
        # and the trace is deliberately non-empty: a pending line carries none.
        self.assertIs(
            refresh_kb_metadata._solidity_line(None, " [= min(0.90, 0.40)]"),
            kb_schema.SOLIDITY_PENDING_LINE,
        )

    def test_solidity_line_is_found_by_the_line_refresh_replaces(self):
        # Refresh REPLACES a `- solidity:` line and never inserts one, so an
        # entry whose slot its matcher misses would never receive a value.
        self.assertIsNotNone(refresh_kb_metadata.SOLIDITY_LINE.match(render.SOLIDITY_PENDING_LINE))

    def test_solidity_annotation_is_substituted_by_refresh(self):
        # Same argument one level down: refresh substitutes INTO an existing
        # `(solidity …)` group and never adds one.
        self.assertIsNotNone(
            refresh_kb_metadata.SOLIDITY_ANNOTATION.fullmatch(render.SOLIDITY_ANNOTATION_PENDING),
        )

    def test_solidity_annotation_survives_a_refresh_substitution(self):
        bullet = render.render_depends_on_bullet(
            render.DependsOnTarget(target="clm-aa1111", title="Foundation Claim A")
        )
        self.assertIn(render.SOLIDITY_ANNOTATION_PENDING, bullet)
        filled = refresh_kb_metadata.SOLIDITY_ANNOTATION.sub("(solidity 0.90)", bullet, count=1)
        self.assertIn("(solidity 0.90)", filled)
        self.assertNotIn(render.PENDING_LITERAL, filled)

    def test_leaf_reference_footer_equals_the_production_renderer(self):
        # `render_leaf_references` ignores the register path for an empty
        # citing set; several are passed so that assumption is asserted rather
        # than relied on.
        for register in ("claim-quality.md", "part3/claim-quality.md", "a/b/claim-quality.md"):
            with self.subTest(register=register):
                self.assertEqual(
                    render.LEAF_REFERENCES_PENDING_FOOTER,
                    kb_index_lib.render_leaf_references(register, []),
                )

    def test_leaf_reference_footer_is_located_as_a_footer(self):
        # The locator both refresh and verify use must see the rendered footer
        # as this entry's footer — otherwise refresh inserts a second one.
        entry = render.render_claim_entry(
            node_id="clm-aa1111", title="Regime Conservation Laws", confidence=0.7, rationale="synthetic."
        )
        bands = kb_index_lib.locate_leaf_reference_footers(entry)
        self.assertIn("clm-aa1111", bands)
        self.assertIsNotNone(bands["clm-aa1111"].footer_line)

    def test_no_derived_subtree_field_is_rendered(self):
        block = _frontmatter_hosts()
        self.assertNotIn("subtree-claims", block)
        self.assertNotIn("subtree-experiments", block)


# ---------------------------------------------------------------------------
# Prose collapse
# ---------------------------------------------------------------------------


def _parse_one_entry(register_text: str):
    """Parse a one-entry register through the production parser.

    ``parse_claim_quality_file`` takes a path, so the specimen is written to a
    tempdir. That is the test's filesystem use, never the renderer's.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "claim-quality.md"
        path.write_text(register_text + "\n", encoding="utf-8")
        entries = kb_index_lib.parse_claim_quality_file(path, root)
    return entries


class TestRationaleCollapse(unittest.TestCase):
    """A key-leading line inside a rationale is representable, and round-trips."""

    maxDiff = None

    #: A rationale whose text contains lines beginning with two of the reader's
    #: own keys. Authored across physical lines, it truncates at the first of
    #: them; collapsed to one line, `kb_index_lib.py:985` never matches at a
    #: line start and the whole value survives.
    RATIONALE = (
        "The register grammar is the subject here.\n"
        "- confidence: is one of the keys the fold breaks on,\n"
        "  and\n"
        "- depends-on: is another; both appear in this sentence as prose."
    )

    def test_collapse_yields_one_physical_line(self):
        collapsed = render.collapse_prose(self.RATIONALE)
        self.assertNotIn("\n", collapsed)

    def test_collapse_matches_the_parsers_normalizer(self):
        self.assertEqual(render.collapse_prose(self.RATIONALE), kb_index_lib._normalize_text(self.RATIONALE))

    def test_collapse_is_byte_stable_across_two_renders(self):
        first = render.render_claim_entry(
            node_id="clm-aa1111", title="Collapse Specimen", confidence=0.5, rationale=self.RATIONALE
        )
        second = render.render_claim_entry(
            node_id="clm-aa1111", title="Collapse Specimen", confidence=0.5, rationale=self.RATIONALE
        )
        self.assertEqual(first, second)
        # Idempotent: re-collapsing an already-collapsed value changes nothing.
        self.assertEqual(
            render.collapse_prose(render.collapse_prose(self.RATIONALE)), render.collapse_prose(self.RATIONALE)
        )

    def test_rendered_rationale_occupies_one_physical_line(self):
        entry = render.render_claim_entry(
            node_id="clm-aa1111", title="Collapse Specimen", confidence=0.5, rationale=self.RATIONALE
        )
        rationale_lines = [ln for ln in entry.split("\n") if ln.startswith("- rationale:")]
        self.assertEqual(len(rationale_lines), 1)
        # Nothing after it may look like a continuation the fold would break on
        # — the next line is the entry's last field or nothing at all.
        body = entry.split("\n")
        idx = body.index(rationale_lines[0])
        self.assertEqual(body[idx + 1 :], [])

    def test_round_trips_through_the_production_parser(self):
        entry = render.render_claim_entry(
            node_id="clm-aa1111", title="Collapse Specimen", confidence=0.5, rationale=self.RATIONALE
        )
        parsed = _parse_one_entry(entry)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].rationale, render.collapse_prose(self.RATIONALE))

    def test_the_authored_multiline_form_is_the_one_that_truncates(self):
        # The teeth: the same value written across physical lines loses
        # everything from the first key-leading line on. This is what the
        # collapse defeats, and asserting it is what proves the collapse is
        # load-bearing rather than cosmetic.
        naive = "\n".join(
            [
                "## Collapse Specimen",
                render.render_id_marker("clm-aa1111"),
                "",
                "### Quality",
                "- confidence: 0.5",
                render.SOLIDITY_PENDING_LINE,
                f"- rationale: {self.RATIONALE}",
            ]
        )
        parsed = _parse_one_entry(naive)
        self.assertEqual(len(parsed), 1)
        self.assertNotEqual(parsed[0].rationale, render.collapse_prose(self.RATIONALE))
        self.assertNotIn("depends-on", parsed[0].rationale)


# ---------------------------------------------------------------------------
# The entry grammar the output must satisfy
# ---------------------------------------------------------------------------


class TestEntryGrammar(unittest.TestCase):
    """The rendered entry parses back to the values it was given."""

    maxDiff = None

    def test_heading_precedes_marker(self):
        # The parser binds a marker to the
        # PRECEDING `## ` heading; marker-above-heading binds it to the
        # previous entry's title, or drops it entirely.
        entry = _claim_entry_full()
        lines = entry.split("\n")
        self.assertTrue(lines[0].startswith("## "))
        self.assertEqual(lines[1], render.render_id_marker("clm-aa1111"))

    def test_full_entry_round_trips(self):
        parsed = _parse_one_entry(_claim_entry_full())
        self.assertEqual(len(parsed), 1)
        record = parsed[0]
        self.assertEqual(record.id, "clm-aa1111")
        self.assertEqual(record.title, "Regime Conservation Laws")
        self.assertEqual(record.confidence, 0.7)
        self.assertIsNone(record.solidity)
        # Claim targets first, then the two framework forms; `Axiom N` is
        # normalized to `axiom-N` by the parser.
        self.assertEqual(
            [e.target for e in record.depends_on],
            ["clm-bb2222", "clm-cc3333", "INVARIANT-S2", "axiom-4"],
        )
        claim_edges = [e for e in record.depends_on if e.target_kind == "claim"]
        self.assertEqual([e.context for e in claim_edges], ["builds directly on the anchor claim", None])
        self.assertEqual(len(record.strengthen_by), 2)

    def test_minimal_entry_round_trips(self):
        parsed = _parse_one_entry(_claim_entry_minimal())
        self.assertEqual(len(parsed), 1)
        self.assertIsNone(parsed[0].confidence)
        self.assertEqual(parsed[0].depends_on, ())
        self.assertEqual(parsed[0].strengthen_by, ())

    def test_no_edge_is_not_absorbed_into_a_folding_field(self):
        # `- no-edge:` is in no fold's key list, so below `- depends-on:` it is
        # appended to the last dependency bullet and below `- rationale:` it is
        # absorbed into the rationale. Rendered above all three, it reaches
        # neither.
        record = _parse_one_entry(_claim_entry_full())[0]
        self.assertNotIn("no-edge", record.rationale)
        for item in record.strengthen_by:
            self.assertNotIn("no-edge", item.text)
        for edge in record.depends_on:
            self.assertNotIn("no-edge", edge.context or "")

    def test_depends_on_head_is_the_target_alone(self):
        # The em-dash is what cuts the head. A hyphen would leave the head
        # running over the title, minting an edge per id token in it.
        bullet = render.render_depends_on_bullet(
            render.DependsOnTarget(target="clm-bb2222", title="A Title Naming clm-cc3333")
        )
        head = kb_index_lib._depends_on_bullet_head(bullet.strip().removeprefix("- "))
        self.assertEqual(head.strip(), "clm-bb2222")
        self.assertIn("—", bullet)
        self.assertNotIn(" - ", bullet.removeprefix("  - "))

    def test_framework_bullet_carries_no_solidity_annotation(self):
        # On a framework bullet the parser reads the first parenthetical as the
        # edge's context, so an annotation there becomes prose.
        bullet = render.render_depends_on_bullet(
            render.DependsOnTarget(target="INVARIANT-S2", context="labelling convention")
        )
        self.assertNotIn("solidity", bullet)
        edges = kb_index_lib._parse_depends_on_line(bullet, "clm-aa1111")
        self.assertEqual([(e.target, e.context) for e in edges], [("INVARIANT-S2", "labelling convention")])

    def test_support_entry_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "claim-quality.md"
            path.write_text(_support_entry() + "\n", encoding="utf-8")
            entries = kb_index_lib.parse_support_quality_entries(path, root)
        self.assertEqual(set(entries), {"sup-ee5555"})
        self.assertEqual(entries["sup-ee5555"]["quality"], 0.9)
        self.assertIsNone(entries["sup-ee5555"]["solidity"])

    def test_staged_supports_round_trip_through_the_production_grammar(self):
        """Render → parse via the production function → values match.

        ``kb_index_lib.parse_register_staged_supports`` is the grammar, and it
        is what the tree walk behind ``scan_authored_support_edges`` — and so
        the phase-2.6 postcondition — stands on. Parsing the rendered block with
        anything else here would prove the renderer agreed with this test's own
        idea of the format, which is the agreement that was never in doubt.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "claim-quality.md"
            path.write_text(_support_entry_staged() + "\n", encoding="utf-8")

            per_file = kb_index_lib.parse_register_staged_supports(path)
            tree_wide = kb_index_lib.scan_authored_support_edges(root)

        want = [("clm-bb2222", 0.5), ("clm-cc3333", kb_index_lib.PENDING_FRACTION)]
        self.assertEqual(per_file, {"sup-ee5555": want})
        # The pairs reach the KB-wide reader the postcondition uses, in order,
        # with the pending literal surviving as the pending object and not as a
        # zero — an unassessed lift, not an absent one.
        self.assertEqual(tree_wide, {"sup-ee5555": tuple(want)})

    def test_the_staging_block_and_the_entry_fields_do_not_absorb_each_other(self):
        """Field order is correctness here, not style.

        A folding field runs until the next line matching the reader's key list,
        so a key absent from that list is absorbed into the field above it — how
        ``- no-edge:`` has to be placed above all three folds. The support entry
        reader already carries ``supports`` in both of its fold-break lists, so
        the block terminates those folds correctly; this asserts the whole entry
        survives with the block in it, in both directions.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "claim-quality.md"
            path.write_text(_support_entry_staged() + "\n", encoding="utf-8")
            entry = kb_index_lib.parse_support_quality_entries(path, root)["sup-ee5555"]
            staged = kb_index_lib.parse_register_staged_supports(path)["sup-ee5555"]

        # The block did not swallow, and was not swallowed by, any field.
        self.assertEqual(entry["quality"], 0.9)
        self.assertNotIn("supports", entry["rationale"])
        self.assertNotIn("no-edge", entry["rationale"])
        self.assertEqual(
            entry["rationale"], "Derives the beneficiary result in full; staged before its hosting leaf exists."
        )
        # The pairs are indented `- `-led lines, which is exactly the shape the
        # depends-on gather collects. A beneficiary must never arrive as a
        # dependency: it is the edge pointing the other way.
        self.assertEqual([e.target for e in entry["depends_on"]], ["clm-aa1111"])
        self.assertEqual([claim_id for claim_id, _ in staged], ["clm-bb2222", "clm-cc3333"])

    def test_a_claim_entry_stages_no_supports_block(self):
        # A fan-out belongs to a support. The claim renderer takes no such
        # value, and the claim parser's fold-break list has no `supports` key,
        # so a block there would be absorbed rather than read.
        self.assertNotIn("- supports:", _claim_entry_full())
        self.assertNotIn("- supports:", _claim_entry_minimal())

    def test_scores_are_rendered_losslessly(self):
        # Fixed-width formatting would round an authored 0.875 to 0.88 — a
        # silent edit of the author's value, which this program forbids.
        entry = render.render_claim_entry(
            node_id="clm-aa1111", title="Precision Specimen", confidence=0.875, rationale="synthetic."
        )
        self.assertEqual(_parse_one_entry(entry)[0].confidence, 0.875)


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


class TestFrontmatterBlock(unittest.TestCase):
    """The canonical output shape is the inline list, and it round-trips."""

    maxDiff = None

    def test_canonical_shape_is_the_inline_list(self):
        block = _frontmatter_claims()
        self.assertIn("claims: [clm-aa1111, clm-bb2222, clm-cc3333]", block)

    def test_all_three_authored_shapes_read_alike(self):
        # The reader accepts three shapes; the writer emits one. This asserts
        # the choice is free — that the other two carry no information the
        # canonical one loses — rather than merely declared.
        wrapped = (
            "<!-- kb-frontmatter\nkind: leaf\nclaims: [clm-aa1111,\n         clm-bb2222,\n         clm-cc3333]\n-->"
        )
        yaml_bullets = "<!-- kb-frontmatter\nkind: leaf\nclaims:\n  - clm-aa1111\n  - clm-bb2222\n  - clm-cc3333\n-->"
        block = render.render_frontmatter_block(
            render.FrontmatterValues(kind="leaf", claims=("clm-aa1111", "clm-bb2222", "clm-cc3333"))
        )
        canonical = kb_index_lib.parse_frontmatter(block)
        self.assertEqual(canonical, kb_index_lib.parse_frontmatter(wrapped))
        self.assertEqual(canonical, kb_index_lib.parse_frontmatter(yaml_bullets))
        self.assertEqual(canonical["claims"], ["clm-aa1111", "clm-bb2222", "clm-cc3333"])

    def test_no_claim_reason_round_trips_quoted(self):
        reason = 'a reason that would otherwise type as a list [clm-aa1111] or as "quoted" text'
        block = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf", no_claim=reason))
        self.assertEqual(kb_index_lib.parse_frontmatter(block)["no-claim"], reason)

    def test_hosted_nodes_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "leaf.md"
            path.write_text(_frontmatter_hosts() + "\n\n# A Container\n", encoding="utf-8")
            experiments = kb_index_lib.parse_experiment_leaf(path, root)
            supports = kb_index_lib.parse_support_leaf(path, root)
        self.assertEqual([e.id for e in experiments], ["exp-gg7777"])
        self.assertEqual(experiments[0].strengthens, (("clm-aa1111", 0.8),))
        self.assertEqual([s.id for s in supports], ["sup-hh8888", "sup-ii9999"])

    def test_pending_support_fraction_renders_as_the_literal(self):
        self.assertEqual(
            render.render_supports_pair_line("clm-aa1111", None),
            f"  - clm-aa1111: {render.PENDING_LITERAL}",
        )

    def test_path_stable_round_trips_quoted(self):
        # The reader types this field generically and strips exactly
        # one outer quote pair, so the quoted form round-trips any single-line
        # label — including one that would otherwise type as a bool or a list.
        for label in (
            "regime conservation — stable reference label",
            "true",
            "[clm-aa1111]",
            'a label with "quoted" text',
        ):
            with self.subTest(label=label):
                block = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf", path_stable=label))
                self.assertEqual(kb_index_lib.parse_frontmatter(block)["path-stable"], label)

    def test_path_stable_is_absent_when_not_supplied(self):
        # Absent is a state, not an empty string: a block that always carried
        # the key would author a value nobody supplied.
        block = render.render_frontmatter_block(render.FrontmatterValues(kind="leaf", claims=("clm-aa1111",)))
        self.assertNotIn("path-stable", block)
        self.assertNotIn("path-stable", kb_index_lib.parse_frontmatter(block))

    def test_path_stable_survives_the_derived_field_anchor(self):
        # `refresh` inserts `subtree-claims:` immediately after `kind:`, which
        # lands it between `kind:` and `path-stable:`. The block is a mapping,
        # so the reader is indifferent — asserted rather than assumed, since
        # this is the one place the authored order is disturbed by a writer
        # this API does not control.
        from kb_tools.kb_write import store as _store

        block = _frontmatter_path_stable()
        after = _store.replace_or_insert_frontmatter_field(
            block, field="subtree-claims", ids=["clm-aa1111"], anchor_prefix="kind:"
        )
        fields = kb_index_lib.parse_frontmatter(after)
        self.assertEqual(fields["path-stable"], "regime conservation — stable reference label")
        self.assertEqual(fields["subtree-claims"], ["clm-aa1111"])
        self.assertEqual(fields["claims"], ["clm-aa1111"])


# ---------------------------------------------------------------------------
# Citation form
# ---------------------------------------------------------------------------


class TestCitationForm(unittest.TestCase):
    """The rendered citation is what the citation checker's own regex reads."""

    def test_citation_parses_as_a_citation_link(self):
        from kb_tools import verify_citations

        rendered = _citation()
        match = verify_citations.CITATION_LINK_RE.search(rendered)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '"the weakest link in the dependency cone"')
        self.assertEqual(match.group(2), "part3/claim-quality.md#regime-conservation-laws")

    def test_excerpt_is_collapsed_to_one_line(self):
        rendered = render.render_citation(excerpt="a clause\nsplit across\n  lines", kb_path="a.md", anchor="x")
        self.assertEqual(rendered, '["a clause split across lines"](a.md#x)')


if __name__ == "__main__":
    unittest.main()
