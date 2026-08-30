#!/usr/bin/env python3
"""Repo-wide Markdown link-integrity checker.

Crawls every `.md` file under the consuming repo's root (auto-detected by
walking up from the cwd; see `kb_util.find_repo_root`), extracts Markdown
links `[text](target)`, and reports broken file targets. Also folds in a
consumer-side id-validity check: hashed claim/experiment/support ids
(`clm-`/`exp-`/`sup-` + 6 [a-z0-9]) cited in prose must resolve to a node
in the KB's `claims.jsonl` (located via `kb_util`).

Pure standard library.

Link classification:
  - external schemes (http, https, mailto, absolute URLs) are skipped.
  - intra-repo  = resolved path stays inside the repo.
  - inter-repo  = resolved path escapes into a sibling repo (../). These are
    legitimately stale/in-flux; handling is controlled by --inter-repo.

Gating:
  EVERY crawled `.md` file gates: any broken intra-repo link or unknown-id
  citation flips the exit code, regardless of which file it originates from —
  there is no warn-only tier. (Crawl exclusions in SKIP_DIRS /
  SKIP_SEGMENT_RUNS still apply — those trees, e.g. test fixtures with
  deliberately broken links, are never scanned at all.)

Skipped targets (never classified broken):
  - targets ending in `.tex` (a LaTeX source is a derived build artifact,
    not a navigation target),
  - targets beginning with `~` (home-dir paths like `~/.claude/...`), and
  - targets resolving INTO an `IGNORED_PATHS` dir — a project-side carveout
    for gitignored generated-artifact dirs; empty by default.

False-positive avoidance:
  - links and ids inside ``` fenced code blocks and `inline code` spans are
    NOT extracted (doc/example links live in fences).
  - a trailing `#anchor` fragment and a trailing `:linenum` suffix are stripped
    before resolving (the codebase cites locations as `path/file.md:42`).
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Route KB path construction through the kb_util module.
from kb_tools import __version__, kb_links, kb_schema, kb_util

# Markdown scanning primitives are single-sourced in kb_links (shared with the
# kb_cmd reverse-find). Re-exported here so existing references and tests that
# reach into this module keep resolving.
from kb_tools.kb_links import SKIP_DIRS, SKIP_SEGMENT_RUNS, iter_markdown_files, strip_code, strip_target

logger = logging.getLogger("verify-md-links")

# Top-level entries that constitute "inside the repo" for the intra/inter
# split. A resolved path that is not under the repo root is inter-repo by
# definition; this set is informational and not used as a gate (the gate is
# repo-root containment), but documents the intra surface.
INTRA_ROOTS = {
    kb_util.KB_DIRNAME,
    "kb_tools",
    "kb_cmd",
}

# Pragmatic carveout (deliberately NOT a .gitignore parser — that is well beyond
# this tool's scope). A broken link whose resolved target points INTO one of
# these repo-relative directories is never reported. Intended for gitignored
# generated-artifact dirs (absent on a fresh checkout) that prose legitimately
# links into. Empty by default; a project needing the carveout populates it.
IGNORED_PATHS: tuple[Path, ...] = ()


def _under_ignored_path(resolved: Path, repo_root: Path) -> bool:
    """True if `resolved` is one of, or lives under, an `IGNORED_PATHS` dir."""
    try:
        rel = resolved.relative_to(repo_root)
    except ValueError:
        return False
    return any(rel == p or p in rel.parents for p in IGNORED_PATHS)


# External / non-file schemes to skip outright.
_SCHEME_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:)?//|^(?:https?|mailto):", re.IGNORECASE)

# Hashed id citation. `xxxxxx` literal placeholders are excluded downstream.
# The id grammar (all three kinds) and the placeholder set are single-sourced
# in kb_schema; this checker only adds the `\b(...)\b` capture wrapper.
_ID_RE = re.compile(rf"\b({kb_schema.id_body()})\b")
_ID_PLACEHOLDERS = kb_schema.ID_PLACEHOLDERS


@dataclass(frozen=True)
class Finding:
    file: Path  # absolute path of the markdown file
    line: int
    kind: str  # "broken intra" | "broken inter" | "unknown id"
    target: str


# Repo-root detection is owned by kb_util (the single source of path truth);
# re-export so existing references (and the CLI auto-detect) keep working.
find_repo_root = kb_util.find_repo_root


def is_error_source(md_file: Path, repo_root: Path) -> bool:
    """True if broken links/ids from `md_file` should gate the exit code.

    Every crawled `.md` file gates — there is no warn-only tier. (Files that
    should not be checked at all are excluded earlier, at crawl time, via
    SKIP_DIRS / SKIP_SEGMENT_RUNS.)
    """
    return True


def load_known_ids(repo_root: Path) -> set[str] | None:
    """Load the set of node ids from `.index/claims.jsonl`, or None if absent.

    None signals the id-validity check should be skipped (e.g. the generated
    index is not present on this branch/worktree).
    """
    index_path = kb_util.claims_jsonl(repo_root)
    if not index_path.is_file():
        logger.info("id-validity check skipped: %s not present", index_path)
        return None
    ids: set[str] = set()
    with index_path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                node = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("%s:%d unparseable JSON line, skipping", index_path, lineno)
                continue
            node_id = node.get("id")
            if isinstance(node_id, str):
                ids.add(node_id)
    logger.info("loaded %d node ids from %s", len(ids), index_path)
    return ids


def check_links(md_file: Path, body: str, repo_root: Path) -> list[Finding]:
    """Extract links from a code-stripped body and classify broken ones."""
    findings: list[Finding] = []
    for lineno, line in enumerate(body.splitlines(), 1):
        for match in kb_links.LINK_RE.finditer(line):
            raw_target = match.group(1)
            if _SCHEME_RE.search(raw_target):
                continue
            if raw_target.startswith("~"):
                continue  # home-dir path (~/.claude/...), not a repo link
            target = strip_target(raw_target)
            if not target:
                continue  # pure anchor link like (#section)
            if target.endswith(".tex"):
                continue  # derived LaTeX build artifact, not a nav target
            resolved = (md_file.parent / target).resolve()
            if _under_ignored_path(resolved, repo_root):
                continue  # gitignored generated-artifact dir — never gate
            try:
                resolved.relative_to(repo_root)
                is_intra = True
            except ValueError:
                is_intra = False
            if resolved.exists():
                continue
            kind = "broken intra" if is_intra else "broken inter"
            findings.append(Finding(md_file, lineno, kind, raw_target))
    return findings


def check_ids(md_file: Path, body: str, known_ids: set[str]) -> list[Finding]:
    """Flag cited hashed ids that do not resolve to a known node."""
    findings: list[Finding] = []
    for lineno, line in enumerate(body.splitlines(), 1):
        for match in _ID_RE.finditer(line):
            cited = match.group(1)
            if cited in _ID_PLACEHOLDERS:
                continue
            if cited not in known_ids:
                findings.append(Finding(md_file, lineno, "unknown id", cited))
    return findings


def scan(repo_root: Path, check_ids_enabled: bool) -> list[Finding]:
    known_ids = load_known_ids(repo_root) if check_ids_enabled else None
    findings: list[Finding] = []
    for md_file in iter_markdown_files(repo_root):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("could not read %s: %s", md_file, exc)
            continue
        body = strip_code(text)
        findings.extend(check_links(md_file, body, repo_root))
        if known_ids is not None:
            findings.extend(check_ids(md_file, body, known_ids))
    return findings


def is_gating(finding: Finding, repo_root: Path) -> bool:
    """True if `finding` flips the exit code.

    Broken-inter findings are handled separately by --inter-repo and are never
    gating here. Broken-intra and unknown-id findings gate iff their source is
    an error source (see `is_error_source`).
    """
    if finding.kind == "broken inter":
        return False
    return is_error_source(finding.file, repo_root)


def report(findings: list[Finding], repo_root: Path) -> None:
    """Print every finding, sorted by file then line. Exhaustive — no truncation."""
    ordered = sorted(findings, key=lambda f: (str(f.file), f.line))
    for finding in ordered:
        try:
            rel = finding.file.relative_to(repo_root)
        except ValueError:
            rel = finding.file
        print(f"{rel}:{finding.line}  [{finding.kind}]  ->  {finding.target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    parser.add_argument(
        "--inter-repo",
        choices=("dont-check", "warn", "error"),
        default="warn",
        help="how to handle links that escape into sibling repos (default: warn)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repo root to scan (default: auto-detected by walking up from the cwd)",
    )
    parser.add_argument(
        "--no-id-check",
        action="store_true",
        help="disable the consumer-side claim/experiment/support id-validity check",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable info-level logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    if args.root is not None:
        repo_root = args.root.resolve()
    else:
        try:
            repo_root = find_repo_root().resolve()
        except kb_util.RepoRootError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
    logger.info("scanning repo root: %s", repo_root)

    findings = scan(repo_root, check_ids_enabled=not args.no_id_check)

    if args.inter_repo == "dont-check":
        findings = [f for f in findings if f.kind != "broken inter"]

    report(findings, repo_root)

    broken_inter = sum(1 for f in findings if f.kind == "broken inter")

    # Every intra/id finding gates (no warn-only tier in this repo).
    intra_id = [f for f in findings if f.kind in ("broken intra", "unknown id")]
    gating_errors = sum(1 for f in intra_id if is_gating(f, repo_root))

    print(
        f"\n[verify-md-links] gating errors: {gating_errors}  "
        f"broken inter: {broken_inter}  "
        f"(inter-repo mode: {args.inter_repo})",
        file=sys.stderr,
    )

    # Exit 1 iff there is >=1 gating error (broken-intra or unknown-id), plus
    # broken-inter under --inter-repo error. Inter-repo links under
    # warn/dont-check do not flip the code.
    failing = gating_errors
    if args.inter_repo == "error":
        failing += broken_inter

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
