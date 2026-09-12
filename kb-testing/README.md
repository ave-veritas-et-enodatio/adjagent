# README - kb_tools testing

Integration testing harness and fixtures for kb- commands, agents, and kb_tools.

## Papers this corpus does not contain

The arXiv survey corpus is declared per category in `justfile` (`ARXIV_*`). Some
papers have been drawn and then removed, and the reasons are worth keeping so
they are not drawn again.

**Source pandoc cannot parse.** The build reads LaTeX through pandoc and does
nothing to the source first. Where pandoc's own parser fails — verified by
running `pandoc -f latex -t json <source>` from the paper's directory with no
filters and none of this project's flags — the paper measures nothing about this
pipeline, so it leaves the corpus rather than being worked around. **Massaging
raw LaTeX into shape before the reader sees it is out of scope**: it is a second
LaTeX implementation living in this repository, and the pipeline's whole premise
is that pandoc is the one thing that knows how LaTeX is read.

Removed on that ground: `2609.10029v1` (econ.TH), `2609.10235v1`
(physics.optics), `2609.10392v1` (eess.AS), `2609.10474v1` (math.PR). Why each
one defeats pandoc is not recorded and is not worth establishing — the test is
the exit code, and a paper that fails it is out.

Some of these are pandoc reaching into a **style or class file** rather than the
paper's own text, which is a property of the venue's template and not of what
the author wrote — so a replacement drawn from the same venue can fail the same
way. Validate a candidate with bare pandoc before adopting it.

**Source pandoc parses cleanly while dropping what it includes.** Worse than a
hard failure, because nothing downstream can see it: pandoc exits **0**, warns
only on stderr, and produces a document with the included files' content simply
absent — so every partition check compares one copy of the gap against another
and passes. `2609.09821v1` (cs.GR) is removed on that ground. The test is a
`Could not load include file` warning on stderr, which is invisible in the exit
code; a candidate that emits one is out.

**Submissions with no LaTeX at all.** arXiv serves a PDF from `/e-print` for a
submission that shipped no source, with a 200 and nothing distinguishing it
before it is unpacked. Two physics.optics draws were such. That category holds
two ids for both reasons together.
