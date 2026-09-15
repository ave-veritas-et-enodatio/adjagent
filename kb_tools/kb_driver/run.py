"""The outer loop: stage iteration, step execution, cap loops, resume, exit selection.

The sequencer. It walks ``steps.py``'s table in order, computes each row's
per-call slots, hands calls to ``call.py``, records stages through
``ledger.py``, raises registry barriers through ``barriers.py``, and chooses the
exit code the run ends on. It holds no template text, constructs no subprocess,
and reads no TOML.

**Nothing a later stage reads is carried in an attribute.**
:data:`RUNNER_ATTRIBUTES` is the closed set a :class:`Runner` may hold and is
where the criterion that admits one is stated; everything else is re-derived at
the point of use, the way ``_recorded`` is re-read from the ledger and the way
``seed.graph-init`` asks the working tree which runner file it carries.

**Position comes from the ledger, and from nothing else.** A recorded stage is
not re-walked and an unrecorded one is walked from its first row, so what a
resume re-runs is everything the last boundary does not account for — including
work whose artifact is sitting on disk. That is the discard rule rather than a
lost optimization: an artifact no boundary accounts for cannot be told from one
a dying process half-earned, and the stage table is what makes discarding it
cheap, every row that spends a model call standing immediately in front of its
own boundary (``steps.STEPS``; SPEC.md, The Driver's Contract).

Within a ``wave*`` step the skip is per member (:func:`pending_members`, through
:meth:`Runner._wave`) — a wave killed mid-flight re-briefs only the members whose
artifacts are absent, and if that leaves exactly one, the step collapses to a
SINGLE. **No row in the current table is a wave**: that is the run side of the
call machinery's wave route, which outlives the last stage that used it
(``steps.Unit.WAVE``, ``steps.Writer.WAVE_SESSION``), and a wave row returning to
the table owes the rule above an answer of its own — member granularity is the
same trust in an unaccounted artifact, asked one member at a time.

**A launch is an invocation that finds ``start`` unrecorded, and nothing
configures that.** There is no build-mode setting and no row states a mode it
belongs to: the ledger's recorded-stage set is read at the top of every walk and
is the whole of what says where this invocation stands. The one place the
distinction is acted on is :meth:`Runner._pre_kb_root`, a row of the ``start``
stage — so it runs on a launch and never on a resume, by the same skip that
makes a recorded stage unwalked — where a ``kb-root/`` holding documents this
build did not write refuses rather than being overwritten.

**A round is counted by the process that runs it, and recorded by the stage's
own boundary.** A stage is recorded once, so its ledger entry says that every
round it ran is behind it and an unrecorded stage has run none — and the entry
states how many, in the boundary commit's own body
(:meth:`Runner._stage_note`). Nothing reads a round number back off disk: a
findings filename carries its round so that two rounds' findings are two files,
and no counter is derived from a name. So a file a dying process left behind is
not a round, spends nothing against a cap, and is overwritten by the round that
really runs; and the rounds an unrecorded stage ran are work no boundary
accounts for, which a resume re-runs rather than adopts.

**Barriers are exits.** Nothing here blocks on a human. A raise persists the
barrier record, relays it, and ends the walk with the registry's exit code; the
answer arrives on the next invocation. A barrier answered from config or
``--decide`` resumes the walk in place, and a second raise of the same pair in
one process is an automatic stop.

**No row grades what it writes.** Every rigor value and every on-point fraction
a minting row authors is the unscored literal, and stays it: the build authors
the graph and the maintenance tooling scores it, through its own front door.
``*pending*`` is the expected terminal state, and nothing here refuses it. The
converse is refused, at every row declaring ``writes_register``:
:meth:`Runner._check_minted_grades` reads what the row's seat wrote and stops
the stage over a grade it assigned itself. Riding the declared property rather
than a row id is what makes a later minting row guarded by construction — no
row in the current table declares it, but the guard stands ready for one that
does.

**No stage repairs a gate.** ``phase-3a`` runs the three verifiers and either
records or stops: what each of them compares is one mechanically-produced
artifact against another, so a red one is a defect in a tool or in what was
authored and there is nothing for a seat to remediate in the KB. ``phase-5``
drives the one remaining cycle — review, fix, re-review
(:class:`ReviewCycle`) — over the documents ``overview-drafted`` wrote and its
boundary committed. **That cycle turns on ``critical`` alone**: a warning and a
note are reported by :meth:`Runner._report_round` and carried past, so the only
finding that spends a fix round is one the reviewer itself called critical.

**Scope**: the walk covers :data:`steps.TABLE_STAGE_IDS` — every stage of the
pipeline. ``execute`` still takes the stage list, because a test that means to
exercise one stage's rows should not have to walk every other stage to reach
them; exit 0 means every stage walked is recorded.

**One flag bounds the walk, and a bounded run is not a failed one.**
``--through`` names the last stage to walk, cut in :meth:`Runner._walk`.
Reaching it is exit :data:`baton.EXIT_BOUNDED`: the ledger stands where the
walk stopped and the next invocation resumes there.

**``--no-inference`` excludes rows and bounds nothing.** Every row that would
cost a model call is dropped by :meth:`Runner._applies` — a property the rows
derive (``steps.Step.spends_inference``) rather than a stage id anything here
names — and the walk carries on past it, so the build closes out. The boundary
commit of a stage that lost rows says so (:meth:`Runner._stage_note`), and the
record carries the same flag onward so the ledger's own coverage check knows
what kind of build it is recording. A bounded run stops and resumes; a build
spending no inference is finished without those rows.

Stdlib only.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from .. import inference, kb_index_lib, kb_pipeline, kb_readme, kb_util
from . import barriers, baton, call, envelope, ledger, runlog, steps, watch
from .config import NO_INFERENCE_FLAG, THROUGH_FLAG, Decision, DriverConfig, run_dir_parent

_log = runlog.logger("run")

# Rows whose execution belongs to another row's handler rather than to the
# linear walk. The loop row is the cycle: it drives its stage's review, fix and
# re-review for as many rounds as its series allows, so the walk must not also
# run them in table order. Every other row is walked.
LOOP_DRIVEN_STEPS: frozenset[str] = frozenset({"p5.review"})

# Preflight's own remediation marker. `ov.docent-check` relays the `restore:`
# line preflight already prints for a missing docent command rather than
# composing a second wording of the same action.
_RESTORE_MARKER = "restore:"


# --- the terminal state ------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Result:
    """How the run ended, and everything its baton and ``exit.json`` need.

    ``context`` is built once, by :func:`execute`, and is the same object the
    barrier record's own baton was rendered from — so the card in the file and
    the card on the terminal cannot differ.
    """

    exit_code: int
    detail: tuple[str, ...] = ()
    pair: str = ""
    question: str = ""
    admissible: tuple[str, ...] = ()
    unconsumed_decisions: tuple[str, ...] = ()
    barrier_record: Path | None = None
    context: baton.BatonContext = baton.BatonContext()


class _Halt(Exception):
    """A terminal state reached inside the walk. Carries the :class:`Result` that holds."""

    def __init__(self, result: Result) -> None:
        super().__init__(f"halt: exit {result.exit_code}")
        self.result = result


def _context(config: DriverConfig, paths: runlog.RunPaths, result: Result) -> baton.BatonContext:
    """The baton context for a result. One function, so file and terminal agree."""
    return baton.BatonContext(
        invocation=config.invocation,
        pair=result.pair,
        question=result.question,
        admissible=result.admissible,
        run_dir=str(paths.run_dir),
        run_dir_parent=run_dir_parent(paths.parent),
        detail=result.detail,
        unconsumed_decisions=result.unconsumed_decisions,
    )


def write_report(
    paths: runlog.RunPaths,
    *,
    exit_code: int,
    barrier_record: Path | None = None,
    unconsumed_decisions: Sequence[str] = (),
) -> None:
    """The run directory's terminal report: ``cadence.jsonl``, then ``exit.json``.

    Called on **every** way out of a run, the ones nobody planned included — a
    boundary check that failed, a signal, an exception no handler names. Those
    are the endings whose own card calls the run directory the bug report, and
    ``exit.json`` is the one channel a backgrounded session has for learning how
    a run ended, so they are exactly the endings that may not leave it empty.

    The cadence extraction lives here rather than beside ``write_exit_json`` for
    one reason: a record carries the *stage* a call served, and ``cli`` states in
    its own docstring that it holds no stage knowledge. This is the outermost
    layer that has any, and by the time a run is over every capture in ``calls/``
    is complete — so the pass is one read of each and no call is missed. Both
    files are always written, so "missing" never has to be told apart from
    "never extracted": a run that made no calls leaves an empty
    ``cadence.jsonl``.
    """
    runlog.write_cadence(paths, stages={step.id: step.stage for step in steps.STEPS_BY_ID.values()})
    runlog.write_exit_json(
        paths,
        exit_code=exit_code,
        barrier_record=barrier_record,
        unconsumed_decisions=unconsumed_decisions,
    )


# --- the ledger seam ---------------------------------------------------------


@dataclass(frozen=True)
class LedgerOps:
    """The ledger surface the walk uses, bound to one repo root — the tests' seam.

    The same shape ``watch.Sensors`` has, for the same reason: the loop's
    decisions are worth testing without a git repository and a real toolchain
    behind every one of them, and the adapter itself is tested against the real
    ``kb_util`` in its own suite. Each callable is keyword-only, so no pair of
    same-typed arguments can be swapped at a call site.
    """

    preflight: Callable[..., ledger.Outcome]
    graph_init: Callable[..., ledger.Outcome]
    # The two build front ends the head runs. `document_graph` takes the run's
    # own sources; `claim_graph` takes the flags of whichever of its three
    # invocations the calling row means, so the row states which pass it is and
    # the adapter states nothing.
    document_graph: Callable[..., ledger.Outcome]
    claim_graph: Callable[..., ledger.Outcome]
    start_build: Callable[..., ledger.Outcome]
    # `note` is the boundary commit's body — empty for most rows, and words
    # for a stage whose rows this build dropped. `no_inference` is not the same
    # thing said twice: the note is prose for a reader, and the flag is the
    # build property `kb_pipeline` maps to which coverage units it excuses. A
    # gate keyed on the note's wording would be a check reading prose.
    advance_step: Callable[..., ledger.Outcome]
    show_status: Callable[..., ledger.Outcome]
    # The consuming repo's `kb-refresh` / `kb-verify`, which the `refresh` and
    # `gate` row subtypes run. A nonzero target is a red gate (exit 11) and the
    # mapping lives in the adapter, not here.
    run_target: Callable[..., ledger.Outcome]


def ledger_ops_for(repo_root: Path) -> LedgerOps:
    """The real ops, with the repo root bound once."""
    return LedgerOps(
        preflight=lambda: ledger.preflight(repo_root),
        graph_init=lambda *, runner: ledger.graph_init(repo_root, runner=runner),
        document_graph=lambda *, sources, bibliographies, kb_root: ledger.document_graph(
            repo_root, sources=sources, bibliographies=bibliographies, kb_root=kb_root
        ),
        claim_graph=lambda *, flags: ledger.claim_graph(repo_root, flags=flags),
        start_build=lambda *, charter: ledger.record_start(repo_root, charter=charter),
        advance_step=lambda *, stage, note="", no_inference=False: ledger.record_stage(
            repo_root, stage=stage, note=note, no_inference=no_inference
        ),
        show_status=lambda *, relay: ledger.show_status(repo_root, relay=relay),
        run_target=lambda *, target: ledger.run_target(repo_root, target=target),
    )


# --- the predicates and readings, as their own units -------------------------


@dataclass(frozen=True)
class Member:
    """One member of a wave: what it is called, what it reads, what it must leave."""

    name: str
    source: str
    artifact: Path


def present(path: Path) -> bool:
    """Existence and non-emptiness — the only artifact question the driver asks."""
    return path.is_file() and path.stat().st_size > 0


def pending_members(members: Sequence[Member]) -> tuple[Member, ...]:
    """Partial-wave reconciliation, at member granularity.

    A wave killed mid-flight may have left some member artifacts on disk.
    Re-running the step drops the members whose declared artifacts are present
    and non-empty and re-briefs only the rest.
    """
    return tuple(member for member in members if not present(member.artifact))


#: The round a capped series opens at, on every invocation that reaches its
#: stage. There is no second value it could take: the stage's ledger entry is
#: the only record of a round, a stage has one, and it is written after the last
#: round — so a stage being walked at all is a stage with no round recorded.
FIRST_ROUND = 1


def revisions_spent(*, series: str, round_number: int) -> int:
    """How many revisions a series has spent by the time round ``round_number`` is reviewed.

    The two series differ by their opening move, and that is the whole of the
    difference: the initial series opens with a *review* of the design as
    written, so round N follows N-1 revisions; the gate-driven series is opened
    by a revision carrying the gate's direction, so round N follows N.
    """
    return round_number if series == steps.SERIES_GATE else round_number - 1


#: What a bibliography file is called. The document graph takes the flag once
#: per file and resolves citations against their union.
BIBLIOGRAPHY_SUFFIX = ".bib"


def bibliographies_beside(repo_root: Path, sources: Sequence[str]) -> tuple[Path, ...]:
    """Every bibliography sitting in a directory one of this run's sources sits in.

    A launch names its sources and nothing else, so where ``[run] bibliography``
    names none this is where the document graph's bibliographies come from: a
    corpus keeps its ``.bib`` beside the volume roots that cite it.

    **Sorted, and that is the determinism requirement rather than tidiness.** A
    key two files define resolves to the first one passed, so a set discovered
    by a directory read has to be ordered by something the filesystem does not
    decide, or two builds of one corpus could differ.

    Resolving the **volume roots** this way is refused by that front end, and
    for a reason that does not carry over: name the wrong root and its content
    is converted twice with every per-volume check passing on both copies,
    invisibly. A ``.bib`` the corpus does not cite is inert — citeproc renders
    only cited entries — so passing one costs nothing.
    """
    directories = dict.fromkeys((repo_root / source).resolve().parent for source in sources)
    return tuple(sorted({path for directory in directories for path in directory.glob(f"*{BIBLIOGRAPHY_SUFFIX}")}))


# --- the findings loop, as data ----------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ReviewCycle:
    """One findings loop's rows and authors.

    One shape: review, fix from what the review wrote, re-review — bounded by
    the loop row's own cap. The rows are data rather than control flow, so the
    loop reads as one sequence whichever stage drives it.

    ``authors`` are the findings filenames' authors, in the order the member
    table lists them — the reviewing seats, so a findings path says who judged.
    """

    loop_step: str
    review_step: str
    fix_step: str
    authors: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RoundsSpent:
    """What one stage's findings loop cost, as its boundary commit states it.

    The rounds a stage ran are an attribute of that stage's own ledger entry and
    of nothing else — there is no per-round entry, the ledger's entries being the
    stage vocabulary — so this is what the record carries and the only place the
    count survives the process at all.
    """

    rounds: int
    fixes: int


REVIEW_CYCLES: Mapping[str, ReviewCycle] = MappingProxyType(
    {
        # The loop row and the fix row are the same row: this stage has no
        # separate loop row, so the row that escalates is the row that drives.
        "phase-5": ReviewCycle(
            loop_step="p5.fix",
            review_step="p5.review",
            fix_step="p5.fix",
            authors=(steps.META_REVIEW_SEAT,),
        ),
    }
)


# --- what a Runner may hold --------------------------------------------------

#: Every attribute a :class:`Runner` may hold, and the whole of it.
#:
#: **The criterion is the stage boundary.** A resume re-enters the walk at one —
#: a recorded stage is never re-walked, and an unrecorded stage is walked from
#: its first row — so an attribute one row writes is read *stale* by a later
#: invocation exactly when its reader runs in a different stage from its writer.
#: Inside one stage there is no process boundary to lose a value across; across
#: two there always is. An attribute is therefore admissible on exactly three
#: grounds, and a name is added here only by naming which of them it stands on:
#:
#: * **the invocation fixes it**, so the invocation that resumes fixes it the
#:   same way — the constructor's arguments, and ``scratch``, computed from one;
#: * **every reader runs in the writer's own stage**, so no resume observes it —
#:   ``_seq``, whose readers are the call it numbers, and ``_rounds``, written by
#:   ``phase-5``'s findings loop and read by ``phase-5``'s own record row;
#: * **the walk re-derives it before any row reads it** — ``_recorded``, re-read
#:   from the ledger at the top of :meth:`Runner.run`.
#:
#: Anything else is resumption state and has no home here: re-derive it at the
#: point of use. ``seed.graph-init`` asking ``kb_util.detected_runner`` is that
#: shape; the same answer carried from ``pre.preflight``, two stages earlier, was
#: the defect it replaced — a resume skips ``start``, so the attribute held its
#: constructor default and the ``spine-seed.runner-choice`` barrier could not
#: fire on any invocation that resumed.
#:
#: Closure is all a machine can check here; which ground an attribute stands on
#: is the author's reading, and this registry is where it is stated.
#: :meth:`Runner._check_attributes` enforces the closure, at construction and at
#: every stage transition.
RUNNER_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "config",
        "paths",
        "repo_root",
        "caller",
        "answers",
        "ops",
        "stages",
        "scratch",
        "_seq",
        "_recorded",
        "_rounds",
    }
)


# --- the walk ----------------------------------------------------------------


class Runner:
    """One process's walk through the step table.

    Every method that can end the run raises :class:`_Halt`; the walk itself
    reads as a sequence of steps rather than as a chain of error checks, which
    is the only way a sequencer of this many stages stays legible.

    **It carries no resumption state.** :data:`RUNNER_ATTRIBUTES` is the closed
    set of attributes it may hold and states what admits one; every other
    reading a row needs is taken at the point of use, from the ledger or from
    the working tree.
    """

    def __init__(
        self,
        *,
        config: DriverConfig,
        paths: runlog.RunPaths,
        repo_root: Path,
        caller: call.Caller,
        answers: barriers.Resolver,
        ops: LedgerOps,
        stages: Sequence[str] = steps.TABLE_STAGE_IDS,
    ) -> None:
        self.config = config
        self.paths = paths
        self.repo_root = repo_root
        self.caller = caller
        self.answers = answers
        self.ops = ops
        self.stages = tuple(stages)
        self.scratch = repo_root / steps.SCRATCH_ROOT
        self._seq = 0
        self._recorded: frozenset[str] = frozenset()
        # What each stage's findings loop cost this process, for the boundary
        # commit that then records it. **Not resumption state**: written by
        # `phase-5`'s loop row and read by `phase-5`'s own record row, so no
        # resume stands between the two — and discarded with the process exactly
        # as the round number is, the next invocation of an unrecorded stage
        # opening at `FIRST_ROUND` again rather than reading a count from
        # anywhere.
        self._rounds: dict[str, RoundsSpent] = {}
        self._check_attributes()

    def _check_attributes(self) -> None:
        """The closure half of :data:`RUNNER_ATTRIBUTES`, which states the criterion.

        Run at construction and again at every stage transition, so an
        attribute bound in ``__init__`` and one a row assigns mid-walk are both
        caught — the second at the next boundary, which is the granularity the
        criterion is about.

        The offending names go in the *message*, not the record's context:
        :class:`runlog.BoundaryError`'s text is what exit 15's card carries, and
        a card saying only that the attribute set is wrong names nothing to fix.
        """
        held = set(vars(self))
        faults = [
            f"{label}: {', '.join(sorted(names))}"
            for label, names in (
                ("undeclared", held - RUNNER_ATTRIBUTES),
                ("declared but unbound", RUNNER_ATTRIBUTES - held),
            )
            if names
        ]
        runlog.require(
            not faults,
            f"a Runner's attributes are not the set RUNNER_ATTRIBUTES declares [{'; '.join(faults)}] — that "
            f"registry states what admits one, and a value a row of a later stage reads is resumption state "
            f"with no home on this object: re-derive it at the point of use",
        )

    # --- entry ---------------------------------------------------------------

    def run(self) -> Result:
        """Walk this run's stages in ledger order. Exit 0 when every one is recorded."""
        missing = sorted(set(steps.STEP_IDS) - set(_HANDLERS) - LOOP_DRIVEN_STEPS)
        runlog.require(not missing, f"step table rows with no handler: {', '.join(missing)}")

        self._recorded = self._recorded_stages()
        for stage in self.stages:
            if stage in self._recorded:
                _log.info("stage is already recorded; skipping it", extra={"context": {"stage": stage}})
                continue
            walk = self._walk()
            if stage not in walk:
                return self._stopped_at_bound(stage, walk)
            self._run_stage(stage)

        _log.info(
            "the implemented cut is complete",
            extra={"context": {"stages": ", ".join(self.stages), "run_dir": str(self.paths.run_dir)}},
        )
        return self._result(baton.EXIT_OK)

    # --- the two bounds ------------------------------------------------------

    def _walk(self) -> tuple[str, ...]:
        """The stages this run may walk, cut by its one bound.

        ``--no-inference`` is not among the cuts and never was a stage-level
        question: it drops rows, in :meth:`_applies`, and every stage is still
        walked and still recorded.
        """
        walk = self.stages
        through = self.config.run.through
        if through in walk:
            walk = walk[: walk.index(through) + 1]
        return walk

    def _bound_reason(self, stage: str) -> str:
        """Why the walk stopped before ``stage``, in the operator's words.

        One bound, so one answer. It is still a method rather than an inlined
        string because the card that prints it is rendered from a result and
        the reason belongs beside the cut that produced it.
        """
        del stage
        return f"{THROUGH_FLAG} {self.config.run.through}"

    def _stopped_at_bound(self, stage: str, walk: Sequence[str]) -> Result:
        """End the run at its bound: told to stop, ledger resumable, nothing failed.

        The stage reached is named here rather than at launch because only this
        layer knows one. Exit :data:`baton.EXIT_BOUNDED`, whose card resumes
        without the bound.
        """
        reason = self._bound_reason(stage)
        _log.info(
            "the run stopped at its bound; the stages past it are unwalked and the ledger resumes there",
            extra={"context": {"stopped_before": stage, "walked": ", ".join(walk), "bound": reason}},
        )
        return self._result(
            baton.EXIT_BOUNDED,
            detail=(
                f"walked through {walk[-1] if walk else '(no stage)'} and stopped before {stage} — {reason}",
                "the ledger is resumable: re-run without the bound to continue from here",
            ),
        )

    def _run_stage(self, stage: str) -> None:
        self._check_attributes()
        for step in steps.steps_for(stage):
            if step.id in LOOP_DRIVEN_STEPS:
                continue
            if not self._applies(step):
                _log.info(
                    "row does not apply to this run",
                    extra={"context": {"step": step.id, "spends_inference": step.spends_inference}},
                )
                continue
            _log.info("step", extra={"context": {"step": step.id, "stage": stage, "unit": step.unit.value}})
            self._run_step(step)

    def _run_step(self, step: steps.Step) -> None:
        """Execute one row, with the guards its own declaration calls for.

        ``writes_register`` is one such declaration: the row's seat authors
        register entries, so what it wrote is checked for a grade it may not
        have supplied. The before-scan is taken here rather than inside a
        handler, so the guard rides the property and not a handler that
        remembered to call it.
        """
        if not step.writes_register:
            _HANDLERS[step.id](self, step)
            return
        existing = frozenset(self._authored_ids())
        _HANDLERS[step.id](self, step)
        self._check_minted_grades(step, existing=existing)

    def _applies(self, step: steps.Step) -> bool:
        """The one condition left: what this run will spend.

        The predicate itself is ``steps``', because the note below asks the same
        question of the same rows, and two readings of "does this row apply" is
        one more than the table can have.
        """
        return steps.applies(step, spend_inference=not self.config.run.no_inference)

    # --- terminal states -----------------------------------------------------

    def _result(self, exit_code: int, **fields: object) -> Result:
        """A result carrying the run's final unconsumed-decision list."""
        return Result(exit_code=exit_code, unconsumed_decisions=self.answers.unconsumed, **fields)  # type: ignore[arg-type]

    def _halt(self, exit_code: int, *detail: str) -> None:
        raise _Halt(self._result(exit_code, detail=tuple(detail)))

    def _halt_unless(self, outcome: ledger.Outcome) -> ledger.Outcome:
        """A ledger op's rc is already a driver exit code; a failed one ends the run."""
        if not outcome.ok:
            raise _Halt(self._result(outcome.exit_code, detail=outcome.detail))
        return outcome

    # --- barriers ------------------------------------------------------------

    def _decide(self, pair: str, *, artifacts: Sequence[Path] = (), detail: Sequence[str] = ()) -> Decision:
        """Resolve a barrier, or end the run at it.

        Returns only a *continuing* answer. An absent answer, an answer already
        spent in this process, and an answer that is itself a stop
        (``cancel``, ``no``, ``stop``) all end the run through the same record
        and the same code — they differ in what the record says, not in what
        happens.
        """
        spec = barriers.spec(pair)
        decision = self.answers.take(pair)
        if decision is not None and decision.answer not in spec.stopping:
            return decision
        raise _Halt(self._raise_barrier(spec, answered=decision, artifacts=artifacts, detail=detail))

    def _raise_barrier(
        self,
        spec: barriers.BarrierSpec,
        *,
        answered: Decision | None,
        artifacts: Sequence[Path],
        detail: Sequence[str],
    ) -> Result:
        """Persist the barrier record, relay it, and return the result that ends the run.

        The record file is this body followed by the very baton ``cli.main`` is
        about to print, rendered from the same context — so the file and the
        terminal carry the same bytes, with the baton appearing exactly once in
        each.
        """
        # The display is read without relaying: it is about to be printed as
        # the head of the record, and printing it twice is the one way a
        # relayed render stops being liftable.
        status = self.ops.show_status(relay=False)
        record = barriers.record(
            spec,
            render=status.stdout,
            artifacts=[self._repo_relative(path) for path in artifacts],
            run_dir=str(self.paths.run_dir),
            answered="" if answered is None else answered.answer,
            unconsumed=self.answers.unconsumed,
        )
        question = spec.question
        if answered is not None:
            # An answered stop is not a question: asking it again would invite
            # an answer that has already been given and honored.
            question = f"none — {spec.pair} was answered {answered.answer!r}; report the stop and stop"

        result = self._result(
            spec.exit_code,
            detail=tuple(detail),
            pair=spec.pair,
            question=question,
            admissible=spec.answers,
            barrier_record=self.paths.barriers / f"{spec.stage}-{spec.kind}.md",
        )
        body = baton.render_record(record)
        card = baton.render(spec.exit_code, _context(self.config, self.paths, result))
        assert result.barrier_record is not None  # set immediately above
        result.barrier_record.write_text(f"{body}\n{card}\n", encoding="utf-8")

        _log.warning(
            "barrier raised; the run stops here",
            extra={
                "context": {
                    "pair": spec.pair,
                    "exit_code": spec.exit_code,
                    "answered": "" if answered is None else answered.answer,
                    "record": str(result.barrier_record),
                }
            },
        )
        # The complete render, verbatim, at the head of the message body
        # the session pastes. cli.main prints the baton under it.
        runlog.relay(body)
        return result

    # --- ledger reads and the display ----------------------------------------

    def _recorded_stages(self) -> frozenset[str]:
        """The recorded-stage set, from the ledger's own render."""
        status = self._halt_unless(self.ops.show_status(relay=False))
        # `watch.recorded_stages` is the one parse of the checklist block. A
        # second regex over the same render here is how two readings of one
        # format start to disagree.
        return watch.recorded_stages(status.stdout)

    def _display(self) -> None:
        """The complete verbatim stdout of show-status, at every stage transition."""
        self._halt_unless(self.ops.show_status(relay=True))

    def _repo_relative(self, path: Path) -> str:
        """A path as a log line, a barrier record or a relay card states it.

        For a reader who is standing in this repository, never for a brief:
        a seat resolves a path against a cwd no brief states, so a path a brief
        carries is :meth:`_brief_path`'s.
        """
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    @staticmethod
    def _brief_path(path: Path | None) -> str:
        """A path as a brief states it: absolute, or the named absence.

        ``steps.PATH_SLOTS`` says which slots this is the composer of, and
        ``call.py`` refuses the call where a value reaches one any other way.
        """
        return steps.NOTHING if path is None else str(path)

    # --- calls ---------------------------------------------------------------

    def _call(
        self,
        step: steps.Step,
        *,
        slots: Mapping[str, str],
        outputs: Sequence[Path] = (),
        members: int = 1,
    ) -> call.CallOutcome:
        """One row's call. A failed contract ends the run at the code ``call.py`` chose."""
        self._seq += 1
        outcome = self.caller.execute(
            call.CallRequest(step=step, seq=self._seq, slots=dict(slots), outputs=tuple(outputs), members=members)
        )
        if not outcome.ok:
            raise _Halt(self._result(outcome.exit_code, detail=outcome.detail))

        if outcome.envelope is not None:
            # The deviation log is the wave-side half of the experiment's
            # comparison; the driver's own retries and cap decisions are the
            # other half, and they are run.log records.
            envelope.append_deviations(
                self.paths.deviations,
                outcome.envelope.deviations,
                run_id=self.paths.run_id,
                stage=step.stage,
                step=step.id,
            )
            if outcome.envelope.gaps:
                _log.warning(
                    "the wave reported gaps",
                    extra={"context": {"step": step.id, "gaps": " | ".join(outcome.envelope.gaps)}},
                )
        return outcome

    # --- pre-stage rows ------------------------------------------------------

    def _pre_lock(self, step: steps.Step) -> None:
        """``pre.lock``: the lock is taken by ``cli`` around the whole run; assert it is held.

        The lock is the repository's, not the run directory's, so this
        asks the repo root about it rather than ``paths``.
        """
        del step
        lock = runlog.repo_lock_path(self.repo_root)
        runlog.require(lock.is_file(), "the run lock is not held", lock=str(lock))

    def _pre_preflight(self, step: steps.Step) -> None:
        """``pre.preflight``: the mechanical environment report; any failure is exit 14.

        Nothing is read back off the report. Its ``runner-file`` FACT is a
        statement to the operator, and the row that needs the same answer asks
        the working tree for it (:meth:`_seed_graph_init`) rather than taking it
        from here — this row belongs to ``start``, which a resume skips whole.
        """
        del step
        self._halt_unless(self.ops.preflight())

    def _pre_charter(self, step: steps.Step) -> None:
        """``pre.charter``: say where the charter was looked for when none was found.

        A charter is optional (SPEC.md, The Driver's Contract), and its one
        consumer is ``start.record``, which names the path in the ``start``
        boundary where one stands and records the absence in words where none
        does. No brief carries it and no row's ``slots`` declares a charter
        slot, so this row resolves nothing for one. What it does is name the
        configured path at the moment it was looked at and found empty — a
        charter written somewhere this build never looked is visible in the run
        log rather than inferred from a boundary that named none.
        """
        del step
        if self._charter() is None:
            _log.info(
                "no charter stands at the configured path; this build carries none",
                extra={"context": {"charter_file": str(self.config.run.charter_file)}},
            )

    def _charter(self) -> str | None:
        """The charter's repo-relative path, or ``None`` where the build carries none."""
        if not present(self.repo_root / self.config.run.charter_file):
            return None
        return str(self.config.run.charter_file)

    def _pre_proceed(self, step: steps.Step) -> None:
        """``pre.proceed``: the start barrier, resolved before anything is seeded or recorded."""
        del step
        self._decide(barriers.START_PROCEED)

    def _pre_kb_root(self, step: steps.Step) -> None:
        """``pre.kb-root``: refuse to open a build over a KB this build did not write.

        The tri-state (``kb_util.kb_root_state``) decides, and each value has a
        behaviour of its own:

        * ``absent`` — nothing is there and ``document-graph`` creates it.
          Proceed.
        * ``spine-only`` — the directory holds nothing outside ``.index/``,
          which is derived space rebuilt unconditionally from the authored
          Markdown. There is no authored byte to lose, so this is the seeded-
          but-empty case and it proceeds too, stating what it found rather than
          passing silently: it is not the same state as ``absent`` and a reader
          of the log should not have to infer which one held.
        * ``populated`` — the tree holds documents, and ``dg.build`` writes a
          tree whole. Refuse.

        **This runs on a launch and never on a resume**, because every row of
        the ``start`` stage is skipped once ``start`` is recorded. That is the
        whole mechanism: an invocation that finds ``start`` unrecorded is
        opening a build, so a populated ``kb-root/`` in front of it is somebody
        else's work; an invocation that finds it recorded is continuing one, and
        the populated tree in front of *it* is the build's own product.
        """
        del step
        state = kb_util.kb_root_state(self.repo_root)
        kb_root = self._repo_relative(kb_util.kb_root(self.repo_root))
        if state != kb_util.KB_ROOT_POPULATED:
            _log.info("kb-root state at launch", extra={"context": {"state": state, "kb_root": kb_root}})
            return
        self._halt(
            baton.EXIT_ENVIRONMENT,
            f"pre.kb-root: {kb_root} is {state} and this invocation is opening a build, not resuming "
            f"one — the document graph writes the tree whole, so the documents there would be "
            f"overwritten",
            "restore: resume the build that wrote them (the ledger's recorded stages are its "
            "position; no mode flag selects it), or move that tree aside and re-run to build afresh",
        )

    def _start_record(self, step: steps.Step) -> None:
        """``start.record``: the build boundary. rc 5 (already started) reads as done.

        A build with no charter records without one: the commit's body names the
        charter where there is one to name, and states the absence where there is
        not (``kb_pipeline.NO_CHARTER_BODY``).
        """
        self._halt_unless(self.ops.start_build(charter=self._charter() or ""))
        self._stage_recorded(step.stage)

    # --- the head: the build's own production --------------------------------

    def _bibliographies(self) -> tuple[Path, ...]:
        """The bibliographies the document graph resolves citations against.

        Resolved from where the sources sit, and narrowed to one file by
        ``[run] bibliography`` where a run needs that said — which is what keeps
        the launch line to the sources it already carries.

        **Several ``.bib`` files is not an ambiguity, and there was never a
        choice to make.** ``--bibliography`` is repeatable: the reader merges
        every file it is given, and citeproc renders only the entries a source
        actually cites, so a template's stray ``sample-base.bib`` full of
        unrelated works is inert. The union therefore contains whatever file the
        author declared, and a ``\\bibliography{strings,refs}`` naming two files
        as one bibliography is an ordinary corpus rather than a conflict.

        **Absence is legal too.** A corpus with no ``.bib`` is an ordinary
        corpus: many ship a pre-generated ``.bbl`` or inline ``\\bibitem``, and
        their citations still reach the tree carrying their own keys — what is
        lost is resolution and the references leaf, not the citations.
        """
        named = self.config.run.bibliography
        if named:
            path = self.repo_root / named
            if not path.is_file():
                self._halt(
                    baton.EXIT_ENVIRONMENT,
                    f"[run] bibliography names {named}, and no file stands there — restore: correct the "
                    f"path, or remove the key and keep the corpus's {BIBLIOGRAPHY_SUFFIX} beside its sources",
                )
            return (path,)
        found = bibliographies_beside(self.repo_root, self.config.run.sources)
        if not found:
            _log.info(
                "no bibliography sits beside this build's sources; citations will carry their own keys "
                "and no volume gains a references leaf",
                extra={"context": {"sources": ", ".join(self.config.run.sources)}},
            )
        return found

    def _dg_build(self, step: steps.Step) -> None:
        """``dg.build``: the document tree, derived from this run's sources.

        The tree is the build's own product rather than something handed to it.
        Every source is a volume root, and ``kb-root/`` is where the tree lands
        — a name the toolchain hard-codes and takes no override for.

        The front end writes the tree whole, so this row over a tree a finished
        build already stamped would overwrite the documents the spine sits in.
        Two things keep it off that tree: a recorded stage is never re-walked,
        and ``pre.kb-root`` refuses to open a build over a populated one.
        """
        del step
        self._halt_unless(
            self.ops.document_graph(
                sources=self.config.run.sources,
                bibliographies=[str(path) for path in self._bibliographies()],
                kb_root=str(self._kb_root),
            )
        )

    def _seed_graph_init(self, step: steps.Step) -> None:
        """``seed.graph-init``: initialise the claim-graph spine over the tree.

        A ``kb-root/`` with no document tree in it halts the run: the stage
        above is what writes one, and its boundary commit is also what leaves
        the worktree clean for this seed's own preflight.

        **Whether this repository carries a runner file is read here, from the
        working tree.** It is the same reading ``preflight`` reports as a FACT —
        ``kb_util.detected_runner``, one definition — and taking it at the point
        of use is what lets the barrier fire on a resume: ``pre.preflight`` is a
        ``start`` row, so an invocation that finds ``start`` recorded never runs
        it, and an attribute holding its answer would hold its constructor
        default instead (:data:`RUNNER_ATTRIBUTES`).
        """
        del step
        runner = self.config.run.runner
        if runner is None and kb_util.detected_runner(self.repo_root) is None:
            runner = self._decide(barriers.SPINE_SEED_RUNNER_CHOICE).answer

        self._halt_unless(self.ops.graph_init(runner=runner))

    def _claim_graph(self, step: steps.Step) -> None:
        """One ``kb_claimgraph`` invocation, composed from the stage the row belongs to.

        The row states nothing about passes or scopes. Which invocation its
        stage is, is ``kb_pipeline``'s declaration, and the tool resolves the
        same flags back to the same stage on the other side of the subprocess —
        so a row cannot ask for a pass that runs as a different stage.
        """
        stage = kb_pipeline.stage_by_id(step.stage)
        runlog.require(
            stage.claimgraph_invocation is not None,
            "a claim-graph row's stage declares no invocation",
            step=step.id,
            stage=step.stage,
        )
        assert stage.claimgraph_invocation is not None  # required above
        # The tool's own flag, not a dropped row: stage D's narrowing settles
        # every edge containment decides whether or not a model is reachable,
        # and only the pairs left open need one.
        flags = stage.claimgraph_invocation.flags + (
            (kb_util.NO_INFERENCE_FLAG,) if self.config.run.no_inference else ()
        )
        self._halt_unless(self.ops.claim_graph(flags=flags))

    def _declared_build(self, step: steps.Step) -> None:
        """``declared.build``: the claims the corpus's author marked, mechanically."""
        self._claim_graph(step)

    def _discover_build(self, step: steps.Step) -> None:
        """``discover.build``: stage C-inf, one ask per awaiting document.

        The model this row spends is spawned inside ``kb_claimgraph``, through
        that package's own seat seam, so no flag of this driver replaces it.
        That is the row's ``spends_own_inference`` declaration, and a run told to
        spend none stops before this stage rather than reaching it — so there is
        no condition left here to warn about.
        """
        self._claim_graph(step)

    def _depends_attribute(self, step: steps.Step) -> None:
        """``depends.attribute``: stage D, over the graph discovery left."""
        self._claim_graph(step)

    # --- recording -----------------------------------------------------------

    def _record_stage(self, step: steps.Step) -> None:
        """Every stage's last row: ``advance-step``, then the render.

        The record carries two things beyond the stage. The note is prose, for a
        reader of the commit trail. ``no_inference`` is the build property the
        ledger's own coverage check reads — passed on every record rather than
        only on the stages that lost rows, because which units it excuses is
        ``kb_pipeline``'s classification and not this layer's to anticipate.
        """
        self._halt_unless(
            self.ops.advance_step(
                stage=step.stage,
                note=self._stage_note(step.stage),
                no_inference=self.config.run.no_inference,
            )
        )
        self._stage_recorded(step.stage)

    def _stage_note(self, stage: str) -> str:
        """What the boundary commit says about this stage beyond that it happened.

        Two clauses, and a stage carries whichever of them it earned.

        **The rounds its findings loop ran.** A round is not a ledger entry of
        its own: the ledger's entries are the stage vocabulary, which is closed
        and ordered, and a cycle's rounds are neither — so the rounds a stage ran
        are an attribute of that stage's one entry, written here. It is also the
        only record of them there is, since the count lives in the process that
        ran them, and that is what a reader of the trail needs: a stage recorded
        after four rounds and one recorded after one are the same `[x]` in the
        checklist.

        **A dropped row is the one thing about a finished build its own product
        cannot state.** A document a stage never read looks exactly like one it
        read and found nothing in — both carry the awaiting reason the declared
        pass wrote — so a later reader counting documents learns nothing, and the
        ledger is the build record. Writing it here is what keeps that reader
        from having to infer it.

        The rows are named rather than counted: which of a stage's rows cost a
        model call is the step table's answer (``steps.inference_rows``), and a
        note stating a number would be a second view of it.
        """
        clauses = []
        spent = self._rounds.get(stage)
        if spent is not None:
            clauses.append(
                f"review: {spent.rounds} round(s), {spent.fixes} fix round(s). This boundary is the whole "
                f"record of them — the stage records once, so an entry says every round it ran is behind it "
                f"and an unrecorded stage has run none."
            )
        if self.config.run.no_inference:
            dropped = steps.inference_rows(stage)
            if dropped:
                clauses.append(
                    f"{NO_INFERENCE_FLAG}: this build spent no model call, so {', '.join(dropped)} did not run. "
                    f"Every row of this stage that costs none ran; what the dropped rows would have authored is "
                    f"absent from the KB, and whatever the stage before them wrote about it stands."
                )
        return " ".join(clauses)

    def _stage_recorded(self, stage: str) -> None:
        self._recorded = self._recorded | {stage}
        self._display()

    def _wave(
        self,
        step: steps.Step,
        members: Sequence[Member],
        *,
        slots: Callable[[tuple[Member, ...]], Mapping[str, str]],
        declares_artifacts: bool = True,
    ) -> call.CallOutcome | None:
        """One ``wave*`` row, reconciled at member granularity.

        Members whose work is already on disk are dropped and only the rest are
        briefed; all present is no call at all. ``slots`` is a function of the
        *pending* set rather than a mapping, because everything a mint-bearing
        brief carries — the member table, the existing-id inventory — must be
        computed after the drop, not before it. ``None`` is returned when there
        was nothing left to ask.

        ``declares_artifacts`` says whether the row has a named artifact per
        member for ``call.py`` to validate.
        """
        if declares_artifacts:
            pending = pending_members(members)
        else:
            pending = tuple(members)
        if not pending:
            _log.info(
                "every member's artifact is already on disk; skipping the wave",
                extra={"context": {"step": step.id, "of": len(members)}},
            )
            return None
        if len(pending) < len(members):
            _log.info(
                "partial-wave re-entry: re-briefing only the members whose artifacts are absent",
                extra={"context": {"step": step.id, "of": len(members), "pending": len(pending)}},
            )
        return self._call(
            step,
            slots=slots(pending),
            outputs=tuple(member.artifact for member in pending) if declares_artifacts else (),
            members=len(pending),
        )

    # --- capped loops: shared machinery ---------------------------------------
    #
    # What a series has spent is the round arithmetic and nothing else, because
    # the rounds a process runs are the rounds it performed: there is no earlier
    # process's act to reconcile against, an unrecorded stage's rounds being work
    # no boundary accounts for and re-run rather than adopted.

    def _only_series(self, step: steps.Step) -> steps.LoopSeries:
        """The one capped series a single-series loop row carries (every loop but ``p1a.loop``)."""
        runlog.require(len(step.series) == 1, "this loop row carries exactly one capped series", step=step.id)
        return step.series[0]

    def _spend_or_escalate(
        self,
        entry: steps.LoopSeries,
        *,
        spent: int,
        artifacts: Sequence[Path],
        detail: Sequence[str] = (),
    ) -> None:
        """The cap arithmetic, in the one place every capped loop reads it.

        ``effective_cap = cap + grants_this_process``. Below the cap this
        returns and the round proceeds; at it, :meth:`_decide` ends the run
        unless one more round was authorized, and a second exhaustion re-raises
        the same pair, whose spent answer turns it into a stop rather than a loop.
        """
        effective_cap = entry.cap + self.answers.grants(entry.cap_barrier)
        if spent < effective_cap:
            return
        self._decide(
            entry.cap_barrier,
            artifacts=artifacts,
            detail=(f"{spent} round(s) spent against a cap of {effective_cap}", *detail),
        )
        self.answers.grant(entry.cap_barrier)

    # --- the runner targets: the `refresh` and `gate` row subtypes -----------

    def _run_target(self, target: str) -> ledger.Outcome:
        return self.ops.run_target(target=target)

    def _refresh(self, step: steps.Step) -> None:
        """A ``refresh`` row: the derived index rebuilt before anything reads it.

        Single-writer by construction rather than by a lock — the driver runs
        exactly one subprocess at a time, which is what makes the property free.
        """
        del step
        self._halt_unless(self._run_target(kb_util.TARGET_REFRESH))

    def _gate(self) -> ledger.Outcome:
        """A ``gate`` row: ``kb-refresh`` then ``kb-verify``. Returns verify's outcome.

        A refresh that cannot run at all ends the run here — nothing downstream
        can read an index that was never rebuilt. Only verify's outcome is a
        gate verdict; an exit 14 (no runner target installed) is an environment
        fault rather than a finding about the KB.
        """
        self._halt_unless(self._run_target(kb_util.TARGET_REFRESH))
        return self._run_target(kb_util.TARGET_VERIFY)

    def _mint_remedy(self, stage: str) -> tuple[str, ...]:
        """The baton lines: the driver never rolls back, so it names the manual remedy.

        Destructive git from inside a sequencer is a worse failure than a named
        stop, so this is prose for an operator and never a command the driver
        runs. The boundary it names is the ledger's own commit prefix, imported
        rather than restated.
        """
        return (
            f"{stage} is mint-bearing and its mechanical check is red; " f"the driver does not roll back",
            f"manual remedy: 'git reset --hard' to the last '{kb_pipeline.LEDGER_PREFIX}' boundary commit, "
            f"then re-run — a re-entry mints nothing for material the register scan already covers",
        )

    # --- the register scan: the one source of the id inventory ---------------

    @property
    def _kb_root(self) -> Path:
        return kb_util.kb_root(self.repo_root)

    def _authored_ids(self) -> Mapping[str, kb_index_lib.IdRecord]:
        """Every authored node id, from a **register scan** and never from ``.index/``.

        The counter-evidence is decisive and live: run-3 held forty-five
        authored ids with every ``.index/*.jsonl`` at zero bytes. An inventory
        read from the derived index would have concluded "no existing ids" and
        minted a second full set. This scan reads authored Markdown, so it is
        correct whether or not a refresh has run.
        """
        inventory = kb_index_lib.scan_authored_ids(self._kb_root)
        _log.info(
            "existing-id inventory read from the register scan",
            extra={"context": {"ids": len(inventory), "kb_root": str(self._kb_root)}},
        )
        return inventory

    def _registers_on_disk(self) -> tuple[str, ...]:
        """Every register the scan found, kb-root-relative, in path order."""
        return tuple(sorted({record.register_path for record in self._authored_ids().values() if record.register_path}))

    def _register_path(self, register_rel: str) -> Path:
        return self._kb_root / register_rel

    def _minted_grades(self, *, existing: frozenset[str]) -> Iterator[tuple[str, str]]:
        """Every graded value carried by a register entry not in ``existing``.

        The three the build must not author, all of them register-resident: a
        claim's ``confidence``, a support's ``quality``, and the on-point
        ``fraction`` of each warrant edge staged in a ``sup-`` entry. Read
        through ``kb_index_lib``'s own parsers, so a grammar change reaches this
        scan and the refresh together.
        """
        pending = kb_index_lib.PENDING_FRACTION
        for register_rel in self._registers_on_disk():
            path = self._register_path(register_rel)
            for entry in kb_index_lib.parse_claim_quality_file(path, self._kb_root):
                if entry.id not in existing and entry.confidence is not None:
                    yield entry.id, f"confidence {kb_index_lib.format_solidity(entry.confidence)}"
            for sup_id, fields in kb_index_lib.parse_support_quality_entries(path, self._kb_root).items():
                if sup_id not in existing and fields["quality"] is not None:
                    yield sup_id, f"quality {kb_index_lib.format_solidity(fields['quality'])}"
            for sup_id, pairs in kb_index_lib.parse_register_staged_supports(path).items():
                if sup_id in existing:
                    continue
                for claim_id, fraction in pairs:
                    if fraction is not pending:
                        yield sup_id, f"on-point fraction for {claim_id} {kb_index_lib.format_solidity(fraction)}"

    def _check_minted_grades(self, step: steps.Step, *, existing: frozenset[str]) -> None:
        """Every entry this row wrote carries the unscored literal where a grade would go.

        **The build does no scoring**: a rigor value grades a written derivation
        and an on-point fraction grades how much of one bears on the claim it is
        offered for. Both are judgments the maintenance tooling supplies through
        its own front door and nothing in this pipeline computes. A seat that
        supplies one has graded its own work, and a stage recorded over it is
        green on a value nobody derived. Only the driver can ask this, because
        only the driver knows which row is running.

        Scoped by subtraction rather than by a blanket scan, because a re-walk
        of the stage re-enters at this row with the entries an earlier attempt
        wrote already on disk.
        """
        offenders = sorted(set(self._minted_grades(existing=existing)))
        if not offenders:
            return
        self._halt(
            baton.EXIT_GATE_RED,
            f"{step.stage}: {len(offenders)} value(s) written here are self-assigned grades, "
            f"where {kb_index_lib.PENDING_LITERAL} is the only one a mint may write",
            *(f"self-assigned: {node_id} — {value}" for node_id, value in offenders),
            *self._mint_remedy(step.stage),
        )

    # --- phase-3a ------------------------------------------------------------

    def _p3a_gate(self, step: steps.Step) -> None:
        """``p3a.gate``: ``kb-refresh`` then ``kb-verify``, green or the run stops.

        There is no repair round here and no seat to run one. Each of the three
        verifiers compares one mechanically-produced artifact against another —
        every edge resolving among the claims that exist, the graph acyclic, the
        derived index against the authored bytes — so a red one is a defect in a
        tool or in what was authored, and neither is answered by rewriting the
        KB. The verifier's whole report reaches the run log at INFO
        (``ledger.run_target``); what reaches the card is which target failed
        and the lines of its report that name the failure, bounded
        (``ledger._failure_detail``). No barrier is raised, so the card is the
        no-answer one rather than the answer-substituting one.
        """
        del step
        self._halt_unless(self._gate())

    # --- phase-5: the findings loop ------------------------------------------

    def _findings_at(self, *, stage: str, series: str, round_number: int, author: str) -> Path:
        return self.scratch / steps.findings(stage=stage, series=series, round_number=round_number, author=author)

    def _findings_set(self, cycle: ReviewCycle, *, series: str, round_number: int) -> tuple[Path, ...]:
        """One round's findings paths, one per reviewing seat, in member-table order."""
        stage = steps.STEPS_BY_ID[cycle.review_step].stage
        return tuple(
            self._findings_at(stage=stage, series=series, round_number=round_number, author=author)
            for author in cycle.authors
        )

    def _report_round(
        self,
        verdict: envelope.Verdict,
        *,
        stage: str,
        round_number: int,
        findings: Sequence[Path],
    ) -> None:
        """State the round's counts by severity and where the findings are.

        **The whole statement is in the message text**, because the console tee
        prints messages alone and a count in the record's context would reach the
        run log and not the operator — the same reason the permission-mode
        announcement carries its value in its words.

        **Both forms are stated**, the way ``kb_docgraph.build._declarations``
        states its zero: only a critical finding stops this stage, so a warning
        and a note now travel past the gate, and a build that said nothing about
        them would read exactly like a build whose reviewer raised none.
        """
        # Scoped to the carried severities and never to the round's outcome: a
        # round carrying a warning past the gate may still be stopping on a
        # critical beside it, and a line claiming the build goes on would be
        # false exactly there.
        carried = verdict.warning + verdict.note
        consequence = (
            f"the gate reads critical alone, so the {carried} finding(s) at warning or note "
            "are reported here and repaired by nobody"
            if carried
            else "the gate reads critical alone, and the reviewer raised no finding at warning "
            "or note over these documents"
        )
        where = ", ".join(self._repo_relative(path) for path in findings)
        _log.info(
            f"{stage} review round {round_number}: critical={verdict.critical} warning={verdict.warning} "
            f"note={verdict.note} — {consequence}; findings: {where}",
            extra={
                "context": {
                    "stage": stage,
                    "round": round_number,
                    "critical": verdict.critical,
                    "warning": verdict.warning,
                    "note": verdict.note,
                    "findings": where,
                }
            },
        )

    def _review_round(self, cycle: ReviewCycle, *, series: str, round_number: int) -> envelope.Verdict:
        """One review round: a SINGLE never-writer whose return ``call.py`` persists.

        The seat returns its findings, ``call.py`` writes them at the path this
        row declares and parses the VERDICT off what it wrote.
        """
        step = steps.STEPS_BY_ID[cycle.review_step]
        paths = self._findings_set(cycle, series=series, round_number=round_number)
        # Each document named for itself, because the pair this brief reads and
        # the set this stage's boundary checks are no longer the same set: the
        # reviewer still judges CONVENTIONS.md, which `phase-3a` stamped and no
        # boundary here asks after.
        readme = self._kb_root / kb_pipeline.OVERVIEW_DOC
        conventions = self._kb_root / kb_pipeline.CONVENTIONS_DOC
        outcome = self._call(
            step,
            slots={
                "readme-path": self._brief_path(readme),
                "conventions-path": self._brief_path(conventions),
            },
            outputs=paths,
        )
        runlog.require(outcome.verdict is not None, "a review returned without the verdict its row declares")
        assert outcome.verdict is not None  # required above

        self._report_round(outcome.verdict, stage=step.stage, round_number=round_number, findings=paths)
        return outcome.verdict

    def _meta_docs(self, step: steps.Step, *, source: Path | None) -> None:
        """``ov.docs`` and ``p5.fix``: ask the seat for prose, then assemble the document.

        One template, two call sites. What changes between them is whether the
        call is answering a reviewer's findings or answering for the first time,
        and that is a slot with a named absence on the first pass.

        **The seat composes no document.** It answers the one question no read of
        the KB answers — what this corpus is and where a reader starts — and its
        return is prose, which ``call.py`` persists under the scratch layout.
        Every count the document states is read here, at the moment it is
        written, so the numbers are exact by construction rather than checked
        after the fact by a second call.
        """
        prose = self.scratch / steps.overview_prose(stage=step.stage)
        self._call(
            step,
            slots={
                "kb-root": self._brief_path(self._kb_root),
                "remediation-source-path": self._brief_path(source),
            },
            outputs=(prose,),
        )
        # A call carrying a remediation source *is* a fix round: it was handed a
        # reviewer's findings and asked to answer them, which is the one case
        # where composing the document that already stands is a failure.
        self._assemble_overview(
            step,
            prose=prose,
            target=self._kb_root / kb_pipeline.OVERVIEW_DOC,
            fixing=source is not None,
        )

    def _assemble_overview(self, step: steps.Step, *, prose: Path, target: Path, fixing: bool) -> None:
        """The stage's own half: the packaged template, the derived facts, the seat's answer.

        The one place this driver writes under ``kb-root/``, and it writes bytes
        it composed rather than bytes a model returned — the seat's answer
        reaches the file as the value of one slot, in a document whose every
        other word is the template's or the index's.

        **A fix round that composes the document already standing there answered
        the review with nothing, and this is the only place that can tell.** The
        stage's coverage check runs at the boundary, once, after every round has
        run, by which point the document differs from ``HEAD`` merely because the
        draft created it — so from there a no-op fix and a real one are the same
        observation. Here both byte strings are in hand, and what ends the round
        is a comparison between two artifacts rather than a reading of one
        (``kb_tools/CONVENTIONS.md``). The draft is exempt because re-composing
        what stands is exactly what it is for on a resume past a lost boundary:
        the answer on disk is work no boundary accounts for, and re-earning it
        byte for byte is the discard working.
        """
        try:
            text = kb_readme.assemble(
                kb_root=self._kb_root,
                project_name=self.repo_root.name,
                prose={kb_readme.PROSE_SLOT: prose.read_text(encoding="utf-8").strip()},
            )
        except (kb_pipeline.PipelineError, FileNotFoundError) as exc:
            self._halt(baton.EXIT_ENVIRONMENT, f"{step.id}: {exc}")
            return
        except kb_readme.TemplateError as exc:
            # The shipped template and the shipped fact set disagree: neither is
            # anything an operator supplied, so this is a defect in the install
            # rather than a state a build can absorb.
            raise runlog.BoundaryError(str(exc)) from exc
        if fixing and target.is_file() and text == target.read_text(encoding="utf-8"):
            self._halt(
                baton.EXIT_CONTRACT,
                f"{step.id}: the answer composes {self._repo_relative(target)} byte for byte as it already "
                f"stands, so this round answered the review with no change to the document it was asked to fix",
            )
            return
        target.write_text(text, encoding="utf-8")
        _log.info(
            "the overview document was assembled from the index and the seat's answer",
            extra={"context": {"step": step.id, "document": self._repo_relative(target)}},
        )

    def _review_loop(self, step: steps.Step) -> None:
        """The cycle ``p5.fix`` drives.

        Review, fix from what the review wrote, re-review — until the round
        comes back with no critical finding or the cap escalates.

        **The round is this process's own count, opening at
        :data:`FIRST_ROUND`.** A stage reached by the walk is a stage the ledger
        does not record, and a stage's entry is the only place a round is ever
        recorded, so there is no round behind this one to find: whatever an
        earlier process ran here is work no boundary accounts for, and it is
        re-run rather than adopted. Nothing on disk is consulted for the number,
        which is what keeps a findings file a dying process left from reading as
        a completed round and spending a fix budget of one on a crash.

        **The gate is ``critical`` and the severities the reviewer sorted its
        findings into are what it reads.** A count of open findings discards
        that sorting, so a note observing that a filename goes unmentioned in a
        document halts a build exactly as hard as a defect does — which is what
        a reviewer told to decide what is critical has already ruled it is not.
        A warning and a note travel past this gate instead, reported by
        :meth:`_report_round` and repaired by nobody, and the documents stand as
        the seat wrote them.
        """
        cycle = REVIEW_CYCLES[step.stage]
        entry = self._only_series(step)

        round_number = FIRST_ROUND
        verdict = self._review_round(cycle, series=entry.letter, round_number=round_number)

        while verdict.critical > 0:
            findings = self._findings_set(cycle, series=entry.letter, round_number=round_number)
            self._spend_or_escalate(
                entry,
                spent=revisions_spent(series=entry.letter, round_number=round_number),
                artifacts=findings,
                detail=tuple(f"findings: {self._repo_relative(path)}" for path in findings),
            )
            # One reviewing seat, so its findings file already *is* the one
            # remediation source; nothing merges anything.
            self._meta_docs(steps.STEPS_BY_ID[cycle.fix_step], source=findings[0])
            round_number += 1
            verdict = self._review_round(cycle, series=entry.letter, round_number=round_number)

        # The stage's own boundary is where these land, and this is the hand-off
        # to the row that writes it.
        self._rounds[step.stage] = RoundsSpent(
            rounds=round_number, fixes=revisions_spent(series=entry.letter, round_number=round_number)
        )

    # --- overview-drafted ----------------------------------------------------

    def _ov_docs(self, step: steps.Step) -> None:
        """``ov.docs``: the overview document, assembled over the seat's first answer.

        **No skip on what is already on disk.** The row's own boundary is the row
        behind it, so the only invocation that reaches this one is an invocation
        the ledger says never recorded the stage — and an answer left by an
        earlier attempt is then work no boundary accounts for, which is discarded
        rather than adopted (SPEC.md, The Driver's Contract). It costs one call to
        re-ask, against trusting a file a dying process may have half-written.
        """
        self._meta_docs(step, source=None)

    def _ov_docent_check(self, step: steps.Step) -> None:
        """``ov.docent-check``: the commands that make a finished KB navigable are installed.

        The filenames are ``kb_util``'s constant, imported and never restated —
        a KB whose docent commands are absent is an incomplete install (exit
        14), not a barrier, because no answer an operator could give would
        install them. The remediation relayed is preflight's own ``restore:``
        line rather than a second wording of the same action.
        """
        commands = self.repo_root / kb_util.CLAUDE_DIRNAME / kb_util.COMMANDS_DIRNAME
        missing = [name for name in kb_util.DOCENT_COMMAND_FILENAMES if not (commands / name).is_file()]
        if not missing:
            return
        report = self.ops.preflight().stdout
        restore = tuple(line.strip() for line in report.splitlines() if _RESTORE_MARKER in line)
        self._halt(
            baton.EXIT_ENVIRONMENT,
            f"{step.id}: missing {', '.join(missing)} under {self._repo_relative(commands)} — incomplete install",
            *(restore or ("restore: re-install the agent set into this repository, then re-run",)),
        )


#: Row id → the handler that executes it. A row with no entry here and no place
#: in :data:`LOOP_DRIVEN_STEPS` fails the boundary check at the top of the walk,
#: so a new row cannot be silently unexecuted.
_HANDLERS: Mapping[str, Callable[[Runner, steps.Step], None]] = MappingProxyType(
    {
        "pre.lock": Runner._pre_lock,
        "pre.preflight": Runner._pre_preflight,
        "pre.charter": Runner._pre_charter,
        "pre.proceed": Runner._pre_proceed,
        "pre.kb-root": Runner._pre_kb_root,
        "start.record": Runner._start_record,
        "dg.build": Runner._dg_build,
        "dg.record": Runner._record_stage,
        "seed.graph-init": Runner._seed_graph_init,
        "seed.record": Runner._record_stage,
        "declared.build": Runner._declared_build,
        "declared.record": Runner._record_stage,
        "discover.build": Runner._discover_build,
        "discover.record": Runner._record_stage,
        "depends.attribute": Runner._depends_attribute,
        "depends.record": Runner._record_stage,
        "p3a.gate": Runner._p3a_gate,
        "p3a.record": Runner._record_stage,
        "ov.docent-check": Runner._ov_docent_check,
        "ov.docs": Runner._ov_docs,
        "ov.record": Runner._record_stage,
        "p5.fix": Runner._review_loop,
        "p5.record": Runner._record_stage,
    }
)


# --- the entry point ---------------------------------------------------------


def _report_unconsumed(decisions: Sequence[str]) -> None:
    """State the ``--decide`` answers no barrier asked for. Silence is the one wrong outcome.

    **The whole statement is in the message text, and it goes to stderr.** The
    console tee prints a record's message alone, so a list carried in the
    context would reach ``run.log`` and not the operator; and stdout is what a
    session pastes — the ledger render, the baton, a failing tool's report — so
    a notice about the invocation itself belongs on the other stream, where it
    cannot land inside a block somebody is about to copy.

    An over-specified resume is the ordinary way this happens and it is not a
    failure: the answer decided nothing because the barrier it names was never
    raised, and the run's exit is whatever the walk decided. What it may not be
    is quiet, because the operator supplied the answer believing it would act.
    """
    if not decisions:
        return
    runlog.notify(
        f"{len(decisions)} --decide answer(s) decided nothing in this run: {', '.join(decisions)} — "
        f"the barrier each one names was never raised, so each was discarded"
    )


def execute(
    *,
    config: DriverConfig,
    paths: runlog.RunPaths,
    decisions: Sequence[Decision] = (),
    invoker: inference.Invoker | None = None,
    ops: LedgerOps | None = None,
    repo_root: Path | None = None,
    stages: Sequence[str] = steps.TABLE_STAGE_IDS,
    prompt_templates_dir: Path | None = None,
) -> Result:
    """Run the build from its ledger position and return the terminal state.

    ``stages`` is the walk, defaulting to every stage of the pipeline. It stays
    a parameter for the same reason ``ops`` and ``invoker`` are — a test that
    means to exercise one stage's rows should not have to walk every other
    stage to reach them — but no caller narrows it to buy a green any more: the
    default is the whole walk and a replayed run is honestly green over it.
    ``prompt_templates_dir`` is the same
    parameterization ``call.Caller`` already documents — composition
    parameterized at its source, so a scenario can be composed against templates
    other than the installed set.

    Every *pipeline* ending returns rather than raises, including a barrier.
    :class:`runlog.BoundaryError` still propagates: a driver defect is exit 15
    and never a pipeline outcome, and ``cli`` is where that translation lives.
    """
    # The resolver is built before the root is resolved, so that even a run
    # that never reaches a barrier reports the decisions that answered nothing
    # Every terminal path carries that list, not only the ones that got
    # far enough to raise something.
    answers = barriers.Resolver(config_decisions=config.decisions, cli_decisions=decisions)

    if repo_root is None:
        try:
            repo_root = kb_util.find_git_root()
        except kb_util.RepoRootError as exc:
            result = Result(
                exit_code=baton.EXIT_ENVIRONMENT,
                detail=(str(exc),),
                unconsumed_decisions=answers.unconsumed,
            )
            return replace(result, context=_context(config, paths, result))

    runner = Runner(
        config=config,
        paths=paths,
        repo_root=repo_root,
        caller=call.Caller(
            invoker=invoker if invoker is not None else inference.SubprocessInvoker(),
            config=config,
            repo_root=repo_root,
            paths=paths,
            **({} if prompt_templates_dir is None else {"prompt_templates_dir": prompt_templates_dir}),
        ),
        answers=answers,
        ops=ops if ops is not None else ledger_ops_for(repo_root),
        stages=stages,
    )

    try:
        result = runner.run()
    except _Halt as halt:
        result = halt.result

    # The unconsumed list is final only once the walk has stopped raising.
    result = replace(result, unconsumed_decisions=answers.unconsumed)
    _report_unconsumed(result.unconsumed_decisions)
    return replace(result, context=_context(config, paths, result))
