"""The build's head, against a repository holding sources and no ``kb-root/``.

The state under test is the one the launch command is run against: a consuming
repository carrying the installed toolchain, its LaTeX sources, and no KB at
all. Nothing is faked. The document graph converts the staged corpus through the
real reader, ``graph-init`` seeds the spine for real, the declared claim-graph
pass runs over the tree that produced, every stage's coverage check runs, and
every boundary is a real commit in a real repository — so a green here says the
head's stages actually produce what the tail assumes it was handed.

**The walk stops before ``claims-discovered``.** That stage spends one inference
per awaiting document, and the model it spends is ``kb_claimgraph``'s own — no
flag of this driver replaces it, so a suite that walked into it would spend
inference on every run. ``stages`` is the seam ``run.execute`` already declares
for exercising part of the table.

**One walk here goes the whole way, and spends nothing to do it.** Under
``--no-inference`` every row that would cost a model call is dropped, so a fresh
entry walks past both claim-graph stages and closes the build out — the only way
this suite reaches them at all. That walk has a consuming repository of its own,
because it advances a ledger every other test in this file reads at the cut.

The corpus is two volume roots rather than all of them: what is proved here is
that the driver drives the front ends, and the front ends' own coverage is
``test_docgraph_build.py``'s.
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

from kb_tools import kb_pipeline, kb_util
from kb_tools.kb_claimgraph import conform, tree
from kb_tools.kb_driver import barriers, baton, config, run, runlog, steps
from kb_tools.kb_write.render import FRONTMATTER_OPENER

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _REPO_ROOT / "gen-defs.py"

#: How many volume roots one walk drives. Two is enough to prove the driver
#: passes a multi-source launch through; more only lengthens the run.
_SOURCES_PER_WALK = 2

#: Every stage of the head whose rows spend no inference, with ``start`` ahead
#: of them. Read off the vocabulary rather than transcribed, so the cut is
#: stated once: everything up to but not including the stage that asks a model.
HEAD_STAGES = kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index("claims-discovered")]


@pytest.fixture(scope="module")
def sources(volume_roots: tuple[str, ...]) -> tuple[str, ...]:
    return volume_roots[:_SOURCES_PER_WALK]


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
    sources: Sequence[str],
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
        config=config.load(
            None, run_overrides={"sources": tuple(sources), **overrides}, admissible=barriers.ADMISSIBLE
        ),
        paths=paths,
        decisions=[config.parse_decision("start.proceed=yes", admissible=barriers.ADMISSIBLE)],
        repo_root=consumer,
        stages=stages,
    )


@pytest.fixture(scope="module")
def fresh_consumer(
    staged_corpus: Path, sources: tuple[str, ...], bibliography: str, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    return _make_fresh_consumer(
        tmp_path_factory.mktemp("fresh") / "consumer", corpus=staged_corpus, files=(*sources, bibliography)
    )


@pytest.fixture(scope="module")
def walked(fresh_consumer: Path, sources: tuple[str, ...], tmp_path_factory: pytest.TempPathFactory) -> run.Result:
    return _walk_head(fresh_consumer, tmp_path_factory.mktemp("head-runs"), sources=sources, stages=HEAD_STAGES)


def _documents(kb_root: Path) -> dict[str, str]:
    """The tree's documents, by the walk the verifiers themselves use.

    Read through ``kb_claimgraph.tree`` rather than globbed: the registers the
    declared pass writes are files under ``kb-root/`` and are not documents, and
    a glob here would assert something no stage claims.
    """
    return {path: document.text for path, document in tree.read(kb_root).documents.items()}


def test_the_head_walks_a_repository_that_holds_only_its_sources(walked: run.Result, fresh_consumer: Path) -> None:
    """Every mechanical head stage recorded, from a repository with no KB in it."""
    assert walked.exit_code == baton.EXIT_OK, walked.detail
    assert kb_pipeline.recorded_stages(fresh_consumer) == set(HEAD_STAGES)


def test_the_document_graph_stage_writes_the_tree_the_rest_of_the_build_reads(
    walked: run.Result, fresh_consumer: Path, sources: tuple[str, ...]
) -> None:
    """One volume directory per source, under the KB root the toolchain hard-codes."""
    assert walked.exit_code == baton.EXIT_OK, walked.detail
    kb_root = kb_util.kb_root(fresh_consumer)

    assert kb_util.document_tree_present(fresh_consumer)
    volumes = [entry.name for entry in kb_root.iterdir() if entry.is_dir() and entry.name != kb_util.INDEX_DIRNAME]
    assert len(volumes) == len(sources)


def test_the_seed_stage_installs_the_spine_the_claim_graph_needs(walked: run.Result, fresh_consumer: Path) -> None:
    """Both halves of the seed: the derived-index directory and the runner include line.

    The include line is what makes ``kb-refresh`` and ``kb-verify`` real targets
    in this repository — which is what the pass after it exits on.
    """
    assert walked.exit_code == baton.EXIT_OK, walked.detail

    assert (kb_util.kb_root(fresh_consumer) / kb_util.INDEX_DIRNAME).is_dir()
    assert kb_util.targets_installed(fresh_consumer)


def test_the_declared_pass_stamps_every_document_of_the_tree(walked: run.Result, fresh_consumer: Path) -> None:
    """The claim graph's own product, over the tree the stage before it derived."""
    assert walked.exit_code == baton.EXIT_OK, walked.detail
    documents = _documents(kb_util.kb_root(fresh_consumer))

    assert documents
    assert [path for path, text in documents.items() if FRONTMATTER_OPENER not in text] == []


def test_every_stage_commits_its_own_product_and_leaves_the_worktree_clean(
    walked: run.Result, fresh_consumer: Path
) -> None:
    """The ordering rule, observed: each boundary sweeps what its stage wrote.

    It is what lets the seed run at all — its preflight refuses a dirty
    worktree, and the tree the stage before it wrote is exactly that until the
    boundary commit lands.
    """
    assert walked.exit_code == baton.EXIT_OK, walked.detail
    subjects = subprocess.run(
        ["git", "log", f"--grep=^{kb_pipeline.LEDGER_PREFIX}", "--format=%s", "--reverse"],
        cwd=fresh_consumer,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\n")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=fresh_consumer, capture_output=True, text=True, check=True
    ).stdout

    assert len([subject for subject in subjects if subject]) == len(HEAD_STAGES)
    assert status.strip() == ""


# ---------------------------------------------------------------------------
# Walking past the rows instead of stopping at them
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def walked_without_inference(
    staged_corpus: Path, sources: tuple[str, ...], bibliography: str, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, run.Result]:
    """A fresh build that spends no model call and finishes anyway.

    Its own consuming repository rather than ``fresh_consumer``'s, because this
    walk runs the whole pipeline and every other test in this file reads a
    ledger that stops at the cut.

    No ``--dry-run``: with every inference-spending row dropped there is nothing
    left for a replay to answer, so the walk runs against the real transport and
    never reaches it. That the run completes is itself the evidence that no row
    tried to spawn a model.
    """
    root = tmp_path_factory.mktemp("no-inference")
    consumer = _make_fresh_consumer(root / "consumer", corpus=staged_corpus, files=(*sources, bibliography))
    result = _walk_head(
        consumer,
        root / "runs",
        sources=sources,
        stages=kb_pipeline.STAGE_IDS,
        overrides={"no_inference": True},
    )
    return consumer, result


def test_a_fresh_build_spending_no_inference_runs_to_completion(
    walked_without_inference: tuple[Path, run.Result],
) -> None:
    """Exit 0 with every stage recorded — a finished build, not a resumable stop.

    This is the whole difference from the flag's previous meaning, and the two
    are otherwise easy to confuse: both leave the inference rows undone. A bound
    ended the walk and the ledger stopped where it stopped; here every stage is
    recorded, including the two the head could never reach without a model.
    """
    consumer, result = walked_without_inference

    assert result.exit_code == baton.EXIT_OK, result.detail
    assert kb_pipeline.recorded_stages(consumer) == set(kb_pipeline.STAGE_IDS)


def test_every_stage_that_lost_rows_records_which_ones(walked_without_inference: tuple[Path, run.Result]) -> None:
    """The build record states what it did without, because the KB cannot.

    A document discovery never read carries the same awaiting reason as one it
    read and found nothing in, and a corpus whose author cross-referenced
    nothing leaves a tree indistinguishable from one attribution never ran over.
    Counting anything in the KB answers neither, so the ledger is where a build
    says what it spent.
    """
    consumer, _ = walked_without_inference
    bodies = {
        stage: subprocess.run(
            ["git", "log", f"--grep=^{kb_pipeline.LEDGER_PREFIX} {stage} ", "--format=%b"],
            cwd=consumer,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        for stage in kb_pipeline.STAGE_IDS
    }

    noted = {stage for stage, body in bodies.items() if config.NO_INFERENCE_FLAG in body}
    assert noted == {stage for stage in kb_pipeline.STAGE_IDS if steps.inference_rows(stage)}
    # Not `depends-attributed`: its row drops in no build. The narrowing
    # settles what containment decides with no model, and the open pairs are
    # reported by the tool rather than dropped by the table. The tail's two are
    # the draft and the review, a model call each and a boundary each.
    assert noted == {"claims-discovered", "overview-drafted", "phase-5"}
    for stage in noted:
        for step_id in steps.inference_rows(stage):
            assert step_id in bodies[stage], stage


def test_the_documents_the_dropped_rows_would_have_read_are_left_for_a_later_pass(
    walked_without_inference: tuple[Path, run.Result],
) -> None:
    """Enrichment's entry condition, over the tree this build actually shipped.

    ``AWAITING`` is discovery's admission ticket and nothing here consumed it:
    every document the declared pass left awaiting still reads awaiting, so a
    later discovery run's scope is exactly those and nothing else.
    """
    consumer, _ = walked_without_inference
    state = conform.pass_two_gate(tree.read(kb_util.kb_root(consumer)))

    assert state.awaiting, "a corpus with nothing awaiting would make this vacuous"
    assert not state.determined, "no document carries an authored reason: nothing here authored one"


def test_the_validity_gates_ran_at_both_boundaries_that_declare_one(
    walked_without_inference: tuple[Path, run.Result],
) -> None:
    """The half of the coverage ruling that is never excused.

    ``_check_verify_gates`` is ``depends-attributed``'s postcondition as well as
    ``phase-3a``'s, and ``depends-attributed`` is a stage this build dropped a
    row from. A rule keyed on the stage rather than on the check would have
    excused it there. It is not excused, and the evidence is that the KB this
    build shipped passes the verifiers those boundaries run — which is what
    recording them means.
    """
    consumer, _ = walked_without_inference
    recorded = kb_pipeline.recorded_stages(consumer)

    assert {"depends-attributed", "phase-3a"} <= recorded
    assert kb_pipeline.advance_step(consumer, "phase-3a") == kb_pipeline.EXIT_OK
