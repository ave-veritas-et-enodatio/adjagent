"""Ask a seat to do a thing, and get back what it said.

A **seat** is one of this agent set's definitions — ``kb-maintainer``,
``architect``, ``prompt-engineer`` — named by the stem of the ``.md`` file that
declares it. That is the whole of what this layer knows, and it is the whole of
the difference between it and :mod:`.claude` below it. It knows nothing about
claims, trees, registers or any other KB vocabulary; anything a seat is asked
about travels in ``prompt``, composed by whoever is asking.

**This function is the seam, not an abstraction over one.** A sibling that
dispatched to a differently-shaped harness would be a second function beside
this one, taking that harness's own arguments; the Claude-specific adaptation
of a seat — that a seat name is spelled as ``--agent <name>`` — stays inside
here rather than in a translation table nobody has two consumers for yet.

**It interprets nothing that comes back.** The response text is returned as it
arrived: envelope extraction, verdict parsing, JSON decoding and every other
reading of it belong to the caller that knows what it asked for.

**No ``--model``, ever, and not by discipline.** A seat's model pin lives in
its own frontmatter, and an explicit ``--model`` overrides it — so the pin is
authoritative only for as long as the flag is omitted. There is no parameter
here through which a command prefix could carry one, this layer spelling no
command at all.

Stdlib only.
"""

import logging
from pathlib import Path

from . import claude
from .claude import Outcome

_log = logging.getLogger(__name__)

#: Where a project's seat definitions sit, relative to the directory the call
#: runs in. A seat may also be defined for the user rather than the project,
#: under the same relative path below ``~``.
SEAT_DIRECTORY = Path(".claude") / "agents"


def _check_seat_name(seat: str) -> None:
    """A seat is named, never pathed. The definition's stem is the name.

    Catches the three spellings that reach the CLI as an unknown agent and come
    back as an opaque rejection: a path, a filename, and a flag.
    """
    complaint = ""
    if not seat.strip():
        complaint = "a seat name is empty"
    elif seat.endswith(".md") or "/" in seat:
        complaint = f"a seat is named by its definition's stem, not by its path: {seat!r}"
    elif seat.startswith("-"):
        complaint = f"a seat name cannot open with a dash: {seat!r}"
    if complaint:
        _log.error("seat ask refused: %s", complaint)
        raise ValueError(complaint)


def _warn_if_undefined(seat: str, *, cwd: Path) -> None:
    """Non-gating: say so when no definition for this seat is where one would be.

    A seat may legitimately be defined somewhere this cannot see, so an absence
    is a warning and never a refusal — but it is by far the likeliest cause of
    a call the CLI rejects, and the rejection says nothing about which name was
    wrong.
    """
    if any((base / SEAT_DIRECTORY / f"{seat}.md").is_file() for base in (cwd, Path.home())):
        return
    _log.warning("no definition for seat %r under %s in %s or the home directory", seat, SEAT_DIRECTORY, cwd)


def ask_seat(
    *,
    seat: str,
    prompt: str,
    cwd: Path | None = None,
    silence_seconds: float = claude.DEFAULT_SILENCE_SECONDS,
    total_seconds: float = claude.DEFAULT_TOTAL_SECONDS,
    capture_path: Path | None = None,
    invoker: claude.Invoker | None = None,
) -> tuple[str, Outcome]:
    """Pose ``prompt`` to ``seat`` and return what it said and how the call ended.

    Synchronous, and **not a single completion**: a seat's own definition may
    have it dispatch further, so this returns when the call stops speaking or a
    bound expires. Budget against ``total_seconds``.

    ``cwd`` defaults to the process's working directory and decides which
    project's ``.claude/agents/`` the seat resolves against. ``invoker``
    replaces the subprocess seam, which is how a caller tests this against no
    model at all.

    Raises :class:`ValueError` for an unusable seat name or prompt; every way a
    call can end, a command that could not be spawned included, is a returned
    :class:`~.claude.Outcome`.
    """
    _check_seat_name(seat)
    directory = Path.cwd() if cwd is None else cwd
    _warn_if_undefined(seat, cwd=directory)
    _log.info("asking seat %s in %s", seat, directory)

    return claude.call_claude(
        prompt=prompt,
        cwd=directory,
        agent=seat,
        silence_seconds=silence_seconds,
        total_seconds=total_seconds,
        capture_path=capture_path,
        invoker=invoker,
    )
