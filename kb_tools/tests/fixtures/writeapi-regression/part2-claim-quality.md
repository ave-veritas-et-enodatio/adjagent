# Part 2 Claim Quality Register

---

<!-- id: clm-3ig11l -->
## Fixed Points

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.3
- depends-on:
  - *(none entry-local)*
- rationale: Lemma statement (lines 314–318) asserts the existence and location of equilibria (origin and zombie at $s^* = (\beta-1)/(\beta\mu)$) without proof or derivation. Statement is used in subsequent proofs but the verification of the claimed equilibrium coordinates is not shown.

### Solidity
- solidity: 0.3

---

<!-- id: clm-l56g59 -->
## Governance Bifurcation Theorem

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-3ig11l — Fixed Points
- rationale: Theorem statement (lines 339–349) with proof sketch at lines 351–361. Full proof deferred to Appendix A (app:bifurcation). Proof sketch explicitly states "The full account is in Appendix~\ref{app:bifurcation}" and names the key ingredients (determinant signs, eigenvalue analysis, Lyapunov certificate). Derivation is complete end-to-end across body and appendix.

### Solidity
- solidity: 0.9

---

<!-- id: clm-i53k14 -->
## β-Independent Fast Certificate

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-l56g59 — Governance Bifurcation Theorem
- rationale: Lemma statement (lines 680–689) with complete proof embedded at lines 690–693. Proof verifies that $\dot V_{\mathrm{fast}}$ decomposes into negative-definite forms on each half-plane ($v>0$ and $v<0$) for all $\beta>1$, and shows continuity across $v=0$. Derivation end-to-end.

### Solidity
- solidity: 0.9

---

<!-- id: clm-48gj1k -->
## Forward Invariance of Safe Region

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 1.0
- depends-on:
  - clm-l56g59 — Governance Bifurcation Theorem
- rationale: Lemma statement (lines 695–699) asserts that $\{I < I_c\}$ is forward-invariant. The claim is an algebraic identity: the inequality $I' = I(1-d) + \alpha_{\mathrm{inv}}d \le (1-d)I_c + d\,\alpha_{\max} < I_c$ follows directly from convexity and the given bounds. No proof shown because the result is immediate from definitions and arithmetic.

### Solidity
- solidity: 1.0

---

<!-- id: clm-4114i0 -->
## Hybrid Stability of Constitutional Firm

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-l56g59 — Governance Bifurcation Theorem
  - clm-i53k14 — β-Independent Fast Certificate
  - clm-48gj1k — Forward Invariance of Safe Region
- rationale: Theorem statement (lines 447–461) with proof deferred to Appendix C (app:hybrid, lines 701–720). Full composition proof shown: establishes safe-set asymptotic stability via $V_{\mathrm{fast}}$-based Lyapunov function and geometric convergence of cap table via $V_{\mathrm{slow}}$ under cap-binding allocation. All three load-bearing conditions stated explicitly (lines 463–471). Derivation end-to-end.

### Solidity
- solidity: 0.9

---

<!-- id: clm-9j27j1 -->
## Macro-to-Firm Propagation

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - *(none entry-local)*
- rationale: Proposition statement (lines 490–495) with complete derivation via competitive factor markets (lines 497–506). Argument traces factor-price transmission: $w_A / r_{\tilde{K}} = \Pi_2 > 1$ (macro) implies firm inherits $\Pi_c > 1$, hence $\muAc > 0$ by costate balance (Appendix E). Full derivation embedded; cross-volume reference to Part 1 for regime parameter $\Pi_2$ and to Appendix E for costate result.

### Solidity
- solidity: 0.9

---

<!-- id: clm-g3iig7 -->
## Institutional Misalignment

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - *(none entry-local)*
- rationale: Proposition statement (lines 844–852) with proof labeled "Argument" (lines 854–865). Proof derives that setting $\mu_{A_c} = 0$ in attention regime ($\Pi_c > 1$) omits optimal first-order conditions, showing misallocation magnitude scales with $(\Pi_c - 1)$. Proof is complete: it identifies the domain error (applying material-regime solution post-transition) and quantifies the cost via costate balance.

### Solidity
- solidity: 0.9

---

<!-- id: clm-14895g -->
## Discrete Pontryagin Analysis

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - *(none entry-local)*
- rationale: Proposition statement (lines 950–967) with four enumerated results (Form, Signs, Timing, Material regime), each with full justification. Form derived from Hamiltonian linearity. Costate signs (S1, S2) proved by backward induction (app:pontryagin-costate, lines 1003–1044). Timing proven by gate-identity contradiction (eq. 959–960). Material regime confirmed numerically. Repair documented in Remark rem:condii (lines 969–984) showing numerical audit falsified earlier claim and locating flaw. Derivation end-to-end.

### Solidity
- solidity: 0.9

---

<!-- id: clm-jll5k1 -->
## Gate Optimality Under Credibility (Open Conjecture)

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.3
- depends-on:
  - clm-14895g — Discrete Pontryagin Analysis
- rationale: Conjecture statement (lines 1082–1088) without proof. Status explicitly OPEN. Supporting argument provided (lines 1090–1099) describing proof programme as "three steps, each well posed" and naming the programme structure. Numerical prototyping (Remark rem:protoB, lines 1101–1123) confirms conjectured flip at KPI margin and sharpens scope. No analytic proof supplied; dependence on extended dynamics with credibility state $c_n$ remains unresolved.

### Solidity
- solidity: 0.3

---

<!-- id: clm-6kk2h6 -->
## Positive Invariance of M

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - *(none entry-local)*
- rationale: Theorem statement (lines 1140–1164) with five enumerated clauses, each with embedded proof or justification. (i) Investor cap proven by convexity (line 1145). (ii) Employee floor proven by absorption (line 1147). (iii) KPI-gating stated as charter rule (line 1149). (iv) Anti-stagnation proven by recurrence and boundedness (lines 1154–1159). (v) Governance structure stated as charter-level invariance (line 1161). All load-bearing conditions verified. Derivation end-to-end.

### Solidity
- solidity: 0.9

---

<!-- id: clm-64k104 -->
## Validity of Dimensional Reduction

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-6kk2h6 — Positive Invariance of M
- rationale: Corollary statement (lines 1166–1170) with supporting justification via slaving argument (lines 1172–1181). Argument cites Haken's slaving principle and geometric singular-perturbation theory (Fenichel) to justify fast–slow decomposition. Cites thm:invariance as necessary condition: fast subsystem stays in productive region, so reduction does not degenerate. Derivation end-to-end as corollary from thm:invariance.

### Solidity
- solidity: 0.9

---

<!-- id: clm-286k63 -->
## Lyapunov Stability of Cap-Table Attractor

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - *(none entry-local)*
- rationale: Proposition statement (lines 1219–1223) with complete proof embedded (lines 1224–1227). Proof derives exact recurrence $V(I_{n+1}) = (1-d)^2 V(I_n)$ by direct substitution and algebra, then concludes convergence $V(I_n) \to 0$. Proof is terse but complete: all steps are algebraically verifiable and no external reference is needed.

### Solidity
- solidity: 0.9

---

<!-- id: clm-4gi6l6 -->
## Geometric Convergence

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-286k63 — Lyapunov Stability of Cap-Table Attractor
- rationale: Corollary statement (lines 1229–1235) states geometric convergence bound $N(\varepsilon) \le \frac{\log(1/\varepsilon)}{2\,d_{\min}}$ on cycle count to reduce displacement by factor $\varepsilon$. Derived directly from prop:lyapunov's recurrence $V(I_n) \le V(I_0)(1-d_{\min})^{2n}$ via logarithm. Derivation end-to-end as corollary.

### Solidity
- solidity: 0.9

---

<!-- id: clm-g0l36l -->
## Global Basin of Attraction

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-286k63 — Lyapunov Stability of Cap-Table Attractor
- rationale: Corollary statement (lines 1237–1240) asserts basin of attraction of $I^* = 0.49$ is all of $[0,1]$ under the 49% cap and dilution condition $d_n \ge d_{\min} > 0$. Derived from prop:lyapunov: the Lyapunov function $V(I_n) = (I_n - I^*)^2$ contracts on its level sets for any initial condition, and the uniform convergence holds globally. Derivation end-to-end as corollary.

### Solidity
- solidity: 0.9

---

<!-- id: clm-962898 -->
## Switched-System Stability of Cap-Table Attractor

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.9
- depends-on:
  - clm-286k63 — Lyapunov Stability of Cap-Table Attractor
  - clm-4gi6l6 — Geometric Convergence
  - clm-g0l36l — Global Basin of Attraction
- rationale: Theorem statement (lines 1291–1296) with complete proof embedded (lines 1297–1307). Proof verifies three Lyapunov conditions for switched systems: (i) positive definiteness of $V(I^*) = 0$; (ii) non-increase in frozen mode ($d_n = 0$); (iii) strict decrease in event mode via prop:lyapunov's recurrence. Proof shows anti-stagnation clause ensures event mode recurs infinitely, so condition (iii) bites. Each mechanism's role made explicit (lines 1309–1312). Derivation end-to-end.

### Solidity
- solidity: 0.9

---
