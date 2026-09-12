"""Subprocess adapter over the sanctioned `kb_tools` ops, front ends and runner targets.

The ledger is the git commit trail, and it is written only by ``kb_util
start-build`` / ``advance-step``, invoked as a subprocess. Nothing
here imports `kb_pipeline` internals and nothing here runs git. The tool's
**complete stdout is relayed verbatim** through :func:`runlog.relay`: this
module contains no checklist formatting and never trims a render, including on
the failure paths, where the refusal render carries the card that is the fix.

What the adapter adds is the rc→exit mapping, so that a tool exit code becomes
a driver exit code in exactly one place:

| Op | tool rc | driver exit |
|---|---|---|
| ``preflight`` | 0 · 1 · 2 | 0 · 14 · 14 |
| ``graph-init`` | 0 · 1 · 2 · 3 | 0 · 11 · 14 · 14 |
| ``start-build`` | 0 · 5 · 4 · 6 · 2 | 0 · 0 (already started reads as done) · 14 · 17 · 14 |
| ``advance-step`` | 0 · 4 · 6 · 2 | 0 · 14 · 17 · 14 |
| ``show-status`` | 0 · 2 | 0 · 14 |
| ``kb_docgraph`` | 0 · 1 · 2 | 0 · 11 · 14 |
| ``kb_claimgraph`` | 0 · 1 · 2 · 3 | 0 · 11 · 14 · 14 |
| ``validate-build`` | 0 · 1 · 2 | 0 · 11 · 14 |
| write op | 0 · 2 · 7 · 8 | 0 · 14 · 15 · **retry**, then 14 |
| runner target | 0 · other | 0 · 11 |

An rc outside its op's vocabulary is a driver/tool contract violation, not a
pipeline outcome: it routes through :func:`runlog.require` and exits 15.
That rule is why the write ops' rc 8 has a row at all: enrolling it is what
keeps "the file was contended" from reading as "the tool is broken".

**Design note — the `postcondition` row.** `kb_pipeline` exit 6 (a stage's
declared artifact is missing at record time) has no row in the driver's exit
vocabulary. It is mapped
to 17 (contract-failure: "a step could not produce its declared output shape"),
which is the closest listed meaning; 17's baton text names a brief/worker
mismatch, which is right for a worker-written artifact and only approximately
right for the charter. Flagged rather than harmonized.

**Dependency note.** ``ledger`` depends on ``runlog``. Naming an exit code
additionally requires ``baton``, the stateless exit-code vocabulary; copying
the constants here instead would be exactly the drift the single-source rule
exists to prevent. :func:`write_op`'s vocabulary is keyed by
``kb_write.ops.ExitCode`` for the same reason — that class is where the write
API's three outcomes are defined, and a driver-side copy of the numbers would
be a second definition of the contract this module exists to honour. The
direction is the legal one: ``kb_driver`` may import ``kb_write``, and
``kb_write`` imports no driver module.

Stdlib only.
"""

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import kb_util
from ..kb_write import ops as write_ops
from . import baton, runlog

_log = runlog.logger("ledger")

# The directory holding the ``kb_tools`` package, for the child's PYTHONPATH.
# ``__file__`` is the right anchor here and only here: this locates the
# *toolchain*, which lives wherever it was installed. Repo and KB paths stay
# cwd-anchored (see ``kb_util``'s module docstring).
_PKG_PARENT = Path(__file__).resolve().parents[2]

# The running interpreter, not a bare ``python3``: the tool and the driver must
# be the same 3.11+ runtime, and PATH resolution could disagree.
_KB_UTIL = (sys.executable, "-m", "kb_tools.kb_util")

_PREFLIGHT_EXITS = {0: baton.EXIT_OK, 1: baton.EXIT_ENVIRONMENT, 2: baton.EXIT_ENVIRONMENT}
# rc 3 raises no barrier. It says kb-root holds no document tree, and the one
# corrective act is to run the document-graph front end over the sources — an
# upstream build step, not a question a run could put to anybody.
_GRAPH_INIT_EXITS = {
    0: baton.EXIT_OK,
    1: baton.EXIT_GATE_RED,  # refresh or verify failed
    2: baton.EXIT_ENVIRONMENT,  # preflight blocked the seed, or no root/runner
    3: baton.EXIT_ENVIRONMENT,  # kb-root holds no document tree to initialise over
}
_START_BUILD_EXITS = {
    0: baton.EXIT_OK,
    2: baton.EXIT_ENVIRONMENT,
    4: baton.EXIT_ENVIRONMENT,
    5: baton.EXIT_OK,  # already started: the boundary exists, which is what was wanted
    6: baton.EXIT_CONTRACT,
}
_ADVANCE_STEP_EXITS = {
    0: baton.EXIT_OK,
    2: baton.EXIT_ENVIRONMENT,
    4: baton.EXIT_ENVIRONMENT,  # refused out of order
    6: baton.EXIT_CONTRACT,
}
_SHOW_STATUS_EXITS = {0: baton.EXIT_OK, 2: baton.EXIT_ENVIRONMENT}
# The two build front ends. rc 1 is a red gate in both — a check the tool ran
# came back failed — and rc 2 is an unusable invocation (a source that is not
# there, no repository root), which is an environment fault. `kb_claimgraph`'s
# rc 3 is the same kind: the spine is not seeded, which is the stage before it.
#
# Neither maps rc 1 to a fix cycle. There is no distiller wave in the head and
# no seat to run one: every check either tool makes compares one mechanical
# product against another, so a red one is a defect in the tool or its input.
_DOCGRAPH_EXITS = {0: baton.EXIT_OK, 1: baton.EXIT_GATE_RED, 2: baton.EXIT_ENVIRONMENT}
_CLAIMGRAPH_EXITS = {
    0: baton.EXIT_OK,
    1: baton.EXIT_GATE_RED,
    2: baton.EXIT_ENVIRONMENT,
    3: baton.EXIT_ENVIRONMENT,
}
# The validator's three rungs, which keep "the validator could not run" from
# reading as "the design is broken": rc 1 is a FAILing check (exit 11, a finding
# a revision round can close), rc 2 is an unreadable input (exit 14).
_VALIDATE_EXITS = {0: baton.EXIT_OK, 1: baton.EXIT_GATE_RED, 2: baton.EXIT_ENVIRONMENT}

#: How many times a contended write op is re-run before its rc is mapped. The
#: bound is this program's to pick, and three is the
#: number the agent-side contract in ``prompt-templates/fragments/write-op-contract.tmpl`` states
#: for the other caller. The two bounds are deliberately not single-sourced:
#: they govern different retriers — an agent re-running its own Bash call, and
#: the driver re-running its own subprocess — and each has to be legible where
#: it is stated.
WRITE_OP_RETRY_LIMIT = 3

#: How many of a failing op's own report lines ride the :class:`Outcome`'s
#: detail — which is what the relay card puts under its ASK, and what an
#: operator pastes into a message body. The whole report reaches the run log
#: either way (:func:`_front_end`, :func:`run_target`), so this bounds the
#: paste rather than the evidence: a 500-line traceback must not bury the card
#: under it.
FAILURE_DETAIL_LINES = 40

_ELIDED = "… {count} line(s) of the report omitted here — the whole of it is in the run log …"

# The write ops' rc vocabulary. ``ExitCode`` is
# the tool-side definition; what this table adds is the driver exit each of its
# four codes earns.
#
# **8 is enrolled and is not a driver exit.** It means the values were right
# and a concurrent writer moved the file, so the answer is the identical
# invocation again — never a re-ask of the model, because re-asking for values
# that were already correct is how a second id gets minted for one thing.
# ``_outcome``'s ``retry_rc`` runs it; only an 8 that outlives the bound
# reaches this table, and it lands on 14 with a ``restore:`` line, because a
# file still contended after four attempts is a fault in the environment the
# run shares rather than in anything the driver composed.
#
# **7 is 15, not 11.** A refusal says the values are wrong, and a
# driver-invoked op's values were composed by the driver — no model wrote them,
# so no fix wave can address them and no re-ask can help. That is a driver
# defect, which is exit 15's meaning. A future row whose values come out of a
# worker's envelope rather than out of the driver's own construction has a
# different answer to that rc, and changing it is a change to this table.
_WRITE_OP_EXITS = {
    write_ops.ExitCode.WRITTEN: baton.EXIT_OK,
    write_ops.ExitCode.ENVIRONMENT: baton.EXIT_ENVIRONMENT,
    write_ops.ExitCode.REFUSED: baton.EXIT_INTERNAL,
    write_ops.ExitCode.RETRY: baton.EXIT_ENVIRONMENT,
}

# The two maintenance targets, and `kb_util`'s hint function for each — the
# single source of runner detection, reused rather than reimplemented.
_TARGET_HINTS = {
    kb_util.TARGET_REFRESH: kb_util.refresh_cmd,
    kb_util.TARGET_VERIFY: kb_util.verify_cmd,
}


@dataclass(frozen=True)
class Outcome:
    """One row's result: the driver exit that holds, and the evidence for it.

    ``exit_code`` is :data:`baton.EXIT_OK` when the row passed. When ``barrier``
    names a ``<stage>.<kind>`` pair, ``exit_code`` is the code that holds if
    that barrier goes unanswered — deciding it belongs to ``barriers.py``.
    """

    exit_code: int
    stdout: str = ""
    detail: tuple[str, ...] = ()
    barrier: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == baton.EXIT_OK


def _child_env() -> dict[str, str]:
    """The child's environment: `kb_tools` importable, no bytecode in a deployed tree."""
    inherited = os.environ.get("PYTHONPATH", "")
    parts = [str(_PKG_PARENT), *(part for part in inherited.split(os.pathsep) if part)]
    return {**os.environ, "PYTHONPATH": os.pathsep.join(parts), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(argv: Sequence[str], *, repo_root: Path, relay: bool) -> subprocess.CompletedProcess[str] | None:
    """Run ``argv`` at ``repo_root``; ``None`` when it could not be spawned at all.

    Crossing to an external process, as ``kb_util.run_git`` does: an unrunnable
    executable is reported as a failed step rather than raised, so a missing
    runner cannot turn a build into a traceback.
    """
    runlog.require(repo_root.is_dir(), "ledger op needs an existing repo root", repo_root=str(repo_root))
    _log.info("ledger op", extra={"context": {"argv": " ".join(argv), "cwd": str(repo_root)}})
    try:
        result = subprocess.run(
            list(argv),
            cwd=repo_root,
            env=_child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            # A runner target is a consuming repo's own recipe: its stdout is
            # whatever the verifiers, and anything they shell out to, happen to
            # print. One non-UTF-8 byte in that stream would otherwise raise
            # UnicodeDecodeError out of `subprocess.run` — past `except OSError`
            # below, past the rc mapping, and out of the driver as a traceback
            # with no baton and no exit.json. Undecodable bytes become U+FFFD
            # and the row keeps its verdict (transport.py does the same).
            errors="replace",
            check=False,
        )
    except OSError as exc:
        _log.error("ledger op could not be spawned", extra={"context": {"argv": " ".join(argv), "error": str(exc)}})
        return None
    if relay and result.stdout:
        # The complete stdout, never trimmed, never re-rendered.
        runlog.relay(result.stdout)
    if result.stderr.strip():
        _log.error("ledger op wrote to stderr", extra={"context": {"stderr": result.stderr.strip()}})
    return result


def _failure_detail(op: str, result: subprocess.CompletedProcess[str]) -> tuple[str, ...]:
    """``<op> exited <rc>``, then the op's own account of what went wrong, bounded.

    **Both streams.** Which one carries the diagnostic is the failing tool's
    choice and not something this adapter can know: a ``kb_claimgraph`` stage
    prints its ``FAIL`` findings on stdout and exits 1, while an unhandled
    exception in ``kb_docgraph`` arrives as a traceback on stderr. Reading
    stderr alone is why a whole sweep of stopped builds relayed ``exited 1`` and
    nothing else — the stage had said exactly what was wrong, on the stream
    nobody read.

    The bound keeps every line carrying the toolchain's ``FAIL`` token, since
    those are the lines that name the failure, and fills what is left of the
    budget from the tail, where an exception's own message sits. What it drops,
    it says it dropped and where the rest is.
    """
    head = f"{op} exited {result.returncode}"
    lines = [*result.stdout.strip().splitlines(), *result.stderr.strip().splitlines()]
    if len(lines) <= FAILURE_DETAIL_LINES:
        return (head, *lines)

    named = [index for index, line in enumerate(lines) if kb_util.FAIL in line][:FAILURE_DETAIL_LINES]
    tail = range(len(lines) - (FAILURE_DETAIL_LINES - len(named)), len(lines))
    detail = [head]
    previous = -1
    for index in sorted({*named, *tail}):
        if index > previous + 1:
            detail.append(_ELIDED.format(count=index - previous - 1))
        detail.append(lines[index])
        previous = index
    if previous < len(lines) - 1:
        detail.append(_ELIDED.format(count=len(lines) - 1 - previous))
    return tuple(detail)


def _outcome(
    argv: Sequence[str],
    *,
    repo_root: Path,
    op: str,
    exits: Mapping[int, int],
    otherwise: int | None = None,
    barriers: Mapping[int, str] | None = None,
    relay: bool = True,
    retry_rc: int | None = None,
) -> Outcome:
    """Run one op and map its rc. ``otherwise`` accepts an open rc vocabulary.

    ``retry_rc`` names the one rc whose answer is to run the **identical**
    ``argv`` again — the write ops' 8, a contended file. It is re-run up
    to :data:`WRITE_OP_RETRY_LIMIT` times before its mapped exit holds. Nothing
    about the invocation changes between attempts and nothing about the model
    is consulted at any point: a retry here is a second subprocess and never a
    second dispatch.
    """
    attempts = 0
    while True:
        attempts += 1
        result = _run(argv, repo_root=repo_root, relay=relay)
        if result is None or result.returncode != retry_rc or attempts > WRITE_OP_RETRY_LIMIT:
            break
    if attempts > 1:
        # Report by exception: one line for the whole retried sequence, naming
        # what was re-run and how many times, so a run that never contended
        # says nothing at all.
        _log.warning(
            "re-ran the identical invocation after a contended write",
            extra={
                "context": {
                    "argv": " ".join(argv),
                    "invocations": attempts,
                    "returncode": None if result is None else result.returncode,
                }
            },
        )
    if result is None:
        return Outcome(
            baton.EXIT_ENVIRONMENT,
            detail=(f"could not run {op}: {argv[0]} is not executable — restore: install it and re-run",),
        )
    if otherwise is None:
        runlog.require(
            result.returncode in exits,
            f"unmapped return code from {op}",
            returncode=result.returncode,
            argv=" ".join(argv),
        )
    fallback = baton.EXIT_INTERNAL if otherwise is None else otherwise
    exit_code = exits.get(result.returncode, fallback)
    detail: tuple[str, ...] = ()
    if exit_code != baton.EXIT_OK:
        detail = _failure_detail(op, result)
    if result.returncode == retry_rc:
        detail = (
            *detail,
            f"{attempts} identical invocations, every one rc {result.returncode} — restore: re-run "
            f"once whatever else is writing that file has finished",
        )
    barrier = "" if barriers is None else barriers.get(result.returncode, "")
    return Outcome(exit_code, stdout=result.stdout, detail=detail, barrier=barrier)


def _kb_util(*args: str) -> tuple[str, ...]:
    return (*_KB_UTIL, *args)


def _front_end(repo_root: Path, *, module: str, flags: Sequence[str], exits: Mapping[int, int]) -> Outcome:
    """Run one build front end as a subprocess and map its rc.

    The report is not relayed on the way through, for ``run_target``'s reason:
    a tool report the driver acts on is evidence, and it is on the returned
    :class:`Outcome` and in the run log either way. A **failing** one is
    relayed, because there it is the whole of what the operator has to read and
    no findings file is written for it — the head has no fix cycle to write one
    for.
    """
    op = f"{module} {' '.join(flags)}".rstrip()
    outcome = _outcome(
        (sys.executable, "-m", f"kb_tools.{module}", *flags),
        repo_root=repo_root,
        op=op,
        exits=exits,
        relay=False,
    )
    if outcome.stdout.strip():
        # The whole report, in the record — as `run_target` already does with a
        # gate's. Without it a *green* front end's report reaches nowhere at
        # all: it is not relayed (below), and a record naming only the op and
        # the code drops the census the run produced. A build whose value is
        # what it counted has to leave the counts somewhere a reader can find.
        _log.info(
            "front-end report",
            extra={"context": {"op": op, "exit_code": outcome.exit_code, "report": outcome.stdout}},
        )
        if not outcome.ok:
            runlog.relay(outcome.stdout)
    return outcome


def document_graph(repo_root: Path, *, sources: Sequence[str], bibliographies: Sequence[str], kb_root: str) -> Outcome:
    """``dg.build``: LaTeX volumes in, the KB's Markdown tree out.

    Every source is passed as its own ``--source``, in the order the run was
    given them: which files are volume roots is the one thing that front end
    will not infer, and the launch line is where the answer is stated. Every
    bibliography is passed the same way, in the order the run resolved them.
    """
    return _front_end(
        repo_root,
        module=kb_util.DOCGRAPH_MODULE,
        flags=kb_util.docgraph_flags(sources=sources, bibliographies=bibliographies, kb_root_path=kb_root),
        exits=_DOCGRAPH_EXITS,
    )


def claim_graph(repo_root: Path, *, flags: Sequence[str]) -> Outcome:
    """The three claim-graph invocations: declared, discovery, and stage D.

    ``flags`` comes from ``kb_util.claimgraph_flags``, so which invocation this
    is stays the calling row's statement and the spelling stays one.
    """
    return _front_end(repo_root, module=kb_util.CLAIMGRAPH_MODULE, flags=flags, exits=_CLAIMGRAPH_EXITS)


# --- the pre-stage rows -------------------------------------------------------


def preflight(repo_root: Path) -> Outcome:
    """``pre.preflight``: the mechanical environment report; any failure is exit 14.

    The report is relayed whole — every ``FAIL`` names its restoring action,
    and exit 14's baton says to relay those lines as printed.
    """
    return _outcome(
        _kb_util(kb_util.OP_PREFLIGHT),
        repo_root=repo_root,
        op=f"kb_util {kb_util.OP_PREFLIGHT}",
        exits=_PREFLIGHT_EXITS,
    )


def graph_init(repo_root: Path, *, runner: str | None = None) -> Outcome:
    """``seed.graph-init`` (fresh builds): initialise the claim-graph spine.

    rc 3 is exit 14 — kb-root holds no document tree, which is a missing
    prerequisite the run cannot supply; rc 2 is exit 14 too; rc 1 (refresh or
    verify red) is exit 11. ``runner`` is needed only for a repo carrying
    neither a justfile nor a Makefile.
    """
    argv = _kb_util(kb_util.OP_GRAPH_INIT, *(("--runner", runner) if runner else ()))
    return _outcome(
        argv,
        repo_root=repo_root,
        op=f"kb_util {kb_util.OP_GRAPH_INIT}",
        exits=_GRAPH_INIT_EXITS,
    )


def revision_entry(repo_root: Path) -> Outcome:
    """``pre.revision-entry`` (revision builds): kb-build.md's revision entry contract.

    The runner targets installed (else exit 14), then ``kb-verify`` green (else
    exit 11). The first is a library read — no ledger is touched — and it is an
    environment fault with a named restore, not a gate failure.

    Revision entry deliberately checks nothing else. A presence check on the
    KB's local format contract — ``<kb-root>/CLAUDE.md`` — would look like a
    proxy for "this spine is sound", but that file is stamped at the validation gate
    rather than at seed time, so such a check would refuse entry to any KB
    whose build halted earlier. ``kb-verify`` below is what actually answers
    whether the spine is sound.
    """
    if not kb_util.targets_installed(repo_root):
        return Outcome(
            baton.EXIT_ENVIRONMENT,
            detail=(
                f"the KB runner targets are not installed at {repo_root} — restore: run "
                f"'python3 -m kb_tools.kb_util {kb_util.OP_INSTALL_TARGETS}' from the repo root "
                f"and commit the change",
            ),
        )
    return run_target(repo_root, target=kb_util.TARGET_VERIFY)


def record_start(repo_root: Path, *, charter: str) -> Outcome:
    """``start.record``: ``start-build``; rc 5 (already started) reads as done.

    ``charter`` is the repo-relative path recorded in the start commit, or
    empty where the build carries none — in which case the flag is not passed
    at all, rather than passed with nothing behind it.
    """
    return _outcome(
        _kb_util(kb_util.OP_START_BUILD, *(("--charter", charter) if charter else ())),
        repo_root=repo_root,
        op=f"kb_util {kb_util.OP_START_BUILD}",
        exits=_START_BUILD_EXITS,
    )


def record_stage(repo_root: Path, *, stage: str, note: str = "", no_inference: bool = False) -> Outcome:
    """A stage record row: ``advance-step --stage``.

    Re-recording a recorded stage is inert and exits 0; a stage whose
    predecessors are unrecorded is refused, which is exit 14.

    ``no_inference`` states what the build was, and the flag is
    ``kb_util``'s own constant rather than a second spelling: the op's coverage
    check is what decides which units a build spending none has nothing left to
    assert, and nothing on this side of the subprocess names a unit.
    """
    runlog.require(bool(stage), f"{kb_util.OP_ADVANCE_STEP} needs a stage id")
    argv = _kb_util(
        kb_util.OP_ADVANCE_STEP,
        "--stage",
        stage,
        *(("--note", note) if note else ()),
        *((kb_util.NO_INFERENCE_FLAG,) if no_inference else ()),
    )
    return _outcome(argv, repo_root=repo_root, op=f"kb_util {kb_util.OP_ADVANCE_STEP}", exits=_ADVANCE_STEP_EXITS)


def show_status(repo_root: Path, *, relay: bool = True) -> Outcome:
    """The display source: the render printed at every transition and barrier.

    Pass ``relay=False`` for a read that is not a transition — resume position,
    watch-mode polling — where printing the render again would be noise rather
    than display.
    """
    return _outcome(
        _kb_util(kb_util.OP_SHOW_STATUS),
        repo_root=repo_root,
        op=f"kb_util {kb_util.OP_SHOW_STATUS}",
        exits=_SHOW_STATUS_EXITS,
        relay=relay,
    )


# --- the write ops ------------------------------------------------------------


def write_op(repo_root: Path, *, op: str, args: Sequence[str] = ()) -> Outcome:
    """Run one ``kb_write`` op as a subprocess, with rc 8 enrolled and retried.

    No current driver row invokes a write op through this function — the one
    that did, ``p3.markers`` (placing a leaf's Tier-2 markers), is deleted with
    the distillation stage it belonged to. The *agent-side* callers remain,
    called from inside a wave session where no driver code sees the rc at all
    (that caller's contract is ``prompt-templates/fragments/write-op-contract.tmpl``). The rc
    vocabulary is declared once, here, for whichever caller reaches it: rc 8 is
    in it (an unenrolled rc exits 15 as a broken tool), and it maps to a
    bounded retry of the identical invocation rather than to a driver exit or
    a re-ask.

    ``args`` is the op's own flags. The report is not relayed, for
    ``run_target``'s reason: a tool report the driver acts on is evidence, and
    it is on the returned :class:`Outcome` and in the run log either way.
    """
    runlog.require(op in kb_util.WRITE_OPS, "not a kb_tools write op", op=op)
    return _outcome(
        _kb_util(op, *args),
        repo_root=repo_root,
        op=f"kb_util {op}",
        exits=_WRITE_OP_EXITS,
        retry_rc=write_ops.ExitCode.RETRY,
        relay=False,
    )


# --- the built-tree validator -------------------------------------------------
#
# A subprocess op like every other: `kb_util` is the one sanctioned front end.
#
# The report is not relayed. The display relay carries what a session pastes
# into its message body; this is evidence, and `run.py` writes the validator's
# report to the round's findings file where the review reads it.

# The validator takes the built tree and nothing else: it walks the tree for
# the documents it checks, so there is no second input to disagree with what
# is on disk. There is no severity argument because there is none in the
# validator: the verdict is the tool's rc, and this maps it.


def validate_build(repo_root: Path, *, kb_root: Path) -> Outcome:
    """Check a built tree's structure — up-links, parent correctness, reachability.

    No current driver row calls this: it fronted ``p3a.validate``, deleted with
    the phase-0/1 stages. Kept because ``kb_util validate-build`` and
    ``kb_survey.validate.validate_build`` are themselves kept per this
    project's docbuild plan (they are stdlib-only, import no parser, and are
    the pandoc replacement's own acceptance checker) — this is their
    driver-side adapter, latent until a caller needs it again.
    """
    argv = _kb_util(kb_util.OP_VALIDATE_BUILD, "--kb-root", str(kb_root))
    return _outcome(
        argv,
        repo_root=repo_root,
        op=f"kb_util {kb_util.OP_VALIDATE_BUILD}",
        exits=_VALIDATE_EXITS,
        relay=False,
    )


# --- the runner targets ------------------------------------------------------


def _target_argv(repo_root: Path, target: str) -> tuple[str, ...] | None:
    """``('just'|'make', target)`` from the detected runner file, or None when there is none.

    Detection is ``kb_util``'s, reused rather than reimplemented: its hint is
    ``"<runner> <target>"`` when a runner file exists at the root, and the raw
    ``python3 -m kb_tools.<module>`` fallback when none does. The fallback names
    one module, not the target's whole gate, so it is not a substitute — a repo
    with no runner file has no target to run.
    """
    hint = _TARGET_HINTS[target](repo_root)
    if not hint.endswith(f" {target}"):
        return None
    argv = tuple(hint.split())
    runlog.require(len(argv) == 2, "unexpected runner hint shape", target=target, hint=hint)
    return argv


def run_target(repo_root: Path, *, target: str) -> Outcome:
    """Run the consuming repo's ``kb-refresh`` / ``kb-verify`` target.

    Nonzero is a red gate (exit 11). A repo with no runner file has no target
    to run at all, which is an environment fault (exit 14) with a named restore.

    Neither does a repo whose runner file never had the KB include line
    installed. That case has to be caught *before* spawning, because the runner
    answers an unknown recipe the same way a verifier answers a broken KB — a
    nonzero exit — and exit 11 says the KB failed a check it was really put
    through. Left unchecked, a repo that never installed the targets would stop
    the build as though its knowledge base were broken.
    ``kb_util.targets_installed`` is the same predicate ``revision_entry``
    already applies at the revision entry contract; running a target is the
    other place the answer decides an exit.

    A gate's report is **not relayed**. The display relay carries what a
    session pastes into its message body, and a non-barrier terminal baton says
    to place "everything above this block" there — which, with every gate dump
    relayed, is the run's entire accumulated stdout. The report is not lost by
    staying out of it: the whole of it goes to the run log at INFO below, and
    :func:`_failure_detail` puts the lines that name the failure on the
    outcome's detail, which the card carries under its ASK.
    """
    runlog.require(target in _TARGET_HINTS, "not a KB maintenance target", target=target)
    argv = _target_argv(repo_root, target)
    if argv is None:
        return Outcome(
            baton.EXIT_ENVIRONMENT,
            detail=(
                f"no justfile or Makefile at {repo_root}, so there is no '{target}' target — restore: "
                f"run 'python3 -m kb_tools.kb_util {kb_util.OP_INSTALL_TARGETS} --runner just|make' "
                f"and commit the change",
            ),
        )
    if not kb_util.targets_installed(repo_root):
        return Outcome(
            baton.EXIT_ENVIRONMENT,
            detail=(
                f"{repo_root} has a runner file, but it does not carry the KB include line, so "
                f"'{target}' is not a target it can run — restore: run "
                f"'python3 -m kb_tools.kb_util {kb_util.OP_INSTALL_TARGETS}' from the repo root "
                f"and commit the change",
            ),
        )
    outcome = _outcome(
        argv,
        repo_root=repo_root,
        op=" ".join(argv),
        exits={0: baton.EXIT_OK},
        otherwise=baton.EXIT_GATE_RED,
        relay=False,
    )
    if outcome.stdout.strip():
        _log.info(
            "gate report",
            extra={"context": {"target": target, "exit_code": outcome.exit_code, "report": outcome.stdout}},
        )
    return outcome
