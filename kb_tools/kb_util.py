"""Shared utility and vocabulary module for the KB toolchain.

Two kinds of single-sourced truth live here: KB path construction and the
maintenance-command hints that build-side scripts emit in remediation text.

Both the build-side scripts (``kb_tools/``) and the read-side query package
(``kb_tools/kb_cmd/``) resolve every ``kb-root/`` path through this module
rather than constructing the literals themselves.

Root discovery is lazy and cwd-anchored: the consuming repo's root is found
by :func:`find_repo_root`, which walks up from the current working directory
to the first directory containing a ``.git`` entry (a directory, or the file
a linked git worktree carries) and requires the ``kb-root/`` content tree
beside it. Nothing is ever derived from ``__file__``: the toolchain is an
installed copy under ``.claude/agents/``, so ``__file__`` only ever describes
where the tools were installed, not the repo being worked on.

Maintenance-command hints (:func:`refresh_cmd` / :func:`verify_cmd`) are
detected, not configured: a ``justfile`` at the detected root selects
``just <target>``, a ``Makefile`` selects ``make <target>`` (justfile wins
when both are present), and with neither the hint falls back to the raw
``python3 -m kb_tools.<module>`` invocation, which always works.

The module doubles as the KB build's mechanical front end. Run as a CLI —
``python3 -m kb_tools.kb_util <op> [options]`` — it manages the single include
line through which a consuming repo's runner (justfile or Makefile) gains
the KB maintenance targets (``install-targets`` / ``uninstall-targets``);
initialises the claim-graph metadata spine over an already-built document tree
under ``graph-init`` (index directory, include line, refresh, verify); reports
the build environment
under ``preflight``; and drives the build's stage ledger through
``show-status`` / ``start-build`` / ``advance-step``, with ``show-stage-status``
reading one stage's coverage beside them.

``show-confirmation`` stands in front of the build's opening gate: it renders
the whole confirmation as one message and writes nothing. What the answer then
releases is ``start-build``, already above — one route into a build, so there is
no second door to keep in step with it. The target definitions themselves ship
in ``runner-snippets/`` (``kb.just`` / ``kb.mk``) and are included from the
installed tree, never copied into the consumer's file.

``validate-build`` joins that op set as the built-tree validator's front end:
handed a KB tree, it walks that tree and checks its structure. The tree is the
whole of what it is told, and the tree is produced upstream of this op.

``show-run-lock`` reports the driver's run lock — live, stale or absent, with
the holder named where there is one — so a caller outside ``kb_driver`` (a
staging recipe about to wipe a workspace) can ask whether a build is running in
this repository. It writes nothing, clears nothing, and restates nothing: the
judgement is ``kb_driver.runlog.lock_state``'s, and its own answer rides its
stdout rather than its exit status.

The nine metadata **write** ops — ``insert-claim-entry``,
``insert-support-entry``, ``insert-experiment-entry``, ``set-rigor``,
``set-rationale``, ``add-depends-on``, ``set-frontmatter``,
``mark-claim-in-leaf`` and ``set-on-point-fraction`` — join the set as the front
end of ``kb_write``. Each takes its values in a TOML
file named by ``--values`` rather than on the command line, and the two register
inserts alone take ``--create``. Their semantics — the validation ladder, the
refusal reasons, the report lines and the three exit codes — live entirely in
``kb_write.ops`` with no argparse in the picture; what is here is the surface
binding and one adapter, so nothing about an op changes when its surface does.

Each op is an ``argparse`` subcommand owning its own options, so a companion
that belongs to one op cannot be spelled on another: an invalid pairing is
unrepresentable rather than policed.

Stdlib only.
"""

import argparse
import functools
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kb_tools import __version__

KB_DIRNAME = "kb-root"
INDEX_DIRNAME = ".index"
CLAIMS_FILENAME = "claims.jsonl"

# The claim-graph sheet, at rest beside `claim-quality.md` rather than inside
# `.index/`: it is a derived view of that directory, and refresh regenerates
# `.index/` wholesale while verify diffs a dry-run rebuild against it, so an
# artifact written there would be clobbered or flagged.
CLAIM_GRAPH_FILENAME = "claim-graph.svg"

# No INVARIANTS_FILENAME here: the corpus-invariant source is `invariants.md`
# (`CLAUDE.md` is the legacy spelling), and `kb_index_lib` owns both names.

# The installed agent-set tree in a consuming repo, and the two docent
# commands whose presence proves the install reached the commands surface.
CLAUDE_DIRNAME = ".claude"
COMMANDS_DIRNAME = "commands"
DOCENT_COMMAND_FILENAMES = ("kb-start.md", "kb-next.md")

# The consuming project's scratch space, and the one subdirectory inside it
# that is a KB-build artifact. Everything else under SCRATCH_DIRNAME is
# unrelated scratch from any other agent work and carries no build meaning.
SCRATCH_DIRNAME = ".claude-temp"
SCRATCH_BUILD_DIRNAME = "kb-build"

# Maintenance-target vocabulary. The consuming project's runner (justfile or
# Makefile) owns the target definitions; these mirror the target names so
# emitted remediation hints stay single-sourced.
TARGET_REFRESH = "kb-refresh"
TARGET_VERIFY = "kb-verify"

# Runner files probed at the repo root, in priority order: any justfile
# variant selects `just`, else any make variant selects `make`.
_JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")

# The always-works raw invocation, written repo-root-relative (a consuming
# repo has the toolchain installed at `.claude/agents`).
_RAW_CMD = "PYTHONPATH=.claude/agents python3 -m kb_tools.{module}"

#: This CLI's consumer-side invocation, up to but not including the op — the one
#: spelling every rendered card, brief slot and remediation hint is built from.
#: No card, brief, definition or recipe hand-writes a `kb_util` command line.
#: `kb_pipeline`'s card commands and `kb_driver.steps`' brief slots are
#: both pinned to this string in test, so the three cannot drift apart.
INVOCATION = _RAW_CMD.format(module="kb_util")

#: The driver's consumer-side invocation, up to but not including the mode. It
#: is published here beside :data:`INVOCATION` rather than inside `kb_driver`
#: because both are the same fact — how a consuming repo reaches an installed
#: module — and a change to that reaches them together only while one string
#: builds both. `kb_driver.baton`'s `THEN RUN:` lines and `kb_driver.watch`'s
#: restore hint are the consumers; the relaying session runs what they print,
#: so a spelling without the prefix does not resolve where that session stands.
DRIVER_INVOCATION = _RAW_CMD.format(module="kb_driver")


# --- the two build front ends -------------------------------------------------
#
# `kb_docgraph` and `kb_claimgraph` are module CLIs of their own rather than ops
# of this one (ARCHITECTURE.md, The Document Graph and The Claim Graph). Their
# module names and their flags are single-sourced here for the reason
# `INVOCATION` is: two consumers spell each invocation — the stage card that
# prints the command a person runs, and `kb_driver.ledger`, which runs it as a
# subprocess — and a flag spelled at both is a flag renamed at one.

DOCGRAPH_MODULE = "kb_docgraph"
CLAIMGRAPH_MODULE = "kb_claimgraph"


def module_invocation(module: str) -> str:
    """The consumer-side invocation of one ``kb_tools`` module CLI, up to its flags."""
    return _RAW_CMD.format(module=module)


def docgraph_flags(*, sources: Sequence[str], bibliographies: Sequence[str], kb_root_path: str) -> tuple[str, ...]:
    """``kb_docgraph``'s flags: one ``--source`` per volume root, one ``--bibliography`` per file.

    Every source is passed; nothing is discovered here, because which of a
    corpus's files are volume roots is the one thing that front end refuses to
    infer and the caller states.

    ``--bibliography`` repeats because the reader takes it repeatedly and merges
    what it is given, so the caller passes the set it found rather than choosing
    one of them. An empty sequence omits the flag entirely: a corpus with no
    ``.bib`` is an ordinary corpus — many ship a pre-generated ``.bbl`` or
    inline ``\bibitem`` — and its citations still reach the tree carrying their
    own keys.
    """
    named = [flag for source in sources for flag in ("--source", source)]
    cited = [flag for path in bibliographies for flag in ("--bibliography", path)]
    return (*named, *cited, "--kb-root", kb_root_path)


# `kb_claimgraph`'s flags are NOT here, and the asymmetry is the point: which of
# its three invocations a run is making is a *stage* of the build pipeline, and
# `kb_pipeline.ClaimgraphInvocation` is where the two vocabularies are paired —
# read by the card that prints the command, by the driver that runs it, and by
# the tool that parses it back. A second spelling here would be the third.

# Canonical installed lines — the one line the installer manages in a
# consuming repo's runner file; the target definitions live in the installed
# tree's runner-snippets/ and are included from there. The non-fatal include
# forms (`-include` / `import?`, just >= 1.33) are deliberate: an absent
# .claude/agents must degrade to missing KB targets, never break the
# consumer's whole runner.
INSTALL_LINE_MAKE = "-include .claude/agents/kb_tools/runner-snippets/kb.mk"
INSTALL_LINE_JUST = "import? '.claude/agents/kb_tools/runner-snippets/kb.just'"

_INSTALL_LINES = {"just": INSTALL_LINE_JUST, "make": INSTALL_LINE_MAKE}
_RUNNER_PROBE_NAMES = {"just": _JUSTFILE_NAMES, "make": _MAKEFILE_NAMES}
_RUNNER_CREATE_NAMES = {"just": "justfile", "make": "Makefile"}

#: The runner a seed creates when the repository carries neither runner file.
#: A KB build's corpus is LaTeX, and a LaTeX project needs real dependency
#: management, so make is the likelier fit; ``--runner just`` names the other.
#: It is consulted only where the probe found nothing — naming a runner
#: restricts the probe to that runner's file names, so applying this as an
#: argparse default would install into a new Makefile beside an existing
#: justfile.
DEFAULT_RUNNER = "make"

# ---------------------------------------------------------------------------
# The CLI's op vocabulary — one subcommand token per op, defined here and only
# here.
#
# Named rather than spelled at each use because this CLI's invocations are
# *rendered* elsewhere: `kb_pipeline`'s stage cards build the command an agent
# is told to run, `kb_driver.ledger` builds the argv the driver spawns, and
# `kb_driver.steps.LedgerOp` takes its member values from the two ledger-write
# tokens below. A renderer holding its own literal could advertise an op this
# parser does not have; reading these, it cannot.
#
# `build_parser` names its subparsers from these same constants, so the surface
# and every renderer of it resolve to one object.
# ---------------------------------------------------------------------------

OP_PREFLIGHT = "preflight"

# The claim-graph metadata seed. Named `graph-init` and not `init`: `init` reads
# as `git init` — start from nothing — and starting from nothing is the one
# thing this verb refuses. It initialises the claim-graph spine *over* a
# document tree the front end already wrote.
OP_GRAPH_INIT = "graph-init"
OP_INSTALL_TARGETS = "install-targets"
OP_UNINSTALL_TARGETS = "uninstall-targets"

# The three ledger ops. Their words are the op names everywhere — in every card,
# brief-lint token, `LedgerOp` member and driver report line.
OP_SHOW_STATUS = "show-status"
OP_START_BUILD = "start-build"
OP_ADVANCE_STEP = "advance-step"

#: The one flag naming a *build property* rather than an op's own argument, and
#: the reason it is spelled once here: the driver declares it on its own command
#: line and passes it through to ``advance-step``, so the two surfaces cannot
#: come to spell one fact two ways.
NO_INFERENCE_FLAG = "--no-inference"

#: Where a run's evidence goes. Spelled here for a different reason than the
#: flag above: no op takes it, but both of the driver's modes declare it and the
#: relay cards print it back in the commands they offer — and a card is rendered
#: by ``kb_driver.baton``, which imports no driver module and so cannot read the
#: driver's own constant. This is the module it can read.
RUN_DIR_FLAG = "--run-dir"

# The stage-coverage read: what one stage still has to cover, asked instead of
# reconstructed. It records nothing, so it is a reading verb over the ledger
# rather than a fourth ledger op.
OP_SHOW_STAGE_STATUS = "show-stage-status"

# The read that stands in front of the build's opening gate: the whole
# confirmation as one message, so a caller relays it rather than composing one.
# It writes nothing, and what the user's answer releases is `start-build` above
# and nothing else — one door into a build, so there is no second route for this
# one to drift from. The spine seed is not part of opening one: `graph-init`
# runs at `spine-seed`, over a tree `document-graph` has by then written.
OP_SHOW_CONFIRMATION = "show-confirmation"

# The built-tree validator: walks a built KB tree and checks its structure.
OP_VALIDATE_BUILD = "validate-build"

# The driver's run lock, read from outside the driver. The lock is the
# repository's and a recipe that is about to wipe a workspace has to know
# whether a build is running in it; the judgement stays `kb_driver.runlog`'s and
# this op only reports it, so nothing outside that module tests a pid.
OP_SHOW_RUN_LOCK = "show-run-lock"

#: The fields :data:`OP_SHOW_RUN_LOCK` prints, in this order. Closed and total:
#: every answer carries all five, empty where there is nobody to name, so a
#: shell reading them branches on the value and never on which keys arrived.
#: Spelled with underscores because a caller reads them into variables of the
#: same names.
RUN_LOCK_KEYS: tuple[str, ...] = ("state", "pid", "run_id", "started", "lock")

# The claim-graph renderer: reads the derived index and emits one SVG sheet.
# `kb_graph.ops` takes its report tag from this token, so the op's name and the
# name its report lines carry are one string.
OP_RENDER_CLAIM_GRAPH = "render-claim-graph"

# The nine metadata write ops. Their
# semantics live in `kb_write.ops`, which imports this module — so the direction
# that would let one read the other's names is the one that exists, and these
# tokens are defined here, upstream of the package implementing them.
# `kb_write.ops.OPS` keys the same set; `test_kb_util.py` asserts the two sets
# equal, which is the mechanical stand-in for the import this module cannot make
# back the other way.
OP_INSERT_CLAIM_ENTRY = "insert-claim-entry"
OP_INSERT_SUPPORT_ENTRY = "insert-support-entry"
OP_INSERT_EXPERIMENT_ENTRY = "insert-experiment-entry"
OP_INSERT_WORK_ENTRY = "insert-work-entry"
OP_SET_WORK_STRENGTH = "set-work-strength"
OP_SET_APPLICABILITY = "set-applicability"
OP_SET_RIGOR = "set-rigor"
OP_SET_RATIONALE = "set-rationale"
OP_ADD_DEPENDS_ON = "add-depends-on"
OP_SET_FRONTMATTER = "set-frontmatter"
OP_MARK_CLAIM_IN_LEAF = "mark-claim-in-leaf"
OP_SET_ON_POINT_FRACTION = "set-on-point-fraction"

#: The write ops, in the order they are declared in and the order a renderer
#: iterating the write surface meets them.
WRITE_OPS: tuple[str, ...] = (
    OP_INSERT_CLAIM_ENTRY,
    OP_INSERT_SUPPORT_ENTRY,
    OP_INSERT_EXPERIMENT_ENTRY,
    OP_INSERT_WORK_ENTRY,
    OP_SET_WORK_STRENGTH,
    OP_SET_APPLICABILITY,
    OP_SET_RIGOR,
    OP_SET_RATIONALE,
    OP_ADD_DEPENDS_ON,
    OP_SET_FRONTMATTER,
    OP_MARK_CLAIM_IN_LEAF,
    OP_SET_ON_POINT_FRACTION,
)

# The metadata surface's one read-only op. Kept out
# of `WRITE_OPS` on purpose: that tuple is what the driver's ledger admits as a
# spawnable write, what `steps.WRITE_OP_SLOTS` renders a brief slot for, and
# what carries the write ops' four-code exit vocabulary. This op writes nothing,
# no brief invokes it, and it can never earn the contended-file code — so it is
# a sibling constant, keyed by `kb_write.ops.READ_OPS`.
OP_RENDER_CITATION = "render-citation"

#: The read-only metadata ops. One, today.
READ_OPS: tuple[str, ...] = (OP_RENDER_CITATION,)

#: The one argument every metadata op takes, published for the same reason
#: :data:`INVOCATION` and :data:`WRITE_OPS` are: this flag is *rendered* as well
#: as declared. :func:`_add_values_option` builds the option from it,
#: `kb_pipeline._kb_util_command` appends it to every card command, and
#: `kb_driver.steps.CONSTANT_SLOTS` publishes it as the `@!values-flag!@` brief
#: slot — so the third token of the sanctioned invocation moves with a rename
#: exactly as the first two do. Hand-typing it anywhere is exactly the staleness
#: this single-sourcing exists to make impossible, which is why
#: `steps.TEMPLATE_PROHIBITIONS` refuses a template that spells it.
VALUES_FLAG = "--values"


class RepoRootError(FileNotFoundError):
    """The consuming repo's root (a ``.git`` entry + ``kb-root/``) was not found."""


class RunnerFileError(FileNotFoundError):
    """No runner file to install the KB include line into (and no ``--runner``)."""


def is_repo_root(p: Path) -> bool:
    """True if ``p`` is a consuming repo's root.

    A root carries a ``.git`` entry (a directory, or the ``.git`` file a
    linked worktree uses) with the ``kb-root/`` content tree beside it.
    """
    return (p / ".git").exists() and (p / KB_DIRNAME).is_dir()


def find_git_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: the cwd) to the enclosing git root.

    The root is the first ancestor containing a ``.git`` entry — a directory,
    or the ``.git`` *file* a linked git worktree carries. Unlike
    :func:`find_repo_root` this does not require ``kb-root/`` beside it, so a
    verb that reports on a repository with no KB in it — ``preflight``, and
    ``graph-init`` on its way to refusing one — can anchor before there is a KB.

    Raises :class:`RepoRootError` when the walk finds no ``.git``.
    """
    start = Path.cwd() if start is None else start
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RepoRootError(
        f"no .git entry found walking up from {start}; cannot locate the "
        f"repo root. The KB tools derive the root from the working "
        f"directory — there is no override flag; run them from inside the "
        f"consuming repository (any subdirectory works)."
    )


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: the cwd) to the consuming repo's root.

    The root is the git root (:func:`find_git_root`) and it must have the
    ``kb-root/`` content tree beside it.

    Raises :class:`RepoRootError` with an actionable message when the walk
    finds no ``.git``, or when the git toplevel has no ``kb-root/``.
    """
    root = find_git_root(start)
    if (root / KB_DIRNAME).is_dir():
        return root
    raise RepoRootError(
        f"git toplevel found at {root}, but it has no '{KB_DIRNAME}/' "
        f"directory beside .git. The KB tools operate on a repo whose root "
        f"contains the '{KB_DIRNAME}/' content tree, and derive that root "
        f"from the working directory — there is no override flag; run them "
        f"from inside such a repo (any subdirectory works), or initialise the "
        f"spine first with 'python3 -m kb_tools.kb_util {OP_GRAPH_INIT}'."
    )


def kb_root(repo_root: Path | None = None) -> Path:
    """The KB top-level directory (``<repo_root>/kb-root``).

    ``repo_root`` defaults to lazy discovery via :func:`find_repo_root`.
    """
    root = find_repo_root() if repo_root is None else repo_root
    return root / KB_DIRNAME


def index_dir(repo_root: Path | None = None) -> Path:
    """The derived-index directory (``<kb_root>/.index``)."""
    return kb_root(repo_root) / INDEX_DIRNAME


def claims_jsonl(repo_root: Path | None = None) -> Path:
    """The type-tagged node register (``<index_dir>/claims.jsonl``)."""
    return index_dir(repo_root) / CLAIMS_FILENAME


def _has_runner_file(repo_root: Path, names: tuple[str, ...]) -> bool:
    return any((repo_root / name).is_file() for name in names)


def runner_cmd(target: str, module: str, repo_root: Path | None = None) -> str:
    """The best invocation hint for a maintenance action at ``repo_root``.

    Returns ``just <target>`` if the root carries a justfile, ``make
    <target>`` if it carries a Makefile (justfile wins when both exist), and
    otherwise the raw ``python3 -m kb_tools.<module>`` invocation. With
    ``repo_root`` None the root is discovered lazily; a hint must never
    raise, so an undiscoverable root also yields the raw invocation.
    """
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RepoRootError:
            return _RAW_CMD.format(module=module)
    if _has_runner_file(repo_root, _JUSTFILE_NAMES):
        return f"just {target}"
    if _has_runner_file(repo_root, _MAKEFILE_NAMES):
        return f"make {target}"
    return _RAW_CMD.format(module=module)


def refresh_cmd(repo_root: Path | None = None) -> str:
    """Invocation hint for the refresh action (see :func:`runner_cmd`)."""
    return runner_cmd(TARGET_REFRESH, "refresh_kb_metadata", repo_root)


def verify_cmd(repo_root: Path | None = None) -> str:
    """Invocation hint for the verify action (see :func:`runner_cmd`)."""
    return runner_cmd(TARGET_VERIFY, "verify_kb_metadata", repo_root)


def _find_installer_target(repo_root: Path, runner: str | None) -> tuple[str, Path] | None:
    """The (runner, file) the installer operates on, or None when none exists.

    Without an explicit ``runner`` the probe order matches :func:`runner_cmd`:
    justfile variants win over Makefile variants. An explicit ``runner``
    restricts the probe to that runner's file names, regardless of what the
    other runner has at the root.
    """
    runners = (runner,) if runner else ("just", "make")
    for kind in runners:
        for name in _RUNNER_PROBE_NAMES[kind]:
            if (repo_root / name).is_file():
                return kind, repo_root / name
    return None


def runner_filename(runner: str) -> str:
    """The file name the installer creates for ``runner`` (``just`` -> ``justfile``)."""
    return _RUNNER_CREATE_NAMES[runner]


def detected_runner(repo_root: Path) -> str | None:
    """The runner whose file sits at ``repo_root`` (justfile wins), or ``None``.

    The public form of the installer's probe, for a caller that has to report
    what was detected rather than act on it.
    """
    found = _find_installer_target(repo_root, None)
    return None if found is None else found[0]


def targets_installed(repo_root: Path, runner: str | None = None) -> bool:
    """True when ``repo_root``'s runner file already carries the include line."""
    found = _find_installer_target(repo_root, runner)
    if found is None:
        return False
    kind, path = found
    return _INSTALL_LINES[kind] in path.read_text(encoding="utf-8").splitlines()


def install_targets(repo_root: Path, runner: str | None = None) -> str:
    """Install the canonical KB include line into ``repo_root``'s runner file.

    Exact-line search first: if the canonical line is already present the file
    is untouched. Otherwise the line is appended (preceded by a blank line
    when the file does not already end with one). With no runner file at the
    root, an explicit ``runner`` creates it; otherwise :class:`RunnerFileError`
    is raised. Returns the one-line report of what was done.
    """
    found = _find_installer_target(repo_root, runner)
    if found is None:
        if runner is None:
            raise RunnerFileError(
                f"no justfile or Makefile found at {repo_root}; nothing to "
                f"install the KB include line into. Re-run with --runner just "
                f"or --runner make to create one containing it."
            )
        path = repo_root / _RUNNER_CREATE_NAMES[runner]
        path.write_text(
            f"# {_RUNNER_CREATE_NAMES[runner]} — created by the kb_tools installer.\n"
            f"# The line below pulls in the KB maintenance targets from the\n"
            f"# installed tree at .claude/agents; add project recipes below it.\n"
            f"\n"
            f"{_INSTALL_LINES[runner]}\n",
            encoding="utf-8",
        )
        return f"created {path} with the KB include line"
    kind, path = found
    line = _INSTALL_LINES[kind]
    text = path.read_text(encoding="utf-8")
    if line in text.splitlines():
        return f"already installed: {path} contains the KB include line"
    if not text:
        new = f"{line}\n"
    elif text.endswith("\n\n"):
        new = f"{text}{line}\n"
    elif text.endswith("\n"):
        new = f"{text}\n{line}\n"
    else:
        new = f"{text}\n\n{line}\n"
    path.write_text(new, encoding="utf-8")
    return f"installed: appended the KB include line to {path}"


def uninstall_targets(repo_root: Path, runner: str | None = None) -> str:
    """Remove the canonical KB include line from ``repo_root``'s runner file.

    Removes exactly the canonical line — plus the blank line the installer
    introduced before it, in the one trivially detectable case (the include
    line ends the file, directly preceded by an empty line). Everything else
    in the file is untouched; an absent line (or absent runner file) reports
    "not installed" without error. Returns the one-line report.
    """
    found = _find_installer_target(repo_root, runner)
    if found is None:
        return f"not installed: no runner file at {repo_root}"
    kind, path = found
    line = _INSTALL_LINES[kind]
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line not in [ln.rstrip("\n") for ln in lines]:
        return f"not installed: {path} does not contain the KB include line"
    out: list[str] = []
    last = len(lines) - 1
    for i, raw in enumerate(lines):
        if raw.rstrip("\n") == line:
            if i == last and out and out[-1] == "\n":
                out.pop()
            continue
        out.append(raw)
    path.write_text("".join(out), encoding="utf-8")
    return f"uninstalled: removed the KB include line from {path}"


# ---------------------------------------------------------------------------
# preflight: the mechanical build-environment report
#
# One line per item in a uniform format, gating items first, non-gating facts
# after — a mechanical report rather than hand-rolled shell probes, which vary
# per invocation. The only side effect anywhere below is the scratch-directory
# mkdir.
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
FACT = "FACT"

_ITEM_NAME_WIDTH = 17


def to_stderr(text: str) -> None:
    """Write to stderr, flushing stdout first.

    stdout is block-buffered when captured (which is how the agent driving
    these commands reads them) while stderr is not, so an unflushed report
    would arrive *after* the message explaining it.
    """
    sys.stdout.flush()
    print(text, file=sys.stderr)


@dataclass(frozen=True)
class PreflightItem:
    """One reported item. ``FACT`` items describe state and never gate."""

    status: str
    name: str
    detail: str

    def line(self) -> str:
        return f"[preflight] {self.status} {self.name:<{_ITEM_NAME_WIDTH}} {self.detail}"


def run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run git at ``repo_root``; ``None`` when git cannot be executed at all.

    Crossing to an external process: a missing or unrunnable git is reported
    as a failed check rather than raised, so one absent tool cannot turn the
    whole report into a traceback.
    """
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None


#: The KB tree's tri-state, named once. Every value :func:`kb_root_state`
#: returns is a member and every consumer branching on one reads it here, so a
#: caller cannot answer for a state the function does not produce.
KB_ROOT_ABSENT = "absent"
KB_ROOT_SPINE_ONLY = "spine-only"
KB_ROOT_POPULATED = "populated"
KB_ROOT_STATES = (KB_ROOT_ABSENT, KB_ROOT_SPINE_ONLY, KB_ROOT_POPULATED)


def kb_root_state(repo_root: Path) -> str:
    """Tri-state of the KB tree, one of :data:`KB_ROOT_STATES`.

    ``spine-only`` means the directory exists with nothing in it outside
    ``.index/`` — a seeded but uncontented KB.
    """
    kb = kb_root(repo_root)
    if not kb.is_dir():
        return KB_ROOT_ABSENT
    if any(entry.name != INDEX_DIRNAME for entry in kb.iterdir()):
        return KB_ROOT_POPULATED
    return KB_ROOT_SPINE_ONLY


def document_tree_present(repo_root: Path) -> bool:
    """True when ``kb-root/`` holds a document tree to hang claim-graph metadata on.

    The shape the document-graph front end writes: ``entry-point.md`` at the KB
    root with at least one volume directory beside it. **This is a precondition
    test, not the conformance gate** — whether the tree satisfies SPEC.md's
    Document-Tree Contract in full is the claim-graph builder's question, asked
    against the whole contract. What this answers is the cheaper one that has to
    come first: is there a tree here at all.
    """
    # Local import for `run_validate`'s reason: `kb_index_lib` imports this
    # module, so a module-scope import would close the cycle. The filename is
    # single-sourced there rather than re-spelled here.
    from kb_tools.kb_index_lib import ENTRY_POINT_FILENAME

    kb = kb_root(repo_root)
    if not (kb / ENTRY_POINT_FILENAME).is_file():
        return False
    return any(entry.is_dir() and entry.name != INDEX_DIRNAME for entry in kb.iterdir())


def preflight_report(repo_root: Path) -> list[PreflightItem]:
    """Build the preflight item list for ``repo_root``.

    Creates the scratch directory when absent — the one and only side effect,
    and not a check, since an absent scratch directory is a thing to make
    rather than a thing to fail on.
    """
    # Read the worktree state BEFORE the mkdir below, so this function's own
    # side effect can never be what makes the worktree look dirty.
    status = run_git(repo_root, "status", "--porcelain")
    items = [PreflightItem(PASS, "git-root", str(repo_root))]

    commands_dir = repo_root / CLAUDE_DIRNAME / COMMANDS_DIRNAME
    missing = [name for name in DOCENT_COMMAND_FILENAMES if not (commands_dir / name).is_file()]
    if missing:
        items.append(
            PreflightItem(
                FAIL,
                "docent-commands",
                f"missing {', '.join(missing)} under {commands_dir} — incomplete install; "
                f"restore: run 'just install {repo_root}' from the generator repo",
            )
        )
    else:
        items.append(PreflightItem(PASS, "docent-commands", f"both present under {commands_dir}"))

    scratch = repo_root / SCRATCH_DIRNAME
    if scratch.is_dir():
        items.append(PreflightItem(FACT, "scratch-dir", f"present: {scratch}"))
    else:
        scratch.mkdir(parents=True)
        items.append(PreflightItem(FACT, "scratch-dir", f"created: {scratch}"))

    ignored = run_git(repo_root, "check-ignore", "-q", SCRATCH_DIRNAME)
    if ignored is None or ignored.returncode not in (0, 1):
        items.append(
            PreflightItem(
                FAIL,
                "scratch-ignored",
                f"could not determine ignore coverage for {SCRATCH_DIRNAME}/ — "
                f"restore: ensure git is on PATH and the root is a git worktree, then re-run",
            )
        )
    elif ignored.returncode == 0:
        items.append(PreflightItem(PASS, "scratch-ignored", f"{SCRATCH_DIRNAME}/ is covered by gitignore rules"))
    else:
        items.append(
            PreflightItem(
                FAIL,
                "scratch-ignored",
                f"{SCRATCH_DIRNAME}/ is not ignored — restore: append '{SCRATCH_DIRNAME}/' to "
                f"{repo_root / '.gitignore'} and commit that change (this tool never writes "
                f"gitignore rules or commits in your repo)",
            )
        )

    if status is None or status.returncode != 0:
        items.append(
            PreflightItem(
                FAIL,
                "worktree-clean",
                "could not read the worktree state — restore: ensure git is on PATH and the "
                "root is a git worktree, then re-run",
            )
        )
    else:
        dirty = [line for line in status.stdout.splitlines() if line.strip()]
        if dirty:
            items.append(
                PreflightItem(
                    FAIL,
                    "worktree-clean",
                    f"{len(dirty)} uncommitted entr{'y' if len(dirty) == 1 else 'ies'} — "
                    f"restore: commit or stash before building",
                )
            )
        else:
            items.append(PreflightItem(PASS, "worktree-clean", "no uncommitted entries"))

    # Non-gating facts. The kb-root tri-state is reported and never acted on
    # here: the one place it decides anything is the driver's launch guard,
    # which refuses to open a build over a populated tree.
    items.append(PreflightItem(FACT, "kb-root", f"{kb_root_state(repo_root)} ({kb_root(repo_root)})"))
    found = _find_installer_target(repo_root, None)
    if found is None:
        items.append(
            PreflightItem(
                FACT,
                "runner-file",
                f"neither justfile nor Makefile — a seed creates a {runner_filename(DEFAULT_RUNNER)} "
                f"carrying the include line, or the one --runner names",
            )
        )
    else:
        items.append(PreflightItem(FACT, "runner-file", f"{found[1].name} (runner: {found[0]})"))
    # Bare existence, no inference: this is a KB-build artifact, unlike the
    # rest of the scratch tree, which says nothing about build state.
    build_scratch = scratch / SCRATCH_BUILD_DIRNAME
    items.append(
        PreflightItem(FACT, "scratch-kb-build", f"{'exists' if build_scratch.is_dir() else 'absent'}: {build_scratch}")
    )
    return items


def run_preflight(repo_root: Path) -> int:
    """Print the preflight report; return 0 iff no item failed."""
    items = preflight_report(repo_root)
    for item in items:
        print(item.line())
    failures = sum(1 for item in items if item.status == FAIL)
    if failures:
        to_stderr(f"[preflight] {FAIL}: {failures} blocking item(s) above.")
        return 1
    print(f"[preflight] {PASS}: environment ready.")
    return 0


# ---------------------------------------------------------------------------
# graph-init: the mechanical claim-graph metadata seed
#
# Every step below is deterministic and every step no-ops when its work is
# already done, so `graph-init` is the whole seed and is safe to re-run. It
# exists because executing this sequence by inference produces ordering,
# redirection, and file-assembly defects that a single command cannot have. The
# preflight suite above is fused into its entry rather than merely recommended
# beside it, so no prose ordering between the two verbs can produce a mis-seed.
#
# The document tree is a PRECONDITION, not an obstacle. The front end writes the
# tree first and this verb initialises claim-graph metadata over it, so a
# `kb-root/` with no tree in it is what has nothing to attach to and is the case
# that refuses.
# ---------------------------------------------------------------------------


def _seed_index_dir(repo_root: Path) -> tuple[bool, str]:
    """Create ``<kb_root>/.index/`` (and ``kb-root/`` with it) if absent."""
    path = index_dir(repo_root)
    if path.is_dir():
        return False, f"present: {path}"
    path.mkdir(parents=True)
    return True, f"created {path}"


def _seed_runner(repo_root: Path, runner: str | None) -> str | None:
    """The runner a seed installs into: the caller's, the detected file's, or the default.

    ``None`` — meaning "probe" — is returned whenever a runner file is already
    there, because naming a runner restricts the probe to that runner's file
    names: answering :data:`DEFAULT_RUNNER` for a repository carrying a justfile
    would create a Makefile beside it.
    """
    if runner is not None or _find_installer_target(repo_root, None) is not None:
        return runner
    return DEFAULT_RUNNER


#: This seeder's report-line tag, one word so a reader scanning a mixed
#: transcript can tell its lines from ``preflight``'s, which run inside them.
GRAPH_INIT_TAG = f"[{OP_GRAPH_INIT}]"

# The exit code for a kb-root holding no document tree. Not 0: no seed
# happened, and a caller that reads 0 as "spine ready" would be wrong. Not 2
# either: 2 means the environment is unfit (preflight failure, unresolvable
# root), all of them faults in this repository's own state and all of them
# repaired here. This is neither — the environment is sound and the missing
# thing is an upstream build product. Its own code is what tells a caller to run
# the document-graph front end rather than to go repair a repository that has
# nothing wrong with it.
EXIT_NO_DOCUMENT_TREE = 3


def graph_init_kb(repo_root: Path, runner: str | None = None) -> int:
    """Initialise the claim-graph spine over ``repo_root``'s document tree, verified green.

    Runs the ``preflight`` suite first, then — over a ``kb-root/`` that already
    holds a document tree — the index directory, the runner include line,
    refresh, and the same three gates the ``kb-verify`` target runs, minus one:
    ``verify_kb_metadata``'s frontmatter-presence check is excluded from this
    pass alone (``--skip-frontmatter-presence``), because kb_docgraph's tree
    carries no frontmatter by contract (SPEC.md, Document-Tree Contract point
    14) and this seed runs before any claim-graph stage stamps one. What "green"
    means here narrows accordingly: the spine is correctly installed over the
    tree that is there, not that the KB is complete — every other caller of
    ``verify_kb_metadata`` keeps the excluded check as a real gate. A repository
    carrying neither runner file gets :data:`DEFAULT_RUNNER`'s; ``runner`` names
    the other, and where a runner file exists the probe order decides as before.

    The seed is not undoable and needs no undo: it is its own op, its work
    stands wherever it got to, and re-running it is a no-op over whatever
    already landed.

    Exit codes: 0 seeded (or already fully seeded) and green; 1 refresh or
    verify failed; 2 preflight found a blocking item and nothing was seeded;
    :data:`EXIT_NO_DOCUMENT_TREE` ``kb-root/`` holds no document tree and was
    left untouched.
    """
    # Local import: refresh/verify import this module, so importing them at
    # module scope would be circular. They are the same code the runner
    # targets wrap — and those targets do not exist until the step below.
    from kb_tools import refresh_kb_metadata, verify_citations, verify_kb_metadata, verify_md_links

    # Fused, not recommended: an agent that skips `preflight` entirely still
    # cannot mis-seed, which is what makes the prose ordering between the two
    # verbs harmless. Same routine, same report lines, inside this op's output.
    if run_preflight(repo_root) != 0:
        to_stderr(f"{GRAPH_INIT_TAG} preflight FAILED — nothing seeded.")
        return 2

    # The precondition, not an obstacle: claim-graph metadata is initialised
    # over documents, so a kb-root with no tree in it has nothing to attach to.
    # Nothing below this branch runs — no refresh, no verify, no include line.
    if not document_tree_present(repo_root):
        to_stderr(
            f"{GRAPH_INIT_TAG} {KB_DIRNAME}/ holds no document tree — nothing written.\n"
            f"{GRAPH_INIT_TAG} {OP_GRAPH_INIT} initialises claim-graph metadata over a tree that "
            f"already exists; it has nothing to attach to over an empty {KB_DIRNAME}/.\n"
            f"{GRAPH_INIT_TAG} restore: build the document tree first — "
            f"'python3 -m kb_tools.kb_docgraph --source <volume-root> [--source ...] "
            f"[--bibliography <path> ...] --kb-root {kb_root(repo_root)}' — then re-run."
        )
        return EXIT_NO_DOCUMENT_TREE

    print(f"{GRAPH_INIT_TAG} repo root: {repo_root}")
    new_index, index_report = _seed_index_dir(repo_root)
    print(f"{GRAPH_INIT_TAG} index dir: {index_report}")
    seed_runner = _seed_runner(repo_root, runner)
    new_targets = not targets_installed(repo_root, seed_runner)
    print(f"{GRAPH_INIT_TAG} runner targets: {install_targets(repo_root, seed_runner)}")

    kb = str(kb_root(repo_root))
    print(f"{GRAPH_INIT_TAG} refresh:")
    if refresh_kb_metadata.main(["--kb-root", kb]) != 0:
        to_stderr(f"{GRAPH_INIT_TAG} refresh FAILED — spine not seeded.")
        return 1

    # Frontmatter presence is excluded here, and only here: SPEC.md's
    # Document-Tree Contract point 14 has kb_docgraph write no frontmatter
    # block, so the check fails by construction on the tree this seed runs
    # over, before any claim-graph stage has stamped one. Every other caller
    # of verify_kb_metadata (the runner target, phase-3a's gate) keeps it as a
    # real gate — this narrows what THIS pass claims, from "the KB is
    # complete" to "the spine is correctly installed over the tree that is
    # there," not what the check itself asserts elsewhere.
    print(f"{GRAPH_INIT_TAG} verify: (frontmatter presence excluded from this pass — see NOTE below)")
    links_rc = verify_md_links.main(["--root", str(repo_root)])
    metadata_rc = verify_kb_metadata.main(["--kb-root", kb, "--skip-frontmatter-presence"])
    citations_rc = verify_citations.main(["--kb-root", kb])
    if links_rc or metadata_rc or citations_rc:
        to_stderr(
            f"{GRAPH_INIT_TAG} verify FAILED (links rc={links_rc}, metadata rc={metadata_rc}, "
            f"citations rc={citations_rc})."
        )
        return 1

    scope = (
        "the spine is correctly installed over the tree that is there — not that the KB is "
        "complete, which needs frontmatter this seed does not write"
    )
    if new_index or new_targets:
        print(f"{GRAPH_INIT_TAG} claim-graph spine initialised; refresh and verify confirm {scope}.")
    else:
        print(f"{GRAPH_INIT_TAG} claim-graph spine was already fully initialised; refresh and verify confirm {scope}.")
    return 0


# ---------------------------------------------------------------------------
# The charter values file
#
# The write ops' transport, over one value: a TOML file of `[[entry]]` tables
# with prose in a `'''` literal block. A charter carries backslashes, quotes and
# dollar signs as a matter of course, and a literal block holds all of it with
# no escape grammar and without ever passing through a shell — which is the
# whole reason that transport exists, and it holds for a charter exactly as it
# holds for a rationale.
#
# The charter arrives as TEXT and never as a path: a caller handed a path
# chooses a location, and the one it chose was the scratch tree a restage wipes.
# Here the op owns the location and the caller owns nothing but the words.
# ---------------------------------------------------------------------------

#: The array-of-tables name, one across every values file this CLI reads.
ENTRY_TABLE = "entry"

#: The charter values file's whole vocabulary. One key, so a refusal names it
#: and the closed set rather than locating a line: a file this small states its
#: own line numbers.
CHARTER_KEY = "charter"

#: The flag ``show-confirmation`` reads that file through — the metadata ops'
#: transport, deliberately not their flag (:func:`_add_charter_values_option`).
CHARTER_VALUES_FLAG = "--charter-values"

#: ``start-build``'s own flag, naming a charter that already stands on disk.
#: Spelled once because two surfaces render it: the subparser that declares it
#: and the ``start`` card that tells a caller to run it.
CHARTER_FLAG = "--charter"


def read_charter_values(path: Path) -> str:
    """The charter text a values file carries.

    One build has one charter, so exactly one ``[[entry]]`` is read: a file
    carrying none, or several, is refused rather than joined or truncated. The
    text is returned as supplied but for a closing newline where it lacks one,
    since what it is about to become is a text file.

    Raises :class:`ValueError` naming the offending key, or :class:`OSError`
    when the file cannot be read; the caller turns those into the exit code.
    """
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: not a readable values file: {exc}") from exc

    unknown = sorted(set(document) - {ENTRY_TABLE})
    if unknown:
        raise ValueError(
            f"{path}: unknown top-level key(s) {', '.join(unknown)}; this file carries "
            f"[[{ENTRY_TABLE}]] tables and nothing else — the op travels on the command line"
        )
    entries = document.get(ENTRY_TABLE)
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise ValueError(
            f"{path}: one [[{ENTRY_TABLE}]] table is read and this file carries "
            f"{len(entries) if isinstance(entries, list) else 0}; one build has one charter"
        )
    entry = entries[0]
    unknown = sorted(set(entry) - {CHARTER_KEY})
    if unknown:
        raise ValueError(
            f"{path}: unknown key(s) {', '.join(unknown)} in [[{ENTRY_TABLE}]]; "
            f"this op's whole vocabulary is '{CHARTER_KEY}'"
        )
    text = entry.get(CHARTER_KEY)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"{path}: '{CHARTER_KEY}' is required and carries the build's charter in the words "
            f"it was given in; a charter saying nothing is refused rather than written"
        )
    return text if text.endswith("\n") else f"{text}\n"


# ---------------------------------------------------------------------------
# validate-build: the survey validator's front end
#
# One CLI: the validator joins this module's op set rather than growing a
# `kb_survey.__main__` beside it. Everything the verdict depends on lives in
# `kb_survey.validate`; this section reads two files, prints the findings it
# is handed, and returns the exit code that module computes. There is no
# severity argument, no warn-only mode and no tier here, because there is none
# there — and the no-downgrade test enumerates this option surface, every
# subcommand of it, to keep it that way.
# ---------------------------------------------------------------------------

# The environment is unfit: an unreadable or unparseable input, a kb-root that
# is not a directory. Distinct from exit 1, which means the checks ran and
# something FAILed — the same distinction `graph-init` draws between its 2 and its 1.
EXIT_ENVIRONMENT_UNFIT = 2


def run_validate(*, kb_root: Path) -> int:
    """Print the validator's findings; return its exit code.

    Checks a built KB tree's structure: every non-root document up-linked to
    the parent its own path names, every document reachable from the entry
    point by down-links, and no per-run survey id anywhere in the tree. The one
    argument cannot arm a measurement into a gate or disarm a gate into a
    measurement — the verdict is :func:`kb_survey.validate.exit_code`'s alone,
    computed from the findings and from nothing this function knows.

    **The tree is the whole input, and the report says so.** This op is handed a
    built KB and nothing that says what should have been built, so it passes
    ``paths=None`` and the tree-diff check reports itself inapplicable rather
    than passing: walking the tree to obtain a list and then comparing the tree
    against it is a check that cannot fail, and a ``PASS`` line for it would tell
    a reader three things were confirmed when two were. A caller that *does* hold
    a separately-derived path list — ``kb_docgraph``, which knows what it wrote —
    calls ``validate.validate_build`` directly and gets the diff for real.

    Exit codes: ``0`` no check ``FAIL``ed, ``1`` at least one ``FAIL``,
    :data:`EXIT_ENVIRONMENT_UNFIT` the tree could not be read — a kb-root that
    is not a directory, or a document that will not decode. The third rung is
    what keeps "the validator could not run" from reading as "the build is
    broken".
    """
    # Local import, for the reason the module docstring gives for kb_pipeline:
    # `kb_index_lib` imports this module and `kb_survey.validate` imports
    # `kb_index_lib`, so a module-scope import here would close the cycle.
    from kb_tools.kb_survey import validate

    try:
        findings = validate.validate_build(paths=None, kb_root=kb_root)
    except (OSError, ValueError) as exc:
        to_stderr(f"[{validate.TAG}] error: {exc}")
        return EXIT_ENVIRONMENT_UNFIT

    for finding in findings:
        print(finding.line())
    failures = sum(1 for finding in findings if finding.status == validate.FAIL)
    if failures:
        to_stderr(f"[{validate.TAG}] {validate.FAIL}: {failures} failing check(s) above.")
    else:
        print(f"[{validate.TAG}] {validate.PASS}: every check that ran passed.")
    return validate.exit_code(findings)


# ---------------------------------------------------------------------------
# The per-op adapters
#
# One per subcommand, bound by `set_defaults(handler=...)` and taking the parsed
# namespace. An adapter does two things and no more: it performs its own op's
# root discovery, and it calls the `run_*` function above with the arguments
# that function already takes. Root discovery lives here rather than in one
# if-chain because here it cannot be reached by the wrong op.
#
# `preflight`, `graph-init`, `show-status`, `show-stage-status`,
# `show-confirmation`, `start-build`, `advance-step` and
# `show-run-lock` anchor on `find_git_root()`: each reports on, or creates, an
# environment that may have no KB yet. The two record ops are there because the
# ledger is the commit trail: a build's first stages are recorded before anything
# has created `kb-root/` — the tree is the `document-graph` stage's own product,
# and the stage that opens the build precedes it. The lock read is there because
# the lock is the repository's, and its caller is often one that is about to
# remove `kb-root/` or has already.
# `install-targets`, `uninstall-targets`, the nine write ops and the one
# read-only metadata op anchor on `find_repo_root()`, which additionally
# requires the KB tree — the read-only op among them because a citation is
# checked against the KB it will live in.
# The survey op and the validator discover no root at all — they read the
# paths they are handed, which is how the driver invokes them from its
# scratch tree and how an agent invokes them against the path its assignment
# carries.
# ---------------------------------------------------------------------------


def _handle_preflight(args: argparse.Namespace) -> int:
    rc = run_preflight(find_git_root())
    if rc == 0:
        # The baton belongs to the standalone probe only: `graph-init` runs the
        # same suite, and there "next: run graph-init" would be absurd.
        print(f"[preflight] next: confirm with the user, then run {OP_GRAPH_INIT}")
    return rc


def _handle_graph_init(args: argparse.Namespace) -> int:
    rc = graph_init_kb(find_git_root(), args.runner)
    if rc == 0:
        # The seed's writes are uncommitted until a boundary commit sweeps them
        # up, so the baton names a record: a caller who stops here leaves the
        # spine standing in a worktree the next preflight refuses.
        print(f"{GRAPH_INIT_TAG} next: {OP_START_BUILD} [{CHARTER_FLAG} <path>]")
    return rc


def _handle_show_confirmation(args: argparse.Namespace) -> int:
    from kb_tools import kb_pipeline

    # The values grammar is read at the surface: what a caller may write in a
    # file is this module's, and the op takes text.
    try:
        charter = read_charter_values(args.charter_values) if args.charter_values is not None else None
    except (OSError, ValueError) as exc:
        to_stderr(f"[{OP_SHOW_CONFIRMATION}] error: {exc}")
        return EXIT_ENVIRONMENT_UNFIT
    # The git root, for `show-status`' reason: this renders before anything is
    # seeded, and kb-root/ not existing yet is one of the things it reports.
    return kb_pipeline.show_confirmation(find_git_root(), sources=args.source, charter=charter)


def _handle_install_targets(args: argparse.Namespace) -> int:
    print(install_targets(find_repo_root(), args.runner))
    return 0


def _handle_uninstall_targets(args: argparse.Namespace) -> int:
    print(uninstall_targets(find_repo_root(), args.runner))
    return 0


def _handle_show_status(args: argparse.Namespace) -> int:
    from kb_tools import kb_pipeline

    # The ledger lives in git rather than in the KB, so this anchors on the git
    # root alone: a fresh build renders its all-undone checklist at confirmation
    # time, before anything has created kb-root/.
    return kb_pipeline.run_op(find_git_root(), op=OP_SHOW_STATUS, stage=None, charter=None, note=None)


def _handle_show_stage_status(args: argparse.Namespace) -> int:
    from kb_tools import kb_pipeline

    # The git root for `show-status`' reason: the stage in flight is read from
    # the commit trail, and a stage may be asked about before kb-root/ exists.
    return kb_pipeline.run_op(find_git_root(), op=OP_SHOW_STAGE_STATUS, stage=args.stage, charter=None, note=None)


def _handle_start_build(args: argparse.Namespace) -> int:
    from kb_tools import kb_pipeline

    return kb_pipeline.run_op(find_git_root(), op=OP_START_BUILD, stage=None, charter=args.charter, note=None)


def _handle_advance_step(args: argparse.Namespace) -> int:
    from kb_tools import kb_pipeline

    return kb_pipeline.run_op(
        find_git_root(),
        op=OP_ADVANCE_STEP,
        stage=args.stage,
        charter=None,
        note=args.note,
        no_inference=args.no_inference,
    )


def _handle_validate_build(args: argparse.Namespace) -> int:
    return run_validate(kb_root=args.kb_root)


def _run_lock_field(value: object) -> str:
    """One reported field: empty where there is nothing to name, never more than a line.

    The format's whole promise to a shell is one ``key=value`` per line, and a
    lock file is a file on disk somebody may have written by hand — so a value
    carrying a newline or a tab is folded to spaces here rather than allowed to
    invent a line a caller would read as a key it does not know.
    """
    return "" if value is None else re.sub(r"\s", " ", str(value))


def _handle_show_run_lock(args: argparse.Namespace) -> int:
    """Print the run lock's state as ``key=value`` lines. The answer is the output.

    Read-only in the strong sense: it does not take the lock, does not clear a
    stale one, and writes nothing anywhere — breaking a stale lock is the
    driver's own recovery and stays there, so a caller learns the state here and
    acts on it itself.

    The judgement is ``runlog.lock_state``'s and is never restated: this reports
    what that function returns, which is why no pid test lives on this side of
    the boundary.

    ``find_git_root``, because that is the root the driver's own lock is anchored
    at — and because a caller asking about the lock is typically one that is
    about to delete ``kb-root/`` or has already, so requiring it would refuse
    exactly the question this op exists to answer.

    The import is local, as ``kb_pipeline``'s and ``kb_write.ops``' are:
    ``kb_driver`` imports this module, so the dependency resolves in that one
    direction at call time.
    """
    from kb_tools.kb_driver import runlog

    path = runlog.repo_lock_path(find_git_root())
    state = runlog.lock_state(path)
    reported = {"state": state.state, "pid": state.pid, "run_id": state.run_id, "started": state.started, "lock": path}
    for key in RUN_LOCK_KEYS:
        print(f"{key}={_run_lock_field(reported[key])}")
    return 0


def _handle_render_claim_graph(args: argparse.Namespace) -> int:
    """The renderer's adapter: the report to stderr, the document to its own path.

    Everything this op says goes to stderr and stdout stays empty: the op's
    product is a file, and a caller redirecting stdout must not collect a census
    it did not ask for.

    The semantics are ``kb_graph.ops``' — the exit code included — and the
    import is local, as ``kb_write.ops``' and ``kb_pipeline``'s are: that package
    reaches this module for the KB path vocabulary, so the dependency is only
    ever resolved in that direction, at call time.
    """
    from kb_tools.kb_graph import ops as graph_ops

    result = graph_ops.render(kb_root=kb_root(find_repo_root()), out=args.out, domain=args.domain)
    for line in result.lines():
        to_stderr(line)
    return result.exit_code


def _handle_write_op(args: argparse.Namespace) -> int:
    """One adapter for all nine write ops — root discovery, the call, the report.

    The op's semantics are ``kb_write.ops``' and there is no argparse in that
    module; this binds the surface shape to them and does nothing else. It
    is one adapter rather than nine because the nine differ only in which member
    of :data:`kb_write.ops.OPS` they run and in whether ``--create`` is
    admissible — and that second fact is already declared, on ``Op``, so reading
    it here is what keeps the surface and the semantics from disagreeing about
    which ops may create a register.

    ``args.op`` is the subcommand token, which the subparsers action stores
    under ``dest="op"`` — the same token the registry is keyed by.

    The import is local, exactly as ``kb_pipeline``'s is above: ``kb_write.ops``
    imports this module, so the dependency is only ever resolved in that one
    direction at call time.

    Nothing here decides an exit code: the op returns one of its three and it
    passes through :func:`main` untouched, as ``EXIT_NO_DOCUMENT_TREE`` does.
    """
    from kb_tools.kb_write import ops as write_ops

    op = write_ops.OPS[args.op]
    arguments: dict[str, object] = {"kb_root": kb_root(find_repo_root()), "values_file": args.values}
    if op.creates_register:
        arguments["create"] = args.create
    result = op.run(**arguments)
    for line in result.lines():
        print(line)
    return int(result.exit_code)


def _handle_render_citation(args: argparse.Namespace) -> int:
    """The read-only op's adapter: the citation to stdout, the report to stderr.

    **The split is the point.** This op's output is transcribed by its caller
    into a document, so stdout carries the composed citations and nothing else —
    one line each, in the order the values file named them, and nothing at all
    on a refusal. The report lines go to stderr, where a caller diagnosing the
    call reads them and a caller capturing the output does not.

    Its semantics are ``kb_write.ops``' like every other op's, and the import is
    local for the same reason: that package imports this module.
    """
    from kb_tools.kb_write import ops as write_ops

    result = write_ops.READ_OPS[args.op].run(kb_root=kb_root(find_repo_root()), values_file=args.values)
    for line in result.lines():
        to_stderr(line)
    for citation in result.printed:
        print(citation)
    return int(result.exit_code)


def _add_runner_option(parser: argparse.ArgumentParser) -> None:
    """``--runner``, declared on the four ops that touch the runner file and nowhere else.

    Declared per-op so ``preflight --runner just`` is a usage error rather than
    a silently accepted and ignored flag.
    """
    parser.add_argument(
        "--runner",
        choices=("just", "make"),
        help=f"target this runner's file regardless of probe order, and create it where the repo "
        f"has neither; a seed with no runner file and no --runner creates {DEFAULT_RUNNER}'s, "
        f"while install-targets refuses",
    )


def _add_values_option(parser: argparse.ArgumentParser) -> None:
    """``--values``, required by every write op and declared on each.

    A file rather than argv, on every op including the scalar-only ones: the
    ops' own signatures take a values file and nothing else, and one transport
    across the nine is also what lets
    a batch — a scoring wave's set of entries for one register — arrive as one
    all-or-nothing call.
    """
    parser.add_argument(
        VALUES_FLAG,
        type=Path,
        required=True,
        metavar="FILE",
        help="path to the TOML values file carrying this call's [[entry]] tables; prose fields "
        "travel in it rather than on the command line, and one file may carry a batch",
    )


def _add_charter_values_option(parser: argparse.ArgumentParser) -> None:
    """``--charter-values``, carrying the charter text, on ``show-confirmation``.

    The metadata ops' transport under a name of its own. The file's grammar is
    theirs — ``[[entry]]`` tables, prose in a ``'''`` literal block — but its
    vocabulary, its one-entry rule and its exit ladder are not, and
    :data:`VALUES_FLAG` advertises all three: an op spelling that flag promises
    a batch and the 7/8 write ladder, and this op has neither. It is also not
    ``start-build``'s :data:`CHARTER_FLAG`, which names a charter that already
    stands on disk; this carries the words of one, and writes nothing.
    """
    parser.add_argument(
        CHARTER_VALUES_FLAG,
        type=Path,
        metavar="FILE",
        help=f"path to the TOML values file whose one [[{ENTRY_TABLE}]] table carries "
        f"{CHARTER_KEY} = '''<the charter's own words>''', quoted back a line at a time for the "
        f"user to check. Write this file under {SCRATCH_DIRNAME}/ — it is the call's input, and "
        f"an uncommitted file anywhere else fails the clean-worktree check",
    )


def _add_create_option(parser: argparse.ArgumentParser) -> None:
    """``--create``, declared on the two register inserts and on no other op.

    Creation is never implicit: an insert naming a register that does not exist
    is a refusal naming the path unless the caller acknowledged the creation
    here, so a typo'd register path cannot succeed into a fresh file.
    Which ops admit it is read off ``kb_write.ops.Op.creates_register`` by the
    adapter, so the surface and the semantics cannot disagree about the set.
    """
    parser.add_argument(
        "--create",
        action="store_true",
        help="create the named register if it does not exist; without it a nonexistent register "
        "is a refusal naming the path, never a fresh file",
    )


def _write_op_help(writes: str) -> str:
    """One write op's help line: what it writes, then the contract shared by all nine.

    The per-op half names the file class the op writes into, because that is
    what a caller has to know before running it; the shared half is the exit
    ladder, which is what a caller branches on afterwards.
    """
    return (
        f"{writes} Values arrive in the {VALUES_FLAG} file. Exit 0 written, 7 the values were refused "
        f"and nothing was written, 8 a concurrent writer moved the file — re-run unchanged, never "
        f"re-author the values"
    )


def _values_vocabulary(op: str) -> str:
    """One op's closed value-key set, rendered FROM the vocabulary itself.

    **Derived, never typed.** A hand-written key list in a help string is a
    second declaration of a closed set that already has exactly one owner
    (``kb_write.values.OP_FIELDS``), and the two drift the first time a key is
    added — leaving ``--help`` confidently naming a vocabulary the op will
    refuse. Required keys are marked, because "which of these may I omit" is the
    other half of the question a caller opens ``--help`` to answer.

    Reached at parser-build time, so an op whose vocabulary changes gets a
    changed ``--help`` with no edit here, and ``test_kb_util_contract.py`` pins
    the description's key set equal to the row's.
    """
    from kb_tools.kb_write import values as write_values

    keys = [f"{field.name}*" if field.required else field.name for field in write_values.OP_FIELDS[op]]
    return f"Value keys: {', '.join(keys)} (* required). An unrecognized key is refused, named and located."


def _described(help_text: str, op: str) -> str:
    """An op's ``description=``: its help line, then its value vocabulary.

    The two differ on purpose. ``help=`` is the one-liner the subcommand list
    prints for every op at once, and nine key lists there would bury it;
    ``description=`` is what a caller reading ``<op> --help`` came for, and the
    vocabulary is the thing they are about to have to get right.
    """
    return f"{help_text} {_values_vocabulary(op)}"


def build_parser() -> argparse.ArgumentParser:
    """The CLI's whole surface, as one object: the top-level parser and every subparser.

    Named rather than built inline in :func:`main` so the no-downgrade guard
    can enumerate the surface mechanically instead of scraping ``--help``:
    the assertion that no flag, value or combination downgrades a validator
    ``FAIL`` is worth only as much as its ability to see every option. Under
    subparsers "every option" means recursing through the subparsers action's
    ``choices`` — the top-level ``_actions`` list holds only ``--help``,
    ``--version`` and the subparsers action itself, so a guard that stopped
    there would pass over a surface it cannot see.
    """
    # Local imports: both packages import this module at their top level, so the
    # dependency is only ever resolved in this direction at call time. They are
    # here rather than in a handler because what is wanted from them — the stage
    # ids, the domain sentinel — is help text, needed while the parser is built.
    from kb_tools import kb_pipeline
    from kb_tools.kb_graph import model as graph_model

    parser = argparse.ArgumentParser(
        prog="python3 -m kb_tools.kb_util",
        description="The KB build's mechanical front end. One subcommand per op, each owning its "
        "own options. The repo root is always derived from the working directory; the validator "
        "instead reads the paths it is given.",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    ops = parser.add_subparsers(dest="op", required=True, metavar="<op>")
    # Every subparser gets the same allow_abbrev=False: argparse's default
    # prefix matching would otherwise let a value-carrying option on one
    # subcommand silently resolve to a longer one sharing its prefix — a hidden
    # alias of exactly the kind this per-op surface exists to prevent. One
    # partial rather than the kwarg repeated at each add_parser call, so a
    # subcommand added later cannot forget it.
    add_parser = functools.partial(ops.add_parser, allow_abbrev=False)

    # Declaration order is the order a build meets these ops: the environment
    # and the spine, then the runner include line, then the ledger, then the
    # survey and its validator, then the renderer that draws what they built.

    preflight_help = (
        "print the build-environment report — git root, docent commands, scratch dir and its "
        "gitignore coverage, worktree cleanliness, plus non-gating kb-root and runner facts. "
        "Writes nothing but the scratch directory itself"
    )
    preflight = add_parser(OP_PREFLIGHT, help=preflight_help, description=preflight_help)
    preflight.set_defaults(handler=_handle_preflight)

    graph_init_help = (
        "initialise the claim-graph spine over the document tree already in kb-root/ — "
        "kb-root/.index/ and the one include line in the repo's runner "
        f"file, creating a {runner_filename(DEFAULT_RUNNER)} where the repo has neither "
        "runner file — then run refresh and verify, printing each step. "
        "Preflight runs first and nothing is written if it FAILs. Idempotent. Exit 0 initialised "
        "and green, 1 refresh or verify failed, 2 preflight blocked, 3 kb-root/ holds no document "
        "tree so there is nothing to initialise over"
    )
    graph_init = add_parser(OP_GRAPH_INIT, help=graph_init_help, description=graph_init_help)
    _add_runner_option(graph_init)
    graph_init.set_defaults(handler=_handle_graph_init)

    install_help = (
        "write the one KB include line into the repo's runner file (justfile or Makefile), " "and print what was done"
    )
    install = add_parser(OP_INSTALL_TARGETS, help=install_help, description=install_help)
    _add_runner_option(install)
    install.set_defaults(handler=_handle_install_targets)

    uninstall_help = (
        "remove the KB include line from the repo's runner file (justfile or Makefile), and " "print what was done"
    )
    uninstall = add_parser(OP_UNINSTALL_TARGETS, help=uninstall_help, description=uninstall_help)
    _add_runner_option(uninstall)
    uninstall.set_defaults(handler=_handle_uninstall_targets)

    show_confirmation_help = (
        "print the build's opening confirmation whole — the parse, the charter, the stage "
        "checklist, the per-project facts still unsettled with the default each takes, and the "
        "preflight report — as one message to put in front of the user unchanged. Reads "
        "everything and writes nothing but the scratch directory preflight creates. Exit 0 ready "
        "to confirm, 1 a blocking item stands in the way, 2 the root will not resolve"
    )
    show_confirmation = add_parser(
        OP_SHOW_CONFIRMATION, help=show_confirmation_help, description=show_confirmation_help
    )
    show_confirmation.add_argument(
        "--source",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="path to one source the build reads, repeated once per source; each is echoed "
        "resolved, and one that does not exist is a blocking item",
    )
    _add_charter_values_option(show_confirmation)
    show_confirmation.set_defaults(handler=_handle_show_confirmation)

    show_status_help = (
        "print the build's stage checklist and the current stage's action card, read back "
        "from the git commit trail. Read-only: writes nothing and exits 0 in every world-state"
    )
    show_status = add_parser(OP_SHOW_STATUS, help=show_status_help, description=show_status_help)
    show_status.set_defaults(handler=_handle_show_status)

    show_stage_status_help = (
        "print one stage's coverage — every unit it must cover, which are COVERED, which are "
        "MISSING and where each is looked for — for the stage in flight unless --stage names "
        "another. Writes nothing and records nothing. Exits 0 whatever it finds: a recorded "
        "stage, a stage not yet reached, and a stage whose units cannot be enumerated all "
        "report rather than refuse. Its one nonzero code is 2, a repo root that will not resolve"
    )
    show_stage_status = add_parser(
        OP_SHOW_STAGE_STATUS, help=show_stage_status_help, description=show_stage_status_help
    )
    show_stage_status.add_argument(
        "--stage",
        choices=kb_pipeline.STAGE_IDS,
        help="the stage to read; without it, the stage the build is in",
    )
    show_stage_status.set_defaults(handler=_handle_show_stage_status)

    start_build_help = (
        "record the build's first ledger boundary — a git add -A sweep, then a commit, so the "
        "spine seed and everything else uncommitted in the worktree land in it — and print the "
        "checklist. Refuses without committing if the build is already started"
    )
    start_build = add_parser(OP_START_BUILD, help=start_build_help, description=start_build_help)
    start_build.add_argument(
        CHARTER_FLAG,
        help=(
            "repo-relative path to the build charter, recorded in the start commit's body. Optional: "
            "a build with no charter records without one, and the commit's body then says so in "
            "words rather than standing empty"
        ),
    )
    start_build.set_defaults(handler=_handle_start_build)

    advance_step_help = (
        "record one stage's ledger boundary — the same git add -A sweep, then a commit — and "
        "print the checklist. At phase-3a it first writes the KB's readiness docs (kb-root/CLAUDE.md, "
        "CONVENTIONS.md) when absent; no other stage writes a file. Reports without committing if the "
        "stage is already recorded, or if a predecessor stage is not"
    )
    advance_step = add_parser(OP_ADVANCE_STEP, help=advance_step_help, description=advance_step_help)
    advance_step.add_argument(
        "--stage",
        required=True,
        choices=kb_pipeline.STAGE_IDS,
        help="the stage to record",
    )
    advance_step.add_argument(
        "--note",
        help="text appended to the stage's commit message as a body paragraph",
    )
    advance_step.add_argument(
        NO_INFERENCE_FLAG,
        action="store_true",
        help="this build spent no model call. It states what the build was and waives nothing: which "
        "coverage units that excuses is the stage table's own classification, not this flag's. A unit "
        "asserting the stage did its work has nothing to assert where the work was excluded and reports "
        "as vacuous; a unit asserting the state is valid for the next stage runs unchanged",
    )
    advance_step.set_defaults(handler=_handle_advance_step)

    show_run_lock_help = (
        "report whether a driver run holds this repository's run lock: live with the holder named, "
        "stale where the recorded holder is gone, absent where no lock file stands. Judged by the "
        "driver's own liveness test, so a caller never writes a second one. Writes nothing and "
        "clears nothing — a stale lock is reported, never broken. Its answer is its output and not "
        "its exit status: all three states exit 0, and its one nonzero code is 2, a repo root that "
        f"will not resolve. Output is one key=value line per field — {', '.join(RUN_LOCK_KEYS)}, in "
        "that order, all of them on every answer, the value empty where there is nobody to name — so "
        "a shell reads it with `while IFS='=' read -r key value` and needs neither a JSON tool nor a "
        "branch per state"
    )
    show_run_lock = add_parser(OP_SHOW_RUN_LOCK, help=show_run_lock_help, description=show_run_lock_help)
    show_run_lock.set_defaults(handler=_handle_show_run_lock)

    validate_build_help = (
        "walk a built KB tree and check its structure — every non-root document up-linked to its own "
        "parent, every document reachable from the entry point — and print the findings. Writes "
        "nothing. Exit 0 iff no check FAILed, 2 if the tree could not be read"
    )
    validate_build = add_parser(OP_VALIDATE_BUILD, help=validate_build_help, description=validate_build_help)
    validate_build.add_argument(
        "--kb-root",
        type=Path,
        required=True,
        help="path to the built KB tree; the op walks it and is told nothing else",
    )
    validate_build.set_defaults(handler=_handle_validate_build)

    render_claim_graph_help = (
        "render the claim graph to one standalone SVG sheet: a box per node carrying its id and "
        "title, hyperlinked to the node's definition in the KB, edges coloured by the strength they "
        "carry, height reading as derivation depth. Reads the derived index and writes exactly one "
        f"file — {KB_DIRNAME}/{CLAIM_GRAPH_FILENAME}, or the path --out names — and nothing else "
        f"anywhere: {KB_DIRNAME}/{INDEX_DIRNAME}/ is refresh's and is never written to. A defective "
        "graph is the picture's subject rather than an error, so a cycle, an edge naming an id no "
        "record carries, an isolated node and a disconnected component each draw as their own mark "
        "and still exit 0. Exit 0 the sheet was written, 2 the environment is unfit (no repo root, "
        f"{INDEX_DIRNAME}/ missing or unreadable, a record that will not parse, --out's parent "
        "absent), 7 refused with nothing written (--domain names no domain in the corpus, or --out "
        f"resolves inside {INDEX_DIRNAME}/)"
    )
    render_claim_graph = add_parser(
        OP_RENDER_CLAIM_GRAPH, help=render_claim_graph_help, description=render_claim_graph_help
    )
    render_claim_graph.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help=f"write the sheet here instead of {KB_DIRNAME}/{CLAIM_GRAPH_FILENAME}; the parent "
        f"directory must already exist, and a path resolving inside {INDEX_DIRNAME}/ is refused",
    )
    render_claim_graph.add_argument(
        "--domain",
        metavar="NAME",
        help="draw one domain's subgraph — every node attributed to it, plus every node one edge "
        "away in another domain drawn outline-only, because no edge is ever dropped from a sheet. A "
        "node's domain is the first path component of its canonical path, matched by exact string "
        f"equality and never by prefix; a node hosted at the KB root takes {graph_model.DOMAIN_ROOT!r}. "
        "Without it the whole corpus is drawn",
    )
    render_claim_graph.set_defaults(handler=_handle_render_claim_graph)

    # The metadata write ops, in declaration order: the births, then
    # the register updates, then those that write a document's own metadata.
    # Every one of them binds to the same adapter and declares argument
    # declarations and nothing else — no validation, no report line and no exit
    # code lives in a subparser body, because all three are `kb_write.ops`' and
    # a second copy here is a second answer.

    insert_claim_help = _write_op_help(
        "mint a clm- id and write its canonical entry into a claim-quality register, creating "
        "the register only under --create; prints the id it minted."
    )
    insert_claim = add_parser(
        OP_INSERT_CLAIM_ENTRY, help=insert_claim_help, description=_described(insert_claim_help, OP_INSERT_CLAIM_ENTRY)
    )
    _add_values_option(insert_claim)
    _add_create_option(insert_claim)
    insert_claim.set_defaults(handler=_handle_write_op)

    insert_support_help = _write_op_help(
        "mint a sup- id and write its canonical entry into a claim-quality register, creating "
        "the register only under --create; prints the id it minted."
    )
    insert_support = add_parser(
        OP_INSERT_SUPPORT_ENTRY,
        help=insert_support_help,
        description=_described(insert_support_help, OP_INSERT_SUPPORT_ENTRY),
    )
    _add_values_option(insert_support)
    _add_create_option(insert_support)
    insert_support.set_defaults(handler=_handle_write_op)

    insert_experiment_help = _write_op_help(
        "mint an exp- id and write its canonical declaration into its hosting document's "
        "kb-frontmatter block; prints the id it minted. Takes no --create: an experiment's host "
        "is authored prose this tool never brings into being."
    )
    insert_experiment = add_parser(
        OP_INSERT_EXPERIMENT_ENTRY,
        help=insert_experiment_help,
        description=_described(insert_experiment_help, OP_INSERT_EXPERIMENT_ENTRY),
    )
    _add_values_option(insert_experiment)
    insert_experiment.set_defaults(handler=_handle_write_op)

    insert_work_help = _write_op_help(
        "write an external work's canonical entry into a claim-quality register, creating the "
        "register only under --create. It mints nothing: the id is derived from the citation key "
        "the values carry, so a key already keyed by an entry is refused rather than doubled."
    )
    insert_work = add_parser(
        OP_INSERT_WORK_ENTRY, help=insert_work_help, description=_described(insert_work_help, OP_INSERT_WORK_ENTRY)
    )
    _add_values_option(insert_work)
    _add_create_option(insert_work)
    insert_work.set_defaults(handler=_handle_write_op)

    set_work_strength_help = _write_op_help(
        "rewrite one external work's authored standing line, leaving everything else in its entry "
        "byte-untouched. Nothing derives this value."
    )
    set_work_strength = add_parser(
        OP_SET_WORK_STRENGTH,
        help=set_work_strength_help,
        description=_described(set_work_strength_help, OP_SET_WORK_STRENGTH),
    )
    _add_values_option(set_work_strength)
    set_work_strength.set_defaults(handler=_handle_write_op)

    set_applicability_help = _write_op_help(
        "rewrite one claim-to-work applicability annotation on the depends-on bullet carrying it; "
        "a pairing with no bullet is refused rather than created."
    )
    set_applicability = add_parser(
        OP_SET_APPLICABILITY,
        help=set_applicability_help,
        description=_described(set_applicability_help, OP_SET_APPLICABILITY),
    )
    _add_values_option(set_applicability)
    set_applicability.set_defaults(handler=_handle_write_op)

    set_rigor_help = _write_op_help(
        "rewrite one register entry's authored rigor line — confidence for a claim, quality for "
        "a support, chosen from the id's own kind — leaving every derived line byte-untouched."
    )
    set_rigor = add_parser(OP_SET_RIGOR, help=set_rigor_help, description=_described(set_rigor_help, OP_SET_RIGOR))
    _add_values_option(set_rigor)
    set_rigor.set_defaults(handler=_handle_write_op)

    set_rationale_help = _write_op_help(
        "rewrite one register entry's rationale block whole, collapsed to the single paragraph "
        "the reader folds it back to; a value carrying a blank line is refused, never truncated."
    )
    set_rationale = add_parser(
        OP_SET_RATIONALE, help=set_rationale_help, description=_described(set_rationale_help, OP_SET_RATIONALE)
    )
    _add_values_option(set_rationale)
    set_rationale.set_defaults(handler=_handle_write_op)

    add_depends_on_help = _write_op_help(
        "add one register entry's outgoing-edge bullets — what it depends on, what it merely "
        "references, or both — leaving the rest of each list and its derived annotations alone; "
        "an unresolvable target is refused rather than written."
    )
    add_depends = add_parser(
        OP_ADD_DEPENDS_ON, help=add_depends_on_help, description=_described(add_depends_on_help, OP_ADD_DEPENDS_ON)
    )
    _add_values_option(add_depends)
    add_depends.set_defaults(handler=_handle_write_op)

    set_frontmatter_help = _write_op_help(
        "replace or insert a document's whole kb-frontmatter block, carrying its derived roll-ups "
        "over verbatim; the document's body prose is never touched."
    )
    set_frontmatter = add_parser(
        OP_SET_FRONTMATTER, help=set_frontmatter_help, description=_described(set_frontmatter_help, OP_SET_FRONTMATTER)
    )
    _add_values_option(set_frontmatter)
    set_frontmatter.set_defaults(handler=_handle_write_op)

    mark_claim_help = _write_op_help(
        "insert a claim's in-body marker into a document at the excerpt a locator value names; "
        "an absent or ambiguous locator is refused rather than placed by guess."
    )
    mark_claim = add_parser(
        OP_MARK_CLAIM_IN_LEAF, help=mark_claim_help, description=_described(mark_claim_help, OP_MARK_CLAIM_IN_LEAF)
    )
    _add_values_option(mark_claim)
    mark_claim.set_defaults(handler=_handle_write_op)

    set_fraction_help = _write_op_help(
        "rewrite one support-to-claim on-point fraction line in the hosting document's "
        "kb-frontmatter block; nothing else in the block moves."
    )
    set_fraction = add_parser(
        OP_SET_ON_POINT_FRACTION,
        help=set_fraction_help,
        description=_described(set_fraction_help, OP_SET_ON_POINT_FRACTION),
    )
    _add_values_option(set_fraction)
    set_fraction.set_defaults(handler=_handle_write_op)

    # The metadata surface's one read-only op. It takes --values like the nine
    # — an excerpt is prose, and prose travels in a file — and its help line
    # says what it PRINTS, because that output is its whole product: nothing on
    # disk changes, so a caller who does not capture stdout gets nothing.
    render_citation_help = (
        "print the sanctioned authority-citation string for a quotation — the exact "
        '["excerpt"](path#anchor) line, with the path spelled relative to the document it will be '
        "written into — after verifying the excerpt appears verbatim at that document's named "
        "section. Reads the KB; writes nothing anywhere. Exit 0 with the citation on stdout, 7 the "
        "values were refused and nothing was printed, 2 the environment is unfit"
    )
    render_citation = add_parser(
        OP_RENDER_CITATION, help=render_citation_help, description=_described(render_citation_help, OP_RENDER_CITATION)
    )
    _add_values_option(render_citation)
    render_citation.set_defaults(handler=_handle_render_citation)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse, then hand the namespace to the op's own adapter.

    The ``FileNotFoundError`` wrapper covers every op rather than only the
    root-discovering ones: ``RepoRootError`` is a ``FileNotFoundError`` is an
    ``OSError``, and every op that can reach the wrapper returns 2 for it
    either way.
    """
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except FileNotFoundError as exc:  # RepoRootError / RunnerFileError
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
