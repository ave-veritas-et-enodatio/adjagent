"""One idea in two places: a candidate sweep over this repository's prose and code.

A rule can be honoured completely inside one worker's view window and violated at
the scale of the project. Two implementations of one capability, each internally
clean. One sentence stated in seven definitions. One constant defined twice.
Nothing inside either site is wrong, so nothing inside either site reports it —
the fact is *comparative*, and it has been found here so far only by adversarial
multi-model review or by accident.

Enumeration is what that needs, and enumeration is mechanical. **This tool emits
candidates and never verdicts**: whether a pair is genuinely one idea is a
judgment, and it stays with the reader or the model who adjudicates the output.
The exit status says the sweep ran, not that the tree is clean — a gate would be
claiming the judgment it refuses to make. It is tuned for a short list somebody
will actually read, not for recall against every coincidence.

Two passes, ``prose`` and ``python``, run from ``just sweep-prose`` and
``just sweep-python``.

**The corpus is the repository's own source, from git** — ``git ls-files`` plus
untracked, non-ignored files, or the tree at ``--rev``. Not a directory walk:
this tree carries byte-copies of the swept sources (``rendered/``, the installed
surface under ``.claude/``, transient install targets under ``kb-testing/``) and
every one is gitignored. A walk reaching them would report a copy as a
duplicate, which is the loudest useless finding a sweep can produce. ``--rev``
exists because a known-answer run is against a tree that is no longer the
working one.

What each pass looks for
========================

``prose`` — one sentence, two places, across the agent-definition templates.
The corpus is ``templates/shared-chunks.toml``'s chunk bodies (``text`` and
every ``variants`` body) plus the inline prose of ``templates/agents/**`` and
``templates/commands/**``. A template's ``+++`` parameter fence and its
frontmatter are dropped — neither renders into a prompt body — and so are
fenced code blocks.

``python`` — the same question over ``kb_tools/``, less ``tests/`` (a fixture is
data and a test function is called by pytest) and ``_vendor/`` (third-party
source this repository never edits). Three of the four questions it was asked
are answered:

* **One concept, two implementations** — functions whose *structural shape*
  matches across modules. A function's shape is the pre-order sequence of AST
  node types of its parameter list and body, docstring dropped: names, literals
  and comments are invisible to it, so two spellings of one algorithm collide.
  Classes are swept through their methods rather than whole: a class's shape
  contains its methods' shapes, so a class and its own method are near-duplicates
  of each other by construction and would fill the report with that artifact.
* **One constant, two definitions** — module-level literal bindings, reported
  when one value is bound to similar names in two modules, or one name is bound
  in two modules.
* **One rule, two homes** — sentences of docstrings and comments, through the
  same engine the prose pass uses.

The fourth question it was asked — **one word, two meanings** — is not
implemented, and :data:`UNANSWERED` is printed in the report rather than
approximated. Nothing in the source distinguishes a term's *referent*: the known
instance here is "row", used for a ``steps.Step`` and for a line of a plan table,
and every mechanical proxy for it (an identifier bound to two types, a word
appearing in two modules' prose) fires on every common name in the tree. What
would make it answerable is a person-supplied glossary — each project term with
the one thing it is allowed to name — after which the check is the boring one:
find the sites using the term where that referent is absent.

How the comparison works
========================

Every comparable span becomes a :class:`Unit` carrying a token run — words for
prose, AST node types for code. Two units are near-duplicates when the **shorter
run is mostly contained, in order, in the longer one**: ``difflib``'s matching
blocks over ``min(len(a), len(b))``. Containment rather than symmetric ratio,
because the case that matters is a sentence restated inside a longer one — the
seven-way duplicate this was first run against has six exact copies and one
carrying an extra parenthetical, which scores 0.94 by containment and only 0.77
by ratio.

Containment alone chains: a short span sits inside a long one at 100%, and the
long one sits inside a longer one, until a cluster is a rope rather than a
finding. ``length_ratio`` is the brake — two spans whose lengths differ by more
than it are never compared.

An all-pairs comparison would be quadratic in a corpus of thousands of
sentences, so candidate pairs come from a shingle index first (every window of
``width`` consecutive tokens) and only those pairs are scored. A shingle shared
by more than :data:`MAX_BUCKET` units is skipped as shared framing rather than
expanded into its quarter-million pairs; a real duplicate reaches its partners
through its rarer windows. Where the token vocabulary is small — code shapes
draw on a few dozen AST node types — each unit is indexed under only its
``probes`` rarest windows, which is the difference between forty seconds and
one. Prose is indexed whole: a restatement's rarest windows are often exactly
the words it added, so probing is where it would lose its partner, and the
seven-way known answer is the case that proves it.

Qualifying pairs are unioned into clusters, so a seven-way duplicate is one
candidate with seven sites and not twenty-one pairs. Above each list is the
file-pair tally: two definitions sharing forty sentences are one finding, and
reading it first is what keeps the forty rows below from being the report.

Everything is sorted — the corpus listing, the units, the pairs, the clusters —
so the same tree yields the same report, byte for byte.

Stated bounds
=============

*Sentence splitting* is punctuation-and-newline, not linguistic: an abbreviation
mid-sentence ("e.g.") splits, and the fragments are usually short enough to fall
under ``--min-words``. Markdown prose is split per line, because a bullet is a
line; Python docstrings and comment blocks are rejoined across their hard wrap
first, because there the newline is a rendering artifact.

*Chunk line numbers* name the ``[chunks.<name>]`` header, not the sentence's own
line — the body is a TOML string and its interior lines have no positions to
read back. The label beside the path is what identifies it.

*A chunk's ``[chunks.*.defaults]`` values are outside the corpus.* They are
argument text a call site binds, not a body.
"""

import argparse
import ast
import difflib
import io
import logging
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

log = logging.getLogger("dupe_sweep")

#: A shingle shared by more than this many units is shared framing, not a
#: duplicate, and expanding it to pairs costs more than it can find.
MAX_BUCKET = 400

#: Defaults per pass, tuned on this tree for a list short enough to read. Twelve
#: words is about the shortest sentence whose repetition is a statement rather
#: than a turn of phrase. Thirty AST nodes is roughly ten lines, and a structural
#: clone is worth a reader's time only when it is near-exact, hence the higher
#: containment bar and the tighter length window on code.
PROSE_DEFAULTS = {"min_tokens": 12, "coverage": 0.85, "width": 4, "length_ratio": 0.6, "probes": None}
CODE_DEFAULTS = {"min_tokens": 30, "coverage": 0.90, "width": 5, "length_ratio": 0.7, "probes": 8}
DOC_DEFAULTS = {"min_tokens": 12, "coverage": 0.85, "width": 4, "length_ratio": 0.6, "probes": None}

#: Printed in place of the fourth question's findings. See the module docstring.
UNANSWERED = (
    "    No mechanical proxy for a term's referent survives contact with this tree.\n"
    '    The instance this was calibrated against is "row", used for a steps.Step and\n'
    "    for a line of a plan table — and every proxy for it (a name bound to two\n"
    "    types, a word appearing in two modules) fires on every common name here.\n"
    "    What would answer it is a glossary naming each project term and the one\n"
    "    thing it may refer to; the check is mechanical after that, guesswork before."
)

TEMPLATE_PREFIXES = ("templates/agents", "templates/commands")
CHUNK_SOURCE = "templates/shared-chunks.toml"
CODE_PREFIX = "kb_tools"
CODE_UNSWEPT = ("kb_tools/tests/", "kb_tools/_vendor/")

_MARKER = re.compile(r"@![^!]*!@")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_LIST_LEAD = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+|>\s*)+")
_CHUNK_HEADER = re.compile(r"^[ \t]*\[chunks\.([a-z0-9-]+)", re.MULTILINE)
_TRIVIAL_LITERALS = frozenset({"None", "True", "False", "''", '""', "()", "[]", "{}", "0", "1", "-1", "0.0", "b''"})
#: What makes a literal identifying rather than a coincidence — see :func:`_distinctive`.
_DISTINCTIVE_MAGNITUDE = 10
_DISTINCTIVE_WIDTH = 5


class SweepError(Exception):
    """The sweep could not run at all — a bad revision, or git unavailable."""


# ── corpus ───────────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=True
        )
    except FileNotFoundError as exc:  # git itself absent
        raise SweepError(
            "git is not on PATH — this sweep reads its corpus from git, not from a directory walk"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SweepError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc
    return done.stdout


def corpus(*, rev: str | None, prefixes: Sequence[str], suffix: str) -> dict[str, str]:
    """Repo-relative path -> text, for the tracked-or-untracked files under `prefixes`.

    `rev` None reads the working tree; otherwise the tree at that revision.
    """
    if rev is None:
        listing = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *prefixes)
    else:
        listing = _git("ls-tree", "-r", "-z", "--name-only", rev, "--", *prefixes)
    texts: dict[str, str] = {}
    for path in sorted(entry for entry in listing.split("\0") if entry.endswith(suffix)):
        if rev is not None:
            texts[path] = _git("show", f"{rev}:{path}")
            continue
        file = REPO_ROOT / path
        if not file.is_file():  # tracked, deleted in the working tree, not yet staged
            log.warning("%s is tracked but absent from the working tree — skipped", path)
            continue
        texts[path] = file.read_text(encoding="utf-8")
    if not texts:
        log.warning("no %s files under %s — the pass has nothing to compare", suffix, ", ".join(prefixes))
    return texts


# ── the comparison engine ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Unit:
    """One comparable span: a sentence, or one function's structural shape."""

    path: str
    line: int
    label: str
    text: str
    tokens: tuple[str, ...]

    @property
    def site(self) -> str:
        return f"{self.path}:{self.line}" + (f" {self.label}" if self.label else "")

    @property
    def sort_key(self) -> tuple[str, int, str]:
        return (self.path, self.line, self.label)


@dataclass(frozen=True)
class Cluster:
    """Units that say the same thing, with each one's containment against the shortest."""

    representative: Unit
    members: tuple[tuple[Unit, float], ...]


def containment(left: Sequence[str], right: Sequence[str]) -> float:
    """Fraction of the shorter token run that also appears, in order, in the longer."""
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / min(len(left), len(right))


def _shingles(tokens: tuple[str, ...], width: int) -> set[tuple[str, ...]]:
    if len(tokens) <= width:
        return {tokens}
    return {tokens[at : at + width] for at in range(len(tokens) - width + 1)}


def clusters(
    units: Iterable[Unit], *, min_tokens: int, coverage: float, width: int, length_ratio: float, probes: int | None
) -> list[Cluster]:
    """Group units whose token runs contain one another, shingle-blocked and sorted.

    `length_ratio` is what keeps containment from chaining: a short span sits
    inside a long one at 100% and would otherwise link everything the long one
    touches. Two spans whose lengths differ by more than this are not compared.

    `probes` indexes each unit under only its rarest windows, and is for a corpus
    whose token vocabulary is small — a code shape draws on a few dozen AST node
    types, so its common windows say nothing and expanding them to pairs costs
    tens of millions of comparisons. None indexes every window, which is what
    natural-language spans want: a restatement's rarest windows are often exactly
    the words it added, so probing is where it would lose its partner.
    """
    kept = sorted((unit for unit in units if len(unit.tokens) >= min_tokens), key=lambda unit: unit.sort_key)

    per_unit = [_shingles(unit.tokens, width) for unit in kept]
    frequency: Counter[tuple[str, ...]] = Counter()
    for shingles in per_unit:
        frequency.update(shingles)

    buckets: dict[tuple[str, ...], list[int]] = {}
    for index, shingles in enumerate(per_unit):
        indexed = sorted(shingles, key=lambda window: (frequency[window], window))
        for shingle in indexed[:probes] if probes else indexed:
            buckets.setdefault(shingle, []).append(index)

    pairs: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) > MAX_BUCKET:
            continue
        pairs.update(combinations(members, 2))  # members are appended in index order

    parent = list(range(len(kept)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in sorted(pairs):
        if root(left) == root(right):  # already linked — this edge can change nothing
            continue
        sizes = (len(kept[left].tokens), len(kept[right].tokens))
        if min(sizes) / max(sizes) < length_ratio:
            continue
        if containment(kept[left].tokens, kept[right].tokens) >= coverage:
            parent[root(right)] = root(left)

    groups: dict[int, list[int]] = {}
    for index in range(len(kept)):
        groups.setdefault(root(index), []).append(index)

    found = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # The shortest member is the tightest statement of the shared idea, and
        # the one a reader should be shown.
        representative = min((kept[index] for index in members), key=lambda unit: (len(unit.tokens), unit.sort_key))
        scored = tuple(
            sorted(
                ((kept[index], containment(kept[index].tokens, representative.tokens)) for index in members),
                key=lambda scored_unit: (-scored_unit[1], scored_unit[0].sort_key),
            )
        )
        found.append(Cluster(representative, scored))
    found.sort(key=lambda cluster: (-len(cluster.members), cluster.representative.sort_key))
    return found


# ── prose ────────────────────────────────────────────────────────────────────


def prose_tokens(text: str) -> tuple[str, ...]:
    """Lowercased words, chunk markers dropped. Code-span contents are kept: two
    sentences differing only in the identifier they name are still one idea."""
    return tuple(_WORD.findall(_MARKER.sub(" ", text).lower()))


def _split_sentences(text: str) -> list[str]:
    return [piece.strip() for piece in _SENTENCE_END.split(text) if piece.strip()]


def line_sentences(text: str) -> Iterator[tuple[int, str]]:
    """(1-based line, sentence) over markdown-ish prose, fenced code dropped.

    Per line, because a bullet is a line: joining a list into a paragraph would
    merge bullets that carry no terminal punctuation into one unreadable span.
    """
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        body = _LIST_LEAD.sub("", line).strip()
        for sentence in _split_sentences(body):
            yield number, sentence


def block_sentences(numbered: Sequence[tuple[int, str]]) -> Iterator[tuple[int, str]]:
    """(1-based line, sentence) over hard-wrapped prose, rejoined across its wrap.

    Takes already-numbered lines so a docstring's offset and a run of comment
    lines reach it the same way. A blank line ends a block.
    """
    block: list[str] = []
    start = 0
    for number, line in [*numbered, (0, "")]:
        if line.strip():
            if not block:
                start = number
            block.append(line.strip())
            continue
        for sentence in _split_sentences(" ".join(block)):
            yield start, sentence
        block = []


def template_body(text: str) -> tuple[int, str]:
    """(1-based line the body starts at, the body) — `+++` fence and frontmatter dropped.

    Neither renders into a prompt: the fence declares a multi-output template's
    parameters and carries maintainer comments, and the frontmatter is dispatch
    metadata. A sentence in either is not prose a seat reads.
    """
    lines = text.splitlines()
    at = 0
    for opener in ("+++", "---"):
        while at < len(lines) and not lines[at].strip():
            at += 1
        if at < len(lines) and lines[at].strip() == opener:
            closing = next((index for index in range(at + 1, len(lines)) if lines[index].strip() == opener), None)
            if closing is None:
                log.warning("unclosed %s block — the whole file is read as body", opener)
                break
            at = closing + 1
    return at + 1, "\n".join(lines[at:])


def chunk_bodies(text: str) -> list[tuple[str, int, str]]:
    """(label, header line, body) for every chunk `text` body and every variant body."""
    data = tomllib.loads(text)
    header_lines: dict[str, int] = {}
    for match in _CHUNK_HEADER.finditer(text):
        header_lines.setdefault(match.group(1), text.count("\n", 0, match.start()) + 1)

    bodies = []
    for name, chunk in sorted(data.get("chunks", {}).items()):
        line = header_lines.get(name, 1)
        if isinstance(chunk.get("text"), str):
            bodies.append((f"chunks.{name}", line, chunk["text"]))
        for variant, body in sorted(chunk.get("variants", {}).items()):
            if isinstance(body, str):
                bodies.append((f"chunks.{name}.{variant}", line, body))
    return bodies


def prose_units(*, rev: str | None) -> list[Unit]:
    """Every sentence of the template corpus, chunk bodies and inline prose alike."""
    units: list[Unit] = []
    for path, text in corpus(rev=rev, prefixes=[CHUNK_SOURCE], suffix=".toml").items():
        for label, line, body in chunk_bodies(text):
            for _, sentence in line_sentences(body):
                units.append(Unit(path, line, label, sentence, prose_tokens(sentence)))
    for path, text in corpus(rev=rev, prefixes=TEMPLATE_PREFIXES, suffix=".md.tmpl").items():
        start, body = template_body(text)
        for offset, sentence in line_sentences(body):
            units.append(Unit(path, start + offset - 1, "", sentence, prose_tokens(sentence)))
    return units


# ── python source ────────────────────────────────────────────────────────────


def code_corpus(*, rev: str | None) -> dict[str, str]:
    swept = corpus(rev=rev, prefixes=[CODE_PREFIX], suffix=".py")
    return {path: text for path, text in swept.items() if not path.startswith(CODE_UNSWEPT)}


def _parse(path: str, text: str) -> ast.Module | None:
    try:
        return ast.parse(text, filename=path)
    except SyntaxError as exc:
        log.warning("%s does not parse (%s) — skipped", path, exc)
        return None


def _shape(node: ast.AST) -> list[str]:
    shape = []
    for child in ast.iter_child_nodes(node):
        shape.append(type(child).__name__)
        shape.extend(_shape(child))
    return shape


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            return body[1:]
    return body


def shape_units(files: dict[str, str]) -> list[Unit]:
    """One unit per function — top-level or a method — carrying its structural shape.

    A function nested inside another is not its own unit: its shape is already
    part of its parent's, so the two would report as duplicates of each other.
    """
    units = []
    for path, text in sorted(files.items()):
        tree = _parse(path, text)
        if tree is None:
            continue
        lines = text.splitlines()
        for owner, function in _functions(tree):
            tokens = _shape(function.args) + [
                token
                for statement in _without_docstring(function.body)
                for token in [type(statement).__name__, *_shape(statement)]
            ]
            label = f"{owner}.{function.name}()" if owner else f"{function.name}()"
            signature = lines[function.lineno - 1].strip() if function.lineno <= len(lines) else label
            units.append(Unit(path, function.lineno, label, signature, tuple(tokens)))
    return units


def _functions(tree: ast.Module) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """(owning class name or "", function) for every module-level function and method."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield "", node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield node.name, member


def doc_units(files: dict[str, str]) -> list[Unit]:
    """Every sentence of every docstring and comment, rejoined across its hard wrap."""
    units = []
    for path, text in sorted(files.items()):
        tree = _parse(path, text)
        if tree is None:
            continue
        for owner, doc, first_line in _docstrings(tree):
            lines = list(enumerate(doc.splitlines(), start=first_line))
            for line, sentence in block_sentences(lines):
                units.append(Unit(path, line, owner, sentence, prose_tokens(sentence)))
        for line, sentence in block_sentences(_comment_lines(path, text)):
            units.append(Unit(path, line, "comment", sentence, prose_tokens(sentence)))
    return units


def _docstrings(tree: ast.Module) -> Iterator[tuple[str, str, int]]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        doc = ast.get_docstring(node, clean=True)
        if doc:
            name = "module docstring" if isinstance(node, ast.Module) else f"{node.name} docstring"
            yield name, doc, node.body[0].lineno


def _comment_lines(path: str, text: str) -> list[tuple[int, str]]:
    """(line, comment text) for every comment, with a blank line between blocks.

    The blank separates comments that merely sit near each other, so
    :func:`block_sentences` rejoins a wrapped comment and nothing else.
    """
    lines: list[tuple[int, str]] = []
    previous = 0
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as exc:
        log.warning("%s does not tokenize (%s) — its comments are skipped", path, exc)
        return lines
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if line != previous + 1:
            lines.append((line, ""))
        lines.append((line, token.string.lstrip("#:").strip()))
        previous = line
    return lines


@dataclass(frozen=True)
class Constant:
    """One module-level binding of a literal value."""

    path: str
    line: int
    name: str
    value: str


def constants(files: dict[str, str]) -> list[Constant]:
    """Module-level literal bindings, trivia dropped."""
    found = []
    for path, text in sorted(files.items()):
        tree = _parse(path, text)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
            else:
                continue
            value = _literal(node.value)
            if value is None:
                continue
            for target in targets:
                if not target.id.startswith("__"):
                    found.append(Constant(path, node.lineno, target.id, value))
    return found


def _literal(node: ast.expr | None) -> str | None:
    """`repr` of a literal expression, or None when it is not one or is trivia."""
    if node is None:
        return None
    try:
        value = ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    shown = repr(value)
    return None if shown in _TRIVIAL_LITERALS else shown


def _distinctive(value: str) -> bool:
    """Whether a value is identifying enough for two bindings of it to mean anything.

    A bare small integer is not: `EXIT_USAGE = 2` and `EDGE_STROKE_WIDTH = 2` share
    a value and nothing else, and reporting every such coincidence is how a
    candidate list stops being read. The repeated-name check still covers them.
    """
    try:
        return abs(float(value)) >= _DISTINCTIVE_MAGNITUDE
    except ValueError:
        return len(value) >= _DISTINCTIVE_WIDTH


def _similar(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left.lower(), right.lower(), autojunk=False).ratio()


def constant_candidates(found: Sequence[Constant], *, name_similarity: float = 0.6) -> list[tuple[str, list[Constant]]]:
    """(what makes them look alike, sites) for repeated values and repeated names."""
    candidates: list[tuple[str, list[Constant]]] = []

    by_value: dict[str, list[Constant]] = {}
    by_name: dict[str, list[Constant]] = {}
    for constant in sorted(found, key=lambda item: (item.path, item.line, item.name)):
        by_value.setdefault(constant.value, []).append(constant)
        by_name.setdefault(constant.name, []).append(constant)

    # Names first: a group both rules find is the repeated name, and reporting it
    # again as a repeated value says nothing the reader did not just read.
    reported: set[frozenset[tuple[str, int]]] = set()
    for name, sites in sorted(by_name.items()):
        if len(sites) < 2:
            continue
        modules = {site.path for site in sites}
        where = "twice in one module" if len(modules) == 1 else f"in {len(modules)} modules"
        candidates.append((f"one name bound {where}: {name}", sites))
        reported.add(frozenset((site.path, site.line) for site in sites))

    for value, sites in sorted(by_value.items()):
        modules = {site.path for site in sites}
        if len(modules) < 2 or not _distinctive(value):
            continue
        if frozenset((site.path, site.line) for site in sites) in reported:
            continue
        pairs = combinations(sites, 2)
        if any(left.path != right.path and _similar(left.name, right.name) >= name_similarity for left, right in pairs):
            candidates.append((f"one value in {len(modules)} modules under similar names: {_ellipsis(value)}", sites))

    candidates.sort(key=lambda item: (-len(item[1]), item[0]))
    return candidates


# ── report ───────────────────────────────────────────────────────────────────


def _ellipsis(text: str, width: int = 150) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def file_pairs(found: Sequence[Cluster]) -> list[tuple[int, str, str]]:
    """(shared candidates, path, path) for file pairs that cluster together repeatedly.

    Forty near-identical sentences between two definitions is one finding, not
    forty, and the forty rows are what stops a report from being read. A file
    paired with itself is a rule stated twice inside one file.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for cluster in found:
        paths = sorted(unit.path for unit, _ in cluster.members)
        counts.update({(left, right) for left, right in combinations(paths, 2)})
    return sorted(
        ((shared, left, right) for (left, right), shared in counts.items() if shared > 1),
        key=lambda row: (-row[0], row[1], row[2]),
    )


def render_pairs(found: Sequence[Cluster], *, out) -> None:
    pairs = file_pairs(found)
    if not pairs:
        return
    print(f"\n### file pairs sharing more than one candidate — {len(pairs)}", file=out)
    for shared, left, right in pairs:
        print(f"    {shared:>4}  {left}  <->  {'itself' if left == right else right}", file=out)


def render_clusters(title: str, found: Sequence[Cluster], *, out) -> None:
    print(f"\n## {title} — {len(found)} candidate{'' if len(found) == 1 else 's'}", file=out)
    render_pairs(found, out=out)
    for number, cluster in enumerate(found, start=1):
        scores = [score for _, score in cluster.members]
        span = f"{min(scores):.0%}" if min(scores) == max(scores) else f"{min(scores):.0%}-{max(scores):.0%}"
        print(
            f"\n[{number}] {len(cluster.members)} sites, {span} of a "
            f"{len(cluster.representative.tokens)}-token span shared",
            file=out,
        )
        print(f"    {_ellipsis(cluster.representative.text)}", file=out)
        for unit, score in cluster.members:
            print(f"    {score:>4.0%}  {unit.site}", file=out)


def render_constants(found: Sequence[tuple[str, list[Constant]]], *, out) -> None:
    print(f"\n## one constant, two definitions — {len(found)} candidate{'' if len(found) == 1 else 's'}", file=out)
    for number, (why, sites) in enumerate(found, start=1):
        print(f"\n[{number}] {_ellipsis(why)}", file=out)
        for site in sites:
            print(f"    {site.path}:{site.line} {site.name} = {_ellipsis(site.value, 60)}", file=out)


def _header(pass_name: str, rev: str | None, scope: str, units: int, out) -> None:
    print(f"# {pass_name} sweep — {scope}", file=out)
    print(f"# tree: {rev or 'working tree'} — {units} spans compared", file=out)
    print("# candidates, not verdicts: every one below is a question for a reader.", file=out)


def prose_pass(*, rev: str | None, settings: dict, out) -> None:
    units = prose_units(rev=rev)
    found = clusters(units, **settings)
    _header("prose", rev, f"{CHUNK_SOURCE}, {', '.join(TEMPLATE_PREFIXES)}", len(units), out)
    render_clusters("one sentence, two places", found, out=out)


def python_pass(*, rev: str | None, settings: dict, out) -> None:
    files = code_corpus(rev=rev)
    shapes = shape_units(files)
    docs = doc_units(files)
    _header("python", rev, f"{CODE_PREFIX}/ less {', '.join(CODE_UNSWEPT)}", len(shapes) + len(docs), out)
    render_clusters("one concept, two implementations", clusters(shapes, **settings), out=out)
    render_constants(constant_candidates(constants(files)), out=out)
    render_clusters("one rule, two homes", clusters(docs, **DOC_DEFAULTS), out=out)
    print("\n## one word, two meanings — UNANSWERED, not zero candidates", file=out)
    print(UNANSWERED, file=out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dupe_sweep",
        description="Enumerate one-idea-two-places candidates. Emits candidates, never verdicts.",
    )
    parser.add_argument("pass_name", choices=("prose", "python"), metavar="PASS", help="prose | python")
    parser.add_argument("--rev", help="sweep the tree at this git revision instead of the working tree")
    parser.add_argument("--min-words", type=int, help="shortest span compared (default: 8 prose, 30 AST nodes)")
    parser.add_argument(
        "--coverage", type=float, help="containment a pair needs to be a candidate (default: 0.8 / 0.9)"
    )
    return parser


def main(argv: Sequence[str] | None = None, *, out=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    settings = dict(PROSE_DEFAULTS if args.pass_name == "prose" else CODE_DEFAULTS)
    if args.min_words is not None:
        settings["min_tokens"] = args.min_words
    if args.coverage is not None:
        settings["coverage"] = args.coverage
    try:
        if args.pass_name == "prose":
            prose_pass(rev=args.rev, settings=settings, out=out)
        else:
            python_pass(rev=args.rev, settings=settings, out=out)
    except SweepError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
