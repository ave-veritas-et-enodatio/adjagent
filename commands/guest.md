@agents/guest-liaison.md

You are continuing the active guest-model session via the `guest-liaison` subagent.

## Prerequisites

Before doing anything else, verify:
- `guest-session/active-topic.txt` exists. If missing, halt with: `"No active guest session — run /guest-start <topic> to begin."`
- The `guest-liaison` subagent contract was loaded above.

## Resolve Active Session

1. Read `guest-session/active-topic.txt` to obtain `<topic>`. Strip surrounding whitespace.

2. Verify `guest-session/<topic>/messages.json` exists and is non-empty. If missing, halt with:
   `"Active topic '<topic>' has no session log at guest-session/<topic>/messages.json — run /guest-start <topic> to initialize."`

3. Read connection params from `guest-session/<topic>/params.env`. Expected keys: `API_BASE_URL`, `API_KEY_CURL_CFG`, `MODEL`, optionally `SYSTEM_PROMPT_AGENT`. Treat `API_KEY_CURL_CFG` as an opaque path — do not read its contents. If the file is missing or any required key is absent, halt with a diagnostic asking the user to re-supply via `/guest-start <topic>`.

## Parsing Arguments

4. The full `$ARGUMENTS` string (all tokens, joined) is the user's message to the guest model. If empty, halt with: `"Usage: /guest <message>"`.

## Dispatch

5. Invoke the `guest-liaison` subagent in **continuation mode**. Pass it:
   - Session topic (`<topic>`)
   - `API_BASE_URL`, `API_KEY_CURL_CFG`, `MODEL` (from params.env)
   - User message (from `$ARGUMENTS`)

   The liaison will detect the existing `messages.json`, append the user message as a new turn, send to the guest model, service any file or tool requests, and return the substantive reply.

6. Relay the liaison's substantive response to the user verbatim, including any warnings the liaison emitted during the call.
