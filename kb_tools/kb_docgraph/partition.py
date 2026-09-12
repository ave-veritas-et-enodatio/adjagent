"""The build's own checks: is the tree a partition of the source, and did the maths survive.

**Two checks, because there are two things that can lose content.** Check A
compares the AST and ``meta`` against the whole-volume rendering and catches what
*pandoc* drops; check B compares that rendering against the tree and catches what
*this stage's splitter* drops. Neither substitutes for the other: pandoc's losses
are invisible to a check that starts from its output, and a splitter's losses are
invisible to a check that never opens the tree.

**Check A matches by contiguous run, not by set membership.** It crosses a format
boundary, so exact bytes are unavailable — but an abstract restates the body, and
a score over shared tokens rates a dropped abstract as present. A run of words in
the same order is what the two documents do not share by accident.

**Check B is a near-total partition**, Markdown on both sides. Navigation text is
exempt by construction rather than by rule: a document's body and its up-link and
child list are different fields, and only the body is compared, so a heading
reaching both its own document and its parent's child list is not duplication.
The comparison runs in both directions — the volume's words against the segments
the splitter cut, and each segment's words against the body that segment became —
so neither a drop in the cut nor a drop in a per-document transform passes.

**The elision list is closed: presentation apparatus, and nothing else.** It is
:data:`outline.APPARATUS_KEYS` plus the ``titlepage`` Div the filter drops, both
literals a diff can show. Widening it is a plan change, not a code change. This
is what makes the maths, citation and figure claims enforceable rather than
aspirational — pandoc is known to lose content silently, including in two ways
nobody had named until a walk went looking.
"""

import html
import posixpath
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .outline import CONTENT_KEYS, VolumeTree
from .text import fenced_blocks, markdown_tokens, unquoted
from .walk import leaf_blocks, math_texts, meta_entries, text_runs

TAG = "docgraph"
PASS = "PASS"
FAIL = "FAIL"
FACT = "FACT"

CHECK_AST = "A-ast-to-markdown"
CHECK_TREE = "B-markdown-to-tree"
CHECK_MATH = "math-survives"
CHECK_ANCHOR = "anchor-lands"
CHECK_ASSET = "image-asset"

#: Not checks over the product but facts about the conversion that produced it,
#: so no function here computes either — stage 1 already knows the answers and
#: ``build`` states them. The names live beside the others because a report
#: line's subject is what a reader greps, and one spelled somewhere else is one
#: nobody finds beside its neighbours.
CHECK_BIBLIOGRAPHY = "bibliography-read"
CHECK_DECLARATION = "declaration-read"

#: How much of a missed run a report line carries. Enough to find the passage,
#: short enough that a hundred of them stay readable.
_EXCERPT_WORDS = 14

#: The three spellings maths reaches gfm in. The third: a caption's maths is
#: emitted as MathML carrying its own LaTeX annotation. Nothing is lost and the
#: form differs, which is why the annotation is read rather than the claim
#: weakened.
_INLINE_MATH = re.compile(r"\$`(.*?)`\$", re.DOTALL)
_DISPLAY_MATH_INFO = "math"
_ANNOTATED_MATH = re.compile(r"<annotation encoding=\"application/x-tex\">(.*?)</annotation>", re.DOTALL)


@dataclass(frozen=True)
class Finding:
    """One report line. ``FACT`` describes state and never gates."""

    status: str
    check: str
    detail: str

    def line(self) -> str:
        return f"[{TAG}] {self.status} {self.check} {self.detail}"


def gating(findings: Sequence[Finding]) -> bool:
    return any(finding.status == FAIL for finding in findings)


def check_ast_against_markdown(*, stem: str, ast: Mapping[str, Any], markdown: str) -> list[Finding]:
    """A: every block of ``blocks`` and every content-bearing entry of ``meta`` reaches the rendering."""
    rendered = markdown_tokens(markdown)
    findings: list[Finding] = []
    blocks = 0
    for block in leaf_blocks(ast["blocks"]):
        blocks += 1
        for run in text_runs(block):
            if not _holds(rendered, run):
                findings.append(Finding(FAIL, CHECK_AST, f"{stem} block {block['t']} lost: {_excerpt(run)}"))
    entries = meta_entries(ast["meta"])
    for entry in entries:
        for run in entry.runs:
            if not _holds(rendered, list(run)):
                findings.append(Finding(FAIL, CHECK_AST, f"{stem} meta.{entry.path} lost: {_excerpt(list(run))}"))
    return findings or [
        Finding(
            PASS,
            CHECK_AST,
            f"{stem} {blocks} blocks and {len(entries)} meta entries "
            f"({sorted({entry.key for entry in entries})}) all reach the rendering",
        )
    ]


def check_markdown_against_tree(tree: VolumeTree) -> list[Finding]:
    """B: the volume's rendering partitions across the documents, and each body keeps its segment."""
    findings: list[Finding] = []
    volume = Counter(tree.content_tokens)
    assigned: Counter[str] = Counter()
    for node in tree.documents:
        assigned.update(markdown_tokens(node.segment))
    findings += _difference(tree.stem, "dropped by the split", volume - assigned)
    findings += _difference(tree.stem, "duplicated by the split", assigned - volume)

    for node in tree.documents:
        lost = Counter(markdown_tokens(node.segment)) - Counter(markdown_tokens(node.body))
        findings += _difference(f"{tree.stem} {node.path}", "lost by a per-document transform", lost)
    return findings or [
        Finding(
            PASS,
            CHECK_TREE,
            f"{tree.stem} {sum(volume.values())} words partition across {len(tree.documents)} documents",
        )
    ]


def _difference(subject: str, what: str, difference: Counter[str]) -> list[Finding]:
    if not difference:
        return []
    worst = sorted(difference.items(), key=lambda item: (-item[1], item[0]))[:_EXCERPT_WORDS]
    return [
        Finding(
            FAIL,
            CHECK_TREE,
            f"{subject}: {sum(difference.values())} word(s) {what}, e.g. "
            + ", ".join(f"{word!r}×{count}" for word, count in worst),
        )
    ]


def check_math(*, stem: str, ast: Mapping[str, Any], tree: VolumeTree) -> list[Finding]:
    """Point 9: every ``Math`` element reaches a document, none degraded to text, counts equal.

    Over the blocks and over the *content* metadata — an abstract's maths is
    checked, a byline's ``^{1}`` affiliation marker is not. Point 13 elides
    apparatus before any document exists for point 9's guarantee to bite on, so
    the two points meet here and this is where the meeting is spelled.
    """
    source = Counter(_normalized(text) for text in math_texts(ast, content_keys=CONTENT_KEYS))
    placed: Counter[str] = Counter()
    for node in tree.documents:
        placed.update(_normalized(text) for text in _math_in(node.body))
    missing = source - placed
    if missing:
        return [
            Finding(
                FAIL,
                CHECK_MATH,
                f"{stem}: {sum(missing.values())} maths element(s) reach no document, e.g. "
                + "; ".join(repr(text[:80]) for text, _ in sorted(missing.items())[:3]),
            )
        ]
    return [Finding(PASS, CHECK_MATH, f"{stem} {sum(source.values())} maths elements all reach a document")]


def _math_in(body: str) -> list[str]:
    """Maths as the three forms gfm carries it in, blockquote markers off first.

    A labelled blockquote's display maths opens with ``> ``` math`` — a fence a
    line-anchored pattern does not see, and its contents would then be counted as
    reaching no document at all.

    **A MathML annotation is XML text, so its LaTeX arrives escaped**, by name
    and by number alike: ``1<t\\le r^2`` is written ``1&lt;t\\le r^2`` and
    ``H''=(…)`` is written ``H&#39;&#39;=(…)``. Compared against the AST's own
    spelling, every element carrying ``<``, ``>``, an apostrophe or an ``align``
    environment's ``&`` reports as reaching no document. Every ``&`` in the
    annotation opens a reference, the writer having escaped the literal ones, so
    resolving all of them is total rather than a guess at which are real.
    """
    plain = unquoted(body)
    display = [content for info, content in fenced_blocks(body) if info == _DISPLAY_MATH_INFO]
    annotated = [html.unescape(annotation) for annotation in _ANNOTATED_MATH.findall(plain)]
    return _INLINE_MATH.findall(plain) + display + annotated


def _normalized(text: str) -> str:
    return " ".join(text.split())


#: A cross-reference as it stands in a written document. Pandoc names the label
#: in ``data-reference`` as well as in the ``href``, which is what lets this
#: check re-derive where each link *should* land instead of trusting the rewrite
#: that put it there.
_REFERENCE = re.compile(r"<a\b[^<>]*?\bhref=\"([^\"]*)\"[^<>]*?\bdata-reference=\"([^\"]*)\"", re.DOTALL)


def check_anchors(tree: VolumeTree) -> list[Finding]:
    """Point 7's second half: every rewritten anchor lands on the node that held its label.

    The label is read back off the written element rather than taken from the map
    that produced the link, so the check compares two independent readings and a
    rewrite that shifted every anchor by one node fails it.
    """
    findings: list[Finding] = []
    rewritten = 0
    raw: list[str] = []
    for node in tree.documents:
        for written in _REFERENCE.finditer(node.body):
            destination, label = written.group(1), written.group(2)
            expected = tree.label_paths.get(label)
            if destination.startswith("#"):
                if expected is not None:
                    findings.append(
                        Finding(
                            FAIL,
                            CHECK_ANCHOR,
                            f"{tree.stem} {node.path}: {label!r} is in the label-to-node map "
                            f"({expected}) but its link was left unrewritten",
                        )
                    )
                else:
                    raw.append(label)
                continue
            rewritten += 1
            landed = posixpath.normpath(posixpath.join(posixpath.dirname(node.path), destination.split("#", 1)[0]))
            if landed != expected:
                findings.append(
                    Finding(
                        FAIL,
                        CHECK_ANCHOR,
                        f"{tree.stem} {node.path}: anchor for {label!r} resolves to {landed} "
                        f"but the label sits in {expected}",
                    )
                )
    gates = list(findings)
    if raw:
        findings.append(
            Finding(
                FACT,
                CHECK_ANCHOR,
                f"{tree.stem}: {len(set(raw))} label(s) outside the map render as the raw label a reader "
                f"can see — {', '.join(sorted(set(raw))[:6])}",
            )
        )
    if gates:
        return findings
    return [
        *findings,
        Finding(PASS, CHECK_ANCHOR, f"{tree.stem} {rewritten} rewritten anchors land on the node that held the label"),
    ]


def check_assets(tree: VolumeTree) -> list[Finding]:
    """Every embedded image the build could copy, and every one it could not.

    An image the source names but does not ship is reported and does not gate.
    What the build still refuses to do is construct a link to it: the tree gets
    the caption pandoc rendered and no image, rather than a target nobody can
    follow and a dead-link failure three checks later.
    """
    missing = [
        Finding(
            FACT,
            CHECK_ASSET,
            f"{tree.stem}: {source!r} is embedded by the source but is not on disk beside it — "
            "no image was copied and no self-linking form was constructed for it",
        )
        for source in sorted(tree.missing_assets)
    ]
    return [
        *missing,
        Finding(PASS, CHECK_ASSET, f"{tree.stem} {len(tree.assets)} image asset(s) copied into the tree"),
    ]


def _holds(rendered: Sequence[str], run: Sequence[str]) -> bool:
    """Does ``run`` appear in ``rendered`` as a contiguous subsequence?"""
    length = len(run)
    if length == 0:
        return True
    head = run[0]
    window = list(run)
    for start in range(len(rendered) - length + 1):
        if rendered[start] == head and list(rendered[start : start + length]) == window:
            return True
    return False


def _excerpt(run: Sequence[str]) -> str:
    shown = " ".join(run[:_EXCERPT_WORDS])
    return f"{shown!r}{'...' if len(run) > _EXCERPT_WORDS else ''} ({len(run)} words)"
