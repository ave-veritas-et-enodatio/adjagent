#!/usr/bin/env python3
"""Regenerate derived KB metadata fields from leaf claims.

Side-effecting: writes to the frontmatter block of every ``kind: index`` /
``kind: entry-point`` node — whatever its filename — and to the derived
``solidity`` fields of every ``claim-quality.md`` register. Idempotent. Run via
the project's refresh target (or directly as
``python3 -m kb_tools.refresh_kb_metadata``).

Currently regenerates:
    * ``subtree-claims`` and ``subtree-experiments`` on every ``kind: index``
      and ``kind: entry-point`` node, discovered by kind (never by filename)
      from the same ``kb_index_lib.discover_kb`` state the verifier enumerates
    * the ``- solidity:`` line of every claim entry in every ``claim-quality.md``
      register — value, build-status phrase, and arithmetic trace are all
      derived from the hand-authored ``confidence`` values via
      ``kb_index_lib.compute_solidity``
    * the ``(solidity X)`` annotation in every claim-target depends-on bullet,
      synced to the depended-on claim's computed solidity
    * the claim-graph sheet at ``<kb-root>/claim-graph.svg``, a pure derived
      view of the ``.index/`` this run just wrote — which is why refresh is
      where it is minted, and why ``verify_kb_metadata`` is where it is checked

Future: bootstrap directive blockquote text (currently hand-maintained).

This script does NOT verify; it ONLY refreshes. Run the project's verify
target afterward to confirm the result is internally consistent.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from kb_tools import __version__, kb_index_lib, kb_schema, kb_util

# The claim-graph sheet's renderer, reached only through its op — which takes
# no argv and exits nothing. The sheet is a derived view of the `.index/` this
# module writes, which is what makes refresh the one place it is minted.
from kb_tools.kb_graph import ops as graph_ops

# The shared writer. This module composes no metadata
# format of its own: the frontmatter-field splice and the register
# locator are `kb_write.store`'s, and the derived-field line builders and the
# block delimiters are `kb_write.render`'s. The dependency runs one way —
# `kb_write` never imports this module — so what refresh writes and what the
# write API writes cannot be two implementations of one format.
from kb_tools.kb_write import render, store

# The KB root and derived-index directory this run operates on. Bound in
# main() — from --kb-root when given, else by lazy repo-root discovery
# (kb_util.kb_root()) — never at import time.
KB: Path = None  # type: ignore[assignment]
INDEX_DIR: Path = None  # type: ignore[assignment]

# JSONL files emitted by the index-emission phase. This tuple IS the file
# inventory and its order — nothing restates it in prose, and
# `verify_kb_metadata` carries the matching tuple for the freshness diff.
INDEX_FILES = (
    "claims",
    "depends-on",
    "strengthen-by",
    "supported-by",
    "cites",
    "subtree-aggregates",
)

# Walk-exclusion vocabulary — single-sourced in kb_index_lib. Only the dir set
# is consumed here; the union comes from `compute_subtree_aggregates` like every
# other consumer.
EXCLUDE_DIRS = kb_index_lib.EXCLUDE_DIRS

# One reader of the frontmatter fields, shared with the checker — never a local
# copy. The emitter reading a leaf's `claims:` differently from the checker is
# the same class of defect as the two disagreeing about where frontmatter ends,
# and a single-line-only parser reads a wrapped id list as one "claim id" per
# character, so the subtree union it writes is assembled from single letters.
parse_frontmatter = kb_index_lib.parse_frontmatter


def replace_subtree_claims(text: str, new_ids: list[str]) -> str:
    """Replace the subtree-claims line in the frontmatter block (or insert it).

    The splice itself is ``kb_write.store``'s; what stays here is the
    pair of facts refresh owns — which derived field this is, and which line it
    anchors after when absent.
    """
    return store.replace_or_insert_frontmatter_field(text, field="subtree-claims", ids=new_ids, anchor_prefix="kind:")


def replace_subtree_experiments(text: str, new_ids: list[str]) -> str:
    """Replace subtree-experiments in the frontmatter (or insert it).

    Inserted directly after the ``subtree-claims:`` line so the two derived
    aggregates sit together; falls back after ``kind:`` if subtree-claims is
    somehow absent (it is written first in the same refresh pass).
    """
    anchor = "subtree-claims:"
    if "subtree-claims:" not in text:
        anchor = "kind:"
    return store.replace_or_insert_frontmatter_field(
        text, field="subtree-experiments", ids=new_ids, anchor_prefix=anchor
    )


SOLIDITY_LINE = re.compile(r"^(\s*)-\s*solidity:")
# Matches a depends-on (solidity X) annotation in either rendering: a numeric
# value or the *pending* form (target has no computable solidity). Matching
# both keeps the annotation sync correct across numeric<->pending transitions.
SOLIDITY_ANNOTATION = re.compile(r"\(solidity\s+(?:-?\d+(?:\.\d+)?|\*pending\*)\)")
CLAIM_ID_TOKEN = re.compile(rf"\b({kb_schema.id_body('clm')})\b")


# The 2-dp value formatter is single-sourced in kb_index_lib, so the emitter
# writes and the checker reports the same rendering of the same number.
_fmt = kb_index_lib.format_solidity


def _solidity_line(solidity: float | None, trace: str) -> str:
    """Build the canonical ``- solidity:`` line for a claim OR support entry.

    The *format* is ``kb_write.render``'s; what this function supplies is the
    pair of values only the computation can give it. ``solidity`` is the
    computed value and ``trace`` the arithmetic suffix rendered by
    ``kb_index_lib`` FROM THE COMPUTATION — not re-derived here. Building
    ``[= min(base, min_dep)]`` here from the raw base value and the dep minimum
    writes traces that contradict their own values on both non-trivial
    branches: a support-lifted claim prints its pre-lift confidence, and an
    experimentally-rescued claim prints a min() that never set the value.

    When ``solidity`` is ``None`` the entry has no computable solidity — its
    base is ``*pending*`` OR a dependency's solidity is ``*pending*``
    (pending-ness propagates transitively, like NaN). Both render the same:
    the bare ``- solidity: *pending*`` form, no phrase, no arithmetic trace —
    which is the renderer's branch, reached by handing it ``None`` rather than a
    formatted value.
    """
    if solidity is None:
        return render.render_solidity_line(value_text=None)
    return render.render_solidity_line(
        value_text=_fmt(solidity),
        status_phrase=kb_index_lib.build_status_phrase(solidity),
        trace=trace,
    )


def _rewrite_claim_quality_solidity(
    path: Path,
    entries,
    full,
    supports=(),
) -> tuple[int, list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Rewrite derived solidity content in a single ``claim-quality.md`` file.

    Handles both claim entries and SUPPORT entries: a support's
    ``### Quality`` section carries the same derived ``- solidity:`` line and the
    same claim-target ``(solidity X)`` depends-on annotations. A support's own
    solidity comes from ``sup_solidity``; its base scalar is ``quality`` (a
    claim's is ``confidence``). Depends-on annotations always reference the claim
    ``solidity`` map (a support's deps are claims).

    For every claim entry, rewrites:

    * the ``- solidity:`` line in its ``### Quality`` section. A claim with a
      computable solidity gets the numeric form (value, build-status phrase,
      arithmetic trace); a claim with no computable solidity — confidence is
      ``*pending*`` OR a dependency is ``*pending*`` — gets the bare
      ``- solidity: *pending*`` form. Pending-ness propagates transitively
      (like NaN through arithmetic): "absent from the ``compute_solidity``
      result" is treated identically to "pending-confidence", regardless of
      the claim's own local confidence.
    * the ``(solidity X)`` annotation on each claim-target depends-on bullet,
      synced to the depended-on claim's solidity. A bullet whose target has
      no computable solidity gets ``(solidity *pending*)``.

    Framework-target depends-on bullets carry no ``(solidity X)`` token and
    are untouched. Lines already in their canonical form are left
    byte-identical, so the rewrite is idempotent.

    Returns ``(files_changed, solidity_changes, annotation_changes)`` where
    ``files_changed`` is 0 or 1 and the change lists hold ``(claim_id, old,
    new)`` tuples for reporting.
    """
    text = path.read_text(encoding="utf-8")
    had_final_newline = text.endswith("\n")
    lines = text.split("\n")
    if had_final_newline:
        # split() leaves a trailing "" element; drop it so indices line up
        # with the visible content lines, restore the newline at write time.
        lines = lines[:-1]

    # Both maps are PROJECTIONS of the one computation, derived here rather than
    # accepted as separate arguments: a caller cannot pass a `solidity` from one
    # computation and a `sup_solidity` from another, and the branch record that
    # renders each trace is guaranteed to describe these very numbers.
    solidity = {cid: r.final for cid, r in full.items() if r.final is not None}
    sup_solidity = {sid: sol for sid, sol in full.sup_solidity.items() if sol is not None}
    by_id = {e.id: e for e in entries}
    sup_by_id = {s.id: s for s in supports}
    # The register-section locator is `kb_write.store`'s — one implementation of
    # "where does this entry's ### Quality section sit", used by the write API's
    # splices and by this write-back. Its indices are raw: fence scrubbing blanks
    # lines without removing them, so an index computed on scrubbed text is an
    # index into `lines`. An entry with no ### Quality section carries `None` and
    # is dropped here.
    ranges = {
        entry.node_id: (entry.quality_start, entry.quality_end)
        for entry in store.locate_entries("\n".join(lines))
        if entry.quality_start is not None and entry.quality_end is not None
    }
    # Match on the SCRUBBED lines, edit the raw ones at the same index. The
    # checker reads its on-disk values from `parse_claim_quality_file`, which
    # parses scrubbed text, so matching raw here would be an emitter/checker
    # split: a `- solidity:` line in a fenced example inside a Quality section
    # would be rewritten by refresh and never read by verify.
    scrubbed = kb_index_lib._strip_code_fences("\n".join(lines)).splitlines()

    solidity_changes: list[tuple[str, str, str]] = []
    annotation_changes: list[tuple[str, str, str]] = []

    for node_id, (qstart, qend) in ranges.items():
        entry = by_id.get(node_id)
        sup = sup_by_id.get(node_id)
        if entry is None and sup is None:
            continue
        # A claim's own solidity comes from ``solidity`` and its base scalar is
        # ``confidence``; a support's comes from ``sup_solidity`` and its base
        # is ``quality``. ``computed`` is None when the node has no computable
        # solidity (base *pending* OR a dependency *pending*) — pending-ness is
        # decided by presence in the relevant map, not by the local base value.
        if entry is not None:
            computed = solidity.get(node_id)
            # The trace comes from the computation's own record of which branch
            # ran and what it consumed — never re-derived at write time.
            trace = kb_index_lib.render_solidity_trace(full[node_id]) if node_id in full else ""
        else:
            computed = sup_solidity.get(node_id)
            # A support has no experimental branch and no lift: its solidity is
            # the plain weakest link over its own quality and dep finals.
            trace = kb_index_lib.render_min_trace(sup.quality, kb_index_lib.min_dependency_solidity(sup, solidity))

        for idx in range(qstart, qend):
            line = lines[idx]
            probe = scrubbed[idx]  # fenced content is blank here, so never matches
            # (1) The solidity line.
            if SOLIDITY_LINE.match(probe):
                new_line = _solidity_line(computed, trace)
                if new_line != line:
                    solidity_changes.append((node_id, line, new_line))
                    lines[idx] = new_line
                continue
            # (2) A claim-target depends-on bullet's (solidity X) annotation.
            if "(solidity" not in probe:
                continue
            head = kb_index_lib._depends_on_bullet_head(re.sub(r"^\s*-\s*", "", probe.strip()))
            targets = CLAIM_ID_TOKEN.findall(head)
            if not targets:
                continue
            # A claim depends-on bullet leads with exactly one claim id; its
            # (solidity X) annotation is that target's solidity. A target with
            # no computable solidity formats as the *pending* literal, so the
            # two cases are one call rather than a branch this module owns.
            replacement = render.render_solidity_annotation(_fmt(solidity.get(targets[0])))
            new_line = SOLIDITY_ANNOTATION.sub(replacement, line, count=1)
            if new_line != line:
                annotation_changes.append((node_id, line, new_line))
                lines[idx] = new_line

    new_text = "\n".join(lines)
    if had_final_newline:
        new_text += "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return 1, solidity_changes, annotation_changes
    return 0, solidity_changes, annotation_changes


def _rewrite_claim_quality_leaf_references(
    path: Path,
    register_rel: str,
    leaf_references: dict[str, list[str]],
) -> tuple[int, list[tuple[str, str, str]]]:
    """Rewrite the derived ``> **Leaf references:**`` footer in one register.

    For every ``clm-`` / ``sup-`` entry in ``path``, regenerates the footer from
    the reverse-citation map ``leaf_references`` (``{node_id: [leaf paths]}``).
    The footer is a single blockquote line in the band between the entry's
    ``<!-- id: ... -->`` marker and its ``### Quality`` heading: if a line
    starting with ``> **Leaf references:**`` is present there it is replaced;
    otherwise the footer is inserted just before ``### Quality`` (with blank-line
    separators). Lines already in canonical form are left byte-identical, so the
    rewrite is idempotent.

    Returns ``(files_changed, footer_changes)`` where ``files_changed`` is 0 or
    1 and ``footer_changes`` holds ``(node_id, old, new)`` tuples for reporting.
    """
    text = path.read_text(encoding="utf-8")
    had_final_newline = text.endswith("\n")
    lines = text.split("\n")
    if had_final_newline:
        lines = lines[:-1]

    # The footer's location — including "there isn't one" — comes from the
    # library locator the VERIFIER also calls, so the emitter and the checker
    # cannot disagree about which line is the footer. Searching the raw lines
    # here while verify searches the scrubbed ones lets refresh overwrite a
    # fenced example with real links while verify reports the real footer
    # missing forever.
    bands = kb_index_lib.locate_leaf_reference_footers(text)
    footer_changes: list[tuple[str, str, str]] = []

    # Edit by node id in DESCENDING band order so insertions never shift the
    # line indices of bands not yet processed.
    for node_id, band in sorted(bands.items(), key=lambda kv: kv[1].body_start, reverse=True):
        new_footer = kb_index_lib.render_leaf_references(register_rel, leaf_references.get(node_id, []))
        if band.footer_line is not None:
            if lines[band.footer_line] != new_footer:
                footer_changes.append((node_id, lines[band.footer_line], new_footer))
                lines[band.footer_line] = new_footer
        else:
            # Insert before `### Quality`, ensuring one blank line on each side.
            insert_at = band.quality_start
            block = [new_footer, ""]
            if insert_at > band.body_start and lines[insert_at - 1].strip() != "":
                block = ["", *block]
            lines[insert_at:insert_at] = block
            footer_changes.append((node_id, "(none)", new_footer))

    new_text = "\n".join(lines)
    if had_final_newline:
        new_text += "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return 1, footer_changes
    return 0, footer_changes


def _refresh_leaf_references() -> tuple[int, list]:
    """Regenerate the ``> **Leaf references:**`` footer across every register.

    The reverse-citation map is computed ONCE via
    ``kb_index_lib.build_leaf_references`` over the whole KB state — the SAME
    function the verifier's drift gate consumes — so the written footer and what
    verify recomputes cannot drift. Every ``claim-quality.md`` register is
    rewritten (a register holding only support entries still gets its footers
    refreshed).

    Returns ``(files_changed, footer_changes)``.
    """
    state = kb_index_lib.discover_kb(KB, diagnostic_stream=None)
    leaf_references = kb_index_lib.build_leaf_references(state)

    files_changed = 0
    all_footer_changes: list = []
    for cq in sorted(KB.rglob("claim-quality.md")):
        if any(part in kb_index_lib.EXCLUDE_DIRS for part in cq.relative_to(KB).parts[:-1]):
            continue
        register_rel = cq.relative_to(KB).as_posix()
        changed, footer_ch = _rewrite_claim_quality_leaf_references(cq, register_rel, leaf_references)
        files_changed += changed
        all_footer_changes.extend((register_rel, *c) for c in footer_ch)
    return files_changed, all_footer_changes


def _refresh_solidity() -> tuple[int, list, list]:
    """Rewrite derived solidity content across every ``claim-quality.md``.

    ``solidity`` is computed ONCE via ``kb_index_lib.compute_solidity`` over
    the whole KB claim graph; the same map drives both the solidity-line
    write-back and the depends-on annotation sync (and, downstream, the
    ``.index/claims.jsonl`` fields). Raises ``kb_index_lib.SolidityCycleError``
    if the claim depends-on graph has a cycle — refresh refuses to write
    solidity in that case rather than emit undefined values.

    Returns ``(files_changed, solidity_changes, annotation_changes)``.
    """
    state = kb_index_lib.discover_kb(KB, diagnostic_stream=None)
    # ``solidity`` (claim finals), ``sup_solidity`` (support node solidities)
    # AND the per-claim branch record that renders each arithmetic trace all
    # come from ONE call — never re-derived — so the claim-quality write-back,
    # the depends-on annotation sync, the trace, and the JSONL fields cannot
    # drift (the dual-compute trap). A thin wrapper per half would run
    # `compute_solidity_full` again for each.
    full = kb_index_lib.compute_solidity_full(state.claim_entries, state.experiments, state.supports, state.works)

    # Group claim entries by their owning claim-quality.md file. Support entries
    # share those registers; ``store.locate_entries`` locates each register's
    # own sup-ids, so the full support list is passed to every file (only its
    # resident sup-ids match). Register every claim-quality.md file so a file
    # holding ONLY support entries is still rewritten.
    entries_by_file: dict[str, list] = {}
    for entry in state.claim_entries:
        entries_by_file.setdefault(entry.canonical_path, []).append(entry)
    for cq in sorted(KB.rglob("claim-quality.md")):
        if any(part in kb_index_lib.EXCLUDE_DIRS for part in cq.relative_to(KB).parts[:-1]):
            continue
        entries_by_file.setdefault(cq.relative_to(KB).as_posix(), [])

    files_changed = 0
    all_solidity_changes: list = []
    all_annotation_changes: list = []
    for rel_path, entries in sorted(entries_by_file.items()):
        path = KB / rel_path
        changed, sol_ch, ann_ch = _rewrite_claim_quality_solidity(path, entries, full, state.supports)
        files_changed += changed
        all_solidity_changes.extend((rel_path, *c) for c in sol_ch)
        all_annotation_changes.extend((rel_path, *c) for c in ann_ch)
    return files_changed, all_solidity_changes, all_annotation_changes


def _emit_jsonl_indexes() -> tuple[int, int]:
    """Write the five JSONL files under ``KB/.index/``.

    Returns ``(written, unchanged)``. A file is "unchanged" when its on-disk
    bytes already match the freshly serialized payload; in that case the
    write is skipped to keep mtime stable and avoid spurious ``git status``
    noise. Otherwise the file is written atomically (rename over existing).
    """
    INDEX_DIR.mkdir(exist_ok=True)
    state = kb_index_lib.discover_kb(KB)
    all_records = kb_index_lib.build_all_records(state)

    written = 0
    unchanged = 0
    for short_name in INDEX_FILES:
        records = all_records[short_name]
        out_path = INDEX_DIR / f"{short_name}.jsonl"
        # Serialize through the library so the bytes compared here are the bytes
        # `check_index_fresh` byte-compares against. Re-implementing
        # `serialize_records` inline here is the emitter hand-rolling the format
        # the checker imports — a drift waiting for one of the two to change.
        body = kb_index_lib.serialize_records(records)
        if out_path.exists() and out_path.read_text(encoding="utf-8") == body:
            unchanged += 1
            continue
        # Atomic rewrite: write to sibling temp file, then rename.
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_text(body, encoding="utf-8", newline="\n")
        os.replace(tmp_path, out_path)
        written += 1
    return written, unchanged


def main(argv: list[str] | None = None) -> int:
    global KB, INDEX_DIR
    parser = argparse.ArgumentParser(description="Regenerate derived KB metadata fields from leaf claims.")
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    parser.add_argument(
        "--kb-root",
        type=Path,
        default=None,
        help=(
            "KB root directory to operate on. Defaults to the repo-root KB "
            "directory (resolved via kb_util). Used by tests to point "
            "the refresher at a synthetic fixture KB instead of the canonical one."
        ),
    )
    args = parser.parse_args(argv)
    if args.kb_root is not None:
        KB = args.kb_root
    else:
        try:
            KB = kb_util.kb_root()
        except kb_util.RepoRootError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 2
    INDEX_DIR = KB / kb_util.INDEX_DIRNAME

    if not KB.is_dir():
        print(f"FAIL: KB directory {KB} not found.", file=sys.stderr)
        return 2

    # BOTH aggregates come from the SAME shared library computation the
    # verifier uses (compute_subtree_aggregates over a single discover_kb
    # state) — never a second, independent walk — so what refresh writes cannot
    # drift from what verify recomputes. Deriving subtree-claims here by a local
    # os.walk would make THREE implementations of the same union (refresh's
    # walk, verify's walk, the library aggregate), whose equivalence nothing
    # enforces.
    exp_state = kb_index_lib.discover_kb(KB, diagnostic_stream=None)
    aggregates = kb_index_lib.compute_subtree_aggregates(exp_state)

    updated = 0

    # Update every kind: index / kind: entry-point node. Discovery is by KIND,
    # from the same ``discover_kb`` state the verifier enumerates — never by
    # filename. An emitter that walked for ``index.md`` (plus a sibling
    # ``entry-point.md`` special case) left any node whose kind was right but
    # whose filename differed verified-but-never-refreshed: a subtree-claims /
    # subtree-experiments drift FAIL no run of refresh could ever clear.
    for idx in exp_state.indexes:
        p = KB / idx.path
        text = p.read_text(encoding="utf-8")
        sorted_ids, exp_ids = aggregates.get(idx.path, ([], []))
        new_text = replace_subtree_claims(text, sorted_ids)
        new_text = replace_subtree_experiments(new_text, exp_ids)
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
            updated += 1

    print(f"[refresh] Updated {updated} subtree-claims field(s).")

    # Phase 1b: rewrite the derived solidity content (solidity lines +
    # depends-on (solidity X) annotations) in every claim-quality.md register.
    try:
        sol_files, sol_changes, ann_changes = _refresh_solidity()
    except kb_index_lib.SolidityCycleError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        print(
            "  -> solidity is undefined for cycle members; refusing to write. "
            "Break the cycle in the claim depends-on graph and re-run.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[refresh] Rewrote solidity in {sol_files} claim-quality.md file(s) "
        f"({len(sol_changes)} solidity line(s), "
        f"{len(ann_changes)} depends-on annotation(s) changed)."
    )
    for rel, cid, old, new in sol_changes:
        print(f"  [solidity] {rel}:{cid}")
        print(f"    - {old.strip()}")
        print(f"    + {new.strip()}")
    for rel, cid, old, new in ann_changes:
        print(f"  [depends-on] {rel}:{cid}")
        print(f"    - {old.strip()}")
        print(f"    + {new.strip()}")

    # Phase 1c: rewrite the derived `> **Leaf references:**` footer in every
    # claim-quality.md entry from the reverse-citation map (which leaves host
    # the entry's id). The footer is a derived field — hand-edits become a
    # verify failure after this lands.
    ref_files, ref_changes = _refresh_leaf_references()
    print(
        f"[refresh] Rewrote leaf-references footer in {ref_files} "
        f"claim-quality.md file(s) ({len(ref_changes)} footer(s) changed)."
    )
    for rel, nid, old, new in ref_changes:
        print(f"  [leaf-refs] {rel}:{nid}")
        print(f"    - {old.strip()}")
        print(f"    + {new.strip()}")

    # Phase 2: emit derived JSONL index files. The frontmatter writes above
    # are already on disk, so discover_kb here picks up the just-written
    # subtree-claims values when materializing subtree-aggregates.jsonl.
    try:
        written, unchanged = _emit_jsonl_indexes()
    except kb_index_lib.FrameworkNodeParseError as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1
    print(f"[refresh-index] Wrote {written} file(s) under " f"{INDEX_DIR.as_posix()}/ ({unchanged} unchanged).")

    # Phase 3: the claim-graph sheet, from the index phase 2 just wrote. The
    # only precondition is that that index loads — no property of the graph is
    # one, so a spine with nothing attributed to it yet draws its unconnected
    # claims rather than waiting for a completeness nobody has reached. Here and
    # at no later point: the gate that follows a claim-graph pass runs refresh
    # and then verify, so a sheet minted after a verify would go missing exactly
    # on the halted build a reader most wants to look at.
    sheet = graph_ops.render(kb_root=KB)
    if sheet.exit_code != graph_ops.EXIT_RENDERED:
        print("\n" + "\n".join(sheet.lines()), file=sys.stderr)
        return 1
    print(f"[refresh-sheet] Wrote {sheet.written}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
