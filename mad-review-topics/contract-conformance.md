# TOPIC: Contract-Conformance & Design-Soundness Review

## Domain

Review of a built system — its **specification/design** AND its **implementation** — against two orthogonal questions:

1. **Conformance** (code ⟶ spec): does the implementation actually do what the spec/design says it does?
2. **Soundness** (spec ⟶ principles & reality): does the design *itself* hold up — is it internally coherent, free of logic holes, and capable of delivering the utility it claims?

This is NOT a structural/architecture review (where things live, how they're factored) and NOT a code-style review. It is about **correctness against contract** and **whether the contract is sound**. A subsystem can be beautifully factored and still be wrong, incoherent, or quietly fail to deliver what it promises.

## Why both axes, and why soundness is the hard one

A pure conformance check cannot find the most dangerous defects, because **code can perfectly conform to a spec that is silent on the failure case.** The motivating example for this review: a recall "dead zone" where durable content was unfindable during an async-flush lag — the code conformed to the spec, but the spec never promised completeness, so no one noticed content could hide. That is the **unspecified-and-unchecked** class: a behavior that is neither specified nor tested, sitting in the gap between "the code is correct per spec" and "the system does what it claims."

The soundness axis exists to find that class. It asks, for every claimed capability: *does the mechanism actually achieve this end-to-end, or is there a gap where the claim quietly fails?*

## Three disciplines (binding — these are how this review avoids the failure mode that let the dead zone survive)

1. **Challenge claimed intent.** Treat every "by-design", "deliberate", "expected", "intentional", "acceptable lag", "good enough for v0.1" — in code comments, docs, or the spec — as a **claim to be attacked, not a premise to inherit**. A prior review inherited a defect's own "by-design" label and missed a real logic hole. Ask: *by-design according to whom, and is that design actually defensible?* The artifact does not get to certify its own correctness.

2. **Hunt the unspecified-and-unchecked.** Actively look for the dead-zone class: behaviors, edge cases, lifecycle states, and failure modes that are **neither specified nor tested**. For each subsystem, ask: what cases does the spec NOT address? Where could data/state be lost, hidden, duplicated, or corrupted without any contract forbidding it and any test catching it? An unaddressed case is a finding, not a non-issue.

3. **Does it do what it says on the tin.** For each capability the system CLAIMS (read the spec/README/architecture for the promises — persistent cross-project memory, complete recall, recoverable archival, no-context-loss, drift-cannot-accumulate, etc.), trace the actual mechanism end-to-end and decide: **does it deliver, or is the claim hollow / partial / conditional in a way the claim doesn't disclose?**

## What to look for

**Conformance (code vs spec):**
- Spec says X; code does Y (silent divergence), or does X-minus-the-hard-part (a shortcut/simplification not disclosed).
- Stubs, `TODO`/`FIXME`/`v0.1: simplified`, placeholder returns, "not yet" paths that a caller would assume work.
- A documented invariant the code does not actually enforce (or enforces only on a happy path).
- An API/port contract whose implementation violates the declared semantics (nil/empty/error handling, ordering, idempotency, atomicity).

**Soundness (is the design itself right):**
- **Logic holes**: a chain of mechanisms that, traced end-to-end, has a gap — a state where a guarantee silently fails (the dead-zone class). Trace lifecycles fully: creation → use → mutation → storage → eviction/archival → recovery → summarization. Where does something fall through?
- **Principle violations** — measure against the project's OWN stated principles (read ARCHITECTURE.md / AGENTS.md): deterministic state as canonical (never canonical state in the LLM), recall completeness / no dead zones, single-source-of-truth, single spelunking location, drift cannot accumulate, propose-and-ack at integrity-critical moments, etc. A design element that violates a load-bearing principle is a finding even if it "works."
- **Incoherence / makes-no-sense**: a mechanism whose stated purpose doesn't match what it does; two parts of the spec that contradict; a guarantee that is impossible given another constraint; a "solution" that doesn't actually solve the problem it cites.
- **Claims that can't be delivered**: a promised property the mechanism cannot actually provide (e.g. "no information loss" with a path that drops data; "recoverable" with a path that isn't).

## What this review is NOT
- Not architecture/structure (cohesion, file layout, coupling) — that is the `architecture` topic. Flag a structural issue only if it *causes* a conformance or soundness defect.
- Not style, naming, micro-DRY, or formatting.
- Do not flag a design choice as wrong merely because you'd choose differently — a soundness finding must show a real hole, incoherence, principle violation, or undeliverable claim, with evidence.

## How to report findings

Each finding must give:
1. **The claim/contract under scrutiny** — quote the spec §, the doc, or the stated capability.
2. **Axis** — Conformance (code≠spec) or Soundness (spec/design itself fails), and which discipline surfaced it.
3. **The gap** — exactly where it breaks: the deviation, the unhandled case, the logic hole, the principle violated, the undeliverable claim.
4. **Evidence** — spec section + code `file:symbol`/`file:line`. Trace the mechanism enough to prove the gap is real, not hypothetical.
5. **Impact on utility** — what capability is degraded or hollow if this stands. (Severity: BLOCKER = a core claimed capability silently fails / data can be lost or hidden; MAJOR = a guarantee is conditional/partial undisclosed; MINOR = a narrow edge or a soundness wart; NIT = cosmetic-contract.)

Where a requirements/spec document is provided it is **authoritative for the contract** — but the soundness axis may also find the spec ITSELF defective (a promise that's unsound, a missing case). Say which: "code violates spec" vs "spec is itself wrong/incomplete."
