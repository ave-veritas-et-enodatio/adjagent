# TOPIC: Python Physics Simulation Code Review

## Domain
First-principles simulation of physical phenomena driven by a well-defined mathematical framework.
This framework is the exclusive source of constants and truth.

## Adversarial Review of Coding Practices and Methods
Code is cost, capability is value. Every line is overhead that must be maintained, read, debugged, and eventually deleted. Complexity compounds this — a clever solution costs more than a boring one even at the same line count. The optimal outcome delivers the required capability with the minimum code and minimum complexity that fully achieves it. When evaluating a choice between approaches, default to the simpler one unless the more complex approach demonstrably earns its cost. Exception: simulation code frequently has legitimate performance requirements — vectorization, memory layout, numerical stability — where complexity is the direct cost of correctness or throughput. Such complexity is justified when the performance constraint is nameable and the simpler approach demonstrably fails to meet it.

Validate the code for general reliability, readability, idiomatic consistency, cleanliness, best practices and principles. These include, but are not limited to DRY, separation of concerns, clean layering, clear contracts between layers. Anti-patterns to call out include, but are not limited to magic numerical and string constants, repeated use of hard-coded strings or numbers that must agree, abstraction inversion, functions with side effects, and hidden dependent behavior.
