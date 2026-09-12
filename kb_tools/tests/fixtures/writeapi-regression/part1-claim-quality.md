# Part 1 Claim Quality Register

---

<!-- id: clm-07687j -->
## Regime Conservation Laws

> **Leaf references:** *(none)*

### Quality
- confidence: 0.7
- depends-on:
  - *(none entry-local)*
- rationale: CES asymptotics applied with explicit exponent formula ($O(\Pi_2^{1-\sigma_2})$) and cited methodology (factor-and-expand). Cyclic-coordinate and transversality steps end-to-end and correct, referenced to standard optimal-control theory. Scope clearly marked as leading-order with error term bounds. Framework assumptions ($\sigma_2 < 1$, nested structure) stated. Not 0.9 because CES asymptotics are applied rather than re-derived; rises to 0.7 because methodology is disclosed and boundaries explicit.

### Solidity
- solidity: *pending*

---

<!-- id: clm-j634h4 -->
## Macro Collapse Under Mis-Encoded Symmetry

> **Leaf references:** *(none)*

### Quality
- confidence: 0.7
- depends-on:
  - clm-07687j — Regime Conservation Laws
- rationale: Parts (i) and (ii) fully derived: inadmissibility via direct substitution and contradiction (algebraic verification); output collapse via standard ODE and CES limit evaluation. Part (iii) aggregation depends on stated macro-to-firm lemma (lines 506–514, derivation deferred to Part 2), disclosed transparently as unproven. Scope qualifications explicit: asymptotic result, $\delta_A > 0$ for strong form, partial vs. complete mis-encoding distinguished (lines 560–571). Methodology disclosed; open dependency clearly marked. Not 0.9 due to lemma; 0.7 because bounds and open steps transparent.

### Solidity
- solidity: *pending*

---
