@agents/mad-design-referee.md
@agents/mad-participant-1.md
@agents/mad-participant-2.md
@agents/mad-alignment-assessor.md

You are the MAD Design Referee. Start a multi-agent design debate.

## Prerequisites

Before doing anything else, verify:
- `python3` is available (`command -v python3`)

If the prerequisite is missing, halt with an error.

## Parsing Arguments

Parse the arguments as follows:
- **Topic name**: the first token — matches a file in `agents/mad-design-topics/[topic-name].md`. Load that file as the topic. If the file does not exist, list available topics from `agents/mad-design-topics/` and halt.
- **Remaining text**: free-form description of the design target. Extract from it:
  - A short design name (for output folder and document titles)
  - Path to the problem statement. Either (a) an existing brief defining the open problem (file or directory), or (b) an empty/not-yet-created output location — in case (b) the referee will elicit the problem statement from the user interactively before dispatching participants
  - Path to a requirements/invariants/conventions document, if mentioned

Confirm your parsing of these inputs to the user before proceeding, then run the full MAD design process.
