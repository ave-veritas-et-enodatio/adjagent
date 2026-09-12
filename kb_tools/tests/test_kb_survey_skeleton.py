"""The derived skeleton: the partition, the tree shape, the segments, the audit.

Every fixture here is a manifest composed through ``manifest.py``'s own record
types — never JSON text — because ``derive`` reads records and a fixture the
schema would refuse tests only the refusal.

The load-bearing property is the partition: every ``sections[]`` record reaches
exactly one document, and no document names a record the manifest does not hold.
It is asserted over a table of synthetic corpora rather than over one, since the
shapes that can break it — a volume whose front matter is a section, a volume
whose front matter is nothing, a source two entries reach — are shapes rather
than sizes. :func:`skeleton.partition_defects` is the derivation's own audit, so
it is exercised from both sides: it stays silent over every derived skeleton, and
it names the fault in hand-built ones that place a section twice, place none, or
name a section that was never declared.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

import pytest

from kb_tools import kb_index_lib
from kb_tools.kb_survey import manifest as mf
from kb_tools.kb_survey import skeleton as sk

_ONE = "sources/VolumeOne.tex"
_TWO = "sources/VolumeTwo.tex"
_ONE_DOMAIN = mf.volume_slug(_ONE)
_TWO_DOMAIN = mf.volume_slug(_TWO)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _section(
    section_id: str,
    *,
    entry_file: str,
    title: str,
    level: str = "section",
    parent: str | None = None,
    ordinal: int = 1,
    line_start: int = 1,
    line_end: int = 10,
) -> mf.Section:
    return mf.Section(
        id=section_id,
        entry_file=entry_file,
        level=level,
        title=title,
        starred=False,
        in_appendix=False,
        parent_id=parent,
        sibling_ordinal=ordinal,
        origin_runs=[mf.OriginRun(file=entry_file, line_start=line_start, line_end=line_end)],
        composed_span=mf.ComposedSpan(start=line_start, end=line_end),
        profile=mf.Profile(stripped_chars=100, result_count=0, subsection_count=0, display_math_count=0),
    )


def _manifest(*, entry_files: Sequence[str], sections: Sequence[mf.Section]) -> mf.Manifest:
    """A manifest whose derived fields agree with its own ``parent_id`` chain.

    ``subsection_count`` and the worklist's ``subdivision`` restate the section
    tree, so they are computed here rather than passed per record: a fixture
    that disagreed with itself about which sections are subdivided would be one
    no survey could produce.
    """
    children = Counter(section.parent_id for section in sections)
    counted = [
        replace(section, profile=replace(section.profile, subsection_count=children[section.id]))
        for section in sections
    ]
    return mf.Manifest(
        run=mf.Run(source_root="sources", entry_files=list(entry_files), invocation_flags=["survey-sources"]),
        vocabulary=mf.Vocabulary(theorem_envs=[]),
        files=[mf.FileRecord(path=path, included_by=None, include_origin=None) for path in entry_files],
        sections=counted,
        results=[],
        edges=[],
        flags=[],
        protected_spans=[],
        worklist=[
            mf.WorklistEntry(
                section_id=section.id,
                subdivision=(mf.Subdivision.SUBDIVIDED if children[section.id] else mf.Subdivision.TERMINAL),
            )
            for section in counted
        ],
    )


def _one_volume() -> mf.Manifest:
    """Two terminal sections and no front matter — the plainest volume there is."""
    return _manifest(
        entry_files=[_ONE],
        sections=[
            _section("mf:1-introduction", entry_file=_ONE, title="Introduction", ordinal=1),
            _section("mf:2-the-model", entry_file=_ONE, title="The Model", ordinal=2),
        ],
    )


def _two_volumes() -> mf.Manifest:
    return _manifest(
        entry_files=[_ONE, _TWO],
        sections=[
            _section("mf:1-introduction", entry_file=_ONE, title="Introduction", ordinal=1),
            _section("mf:2-the-model", entry_file=_ONE, title="The Model", ordinal=2),
            _section("mf:1-derivations", entry_file=_TWO, title="Derivations", ordinal=1),
        ],
    )


def _front_matter() -> mf.Manifest:
    """A volume whose synthetic ``document`` root owns front matter, plus an orphan.

    The orphan subsection is a real harvest outcome — a ``\\subsection`` before
    the first ``\\section`` is attached to the document root — and it is the one
    way a section hangs from the volume index rather than beside it.
    """
    return _manifest(
        entry_files=[_ONE],
        sections=[
            _section(
                "mf:0-acme-widgets-quarterly",
                entry_file=_ONE,
                title="Acme Widgets Quarterly",
                level=sk.DOCUMENT_LEVEL,
                ordinal=1,
                line_start=1,
                line_end=40,
            ),
            _section(
                "mf:0.1-abstract",
                entry_file=_ONE,
                title="Abstract",
                level="subsection",
                parent="mf:0-acme-widgets-quarterly",
                ordinal=1,
                line_start=5,
                line_end=20,
            ),
            _section("mf:1-introduction", entry_file=_ONE, title="Introduction", ordinal=1, line_start=41, line_end=80),
        ],
    )


def _mixed_front_matter() -> mf.Manifest:
    """Two volumes: the first has a ``document`` root, the second has none."""
    return _manifest(
        entry_files=[_ONE, _TWO],
        sections=[
            _section(
                "mf:0-acme-widgets-quarterly",
                entry_file=_ONE,
                title="Acme Widgets Quarterly",
                level=sk.DOCUMENT_LEVEL,
                ordinal=1,
            ),
            _section("mf:1-introduction", entry_file=_ONE, title="Introduction", ordinal=1),
            _section("mf:1-derivations", entry_file=_TWO, title="Derivations", ordinal=1),
        ],
    )


def _nested() -> mf.Manifest:
    """Three levels, so a subdivided section sits under another subdivided one."""
    return _manifest(
        entry_files=[_ONE],
        sections=[
            _section("mf:1-theory", entry_file=_ONE, title="Theory", ordinal=1, line_start=1, line_end=200),
            _section(
                "mf:1.1-lemmas",
                entry_file=_ONE,
                title="Lemmas",
                level="subsection",
                parent="mf:1-theory",
                ordinal=1,
                line_start=10,
                line_end=60,
            ),
            _section(
                "mf:1.2-proofs",
                entry_file=_ONE,
                title="Proofs",
                level="subsection",
                parent="mf:1-theory",
                ordinal=2,
                line_start=61,
                line_end=200,
            ),
            _section(
                "mf:1.2.1-the-long-case",
                entry_file=_ONE,
                title="The Long Case",
                level="subsubsection",
                parent="mf:1.2-proofs",
                ordinal=1,
                line_start=100,
                line_end=200,
            ),
        ],
    )


def _shared_source() -> mf.Manifest:
    """One source two entry files reach: one record per entry, same title.

    The coverage-correct answer is two documents, one under each volume — not a
    duplicate to be collapsed.
    """
    return _manifest(
        entry_files=[_ONE, _TWO],
        sections=[
            _section("mf:v1-1-preliminaries", entry_file=_ONE, title="Preliminaries", ordinal=1),
            _section("mf:v2-1-preliminaries", entry_file=_TWO, title="Preliminaries", ordinal=1),
        ],
    )


def _colliding_titles() -> mf.Manifest:
    return _manifest(
        entry_files=[_ONE],
        sections=[
            _section("mf:1-notes", entry_file=_ONE, title="Notes", ordinal=1),
            _section("mf:2-notes", entry_file=_ONE, title="Notes", ordinal=2),
        ],
    )


_MANIFESTS = {
    "one-volume": _one_volume,
    "two-volumes": _two_volumes,
    "front-matter": _front_matter,
    "mixed-front-matter": _mixed_front_matter,
    "nested": _nested,
    "shared-source": _shared_source,
    "colliding-titles": _colliding_titles,
}


def _node_at(skeleton: sk.Skeleton, path: str) -> sk.Node:
    (node,) = [node for node in skeleton.nodes if node.path == path]
    return node


# ---------------------------------------------------------------------------
# The partition, over every shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_MANIFESTS))
def test_every_section_is_placed_at_exactly_one_path(name: str) -> None:
    manifest = _MANIFESTS[name]()
    skeleton = sk.derive(manifest)

    assert sk.partition_defects(manifest, skeleton) == ()
    assert set(skeleton.by_section()) == {section.id for section in manifest.sections}
    assert len(skeleton.paths) == len(set(skeleton.paths))
    assert skeleton.paths[0] == kb_index_lib.ENTRY_POINT_FILENAME


@pytest.mark.parametrize("name", sorted(_MANIFESTS))
def test_deriving_the_same_manifest_twice_gives_the_same_skeleton(name: str) -> None:
    """A skeleton is re-derived rather than persisted, which only holds if it is stable."""
    manifest = _MANIFESTS[name]()

    assert sk.derive(manifest) == sk.derive(manifest)


def test_one_source_two_entries_reach_yields_one_document_per_volume() -> None:
    skeleton = sk.derive(_shared_source())
    by_section = skeleton.by_section()

    assert by_section["mf:v1-1-preliminaries"].path == f"{_ONE_DOMAIN}/preliminaries.md"
    assert by_section["mf:v2-1-preliminaries"].path == f"{_TWO_DOMAIN}/preliminaries.md"


# ---------------------------------------------------------------------------
# The shape of the tree
# ---------------------------------------------------------------------------


def test_a_volumes_document_level_root_is_that_volumes_index() -> None:
    manifest = _front_matter()
    front = next(section for section in manifest.sections if section.level == sk.DOCUMENT_LEVEL)

    node = _node_at(sk.derive(manifest), f"{_ONE_DOMAIN}/{kb_index_lib.INDEX_FILENAME}")

    assert node.kind is sk.NodeKind.INDEX
    assert node.section_id == front.id
    assert node.origin_runs == tuple(front.origin_runs)
    assert node.entry_file == _ONE
    assert node.parent_path == kb_index_lib.ENTRY_POINT_FILENAME


def test_a_volume_without_one_still_gets_an_index_bound_to_no_section() -> None:
    """A directory needs a node whether or not the source put material before its first heading."""
    node = _node_at(sk.derive(_mixed_front_matter()), f"{_TWO_DOMAIN}/{kb_index_lib.INDEX_FILENAME}")

    assert node.kind is sk.NodeKind.INDEX
    assert node.section_id is None
    assert node.origin_runs == ()
    assert node.parent_path == kb_index_lib.ENTRY_POINT_FILENAME


def test_a_section_the_document_root_owns_hangs_from_the_volume_index() -> None:
    node = sk.derive(_front_matter()).by_section()["mf:0.1-abstract"]

    assert node.path == f"{_ONE_DOMAIN}/abstract.md"
    assert node.parent_path == f"{_ONE_DOMAIN}/{kb_index_lib.INDEX_FILENAME}"


def test_a_terminal_section_becomes_a_leaf_beside_its_parents_index() -> None:
    node = sk.derive(_nested()).by_section()["mf:1.1-lemmas"]

    assert node.kind is sk.NodeKind.LEAF
    assert node.path == f"{_ONE_DOMAIN}/theory/lemmas.md"
    assert node.parent_path == f"{_ONE_DOMAIN}/theory/{kb_index_lib.INDEX_FILENAME}"


def test_a_subdivided_section_becomes_an_index_still_carrying_its_own_source() -> None:
    """The lead-in between a heading and its first subsection belongs in that index."""
    manifest = _nested()
    section = next(section for section in manifest.sections if section.id == "mf:1.2-proofs")

    node = sk.derive(manifest).by_section()["mf:1.2-proofs"]

    assert node.kind is sk.NodeKind.INDEX
    assert node.path == f"{_ONE_DOMAIN}/theory/proofs/{kb_index_lib.INDEX_FILENAME}"
    assert node.section_id == section.id
    assert node.origin_runs == tuple(section.origin_runs)


# ---------------------------------------------------------------------------
# Slugging
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        pytest.param("Introduction", "introduction", id="one-word"),
        pytest.param("  The Model's Scope  ", "the-model-s-scope", id="punctuation-and-padding"),
        # Under a rule that always trimmed back to the last hyphen, this would
        # lose "four" to a rule about titles far longer than it.
        pytest.param("one two three four", "one-two-three-four", id="short-title-keeps-its-last-word"),
        pytest.param(
            "Extremely Long Section Title That Runs Past Segment Boundary",
            "extremely-long-section-title-that-runs-past",
            id="truncated-back-to-a-whole-word",
        ),
        pytest.param("$\\{+\\}$", sk.UNTITLED_SLUG, id="latex-with-no-letters"),
        pytest.param("", sk.UNTITLED_SLUG, id="empty"),
        pytest.param("---", sk.UNTITLED_SLUG, id="separators-only"),
    ],
)
def test_slug_renders_one_path_segment(title: str, expected: str) -> None:
    assert sk.slug(title) == expected
    assert len(sk.slug(title)) <= sk.SLUG_MAX_CHARS


# ---------------------------------------------------------------------------
# Placement: siblings that collide, and names the indexer would not see
# ---------------------------------------------------------------------------


def test_two_sibling_sections_sharing_a_title_land_on_distinct_paths() -> None:
    by_section = sk.derive(_colliding_titles()).by_section()

    first, second = by_section["mf:1-notes"].path, by_section["mf:2-notes"].path
    assert first != second
    assert {first, second} <= {f"{_ONE_DOMAIN}/notes.md", f"{_ONE_DOMAIN}/notes-2.md"}


#: Every segment a derived path may not take: the indexer's own exclusion
#: vocabulary, read from ``kb_index_lib`` so a name added there joins this table
#: rather than being missed by a list typed out here.
_RESERVED_SEGMENTS = frozenset(
    {name.removesuffix(".md") for name in kb_index_lib.EXCLUDE_NAMES}
    | set(kb_index_lib.EXCLUDE_DIRS)
    | {kb_index_lib.INDEX_FILENAME.removesuffix(".md")}
)


@pytest.mark.parametrize("title", sorted(_RESERVED_SEGMENTS))
def test_a_section_titled_like_a_reserved_name_never_lands_on_it(title: str) -> None:
    """A document at an excluded name passes every mechanical gate by not existing to them."""
    manifest = _manifest(
        entry_files=[_ONE],
        sections=[_section("mf:1-reserved", entry_file=_ONE, title=title, ordinal=1)],
    )

    node = sk.derive(manifest).by_section()["mf:1-reserved"]
    segment = node.path.removeprefix(f"{_ONE_DOMAIN}/").removesuffix(".md")

    assert segment not in _RESERVED_SEGMENTS
    assert segment.startswith(sk.slug(title)), "the segment still says which section it is"


# ---------------------------------------------------------------------------
# The audit, from the failing side
# ---------------------------------------------------------------------------


def _audited() -> mf.Manifest:
    return _manifest(
        entry_files=[_ONE],
        sections=[
            _section("mf:1-alpha", entry_file=_ONE, title="Alpha", ordinal=1),
            _section("mf:2-beta", entry_file=_ONE, title="Beta", ordinal=2),
        ],
    )


def _leaf(path: str, section_id: str | None) -> sk.Node:
    return sk.Node(
        path=path,
        kind=sk.NodeKind.LEAF,
        title=path,
        parent_path=f"{_ONE_DOMAIN}/{kb_index_lib.INDEX_FILENAME}",
        domain=_ONE_DOMAIN,
        section_id=section_id,
    )


@pytest.mark.parametrize(
    ("nodes", "expected"),
    [
        pytest.param(
            (_leaf("a.md", "mf:1-alpha"), _leaf("b.md", "mf:1-alpha"), _leaf("c.md", "mf:2-beta")),
            ("mf:1-alpha", "2 paths", "a.md", "b.md"),
            id="one-section-at-two-paths",
        ),
        pytest.param(
            (_leaf("a.md", "mf:1-alpha"),),
            ("mf:2-beta", "no path"),
            id="a-section-at-no-path",
        ),
        pytest.param(
            (_leaf("a.md", "mf:1-alpha"), _leaf("b.md", "mf:2-beta"), _leaf("c.md", "mf:9-ghost")),
            ("mf:9-ghost", "c.md", "the manifest does not hold"),
            id="a-node-naming-an-undeclared-section",
        ),
    ],
)
def test_partition_defects_names_the_fault(nodes: tuple[sk.Node, ...], expected: tuple[str, ...]) -> None:
    """A hand-built skeleton is the only way to reach these: ``derive`` produces none of them."""
    (defect,) = sk.partition_defects(_audited(), sk.Skeleton(nodes=nodes))

    assert all(fragment in defect for fragment in expected)


def test_a_node_bound_to_no_section_is_not_a_defect() -> None:
    """The entry point and a front-matter-less volume index name no section by design."""
    nodes = (_leaf("a.md", "mf:1-alpha"), _leaf("b.md", "mf:2-beta"), _leaf("structural.md", None))

    assert sk.partition_defects(_audited(), sk.Skeleton(nodes=nodes)) == ()


# ---------------------------------------------------------------------------
# The orders the driver reads off a skeleton
# ---------------------------------------------------------------------------


def test_indexes_come_back_deepest_first_and_exclude_the_entry_point() -> None:
    """An index summarises what is beneath it, so everything below one is written first."""
    indexes = sk.derive(_nested()).indexes
    depths = [path.count("/") for path in (node.path for node in indexes)]

    assert depths == sorted(depths, reverse=True)
    assert [node.path for node in indexes] == [
        f"{_ONE_DOMAIN}/theory/proofs/{kb_index_lib.INDEX_FILENAME}",
        f"{_ONE_DOMAIN}/theory/{kb_index_lib.INDEX_FILENAME}",
        f"{_ONE_DOMAIN}/{kb_index_lib.INDEX_FILENAME}",
    ]
    assert kb_index_lib.ENTRY_POINT_FILENAME not in {node.path for node in indexes}


def test_domains_are_one_prefix_per_volume_in_first_appearance_order() -> None:
    """Declaration order, not alphabetical: the volumes below are declared reversed."""
    manifest = _manifest(
        entry_files=[_TWO, _ONE],
        sections=[
            _section("mf:1-derivations", entry_file=_TWO, title="Derivations", ordinal=1),
            _section("mf:1-introduction", entry_file=_ONE, title="Introduction", ordinal=1),
        ],
    )

    domains = sk.derive(manifest).domains

    assert dict(domains) == {_TWO_DOMAIN: (_TWO_DOMAIN,), _ONE_DOMAIN: (_ONE_DOMAIN,)}
    assert list(domains) == [_TWO_DOMAIN, _ONE_DOMAIN]


# ---------------------------------------------------------------------------
# The judgment seam
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_MANIFESTS))
def test_no_section_is_referred_for_a_re_cut(name: str) -> None:
    """Today: none, always. The document's own segmentation is taken as it stands."""
    assert sk.sections_needing_recut(sk.derive(_MANIFESTS[name]())) == ()
