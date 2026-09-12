# README – Adjagent: An Agent and Command Set Generator

This repository generates agents rather than collecting them — a single-sourced generation system paired with the multi-capability agent and command set it renders. Definitions are produced to order: tuned per model family, installed into consuming projects as hash-verified artifacts, regenerated rather than copied and hand-maintained. The generator owns the guarantees — one source for every shared span of text, additive-only model tuning, drift caught by content hash; the rendered set does the work — coder and platform specialists, multi-agent debate processes, knowledge-base construction and tooling, guest-model liaisons.

## Install

Clone this repo anywhere, then install both deployed surfaces into the consuming project — from **this** repo's root:

```sh
just install ~/projects/foo
```

That produces `agents/` and `commands/` inside `~/projects/foo/.claude/` in full: every definition, the MAD design-topic set, `kb_tools/`, `liaison_tools/`, and the slash commands. The definitions are rendered by that invocation — this repo keeps no checked-in copy of them — and the tool packages are copied. Test suites and caches stay behind. Every `--*` flag after the target forwards verbatim to `gen-defs.py`, so tuning the render is one more flag, not a different command — `just install ~/projects/foo --family=gemma-4` tunes the generated definitions for that model family (see "Variants and platform compatibility" below).

The installed tree is an **artifact**: this repo is the source of truth, and re-running the install overwrites it. Don't edit files under a consuming project's `.claude/agents/` — change the template here, then re-install. Every installed file says as much in a banner of its own, which also records the hash of the content below it; an edit that breaks that hash is not lost when the re-install replaces the file, but set aside beside it as a numbered `.bak` (yours to delete). Re-installing an untouched tree changes nothing and backs up nothing.

Claude Code reads agent and command definitions from `.claude/`; nothing outside `agents/` and `commands/` is installed, so nothing else is visible to a session. Optionally, a symlink gives sessions in a consuming project a path to this repo's project space (justfile, templates, contract docs):

```sh
ln -s <path-to-this-repo>  .claude/adjagent-repo
```

Upgrading from the old symlink setup: remove the `.claude/agents` and `.claude/commands` symlinks and run `just install` against the project instead — that is now the only supported shape.

## Layout

```
templates/      template sources (not session-visible), rendered by gen-defs.py at the repo root — see ARCHITECTURE.md
rendered/       gitignored build product — `just generate` puts a full render here to inspect or PR-diff
user-config/    published operator baseline — see user-config/README.md
ROADMAP_PLANS/  tracked home for work that has some planning done but isn't ready to execute — usually, not necessarily, behind a ROADMAP.md item
```

`agents/` and `commands/` are not directories in this repository. They are the two deployed surfaces of the *product*, produced into `<project>/.claude/` by an install (or into `rendered/` by `just generate`), and the catalog below names entries by where they land there: every entry lives under `agents/` unless noted as a command.

The agent definitions assume a set of operator-level working rules; `user-config/` publishes that recommended baseline (`~/.claude/CLAUDE.md`) so it travels with the repo — install with `just install-claude-md`, which **merges rather than overwrites**: it recovers the published revision your live file was last integrated from out of this repo's git history, reports section by section what it found before writing a byte, keeps your own sections and edits, and keeps one rolling backup beside your file of whatever it replaces whenever it changes a byte — yours to delete. A file it cannot merge cleanly is left untouched and the baseline lands beside it as `incoming.CLAUDE.md`, for you to integrate by hand. See [user-config/README.md](user-config/README.md) for every case and for publishing changes the other way.

Contract docs, precedence in this order (code is the defect when it disagrees with a higher one): [SPEC.md](SPEC.md) — observable contract, [ARCHITECTURE.md](ARCHITECTURE.md) — how the mechanism works, [CONVENTIONS.md](CONVENTIONS.md) — house rules. [ROADMAP.md](ROADMAP.md) tracks open follow-on work, outside the precedence chain.

Working in this repo itself: `just generate` renders the full install product into the gitignored `rendered/`, where you inspect or PR-diff a change; `just check` re-renders from the templates and **diffs against what is in `rendered/`**, so it reports which definitions a template or chunk edit actually changed — run `just generate` first to lay down the baseline it compares against; `just test` runs the full tooling test suite (`kb_tools` + `liaison_tools` + `gen-defs.py`, auto-provisioning a `.venv`).

## Variants and platform compatibility

Model-specific defensive text is delivered through **overlay anchors and model-family files**. A template or shared chunk may expose an `@!fam.<key>!@` anchor at a spot where an observed failure mode needs a targeted note; the namespace ahead of the key names the registered source that fills it (`fam`, the family file, is the only one registered today), and with no such source loaded, or no entry for the key, the anchor renders as nothing, so the base definitions are byte-identical to an anchor-free render. A family file — `templates/family/<family>.toml`, one per model family, schema and the two-map system in [templates/family/README.md](templates/family/README.md) — fills anchors: family-wide `text`, with per-model overrides inside the same file. Resolution, not accumulation: at most one overlay renders per anchor, model scope winning over family scope, and the filled text renders verbatim in place with no lead-in or wrapper — an author who wants one writes it into their own text. A family file can never replace, suppress, or modify base text — it only fills anchors. Tuned sets are rendered to order, typically out of repo: `gen-defs.py --generate --output-dir <root> --family NAME`, where `NAME` selects `templates/family/<NAME>.toml` — a bare model name is not a family name. Two independent flags carry the tuning further: `--model-tier-map` overrides which family member each tier is tuned against, `--model-pin-map` overrides that tier's rendered `model:` value; narrowed to a subset of definitions by `--agent-glob`/`--command-glob` when the whole set is not wanted. `just install <target> [--family=NAME] [--model-tier-map=SPEC] [--model-pin-map=SPEC]` applies the same mechanism to an install (see "Install" above) — every flag forwards verbatim to `gen-defs.py`. (The earlier delivery vehicle — a parallel per-model definition file, e.g. `applied-mathematician-strict.md`, a gap-aversion variant built for running the math agent on Gemma 4 — was retired 2026-08-21; the anchor mechanism replaces whole-definition forks.)

The mechanism is a response to a real phenomenon: agent definitions tend to accumulate defensive language that's keyed to the *specific* model they were tested against. Defensive clauses that *protect* one model can *smother* another — same clause, opposite effect, no error event. (Example: probe data from 2026-04-29 showed Gemma 4 31B-it silently filling axiom gaps with textbook conventions, while Gemini 3.1 Pro spontaneously surfaced the same gaps. A "do not fill gaps" clause helps the first model and slows the second.)

The operating rule when porting an agent definition to a new model:
**strip first, observe, patch.** Run the base definitions with the new model on a known-shape probe set. Watch for failure modes; only then author an anchor and a family-file entry targeted at the failure modes you actually observed. Anchors are never pre-sprinkled speculatively. Heavy scaffolding hides the model's true tendencies; you can't engineer for failure modes you never see.

When adding defensive clauses, record *what tendency the clause was added to correct, against which model*. Without that record, future maintainers can't distinguish "still load-bearing" from "residue from a model we don't use anymore." Comment family-file entries like code: not what the overlay says, but why and against which observed behavior it was added.

## Coding Agent Set

* architect.md - architecture review and design
* security-reviewer.md - security-specific reviewer
* generalist-coder.md
* tech-writer.md
* tech-writer-reviewer.md
* language-specific coders
  * go-coder.md
  * python-coder.md
  * rust-coder.md
  * shell-dsl-coder.md
* platform-specific coders
  * android-app-expert.md
  * ios-app-expert.md
  * linux-app-expert.md
  * macos-app-expert.md
  * web-app-expert.md
  * windows-app-expert.md

Every definition in this catalog is **generated** — the coder and platform files, the MAD agent set, all three kb definitions, the specialists, the liaisons, and every slash command. There are no hand-maintained definitions and nothing to edit directly. Each is rendered from `templates/agents/<name>.md.tmpl` plus the shared text in `templates/shared-chunks.toml`, which is the single home of the sections they hold in common (command templates live in `templates/commands/` and render into the product's `commands/`). A template in a subdirectory renders to the mirrored path — `templates/agents/mad/participant-contract.md.tmpl` → `agents/mad/participant-contract.md`. Edit the template (agent-specific text) or the shared chunks (common text), then run `just generate`.

**`just check` is the refactor instrument, and the order matters.** It re-renders from the templates and diffs against what `rendered/` already holds, so the sequence is: `just generate` to lay down a baseline, make the edit, then `just check` to read **which definitions changed and how**. Lifting a duplicated span into a shared chunk should leave every other definition byte-identical; a refactor meant to touch two that quietly reflows a third is what this catches, by name. Regenerating immediately before checking would overwrite the baseline and make the diff empty, which is why `check` no longer does it for you. A definition is generated only if a template declares it, and a file lacking the `# !GENERATED!` banner is never overwritten. One template can declare several definitions — `templates/agents/mad-participant.md.tmpl` renders the four model-pinned participants from one body, so they cannot drift apart. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full mechanism.

## Specialists
Single-purpose agents invoked directly for non-coding work.
* prose-architect.md - rhythm and structural review of long-form prose
* marketing-comms-expert.md - messaging, positioning, copywriting, competitive framing
* biz-dev-strategist.md - business strategy, market analysis, GTM, monetization
* applied-mathematician.md - rigorous derivation, model construction, dimensional analysis, claim classification (identity / manifestation / consistency check / derived prediction). Takes given axioms at face value and derives consequences honestly. Use when working inside a formal system — established, novel, or mid-construction — and the task requires careful step-by-step reasoning rather than retrieval of textbook results.
* economic-historian.md - stress-tests historical claims, analogies, and "laws of history" against the record; the history lens in an adversarial panel
* literature-scout.md - finds the citations a manuscript should include, especially the omissions a referee would flag; verifies real references via web search rather than inventing them
* theoretical-economist.md - stress-tests production/growth, market-structure, and mechanism-design claims; the economics lens in an adversarial panel
* prompt-engineer.md - authors and revises model-facing text: agent definitions, commands, skills, template/chunk bodies, model-tuning overlay entries, agent-facing docs.

## Multi-Agent Debate Agent Set
Uses Multi-Agent Debate Process.
Two modes share the same participants but use different referees; only design mode carries a topic library:
* **Review mode** — adversarial assessment of an *existing* artifact (architecture, code, math, agent definitions).
* **Design mode** — constructive proposal for an *open problem* (derivations, software designs, hardware designs, other problem-solving).

### Shared Agents
* mad/participant-contract.md - the model-neutral participant contract (reviewer in review mode, proposer in design mode); the body a guest model receives as its system prompt
* mad-participant-fable.md, mad-participant-opus.md, mad-participant-sonnet.md, mad-participant-haiku.md - the same contract as dispatchable agents, one per model pin; a run draws its participants from different pins so their blind spots differ
* mad-guest-liaison.md - a liaison that can loop in an external model via API base url, key, and model name
* mad-alignment-assessor.md - only assesses alignment/disagreement among participants

### Liaison Tooling
`liaison_tools/` contains the helpers used by both `mad-guest-liaison.md` (MAD process) and `guest-liaison.md` (general guest-model sessions); an install delivers it to `.claude/agents/liaison_tools/`, which is the path the definitions name. The liaison agents are the primary callers; MAD referees set `TMPDIR` for liaison invocations to keep `mktemp` output contained in the review/design directory.
* `post-openai.py` - posts a message history to an OpenAI-compatible API and prints the assistant reply. Reads the API key from a file so it never enters argv or env.
* `msg-util.py` - the only sanctioned path for creating or mutating the messages JSON (init / append / validate). Use this rather than ad-hoc jq or sed.
* `relay-driver.py` - the corpus-relay eval instrument: runs budgeted, fresh-history Q&A sessions against a guest model over a read-only corpus, appending its own READ/LIST/GREP relay protocol block to the caller's system prompt (callers supply navigation doctrine only) and servicing exactly what it appended. Per-question sessions, answers, and a `stats.csv` with real token totals (via `post-openai.py`'s `USAGE_STATS_FILE` side channel) land under `--output-dir`. Sketch: `relay-driver.py --corpus-root kb-root --system-prompt scout.md --questions-file questions.md --output-dir eval-out --env-file guest.env` (single questions via `--question` or `--question-number N`).
* `tests/` - fixtures for the above scripts.

### Review Mode

* mad-review-referee.md - runs multi-agent debate review process
* commands
  * mad-review.md - initiates a review process. you must provide:
    * a review name — a bare slug naming the output folder and documents; review mode has no topic library, so the invoker's charter carries the methodology
    * a seat roster (`SEATS=`) — comma-separated subset of `fable`, `opus`, `sonnet`, `haiku`, `guest`; at most one of each, at least two. No default: a roster-less invocation is refused. `opus,sonnet` is a reasonable pick for most review jobs. A `guest` seat additionally requires `ENV_FILE=` (path to the guest model's env file)
    * \[optional\] a requirements/constraints doc (e.g. coding invariants, math invariants, etc.)
    * a review target (path to document, file, or hierarchy)
* process
  * /mad-review \[your specifics\]
  * referee launches agents and reviews/debate/resolution occurs
  * output is a directory `./mad-review/<review-name>/` of documents containing
    * summary doc - burndown list, unresolved conflicts, resolved conflicts
    * audit trail of debate process and outcomes
    * all artifacts, temporary or otherwise, are produced under the review work directory
  * round cap: 5
  * examples 
    * ```/mad-review src-review SEATS=opus,sonnet,guest ENV_FILE=~/.config/guest.env CONSTRAINTS=CONVENTIONS.md TARGET=src/ **IGNORE
    `src/third_party`**```

### Design Mode

* mad-design-referee.md - runs multi-agent debate design process
* commands
  * mad-design.md - initiates a design process. you must provide:
    * a topic from .claude/agents/mad/design-topics/
      * architecture.md
      * math-derivation.md
      * ml-engineering.md (more topics can be added: software-design, hardware-design, etc.)
    * a seat roster (`SEATS=`) — same contract as review mode: subset of `fable`, `opus`, `sonnet`, `haiku`, `guest`, at most one of each, at least two, no default; `guest` requires `ENV_FILE=`
    * \[optional\] a requirements/constraints doc
    * a problem statement: either (a) a path to an existing brief defining the open problem, or (b) an empty/not-yet-created output location — in case (b) the referee elicits the brief from the user via interactive dialogue before dispatching participants
* process
  * /mad-design \[your specifics\]
  * referee launches agents and proposals/debate/convergence occurs
  * output is a directory `./mad-design/<design-name>/` of documents containing
    * SOLUTION.md - the constructed solution with full provenance (which participant proposed what, where it survived/failed), or a convergent under-determination diagnosis, or preserved candidates if unresolved
    * audit trail of debate process and outcomes
    * all artifacts, temporary or otherwise, are produced under the design work directory
  * round cap: 10 (construction is iterative; converging two independently-built proposals takes longer than retiring a list of flaws)
  * convergence criteria
    * **algebraic**: all participants reach the same closed-form result modulo trivial reformulation
    * **multi-path**: participants reach the same numerical answer via demonstrably independent paths (treated as strong-positive; multiple independent constructions reaching the same answer is mutual reinforcement)
    * **under-determined**: all participants converge on the same diagnosis of why the problem cannot be closed from the supplied axioms, identifying the specific missing axiom/principle/input
    * **unresolved**: candidates preserved for human arbitration if no convergence within round cap
  * examples
    * ```/mad-design math-derivation SEATS=fable,opus,sonnet CONSTRAINTS=SPEC.md TARGET=mad-design/my-derivation/```

## Guest Liaison

`guest-liaison.md` relays a multi-turn conversation between you and an external "guest" model accessed via an OpenAI-compatible API. Distinct from `mad-guest-liaison.md`, which is the MAD-process-only variant invoked by referees inside review/design debates. Both share `liaison_tools/`.

Properties of the relay:
* **Verbatim relay** of the system prompt and each user message — no summarizing, paraphrasing, or topic-tailoring. Verified post-write by `diff` against the source; the liaison aborts before sending if the diff is non-empty.
* **Secrets containment** — the API key lives in a file read directly by `post-openai.py`. The liaison treats the file path as opaque and is forbidden from reading the contents.
* **Audit-permanent session log** — every turn (system, user, agent) is appended to `guest-session/<topic>/messages.json` and never deleted.
* **File and tool-call services** — when the guest model asks for a file's contents or emits a tool call, the liaison reads the file (using a fixed `Here is the content of <path>:` frame) or returns a "not available in this environment" stub, then re-invokes the model.

### Slash commands

* `/guest-start <topic>` — initiate (or resume) a session: onboarding, persist session state, send the first user message, return the guest's reply.
* `/guest <message>` — append `<message>` as the next user turn in the active session and return the guest's reply.
* `/guest-end` — clear the active-topic pointer (`guest-session/active-topic.txt`). The session directory itself is preserved as audit history.

### `/guest-start` arguments

`/guest-start` takes one argument: the **topic slug**, which names the session directory under `guest-session/`. It must contain no path separators, leading dots, or whitespace.

Everything else is collected interactively after the command starts:

* `API_BASE_URL`, `API_KEY_FILE`, `MODEL` — the connection parameters. `API_BASE_URL` is the **API root**, not the chat-completions endpoint — `post-openai.py` appends `/chat/completions` itself. (Including `/chat/completions` in the URL produces a doubled path and a 404.)
* **System prompt source** *(new session only)* — either a path to an agent definition under `.claude/agents/` whose body (frontmatter stripped) is sent verbatim as the guest model's system prompt, or, if you decline to pick one, the literal default `You are a helpful assistant.` The agent body is not a Claude-only artifact — any agent body that reads as a coherent instruction set works (e.g. `applied-mathematician`, `architect`, `tech-writer-reviewer`).
* **Initial message** *(new session only)* — the first prompt sent to the guest model.

The collected connection parameters are persisted to `guest-session/<topic>/params.env` so `/guest` can reuse them on later turns.

### API key file format (the file at `API_KEY_FILE`)

The file contains only the API key. Its entire content, with leading and trailing whitespace trimmed, is the key; the key must contain no internal whitespace.

The liaison never reads this file. `post-openai.py` reads the key directly — the key does not appear in argv, env, transcripts, or any tool output the liaison sees.

### Session directory layout

```
guest-session/
  active-topic.txt              # one line: the currently active topic slug
  <topic>/
    messages.json               # full conversation (system + user + agent turns); permanent audit
    params.env                  # API_BASE_URL, MODEL, API_KEY_FILE, SYSTEM_PROMPT_AGENT
    tmp/                        # mktemp scratch space; safe to leave between turns
```

`guest-session/` is in `.gitignore` so audit logs and `params.env` (which references local API key file paths) do not leak into commits.

### Resuming and switching topics

Re-running `/guest-start` with an existing topic slug offers to resume — the prior `messages.json` is preserved and new turns continue from it. To switch active sessions without deleting either, run `/guest-end` then `/guest-start` with the new topic; both session directories survive.

### Example

```
/guest-start solar-system
/guest how long does light take to get from the sun to the earth?
/guest now compute the same for Proxima Centauri.
/guest-end
```

## Knowledge Base Agent Set

Agents and portable tooling for building, navigating, and maintaining a knowledge base — a navigable, verbatim Markdown distillation of a canonical corpus with a queryable claim-graph metadata spine. All three kb-* definitions are project-portable: per-project facts live in the consuming project's `kb-root/CLAUDE.md`, not in the definitions. All three are **generated** from their templates.

A build derives the KB from the LaTeX corpus, and the corpus is the only thing that exists at that point. Once you
have edited the KB by hand — a leaf's prose, a claim minted or wired — that work exists nowhere else, and a fresh
build over the same `kb-root/` would overwrite it. Point a fresh build at a KB you have been working in and you lose
the work; continue an interrupted one and you do not.

* Using/navigating a knowledge base
  * kb-docent.md
  * commands/ - custom slash commands
    * kb-start.md (/kb-start)
    * kb-next.md  (/kb-next)

* Building/Modifying Knowledge Base
  * commands/ - custom slash commands
    * kb-build.md (/kb-build) - prints the command line that starts a build, and stops
  * kb_tools/kb_driver - the builder itself: walks a static step table, dispatches each row to the seat it names, and exits at a barrier rather than asking
  * kb-claim-scorer.md - grades written derivations; returns judgments and writes nothing
  * kb-maintainer.md - the write side: leaf edits, claim-graph wiring, refresh→verify loop

### KB toolchain

`kb_tools/` is the stdlib-only, zero-config Python toolchain the kb agents drive — see [kb_tools/CONVENTIONS.md](kb_tools/CONVENTIONS.md) for the full picture. An install delivers it to `.claude/agents/kb_tools/`, which is the path the definitions and the runner snippets name. A consuming project:

1. Installs `.claude/agents` as in [Install](#install) and keeps its KB in `kb-root/` at the repo root — the tools self-anchor by walking up from the cwd to `.git` and requiring `kb-root/` beside it.
2. Installs the runner targets once, from the project root: `PYTHONPATH=.claude/agents python3 -m kb_tools.kb_util install-targets` — this adds a single non-fatal include line to the project's justfile or Makefile.
3. Uses `just`/`make` `kb-verify`, `kb-refresh`, and `kb-stats` from then on.

## Third Party Acknowledgements

The deployed runtime (`agents/`, `commands/`) is stdlib-only Python and shell with no third-party dependencies. `pytest`, `black`, and `isort` are dev/test-only tools, installed into a local `.venv` by the `just test` and `just format-python` recipes.
