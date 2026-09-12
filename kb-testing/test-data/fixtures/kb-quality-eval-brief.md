# Fixture: KB inferential-quality evaluation brief (instrument v2)

The standing comparison instrument for grading built KBs (claude-tier and
gemma-tier runs alike). Every evaluation MUST use the prompt body below
unchanged, so reports compare row for row across runs — edits to the
instrument are edits to the comparison baseline and get owner sign-off.

Dispatch contract: fresh `general-purpose` agent, `model: fable`, cold
context, background. Substitute exactly three parameters, nothing else:

- `{kb-root-path}` — absolute path to the built KB's kb-root/
- `{output-report-path}` — absolute path for the report (convention:
  `mad-design/KB_INFERENTIAL_QUALITY_EVAL_<run-tag>.md`, one per run)
- `{corpus-note}` — one sentence describing corpus and build context
  (e.g. "built overnight by an AI pipeline from 7 LaTeX volumes (an
  economics working paper, its derivations companion, and five short
  variants)")

Instrument provenance: authored 2026-08-31 for the run-1 evaluation,
reused verbatim for run-2 (2026-09-01). Validated in use: run-1 produced
2 material findings, both subsequently confirmed by independent
provenance tracing.

---- PROMPT BODY BELOW — SUBSTITUTE PARAMETERS, CHANGE NOTHING ELSE ----

You are an independent evaluator examining a freshly built knowledge base for INFERENTIAL quality — the quality of the judgment calls that shaped it. You had no part in building it; bring fresh eyes and no loyalty to its choices.

The KB: {kb-root-path} — a hierarchical Markdown knowledge base with a claim-dependency graph, {corpus-note}. Mechanically verified green, including a citation-integrity validator.

READ-ONLY throughout. Do NOT run `git show`, inspect git tags, or consult any prior/reference KB or earlier evaluation reports — evaluate only what exists in kb-root/ now, independently. Do not modify anything anywhere except writing your one output file.

**Out of scope**: mechanical consistency (link integrity, frontmatter validity, index freshness, citation-channel conformance) — structurally guaranteed and separately verified. Do not spend attention re-checking it.

**In scope — the judgment calls**: taxonomy shape (domain decomposition, hierarchy depth, what became a domain vs a subtopic), leaf granularity (chunking of content into files), entry-point design, intermediate index summaries (do they accurately and discriminatively represent their subtrees?), claim-vs-support assignments and dependency-edge judgment in the claim graph, invariant placement (what went into kb-root/invariants.md), naming choices.

**The test — calibrate carefully**: NOT "would you have done it differently" (you always could; that is noise). The question is whether any call could have been made differently in a way that MATERIALLY improves the artifact, where quality means: clear discovery paths; accurate summaries at intermediate nodes; high likelihood that even a low-parameter model can find and synthesize answers; in one phrase — low drag in the substrate, leaving maximum model attention for the real task. Every wrong turn a summary invites, every non-discriminating index, every mis-chunked leaf is attention taxed away from the reader's actual problem.

**Method**:
1. Task-driven walks: run every question in the fixed walk set below, each exactly as written; log and report those first, in listed order. Then keep walking — add your own questions, and press variant hunts off any walk that resolved cleanly (re-ask the same target one step narrower; ask what the KB itself says about the thing you just found). The fixed set makes runs comparable; the walks you invent are where a substrate's edges usually show, so do not stop at the fixed set. Walk each from entry-point.md down as a small model would — following summaries, not your own shortcuts. Log every point where the substrate fights back: a summary that misleads, an index that doesn't discriminate between its children, nesting deeper than the content warrants, a leaf that chunks two ideas or splits one.
2. Summary-faithfulness sampling: pick ~8 intermediate index nodes; compare each summary against its actual subtree for accuracy and discrimination.
3. Claim-graph judgment sweep: sample claims and supports; assess whether clm/sup assignments and depends-on edges reflect sound judgment about what is load-bearing.

**Fixed walk set** (step 1 — every one, verbatim, in this order; the bracketed tag is the walk's category):

1. [locate] "What is the healthy-state lifetime of a weakly governed firm, and where is it derived?"
2. [locate] "What are the five constitutional modifications?"
3. [locate] "What is σ₂ and how would it be measured?"
4. [locate] "Where does the factor-of-two discrepancy live?"
5. [locate] "What deployment evidence exists?"
6. [synthesize] "what falsifies the thesis?"
7. [synthesize] "does the corpus prove the cure spreads?"
8. [synthesize] "Can a productive elite escape into an enclave?"
9. [synthesize] "Is KPI-gating optimal?"
10. [trace-support-chain] "Percolation threshold: origin, support, dependence on the firm result?"
11. [trace-support-chain] "What supports Theorem 5.1?"
12. [trace-support-chain] "Can I build on Theorem 4.2?"

A question whose subject this build does not contain is itself a result: report it as a walk that found nothing, with what you looked at, rather than substituting a nearby question.

**Attribution, per material finding**: read the pipeline's worker definitions in the consuming repo's .claude/agents/ (kb-taxonomy-architect.md, kb-content-distiller.md, kb-latex-specialist.md, and the KB's own invariants.md/CONVENTIONS.md conventions) and classify each finding: (a) INSTRUCTION CLARITY — the guidance was silent or ambiguous on the dimension that went wrong; (b) CAPABILITY — the guidance was explicit and the execution fell short; (c) TASK DIFFICULTY — the call is genuinely contested among reasonable designs. This attribution decides what gets fixed and how, so ground it in the definition text, quoting the relevant passage or noting its absence.

**Output**: write your report to {output-report-path}. Structure: verdict summary first (the overall shape: headroom-above-a-floor vs material-quality-loss, with the count of material findings); then material findings each with severity, the specific alternative call that would have been better, the quality dimension impacted, and the attribution with its grounding; then a section of immaterial observations (calls that could differ without material impact — these are evidence of headroom, list them briefly); then your walk logs as an appendix (the raw evidence). Be the evaluator who would rather report three real findings than thirty defensible ones.

---- PROMPT BODY ABOVE — MAINTAINER NOTES BELOW, NEVER EXTRACTED ----

## Changelog

- **v2 — 2026-09-01.** Fixed walk set replaces per-run invented questions;
  adopted at the gemma series boundary. Walk-set change only — no other
  pending revision to this instrument is folded in.
- **v1 — 2026-08-31.** Original; each run invented its own ~6-10 walks.

## Fixed walk set — provenance

Harvested from the walk appendices of the three v1 runs
(`mad-design/KB_INFERENTIAL_QUALITY_EVAL.md` = run 1,
`..._RUN2.md`, `..._RUN3.md`), de-duped on target rather than wording;
each survivor's phrasing is its source walk's, verbatim.

Probe results do not travel into the body, and neither does this table:
an evaluator who knows which probes have drawn findings inherits the
prior run's path instead of walking fresh, and reports a re-check
wearing the costume of an independent walk.

| # | source walk | finding it exposed |
|---|---|---|
| 1 | RUN3 walk 1 (also RUN2 walk A, dropped wording) | none |
| 2 | RUN3 walk 2 (also RUN1 W3, dropped wording) | none |
| 3 | RUN3 walk 5 | none on the walk; its variant hunt ("what confidence does the KB assign the σ₂ < 1 hinge?") produced RUN3 Finding 2 |
| 4 | RUN3 walk 8 (also RUN1 W2, dropped wording) | none on the walk; its variant hunt ("read Corollary G.2's leaf") produced RUN3 Finding 1 |
| 5 | RUN2 walk B (also RUN1 W7, dropped wording) | none |
| 6 | RUN1 W4 | none |
| 7 | RUN1 W6 | none |
| 8 | RUN3 walk 3 | none |
| 9 | RUN2 walk E = RUN3 walk 6 (identical wording) | none |
| 10 | RUN2 walk C | none |
| 11 | RUN3 walk 7 | none |
| 12 | RUN2 walk F | RUN2 M1 + M2 (the solidity-band collapse and the inert support layer) |

Dropped, with reason — restore any by re-adding the source wording:

- RUN1 W1 ("what does β = 1 separate, and on what proof?") — same theorem
  and same route as #12; kept the reliability framing, which exercises
  the graph layer too.
- RUN1 W5 ("support chain of the healthy-state lifetime") — same route as
  #1; the trace category is carried by #10-#12.
- RUN1 W8 ("how solid is `thm:switched` given σ₂ is undischarged?") — same
  target as #12 (consult the derived reliability layer about a headline
  theorem). #12 survives as the plainer wording.
- RUN1 W9 ("Part 1's ψ coupling and its resolution") — reached as a
  cross-link on the #1 and #9 routes in prior runs.
- RUN2 walk D ("What is σ_att and who treats it?") — same target species as
  #3 (define a corpus symbol, find the volume that owns it); RUN2's walk
  resolved out of the notation table without entering a domain, so it
  exercises little substrate.
- RUN3 walk 4 ("Under what conditions does the constitutional form
  spread?") — same target as #10, which reaches the same thresholds and
  also tests origin and support.
- RUN3's two variant hunts (parenthesized in rows 3 and 4 above) are
  deliberately NOT in the fixed set: both findings that run reported came
  from hunts a fixed list would not have contained, and promoting them
  would convert the demonstration that free hunting pays into a checklist
  item. The free tail in step 1 exists to regenerate that behavior.

All twelve were checked as corpus-facing — each names source content
(a result, a symbol, a labelled theorem, a volume's subject) rather than
one build's directory or index shape — so they transfer across builds.
A build that lacks a probe's subject is handled in the body: report the
empty walk, do not substitute.
