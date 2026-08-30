# kb_tools — the KB capability

**Bottom line:** `kb_tools/` is a portable, stdlib-only Python toolchain — paired
with a set of specialized agents — for turning a canonical authored corpus into a
**knowledge base (KB)**: a navigable, verbatim Markdown distillation of the source
that also carries a queryable *claim-graph metadata spine*. The tooling **builds,
validates, and queries** that spine; the agents **create, navigate, and maintain**
the KB on top of it. The toolchain authors no content and is corpus-agnostic —
nothing here is specific to any one paper or subject. Everything is Python 3.11+,
**standard-library only** (see [Invariants](#invariants)).

---

## What a KB is

A KB is a directory of Markdown that mirrors a canonical authored corpus and layers
structured, machine-checkable metadata over it. Two orthogonal graphs run through
it, and this toolchain builds and checks both:

1. **Topography graph** (navigation) — a hyperlink tree: `entry-point.md` → domain
   `index.md` → subtopic `index.md` → leaf. Every non-root document opens with an
   up-link to its parent. A container's `kind`
   (`entry-point` | `index` | `leaf` | `leaf-as-index`) is its structural-position
   label only; it does **not** encode claim-graph flavor.
2. **Claim graph** (the metadata spine) — an acyclic graph whose nodes are the
   corpus's formal results and whose edges record how they depend on and reinforce
   one another. Node types: `clm-` (claim), optional framework nodes
   (`invariant` / `axiom`), and `exp-` (experiment) / `sup-` (support). Nodes are
   declared additively in leaf frontmatter; a leaf is a **container** hosting any
   combination of node bodies. Format contract:
   [`METADATA_SCHEMA.md`](METADATA_SCHEMA.md) (corpus-neutral). A project may keep a
   local `SCHEMA.md` beside its `.index/` that specializes the contract with its own
   scope and examples.

**Leaves are verbatim.** A leaf is a faithful translation of its source — no
paraphrase, reframing, or audience simplification. This is what lets the KB stand
in for the source and lets a divergence be resolved mechanically (below).

**Canonical direction.** Each layer is derived from the one above it and never
overrides it: the authored **source corpus** is canonical; the **KB Markdown** is
derived from the source; and within the KB, the **`.index/*.jsonl` artifacts** and
every derived metadata field (`solidity`, `build_status`, `build_band`,
`subtree-claims`, leaf-reference footers) are derived from the authored Markdown.
On any disagreement the more-canonical layer wins and the derived layer is rebuilt.
(If a project ever inverts this — editing *through* the KB and regenerating the
source — that decision is encoded in the navigation/maintenance agents and the
`/kb-*` commands, and must be flipped there.)

### Derived metadata, defined

- `confidence` (claims) / `quality` (supports) — **hand-authored** local rigor.
- `solidity` — **derived, never authored**: `round-half-up-2dp(min(confidence,
  *dependency solidities))`, the weakest link in the dependency cone. Framework
  deps contribute 1.0. `*pending*` propagates NaN-style through the DAG (a pending
  dependency forces a pending result regardless of local confidence).
- `build_band` / `build_status` — **derived** from `solidity` via the build-band
  ladder (`ok-to-build` ≥ 0.85 → … → `refuted` < 0.20).
- `subtree-claims` / leaf-reference footers — **derived** roll-ups /
  reverse-citation maps.

The `min` (not a confidence product) is deliberate: solidity must be
refactor-invariant — subdividing one derivation step into two same-quality steps
must not lower the result. See [`METADATA_SCHEMA.md`](METADATA_SCHEMA.md) for the
full rule.

---

## The agent set — creating and using a KB

The toolchain is the mechanism; the agents are the workflow. KB navigation and
editing go **through** these agents (the project contract mandates it), because the
agents carry the canonical-direction discipline and the verbatim/derived rules that
raw file edits would silently violate. Roles, by lifecycle stage:

**Create** — `kb-coordinator` orchestrates a build/revision pipeline that ingests a
canonical corpus and emits the KB:
- **source specialist** — surveys the source's structure and extracts content mapped
  to taxonomy positions (this toolchain ships a LaTeX specialist;
  `kb-latex-specialist`).
- `kb-taxonomy-architect` — designs the hierarchy: invariants list, document
  skeleton, navigation spec, acceptance criteria.
- `kb-content-distiller` — writes the Markdown leaves (verbatim source→Markdown) and
  the progressive index/entry-point summaries.
- `kb-structure-reviewer` + `kb-accuracy-reviewer` — adversarial review of
  navigability/link-integrity and of leaf fidelity (catching paraphrase drift and
  derived-as-given contamination). Never modify files.

**Use** — `kb-docent` (read-only) guides navigation through the topography graph,
manages session state, and executes topic switches. Loaded via the `/kb-start` and
`/kb-next` commands; it is the sanctioned way to browse the KB.

**Maintain** — `kb-maintainer` is the write side: add/edit leaves, wire S5
frontmatter and claim-graph ids/edges, migrate finished work into canonical leaves,
and run the refresh→verify loop to green. Parallel-safe by file ownership.

**Score the graph** — `applied-mathematician` supplies the one input the tooling
cannot derive: hand-authored `confidence` / `quality` (local rigor) and
derivation-dependency (`depends-on`) judgments. It reads a leaf and takes its
dependencies as given; everything downstream (`solidity`, bands, aggregates) is then
mechanical.

**Extend the toolchain** — `python-coder` for changes to `kb_tools/` itself
(stdlib-only, no third-party deps or required virtualenvs in committed tooling).

---

## Module inventory

Single-source modules (the anti-drift discipline — one definition, many consumers):

| Module | Role |
|---|---|
| `kb_util.py` | Repo/KB path construction (the consuming repo's root is discovered by walking up from the cwd to `.git` with `kb-root/` beside it — never from `__file__`, which resolves through the consumption symlink into the wrong repo; the KB directory name is single-sourced here, not hardwired across tools) **and** maintenance-command hints (`just`/`make` `refresh`/`verify`, detected from the root's runner file) for remediation output + tests. Also the runner-target installer CLI (`--install-targets` / `--uninstall-targets`; see [Runner targets](#runner-targets-the-only-sanctioned-entry-points)). |
| `kb_schema.py` | The build-band ladder, the `clm`/`exp`/`sup` id grammar (`[a-z0-9]{6}` body), and the collision-checked id minter. Mirrors the vocabulary in `METADATA_SCHEMA.md`. |
| `kb_links.py` | Low-level Markdown link-scanning primitives (code-span neutralization, inline-link regex, file crawl, skip-dir rules) shared by the link checker and the query CLI's reverse-find. Primitives only; classification/gating stays in the checker. |

Pipeline scripts:

| Script | Role |
|---|---|
| `kb_index_lib.py` | Core library: leaf/register parsing + the shared `compute_solidity` / subtree-aggregate / leaf-reference computations. Both refresh and verify call these so the two can never dual-compute and drift. |
| `refresh_kb_metadata.py` | The refresh target — regenerates derived metadata (solidity write-backs, subtree aggregates, leaf-reference footers) and the `.index/*.jsonl`. Idempotent: same canonical state → byte-identical output. |
| `verify_kb_metadata.py` | Claim-graph integrity gate — frontmatter coverage, id uniqueness, orphan/referential integrity, bidirectional coverage, acyclicity, and freshness (runs the build in dry-run, diffs against on-disk; any diff = stale = hard fail). |
| `verify_md_links.py` | Repo-wide dead-link gate — **every** `.md` file must have zero dead links. |
| `mint_claim_ids.py` | Mint N collision-checked `clm-` ids. |
| `kb_cmd/` | Read-side query package over `.index/` (`index.py` loads JSONL into dataclasses and exposes question-shaped lookups; `cli.py` is the shell wrapper). |
| `tests/` | Unit tests over synthetic fixtures (the project's test target). |

### The derived index (`<kb-root>/.index/`)

`refresh` materializes a fixed set of JSONL files, one record per line, sorted by a
documented per-file key so a new node/edge is a single inserted `git diff` line:
`claims.jsonl` (type-tagged node union), `depends-on.jsonl` (edge classes:
`depends` / `strengthens` / `supports`), `strengthen-by.jsonl`, `cites.jsonl`,
`supported-by.jsonl`, `subtree-aggregates.jsonl`. Record schemas, field orders, and
build invariants are specified in [`METADATA_SCHEMA.md`](METADATA_SCHEMA.md). This
section is the map.

---

## Query surface

`kb_cmd/index.py` loads the JSONL once per process into plain dataclasses and
exposes forward/inverse dependency, open-work, citation, subtree, filter, and lookup
queries. It is a normal package — `from kb_tools import kb_cmd` (or
`from kb_tools.kb_cmd import load`) for programmatic access, or run the CLI as
`python -m kb_tools.kb_cmd`:

```sh
PYTHONPATH=<repo> python3 -m kb_tools.kb_cmd <cmd>
# deps <id> [-i] | gated-on <id> | cited-by <id> | solidity-below <n>
# weak-points | subtree <path> | show <id> | stats     (--json for jq)
```

At KB scales in the low hundreds of nodes, full load + query is sub-10 ms cold; no
caching layer.

---

## Runner targets (the only sanctioned entry points)

Per project policy, **do not** run these tools ad-hoc — use the consuming
project's runner target (the tools detect the runner and name it in
remediation hints).

A consuming project's runner gains the KB targets by **include, not copy**:
exactly one installed line pulls a fragment shipped in this repo live through
the `.claude/agents` symlink — `-include
.claude/agents/kb_tools/runner-snippets/kb.mk` in a Makefile, or `import?
'.claude/agents/kb_tools/runner-snippets/kb.just'` in a justfile (`import?`
requires just ≥ 1.33). The non-fatal include forms are deliberate: a broken
symlink degrades to missing KB targets, never a broken runner. The line is
managed mechanically by the installer, run from the consumer root:

```sh
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --install-targets
PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util --uninstall-targets
```

(An existing justfile wins over a Makefile; `--runner just|make` forces the
choice, and on install creates the runner file when it doesn't exist.)

Consumer-side targets, defined by the included fragment (stdlib-only, run
under the system `python3` — no venv):

- `verify` — the green gate: `verify_md_links.py` then `verify_kb_metadata.py`.
- `refresh` — regenerate derived metadata + `.index/` from authored sources.
- `stats` — claim-graph dashboard via the query CLI.

Tooling-repo targets (the agent-definition repo's own justfile; never
installed into consumers):

- `test` — the `kb_tools/tests/` suite (auto-provisions `.venv` + `pytest`;
  the sole non-stdlib dep, used only to run the tests).
- `format-python` — `black` (line-length 120) + `isort` over `kb_tools`
  (run after modifying tooling).

---

## Invariants

- **Stdlib-only.** No third-party runtime dependency and no required virtualenv in
  committed tooling. `pytest` (+ `black`/`isort` for formatting) live only in the
  dev `.venv` the test/format targets provision; nothing under `kb_tools/`
  imports them at runtime.
- **Determinism.** `refresh` against a fixed canonical state is byte-identical (no
  timestamps, random ids, or environment-dependent paths in records). Mint-time
  randomness never enters the rebuild.
- **Single-source / anti-drift.** Paths, the KB directory name, command names, the
  build-band ladder, the id grammar, and every shared computation have exactly one
  definition; consumers import it. A change flows through one constant, not
  scattered literals.
- **Derived-vs-authored split.** Only `confidence` / `quality` and the graph
  structure are hand-authored; every solidity/band/aggregate/footer is recomputed
  and drift-gated. Hand-editing a derived field is a `refresh-fixable` verify
  failure.
- **Verbatim leaves + canonical direction.** Leaves do not paraphrase the source,
  and no derived layer overrides its canonical parent.

---

## Project scoping

The format fully specifies `exp-` (experiment) and `sup-` (support) node types,
their edge classes (`strengthens`, `supports`), their solidity branches, and the
framework (`invariant` / `axiom`) node type — but **which of these a given KB
populates is a project decision, not a property of the toolchain.** Claim nodes are
the universal core. A KB populates:

- **framework nodes** only if its `<kb-root>/CLAUDE.md` declares invariant/axiom
  headings (else claim chains terminate on dependency-free foundational claims, and
  out-of-graph modeling assumptions are noted in rationales as prose);
- **experiment nodes** only if it documents physical experiments the authors design
  and control (a simulation feeds derivation confidence, not experimental solidity;
  a re-analysis of outside data is a `sup-` or a `clm-` citation, never an `exp-`);
- **support nodes** only if it carries non-physical analytical support that lifts a
  claim without gating it.

A KB that exercises only claim nodes still conforms; the unused record schemas
simply have zero instances, and verify/refresh stay green either way. The concrete
node population, scope, and canonical source for **this** repository's KB live in
the repo-root orientation docs and the KB's local `SCHEMA.md`; run the project's
stats target for live node/edge counts (not hard-coded here, to avoid drift).
