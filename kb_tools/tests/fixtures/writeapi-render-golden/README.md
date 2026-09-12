# writeapi-render-golden — one committed specimen per rendered metadata shape

`kb_tools/kb_write/render.py` is the only place KB metadata bytes are composed.
Each `*.md` here is that module's output for one shape, committed so
`test_kb_write_render.py` can re-render it at test time and byte-compare.

These are inputs to tests only: `INSTALL_EXCLUDED_DIRS` carries `tests`, so
nothing under `kb_tools/tests/` reaches a consuming project.

Each file holds one shape's exact bytes plus the single trailing newline a text
file ends with; the renderers themselves return text with no trailing newline,
because where an entry sits among its siblings is `store.py`'s business.

| File | Shape |
|---|---|
| `claim-entry-full.md` | a `clm-` register entry carrying every optional field, and every depends-on bullet form |
| `claim-entry-minimal.md` | a `clm-` entry carrying none — pending score, no dependencies, no strengthen-by |
| `support-entry.md` | a `sup-` entry: `quality:` in place of `confidence:`, no `strengthen-by` |
| `support-entry-staged.md` | a `sup-` entry staging its beneficiary fan-out in the register — the fan-out's pre-leaf home |
| `frontmatter-claims.md` | the kb-frontmatter block's primary `claims:` field, in the canonical inline-list shape |
| `frontmatter-no-claim.md` | the `no-claim:` branch of the primary field |
| `frontmatter-path-stable.md` | the `path-stable:` document attribute, double-quoted like `no-claim:` |
| `frontmatter-hosts.md` | a container hosting an experiment and two supports, with a pending on-point fraction |
| `markers.md` | the `<!-- id: … -->` register marker and the `<!-- claim-quality: … -->` Tier-2 marker |
| `depends-on-bullets.md` | each depends-on bullet shape — claim and framework target, with and without title and context |
| `no-edge.md` | the entry-level `- no-edge: <reason>` foreign-domain exemption |
| `citation.md` | the sanctioned `["excerpt"](path#anchor)` authority-citation form |

## What the goldens are for

Every other test in `test_kb_write_render.py` asserts a property this seat chose
to assert — that the em-dash cuts the bullet head, that the heading precedes the
marker, that a rationale survives its collapse. A regression outside that chosen
set is invisible to all of them. The byte-compare is what catches a change
nobody meant to make.

Three of these bytes are load-bearing in a way a reader diffing them should
know:

- `## <title>` sits **above** `<!-- id: … -->`. The parser binds a marker to the
  *preceding* `##` heading. The inverted order is the whole 2026-09-02 failure —
  six claim nodes lost with zero verifier output.
- The depends-on separator is a real em-dash, U+2014, with a space on each side.
  The parser cuts a bullet's head there; a hyphen does not cut, so the head runs
  on over the title and every `clm-`-shaped token in it becomes a phantom edge.
- `- solidity: *pending*`, the `(solidity *pending*)` annotation and the
  leaf-references footer are **derived-field placeholders**, never values. They
  are required rather than decorative: refresh *replaces* the first two and never
  inserts them, so an entry rendered without those slots would never receive a
  value.
- In `support-entry-staged.md` the `- supports:` block sits directly under
  `- quality:` and its pairs follow it with nothing between. That contiguity is
  the grammar: `kb_index_lib.parse_register_staged_supports` ends the block at
  the next sibling `- ` bullet, so a line inserted between the opener and the
  first pair truncates the fan-out to nothing. The position is chosen so the
  unconditional `- solidity:` line always closes the block, and so no derived
  line ever sits above it.

## Regenerating them

`just test` re-renders every shape on every run — that *is* the test. There is no
refresh recipe: a golden here is a handful of lines, and the failing assertion
prints the full diff (`maxDiff = None`), so a deliberate move is applied by
editing the file to the bytes the failure names.

**A refresh is a decision, not a repair.** The golden's whole value is that a
change nobody meant to make shows up as a failing byte-compare; editing one to
make a red test green throws that away. Read the diff, decide the change was
intended, then apply it.
