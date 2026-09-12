"""No module-level name in the shipped `kb_tools` package is unreferenced.

A constant, function or class whose only occurrence in the repository is its own
definition is either dead — stranded when its last caller left — or a deliberate
exception the next reader has to be told about. Neither is visible to the
interpreter or to the suite: nothing imports a stranded name, so nothing breaks,
and it survives every green run until somebody happens to read the module. A
one-off script found `replay._ID_MARKER_RE` that way once, and its output was
read rather than obeyed, because a re-export and a test-only helper are
unreferenced for good reasons. This file is the standing form. The difference is
:data:`CLASSIFIED`: every orphan is named there with the reason it is one, and an
orphan that is not named fails.

**The corpus is the repository's own source, from git.** Not a directory walk:
this tree carries at least three byte-copies of `kb_tools/` — the installed
surface under `.claude/agents/`, the `rendered/` build product, and the
transient install targets under `kb-testing/test-data/` — and every one of them
is gitignored. A walk that reached them found *zero* orphans, because a stale
copy still holds the caller that the live tree deleted; that is a silent pass,
which is the worst failure a sweep can have. `git ls-files` plus untracked,
non-ignored files is exactly "the source this repository maintains", and it
excludes every copy by construction rather than by a skip list that the next
copy tree would defeat. :func:`test_the_corpus_holds_no_copy_of_the_swept_tree`
is what says so on each run.

**Three stated bounds.**

*What is swept*: `.py` under `kb_tools/`, less `_vendor/` (third-party source
this repository never edits) and `kb_tools/tests/` (a test function is called by
pytest and a fixture is data — sweeping them would fill the table with the test
tree and say nothing about the shipped package).

*What counts as a name*: a top-level `def`, `async def`, `class`, `x = ...` or
`x: T = ...` — the module's own namespace and not one line deeper. Names opening
with `__` are out: the interpreter calls those.

*What counts as a reference*: an occurrence of the identifier anywhere in the
corpus, prose and string literals included. So a name reached by string dispatch
counts as referenced, and so does one merely discussed in `ARCHITECTURE.md` —
both are things that break when it goes. The cost is collisions: a module-level
`build` would be masked by any other `build` in the repository. The sweep
therefore under-reports and never over-reports, which is the safe direction for
something whose findings a person has to adjudicate.
"""

import ast
import re
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from pathlib import Path
from types import MappingProxyType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The swept package, and the two subtrees inside it that are out of scope.
_SWEPT_ROOT = _REPO_ROOT / "kb_tools"
_UNSWEPT_PARTS = frozenset({"_vendor", "tests"})

#: Text filetypes the corpus reads (`""` is an extensionless text file). An
#: allowlist rather than a denylist, so a committed binary is never decoded.
_TEXT_SUFFIXES = frozenset({".py", ".md", ".tmpl", ".toml", ".just", ".mk", ".sh", ".json", ".txt", ".cfg", ""})

#: A Python identifier. Counting these is equivalent to a `\b<name>\b` scan for
#: identifier-shaped names, and is one pass over the corpus instead of one per
#: name.
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: ``(module path from the repo root, name) -> why it is legitimately
#: unreferenced``. Adding an entry is the deliberate act this sweep exists to
#: make visible; a reason that does not survive a reader's "so why is it here?"
#: is not a reason.
#:
#: A second kind of entry is temporary. A name the sweep finds dead is parked
#: here with its reason prefixed ``DELETION CANDIDATE``, which keeps the suite
#: green between the run that finds it and the commit that removes it. That is
#: the only thing the prefix buys — it is not a resting place, and the category
#: is empty in the steady state. They leave with their names:
#: :func:`test_no_classification_is_stale` fails on an entry whose name is gone,
#: so the deletion and its entry are one commit or neither.
CLASSIFIED: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        (
            "kb_tools/verify_md_links.py",
            "INTRA_ROOTS",
        ): "the documented surface of the intra/inter split, unreferenced by design and said to be so by the "
        "comment above it: the gate is repo-root containment, and this set records which top-level entries "
        "that covers for a reader deciding whether a link is inside the repository",
    }
)


@cache
def _corpus() -> tuple[Path, ...]:
    """Every text file this repository maintains: tracked, plus untracked and not ignored.

    Untracked-not-ignored is in because a file authored this minute is source
    the moment it exists, and a reference living only there would otherwise read
    as an orphan until someone staged it.

    This module is out, and has to be: :data:`CLASSIFIED` spells every name it
    excuses, so counting itself would make each entry its own second occurrence
    and empty the sweep.
    """
    listed = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    here = Path(__file__).resolve()
    paths = []
    for entry in listed.stdout.split("\0"):
        path = _REPO_ROOT / entry
        if entry and path.suffix in _TEXT_SUFFIXES and path.is_file() and "_vendor" not in path.parts:
            paths.append(path)
    return tuple(sorted(set(paths) - {here}))


def swept_modules(root: Path) -> list[Path]:
    """The `.py` files whose module-level names this sweep judges."""
    maintained = set(_corpus())
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not _UNSWEPT_PARTS & set(path.relative_to(root).parts) and path in maintained
    ]


def module_level_names(path: Path) -> list[str]:
    """Names bound in ``path``'s own namespace, dunders dropped."""
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [name for name in names if not name.startswith("__")]


def _where(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix() if path.is_relative_to(_REPO_ROOT) else path.name


def orphans(modules: Sequence[Path], corpus: Iterable[Path]) -> list[tuple[str, str]]:
    """``(module, name)`` for every module-level name whose only occurrence in ``corpus`` is its definition.

    Both sides are parameters so the same sweep runs over the shipped package
    and over a planted tree — which is what makes its teeth testable rather than
    assumed.
    """
    counts: Counter[str] = Counter()
    for path in corpus:
        counts.update(_IDENTIFIER.findall(path.read_text(encoding="utf-8")))
    return sorted(
        (_where(module), name) for module in modules for name in module_level_names(module) if counts[name] <= 1
    )


@cache
def _shipped_orphans() -> tuple[tuple[str, str], ...]:
    return tuple(orphans(swept_modules(_SWEPT_ROOT), _corpus()))


# --- the sweep can see ------------------------------------------------------


def test_the_sweep_can_see() -> None:
    """A sweep over nothing passes vacuously; this is what says it did not.

    Floors rather than fixtures: a fiftieth module and a two-hundredth name must
    not require editing this line, only a package that has quietly stopped being
    walked should.
    """
    modules = swept_modules(_SWEPT_ROOT)
    names = [name for module in modules for name in module_level_names(module)]

    assert len(_corpus()) >= 200
    assert len(modules) >= 40, [_where(module) for module in modules]
    assert len(names) >= 500


def test_the_sweep_reaches_the_modules_and_the_corpus_reaches_their_callers() -> None:
    """Named ends of both halves, so neither can shrink toward nothing unnoticed."""
    swept = {_where(module) for module in swept_modules(_SWEPT_ROOT)}
    for relative in ("kb_tools/kb_pipeline.py", "kb_tools/kb_driver/run.py", "kb_tools/kb_graph/svg.py"):
        assert relative in swept, relative

    corpus = {_where(path) for path in _corpus()}
    for relative in ("kb_tools/tests/test_kb_driver_run.py", "templates/shared-chunks.toml", "ARCHITECTURE.md"):
        assert relative in corpus, relative


def test_the_corpus_holds_no_copy_of_the_swept_tree() -> None:
    """The silent-pass guard.

    A second `kb_tools/` in the corpus supplies the caller the live tree
    deleted, and every orphan disappears with no test turning red. The copies
    this repository produces are all gitignored, so `ls-files` already excludes
    them; this fails if one is ever committed.
    """
    duplicated = [_where(path) for path in _corpus() if path.name == "kb_index_lib.py"]

    assert duplicated == ["kb_tools/kb_index_lib.py"]
    # And this file, whose table names every excused name, is not its own corpus.
    assert Path(__file__).resolve() not in _corpus()


# --- teeth ------------------------------------------------------------------


def test_a_planted_orphan_is_caught(tmp_path: Path) -> None:
    """Without this, a green run is equally consistent with a sweep that finds nothing ever."""
    module = tmp_path / "stranded.py"
    module.write_text(
        "__all__ = ['kept']\n"
        "STRANDED = 'nothing calls this'\n"
        "def kept() -> None:\n"
        "    pass\n"
        "class Adrift:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "caller.py").write_text("from stranded import kept\nkept()\n", encoding="utf-8")

    found = orphans([module], (module, tmp_path / "caller.py"))

    # `__all__` is a dunder and out of scope; `kept` has a caller. The bare
    # constant and the class are the finding.
    assert found == [("stranded.py", "Adrift"), ("stranded.py", "STRANDED")]


def test_a_name_mentioned_only_in_prose_is_referenced(tmp_path: Path) -> None:
    """The corpus is text, not imports: a constant a document names is not stranded."""
    module = tmp_path / "documented.py"
    module.write_text("TUNING_KNOB = 4\n", encoding="utf-8")
    (tmp_path / "ARCHITECTURE.md").write_text("Raising `TUNING_KNOB` widens the window.\n", encoding="utf-8")

    assert orphans([module], (module,)) == [("documented.py", "TUNING_KNOB")]
    assert orphans([module], (module, tmp_path / "ARCHITECTURE.md")) == []


# --- the standing claim -----------------------------------------------------


def test_every_module_level_orphan_is_classified() -> None:
    """The claim itself: no unexplained orphan ships."""
    unclassified = [f"{module}: {name}" for module, name in _shipped_orphans() if (module, name) not in CLASSIFIED]

    assert unclassified == [], (
        "a module-level name in kb_tools/ has no reference anywhere in the repository and no entry in this "
        "file. Read the definition: if its last caller is gone, delete it; if it is unreferenced for a "
        "reason — a re-export, a documented surface, a name a tool reaches by other means — add it to "
        "CLASSIFIED with that reason.\n" + "\n".join(unclassified)
    )


@pytest.mark.parametrize("key", sorted(CLASSIFIED))
def test_no_classification_is_stale(key: tuple[str, str]) -> None:
    """An entry outliving its name turns the table into an accumulating suppression list.

    Two ways to go stale, and the message separates them: the name is gone
    (deleted, and its entry outlived it), or it has gained a reference and is no
    longer an exception to anything. The first is also what forces a
    ``DELETION CANDIDATE`` out with the name it names.
    """
    module, name = key
    path = _REPO_ROOT / module

    assert path.is_file() and name in module_level_names(path), (
        f"{module} no longer defines {name!r} at module level. Delete the CLASSIFIED entry — its reason "
        f"describes code that is gone."
    )
    assert key in _shipped_orphans(), (
        f"{module}:{name} now has a reference elsewhere in the repository, so it is not an orphan and needs "
        f"no exception. Delete the CLASSIFIED entry."
    )
