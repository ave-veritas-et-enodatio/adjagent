# ROADMAP

Future intent only. Not part of the contract-doc precedence chain; not handed to coding dispatches.

1. **Periodic load-bearing-ness pruning pass** over definition clauses, across model generations: apply strip-first-observe-patch (README.md, "Variants and platform compatibility") on a cadence, using behavioral probe sets as the instrument. Byte-level checks (`just check`) exist today; behavioral checks do not yet.
2. **Resolve the README ↔ command-files invocation-syntax divergence**: README examples show `KEY=` style argument passing; the command files parse positional/free-form input. Pick one and make the other conform.
3. **Possible promotion of the guest READ/LIST/GREP relay protocol** into a reusable eval instrument under `liaison-tools/`.
4. **Replace the kb-* agent set with the ModernCorp port.** The kb-* definitions here are stale copies no project consumes from this repo (AVE-Core carries its own); an updated, more portable/retargetable version of the kb agents and tooling exists in the ModernCorp project and will be ported over to supersede them.
5. No other work intended at this time.
