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
