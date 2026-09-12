"""Tests for the validator's design-time and post-build checks.

Design time is check 1 — the derivation against its own input — check 2's
measurements, and check 4's reachability over the derived path list. The planted
classes are what check 1 can still be answered wrongly about: (a) a section
placed at no path, (b) a section placed at two, (c) a node naming a section the
manifest does not hold, and (f) a path whose parent index is absent. None of
these is reachable through :func:`skeleton.derive`, so each is planted by handing
:func:`validate.validate_skeleton` a skeleton mutated away from the one its
manifest implies — which is the shape a real defect in ``derive`` would take.
Converted rather than retired: (d)/(e), a tree exceeding any nominal depth or
fan-out, produce ``FACT`` lines and exit 0; and (r), its mirror — the enumerated
surface admits nothing that arms a measurement into a ``FAIL``.

Plus the shaped fixture: the ``novice.tex`` manifest, one enormous section, whose
size is reported and never judged.

The fixtures here are *manifests* shaped like what those hostile ``.tex`` files
harvest to. The validator never reads LaTeX and never opens a ``.tex``, so a
fixture that was a document rather than a manifest would test the wrong module.

Post-build is the other half: the planted classes (g) a file on disk absent
from the skeleton's list and (h) a listed file absent from disk, each nonzero and
naming the planted identity; a planted ``mf:`` token in an authored leaf failing the
guard; and the on-disk reachability the link primitives read. Those fixtures *are*
trees, built per-test under ``tmp_path`` — nothing here reads or writes a real
``kb-root/``.
"""

import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from kb_tools.kb_survey import manifest as mf
from kb_tools.kb_survey import skeleton as sk
from kb_tools.kb_survey import validate

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

_ENTRY = "sources/a.tex"
_DOMAIN = mf.volume_slug(_ENTRY)


def _section(
    section_id: str,
    title: str,
    *,
    chars: int,
    level: str = "section",
    parent: str | None = None,
    ordinal: int = 1,
    line_start: int = 1,
    line_end: int = 100,
) -> mf.Section:
    return mf.Section(
        id=section_id,
        entry_file=_ENTRY,
        level=level,
        title=title,
        starred=False,
        in_appendix=False,
        parent_id=parent,
        sibling_ordinal=ordinal,
        origin_runs=[mf.OriginRun(file=_ENTRY, line_start=line_start, line_end=line_end)],
        composed_span=mf.ComposedSpan(start=0, end=chars),
        profile=mf.Profile(stripped_chars=chars, result_count=0, subsection_count=0, display_math_count=0),
    )


def _manifest(sections: Sequence[mf.Section]) -> mf.Manifest:
    return mf.Manifest(
        run=mf.Run(source_root="sources", entry_files=[_ENTRY], invocation_flags=["survey-sources"]),
        vocabulary=mf.Vocabulary(theorem_envs=[]),
        files=[mf.FileRecord(path=_ENTRY, included_by=None, include_origin=None)],
        sections=list(sections),
        results=[],
        edges=[],
        flags=[],
        protected_spans=[],
        worklist=[mf.WorklistEntry(section_id=section.id, subdivision=mf.Subdivision.TERMINAL) for section in sections],
    )


def _corpus() -> mf.Manifest:
    """One volume: a subdivided section, its subsection, and a terminal sibling."""
    return _manifest(
        [
            _section("mf:1-alpha", "Alpha", chars=10000, line_start=1, line_end=100),
            _section(
                "mf:1.1-beta", "Beta", chars=4000, level="subsection", parent="mf:1-alpha", line_start=20, line_end=60
            ),
            _section("mf:2-gamma", "Gamma", chars=5000, ordinal=2, line_start=101, line_end=150),
        ]
    )


def _run(manifest: mf.Manifest | None = None, skeleton: sk.Skeleton | None = None) -> list[validate.Finding]:
    """The pre-build checks over a manifest and, by default, the skeleton it implies."""
    subject = _corpus() if manifest is None else manifest
    return validate.validate_skeleton(
        manifest=subject,
        skeleton=sk.derive(subject) if skeleton is None else skeleton,
    )


def _failures(findings: Sequence[validate.Finding]) -> list[str]:
    return [finding.line() for finding in findings if finding.status == validate.FAIL]


def _node(path: str, section_id: str | None) -> sk.Node:
    """One derived-looking node, for the skeletons ``derive`` would never produce."""
    return sk.Node(
        path=path,
        kind=sk.NodeKind.LEAF,
        title=path,
        parent_path=f"{_DOMAIN}/index.md",
        domain=_DOMAIN,
        section_id=section_id,
    )


def _without(skeleton: sk.Skeleton, section_id: str) -> sk.Skeleton:
    return sk.Skeleton(nodes=tuple(node for node in skeleton.nodes if node.section_id != section_id))


def _plus(skeleton: sk.Skeleton, *nodes: sk.Node) -> sk.Skeleton:
    return sk.Skeleton(nodes=(*skeleton.nodes, *nodes))


# ---------------------------------------------------------------------------
# The clean derivation: every gate reports PASS, and nothing gates on a measurement
# ---------------------------------------------------------------------------


def test_a_derived_skeleton_passes_every_gate_and_exits_zero() -> None:
    findings = _run()

    assert _failures(findings) == []
    assert validate.exit_code(findings) == validate.EXIT_OK
    assert {finding.check for finding in findings if finding.status == validate.PASS} == {
        validate.CHECK_PARTITION,
        validate.CHECK_REACHABILITY,
    }


def test_the_partition_pass_line_counts_both_sides_rather_than_merely_reporting_absence() -> None:
    manifest = _corpus()
    skeleton = sk.derive(manifest)

    (line,) = [
        finding.detail
        for finding in _run(manifest, skeleton)
        if finding.status == validate.PASS and finding.check == validate.CHECK_PARTITION
    ]

    assert f"{len(manifest.sections)} sections" in line and f"{len(skeleton.nodes)} documents" in line


# ---------------------------------------------------------------------------
# Planted violations: each nonzero, each naming the planted identity
# ---------------------------------------------------------------------------


def test_a_section_reaching_no_document_is_reported_by_the_partition_check() -> None:
    findings = _run(skeleton=_without(sk.derive(_corpus()), "mf:1.1-beta"))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_PARTITION in line]
    assert "mf:1.1-beta" in line and "no path" in line


def test_a_section_placed_at_two_paths_is_reported_naming_both() -> None:
    findings = _run(skeleton=_plus(sk.derive(_corpus()), _node(f"{_DOMAIN}/twin.md", "mf:1.1-beta")))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_PARTITION in line]
    assert "mf:1.1-beta" in line and f"{_DOMAIN}/twin.md" in line and f"{_DOMAIN}/alpha/beta.md" in line


def test_a_node_naming_a_section_the_manifest_does_not_hold_is_reported_naming_both() -> None:
    findings = _run(skeleton=_plus(sk.derive(_corpus()), _node(f"{_DOMAIN}/ghost.md", "mf:9-no-such-section")))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_PARTITION in line]
    assert "mf:9-no-such-section" in line and f"{_DOMAIN}/ghost.md" in line


def test_a_skeleton_derived_from_a_different_manifest_is_what_the_partition_check_reports() -> None:
    """The one way a caller can hand these two arguments a disagreement."""
    other = _manifest([_section("mf:1-elsewhere", "Elsewhere", chars=100)])

    findings = _run(skeleton=sk.derive(other))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    assert [line for line in _failures(findings) if "mf:1-elsewhere" in line and "does not hold" in line]
    assert [line for line in _failures(findings) if "mf:1-alpha" in line and "no path" in line]


def test_a_path_whose_parent_index_is_absent_is_reported_an_orphan() -> None:
    findings = _run(skeleton=_plus(sk.derive(_corpus()), _node(f"{_DOMAIN}/delta/sub/leaf.md", None)))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert f"{_DOMAIN}/delta/sub/leaf.md" in line and f"{_DOMAIN}/delta/sub/index.md" in line


# ---------------------------------------------------------------------------
# (d)/(e)/(r): the shape measurement reports and never gates
# ---------------------------------------------------------------------------


def _deep_and_wide() -> mf.Manifest:
    """A corpus whose own sectioning nests four deep and fans out twelve wide."""
    spine = ["d", "e", "f", "g"]
    sections = []
    for depth, name in enumerate(spine):
        sections.append(
            _section(
                f"mf:{'.'.join('1' * (depth + 1))}-{name}",
                name,
                chars=1000,
                level="section" if depth == 0 else "subsection",
                parent=None if depth == 0 else f"mf:{'.'.join('1' * depth)}-{spine[depth - 1]}",
            )
        )
    sections += [
        _section(
            f"mf:1.1.1.1.{index}-leaf-{index:02d}",
            f"Leaf {index:02d}",
            chars=100 * index,
            parent=sections[-1].id,
            ordinal=index,
        )
        for index in range(1, 13)
    ]
    return _manifest(sections)


def test_a_tree_deeper_and_wider_than_any_nominal_bound_reports_facts_and_exits_zero() -> None:
    findings = _run(_deep_and_wide())

    assert _failures(findings) == []
    assert validate.exit_code(findings) == validate.EXIT_OK
    shape = [finding for finding in findings if finding.check.startswith("2-")]
    assert all(finding.status == validate.FACT for finding in shape)
    assert any("depth=6 paths=13" in finding.detail for finding in shape)
    assert any("children=12" in finding.detail for finding in shape)


def test_the_enumerated_surface_admits_no_argument_that_arms_a_measurement() -> None:
    """(r) — the mirror of the no-downgrade test: no measurement can be armed."""
    parameters = inspect.signature(validate.validate_skeleton).parameters
    assert [(name, parameter.kind) for name, parameter in parameters.items()] == [
        ("manifest", inspect.Parameter.KEYWORD_ONLY),
        ("skeleton", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters.values())
    assert list(inspect.signature(validate.exit_code).parameters) == ["findings"]


def test_the_only_number_in_the_module_is_the_display_cap_and_the_two_exit_codes() -> None:
    """No threshold anywhere, no override, no configuration surface."""
    numbers = {
        name
        for name, value in vars(validate).items()
        if not name.startswith("_") and isinstance(value, int) and not isinstance(value, bool)
    }

    assert numbers == {"SHAPE_LIST_MAX_ENTRIES", "EXIT_OK", "EXIT_VIOLATION"}


def test_a_fact_only_report_exits_zero_however_extreme_the_measurement() -> None:
    facts = [validate.Finding(validate.FACT, validate.CHECK_LEAF_SIZE, "one.md stripped_chars=9999999")]

    assert validate.exit_code(facts) == validate.EXIT_OK


def test_the_display_cap_bounds_list_length_without_judging_the_document() -> None:
    sections = [_section(f"mf:{index}-s", f"Section {index}", chars=index, ordinal=index) for index in range(1, 30)]
    findings = _run(_manifest(sections))

    sizes = [finding for finding in findings if finding.check == validate.CHECK_LEAF_SIZE]
    assert _failures(findings) == []
    assert len(sizes) == validate.SHAPE_LIST_MAX_ENTRIES + 2, "one distribution line, the cap, one omission line"
    assert sizes[-1].detail.startswith("... 19 more not shown")


def test_the_novice_corpus_reports_its_one_enormous_leaf_and_gates_on_nothing() -> None:
    """``novice.tex``: one enormous section, no labels, no results — a document diagnosed."""
    findings = _run(_manifest([_section("mf:1-everything", "Everything", chars=40000, line_end=400)]))

    assert _failures(findings) == []
    assert validate.exit_code(findings) == validate.EXIT_OK
    (line,) = [
        finding.detail
        for finding in findings
        if finding.check == validate.CHECK_LEAF_SIZE and finding.detail.startswith("leaves")
    ]
    assert line == "leaves count=1 min=40000 median=40000 max=40000"


def test_a_corpus_with_no_sections_reports_its_volume_index_and_nothing_beneath_it() -> None:
    """A source the parser found no heading in is a real outcome, not a failure."""
    findings = _run(_manifest([]))

    assert _failures(findings) == []
    assert [finding for finding in findings if finding.check == validate.CHECK_LEAF_SIZE] == []


# ---------------------------------------------------------------------------
# Report form and order
# ---------------------------------------------------------------------------


def test_every_report_line_carries_the_tag_a_status_and_a_check() -> None:
    findings = _run(skeleton=_without(sk.derive(_corpus()), "mf:1.1-beta"))

    for finding in findings:
        tag, status, check, _ = finding.line().split(" ", 3)
        assert tag == f"[{validate.TAG}]"
        assert status in {validate.PASS, validate.FAIL, validate.FACT}
        assert check == finding.check


def test_findings_come_back_in_check_order_and_sorted_within_a_check() -> None:
    skeleton = _plus(
        sk.derive(_corpus()),
        _node(f"{_DOMAIN}/twin-gamma.md", "mf:2-gamma"),
        _node(f"{_DOMAIN}/twin-beta.md", "mf:1.1-beta"),
    )
    findings = _run(skeleton=skeleton)
    checks = [finding.check for finding in findings]

    assert checks.index(validate.CHECK_PARTITION) < checks.index(validate.CHECK_DEPTH)
    assert checks.index(validate.CHECK_DEPTH) < checks.index(validate.CHECK_REACHABILITY)
    partition = [finding.detail for finding in findings if finding.check == validate.CHECK_PARTITION]
    assert len(partition) == 2
    assert partition == sorted(partition)


# ---------------------------------------------------------------------------
# Post-build: the tree diff, on-disk reachability, the `mf:` guard
#
# The fixture is a five-document KB written under `tmp_path`: a root, two domain
# indexes, two leaves, every non-root document up-linked and every one linked to
# from above.
# ---------------------------------------------------------------------------

_BUILT_PATHS = ("alpha/beta.md", "alpha/index.md", "entry-point.md", "gamma/index.md", "gamma/leaf.md")


def _doc(title: str, *, up: str | None = None, links: Sequence[str] = (), body: str = "") -> str:
    lines = [f"[{validate.UPLINK_MARKER} {title} parent]({up})", ""] if up else []
    lines += [f"# {title}", ""]
    lines += [f"- [{target}]({target})" for target in links]
    if body:
        lines += ["", body]
    return "\n".join(lines) + "\n"


def _built_tree() -> dict[str, str]:
    return {
        "entry-point.md": _doc("Entry", links=("alpha/index.md", "gamma/index.md")),
        "alpha/index.md": _doc("Alpha", up="../entry-point.md", links=("beta.md",)),
        "alpha/beta.md": _doc("Beta", up="index.md"),
        "gamma/index.md": _doc("Gamma", up="../entry-point.md", links=("leaf.md",)),
        "gamma/leaf.md": _doc("Leaf", up="index.md"),
    }


def _build_run(
    tmp_path: Path,
    documents: Mapping[str, str] | None = None,
    paths: Sequence[str] = _BUILT_PATHS,
) -> list[validate.Finding]:
    kb_root = tmp_path / "kb-root"
    for path, text in (_built_tree() if documents is None else documents).items():
        file = kb_root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return validate.validate_build(paths=paths, kb_root=kb_root)


def test_a_built_tree_matching_its_skeleton_passes_every_post_build_check(tmp_path: Path) -> None:
    findings = _build_run(tmp_path)

    assert _failures(findings) == []
    assert validate.exit_code(findings) == validate.EXIT_OK
    assert {finding.check for finding in findings if finding.status == validate.PASS} == {
        validate.CHECK_TREE_DIFF,
        validate.CHECK_REACHABILITY,
        validate.CHECK_ID_GUARD,
    }


def test_g_a_file_on_disk_absent_from_the_path_list_is_reported_unlisted(tmp_path: Path) -> None:
    documents = _built_tree()
    documents["gamma/stowaway.md"] = _doc("Stowaway", up="index.md")
    documents["gamma/index.md"] = _doc("Gamma", up="../entry-point.md", links=("leaf.md", "stowaway.md"))

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_TREE_DIFF in line]
    assert "gamma/stowaway.md" in line and "unlisted" in line


def test_h_a_listed_file_absent_from_disk_is_reported_listed(tmp_path: Path) -> None:
    findings = _build_run(tmp_path, paths=(*_BUILT_PATHS, "gamma/promised.md"))

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_TREE_DIFF in line]
    assert "gamma/promised.md" in line and "listed" in line


def test_the_diff_reports_both_directions_in_one_run(tmp_path: Path) -> None:
    """A set difference reported both ways, not a count comparison."""
    documents = _built_tree()
    documents["gamma/index.md"] = _doc("Gamma", up="../entry-point.md", links=("stowaway.md",))
    documents["gamma/stowaway.md"] = documents.pop("gamma/leaf.md")

    findings = _build_run(tmp_path, documents)
    diff = [line for line in _failures(findings) if validate.CHECK_TREE_DIFF in line]

    assert len(diff) == 2
    assert [line for line in diff if "gamma/leaf.md" in line and "listed" in line]
    assert [line for line in diff if "gamma/stowaway.md" in line and "unlisted" in line]


def test_a_planted_manifest_id_token_in_an_authored_leaf_fails_the_guard(tmp_path: Path) -> None:
    documents = _built_tree()
    documents["gamma/leaf.md"] = _doc("Leaf", up="index.md", body="Distilled from mf:2-gamma of the source.")
    planted = next(
        number for number, text in enumerate(documents["gamma/leaf.md"].splitlines(), start=1) if mf.ID_PREFIX in text
    )

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_ID_GUARD in line]
    assert f"gamma/leaf.md:{planted}" in line and mf.ID_PREFIX in line


def test_the_guard_reads_the_document_as_written_so_a_fenced_token_still_fails(tmp_path: Path) -> None:
    """A per-run id in a code fence is in the built tree just the same."""
    documents = _built_tree()
    documents["alpha/beta.md"] = _doc("Beta", up="index.md", body="```\nsection: mf:1-alpha\n```")

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    assert [line for line in _failures(findings) if "alpha/beta.md" in line and validate.CHECK_ID_GUARD in line]


def test_a_document_no_down_link_chain_reaches_is_reported_unreachable(tmp_path: Path) -> None:
    documents = _built_tree()
    documents["gamma/index.md"] = _doc("Gamma", up="../entry-point.md")

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert "gamma/leaf.md" in line and "unreachable" in line


def test_a_link_inside_a_code_fence_does_not_rescue_an_unreachable_document(tmp_path: Path) -> None:
    """The scan is ``kb_links``': a fenced example link is not a navigation edge."""
    documents = _built_tree()
    documents["gamma/index.md"] = _doc("Gamma", up="../entry-point.md", body="```\n- [Leaf](leaf.md)\n```")

    findings = _build_run(tmp_path, documents)

    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert "gamma/leaf.md" in line and "unreachable" in line


def test_a_document_without_an_up_link_is_reported_even_when_its_parent_links_to_it(tmp_path: Path) -> None:
    documents = _built_tree()
    documents["alpha/beta.md"] = _doc("Beta")

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert "alpha/beta.md" in line and "no up-link" in line


def test_an_up_link_resolving_to_no_authored_document_is_reported_dangling(tmp_path: Path) -> None:
    documents = _built_tree()
    documents["gamma/leaf.md"] = _doc("Leaf", up="../missing/index.md")

    findings = _build_run(tmp_path, documents)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert "gamma/leaf.md" in line and "dangles" in line and "../missing/index.md" in line


_NESTED_PATHS = ("delta/index.md", "delta/sub/index.md", "delta/sub/leaf.md", "entry-point.md")


def _nested_tree(*, leaf_up: str) -> dict[str, str]:
    """A three-level tree; the leaf's up-link is the one thing a caller varies.

    Three levels because two cannot tell the parent relation from mere existence:
    ``../index.md`` from a leaf one level down reaches nothing at all, and the
    dangling case already covers that.
    """
    return {
        "entry-point.md": _doc("Entry", links=("delta/index.md",)),
        "delta/index.md": _doc("Delta", up="../entry-point.md", links=("sub/index.md",)),
        "delta/sub/index.md": _doc("Sub", up="../index.md", links=("leaf.md",)),
        "delta/sub/leaf.md": _doc("Leaf", up=leaf_up),
    }


def test_a_nested_tree_whose_up_links_name_their_own_parents_passes(tmp_path: Path) -> None:
    findings = _build_run(tmp_path, _nested_tree(leaf_up="index.md"), paths=_NESTED_PATHS)

    assert _failures(findings) == []
    assert validate.exit_code(findings) == validate.EXIT_OK


def test_an_up_link_to_a_real_document_that_is_not_the_parent_is_reported_misparented(tmp_path: Path) -> None:
    """``../index.md`` from a leaf lands on the domain index: extant, and a level too high."""
    findings = _build_run(tmp_path, _nested_tree(leaf_up="../index.md"), paths=_NESTED_PATHS)

    assert validate.exit_code(findings) == validate.EXIT_VIOLATION
    (line,) = [line for line in _failures(findings) if validate.CHECK_REACHABILITY in line]
    assert "delta/sub/leaf.md" in line and "misparented" in line
    assert "'../index.md'" in line and "delta/index.md" in line and "delta/sub/index.md" in line


def test_the_index_directory_and_the_exclusion_names_are_not_authored_documents(tmp_path: Path) -> None:
    documents = _built_tree()
    documents[".index/SCHEMA.md"] = "# generated format spec\n"
    documents["README.md"] = "# orientation, not a KB node\n"

    findings = _build_run(tmp_path, documents)

    assert _failures(findings) == []
    assert any("5 listed paths and 5 authored documents" in finding.detail for finding in findings)


def test_the_post_build_surface_admits_no_argument_that_disarms_a_check() -> None:
    """(r)'s post-build mirror: no mode, no severity, no widenable exclusion list."""
    parameters = inspect.signature(validate.validate_build).parameters

    assert [(name, parameter.kind) for name, parameter in parameters.items()] == [
        ("paths", inspect.Parameter.KEYWORD_ONLY),
        ("kb_root", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters.values())


def test_a_kb_root_that_is_not_a_directory_is_refused_rather_than_read_as_empty(tmp_path: Path) -> None:
    """An unfit environment is not a tree that failed every check."""
    with pytest.raises(NotADirectoryError):
        validate.validate_build(paths=_BUILT_PATHS, kb_root=tmp_path / "never-built")
