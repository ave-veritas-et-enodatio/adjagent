-- Author-distinguished blocks become labelled blockquotes; the title page goes.
--
-- IDENTIFYING a block is structural — a Div whose class is none of pandoc's own.
-- Pandoc classes an environment it does not recognise with the environment's own
-- name, so `\newtheorem{claimbox}{Result}` yields a Div classed `claimbox`, and
-- a list of declarations would find neither an environment nobody declared nor
-- amsthm's `proof`, which is declared by nobody.
--
-- NAMING it is a different question with a different answer. `\newtheorem`
-- declares two strings: an arbitrary internal handle, which is what the class
-- carries, and a display name, which is what the block IS. `claimbox` says
-- nothing; `Result` says everything a downstream classifier wants, and the tail
-- of internal handles across real corpora is aliases (`Cor`, `Lem`, `Pro`),
-- starred variants (`lem*`) and other languages (`Teo`, `oss`) — a tail no list
-- of names closes. So the label line carries the display name where the caller
-- supplied one and the class where it did not.
--
-- THEOREM_NAMES is that mapping, and it is the second thing this filter cannot
-- infer: pandoc consumes the preamble, so no `\newtheorem` reaches a filter in
-- any form. The caller reads it off the source and says so in metadata, the way
-- it does for KEYS_ONLY below, and for a class the mapping does not answer the
-- fallback is the class itself — which is the label every block carried before
-- the mapping existed.
--
-- STRUCTURAL is that closed list, and it is the literal a reader checks against
-- a corpus. Six of its members are exercised by the tracked corpus; `references`,
-- `csl-bib-body` and `hanging-indent` are the three classes citeproc's own
-- bibliography Div carries (its *identifier* is `refs`).
--
-- The identifier is carried forward on a Span around the label. A `\label` on a
-- theorem lands as the Div's id and is what every `\ref` to that theorem targets;
-- the straightforward reshaping drops it, which breaks exactly the blocks a claim
-- graph cares most about. A Span rather than raw HTML because the identifier has
-- to survive as a *node* — the stage that builds the label-to-node map walks this
-- filter's output, and an id spelled into a raw string is invisible to a walk.
--
-- CITATIONS. Every Cite is wrapped in the markup pandoc itself emits for one --
-- `<span class="citation" data-cites="key1 key2">` -- so a citation is
-- recognisable by its own key whatever happened to it downstream. This is not an
-- invented form: it is what pandoc's own HTML writer produces for a Cite,
-- checked against the binary rather than read off documentation.
--
-- The wrap is the whole trick, and what it buys is that citeproc is untouched.
-- The Cite node stays inside the Span, so citeproc still resolves it, still
-- writes `(**key?**)` for a key the bibliography does not answer, and still
-- emits the `refs` Div the references leaf is cut from. Replacing the Cite
-- instead would leave citeproc no citation to find and no bibliography to build.
--
-- KEYS_ONLY is the one thing the filter cannot infer: with no bibliography there
-- is no citeproc, and a Cite nobody resolves renders as *nothing at all* -- the
-- silent deletion this exists to end. The caller knows, because it is the same
-- condition that decides `--citeproc`, and says so in metadata. The filter list
-- at the bottom is what makes that readable: a Meta pass runs before the inline
-- pass, where a single filter table would read the metadata after the inlines it
-- governs.
--
-- DISPLAY MATHS OUT OF EMPHASIS. `amsthm` sets a theorem's statement in italic,
-- so the reader hands back the whole statement as one Emph -- display equations
-- included -- and the gfm writer then spells that Emph's own delimiter against
-- the fence it opens for the first equation, `*``` math`, and against the one
-- that closes the last, ` ```*`. Neither is a fence to any reader of the result:
-- SPEC.md point 9 says display maths reaches a document as a fenced block holding
-- the LaTeX verbatim, and a `*` glued to the opening delimiter means it does not.
-- The damage is not local either -- a fence that never opens leaves its closer to
-- open one, and everything after it, headings included, is read as fenced.
--
-- So the equation is lifted out and the words either side of it keep their
-- emphasis. Emph is where this has been measured; a wrapper nobody has seen
-- carry display maths is absent for the reason STRUCTURAL's members are, and
-- meets the stage-2 alignment check rather than passing quietly.

-- The metadata key the caller sets when no bibliography stands, so no citeproc
-- will run. Spelled in `pandoc.py` as well, and the two are held equal by
-- `tests/test_pandoc.py`.
local KEYS_ONLY = "kb-citation-keys-only"

-- The metadata key carrying the `\newtheorem` internal-name -> display-name
-- mapping, JSON-encoded: an internal name may carry a `*` and a display name a
-- space, so a delimited list would be a parser with a corpus that breaks it.
-- Spelled in `pandoc.py` as well, and the two are held equal by
-- `tests/test_pandoc.py`.
local THEOREM_NAMES = "kb-theorem-names"

local keys_only = false

local display_names = {}

function Cite(element)
  local keys = {}
  for _, citation in ipairs(element.citations) do
    table.insert(keys, citation.id)
  end
  -- The attribute is space-separated because that is what pandoc's own writer
  -- puts there; the visible text is `; `-separated because that is what
  -- citeproc renders a multi-work citation as -- `(Mertens 2026; Other 2020)`.
  -- One citation element is one parenthesised group either way.
  local joined = table.concat(keys, " ")
  local attributes = pandoc.Attr("", { "citation" }, { ["data-cites"] = joined })
  if keys_only then
    -- Parenthesised rather than bracketed: the gfm writer escapes `[`, so
    -- brackets reach the leaf a person opens as `\[key\]`, and every other
    -- citation form this pipeline renders -- citeproc's `(Author Year)`, and its
    -- `(**key?**)` for one it cannot answer -- is parenthesised already.
    return pandoc.Span({ pandoc.Str("(" .. table.concat(keys, "; ") .. ")") }, attributes)
  end
  return pandoc.Span({ element }, attributes)
end

local STRUCTURAL = {
  center = true,
  tabular = true,
  titlepage = true,
  thebibliography = true,
  references = true,
  ["csl-bib-body"] = true,
  ["hanging-indent"] = true,
  ["csl-entry"] = true,
}

function Div(element)
  local authored = nil
  for _, class in ipairs(element.classes) do
    if class == "titlepage" then
      return {}
    end
    if authored == nil and not STRUCTURAL[class] then
      authored = class
    end
  end
  if authored == nil then
    return nil
  end

  local name = pandoc.Strong({ pandoc.Str(display_names[authored] or authored) })
  local label = pandoc.Plain({ name })
  if element.identifier ~= nil and element.identifier ~= "" then
    label = pandoc.Plain({ pandoc.Span({ name }, pandoc.Attr(element.identifier)) })
  end

  local blocks = { label }
  for _, block in ipairs(element.content) do
    table.insert(blocks, block)
  end
  return pandoc.BlockQuote(blocks)
end

-- Inlines that are a gap rather than a word. A group holding nothing else is
-- emitted unwrapped: the gfm writer spells an Emph with no word in it as a bare
-- `**`, and the writer already moves a group's own edge whitespace outside the
-- delimiters, so there is nothing here to trim.
local WHITESPACE = { Space = true, SoftBreak = true, LineBreak = true }

local function is_display(inline)
  return inline.t == "Math" and inline.mathtype == "DisplayMath"
end

local function has_display(content)
  for _, inline in ipairs(content) do
    if is_display(inline) then
      return true
    end
  end
  return false
end

function Emph(element)
  if not has_display(element.content) then
    return nil
  end

  local lifted = {}
  local group = {}
  local worded = false

  local function flush()
    if worded then
      table.insert(lifted, pandoc.Emph(group))
    else
      for _, inline in ipairs(group) do
        table.insert(lifted, inline)
      end
    end
    group, worded = {}, false
  end

  for _, inline in ipairs(element.content) do
    if is_display(inline) then
      flush()
      table.insert(lifted, inline)
    else
      table.insert(group, inline)
      worded = worded or not WHITESPACE[inline.t]
    end
  end
  flush()
  return lifted
end

-- Two passes, in this order and for this reason: a filter table's Meta function
-- runs *after* the inlines it would govern, so a single table would read
-- KEYS_ONLY too late to act on it.
--
-- Both keys are read and then REMOVED. Neither is the document's metadata --
-- each is an invocation argument the tool passed itself, the same category
-- point 13 puts the bibliography path in -- and the outline reader classifies
-- every metadata key it finds against a closed content/apparatus list. Leaving
-- one would either stop the build or widen that list, and widening it for a
-- flag this filter invented is the wrong direction entirely: what the split is
-- closed against is a metadata field arriving unclassified, and the answer for
-- one nobody authored is to hand it back.
return {
  {
    Meta = function(meta)
      keys_only = meta[KEYS_ONLY] ~= nil
      meta[KEYS_ONLY] = nil
      local declared = meta[THEOREM_NAMES]
      if declared ~= nil then
        display_names = pandoc.json.decode(pandoc.utils.stringify(declared))
        meta[THEOREM_NAMES] = nil
      end
      return meta
    end,
  },
  { Cite = Cite, Div = Div, Emph = Emph },
}
