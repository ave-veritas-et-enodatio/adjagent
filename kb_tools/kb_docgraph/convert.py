"""Stage 1 — one volume root in, the two artifacts stage 2 needs out.

**A volume root is a top-level LaTeX document, and the caller says which files
are.** Nothing here discovers: no directory walk, no glob, no inference about
which of a directory's ``.tex`` files are roots. A file reached by ``\\input`` is
not a volume and is never named on its own — it arrives as part of the volume
that includes it. Naming one alongside its includer converts the same content
twice and no per-volume check can see it, because neither volume can see the
other's copy. The reverse costs more than duplication: a fragment converted alone
renders its includer's macros unexpanded, so it is not merely repeated, it is
wrong.

**The working directory is the volume's own, on every invocation.** Pandoc
resolves ``\\input`` against the invoking process's directory rather than the
source file's, and ``--resource-path`` does not cover it. Converted from anywhere
else a volume loses its own parts to a stderr warning and **exit 0**. This is the
one loss mode the pipeline can cause by itself, which is why it is a requirement
here rather than a note about pandoc.

**One pandoc failure is recoverable here and the rest are stops.** A
``--bibliography`` the reader cannot parse is the input point 10 says a corpus
may not have, so the volume is converted again as though it had none and the
files are carried out on the :class:`Volume` for the build to report. An
unparseable source is not that, and nothing here catches
:exc:`pandoc.PandocError` at large. An include the reader could not load is a
stop as well — it is caught only to name the volume it was lost from, and
re-raised as itself.

**Two transforms, at two different moments, and the distinction is not
cosmetic.** :func:`strip_environment_declarations` runs on the source *before*
pandoc parses, because pandoc expands a declared environment during parsing and
by the time any filter runs the author's name for it is gone. The reshaping of
those environments into labelled blockquotes is a Lua filter and runs *after*
parsing, when there is a Div to reshape. A filter cannot do the first job and the
pre-pass cannot do the second.

**Neither source reading refuses.** Both pre-scan author markup held to no fixed
standard, so a form they do not recognise is an ordinary input and not an
exception: an unreadable ``\\newtheorem`` display name is left out of the
mapping, and an unreadable ``\\newenvironment`` declaration is left where it was
written and named on the :class:`Volume`. Each costs its own declaration — the
blocks of that environment arrive as the author defined them rather than as
labelled blockquotes carrying a name — and neither costs the build.

:func:`theorem_display_names` is a third reading of the source, and it is here
for the same reason the first one is: pandoc consumes the preamble, so a
``\\newtheorem`` declaration reaches no filter — not as a ``RawBlock``, not in
``meta``, not as an attribute on the Div pandoc classes with the declaration's
*internal* name. The declaration's second string is the one that says what the
block is, so it is read here and handed to the filter as metadata
(``pandoc.THEOREM_NAMES_FLAG``).

**A declaration this reading cannot see costs the display name and nothing
else.** The scan is over the volume root's own text, so a preamble reached by
``\\input`` — or one a document class supplies, as ``amsthm`` supplies
``proof`` — yields no mapping and the filter falls back to the internal name.
That is the label every block carried before this reading existed, and stage B's
census is where the name surfaces.

Both artifacts carry the same filter set and the same mapping, so their header
sequences agree — which is the property stage 2 asserts before it zips them —
and so a label word reaches both sides of the check that compares the AST's text
against the rendering's.
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import pandoc

_log = logging.getLogger(__name__)

#: The Lua filter both artifacts are produced under (point 12, point 13).
FILTER = Path(__file__).resolve().parent / "authored_blocks.lua"

#: The declaration forms whose expansion would erase the author's environment
#: name before pandoc could class a Div with it.
_DECLARATION = re.compile(r"\\(?:re)?newenvironment\s*\*?\s*(?=\{)")

#: ``\newtheorem`` in every form amsthm admits: the starred (unnumbered)
#: variant, a shared counter in a leading optional argument
#: (``\newtheorem{corollary}[theorem]{Corollary}``), and a subordinate counter
#: in a trailing one (``\newtheorem{theorem}{Theorem}[section]``, which this
#: pattern simply stops before). The first braced group is the internal handle
#: pandoc classes the Div with; the second is the display name.
_THEOREM_DECLARATION = re.compile(r"\\newtheorem\s*\*?\s*\{([^}]*)\}\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")

#: A babel translation macro standing in for the display word —
#: ``\newtheorem{theorem}{\protect\theoremname}``. Nine of these appear across
#: the surveyed corpora and they are a fixed vocabulary, so the stem is the
#: display word: ``\protect\lemmaname`` is a Lemma in whatever language the
#: document is set in, and the classifier's vocabulary is English either way.
_TRANSLATION_MACRO = re.compile(r"\\(?:protect\s*\\)?([a-zA-Z]+)name")


@dataclass(frozen=True)
class UnreadableDeclaration:
    """A ``\\newenvironment`` the pre-scan could not read, left where it was written.

    ``line`` is the line of the volume root it opens on and ``text`` is that
    opening line, because a person diagnosing one looks it up in the source: the
    offset the scan works in is a position in a string nobody has on screen.
    """

    line: int
    text: str


@dataclass(frozen=True)
class Volume:
    """One converted volume: the index, the content, and where both came from.

    ``unreadable_bibliographies`` are the files the run was handed and pandoc
    refused, on the volumes where that happened and empty everywhere else — all
    of them, because the exit code says a file failed and not which one, so what
    the volume lost is the whole set. It is carried rather than logged and
    forgotten because the build owes a finding for it (SPEC.md point 10).

    ``unreadable_declarations`` are carried for the same reason and cost the same
    kind of thing: the environment expands as the author defined it, so its
    blocks reach the tree as ordinary content rather than as labelled blockquotes
    carrying a name (SPEC.md point 12), and a tree missing a block's name is
    indistinguishable from a corpus that distinguished no block there.
    """

    root: Path
    ast: dict[str, Any]
    markdown: str
    unreadable_bibliographies: tuple[Path, ...] = ()
    unreadable_declarations: tuple[UnreadableDeclaration, ...] = ()

    @property
    def stem(self) -> str:
        return self.root.stem


def convert(root: Path, *, bibliographies: Sequence[Path]) -> Volume:
    """Convert one volume root to its AST and its whole-volume Markdown.

    **Every bibliography given is passed, and their union is what resolves.**
    Pandoc merges them and citeproc renders only the entries this source cites,
    so a file carrying works this volume does not cite contributes nothing —
    which is why the caller is free to hand over everything sitting beside the
    corpus rather than choosing among them.

    **A bibliography pandoc cannot read is degraded to no bibliography, never to
    a stop.** It is the same input SPEC.md point 10 already admits a corpus may
    not have, so it takes the same outcome: the citations still reach the tree
    carrying their own keys, none of them resolved, and the volume gains no
    references leaf. Every other pandoc failure — an unparseable source above
    all — is still a stop, which is why the retry is keyed on
    :exc:`pandoc.PandocBibliographyError` and not on catching the base type.

    **The retry drops the whole set**, because pandoc's exit code says a file
    failed and not which one, and a retry that dropped a guess would be a build
    resolving against files nobody established were readable.

    **The retry re-runs both conversions.** Stage 2 zips the AST's headers
    against the rendering's headings and check A compares one's text against the
    other's, so a pair in which one resolved its citations and the other spelled
    their keys would be two readings of two different documents.
    """
    if not root.is_file():
        raise FileNotFoundError(f"{root} is not a file; a volume root is a top-level LaTeX document")
    declared = root.read_text(encoding="utf-8")
    names = theorem_display_names(declared)
    source, declarations = strip_environment_declarations(declared)
    for declaration in declarations:
        _log.warning(
            "%s:%d: unreadable environment declaration, left as written: %s", root, declaration.line, declaration.text
        )
    try:
        return _converted(root, source=source, names=names, bibliographies=bibliographies, declarations=declarations)
    except pandoc.PandocBibliographyError as unreadable:
        # Pandoc's own complaint names the file and the line it went wrong on,
        # and the finding the build reports carries only paths — so without this
        # the recovery swallows the whole diagnosis, and with several files
        # passed it swallows which of them failed.
        _log.warning("%s", unreadable)
        return _converted(
            root,
            source=source,
            names=names,
            bibliographies=(),
            unreadable=tuple(bibliographies),
            declarations=declarations,
        )


def _converted(
    root: Path,
    *,
    source: str,
    names: Mapping[str, str],
    bibliographies: Sequence[Path],
    declarations: tuple[UnreadableDeclaration, ...],
    unreadable: tuple[Path, ...] = (),
) -> Volume:
    """Both artifacts under one citation decision.

    **An include pandoc could not load gains the volume it was lost from and is
    re-raised.** The source travels to pandoc on stdin, so its complaint names a
    line and no file, and a run converting seven roots would report eight
    unloaded includes against none of them. It stays the same failure and stays a
    stop: this names it, it does not soften it.
    """
    directory = root.parent
    try:
        ast = pandoc.to_ast(
            source, bibliographies=bibliographies, filters=[FILTER], theorem_names=names, working_directory=directory
        )
        markdown = pandoc.to_markdown(
            source, bibliographies=bibliographies, filters=[FILTER], theorem_names=names, working_directory=directory
        )
    except pandoc.PandocIncludeError as unloadable:
        raise pandoc.PandocIncludeError(f"{root}: {unloadable}") from unloadable
    return Volume(
        root=root,
        ast=ast,
        markdown=markdown,
        unreadable_bibliographies=unreadable,
        unreadable_declarations=declarations,
    )


def theorem_display_names(source: str) -> dict[str, str]:
    """Every ``\\newtheorem`` declaration in ``source``, internal name to display name.

    The internal name is an arbitrary handle — ``claimbox``, ``Teo``, ``lem*``,
    ``Pro`` — and pandoc classes the Div with it. The display name is what the
    declaration says the block *is*, and it is what a classifier downstream has
    to read, so this is the mapping the filter is handed.

    **A declaration whose display name is LaTeX this cannot resolve is left
    out.** A babel translation macro resolves to its stem; anything else
    carrying a backslash would put markup on a label line, and no name at all
    is better than that — the internal name is then what the block carries, as
    it did before.
    """
    names: dict[str, str] = {}
    for internal, declared in _THEOREM_DECLARATION.findall(source):
        display = _display_name(declared.strip())
        if display is not None:
            names[internal.strip()] = display
    return names


def _display_name(declared: str) -> str | None:
    """``declared`` as a word, or ``None`` where it is markup or empty."""
    macro = _TRANSLATION_MACRO.fullmatch(declared)
    if macro is not None:
        return macro.group(1).capitalize()
    if not declared or "\\" in declared:
        return None
    return declared


def strip_environment_declarations(source: str) -> tuple[str, tuple[UnreadableDeclaration, ...]]:
    """``source`` with every readable ``\\newenvironment`` declaration removed, and the rest.

    The declaration is what makes pandoc expand the environment instead of
    classing a Div with its name, so removing it is what carries the author's own
    name through to the AST. The bodies go with it: an unexpanded environment's
    definition is dead text once nothing expands it.

    The scan is brace-balanced rather than line-based — a real declaration runs
    over several lines — and takes the optional argument-count and default-value
    brackets a declaration may carry, and the line comments an author may write
    between any two of its parts. LyX ends the argument list with one
    (``\\newenvironment{elabeling}[2][]%``), so the comment is an ordinary form
    and not a curiosity.

    **A declaration this cannot read costs itself and not the build.** This
    pre-scans author markup held to no fixed standard, so a form it does not
    recognise is an ordinary input: the declaration is left where it was written,
    returned in the second half of the pair, and the scan carries on from the end
    of its keyword. What it costs is that environment's blocks — pandoc expands
    them as the author defined them, so their content still reaches the tree and
    no check goes false, but they arrive as ordinary content rather than as
    labelled blockquotes carrying a name (SPEC.md point 12). A declaration inside
    ``\\makeatletter`` has not been measured and is now that case rather than a
    stop.
    """
    kept: list[str] = []
    unreadable: list[UnreadableDeclaration] = []
    position = 0
    while (found := _DECLARATION.search(source, position)) is not None:
        end = _declaration_end(source, found.end())
        if end is None:
            unreadable.append(_unreadable(source, found.start()))
            kept.append(source[position : found.end()])
            position = found.end()
            continue
        kept.append(source[position : found.start()])
        position = end
    kept.append(source[position:])
    return "".join(kept), tuple(unreadable)


def _declaration_end(source: str, start: int) -> int | None:
    """The offset just past the declaration whose name group begins at ``start``, or ``None``.

    ``None`` is *this scan cannot read it*, never *the source is wrong*: the five
    parts below are the forms measured, and an author may have written a sixth.
    """
    cursor = _skip_group(source, start)
    if cursor is None:
        return None
    cursor = _skip_optional(source, cursor)
    cursor = _skip_optional(source, cursor)
    for _ in range(2):
        cursor = _skip_group(source, cursor)
        if cursor is None:
            return None
    return cursor


def _unreadable(source: str, start: int) -> UnreadableDeclaration:
    """The declaration opening at ``start``, located the way a person looks it up."""
    line = source.count("\n", 0, start) + 1
    ending = source.find("\n", start)
    return UnreadableDeclaration(line=line, text=source[start : len(source) if ending == -1 else ending].rstrip())


def _skip_group(source: str, start: int) -> int | None:
    """The offset just past the braced group at ``start``, or ``None`` where there is none.

    A ``%`` comments out the rest of its line here as anywhere else, so a brace
    inside one is not a brace this counts.
    """
    cursor = _skip_ignorable(source, start)
    if cursor >= len(source) or source[cursor] != "{":
        return None
    depth = 0
    while cursor < len(source):
        character = source[cursor]
        if character == "\\":
            cursor += 2
            continue
        if character == "%":
            cursor = _end_of_line(source, cursor)
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return cursor + 1
        cursor += 1
    return None


def _skip_optional(source: str, start: int) -> int:
    cursor = _skip_ignorable(source, start)
    if cursor >= len(source) or source[cursor] != "[":
        return start
    closing = source.find("]", cursor)
    return closing + 1 if closing != -1 else start


def _skip_ignorable(source: str, start: int) -> int:
    """Past the whitespace and the line comments at ``start``.

    A comment separates a declaration's parts the way whitespace does, and a scan
    reading only whitespace calls the declaration LyX writes malformed.
    """
    cursor = start
    while cursor < len(source):
        if source[cursor] in " \t\r\n":
            cursor += 1
        elif source[cursor] == "%":
            cursor = _end_of_line(source, cursor)
        else:
            break
    return cursor


def _end_of_line(source: str, start: int) -> int:
    ending = source.find("\n", start)
    return len(source) if ending == -1 else ending + 1
