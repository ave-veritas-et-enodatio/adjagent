"""The outer loop: stage iteration, step execution, cap loops, resume, exit selection.

The sequencer. It walks ``steps.py``'s table in order, computes each row's
per-call slots, hands calls to ``call.py``, records stages through
``ledger.py``, raises registry barriers through ``barriers.py``, and chooses the
exit code the run ends on. It holds no template text, constructs no subprocess,
and reads no TOML.

**Position comes from the ledger, never from memory.** Scratch presence
is an optimization on top of it: within a stage, a step whose declared
artifacts are all present and non-empty is skipped
(:func:`resume_skip`), and within a ``wave*`` step the skip is per member
(:func:`pending_members`, through :meth:`Runner._wave`) — a wave killed
mid-flight re-briefs only the members whose artifacts are absent, and if that
leaves exactly one, the step collapses to a SINGLE. Both predicates are free
functions with no run state, because they are the two places a resume can
silently do the wrong work twice. **No row in the current table is a wave**:
the member-granular half of the resume is the run side of the call machinery's
wave route, which outlives the last stage that used it (``steps.Unit.WAVE``,
``steps.Writer.WAVE_SESSION``).

**Loop counters are reconstructed from disk**: the round of a series
is ``1 + max(N)`` over ``review/<stage>-<series><N>-*.md``. Counting files is
wrong and is not done — how many files a round writes is a property of the
stage, so a count-based rule reads the round number high by that factor and
trips the cap early.

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
(:class:`ReviewCycle`) — over documents a seat wrote, whose round numbers come
off disk. **That cycle turns on ``critical`` alone**: a warning and a note are
reported by :meth:`Runner._report_round` and carried past, so the only finding
that spends a fix round is one the reviewer itself called critical.

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

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from .. import kb_index_lib, kb_pipeline, kb_readme, kb_util
from . import barriers, baton, call, envelope, ledger, runlog, steps, transport, watch
from .config import NO_INFERENCE_FLAG, THROUGH_FLAG, Decision, DriverConfig

_log = runlog.logger("run")

# Rows whose execution belongs to another row's handler rather than to the
# linear walk. The loop row is the cycle: it drives its stage's review, fix and
# re-review for as many rounds as its series allows, so the walk must not also
# run them in table order. Every other row is walked.
LOOP_DRIVEN_STEPS: frozenset[str] = frozenset({"p5.review"})

# Preflight's own remediation marker. `p5.docent-check` relays the `restore:`
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
        detail=result.detail,
        unconsumed_decisions=result.unconsumed_decisions,
    )


def _finish(config: DriverConfig, paths: runlog.RunPaths, result: Result) -> Result:
    """Every terminal path's last act: write the cadence, then attach the context.

    The cadence extraction belongs here rather than beside
    ``write_exit_json`` for one reason: a record carries the *stage* a call
    served, and ``cli`` states in its own docstring that it holds no stage
    knowledge. This is the outermost layer that has any, and by the time a
    result exists every capture in ``calls/`` is complete — so the pass is one
    read of each and no call is missed. Both of ``execute``'s exits route
    through here, including the one that never reaches the sequencer: a run that
    made no calls leaves an empty ``cadence.jsonl`` rather than none, so
    "missing" never has to be told apart from "never extracted".
    """
    runlog.write_cadence(paths, stages={step.id: step.stage for step in steps.STEPS_BY_ID.values()})
    return replace(result, context=_context(config, paths, result))


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
    revision_entry: Callable[..., ledger.Outcome]
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
        revision_entry=lambda: ledger.revision_entry(repo_root),
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


def resume_skip(outputs: Sequence[Path]) -> bool:
    """The resume-skip predicate: every declared artifact present and non-empty.

    A row declaring no artifacts is never skipped by this rule — a driver-op, a
    record, or a barrier row is cheap and idempotent, and re-recording is
    explicitly safe. Only work that left evidence behind can be skipped
    on the evidence.
    """
    return bool(outputs) and all(present(path) for path in outputs)


def pending_members(members: Sequence[Member]) -> tuple[Member, ...]:
    """Partial-wave reconciliation, at member granularity.

    A wave killed mid-flight may have left some member artifacts on disk.
    Re-running the step drops the members whose declared artifacts are present
    and non-empty and re-briefs only the rest.
    """
    return tuple(member for member in members if not present(member.artifact))


# `review/<stage>-<series><N>-<who>.md`: the series letter and the round number,
# read off the filename. This is the *only* place a round number comes from.
_ROUND_RE = re.compile(r"-([a-z])(\d+)-")


def next_round(review_dir: Path, *, stage: str, series: str, author: str = "") -> int:
    """One rule, for every stage and both series: ``1 + max(N)``, or 1 when none.

    **Counting files is wrong and is not done here**: a round writes one file
    per author and how many authors a round has is a property of the stage, so
    a count-based rule runs iteration numbers high by that factor and trips the
    cap early.

    ``author`` narrows the glob to one author's files, for a stage where a
    round's defining file is not the only one a process can leave behind
    before dying — a row that writes a file ahead of the one that actually
    closes the round would otherwise leave a number that reads as a completed
    round when the row that closes it never ran. No current row needs the
    narrowing; every caller today omits ``author`` and counts every file the
    stage's series writes.
    """
    rounds = []
    for path in review_dir.glob(f"{stage}-*-{author}.md" if author else f"{stage}-*"):
        match = _ROUND_RE.search(path.name)
        if match is not None and match.group(1) == series:
            rounds.append(int(match.group(2)))
    return 1 + max(rounds, default=0)


def revisions_spent(*, series: str, round_number: int) -> int:
    """How many revisions a series has spent by the time round ``round_number`` is reviewed.

    The two series differ by their opening move, and that is the whole of the
    difference: the initial series opens with a *review* of the design as
    written, so round N follows N-1 revisions; the gate-driven series is opened
    by a revision carrying the gate's direction, so round N follows N.
    """
    return round_number if series == steps.SERIES_GATE else round_number - 1


# `[preflight] FACT runner-file      justfile (runner: just)` — the FACT line
# the detection source for `start.runner-choice`. An item line is a
# format the driver may read; the absence of the parenthesized runner is
# the "neither justfile nor Makefile" case.
_RUNNER_FACT_RE = re.compile(r"^\[preflight\]\s+FACT\s+runner-file\s+.*\(runner:\s*(\w+)\)", re.MULTILINE)


def runner_file_present(preflight_stdout: str) -> bool:
    """Whether preflight found a runner file. False means ``init`` needs ``--runner``."""
    return _RUNNER_FACT_RE.search(preflight_stdout) is not None


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


# --- the walk ----------------------------------------------------------------


class Runner:
    """One process's walk through the step table.

    Every method that can end the run raises :class:`_Halt`; the walk itself
    reads as a sequence of steps rather than as a chain of error checks, which
    is the only way a sequencer of this many stages stays legible.
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
        # `[run] build_mode` is the carrier; `start.build-mode` is the override
        # door, resolved at `pre.mode`.
        self.build_mode = config.run.build_mode
        self._seq = 0
        self._recorded: frozenset[str] = frozenset()
        self._runner_file = True

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
        layer knows one, and only by now is the build mode settled. Exit
        :data:`baton.EXIT_BOUNDED`, whose card resumes without the bound.
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
        for step in steps.steps_for(stage):
            if step.id in LOOP_DRIVEN_STEPS:
                continue
            if not self._applies(step):
                _log.info(
                    "row does not apply to this run",
                    extra={
                        "context": {
                            "step": step.id,
                            "when": step.when,
                            "build_mode": self.build_mode,
                            "spends_inference": step.spends_inference,
                        }
                    },
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
        """The conditional rows: the two build modes, and what this run will spend.

        The predicate itself is ``steps``', because the note below asks the same
        question of the same rows, and two readings of "does this row apply" is
        one more than the table can have.
        """
        return steps.applies(step, build_mode=self.build_mode, spend_inference=not self.config.run.no_inference)

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
        del step
        outcome = self._halt_unless(self.ops.preflight())
        self._runner_file = runner_file_present(outcome.stdout)

    def _pre_charter(self, step: steps.Step) -> None:
        """``pre.charter``: resolve the charter's existence once, for every row that reads it.

        A charter is optional. Where one stands at the configured path the
        build carries it — the record names it and the two briefs that quote it
        carry its path; where none does, the build runs on its sources and the
        rows say so. The existence question is settled here and nowhere else,
        so no brief is ever handed a path to a file that is not there.
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

    def _pre_mode(self, step: steps.Step) -> None:
        """``pre.mode``: both start barriers resolved before anything is seeded or recorded."""
        del step
        mode = self.answers.resolve(barriers.START_BUILD_MODE, fallback=self.build_mode)
        self.build_mode = mode.answer
        self._decide(barriers.START_PROCEED)
        _log.info("build mode resolved", extra={"context": {"build_mode": self.build_mode}})

    def _pre_revision_entry(self, step: steps.Step) -> None:
        del step
        self._halt_unless(self.ops.revision_entry())

    def _start_record(self, step: steps.Step) -> None:
        """``start.record``: the build boundary. rc 5 (already started) reads as done.

        A build with no charter records without one: the commit's body names a
        charter only where there is one to name.
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
        """``dg.build`` (fresh only): the document tree, derived from this run's sources.

        The tree is the build's own product rather than something handed to it.
        Every source is a volume root, and ``kb-root/`` is where the tree lands
        — a name the toolchain hard-codes and takes no override for.

        The front end writes the tree whole, so this row over a tree a finished
        build already stamped would overwrite the documents the spine sits in.
        Two things keep it off that tree: the row is fresh-only, and a recorded
        stage is never re-walked.
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
        """``seed.graph-init`` (fresh only): initialise the claim-graph spine over the tree.

        A ``kb-root/`` with no document tree in it halts the run: the stage
        above is what writes one, and its boundary commit is also what leaves
        the worktree clean for this seed's own preflight.
        """
        del step
        runner = self.config.run.runner
        if runner is None and not self._runner_file:
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
        """``declared.build`` (fresh only): the claims the corpus's author marked, mechanically."""
        self._claim_graph(step)

    def _discover_build(self, step: steps.Step) -> None:
        """``discover.build`` (fresh only): stage C-inf, one ask per awaiting document.

        The model this row spends is spawned inside ``kb_claimgraph``, through
        that package's own seat seam, so no flag of this driver replaces it.
        That is the row's ``spends_own_inference`` declaration, and a run told to
        spend none stops before this stage rather than reaching it — so there is
        no condition left here to warn about.
        """
        self._claim_graph(step)

    def _depends_attribute(self, step: steps.Step) -> None:
        """``depends.attribute`` (fresh only): stage D, over the graph discovery left."""
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
        if not self.config.run.no_inference:
            return ""
        dropped = steps.inference_rows(stage, build_mode=self.build_mode)
        if not dropped:
            return ""
        return (
            f"{NO_INFERENCE_FLAG}: this build spent no model call, so {', '.join(dropped)} did not run. "
            f"Every row of this stage that costs none ran; what the dropped rows would have authored is "
            f"absent from the KB, and whatever the stage before them wrote about it stands."
        )

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

    @property
    def _review_dir(self) -> Path:
        # Derived from the generic findings pattern rather than restated: the
        # findings path is the contract, and "review/" is a fact about it that
        # holds for every stage's findings alike.
        return self.scratch / PurePosixPath(steps.FINDINGS).parent

    def _only_series(self, step: steps.Step) -> steps.LoopSeries:
        """The one capped series a single-series loop row carries (every loop but ``p1a.loop``)."""
        runlog.require(len(step.series) == 1, "this loop row carries exactly one capped series", step=step.id)
        return step.series[0]

    # --- what a capped loop has actually spent -------------------------------
    #
    # A remediation act leaves nothing a later process can read. A revision
    # overwrites the design doc without rotating it; a fix wave edits the
    # KB in place, and `_fix` says so in its own docstring — "a fix leaves no
    # new named artifact to reconcile against". What says the act happened is
    # the NEXT round's findings, so a process that dies between the two erases
    # the evidence of work it really did: the resume repeats the act with the
    # same ordinal, and the cap counts neither of them.
    #
    # The answer is to record the state rather than infer it from the artifact,
    # kept under the driver's own area of the scratch tree. Deliberately NOT in
    # the `review/` grammar: `next_round` reads that directory by filename, and
    # a file there is a round.

    def _attempts_file(self, *, stage: str, series: str) -> Path:
        """Where one capped series records the acts it has performed."""
        return self.scratch / stage / f"attempts-{series}"

    def _attempts_performed(self, *, stage: str, series: str) -> set[int]:
        path = self._attempts_file(stage=stage, series=series)
        if not path.is_file():
            return set()
        return {int(token) for token in path.read_text(encoding="utf-8").split() if token.isdigit()}

    def _record_attempt(self, *, stage: str, series: str, ordinal: int) -> None:
        """Record an act the moment it returns — before anything that can stop the run."""
        path = self._attempts_file(stage=stage, series=series)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{ordinal}\n")

    def _already_performed(self, *, stage: str, series: str, ordinal: int) -> bool:
        """Did an earlier process perform this act and die before it was reviewed?"""
        if ordinal not in self._attempts_performed(stage=stage, series=series):
            return False
        _log.info(
            "resume-skipping an act this series already performed",
            extra={"context": {"stage": stage, "series": series, "ordinal": ordinal}},
        )
        return True

    def _spent(self, *, stage: str, series: str, round_number: int) -> int:
        """How many acts this series has spent against its cap.

        The round arithmetic is the floor rather than the answer: it is exact
        for a run that never crashed, and it under-counts by precisely the acts
        a dead process performed and no findings file recorded. Taking the
        larger of the two is what makes a repeatedly interrupted loop advance
        its cap instead of revising forever, and it leaves a run started before
        this record existed counting the way it always did.
        """
        performed = len(self._attempts_performed(stage=stage, series=series))
        return max(performed, revisions_spent(series=series, round_number=round_number))

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
        readme, conventions = (self._kb_root / name for name in kb_pipeline.META_DOCS)
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

    def _resume_findings(self, cycle: ReviewCycle, *, series: str) -> tuple[int, envelope.Verdict] | None:
        """Resume at round granularity: the highest round whose findings are all written."""
        stage = steps.STEPS_BY_ID[cycle.review_step].stage
        last = next_round(self._review_dir, stage=stage, series=series) - 1
        if last < 1:
            return None
        paths = self._findings_set(cycle, series=series, round_number=last)
        if not resume_skip(paths):
            return None
        try:
            rounds = [envelope.parse_verdict(path.read_text(encoding="utf-8")) for path in paths]
        except envelope.ParseError as exc:
            _log.warning(
                "a findings file on disk carries no parsable verdict; reviewing again",
                extra={"context": {"stage": stage, "round": last, "complaint": str(exc)}},
            )
            return None
        _log.info("resuming from findings already on disk", extra={"context": {"stage": stage, "round": last}})
        verdict = envelope.Verdict(
            critical=sum(item.critical for item in rounds),
            warning=sum(item.warning for item in rounds),
            note=sum(item.note for item in rounds),
        )
        # The round this resume adopts was reported by the process that ran it,
        # and that process's log is not this one's. Reporting it here is what
        # keeps a resumed build's own record complete.
        self._report_round(verdict, stage=stage, round_number=last, findings=paths)
        return last, verdict

    def _meta_docs(self, step: steps.Step, *, source: Path | None) -> None:
        """``p5.docs`` and ``p5.fix``: ask the seat for prose, then assemble the document.

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
        self._assemble_overview(step, prose=prose, target=self._kb_root / kb_pipeline.OVERVIEW_DOC)

    def _assemble_overview(self, step: steps.Step, *, prose: Path, target: Path) -> None:
        """The stage's own half: the packaged template, the derived facts, the seat's answer.

        The one place this driver writes under ``kb-root/``, and it writes bytes
        it composed rather than bytes a model returned — the seat's answer
        reaches the file as the value of one slot, in a document whose every
        other word is the template's or the index's.
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
        target.write_text(text, encoding="utf-8")
        _log.info(
            "the overview document was assembled from the index and the seat's answer",
            extra={"context": {"step": step.id, "document": self._repo_relative(target)}},
        )

    def _review_loop(self, step: steps.Step) -> None:
        """The cycle ``p5.fix`` drives.

        Review, fix from what the review wrote, re-review — until the round
        comes back with no critical finding or the cap escalates. The
        round number is reconstructed from disk every time, so a resume
        neither re-reviews a written round nor re-spends a cap.

        **The gate is ``critical`` and the severities the reviewer sorted its
        findings into are what it reads.** A count of open findings discards
        that sorting, so a note observing that a filename goes unmentioned in a
        document halts a build exactly as hard as a defect does — which is what
        a reviewer told to decide what is critical has already ruled it is not.
        A warning and a note travel past this gate instead, reported by
        :meth:`_report_round` and repaired by nobody, and the documents stand as
        the seat wrote them.

        A fix leaves no artifact of its own, so the round's findings are still
        the last thing on disk after it runs, and without the attempt record a
        process dying there would resume into the same round and run the whole
        cycle a second time against work it had already done — for as many
        processes as it took, with the cap counting none of it. The attempt
        record closes that.
        """
        cycle = REVIEW_CYCLES[step.stage]
        entry = self._only_series(step)
        stage = step.stage

        resumed = self._resume_findings(cycle, series=entry.letter)
        if resumed is not None:
            round_number, verdict = resumed
        else:
            round_number = next_round(self._review_dir, stage=stage, series=entry.letter)
            verdict = self._review_round(cycle, series=entry.letter, round_number=round_number)

        while verdict.critical > 0:
            # The ordinal is a fact about the round and names the same fix on
            # every process that reaches it; what has been spent is a fact about
            # the series. Reading them separately is what lets a resume skip an
            # act it already performed and still count it against the cap.
            ordinal = revisions_spent(series=entry.letter, round_number=round_number) + 1
            spent = self._spent(stage=stage, series=entry.letter, round_number=round_number)
            findings = self._findings_set(cycle, series=entry.letter, round_number=round_number)
            self._spend_or_escalate(
                entry,
                spent=spent,
                artifacts=findings,
                detail=tuple(f"findings: {self._repo_relative(path)}" for path in findings),
            )
            if not self._already_performed(stage=stage, series=entry.letter, ordinal=ordinal):
                # One reviewing seat, so its findings file already *is* the one
                # remediation source; nothing merges anything.
                self._meta_docs(steps.STEPS_BY_ID[cycle.fix_step], source=findings[0])
                self._record_attempt(stage=stage, series=entry.letter, ordinal=ordinal)
            round_number += 1
            verdict = self._review_round(cycle, series=entry.letter, round_number=round_number)

    # --- phase-5 -------------------------------------------------------------

    def _p5_docs(self, step: steps.Step) -> None:
        """``p5.docs``: the overview document, assembled over the seat's first answer.

        Both halves must be on disk for the row to be skipped — the answer and
        the document assembled from it. A document standing over an answer that
        is gone is a document nothing can be re-assembled from, which is the one
        state a resume must not read as finished.
        """
        targets = (
            self.scratch / steps.overview_prose(stage=step.stage),
            self._kb_root / kb_pipeline.OVERVIEW_DOC,
        )
        if resume_skip(targets):
            _log.info("the overview document and the answer it was assembled from are on disk; calling nothing")
            return
        self._meta_docs(step, source=None)

    def _p5_docent_check(self, step: steps.Step) -> None:
        """``p5.docent-check``: the commands that make a finished KB navigable are installed.

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
        "pre.mode": Runner._pre_mode,
        "pre.revision-entry": Runner._pre_revision_entry,
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
        "p5.docs": Runner._p5_docs,
        "p5.fix": Runner._review_loop,
        "p5.docent-check": Runner._p5_docent_check,
        "p5.record": Runner._record_stage,
    }
)


# --- the entry point ---------------------------------------------------------


def execute(
    *,
    config: DriverConfig,
    paths: runlog.RunPaths,
    decisions: Sequence[Decision] = (),
    invoker: transport.Invoker | None = None,
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
            return _finish(config, paths, result)

    runner = Runner(
        config=config,
        paths=paths,
        repo_root=repo_root,
        caller=call.Caller(
            invoker=invoker if invoker is not None else transport.SubprocessInvoker(),
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
    if result.unconsumed_decisions:
        _log.warning(
            "--decide values whose barrier was never raised in this run",
            extra={"context": {"unconsumed": ", ".join(result.unconsumed_decisions)}},
        )
    return _finish(config, paths, result)
