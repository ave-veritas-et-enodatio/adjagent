"""The returned formats — envelope, VERDICT, SCOPE — and the toolkit they are built from.

Validation is parse-only, so the tests are two tables: shapes that must parse
into the right typed value, and malformed shapes that must be refused rather
than half-read. A driver that reads a broken envelope optimistically records a
completed wave that did no work — the failure class this layer exists to
eliminate.

The JSON record toolkit at the top is the layer under all of that. It was
``kb_survey.blocks`` while the design document's own blocks were read off disk
by the survey validator; the document tree is derived from the sources now and
the driver walks it, so the toolkit has one consumer and lives with it. Its properties are the
model-facing ones — a closed vocabulary in both directions, markers matched as
whole lines, a line that ends at ``\\n`` and nowhere else, and a decode failure
that names a line and a column — and they are asserted here because they
survived the module that used to hold them.
"""

import json
from pathlib import Path

import pytest

import kb_tools
from kb_tools.kb_driver import envelope

# ---------------------------------------------------------------------------
# The JSON record toolkit
# ---------------------------------------------------------------------------

PROBE_OPEN = "<<<KB-DRIVER-PROBE"
PROBE_CLOSE = "KB-DRIVER-PROBE"


def _probe(body: str) -> str:
    """A document carrying one probe block, prose on both sides of it."""
    return f"# Document\n\nSome prose.\n\n{PROBE_OPEN}\n{body}\n{PROBE_CLOSE}\n\nMore prose.\n"


def _extract(text: str) -> str:
    return envelope.extract_block(text, open_marker=PROBE_OPEN, close_marker=PROBE_CLOSE, label="probe")


def _decode(text: str) -> object:
    return envelope.parse_json_block(text, open_marker=PROBE_OPEN, close_marker=PROBE_CLOSE, label="probe")


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        pytest.param("nothing here at all", "no '<<<KB-DRIVER-PROBE' block", id="absent"),
        pytest.param(f"{PROBE_OPEN}\nbody\n", "never closed", id="unclosed"),
        pytest.param(
            f"{PROBE_OPEN}\na\n{PROBE_CLOSE}\n{PROBE_OPEN}\nb\n{PROBE_CLOSE}\n",
            "2 '<<<KB-DRIVER-PROBE' blocks; exactly one is required",
            id="two-blocks",
        ),
    ],
)
def test_the_marker_pair_owns_three_refusals_and_names_the_marker_in_each(text: str, complaint: str) -> None:
    """Absent, unclosed, duplicated. None of the three is read optimistically."""
    with pytest.raises(envelope.ParseError, match=complaint):
        _extract(text)


def test_a_block_named_in_prose_without_being_opened_does_not_open_one() -> None:
    """Markers are whole stripped lines, so a format can be discussed without being spoken."""
    with pytest.raises(envelope.ParseError, match="no .* block"):
        _extract(f"The block is called {PROBE_CLOSE} and it is written like this.\n")


def test_a_marker_with_a_tail_on_its_line_is_not_a_marker() -> None:
    """A marker is a whole line, not a line's prefix."""
    with pytest.raises(envelope.ParseError, match="no .* block"):
        _extract(f"# Document\n\n{PROBE_OPEN} []\n{PROBE_CLOSE}\n")


def test_an_indented_marker_still_opens_its_block() -> None:
    """Stripped, so leading whitespace a model adds is not a second grammar to learn."""
    assert _extract(f"  {PROBE_OPEN}\n[1]\n   {PROBE_CLOSE}\n") == "[1]"


def test_a_body_that_is_not_json_is_refused_with_the_decoders_own_line_and_column() -> None:
    """A decode failure is precise and repairable where a grammar complaint is a puzzle."""
    with pytest.raises(envelope.ParseError, match=r"not valid JSON: .*line \d+ column \d+"):
        _decode(_probe("a.md: mf:1-a"))


# `str.splitlines` breaks on all eight; the block grammar breaks on none of
# them. Each one silently turns one physical line into two accepted ones, which
# is how a marker line acquires an invisible tail.
_SPLITLINES_SEPARATORS = [" ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85"]
_SEPARATOR_IDS = [
    "line-sep",
    "para-sep",
    "vertical-tab",
    "form-feed",
    "file-sep",
    "group-sep",
    "record-sep",
    "next-line",
]


@pytest.mark.parametrize("separator", _SPLITLINES_SEPARATORS, ids=_SEPARATOR_IDS)
def test_a_separator_inside_the_body_does_not_end_the_block_early(separator: str) -> None:
    """The body handed to the decoder is whole: a line ends at ``\\n`` and nowhere else."""
    body = f'["a{separator}b"]'

    assert _extract(_probe(body)) == body


@pytest.mark.parametrize("separator", _SPLITLINES_SEPARATORS, ids=_SEPARATOR_IDS)
def test_a_separator_cannot_smuggle_a_marker_onto_its_own_line(separator: str) -> None:
    """A marker preceded by a separator is a marker with a head, so it opens nothing."""
    with pytest.raises(envelope.ParseError, match="no .* block"):
        _extract(f"# Document\n\nprose{separator}{PROBE_OPEN}\n[]\n{PROBE_CLOSE}\n")


def test_a_crlf_document_still_reads_as_its_lines() -> None:
    """The one separator that is dropped, because it is half of the line ending."""
    assert _decode(_probe('["a", "b"]').replace("\n", "\r\n")) == ["a", "b"]


def test_the_key_vocabulary_is_closed_and_total_in_both_directions() -> None:
    """A key left out is refused by name, and so is a key added."""
    envelope.check_keys({"name": "a", "status": "ok"}, ("name", "status"), label="probe")

    with pytest.raises(envelope.ParseError, match="missing required key.*status"):
        envelope.check_keys({"name": "a"}, ("name", "status"), label="probe")
    with pytest.raises(envelope.ParseError, match="unknown key.*confidence"):
        envelope.check_keys({"name": "a", "status": "ok", "confidence": 0.9}, ("name", "status"), label="probe")


# ---------------------------------------------------------------------------
# The prose blocks beside the JSON
# ---------------------------------------------------------------------------

PROSE = "KB-DRIVER-PROBE-PROSE"

#: One sequence of each class. ``\sigma`` is not a JSON escape and fails loudly
#: inside a JSON string; ``\beta`` is one, decodes to a backspace, and fails
#: later and silently as a value that no longer matches anything. Both are
#: ordinary mathematics in the corpus this toolchain reads.
MATHEMATICS = "supercritical for $\\sigma < 1$, and $\\beta$ decay dominates"


def _blocks(*texts: str, numbers: tuple[int, ...] | None = None) -> str:
    chosen = numbers if numbers is not None else tuple(range(1, len(texts) + 1))
    return "".join(f"<<<{PROSE} {n}\n{text}\n{PROSE} {n}\n" for n, text in zip(chosen, texts, strict=True))


def _prose(text: str) -> envelope.ProseBlocks:
    return envelope.extract_prose_blocks(text, name=PROSE, label="probe")


def test_a_prose_block_is_taken_verbatim_between_its_delimiters() -> None:
    """No escape grammar exists in the transport, so nothing can decode to something else."""
    blocks = _prose(_blocks(MATHEMATICS))

    assert blocks.take(1, key="what", label="probe") == MATHEMATICS
    blocks.check_exhausted()


def test_a_prose_block_holds_the_lines_it_was_given() -> None:
    blocks = _prose(f"<<<{PROSE} 1\nfirst\n\nsecond\n{PROSE} 1\n")

    assert blocks.take(1, key="what", label="probe") == "first\n\nsecond"


@pytest.mark.parametrize(
    ("text", "value", "complaint"),
    [
        pytest.param(_blocks("a"), "the words themselves", "must be the number of", id="words-in-the-json"),
        pytest.param(_blocks("a"), True, "must be the number of", id="a-bool-is-not-an-index"),
        pytest.param(_blocks("a"), 7, "no block with that number", id="referent-with-no-block"),
    ],
)
def test_a_reference_that_does_not_name_a_block_is_refused(text: str, value: object, complaint: str) -> None:
    with pytest.raises(envelope.ParseError, match=complaint):
        _prose(text).take(value, key="what", label="probe")


def test_a_block_no_key_names_is_refused_rather_than_dropped() -> None:
    """Words the seat composed and nothing reads, on ``check_keys``' own argument."""
    blocks = _prose(_blocks("named", "unnamed"))
    blocks.take(1, key="what", label="probe")

    with pytest.raises(envelope.ParseError, match="named by no key"):
        blocks.check_exhausted()


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        pytest.param(f"<<<{PROSE} 1\nbody\n", "never closed", id="unclosed"),
        pytest.param(f"<<<{PROSE} 1\nbody\n{PROSE} 2\n", "opened as 1 is closed as 2", id="mismatched-number"),
        pytest.param(_blocks("a", "b", numbers=(1, 1)), "two .* blocks are numbered 1", id="duplicate-number"),
    ],
)
def test_a_malformed_prose_block_is_refused_and_named(text: str, complaint: str) -> None:
    with pytest.raises(envelope.ParseError, match=complaint):
        _prose(text)


def test_a_site_declaring_no_prose_keys_still_refuses_a_prose_block() -> None:
    """Which is what makes an empty declaration a check rather than an omission."""
    payload, blocks = envelope.parse_record(
        _probe('{"a": 1}') + _blocks("uninvited"),
        open_marker=PROBE_OPEN,
        close_marker=PROBE_CLOSE,
        prose_name=PROSE,
        label="probe",
    )

    assert payload == {"a": 1}
    with pytest.raises(envelope.ParseError, match="named by no key"):
        blocks.check_exhausted()


def test_a_levels_declaration_that_is_not_a_partition_is_refused_at_the_point_it_is_made() -> None:
    envelope.check_levels([envelope.Level("fine", ("a", "b"), json=("a",), prose=("b",))])

    for broken in (
        envelope.Level("gap", ("a", "b"), json=("a",), prose=()),
        envelope.Level("overlap", ("a",), json=("a",), prose=("a",)),
        envelope.Level("stray", ("a",), json=("a",), prose=("b",)),
    ):
        with pytest.raises(envelope.ParseError, match="do not partition"):
            envelope.check_levels([broken])


_KB_TOOLS_ROOT = Path(kb_tools.__file__).resolve().parent

#: A phrase from the extractor's own third refusal. Whoever writes a second
#: extractor writes this sentence again — which is what makes its file set a
#: usable proxy for "how many implementations of the grammar ship".
_REFUSAL_PHRASE = "blocks; exactly one is required"


def _shipped_sources() -> dict[str, str]:
    """Every shipped ``kb_tools`` module, keyed by its path relative to the package.

    ``_vendor/`` is out: it is fetched source this repo does not write. Test
    modules are out for the same reason a test may legitimately spell a refusal
    it is asserting on.
    """
    return {
        str(path.relative_to(_KB_TOOLS_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(_KB_TOOLS_ROOT.rglob("*.py"))
        if "_vendor" not in path.parts and "tests" not in path.parts
    }


def test_the_block_grammar_is_worded_in_exactly_one_place() -> None:
    """One implementation, so a strictness change to marker matching lands once."""
    spelling = sorted(name for name, source in _shipped_sources().items() if _REFUSAL_PHRASE in source)

    assert spelling == ["kb_driver/envelope.py"]


# ---------------------------------------------------------------------------
# The wave envelope
# ---------------------------------------------------------------------------


def _envelope_text(payload: object, *, prose: tuple[str, ...] = (), lead: str = "Wave complete.") -> str:
    """A returned envelope, hand-composed so a malformed one can be stated.

    Only the malformed cases go through here. A well-formed envelope is composed
    by :func:`envelope.envelope_block` — the module that parses it — because a
    fixture that hand-typed the format would be a second statement of it.
    """
    blocks = "".join(
        f"<<<{envelope.PROSE_NAME} {number}\n{text}\n{envelope.PROSE_NAME} {number}\n"
        for number, text in enumerate(prose, start=1)
    )
    return f"{lead}\n\n{envelope.ENVELOPE_OPEN}\n{json.dumps(payload)}\n{envelope.ENVELOPE_CLOSE}\n{blocks}"


#: Every value a seat composes, in the order :func:`envelope.envelope_block`
#: numbers them: the gap, then the deviation's what and why.
GOOD_PROSE = ("appendix C has no taxonomy position", "re-briefed", "empty return")

GOOD_PAYLOAD = {
    "step": "p0.survey",
    "members": [
        {"name": "AcmeWidgets.tex", "status": "ok"},
        {"name": "AcmeWidgetsDerivations.tex", "status": "ok"},
    ],
    "gaps": [1],
    "deviations": [{"kind": "re-brief", "member": "AcmeWidgets.tex", "what": 2, "why": 3}],
}

GOOD_ENVELOPE = envelope.Envelope(
    step="p0.survey",
    members=(
        envelope.Member(name="AcmeWidgets.tex", status="ok"),
        envelope.Member(name="AcmeWidgetsDerivations.tex", status="ok"),
    ),
    gaps=("appendix C has no taxonomy position",),
    deviations=(envelope.Deviation(kind="re-brief", member="AcmeWidgets.tex", what="re-briefed", why="empty return"),),
)


def test_envelope_parses_members_gaps_and_deviations() -> None:
    parsed = envelope.parse_envelope(_envelope_text(GOOD_PAYLOAD, prose=GOOD_PROSE), step="p0.survey")

    assert parsed.step == "p0.survey"
    assert [member.name for member in parsed.members] == ["AcmeWidgets.tex", "AcmeWidgetsDerivations.tex"]
    assert [member.status for member in parsed.members] == ["ok", "ok"]
    assert parsed.gaps == ("appendix C has no taxonomy position",)
    assert parsed.deviations[0].kind == "re-brief"
    assert parsed.deviations[0].why == "empty return"


def test_the_composer_and_the_parser_are_one_statement_of_the_format() -> None:
    """Composed, parsed, and equal — so a fixture cannot drift from what ships."""
    assert envelope.parse_envelope(envelope.envelope_block(GOOD_ENVELOPE)) == GOOD_ENVELOPE


@pytest.mark.parametrize("kind", envelope.DEVIATION_KINDS)
def test_every_deviation_kind_in_the_vocabulary_parses(kind: str) -> None:
    payload = GOOD_PAYLOAD | {"deviations": [{"kind": kind, "member": "m", "what": 2, "why": 3}]}

    parsed = envelope.parse_envelope(_envelope_text(payload, prose=GOOD_PROSE))

    assert parsed.deviations[0].kind == kind


@pytest.mark.parametrize(
    ("payload", "complaint"),
    [
        pytest.param(GOOD_PAYLOAD | {"elapsed_s": 41}, "envelope: unknown key.*elapsed_s", id="top-level"),
        pytest.param(
            GOOD_PAYLOAD | {"members": [{"name": "a", "status": "ok", "confidence": 0.9}]},
            r"members\[0\]: unknown key.*confidence",
            id="member",
        ),
        pytest.param(
            GOOD_PAYLOAD | {"deviations": [{"kind": "triage", "member": "m", "what": 2, "why": 3, "at": "T2"}]},
            r"deviations\[0\]: unknown key.*at",
            id="deviation",
        ),
    ],
)
def test_an_invented_key_is_refused_and_named(payload: dict, complaint: str) -> None:
    """A key nobody asked for is evidence, not noise.

    It says the seat's attention went somewhere the assignment did not send it,
    which impugns what it put in the keys that *were* asked for. Dropping it
    would discard the one signal that the rest of the return may be unsound.
    """
    with pytest.raises(envelope.ParseError, match=complaint):
        envelope.parse_envelope(_envelope_text(payload, prose=GOOD_PROSE))


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("no block here at all", "no '<<<KB-DRIVER-ENVELOPE'"),
        (_envelope_text(GOOD_PAYLOAD) * 2, "2 '<<<KB-DRIVER-ENVELOPE' blocks"),
        (f"{envelope.ENVELOPE_OPEN}\n{{}}\n", "never closed"),
        (f"{envelope.ENVELOPE_OPEN}\nnot json\n{envelope.ENVELOPE_CLOSE}\n", "not valid JSON"),
        (f"{envelope.ENVELOPE_OPEN}\n[1, 2]\n{envelope.ENVELOPE_CLOSE}\n", "must be a JSON object"),
    ],
)
def test_malformed_envelope_blocks_are_refused(text: str, complaint: str) -> None:
    with pytest.raises(envelope.ParseError, match=complaint):
        envelope.parse_envelope(text)


@pytest.mark.parametrize(
    ("payload", "complaint"),
    [
        ({"members": [], "gaps": [], "deviations": []}, "missing required key"),
        ({"step": "p0.survey", "gaps": [], "deviations": []}, "missing required key"),
        (GOOD_PAYLOAD | {"members": [{"status": "ok"}]}, r"members\[0\]: missing required key"),
        (GOOD_PAYLOAD | {"members": [{"name": "", "status": "ok"}]}, "name must be a non-empty string"),
        (GOOD_PAYLOAD | {"members": "all fine"}, "members: must be an array of objects"),
        (GOOD_PAYLOAD | {"gaps": [{"what": "a gap"}]}, "must be the number of a KB-DRIVER-PROSE block"),
        (
            GOOD_PAYLOAD | {"deviations": [{"kind": "improvised", "member": "m", "what": 2, "why": 3}]},
            "is not one of",
        ),
        (GOOD_PAYLOAD | {"deviations": [{"kind": "triage", "member": "m", "what": 2}]}, "missing required key"),
    ],
)
def test_malformed_envelope_payloads_are_refused(payload: dict, complaint: str) -> None:
    with pytest.raises(envelope.ParseError, match=complaint):
        envelope.parse_envelope(_envelope_text(payload, prose=GOOD_PROSE))


def test_a_status_that_is_not_ok_is_the_seat_s_own_phrase_and_arrives_in_a_block() -> None:
    """The status stays free text: this moves where it travels, and closes no vocabulary.

    ``status`` is the key the whole envelope change turns on. It is prose
    wherever it is not ``ok`` — a seat's short phrase for what happened to the
    content it just worked on, over a mathematical corpus — so it carries the
    identical latent defect behind a stage that costs inference to reach.
    """
    failed = envelope.Envelope(
        step="p0.survey",
        members=(
            envelope.Member(name="A.tex", status=envelope.STATUS_OK),
            envelope.Member(name="B.tex", status=MATHEMATICS),
        ),
        gaps=(),
        deviations=(),
    )
    text = envelope.envelope_block(failed)

    assert f"\n{MATHEMATICS}\n" in text, "the phrase is in a block, exactly as it was written"
    assert MATHEMATICS not in text.splitlines()[1], "and not inside the JSON"
    assert envelope.parse_envelope(text) == failed


def test_the_prose_declaration_and_the_key_vocabulary_partition_every_level() -> None:
    """Acceptance: total and disjoint, on ``check_keys``' own terms, at every level."""
    envelope.check_levels(envelope.LEVELS)

    assert {level.label for level in envelope.LEVELS} == {"envelope", "envelope member", "envelope deviation"}
    for level, keys in zip(
        envelope.LEVELS,
        (envelope.ENVELOPE_KEYS, envelope.MEMBER_KEYS, envelope.DEVIATION_KEYS),
        strict=True,
    ):
        assert level.keys == keys
        assert set(level.json) | set(level.prose) == set(keys)
        assert not set(level.json) & set(level.prose)


def test_envelope_declaring_another_step_is_refused() -> None:
    with pytest.raises(envelope.ParseError, match="declares step 'p0.survey'"):
        envelope.parse_envelope(_envelope_text(GOOD_PAYLOAD, prose=GOOD_PROSE), step="p3.distill")


def test_deviations_are_appended_with_their_run_context(tmp_path: Path) -> None:
    log = tmp_path / "deviations.jsonl"
    parsed = envelope.parse_envelope(_envelope_text(GOOD_PAYLOAD, prose=GOOD_PROSE))

    first = envelope.append_deviations(log, parsed.deviations, run_id="R1", stage="phase-0", step="p0.survey")
    second = envelope.append_deviations(log, parsed.deviations, run_id="R1", stage="phase-0", step="p0.survey")

    assert (first, second) == (1, 1)
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["run_id"] == "R1"
    assert records[0]["stage"] == "phase-0"
    assert records[0]["step"] == "p0.survey"
    assert records[0]["member"] == "AcmeWidgets.tex"
    assert records[0]["kind"] == "re-brief"


def test_every_deviation_kind_a_wave_may_report_reaches_the_log(tmp_path: Path) -> None:
    """The whole vocabulary, composed, parsed and then logged — one round trip.

    The vocabulary is closed at both ends and the ends are different code:
    ``parse_envelope`` admits a kind, ``append_deviations`` writes it. A kind one
    end took and the other dropped would leave a deviation reported and
    unrecorded, which is invisible to either end's own test. It was asserted
    through the driver's last wave row until that row retired; the property
    belongs to the vocabulary rather than to any stage, so it is asked here, and
    the words go through :func:`envelope.envelope_block` because a deviation's
    ``what`` and ``why`` travel in prose blocks beside the JSON.
    """
    log = tmp_path / "deviations.jsonl"
    reported = envelope.Envelope(
        step="p0.survey",
        members=(envelope.Member(name="A.tex", status=envelope.STATUS_OK),),
        gaps=(),
        deviations=tuple(
            envelope.Deviation(kind=kind, member="A.tex", what=f"did {kind}", why="synthetic")
            for kind in envelope.DEVIATION_KINDS
        ),
    )
    parsed = envelope.parse_envelope(envelope.envelope_block(reported))

    written = envelope.append_deviations(log, parsed.deviations, run_id="R1", stage="phase-0", step="p0.survey")

    assert written == len(envelope.DEVIATION_KINDS)
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in records] == list(envelope.DEVIATION_KINDS)
    assert [record["what"] for record in records] == [f"did {kind}" for kind in envelope.DEVIATION_KINDS]


def test_appending_no_deviations_writes_nothing(tmp_path: Path) -> None:
    log = tmp_path / "deviations.jsonl"

    assert envelope.append_deviations(log, (), run_id="R1", stage="phase-0", step="p0.survey") == 0
    assert not log.exists()


# ---------------------------------------------------------------------------
# The review VERDICT line
# ---------------------------------------------------------------------------


def test_verdict_parses_its_counts() -> None:
    verdict = envelope.parse_verdict("findings…\n\nVERDICT: critical=2 warning=0 note=13\n")

    assert (verdict.critical, verdict.warning, verdict.note) == (2, 0, 13)


def test_the_final_verdict_line_is_the_one_that_counts() -> None:
    text = (
        "quoting the contract:\nVERDICT: critical=0 warning=0 note=0\n\n"
        "findings…\nVERDICT: critical=1 warning=2 note=3"
    )

    assert envelope.parse_verdict(text).critical == 1


@pytest.mark.parametrize(
    "text",
    [
        "no verdict anywhere",
        "VERDICT: critical=1 warning=2",
        "VERDICT: critical=one warning=2 note=3",
        "VERDICT: warning=2 critical=1 note=3",
        "VERDICT: critical=1 warning=2 note=3 blocking=yes",
        "VERDICT:critical=1 warning=2 note=3",
    ],
)
def test_malformed_verdict_lines_are_refused(text: str) -> None:
    with pytest.raises(envelope.ParseError):
        envelope.parse_verdict(text)


# ---------------------------------------------------------------------------
# The design doc's SCOPE line
# ---------------------------------------------------------------------------


def test_within_charter_scope_parses() -> None:
    scope = envelope.parse_scope("prose\nSCOPE: within-charter\nmore prose")

    assert scope.kind == envelope.SCOPE_WITHIN
    assert not scope.is_expansion
    assert scope.detail == ""


def test_expansion_scope_carries_its_one_line() -> None:
    scope = envelope.parse_scope("SCOPE: expansion the derivations volume needs a second domain")

    assert scope.is_expansion
    assert scope.detail == "the derivations volume needs a second domain"


@pytest.mark.parametrize(
    "text",
    [
        "no scope line at all",
        "SCOPE: expansion",
        "SCOPE: within-charter but see appendix C",
        "SCOPE: unchanged",
        "SCOPE:within-charter",
    ],
)
def test_malformed_scope_lines_are_refused(text: str) -> None:
    with pytest.raises(envelope.ParseError):
        envelope.parse_scope(text)


def test_the_final_scope_line_is_the_one_that_counts() -> None:
    text = "the contract asks for:\nSCOPE: within-charter\n\nand my answer is:\nSCOPE: expansion two new domains"

    assert envelope.parse_scope(text).is_expansion


# ---------------------------------------------------------------------------
# The contract text a brief carries
# ---------------------------------------------------------------------------


def test_the_scope_contract_states_both_admissible_kinds() -> None:
    assert f"SCOPE: {envelope.SCOPE_WITHIN}" in envelope.SCOPE_LINE_CONTRACT
    assert f"SCOPE: {envelope.SCOPE_EXPANSION}" in envelope.SCOPE_LINE_CONTRACT
