#!/usr/bin/env python3
"""Citation-grammar gate for a KB's authored files.

A claim may be referenced only through a sanctioned CHANNEL, and an authority
may be cited only in a sanctioned FORM. Prose that names a claim or an
invariant in passing is neither: it reads as authority while carrying none,
which is how a wrong rule rationalizes itself into a corpus.

Sanctioned reference channels (a claim/experiment/support id may appear here):

  - leaf frontmatter fields (``claims:``, ``depends-on:``, ``exp-id:``, ...)
  - sidecar structured fields — a register entry's ``- <field>:`` bullets
  - Tier-2 markers — ``<!-- claim-quality: <id> ... -->``
  - a markdown link whose target is a KB path

Sanctioned authority-citation form:

    ["<excerpt>"](<kb-relative-path>#<anchor>)

the link text carrying the minimal quoted excerpt and the target a durable KB
path plus anchor.

Five checks, all errors:

  1. channel exclusivity — citation-shaped prose outside the sanctioned forms
  2. referent existence  — every citation link resolves
  3. excerpt match       — the quoted excerpt appears at the target's section,
                           whitespace-normalized, and is bounded to one clause
  4. durable target      — targets live inside kb-root, never scratch or
                           outside the repo
  5. edge-backed foreign reference — a foreign-domain id in a register entry
                           needs a matching depends-on edge, or an entry-level
                           ``no-edge: <reason>`` exemption

SCOPE — authored files only. Leaf BODIES are exempt: a leaf is a verbatim
translation of its source, and its claim-graph obligations live in its
frontmatter and markers, which ARE in scope. Registers, indexes, summaries,
``invariants.md`` and the meta-docs are scanned whole. ``.index/`` is out of
scope entirely — it is contract and derived space, not build-authored content
(the exclusion comes free from ``kb_links.SKIP_DIRS``).

Every check is code-fence aware: an example inside a fence is documentation,
not a citation.

Stdlib only.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from kb_tools import __version__, kb_index_lib, kb_links, kb_schema, kb_util

# A markdown link, text and target both captured — the citation form needs
# them together, where the shared LINK_RE primitive needs only the target. The
# text pattern is shared so a `]` inside an excerpt cannot make the citation
# invisible to one gate and visible to the other.
CITATION_LINK_RE = re.compile(rf"\[({kb_links.LINK_TEXT})\]\(\s*([^)\s]+)\s*\)")
# The quoted-excerpt link text an authority citation carries. Typographic quote
# pairs count: an editor that curls the quotes must not turn an authority
# citation into an unchecked reference link.
# `.*`, not `.+`: `[""](t.md#a)` is an authority citation carrying nothing, and
# it has to be *recognized* as one before it can be rejected as one. With `.+`
# it fell through as an ordinary reference link and no check ever ran.
EXCERPT_RE = re.compile(r'^\s*(?:"(.*)"|“(.*)”|‘(.*)’)\s*$', re.DOTALL)
# Citation-shaped prose. The first is design-doc plain ordered-list numbering,
# which reads as authority while carrying no durable id.
PLAIN_INVARIANT_RE = re.compile(r"\bInvariant [0-9]+\b")
INVARIANT_ID_RE = re.compile(r"\bINVARIANT-[A-Z]+[0-9]+\b")
NODE_ID_RE = re.compile(rf"\b{kb_schema.id_body()}\b")
# A `### INVARIANT-XX: title` declaration heading, and any heading at all.
DECLARATION_HEADING_RE = re.compile(r"^###\s+INVARIANT-[A-Z]+[0-9]+\s*:")
HEADING_RE = re.compile(r"^(#{1,6})\s")
# The two sanctioned HTML-comment channels this corpus actually uses: the
# Tier-2 inline marker in a multi-claim leaf, and the canonical-id marker that
# opens a register entry. The frozen grammar names only the first; the second
# is the register's own entry header and is sanctioned in substance.
TIER2_MARKER_RE = re.compile(r"<!--\s*(?:claim-quality|id):.*?-->", re.DOTALL)
ENTRY_MARKER_RE = re.compile(rf"<!--\s*id:\s*({kb_schema.id_body()})")
# A register entry's structured field: the `- <field>:` bullet plus the
# indented bullets that continue it (a depends-on list, for instance).
FIELD_BULLET_RE = re.compile(r"^-\s+[a-z][a-z-]*:")
FIELD_CONTINUATION_RE = re.compile(r"^\s+\S")
NO_EDGE_RE = re.compile(r"^\s*-\s+no-edge:\s*(\S.*)$")
FRONTMATTER_RE = re.compile(r"<!--\s*kb-frontmatter\b.*?-->", re.DOTALL)

# An excerpt is a minimal quotation, not a pasted section: one line, and short
# enough that quoting a whole paragraph is a failure rather than a habit.
EXCERPT_MAX_CHARS = 240

LEAF_KINDS = ("leaf", "leaf-as-index")


@dataclass(frozen=True)
class Finding:
    """One violation, reported in the shape the other gates report."""

    check: str
    path: str
    line: int
    message: str


def _blank(text: str, span: tuple[int, int]) -> str:
    """Blank a span, preserving every newline so line numbers survive."""
    start, end = span
    return text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]


def _blank_all(text: str, pattern: re.Pattern[str]) -> str:
    for match in reversed(list(pattern.finditer(text))):
        text = _blank(text, match.span())
    return text


def frontmatter_span(text: str) -> tuple[int, int] | None:
    match = FRONTMATTER_RE.search(text)
    return match.span() if match else None


def leaf_kind(text: str) -> str | None:
    """The ``kind:`` a file's kb-frontmatter declares, or None."""
    match = FRONTMATTER_RE.search(text)
    if match is None:
        return None
    kind = re.search(r"^kind:\s*(\S+)", match.group(0), re.MULTILINE)
    return kind.group(1) if kind else None


def in_scope_text(text: str) -> str:
    """The part of a file the checks read, everything else blanked.

    A leaf contributes only its frontmatter and its Tier-2 markers — its body
    is a verbatim translation of the source and carries no citation
    obligations. Every other authored file is scanned whole. Fenced and inline
    code is blanked either way: an example is documentation, not a citation.
    """
    text = kb_links.strip_code(text)
    if leaf_kind(text) not in LEAF_KINDS:
        return text
    keep = [span for span in (frontmatter_span(text),) if span is not None]
    keep += [m.span() for m in TIER2_MARKER_RE.finditer(text)]
    blanked = re.sub(r"[^\n]", " ", text)
    return "".join(text[i] if any(start <= i < end for start, end in keep) else blanked[i] for i in range(len(text)))


def sanctioned_channels_blanked(text: str, *, declaration_sections: bool) -> str:
    """``text`` with every sanctioned channel blanked, leaving only prose.

    What remains is where a claim id or an invariant token would be bare
    citation-shaped prose. ``declaration_sections`` additionally blanks the
    body of each ``### INVARIANT-*`` section, which is the framework source's
    own declaration space: an invariant's section may name its siblings.
    """
    text = _blank_all(text, FRONTMATTER_RE)
    text = _blank_all(text, TIER2_MARKER_RE)
    text = _blank_all(text, CITATION_LINK_RE)
    lines = text.split("\n")
    in_declaration = False
    in_field = False
    for i, line in enumerate(lines):
        if declaration_sections:
            heading = HEADING_RE.match(line)
            if DECLARATION_HEADING_RE.match(line):
                in_declaration = True
            elif heading is not None and len(heading.group(1)) <= 3:
                in_declaration = False
        if FIELD_BULLET_RE.match(line):
            in_field = True
        elif in_field and not FIELD_CONTINUATION_RE.match(line):
            in_field = False
        if in_declaration or in_field:
            lines[i] = " " * len(line)
    return "\n".join(lines)


def check_channel_exclusivity(path: str, text: str, *, is_framework_source: bool) -> list[Finding]:
    """Check 1 — citation-shaped prose outside a sanctioned channel."""
    prose = sanctioned_channels_blanked(text, declaration_sections=is_framework_source)
    findings = []
    rules = (
        (
            PLAIN_INVARIANT_RE,
            "plain-numbered invariant reference in prose — design-doc ordered-list "
            "numbering is not a durable id. Cite the invariant's declaration: "
            '["<excerpt>"](invariants.md#<anchor>)',
        ),
        (
            INVARIANT_ID_RE,
            "invariant id in prose outside its declaration section. Cite it: " '["<excerpt>"](invariants.md#<anchor>)',
        ),
        (
            NODE_ID_RE,
            "bare claim/experiment/support id in prose. Reference it through a "
            "sanctioned channel — frontmatter, a register field, a Tier-2 marker, "
            "or a markdown link to its KB path",
        ),
    )
    for line_no, line in enumerate(prose.split("\n"), start=1):
        for pattern, remedy in rules:
            for match in pattern.finditer(line):
                findings.append(Finding("channel", path, line_no, f"{match.group(0)!r}: {remedy}"))
    return findings


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check_citations(path: str, text: str, kb_root: Path, source: Path) -> list[Finding]:
    """Checks 2, 3 and 4 over every markdown link in an authored file."""
    findings = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        for match in CITATION_LINK_RE.finditer(line):
            link_text, target = match.group(1), match.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue  # external scheme or same-page anchor
            raw_path, _, anchor = target.partition("#")
            resolved = (source.parent / kb_links.strip_target(raw_path)).resolve()

            # Check 4 — durable target.
            if not resolved.is_relative_to(kb_root.resolve()):
                findings.append(
                    Finding(
                        "durable",
                        path,
                        line_no,
                        f"citation target {target!r} resolves outside {kb_util.KB_DIRNAME}/ — "
                        f"cite a durable KB path; scratch and repo-external paths do not survive",
                    )
                )
                continue

            # Check 2 — referent existence.
            if not resolved.is_file():
                findings.append(
                    Finding("referent", path, line_no, f"citation target {target!r} does not resolve to a file")
                )
                continue

            excerpt_match = EXCERPT_RE.match(link_text)
            if excerpt_match is None:
                continue  # a reference link, not an authority citation
            excerpt = next(group for group in excerpt_match.groups() if group is not None)

            # Check 3a — the excerpt is minimal, and is an excerpt at all. A
            # blank one normalizes to "", and "" is a substring of every
            # section, so it verifies as authority against anything.
            if not _normalize(excerpt):
                findings.append(
                    Finding(
                        "excerpt",
                        path,
                        line_no,
                        f"citation of {target!r} carries an empty quoted excerpt — an authority "
                        f"citation quotes the clause it rests on, and a blank quotation carries "
                        f"nothing while reading as a verified one",
                    )
                )
                continue
            if "\n" in excerpt or len(excerpt) > EXCERPT_MAX_CHARS:
                findings.append(
                    Finding(
                        "excerpt",
                        path,
                        line_no,
                        f"citation excerpt is {len(excerpt)} chars"
                        f"{' and spans lines' if chr(10) in excerpt else ''} — an excerpt is the "
                        f"minimal quotation that carries the point: one line, at most "
                        f"{EXCERPT_MAX_CHARS} chars. Quote the clause, not the section",
                    )
                )
                continue

            # Check 3b — the excerpt is really there.
            if not anchor:
                findings.append(
                    Finding(
                        "excerpt",
                        path,
                        line_no,
                        f"citation of {target!r} carries a quoted excerpt but no #anchor — "
                        f"an authority citation names the section it quotes",
                    )
                )
                continue
            # The TARGET is fence-scrubbed too, inside `anchor_section` itself.
            # Without that the module's own "every check is code-fence aware"
            # would hold only of the citing file: an excerpt — or a heading —
            # existing solely inside a fenced example at the target would verify
            # as authority, which is a citation of documentation *about* a rule
            # as though it were the rule. The section finder is
            # `kb_index_lib`'s rather than this module's so that the op which
            # *composes* a citation asks the same question this check asks —
            # a composer with its own section reader could print a citation
            # this gate then rejects.
            body = kb_index_lib.anchor_section(resolved.read_text(encoding="utf-8"), anchor)
            if body is None:
                findings.append(
                    Finding("referent", path, line_no, f"citation anchor {'#' + anchor!r} not found in {raw_path}")
                )
            elif _normalize(excerpt) not in _normalize(body):
                findings.append(
                    Finding(
                        "excerpt",
                        path,
                        line_no,
                        f"quoted excerpt does not appear at {target!r} — an excerpt is quoted "
                        f"verbatim from the section it cites",
                    )
                )
    return findings


def _domain_of(canonical_path: str) -> str:
    """A node's domain: its top-level directory under kb-root."""
    parts = Path(canonical_path).parts
    return parts[0] if len(parts) > 1 else ""


def load_node_domains(index_dir: Path) -> dict[str, str]:
    """id -> domain, from the derived node register."""
    claims = index_dir / kb_util.CLAIMS_FILENAME
    if not claims.is_file():
        return {}
    domains = {}
    for raw in claims.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "id" in record and "canonical_path" in record:
            domains[record["id"]] = _domain_of(record["canonical_path"])
    return domains


def load_edges(index_dir: Path) -> set[tuple[str, str]]:
    """(source, target) pairs from the depends-on edge register."""
    path = index_dir / "depends-on.jsonl"
    if not path.is_file():
        return set()
    edges = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "source" in record and "target" in record:
            edges.add((record["source"], record["target"]))
    return edges


def check_foreign_domain_edges(
    path: str, text: str, domains: dict[str, str], edges: set[tuple[str, str]]
) -> list[Finding]:
    """Check 5 — a foreign-domain id in a register entry needs an edge.

    A register entry naming a node from another domain is asserting a
    cross-domain dependency; it has to be in the graph, or be explicitly
    exempted, or the graph and the prose disagree about what depends on what.
    """
    findings = []
    entry_id: str | None = None
    entry_line = 0
    exempt = False
    for line_no, line in enumerate(text.split("\n"), start=1):
        marker = ENTRY_MARKER_RE.search(line)
        if marker:
            entry_id, entry_line, exempt = marker.group(1), line_no, False
            continue
        if entry_id is None:
            continue
        if NO_EDGE_RE.match(line):
            exempt = True
            continue
        own_domain = domains.get(entry_id)
        if own_domain is None or exempt:
            continue
        for referenced in NODE_ID_RE.findall(line):
            if referenced == entry_id:
                continue
            other = domains.get(referenced)
            if other is None or other == own_domain:
                continue
            if (entry_id, referenced) in edges:
                continue
            findings.append(
                Finding(
                    "edge",
                    path,
                    line_no,
                    f"{entry_id} (domain {own_domain!r}) references foreign-domain "
                    f"{referenced} (domain {other!r}) with no depends-on edge. Add the edge, "
                    f"or exempt this entry with a '- no-edge: <reason>' line",
                )
            )
    _ = entry_line
    return findings


def authored_files(kb_root: Path) -> list[Path]:
    """Every authored Markdown file in scope, sorted.

    ``.index/`` is skipped by ``kb_links.SKIP_DIRS``, which is the boundary:
    contract and derived space is not build-authored content.
    """
    return sorted(kb_links.iter_markdown_files(kb_root))


def scan(kb_root: Path, index_dir: Path) -> list[Finding]:
    framework = kb_index_lib.framework_source(kb_root)
    domains = load_node_domains(index_dir)
    edges = load_edges(index_dir)
    findings: list[Finding] = []
    for source in authored_files(kb_root):
        raw = source.read_text(encoding="utf-8")
        text = in_scope_text(raw)
        rel = str(source.relative_to(kb_root))
        findings += check_channel_exclusivity(
            rel, text, is_framework_source=framework is not None and source.samefile(framework)
        )
        findings += check_citations(rel, text, kb_root, source)
        if source.name == "claim-quality.md":
            findings += check_foreign_domain_edges(rel, text, domains, edges)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    parser.add_argument("--kb-root", type=Path, default=None, help="KB root to scan (default: resolved via kb_util)")
    args = parser.parse_args(argv)
    if args.kb_root is not None:
        kb_root = args.kb_root
    else:
        try:
            kb_root = kb_util.kb_root()
        except kb_util.RepoRootError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
    if not kb_root.is_dir():
        print(f"FAIL: KB directory {kb_root} not found.", file=sys.stderr)
        return 2

    findings = scan(kb_root, kb_root / kb_util.INDEX_DIRNAME)
    files = authored_files(kb_root)
    print(
        f"[citations] Scanned {len(files)} authored file(s) under {kb_root} "
        f"(leaf bodies exempt; {kb_util.INDEX_DIRNAME}/ out of scope)."
    )
    for finding in sorted(findings, key=lambda f: (f.path, f.line, f.check)):
        print(f"  [{finding.check}] {finding.path}:{finding.line}: {finding.message}")
    if findings:
        print(f"\n[citations] FAIL — {len(findings)} citation-grammar violation(s).", file=sys.stderr)
        return 1
    print("[citations] PASS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
