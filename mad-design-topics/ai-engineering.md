# TOPIC: AI Engineering

## Domain

Complex problem solving, technique research, and exploration in machine learning systems — particularly inference, training, sampling, attention, optimization, quantization, distillation, and novel inference-time mechanics. The space includes designing new techniques, adapting published ones to non-standard regimes, diagnosing model behavior, and exploring open questions where the answer is not yet known and may require empirical validation to resolve.

This topic is not for system architecture (use `architecture.md`), formal derivations from axioms (use `math-derivation.md`), or implementation-level coding tasks.

Prior work — published papers, reference implementations, established techniques — is legitimate context but is not load-bearing on its own. A proposal that says "this is what paper X does" without explaining why the mechanism applies to the current problem is not a proposal. Cite prior work to ground reasoning, not to substitute for it.

## Constructive Technique, Adversarially Defended

**Each proposal must produce three indivisible elements: a *mechanism* (how the technique works), a *justification* (why it should work, grounded in model or system behavior), and a *measurement* (how we will know whether it does).**

Independently construct an end-to-end proposal that addresses the stated problem. The proposal must include, at minimum:

- **Mechanism** — the specific technique or modification, described precisely enough to implement: what computation changes, where in the pipeline it sits, what new state or weights it introduces, what it replaces
- **Justification** — why this mechanism should produce the desired behavior. Ground in known model dynamics (residual stream behavior, attention patterns, gradient flow, distribution shift, etc.), prior empirical results from analogous regimes, or first-principles reasoning. If the justification is "it worked in paper X," explain why the conditions of paper X transfer to the current problem
- **Predicted observable behavior** — what should be measurable if the mechanism is working as hypothesized. Concrete predictions ("logprob agreement within 0.01 on held-out prompts," "attention entropy drops by ≥30% in layers > N," "perplexity matches baseline within 5%") are stronger than vague ones ("output quality should improve")
- **Measurement plan** — how the predicted behavior will be tested. Identify the dataset/prompt set, the metric, the baseline to compare against, and the threshold that would falsify the hypothesis. If the measurement requires infrastructure that does not yet exist, name it
- **Failure modes** — what specifically would go wrong if the mechanism is incorrect, and whether each failure mode would be silent (model produces wrong-but-plausible output) or loud (NaN, exception, obvious garbage). Identify every silent failure mode explicitly — these are the highest-cost category to debug
- **Cost accounting** — compute (FLOPs, wall time), memory (VRAM, RAM, persistent storage), and complexity (lines changed, new abstractions introduced, ongoing maintenance burden). Compare to the cheapest baseline that achieves a comparable result and explain why the additional cost is justified

Mark any step that depends on an empirical outcome the proposal does not yet have data for. Such steps are conditional and surface as candidates for empirical under-determination resolution.

The artifact under debate is the **open problem**, not an existing technique. Phase 1 produces a candidate technique, not a critique.

In debate rounds, attack the counterpart's proposal — identify mechanism descriptions that are too vague to implement, justifications that smuggle the conclusion as a premise, predictions that cannot be measured or that would be true regardless of whether the mechanism worked, baselines that are not actually comparable, missing failure-mode analysis (especially silent ones), cost accounting that ignores ongoing maintenance or omits a cheaper alternative, hidden assumptions about model size / architecture / regime that the problem does not specify, and citations to prior work whose conditions do not transfer to the current problem. Defend your own proposal by sharpening the mechanism, grounding the justification more concretely, tightening the prediction, or proposing a more discriminating measurement.

Concede when the attack identifies a hole the proposal cannot fill. Refine when the attack identifies a fixable seam without invalidating the mechanism. Replace your load-bearing technique when the attack invalidates it. Do not concede from exhaustion — if you cannot defend the proposal but are not convinced of the counterpart's, say so explicitly. That is a valid round outcome and signals an under-determination diagnosis is forming.

## Convergence Criteria

The choice between viable techniques frequently depends on empirical outcomes that reasoning cannot predetermine. Convergence here is about whether participants agree on the proposed technique and on a path to validate it — or, when they do not, on a clear diagnosis of why.

A successful debate terminates in one of these states:

- **(a) Mechanism convergence**: all active participants converge on the same proposed technique, the same hypothesized mechanism, the same predicted observable behavior, and the same measurement plan — modulo trivial reformulation. The technique specification is the deliverable.
- **(b) Multi-path convergence**: participants reach the same technique via demonstrably independent justification paths (different mechanism rationales, different prior-work invocations, but the same final specification and the same predicted observables). Both reasoning paths are preserved as mutually reinforcing confirmations of the same technique. This is a strong-positive outcome.
- **(c) Empirical under-determination**: all active participants converge on the same characterization of the candidate space — same set of viable techniques, same hypothesized mechanism for each, same predicted distinguishing measurements — but cannot select among them without running the experiment that distinguishes them. The candidate set, the distinguishing experiment, and the decision criterion are the deliverable. **Running the experiment IS the resolution** — this outcome is productive, not a failure.
- **(d) Theoretical under-determination**: all active participants converge on the same diagnosis that the problem cannot be closed from the supplied context — missing baseline, missing prior measurement, missing model-behavior characterization, or missing constraint. The diagnostic statement is the deliverable.
- **(e) Unresolved divergence**: participants remain at distinct techniques making different mechanism-level claims after the round cap. All candidates are documented with their full triple (mechanism, justification, measurement) and preserved for human arbitration.

States (a), (b), (c), and (d) all retire the debate as productive. State (e) escalates to human arbitration with the candidate space preserved.

## Empirical Validation and Reference Values

Where the problem has a known reference value or measurement (e.g., a published baseline, a logprob match against a reference implementation, a benchmark score), participants may compute their proposal's prediction and compare. A proposal that disagrees with the reference is not automatically wrong — the reference may itself be specific to a regime that does not transfer — but the disagreement must be acknowledged and explained.

A reference value MUST NOT be used as the load-bearing justification for a mechanism. It may be used at the end for validation, and as evidence that the regime is comparable, but the mechanism must stand on its own justification.

When the resolution path is "run the experiment," the experiment specification must be concrete enough that a human or downstream agent can execute it without reinterpreting the proposal: specify the prompt set or dataset, the baseline configuration, the metric, the threshold for accepting/rejecting the hypothesis, and the failure modes that would invalidate the experiment itself.

## What This Topic Is Not

- This is not architecture design. If the problem is "how should the system be structured," use `architecture.md`.
- This is not formal derivation. If the problem requires building a closed-form result from axioms, use `math-derivation.md`.
- This is not implementation. Findings about specific code patterns, error handling, or local DRY belong in code review.
- This is not a place to reach for novelty. If a well-established technique solves the problem, the strongest proposal is "use the established technique, here is why its conditions transfer." Novelty is a cost, not a virtue.

## How to Report Proposals and Findings

In debate rounds, attacks and defenses must be grounded in specific evidence — a specific tensor or operation in the mechanism, a specific claim in the justification, a specific metric in the measurement plan, a specific cost line in the accounting. Vague disagreements ("I don't think this will work") are not actionable and do not advance the debate.

Tradeoff and uncertainty disagreements should be framed as "this proposal optimizes for X (e.g., compute) at cost to Y (e.g., flexibility); the problem prioritizes X" — not as right/wrong. When the disagreement reduces to "we won't know until we measure," that is the signal for empirical under-determination convergence, not for continued argumentation.

For artifact-specific invariants a requirements document may be provided. When provided it is additive and authoritative — the proposed technique must conform to every invariant it states.
