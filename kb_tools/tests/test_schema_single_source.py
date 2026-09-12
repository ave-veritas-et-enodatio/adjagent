"""Cross-tree consistency tests for the single-sourced schema vocabulary.

The build-band ladder used to be encoded three times — twice on the build side
(``kb_index_lib.derive_build_band`` / ``_BUILD_STATUS_BANDS``) and once on the
query side (``kb_cmd/index.py::BUILD_BANDS``) — with nothing tying them. These
tests pin all three to the single source, :data:`kb_schema.BUILD_BAND_LADDER`,
so a future edit to one rung that forgets another fails loudly here.

The query side is a normal package, imported directly as ``kb_tools.kb_cmd.index``.

The same shape covers the KB's navigation convention: the node filenames
and the up-link marker had four spellings across the survey validator, the driver
and the verifier, and :class:`TestNavigationConventionSingleSourced` pins each to
``kb_index_lib``'s.

And the same shape again for the node-kind vocabulary, which had no single
statement at all until :data:`kb_schema.NODE_KINDS`:
:class:`TestNodeKindVocabularySingleSourced`.
"""

import importlib
import re
import tempfile
import unittest
from pathlib import Path

from kb_tools import kb_index_lib as lib
from kb_tools import kb_util
from kb_tools.kb_cmd import index as kb_cmd_index
from kb_tools.kb_schema import (
    BUILD_BAND_LADDER,
    FRAMEWORK_KINDS,
    NODE_KINDS,
    UNKNOWN_BAND_SLUG,
    WORK_ID_RE,
    id_body,
    kind_table,
    node_kind_plural,
)


class TestBandLadderSingleSourced(unittest.TestCase):
    """The band ladder is single-sourced and consistent across both trees."""

    def test_query_side_slugs_match_ladder_in_order(self):
        qidx = kb_cmd_index
        ladder_slugs = [b.slug for b in BUILD_BAND_LADDER]
        # BUILD_BANDS carries the scored ladder rungs in order, plus a trailing
        # query-only "unknown" pending bucket.
        query_slugs = [slug for slug, _label in qidx.BUILD_BANDS]
        self.assertEqual(query_slugs, ladder_slugs + [UNKNOWN_BAND_SLUG])

    def test_query_side_labels_match_ladder(self):
        qidx = kb_cmd_index
        scored = [(slug, label) for slug, label in qidx.BUILD_BANDS if slug != UNKNOWN_BAND_SLUG]
        self.assertEqual(scored, [(b.slug, b.label) for b in BUILD_BAND_LADDER])

    def test_derive_build_band_slug_set_in_ladder(self):
        # Every slug derive_build_band can emit for an in-domain solidity is a
        # ladder slug; None maps to the unknown bucket.
        ladder_slugs = {b.slug for b in BUILD_BAND_LADDER}
        for s in (1.0, 0.85, 0.7, 0.5, 0.3, 0.1, 0.0, -1.0):
            self.assertIn(lib.derive_build_band(s), ladder_slugs)
        self.assertEqual(lib.derive_build_band(None), UNKNOWN_BAND_SLUG)

    def test_threshold_boundaries(self):
        # (solidity, expected slug, expected build-status phrase) — locks the
        # inclusive lower bounds of each band.
        cases = [
            (0.85, "ok-to-build", "ok to build on"),
            (0.84, "ok-with-caveats", "ok to build on, see caveats"),
            (0.65, "ok-with-caveats", "ok to build on, see caveats"),
            (0.45, "input-only", "use as input only, don't build deeper"),
            (0.20, "do-not-build", "do not build on, rework needed"),
            (0.19, "refuted", "refuted, do not use"),
            (-0.5, "refuted", "refuted, do not use"),
        ]
        for solidity, slug, phrase in cases:
            self.assertEqual(lib.derive_build_band(solidity), slug, solidity)
            self.assertEqual(lib.build_status_phrase(solidity), phrase, solidity)
        self.assertIsNone(lib.build_status_phrase(None))

    def test_input_only_label_phrase_divergence_preserved(self):
        # The one intentional divergence: input-only's display label is shorter
        # than its build-status phrase.
        band = next(b for b in BUILD_BAND_LADDER if b.slug == "input-only")
        self.assertEqual(band.label, "use as input only")
        self.assertEqual(band.status_phrase, "use as input only, don't build deeper")
        self.assertNotEqual(band.label, band.status_phrase)


class TestInvariantsFilenameSingleSourced(unittest.TestCase):
    """``kb_index_lib`` is the only module that names the corpus-invariant file.

    ``kb_util`` carried a second ``INVARIANTS_FILENAME`` whose value was
    ``CLAUDE.md`` — the legacy spelling, not the current one. Nothing imported
    it, which is why it rotted and why a grep for the name was a coin flip
    between the right answer and the wrong one. This keeps it deleted.
    """

    def test_kb_index_lib_owns_both_spellings(self):
        self.assertEqual(lib.INVARIANTS_FILENAME, "invariants.md")
        self.assertEqual(lib.LEGACY_INVARIANTS_FILENAME, "CLAUDE.md")

    def test_kb_util_exports_no_second_invariants_channel(self):
        for name in ("INVARIANTS_FILENAME", "invariants_md"):
            self.assertFalse(hasattr(kb_util, name), f"kb_util.{name} is back; kb_index_lib owns this channel")


class TestNavigationConventionSingleSourced(unittest.TestCase):
    """``kb_index_lib`` is the only module that spells the KB's navigation convention.

    The node filenames and the up-link marker had three independent spellings —
    the survey validator's design-time reachability, ``verify_kb_metadata``'s
    refresh-fixable test, and the query side's directory-to-node-file
    resolution — one of which carried a comment explaining why it had to be
    local. They are not cosmetic duplicates: the validator computes the
    design-time parent relation from the index name and gates post-build
    reachability on the marker. A convention change reaching two of the three
    would leave design-time and post-build reachability disagreeing about one
    tree — or, at the third, every directory-form subtree query answering
    empty.

    ``kb_tools.kb_driver.replay`` is not a site: it wrote the very up-links and
    index nodes the validator's reachability check read, but the distillation
    stages that let a replayed run stand in for are gone with the LaTeX-reading
    pipeline they belonged to, and the driver never derives KB navigation
    content of its own.
    """

    #: The three modules the convention was spelled in, and the names each binds.
    SITES = (
        ("kb_tools.kb_survey.validate", ("ENTRY_POINT_FILENAME", "INDEX_FILENAME", "UPLINK_MARKER")),
        ("kb_tools.verify_kb_metadata", ("ENTRY_POINT_FILENAME", "INDEX_FILENAME")),
        ("kb_tools.kb_cmd.index", ("INDEX_FILENAME",)),
    )

    #: Every local name above, mapped to the published name it must resolve to.
    PUBLISHED = {
        "ENTRY_POINT_FILENAME": "ENTRY_POINT_FILENAME",
        "INDEX_FILENAME": "INDEX_FILENAME",
        "UPLINK_MARKER": "UPLINK_MARKER",
    }

    #: The literals a re-spelling would reintroduce.
    LITERALS = ("entry-point.md", "index.md", "↑")

    def test_kb_index_lib_publishes_the_convention(self):
        self.assertEqual(lib.ENTRY_POINT_FILENAME, "entry-point.md")
        self.assertEqual(lib.INDEX_FILENAME, "index.md")
        self.assertEqual(lib.UPLINK_MARKER, "↑")

    def test_every_site_binds_the_published_object(self):
        """Identity, not equality: an equal copy is exactly what is being kept out."""
        for module_name, names in self.SITES:
            module = importlib.import_module(module_name)
            for name in names:
                with self.subTest(module=module_name, name=name):
                    self.assertIs(getattr(module, name), getattr(lib, self.PUBLISHED[name]))

    def test_no_site_spells_the_convention_itself(self):
        """The sweep, scoped to the three files that bind the convention.

        ``kb_cmd/index.py`` joined them: it was the third site, building
        ``"<dir>/" + <node filename>`` inline on the query side, and it is now an
        importer like the other two — so the sweep covers it rather than naming
        it as the exception. Still deliberately not repo-wide, and the limit is
        stated rather than implied: a *fourth* spelling, in a module not listed
        above, is not caught here. ``SITES`` is this sweep's whole scope, and
        extending it is how a newly found site joins.
        """
        for module_name, _ in self.SITES:
            source = Path(importlib.import_module(module_name).__file__).read_text(encoding="utf-8")
            for literal in self.LITERALS:
                with self.subTest(module=module_name, literal=literal):
                    self.assertNotIn(literal, source, f"{module_name} spells {literal!r} rather than importing it")


#: The id-grammar literal, in both spellings a module can carry it: a plain
#: regex (``[a-z0-9]{6}``) and the same thing inside an f-string, where the
#: braces are doubled (``[a-z0-9]{{6}}``). A sweep that matched only the first
#: would leave the f-string form as a place for the ninth site to come back.
_ID_LITERAL_RE = re.compile(r"\[a-z0-9\]\{\{?6\}\}?")

#: The one module allowed to spell the grammar: it IS the definition.
_ID_GRAMMAR_SOURCE = "kb_schema.py"

#: Files that carry the literal legitimately, keyed by path relative to
#: ``kb_tools/``, with the reason. One entry, and it is this file: the pin below
#: asserts what ``id_body`` composes, and a pin written in terms of ``id_body``
#: would assert that a function equals itself. Kept honest by
#: :func:`TestIdGrammarSweep.test_the_allowlist_cannot_outlive_its_reason`,
#: which fails on an entry whose file no longer carries the literal.
ID_LITERAL_ALLOWLIST = {
    "tests/test_schema_single_source.py": (
        "the pin — it states the spelling `id_body` must compose, which is the one claim that cannot be "
        "written in terms of `id_body` without becoming a tautology"
    ),
}

_KB_TOOLS = Path(__file__).resolve().parent.parent
_SWEEP_SKIP_DIRS = frozenset({"_vendor", "__pycache__", ".pytest_cache"})


def scan_id_literals(root: Path) -> list[str]:
    """``<path>:<line>`` for every id-grammar literal under ``root``.

    ``root`` is a parameter so the teeth check can aim the same scanner at a
    plant rather than assert on a walk it never proved reaches anything.
    """
    findings: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if _SWEEP_SKIP_DIRS & set(relative.parts):
            continue
        if relative.as_posix() == _ID_GRAMMAR_SOURCE or relative.as_posix() in ID_LITERAL_ALLOWLIST:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _ID_LITERAL_RE.search(line):
                findings.append(f"{relative.as_posix()}:{number}")
    return findings


class TestIdGrammarSweep(unittest.TestCase):
    """No module outside ``kb_schema`` re-spells the id grammar.

    The hardcoded id-regex re-implementations collapse onto
    ``kb_schema.id_body`` — but that landed once before and came back, because
    nothing was watching: eight sites across four shipped modules each
    re-spelled ``[a-z0-9]{6}`` by hand, and ``verify_kb_metadata`` carried both
    conventions three lines apart. A collapse with no sweep is a state the tree
    passes through, not one it holds.
    """

    def test_no_module_outside_kb_schema_spells_the_id_grammar(self):
        self.assertEqual(scan_id_literals(_KB_TOOLS), [])

    def test_the_sweep_reaches_the_modules_that_carried_the_eight_sites(self):
        """Anti-vacuity: an empty walk would pass the assertion above."""
        swept = set()
        for path in sorted(_KB_TOOLS.rglob("*.py")):
            relative = path.relative_to(_KB_TOOLS)
            if _SWEEP_SKIP_DIRS & set(relative.parts):
                continue
            swept.add(relative.as_posix())

        for relative in (
            "kb_index_lib.py",
            "verify_kb_metadata.py",
            "verify_citations.py",
            "refresh_kb_metadata.py",
            "kb_write/store.py",
            "tests/test_writeapi_regression_fixture.py",
        ):
            self.assertIn(relative, swept, relative)

    def test_a_planted_literal_is_caught(self):
        """Teeth, in both spellings — the plain regex and the f-string form."""
        for name, line in (
            ("plain.py", 'PATTERN = re.compile(r"\\b(clm-[a-z0-9]{6})\\b")\n'),
            ("fstring.py", 'MESSAGE = f"expected {kind}-[a-z0-9]{{6}}"\n'),
        ):
            with self.subTest(spelling=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / name).write_text(line, encoding="utf-8")
                self.assertEqual(scan_id_literals(root), [f"{name}:1"])

    def test_the_definition_and_the_allowlist_are_out_of_the_sweep(self):
        """The two exemptions, asserted rather than assumed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / _ID_GRAMMAR_SOURCE).write_text('HASH_RE = r"[a-z0-9]{6}"\n', encoding="utf-8")
            (root / "tests").mkdir()
            for relative in ID_LITERAL_ALLOWLIST:
                (root / relative).write_text('EXPECTED = "clm-[a-z0-9]{6}"\n', encoding="utf-8")
            self.assertEqual(scan_id_literals(root), [])

    def test_the_allowlist_cannot_outlive_its_reason(self):
        """An entry whose file no longer carries the literal has lost its reason."""
        stale = []
        for relative, reason in ID_LITERAL_ALLOWLIST.items():
            path = _KB_TOOLS / relative
            source = path.read_text(encoding="utf-8") if path.is_file() else ""
            if not _ID_LITERAL_RE.search(source):
                stale.append(f"{relative}: {reason}")
        self.assertEqual(stale, [])


class TestIdGrammarSingleSourced(unittest.TestCase):
    """id_body wraps into the exact pre-refactor patterns at each callsite."""

    def test_id_body_shapes(self):
        self.assertEqual(id_body("clm"), "clm-[a-z0-9]{6}")
        self.assertEqual(id_body("clm", "exp"), "(?:clm|exp)-[a-z0-9]{6}")
        self.assertEqual(id_body(), "(?:clm|exp|sup)-[a-z0-9]{6}")
        self.assertEqual(id_body("clm", "sup"), "(?:clm|sup)-[a-z0-9]{6}")

    def test_id_body_preserves_kind_order(self):
        # Argument order does not affect the alternation order.
        self.assertEqual(id_body("exp", "clm"), "(?:clm|exp)-[a-z0-9]{6}")

    def test_id_body_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            id_body("xyz")

    def test_build_side_patterns(self):
        self.assertEqual(lib._CLAIM_ID_RE.pattern, r"\b(clm-[a-z0-9]{6})\b")
        self.assertEqual(lib._ANY_ID_RE.pattern, r"\b((?:clm|exp)-[a-z0-9]{6})\b")
        self.assertEqual(lib._EXP_ID_RE.pattern, r"\b(exp-[a-z0-9]{6})\b")
        self.assertEqual(lib._SUP_ID_RE.pattern, r"\b(sup-[a-z0-9]{6})\b")

    def test_the_eight_collapsed_sites_compose_their_pre_refactor_spelling(self):
        """The behavior-identical claim, site by site, against the retired literal.

        Seven of the eight recompose byte-for-byte. The eighth
        (``verify_citations.NODE_ID_RE``) cannot: it spelled its alternation
        ``clm|sup|exp`` while ``kb_schema.ID_KINDS`` orders them ``clm|exp|sup``,
        and ``id_body`` normalizes to that order regardless of argument order.
        Byte-equality is the wrong assertion for it — see
        :func:`test_the_reordered_alternation_matches_the_same_ids`.
        """
        from kb_tools import refresh_kb_metadata, verify_citations, verify_kb_metadata

        cases = (
            (lib._STRENGTHENS_PAIR_RE, r"^\s*(?:-\s*)?(clm-[a-z0-9]{6})\s*:\s*(-?\d+(?:\.\d+)?)\s*$"),
            (
                lib._SUPPORTS_PAIR_RE,
                r"^\s*(?:-\s*)?(clm-[a-z0-9]{6})\s*:\s*(-?\d+(?:\.\d+)?|\*pending\*)\s*$",
            ),
            (verify_kb_metadata.CANONICAL_ID, r"<!-- id: (clm-[a-z0-9]{6}) -->"),
            # CANONICAL_ANY_ID has since gained the external-work alternative,
            # whose id is not hash-bodied and so is not `id_body`'s to compose;
            # the pre-refactor spelling is the clm/sup half it still carries.
            (
                verify_kb_metadata.CANONICAL_ANY_ID,
                r"<!-- id: ((?:clm|sup)-[a-z0-9]{6}|" + WORK_ID_RE + r") -->",
            ),
            (verify_kb_metadata.ID_RE, r"\b(clm-[a-z0-9]{6})\b"),
            (verify_citations.ENTRY_MARKER_RE, r"<!--\s*id:\s*((?:clm|exp|sup)-[a-z0-9]{6})"),
            (refresh_kb_metadata.CLAIM_ID_TOKEN, r"\b(clm-[a-z0-9]{6})\b"),
        )
        for pattern, expected in cases:
            with self.subTest(pattern=expected):
                self.assertEqual(pattern.pattern, expected)

    def test_the_reordered_alternation_matches_the_same_ids(self):
        """`clm|sup|exp` and `clm|exp|sup` are one language, not two.

        The kinds are mutually exclusive fixed-length prefixes, so alternation
        order cannot change what matches — but "cannot" is the kind of claim
        that wants an instrument rather than a reader's agreement.
        """
        from kb_tools import verify_citations

        retired = re.compile(r"\b(?:clm|sup|exp)-[a-z0-9]{6}\b")
        corpus = (
            "clm-aa1111",
            "sup-bb2222",
            "exp-cc3333",
            "clm-xxxxxx",
            "see clm-aa1111 and exp-cc3333 in one line",
            "clm-toolong99",
            "clm-SHORT",
            "xclm-aa1111",
            "clm-aa111",
            "",
        )
        for text in corpus:
            with self.subTest(text=text):
                self.assertEqual(
                    verify_citations.NODE_ID_RE.findall(text),
                    retired.findall(text),
                )


class TestNodeKindVocabularySingleSourced(unittest.TestCase):
    """The node-kind vocabulary has one statement, and the sites that branch on
    it are total over that statement.

    The defect this pins: a fifth kind (``work``) arrived and every site that
    needed the list spelled it by hand, so each spelling drifted on its own —
    a census that counted the work in its total and never in its breakdown, a
    union that returned four of five, and a renderer that drew the fifth as a
    ghost id with no record behind it. The iterating sites now read
    ``NODE_KINDS``; the branching ones are proven total against it here and at
    the moment their table is defined.
    """

    def test_a_branching_table_missing_a_kind_is_refused(self):
        """The property the vocabulary buys. A table naming some of the kinds
        fails, naming the site and the kinds it does not handle — rather than
        passing and falling through to whichever branch came last."""
        short = {kind: kind for kind in NODE_KINDS[:-1]}
        with self.assertRaises(ValueError) as caught:
            kind_table(short, what="probe")
        message = str(caught.exception)
        self.assertIn("probe", message)
        self.assertIn(NODE_KINDS[-1], message)

    def test_a_table_naming_a_non_kind_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            kind_table({**{kind: kind for kind in NODE_KINDS}, "conjecture": "x"}, what="probe")
        self.assertIn("conjecture", str(caught.exception))

    def test_a_total_table_comes_back_keyed_in_the_vocabularys_order(self):
        written = {kind: kind.upper() for kind in reversed(NODE_KINDS)}
        got = kind_table(written, what="probe")
        self.assertEqual(got, written)
        self.assertEqual(tuple(got), NODE_KINDS)

    def test_every_branching_site_is_total_over_the_vocabulary(self):
        """Each table is already proven total when its module imports; this is
        the standing form, so a site that stopped using ``kind_table`` is not
        silently exempt."""
        from kb_tools.kb_graph import svg

        cases = (
            ("kb_cmd.index node builders", kb_cmd_index._NODE_BUILDERS),
            ("kb_graph.svg node fills", svg._FILL_BY_KIND),
        )
        for what, table in cases:
            with self.subTest(site=what):
                self.assertEqual(tuple(table), NODE_KINDS)

    def test_the_framework_pair_is_part_of_the_vocabulary(self):
        self.assertEqual(set(FRAMEWORK_KINDS) - set(NODE_KINDS), set())

    def test_minting_and_the_node_vocabulary_answer_different_questions(self):
        """``ID_KINDS`` is what gets *minted*; ``NODE_KINDS`` is what exists.

        Nothing mints an external work — its identity is the citation key — and
        nothing mints a framework node, so widening ``ID_KINDS`` toward this
        vocabulary would break every consumer asking the minting question.
        """
        from kb_tools.kb_schema import ID_KINDS, WORK_PREFIX

        self.assertNotIn(WORK_PREFIX, ID_KINDS)
        self.assertLess(len(ID_KINDS), len(NODE_KINDS))

    def test_the_census_keys_are_the_vocabulary_pluralized(self):
        """``stats``' node buckets are the vocabulary's own, in its own order —
        no second list of bucket names anywhere."""
        self.assertEqual(
            [node_kind_plural(kind) for kind in NODE_KINDS],
            ["claims", "supports", "experiments", "invariants", "axioms", "works"],
        )
        with self.assertRaises(ValueError):
            node_kind_plural("conjecture")


if __name__ == "__main__":
    unittest.main()
