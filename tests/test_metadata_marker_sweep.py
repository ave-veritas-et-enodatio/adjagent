"""No file hand-composes a metadata marker.

`kb_write.render` is the only writer of a `<!-- id:`, `<!-- kb-frontmatter` or
`<!-- claim-quality:` opener. The prose sweep that removed the freehand sites
is evidence for that change; it is not a guard for the repository, and its
finding list is not something a reviewer re-checks on every future edit. This
file is the guard, in two halves:

* **The template surface** — every file under `templates/` and
  `kb_tools/installed/`. The third tree carrying brief text,
  `kb_tools/kb_driver/prompt-templates/`, is `prompt_templates.lint`'s half:
  the tokens ride in ``steps.TEMPLATE_PROHIBITIONS`` and
  ``test_kb_driver_prompt_templates.test_the_shipped_template_directory_lints_clean``
  runs the lint over the shipped templates. The patterns below are that same
  compiled map, sliced — one token vocabulary, two walks.
* **The code surface** — every `.py` under `kb_tools/` outside
  `kb_write/`. "`render.py` is the only module composing a marker" is a
  negative constraint, and a negative constraint with no instrument is a wish.

**Token-bearing files are allowlisted by name, with a reason**
(:data:`MARKER_ALLOWLIST`) — five readers of the format, plus the module that
spells the tokens for the lint that matches them.
Composing a marker and reading one are indistinguishable to a line scanner —
`CANONICAL_ID = re.compile(r"<!-- id: ...")` and a docstring describing the
band it parses both carry the literal — so the discrimination is a human
judgement recorded once per file rather than a cleverness re-derived per line.
Two properties keep the list honest: it is per-file and therefore visible in a
diff, and
:func:`test_every_allowlisted_reader_still_carries_a_marker_token` fails on an
entry whose file no longer carries a token, so an entry cannot outlive its
reason. What it does *not* buy: a new composition added to an allowlisted
reader is not caught here. That file's own review is what catches it, and
widening the list is the cost of not maintaining a per-line ledger.

Out of scope, deliberately: `kb_tools/_vendor/` (third-party source this
repository never edits), `kb_tools/tests/` — fixtures *and* test modules —
because a register fixture and a test's expected output are data, not writers;
`mad-design/` and `ROADMAP.md`, which are not under either root; and
`rendered/`, a gitignored build product (the same bound
`test_retired_cli_tokens.py` states).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

import pytest

from kb_tools.kb_driver import prompt_templates, steps

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The marker tokens and the patterns the brief lint matches them with — sliced
#: from the driver's own map rather than recompiled, so the two halves of the
#: lint can never come to disagree about what a marker looks like.
_PATTERNS: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {token: steps.TEMPLATE_PROHIBITIONS[token] for token in steps.METADATA_MARKER_TOKENS}
)

#: The template half's trees.
_TEMPLATE_ROOTS: tuple[Path, ...] = (_REPO_ROOT / "templates", _REPO_ROOT / "kb_tools" / "installed")

#: The code half's tree.
_CODE_ROOT = _REPO_ROOT / "kb_tools"

#: Directories skipped wherever they appear under a scanned root.
_SKIP_DIRS = frozenset({"kb_write", "_vendor", "tests", "__pycache__", ".pytest_cache", ".venv"})

#: Text filetypes the template sweep reads (`""` is an extensionless text
#: file). An allowlist rather than a denylist so a binary asset is never
#: decoded; :func:`test_the_template_sweep_reaches_the_shipped_surfaces` is
#: what keeps it from quietly shrinking the sweep toward nothing.
_TEXT_SUFFIXES = frozenset({".py", ".md", ".tmpl", ".toml", ".just", ".mk", ".sh", ".json", ".txt", ".cfg", ""})

#: Modules that carry a marker token legitimately — five that read the format
#: and the one that spells the tokens for the lint that matches them. Keyed by
#: path relative to ``kb_tools/``.
MARKER_ALLOWLIST: Mapping[str, str] = MappingProxyType(
    {
        "kb_index_lib.py": (
            "the register/leaf parser — the marker regexes and the docstrings naming what each one reads"
        ),
        "verify_kb_metadata.py": (
            "the verifier — `CANONICAL_ID`/`TIER2_INLINE` match markers, and a failure names the marker a "
            "malformed section is missing"
        ),
        "verify_citations.py": "module docstring naming the tier-2 marker the citation check reads",
        "refresh_kb_metadata.py": (
            "docstring of the leaf-references rewrite, describing the band between an entry's id marker and its "
            "`### Quality` heading (the opener this module used to compose is gone)"
        ),
        "kb_cmd/index.py": "prose comment describing the frontmatter block the reader parses",
        "kb_driver/steps.py": (
            "`METADATA_MARKER_TOKENS` — the vocabulary this sweep matches with, which cannot be spelled in pieces "
            "without becoming unreadable to the next person and unmatched against what the scanner looks for"
        ),
    }
)


def _text_files(roots: Iterable[Path]) -> list[Path]:
    """Every readable text file under ``roots``, in path order."""
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
                continue
            if _SKIP_DIRS & set(path.relative_to(root).parts):
                continue
            found.append(path)
    return sorted(found)


def _modules(root: Path) -> list[Path]:
    """Every `.py` under ``root`` the code half covers, allowlisted readers dropped."""
    kept: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if _SKIP_DIRS & set(relative.parts) or relative.as_posix() in MARKER_ALLOWLIST:
            continue
        kept.append(path)
    return kept


def _findings(paths: Sequence[Path]) -> list[str]:
    """``<path>:<line>: <token>`` for every marker token on a line of ``paths``."""
    findings: list[str] = []
    for path in paths:
        where = path.relative_to(_REPO_ROOT) if path.is_relative_to(_REPO_ROOT) else path
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            findings += [f"{where}:{number}: {token}" for token, pattern in _PATTERNS.items() if pattern.search(line)]
    return findings


def scan_templates(roots: Sequence[Path]) -> list[str]:
    """The template half. ``roots`` is a parameter so the teeth check can aim it at a plant."""
    return _findings(_text_files(roots))


def scan_modules(root: Path) -> list[str]:
    """The code half. ``root`` is a parameter for the same reason."""
    return _findings(_modules(root))


# --- the token vocabulary ----------------------------------------------------


def test_the_marker_tokens_are_the_three_openers() -> None:
    """Three openers. Spelled here so a fourth — or a lost third — is a decision, not a drift."""
    assert set(steps.METADATA_MARKER_TOKENS) == {"<!-- id:", "<!-- kb-frontmatter", "<!-- claim-quality:"}
    assert set(_PATTERNS) == set(steps.METADATA_MARKER_TOKENS)


# --- half one: the template surface ------------------------------------------


def test_no_template_or_installed_file_composes_a_metadata_marker() -> None:
    """The standing assertion over the two trees `prompt_templates.lint` does not walk."""
    assert scan_templates(_TEMPLATE_ROOTS) == []


def test_the_template_sweep_reaches_the_shipped_surfaces() -> None:
    """The walk's own check — an empty sweep would pass the assertion above."""
    swept = set(_text_files(_TEMPLATE_ROOTS))

    for relative in (
        "templates/agents/kb-maintainer.md.tmpl",
        "templates/commands/kb-build.md.tmpl",
        "templates/shared-chunks.toml",
        "kb_tools/installed/CONVENTIONS.md.tmpl",
        "kb_tools/installed/CLAUDE.md.tmpl",
    ):
        assert _REPO_ROOT / relative in swept, relative


@pytest.mark.parametrize("token", steps.METADATA_MARKER_TOKENS)
def test_a_planted_marker_in_a_template_tree_is_caught(tmp_path: Path, token: str) -> None:
    (tmp_path / "leak.md.tmpl").write_text(f"Write {token} clm-xxxxxx --> under the heading.\n", encoding="utf-8")

    assert [finding for finding in scan_templates((tmp_path,)) if finding.endswith(token)]


def test_a_planted_marker_in_a_brief_template_fails_the_lint(tmp_path: Path) -> None:
    """The `prompt_templates.lint` half of the sweep, with teeth."""
    (tmp_path / "leak.single.tmpl").write_text("Then write `<!-- id: clm-xxxxxx -->` yourself.\n", encoding="utf-8")

    findings = prompt_templates.lint(prompt_templates.template_paths(tmp_path), prohibited=steps.TEMPLATE_PROHIBITIONS)

    assert [finding for finding in findings if finding.startswith("leak.single.tmpl:1:") and "<!-- id:" in finding]


# --- half two: the code surface --------------------------------------------


def test_no_kb_tools_module_outside_kb_write_composes_a_metadata_marker() -> None:
    """The instrument: `render.py` is the only composer, and this is what says so."""
    assert scan_modules(_CODE_ROOT) == []


def test_the_module_sweep_reaches_the_modules_that_could_compose_one() -> None:
    """Anti-vacuity for the walk: named modules that are *not* allowlisted are in it.

    The allowlisted files are reached by
    :func:`test_every_allowlisted_reader_still_carries_a_marker_token`, which
    reads each one; what this asserts is that the walk that has to catch a new
    composer still reaches ordinary modules.
    """
    swept = set(_modules(_CODE_ROOT))

    for relative in ("kb_cmd/cli.py", "kb_driver/prompt_templates.py", "kb_driver/replay.py", "kb_pipeline.py"):
        assert _CODE_ROOT / relative in swept, relative


def test_every_allowlisted_reader_still_carries_a_marker_token() -> None:
    """The allowlist cannot rot: an entry whose file lost its token has lost its reason.

    Without this, a name stays on the list after the code that earned it is
    gone, and the exemption quietly widens to whatever that path becomes next.
    """
    stale = []
    for relative, reason in MARKER_ALLOWLIST.items():
        path = _CODE_ROOT / relative
        if not path.is_file() or not _findings((path,)):
            stale.append(f"{relative}: {reason}")

    assert stale == []


@pytest.mark.parametrize("token", steps.METADATA_MARKER_TOKENS)
def test_a_planted_marker_in_a_module_is_caught(tmp_path: Path, token: str) -> None:
    """The code half's teeth, one plant per token."""
    (tmp_path / "composer.py").write_text(f'BLOCK = "{token} kind: leaf -->"\n', encoding="utf-8")

    assert [finding for finding in scan_modules(tmp_path) if finding.endswith(token)]


def test_the_composer_and_the_test_tree_are_out_of_the_module_sweep(tmp_path: Path) -> None:
    """The three exemptions that are structural rather than named, asserted rather than assumed."""
    for skipped in ("kb_write", "tests", "_vendor"):
        directory = tmp_path / skipped
        directory.mkdir()
        (directory / "writer.py").write_text('OPEN = "<!-- kb-frontmatter"\n', encoding="utf-8")

    assert scan_modules(tmp_path) == []
