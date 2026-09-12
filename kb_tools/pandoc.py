"""The one module that knows how LaTeX is read.

Everything the package learns from a LaTeX source comes back through these four
calls, and nothing else in it names the ``pandoc`` binary or builds an argv for
it. Replacing the reader is then one module's rewrite rather than the package's
goal;
``tests/test_pandoc_monopoly.py`` is what says the monopoly still holds on each
run.

The binary is assumed present on the system. This module neither installs nor
vendors it, and an absent one is :exc:`PandocMissingError` naming what to do —
never the ``FileNotFoundError`` subprocess would otherwise raise from the middle
of a build.

**Three of pandoc's behaviours are load-bearing here, and each loses content at
exit 0 with at most a stderr line to show for it. All three measured against
pandoc 3.11:**

* **``-s`` on the Markdown route is required, not cosmetic.** Without it the
  whole metadata channel — abstract, title, author, date — is dropped and stderr
  is empty. With it those arrive as a YAML block at the head of the output,
  already rendered by the same document-wide pass as the body. So
  :func:`to_markdown` sets it and offers no say in the matter.
* **``--bibliography`` is repeatable and pandoc merges what it is given**, so
  ``bibliographies`` is a set of files rather than a choice among them. Citeproc
  renders only the entries a document actually cites, so a file in the set
  carrying nothing this source cites contributes nothing to the output — an
  uncited entry is inert, not a competing answer. Where one key is defined
  twice, the first file given wins, which is what makes the **order** the
  caller's to fix: a caller that discovered its files rather than being handed
  them sorts them, so two runs over one corpus resolve the same way.
* **``--citeproc`` and the citation-key flag are one decision.** ``--citeproc``
  used to be *what makes a citation reach the output at all*, so its absence
  deleted every citation silently — a bib-less corpus built a document with no
  citations in it and nothing said so. The filter now owns citation rendering
  (``kb_docgraph/authored_blocks.lua``), so a citation survives either way and
  the cost of no bibliography is resolution, not existence. What the condition
  still decides is the **third** flag: :data:`CITATION_KEYS_ONLY_FLAG` is passed
  exactly when ``--citeproc`` is not, because it tells the filter to render the
  key itself where nothing will resolve it. One condition, three flags, decided
  in one place — a caller that set two of them and forgot the third would be
  back to silent deletion.
* **``\\input`` resolves against the invoking process's working directory** —
  not the source file's directory, and ``--resource-path`` does not cover it. A
  volume root converted from anywhere else loses its own parts to a stderr
  warning. ``working_directory`` is how a caller says where the volume lives;
  filter and bibliography paths are resolved here against *this* process's
  directory, so setting it cannot move them.

Pandoc writing to stderr on an otherwise successful run is how the third of
those, and an unresolvable citation key, announce themselves — so any such
output is logged at WARNING rather than dropped. Judging it belongs to the
caller wherever a caller can judge it: a build's partition check is what turns
lost content into a failure.

**One warning is judged here instead, because no caller can judge it.** An
include pandoc could not load takes its file's content out of the parse
entirely, so the AST, the rendering and the tree all agree about a document
that never had it and every check downstream compares two halves of the same
absence. There is nothing left to compare, and the stderr line is the whole of
the evidence — so :exc:`PandocIncludeError` is raised at exit 0, on the text.

Stdlib only.
"""

import json
import logging
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: The binary, spelled once for the whole package.
BINARY = "pandoc"

#: Pandoc's own exit code for a bibliography it could not parse. An exit code and
#: not a message match: the message is prose pandoc is free to reword, and what
#: reads it has to tell this failure from an unparseable *source*, which arrives
#: on the same channel and exits 64. Measured against pandoc 3.11.
_BIBLIOGRAPHY_EXIT = 25

#: Pandoc's own warning for a file it was told to include and could not read.
#: There is no exit code behind it — the reader carries on with the file's
#: content simply absent and exits 0 — so this is a message match, and the
#: opposite decision from :data:`_BIBLIOGRAPHY_EXIT` above for the opposite
#: reason: there, an exit code existed and prose would have been the fragile
#: reading; here, prose is the only reading there is.
#:
#: Narrowed as far as the message allows, so a reworded one stops matching
#: rather than matching something else: the ``[WARNING]`` level, pandoc's whole
#: sentence, and a source position of its own shape closing the line. The
#: target is greedy against that fixed tail, so a filename carrying " at " does
#: not split the match early.
#:
#: **The position's filename is optional because this module's sources arrive on
#: stdin**, which pandoc has no name for: the same warning reads ``at main.tex
#: line 112 column 23`` for a file argument and ``at line 112 column 23`` here,
#: and a pattern demanding the name would match nothing on the only route the
#: package uses. Measured against pandoc 3.11.
_UNLOADABLE_INCLUDE_RE = re.compile(
    r"^\[WARNING\] Could not load include file (?P<target>.+) at (?P<origin>(?:\S+ )?line \d+ column \d+)$",
    re.MULTILINE,
)

#: The metadata flag telling the Lua filter to render each citation's key
#: itself, set exactly when no ``--citeproc`` will run to render anything.
#: Spelled here and in ``kb_docgraph/authored_blocks.lua``, and held equal by
#: ``tests/test_pandoc.py`` — it is one fact crossing a process boundary, and
#: the two sides cannot import each other.
CITATION_KEYS_ONLY_FLAG = "kb-citation-keys-only"

#: The metadata key carrying a ``\newtheorem`` internal-name → display-name
#: mapping to the filter, which needs it and cannot obtain it: pandoc consumes
#: the preamble, so the declaration reaches no filter — not as a ``RawBlock``,
#: not in ``meta``, not as an attribute on the Div it classes. The caller reads
#: it off the source, which is the same shape :data:`CITATION_KEYS_ONLY_FLAG`
#: has and for the same reason.
#:
#: **The value is JSON**, not a delimited list: an internal name may carry a
#: ``*`` (``\newtheorem{lem*}``) and a display name a space (``Test example``),
#: so a hand-rolled separator would be a parser with a corpus that breaks it.
#: Also spelled in ``kb_docgraph/authored_blocks.lua`` and held equal by
#: ``tests/test_pandoc.py``.
THEOREM_NAMES_FLAG = "kb-theorem-names"


class PandocError(RuntimeError):
    """Any failure invoking pandoc — one type for a caller whose answer is to stop."""


class PandocBibliographyError(PandocError):
    """A ``--bibliography`` file could not be parsed.

    Its own type because it is the one pandoc failure a caller may legitimately
    continue past. A bibliography that cannot be read is the input SPEC.md point
    10 says a corpus may not have, so the same degradation applies — citations
    reach the tree by their keys and the volume gains no reference list. Every
    other failure, an unparseable source above all, is still a stop, and a caller
    separating the two on message text would swallow the second the day pandoc
    rewords the first.

    **The exit code says a file failed and not which one.** Where several were
    passed, pandoc's own complaint — carried in this exception's message and
    logged by the caller that recovers — is the only thing naming the offender.
    """


class PandocIncludeError(PandocError):
    """A file the source told pandoc to include, and pandoc could not load.

    Its own type because it is the one failure here that arrives at **exit 0**:
    pandoc reports it as a warning, converts everything else, and returns
    success, so a caller reading the return code sees a clean run over a
    document missing whatever those files held. Eight ``\\input`` lines lost this
    way built a two-document tree of 108 words from an eight-section paper, and
    every check the build runs passed over it.

    **Matched on the message text, and that is unavoidable rather than
    preferred.** The content never entered the parse, so it is in neither the
    AST nor the rendering nor the tree: every comparison downstream has the same
    hole on both sides and reads clean. The stderr line is the only record that
    the file existed at all, which is why this is keyed on prose while
    :exc:`PandocBibliographyError`, which has an exit code to key on, is keyed on
    that instead.

    What the caller is owed, and what this carries, is the list: each file
    pandoc could not load and the line of the source that named it.
    """


class PandocMissingError(PandocError):
    """The binary is not on PATH.

    Its own type because the remediation differs in kind — install a program,
    rather than fix a document or a filter — and a caller acting on that
    difference should not have to match on message text.
    """


def version() -> str:
    """The version of the binary behind this seam, e.g. ``"3.11"``.

    A build records it, because a tree that differs from the last one is
    diagnosable only if what produced each is on record.
    """
    reported = _run(["--version"], stdin="").split("\n", 1)[0].split()
    if len(reported) < 2 or reported[0] != BINARY:
        raise PandocError(f"unrecognized `{BINARY} --version` output: {reported!r}")
    return reported[1]


def to_ast(
    latex: str,
    *,
    bibliographies: Sequence[Path],
    filters: Sequence[Path] = (),
    theorem_names: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> dict[str, Any]:
    """Parse ``latex`` and return pandoc's own JSON AST.

    The AST is word-granular — every word a ``Str``, every gap a ``Space`` — so
    it is walked in code and never phrase-matched as text.
    """
    rendered = _run(
        [
            *_conversion_argv(bibliographies=bibliographies, filters=filters, theorem_names=theorem_names),
            "-t",
            "json",
        ],
        stdin=latex,
        working_directory=working_directory,
    )
    return json.loads(rendered)


def to_markdown(
    latex: str,
    *,
    bibliographies: Sequence[Path],
    filters: Sequence[Path] = (),
    theorem_names: Mapping[str, str] | None = None,
    working_directory: Path | None = None,
) -> str:
    """Render ``latex`` to GitHub-flavoured Markdown, metadata block included."""
    return _run(
        [
            *_conversion_argv(bibliographies=bibliographies, filters=filters, theorem_names=theorem_names),
            "-s",
            "-t",
            "gfm",
        ],
        stdin=latex,
        working_directory=working_directory,
    )


def from_ast(node: Mapping[str, Any]) -> str:
    """Render a pandoc document back to GitHub-flavoured Markdown.

    Deliberately off the build path: a build renders each volume once and whole,
    so that theorem numbering, citation rendering and the bibliography are
    computed across the document rather than once per slice. This direction is
    here for a later stage that constructs a document of its own.

    ``node`` is a whole document — the shape :func:`to_ast` returns — because
    ``-f json`` accepts nothing else.
    """
    return _run(["-f", "json", "-t", "gfm"], stdin=json.dumps(node))


def _conversion_argv(
    *, bibliographies: Sequence[Path], filters: Sequence[Path], theorem_names: Mapping[str, str] | None = None
) -> list[str]:
    """The arguments both conversion directions share: the reader, the filters, the citation decision.

    **The filters precede ``--citeproc``, and the order is load-bearing.**
    Pandoc applies filters in command-line order and treats ``--citeproc`` as
    one of them, so a filter named first sees ``Cite`` nodes before citeproc
    resolves them — which is what lets the filter mark every citation with its
    own key whatever citeproc then does to it. Measured against pandoc 3.11
    rather than read off the manual.

    **Every bibliography is passed, in the order given.** Pandoc takes the flag
    repeatedly and merges the files into one collection; the order is preserved
    because a key two files define resolves to the first.

    **Both directions get the same arguments, and for the theorem names that is
    a requirement rather than a convenience.** A build compares the AST's own
    text against the rendering's, so a label word carried on one side and not
    the other reads as content the reader dropped.
    """
    argv = ["-f", "latex"]
    argv += [f"--lua-filter={path.resolve()}" for path in filters]
    if theorem_names:
        # Sorted so a rebuild of one source produces one argv (SPEC.md,
        # Corpus Invariants, determinism).
        argv += ["-M", f"{THEOREM_NAMES_FLAG}={json.dumps(theorem_names, sort_keys=True)}"]
    if not bibliographies:
        argv += ["-M", f"{CITATION_KEYS_ONLY_FLAG}=1"]
    else:
        argv += ["--citeproc", *(f"--bibliography={path.resolve()}" for path in bibliographies)]
    return argv


def _unloadable_includes(complaint: str) -> list[tuple[str, str]]:
    """Each include pandoc reported it could not load, as the target and where the source named it."""
    return [(found.group("target"), found.group("origin")) for found in _UNLOADABLE_INCLUDE_RE.finditer(complaint)]


def _run(arguments: Sequence[str], *, stdin: str, working_directory: Path | None = None) -> str:
    """Invoke pandoc and return its stdout, every failure shape raised as a :exc:`PandocError`.

    Three of them: the binary absent, a non-zero exit, and — at exit 0 — an
    include that did not load, which is a failure however successful the return
    code says the run was.
    """
    argv = [BINARY, *arguments]
    try:
        completed = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=working_directory,
            check=False,
        )
    except FileNotFoundError as absent:
        raise PandocMissingError(
            f"`{BINARY}` is not on PATH. This toolchain reads LaTeX by invoking it and does not install or "
            f"vendor it — install pandoc (https://pandoc.org/installing.html) and re-run."
        ) from absent

    complaint = completed.stderr.strip()
    if completed.returncode != 0:
        failure = PandocBibliographyError if completed.returncode == _BIBLIOGRAPHY_EXIT else PandocError
        raise failure(f"`{' '.join(argv)}` exited {completed.returncode}: {complaint or '<no stderr>'}")
    unloadable = _unloadable_includes(complaint)
    if unloadable:
        listing = "\n".join(f"  {target} — named at {origin} of the source" for target, origin in unloadable)
        raise PandocIncludeError(
            f"`{' '.join(argv)}` exited 0 without loading {len(unloadable)} of the files this source told it "
            f"to include, so everything each one held is absent from the parse, from the rendering, and from "
            f"anything built out of them:\n{listing}\n"
            f"Nothing downstream can see this: the content entered no artifact, so every check compares one "
            f"copy of the gap against another and passes."
        )
    if complaint:
        _log.warning("%s exited 0 but wrote to stderr: %s", BINARY, complaint)
    return completed.stdout
