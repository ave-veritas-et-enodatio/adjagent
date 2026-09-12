"""No agent definition is sent to the manifest file directly (V5.3.5, arch-W2).

``manifest.py``'s views reach a seat through brief slots, never through a seat
opening the JSON itself. This file is the check for that: it reads
``templates/`` rather than ``rendered/``, since the templates are the source a
render is a function of and ``rendered/`` is a gitignored build product that
may be absent or stale — the same bound ``test_traversal_sweep.py`` states.

There is no per-seat view assertion here: the one seat that worked from the
manifest by verb (``render-manifest section-index``) was ``kb-latex-specialist``,
retired with the LaTeX-reading survey pipeline it served.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _REPO_ROOT / "templates" / "agents"

#: ``definition -> the view it must be able to obtain``. Empty: no surviving
#: seat works from the manifest by verb. Kept as the shape a future seat's
#: entry would take, rather than removed with its last occupant.
_SEATS: dict[str, tuple[str, ...]] = {}

#: The manifest as a file on disk. Spelled here because it is spelled nowhere
#: else any more: the constant that used to hold it went with the manifest
#: requirement, and this sweep is a guard against the string coming back.
_MANIFEST_FILENAME = "survey-manifest.json"


def _seats_sent_to_the_file(definitions: tuple[tuple[str, str], ...]) -> list[str]:
    return [name for name, text in definitions if _MANIFEST_FILENAME in text]


def test_no_agent_definition_sends_a_seat_to_the_manifest_file() -> None:
    """The other half: naming the verb is worth nothing beside a surviving "read the JSON".

    Scoped to the artifact rather than to a phrasing — a definition naming the
    manifest's *filename* is one with a seat opening it, whatever verb it also
    mentions. Every definition, not only the two above: the next seat handed a
    manifest path gets the verb too, or this is a rule that held once.
    """
    definitions = tuple(
        (template.name, template.read_text(encoding="utf-8")) for template in sorted(_TEMPLATES.glob("*.md.tmpl"))
    )

    assert len(definitions) >= len(_SEATS)
    assert _seats_sent_to_the_file(definitions) == []


def test_the_file_guard_has_teeth() -> None:
    """A green sweep over a surface that never carried the string proves nothing on its own."""
    assert _seats_sent_to_the_file((("kb-planted.md.tmpl", f"Read `{_MANIFEST_FILENAME}` first.\n"),)) == [
        "kb-planted.md.tmpl"
    ]
