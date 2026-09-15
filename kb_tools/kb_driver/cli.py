"""Argparse, mode dispatch, and exit-code translation.

The driver's outermost layer, and the one that guarantees that every
terminating path — including the ones that never reach the sequencer, like a
config error or a held lock — leaves through :func:`main`, which renders the
relay baton for the code it is about to return. The relay reads a card; it
never remembers a protocol.

This layer holds no pipeline logic and no stage knowledge. It loads config,
takes the run lock (``pre.lock``), lays out the run directory, starts logging,
and hands control to the stage sequencer.

A run is launchable with no file to compose first: ``--source`` (repeatable)
carries the one ``[run]`` field that has no default, ``--permission-mode``
overrides the mode ``config`` defaults, and every other field keeps the default
``config`` holds. ``--config`` stays, and the two combine — the flag wins for
the field it names, which is the precedence ``--decide`` already has over the
config's ``[barriers.*]`` tables. Composing the flags and the file is
``config.load``'s; this layer only reads which flags were given.

The effective permission mode is announced on the way in, in the message rather
than the log record's context, because the console tee prints messages alone: a
run whose mode nobody chose still says what it is dispatching under and which
flag changes it.

**Two flags change what a run is made of, and they are different claims.**
``--dry-run`` swaps the ``Invoker`` and nothing else: the ledger ops still run
against the real ``kb_util`` in the consuming repo, the run directory and its
briefs and captures are still written, barriers still stop the run, and
persistence still happens — it replaces the model, not the pipeline. What it
does **not** replace is a model spawned inside a tool this driver invokes, which
is the two claim-graph stages, so it is a smoke test of the state machine rather
than a promise that nothing spends inference.

``--no-inference`` is that promise, and it is the sequencer's rather than this
layer's: every row that would cost a model call is dropped and the walk carries
on past it, so a build under it closes out real and inference-free. Which rows
those are is the step table's (``steps.Step.spends_inference``); this layer
holds no stage knowledge and does not acquire any to announce the flag.

Both barrier doors are validated here, against the registry ``barriers.py``
holds: ``config.load`` and ``config.parse_decision`` each take the admissible
answers, so an unknown pair or an inadmissible answer is exit 13 **at load** —
before a run directory exists and before anything is spawned. A typo cannot
become a mid-build stop three hours in.

**A run that dies abnormally still leaves its report.** Once the run directory
exists, every way out of the sequencer writes ``cadence.jsonl`` and
``exit.json`` (``run.write_report``) — a boundary check that failed, a
terminating signal, an exception no handler names, alongside the endings the
walk chose. Those are the endings that most need one: their own card calls the
run directory the bug report, and ``exit.json`` is the one channel a
backgrounded session has for learning how a run ended. The exception-to-exit
mapping is :func:`_terminal`, read by that path and by :func:`main`'s
last-resort handler alike, so a failure raised inside the run and the same
failure raised a frame higher cannot report different codes.

Stdlib only.
"""

import argparse
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from .. import __version__, kb_util
from . import barriers, baton, config, replay, run, runlog, watch

_log = runlog.logger("cli")


def _build_parser() -> argparse.ArgumentParser:
    # allow_abbrev=False: prefix abbreviation silently aliases a flag onto a
    # longer one that shares its prefix, so a flag removed or renamed later
    # keeps answering to its old spelling. Exact flags only.
    parser = argparse.ArgumentParser(
        prog="kb-driver",
        description="Sequence the KB build pipeline.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")

    sub = parser.add_subparsers(dest="mode", required=True, metavar="<mode>")

    p_run = sub.add_parser("run", help="Run the build from its ledger position.", allow_abbrev=False)
    p_run.add_argument(
        config.CONFIG_FLAG,
        type=Path,
        default=None,
        metavar="<path.toml>",
        help="Path to a driver config TOML. Optional: the flags below specify a run on their own, and where "
        "both are given a flag wins for the field it names.",
    )
    p_run.add_argument(
        config.SOURCE_FLAG,
        action="append",
        default=[],
        metavar="<path>",
        help="One source the build reads, relative to the consuming repository's root. Repeatable, one per "
        "source; given at all, it replaces [run] sources rather than extending it.",
    )
    p_run.add_argument(
        config.PERMISSION_MODE_FLAG,
        default=None,
        metavar="<mode>",
        help="The permission mode every dispatched call runs under, one of: "
        + ", ".join(config.PERMISSION_MODES)
        + f" (default: {config.DEFAULT_PERMISSION_MODE}). The driver spawns claude headless, so a mode "
        "that gates a tool the build needs stops it with nobody to answer.",
    )
    p_run.add_argument(
        "--decide",
        action="append",
        default=[],
        metavar="<stage>.<kind>=<answer>[:<note>]",
        help="Answer a barrier, taking precedence over config for its pair. Repeatable.",
    )
    p_run.add_argument(
        config.RUN_DIR_FLAG,
        type=Path,
        default=None,
        help="Override [log] run_dir: the parent holding LATEST and one directory per run.",
    )
    p_run.add_argument(
        config.NO_INFERENCE_FLAG,
        action="store_true",
        help="Spend no model call: every row that would cost one is dropped and the walk continues past "
        "it, so the build closes out without them. Not a bound and not a replay — the stages around a "
        "dropped row run for real, each stage still records, and the boundary of a stage whose work was "
        "dropped says so.",
    )
    p_run.add_argument(
        config.DRY_RUN_FLAG,
        action="store_true",
        help="Replay every call this driver dispatches instead of spawning one — a smoke test of the "
        "state machine. Everything else runs for real. It is not a promise that no model runs: the two "
        "claim-graph stages spawn theirs inside the tool this driver invokes, so a fresh build wanting "
        "to spend nothing wants --no-inference as well.",
    )
    p_run.add_argument(
        config.THROUGH_FLAG,
        default=None,
        metavar="<stage>",
        help="Walk no further than this stage, inclusive, named by its id or by its display name. "
        "Absent, the run walks the whole build.",
    )

    p_watch = sub.add_parser(
        "watch",
        help="Poll a backgrounded run until it advances, ends, or the timeout expires.",
        allow_abbrev=False,
    )
    p_watch.add_argument(
        "--timeout",
        type=int,
        default=watch.DEFAULT_TIMEOUT_SECONDS,
        metavar="<seconds>",
        help=f"Give up waiting for the ledger to grow after this long (default: {watch.DEFAULT_TIMEOUT_SECONDS}).",
    )
    p_watch.add_argument(
        "--poll",
        type=int,
        default=watch.DEFAULT_POLL_SECONDS,
        metavar="<seconds>",
        help=f"Seconds between ledger reads (default: {watch.DEFAULT_POLL_SECONDS}).",
    )
    # Watch has no --config: the relay's watch invocation carries none, and the
    # only config value it would read is [log] run_dir, which this names
    # directly. Required in the kb-testing recipes, whose run directory sits
    # outside the tree the restage wipes.
    p_watch.add_argument(
        config.RUN_DIR_FLAG,
        type=Path,
        default=Path(config.DEFAULT_RUN_DIR),
        help="The parent holding LATEST and one directory per run (default: %(default)s).",
    )

    return parser


def _run_overrides(args: argparse.Namespace) -> dict[str, object]:
    """The ``[run]`` keys this invocation's flags carry. A flag not given carries nothing.

    An absent flag must be absent from the mapping rather than present as a
    default: a default here would overwrite the config's own value with a
    value nobody asked for, which is the one way "a flag wins" turns into "the
    file is ignored".
    """
    overrides: dict[str, object] = {}
    sources = getattr(args, "source", None)  # watch mode declares neither flag
    if sources:
        overrides["sources"] = tuple(sources)
    mode = getattr(args, "permission_mode", None)
    if mode is not None:
        overrides["permission_mode"] = mode
    # A store_true reads False when it was never given, and False is a value
    # that would overwrite a config's own `true`. Only the flag actually
    # passed carries anything, which is this function's rule throughout.
    for attribute, key in (("no_inference", "no_inference"), ("dry_run", "dry_run")):
        if getattr(args, attribute, False):
            overrides[key] = True
    through = getattr(args, "through", None)
    if through is not None:
        overrides["through"] = through
    return overrides


def _terminal(exc: BaseException) -> tuple[int, tuple[str, ...]]:
    """The exit code and ASK lines for an exception that ended an invocation.

    One mapping, two readers: the handler standing between a dying run and its
    report, and :func:`main`'s last-resort row. A second spelling would let a
    failure raised inside the sequencer report a different code than the same
    failure raised a frame higher.

    The unrecognized class is the last row and the one that owes a traceback to
    the run log: the exception's text reaches the operator through the baton,
    while the traceback stays in the run directory EXIT_INTERNAL's card calls
    the bug report.
    """
    if isinstance(exc, config.ConfigError):
        return baton.EXIT_CONFIG, (str(exc),)
    if isinstance(exc, runlog.LockedError):
        return baton.EXIT_LOCKED, (str(exc),)
    if isinstance(exc, runlog.Terminated):
        return exc.exit_code, (str(exc),)
    if isinstance(exc, runlog.BoundaryError):
        return baton.EXIT_INTERNAL, (str(exc),)
    _log.exception("unhandled exception; exiting %s", baton.EXIT_INTERNAL)
    return baton.EXIT_INTERNAL, (f"{type(exc).__name__}: {exc}",)


def _mode_run(args: argparse.Namespace, ctx: baton.BatonContext) -> tuple[int, baton.BatonContext]:
    del ctx  # the sequencer's result carries the context every run-mode exit needs
    cfg = config.load(
        args.config,
        run_overrides=_run_overrides(args),
        admissible=barriers.ADMISSIBLE,
        run_dir=args.run_dir,
    )
    decisions = [config.parse_decision(spec, admissible=barriers.ADMISSIBLE) for spec in args.decide]

    # The lock is anchored at the repository, not at the run directory,
    # so the root has to be resolved before it can be taken; it is then handed
    # to the sequencer rather than discovered a second time. No root means no
    # repository, so there is nothing to lock and nothing to build — the run
    # directory and exit.json that say so are still laid out below, and
    # `run.execute` is what names the environment fault.
    try:
        repo_root: Path | None = kb_util.find_git_root()
    except kb_util.RepoRootError:
        repo_root = None

    # `config.load` has already settled the flag against `[log] run_dir`, so the
    # effective parent is read off the config rather than resolved a second time
    # here — the resume line is rendered from that same value.
    parent = cfg.log.run_dir
    run_id = runlog.new_run_id()
    lock = nullcontext() if repo_root is None else runlog.run_lock(runlog.repo_lock_path(repo_root), run_id=run_id)
    with lock:
        paths = runlog.prepare(parent, run_id)
        runlog.configure(run_log=paths.run_log, level=cfg.log.level)
        _log.info(
            "run started",
            extra={"context": {"run_id": run_id, "invocation": cfg.invocation, "run_dir": str(paths.run_dir)}},
        )
        _log.info(
            "every dispatched call runs under permission mode %s; pass `%s <mode>` to change it",
            cfg.run.permission_mode,
            config.PERMISSION_MODE_FLAG,
            extra={"context": {"run_id": run_id, "permission_mode": cfg.run.permission_mode}},
        )

        if cfg.run.dry_run:
            # WARNING, not INFO: every artifact a replayed run leaves behind is
            # synthetic, and the one place that fact is recorded for whoever
            # reads the run log afterwards is here. Said once, on the way in,
            # beside the permission mode.
            _log.warning(
                "dry run: every call this driver dispatches is replayed, so nothing below it was produced "
                "by a model. `%s` does not reach a model spawned inside a tool this driver invokes; a run "
                "that must spend nothing at all passes `%s` too",
                config.DRY_RUN_FLAG,
                config.NO_INFERENCE_FLAG,
                extra={"context": {"run_id": run_id}},
            )
        if cfg.run.no_inference:
            # Not synthetic and not a bound: the rows are gone, the rest of the
            # build is real, and what it produced is a real KB built without
            # them. Said on the way in for the same reason the mode is.
            _log.warning(
                "no inference: every row that would cost a model call is dropped and the walk continues "
                "past it, so this build closes out without them; each such stage's boundary records that "
                "its work did not run. Re-run without `%s` to spend inference",
                config.NO_INFERENCE_FLAG,
                extra={"context": {"run_id": run_id}},
            )

        try:
            with runlog.terminating_signals():
                result = run.execute(
                    config=cfg,
                    paths=paths,
                    decisions=decisions,
                    invoker=replay.dry_run_invoker() if cfg.run.dry_run else None,
                    repo_root=repo_root,
                )
        except BaseException as exc:  # noqa: BLE001 — see below
            # The run directory exists from here on, so an ending the walk did
            # not choose still writes the report a planned one does. Catching
            # the base class is what covers the endings nobody planned — a
            # signal, a boundary check, a defect — and this is the layer that
            # turns an exception into an exit code, so there is nothing above
            # it that a re-raise would inform.
            exit_code, detail = _terminal(exc)
            run.write_report(paths, exit_code=exit_code)
            return exit_code, baton.BatonContext(
                invocation=cfg.invocation,
                run_dir=str(paths.run_dir),
                run_dir_parent=config.run_dir_parent(paths.parent),
                detail=detail,
            )

        # This is what a session reads after watch reports the driver gone —
        # the terminal code, the barrier record to paste, and the decisions
        # that answered nothing.
        run.write_report(
            paths,
            exit_code=result.exit_code,
            barrier_record=result.barrier_record,
            unconsumed_decisions=result.unconsumed_decisions,
        )
        return result.exit_code, result.context


# Mode dispatch. Each further mode (watch, and whatever the registry adds)
# registers here and nowhere else.
_MODES = {"run": _mode_run, "watch": watch.mode}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # --help and --version print their own output and are not build
        # invocations; a usage error is an unrecognized terminal state, which
        # is exactly what the fallback baton exists for.
        code = exc.code if isinstance(exc.code, int) else 1
        if code:
            runlog.relay(baton.render(code))
        return code

    # The context for every ending that never reaches a mode's own — a config
    # refusal, a held lock. The flag is all it can read the run directory from:
    # the file has not been loaded, and on the paths that get here it never
    # will be.
    ctx = baton.BatonContext(
        invocation=config.invocation(
            getattr(args, "config", None), _run_overrides(args), run_dir=getattr(args, "run_dir", None)
        ),
        run_dir_parent=config.run_dir_parent(getattr(args, "run_dir", None)),
    )
    try:
        exit_code, ctx = _MODES[args.mode](args, ctx)
    except BaseException as exc:  # noqa: BLE001 — the last-resort baton row; see below
        # The rule is absolute: no terminating path leaves without a baton.
        # `_terminal` names the failures the driver understands and ends with a
        # row for the ones it does not — an OSError writing exit.json, a
        # KeyError in a registry — where a traceback and a bare exit 1 would
        # leave the relay with no card and nothing to report but the traceback.
        # The base class rather than `Exception`, because a signal that reached
        # a path outside the run is still a terminating path and still owes a
        # card.
        exit_code, detail = _terminal(exc)
        ctx = replace(ctx, detail=detail)

    runlog.relay(baton.render(exit_code, ctx))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
