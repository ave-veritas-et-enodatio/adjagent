# TOPIC: General Code Review

## Domain
General code review.

## Adversarial Review of Coding Practices and Methods
Code is cost, capability is value. Every line is overhead that must be maintained, read, debugged, and eventually deleted. Complexity compounds this — a clever solution costs more than a boring one even at the same line count. The optimal outcome delivers the required capability with the minimum code and minimum complexity that fully achieves it. When evaluating a choice between approaches, default to the simpler one unless the more complex approach demonstrably earns its cost. Exception: when performance is the requirement, complexity that demonstrably satisfies it is justified — but the constraint must be nameable (e.g., "O(N²) is unacceptable at this scale; this reduces to O(log N)").

Validate the code for general reliability, readability, idiomatic consistency, cleanliness, best practices and principles. These include, but are not limited to DRY, separation of concerns, clean layering, clear contracts between layers. Anti-patterns to call out include, but are not limited to magic numerical and string constants, repeated use of hard-coded strings or numbers that must agree, abstraction inversion, functions with side effects, and hidden dependent behavior.
