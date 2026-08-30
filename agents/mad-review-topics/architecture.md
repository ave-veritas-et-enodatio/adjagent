# TOPIC: Architecture Review

## Domain

Software architecture review: package and module structure, abstraction boundaries, layering, coupling, interface contracts, and the cost/capability tradeoff at the design level.

## Adversarial Review of Architectural Decisions

**Structure is cost, capability is value.** Every structural decision — a package boundary, an abstraction layer, an interface contract, a design pattern — is overhead that must be maintained, understood, and navigated by every engineer who touches the system. Complexity compounds this: an indirection that adds a hop costs more than the structural clarity it might provide. The optimal architecture delivers the required clarity, extensibility, and local reasoning with the minimum structural complexity that fully achieves it.

**Apply this lens across every review dimension**: does the structural complexity serve the capability, or does it exist for its own sake? Structure that earns its place — by removing harder complexity, hiding a volatile decision, or making a hard problem locally tractable — is justified. Structure that exists to be elegant, to anticipate hypothetical future needs, or because a pattern was fashionable is a finding.

Review the artifact for architectural soundness. This is a review of *where things live and how they talk to each other*, not of implementation details. Findings should be grounded in specific structural evidence, not general preferences.

### What to look for

**Package and module cohesion**
- Do packages group things that change together and for the same reason?
- Are package names accurate descriptions of what they contain, or have they become catch-alls?
- Is any package doing two or more unrelated jobs? Could it be split without introducing unwanted coupling?

**Abstraction level and justification**
- Is each abstraction layer earning its cost? An abstraction is justified when it removes duplication, hides a volatile decision, or makes a hard problem locally tractable. An abstraction is unjustified when it adds indirection without removing complexity.
- Are abstractions at a consistent level? Mixing high-level orchestration with low-level implementation detail in the same layer is a sign of level mismatch.
- Are there abstraction inversions — where a lower layer depends on or is shaped by the needs of a specific higher-layer caller?

**Layering and dependency direction**
- Do dependencies flow in a consistent direction (e.g., high-level → low-level, not bidirectionally)?
- Does any layer have knowledge of another layer's internals that it should not need?
- Is any layer pre-digesting data in a way only useful to a specific downstream consumer? That is a separation-of-concerns violation disguised as a helper.

**Interface contracts**
- Are the contracts between layers explicit and stable, or implicit and fragile?
- Do interfaces carry only what callers need, or do they expose implementation details?
- Is the contract narrow enough that implementations can be swapped without changing callers?
- Are there hidden contracts — behavioral assumptions that callers depend on but the interface does not declare?

**Coupling**
- Are components that should be independent actually independent? Could you change one without touching the other?
- Is there shared mutable state that creates implicit coupling between otherwise-separate components?
- Are there features that belong to one layer implemented in another because it was convenient at the time?

**Design patterns and their cost**
- Are design patterns (registries, factories, builders, strategies) earning their structural complexity? Each pattern adds indirection — that indirection is only justified if it removes a harder complexity elsewhere.
- Are patterns applied consistently, or do similar problems get solved differently in different parts of the codebase?
- Is there pattern-for-its-own-sake? A pattern applied where a direct call would be clearer is an unjustified abstraction cost.

**Extensibility vs over-engineering**
- Is the architecture data-driven where appropriate (configuration drives behavior) or code-driven where it should be data-driven?
- Conversely, is anything made configurable that will never vary in practice, adding complexity without real flexibility?
- Does the structure make the most common extension paths easy, or does it accidentally make them hard?

### What this review is NOT

- Do not flag implementation-quality issues (magic strings, error handling, DRY within a file). Those belong in a general code review.
- Do not flag style or naming issues unless they indicate a structural confusion.
- Do not flag an architectural pattern as wrong because you would have made a different choice. The question is whether the pattern is *unjustified given the actual complexity it manages*. Acknowledge that reasonable engineers disagree on tradeoffs — frame findings as tradeoff analysis, not verdicts.

### How to report findings

Architecture findings are almost always about tradeoffs, not clear-cut violations. Each finding must:
1. Identify the specific structural decision under scrutiny (package, interface, pattern, dependency)
2. Explain what capability the decision provides
3. Explain what structural cost it imposes
4. State whether the cost is justified by the capability — and why

Where a requirements document is provided, validate the artifact against any architectural invariants stated there.

For artifact-specific invariants a requirements document may be provided. When provided it is additive and authoritative.
