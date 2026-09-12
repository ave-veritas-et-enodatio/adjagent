"""Discovery and staging for the pytest-driven integration suite.

Every test in this tree drives the shipped pipeline over a corpus that is
**staged rather than committed**. The staging directory is under
``test-data/transient/``, which is gitignored, so a fresh checkout has no corpus
at all and every test here skips. That is the ordinary state of this tree, not a
failure: the recipe-driven integration tests in ``justfile`` work the same way.

The contract a staging recipe has to meet is the whole of what these fixtures
read:

* ``test-data/transient/integration-corpus/`` exists;
* it holds one or more ``.tex`` files that declare ``\\documentclass`` — those
  are the volume roots, and a ``.tex`` that does not declare one is treated as
  an ``\\input``-ed part rather than a root, so converting it standalone is not
  attempted;
* any ``.bib`` beside them is the bibliography the volumes resolve against.

Nothing else is assumed about the corpus. A test that needs a property a
particular corpus happens to have — a count of volumes, a declared environment
name, a title — does not belong here; it belongs in a fixture it can carry.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where a staging recipe puts the corpus under test. Gitignored, and absent on
#: a fresh checkout.
STAGED_CORPUS = REPO_ROOT / "kb-testing" / "test-data" / "transient" / "integration-corpus"

#: The one skip reason this tree uses. Generic on purpose: a corpus is staged or
#: it is not, and naming a particular one here would outlive it.
NO_CORPUS = "no corpus staged"


@pytest.fixture(scope="session")
def staged_corpus() -> Path:
    """The staged corpus directory, or a skip."""
    if not STAGED_CORPUS.is_dir() or not any(STAGED_CORPUS.glob("*.tex")):
        pytest.skip(NO_CORPUS)
    return STAGED_CORPUS


@pytest.fixture(scope="session")
def volume_roots(staged_corpus: Path) -> tuple[str, ...]:
    """Every ``.tex`` that declares a document class, in the order a build takes them.

    Read off the sources rather than configured: a part reached through
    ``\\input`` carries no ``\\documentclass``, and naming one as a root would
    convert its content twice — once standalone and once in place — with every
    per-volume check passing on both copies.
    """
    roots = sorted(
        path.name for path in staged_corpus.glob("*.tex") if "\\documentclass" in path.read_text(encoding="utf-8")
    )
    if not roots:
        pytest.skip(NO_CORPUS)
    return tuple(roots)


@pytest.fixture(scope="session")
def bibliography(staged_corpus: Path) -> str:
    """The one ``.bib`` beside the sources, or a skip for a corpus that has none."""
    found = sorted(path.name for path in staged_corpus.glob("*.bib"))
    if not found:
        pytest.skip("no bibliography beside the staged corpus")
    return found[0]
