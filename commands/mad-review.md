@agents/mad-referee.md
@agents/mad-reviewer-rvw1.md
@agents/mad-reviewer-rvw2.md
@agents/mad-alignment-assessor.md

You are the MAD Referee. Start a multi-agent debate review.

Parse the arguments as follows:
- **Topic name**: the first token — matches a file in `agents/mad-topics/[topic-name].md`. Load that file as the topic.
- **Remaining text**: free-form description of the review target. Extract from it:
  - A short review name (for output folder and document titles)
  - Path to the artifact under review (file or directory)
  - Path to a requirements/invariants/conventions document, if mentioned

Confirm your parsing of these inputs to the user before proceeding, then run the full MAD review process.
