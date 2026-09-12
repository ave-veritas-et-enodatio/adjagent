"""The store module's contract, as tests.

Three proofs, and the three classes below carry them.

**The readback-failure injection.** A deliberately broken renderer — the
marker rendered above its own heading — is fed through the real write path.
Every insert must refuse, the live file must be byte-unchanged, and no temp
may survive. This is the only test that proves the readback and the
all-or-nothing guarantee are load-bearing rather than decorative: if the
readback were absent, or ran against the live file instead of the temp, these
inserts would succeed and silently lose their records.

Two shapes of the injection are exercised because the parser fails them
differently, and a test that caught only one would leave the other class open.
Into an empty register the misplaced marker has no preceding ``## `` heading at
all and yields **no record**, so the temp's census sees a marker with nothing
behind it. Into a populated register it binds to the *previous* entry's heading
and yields a record with the wrong title, which the counts cannot see — the
temp's census sees the *binding* instead, and the field-by-field comparison
alone would miss this shape whenever the values happen to match. Both
refusals are still on the temp, with the live file unwritten.

**A planted census mismatch is refused before anything is composed.** The
fixture is the frozen 2026-09-02 corpus itself, which carries the defect
in its own bytes: every marker sits above its heading, so the first one binds to
nothing and the register's records are one short of its markers. The splice
callable in that test raises if it is ever called, which is how "before anything
is composed" is asserted rather than assumed.

**The forced interleaving.** Two writers, one register, one deterministic
ordering — the second writer completes inside the first's splice callable, which
is the real seam between the first writer's read and its replace. No test-only
hook exists in ``store.py`` for this: the splice is caller-supplied, so a test
splice with a side effect is enough, and a production surface that exists only
for tests would be its own defect. The first writer must return *retry*, not
*refused*, and the register must hold exactly one new entry with a passing
census.

**The exclusion the check and the replace run under.** The forced interleaving
above proves the check catches a writer that finished *before* it ran; it says
nothing about one that lands between the check and the replace, which is the
interleaving a real wave of concurrent writers produces. Closing it made the
check-then-replace sequence a critical section, and
:class:`TestTheReplaceRunsUnderAnExclusion` asserts that section is there: with
the directory held by somebody else, a writer that would have replaced anyway
waits instead, and then answers *retry* rather than publishing over the
holder. The outcome the section exists for — a wave of real processes losing
nothing — is ``test_writeapi_concurrency.py``, because no in-process seam can
put a second writer inside that window.

The remaining classes cover the boundaries this module also owns: path
containment, strict UTF-8, explicit register creation, all-or-nothing
batching, the splice primitives, and the structural instrument that keeps the
by-name exclusion honest.

**No test asserts a report string's wording** — only the status, the
reason token, and the named identity.
"""

import dataclasses
import fcntl
import os
import shutil
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from kb_tools import kb_index_lib
from kb_tools.kb_write import render, store

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "writeapi-regression"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _entry(node_id: str, title: str, **overrides) -> str:
    """A correctly rendered claim entry — the control for every injection."""
    values = {
        "confidence": 0.8,
        "rationale": "Synthetic entry for the store's write path.",
    }
    values.update(overrides)
    return render.render_claim_entry(node_id=node_id, title=title, **values)


def _marker_above_heading(node_id: str, title: str, **overrides) -> str:
    """**The broken renderer.** Emits the marker above its own ``## `` heading.

    The values are all correct — the id was minted correctly, the title chosen
    correctly, the rationale written correctly — and the only defect is the
    position of an HTML comment relative to a heading. No id check, no
    reference check and no rubric fires on it.
    """
    lines = _entry(node_id, title, **overrides).splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    return "\n".join(lines)


def _register(*entries: str) -> str:
    """A well-formed register document holding ``entries``, in order."""
    document = "# Synthetic Claim Quality Register\n"
    for entry in entries:
        document = store.insert_entry(document, entry)
    return document


def _temps_under(directory: Path) -> list[Path]:
    """Every surviving temp in ``directory``.

    Listed by suffix rather than globbed, so a leading-dot name cannot hide from
    the assertion the way it would from a shell glob.
    """
    return sorted(p for p in directory.iterdir() if p.name.endswith(store.TEMP_SUFFIX))


class _KbCase(unittest.TestCase):
    """A throwaway kb-root holding one register at ``part1/claim-quality.md``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolved, because a system temp directory is a symlink on macOS and
        # an unresolved root would make every containment test vacuous.
        self.kb_root = Path(self._tmp.name).resolve()
        self.rel = "part1/claim-quality.md"
        self.register = self.kb_root / self.rel
        self.register.parent.mkdir(parents=True)

    def write_register(self, text: str) -> None:
        self.register.write_text(text, encoding="utf-8")

    def insert(self, entry_text: str, expected: store.ExpectedEntry, **kwargs) -> store.Outcome:
        """Run one insert of ``entry_text`` through the real write path."""
        return store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=lambda text: store.insert_entry(text, entry_text),
                    expect=(expected,),
                    claim_delta=1,
                    **kwargs,
                )
            ],
        )

    def assert_untouched(self, before: bytes) -> None:
        """The live file is byte-unchanged and no temp survives."""
        self.assertEqual(self.register.read_bytes(), before)
        self.assertEqual(_temps_under(self.register.parent), [])


# ---------------------------------------------------------------------------
# The readback-failure injection
# ---------------------------------------------------------------------------


class TestReadbackFailureInjection(_KbCase):
    """With a marker-above-heading renderer, every insert refuses."""

    def _expected(self, node_id: str, title: str) -> store.ExpectedEntry:
        return store.ExpectedEntry(
            node_id=node_id,
            title=title,
            rigor=0.8,
            rationale="Synthetic entry for the store's write path.",
        )

    def test_the_correct_renderer_writes_so_the_injection_is_attributable(self):
        # The control. Without it a refusal below would be evidence only that
        # something in the harness is wrong, not that the injection was caught.
        self.write_register(_register())
        outcome = self.insert(_entry("clm-aa1111", "Anchor Claim"), self._expected("clm-aa1111", "Anchor Claim"))
        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertEqual(outcome.written, (self.rel,))
        self.assertEqual(_temps_under(self.register.parent), [])
        records = kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)
        self.assertEqual([r.id for r in records], ["clm-aa1111"])
        self.assertEqual(records[0].title, "Anchor Claim")

    def test_into_an_empty_register_the_record_vanishes_and_the_insert_refuses(self):
        self.write_register(_register())
        before = self.register.read_bytes()
        outcome = self.insert(
            _marker_above_heading("clm-aa1111", "Anchor Claim"),
            self._expected("clm-aa1111", "Anchor Claim"),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        # No preceding heading, so the parser returns no record at all: the
        # census on the temp is what sees the marker with nothing behind it.
        self.assertEqual(outcome.reason, store.Reason.RECORD_COUNT)
        self.assertEqual(outcome.subject, self.rel)
        self.assert_untouched(before)

    def test_into_a_populated_register_the_title_misbinds_and_the_insert_refuses(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        before = self.register.read_bytes()
        outcome = self.insert(
            _marker_above_heading("clm-bb2222", "Second Claim"),
            self._expected("clm-bb2222", "Second Claim"),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        # The marker binds to the PREVIOUS entry's heading: two markers under
        # one heading, both records present, counts equal. The census's binding
        # half is what sees it, one step before the field-by-field comparison
        # would — which matters because the comparison only catches this when
        # the values differ, and here they need not.
        self.assertEqual(outcome.reason, store.Reason.RECORD_COUNT)
        self.assertEqual(outcome.subject, self.rel)
        self.assert_untouched(before)

    def test_a_wrong_rationale_is_caught_by_the_same_comparison(self):
        # The comparison is field-by-field against the supplied values, not a
        # shape check: a renderer that dropped or mangled a value fails here
        # even when the layout is perfect.
        self.write_register(_register())
        before = self.register.read_bytes()
        outcome = self.insert(
            _entry("clm-aa1111", "Anchor Claim", rationale="what the renderer actually wrote"),
            self._expected("clm-aa1111", "Anchor Claim"),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.READBACK_MISMATCH)
        self.assert_untouched(before)

    def test_prose_is_compared_in_its_collapsed_form(self):
        # The value is supplied across three physical lines; the renderer
        # stores it as one, and the parser returns that. Comparing the raw
        # supplied text would refuse every correct write in the corpus.
        supplied = "A rationale\n   wrapped across lines\n\tand indented."
        self.write_register(_register())
        outcome = self.insert(
            _entry("clm-aa1111", "Anchor Claim", rationale=supplied),
            store.ExpectedEntry(node_id="clm-aa1111", title="Anchor Claim", rigor=0.8, rationale=supplied),
        )
        self.assertEqual(outcome.status, store.Status.WRITTEN)
        record = kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)[0]
        self.assertEqual(record.rationale, "A rationale wrapped across lines and indented.")

    def test_a_framework_dependency_reads_back_under_the_parsers_own_spelling(self):
        # `Axiom 4` is authored, `axiom-4` is parsed. An expectation stated in
        # the authored spelling must still match, or every axiom dependency in
        # the corpus reads back as a mismatch and no write with one succeeds.
        self.write_register(_register())
        outcome = self.insert(
            _entry(
                "clm-aa1111",
                "Anchor Claim",
                depends_on=(render.DependsOnTarget(target="Axiom 4", context="framework input"),),
            ),
            store.ExpectedEntry(
                node_id="clm-aa1111",
                title="Anchor Claim",
                rigor=0.8,
                rationale="Synthetic entry for the store's write path.",
                depends_on=(store.ExpectedEdge(target="Axiom 4", context="framework input"),),
            ),
        )
        self.assertEqual(outcome.status, store.Status.WRITTEN)


# ---------------------------------------------------------------------------
# The staged fan-out is proven like every other field
# ---------------------------------------------------------------------------


class TestStagedSupportsAreProven(_KbCase):
    """A ``sup-`` entry has two grammars, and the readback proves both.

    Its own fields come back through ``parse_support_quality_entries`` and its
    staged ``supports:`` block through ``parse_register_staged_supports``.
    Proving one and not the other would leave the block composed by this API,
    read by a production parser, and checked by nothing in between.
    """

    def _support(self, *, supports, **overrides) -> str:
        values = {
            "quality": 0.8,
            "rationale": "Synthetic support for the store's write path.",
        }
        values.update(overrides)
        return render.render_support_entry(node_id="sup-cc3333", title="A Support", supports=supports, **values)

    def _expected(self, supports) -> store.ExpectedEntry:
        return store.ExpectedEntry(
            node_id="sup-cc3333",
            title="A Support",
            rigor=0.8,
            rationale="Synthetic support for the store's write path.",
            supports=supports,
        )

    def _insert_support(self, entry_text: str, expected: store.ExpectedEntry) -> store.Outcome:
        return store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=lambda text: store.insert_entry(text, entry_text),
                    expect=(expected,),
                    support_delta=1,
                )
            ],
        )

    def test_the_control_writes_and_the_pairs_read_back(self):
        pairs = (("clm-bb2222", 0.5), ("clm-dd4444", None))
        self.write_register(_register())
        outcome = self._insert_support(self._support(supports=pairs), self._expected(pairs))
        self.assertEqual(outcome.status, store.Status.WRITTEN, outcome.detail)
        staged = kb_index_lib.parse_register_staged_supports(self.register)
        self.assertEqual(staged, {"sup-cc3333": [("clm-bb2222", 0.5), ("clm-dd4444", kb_index_lib.PENDING_FRACTION)]})

    def test_a_block_the_reader_cannot_find_is_refused(self):
        # The injection: a renderer that separates the `- supports:` opener from
        # its pairs. The staging reader ends the block at the next sibling
        # bullet, so the fan-out silently becomes empty — every entry field
        # still reads back perfectly, and only the supports comparison sees it.
        pairs = (("clm-bb2222", 0.5),)
        self.write_register(_register())
        before = self.register.read_bytes()
        broken = self._support(supports=pairs).replace("- supports:\n", "- supports:\n- no-edge: interposed\n", 1)
        outcome = self._insert_support(broken, self._expected(pairs))
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.READBACK_MISMATCH)
        self.assertEqual(outcome.subject, f"{self.rel}:sup-cc3333")
        self.assert_untouched(before)

    def test_a_wrong_fraction_is_caught_by_the_same_comparison(self):
        self.write_register(_register())
        before = self.register.read_bytes()
        outcome = self._insert_support(
            self._support(supports=(("clm-bb2222", 0.25),)),
            self._expected((("clm-bb2222", 0.5),)),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.READBACK_MISMATCH)
        self.assert_untouched(before)

    def test_a_pending_fraction_is_not_an_absent_one(self):
        # `*pending*` is an authored value — an intended-but-unassessed lift.
        # An expectation of a pending pair must not be satisfied by a block
        # that lost the pair entirely, which is what a `None`-means-missing
        # normalization would do.
        self.write_register(_register())
        before = self.register.read_bytes()
        outcome = self._insert_support(
            self._support(supports=()),
            self._expected((("clm-bb2222", None),)),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.READBACK_MISMATCH)
        self.assert_untouched(before)


# ---------------------------------------------------------------------------
# The planted census mismatch
# ---------------------------------------------------------------------------


class TestPlantedCensusMismatch(_KbCase):
    """A register already losing entries is refused before anything is composed."""

    def _plant_from_the_frozen_corpus(self, fixture: str) -> None:
        """Copy one register in, read-only material used as-is.

        The frozen corpus carries the defect in its own bytes — every marker
        above its heading — so the first marker in each file binds to nothing
        and the register's record count is one short of its marker count. This
        is the 23 ≠ 29 shape, not a re-enactment of it.
        """
        shutil.copyfile(_FIXTURES / fixture, self.register)

    def test_the_frozen_corpus_registers_carry_the_mismatch(self):
        for fixture in sorted(p.name for p in _FIXTURES.glob("*-claim-quality.md")):
            with self.subTest(fixture=fixture):
                self._plant_from_the_frozen_corpus(fixture)
                census = store.take_census(self.register, self.kb_root)
                self.assertFalse(census.consistent, census.describe())
                self.assertEqual(census.claim_records, census.claim_markers - 1)

    def test_an_insert_into_such_a_register_refuses_naming_the_file(self):
        self._plant_from_the_frozen_corpus("part2-claim-quality.md")
        before = self.register.read_bytes()

        def never(_text: str) -> str:
            raise AssertionError("the splice ran; the census refusal must precede composition")

        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[store.Edit(path=self.rel, splice=never, claim_delta=1)],
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.CENSUS_MISMATCH)
        self.assertEqual(outcome.subject, self.rel)
        self.assert_untouched(before)

    def test_a_healthy_register_passes_its_census(self):
        self.write_register(_register(_entry("clm-aa1111", "A"), _entry("clm-bb2222", "B")))
        census = store.take_census(self.register, self.kb_root)
        self.assertTrue(census.consistent, census.describe())
        self.assertEqual((census.claim_markers, census.claim_records), (2, 2))

    def test_a_register_holding_support_entries_is_not_falsely_refused(self):
        # `parse_claim_quality_file` returns clm- records only, so a census that
        # counted every marker against it would refuse every register with a
        # support entry in it — a false refusal wearing a true check's report.
        self.write_register(
            _register(
                _entry("clm-aa1111", "A Claim"),
                render.render_support_entry(
                    node_id="sup-cc3333", title="A Support", quality=0.9, rationale="Analytical support."
                ),
            )
        )
        census = store.take_census(self.register, self.kb_root)
        self.assertTrue(census.consistent, census.describe())
        self.assertEqual((census.claim_markers, census.support_markers), (1, 1))

    def test_a_healthy_register_binds_every_marker_to_a_heading_of_its_own(self):
        self.write_register(_register(_entry("clm-aa1111", "A"), _entry("clm-bb2222", "B")))
        self.assertEqual(store.take_census(self.register, self.kb_root).misbound, ())

    def test_the_same_defect_one_heading_earlier_is_refused_at_equal_counts(self):
        """One stray H2 above the marker: 2 markers, 2 records.

        Every marker sits above its own heading and therefore binds to the one
        before it — ``clm-111111`` titled 'Preamble', ``clm-222222`` titled
        'Alpha Claim', every title bound to the wrong id. The counts are equal,
        so counting cannot see it; this is the case the frozen corpus would have
        been if its first marker had had any ``## `` heading above it at all.
        """
        self.write_register(
            "# Claim Quality\n\n## Preamble\n\n"
            "<!-- id: clm-111111 -->\n\n## Alpha Claim\n\n### Quality\n- confidence: 0.8\n- rationale: a\n\n---\n\n"
            "<!-- id: clm-222222 -->\n\n## Beta Claim\n\n### Quality\n- confidence: 0.7\n- rationale: b\n"
        )
        before = self.register.read_bytes()

        census = store.take_census(self.register, self.kb_root)
        self.assertEqual((census.claim_markers, census.claim_records), (2, 2))
        self.assertEqual(census.misbound, ("clm-111111", "clm-222222"))
        self.assertFalse(census.consistent)
        self.assertIn("clm-111111", census.describe())

        def never(_text: str) -> str:
            raise AssertionError("the splice ran; the census refusal must precede composition")

        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[store.Edit(path=self.rel, splice=never, claim_delta=1)],
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.CENSUS_MISMATCH)
        self.assertEqual(outcome.subject, self.rel)
        self.assert_untouched(before)

    def test_two_markers_under_one_heading_are_misbound(self):
        # Both records come back, and both carry the same title: one of the two
        # ids is titled with something that was never written for it.
        self.write_register(
            "# R\n\n## One Title\n<!-- id: clm-111111 -->\n<!-- id: clm-222222 -->\n\n"
            "### Quality\n- confidence: 0.5\n- rationale: r\n"
        )
        census = store.take_census(self.register, self.kb_root)
        self.assertEqual((census.claim_markers, census.claim_records), (2, 2))
        self.assertEqual(census.misbound, ("clm-111111", "clm-222222"))

    def test_a_register_that_moves_mid_census_is_retry_not_refusal(self):
        """A census is several opens of one name, and a wave replaces between them.

        The marker scan and the record parse are two different reads of the
        register; a writer that replaces the file between them makes their
        counts describe two different generations, which reads as the file
        losing entries. That must answer retry (8), not refused (7): the
        values were right, nothing was written, re-run unchanged. The second
        writer here lands between the census's two halves, which is where a
        real wave puts it.
        """
        self.write_register(_register(_entry("clm-aa1111", "A")))
        real_parse = kb_index_lib.parse_claim_quality_file
        published: list[bool] = []

        def parse_after_a_concurrent_publish(path, kb_root, *args, **kwargs):
            if Path(path) == self.register and not published:
                published.append(True)
                self.write_register(_register(_entry("clm-aa1111", "A"), _entry("clm-bb2222", "B")))
            return real_parse(path, kb_root, *args, **kwargs)

        with mock.patch.object(kb_index_lib, "parse_claim_quality_file", parse_after_a_concurrent_publish):
            outcome = self.insert(
                _entry("clm-cc3333", "Racer"),
                store.ExpectedEntry(
                    node_id="clm-cc3333",
                    title="Racer",
                    rigor=0.8,
                    rationale="Synthetic entry for the store's write path.",
                ),
            )

        self.assertTrue(published, "the injected writer never ran; this test would pass vacuously")
        self.assertEqual(outcome.status, store.Status.RETRY)
        self.assertEqual(outcome.reason, store.Reason.CONTENDED)
        self.assertEqual(outcome.subject, self.rel)
        self.assertEqual(outcome.written, ())
        self.assertEqual(_temps_under(self.register.parent), [])

    def test_a_marker_inside_a_documentation_fence_is_counted_by_neither_side(self):
        fenced = "# Register\n\n```markdown\n## Example\n<!-- id: clm-ff6666 -->\n```\n"
        self.write_register(store.insert_entry(fenced, _entry("clm-aa1111", "A Claim")))
        census = store.take_census(self.register, self.kb_root)
        self.assertTrue(census.consistent, census.describe())
        self.assertEqual((census.claim_markers, census.claim_records), (1, 1))


# ---------------------------------------------------------------------------
# The forced interleaving
# ---------------------------------------------------------------------------


class TestForcedInterleaving(_KbCase):
    """Two writers, one register, one deterministic ordering."""

    def _insert_by(self, node_id: str, title: str, splice_hook=None) -> store.Outcome:
        entry = _entry(node_id, title)

        def splice(text: str) -> str:
            if splice_hook is not None:
                splice_hook()
            return store.insert_entry(text, entry)

        return store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=splice,
                    expect=(
                        store.ExpectedEntry(
                            node_id=node_id,
                            title=title,
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                )
            ],
        )

    def test_the_loser_retries_and_the_register_holds_exactly_one_new_entry(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        second: list[store.Outcome] = []

        # The whole of writer B runs between writer A's read and A's replace —
        # the ordering a wave of distiller seats produces by accident, forced
        # here through the one seam that already exists.
        first = self._insert_by(
            "clm-bb2222",
            "Writer A Claim",
            splice_hook=lambda: second.append(self._insert_by("clm-cc3333", "Writer B Claim")),
        )

        self.assertEqual(second[0].status, store.Status.WRITTEN)
        # Retry, not refused: A's values were correct and nothing about them can
        # fix this. Re-asking a model for them is how a duplicate id is written.
        self.assertEqual(first.status, store.Status.RETRY)
        self.assertEqual(first.reason, store.Reason.CONTENDED)
        self.assertEqual(first.subject, self.rel)
        self.assertEqual(first.written, ())

        ids = [r.id for r in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)]
        self.assertEqual(ids, ["clm-aa1111", "clm-cc3333"])
        self.assertTrue(store.take_census(self.register, self.kb_root).consistent)
        self.assertEqual(_temps_under(self.register.parent), [])

    def test_the_identical_invocation_succeeds_on_the_retry(self):
        # What the exit-8 contract asks the caller to do: re-run unchanged,
        # never re-author. If a retried write could not then succeed, the
        # contract would be advice rather than a mechanism.
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        first = self._insert_by(
            "clm-bb2222",
            "Writer A Claim",
            splice_hook=lambda: self._insert_by("clm-cc3333", "Writer B Claim"),
        )
        self.assertEqual(first.status, store.Status.RETRY)
        retried = self._insert_by("clm-bb2222", "Writer A Claim")
        self.assertEqual(retried.status, store.Status.WRITTEN)
        ids = [r.id for r in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)]
        self.assertEqual(ids, ["clm-aa1111", "clm-cc3333", "clm-bb2222"])


# ---------------------------------------------------------------------------
# The exclusion the check and replace run under
# ---------------------------------------------------------------------------


class TestTheReplaceRunsUnderAnExclusion(_KbCase):
    """The check and the ``os.replace`` are one critical section.

    The holder here is a second descriptor on the register's own directory,
    taken in this process. That is a real conflict rather than a simulated one:
    a ``flock`` belongs to the *open file description*, not to the process or
    the thread, so a second ``os.open`` of a directory conflicts with the first
    exactly as another process's would — which is also why ``_lock_sites``
    deduplicates, and why the last test below exists.

    What each test rules out is a write that publishes without the exclusion. If
    the replace took no lock, the first test's insert would succeed while the
    holder held; if it took the lock but never waited, the second would fail;
    if it took one lock per edit rather than one per directory, the third
    would deadlock against itself until the timeout.
    """

    def _hold(self) -> int:
        """Hold the register's directory, released at the end of the test."""
        fd = os.open(self.register.parent, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_a_writer_will_not_replace_while_the_directory_is_held(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        before = self.register.read_bytes()
        self._hold()

        # Shortened so the test does not sit out the production wait. The
        # timeout's *value* is a policy about wedged peers; that it is honoured
        # at all is what is under test.
        with mock.patch.object(store, "LOCK_TIMEOUT_SECONDS", 0.2):
            outcome = self.insert(
                _entry("clm-bb2222", "Blocked Claim"),
                store.ExpectedEntry(
                    node_id="clm-bb2222",
                    title="Blocked Claim",
                    rigor=0.8,
                    rationale="Synthetic entry for the store's write path.",
                ),
            )

        # Retry, not refused, and not a fourth code: an unavailable lock is the
        # same answer to the caller as a moved file.
        self.assertEqual(outcome.status, store.Status.RETRY)
        self.assertEqual(outcome.reason, store.Reason.CONTENDED)
        self.assertEqual(outcome.written, ())
        self.assert_untouched(before)

    def test_the_writer_waits_for_the_holder_rather_than_failing_fast(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        fd = self._hold()
        releaser = threading.Timer(0.1, lambda: fcntl.flock(fd, fcntl.LOCK_UN))
        releaser.start()
        self.addCleanup(releaser.join)

        outcome = self.insert(
            _entry("clm-bb2222", "Patient Claim"),
            store.ExpectedEntry(
                node_id="clm-bb2222",
                title="Patient Claim",
                rigor=0.8,
                rationale="Synthetic entry for the store's write path.",
            ),
        )

        self.assertEqual(outcome.status, store.Status.WRITTEN)
        ids = [r.id for r in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)]
        self.assertEqual(ids, ["clm-aa1111", "clm-bb2222"])

    def test_a_batch_touching_two_files_in_one_directory_does_not_block_itself(self):
        second_rel = "part1/support-quality.md"
        second = self.kb_root / second_rel
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        second.write_text(_register(_entry("clm-dd4444", "Neighbour Claim")), encoding="utf-8")

        def edit(rel: str, node_id: str, title: str) -> store.Edit:
            entry = _entry(node_id, title)
            return store.Edit(
                path=rel,
                splice=lambda text: store.insert_entry(text, entry),
                expect=(
                    store.ExpectedEntry(
                        node_id=node_id,
                        title=title,
                        rigor=0.8,
                        rationale="Synthetic entry for the store's write path.",
                    ),
                ),
                claim_delta=1,
            )

        with mock.patch.object(store, "LOCK_TIMEOUT_SECONDS", 0.2):
            outcome = store.apply_edits(
                kb_root=self.kb_root,
                edits=[edit(self.rel, "clm-bb2222", "First Claim"), edit(second_rel, "clm-cc3333", "Second Claim")],
            )

        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertEqual(outcome.written, (self.rel, second_rel))


class TestOrphanTempsAreSweptByTheNextWriter(_KbCase):
    """A killed writer's temp is litter, and the next writer under the lock takes it out.

    There is no signal handler and no reaper, deliberately: correctness never
    depends on cleanup running at death — a ``SIGKILL``
    leaves the target holding either its prior content or a proven candidate
    whatever becomes of the temps — and cleanup that has to run in a dying
    process is cleanup that a ``SIGKILL`` skips. The sweep survives because the
    *next* writer performs it.

    The planted orphan below is spelled through the module's own naming so the
    test cannot drift from the writer: a hand-typed name would keep passing
    after the writer's changed.
    """

    def _plant_orphan(self) -> Path:
        orphan = self.register.parent / f"{store._temp_prefix(self.register)}deadbeef{store.TEMP_SUFFIX}"
        orphan.write_text("half a candidate from a process that died\n", encoding="utf-8")
        # Not vacuous: every assertion below is that this file went away.
        self.assertTrue(orphan.exists())
        return orphan

    def _expected(self, node_id: str, title: str) -> store.ExpectedEntry:
        return store.ExpectedEntry(
            node_id=node_id,
            title=title,
            rigor=0.8,
            rationale="Synthetic entry for the store's write path.",
        )

    def test_a_successful_write_sweeps_it(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        orphan = self._plant_orphan()
        outcome = self.insert(_entry("clm-bb2222", "New Claim"), self._expected("clm-bb2222", "New Claim"))
        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertFalse(orphan.exists())
        self.assertEqual(_temps_under(self.register.parent), [])

    def test_a_refused_write_sweeps_it_too(self):
        # The sweep runs ahead of the proof, so it is unconditional on the
        # outcome: a register that refuses every write must not accumulate the
        # litter of every crash before it.
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        before = self.register.read_bytes()
        orphan = self._plant_orphan()
        outcome = self.insert(
            _marker_above_heading("clm-bb2222", "New Claim"),
            self._expected("clm-bb2222", "New Claim"),
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertFalse(orphan.exists())
        self.assert_untouched(before)

    def test_it_sweeps_the_temp_shape_and_nothing_else(self):
        """Both ends of the name are pinned, and the sweep stays on this target.

        Everything planted here shares the register's directory and some part of
        the temp's shape. Only the file carrying *both* the target's publish
        prefix and the temp suffix is this module's to delete — the rest are a
        neighbouring document, an author's own dotfile, another register's temp,
        and the shape ``ops._proven`` writes its proof temps in, which is live
        while its splice runs and is not covered by this lock.
        """
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        keep = {
            "a neighbour document": self.register.parent / "notes.md",
            "an author's dotfile": self.register.parent / ".claim-quality.md.swp",
            "a suffix-only match": self.register.parent / f"unrelated{store.TEMP_SUFFIX}",
            "another target's temp": self.register.parent / f".other.md.{store.PUBLISH_MARK}.z{store.TEMP_SUFFIX}",
            "a proof temp": self.register.parent / f".{self.register.name}.z{store.TEMP_SUFFIX}",
        }
        for path in keep.values():
            path.write_text("not the write API's to delete\n", encoding="utf-8")
        orphan = self._plant_orphan()

        outcome = self.insert(_entry("clm-bb2222", "New Claim"), self._expected("clm-bb2222", "New Claim"))

        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertFalse(orphan.exists())
        for description, path in keep.items():
            self.assertTrue(path.exists(), f"the sweep deleted {description}")


# ---------------------------------------------------------------------------
# Path containment and encoding
# ---------------------------------------------------------------------------


class TestBoundaries(_KbCase):
    """The three boundaries this API introduces, refused cheaply and always."""

    def _apply(self, rel_path: str, **kwargs) -> store.Outcome:
        def never(_text: str) -> str:
            raise AssertionError("a contained target was composed for an uncontained path")

        return store.apply_edits(kb_root=self.kb_root, edits=[store.Edit(path=rel_path, splice=never, **kwargs)])

    def test_a_traversal_path_is_refused_unread(self):
        outside = self.kb_root.parent / "escape.md"
        outside.write_text("untouched\n", encoding="utf-8")
        outcome = self._apply("../escape.md")
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.PATH_OUTSIDE_ROOT)
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_a_symlink_reaching_out_of_kb_root_is_refused(self):
        outside = self.kb_root.parent / "secret.md"
        outside.write_text("untouched\n", encoding="utf-8")
        (self.kb_root / "link.md").symlink_to(outside)
        outcome = self._apply("link.md")
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.PATH_OUTSIDE_ROOT)
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_an_absolute_path_is_refused(self):
        outcome = self._apply("/etc/hosts")
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.PATH_OUTSIDE_ROOT)

    def test_the_root_itself_is_refused_before_anything_is_written_outside_it(self):
        """The degenerate path, and where a temp for it would land.

        ``Path.is_relative_to`` is reflexive, so ``"."`` and ``"common/.."``
        must not be allowed to pass containment — a temp for either would land
        beside the *root*, which is the KB's parent directory. The refusal is
        what is under test, and the directory listing is what says the refusal
        happened early enough: "refused unread and unwritten" means a temp
        outside the root is not unwritten however briefly it lived.
        """
        outside = sorted(p.name for p in self.kb_root.parent.iterdir())
        for value in (".", "common/..", ""):
            with self.subTest(path=value):
                outcome = self._apply(value, create=True)
                self.assertEqual(outcome.status, store.Status.REFUSED)
                self.assertEqual(outcome.reason, store.Reason.PATH_OUTSIDE_ROOT)
                self.assertEqual(outcome.subject, value)
                self.assertEqual(sorted(p.name for p in self.kb_root.parent.iterdir()), outside)

    def test_a_path_value_carrying_a_nul_is_refused_rather_than_raising(self):
        # `Path.resolve` raises ValueError on an embedded NUL, which this
        # refuses rather than letting propagate. The subject is escaped: a
        # report line is text.
        outcome = self._apply("part1\x00/claim-quality.md", create=True)
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.PATH_OUTSIDE_ROOT)
        self.assertEqual(outcome.subject, "part1\\x00/claim-quality.md")
        self.assertNotIn("\x00", outcome.subject)

    def test_a_path_naming_an_existing_directory_is_refused_naming_the_value(self):
        # Not "could not be written under: Is a directory" from the replace —
        # an environment report for a value defect, which also never named the
        # value that caused it.
        outcome = self._apply("part1", create=True)
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.REGISTER_ABSENT)
        self.assertEqual(outcome.subject, "part1")

    def test_a_register_that_does_not_decode_as_utf8_is_refused(self):
        # Written as bytes deliberately: the point of the fixture is that it is
        # not decodable text. A strict decode refuses it; `errors="replace"`
        # would put a substitution character into a file this API then claims
        # to have proven.
        self.register.write_bytes(b"# Register\n\xff\xfe not utf-8\n")
        outcome = self._apply(self.rel)
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.NOT_UTF8)
        self.assertEqual(outcome.subject, self.rel)

    def test_a_nonexistent_register_is_refused_naming_the_path(self):
        outcome = self._apply("part9/claim-quality.md")
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.REGISTER_ABSENT)
        self.assertEqual(outcome.subject, "part9/claim-quality.md")
        self.assertFalse((self.kb_root / "part9").exists())

    def test_creation_is_a_distinct_path_the_caller_opts_into(self):
        entry = _entry("clm-aa1111", "First Claim")
        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=lambda text: store.insert_entry(text, entry),
                    expect=(
                        store.ExpectedEntry(
                            node_id="clm-aa1111",
                            title="First Claim",
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                    create=True,
                )
            ],
        )
        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertTrue(store.take_census(self.register, self.kb_root).consistent)

    def test_creation_makes_a_file_and_never_a_directory_tree(self):
        # A typo'd domain directory must not be conjured into existence by the
        # acknowledgment that was added to stop typos succeeding.
        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[store.Edit(path="prt9/claim-quality.md", splice=lambda text: text, create=True)],
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.REGISTER_ABSENT)
        self.assertFalse((self.kb_root / "prt9").exists())

    def test_one_file_named_twice_in_a_batch_is_refused(self):
        # Both edits splice from the same baseline, so the second replace would
        # silently drop the first entry — the losing-a-record class, arriving
        # through the batch rather than through a concurrent process.
        self.write_register(_register(_entry("clm-aa1111", "A")))
        edits = [
            store.Edit(path=self.rel, splice=lambda text: text),
            store.Edit(path="./part1/claim-quality.md", splice=lambda text: text),
        ]
        outcome = store.apply_edits(kb_root=self.kb_root, edits=edits)
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.reason, store.Reason.DUPLICATE_TARGET)

    def test_an_empty_batch_is_a_usage_error_not_an_outcome(self):
        with self.assertRaises(ValueError):
            store.apply_edits(kb_root=self.kb_root, edits=[])


# ---------------------------------------------------------------------------
# All-or-nothing per invocation
# ---------------------------------------------------------------------------


class TestBatchIsAllOrNothing(_KbCase):
    """A batch that refuses any entry writes none."""

    def setUp(self) -> None:
        super().setUp()
        self.second_rel = "part2/claim-quality.md"
        self.second = self.kb_root / self.second_rel
        self.second.parent.mkdir(parents=True)

    def test_a_refusal_on_the_second_file_leaves_the_first_unwritten(self):
        self.write_register(_register())
        self.second.write_text(_register(), encoding="utf-8")
        before = (self.register.read_bytes(), self.second.read_bytes())

        good = _entry("clm-aa1111", "Good Claim")
        bad = _marker_above_heading("clm-bb2222", "Broken Claim")
        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=lambda text: store.insert_entry(text, good),
                    expect=(
                        store.ExpectedEntry(
                            node_id="clm-aa1111",
                            title="Good Claim",
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                ),
                store.Edit(
                    path=self.second_rel,
                    splice=lambda text: store.insert_entry(text, bad),
                    expect=(
                        store.ExpectedEntry(
                            node_id="clm-bb2222",
                            title="Broken Claim",
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                ),
            ],
        )
        self.assertEqual(outcome.status, store.Status.REFUSED)
        self.assertEqual(outcome.written, ())
        self.assertEqual((self.register.read_bytes(), self.second.read_bytes()), before)
        self.assertEqual(_temps_under(self.register.parent), [])
        self.assertEqual(_temps_under(self.second.parent), [])

    def test_a_replace_that_fails_midway_names_what_landed_and_what_did_not(self):
        """No half-*refusal*: a half-*interrupted* batch says so.

        Every verdict on the values is reached before the first replace, so the
        only thing that can still fail partway is the filesystem — ENOSPC, EIO,
        a quota, a permission change on one directory of several. Multi-file
        atomicity would need a journal, which this program does not keep, and
        cannot be had across directories anyway, so the fix is the accounting:
        what already landed and what did not are both reported.
        """
        self.write_register(_register())
        self.second.write_text(_register(), encoding="utf-8")
        untouched = self.second.read_bytes()
        real_replace = os.replace
        replaced: list[Path] = []

        def flaky(src, dst):
            replaced.append(Path(dst))
            if len(replaced) > 1:
                raise PermissionError(13, "Permission denied")
            return real_replace(src, dst)

        edits = [
            self._insert_edit(self.rel, "clm-aa1111", "First Claim"),
            self._insert_edit(self.second_rel, "clm-bb2222", "Second Claim"),
        ]
        with mock.patch.object(store.os, "replace", flaky):
            with self.assertRaises(store.BatchInterrupted) as caught:
                store.apply_edits(kb_root=self.kb_root, edits=edits)

        interrupted = caught.exception
        # An OSError by inheritance, so a caller's environment handler still
        # catches it and the exit code stays the environment's.
        self.assertIsInstance(interrupted, OSError)
        self.assertEqual(interrupted.written, (self.rel,))
        self.assertEqual(interrupted.failed, self.second_rel)
        self.assertIn(self.rel, str(interrupted))
        self.assertIn(self.second_rel, str(interrupted))

        # And the accounting is true of the disk, not just of the report.
        ids = [r.id for r in kb_index_lib.parse_claim_quality_file(self.register, self.kb_root)]
        self.assertEqual(ids, ["clm-aa1111"])
        self.assertEqual(self.second.read_bytes(), untouched)
        self.assertEqual(_temps_under(self.register.parent), [])
        self.assertEqual(_temps_under(self.second.parent), [])

    def test_a_batch_whose_first_replace_fails_reports_that_nothing_landed(self):
        self.write_register(_register())
        before = self.register.read_bytes()
        with mock.patch.object(store.os, "replace", mock.Mock(side_effect=OSError(28, "No space left on device"))):
            with self.assertRaises(store.BatchInterrupted) as caught:
                store.apply_edits(kb_root=self.kb_root, edits=[self._insert_edit(self.rel, "clm-aa1111", "A")])
        self.assertEqual(caught.exception.written, ())
        self.assertEqual(caught.exception.failed, self.rel)
        self.assert_untouched(before)

    def _insert_edit(self, rel: str, node_id: str, title: str) -> store.Edit:
        entry = _entry(node_id, title)
        return store.Edit(
            path=rel,
            splice=lambda text: store.insert_entry(text, entry),
            expect=(
                store.ExpectedEntry(
                    node_id=node_id,
                    title=title,
                    rigor=0.8,
                    rationale="Synthetic entry for the store's write path.",
                ),
            ),
            claim_delta=1,
        )

    def test_a_clean_batch_writes_every_file_it_names(self):
        self.write_register(_register())
        self.second.write_text(_register(), encoding="utf-8")
        edits = []
        for rel, node_id, title in (
            (self.rel, "clm-aa1111", "First Claim"),
            (self.second_rel, "clm-bb2222", "Second Claim"),
        ):
            entry = _entry(node_id, title)
            edits.append(
                store.Edit(
                    path=rel,
                    splice=(lambda body: lambda text: store.insert_entry(text, body))(entry),
                    expect=(
                        store.ExpectedEntry(
                            node_id=node_id,
                            title=title,
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                )
            )
        outcome = store.apply_edits(kb_root=self.kb_root, edits=edits)
        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertEqual(sorted(outcome.written), [self.rel, self.second_rel])


# ---------------------------------------------------------------------------
# The splice primitives
# ---------------------------------------------------------------------------


class TestLocateAndSplice(unittest.TestCase):
    """Location follows the reader's grammar; splicing disturbs nothing else."""

    def test_a_marker_binds_to_the_heading_above_it(self):
        document = _register(_entry("clm-aa1111", "A"), _entry("clm-bb2222", "B"))
        located = store.locate_entries(document)
        self.assertEqual([e.node_id for e in located], ["clm-aa1111", "clm-bb2222"])
        lines = document.splitlines()
        for entry in located:
            self.assertTrue(lines[entry.heading_line].startswith("## "))
            self.assertLess(entry.heading_line, entry.marker_line)
            self.assertEqual(lines[entry.quality_start - 1].strip(), "### Quality")

    def test_a_marker_with_no_heading_above_it_locates_nothing(self):
        # The reader's behaviour, reproduced rather than corrected: this is
        # what makes the census able to see a record the file has lost.
        document = "# Register\n\n" + _marker_above_heading("clm-aa1111", "A") + "\n"
        self.assertEqual(store.locate_entries(document), ())

    def test_an_insert_does_not_disturb_the_entry_above_it(self):
        first = _register(_entry("clm-aa1111", "A"))
        second = store.insert_entry(first, _entry("clm-bb2222", "B"))
        self.assertTrue(second.startswith(first.rstrip()))

    def test_an_insert_reuses_a_trailing_rule_rather_than_doubling_it(self):
        document = store.insert_entry("# Register\n\n---\n", _entry("clm-aa1111", "A"))
        self.assertNotIn("---\n\n---", document)

    def test_a_field_replacement_leaves_the_derived_solidity_line_untouched(self):
        # Rewriting a whole `### Quality`
        # block would reset a refresh-computed solidity to its placeholder,
        # which refresh then reports as drift and verify fails on.
        computed = "- solidity: 0.72 (ok to build on, see caveats) [= min(0.80, 0.90)]"
        document = _register(_entry("clm-aa1111", "A")).replace(render.SOLIDITY_PENDING_LINE, computed)
        after = store.replace_field_line(
            document, node_id="clm-aa1111", field_name="confidence", line="- confidence: 0.4"
        )
        self.assertIn(computed, after)
        record = self._parse(after)
        self.assertEqual(record.confidence, 0.4)
        self.assertEqual(record.solidity, 0.72)

    def test_a_rationale_replacement_removes_the_span_the_reader_folds(self):
        # A hand-authored rationale wrapped over several lines: replacing only
        # its first line would strand the continuations for the next parse to
        # fold into the new value.
        document = _register(_entry("clm-aa1111", "A")).replace(
            "- rationale: Synthetic entry for the store's write path.",
            "- rationale: first line\n  second line\n  third line",
        )
        after = store.replace_field_line(
            document, node_id="clm-aa1111", field_name="rationale", line="- rationale: replaced."
        )
        self.assertNotIn("second line", after)
        self.assertEqual(self._parse(after).rationale, "replaced.")

    def test_a_list_valued_field_is_refused_by_name(self):
        document = _register(_entry("clm-aa1111", "A"))
        with self.assertRaises(store.SpliceError):
            store.replace_field_line(document, node_id="clm-aa1111", field_name="depends-on", line="- depends-on:")

    def test_an_absent_entry_or_field_raises_rather_than_inserting(self):
        document = _register(_entry("clm-aa1111", "A"))
        with self.assertRaises(store.SpliceError):
            store.replace_field_line(document, node_id="clm-zz9999", field_name="confidence", line="- confidence: 0.1")
        with self.assertRaises(store.SpliceError):
            store.replace_field_line(document, node_id="clm-aa1111", field_name="quality", line="- quality: 0.1")

    def _parse(self, document: str) -> kb_index_lib.ClaimEntry:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            path = root / "claim-quality.md"
            path.write_text(document, encoding="utf-8")
            return kb_index_lib.parse_claim_quality_file(path, root)[0]


# ---------------------------------------------------------------------------
# Line fidelity: the lines a splice did not name
# ---------------------------------------------------------------------------


#: One prose line carrying three separators that ``str.splitlines`` breaks on
#: and ``"\n".join`` cannot put back: a form feed, U+2028 and U+0085. They are
#: not exotica for their own sake — a rebuild-the-document splice would
#: silently turn each into a newline in an author's leaf body.
_EXOTIC_PROSE = "A paragraph with a form feed \x0c a U+2028   and a U+0085 \x85 inside it."


def _crlf_register() -> str:
    """A CRLF register whose entry carries a line of :data:`_EXOTIC_PROSE`."""
    lines = _register(_entry("clm-aa1111", "Anchor Claim")).splitlines()
    marker = next(i for i, line in enumerate(lines) if line.strip().startswith("<!-- id:"))
    lines[marker + 1 : marker + 1] = ["", _EXOTIC_PROSE]
    return "\r\n".join(lines) + "\r\n"


def _changed_lines(before: str, after: str) -> list[int]:
    """The indices at which two documents differ, line by line and byte by byte.

    ``keepends``, so a line whose *terminator* changed counts as changed. That
    is the whole point: every measurement here is of terminators, and a
    comparison that dropped them would report the corrupting splice as clean.
    """
    old = before.splitlines(keepends=True)
    new = after.splitlines(keepends=True)
    if len(old) != len(new):
        return [i for i in range(max(len(old), len(new)))]
    return [i for i, (a, b) in enumerate(zip(old, new)) if a != b]


class TestUntouchedLinesKeepTheirBytes(unittest.TestCase):
    """A splice rewrites its span and nothing else — bytes, not line contents.

    A splice that rebuilds the whole document as
    ``"\\n".join(document.splitlines())`` reads as a no-op and is not:
    ``splitlines`` breaks on ``\\r``, ``\\x0c``, U+2028 and U+0085 among others,
    and the join emits ``\\n`` for each, silently rewriting every line
    terminator in the file.

    Neither the readback nor the retry check can see this — the parsers
    normalize whitespace, so the readback compares equal, and the baseline is
    read before the splice runs — which is why the assertion here is on bytes
    and not on a re-parse.
    """

    def test_a_field_replacement_changes_exactly_one_line_of_a_crlf_document(self):
        document = _crlf_register()
        after = store.replace_field_line(
            document, node_id="clm-aa1111", field_name="confidence", line="- confidence: 0.4"
        )
        changed = _changed_lines(document, after)
        self.assertEqual(len(changed), 1, f"lines {changed} changed")
        self.assertEqual(after.splitlines(keepends=True)[changed[0]], "- confidence: 0.4\r\n")

    def test_no_separator_the_author_wrote_is_converted_to_a_newline(self):
        document = _crlf_register()
        after = store.replace_field_line(
            document, node_id="clm-aa1111", field_name="rationale", line="- rationale: replaced."
        )
        for name, char in (("CR", "\r"), ("form feed", "\x0c"), ("U+2028", " "), ("U+0085", "\x85")):
            with self.subTest(separator=name):
                self.assertEqual(after.count(char), document.count(char), f"{name} count moved")
        self.assertIn(_EXOTIC_PROSE, after)

    def test_a_replacement_line_takes_the_terminator_it_displaces(self):
        # Not "\n" into a CRLF document: one file in two conventions is a diff
        # nobody asked for and a `git diff --stat` nobody can read.
        after = store.replace_field_line(
            _crlf_register(), node_id="clm-aa1111", field_name="confidence", line="- confidence: 0.4"
        )
        self.assertIn("- confidence: 0.4\r\n", after)
        self.assertNotIn("- confidence: 0.4\n\n", after)

    def test_an_insert_leaves_every_prior_byte_in_place(self):
        document = _crlf_register()
        after = store.insert_entry(document, _entry("clm-bb2222", "Second Claim"))
        self.assertTrue(after.startswith(document))
        self.assertNotIn("\n\n", after.replace("\r\n", "\r"))

    def test_a_frontmatter_field_replacement_keeps_the_block_it_did_not_write(self):
        document = (
            "[up](index.md)\r\n\r\n<!-- kb-frontmatter\r\nkind: leaf\r\n"
            "subtree-claims: [clm-aa1111]\r\npath-stable: y\r\n-->\r\n\r\n# Leaf\r\n\r\n" + _EXOTIC_PROSE + "\r\n"
        )
        after = store.replace_or_insert_frontmatter_field(
            document, field="subtree-claims", ids=["clm-bb2222"], anchor_prefix="kind:"
        )
        changed = _changed_lines(document, after)
        self.assertEqual(len(changed), 1, f"lines {changed} changed")
        self.assertIn("subtree-claims: [clm-bb2222]\r\n", after)
        self.assertEqual(after.count("\r"), document.count("\r"))

    def test_a_field_inserted_after_the_blocks_last_line_is_a_line_of_its_own(self):
        """The block's last line carries no terminator; a new neighbour needs one.

        Gluing the new line onto the block's last line would read back as
        ``subtree-claims`` holding the *experiments* value, so refresh would
        report one field with an extra id and the other missing — a drift no
        amount of re-running could clear. Caught by ``test_leaf_references``'
        verify gate, not by this file.
        """
        document = "<!-- kb-frontmatter\nkind: index\nsubtree-claims: [clm-aa1111]\n-->\n\n# Index\n"
        after = store.replace_or_insert_frontmatter_field(
            document, field="subtree-experiments", ids=["exp-ee1111"], anchor_prefix="subtree-claims:"
        )
        self.assertIn("subtree-claims: [clm-aa1111]\nsubtree-experiments: [exp-ee1111]\n-->", after)

    def test_an_inserted_frontmatter_field_lands_without_re_terminating_the_block(self):
        document = "<!-- kb-frontmatter\r\nkind: leaf\r\npath-stable: y\r\n-->\r\n\r\n# Leaf\r\n"
        after = store.replace_or_insert_frontmatter_field(
            document, field="subtree-claims", ids=["clm-aa1111"], anchor_prefix="kind:"
        )
        self.assertEqual(after.count("\r"), document.count("\r") + 1)
        self.assertIn("kind: leaf\r\nsubtree-claims: [clm-aa1111]\r\npath-stable: y\r\n-->", after)

    def test_the_whole_write_path_publishes_the_bytes_the_splice_composed(self):
        # End to end, because `_write_temp` and `_read_text` are as able to
        # translate a newline as a splice is: with translation on anywhere in
        # the chain, the bytes proven and the bytes published are not the same.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            register = root / "part1" / "claim-quality.md"
            register.parent.mkdir(parents=True)
            register.write_bytes(_crlf_register().encode("utf-8"))
            before = register.read_bytes()

            outcome = store.apply_edits(
                kb_root=root,
                edits=[
                    store.Edit(
                        path="part1/claim-quality.md",
                        splice=lambda text: store.replace_field_line(
                            text, node_id="clm-aa1111", field_name="confidence", line="- confidence: 0.4"
                        ),
                        expect=(
                            store.ExpectedEntry(
                                node_id="clm-aa1111",
                                title="Anchor Claim",
                                rigor=0.4,
                                rationale="Synthetic entry for the store's write path.",
                            ),
                        ),
                    )
                ],
            )

            self.assertEqual(outcome.status, store.Status.WRITTEN, outcome.detail)
            after = register.read_bytes()
            self.assertEqual(after.count(b"\r"), before.count(b"\r"))
            self.assertEqual(len(_changed_lines(before.decode(), after.decode())), 1)


class TestSpliceLines(unittest.TestCase):
    """The primitive's own edges, stated once here rather than per caller."""

    def test_a_replacement_puts_back_what_it_removed_when_it_is_the_same(self):
        document = "a\r\nb\r\nc\r\n"
        self.assertEqual(store.splice_lines(document, start=1, end=2, lines=["b"]), document)

    def test_a_document_with_no_trailing_newline_does_not_gain_one(self):
        self.assertEqual(store.splice_lines("a\nb", start=1, end=2, lines=["B"]), "a\nB")

    def test_an_append_past_an_unterminated_last_line_terminates_it(self):
        self.assertEqual(store.splice_lines("a", start=1, end=1, lines=["b"]), "a\nb")

    def test_an_insert_carries_the_terminator_of_the_line_it_displaces(self):
        self.assertEqual(store.splice_lines("a\r\nb\r\n", start=1, end=1, lines=["x"]), "a\r\nx\r\nb\r\n")

    def test_a_line_the_reader_splits_on_is_a_line_here_too(self):
        # `splitlines` breaks on the form feed, so the reader counts three lines
        # in this document and so must the splice, or an index means two
        # different places to the locator and to the editor.
        document = "a\x0cb\nc\n"
        self.assertEqual(store.splice_lines(document, start=2, end=3, lines=["C"]), "a\x0cb\nC\n")

    def test_a_range_outside_the_document_is_a_usage_error(self):
        with self.assertRaises(ValueError):
            store.splice_lines("a\n", start=0, end=9, lines=[])


# ---------------------------------------------------------------------------
# The by-name exclusion, kept honest
# ---------------------------------------------------------------------------


class TestComparisonSurfaceIsTotal(unittest.TestCase):
    """Every field of a parsed record is compared or named as excluded.

    ``store.py`` takes the by-name exclusion, stated in code rather than left
    implicit, and this is what stops that exclusion from quietly growing: a
    field added to ``ClaimEntry`` lands in neither tuple and fails here,
    forcing the choice to be made rather than defaulted.
    """

    def test_the_three_tuples_partition_claim_entry(self):
        declared = {f.name for f in dataclasses.fields(kb_index_lib.ClaimEntry)}
        named = set(store.COMPARED_FIELDS) | set(store.PATH_DERIVED_FIELDS) | set(store.DERIVED_FIELDS)
        self.assertEqual(declared, named)

    def test_the_three_tuples_do_not_overlap(self):
        groups = (store.COMPARED_FIELDS, store.PATH_DERIVED_FIELDS, store.DERIVED_FIELDS)
        self.assertEqual(sum(len(g) for g in groups), len({name for g in groups for name in g}))

    def test_the_declared_map_is_what_the_comparison_reads(self):
        """The guard must not certify a tuple while a literal does the comparing.

        A field could be added to ``ClaimEntry``, named in ``COMPARED_FIELDS``,
        and never looked at — with the partition test above green and asserting
        that it was compared, which is worse than having no guard. Binding the
        two means asking the comparison what it does when the map changes.
        """
        expected = store.ExpectedEntry(node_id="clm-aa1111", title="Wanted", rigor=0.8, rationale="Wanted rationale.")
        read = store._Read(
            title="Written",
            rigor=0.8,
            rationale="Written rationale.",
            depends_on=(),
            references=(),
            strengthen_by=(),
            supports=(),
        )
        self.assertEqual(store._mismatched_fields(expected, read), ["title", "rationale"])

        with mock.patch.dict(store.COMPARED_FIELD_READS, {"title": "title"}, clear=True):
            self.assertEqual(store._mismatched_fields(expected, read), ["title"])

    def test_every_compared_name_is_a_field_of_the_record_the_readback_builds(self):
        # The other half of the totality claim: a map entry naming an attribute
        # `_Read` does not carry would raise at comparison time, and a `_Read`
        # field no entry names would be read back and never checked.
        declared = {f.name for f in dataclasses.fields(store._Read)}
        compared = {*store.COMPARED_FIELD_READS.values(), *store.COMPARED_NON_ENTRY_READS}
        self.assertEqual(declared, compared)

    def test_the_groups_partition_external_work(self):
        # The same guard over the other record the readback proves. A work is
        # the simpler object — three authored fields, none of them a list — and
        # it carries no derived field at all, nothing computing a value for a
        # terminal node, so `DERIVED_FIELDS` has no part in this partition.
        declared = {f.name for f in dataclasses.fields(kb_index_lib.ExternalWork)}
        named = set(store.COMPARED_WORK_FIELDS) | set(store.PATH_DERIVED_FIELDS) | set(store.WORK_ID_DERIVED_FIELDS)
        self.assertEqual(declared, named)
        self.assertEqual(set(store.COMPARED_WORK_FIELDS) & set(store.DERIVED_FIELDS), set())

    def test_the_declared_work_map_is_what_the_work_comparison_reads(self):
        """A work's readback is built through its map, not beside it.

        The same binding the claim map carries: a field named compared and
        never looked at is worse than an unguarded one, because the partition
        above then asserts a proof that does not run.
        """
        work = kb_index_lib.ExternalWork(
            id="work-nobody2026",
            key="nobody2026",
            title="Written",
            canonical_path="part1/claim-quality.md",
            canonical_anchor="written",
            strength=0.4,
            rationale="Written rationale.",
        )
        read = store._work_read(work)
        for field, attribute in store.COMPARED_WORK_FIELD_READS.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(read, attribute), getattr(work, field))
        # The three the map names none of: a work is terminal and no leaf hosts
        # it, so an expectation stating any of them is a mismatch rather than an
        # ignored value.
        self.assertEqual((read.depends_on, read.strengthen_by, read.supports), ((), (), ()))
        expected = store.ExpectedEntry(node_id=work.id, title="Wanted", rigor=0.4, rationale="Wanted rationale.")
        self.assertEqual(store._mismatched_fields(expected, read), ["title", "rationale"])

    def test_no_derived_field_can_be_supplied_through_an_expectation(self):
        # Stated structurally: the API cannot compare a derived field
        # because it has no field to carry one in.
        supplied = {f.name for f in dataclasses.fields(store.ExpectedEntry)}
        self.assertEqual(supplied & set(store.DERIVED_FIELDS), set())


class TestTheTempIsInvisibleToBothKbWalks(_KbCase):
    """A real temp, put in front of the real walks.

    The temp is written inside ``kb-root/``, where two walks are
    looking: ``kb_index_lib.kb_files`` globs ``*.md`` and the register scan
    globs the exact name ``claim-quality.md``. A temp either walk could see
    would be read as authored content by a concurrent reader, and every id in
    it would answer this API's own uniqueness question wrong — a duplicate
    register entry manufactured by the write path's own scratch file. Asserting
    on a hand-typed name would prove nothing about the walks; this writes one
    through the module's own temp writer and asks them.
    """

    def test_neither_walk_sees_a_live_temp(self):
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        temp = store._write_temp(self.register, _register(_entry("clm-zz9999", "Scratch Claim")))
        self.addCleanup(temp.unlink, True)

        self.assertNotIn(temp, list(kb_index_lib.kb_files(self.kb_root)))
        inventory = kb_index_lib.scan_authored_ids(self.kb_root)
        self.assertIn("clm-aa1111", inventory)
        self.assertNotIn("clm-zz9999", inventory)
        self.assertEqual(kb_index_lib.scan_duplicate_register_ids(self.kb_root), {})


class TestTheProofTempSeam(_KbCase):
    """A candidate on disk for a caller to parse, held and reapable.

    A splice that must parse what it composed needs a real path — the production
    parsers derive a node's identity from the file's own ``relative_to(kb_root)``
    — so it writes a temp inside ``kb-root/``. Without holding the target's
    directory for the life of the proof, such a temp cannot be told from a
    living writer's, so nothing could ever sweep one and it accumulates as
    permanent litter. Taking the directory is what supplies the missing
    argument, and it is the same one the publish temp's sweep already rests on.
    """

    def test_the_temp_holds_the_bytes_it_was_given_and_is_gone_afterwards(self):
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        candidate = _register(_entry("clm-aa1111", "A Claim"), _entry("clm-bb2222", "B Claim"))
        with store.proof_temp(self.register, candidate) as temp:
            self.assertEqual(temp.read_text(encoding="utf-8"), candidate)
            self.assertEqual(temp.parent, self.register.parent)
            ids = [r.id for r in kb_index_lib.parse_claim_quality_file(temp, self.kb_root)]
            self.assertEqual(ids, ["clm-aa1111", "clm-bb2222"])
        self.assertFalse(temp.exists())
        self.assertEqual(_temps_under(self.register.parent), [])

    def test_a_crlf_candidate_is_proven_over_the_bytes_a_publish_would_write(self):
        # `newline=""`, like the publish temp: with translation on, the bytes
        # proven and the bytes published are not the same bytes.
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        candidate = "# R\r\n\r\n## A\r\n"
        with store.proof_temp(self.register, candidate) as temp:
            self.assertEqual(temp.read_bytes(), candidate.encode("utf-8"))

    def test_neither_kb_walk_sees_it_while_it_lives(self):
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        with store.proof_temp(self.register, _register(_entry("clm-zz9999", "Scratch Claim"))) as temp:
            self.assertNotIn(temp, list(kb_index_lib.kb_files(self.kb_root)))
            self.assertNotIn("clm-zz9999", kb_index_lib.scan_authored_ids(self.kb_root))
            self.assertEqual(kb_index_lib.scan_duplicate_register_ids(self.kb_root), {})

    def test_the_directory_is_held_for_the_life_of_the_temp(self):
        # The lock is the whole reason the sweep below is allowed to exist: it
        # is what makes "a proof temp of this target, seen under this lock" mean
        # "left by a process that died".
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        with store.proof_temp(self.register, "# R\n"):
            fd = os.open(self.register.parent, os.O_RDONLY)
            self.addCleanup(os.close, fd)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_an_orphaned_proof_temp_is_swept_by_the_next_writer(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        orphan = self.register.parent / f"{store._temp_prefix(self.register, store.PROOF_MARK)}dead{store.TEMP_SUFFIX}"
        orphan.write_text("a candidate nobody will ever parse\n", encoding="utf-8")

        outcome = store.apply_edits(
            kb_root=self.kb_root,
            edits=[
                store.Edit(
                    path=self.rel,
                    splice=lambda text: store.insert_entry(text, _entry("clm-bb2222", "New Claim")),
                    expect=(
                        store.ExpectedEntry(
                            node_id="clm-bb2222",
                            title="New Claim",
                            rigor=0.8,
                            rationale="Synthetic entry for the store's write path.",
                        ),
                    ),
                    claim_delta=1,
                )
            ],
        )

        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertFalse(orphan.exists())

    def test_a_proof_takes_the_temps_a_killed_publisher_left_too(self):
        self.write_register(_register(_entry("clm-aa1111", "Anchor Claim")))
        orphan = self.register.parent / f"{store._temp_prefix(self.register)}dead{store.TEMP_SUFFIX}"
        orphan.write_text("half a candidate from a process that died\n", encoding="utf-8")
        with store.proof_temp(self.register, "# R\n"):
            self.assertFalse(orphan.exists())


class TestFileModeIsNotSilentlyNarrowed(_KbCase):
    """A write through this API is not the mode's to change.

    ``tempfile.mkstemp`` makes its temp ``0600`` and ``os.replace`` carries a
    file's permission bits with it rather than the destination name's — left
    unaddressed, every write through this API would narrow its target to
    owner-only regardless of what it had before. The umask is set explicitly
    in each test and restored, rather than trusted to the runner's ambient
    value.
    """

    def test_a_preexisting_files_mode_survives_a_write_regardless_of_umask(self):
        self.write_register(_register(_entry("clm-aa1111", "A Claim")))
        self.register.chmod(0o640)

        # A umask that would default a *new* file to 0o644 — different from
        # the mode already on the register — so a pass here cannot be the
        # umask-derived default landing on the file by coincidence.
        old_umask = os.umask(0o022)
        try:
            outcome = self.insert(
                _entry("clm-bb2222", "B Claim"),
                store.ExpectedEntry(
                    node_id="clm-bb2222",
                    title="B Claim",
                    rigor=0.8,
                    rationale="Synthetic entry for the store's write path.",
                ),
            )
        finally:
            os.umask(old_umask)

        self.assertEqual(outcome.status, store.Status.WRITTEN)
        self.assertEqual(stat.S_IMODE(self.register.stat().st_mode), 0o640)

    def test_a_newly_created_files_mode_follows_the_umask_not_a_hard_coded_default(self):
        for umask, want in ((0o002, 0o664), (0o022, 0o644)):
            with self.subTest(umask=oct(umask)):
                self.register.unlink(missing_ok=True)
                entry = _entry("clm-aa1111", "First Claim")

                old_umask = os.umask(umask)
                try:
                    outcome = store.apply_edits(
                        kb_root=self.kb_root,
                        edits=[
                            store.Edit(
                                path=self.rel,
                                splice=lambda text: store.insert_entry(text, entry),
                                expect=(
                                    store.ExpectedEntry(
                                        node_id="clm-aa1111",
                                        title="First Claim",
                                        rigor=0.8,
                                        rationale="Synthetic entry for the store's write path.",
                                    ),
                                ),
                                claim_delta=1,
                                create=True,
                            )
                        ],
                    )
                finally:
                    os.umask(old_umask)

                self.assertEqual(outcome.status, store.Status.WRITTEN)
                self.assertEqual(stat.S_IMODE(self.register.stat().st_mode), want)


if __name__ == "__main__":
    unittest.main()
