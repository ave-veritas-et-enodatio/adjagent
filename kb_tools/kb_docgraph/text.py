"""Turning Markdown and AST text into comparable tokens, and finding headings in it.

Two things live here because they are the same problem seen twice: the partition
checks compare text that crossed a format boundary, and the splitter has to find
headings in text that a writer wrapped.

**Why a token stream and not a string compare.** The AST is word-granular and the
Markdown is a *rendering* of it — emphasis marked, entities resolved, lines
wrapped at 72 columns, citations expanded, HTML injected. Nothing survives a byte
compare. What does survive is the sequence of words, and a *contiguous run* of
words is what tells a dropped abstract from a restated one: an abstract shares a
body's vocabulary, so set overlap scores it present, and shares none of the
body's phrasing, so a run does not.

**The two normalizations are deliberately not one function.** ``plain_tokens``
reads text that is already plain — the ``Str`` values of an AST inline stream.
``markdown_tokens`` reads a rendering and has to undo what the writer did to it:
strip tags, drop the math it re-spelled, unescape, unwrap autolinks and inline
link targets. Running the Markdown side's undo over plain text is what
*mis*-normalizes it — a bibliography DOI holding a literal ``<`` is read as an
open tag and eats the rest of the entry. Both ends then agree on one last pass,
:func:`_split`, which is where emphasis and quotation marks are deleted rather
than merely trimmed: ``*rate*-limited`` and ``rate-limited`` are one word, and
whether a quotation mark is inside or outside a word is the writer's business.

**Verbatim content is tokenized verbatim.** It holds maths or raw source, and the
undo pass would read a ``<`` in ``\\det J_+ > 0`` as a tag. It is also the one
place a token stream must stay byte-faithful, because point 9's claim is about
maths surviving. So the scan splits the document at its verbatim spans first and
each side is tokenized by its own rule.

**The writer spells verbatim two ways and both are it.** A fence carries an info
string, so the gfm writer reaches for one where a ``CodeBlock`` has attributes to
put there and writes an attribute-less one *indented* instead — which is every
``verbatim`` environment an author wrote. A scan that knows only the fence hands
the indented spelling to the undo pass, where ``_`` is deleted as an emphasis
mark: ``interleavedDA_MSRR`` on the AST side against ``interleavedDAMSRR`` on the
rendering's, and check A reports a code block nothing dropped.
"""

import html
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

#: A fenced block's opening or closing marker, at any indentation. A list item's
#: own content column is where its fence opens, and that is four spaces in for a
#: single ``1.  `` marker and deeper for a nested one — so the three-space bound
#: CommonMark puts on a *top-level* fence is not the bound here, and a pattern
#: carrying it sees no fence inside any list at all. The indentation is the
#: item's, not the fence's content: :func:`fenced_blocks` takes it back off.
_FENCE = re.compile(r"^( *)(`{3,}|~{3,})")

#: How far past its container's content column a line has to sit to be an
#: indented code block. CommonMark's four.
_CODE_INDENT = 4

#: A list item's marker and the gap after it, whose end is the content column the
#: item opens. That column is what an indented code block inside the item is
#: measured from, and it is why indentation alone decides nothing: pandoc writes
#: ``1.  `` and a second paragraph of that item then sits at the same four
#: columns a top-level code block does.
_LIST_MARKER = re.compile(r"^ *(?:[-*+]|\d+[.)]) +")

#: A blockquote's own markers. Point 12's labelled blockquotes hold maths and
#: raw HTML, and a fence inside one opens with ``> `` — unrecognised, its LaTeX
#: is read as prose and the ``>`` in ``\\det J > 0`` is taken for a tag that eats
#: the sentence after it. Stripping the marker is token-neutral either way: a
#: lone ``>`` is punctuation and trims to nothing.
_QUOTE = re.compile(r"^ {0,3}(?:> ?)+")

#: An ATX heading line. The gfm writer emits every heading in this form.
_ATX = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$")

_FOOTNOTE_REF = re.compile(r"\[\^[^\]\s]*\]")
_MATH_SPAN = re.compile(r"\$`.*?`\$", re.DOTALL)
#: A whole MathML element, dropped the way a math span is. Point 9's one
#: exception emits a caption's maths as MathML, and that element spells the same
#: maths twice — once as presentation glyphs, once as the LaTeX annotation. The
#: tag pass alone leaves both behind as bare text glued to the word after them,
#: so a caption reading ``The <math>…8…</math>-qubit`` tokenizes as ``88-qubit``
#: and no AST run can match it; the AST spells the element once, as a ``Math``
#: node its neighbouring ``Str`` runs stop either side of.
_MATH_ELEMENT = re.compile(r"<math\b.*?</math\s*>", re.DOTALL)
#: An inline code span, padded so that it is a token boundary. ``walk``'s runs
#: break at a ``Code`` and hold none of its content, while the writer spells the
#: span against whatever touches it — a macro expanding to ``\\texttt`` written
#: against the next word arrives as ``` `A7`-directed ```, one token here
#: against a run beginning ``directed`` there, and check A reports a paragraph
#: nothing dropped. The content **stays**, unlike a math span's: check B compares
#: this stream against itself, so deleting a code span would be a splitter
#: dropping one with nothing left to notice.
#:
#: One backtick and no line break, measured rather than assumed — of the staged
#: corpus's 993 inline spans none is written with a longer run and none is
#: wrapped. The pass runs after :data:`_MATH_SPAN`, which is the one thing the
#: writer spells with backticks *and* wraps.
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_AUTOLINK = re.compile(r"<((?:https?|ftp|mailto):[^<>\s]*)>")
#: An inline link's target, dropped so the link reads as its own text. A URL
#: holds no whitespace, so the chunk between ``](`` and the last ``)`` on it is
#: the target however many parentheses the URL itself carries.
_LINK_TARGET = re.compile(r"\]\(\S*\)")
_TAG = re.compile(r"<[^<>]*>", re.DOTALL)
_ESCAPE = re.compile(r"\\([^0-9A-Za-z\s])")

#: Characters deleted wherever they sit. Emphasis and quotation are the writer's
#: marks on a word, not part of it, and the AST spells neither.
_DELETED = str.maketrans("", "", "*_~\u201c\u201d\u2018\u2019\"'")

#: Characters trimmed from a token's ends only — punctuation that attaches to a
#: word without being in it. Kept mid-token, where a hyphen or a slash is
#: structure (``rate-limited``, ``zombie/calcified``).
_EDGES = "`()[]{}<>.,;:!?|\\/#$&+=-\u2013\u2014\u2026"

#: Placeholders for an escaped angle bracket, so the tag pass cannot read a
#: literal ``<`` in the text as the start of one.
_ESCAPED_LT, _ESCAPED_GT = "\x01", "\x02"

#: A character reference, named or numeric, **with its semicolon**. Raw HTML the
#: writer emits — a ``<figcaption>``, a ``<td>`` — is XML text, so a caption's
#: ``&`` arrives as ``&amp;`` and tokenizes as a word of its own that no AST run
#: has; the AST holds the character itself. Nothing wider than this is resolved,
#: because a bare ``&`` is ordinary Markdown and ``R&D`` is not a reference to
#: anything.
_ENTITY = re.compile(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")


def plain_tokens(text: str) -> list[str]:
    """Tokens of text that is already plain — an AST inline stream's own words."""
    return _split(text)


def markdown_tokens(text: str) -> list[str]:
    """Tokens of rendered Markdown, verbatim content taken verbatim.

    The whole document, not one line: a tag, a math span and a wrapped heading
    all straddle line breaks in the gfm writer's output.
    """
    tokens: list[str] = []
    for verbatim, chunk in _verbatim_split(text):
        tokens.extend(chunk.split() if verbatim else _undo_rendering(chunk))
    return tokens


def _undo_rendering(text: str) -> list[str]:
    text = _ESCAPE.sub(lambda m: {"<": _ESCAPED_LT, ">": _ESCAPED_GT}.get(m.group(1), m.group(1)), text)
    text = _FOOTNOTE_REF.sub(" ", text)
    text = _MATH_ELEMENT.sub(" ", text)
    text = _MATH_SPAN.sub(" ", text)
    text = _CODE_SPAN.sub(lambda span: f" {span.group()} ", text)
    text = _AUTOLINK.sub(r"\1", text)
    text = _LINK_TARGET.sub("]", text)
    text = _TAG.sub("", text)
    # After the tag pass, never before it: an ``&lt;`` resolved early is a ``<``
    # the pass then reads as the start of a tag that eats the rest of the line.
    text = _ENTITY.sub(lambda reference: html.unescape(reference.group()), text)
    return _split(text.replace(_ESCAPED_LT, "<").replace(_ESCAPED_GT, ">"))


def _split(text: str) -> list[str]:
    return [stripped for chunk in text.split() if (stripped := chunk.translate(_DELETED).strip(_EDGES))]


def unquoted(text: str) -> str:
    """``text`` with every blockquote marker removed, line by line."""
    return "\n".join(_QUOTE.sub("", line) for line in text.splitlines())


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """``(info string, content)`` for every fenced block, blockquote markers off.

    The close is "a line opening with the marker", not "a line holding nothing
    else": a display equation inside an emphasised theorem statement closes on
    ``` ```* ```, the writer having put the emphasis' own delimiter there.

    Content is dedented by the opening fence's own indentation, which is what a
    fence inside a list item carries and what point 9's verbatim LaTeX does not:
    the item put those columns there, so taking exactly them back off is what
    recovers the bytes the source wrote.
    """
    blocks: list[tuple[str, str]] = []
    fence: str | None = None
    info = ""
    indent = 0
    buffered: list[str] = []
    for quoted in text.splitlines():
        line = _QUOTE.sub("", quoted)
        was_open = fence is not None
        fence, marker = _delimiter(line, fence)
        if marker is not None and not was_open:
            indent = len(marker.group(1))
            info, buffered = line[marker.end() :].strip(), []
        elif marker is not None:
            blocks.append((info, "\n".join(buffered)))
        elif was_open:
            buffered.append(_dedent(line, indent))
    return blocks


def _delimiter(line: str, fence: str | None) -> tuple[str | None, re.Match[str] | None]:
    """The fence state after ``line``, and the delimiter ``line`` is — ``None`` where it is not one.

    **A fence closes on its own marker and on nothing else.** An author's
    ``~~~\\mbox{and }~~~`` spacing sits inside a display equation, and a reader
    that took it for a tilde fence would close the backtick one there: the rest
    of the LaTeX is then read as prose, and every heading after it is read as
    sitting inside a fence.
    """
    marker = _FENCE.match(line)
    if marker is None:
        return fence, None
    if fence is None:
        return marker.group(2)[0] * len(marker.group(2)), marker
    return (None, marker) if line.lstrip().startswith(fence) else (fence, None)


def _dedent(line: str, indent: int) -> str:
    """``line`` with at most ``indent`` leading spaces removed."""
    return line[min(indent, len(line) - len(line.lstrip(" "))) :]


def _verbatim_split(text: str) -> Iterator[tuple[bool, str]]:
    """``(is_verbatim, chunk)`` over ``text``; a fence's own marker lines carry nothing.

    Both of the writer's spellings, and an open fence outranks the other: a
    fence's content is already verbatim, so a line of it that happens to be
    indented starts nothing.
    """
    lines = [_QUOTE.sub("", quoted) for quoted in text.splitlines()]
    buffered: list[str] = []
    fence: str | None = None
    column: int | None = None
    for number, line in enumerate(lines):
        if column is not None:
            if not line.strip() or _indent(line) >= column:
                buffered.append(line)
                continue
            yield True, "\n".join(buffered)
            buffered, column = [], None
        if fence is None:
            column = _code_column(lines, number)
            if column is not None:
                if buffered:
                    yield False, "\n".join(buffered)
                buffered = [line]
                continue
        was_open = fence is not None
        fence, marker = _delimiter(line, fence)
        if marker is not None:
            if buffered:
                yield was_open, "\n".join(buffered)
                buffered = []
            continue
        buffered.append(line)
    if buffered:
        yield fence is not None or column is not None, "\n".join(buffered)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _code_column(lines: Sequence[str], number: int) -> int | None:
    """The column an indented code block opening at ``lines[number]`` holds to, or ``None``.

    Indented code cannot interrupt a paragraph, so the line before it is blank or
    there is none. What it is indented *from* is the content column of whatever
    holds it, and the nearest preceding shallower line is what names that: a list
    item's marker puts the item's own content four columns in, and anything else
    sits at its own indentation.
    """
    line = lines[number]
    if not line.strip() or _indent(line) < _CODE_INDENT or (number and lines[number - 1].strip()):
        return None
    column = _container_column(lines[:number], _indent(line)) + _CODE_INDENT
    return column if _indent(line) >= column else None


def _container_column(preceding: Sequence[str], indent: int) -> int:
    for line in reversed(preceding):
        if not line.strip() or _indent(line) >= indent:
            continue
        marker = _LIST_MARKER.match(line)
        return marker.end() if marker else _indent(line)
    return 0


@dataclass(frozen=True)
class Heading:
    """One heading found in rendered Markdown.

    ``text`` is the heading's whole content. The gfm writer wraps at 72 columns
    and does not exempt a heading from it, so a heading holding a cross-reference
    — which renders as a multi-attribute ``<a>`` element — arrives spread over
    two lines, of which only the first is a heading as far as Markdown is
    concerned. Reading the continuation back is what keeps the document the
    splitter writes from carrying that break forward.
    """

    level: int
    text: str
    start: int
    end: int


def headings(markdown: str) -> list[Heading]:
    """Every ATX heading, fence-aware, continuation lines folded into the text."""
    lines = markdown.splitlines()
    found: list[Heading] = []
    fence: str | None = None
    for number, line in enumerate(lines):
        fence, marker = _delimiter(_QUOTE.sub("", line), fence)
        if marker is not None or fence is not None:
            continue
        atx = _ATX.match(line)
        if not atx:
            continue
        end = number + 1
        while end < len(lines) and lines[end].strip() and not _ATX.match(lines[end]) and not _FENCE.match(lines[end]):
            end += 1
        content = " ".join([atx.group(2), *(part.strip() for part in lines[number + 1 : end])]).strip()
        found.append(Heading(level=len(atx.group(1)), text=content, start=number, end=end))
    return found


def github_anchor(heading_text: str) -> str:
    """The fragment GitHub derives from a heading's text.

    Needed because the gfm writer discards a ``Header``'s identifier — GitHub
    computes the anchor from what it reads, so a link into a heading has to spell
    it the same way.
    """
    plain = _TAG.sub("", _MATH_SPAN.sub("", heading_text))
    plain = _ESCAPE.sub(r"\1", _LINK_TARGET.sub("]", plain))
    kept = [character for character in plain.lower() if character.isalnum() or character in " -_"]
    return "".join(kept).strip().replace(" ", "-")
