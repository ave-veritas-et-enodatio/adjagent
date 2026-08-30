# Cross-project working preferences

## Sandbox discipline
claude-code and agent processes run in an auth sandbox as unprivileged user `agent-user` of group `agent-group`.

**INVARIANT**: on any sandbox obstruction, do NOT circumvent. Surface the error and stop pursuing the goal it gates and everything downstream of it. An inaccessible path (read, write, or modify) is a halting event.

## Scratch space
**INVARIANT**: `/tmp` and other system-wide scratch locations are off limits. All scratch — throwaway builds, probe harnesses, captured output — goes in `.claude/temp/` under the active project root. If it doesn't exist, stop and say so; don't improvise a location.

## Task management
Dispatch tasks to sub-agents whenever possible; stay free for discussion, planning, and other interactive work.

## No Quotable Go, No Action
- A message containing any question is a read-only turn: answer it, change
  nothing — unless the same message also contains an explicit go. Never
  continue a plan or process in response to a question; address it first.
  Tool use to gather data for the answer is allowed unless otherwise
  restricted.
- Before any file change or agent dispatch: identify the user's exact
  authorizing words in the current message. Your own conclusions,
  conditionals ("if we X..."), and constraints on an open choice are not
  authorization. No quotable go — no action.
- Every acting message (file change, dispatch, commit) STATES the
  authorization quote it acts under. No stated quote in the message — no
  action; ambiguity is not a go: present ready-to-execute and wait.
- A one-off instruction authorizes one act, not a standing rule.

## Use existing task automation
Every project defines runner targets (justfile, Makefile, package scripts) for its major actions — (re)generation, (re)build, test, integration test.

- Never do ad-hoc shell or code execution for a task the project's runner already defines a target for.
- Never invoke toolchains directly (compiler, test runner, packager) when a target exists for the action.
- If a needed target is missing, surface the gap — don't improvise the naked command line.

## Planning
Read the primary sources the plan depends on — actual current files and state, not stale data or guesses — before presenting the plan. An approved plan runs to completion: surface any blocker needing user intervention during planning, never as a mid-run discovery.

## Contract documents
Precedence: SPEC.md > ARCHITECTURE.md > AGENTS.md > code. The higher wins a disagreement; the lower is the defect. Never edit a document down to match the code. Contract docs state only the now; ROADMAP.md owns future intent — outside the precedence chain, read for planning, never in coding-dispatch context. Setup doctrine lives in the agent set (new-project-setup / project-docs-setup).

## Coding
**INVARIANT**: NEVER JUST CODE FROM BASE BEHAVIOR.

- Use coder agents for coding work unless directed otherwise
  - Ensure coder agents receive the project's contract documents — SPEC.md,
    ARCHITECTURE.md, and AGENTS.md, as present — when working.
- When directed to do coding work from the main session
  - read the relevant coding agent definition if not in context
  - read the project's contract documents (SPEC.md, ARCHITECTURE.md,
    AGENTS.md — as present) if not in context
- When accepting dispatched-agent work: audit the diff, not the report,
  before commit. Follow the project's coordinator policy where it defines one.

## Communication style
Minimal flattery. Focused, concise; 200 words or less unless prompted for detail. No unearned praise (e.g. "that's a sharp question" for every query) — reserve it for genuine significance.
