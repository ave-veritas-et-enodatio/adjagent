@agents/guest-liaison.md

You are starting a new guest-model session via the `guest-liaison` subagent.

## Prerequisites

Before doing anything else, verify:
- `jq` or `python3` is available (`command -v jq || command -v python3`)
- The `guest-liaison` subagent contract was loaded above

If a prerequisite is missing, halt with an error listing what is missing.

## Parsing Arguments

Parse `$ARGUMENTS` as follows:
- **Topic name**: the first whitespace-delimited token. If empty, halt with: `"Usage: /guest-start <topic>"`.
- Reject topic names containing `/`, `\`, leading dots, or whitespace. If invalid, halt with a diagnostic.

## Pre-flight Checks

1. If `guest-session/<topic>/messages.json` exists and is non-empty, ask the user whether to **resume** the existing session or pick a different topic name. If the user chooses resume, skip system-prompt collection in step 4 and skip the initial-message step (the session already has its first user turn).

2. If `guest-session/active-topic.txt` exists and names a different topic, warn the user that switching will redirect `/guest` away from the prior session. Confirm before continuing. The prior session's directory is preserved either way; only the active-topic pointer changes.

## Onboarding

3. Collect connection parameters from the user:
   - `API_BASE_URL` — e.g. `https://api.example.com/v1`
   - `API_KEY_FILE` — path to a file containing only the API key (entire content trimmed of leading/trailing whitespace, no internal whitespace). **Do not read or display this file's contents** — only confirm presence with `test -f`.
   - `MODEL` — the model identifier

4. **New session only** — collect the system-prompt source:
   - **Agent identity**: a path under `.claude/agents/` (e.g. `.claude/agents/applied-mathematician.md`). The liaison will extract the body via `extract-agent-body.sh`.
   - **Default**: if the user declines to pick an agent, the liaison will use the literal string `You are a helpful assistant.`

5. **New session only** — collect the **initial message** to send to the guest model.

## Persist Session State

6. Create the session directory: `mkdir -p guest-session/<topic>/tmp`.

7. Write the connection params to `guest-session/<topic>/params.env` as KEY=VALUE lines (one per line, no quoting, no secrets — only the API key file *path*, which is not itself a secret):

   ```
   API_BASE_URL=<url>
   API_KEY_FILE=<path>
   MODEL=<model>
   ```

   If the user picked an agent identity, append `SYSTEM_PROMPT_AGENT=<path>` on a fourth line. Otherwise omit (default identity is implied).

8. Write `<topic>` to `guest-session/active-topic.txt` (overwrite any existing pointer).

## Dispatch

9. Invoke the `guest-liaison` subagent. Pass it:
   - Session topic
   - `API_BASE_URL`, `API_KEY_FILE`, `MODEL`
   - System-prompt source (agent path or literal default)
   - Initial user message (skip if resuming)

10. Relay the liaison's substantive response to the user verbatim, including any warnings the liaison emitted during the call.
