# Part 3 Claim Quality Register

---

<!-- id: clm-k970j7 -->
## Price-Unclosable Attention Wedge

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.7
- depends-on:
  - *(none entry-local)*
- rationale: Proof note (lines 211–217) supplies the derivation chain: firm equates perceived MP to wage, true MP exceeds wage by multiplicative factor $(1+\varphi_e)^{1-\rho_2}$. Mechanism is explained step-by-step (distorted perception of stock → MP calculation → true MP revealed by solving). Open item explicitly named: general case ($\Ahat_i > 0$) deferred to appendix. Disclosed methodology with stated bounds. Classified as manifestation of the production structure and reporting distortion: falsifiable if $(1+\varphi_e)^{1-\rho_2} \not> 1$ for $\varphi_e > 0$ and $\rho_2 < 0$.

### Solidity
- solidity: *pending*

---

<!-- id: clm-3105j6 -->
## Self-masking Slows Learning Below Collapse Clock

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.7
- depends-on:
  - clm-k970j7 — Price-Unclosable Attention Wedge
- rationale: Proof note (lines 314–326) derives the learning-clock scaling: Fisher information $\int_0^t r(e(s))\,ds$ governs Bayesian consistency; under ratchet $d|e|/dt \to k/\mu$, the integral diverges logarithmically for $\gamma = 1$ and finitely for $\gamma > 1$. Precision grows as $\ln t$ while deficit grows linearly, starving correction on collapse timescale. Methodology disclosed with specific rate function, dynamics, and Fisher-information criterion. Open items explicitly bounded: (1) zombie equilibrium (finite $\beta \in (0,1)$) has positive constant signal rate and different starvation regime; (2) first-passage constant left open. Classified as derived prediction of the signal-rate and deficit-dynamics framework. Falsifiable if cumulative information diverges or grows faster than stated.

### Solidity
- solidity: *pending*

---

<!-- id: clm-h77054 -->
## The Un-blinded Selector

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.5
- depends-on:
  - clm-k970j7 — Price-Unclosable Attention Wedge
- rationale: Proposition states three experiential channels (labour, consumer, mortality) remain unblinded where capital is blinded. Argument given (lines 381–389): experiential signal $s_X = q(A_P)$ is "a draw from the true state, since under $\sigma_2 < 1$ the attention deficit propagates into output and into the work; it never transits the channel that generates $\varphi_e$." Mechanism is structural argument, not formally derived here. Validity rests substantively on Section 3's characterization (that friction is on reporting channel, not state) and the production-function implication (deficit propagates to experiential outcomes under complementarity). Those links are asserted rather than formally proven in this section. Classified as manifestation of observability structure. Falsifiable if experiential outcomes can be distorted by reporting channel or if deficit does not propagate to experience under $\sigma_2 < 1$.

### Solidity
- solidity: *pending*

---

<!-- id: clm-j6i01j -->
## Non-transferability

> **Leaf references:** *(auto-generated)*

### Quality
- confidence: 0.3
- depends-on:
  - clm-k970j7 — Price-Unclosable Attention Wedge
  - clm-3105j6 — Self-masking Slows Learning Below Collapse Clock
  - clm-h77054 — The Un-blinded Selector
- rationale: Proposition states three-part conjunction: (1) cannot substitute (no machine bears accountability), (2) cannot import without re-inclusion (procurement relocates corrupted channel, not observable state), (3) therefore no decoupled economy at prior scale is fixed point. Components rely on prior sections (hinges, observability, friction characterization). Lines 530–540 supply partial argument for (2)—procurement narrative (offshoring widens wedge, arms-length maximizes φₑ)—but this is narrative reasoning, not formal derivation. Claim (1) asserted without proof. The overall proposition reads as conjunction/restatement of prior results (lines 551–553: "This is the conjunction of Section 3 read for the thesis") rather than independent derivation. Classified as manifestation of binding + non-observability structure. Falsifiable if substitution or relocation can close observability gap.

### Solidity
- solidity: *pending*

---
