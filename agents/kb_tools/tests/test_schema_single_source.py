"""Cross-tree consistency tests for the single-sourced schema vocabulary.

The build-band ladder used to be encoded three times — twice on the build side
(``kb_index_lib.derive_build_band`` / ``_BUILD_STATUS_BANDS``) and once on the
query side (``kb_cmd/index.py::BUILD_BANDS``) — with nothing tying them. These
tests pin all three to the single source, :data:`kb_schema.BUILD_BAND_LADDER`,
so a future edit to one rung that forgets another fails loudly here.

The query side is a normal package, imported directly as ``kb_tools.kb_cmd.index``.
"""

import unittest

from kb_tools import kb_index_lib as lib
from kb_tools.kb_cmd import index as kb_cmd_index
from kb_tools.kb_schema import BUILD_BAND_LADDER, UNKNOWN_BAND_SLUG, id_body


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


if __name__ == "__main__":
    unittest.main()
