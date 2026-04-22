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
  * windows-app-expert.md

## Math & Code Review Agent Set
Uses Multi-Agent Debate Process

* mad-referee.md - runs multi-agent debate review process
* mad-reviewer-rvw1.md - one of two independent reviewers
* mad-reviewer-rvw2.md - two of two independent reviewers
* mad-alignment-assessor.md - only assesses alignment/disagreement between reviewers
* commands
  * mad-review.md - initiates a review process. you must provide:
    * a topic (see agents/mad-topics/)
    * \[optional\] a requirements/constraints doc (e.g. coding invariants, math invariants, etc.)
    * a review target (path to document or hierarchy)
* process
  * /mad-review \[your specifics\]
  * referee launches agents and reviews/debate/resolution occurs
  * output is a directory of documents containing
    * summary doc - burndown list, unresolved conflicts, resolved conflicts
    * audit trail of debate process and outcomes 
  * examples 
    * ```/mad-review TOPIC=.claude/agents/mad-topics/sim-code.md CONSTRAINTS=AVE-Core/LIVING-REFERENCE.md TARGET=AVE-Core/src/ave/```
    * ```/mad-review TOPIC=.claude/agents/mad-topics/general-code.md CONSTRAINTS=AGENTS.md TARGET=src/ **IGNORE
    `src/third_party`**```

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
