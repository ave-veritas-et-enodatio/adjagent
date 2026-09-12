"""The ``--dry-run`` ``Invoker``: a step context to synthetic stream-json.

Ships as an installed module rather than living in ``tests/``: spending no
inference is a first-class flag and the verification ladder's first rung, so an
installed consumer must be able to smoke-test the state machine without
spending inference.

It is a **combinator, not a fixture library**. A scenario is a function from
the call's context to a :class:`Response`, so cap-loop cases — 3a
red-red-green, phase-5 review-fix-re-review — compose in Python with
:func:`sequence` instead of accumulating dozens of static JSON files that break
on every schema change.

The seam sits **below** stream parsing: a replayed call is emitted as
stream-json lines and read back through ``transport.invoke`` — the same
capture, the same watchdog, the same classifier a real call goes through.
Scenarios that end badly end badly the same way too: :func:`stall` blocks
until the watchdog kills it, and :func:`cli_rejection` emits nothing at all so
the zero-event classifier fires for the reason it fires in production.

Content-shaped payloads — a wave envelope, a review VERDICT line — are
composed here from ``envelope.py``'s own markers and record builders, never
from sentinel literals restated locally: the parser is the source of truth
for the shape, and each helper below is round-tripped through it by its own
test. What a scenario returns is still only result text; the driver reads it
back through the parse path a real return takes.

:func:`green_run` is the scenario ``--dry-run`` selects — every calling row of
every stage this table has rows for, each answering the shape its row
declares. It is deliberately not a scenario language.

**The flag replaces the model, not the pipeline**, and that is what shapes the
content below. The ledger ops, the ``kb-refresh`` and ``kb-verify``
subprocesses, and every postcondition run for real against the consuming
fixture — a KB tree the fixture stands up as the pandoc pipeline's hand-off,
not one this driver derives. Content that merely *looked* plausible would
stop the run at ``p3a.gate``, which is the first point where ``kb-verify`` is
satisfiable at all. What the scenarios never do is bypass a gate: nothing
here stubs a ledger op, and a stage recorded under the flag was recorded by
the same tool a real build records with.

Where a call writes, **it writes where its brief told it to**. Every path comes
out of the composed brief — the assignment table's rows, the backticked target
paths — because the driver chose those paths and validates them afterwards, so
a scenario inventing one would be answering a call nobody made.

**Dependency note.** This module is a leaf under ``transport``. Composing the
shapes above additionally requires ``envelope`` (itself a leaf), ``briefs``
for the step-id-from-filename grammar a replayed call's context reads, and
``kb_write.render`` for :func:`stamped_leaf`'s frontmatter block. Each is
imported for the reason ``ledger.py`` states for ``baton``: restating any of
them here would be exactly the drift single-sourcing exists to prevent.
Nothing here imports upward, and nothing outside a dry run imports this.

Stdlib only.
"""

import json
import re
import signal
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

from ..kb_write import render
from . import prompt_templates, runlog
from .envelope import STATUS_OK, VERDICT_PREFIX, Deviation, Envelope, Member, envelope_block

_log = runlog.logger("replay")

DEFAULT_SESSION_ID = "replay-00000000"

# The placeholder an async dispatch returns before its members finish — the
# observed text shape of the premature result.
WAITING_PLACEHOLDER = "I'm waiting for the agent to complete. You'll see the response once it returns."


@dataclass(frozen=True)
class ReplayContext:
    """What a scenario gets to decide on: the call as composed, and which call it is."""

    argv: tuple[str, ...]
    cwd: Path
    brief_path: Path
    call_index: int  # 0-based within one invoker: the cap-loop round selector

    @property
    def step(self) -> str:
        """The step label, from the brief's filename ``briefs/<seq>-<step>.md``."""
        return self.brief_path.stem

    @property
    def step_id(self) -> str:
        """The row this call is for, read back through the filename grammar that wrote it.

        Not a substring search over :attr:`step`: a step id contains hyphens and
        so does the re-ask marker, so one row's id can match inside another's
        and a scenario map would answer the wrong row.
        """
        return prompt_templates.step_of(self.brief_path)

    @property
    def agent(self) -> str | None:
        """The seat this call names, or None for a seatless WAVE."""
        argv = self.argv
        for index, token in enumerate(argv[:-1]):
            if token == "--agent":
                return argv[index + 1]
        return None

    def brief_text(self) -> str:
        """The composed brief, for a scenario that wants to answer what it was asked."""
        return self.brief_path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class Response:
    """What a scenario produces: lines to emit, how the process ends, and whether it wedges."""

    lines: tuple[str, ...] = ()
    exit_status: int = 0
    stderr: str = ""
    stall: bool = False  # after the lines, say nothing until killed


Scenario = Callable[[ReplayContext], Response]


# --- events -----------------------------------------------------------------


def init_event(*, session_id: str = DEFAULT_SESSION_ID, model: str = "replay", tools: Sequence[str] = ("Task",)) -> str:
    """A ``system``/``init`` event. ``tools`` spells the dispatch tool ``Task``; the wire
    name in a ``tool_use`` is ``Agent`` — the two do not agree, and no parse may assume they do."""
    return json.dumps(
        {"type": "system", "subtype": "init", "session_id": session_id, "model": model, "tools": list(tools)}
    )


def assistant_event(text: str, *, session_id: str = DEFAULT_SESSION_ID) -> str:
    """An ``assistant`` message event carrying one text block."""
    return json.dumps(
        {
            "type": "assistant",
            "session_id": session_id,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def result_event(
    text: str,
    *,
    subtype: str = "success",
    session_id: str = DEFAULT_SESSION_ID,
    num_turns: int = 2,
    duration_ms: int = 1000,
    total_cost_usd: float = 0.0,
) -> str:
    """A ``result`` event. Carries the cost and duration fields cadence extraction reads."""
    return json.dumps(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": subtype != "success",
            "result": text,
            "session_id": session_id,
            "num_turns": num_turns,
            "duration_ms": duration_ms,
            "total_cost_usd": total_cost_usd,
        }
    )


# --- the shapes a return carries --------------------------------------------
#
# Composed from `envelope.py`'s markers and grammars, so a change to a format
# breaks these helpers at import or in their round-trip tests rather than
# producing a replayed run that is green against a contract nothing else honors.

SYNTHETIC_NOTE = "Synthetic content: --dry-run replaces the model, not the pipeline."


def verdict(*, critical: int = 0, warning: int = 0, note: int = 0) -> str:
    """A reviewer return whose final line is the VERDICT the driver counts.

    ``critical`` above zero is what drives a capped series: composed with
    :func:`sequence` it scripts red-then-green, and held constant it runs a
    series to its cap and its escalation.
    """
    return "\n".join(
        [
            "# Findings",
            "",
            SYNTHETIC_NOTE,
            "",
            f"{VERDICT_PREFIX} critical={critical} warning={warning} note={note}",
        ]
    )


def envelope(
    *,
    step: str,
    members: Sequence[str],
    gaps: Sequence[str] = (),
    deviations: Sequence[Mapping[str, str]] = (),
) -> str:
    """A WAVE session's return: a line of prose and the one sentinel block.

    ``step`` is echoed back as the envelope's own ``step``, which is what makes
    a returned envelope answerable to the call that asked for it. A member
    carries a name and a status and no values: what a wave produces is the
    artifacts it left at the paths the driver named. The one
    field a calling row can add, and a row that did not ask for it is handed an
    envelope without it — which is what exercises the parser's refusal of a
    field nobody declared.

    The block itself is :func:`envelope_block`'s, so a synthetic return is
    composed by the module that parses it rather than typed here: a replay whose
    format has drifted from the parser's is a green run that proves nothing.
    """
    return "\n".join(
        [
            "Wave complete.",
            "",
            envelope_block(
                Envelope(
                    step=step,
                    members=tuple(Member(name=name, status=STATUS_OK) for name in members),
                    gaps=tuple(gaps),
                    deviations=tuple(Deviation(**dict(deviation)) for deviation in deviations),
                )
            ).rstrip("\n"),
        ]
    )


# A table's body is found by its delimiter row, so the header — whose wording
# belongs to the run loop that writes it and would be a second definition if
# restated here — is recognized structurally as "the row above the delimiter"
# rather than by what it says. Every assignment table shares this reader and no
# two spell their headers alike.
_DELIMITER_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _cells(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return None
    return tuple(cell.strip() for cell in stripped.strip("|").split("|"))


def tables(brief_text: str) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Every Markdown table body in ``brief_text``, as rows of cells.

    Header and delimiter rows are dropped: a body row is one that follows a
    delimiter inside the same run of pipe lines.
    """
    found: list[tuple[tuple[str, ...], ...]] = []
    body: list[tuple[str, ...]] = []
    in_body = False
    for line in brief_text.splitlines():
        cells = _cells(line)
        if cells is None:
            if in_body:
                found.append(tuple(body))
            body, in_body = [], False
            continue
        if all(_DELIMITER_CELL_RE.match(cell) for cell in cells):
            body, in_body = [], True
            continue
        if in_body:
            body.append(cells)
    if in_body:
        found.append(tuple(body))
    return tuple(found)


def assignment_rows(brief_text: str) -> tuple[tuple[str, ...], ...]:
    """The one assignment table's body rows — the call's own read of what it was given.

    A brief carries exactly one table, and it is the table the run loop
    composed. Two would mean a scenario is choosing between them, which is a
    choice no member makes.
    """
    found = tables(brief_text)
    if not found:
        return ()
    runlog.require(len(found) == 1, "a brief carries exactly one assignment table", tables=len(found))
    return found[0]


# A path as a brief spells one: backticked, repo-relative, with an extension.
# The leading segment may itself begin with a dot — the whole scratch layout
# lives under `.claude-temp/`. `{kb_root}` is backticked too and carries no
# extension, which is what keeps it out of a path list.
_BRIEF_PATH_RE = re.compile(r"`([\w.][\w./-]*\.\w+)`")


def brief_paths(brief_text: str, *, named: str = "") -> tuple[str, ...]:
    """Every backticked path the brief names, or only those with basename ``named``.

    Where a call writes, the driver named the path and validates it afterwards
    — so the brief is the only place a member may learn it, and a scenario that
    composed one itself would be writing where nothing looks.
    """
    found = []
    for path in _BRIEF_PATH_RE.findall(brief_text):
        if path not in found and (not named or PurePosixPath(path).name == named):
            found.append(path)
    return tuple(found)


def brief_path(brief_text: str, *, named: str) -> str:
    """The one backticked path in the brief with basename ``named``."""
    found = brief_paths(brief_text, named=named)
    runlog.require(len(found) == 1, "the brief names this path exactly once", named=named, found=len(found))
    return found[0]


# --- scenarios --------------------------------------------------------------


def clean(result_text: str = "ok", *, session_id: str = DEFAULT_SESSION_ID) -> Scenario:
    """One ``init``, one answer, one ``result``, exit 0 — a call that behaved."""

    def scenario(context: ReplayContext) -> Response:
        del context
        return Response(
            lines=(
                init_event(session_id=session_id),
                assistant_event(result_text, session_id=session_id),
                result_event(result_text, session_id=session_id),
            )
        )

    return scenario


def premature_dispatch(
    result_text: str = "ok",
    *,
    placeholder: str = WAITING_PLACEHOLDER,
    session_id: str = DEFAULT_SESSION_ID,
) -> Scenario:
    """The async-dispatch shape: a premature ``result``, then a second ``init``/``result``.

    Exit 0 throughout — this is what a wave that forgot ``run_in_background:
    false`` looks like, and reading only the first ``result`` would record it
    as a completed wave with no member artifacts.
    """

    def scenario(context: ReplayContext) -> Response:
        del context
        return Response(
            lines=(
                init_event(session_id=session_id),
                assistant_event(placeholder, session_id=session_id),
                result_event(placeholder, session_id=session_id, num_turns=3),
                init_event(session_id=session_id),
                assistant_event(result_text, session_id=session_id),
                result_event(result_text, session_id=session_id),
            )
        )

    return scenario


def stall(*, lines: Sequence[str] | None = None) -> Scenario:
    """Emit an ``init`` (or the given lines) and then go silent until killed.

    The wedge the silence watchdog exists for: the process is alive, its pipe
    is open, and nothing is ever coming.
    """
    prefix = tuple(lines) if lines is not None else (init_event(),)

    def scenario(context: ReplayContext) -> Response:
        del context
        return Response(lines=prefix, stall=True)

    return scenario


def cli_rejection(
    stderr: str = "error: unknown option '--frobnicate-widget'\n",
    *,
    exit_status: int = 1,
) -> Scenario:
    """Nonzero exit with **zero** stream events: the CLI refused the argv."""

    def scenario(context: ReplayContext) -> Response:
        del context
        return Response(lines=(), exit_status=exit_status, stderr=stderr)

    return scenario


def transport_die(
    *,
    exit_status: int = 1,
    stderr: str = "",
    lines: Sequence[str] | None = None,
) -> Scenario:
    """Speak, then die nonzero: a mid-stream failure, which is retry territory."""
    prefix = tuple(lines) if lines is not None else (init_event(),)

    def scenario(context: ReplayContext) -> Response:
        del context
        return Response(lines=prefix, exit_status=exit_status, stderr=stderr)

    return scenario


def sequence(*scenarios: Scenario) -> Scenario:
    """Answer differently on successive calls; the last one repeats.

    This is how a cap loop is scripted: ``sequence(red, red, green)`` is a
    stage that goes green on its third round, with no state outside the
    invoker.
    """
    runlog.require(bool(scenarios), "sequence() needs at least one scenario")

    def scenario(context: ReplayContext) -> Response:
        chosen = scenarios[min(context.call_index, len(scenarios) - 1)]
        return chosen(context)

    return scenario


def by_step(scenarios: Mapping[str, Scenario]) -> Scenario:
    """Answer per step id, read off the brief's filename.

    Matched through :attr:`ReplayContext.step_id` — the filename grammar's own
    inverse — rather than as a substring of the stem: one step id can be a
    prefix of another's, and answering the wrong row's shape to a call is a
    very quiet way to be wrong. A step id no key names is a boundary
    violation rather than a silent pick: an unscripted call answered by a
    default is how a replayed run reports a step it never exercised as green.
    """

    def scenario(context: ReplayContext) -> Response:
        step_id = context.step_id
        runlog.require(
            step_id in scenarios,
            "a replayed call names a step no scenario answers",
            step=step_id,
            scripted=", ".join(scenarios),
        )
        return scenarios[step_id](context)

    return scenario


# --- the calling rows, one scenario each -------------------------------------


def write_assigned(context: ReplayContext, relative: str, text: str) -> Path:
    """Write one file the brief assigned, under the call's own repo root.

    The containment check is the same one :func:`worker_writes` makes and for
    the same reason: every path here came out of a brief, and a brief that
    named somewhere outside the repository is a defect to stop on rather than
    to act on.
    """
    target = context.cwd / relative
    runlog.require(
        target.resolve().is_relative_to(context.cwd.resolve()),
        "an assigned path leaves the repo root",
        step=context.step_id,
        path=relative,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return target


def stamped_leaf(document: str, *, claims: Sequence[str]) -> str:
    """One rendered leaf with its metadata block stamped in, and its body untouched.

    Not wired into :data:`SCENARIOS` — no calling row renders a leaf's body any
    more, that being the pandoc pipeline's job — but kept as the one composer
    of a realistic distilled leaf, which other suites stand a KB tree up with.
    The block itself is ``render``'s — the one composer of the shape the write
    op a real member calls already owns.
    """
    values = render.FrontmatterValues(kind="leaf", claims=tuple(claims))
    head, _, body = document.partition("\n")
    return "\n".join([head, "", render.render_frontmatter_block(values), body])


def meta_docs() -> Scenario:
    """``p5.docs`` / ``p5.fix``: the one prose answer the stage asks for.

    It writes no document, because neither does the seat it stands in for: the
    stage assembles ``kb_pipeline.OVERVIEW_DOC`` from this answer and the counts
    it reads out of the KB, and a return that is only prose has no structure a
    replay could get right or wrong.
    """
    return clean(f"This corpus is synthetic and has no orientation to give.\n\n{SYNTHETIC_NOTE}")


#: Every calling row of the step table and the shape each answers. Named rather
#: than inlined below so a test can hold it against the table: a calling row
#: that lands with no scenario here is a replayed run that stops at :func:`by_step`'s
#: boundary check, and that belongs in the suite rather than in a run.
SCENARIOS: Mapping[str, Scenario] = MappingProxyType(
    {
        "p5.docs": meta_docs(),
        "p5.review": clean(verdict()),
        "p5.fix": meta_docs(),
    }
)


def green_run() -> Scenario:
    """Every calling row of every stage, answering the shape its row declares.

    Green throughout: the distilled corpus passes ``kb-verify`` at
    ``p3a.gate`` — the tree it is run against is not this driver's own output,
    but a fixture standing in for the pandoc pipeline's hand-off — and every
    review returns no findings, so a run reaches ``phase-5`` with the
    registry's barriers as its only stops. The red shapes compose from the same
    helpers (``verdict(critical=…)``, :func:`sequence`, :func:`stall`), which
    is what a combinator buys over a fixture library.
    """
    return by_step(SCENARIOS)


# --- the seam ---------------------------------------------------------------


def dry_run_invoker() -> "ReplayInvoker":
    """The ``Invoker`` ``--dry-run`` selects.

    Named here rather than assembled at the call site so that ``cli.py`` keeps
    no stage knowledge: it chooses between the real transport and this one, and
    which steps exist stays a fact of the step table and this module.
    """
    return ReplayInvoker(green_run())


class ReplayInvoker:
    """``transport.Invoker`` that runs a scenario instead of a process (``--dry-run``)."""

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._calls = 0

    @property
    def calls(self) -> int:
        """How many calls this invoker has served — the cap-loop round count, observable."""
        return self._calls

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        brief_path: Path,
    ) -> "_ReplayInvocation":
        del env  # a synthetic call has no environment to overlay
        # A replayed run still composes and persists a real brief; a scenario
        # that cannot see one is being handed a call that was never composed.
        runlog.require(
            brief_path.is_file(), "a replayed call still requires the composed brief on disk", brief=str(brief_path)
        )

        context = ReplayContext(argv=tuple(argv), cwd=cwd, brief_path=brief_path, call_index=self._calls)
        self._calls += 1
        response = self._scenario(context)
        _log.debug(
            "replaying a synthetic call",
            extra={
                "context": {
                    "step": context.step,
                    "call_index": context.call_index,
                    "lines": len(response.lines),
                    "exit_status": response.exit_status,
                    "stall": response.stall,
                }
            },
        )
        return _ReplayInvocation(response)


class _ReplayInvocation:
    """A scenario's response, presented as a live call."""

    def __init__(self, response: Response) -> None:
        self._response = response
        self._killed = threading.Event()
        self._status = response.exit_status

    def lines(self) -> Iterator[str]:
        for line in self._response.lines:
            if self._killed.is_set():
                return
            yield line if line.endswith("\n") else line + "\n"
        if self._response.stall:
            self._killed.wait()

    def kill(self) -> None:
        self._killed.set()
        # What a process group killed by the watchdog reports back.
        self._status = -signal.SIGKILL

    def wait(self) -> int:
        return self._status

    def stderr_text(self) -> str:
        return self._response.stderr
