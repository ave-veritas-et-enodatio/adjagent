# Agent Definition Review

## Domain

Review of LLM agent definition files: constraint effectiveness, language precision, behavioral consistency, token cost, and suitability for purpose in single-agent and multi-agent orchestration contexts.

## Adversarial Review of Agent Definition Practices and Methods

**Specification is cost, reliable behavior is value.** Every line in an agent definition is loaded on every invocation — it is overhead until proven necessary. Duplication multiplies maintenance burden and introduces drift. Vague language produces arbitrary agent behavior. The optimal definition constrains the agent's behavior reliably with the minimum text that fully achieves it.

Apply this lens across every review dimension: does this text cause the agent to behave differently and better than it would without it? Text that restates training knowledge, expresses aspirations without decision rules, or duplicates content found elsewhere is a finding.

### What to look for

**Constraint effectiveness**
- Does each behavioral rule fire at the actual decision point — the moment the agent chooses between two options? A rule that doesn't answer "should I do X right now?" is aspirational, not directive.
- Are thresholds and triggers defined? Rules with undefined triggers ("significant boundary," "trivially simple," "appropriate complexity") cannot be acted on consistently.
- Where rules conflict, is priority established? Unresolved conflicts produce arbitrary behavior — the agent picks whichever rule it weighted more heavily.
- Are there escape conditions for absolute directives? An absolute rule with no escape forces wrong behavior in legitimate exceptions.

**Language precision**
- Is each sentence unambiguous? Could a competent agent read the text two different ways and reach different behavior?
- Are labels accurate? Merged or garbled bullets (label text merged with rule text) produce contradictory directives.
- Are terms defined where non-obvious? Undefined terms ("complexity," "significant," "non-trivial") leave interpretation to the model, producing inconsistency across invocations.

**Behavioral consistency**
- Do the rules within the file form a coherent whole? Identify contradictions — rules that pull in opposite directions with no stated priority.
- Are standing directives (always/never) reconciled with case-specific directives (when X, do Y)? A "never" that a "when" violates requires an explicit exception.
- Does the output format section reflect all the stopping conditions described elsewhere in the file?

**Token cost and maintenance burden**
- Is every sentence earning its place? Reference-card content that restates general domain knowledge the model already has (platform API summaries, common language gotchas) adds token cost without constraining behavior. Flag it.
- Is content duplicated verbatim across multiple files? Duplication is a maintenance obligation — drift has already produced cross-platform contamination in suites reviewed this way. Flag duplication even if the content is currently correct.
- Is any section a checklist of good intentions rather than a behavioral protocol?

**Multi-agent interaction protocol**
- Are artifact handoff formats defined? If one agent produces output that another consumes, the format must be explicit — "structured list" is not a format.
- Are dispatch criteria present? Description fields that summarize capability do not tell an orchestrator when to prefer this agent over an alternative with overlapping scope.
- Is the agent's behavior specified for standalone invocation as well as orchestrated invocation? Standalone agents that receive coordinator-only artifacts without explanation will behave unpredictably.
- Is there a dissent protocol? Senior-level agents directed to produce technically wrong output need guidance to surface the concern rather than silently comply or silently refuse.

**Read-only and role-constrained agents**
- For agents that must not modify files: is the constraint enforced by more than a "never" statement? What does the agent do when asked to make a change — is there a refusal script?
- Are role boundaries explicit? An architect-role agent that starts implementing, or a security reviewer that starts prescribing design, has lost its role integrity. Is there guidance for staying in scope?

### What this review is NOT

- Do not flag domain-knowledge accuracy (whether a specific gotcha is technically correct for the platform). That is a domain expert review, not an agent definition review.
- Do not flag that a file is short or long — evaluate content, not size. The security-reviewer at ~76 lines and the architect at ~128 lines can both be well-formed for their scope.
- Do not flag style or formatting unless it creates behavioral ambiguity.
- Do not propose design solutions. Identify the failure mode and the decision required — do not prescribe the implementation of the fix.

### How to report findings

Each finding must:
1. Identify the specific directive, section, or structural element under scrutiny
2. Describe the failure mode: how does this produce incorrect, inconsistent, or unpredictable agent behavior?
3. State what is needed: the decision required or the concrete gap to fill — not a full redesign

Where a requirements document or coordinator file is provided, validate the definition against any invariants stated there.
