# ARCHITECTURE — Agent Definition Repository

How this repository satisfies SPEC.md. Cites SPEC's requirements rather than restating them.

## Repo Layout

```
agents/                 deployed surface — symlinked as .claude/agents
  mad-review-topics/    review methodology topics, read by mad-review-referee
  mad-design-topics/    design methodology topics, read by mad-design-referee
  liaison_tools/        shell/python helpers shared by guest-liaison.md and mad-guest-liaison.md
  kb_tools/             portable KB toolchain — stdlib-only python package + kb_cmd/ query package, its contract docs, tests, runner-include fragments
commands/               deployed surface — symlinked as .claude/commands
templates/              template sources — maintenance-only, not session-visible
  agents/               *.md.tmpl — one per generated agent definition (or family), rendered into agents/
  commands/             *.md.tmpl — command templates, rendered into commands/
  shared-sections.toml  the single source of shared text (chunks) for both template types
gen-defs.py             renders templates/ into the deployed surfaces, and checks them
user-config/            published operator baseline (~/.claude/CLAUDE.md) + its README
```

## Symlink Consumption Model

A consuming project links `.claude/agents` → `agents/` and `.claude/commands` → `commands/` (SPEC.md, Deployed Surfaces). This is the repo's load-bearing partition: everything under `agents/` and `commands/` is **session-visible** — a live Claude Code session in a consuming project reads it through the symlink — and everything else (`templates/`, `user-config/`) is **project space**, visible only when working inside this repository itself. Generator internals, chunk sources, and maintainer notes therefore never leak into a dispatched agent's context; only rendered output does.

## Template System

`gen-defs.py`'s module docstring is the definitive mechanism description; this section is a map into it, not a restatement.

- **Output routing**: a template's parent directory routes its output — `templates/agents/*.md.tmpl` renders into `agents/`, `templates/commands/*.md.tmpl` into `commands/`. Every other mechanism below applies identically to both template types. `templates/commands/` may be absent or empty (git does not track an empty directory). As a guard against a wrong output-directory constant, generation asserts each resolved output directory exists inside the repository before writing, and its report states where files landed.

- **Chunks** (`shared-sections.toml`, `[chunks.*]`): the single source for text shared across two or more definitions. A chunk has either a `text` body or a set of named `variants` — never both.
- **Marker syntax**, usable in templates and inside chunk bodies:
  - `@@name@@` — expand chunk `name`
  - `@@name variant="x"@@` — expand a specific variant of a multi-variant chunk
  - `@@name key="value"@@` — bind `@@key@@` inside the chunk body (any key other than the reserved `variant`/`wrap`); falls back to `[chunks.name.defaults]` when the marker omits it
  - `@@name wrap="70"@@` — greedy-wrap the expansion to 70 columns
- **Multi-output templates**: a template may open with a fenced `+++ ... +++` TOML block declaring `[outputs.<definition-name>]` tables, one per rendered definition, each supplying the parameters that differ (e.g. `model`, `color`). The body below the fence renders once per declared output, with `@@name@@` bound to that output's own key plus its declared parameters — bodies are identical by construction, not by maintenance discipline. `templates/agents/mad-participant.md.tmpl` is the instance: one body, four model-pinned outputs (`mad-participant-fable/opus/sonnet/haiku`).
- **Single-source discipline**: shared text lives in exactly one chunk; a template never pastes a paraphrase of a chunk's text inline (see AGENTS.md).

## Banner and Backup Mechanism

Every generated definition is stamped with a three-line YAML-comment banner naming its source template (`# !GENERATED! from templates/agents/<name>.md.tmpl and templates/shared-sections.toml`, or `templates/commands/<name>.md.tmpl` for a command). The banner always lives in frontmatter, never in the prompt body: agent templates must open with a frontmatter block and the banner is injected at its top; a command template with a frontmatter block gets the same injection, and one without gets a minimal frontmatter block emitted above its body containing only the banner comment lines. The banner is a definition's only claim to being generated, and drives every branch of SPEC.md's write-safety table. When a bannered target's render changes, the prior content is copied to `<name>.md.<NN>.bak` beside it before the overwrite — a copy, not a rename, so the live file keeps its inode, owner, and mode regardless of who regenerates it. `NN` is per-target, zero-padded from `00`, allocated as the highest existing serial plus one, and never reused. `*.bak` files are gitignored generator safety copies (see AGENTS.md).

## The Two Checks

Running the checker (no `--generate` flag) performs two independent checks; either failing exits nonzero:

1. **Render-identity**: every declared target is byte-identical to what its template currently renders. Catches hand-edits to generated output and definitions left stale by a chunk/template change. A target with no banner is reported `REFUSED`, not compared.
2. **Banner-claims-vs-templates**: every `agents/*.md` and `commands/*.md` carrying a banner names a template that exists and declares that definition. Catches what check 1 is blind to — a banner surviving after its template is deleted or renamed (`ORPHAN`), or naming a template that no longer declares it (`MISLABELED`). Check 1 walks templates outward to definitions; check 2 walks the claim back the other way.

## Sanctioned Invocation

`just generate` and `just check` are the sole sanctioned entry points to this mechanism — both wrap `gen-defs.py` at the repo root. No other invocation (direct `python3` calls, ad-hoc scripting against `shared-sections.toml`) is sanctioned (see AGENTS.md).

## Subsystem Map

One level under `agents/`:

- **Coder family** — generalist-coder, language coders (go, python, rust), shell-dsl-coder, platform experts (android/ios/linux/macos/web/windows-app-expert), and architect: generated from `templates/agents/*.md.tmpl`. security-reviewer, tech-writer, and tech-writer-reviewer share the coding-review domain but are hand-maintained — no template exists for them.
- **MAD set + topics** — mad-review-referee, mad-design-referee, mad-alignment-assessor, mad-participant-contract plus its four model-pinned renders (mad-participant-fable/opus/sonnet/haiku), and mad-guest-liaison: all generated. `mad-review-topics/` and `mad-design-topics/` hold the per-domain methodology each referee reads at dispatch.
- **kb set + kb_tools** — kb-accuracy-reviewer and kb-structure-reviewer: generated from `templates/agents/kb-accuracy-reviewer.md.tmpl` and `templates/agents/kb-structure-reviewer.md.tmpl`; kb-docent, kb-coordinator, kb-content-distiller, kb-taxonomy-architect, kb-latex-specialist, kb-maintainer: hand-maintained. All eight are project-portable — per-project facts live in the consuming project's `kb-root/CLAUDE.md`. `kb_tools/` is the toolchain the set drives: tools self-anchor by walking up from cwd to `.git` and requiring `kb-root/` beside it, detect the consumer's runner for remediation hints, and install their runner targets via `python3 -m kb_tools.kb_util --install-targets` (one non-fatal include line pulling `runner-snippets/kb.mk` or `kb.just`); the consumer surface is `verify`/`refresh`/`stats`, and the tool self-tests run under this repo's `just test`.
- **Liaisons + liaison_tools** — guest-liaison.md (hand-maintained, general-purpose) and mad-guest-liaison.md (generated, MAD-only) share `liaison_tools/`'s helpers (`post-openai.sh`/`.py`, `msg-util.sh`, `extract-agent-body.sh`) rather than each reimplementing the wire protocol or the messages-file format.
- **Specialists** — applied-mathematician, biz-dev-strategist, economic-historian, literature-scout, marketing-comms-expert, prose-architect, theoretical-economist: hand-maintained, single-purpose, invoked directly rather than as part of a coding or MAD pipeline.
