# TOPIC: Architecture Design

## Domain

Software architecture construction for difficult systems: package and module structure, abstraction boundaries, layering, coupling, interface contracts, data flow, state ownership, and failure-mode handling for problems that do not have a standard, well-trodden solution. The provided problem statement, requirements, and any invariant document are the exclusive source of constraint truth — references to "the way framework X does it" or "the canonical pattern for Y" are not load-bearing on their own; they must be justified against the actual problem at hand.

This topic is for systems where the structural decisions are non-obvious — novel data flows, cross-cutting concerns, hard performance/safety/portability constraints, or hybrid requirements that resist any single off-the-shelf pattern. It is not for greenfield CRUD-shaped problems where the answer is "the usual thing."

## Constructive Architecture, Adversarially Defended

**Structure is cost, capability is value.** Every structural decision — a package boundary, an abstraction layer, an interface contract, a design pattern — is overhead that must be maintained, understood, and navigated by every engineer who touches the system. The optimal architecture delivers the required clarity, extensibility, and local reasoning with the minimum structural complexity that fully achieves it.

Independently construct an end-to-end architecture proposal that resolves the stated problem. Build from the supplied requirements, invariants, and constraints; do not import patterns as load-bearing without justifying them against the actual problem. The proposal must include, at minimum:

- **Module / package decomposition** with the responsibility of each unit stated in one sentence
- **Key abstractions and interface contracts** between units — what each interface carries, what it deliberately does not, and what behavioral guarantees it makes
- **Layering and dependency direction** — which units depend on which, and why the direction is consistent
- **Data flow and state ownership** — where each piece of state lives, who is allowed to mutate it, and how it crosses boundaries
- **Failure-mode handling** — where errors are produced, where they are handled, and where they cross system boundaries
- **Cost / capability accounting** for each non-trivial structural decision: what hard problem it solves, what cheaper structure was considered and rejected, and why the cheaper structure fails

Mark any decision that depends on a requirement the problem statement asserts but does not fully specify (e.g., implied performance bounds, unstated portability targets, undefined consistency guarantees). Such decisions are conditional and surface as candidates for under-determination diagnosis.

The artifact under debate is the **open architecture problem**, not an existing design. Phase 1 produces a candidate architecture, not a critique.

In debate rounds, attack the opposing architectures as presented in the alignment map — identify unjustified abstractions, hidden coupling, abstraction inversions, leaky contracts, pattern-for-its-own-sake, premature configurability, structural complexity that does not pay rent, separation-of-concerns violations disguised as helpers, and decisions that close doors the requirements say must remain open. Defend your own architecture against the same attacks by naming what hard problem each structure solves and why a simpler structure fails to solve it.

Concede when the attack identifies overhead with no offsetting capability. Refine when the attack identifies a fixable seam without invalidating the load-bearing decisions. Replace your load-bearing structural decision when the attack invalidates it. Do not concede from exhaustion — if you cannot defend a decision but are not convinced by an opposing architecture, say so explicitly. That is a valid round outcome and signals an under-determination diagnosis is forming.

## Convergence Criteria

Architecture problems usually admit a small family of valid solutions rather than a single right answer. Convergence here is about whether participants agree on the structure of the solution space and on a specific point within it — or, when they do not, on a clear diagnosis of why.

A successful debate terminates in one of these states:

- **(a) Structural convergence**: all active participants converge on the same module decomposition, the same key interface contracts, the same layering and dependency direction, and the same data/state ownership boundaries — modulo trivial naming or grouping differences. The architecture is the deliverable.
- **(b) Multi-path convergence**: participants reach the same structural decisions via demonstrably independent reasoning paths (different driving constraints invoked, different rejected alternatives, but the same final structure). Both reasoning paths are preserved in the deliverable as mutually reinforcing confirmations of the same architecture. This is a strong-positive outcome.
- **(c) Tradeoff under-determination**: all active participants converge on the same characterization of the design space — same Pareto frontier, same set of viable architectures, same axes of tradeoff (e.g., latency vs. flexibility, simplicity vs. extensibility) — but cannot select a single point on the frontier from the supplied requirements. The frontier characterization and the specific missing requirement that would close the choice are the deliverable.
- **(d) Constraint under-determination**: all active participants converge on the same diagnosis that the problem cannot be closed from the supplied requirements at all, and identify the specific missing constraint, invariant, capability target, or stakeholder decision required. The diagnostic statement is the deliverable.
- **(e) Unresolved divergence**: participants remain at distinct architectures making different structural commitments after the round cap. All candidates are documented with their cost/capability accounting and preserved for human arbitration.

States (a), (b), (c), and (d) all retire the debate as productive. State (e) escalates to human arbitration with the candidate space preserved.

## What This Topic Is Not

- This is not architecture *review* of an existing system. If the artifact already exists and the goal is to find flaws, use `mad-review-topics/architecture.md` with the review referee.
- This is not implementation-level design. Findings about magic strings, error-handling style, or local DRY belong in code review, not here.
- This is not a search for an elegant pattern. A pattern is justified only if it removes a harder complexity than it introduces.
- This is not greenfield boilerplate. Reserve this process for problems where the structural decisions are genuinely non-obvious and the cost of getting them wrong is high.

## How to Report Proposals and Findings

In debate rounds, attacks and defenses must be grounded in specific structural evidence (a named interface, a named dependency, a named pattern), not general preferences. Tradeoff disagreements should be framed as "this choice optimizes axis X at cost to axis Y; the requirements weigh X higher" — not as right/wrong.

For artifact-specific invariants a requirements document may be provided. When provided it is additive and authoritative — the proposed architecture must conform to every invariant it states.
