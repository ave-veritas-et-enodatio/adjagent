# ARCHITECTURE — Agent Definition Repository

How this repository satisfies SPEC.md. Cites SPEC's requirements rather than restating them.

## Repo Layout

```
agents/                 deployed surface — installed as .claude/agents
  mad/                  MAD supporting material, outside the dispatch namespace
    participant-contract.md  generated; extracted as a guest model's system prompt
    review-topics/      review methodology topics, read by mad-review-referee
    design-topics/      design methodology topics, read by mad-design-referee
  liaison_tools/        shell/python helpers shared by guest-liaison.md and mad-guest-liaison.md
  kb_tools/             portable KB toolchain — stdlib-only python package + kb_cmd/ query package, its contract docs, tests, runner-include fragments
commands/               deployed surface — installed as .claude/commands
templates/              template sources — maintenance-only, not session-visible
  agents/               *.md.tmpl — one per generated agent definition (or family), rendered into agents/; subdirectories mirror into the surface (mad/participant-contract.md.tmpl -> agents/mad/participant-contract.md)
  commands/             *.md.tmpl — command templates, rendered into commands/
  shared-sections.toml  the single source of shared text (chunks) for both template types
gen-defs.py             renders templates/ into the deployed surfaces, and checks them
user-config/            published operator baseline (~/.claude/CLAUDE.md) + its README
```

## Install Consumption Model

A consuming project receives `agents/` and `commands/` by install (SPEC.md, Deployed Surfaces) — `just install-defs <project> [flavor]`, wrapping `gen-defs.py --install <project>/.claude`.

The surface boundary remains the repo's load-bearing partition, and the install is what enforces it: everything under `agents/` and `commands/` is **session-visible** — it is what an install copies, and therefore all a live Claude Code session in a consuming project can read — while everything else (`templates/`, `user-config/`, the contract docs, the justfile) is **project space** that no install ever emits, visible only when working inside this repository itself. Generator internals, chunk sources, and maintainer notes cannot leak into a dispatched agent's context because they are never installed. The partition is now checked by a filesystem walk rooted at the two surfaces rather than trusted to a link's scope, which also means the two exceptions inside the surfaces — test suites and caches, which are maintenance material sitting in session-visible space — are excluded explicitly (`INSTALL_EXCLUDED_*` in `gen-defs.py`) rather than by placement.

Mechanism, in three parts:

- **Plain copy.** An install is a recursive copy of both surface trees, minus that exclusion list. There is no complement computation and no inclusion list: generated definitions are copied like everything else, because a checked-in surface already holds each definition's base render. A new hand-maintained definition or tool file therefore ships with no enrollment step.
- **Flavor render.** Only a flavored install renders. After the copy, the ordinary generation pass (below) runs against the installed tree under the requested family/model, rewriting what the flavor changes. Because every target it meets is a fresh copy — provably untouched output by its banner hash — that pass leaves no numbered backups behind. An unflavored install instead runs *check* against the installed tree and reports the verdict without gating on it: the target may hold material this tool never put there.
- **Provenance stamp.** `agents-install-manifest.json` is written into the installed `.claude/` on every install, recording generator version, source commit (`-dirty`-suffixed for a modified work tree), flavor, and timestamp. It is write-only — nothing reads it back, because SPEC's artifact semantic means a re-install overwrites unconditionally rather than reasoning about what is already there.

## Template System

`gen-defs.py`'s module docstring is the definitive mechanism description; this section is a map into it, not a restatement.

- **Output routing**: a template's surface tree routes its output — `templates/agents/` renders into `agents/`, `templates/commands/` into `commands/`. Discovery recurses within each tree and a template's relative subpath is mirrored into its surface (`templates/agents/mad/participant-contract.md.tmpl` → `agents/mad/participant-contract.md`), so placement is declared by the filesystem alone — there is no metadata key for it. Every other mechanism below applies identically to both template types and unchanged at any nesting depth. `templates/commands/` may be absent or empty (git does not track an empty directory). As a guard against a wrong output-directory constant, generation asserts each surface directory receiving output exists inside the repository before writing — mirrored subdirectories beneath a surface come from the template tree, not a constant, and are created on demand — and its report states where files landed.

- **Chunks** (`shared-sections.toml`, `[chunks.*]`): the single source for text shared across two or more definitions. A chunk has either a `text` body or a set of named `variants` — never both.
- **Marker syntax**, usable in templates and inside chunk bodies:
  - `@@name@@` — expand chunk `name`
  - `@@name variant="x"@@` — expand a specific variant of a multi-variant chunk
  - `@@name key="value"@@` — bind `@@key@@` inside the chunk body (any key other than the reserved `variant`/`wrap`); falls back to `[chunks.name.defaults]` when the marker omits it
  - `@@name wrap="70"@@` — greedy-wrap the expansion to 70 columns
  - `@@nb name="anchor"@@` — model-tuning NB anchor; expands to nothing unless a loaded family file fills it (`nb` is a reserved marker name)
- **Model tuning and render-to-order**: a family file (`templates/models/<family>.toml`, authoring guide in `templates/models/README.md`) fills NB anchors — at most one `**NB**:` per anchor, per-model override winning over family-wide text — and can never replace, suppress, or modify base text; a base render is byte-identical with or without anchors present. `--output-dir ROOT` (an existing directory; surface subtrees created under it) and `--surfaces agents|commands|both` render tuned sets outside the deployed surfaces, with the full banner/backup/refusal write-safety table applying identically there. `--model-family` accepts a bare family name as well as a path — resolved against `templates/models/` as `<name>-addenda.toml` or `<name>.toml` (both matching or neither is an error) — and a bare `--model` without `--model-family` implies the unique family whose `[nb.*.models.*]` tables mention that model (none or several is an error naming the fix). A tuned banner additionally claims its family file (and model); check accepts the same four flags, renders under exactly that tuning, and reports a target whose banner claims a different tuning as `MISTUNED` — so validating a tuned set means invoking check with that set's flags.
- **Multi-output templates**: a template may open with a fenced `+++ ... +++` TOML block declaring `[outputs.<definition-name>]` tables, one per rendered definition, each supplying the parameters that differ (e.g. `model`, `color`). The body below the fence renders once per declared output, with `@@name@@` bound to that output's own key plus its declared parameters — bodies are identical by construction, not by maintenance discipline. `templates/agents/mad-participant.md.tmpl` is the instance: one body, four model-pinned outputs (`mad-participant-fable/opus/sonnet/haiku`).
- **Single-source discipline**: shared text lives in exactly one chunk; a template never pastes a paraphrase of a chunk's text inline (see AGENTS.md).

## Banner and Backup Mechanism

Every generated definition is stamped with a four-line YAML-comment banner naming its source template (`# !GENERATED! from templates/agents/<name>.md.tmpl and templates/shared-sections.toml`, or `templates/commands/<name>.md.tmpl` for a command) and, on its own line, `# !BODY-SHA256! <hex>` — the sha256 of everything the file holds after the banner block, as written. The banner always lives in frontmatter, never in the prompt body: agent templates must open with a frontmatter block and the banner is injected at its top; a command template with a frontmatter block gets the same injection, and one without gets a minimal frontmatter block emitted above its body containing only the banner comment lines. The banner is stamped last, since it carries the hash of what follows it.

The banner is a definition's only claim to being generated, and drives every branch of SPEC.md's write-safety table; the hash line is what splits that table's two overwrite rows. A body hashing to its own claim is provably this tool's output — reproducible from the template, so no backup is kept and routine regeneration produces no `*.bak` churn. A body that does not match, or a file whose banner predates the hash line, is treated as hand-edited: the prior content is copied to `<name>.md.<NN>.bak` beside it before the overwrite — a copy, not a rename, so the live file keeps its inode, owner, and mode regardless of who regenerates it. `NN` is per-target, zero-padded from `00`, allocated as the highest existing serial plus one, and never reused. `*.bak` files are gitignored generator safety copies (see AGENTS.md), swept by `just clean-backups` across both surface trees at any depth.

## The Two Checks

Running the checker (no `--generate` flag) performs two independent checks; either failing exits nonzero:

1. **Render-identity**: every declared target is byte-identical to what its template currently renders. Catches hand-edits to generated output and definitions left stale by a chunk/template change. A target with no banner is reported `REFUSED`, not compared. The reported diff is taken over the post-banner bytes — a body change already carries its own hash line, and diffing that too would only add noise.
2. **Banner-claims-vs-templates**: every `*.md` anywhere under `agents/` and `commands/` — the walk recurses, since outputs mirror nested template paths — carrying a banner names a template that exists and declares that definition. Catches what check 1 is blind to — a banner surviving after its template is deleted or renamed (`ORPHAN`), or naming a template that no longer declares it (`MISLABELED`). Check 1 walks templates outward to definitions; check 2 walks the claim back the other way.

## Sanctioned Invocation

`just generate`, `just check`, and `just install-defs` are the sole sanctioned entry points to this mechanism — all three wrap `gen-defs.py` at the repo root. No other invocation (direct `python3` calls, ad-hoc scripting against `shared-sections.toml`, hand-copying a surface into a project) is sanctioned (see AGENTS.md).

## Subsystem Map

One level under `agents/`:

- **Coder family** — generalist-coder, language coders (go, python, rust), shell-dsl-coder, platform experts (android/ios/linux/macos/web/windows-app-expert), and architect: generated from `templates/agents/*.md.tmpl`. security-reviewer, tech-writer, and tech-writer-reviewer share the coding-review domain but are hand-maintained — no template exists for them.
- **MAD set + `mad/`** — mad-review-referee, mad-design-referee, mad-alignment-assessor, the four model-pinned participants (mad-participant-fable/opus/sonnet/haiku), and mad-guest-liaison sit directly under `agents/` as dispatchable definitions: all generated. Everything the set reads rather than dispatches is consolidated under `agents/mad/`: `participant-contract.md` (generated from `templates/agents/mad/participant-contract.md.tmpl`, the body the four participants share and a guest model receives as its system prompt), and `review-topics/` / `design-topics/`, the per-domain methodology each referee reads at dispatch.
- **kb set + kb_tools** — kb-accuracy-reviewer and kb-structure-reviewer: generated from `templates/agents/kb-accuracy-reviewer.md.tmpl` and `templates/agents/kb-structure-reviewer.md.tmpl`; kb-docent, kb-coordinator, kb-content-distiller, kb-taxonomy-architect, kb-latex-specialist, kb-maintainer: hand-maintained. All eight are project-portable — per-project facts live in the consuming project's `kb-root/CLAUDE.md`. `kb_tools/` is the toolchain the set drives: tools self-anchor by walking up from cwd to `.git` and requiring `kb-root/` beside it, detect the consumer's runner for remediation hints, and install their runner targets via `python3 -m kb_tools.kb_util --install-targets` (one non-fatal include line pulling `runner-snippets/kb.mk` or `kb.just`); the consumer surface is `verify`/`refresh`/`stats`, and the tool self-tests run under this repo's `just test`.
- **Liaisons + liaison_tools** — guest-liaison.md (hand-maintained, general-purpose) and mad-guest-liaison.md (generated, MAD-only) share `liaison_tools/`'s helpers (`post-openai.sh`/`.py`, `msg-util.sh`, `extract-agent-body.sh`) rather than each reimplementing the wire protocol or the messages-file format. `relay_driver.py` — the corpus-relay eval instrument — composes those same helpers (`post-openai.sh` as sole transport, `msg-util.sh` as sole messages-file mutator) into a scripted READ/LIST/GREP retrieval-eval loop.
- **Specialists** — applied-mathematician, biz-dev-strategist, economic-historian, literature-scout, marketing-comms-expert, prose-architect, theoretical-economist: single-purpose, invoked directly rather than as part of a coding or MAD pipeline. applied-mathematician is generated from `templates/agents/applied-mathematician.md.tmpl` (it carries model-tuning NB anchors); the rest are hand-maintained.
