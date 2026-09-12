#!/usr/bin/env python3
"""Measure the mechanical structure an arXiv paper carries, before building it.

Everything the pipeline can extract without inference is visible in the LaTeX
source: which environments an author declared, how many results carry a
``\\label``, whether cross-references point at results or at sections, and which
bibliography form the submission shipped. This reads that off the source and
reports it per paper, so a corpus can be sized before a build is spent on it.

**Why a survey rather than builds.** A zero-inference build over one paper takes
minutes and halts on the first thing it cannot classify. The grep takes a second
and never halts, so it answers "which of these is worth building" for a whole
sample at once — and the environment names it collects are what the closed
classification table would have to admit.

**Sample by category, not by citation.** Construction style tracks field far more
closely than it tracks any individual paper's merit: ``math.*`` is theorem-dense
and reference-heavy, ``cs.LG`` states its claims in benchmark tables and declares
almost no environments. Ranking by citations selects for the second, which
measures the empty case repeatedly. Categories worth spanning:

    math.AG math.DG math.NT   theorem-dense pure
    math.ST stat.TH           theorem-dense applied
    econ.TH                   theory econ
    cs.LG cs.CV               empirical, near-zero environments
    physics.optics            figure-heavy, mixed
    eess.AS eess.SP           signal processing
    q-bio.QM                  often minimal structure

Sample across time as well. arXiv began processing ``.bib`` files itself in
November 2025, so recent submissions increasingly ship the database and older
ones ship a pre-compiled ``.bbl`` — a few from each covers both paths.

**Counts say what a paper declares; containment says what it connects.** A
paper with two hundred ``\\ref`` and forty theorems may have every reference
pointing at a section, in which case none of them is a claim-to-claim edge and
the count measured nothing about the graph. Two readings answer that, and both
need to know where an environment's body begins and ends rather than only that
it was declared:

* a ``\\ref`` inside a ``proof`` body is direction-bearing by construction — the
  proof establishes the claim it belongs to, so the proved claim rests on what
  the proof cites, and nobody has to infer the direction;
* a ``\\ref`` whose target ``\\label`` sits inside a claim-bearing body points at
  a *result* rather than at a document. That count is the ceiling on how many of
  a paper's cross-references could ever become claim-to-claim edges.

Both are reported as counts and as a fraction of that paper's own ``\\ref``
total, because a long paper and a short one are not comparable on counts.

**Two source paths, one measurement.** ``--category`` fetches from arXiv;
``--staged`` reads papers already unpacked by ``just stage-arxiv-paper``, makes
no network request, and takes its category grouping from the ``ARXIV_*``
variables in ``kb-testing/justfile`` so the sample's structure is stated once.
Everything past the source map is shared, so the two paths cannot drift.

Stdlib only, so it runs anywhere python3 does. Downloads land under the scratch
directory the caller names; nothing is written outside it.
"""

import argparse
import ast
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

API = "http://export.arxiv.org/api/query"
EPRINT = "https://arxiv.org/e-print/"

#: The classification vocabulary and the sample's category grouping are each
#: stated in exactly one place, and this file is neither of them.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_INVENTORY = _REPO_ROOT / "kb_tools" / "kb_claimgraph" / "inventory.py"
_KB_TESTING_JUSTFILE = _REPO_ROOT / "kb-testing" / "justfile"

#: A staged paper directory is a throwaway git consumer with the agent set
#: installed. Neither tree is the paper, and `.git/objects` in particular is
#: full of extensionless files this scan would otherwise read as LaTeX.
_STAGED_EXCLUDE = frozenset({".claude", ".git", ".claude-temp"})

#: arXiv asks callers to space API requests. Their documented courtesy is three
#: seconds; this is not a rate limit we are working around but the one they
#: publish, and a survey of thirty papers pays it thirty times for no benefit to
#: anyone if it is skipped.
COURTESY_DELAY = 3.0

_ID = re.compile(r"<id>http://arxiv\.org/abs/([^<]+)</id>")

# --- what a paper declares ---------------------------------------------------
#
# Each pattern answers one question the build would otherwise answer expensively.
# `\newtheorem` names are the vocabulary a closed classification table must
# admit; label prefixes say whether the author uses a taxonomy at all; the
# ref/eqref split and the target prefix say whether a cross-reference can reach a
# result or only a section.

_NEWTHEOREM = re.compile(r"\\newtheorem\*?\{([^}]*)\}(?:\[[^\]]*\])?\{([^}]*)\}")
_LABEL = re.compile(r"\\label\{([^}]*)\}")
_REF = re.compile(r"\\(ref|cref|Cref|autoref|eqref)\{([^}]*)\}")
_CITE = re.compile(r"\\(cite[a-zA-Z]*)\{([^}]*)\}")
_DOCUMENTCLASS = re.compile(r"\\documentclass(?:\[[^\]]*\])?\{([^}]*)\}")
_PROOF = re.compile(r"\\begin\{proof\}")

#: `\begin{proof}[Proof of Theorem 3]` — an environment's opening may carry an
#: optional argument, and it is part of the delimiter rather than of the body.
#: Bounded to one line so a stray `[` a paragraph later cannot be swallowed.
_OPTIONAL_ARGUMENT = re.compile(r"[ \t]*\[[^\]\n]*\]")

#: `\cref{a,b}` names two targets in one command. The survey counts commands,
#: not targets, so this splits only for the question "does this command reach a
#: claim" — a command reaching one claim and one section counts once.
_TARGET_SEPARATOR = re.compile(r"\s*,\s*")


@dataclass(frozen=True)
class Vocabulary:
    """The claim-bearing display names, and every name anybody has classified."""

    claim_bearing: frozenset[str]
    classified: frozenset[str]


def _frozenset_literal(module: Path, name: str) -> frozenset[str]:
    """A module-level ``NAME = frozenset({...})``, read off the source.

    Read rather than imported: ``kb_claimgraph.inventory`` pulls the rest of its
    package in behind it, and this survey is stdlib-only so it runs against a
    checkout with nothing installed. Reading the literal keeps the vocabulary
    single-sourced at neither cost.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            return frozenset(ast.literal_eval(value.args[0]))
        raise ValueError(f"{module}: {name} is not a frozenset literal")
    raise LookupError(f"{module}: no module-level {name}")


def load_vocabulary(inventory: Path = _INVENTORY) -> Vocabulary:
    """The claim-site classification table, as the build stage itself reads it."""
    claim_bearing = _frozenset_literal(inventory, "CLAIM_BEARING")
    not_claim_bearing = _frozenset_literal(inventory, "NOT_CLAIM_BEARING")
    return Vocabulary(claim_bearing=claim_bearing, classified=claim_bearing | not_claim_bearing)


#: An arXiv identifier in either scheme, versioned. This is what separates the
#: justfile's per-category id lists from its other `ARXIV_*` variables (the
#: stage directory, the courtesy delay) without naming them here — a variable
#: holding ids IS a category, and a new one needs no edit on this side.
_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$")
_JUST_STRING = re.compile(r'^ARXIV_([A-Z0-9_]+)\s*:=\s*"([^"]*)"', re.MULTILINE)


def _category_name(variable: str) -> str:
    """``CS_GR`` → ``cs.GR``. The archive is lowercase; the subject class is not.

    ``physics`` is the exception in arXiv's own scheme — its subject classes are
    spelled lowercase (``physics.optics``) where every other archive's are not.

    The archive is taken to be the first underscore-separated token, which is
    true of every archive the sample spans and false of the hyphenated ones:
    ``q-bio.QM`` would have to arrive as ``ARXIV_Q_BIO_QM`` and would come back
    out as ``q.BIO_QM``. Adding one means teaching this function about it.
    """
    archive, _, subject = variable.partition("_")
    archive = archive.lower()
    return f"{archive}.{subject.lower() if archive == 'physics' else subject.upper()}"


def load_grouping(justfile: Path = _KB_TESTING_JUSTFILE) -> dict[str, list[str]]:
    """Category → arXiv ids, off the justfile's own ``ARXIV_*`` variables."""
    grouping: dict[str, list[str]] = {}
    for variable, value in _JUST_STRING.findall(justfile.read_text(encoding="utf-8")):
        ids = value.split()
        if ids and all(_ARXIV_ID.match(one) for one in ids):
            grouping[_category_name(variable)] = ids
    if not grouping:
        raise LookupError(f"{justfile}: no ARXIV_* variable holds a list of arXiv ids")
    return grouping

#: A `.bbl` from bibtex is a `thebibliography` environment — plain LaTeX, and
#: spliceable into the source. One from biblatex is that package's internal
#: format, which nothing else renders. The two are not interchangeable and the
#: distinction decides whether a bibliography is recoverable at all.
_BBL_BIBTEX = re.compile(r"\\begin\{thebibliography\}")
_BBL_BIBLATEX = re.compile(r"\\entry\{")


def fetch_ids(category: str, count: int) -> list[str]:
    """Recent arXiv ids in one category, newest first."""
    query = (
        f"{API}?search_query=cat:{category}&start=0&max_results={count}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    with urllib.request.urlopen(query, timeout=60) as response:
        feed = response.read().decode("utf-8", errors="replace")
    # The feed's own <id> is an abs URL; the version suffix is kept because
    # e-print serves a specific version and dropping it silently changes which.
    return _ID.findall(feed)


def fetch_source(arxiv_id: str, into: Path) -> Path | None:
    """The e-print tarball for one id, or None where arXiv serves no source.

    A submission may be PDF-only, in which case there is nothing to measure and
    that is itself a finding — it is a paper this toolchain could never build.
    """
    target = into / f"{arxiv_id.replace('/', '_')}.tar.gz"
    if target.exists():
        return target
    try:
        with urllib.request.urlopen(EPRINT + arxiv_id, timeout=120) as response:
            payload = response.read()
    except urllib.error.HTTPError:
        return None
    target.write_bytes(payload)
    return target


def _decode(raw: bytes) -> str:
    """The member's text. Latin-1 is the fallback because it cannot fail.

    A figure that slips through decodes to noise rather than raising; only
    ``.tex`` members reach the measurement, so noise costs nothing but the read.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _sources(tarball: Path) -> dict[str, str]:
    """Every text member of the tarball, by name. Binary members are skipped."""
    found: dict[str, str] = {}
    try:
        with tarfile.open(tarball) as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.size > 8_000_000:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                found[member.name] = _decode(handle.read())
    except (tarfile.TarError, EOFError):
        return {}
    return found


def staged_sources(paper: Path) -> dict[str, str]:
    """The same map as :func:`_sources`, off an already-unpacked paper directory.

    Keyed on the path relative to the paper, so a member name means the same
    thing whichever path produced it.
    """
    found: dict[str, str] = {}
    for path in sorted(paper.rglob("*")):
        relative = path.relative_to(paper)
        if _STAGED_EXCLUDE.intersection(relative.parts):
            continue
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 8_000_000:
            continue
        found[relative.as_posix()] = _decode(path.read_bytes())
    return found


def _prefix(label: str) -> str:
    """A label's taxonomy prefix, or `<none>` where the author uses none.

    The prefix is convention rather than syntax — nothing in LaTeX enforces
    `thm:` — so its *consistency* is the measurement, not its presence.
    """
    head, sep, _ = label.partition(":")
    return head if sep else "<none>"


def _bodies(text: str, environment: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each ``\\begin{env}`` … ``\\end{env}`` body.

    Depth-counted rather than nearest-match, so a claim quoted inside another
    block of the same name yields one span rather than a truncated one. The
    optional argument belongs to the opening delimiter, not to the body.
    """
    marker = re.compile(rf"\\(begin|end)\{{{re.escape(environment)}\}}")
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    for match in marker.finditer(text):
        if match.group(1) == "begin":
            if depth == 0:
                optional = _OPTIONAL_ARGUMENT.match(text, match.end())
                start = optional.end() if optional else match.end()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                spans.append((start, match.start()))
    return spans


def _within(spans: Sequence[tuple[int, int]], position: int) -> bool:
    return any(start <= position < end for start, end in spans)


def _targets(raw: str) -> list[str]:
    return [target for target in _TARGET_SEPARATOR.split(raw.strip()) if target]


def _fraction(part: int, whole: int) -> float | None:
    """None rather than zero where there is nothing to take a fraction of."""
    return round(part / whole, 4) if whole else None


def measure(arxiv_id: str, members: Mapping[str, str], *, vocabulary: Vocabulary) -> dict:
    """Everything the source declares, without rendering any of it."""
    tex = {name: text for name, text in members.items() if name.endswith(".tex") or "." not in name}
    body = "\n".join(tex.values())

    environments = {name: display for name, display in _NEWTHEOREM.findall(body)}
    labels = _LABEL.findall(body)
    refs = _REF.findall(body)
    cites = _CITE.findall(body)

    # A reference reaches a *result* only where its target label carries a
    # prefix the author also used on a theorem-like environment. Everything else
    # points at a section, an equation or a float, and fans out to a whole
    # document rather than naming one claim.
    ref_targets = Counter(_prefix(target) for _, target in refs)

    # The display name is the semantic label and the internal handle is
    # arbitrary, so classification runs on the second string `\newtheorem`
    # declares, case-folded — `\newtheorem{haupt}{Theorem}` is a theorem.
    # `proof` is amsthm's, declared by nobody, and matched literally.
    claim_handles = [
        handle for handle, display in environments.items() if display.casefold() in vocabulary.claim_bearing
    ]
    claim_spans = [span for handle in claim_handles for span in _bodies(body, handle)]
    proof_spans = _bodies(body, "proof")

    claim_labels = {
        match.group(1) for match in _LABEL.finditer(body) if _within(claim_spans, match.start())
    }
    ref_sites = list(_REF.finditer(body))
    in_proof = sum(1 for match in ref_sites if _within(proof_spans, match.start()))
    to_claim = sum(1 for match in ref_sites if claim_labels.intersection(_targets(match.group(2))))

    bbl = [text for name, text in members.items() if name.endswith(".bbl")]
    bbl_flavour = None
    if bbl:
        joined = "\n".join(bbl)
        if _BBL_BIBTEX.search(joined):
            bbl_flavour = "bibtex"          # thebibliography — spliceable
        elif _BBL_BIBLATEX.search(joined):
            bbl_flavour = "biblatex"        # internal format — not renderable elsewhere
        else:
            bbl_flavour = "unknown"

    return {
        "id": arxiv_id,
        "tex_files": len(tex),
        "documentclass": sorted({c for c in _DOCUMENTCLASS.findall(body)}),
        "newtheorem": environments,
        "labels": len(labels),
        "label_prefixes": dict(Counter(_prefix(label) for label in labels).most_common()),
        "refs": len(refs),
        "ref_commands": dict(Counter(command for command, _ in refs).most_common()),
        "ref_target_prefixes": dict(ref_targets.most_common()),
        "cites": len(cites),
        "proofs": len(_PROOF.findall(body)),
        "claim_bodies": len(claim_spans),
        "proof_bodies": len(proof_spans),
        "labels_in_claim_body": len(claim_labels),
        "refs_in_proof": in_proof,
        "refs_in_proof_fraction": _fraction(in_proof, len(refs)),
        "refs_to_claim_label": to_claim,
        "refs_to_claim_label_fraction": _fraction(to_claim, len(refs)),
        "unclassified_display_names": sorted(
            {display for display in environments.values() if display.casefold() not in vocabulary.classified}
        ),
        "has_bib": any(name.endswith(".bib") for name in members),
        "bib_count": sum(1 for name in members if name.endswith(".bib")),
        "has_bbl": bool(bbl),
        "bbl_flavour": bbl_flavour,
    }


def _row(arxiv_id: str, category: str, members: Mapping[str, str], *, vocabulary: Vocabulary) -> dict:
    """One measured paper, or one recorded failure. Never a raise.

    A paper nobody can parse is a finding — the count of them and their ids is
    part of the answer — while a halted run reports one bit and no distribution
    at all, so every failure here becomes a row and the walk continues.
    """
    try:
        row = measure(arxiv_id, members, vocabulary=vocabulary)
        row["source"] = "present"
    except Exception as exc:  # broad on purpose — the failure is the datum, see above
        row = {"id": arxiv_id, "source": "unparseable", "error": f"{type(exc).__name__}: {exc}"}
        print(f"{category} {arxiv_id}: unparseable ({type(exc).__name__}: {exc})", file=sys.stderr)
    row["category"] = category
    if row["source"] == "present":
        print(
            f"{category} {arxiv_id}: "
            f"{len(row['newtheorem'])} env, {row['labels']} labels, "
            f"{row['refs']} refs ({row['refs_in_proof']} in proof, "
            f"{row['refs_to_claim_label']} → claim), bbl={row['bbl_flavour']}",
            file=sys.stderr,
        )
    return row


def survey(categories: list[str], per_category: int, scratch: Path, *, vocabulary: Vocabulary) -> list[dict]:
    scratch.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for index, category in enumerate(categories):
        if index:
            time.sleep(COURTESY_DELAY)
        try:
            ids = fetch_ids(category, per_category)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"{category}: query failed ({exc})", file=sys.stderr)
            continue
        for arxiv_id in ids:
            time.sleep(COURTESY_DELAY)
            tarball = fetch_source(arxiv_id, scratch)
            if tarball is None:
                # Recorded rather than skipped: a paper with no source is a
                # paper this toolchain cannot build, which is a real outcome.
                rows.append({"id": arxiv_id, "category": category, "source": "absent"})
                print(f"{category} {arxiv_id}: no source", file=sys.stderr)
                continue
            rows.append(_row(arxiv_id, category, _sources(tarball), vocabulary=vocabulary))
    return rows


def survey_staged(stage: Path, grouping: Mapping[str, Sequence[str]], *, vocabulary: Vocabulary) -> list[dict]:
    """The same measurement over papers already unpacked, with no network access."""
    rows: list[dict] = []
    for category, ids in grouping.items():
        for arxiv_id in ids:
            # `stage-arxiv-paper` maps the pre-2007 scheme's `/` to `_` so one
            # paper is one directory; the id itself keeps its original spelling.
            paper = stage / arxiv_id.replace("/", "_")
            if not paper.is_dir():
                rows.append({"id": arxiv_id, "category": category, "source": "absent"})
                print(f"{category} {arxiv_id}: not staged at {paper}", file=sys.stderr)
                continue
            rows.append(_row(arxiv_id, category, staged_sources(paper), vocabulary=vocabulary))
    return rows


def aggregate(rows: Iterable[dict]) -> dict:
    """Both containment readings over a set of papers, pooled and per-paper.

    The two fractions answer different questions and neither substitutes for the
    other: *pooled* is the corpus's own ratio and a long paper dominates it,
    *paper_mean* weights every paper alike and is what makes the categories
    comparable. ``papers_with_refs`` is the mean's own denominator — a paper
    that cross-references nothing has no fraction to average, and counting it as
    a zero would be reporting a measurement nobody took.
    """
    rows = list(rows)
    measured = [row for row in rows if row["source"] == "present"]
    total_refs = sum(row["refs"] for row in measured)
    summary: dict = {
        "papers": len(rows),
        "measured": len(measured),
        "papers_with_refs": sum(1 for row in measured if row["refs"]),
        "unmeasured": {
            row["id"]: row["source"] for row in rows if row["source"] != "present"
        },
        "refs": total_refs,
    }
    for reading in ("refs_in_proof", "refs_to_claim_label"):
        total = sum(row[reading] for row in measured)
        seen = [row[f"{reading}_fraction"] for row in measured if row[f"{reading}_fraction"] is not None]
        summary[reading] = total
        summary[f"{reading}_fraction_pooled"] = _fraction(total, total_refs)
        summary[f"{reading}_fraction_paper_mean"] = round(sum(seen) / len(seen), 4) if seen else None

    census: Counter[str] = Counter()
    for row in measured:
        census.update(row["unclassified_display_names"])
    summary["unclassified_display_names"] = dict(census.most_common())
    return summary


def report(rows: Sequence[dict]) -> dict:
    """The rows plus the aggregates the rows alone do not answer."""
    categories = dict.fromkeys(row["category"] for row in rows)
    return {
        "papers": list(rows),
        "categories": {
            category: aggregate(row for row in rows if row["category"] == category)
            for category in categories
        },
        "overall": aggregate(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--category",
        action="append",
        help="an arXiv category to fetch and sample; repeatable (math.DG, cs.LG, econ.TH, ...)",
    )
    source.add_argument(
        "--staged",
        type=Path,
        help=(
            "measure papers already unpacked under this directory, one per id, "
            f"grouped by the ARXIV_* variables in {_KB_TESTING_JUSTFILE.name}; makes no network request"
        ),
    )
    parser.add_argument("--per-category", type=int, default=5, help="papers per category (default 5)")
    parser.add_argument(
        "--scratch",
        type=Path,
        help="where tarballs land; nothing is written outside it (required with --category)",
    )
    parser.add_argument("--out", type=Path, help="write the report here as JSON (default: stdout)")
    arguments = parser.parse_args(argv)

    vocabulary = load_vocabulary()
    if arguments.staged:
        rows = survey_staged(arguments.staged, load_grouping(), vocabulary=vocabulary)
    else:
        if arguments.scratch is None:
            parser.error("--scratch is required with --category")
        rows = survey(arguments.category, arguments.per_category, arguments.scratch, vocabulary=vocabulary)

    payload = json.dumps(report(rows), indent=2, sort_keys=True)
    if arguments.out:
        arguments.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
