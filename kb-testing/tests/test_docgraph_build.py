"""The document-graph pipeline end to end, driven as it ships, over a staged corpus.

The subject is the command line, not the functions behind it: a test that
reached into :mod:`kb_tools.kb_docgraph.build` would be a component test whatever
the file is called, and the surface a caller has is the one worth proving.

Every assertion here is a property of *any* corpus — the build exits clean, both
partition checks run per volume, two runs agree byte for byte, a citing volume
gets a reference list — so nothing in this file has to be revised when the staged
corpus changes. See ``conftest.py`` for what staging has to provide.
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from kb_tools.kb_claimgraph import inventory

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(
    corpus: Path, kb_root: Path, roots: Sequence[str], *, bibliography: str | None = None
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, "-m", "kb_tools.kb_docgraph"]
    for name in roots:
        argv += ["--source", str(corpus / name)]
    if bibliography is not None:
        argv += ["--bibliography", str(corpus / bibliography)]
    argv += ["--kb-root", str(kb_root)]
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PYTHONPATH": str(_REPO_ROOT), "PATH": os.environ.get("PATH", "")},
    )


@pytest.fixture(scope="module")
def built(
    staged_corpus: Path, volume_roots: tuple[str, ...], bibliography: str, tmp_path_factory: pytest.TempPathFactory
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """One corpus, two builds. The second is what says the first was deterministic."""
    first, second = tmp_path_factory.mktemp("first"), tmp_path_factory.mktemp("second")
    completed = _run(staged_corpus, first / "kb-root", volume_roots, bibliography=bibliography)
    _run(staged_corpus, second / "kb-root", volume_roots, bibliography=bibliography)
    return completed, first / "kb-root", second / "kb-root"


def _report(built: tuple[subprocess.CompletedProcess[str], Path, Path]) -> list[str]:
    return built[0].stdout.splitlines()


def test_the_build_exits_clean_over_the_corpus(built) -> None:
    completed, _, _ = built

    assert [line for line in _report(built) if " FAIL " in line] == []
    assert completed.returncode == 0, completed.stderr


def test_both_partition_checks_run_on_every_volume(built, volume_roots) -> None:
    """They run on every build, because the next silent drop is one no probe was aimed at."""
    report = _report(built)

    for stem in (Path(name).stem for name in volume_roots):
        assert any(f"PASS A-ast-to-markdown {stem} " in line for line in report), stem
        assert any(f"PASS B-markdown-to-tree {stem} " in line for line in report), stem


def test_the_gates_the_plan_names_all_report(built) -> None:
    """``validate_build`` and ``verify_md_links`` over the produced tree."""
    report = _report(built)

    assert any("PASS validate-build/3-tree-diff" in line for line in report)
    assert any("PASS validate-build/4-reachability" in line for line in report)
    assert any("PASS verify-md-links" in line for line in report)


def test_two_runs_produce_byte_identical_trees_and_the_build_records_its_pandoc(built) -> None:
    _, first, second = built

    assert any(line.startswith("[docgraph] FACT pandoc-version ") for line in _report(built))
    for document in sorted(first.rglob("*")):
        counterpart = second / document.relative_to(first)
        assert counterpart.exists(), document
        if document.is_file():
            assert document.read_bytes() == counterpart.read_bytes(), document


def test_the_tree_carries_no_claim_graph_artifact(built) -> None:
    """Body text, a heading, an up-link, a child list — and nothing the claim graph owns."""
    _, kb_root, _ = built

    assert list(kb_root.rglob(".index")) == []
    for document in kb_root.rglob("*.md"):
        text = document.read_text(encoding="utf-8")
        assert not text.startswith("---"), document
        assert not any(line.startswith("claims:") for line in text.splitlines()), document
        assert "<!-- id:" not in text and "<!-- claim-quality:" not in text, document


def test_every_volume_reports_how_its_declarations_read(built, volume_roots) -> None:
    """The finding is emitted once per volume, in both its states.

    A build silent here says nothing about either: a volume whose declarations
    all read and one whose declarations were never scanned look identical in an
    output that only speaks up on failure.
    """
    reported = [line for line in _report(built) if " declaration-read " in line]

    assert len(reported) == len(volume_roots), _report(built)
    for line in reported:
        assert line.startswith("[docgraph] FACT declaration-read ")


def test_each_citing_volume_s_reference_list_is_its_own_leaf_under_the_volume_index(built) -> None:
    """Placement, for whichever volumes cite.

    ``--citeproc`` and an explicit ``--bibliography`` are what produce the block,
    not a ``\\bibliography`` command in the source — so a volume that declares
    none and still cites gets a reference list like any other.
    """
    _, kb_root, _ = built
    citing = [path for path in sorted(kb_root.iterdir()) if path.is_dir() and (path / "references.md").is_file()]

    if not citing:
        pytest.skip("no volume of the staged corpus resolved a reference list")
    for volume in citing:
        # Last in the child list because last in document order, and titled so a
        # reader recognises it. Up-link correctness itself is validate-build's.
        assert (
            (volume / "index.md").read_text(encoding="utf-8").rstrip().endswith("- [References](references.md)")
        ), volume.name
        opening = (volume / "references.md").read_text(encoding="utf-8").splitlines()[:3]
        assert opening[0].startswith("[↑ ") and opening[0].endswith("](index.md)"), volume.name
        assert opening[2] == "# References", volume.name


def test_no_document_but_the_reference_list_holds_a_reference_list(built) -> None:
    """The lift is a relocation: the block leaves the section it fell in, whole."""
    _, kb_root, _ = built
    holders = sorted(
        document.relative_to(kb_root).as_posix()
        for document in kb_root.rglob("*.md")
        if "csl-bib-body" in document.read_text(encoding="utf-8")
    )

    assert holders == sorted(
        f"{volume.name}/references.md"
        for volume in kb_root.iterdir()
        if volume.is_dir() and (volume / "references.md").is_file()
    )


# ---------------------------------------------------------------------------
# A corpus with no bibliography
#
# The state that used to be silent data loss: without `--citeproc` a Cite
# reaches the gfm writer as something it has nowhere to put, so every citation
# was simply deleted and the build exited clean. Many arXiv tarballs are exactly
# this — a pre-generated .bbl, or inline \bibitem, and no .bib at all.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_without_a_bibliography(
    staged_corpus: Path, volume_roots: tuple[str, ...], bibliography: str, tmp_path_factory: pytest.TempPathFactory
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """The same corpus, converted with no bibliography named at all."""
    corpus = tmp_path_factory.mktemp("bibless-corpus")
    shutil.copytree(staged_corpus, corpus, dirs_exist_ok=True)
    (corpus / bibliography).unlink()

    kb_root = tmp_path_factory.mktemp("bibless") / "kb-root"
    return _run(corpus, kb_root, volume_roots), kb_root


def test_a_build_completes_over_a_citing_corpus_with_no_bibliography(built_without_a_bibliography) -> None:
    """The property the sweep depends on, and the one that used to fail silently.

    Every check the build runs still passes — the partition checks included,
    which is the interesting half: a citation that renders as its own key is
    content on both sides of the comparison, where a deleted one was content on
    neither and so was invisible to them too.
    """
    completed, _ = built_without_a_bibliography

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "FAIL" not in completed.stdout


def test_every_citation_survives_a_build_with_no_bibliography(built_without_a_bibliography) -> None:
    """The citations are in the tree, carrying their keys, and a person can read them.

    The tree's own keys are the subject rather than a transcribed list: what has
    to survive is whatever this corpus cites, and a fixture naming its own keys
    would pass over a tree that lost the ones it forgot to name.
    """
    _, kb_root = built_without_a_bibliography
    tree_text = "\n".join(path.read_text(encoding="utf-8") for path in kb_root.rglob("*.md"))
    attributes = [span.group("keys") for span in inventory.CITATION_SPAN_RE.finditer(tree_text)]

    if not attributes:
        pytest.skip("the staged corpus cites nothing")
    # Legible, not merely machine-readable: every citation renders its own keys
    # as visible text, which is what a span carrying an empty body would fail.
    # Asked per citation rather than per key, because one `\citep{a,b}` is one
    # citation element and renders as one parenthesised group.
    unreadable = sorted(
        attribute for attribute in set(attributes) if f"({'; '.join(attribute.split())})" not in tree_text
    )
    assert unreadable == [], unreadable


def test_a_bib_less_build_gains_no_references_leaf(built_without_a_bibliography) -> None:
    """What such a build loses, stated as a test rather than left to be discovered.

    The reference list is citeproc's product, so with no bibliography there is
    none to cut a leaf from. A volume the processor emits no reference list for
    gets no such document rather than an empty one — so the loss is legal, and
    naming it here is what keeps it from being read as a defect later.
    """
    _, kb_root = built_without_a_bibliography

    assert list(kb_root.rglob("references.md")) == []
    assert not any("[References](references.md)" in path.read_text(encoding="utf-8") for path in kb_root.rglob("*.md"))


def test_the_same_citation_markup_appears_whether_or_not_a_bibliography_resolved(
    built, built_without_a_bibliography
) -> None:
    """One recognisable form across all three states a citation can be in.

    Resolved to author-year prose, rendered as ``(**key?**)`` because the
    bibliography did not answer, or carrying its own key because there was no
    bibliography — the span and its ``data-cites`` are the same in each, which
    is what lets one reader find every citation without knowing which state it
    is in.
    """
    _, resolved_root, _ = built
    _, bibless_root = built_without_a_bibliography

    def keys(root: Path) -> set[str]:
        text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.md"))
        return {key for span in inventory.CITATION_SPAN_RE.finditer(text) for key in span.group("keys").split()}

    with_bib, without_bib = keys(resolved_root), keys(bibless_root)

    if not with_bib and not without_bib:
        pytest.skip("the staged corpus cites nothing")
    # The reference list adds documents to the resolved tree but no inline
    # citation, so the same corpus cites the same works either way.
    assert with_bib == without_bib


# ---------------------------------------------------------------------------
# A bibliography the reader cannot parse
#
# Six papers of a 33-paper arXiv sweep stopped on a `PandocError`, and three of
# those were this: a `.bib` opening `@String(PAMI = {...})`, parenthesised where
# citeproc's reader accepts only braces. A bibliography is an input a corpus may
# not have, so the build takes the same degradation rather than dying, and says
# so.
# ---------------------------------------------------------------------------

#: The defect as three papers of the sweep ship it, prepended to a bibliography
#: that is otherwise entirely well formed. Quoted rather than approximated: the
#: subject is a file citeproc refuses, and a `.bib` broken some other way would
#: prove the recovery against an input nobody has.
_UNREADABLE_BIB_PREFIX = "@String(PAMI = {IEEE Trans. Pattern Anal. Mach. Intell.})\n"


@pytest.fixture(scope="module")
def built_with_an_unreadable_bibliography(
    staged_corpus: Path, volume_roots: tuple[str, ...], bibliography: str, tmp_path_factory: pytest.TempPathFactory
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """The same corpus, its bibliography broken the way the sweep's are."""
    corpus = tmp_path_factory.mktemp("unreadable-corpus")
    shutil.copytree(staged_corpus, corpus, dirs_exist_ok=True)
    broken = corpus / bibliography
    broken.write_text(_UNREADABLE_BIB_PREFIX + broken.read_text(encoding="utf-8"), encoding="utf-8")

    kb_root = tmp_path_factory.mktemp("unreadable") / "kb-root"
    return _run(corpus, kb_root, volume_roots, bibliography=bibliography), kb_root, broken


def test_an_unreadable_bibliography_degrades_instead_of_stopping_the_build(
    built_with_an_unreadable_bibliography,
) -> None:
    """The whole recovery, through the surface a caller has.

    It also proves the retry re-ran *both* conversions: stage 2 asserts the AST's
    headers against the rendering's headings before it zips them and check A
    compares one's text against the other's, so a pair in which only one had been
    redone without the bibliography could not reach a clean exit.
    """
    completed, _, _ = built_with_an_unreadable_bibliography

    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "FAIL" not in completed.stdout


def test_the_fallback_names_the_file_and_what_it_cost(built_with_an_unreadable_bibliography, volume_roots) -> None:
    """Loud, because a silent one is worse than the crash it replaces.

    A reader of a quiet build's output would take these citations for resolved
    ones — the tree carries the same citation markup either way, which is
    exactly what makes the degradation invisible in the product.
    """
    completed, _, broken = built_with_an_unreadable_bibliography
    reported = [line for line in completed.stdout.splitlines() if " bibliography-read " in line]

    assert len(reported) == len(volume_roots), completed.stdout
    for line in reported:
        assert line.startswith("[docgraph] FACT bibliography-read ")
        assert str(broken) in line and "no citation resolved" in line
    # Pandoc's own complaint says what is wrong with the file, which the finding
    # deliberately does not carry; losing both would leave the recovery with no
    # diagnosis at all.
    assert "Error reading bibliography file" in completed.stderr


def test_an_unreadable_bibliography_builds_the_tree_a_missing_one_builds(
    built_with_an_unreadable_bibliography, built_without_a_bibliography
) -> None:
    """ "The same input as none" as a comparison rather than an assertion.

    Byte for byte across the whole tree: the degradation the contract promises is
    the bib-less one, so anything a broken file changed about the product — a
    half-resolved citation, a stray references leaf — shows up here as a diff.
    """
    _, degraded, _ = built_with_an_unreadable_bibliography
    _, bibless = built_without_a_bibliography

    def documents(root: Path) -> dict[str, str]:
        return {str(path.relative_to(root)): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.md"))}

    assert documents(degraded) == documents(bibless)
