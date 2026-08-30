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
beside it. Nothing is ever derived from ``__file__``: the toolchain is
consumed through a symlink (``.claude/agents -> <tools repo>/agents``), so
``__file__`` resolves into the tools repo — the wrong repo.

Maintenance-command hints (:func:`refresh_cmd` / :func:`verify_cmd`) are
detected, not configured: a ``justfile`` at the detected root selects
``just <target>``, a ``Makefile`` selects ``make <target>`` (justfile wins
when both are present), and with neither the hint falls back to the raw
``python3 -m kb_tools.<module>`` invocation, which always works.

The module doubles as the runner-integration installer. Run as a CLI —
``python3 -m kb_tools.kb_util --install-targets | --uninstall-targets
[--runner just|make]`` — it manages the single include line through which a
consuming repo's runner (justfile or Makefile) gains the KB maintenance
targets. The target definitions themselves ship in ``runner-snippets/``
(``kb.just`` / ``kb.mk``) and are included live through the consumption
symlink, never copied into the consumer's file.

Stdlib only.
"""

import argparse
import sys
from pathlib import Path

from kb_tools import __version__

KB_DIRNAME = "kb-root"
INDEX_DIRNAME = ".index"
CLAIMS_FILENAME = "claims.jsonl"
SCHEMA_FILENAME = "SCHEMA.md"
INVARIANTS_FILENAME = "CLAUDE.md"

# Maintenance-target vocabulary. The consuming project's runner (justfile or
# Makefile) owns the target definitions; these mirror the target names so
# emitted remediation hints stay single-sourced.
TARGET_REFRESH = "refresh"
TARGET_VERIFY = "verify"

# Runner files probed at the repo root, in priority order: any justfile
# variant selects `just`, else any make variant selects `make`.
_JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")

# The always-works raw invocation, written repo-root-relative (a consuming
# repo links the toolchain in at `.claude/agents`).
_RAW_CMD = "PYTHONPATH=.claude/agents python3 -m kb_tools.{module}"

# Canonical installed lines — the one line the installer manages in a
# consuming repo's runner file; the target definitions live in this repo's
# runner-snippets/ and are included live through the consumption symlink.
# The non-fatal include forms (`-include` / `import?`, just >= 1.33) are
# deliberate: a broken symlink must degrade to missing KB targets, never
# break the consumer's whole runner.
INSTALL_LINE_MAKE = "-include .claude/agents/kb_tools/runner-snippets/kb.mk"
INSTALL_LINE_JUST = "import? '.claude/agents/kb_tools/runner-snippets/kb.just'"

_INSTALL_LINES = {"just": INSTALL_LINE_JUST, "make": INSTALL_LINE_MAKE}
_RUNNER_PROBE_NAMES = {"just": _JUSTFILE_NAMES, "make": _MAKEFILE_NAMES}
_RUNNER_CREATE_NAMES = {"just": "justfile", "make": "Makefile"}


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


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default: the cwd) to the consuming repo's root.

    The root is the first ancestor containing a ``.git`` entry — a directory,
    or the ``.git`` *file* a linked git worktree carries — and it must have
    the ``kb-root/`` content tree beside it.

    Raises :class:`RepoRootError` with an actionable message when the walk
    finds no ``.git``, or when the git toplevel has no ``kb-root/``.
    """
    start = Path.cwd() if start is None else start
    for parent in (start, *start.parents):
        if not (parent / ".git").exists():
            continue
        if (parent / KB_DIRNAME).is_dir():
            return parent
        raise RepoRootError(
            f"git toplevel found at {parent}, but it has no '{KB_DIRNAME}/' "
            f"directory beside .git. The KB tools operate on a repo whose "
            f"root contains the '{KB_DIRNAME}/' content tree; run them from "
            f"inside such a repo, or pass --kb-root explicitly."
        )
    raise RepoRootError(
        f"no .git entry found walking up from {start}; cannot locate the "
        f"repo root. Run the KB tools from inside the consuming repository "
        f"(any subdirectory works), or pass --kb-root explicitly."
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


def schema_md(repo_root: Path | None = None) -> Path:
    """The index format spec (``<index_dir>/SCHEMA.md``)."""
    return index_dir(repo_root) / SCHEMA_FILENAME


def invariants_md(repo_root: Path | None = None) -> Path:
    """The framework-node source (``<kb_root>/CLAUDE.md``)."""
    return kb_root(repo_root) / INVARIANTS_FILENAME


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
            f"# The line below pulls the KB maintenance targets in live through\n"
            f"# the .claude/agents symlink; add project recipes below it.\n"
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: manage the KB include line in a consumer's runner file."""
    parser = argparse.ArgumentParser(
        prog="python3 -m kb_tools.kb_util",
        description="Install or remove the one runner line that includes the KB maintenance targets "
        "(runner-snippets/kb.just or kb.mk) in the consuming repo's justfile or Makefile.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install-targets", action="store_true", help="install the KB include line")
    action.add_argument("--uninstall-targets", action="store_true", help="remove the KB include line")
    parser.add_argument(
        "--runner",
        choices=("just", "make"),
        help="target this runner's file regardless of probe order; on install, create it if missing",
    )
    args = parser.parse_args(argv)
    try:
        repo_root = find_repo_root()
        if args.install_targets:
            report = install_targets(repo_root, args.runner)
        else:
            report = uninstall_targets(repo_root, args.runner)
    except FileNotFoundError as exc:  # RepoRootError / RunnerFileError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
