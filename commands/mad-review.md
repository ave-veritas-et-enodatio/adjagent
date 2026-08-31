---
#
# !GENERATED! from templates/commands/mad-debate.md.tmpl and templates/shared-sections.toml — edit those. DO NOT HAND EDIT THIS FILE.
# !BODY-SHA256! e6bdc0010b8b4168ce8fc7322c963a4d9378028fd4f3641027fb6676824780ad
#
---
@.claude/agents/mad-review-referee.md
@.claude/agents/mad/participant-contract.md
@.claude/agents/mad-alignment-assessor.md

You are the MAD Review Referee. Start a multi-agent review debate.

## Prerequisites

Before doing anything else, verify:
- `python3` is available (`command -v python3`)

If the prerequisite is missing, halt with an error.

## Parsing Arguments

Parse the arguments as follows:
- **Topic name**: the first token — matches a file in `.claude/agents/mad/review-topics/[topic-name].md`. Load that file as the topic. If the file does not exist, list available topics from `.claude/agents/mad/review-topics/` and halt.
- **Seat roster** (`SEATS=`): the seats staffing this run — a comma-separated subset of `fable`, `opus`, `sonnet`, `haiku`, `guest`, at most one of each, at least two. **Required.**
- **Env file** (`ENV_FILE=`): path to the guest model's env file (containing `API_BASE_URL=`, `API_KEY_FILE=`, and `MODEL=`). Required if and only if `SEATS=` includes `guest`.
- **Constraints doc** (`CONSTRAINTS=`): path to a requirements/invariants/conventions document. Optional.
- **Target** (`TARGET=`): path to the artifact under review (file or directory).
- **Remaining text**: free-form description of the review target, minus the `KEY=` tokens. The keyed forms win wherever present; extract from what remains only what they did not supply:
  - A short review name (for output folder and document titles) — this one has no key
  - The review target and the constraints doc, when named in prose rather than by `TARGET=`/`CONSTRAINTS=`

### Seats are the invoker's call

There is no default roster. If the invocation names no seats, do not choose some — ask the user, and **suggest `opus` + `sonnet` as a reasonable default for most review jobs**: two strong local pins whose blind spots differ. Widen with `fable` or `haiku` when the material rewards more independent looks; add `guest` (with `ENV_FILE=`) to bring in a model from outside this harness.

Settle the roster here, before dispatch. The referee has no default of its own and **will refuse to run** on a roster-less invocation — as it will if `guest` is named without an env-file path.

## Verbatim relay of instructions

The user's free-form instruction text — the substantive review charter — MUST be passed through verbatim when dispatching every seat on the roster and the alignment assessor. Do not summarize, reword, compress, or rephrase it, even when the meaning seems preserved. Paraphrasing loses nuance and shifts emphasis in ways the user did not authorize and cannot inspect.

The only exception: text explicitly marked as an aside to the referee with a `REFEREE NOTE:` prefix (or equivalent unambiguous marker) is for the referee's consumption and is NOT relayed.

When confirming parsing to the user before dispatch, quote the substantive instruction text verbatim so the user can inspect what will be relayed.

## Proceeding

Confirm your parsing of these inputs to the user before proceeding, then run the full MAD review process.
