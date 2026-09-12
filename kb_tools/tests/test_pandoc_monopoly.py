"""The pandoc monopoly, as a mechanical assertion rather than a convention.

``kb_tools/pandoc.py`` is the only module that knows how LaTeX gets read.
Nothing else in the package names the binary or spells one of its flags into an
argv, so replacing the reader — with the in-house parser of
an in-house replacement, or with anything else — is one
module's rewrite rather than the package's. This is the same property the
vendored parser's seam held, re-pointed at its successor; only the subject
changed.

**One shape, where the parser seam had two, and the second is absent for a
reason.** That parser was a *module*, so its monopoly could also be measured at
run time: a fresh process imported the driver-facing modules and reported which
``pylatexenc`` modules were resident. Pandoc is a *binary* reached by
subprocess. There is no import to observe, and a process that has not yet
converted anything looks the same whichever module would have converted it. The
property is entirely about what the sources name, so the sweep is the whole
instrument, and its teeth (:func:`test_the_sweep_reads_argv_and_not_prose`)
carry the weight the probe used to.

**What counts as naming it**: a string constant containing ``pandoc``, in any
case, that is not a docstring or other bare string expression. That draws the
line where it belongs. ``from kb_tools import pandoc`` is a module *using* the
seam and reaches no such constant; ``subprocess.run(["pandoc", ...])`` is a
second reader and does. A prose mention is not a dependency, and a sweep that
could not tell the two apart would need an allowlist on its first run.
"""

import ast
from pathlib import Path

import pytest

import kb_tools

#: The swept package, and the two subtrees inside it that are out of scope:
#: ``_vendor`` is third-party source this repository never edits, and the test
#: tree names the binary by construction — this module plants argv shapes below,
#: and ``test_pandoc.py`` drives the real one.
_SWEPT_ROOT = Path(kb_tools.__file__).resolve().parent
_REPO_ROOT = _SWEPT_ROOT.parent
_UNSWEPT_PARTS = frozenset({"_vendor", "tests"})

#: The seam, named by path rather than found by rule: it is the definition site,
#: so a rule that excused it would make the assertion say nothing.
SEAM = "kb_tools/pandoc.py"


def _named_in_argv(source: str) -> set[str]:
    """Every string constant in ``source`` that names pandoc, prose excluded.

    A bare string expression is a docstring or a comment by other means; every
    other string constant is data the module hands to something.
    """
    tree = ast.parse(source)
    prose = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "pandoc" in node.value.lower()
        and id(node) not in prose
    }


def _package_modules() -> dict[str, str]:
    return {
        path.relative_to(_REPO_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(_SWEPT_ROOT.rglob("*.py"))
        if not _UNSWEPT_PARTS & set(path.relative_to(_SWEPT_ROOT).parts)
    }


def test_only_the_seam_names_the_reader() -> None:
    """The monopoly as a set difference, over every module in the package."""
    naming = {path for path, source in _package_modules().items() if _named_in_argv(source)}

    assert naming == {SEAM}, sorted(naming)


def test_the_sweep_can_see() -> None:
    """A sweep over nothing passes vacuously; this is what says it did not.

    A floor rather than a count, so a module arriving or leaving does not edit
    this line — only a package that has quietly stopped being walked.
    """
    modules = _package_modules()

    assert len(modules) >= 30, sorted(modules)
    assert SEAM in modules
    assert _named_in_argv(modules[SEAM]), "the seam itself no longer names the binary — the sweep has no subject"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('BINARY = "pandoc"\n', True),
        ('subprocess.run(["pandoc", "-f", "latex"], check=True)\n', True),
        ('ARGV = ["Pandoc", "--citeproc"]\n', True),
        ('COMMAND = f"pandoc {flags}"\n', True),
        ("from kb_tools import pandoc\n\npandoc.to_ast(text, bibliographies=())\n", False),
        ('"""Reads the volume through the pandoc seam."""\n', False),
        ("import subprocess\n\nsubprocess.run([BINARY], check=True)\n", False),
    ],
    ids=["constant", "argv", "case", "fstring", "seam-caller", "docstring-only", "clean"],
)
def test_the_sweep_reads_argv_and_not_prose(source: str, expected: bool) -> None:
    """The sweep's teeth, both directions: every argv shape fires, a mention and a use do not."""
    assert bool(_named_in_argv(source)) is expected
