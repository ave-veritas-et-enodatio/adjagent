# AVE Agent Set
Markdown definitions of various useful agents for AVE projects.
These are shared among various projects by cloning the repo to the agents/ subdirectory for your agent system to find.

## Coding Agent Set

* coordinator.md - will run multi-agent development process that includes analysis, planning, dispatch, review, process review
* architect.md - architecture review and design
* security-reviewer.md - security-specific reviewer
* generalist-coder.md
* tech-writer.md
* tech-writer-reviewer.md
* language-specific coders
  * go-coder.md
  * python-coder.md
* platform-specific coders
  * android-app-expert.md
  * ios-app-expert.md
  * linux-app-expert.md
  * macos-app-expert.md
  * web-app-expert.md
  * windows-app-expert.md

These coder/platform files are **generated** — do not edit them directly. Each is rendered from `coders/<name>.md.tmpl` plus the shared text in `coders/shared-sections.toml`, which is the single home of the sections they hold in common. Edit the template (agent-specific text) or the shared sections (common text), then run `python3 coders/gen-agents.py --generate`. Running the script with no arguments checks that every generated file still matches its template. A definition is generated only if `coders/<name>.md.tmpl` exists, and a file lacking the `# !GENERATED!` banner is never overwritten.

## Specialists
Single-purpose agents invoked directly or by the coordinator for non-coding work.
* prose-architect.md - rhythm and structural review of long-form prose
* marketing-comms-expert.md - messaging, positioning, copywriting, competitive framing
* biz-dev-strategist.md - business strategy, market analysis, GTM, monetization
* applied-mathematician.md - rigorous derivation, model construction, dimensional analysis, claim classification (identity / manifestation / consistency check / derived prediction). Takes given axioms at face value and derives consequences honestly. Use when working inside a formal system — established, novel, or mid-construction — and the task requires careful step-by-step reasoning rather than retrieval of textbook results.
* applied-mathematician-strict.md - same role with added foundational-gap discipline: when the stated postulate set is incomplete, halt and surface the gap (or stipulate a closure explicitly) rather than silently filling with textbook defaults. Use for smaller / less-priored models that tend to silently interpolate when axioms are underspecified. **Avoid for frontier models** — the same clauses that protect a small model from gap-filling tend to over-constrain a frontier model that already does the right thing on its own (see "Variants and platform compatibility" below).

### Variants and platform compatibility
A few agents in this set ship as a base + a defensive variant: `applied-mathematician.md` and `applied-mathematician-strict.md` are the current example. The variant adds clauses targeted at specific failure modes observed in smaller / less-priored models; the base relies on the model's own discipline.

The two-variant pattern is a response to a real phenomenon: agent definitions tend to accumulate defensive language that's keyed to the *specific* model they were tested against. Defensive clauses that *protect* one model can *smother* another — same clause, opposite effect, no error event. (Example: probe data from 2026-04-29 showed Gemma 4 31B-it silently filling axiom gaps with textbook conventions, while Gemini 3.1 Pro spontaneously surfaced the same gaps. A "do not fill gaps" clause helps the first model and slows the second.)

The operating rule when porting an agent definition to a new model:
**strip first, observe, patch.** Run with the base variant and the new model on a known-shape probe set. Watch for failure modes; only then add defensive language targeted at the failure modes you actually observed. Heavy scaffolding hides the model's true tendencies; you can't engineer for failure modes you never see.

When adding defensive clauses, record *what tendency the clause was added to correct, against which model*. Without that record, future maintainers can't distinguish "still load-bearing" from "residue from a model we don't use anymore." Comment agent defs like code: not what the clause says, but why and against which observed behavior it was added.

## Multi-Agent Debate Agent Set
Uses Multi-Agent Debate Process.
Two modes share the same participants but use different referees and topic libraries:
* **Review mode** — adversarial assessment of an *existing* artifact (architecture, code, math, agent definitions).
* **Design mode** — constructive proposal for an *open problem* (derivations, software designs, hardware designs, other problem-solving).

### Shared Agents
* mad-participant-1.md - an independent participant (reviewer in review mode, proposer in design mode)
* mad-participant-2.md - another independent participant
* mad-guest-liaison.md - a liaison that can loop in an external model via API base url, key, and model name
* mad-alignment-assessor.md - only assesses alignment/disagreement among participants

### Liaison Tooling
`liaison-tools/` contains shell helpers used by both `mad-guest-liaison.md` (MAD process) and `guest-liaison.md` (general guest-model sessions). The liaison agents are the primary callers; MAD referees set `TMPDIR` for liaison invocations to keep `mktemp` output contained in the review/design directory.
* `post-openai.sh` - posts a message history to an OpenAI-compatible API and prints the assistant reply. Reads the API key from a file so it never enters argv or env.
* `msg-util.sh` - the only sanctioned path for creating or mutating the messages JSON (init / append). Use this rather than ad-hoc jq or sed.
* `extract-agent-body.sh` - extracts the body of an agent definition file (drops the frontmatter) for use as a system prompt.
* `tests/` - fixtures for the above scripts.

### Review Mode

* mad-review-referee.md - runs multi-agent debate review process
* commands
  * mad-review.md - initiates a review process. you must provide:
    * a topic from agents/mad-review-topics/
      * agent-definition.md 
      * architecture.md
      * general-code.md
      * math-derivation.md
      * sim-code.md
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
    * ```/mad-review TOPIC=.claude/agents/mad-review-topics/sim-code.md CONSTRAINTS=AVE-Core/LIVING-REFERENCE.md TARGET=AVE-Core/src/ave/```
    * ```/mad-review TOPIC=.claude/agents/mad-review-topics/general-code.md CONSTRAINTS=AGENTS.md TARGET=src/ **IGNORE
    `src/third_party`**```

### Design Mode

* mad-design-referee.md - runs multi-agent debate design process
* commands
  * mad-design.md - initiates a design process. you must provide:
    * a topic from agents/mad-design-topics/
      * ai-engineering.md
      * architecture.md
      * math-derivation.md (more topics can be added: software-design, hardware-design, etc.)
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
    * ```/mad-design TOPIC=.claude/agents/mad-design-topics/math-derivation.md CONSTRAINTS=AVE-Core/LIVING-REFERENCE.md TARGET=mad-design/inter-alpha-resonance/```

## Guest Liaison

`guest-liaison.md` relays a multi-turn conversation between you and an external "guest" model accessed via an OpenAI-compatible API. Distinct from `mad-guest-liaison.md`, which is the MAD-process-only variant invoked by referees inside review/design debates. Both share `liaison-tools/`.

Properties of the relay:
* **Verbatim relay** of the system prompt and each user message — no summarizing, paraphrasing, or topic-tailoring. Verified post-write by `diff` against the source; the liaison aborts before sending if the diff is non-empty.
* **Secrets containment** — the API key lives in a file read directly by `post-openai.sh`. The liaison treats the file path as opaque and is forbidden from reading the contents.
* **Audit-permanent session log** — every turn (system, user, agent) is appended to `guest-session/<topic>/messages.json` and never deleted.
* **File and tool-call services** — when the guest model asks for a file's contents or emits a tool call, the liaison reads the file (using a fixed `Here is the content of <path>:` frame) or returns a "not available in this environment" stub, then re-invokes the model.

### Slash commands

* `/guest-start <topic>` — initiate (or resume) a session: onboarding, persist session state, send the first user message, return the guest's reply.
* `/guest <message>` — append `<message>` as the next user turn in the active session and return the guest's reply.
* `/guest-end` — clear the active-topic pointer (`guest-session/active-topic.txt`). The session directory itself is preserved as audit history.

### `/guest-start` arguments

`/guest-start` takes one argument: the **topic slug**, which names the session directory under `guest-session/`. It must contain no path separators, leading dots, or whitespace.

Everything else is collected interactively after the command starts:

* `API_BASE_URL`, `API_KEY_FILE`, `MODEL` — the connection parameters. `API_BASE_URL` is the **API root**, not the chat-completions endpoint — `post-openai.sh` appends `/chat/completions` itself. (Including `/chat/completions` in the URL produces a doubled path and a 404.)
* **System prompt source** *(new session only)* — either a path to an agent definition under `.claude/agents/` whose body (frontmatter stripped via `extract-agent-body.sh`) is sent verbatim as the guest model's system prompt, or, if you decline to pick one, the literal default `You are a helpful assistant.` The agent body is not a Claude-only artifact — any agent body that reads as a coherent instruction set works (e.g. `applied-mathematician`, `architect`, `tech-writer-reviewer`).
* **Initial message** *(new session only)* — the first prompt sent to the guest model.

The collected connection parameters are persisted to `guest-session/<topic>/params.env` so `/guest` can reuse them on later turns.

### API key file format (the file at `API_KEY_FILE`)

The file contains only the API key. Its entire content, with leading and trailing whitespace trimmed, is the key; the key must contain no internal whitespace.

The liaison never reads this file. `post-openai.sh` reads the key directly — the key does not appear in argv, env, transcripts, or any tool output the liaison sees.

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

* Using/navigating knowledge base - see manuscript/ave-kb/README.md in any AVE-\* repo
  * kb-docent.md
  * commands/ - custom slash commands
    * kb-start.md (/kb-start)
    * kb-next.md  (/kb-next)

* Building/Modifying Knowledge Base
  * kb-coordinator - runs knowledge base creation/update multi-agent process
  * kb-content-distiller.md
  * kb-accuracy-reviewer.md
  * kb-taxonomy-architect.md
  * kb-structure-reviewer.md
  * kb-latex-specialist.md
