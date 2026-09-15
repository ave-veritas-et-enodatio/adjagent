"""Call **policy**: transport retry, contract validation, the one re-ask, persistence.

The driver's half of one call. ``kb_tools.inference`` owns the mechanism —
spawn, capture, watchdog, kill/reap — and classifies how a call ended without
ever naming an exit code; it is shared with every other tool that asks a seat
something, so nothing of this driver's policy lives there. This module is that
policy, and owns everything downstream of the classification:

* **Retry.** A retryable transport death (a mid-stream failure, a silence
  wedge, an expired total bound, a ``result`` event the CLI marked failed) is
  retried on ``[retry] transport_attempts`` with ``backoff_seconds``;
  exhaustion is exit 12. Two classifications are never retried, and each earns
  a different exit than the exhaustion would give it. A **CLI rejection** — the
  zero-event classification — is exit 13, with the stderr line as the
  diagnostic, because three identical failures and a misleading exit 12 is the
  whole reason that classifier runs first. A **spawn failure** — a
  ``[claude] command`` that is not runnable — is exit 14, the code
  ``ledger.py`` already gives a tool it could not spawn: the fault is in the
  environment the run was launched in, and both the exit and its card say so
  rather than naming a defect in the driver.
* **Contract validation — existence and parse only.** Declared artifacts exist
  and are non-empty; the envelope parses; the ``VERDICT`` line parses;
  the ``SCOPE`` line parses. Never content quality — that is the
  reviewers' job. A **second ``init``** fails this check rather than passing on
  the premature success it announces. That is a *step-model* rule and so it is
  here: one call is one turn for this driver, where the layer below promises no
  such thing and returns the same call as an ordinary success having counted
  the events (the brief-side defense is ``{dispatch_discipline}``).
* **The one re-ask.** A contract failure re-briefs the *same* step once, with
  the validator's complaint appended to the identical composed brief; a second
  failure is exit 17, naming the step and the complaint. It is not a barrier:
  no pre-supplied answer can resolve a step that cannot produce its declared
  output shape twice.
* **Persistence, three routes**, keyed to the seat's own definition.
  ``driver`` — a never-writer SINGLE's returned text *is* the artifact, and
  this module writes it to a contract path the model never chose, through a temp
  and a rename so the path never holds bytes nobody finished writing.
  ``wave-session`` and ``worker`` — the call wrote its own artifacts and this
  module only validates them. The routes are the mechanism, not a sandbox: the
  CLI does not enforce a definition's tool list, so a never-writer that writes
  anyway is a definition-compliance defect, invisible here except where an
  output contract happens to notice.

**The driver process never writes under ``kb-root/``.** Its writes from
here are exactly two — the composed brief in the run directory, and the
driver-persisted artifact under the scratch layout root — and the second is
held by a boundary check rather than by convention.

**The boundary checks are this driver's own**, because what they are is a
driver defect the run must stop on with exit 15: a cwd or a composed brief that
is not there, a capture directory the run never made, a bound the config let
through non-positive, and a ``[claude] command`` prefix smuggling a
``--model``. The layer below refuses the same states as ordinary
``ValueError``s at its own door; a tool asking a seat something has no exit
ladder for them to land on, which is why they are stated twice rather than
moved down. No brief text can reach argv at all — ``inference.build_argv``
has no parameter through which it could.

**What this module deliberately does not do.** It parses the envelope and hands
it back; appending its deviations to ``deviations.jsonl`` needs the run id and
the stage, which are the run loop's. It counts nothing about rounds, caps, or
position, and it selects no successor.

**Dependency note.** ``call`` depends on ``{inference, prompt_templates, envelope,
runlog, config}``. Naming an exit code additionally requires ``baton``, and executing a
row requires ``steps`` — both leaves, both imported for the reason
``ledger.py`` states for ``baton``: copying those constants here would be
exactly the drift single-sourcing exists to prevent. The atomic write is
``kb_survey.manifest.write_text_atomic``, the toolchain's own, imported for the
same reason — a second implementation of a write that must not tear is a second
thing to get right.

Stdlib only.
"""

import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .. import inference
from ..kb_survey.manifest import write_text_atomic
from . import baton, prompt_templates, runlog, steps
from .config import DriverConfig
from .envelope import Envelope, ParseError, Scope, Verdict, parse_envelope, parse_scope, parse_verdict

_log = runlog.logger("call")

# The flag the driver never passes, in both spellings argparse accepts. An
# explicit `--model` overrides a seat's frontmatter pin, so "the pin is
# authoritative" holds only while the flag is absent — and exact list membership
# reads `--model haiku` and misses `--model=haiku`, the identical override
# applied to every seat with no log line and no change of exit code.
MODEL_FLAG = "--model"

# The re-ask's brief and captures carry this suffix. It belongs to the brief
# filename grammar, so it is `prompt_templates`' — named here only because this
# is where the second ask is labelled.
REASK_SUFFIX = prompt_templates.REASK_SUFFIX

# The units this module serves. Every other row in the step table is a
# driver-op, a refresh, or a gate, and none of those spawns inference.
CALL_UNITS = (steps.Unit.SINGLE, steps.Unit.WAVE, steps.Unit.WAVE_STAR)

# The three persistence routes, plus the absence of one: a calling row whose
# whole return travels in the envelope writes nothing at all. A route describes
# where an artifact comes from, so a row with no artifact has none — but it must
# then declare no artifact either, which `_check` asserts. `tool` belongs to
# rows that make no call.
CALL_WRITERS = (steps.Writer.NONE, steps.Writer.DRIVER, steps.Writer.WAVE_SESSION, steps.Writer.WORKER)

# The parses that read a *document* rather than a return. On a never-writer row
# the document IS the return, so the distinction costs nothing; on the
# write-to-disk route it is the whole point — the seat writes the design to the
# path the driver named and returns only the envelope, precisely because a
# design carried as a message body is truncated in transport and what a
# truncation takes is the tail, where the scope line sits.
DOCUMENT_PARSES = frozenset({steps.Parse.SCOPE})

_REASK_HEADING = "## Re-ask — the previous return did not satisfy this brief's output contract"
_REASK_PREAMBLE = (
    "A mechanical validator rejected the previous answer to this brief. The check is existence "
    "and parse only; it is not a judgment about the content:"
)
_REASK_CLOSE = "Do the same work again and return it in the declared shape. Nothing else has changed."


@dataclass(frozen=True, kw_only=True)
class CallRequest:
    """One step's call, as the run loop composes it.

    ``outputs`` are the step's declared artifacts with every ``<...>`` segment
    already expanded — absolute paths, because whose cwd a relative one would
    be resolved against is exactly the ambiguity a contract check must not
    have. **``slots`` carries paths in the other direction and the same reason
    binds them**: every value of a :data:`steps.PATH_SLOTS` slot is an absolute
    path that exists, or — where the slot is optional — the named absence, so
    that no brief states a path a seat can only act on by going looking.
    ``members`` is the count that fixes the call
    unit: a ``wave*`` row is a WAVE above one member and a SINGLE at one, which
    is what lets the one-volume mini-run exercise both paths without a second
    code path.
    """

    step: steps.Step
    seq: int
    slots: Mapping[str, str] = field(default_factory=dict)
    outputs: tuple[Path, ...] = ()
    members: int = 1


@dataclass(frozen=True, kw_only=True)
class CallOutcome:
    """What one step's call produced, and the driver exit that holds if it failed.

    ``exit_code`` is :data:`baton.EXIT_OK` when the step met its contract, and
    12, 13, 14, or 17 otherwise. ``detail`` is the baton's extra ASK lines —
    for exit 17, the step and the validator's complaint; for exit 14, the
    ``restore:`` line that card is printed to carry.
    """

    exit_code: int
    result_text: str = ""
    envelope: Envelope | None = None
    verdict: Verdict | None = None
    scope: Scope | None = None
    written: tuple[Path, ...] = ()
    attempts: int = 0
    detail: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == baton.EXIT_OK


@dataclass(frozen=True, kw_only=True)
class _Ask:
    """One ask's transport history: how it ended, and how many invocations that took."""

    result: inference.CallResult
    attempts: int


@dataclass(frozen=True)
class Bounds:
    """The two per-call bounds. Both come from config; neither has a default here."""

    silence_seconds: float
    total_seconds: float


_spawn_lock = threading.Lock()
_call_is_live = False


@contextmanager
def _single_flight() -> Iterator[None]:
    """Exactly one ``claude`` subprocess at a time. Parallelism lives inside a wave.

    A driver policy and not the layer below's, which serves callers that make no
    such promise: a tool asking a seat a question is not a build and owns its own
    concurrency.
    """
    global _call_is_live
    with _spawn_lock:
        runlog.require(not _call_is_live, "a call would be spawned while another is still live")
        _call_is_live = True
    try:
        yield
    finally:
        with _spawn_lock:
            _call_is_live = False


def _require_no_model(tokens: Sequence[str], **context: object) -> None:
    """Refuse a ``--model`` in either spelling argparse accepts. Exit 15 for both."""
    smuggled = [token for token in tokens if token == MODEL_FLAG or token.startswith(f"{MODEL_FLAG}=")]
    runlog.require(not smuggled, "the driver never passes --model", smuggled=" ".join(smuggled), **context)


def is_wave(step: steps.Step, *, members: int) -> bool:
    """One call is a SINGLE. A ``wave*`` row is a WAVE only above one member."""
    runlog.require(members >= 1, "a call needs at least one member", step=step.id, members=members)
    if step.unit is steps.Unit.WAVE:
        return True
    return step.unit is steps.Unit.WAVE_STAR and members > 1


def _within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _path_complaints(slots: Mapping[str, str]) -> list[str]:
    """What is wrong with each path this call's brief would state. Empty is a pass.

    Two preconditions over :data:`steps.PATH_SLOTS` rather than one, because a
    relative path and an absent one fail identically at the seat — it goes
    looking — and a precondition is what a brief instructing it not to would be
    standing in for. The named absence is the third legal value and belongs to
    :data:`steps.OPTIONAL_PATH_SLOTS` alone: a required slot names something an
    earlier stage has already produced, so nothing is absent there without
    something having gone wrong before this call.
    """
    complaints: list[str] = []
    for slot in sorted(slots.keys() & steps.PATH_SLOTS):
        value = slots[slot]
        if value == steps.NOTHING:
            if slot in steps.REQUIRED_PATH_SLOTS:
                complaints.append(f"{slot} carries the named absence, and this slot admits none")
            continue
        path = Path(value)
        if not path.is_absolute():
            complaints.append(f"{slot}={value} is relative, and a seat has no base to resolve it against")
        elif not path.exists():
            complaints.append(f"{slot}={value} is not there")
    return complaints


def _reask_brief(brief_text: str, complaint: str) -> str:
    """The same brief, with the validator's complaint attached — the one re-ask."""
    quoted = "\n".join(f"    {line}" for line in complaint.splitlines())
    body = [brief_text.rstrip("\n"), "", "---", "", _REASK_HEADING, "", _REASK_PREAMBLE, "", quoted, "", _REASK_CLOSE]
    return "\n".join(body) + "\n"


@dataclass(frozen=True, kw_only=True)
class Caller:
    """The invariant half of a call: everything that does not change between steps.

    ``prompt_templates_dir`` is a parameter for the same reason
    ``prompt_templates.load`` takes one — composition is parameterized at its
    source — so a scenario can be composed against templates other than the
    installed set. It is the shelf the composer reads from, never
    ``paths.briefs``, which is where a *composed* brief then lands. ``sleep`` is
    the backoff clock, injected so the retry policy can be exercised without
    spending its own backoff.
    """

    invoker: inference.Invoker
    config: DriverConfig
    repo_root: Path
    paths: runlog.RunPaths
    prompt_templates_dir: Path = prompt_templates.PROMPT_TEMPLATES_DIR
    sleep: Callable[[float], None] = time.sleep

    @property
    def scratch_root(self) -> Path:
        """The build-artifact layout root — the only place under the repo this module writes."""
        return self.repo_root / steps.SCRATCH_ROOT

    # --- the one entry point -------------------------------------------------

    def execute(self, request: CallRequest) -> CallOutcome:
        """Compose, call, validate, persist — under one retry policy and one re-ask.

        Returns rather than raises for every *pipeline* outcome. A boundary
        violation still raises :class:`runlog.BoundaryError` (exit 15): a step
        table and a template that disagree are a driver defect, not something a
        build can absorb.
        """
        step = request.step
        template, wave = self._check(request)
        brief_text = prompt_templates.render(
            template,
            slots=request.slots,
            constants=steps.CONSTANT_SLOTS,
            row=prompt_templates.RowSlots(step_id=step.id, seat=step.seat),
            wave=wave,
            directory=self.prompt_templates_dir,
        )
        _log.info(
            "call composed",
            extra={
                "context": {
                    "step": step.id,
                    "unit": "WAVE" if wave else "SINGLE",
                    "seat": step.seat or "(seatless)",
                    "members": request.members,
                    "writer": step.writer.value,
                }
            },
        )

        attempts = 0
        complaint = ""
        for re_ask in (False, True):
            label = f"{step.id}{REASK_SUFFIX}" if re_ask else step.id
            text = _reask_brief(brief_text, complaint) if re_ask else brief_text
            brief_path = prompt_templates.persist(self.paths.briefs, seq=request.seq, step_id=label, text=text)

            ask = self._ask(request, brief_path=brief_path, label=label, wave=wave)
            attempts += ask.attempts
            if not ask.result.ok:
                return self._transport_failure(request, ask=ask, attempts=attempts)

            try:
                return self._accept(request, result=ask.result, attempts=attempts)
            except ParseError as exc:
                complaint = str(exc)
                _log.warning(
                    "the return did not satisfy the step's output contract",
                    extra={
                        "context": {
                            "step": step.id,
                            "complaint": complaint,
                            "stream": str(ask.result.capture_path),
                            "re_ask": re_ask,
                        }
                    },
                )

        _log.error(
            "contract failure twice: the step cannot produce its declared output shape",
            extra={"context": {"step": step.id, "complaint": complaint}},
        )
        return CallOutcome(
            exit_code=baton.EXIT_CONTRACT,
            attempts=attempts,
            detail=(f"{step.id}: the declared output shape was not produced, twice", *complaint.splitlines()),
        )

    # --- boundary checks -------------------------------------------------------

    def _check(self, request: CallRequest) -> tuple[str, bool]:
        """The call boundary. Returns the template to compose and whether this is a WAVE."""
        step = request.step
        template = step.template or ""
        runlog.require(step.unit in CALL_UNITS, "this step makes no call", step=step.id, unit=step.unit.value)
        runlog.require(template, "a call step names no template", step=step.id)
        runlog.require(
            step.writer in CALL_WRITERS,
            "a call step's writer is not one of the three persistence routes",
            step=step.id,
            writer=step.writer.value,
        )
        runlog.require(
            all(path.is_absolute() for path in request.outputs),
            "declared artifacts must be absolute paths",
            step=step.id,
            outputs=", ".join(str(path) for path in request.outputs),
        )
        bad_paths = _path_complaints(request.slots)
        runlog.require(
            not bad_paths,
            f"this call's brief would state a path no seat can act on: {'; '.join(bad_paths)}",
            step=step.id,
        )

        parses = set(step.parses)
        wave = is_wave(step, members=request.members)
        runlog.require(wave or step.seat, "a SINGLE names the seat it calls", step=step.id)
        if step.writer is steps.Writer.NONE:
            # A row taking no persistence route leaves nothing behind, so an
            # artifact declared for one would be an artifact nobody was asked to
            # write. No row in the table takes this branch today; it is the
            # table's own consistency rule rather than a live case.
            runlog.require(
                not request.outputs,
                "a call step that writes nothing must declare no artifact",
                step=step.id,
                outputs=", ".join(str(path) for path in request.outputs),
            )
        if step.writer is steps.Writer.DRIVER:
            # The never-writer route persists the returned text, so there is
            # exactly one thing it can be persisted as.
            runlog.require(
                len(request.outputs) == 1,
                "the driver-persists route needs exactly one declared artifact",
                step=step.id,
                outputs=len(request.outputs),
            )
        if step.writer is steps.Writer.WORKER and DOCUMENT_PARSES & parses:
            # The write-to-disk route reads its declared blocks off the file the
            # seat wrote, so there has to be exactly one file to read them from.
            runlog.require(
                len(request.outputs) == 1,
                "a worker-writes row declaring a document parse needs exactly one declared artifact",
                step=step.id,
                outputs=len(request.outputs),
            )
        return template, wave

    # --- transport, with the retry policy ------------------------------------

    def _bounds(self, step: steps.Step, *, wave: bool) -> Bounds:
        """The two per-call bounds: the silence watchdog, and a total that a step may override."""
        timeouts = self.config.timeouts
        default = timeouts.wave_seconds if wave else timeouts.single_seconds
        return Bounds(
            silence_seconds=timeouts.silence_seconds,
            total_seconds=timeouts.by_step.get(step.id, default),
        )

    def _backoff(self, attempt: int) -> float:
        """The pause before attempt ``attempt + 1``; the last configured value repeats."""
        pauses: Sequence[int] = self.config.retry.backoff_seconds
        return float(pauses[min(attempt - 1, len(pauses) - 1)]) if pauses else 0.0

    def _check_call(self, *, brief_path: Path, stream_path: Path, bounds: Bounds, step: steps.Step) -> None:
        """The spawn boundary: what must hold before a call is worth making, as exit 15.

        A non-positive bound reaches here from ``[timeouts.by_step]`` alone —
        every other duration is refused at load — which is the one of these four
        a config file can produce rather than a defect in this driver.
        """
        runlog.require(self.repo_root.is_dir(), "the call's cwd does not exist", cwd=str(self.repo_root))
        runlog.require(brief_path.is_file(), "the composed brief is not on disk", brief=str(brief_path))
        runlog.require(brief_path.stat().st_size > 0, "the composed brief is empty", brief=str(brief_path))
        runlog.require(stream_path.parent.is_dir(), "the capture directory does not exist", stream=str(stream_path))
        runlog.require(
            bounds.silence_seconds > 0 and bounds.total_seconds > 0,
            "call bounds must be positive",
            step=step.id,
            silence_seconds=bounds.silence_seconds,
            total_seconds=bounds.total_seconds,
        )

    def _ask(self, request: CallRequest, *, brief_path: Path, label: str, wave: bool) -> _Ask:
        """One ask: invocations up to the attempt budget, stopping at the first that stands."""
        step = request.step
        _require_no_model(self.config.claude.command, command=" ".join(self.config.claude.command))
        argv = inference.build_argv(
            command=self.config.claude.command,
            permission_mode=self.config.run.permission_mode,
            agent=None if wave else step.seat,
        )
        bounds = self._bounds(step, wave=wave)
        budget = self.config.retry.transport_attempts

        for attempt in range(1, budget + 1):
            stream_path = runlog.call_stream_path(self.paths, seq=request.seq, label=label, attempt=attempt)
            self._check_call(brief_path=brief_path, stream_path=stream_path, bounds=bounds, step=step)
            with _single_flight():
                result = inference.invoke(
                    invoker=self.invoker,
                    argv=argv,
                    cwd=self.repo_root,
                    prompt_path=brief_path,
                    capture_path=stream_path,
                    silence_seconds=bounds.silence_seconds,
                    total_seconds=bounds.total_seconds,
                    env=self.config.claude.env,
                )
            # A CLI rejection is not retried: the classifier ran first precisely
            # so that a bad flag is not three identical failures and an exit 12.
            if result.ok or not result.outcome.retryable or attempt == budget:
                return _Ask(result=result, attempts=attempt)
            pause = self._backoff(attempt)
            _log.warning(
                "the call died in transport; retrying",
                extra={
                    "context": {
                        "step": step.id,
                        "outcome": result.outcome.value,
                        "attempt": attempt,
                        "of": budget,
                        "backoff_seconds": pause,
                    }
                },
            )
            self.sleep(pause)

        raise runlog.BoundaryError(f"[retry] transport_attempts must be positive, got {budget}")

    def _transport_failure(self, request: CallRequest, *, ask: _Ask, attempts: int) -> CallOutcome:
        """A call that never returned a usable stream: 14 if it never started, 13 if refused, else 12."""
        result = ask.result
        stderr = tuple(line for line in result.stderr.strip().splitlines() if line.strip())
        if result.outcome is inference.Outcome.SPAWN_FAILURE:
            # The command is not runnable, which is a fault in the environment
            # the run was launched in and not in the driver. Exit 14 is where
            # `ledger._run` already puts the same fault when a runner or a tool
            # cannot be spawned, and its card is the one that fits: relay the
            # `restore:` line and re-run after it.
            command = self.config.claude.command[0]
            _log.error(
                "the call could not be spawned; not retried",
                extra={"context": {"step": request.step.id, "command": command}},
            )
            return CallOutcome(
                exit_code=baton.EXIT_ENVIRONMENT,
                attempts=attempts,
                detail=(
                    f"{request.step.id}: could not spawn {command} — restore: install it, or point "
                    "[claude] command at where it is, and re-run",
                    *stderr,
                ),
            )

        if result.outcome is inference.Outcome.CLI_REJECTION:
            _log.error(
                "the CLI refused the invocation; not retried",
                extra={"context": {"step": request.step.id, "exit_status": result.exit_status}},
            )
            return CallOutcome(
                exit_code=baton.EXIT_CONFIG,
                attempts=attempts,
                detail=(f"{request.step.id}: the CLI rejected the invocation before making a call", *stderr),
            )

        _log.error(
            "transport exhausted",
            extra={
                "context": {
                    "step": request.step.id,
                    "outcome": result.outcome.value,
                    "attempts": ask.attempts,
                    "stream": str(result.capture_path),
                }
            },
        )
        return CallOutcome(
            exit_code=baton.EXIT_TRANSPORT,
            attempts=attempts,
            detail=(
                f"{request.step.id}: {result.outcome.value} after {ask.attempts} attempt(s)",
                f"last capture: {result.capture_path}",
                *stderr,
            ),
        )

    # --- contract validation and the three persistence routes ----------------

    def _accept(self, request: CallRequest, *, result: inference.CallResult, attempts: int) -> CallOutcome:
        """Validate the return, persist it where the route says, and check the artifacts.

        Raises :class:`ParseError` — the one re-ask's trigger — for every way a
        return can fail its contract. **Every parse that reads the return runs
        before any write**, so a malformed return never overwrites the artifact
        a resume would read. The document parses run after, because what they
        read is a file this module did not write and the seat did.
        """
        step = request.step
        if result.init_count > 1:
            # One call is one turn for this driver, and the layer below promises
            # no such thing — so the count is its answer and the violation is
            # this module's reading of it.
            _log.error(
                "more than one init event in one call: the session dispatched asynchronously",
                extra={"context": {"stream": str(result.capture_path), "inits": result.init_count}},
            )
            raise ParseError(
                f"{result.init_count} init events in one call: the session dispatched asynchronously and "
                f"answered before its members did (every member call needs run_in_background: false); "
                f"capture: {result.capture_path}"
            )

        text = result.result_text
        parses = set(step.parses)
        wave_envelope = parse_envelope(text, step=step.id) if steps.Parse.ENVELOPE in parses else None
        verdict = parse_verdict(text) if steps.Parse.VERDICT in parses else None

        written = (self._persist(request, text=text),) if step.writer is steps.Writer.DRIVER else ()
        self._check_artifacts(request)

        # The document parses read the artifact on the write-to-disk route and
        # the return everywhere else. On a never-writer row the two are the same
        # bytes, since `_persist` above just wrote the return to that path.
        document = text
        if step.writer is steps.Writer.WORKER and DOCUMENT_PARSES & parses:
            document = request.outputs[0].read_text(encoding="utf-8")
        scope = parse_scope(document) if steps.Parse.SCOPE in parses else None

        _log.info(
            "call met its contract",
            extra={
                "context": {
                    "step": step.id,
                    "attempts": attempts,
                    "artifacts": ", ".join(str(path) for path in request.outputs),
                    "driver_wrote": ", ".join(str(path) for path in written),
                }
            },
        )
        return CallOutcome(
            exit_code=baton.EXIT_OK,
            result_text=text,
            envelope=wave_envelope,
            verdict=verdict,
            scope=scope,
            written=written,
            attempts=attempts,
        )

    def _persist(self, request: CallRequest, *, text: str) -> Path:
        """The driver-persists route: a never-writer's returned text becomes the artifact.

        **The write is a temp and a rename, so the target name never holds a
        partial file.** Every reader of these paths asks presence and
        non-emptiness and nothing else — the contract check below, and the
        resume that skips a step whose artifacts are already there — so bytes a
        dying process left half-written would be read as work that finished. A
        rename is what makes the target either the previous file or the whole
        new one, with no third state for a reader to meet.
        """
        target = request.outputs[0]
        if not text.strip():
            raise ParseError(f"the returned text is empty, and it is the artifact this step declares ({target.name})")
        # The driver's writes are its run directory and the never-writer
        # artifacts under the scratch layout. KB content is the workers' and the
        # runner's, and nothing here may reach it.
        runlog.require(
            _within(target, self.scratch_root),
            "the driver persists only under the scratch layout root",
            step=request.step.id,
            target=str(target),
            scratch_root=str(self.scratch_root),
        )
        write_text_atomic(text if text.endswith("\n") else text + "\n", target)
        _log.debug("driver persisted a never-writer return", extra={"context": {"artifact": str(target)}})
        return target

    def _check_artifacts(self, request: CallRequest) -> None:
        """Existence and non-emptiness of every declared artifact, whoever wrote it."""
        missing = [str(path) for path in request.outputs if not (path.is_file() and path.stat().st_size > 0)]
        if missing:
            raise ParseError(f"declared artifact(s) missing or empty: {', '.join(missing)}")
