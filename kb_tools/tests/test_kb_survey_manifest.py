"""Tests for the manifest module: round-trip, schema, and rendering contracts.

Round-trip, serialization determinism, a record the schema does not describe refused
rather than coerced, the ``FlagCode`` enum closed and exhaustive against a
hostile-fixture table, both joins produced by the reader from a manifest carrying no
stored profile copy, and every schema-doc sentence present in the module docstring.

Plus a structural guarantee belonging to the record type rather than to any
arithmetic: the worklist record has no profile field, no rationale field, and no
field that could hold one.
"""

import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path

import pytest

from kb_tools.kb_survey import manifest as mf

# ---------------------------------------------------------------------------
# Fixture: a small manifest exercising every record type and both nullable cases
# ---------------------------------------------------------------------------


def _manifest() -> mf.Manifest:
    return mf.Manifest(
        run=mf.Run(
            source_root="sources",
            entry_files=["sources/book.tex"],
            invocation_flags=["survey-sources"],
        ),
        vocabulary=mf.Vocabulary(
            theorem_envs=[
                mf.TheoremEnv(
                    name="theorem",
                    printed_name="Theorem",
                    numbered_within="section",
                    origin=mf.Origin(file="sources/book.tex", line=12),
                ),
                mf.TheoremEnv(
                    name="remark",
                    printed_name="Remark",
                    numbered_within=None,
                    origin=mf.Origin(file="sources/book.tex", line=13),
                ),
            ]
        ),
        files=[
            mf.FileRecord(path="sources/book.tex", included_by=None, include_origin=None),
            mf.FileRecord(
                path="sources/ch1.tex",
                included_by="sources/book.tex",
                include_origin=mf.Origin(file="sources/book.tex", line=40),
            ),
        ],
        sections=[
            mf.Section(
                id="mf:1-flow",
                entry_file="sources/book.tex",
                level="chapter",
                title="Flow",
                starred=False,
                in_appendix=False,
                parent_id=None,
                sibling_ordinal=1,
                origin_runs=[mf.OriginRun(file="sources/ch1.tex", line_start=1, line_end=400)],
                composed_span=mf.ComposedSpan(start=0, end=9000),
                profile=mf.Profile(stripped_chars=9000, result_count=0, subsection_count=1, display_math_count=4),
            ),
            mf.Section(
                id="mf:1.1-navier-stokes",
                entry_file="sources/book.tex",
                level="section",
                title="Navier–Stokes",
                starred=False,
                in_appendix=False,
                parent_id="mf:1-flow",
                sibling_ordinal=1,
                origin_runs=[
                    mf.OriginRun(file="sources/ch1.tex", line_start=20, line_end=300),
                    mf.OriginRun(file="sources/appendix.tex", line_start=1, line_end=40),
                ],
                composed_span=mf.ComposedSpan(start=400, end=8000),
                profile=mf.Profile(stripped_chars=7600, result_count=2, subsection_count=0, display_math_count=3),
            ),
        ],
        results=[
            mf.Result(
                id="mf:thm-1.1",
                env="theorem",
                label="thm:existence",
                section_id="mf:1.1-navier-stokes",
                origin_runs=[mf.OriginRun(file="sources/ch1.tex", line_start=100, line_end=130)],
                composed_span=mf.ComposedSpan(start=2000, end=2600),
            ),
            mf.Result(
                id="mf:rem-1.1",
                env="remark",
                label=None,
                section_id="mf:1.1-navier-stokes",
                origin_runs=[mf.OriginRun(file="sources/ch1.tex", line_start=200, line_end=210)],
                composed_span=mf.ComposedSpan(start=5000, end=5200),
            ),
        ],
        edges=[
            mf.Edge(
                kind=mf.EdgeKind.CITE,
                macro="citep",
                from_section_id="mf:1.1-navier-stokes",
                from_origin=mf.Origin(file="sources/ch1.tex", line=150),
                target_token="leray1934",
                resolved_result_id=None,
            ),
            mf.Edge(
                kind=mf.EdgeKind.REF,
                macro="Cref",
                from_section_id="mf:1.1-navier-stokes",
                from_origin=mf.Origin(file="sources/ch1.tex", line=160),
                target_token="thm:existence",
                resolved_result_id="mf:thm-1.1",
            ),
        ],
        flags=[
            mf.Flag(
                code=mf.FlagCode.ENCODING_FALLBACK,
                source_span=mf.SourceSpan(file="sources/appendix.tex", line_start=1, line_end=40),
                detail="decoded latin-1 after strict utf-8 failed",
            )
        ],
        protected_spans=[
            mf.ProtectedSpan(
                env="lstlisting",
                origin_runs=[mf.OriginRun(file="sources/ch1.tex", line_start=140, line_end=168)],
            ),
            mf.ProtectedSpan(
                env="verbatim",
                origin_runs=[mf.OriginRun(file="sources/appendix.tex", line_start=10, line_end=22)],
            ),
        ],
        worklist=[
            mf.WorklistEntry(section_id="mf:1-flow", subdivision=mf.Subdivision.SUBDIVIDED),
            mf.WorklistEntry(section_id="mf:1.1-navier-stokes", subdivision=mf.Subdivision.TERMINAL),
        ],
    )


# ---------------------------------------------------------------------------
# Round-trip and determinism
# ---------------------------------------------------------------------------


def test_a_manifest_survives_a_round_trip_through_the_one_reader_and_writer(tmp_path: Path) -> None:
    original = _manifest()
    path = tmp_path / "phase0" / "survey-manifest.json"
    mf.write_manifest(original, path)

    assert mf.read_manifest(path) == original


def test_serialization_is_byte_stable_and_carries_no_unordered_iteration() -> None:
    manifest = _manifest()
    first = mf.to_json(manifest)

    assert first == mf.to_json(manifest)
    assert first == mf.to_json(mf.from_json(first))

    payload = json.loads(first)
    assert list(payload) == sorted(payload), "top-level keys must be sorted, not insertion-ordered"
    assert [section["id"] for section in payload["sections"]] == [
        "mf:1-flow",
        "mf:1.1-navier-stokes",
    ], "list order is document order and must survive the round trip"


def test_the_write_is_atomic_and_leaves_no_temporary_behind(tmp_path: Path) -> None:
    path = tmp_path / "survey-manifest.json"
    mf.write_manifest(_manifest(), path)

    assert path.read_text(encoding="utf-8") == mf.to_json(_manifest())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["survey-manifest.json"]


def test_concurrent_writers_of_one_manifest_all_succeed_and_leave_it_intact(tmp_path: Path) -> None:
    """Two runs writing one ``--manifest-out`` must not collide.

    With the temporary name derived from the destination's, every writer used the
    same temp file: their writes interleaved inside it, and the second rename found
    nothing to rename and raised. Both halves are checked here — every writer
    returns, and every read taken while they run parses, which is the half-manifest
    this guards against. Threads rather than processes because the failure is in
    the filesystem calls, which the GIL does not serialize.
    """
    path = tmp_path / "phase0" / "survey-manifest.json"
    first, second = _manifest(), replace(_manifest(), results=[])
    mf.write_manifest(first, path)
    read_failures: list[Exception] = []
    stop = threading.Event()

    def write(manifest: mf.Manifest) -> None:
        for _ in range(40):
            mf.write_manifest(manifest, path)

    def read() -> None:
        while not stop.is_set():
            try:
                mf.read_manifest(path)
            except Exception as exc:  # noqa: BLE001 - the point is that nothing at all is raised
                read_failures.append(exc)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for future in [pool.submit(write, manifest) for manifest in (first, second) * 4]:
                future.result()  # re-raises whatever a writer raised
    finally:
        stop.set()
        reader.join()

    assert read_failures == []
    assert mf.read_manifest(path) in (first, second)
    assert sorted(p.name for p in path.parent.iterdir()) == ["survey-manifest.json"]


def test_an_absolute_path_is_refused_at_the_write_rather_than_persisted() -> None:
    manifest = _manifest()
    broken = mf.Manifest(
        **{**manifest.__dict__, "run": mf.Run(**{**manifest.run.__dict__, "source_root": "/abs/root"})}
    )

    with pytest.raises(mf.ManifestContractError, match="absolute path"):
        mf.to_json(broken)


def test_an_id_without_the_mf_prefix_is_refused_at_the_write() -> None:
    manifest = _manifest()
    sections = [mf.Section(**{**manifest.sections[0].__dict__, "id": "1-flow"}), manifest.sections[1]]
    broken = mf.Manifest(**{**manifest.__dict__, "sections": sections})

    with pytest.raises(mf.ManifestContractError, match="mf:"):
        mf.to_json(broken)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda payload: payload["sections"][0].pop("title"), "missing fields"),
        (lambda payload: payload["sections"][0].update(rationale="because"), "unknown fields"),
        (lambda payload: payload["flags"][0].update(code="not-a-code"), "FlagCode"),
        (lambda payload: payload["worklist"][0].update(subdivision="one-leaf"), "Subdivision"),
    ],
)
def test_the_reader_refuses_a_record_that_does_not_match_the_schema(mutate, expected: str) -> None:
    payload = json.loads(mf.to_json(_manifest()))
    mutate(payload)

    with pytest.raises(mf.ManifestContractError, match=expected):
        mf.from_json(json.dumps(payload))


def test_the_readers_refusal_names_the_re_run_that_repairs_a_manifest_an_older_writer_wrote() -> None:
    """The refusal is the only place the toolchain says a manifest is re-creatable by construction."""
    payload = json.loads(mf.to_json(_manifest()))
    payload["a_field_this_reader_does_not_know"] = 1

    with pytest.raises(mf.ManifestContractError, match="Re-run the survey op"):
        mf.from_json(json.dumps(payload))


# ---------------------------------------------------------------------------
# The closed vocabularies
# ---------------------------------------------------------------------------


def test_the_flag_code_enum_is_closed_and_exhaustive_against_the_hostile_fixture_table() -> None:
    """Set equality, not containment: an extra code is as much a defect as a missing one."""
    assert {code.value for code in mf.FlagCode} == {
        "macro-sectioning",  # macro-indirection.tex
        "macro-environment",  # macro-indirection.tex
        "macro-include-arg",  # macro-indirection.tex
        "conditional-branch",  # macro-indirection.tex
        "wrapped-title",  # md2latex-sludge.tex
        "missing-include",  # nested-input.tex
        "include-cycle",  # nested-input.tex
        "include-outside-root",  # path-escape.tex
        # The twelfth: it is what makes a second inclusion of an already-composed
        # target visible instead of silently absent. Exercised by the diamond and
        # branching fan-out shapes in test_kb_survey_compose.py, which are tmp_path
        # trees rather than table rows.
        "include-collapsed",
        "orphan-subsection",  # starred-appendix.tex
        "encoding-fallback",  # latin1.tex
        "parser-exception",  # no corpus fixture: exercised by fault injection
    }


def test_the_closed_two_value_vocabularies_hold_exactly_their_stated_values() -> None:
    assert {value.value for value in mf.Subdivision} == {"terminal", "subdivided"}
    # The presentation macros collapse into the two relations they spell, and the
    # spelling survives on `Edge.macro` rather than in a third and fourth enum member.
    assert {value.value for value in mf.EdgeKind} == {"cite", "ref"}


# ---------------------------------------------------------------------------
# The structural half, which belongs to the record type
# ---------------------------------------------------------------------------


def test_the_worklist_record_has_no_field_that_could_hold_a_recommendation() -> None:
    """No profile copy, no rationale, and no field a rationale could be written into.

    ``subdivision`` is typed as the closed enum rather than as ``str``, which is what
    makes "no field that could hold one" structural rather than a matter of care: a
    two-member enum cannot carry prose.
    """
    by_name = {field.name: field for field in fields(mf.WorklistEntry)}

    assert set(by_name) == {"section_id", "subdivision"}
    assert by_name["subdivision"].type is mf.Subdivision
    assert by_name["section_id"].type is str


def test_the_serialized_worklist_stores_no_copy_of_the_section_profile() -> None:
    payload = json.loads(mf.to_json(_manifest()))

    assert payload["worklist"] == [
        {"section_id": "mf:1-flow", "subdivision": "subdivided"},
        {"section_id": "mf:1.1-navier-stokes", "subdivision": "terminal"},
    ]


# ---------------------------------------------------------------------------
# The join the reader owns
# ---------------------------------------------------------------------------


def test_the_section_index_is_one_line_per_section_ordered_by_id() -> None:
    index = mf.render_section_index(_manifest())

    assert index.splitlines() == [
        "mf:1-flow · Flow · chapter · subdivided",
        "mf:1.1-navier-stokes · Navier–Stokes · section · terminal",
    ]


def _many_sections(count: int) -> mf.Manifest:
    """A manifest of ``count`` sections, each in the worklist — ids zero-padded to sort stably."""
    manifest = _manifest()
    sections = [
        replace(manifest.sections[1], id=f"mf:{index:04d}-s", parent_id=None, sibling_ordinal=index)
        for index in range(count)
    ]
    return replace(
        manifest,
        sections=sections,
        results=[],
        edges=[],
        worklist=[mf.WorklistEntry(section_id=section.id, subdivision=mf.Subdivision.TERMINAL) for section in sections],
    )


def test_a_manifest_larger_than_the_deleted_cap_briefs_whole() -> None:
    """401 sections, 401 lines, nothing omitted.

    The rendering carried a 400-line cap that appended ``... N more sections
    omitted``. The brief receiving it tells the architect that an id the list
    does not carry does not exist, so past the cap every omitted section came
    back ``uncovered`` against a designer that was never shown its id — a finding
    no revision could close. 401 is the first count the old branch fired at.
    """
    manifest = _many_sections(401)

    lines = mf.render_section_index(manifest).splitlines()

    assert len(lines) == 401
    assert lines[0] == "mf:0000-s · Navier–Stokes · section · terminal"
    assert lines[-1] == "mf:0400-s · Navier–Stokes · section · terminal"
    assert [line for line in lines if "omitted" in line] == []
    assert {line.split(" · ")[0] for line in lines} == {section.id for section in manifest.sections}


def test_the_index_rendering_takes_no_argument_that_could_shorten_it() -> None:
    """The deletion's other half: no caller can re-arm the cap through a parameter."""
    assert list(inspect.signature(mf.render_section_index).parameters) == ["manifest"]


def test_the_module_holds_no_numbers() -> None:
    """No display cap, and no successor to one.

    ``validate.py`` carries the same assertion for the same reason, over its own
    constants. Here the rule has no exception: an integer constant in this module
    is a bound on something a manifest holds, which is the defect this assertion
    exists to catch, whatever it is named.
    """
    numbers = {
        name
        for name, value in vars(mf).items()
        if not name.startswith("_") and isinstance(value, int) and not isinstance(value, bool)
    }

    assert numbers == set()


# ---------------------------------------------------------------------------
# Line discipline across all three renderings
# ---------------------------------------------------------------------------

#: A title as the frozen corpus actually carries one: wrapped across two source
#: lines by the author. The manifest transcribes it as written.
_WRAPPED_TITLE = "The propagation operator is genuine on the spine, and\nits link-attack is genuine at scale."


def _with_a_wrapped_title() -> mf.Manifest:
    manifest = _manifest()
    return replace(manifest, sections=[replace(manifest.sections[0], title=_WRAPPED_TITLE), manifest.sections[1]])


def test_a_title_the_author_wrapped_renders_on_one_line() -> None:
    """The record keeps the raw fact; the display derives it.

    Without the normalization, the section index would emit 162 lines for the
    corpus's 161 sections and nothing would say so — the stray fragment line
    reaches the model through a prompt slot.
    """
    manifest = _with_a_wrapped_title()

    assert manifest.sections[0].title == _WRAPPED_TITLE, "the record is not normalized; the rendering is"
    assert len(mf.render_section_index(manifest).splitlines()) == len(manifest.sections)
    assert "on the spine, and its link-attack" in mf.render_section_index(manifest)


def test_the_one_line_per_record_assertion_fires_when_the_normalization_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teeth. With ``_one_line`` neutered the rendering must refuse, not emit two lines.

    The assertion is unreachable while the normalization in front of it works, so
    removing the normalization is the only way to demonstrate the guard is live
    rather than decorative.
    """
    monkeypatch.setattr(mf, "_one_line", lambda text: text)

    with pytest.raises(mf.ManifestContractError, match="one line per record"):
        mf.render_section_index(_with_a_wrapped_title())


def test_the_rendering_is_not_persisted_beside_the_manifest(tmp_path: Path) -> None:
    """The rendering is produced at the point of use, never stored."""
    path = tmp_path / "survey-manifest.json"
    mf.write_manifest(_manifest(), path)
    mf.render_section_index(mf.read_manifest(path))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["survey-manifest.json"]


# ---------------------------------------------------------------------------
# The schema-doc sentences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "``composed_span`` is an extent in the composed stream and never a place to read from"
        " — a locator is ``origin`` or ``source_span``.",
        "``resolved_result_id: null`` is the ordinary case for a bibliographic ``cite``" " and never means malformed.",
        "The ``subdivision`` value restates ``subsection_count == 0`` and carries no judgment.",
        "``mf:`` ids are per-run and never enter ``kb-root/``.",
        "``parent_id`` is null at the tree root.",
        "``files[].included_by`` is null for an entry file.",
        "``stripped_chars`` is a character count and not a token estimate — a reader comparing it"
        " against a token-stated criterion applies a rough conversion at the point of reading,"
        " and no document is restated to match the unit.",
    ],
)
def test_every_schema_doc_sentence_the_field_names_cannot_carry_alone_is_in_the_module_docstring(
    sentence: str,
) -> None:
    """The schema-doc sentences, transcribed. The docstring is the schema doc; a missing sentence is a gap.

    Compared with whitespace collapsed, so the assertion is about the sentence rather
    than about where the docstring happens to wrap.
    """
    assert mf.__doc__ is not None
    assert " ".join(sentence.split()) in " ".join(mf.__doc__.split())
