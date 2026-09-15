"""The ``--dry-run`` rung: every stage, end to end.

The verification ladder's first rung, exercised through the **shipped entry
point** — ``python3 -m kb_tools.kb_driver run --config … --dry-run`` — against a
real consuming repository. Nothing about the pipeline is faked: the repo is a
git repo, the ledger ops are the real ``kb_util`` as a subprocess writing real
boundary commits, ``kb-refresh`` and ``kb-verify`` are the consuming repo's own
runner targets running the three real verifiers, every postcondition runs, the
run directory and its briefs and captures are written, the barriers are
answered the way an operator answers them, and the artifacts are validated
where the step table says they must be. The one substitution is the one the
flag names: ``replay.py`` stands in for the model.

**The consumer is a resume, not a launch, and under ``--dry-run`` that is
load-bearing.** Its tree is already built and ``kb-verify`` green, and its head
stages are already recorded in the ledger — which is the only thing that says an
invocation is continuing a build rather than opening one, there being no mode to
configure. So the head's tool rows are not walked here: a recorded stage is
never re-walked. That is what this file is for: the tail, walked by the shipped
entry point with only the model replaced.

``--dry-run`` replaces the calls **this driver dispatches** and nothing else, so
it does not reach a model spawned inside a tool the driver invokes. Those rows
are ``claims-discovered``'s and ``depends-attributed``'s, and a walk resuming
past their stages never reaches them — so every call left in this walk is the
driver's own and ``replay.py`` stands in for all of them. A build that must walk
those stages and spend nothing wants ``--no-inference``, which drops the rows
rather than replaying them — exercised in ``test_kb_driver_head.py`` and by this
file's own no-inference cases at the end.

**That is what makes a green here worth something.** ``p3a.gate`` is a real
composite gate over a real corpus; reaching ``phase-5`` means the synthetic
content the scenarios author (findings, meta-docs) survived every gate.

That is also what makes this a run-level test rather than a loop-level one —
the in-process seams of ``test_kb_driver_call.py`` and
``test_kb_driver_buildout.py`` cover the loop's decisions, and this covers the
claim those tests cannot make, that the flag reaches the seam through the CLI
a recipe actually invokes.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from kb_tools import kb_index_lib, kb_pipeline, kb_util
from kb_tools.kb_driver import barriers, baton, config, replay, run, runlog, steps

#: The directory holding the ``kb_tools`` package — this repository's root,
#: which is both the subprocess PYTHONPATH and the installer's own home.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INSTALLER = _REPO_ROOT / "gen-defs.py"

SOURCES = ("AcmeWidgets.tex", "AcmeWidgetsDerivations.tex")
SLUGS = ("acme-widgets", "acme-widgets-derivations")

#: Every stage, which is what rung 1 means. Sourced from the pipeline's
#: own vocabulary rather than transcribed: a stage added there must be walked,
#: and a test carrying its own list would go on passing without it.
ALL_STAGES = kb_pipeline.STAGE_IDS

#: The run overlay: the launch's own input, written into the gitignored scratch
#: tree so that the one file a session writes before launch cannot dirty the
#: worktree ``start``'s preflight is about to read.
CONFIG_NAME = "driver-run.toml"
CONFIG_PATH = f"{kb_pipeline.SCRATCH_RELROOT}/{CONFIG_NAME}"

#: Where this consumer's charter stands — read off the config rather than
#: spelled, so the fixture cannot disagree with the driver about where a build
#: looks for one. Tracked content, unlike the overlay above: the ``start``
#: boundary names this path permanently.
CHARTER_FILE = config.load(None, run_overrides={"sources": SOURCES}).run.charter_file

DRIVER_CONFIG = f"""\
[run]
sources = {json.dumps(list(SOURCES))}
permission_mode = "acceptEdits"
"""

#: The stages this consumer arrives with already behind it, and the ones the
#: driver is here to walk. Sliced from the pipeline's own vocabulary rather
#: than transcribed, so a stage inserted into the head joins the recorded set
#: instead of being walked for real against a fixture that cannot answer it.
HEAD_STAGES = kb_pipeline.STAGE_IDS[: kb_pipeline.STAGE_IDS.index("phase-3a")]
TAIL_STAGES = kb_pipeline.STAGE_IDS[kb_pipeline.STAGE_IDS.index("phase-3a") :]

#: The tree the pandoc front end hands off: two volume directories, two leaves
#: each — the least that distinguishes "one domain's worth of rows landed" from
#: "all of them did" while still giving every build-out row something to do at
#: integration scale. Written out rather than derived from anything: the driver
#: is handed a tree and is told nothing else about the corpus, so a fixture
#: computing one from a second source would be standing in for an input that
#: does not exist. A volume directory here is a domain the walk finds.
DERIVED_DOCUMENTS = (
    "entry-point.md",
    f"{SLUGS[0]}/index.md",
    f"{SLUGS[0]}/overview.md",
    f"{SLUGS[0]}/details.md",
    f"{SLUGS[1]}/index.md",
    f"{SLUGS[1]}/overview.md",
    f"{SLUGS[1]}/details.md",
)

#: The frontmatter each kind of document carries, by the filename that says
#: which kind it is. Claimless throughout, which is what a build authoring no
#: claims looks like: the entry point and every index declare an empty
#: ``subtree-claims`` — refresh recomputes the real rollup — and a leaf declares
#: no result of its own.
_ENTRY_POINT_FRONTMATTER = "kind: entry-point\nsubtree-claims: []\nsubtree-experiments: []\nbootstrap: true"
_INDEX_FRONTMATTER = "kind: index\nsubtree-claims: []\nsubtree-experiments: []"
_LEAF_FRONTMATTER = "kind: leaf\nno-claim: fixture leaf, carrying no result of its own"


def _frontmatter(relative: str) -> str:
    if relative == kb_index_lib.ENTRY_POINT_FILENAME:
        return _ENTRY_POINT_FRONTMATTER
    return _INDEX_FRONTMATTER if relative.endswith(kb_index_lib.INDEX_FILENAME) else _LEAF_FRONTMATTER


def _write_derived_tree(kb_root: Path) -> None:
    """:data:`DERIVED_DOCUMENTS` on disk, stamped as the pandoc pipeline leaves them."""
    for relative in DERIVED_DOCUMENTS:
        target = kb_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        title = Path(relative).stem
        target.write_text(
            f"<!-- kb-frontmatter\n{_frontmatter(relative)}\n-->\n\n# {title}\n\nContent.\n", encoding="utf-8"
        )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}


def _run_ok(*args: str, cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, env=_env(), capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, f"{args}:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _make_consumer(root: Path, *, opened: bool = True) -> Path:
    """A committed, preflight-clean, ``kb-verify``-green consuming repo — a resume's entry state.

    The toolchain arrives in ``.claude/`` the one way a consuming project ever
    receives it — by running the installer, ``gen-defs.py install``, which
    ``just install`` wraps. Not a symlink standing in for one: the packages
    land at their frozen ``.claude/agents/…`` destinations because the
    installer put them there, so a package that fails to arrive fails this
    fixture rather than passing behind a link.

    The runner include line is installed directly — ``kb_util install-targets``
    is what a consumer resuming into the tail already carries from the head of
    its own build, since ``seed.graph-init`` (which installs it) is not a row
    this walk reaches. That is also what makes ``just kb-refresh`` and ``just
    kb-verify`` real commands in this repo.

    The KB tree is authored here directly rather than surveyed and distilled:
    that is the pandoc front end's job now, run before this driver ever sees
    the repo, and standing it up as a fixture is what makes it available to
    stand in for.

    ``opened`` records the head in the ledger, which is what makes an invocation
    against this repo a resume — the driver reads position from the recorded
    stages and from nothing else, so a consumer whose tree stands and whose
    ledger is empty is not a resume but a launch, and a launch over a populated
    ``kb-root/`` is exactly what ``pre.kb-root`` refuses. The two cases that
    want a barrier of the ``start`` stage pass ``opened=False`` and stop before
    that row.
    """
    root.mkdir()
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
    for name in kb_util.DOCENT_COMMAND_FILENAMES:
        assert (claude / kb_util.COMMANDS_DIRNAME / name).is_file(), name

    (root / ".gitignore").write_text(f"{kb_util.SCRATCH_DIRNAME}/\n", encoding="utf-8")

    # The charter goes down before the seed commit, because it is tracked
    # content: left uncommitted it would fail the clean-worktree check
    # ``start``'s preflight makes, and a consumer whose charter is untracked is
    # one whose ledger entry outlives the file it names.
    charter = root / CHARTER_FILE
    charter.parent.mkdir(parents=True, exist_ok=True)
    charter.write_text("# Build charter\n\nEverything in the two domains.\n", encoding="utf-8")

    kb_root = kb_util.kb_root(root)
    kb_root.mkdir(parents=True, exist_ok=True)
    _write_derived_tree(kb_root)

    # `install-targets` derives its root from the working directory and
    # requires `kb-root/` to resolve it, so the tree goes down first.
    _run_ok(sys.executable, "-m", "kb_tools.kb_util", kb_util.OP_INSTALL_TARGETS, "--runner", "just", cwd=root)

    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed: the tree the pandoc pipeline hands off")

    _run_ok(sys.executable, "-m", "kb_tools.refresh_kb_metadata", cwd=root)
    _run_ok("just", kb_util.TARGET_VERIFY, cwd=root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "refresh: .index/ matches the authored tree")

    overlay = root / CONFIG_PATH
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(DRIVER_CONFIG, encoding="utf-8")

    if opened:
        # With the charter the run resolves, so the recorded boundary names the
        # same path the driver's own `start.record` would have named it.
        _run_ok(
            sys.executable,
            "-m",
            "kb_tools.kb_util",
            kb_util.OP_START_BUILD,
            kb_util.CHARTER_FLAG,
            str(CHARTER_FILE),
            cwd=root,
        )
        for stage in HEAD_STAGES[1:]:
            _run_ok(sys.executable, "-m", "kb_tools.kb_util", kb_util.OP_ADVANCE_STEP, "--stage", stage, cwd=root)
    return root


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    """A consuming repo of this test's own, for a case that drives it somewhere."""
    return _make_consumer(tmp_path / "consumer")


@pytest.fixture
def unopened_consumer(tmp_path: Path) -> Path:
    """The same repo with an empty ledger — the only state a ``start`` row is walked in."""
    return _make_consumer(tmp_path / "unopened", opened=False)


def drive_without(consumer: Path, *args: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
    """One driver invocation with no mode flag but the ones the caller passes.

    ``--run-dir`` points outside the consuming repo because a restage wipes
    ``.claude-temp/``, and the run directory is the evidence.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_driver",
            "run",
            "--config",
            CONFIG_PATH,
            "--run-dir",
            str(run_dir),
            *args,
        ],
        cwd=consumer,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def drive(consumer: Path, *args: str, run_dir: Path) -> subprocess.CompletedProcess[str]:
    """The rung's own invocation: the replay seam, exactly as a runner recipe makes it."""
    return drive_without(consumer, config.DRY_RUN_FLAG, *args, run_dir=run_dir)


def recorded_stages(consumer: Path) -> frozenset[str]:
    """The recorded set, read back through ``show-status`` rather than assumed."""
    status = subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_util", kb_util.OP_SHOW_STATUS],
        cwd=consumer,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert status.returncode == 0, status.stderr
    return frozenset(kb_pipeline.recorded_stages(consumer))


def boundary_commits(consumer: Path) -> list[str]:
    """The ledger's boundary commits, oldest first — one per recorded stage."""
    log = subprocess.run(
        ["git", "log", f"--grep=^{kb_pipeline.LEDGER_PREFIX}", "--format=%H", "--reverse"],
        cwd=consumer,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return log.stdout.split()


def recorded_charter(consumer: Path) -> str | None:
    """The charter path the ``start`` boundary recorded, or ``None`` where it named none.

    Read out of the commit body rather than off the config, because the ledger is
    the durable record and the only place a build that was handed a charter is
    told from one that was not.
    """
    body = subprocess.run(
        ["git", "log", f"--grep=^{kb_pipeline.LEDGER_PREFIX} {kb_pipeline.FIRST_STAGE_ID} ", "--format=%b"],
        cwd=consumer,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    if body == kb_pipeline.NO_CHARTER_BODY:
        return None
    assert body.startswith(kb_pipeline.CHARTER_BODY_FIELD), body
    return body.removeprefix(kb_pipeline.CHARTER_BODY_FIELD).strip()


def write_config(consumer: Path, name: str, body: str) -> str:
    """A second run overlay in the scratch tree, for a case the default cannot express."""
    relpath = f"{kb_pipeline.SCRATCH_RELROOT}/{name}"
    (consumer / relpath).write_text(DRIVER_CONFIG + body, encoding="utf-8")
    return relpath


def execute_in_process(
    consumer: Path,
    runs: Path,
    *,
    scenario: replay.Scenario,
    run_id: str = "20260901T120000-1",
    decisions: Sequence[str] = (),
    stages: Sequence[str] = ALL_STAGES,
    config_path: str = CONFIG_PATH,
) -> run.Result:
    """One run against the real consuming repo, with only the model replaced.

    The same substitution ``--dry-run`` makes, driven in-process because the
    scenario varies: the CLI's flag selects the *green* invoker by design, and
    the shapes under test here are the ones a green run never produces. The
    ledger ops, the runner targets, and the postconditions are still real.
    """
    paths = runlog.prepare(runs, run_id)
    lock = runlog.repo_lock_path(consumer)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "run_id": paths.run_id}), encoding="utf-8")
    return run.execute(
        config=config.load(consumer / config_path, admissible=barriers.ADMISSIBLE),
        paths=paths,
        decisions=[config.parse_decision(spec, admissible=barriers.ADMISSIBLE) for spec in decisions],
        invoker=replay.ReplayInvoker(scenario),
        repo_root=consumer,
        stages=stages,
    )


# ---------------------------------------------------------------------------
# The rung
# ---------------------------------------------------------------------------


#: A resume raises no barrier of the ``start`` stage, so nothing here supplies
#: one: a ``--decide`` this walk never raises would be reported unconsumed, and
#: that report is one of the things asserted below.
NO_ANSWERS: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanRun:
    """One finished green run: the repo it left behind, and the evidence beside it."""

    consumer: Path
    result: subprocess.CompletedProcess[str]
    run_dir: Path


@pytest.fixture(scope="module")
def clean_run(tmp_path_factory: pytest.TempPathFactory) -> CleanRun:
    """One green whole-pipeline replayed run, shared by every test that only reads it.

    A clean run is the most expensive thing in this file and the questions asked
    of it are independent, so it is walked once and interrogated many times.

    **Every consumer of this fixture must be read-only against the run.**
    Nothing enforces that mechanically. A test that writes into the shared
    consumer repository — or drives it one stage further — makes these results
    order-dependent, and the failure will surface in some later test as what
    looks like a driver bug. A case whose *input* differs, or that needs to
    mutate what it inspects, takes the function-scoped ``consumer`` fixture and
    pays for its own run.
    """
    root = tmp_path_factory.mktemp("clean-run")
    consumer = _make_consumer(root / "consumer")
    runs = root / "runs"
    result = drive(consumer, *NO_ANSWERS, run_dir=runs)
    return CleanRun(
        consumer=consumer,
        result=result,
        run_dir=Path((runs / "LATEST").read_text(encoding="utf-8").strip()),
    )


def test_every_stage_runs_green_under_dry_run(clean_run: CleanRun) -> None:
    """Rung 1, exit 0, with the ledger — not the driver — asked what happened."""
    consumer, result = clean_run.consumer, clean_run.result

    assert result.returncode == baton.EXIT_OK, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert recorded_stages(consumer) == set(ALL_STAGES)

    # The findings the one review row declares, and the meta-docs the last
    # stage writes — every artifact a row still standing after the cascade
    # declares.
    scratch = consumer / steps.SCRATCH_ROOT
    assert (
        scratch
        / steps.findings(stage="phase-5", series=steps.SERIES_INITIAL, round_number=1, author=steps.META_REVIEW_SEAT)
    ).is_file()
    kb_root = kb_util.kb_root(consumer)
    assert (kb_root / "README.md").is_file()
    assert (kb_root / "CONVENTIONS.md").is_file()

    # The tree this walk reviews is the one the fixture authored — this driver
    # writes no leaf, no index and no entry point of its own, and edits none.
    # It is also the only thing the walk was told about the corpus: the domain
    # partition every fix wave sized against was read off these directories.
    walked = {path.relative_to(kb_root).as_posix() for path in kb_root.rglob("*.md")}
    assert set(DERIVED_DOCUMENTS) <= walked
    for relative in DERIVED_DOCUMENTS:
        assert (kb_root / relative).read_text(encoding="utf-8").endswith("\n\nContent.\n"), relative
    # What the walk added is the overview document and the stamped orientation
    # docs, every one of them at the root: no volume directory gained a file.
    assert {path for path in walked - set(DERIVED_DOCUMENTS) if "/" in path} == set()

    # The run ends on a baton, and exit.json is what a relaying session reads.
    assert f"{baton.PREFIX} ASK THE USER:" in result.stdout
    payload = json.loads((clean_run.run_dir / "exit.json").read_text(encoding="utf-8"))
    assert payload["exit_code"] == baton.EXIT_OK
    assert payload["unconsumed_decisions"] == []


def test_a_replayed_run_still_writes_its_briefs_and_captures(clean_run: CleanRun) -> None:
    """The brief is on disk before the call, and the stream is captured.

    Dry-run replaces the model, not the pipeline — so the evidence a real run
    leaves is the evidence this one leaves, which is what makes the rung worth
    running at all.
    """
    briefs = sorted((clean_run.run_dir / "briefs").iterdir())
    captures = sorted((clean_run.run_dir / "calls").iterdir())
    asked = [path.name.split("-", 1)[1].removesuffix(".md") for path in briefs]

    # Every calling row a clean run reaches, asked exactly once, in table order.
    # What it does not reach is the one remediation row left, because nothing
    # came back red. That is the assertion worth making here: a green run that
    # had quietly entered a fix cycle would still record every stage.
    remediation = {"p5.fix"}
    assert asked == [step_id for step_id in replay.SCENARIOS if step_id not in remediation]
    assert len(captures) == len(briefs)
    assert all(path.stat().st_size > 0 for path in captures)


def test_a_replayed_run_leaves_a_cadence_record_for_every_call(clean_run: CleanRun) -> None:
    """One record per capture, naming the step, its stage, and what it cost.

    The producer half shipped without the consumer: ``replay.result_event`` has
    always carried the duration and cost fields "cadence extraction reads",
    against an extraction that did not exist. Two acceptance criteria stand on
    this file — the rung-3 cadence and the <2% driver-overhead bound — and
    neither can be evaluated from a path that is only provisioned.
    """
    run_dir = clean_run.run_dir
    captures = sorted((run_dir / "calls").iterdir())
    records = [json.loads(line) for line in (run_dir / "cadence.jsonl").read_text(encoding="utf-8").splitlines()]

    assert len(records) == len(captures)
    assert [record["seq"] for record in records] == sorted(record["seq"] for record in records)
    # Every record resolves to a real step and a real stage — which is the half
    # the filename grammar has to get right, hyphenated step ids included.
    assert {record["step"] for record in records} <= set(steps.STEPS_BY_ID)
    assert {record["stage"] for record in records} <= set(ALL_STAGES)
    assert all(record["duration_ms"] is not None and record["cost_usd"] is not None for record in records)


def test_an_unanswered_barrier_still_stops_a_replayed_run(unopened_consumer: Path, tmp_path: Path) -> None:
    """Under ``--dry-run``: the barriers are real, and the record is the message body.

    The unopened repo, because ``start.proceed`` is a row of the ``start``
    stage and a resume skips every one of them — an empty ledger is the only
    state in which this barrier is raised at all.
    """
    consumer = unopened_consumer
    result = drive(consumer, run_dir=tmp_path / "runs")

    assert result.returncode == baton.EXIT_BARRIER, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert recorded_stages(consumer) == set(), "nothing was recorded before the confirmation was answered"

    run_dir = Path((tmp_path / "runs" / "LATEST").read_text(encoding="utf-8").strip())
    payload = json.loads((run_dir / "exit.json").read_text(encoding="utf-8"))
    record = Path(payload["barrier_record"])
    assert record.is_file()
    assert "# Barrier: start / proceed" in record.read_text(encoding="utf-8")


def test_the_recorded_charter_resolves_after_scratch_is_deleted_wholesale(consumer: Path) -> None:
    """The charter has one home and staging cannot reach it.

    A build's charter is resolved from ``[run] charter_file``, whose default is
    the tracked path ``kb_pipeline.CHARTER_RELPATH``, and the ``start``
    boundary's body names what the build was told (``charter: <path>``, or
    ``kb_pipeline.NO_CHARTER_BODY`` where it was told nothing). So the question a
    wiped scratch tree asks is answerable from the ledger alone: staging deletes
    ``.claude-temp/`` wholesale, and the path recorded there must still resolve.

    A function-scoped consumer rather than the shared clean run, because this
    case destroys part of the repository it reads.
    """
    shutil.rmtree(consumer / kb_util.SCRATCH_DIRNAME)

    recorded = recorded_charter(consumer)

    assert recorded is not None, "the start boundary named no charter to resolve"
    assert (consumer / recorded).is_file(), f"{recorded} did not survive the wipe"


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def test_the_run_resumes_from_every_stage_boundary(consumer: Path, tmp_path: Path) -> None:
    """At every boundary: a new invocation reads its position and advances.

    Built by walking one stage further each time, so that each invocation meets
    a ledger a previous one left — the position a killed run actually leaves,
    with exactly the scratch that position produced. Resetting a *finished*
    repository back to a boundary commit would be a cheaper construction and a
    dishonest one: it pairs an early ledger with a late scratch tree, a state
    no run can reach, and what it then tests is stale-artifact handling rather
    than resume.

    **No invocation carries a mode and none needs one.** Position is the
    recorded-stage set and nothing else, so each of these continues where the
    last one stopped on the strength of the ledger alone — the head stages the
    fixture recorded included, which is why the first invocation here starts at
    the tail rather than re-deriving a tree that already stands.

    Every invocation re-reads the ledger, opens each stage's rounds at
    ``run.FIRST_ROUND``, and re-walks the domain partition off ``kb-root/`` —
    which is the machinery a resume actually leans on.
    """
    for index, stage in enumerate(TAIL_STAGES):
        walked = ALL_STAGES[: len(HEAD_STAGES) + index + 1]
        result = execute_in_process(
            consumer,
            tmp_path / f"runs-{index:02d}",
            scenario=replay.green_run(),
            run_id=f"20260901T1200{index:02d}-1",
            stages=walked,
        )

        assert result.exit_code == baton.EXIT_OK, f"the invocation ending at {stage}: {result.detail}"
        assert recorded_stages(consumer) == set(walked), f"resume did not advance to {stage}"

    assert recorded_stages(consumer) == set(ALL_STAGES)
    assert len(boundary_commits(consumer)) == len(ALL_STAGES)


# ---------------------------------------------------------------------------
# Exit codes, in one place
# ---------------------------------------------------------------------------


def test_every_run_mode_exit_code_in_the_design_is_reached(
    consumer: Path, unopened_consumer: Path, tmp_path: Path
) -> None:
    """The whole run-mode ladder, each code reached by the thing that causes it.

    One test rather than nine scattered ones, because the claim is about the
    *set*: a code nothing reaches is a baton nobody sees, and the way that goes
    unnoticed is each code having a home somewhere and no one asking whether
    the ladder is covered. The two watch codes are excluded here and asserted
    by ``test_kb_driver_watch.py`` — they belong to a different mode's set, not
    to this one.

    **``EXIT_INTERNAL`` (15) is excluded too, and this is new.** Its one
    config-reachable cause — two source volumes whose slugs collided, caught by
    the source survey's own manifest writer — is gone with that survey: every
    ``runlog.require`` left in the run loop now guards a driver-internal
    invariant (a row with no handler, a loop row carrying two capped series)
    rather than anything a config or a scripted return can reach from outside.
    Flagged rather than worked around: it is a real gap in this rung's coverage
    of the ladder, not a case this file forgot to write.

    **``EXIT_COVERAGE`` (19) is excluded on the watch codes' terms, not on 15's.**
    It says a stage's own declared output was not on disk when its boundary was
    recorded, and this fixture's whole premise is a tree that is there and a
    ``kb-verify`` that is green — the state a coverage refusal is the absence of.
    Reaching it here would mean dismantling the fixture between a row and its
    record. It is reached instead in ``test_kb_driver_baton.py``, where a head
    record row is refused by the real ``kb_pipeline`` through the ledger adapter,
    so the ladder's coverage holds across two files as it already does for 21
    and 22.

    Each entry names the cause, not the mechanism: what a reader needs from
    this table is what makes a build exit 14 rather than 11.
    """
    reached: dict[int, str] = {}

    def note(code: int, cause: str) -> None:
        assert code not in reached, f"{cause} and {reached[code]} both reached {code}"
        reached[code] = cause

    def _copy(source: Path, name: str) -> Path:
        target = tmp_path / "repos" / name
        target.parent.mkdir(exist_ok=True)
        shutil.copytree(source, target)
        return target

    def fresh(name: str) -> Path:
        """A pristine copy of the consuming repo, so no case inherits another's ledger.

        Each cause below states its own precondition by walking to it from
        nothing. Sharing one repository would make this a test of the order the
        cases happen to be written in.
        """
        return _copy(consumer, name)

    def unopened(name: str) -> Path:
        """The same, with an empty ledger — for the two causes that live in ``start``.

        ``start``'s rows are walked only where the stage is unrecorded, so a
        barrier of that stage and preflight's own refusal are reachable from
        this copy and from no other.
        """
        return _copy(unopened_consumer, name)

    # 0 — every walked stage recorded. The validation gate rather than `start`,
    # which this consumer arrives with behind it: a stage walked for real and
    # recorded is what the code means.
    green = execute_in_process(fresh("ok"), tmp_path / "ok", scenario=replay.green_run(), stages=(TAIL_STAGES[0],))
    note(green.exit_code, "a walk whose every stage recorded")

    # 10 — a barrier with no answer supplied.
    stopped = execute_in_process(
        unopened("barrier"), tmp_path / "barrier", scenario=replay.green_run(), stages=("start",)
    )
    note(stopped.exit_code, "an unanswered barrier")
    assert stopped.pair == barriers.START_PROCEED

    # 11 — a driver loop that spent its cap with findings still open.
    capped = execute_in_process(
        fresh("cap"),
        tmp_path / "cap",
        scenario=replay.by_step({**replay.SCENARIOS, "p5.review": replay.clean(replay.verdict(critical=1))}),
        stages=ALL_STAGES,
    )
    note(capped.exit_code, "a capped loop that exhausted its cap")
    assert capped.pair == "phase-5.cap-exhausted"

    # 17 — a return that could not carry its declared shape, twice.
    malformed = execute_in_process(
        fresh("contract"),
        tmp_path / "contract",
        scenario=replay.by_step({**replay.SCENARIOS, "p5.review": replay.clean("a return carrying no verdict")}),
        stages=ALL_STAGES,
    )
    note(malformed.exit_code, "a step that could not produce its declared output shape twice")

    # 12 — transport exhausted: the call went silent and the watchdog killed it.
    wedge_repo = fresh("wedge")
    wedged = execute_in_process(
        wedge_repo,
        tmp_path / "wedge",
        scenario=replay.by_step({**replay.SCENARIOS, "p5.review": replay.stall()}),
        stages=ALL_STAGES,
        config_path=write_config(
            wedge_repo, "impatient.toml", "\n[timeouts]\nsilence_seconds = 1\n\n[retry]\ntransport_attempts = 1\n"
        ),
    )
    note(wedged.exit_code, "a call that went silent past its watchdog")

    # 18 — the run stopped where it was told to, with stages left unwalked.
    # Taken through the CLI because the bound is a flag: `--through` is named
    # by the stage's *display* name here, which is the spelling an operator has
    # and the one no other case exercises.
    bounded_repo = fresh("bounded")
    note(
        _cli_exit(bounded_repo, tmp_path / "bounded", extra=("--through", "validation gate")),
        "a walk bounded short of the last stage",
    )
    # The bound is honoured and the ledger is left resumable at it: the stage
    # named is recorded, and nothing past it is.
    assert recorded_stages(bounded_repo) == set(HEAD_STAGES) | {TAIL_STAGES[0]}

    # 14 — the environment is not fit: preflight refuses a dirty worktree. An
    # unopened copy, preflight being a `start` row.
    unfit_repo = unopened("env")
    (unfit_repo / "stray.tex").write_text("untracked\n", encoding="utf-8")
    unfit = execute_in_process(unfit_repo, tmp_path / "env", scenario=replay.green_run(), stages=("start",))
    note(unfit.exit_code, "preflight refusing the environment")

    # 13 and 16 reach their code before the sequencer runs at all, and 15 is
    # raised from inside it — so both are taken through `cli.main`, which is
    # where that translation lives.
    note(_cli_exit(fresh("cfg"), tmp_path / "cfg", config_path="nowhere.toml"), "a config that is not there")

    locked = tmp_path / "locked"
    locked.mkdir()
    locked_repo = fresh("locked")
    held = runlog.repo_lock_path(locked_repo)  # the REPO's lock, not the run directory's
    held.parent.mkdir(parents=True, exist_ok=True)
    held.write_text(json.dumps({"pid": os.getpid(), "run_id": "held"}), encoding="utf-8")
    note(_cli_exit(locked_repo, locked), "another driver run alive in this repository")

    expected = set(baton.RUN_MODE_EXIT_CODES) - {baton.EXIT_INTERNAL, baton.EXIT_COVERAGE}
    assert set(reached) == expected, "\n".join(f"{code}: {cause}" for code, cause in sorted(reached.items()))


def _cli_exit(consumer: Path, runs: Path, *, config_path: str = CONFIG_PATH, extra: Sequence[str] = ()) -> int:
    """One shipped-entry-point invocation, for the codes chosen outside the sequencer.

    13 and 16 are decided before the walk starts, so both are taken through the
    layer that translation lives in. ``extra`` carries the flags a case is
    actually about — 18's bound is a flag, so it can only be asked for here.
    """
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "kb_tools.kb_driver",
            "run",
            "--config",
            config_path,
            config.DRY_RUN_FLAG,
            "--run-dir",
            str(runs),
            *extra,
        ],
        cwd=consumer,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).returncode


# ---------------------------------------------------------------------------
# Spending no inference
# ---------------------------------------------------------------------------


def test_a_build_spending_no_inference_finishes_and_records_that_it_did(consumer: Path, tmp_path: Path) -> None:
    """``--no-inference`` through the shipped entry point: every stage recorded, exit 0.

    Not a bound and not a replay. The rows that would cost a model call are
    gone, every other row runs for real, and the walk reaches the last stage —
    which is the whole difference from the flag's previous meaning, where it
    ended the walk instead.

    ``--dry-run`` is deliberately absent: with the inference rows dropped there
    is nothing left for a replay to answer, and a run passing both would prove
    only that the two do not collide. What this asserts is that the build closed
    out with no invoker substituted at all.
    """
    result = drive_without(consumer, *NO_ANSWERS, config.NO_INFERENCE_FLAG, run_dir=tmp_path / "runs")

    assert result.returncode == baton.EXIT_OK, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert recorded_stages(consumer) == set(ALL_STAGES)

    bodies = {
        stage: subprocess.run(
            ["git", "log", f"--grep=^{kb_pipeline.LEDGER_PREFIX} {stage} ", "--format=%b"],
            cwd=consumer,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
        for stage in ALL_STAGES
    }

    # This walk resumes past the head, so the two meta-documentation stages are
    # the ones it records here that lose any row — the draft and the review being
    # a stage each, one model call each — and each boundary names the rows it lost
    # rather than counting them. The head's boundaries were written by the
    # fixture, without the flag, which is why they say nothing about it.
    noted = [stage for stage, body in bodies.items() if config.NO_INFERENCE_FLAG in body]
    assert noted == ["overview-drafted", "phase-5"]
    for stage in noted:
        for step_id in steps.inference_rows(stage):
            assert step_id in bodies[stage], stage


def test_the_meta_documents_are_absent_and_the_stage_records_anyway(consumer: Path, tmp_path: Path) -> None:
    """The coverage ruling, observed end to end at the boundaries it was made for.

    ``overview-drafted``'s postcondition asks whether the overview document was
    written, and ``phase-5``'s asks the same of what it then reviewed — an
    assertion that *this stage did its work*, and there is nothing to assert of
    a build whose rows were dropped. Both stages record with that unit vacuous,
    and the document really is not there.

    **``README.md`` and nothing beside it**: ``phase-3a``'s
    ``stamp_readiness_docs`` writes ``CONVENTIONS.md`` when absent, an earlier
    stage's own work and no part of this one's — which is why it is not in the
    set this walks. So the unit that would have refused this build is the
    README's alone, which is worth asserting rather than assuming: an excused
    unit and an absent one look the same from the exit code.
    """
    result = drive_without(consumer, *NO_ANSWERS, config.NO_INFERENCE_FLAG, run_dir=tmp_path / "runs")

    assert result.returncode == baton.EXIT_OK, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    kb_root = kb_util.kb_root(consumer)
    assert [name for name in kb_pipeline.META_DOCS if not (kb_root / name).is_file()] == ["README.md"]
    assert {"overview-drafted", "phase-5"} <= recorded_stages(consumer)


def test_the_validity_gate_still_runs_at_every_boundary_that_declares_one(consumer: Path, tmp_path: Path) -> None:
    """The half of the ruling that may not be relaxed, asserted where it would be lost.

    ``_check_verify_gates`` guards two boundaries — ``depends-attributed``'s and
    ``phase-3a``'s — and a validity unit is excused by nothing, whatever the
    build spent: a KB that cannot pass the verifiers cannot record the stage.
    Asserted here at ``phase-3a``, which is the first boundary declaring one
    that this walk reaches; ``depends-attributed``'s instance of the same check
    is behind a resume's recorded set and belongs to a walk that reaches its
    stage (``kb_tools/tests/test_kb_driver_head.py``, and the integration
    suite's own head walk).
    """
    kb_root = kb_util.kb_root(consumer)
    broken = kb_root / SLUGS[0] / "overview.md"
    broken.write_text(
        broken.read_text(encoding="utf-8") + "\n[a link to nowhere](./nowhere-at-all.md)\n", encoding="utf-8"
    )
    _git(consumer, "commit", "-aqm", "a dead link the verifiers will find")

    result = drive_without(consumer, *NO_ANSWERS, config.NO_INFERENCE_FLAG, run_dir=tmp_path / "runs")

    assert result.returncode == baton.EXIT_GATE_RED, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "phase-3a" not in recorded_stages(consumer)


def test_the_resume_line_carries_both_mode_flags_and_not_the_bound(consumer: Path, tmp_path: Path) -> None:
    """The two kinds of flag, told apart on the one card that has to get it right.

    Taken through a bounded run because a green terminal card resumes nothing
    and so renders no invocation. The card that does render one must hand back
    what the build is made of — dropping ``--no-inference`` would resume a build
    that spends the very calls this one was told to do without, and dropping
    ``--dry-run`` would spend real ones a smoke test never meant to — and must
    not hand back the bound, since resuming past it is what resuming is for.
    """
    result = drive(
        consumer,
        *NO_ANSWERS,
        config.NO_INFERENCE_FLAG,
        config.THROUGH_FLAG,
        "spine-seed",
        run_dir=tmp_path / "runs",
    )

    assert result.returncode == baton.EXIT_BOUNDED, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    resume = [line for line in result.stdout.splitlines() if f"{kb_util.DRIVER_INVOCATION} run " in line]
    assert resume, result.stdout
    assert all(config.NO_INFERENCE_FLAG in line and config.DRY_RUN_FLAG in line for line in resume)
    assert all(config.THROUGH_FLAG not in line for line in resume)
