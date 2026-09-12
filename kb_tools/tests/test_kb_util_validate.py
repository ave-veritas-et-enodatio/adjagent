"""The ``validate-build`` op's CLI wiring, and the standing no-downgrade guard
over the option surface it joins.

The wiring half runs a fixture tree through the real argv path —
``kb_util.main([...])``, and once through ``python3 -m kb_tools.kb_util`` as a
subprocess, from a cwd that is not a repo, since the validator reads the tree it
is handed and discovers no root. ``--kb-root`` is the whole of what the op
takes: it walks that tree for the documents it checks, so there is no second
input to plant a disagreement in. The findings reach stdout in the
``[survey-validate] STATUS check detail`` convention, exit 0 iff no ``FAIL``,
and the third rung: a tree that could not be read is exit 2, not a verdict
about it.

**The planted violation here is a reachability one**, and has to be. This
caller holds no path list independent of the tree, so it passes ``paths=None``
and the tree-diff check reports itself inapplicable rather than passing — a
check that cannot fail must not read as one that was confirmed. What the op
still answers is whether every non-root document up-links to the parent its own
path names and whether every document is reachable from the entry point. A
caller holding a separately-derived path list — ``kb_docgraph``, which knows
what it wrote — calls ``kb_survey.validate.validate_build`` directly and gets
the diff for real (`test_kb_docgraph_build.py`); the pre-build mode over a
manifest and a derived skeleton is ``validate_skeleton``, unchanged and
`test_kb_survey_validate.py`'s.

The guard half is the row's real deliverable and is a standing assertion rather
than a spot check. It enumerates the shipped parser's option surface — the
top-level parser **and every subparser under it**, every flag, and for a flag
taking a value every choice it declares — and asserts across every single
assignment, every pair of them, and the maximal combination of all of them
that:

* a planted partition violation never exits 0 and never prints the success line, and
* the mirror: no assignment arms a shape *measurement* into a ``FAIL``.

**The enumeration recurses, and must.** Under subcommands the top-level parser's
``_actions`` holds only ``--help``, ``--version`` and the subparsers action, so a
guard reading it alone would pass vacuously over a surface it cannot see — every
real option now lives on a subparser. :func:`_parsers` walks
``_SubParsersAction.choices`` to reach them, and
:func:`test_the_guard_catches_a_downgrade_named_option_planted_on_a_subparser` is
its teeth check.

**The bound is stated because it is a bound.** Singles, pairs and the maximal
combination is not the full powerset. A downgrade needing exactly three specific
flags together, with every pair of them inert, is not a shape this CLI can express:
argparse binds each option independently, and there is no longer any cross-option
pairing code for a conditional with three-way arity to live in.
``--help`` and ``--version`` are excluded, and only they: both exit during parsing
without reaching an op, so a surface that could not carry them would have to
reimplement argparse. Abbreviations are not enumerated separately because argparse
resolves them to these same actions — the enumeration is over actions, which is
where behavior lives.
"""

import argparse
import inspect
import itertools
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from kb_tools import kb_util
from kb_tools.kb_survey import validate

# The directory holding the `kb_tools` package — the subprocess cases' PYTHONPATH,
# and never a way to derive a repo root.
_PKG_PARENT = Path(__file__).resolve().parents[2]

_DOMAIN = "volume-one"

# argparse's two terminating actions: they print and exit during parsing, so they
# never reach an op and cannot downgrade one.
_TERMINATING_FLAGS = frozenset({"--help", "--version"})

# Vocabulary no option on this parser may carry: there is no severity ladder,
# no warn-only mode, no tier, and no pre-authored bound anywhere in the validator,
# so there is nothing here for a flag by these names to control. A future flag
# tripping this is a deliberate change, not a lint failure.
_DOWNGRADE_WORDS = (
    "force",
    "warn",
    "severity",
    "ignore",
    "allow",
    "skip",
    "tier",
    "soft",
    "lenient",
    "override",
    "threshold",
    "bound",
    "limit",
    "max",
    "min",
    "nofail",
    "no-fail",
)


# ---------------------------------------------------------------------------
# Fixture builders: the built tree, which is the whole of this op's input
# ---------------------------------------------------------------------------

#: The entry point, one volume index, and a leaf each side of it. Typed out
#: rather than derived from anything: the op is handed a tree and nothing that
#: says what should have been built, so a fixture composing one from a second
#: source would be modelling an input this op does not take.
_PATHS = (
    validate.ENTRY_POINT_FILENAME,
    f"{_DOMAIN}/{validate.INDEX_FILENAME}",
    f"{_DOMAIN}/alpha.md",
    f"{_DOMAIN}/beta.md",
)

#: The planted violation: a document nothing links down to, carrying no up-link
#: of its own. It fails reachability in both directions at once, which is what
#: the walked path list leaves this op still able to catch.
_ORPHAN = f"{_DOMAIN}/stowaway.md"


def _doc(title: str, *, up: str | None = None, links: Sequence[str] = ()) -> str:
    lines = [f"[{validate.UPLINK_MARKER} {title} parent]({up})", ""] if up else []
    lines += [f"# {title}", ""]
    lines += [f"- [{target}]({target})" for target in links]
    return "\n".join(lines) + "\n"


def _built_tree(paths: Sequence[str]) -> dict[str, str]:
    """A well-formed tree over ``paths``: each document up-linked and listed by its parent.

    The up-link and the child list are computed from ``parent_index`` — the same
    relation the check walks — so the fixture cannot drift into a tree that is
    legal by one spelling of the parent rule and not by the other.
    """
    children: dict[str, list[str]] = {path: [] for path in paths}
    for path in paths:
        parent = validate.parent_index(path)
        if parent is not None:
            children[parent].append(path)
    documents = {}
    for path in paths:
        parent = validate.parent_index(path)
        up = None if parent is None else os.path.relpath(parent, os.path.dirname(path))
        links = [os.path.relpath(child, os.path.dirname(path)) for child in children[path]]
        documents[path] = _doc(path, up=up, links=links)
    return documents


def _write_tree(tmp_path: Path, documents: Mapping[str, str]) -> list[str]:
    """Write a tree under ``tmp_path/kb-root``; return the argv that validates it."""
    kb_root = tmp_path / "kb-root"
    for path, text in documents.items():
        file = kb_root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return [kb_util.OP_VALIDATE_BUILD, "--kb-root", str(kb_root)]


def _write_build_case(tmp_path: Path) -> list[str]:
    """A tree every check passes over."""
    return _write_tree(tmp_path, _built_tree(_PATHS))


def _write_orphan_case(tmp_path: Path) -> list[str]:
    """That tree plus :data:`_ORPHAN` — a document with no up-link nothing links to."""
    return _write_tree(tmp_path, {**_built_tree(_PATHS), _ORPHAN: "# Stowaway\n"})


# ---------------------------------------------------------------------------
# Running the CLI
# ---------------------------------------------------------------------------


def _cli(*argv: str) -> int:
    """``main`` through the real argv path; an argparse usage error is its exit code."""
    try:
        return kb_util.main(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2


def _finding_lines(out: str, status: str) -> list[str]:
    """Report lines of one status, excluding the trailing summary (which carries a colon)."""
    prefix = f"[{validate.TAG}] {status} "
    return [line for line in out.splitlines() if line.startswith(prefix)]


# ---------------------------------------------------------------------------
# Design-time wiring
# ---------------------------------------------------------------------------


def test_every_printed_line_follows_the_report_convention(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``[survey-validate] PASS|FAIL|FACT <check> <detail>``, one line per finding."""
    _cli(*_write_build_case(tmp_path))
    out = capsys.readouterr().out

    for line in out.splitlines():
        tag, status, *rest = line.split()
        assert tag == f"[{validate.TAG}]"
        assert status in {validate.PASS, validate.FAIL, validate.FACT, f"{validate.PASS}:"}
        assert rest


# ---------------------------------------------------------------------------
# Post-build wiring
# ---------------------------------------------------------------------------


def test_a_well_formed_tree_exits_zero_with_every_check_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both checks this caller can answer pass, and the third says it cannot be asked.

    The set assertions are what say the report claims exactly its own coverage:
    a run that reported only reachability would exit 0 just the same, and a
    tree-diff ``PASS`` here would be a third confirmation nobody performed.
    """
    code = _cli(*_write_build_case(tmp_path))
    out = capsys.readouterr().out

    assert code == validate.EXIT_OK, out
    assert {line.split()[2] for line in _finding_lines(out, validate.PASS)} == {
        validate.CHECK_REACHABILITY,
        validate.CHECK_ID_GUARD,
    }
    (tree_diff,) = [line for line in _finding_lines(out, validate.FACT) if validate.CHECK_TREE_DIFF in line]
    assert "not applicable" in tree_diff


def test_a_document_nothing_reaches_and_that_up_links_nowhere_exits_one_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The op's remaining teeth, in both directions of the reachability walk.

    Down: no chain of down-links from the entry point arrives at it. Up: it
    carries no up-link, so nothing walks up from it either. Each is its own
    finding, and the tree that holds one is not one this op passes.
    """
    code = _cli(*_write_orphan_case(tmp_path))
    out = capsys.readouterr().out

    assert code == validate.EXIT_VIOLATION
    faults = [line for line in _finding_lines(out, validate.FAIL) if _ORPHAN in line]
    assert [line for line in faults if "no up-link" in line]
    assert [line for line in faults if "unreachable" in line]


# ---------------------------------------------------------------------------
# The exit-2 rung: unfit inputs, distinct from a verdict
# ---------------------------------------------------------------------------


def _unfit(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> str:
    code = _cli(*argv)
    captured = capsys.readouterr()
    assert code == kb_util.EXIT_ENVIRONMENT_UNFIT
    assert _finding_lines(captured.out, validate.FAIL) == []
    assert captured.err.startswith(f"[{validate.TAG}] error: ")
    return captured.err


def test_a_kb_root_that_is_not_a_directory_is_unfit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The whole of the third rung now: the tree is the op's one input.

    A missing or unparseable manifest used to be the other two cases here, and
    they are gone with the argument that carried them — a tree that is not there
    is the only unreadable input this op can be handed.
    """
    argv = _write_build_case(tmp_path)
    argv[argv.index("--kb-root") + 1] = str(tmp_path / "never-built")

    assert "never-built" in _unfit(argv, capsys)


# ---------------------------------------------------------------------------
# The pairing shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([kb_util.OP_VALIDATE_BUILD], id="build-no-kb-root"),
        pytest.param([kb_util.OP_VALIDATE_BUILD, "--design-paths", "p"], id="the-retired-path-list"),
        pytest.param([kb_util.OP_VALIDATE_BUILD, "--kb-root", "r", "--manifest", "m"], id="the-retired-manifest"),
        pytest.param([kb_util.OP_VALIDATE_BUILD, kb_util.OP_PREFLIGHT], id="two-ops"),
    ],
)
def test_a_mispaired_invocation_is_a_usage_error_and_runs_nothing(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """Each of these is refused by the subparser's own declarations."""
    assert _cli(*argv) == 2
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# The delivered artifact, as shipped
# ---------------------------------------------------------------------------


def _module_run(cwd: Path, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_util", *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )


def test_the_module_entry_point_validates_a_built_tree_from_outside_any_repo(tmp_path: Path) -> None:
    """No root discovery: the validator walks the tree it is handed (the driver's case)."""
    result = _module_run(tmp_path, _write_build_case(tmp_path))

    assert result.returncode == validate.EXIT_OK, result.stderr
    assert _finding_lines(result.stdout, validate.PASS)


def test_the_module_entry_point_validates_a_built_tree_and_reports_a_violation(tmp_path: Path) -> None:
    result = _module_run(tmp_path, _write_orphan_case(tmp_path))

    assert result.returncode == validate.EXIT_VIOLATION
    assert [line for line in _finding_lines(result.stdout, validate.FAIL) if _ORPHAN in line]


# ---------------------------------------------------------------------------
# The no-downgrade guard, and its mirror
# ---------------------------------------------------------------------------


def _subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    """The one subparsers action on ``parser``, as the surface walk and the teeth check read it."""
    found = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    assert len(found) == 1, found
    return found[0]


def _parsers() -> list[argparse.ArgumentParser]:
    """The top-level parser and every subparser under it.

    **The whole point of the recursion**: under subcommands the top-level
    ``_actions`` list holds only the two terminating flags and the subparsers
    action itself, so every option this guard exists to enumerate lives one level
    down, in ``_SubParsersAction.choices``.
    """
    root = kb_util.build_parser()
    return [root, *_subparsers_action(root).choices.values()]


def _surface() -> list[tuple[str, ...]]:
    """Every flag-and-value assignment the shipped parser accepts, at any depth.

    Read off the parser object rather than off ``--help``, which is why
    :func:`kb_util.build_parser` exists: a guard that scraped help text would
    assert over a rendering of the surface instead of the surface. ``_actions``
    is argparse's only enumeration of it — the module offers no public accessor.

    Deduplicated, and only that: an option several subcommands both declare
    (``--kb-root``, ``--runner``) is one assignment to try, not one per
    declaring op, and the pair enumeration below is quadratic in this list's
    length.
    """
    assignments: dict[tuple[str, ...], None] = {}
    for parser in _parsers():
        for action in parser._actions:
            flags = [flag for flag in action.option_strings if flag.startswith("--")]
            if not flags or flags[0] in _TERMINATING_FLAGS:
                continue
            flag = flags[0]
            if action.nargs == 0:
                assignments[(flag,)] = None
            elif action.choices:
                assignments.update(((flag, str(choice)), None) for choice in action.choices)
            else:
                assignments.update({(flag, "a-value-nothing-reads"): None, (flag, ""): None})
    return list(assignments)


def _downgrade_named(assignments: Sequence[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The assignments whose flag name carries downgrade vocabulary.

    Extracted so the standing assertion and its teeth check run the same
    predicate: a guard proven only against the surface it already passes on is
    a guard proven against nothing.
    """
    flagged = []
    for assignment in assignments:
        name = assignment[0].removeprefix("--").replace("-", "")
        if [word for word in _DOWNGRADE_WORDS if word.replace("-", "") in name]:
            flagged.append(assignment)
    return flagged


def _combinations() -> list[tuple[str, ...]]:
    """Every single assignment, every pair, and all of them at once, as argv tails."""
    surface = _surface()
    groups: list[tuple[tuple[str, ...], ...]] = [
        (),
        *((assignment,) for assignment in surface),
        *itertools.combinations(surface, 2),
        tuple(surface),
    ]
    return [tuple(itertools.chain.from_iterable(group)) for group in groups]


def test_the_enumerated_surface_holds_every_flag_the_validator_ops_use() -> None:
    """The guard is only as good as the enumeration; this is the enumeration's own check.

    Every flag named here lives on a *subparser*, so this fails outright if the
    walk stops at the top-level parser.
    """
    flags = {assignment[0] for assignment in _surface()}

    assert "--kb-root" in flags
    # And the retired one is gone from the whole surface, not merely from this
    # op: `--manifest` was declared nowhere else, so a survivor anywhere is a
    # second door onto an input the validator no longer takes.
    assert "--manifest" not in flags
    assert "--help" not in flags and "--version" not in flags


def test_the_enumeration_reaches_every_subcommand_the_cli_declares() -> None:
    """No op may be invisible to the guard, whatever it declares or does not."""
    walked = _parsers()

    assert {parser.prog.split()[-1] for parser in walked[1:]} == set(_subparsers_action(walked[0]).choices)
    # Not vacuous: the eleven build ops, the claim-graph renderer, the twelve
    # write ops and the one read-only metadata op are all there, and the
    # top-level parser with them.
    assert len(walked) == 26
    subcommands = {parser.prog.split()[-1] for parser in walked[1:]}
    assert set(kb_util.WRITE_OPS) < subcommands
    assert set(kb_util.READ_OPS) < subcommands


def test_no_option_on_this_cli_carries_downgrade_vocabulary() -> None:
    """No severity ladder, no warn-only mode and no bound to name."""
    assert _downgrade_named(_surface()) == []


@pytest.mark.parametrize("op", [kb_util.OP_VALIDATE_BUILD, kb_util.OP_INSERT_CLAIM_ENTRY, kb_util.OP_SET_RIGOR])
def test_the_guard_catches_a_downgrade_named_option_planted_on_a_subparser(
    monkeypatch: pytest.MonkeyPatch, op: str
) -> None:
    """The teeth check, and the one the subcommand conversion made necessary.

    A downgrade dial would now be declared on the op it downgrades — a
    subparser — where the pre-conversion guard could not see it at all. Planting
    one there and watching the standing assertion fail is what proves the
    enumeration recurses; passing on the shipped surface proves nothing, since
    an enumeration returning ``[]`` passes it too.

    Planted on a write op as well as on the validator, because the write ops
    joined the surface after the guard was written and "the walk reaches them"
    is a claim about this enumeration, not about argparse in general — a
    ``--force`` or a ``--skip-census`` on an op that writes into a KB is exactly
    the dial this guard exists to catch.
    """
    real = kb_util.build_parser

    def planted() -> argparse.ArgumentParser:
        parser = real()
        _subparsers_action(parser).choices[op].add_argument("--severity-floor")
        return parser

    monkeypatch.setattr(kb_util, "build_parser", planted)

    assert {assignment[0] for assignment in _downgrade_named(_surface())} == {"--severity-floor"}


def test_no_flag_value_or_combination_makes_a_planted_violation_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The standing guard: the planted violation reaches exit 1, or nothing ran.

    **Two outcomes, and the guard names both.** ``!= EXIT_OK`` was the
    weaker assertion it looks like: exit 1 is also what an uncaught exception
    gives a process, so a validator that crashed on its way to the checks
    satisfied it while reporting nothing — and exit 1 is the driver's
    gate-red signal, so the crash would have spent a revision round. The
    outcomes this base admits are exactly: the checks ran and the verdict is the
    violation's, or the argv was refused before any check ran and the run says
    so on the third rung. A report with no verdict, or a verdict with no report,
    is neither.
    """
    base = _write_orphan_case(tmp_path)
    assert _cli(*base) == validate.EXIT_VIOLATION
    capsys.readouterr()

    for tail in _combinations():
        argv = [*base, *tail]
        code = _cli(*argv)
        out = capsys.readouterr().out
        reported = any(_finding_lines(out, status) for status in (validate.FAIL, validate.PASS, validate.FACT))

        assert f"[{validate.TAG}] {validate.PASS}: " not in out, tail
        if reported:
            assert _finding_lines(out, validate.FAIL), tail
            assert code == validate.EXIT_VIOLATION, tail
        else:
            assert code == kb_util.EXIT_ENVIRONMENT_UNFIT, tail


def test_the_op_entry_point_takes_no_argument_that_could_change_a_verdict() -> None:
    """The CLI's own half of the (r) assertion: one input, no default, no mode dial."""
    parameters = inspect.signature(kb_util.run_validate).parameters

    assert [(name, parameter.kind) for name, parameter in parameters.items()] == [
        ("kb_root", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters.values())
