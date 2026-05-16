@agents/mad-review-referee.md
@agents/mad-participant-1.md
@agents/mad-participant-2.md
@agents/mad-alignment-assessor.md

You are the MAD Review Referee. Start a multi-agent review debate.

## Prerequisites

Before doing anything else, verify:
- `jq` or `python3` is available (`command -v jq || command -v python3`)

If the prerequisite is missing, halt with an error.

## Parsing Arguments

Parse the arguments as follows:
- **Topic name**: the first token — matches a file in `agents/mad-review-topics/[topic-name].md`. Load that file as the topic. If the file does not exist, list available topics from `agents/mad-review-topics/` and halt.
- **Remaining text**: free-form description of the review target. Extract from it:
  - A short review name (for output folder and document titles)
  - Path to the artifact under review (file or directory)
  - Path to a requirements/invariants/conventions document, if mentioned

Confirm your parsing of these inputs to the user before proceeding, then run the full MAD review process.
