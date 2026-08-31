# KB_PUB_TODO — publication & publicization plan

Working doc, 2026-08-30. Synthesizes the biz-dev-strategist analysis and the
landscape survey (both run against the post-1.7.0 repo state). Not a contract
doc; delete or trim as items complete.

## Strategy frame (from the strategist report)

- **Staged, not binary**: publish the KB *format/spec* early — it's the
  standard-setting asset at scoop risk. Hold the agent *pipeline* release to
  coincide with kbase having a public face, where it becomes the launch
  story's free reference implementation ("here's the format, here's the free
  Claude-based implementation, here's the local appliance").
- **The seeding funnel is weak; don't lean on it.** Claude-gated agent set →
  local-inference app has no natural upgrade moment, and the populations
  differ (math-LaTeX Claude users vs. docs-team buyers). The free tier's real
  value is credibility ("survives the hardest fidelity case — math"), not
  conversion.
- **The pipeline was never the moat** — kbase's differentiation (determinism,
  mechanical verification, no-Claude-required) survives the pipeline being
  public. Don't over-protect it.
- **Timing pressure is real but bounded**: OpenWiki (LangChain reach) is the
  nearest public neighbor and is missing exactly the verbatim-fidelity /
  LaTeX wedge. The category could consolidate around someone else's spec.
- **Counter-argument on file**: staged plans drift; a spec with no public
  reference implementation for months reads as vaporware. If capacity is the
  binding constraint, "say nothing until kbase ships" is the honest
  alternative. Decide deliberately, not by default.

## Channels — findability over performance

No feed-tending, no algorithm-piquing. Ordered by effort-to-yield.

### 1. Be listed where users already search (lowest effort, compounding)
- [ ] PR this repo into `awesome-claude-code` (hesreallyhim) and the major
      subagent collection lists (VoltAgent's and siblings index external
      sets).
- [ ] Add GitHub topics to the repo (claude-code, agents, subagents,
      knowledge-base, prompt-engineering, multi-agent).
- [ ] Consider `kb_tools` on PyPI — pip-installable stdlib tooling surfaces
      in searches the repo never will. (Decide: does publishing the toolchain
      precede or follow the format spec? Consistent with "format first.")
- [ ] Claude Code plugin/marketplace listing if/when the harness offers one
      for agent sets.

### 2. One durable technical writeup, submitted once
- [ ] Write the piece. Candidate angles (pick one, the others become sections):
      - the KB format itself: claim-graph spine, verbatim-fidelity leaves,
        machine-verified solidity — the spec as narrative;
      - "drift-proof agent definitions": single-sourced chunks, hash banners,
        model-tuned rendering — the gen-to-spec story;
      - the docent-headroom eval: measured retrieval behavior of a small
        model over a claim-graph KB (benchmark-shaped, citable).
- [ ] Host on a personal blog / repo docs page (evergreen search target).
- [ ] Single submissions: Show HN, lobste.rs. One shot each, judged on the
      artifact. Failure mode is silence, not obligation.

### 3. Maintainer-to-maintainer email (highest conversion per hour)
- [ ] OpenWiki (langchain-ai) — engage their claim-format discussion; the
      verbatim-fidelity wedge is exactly what they lack. Their audience is
      the spec's natural early adopters.
- [ ] BMAD-METHOD — nearest neighbor on generation+install; the per-model
      overlay axis is what they lack.
- [ ] zen-mcp-server — nearest neighbor on model-diverse debate; the
      referee/alignment-assessor information hygiene is what they lack.
- Format: short "we built X, overlaps yours here, differs there" notes.
  Private correspondence, zero performance.

### 4. The format spec as a citable artifact (the math wedge)
- [ ] Extract the KB format into a standalone spec document (claim-graph
      schema, S-invariants, leaf/summary contract, verification semantics) —
      independent of any app's ship date. METADATA_SCHEMA.md is most of the
      raw material.
- [ ] Consider a short technical report (arXiv-adjacent) — reaches the
      LaTeX-authoring population through the channel they actually read.
      Specs and benchmarks get cited; posts get scrolled past.

### 5. Forum-norm communities (substance-judged, no persona)
- [ ] r/LocalLLaMA — literally the local-inference audience (personant's
      market). Post when there's an artifact to show, not before.
- [ ] Claude Code community Discord — the agent-set audience.
- [ ] TeX community venues (TUG, TeX StackExchange presence) — for the
      LaTeX wedge, when the pipeline publishes.

## Sequencing

1. Findability items (§1) — can happen the day the repo goes public; ~1 hr.
2. Format spec extraction (§4) — precedes or accompanies the writeup.
3. Writeup + submissions (§2) — after the spec exists to link to.
4. Maintainer emails (§3) — after there's a public artifact to point at.
5. Community posts (§5) — opportunistic, artifact-driven, never scheduled.
6. Pipeline publication + full marketing push — coupled to kbase's public
   debut, per the staged strategy.

## Open decisions (owner)

- [ ] Open-vs-reserve ruling: adopt the staged plan, or hold everything until
      kbase ships (the strategist's own counter-argument).
- [ ] License shape for whatever publishes (permissive spreads standards;
      source-available protects against re-hosting but mutes adoption).
- [ ] Repo hygiene before going public: README is ready (post-editorial
      pass); decide fate of working docs like this one.
