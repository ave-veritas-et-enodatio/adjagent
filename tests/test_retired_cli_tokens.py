"""The retired CLI surface appears nowhere.

`kb_util`'s nine action flags became subcommands in one change, with no
compatibility shim and no deprecation window — the requirement was **no period
of two conventions on one tool**. A grep run once while making that change is
evidence for the change; it is not a guard for the repository. This file is the
guard: it re-runs the sweep on every test run, so a doc, a card, a recipe or a
brief that re-acquires an old spelling is caught here rather than by whoever
next runs the command it advertises. A retired *command* joins the same guard
for the same reason: `mint_claim_ids` left the agent-facing surface with mint
fusion, and it had no standing sweep until this one.

It reads the source trees, never `rendered/`: the templates are what a render is
a function of, and `rendered/` is a gitignored build product that may be absent
or stale (the same bound `test_manifest_views_in_definitions.py` states).

Out of scope, deliberately, and only these: `mad-design/` and `ROADMAP.md` are
design history — the plan that retired these tokens has to be able to name them
— `kb_tools/_vendor/` is third-party source this repository never edits, and
this file is the token list's one legitimate home.
"""

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The nine flags the subcommand conversion retired. Spelled here rather than
#: derived: these strings no longer exist anywhere in the code to import from,
#: which is the whole property being asserted.
RETIRED_TOKENS: tuple[str, ...] = (
    "--coordinator-internal-op",
    "--preflight",
    "--init",
    "--install-targets",
    "--uninstall-targets",
    "--survey-sources",
    "--validate-manifest",
    "--print-contract",
    "--render-manifest",
)

#: The retired *commands*, spelled as they were invoked. ``mint_claim_ids``
#: retired with mint fusion: insert takes values, mints implicitly, and returns
#: the id, so no agent is ever told to run the minter first. Both spellings are listed because both were in use — the
#: module invocation (`kb-maintainer.md`, `kb_driver/run.py`) and the filename
#: (`CONVENTIONS.md`'s tool table, `ARCHITECTURE.md`).
#:
#: The bare identifier is deliberately *not* a token. Naming a retired command
#: in prose about its retirement is legitimate and current
#: (`kb_write/ops.py:304`, `tests/test_mint_and_scan.py:334`); what may not
#: come back is an instruction to invoke it, and an invocation cannot be
#: written without one of these two spellings.
RETIRED_COMMANDS: tuple[str, ...] = (
    "kb_tools.mint_claim_ids",
    "mint_claim_ids.py",
)

#: Matched with a right boundary so `--init` does not answer for a future
#: `--initial-guess`, and so each token is reported under its own name. A left
#: boundary too, since the command spellings do not open with `--`: it admits
#: the `.` and the `-m ` an invocation puts in front of them while refusing a
#: longer identifier that merely ends in one.
_PATTERNS = {
    token: re.compile(rf"(?<![\w-]){re.escape(token)}(?![\w-])") for token in RETIRED_TOKENS + RETIRED_COMMANDS
}

#: The trees and files the sweep covers.
_ROOTS: tuple[Path, ...] = (
    _REPO_ROOT / "templates",
    _REPO_ROOT / "kb_tools",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "justfile",
    _REPO_ROOT / "kb-testing" / "justfile",
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "ARCHITECTURE.md",
)

#: Directories skipped wherever they appear under a root.
_SKIP_DIRS = frozenset({"_vendor", "__pycache__", ".pytest_cache", ".venv"})

#: This file. It sits inside a scanned tree and carries every retired token by
#: necessity, so it is excluded by path — the alternative, spelling the tokens
#: in pieces to hide them from the scanner, would leave the list unreadable to
#: the next person and unmatched against what the scanner actually looks for.
_SELF = Path(__file__).resolve()

#: Text filetypes the sweep reads. An allowlist rather than a denylist so a
#: binary fixture is never decoded: `read_text` on one raises
#: `UnicodeDecodeError`, which fails the sweep instead of reporting on it.
_TEXT_SUFFIXES = frozenset({".py", ".md", ".tmpl", ".toml", ".just", ".mk", ".sh", ".json", ".txt", ".cfg"})

#: Extensionless text files, admitted by name the way the list above admits by
#: extension. Admitting the empty suffix instead would readmit every
#: extensionless *binary* — `Path(".DS_Store").suffix` is `""` — and a Finder
#: dropping beside a fixture would then take the run down.
#:
#: :func:`test_the_sweep_reaches_the_files_that_carried_the_old_surface` holds
#: both lists against the files that must stay swept, so neither shrinks the
#: sweep toward nothing without saying so.
_TEXT_NAMES = frozenset({"justfile"})


def _files(roots: Iterable[Path]) -> list[Path]:
    """Every readable text file under ``roots``, in path order."""
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
            continue
        for path in root.rglob("*"):
            if not path.is_file() or not (path.suffix in _TEXT_SUFFIXES or path.name in _TEXT_NAMES):
                continue
            if _SKIP_DIRS & set(path.relative_to(root).parts):
                continue
            found.append(path)
    return sorted(path for path in found if path.resolve() != _SELF)


def _report(path: Path, number: int, token: str) -> str:
    where = path.relative_to(_REPO_ROOT) if path.is_relative_to(_REPO_ROOT) else path
    return f"{where}:{number}: {token}"


def scan(roots: Sequence[Path]) -> list[str]:
    """``<path>:<line>: <token>`` for every retired token found under ``roots``.

    ``roots`` is a parameter rather than the module constant so the teeth check
    below can aim this same scanner at a planted tree: a guard exercised only
    against the surface it already passes on is a guard exercised against
    nothing.
    """
    findings: list[str] = []
    for path in _files(roots):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            findings.extend(
                _report(path, number, token) for token, pattern in _PATTERNS.items() if pattern.search(line)
            )
    return findings


def test_no_retired_cli_token_survives_anywhere_in_the_source_trees() -> None:
    """The standing assertion: one convention on these tools, checkably."""
    assert scan(_ROOTS) == []


def test_the_sweep_reaches_the_files_that_carried_the_old_surface() -> None:
    """The enumeration's own check — an empty sweep would pass the assertion above.

    Each file named here held at least one retired token before the conversion,
    so a suffix allowlist or a skip rule that stopped reaching them would make
    the guard vacuous rather than green.

    The extensionless roots are checked against the name allowlist rather than
    against the sweep: a root named as a file is read whatever it is called, so
    only :data:`_TEXT_NAMES` decides whether a second copy of it — a `justfile`
    nested inside a scanned tree — is read too.
    """
    swept = set(_files(_ROOTS))

    extensionless_roots = {root.name for root in _ROOTS if root.is_file() and not root.suffix}
    assert extensionless_roots <= _TEXT_NAMES, extensionless_roots - _TEXT_NAMES

    for relative in (
        "kb_tools/kb_util.py",
        "kb_tools/kb_pipeline.py",
        "kb_tools/kb_driver/ledger.py",
        "kb_tools/kb_driver/steps.py",
        "kb_tools/CONVENTIONS.md",
        "kb_tools/runner-snippets/kb.just",
        "kb_tools/runner-snippets/kb.mk",
        "kb_tools/tests/test_kb_util.py",
        "templates/commands/kb-build.md.tmpl",
        "justfile",
        "kb-testing/justfile",
        "README.md",
        "ARCHITECTURE.md",
    ):
        assert _REPO_ROOT / relative in swept, relative


def test_the_sweep_has_teeth(tmp_path: Path) -> None:
    """A planted token in each retired spelling is caught, one finding per token."""
    retired = RETIRED_TOKENS + RETIRED_COMMANDS
    for number, token in enumerate(retired):
        (tmp_path / f"{number}-{token.strip('-')}.md").write_text(f"Run `{token}` first.\n", encoding="utf-8")

    findings = scan((tmp_path,))

    assert {finding.rsplit(": ", 1)[1] for finding in findings} == set(retired)


def test_prose_about_a_retired_command_is_not_an_invocation(tmp_path: Path) -> None:
    """The bare identifier stays sayable: a note that a command retired is not the command.

    Two current sites say exactly this — `kb_write/ops.py:304` and
    `tests/test_mint_and_scan.py:334` — and a guard that failed on them would
    be asking the repository to forget what it retired.
    """
    (tmp_path / "note.md").write_text("`mint_claim_ids` retired: insert mints implicitly.\n", encoding="utf-8")

    assert scan((tmp_path,)) == []


def test_a_longer_flag_is_not_matched_by_a_retired_prefix(tmp_path: Path) -> None:
    """The right boundary is load-bearing: `--init` must not answer for `--initial-guess`."""
    (tmp_path / "future.md").write_text("A future `--initial-guess` option would be its own flag.\n", encoding="utf-8")

    assert scan((tmp_path,)) == []
