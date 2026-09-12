"""The build's head, against a repository holding one paper and no ``kb-root/``.

The state under test is the one the launch command is run against: a consuming
repository carrying the installed toolchain, its LaTeX source, and no KB at all.
Nothing is faked. The document graph converts through the real reader,
``graph-init`` seeds the spine for real, the declared claim-graph pass runs over
the tree that produced, and every boundary is a real commit in a real
repository.

The source is ``fixtures/lamb/`` — a whole paper in twenty lines, committed, so
this file needs nothing staged. A walk over a multi-volume corpus is the
integration suite's (``kb-testing/tests/test_driver_head.py``); what is here is
the head driven over the one paper that ships with the tests.

``--no-inference`` is what lets it go the whole way: every row that would cost a
model call is dropped, so a fresh entry walks past both claim-graph stages and
closes the build out without spending anything.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_util
from kb_tools.kb_claimgraph import tree
from kb_tools.kb_driver import barriers, baton, config, run, runlog
from kb_tools.kb_write.render import FRONTMATTER_OPENER

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO_ROOT / "gen-defs.py"

#: A whole paper in twenty lines, with its own bibliography beside it. Its
#: deliberate properties are commented in the source; what matters here is that
#: reaching them costs a build rather than a corpus.
_LAMB = Path(__file__).resolve().parent / "fixtures" / "lamb"
LAMB_SOURCES = ("lamb.tex",)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _make_fresh_consumer(root: Path, *, corpus: Path, files: Sequence[str]) -> Path:
    """A committed repository with the toolchain installed, sources present, and no KB.

    This is the whole of what a fresh build starts from — there is no tree, no
    ``.index/``, and no KB include line in the runner file, because every one of
    those is something the head is supposed to produce.
    """
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    for key, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "fixture"),
        ("commit.gpgsign", "false"),
    ):
        _git(root, "config", key, value)

    claude = root / kb_util.CLAUDE_DIRNAME
    claude.mkdir()
    installed = subprocess.run(
        [sys.executable, str(_INSTALLER), "install", str(claude)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert installed.returncode == 0, f"stdout:\n{installed.stdout}\nstderr:\n{installed.stderr}"

    (root / ".gitignore").write_text(f"{kb_util.SCRATCH_DIRNAME}/\n", encoding="utf-8")
    (root / "justfile").write_text("default:\n    @true\n", encoding="utf-8")
    for name in files:
        shutil.copy(corpus / name, root / name)

    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "sources, and the toolchain that will build from them")
    return root


def _walk_head(
    consumer: Path,
    runs: Path,
    *,
    stages: Sequence[str],
    overrides: Mapping[str, object] = MappingProxyType({}),
) -> run.Result:
    """One walk of the head, driven the way the launch line specifies a run.

    The overrides carry exactly what ``--source`` carries and no config file
    stands anywhere, which is the launch this build is started by: the sources
    are the only field with no default, and the bibliography beside them is
    resolved rather than named.
    """
    paths = runlog.prepare(runs, "20260901T120000-1")
    lock = runlog.repo_lock_path(consumer)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "run_id": paths.run_id}), encoding="utf-8")
    return run.execute(
        config=config.load(None, run_overrides=dict(overrides), admissible=barriers.ADMISSIBLE),
        paths=paths,
        decisions=[config.parse_decision("start.proceed=yes", admissible=barriers.ADMISSIBLE)],
        repo_root=consumer,
        stages=stages,
    )


def _documents(kb_root: Path) -> dict[str, str]:
    """The tree's documents, by the walk the verifiers themselves use.

    Read through ``kb_claimgraph.tree`` rather than globbed: the registers the
    declared pass writes are files under ``kb-root/`` and are not documents, and
    a glob here would assert something no stage claims.
    """
    return {path: document.text for path, document in tree.read(kb_root).documents.items()}


# ---------------------------------------------------------------------------
# The one thing the launch line does not carry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("beside", "expected"),
    [
        pytest.param(("one.bib",), ("one.bib",), id="the-one-beside-the-sources"),
        pytest.param((), (), id="none-at-all"),
        # Created in the reverse of the order they come back in: a key two files
        # define resolves to the first passed, so the order cannot be the
        # directory's.
        pytest.param(("two.bib", "one.bib"), ("one.bib", "two.bib"), id="more-than-one"),
    ],
)
def test_the_bibliography_is_resolved_from_where_the_sources_sit(
    tmp_path: Path, beside: Sequence[str], expected: Sequence[str]
) -> None:
    """Every ``.bib`` beside the sources is passed, sorted, and several is not an error.

    The launch line carries sources and nothing else, so this is where the
    document graph's bibliographies come from. The reader merges them and
    renders only cited entries, so the set needs no narrowing — what it does
    need is a fixed order, a key two files define resolving to the first.
    """
    (tmp_path / "corpus").mkdir()
    (tmp_path / "corpus" / "volume.tex").write_text("", encoding="utf-8")
    for name in beside:
        (tmp_path / "corpus" / name).write_text("", encoding="utf-8")

    found = run.bibliographies_beside(tmp_path, ("corpus/volume.tex",))

    assert [path.name for path in found] == list(expected)


# ---------------------------------------------------------------------------
# One whole paper, twenty lines, two deliberate properties — and two .bib files
#
# `stray.bib` sits beside the source answering nothing the paper cites, which is
# what four of fifty surveyed arXiv papers ship and what this walk used to stop
# on: the run refused to pick between candidates. There was never a pick to make
# — the reader takes `--bibliography` repeatedly, merges what it is given, and
# renders only cited entries — so the walk passes both and the stray one is
# inert.
#
# Driven here rather than as a `kb_docgraph` fixture because the defect it was
# fabricated to catch did not surface in the document graph: that stage built a
# clean tree and exited 0. It surfaced two stages later, at `graph-init`'s fused
# verify, as a citation-grammar violation — the quoted title having become
# quoted link text, which is that gate's excerpt grammar. Only a walk that
# reaches `graph-init` sees it, and this file is where a fresh entry walks.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lamb(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, run.Result]:
    """A build of the twenty-line paper, walked to completion spending nothing."""
    root = tmp_path_factory.mktemp("lamb")
    consumer = _make_fresh_consumer(root / "consumer", corpus=_LAMB, files=(*LAMB_SOURCES, "lamb.bib", "stray.bib"))
    result = _walk_head(
        consumer,
        root / "runs",
        stages=kb_pipeline.STAGE_IDS,
        overrides={"sources": LAMB_SOURCES, "no_inference": True},
    )
    return consumer, result


def test_the_head_walks_a_repository_that_holds_only_its_sources(lamb: tuple[Path, run.Result]) -> None:
    """Every stage recorded, from a repository with no KB in it — and the worktree clean after.

    The ordering rule, observed: each boundary sweeps what its stage wrote. It
    is what lets the seed run at all — its preflight refuses a dirty worktree,
    and the tree the stage before it wrote is exactly that until the boundary
    commit lands.
    """
    consumer, result = lamb

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert kb_pipeline.recorded_stages(consumer) == set(kb_pipeline.STAGE_IDS)
    assert kb_util.document_tree_present(consumer)
    assert (kb_util.kb_root(consumer) / kb_util.INDEX_DIRNAME).is_dir()
    assert kb_util.targets_installed(consumer)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=consumer, capture_output=True, text=True, check=True
    ).stdout
    assert status.strip() == ""


def test_a_subtitled_title_does_not_fail_the_build(lamb: tuple[Path, run.Result]) -> None:
    """The defect, at the stage it actually fired.

    A YAML emitter quotes a scalar containing ``": "``; a reader that kept the
    quotes put them in the volume's heading, its entry-point link text and its
    directory slug. The build then died at `graph-init` reporting a
    citation-grammar violation, because quoted link text *is* that gate's
    excerpt grammar — an error naming nothing resembling a title.
    """
    consumer, result = lamb

    assert result.exit_code == baton.EXIT_OK, result.detail
    kb_root = kb_util.kb_root(consumer)
    entry_point = (kb_root / kb_index_lib.ENTRY_POINT_FILENAME).read_text(encoding="utf-8")

    assert "Mary and Her Lamb: An Account" in entry_point
    assert '"Mary' not in entry_point, entry_point
    # The slug is taken from the same string, so a quote that survived would be
    # visible here too — and this is the half no gate would ever have caught.
    volumes = [entry.name for entry in kb_root.iterdir() if entry.is_dir() and entry.name != kb_util.INDEX_DIRNAME]
    assert volumes == ["mary-and-her-lamb-an-account"]


def test_a_corpus_declaring_no_result_builds_an_empty_claim_graph(lamb: tuple[Path, run.Result]) -> None:
    """The second property: a paper with no claim-bearing environment anywhere.

    A legitimate build whose claim graph is empty — every document stamped,
    every gate green, and no register entry minted, because the corpus states no
    result to mint one for.
    """
    consumer, result = lamb

    assert result.exit_code == baton.EXIT_OK, result.detail
    kb_root = kb_util.kb_root(consumer)
    documents = _documents(kb_root)

    assert documents
    assert [path for path, text in documents.items() if FRONTMATTER_OPENER not in text] == []
    assert kb_index_lib.scan_authored_ids(kb_root) == {}
    # No claim-bearing block means no volume register was ever created.
    assert list(kb_root.rglob("claim-quality.md")) == []


def test_the_resolved_citation_path_reaches_the_leaf_and_the_reference_list(lamb: tuple[Path, run.Result]) -> None:
    """The first of the paper's three citation states, walked end to end.

    Two bibliographies stand beside the source, so citeproc resolves the paper's
    one ``\\cite`` out of their union, to author-year prose, and the volume gains
    its references leaf. Both halves are asserted because they fail apart: a
    citation can survive with no reference list behind it, and this is the path
    where it should have one.

    The third assertion is the stray file's: an uncited entry reaches no
    document, so a reference list naming it would mean the union had been
    rendered rather than the citations resolved.
    """
    consumer, _ = lamb
    kb_root = kb_util.kb_root(consumer)
    tree_text = "\n".join(path.read_text(encoding="utf-8") for path in kb_root.rglob("*.md"))

    assert 'data-cites="nobody2026"' in tree_text
    assert "Nobody" in tree_text, "citeproc resolved nothing"
    assert [path.name for path in kb_root.rglob("references.md")] == ["references.md"]
    assert "strayentry1999" not in tree_text and "A Work This Paper Does Not Cite" not in tree_text
