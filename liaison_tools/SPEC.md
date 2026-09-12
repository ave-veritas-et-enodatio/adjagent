# SPEC – liaison_tools

`liaison_tools/` is the shipped package of Python helpers that let an agent relay a conversation with an external, non-Claude model without that model's credentials or wire protocol ever entering the agent's own context. This document states the observable contract each helper holds today, independent of how it is implemented — see ARCHITECTURE.md for mechanism. **Draft status**: distilled from the code, not yet ruled. Where the code leaves a question open, the "not established by the code" notes below say so rather than assert a guarantee.

This document specializes root `SPEC.md`'s "Deployed Surfaces" (shipped-package class) for this package specifically; on any conflict, the root document governs and this one is the defect.

## Components

| File | Contract role |
|---|---|
| `post-openai.py` | The wire transport: one OpenAI-compatible chat-completions call, SSE-streamed, reassembled to a canonical stdout shape. |
| `msg-util.py` | The sole sanctioned mutator of a messages-file: `init` / `append` / `validate`. |
| `relay-driver.py` | A scripted corpus-relay eval instrument, invoked directly rather than by a liaison agent; composes the two tools above rather than reimplementing either. |

`__init__.py` is a package marker only (for pytest module-path stability) and carries no contract of its own. `tests/` is the verification suite for the claims below, not part of the contract.

**stdlib-only.** No file in this package imports a third-party module, tests included; `post-openai.py`, `msg-util.py` and `relay-driver.py` each state the invariant in their own module docstring. An installed consumer needs a `python3` on `PATH` and nothing else — no install step is ever a precondition for these tools running.

**Every file here is a command, not a module.** `msg-util.py`, `post-openai.py` and `relay-driver.py` are hyphenated, so none of them is a legal module name and none can be imported — which is the whole of the contract, since each is invoked by command line and nothing in this package imports another.

## Wire Transport (`post-openai.py`)

**Invocation.** `API_BASE_URL`, `API_KEY_FILE`, and `MODEL` are required environment variables; the messages-file path is a positional argument or `MESSAGES_FILE`. The full optional-parameter list (`MAX_TOKENS`, `ENABLE_THINKING`, `TEMPERATURE`, `DEBUG_POST`, `DEBUG_RESPONSE`, `USAGE_STATS_FILE`) and their defaults are documented once, in `post-openai.py`'s module docstring — that docstring is the source of record for the exact parameter grammar; this section states the guarantees built on top of it.

**Output contract.** On success, stdout carries exactly one of two shapes: the reassembled response text, or `TOOL_CALLS\n<json>` where `<json>` is an ordered array of `{"id", "type", "function": {"name", "arguments"}}` objects. Nothing else is ever written to stdout; warnings and errors go to stderr only.

**Exit codes** — the contract a caller branches on:

| Code | Meaning | Caller obligation |
|---|---|---|
| 0 | A complete reply; stdout carries the output contract above. | Treat as substantive. |
| 1 | Usage, configuration, or transport failure. | Retryable. |
| 3 | The endpoint completed the call but the reply is incomplete (`finish_reason` is not `stop`, `tool_calls`, or `function_call` — e.g. `"length"`). Whatever arrived is still written to stdout for the audit trail. | Never record as complete; never retry the identical request — retrying will not help. |
| 4 | The endpoint returned an empty completion with a normal finish reason — a protocol-level empty result, not a transport failure. stdout is empty. | Never retry the identical request. |

Exit 3 and 4 are protocol events, not transport failures — a caller retries neither, but both still append to `USAGE_STATS_FILE` when it is set, because the tokens were spent regardless of the reply's usability.

**Transport rules that protect the API key** (see also API Key Handling, below):
- `API_BASE_URL` must be `https`, except for loopback hosts (`localhost`, `127.0.0.1`, `::1`), where `http` is accepted for local inference, or any host when `ALLOW_HTTP=1` or `--allow-http` is given — an explicit, off-by-default opt-in for an operator who knows their endpoint is on a trusted private network.
- Redirects are never followed. A `3xx` from the endpoint is a hard error naming the refused target, because following it would carry the `Authorization` header — and with it the key — to whatever host the `Location` header names.

**Model resolution.** If a request fails with what looks like a model-not-found error, the script queries `GET /models` and retries once against an exact or unambiguous-substring match, emitting a warning either way. This retry consumes a second live call; it is not a dry-run check.

**Usage side channel.** When `USAGE_STATS_FILE` is set, one JSON line — `{"prompt_tokens", "completion_tokens", "total_tokens", "model"}`, fields `null` where the endpoint omitted them — is appended per successful call (any of exit 0, 3, or 4). A write failure there is a stderr warning only and never fails the transport call itself. This channel never carries key material, and nothing about it reaches stdout.

**Not established by the code:** behavior on a key file containing non-whitespace control bytes is unspecified beyond the internal-whitespace check (see API Key Handling); the retry-once-on-model-error path is not itself retried, so a resolution query that itself fails transiently is not retried.

## API Key Handling

`API_KEY_FILE` names a file whose entire content, leading/trailing whitespace trimmed, is the key. The key is invalid — a usage error, exit 1 — if it is empty after trimming or contains any internal whitespace.

- `post-openai.py` is the only file in this package that ever opens the key file. `msg-util.py` and `relay-driver.py` never read it; `relay-driver.py` passes `API_KEY_FILE`'s path through opaquely and never reads or prints key material itself.
- The key is read directly into process memory (`Path.read_text`) and is never placed on the command line and never written into an environment variable — so it is not exposed through `argv` or through process-environment inspection.
- The only place the key leaves process memory is the `Authorization: Bearer <token>` header, sent on the `/chat/completions` and `/models` requests over the transport rules stated above (https-or-loopback, no redirects followed).
- No file in this package, and no test fixture under `tests/`, is permitted to hold a real key; `tests/test-fixture-*.txt` fixtures used by `test_post_openai.py` are synthetic.

## Messages-File Format and Legal Mutations (`msg-util.py`)

**Format.** A messages file is a JSON array of turn objects `{"role": <string>, "content": <string>}`. A well-formed session (checkable by `validate`) additionally requires: the array is non-empty, every turn's `role` is one of `system`, `user`, `assistant`, every turn's `content` is a string, and turn 0's role is `system`.

**The three modes are the only legal mutations:**
- `init --system-prompt=<file> --instructions=<file> <messages.json>` — overwrites the target unconditionally with exactly a 2-turn array: `{"role": "system", "content": <system-prompt file content>}`, `{"role": "user", "content": <instructions file content>}`.
- `append --role=<user|agent> <messages.json> <content-file>` — appends exactly one turn to the end of the array, `content` equal to the full content of `<content-file>`. `--role=user` maps to JSON role `user`; `--role=agent` maps to JSON role `assistant`. (`agent` is a CLI-only spelling — it never appears as a role value inside the file.)
- `validate <messages.json>` — reads only; exits 0 and prints `valid: <N> turns` for a well-formed session per the Format rule above, otherwise exits 1 with a diagnostic naming the specific defect (not valid JSON, wrong top-level type, empty array, a turn missing a legal role, non-string content, or turn 0 not `system`).

**Concurrency.** `init` and `append` take an exclusive lock (`flock` on `<messages-file>.lock`, waited for up to 10 seconds) spanning the entire read-modify-write, so two concurrent mutators against the same file never race. The lock is held by an open descriptor, so the operating system releases it when its holder exits by any route — a mutator that dies mid-write leaves no lock behind and never has to be unwedged by hand. The lock file itself is created once and never removed; it is empty, and its presence says nothing about whether the lock is held. `validate` takes no lock: every mutation writes to a scratch temp file on the same filesystem and replaces the target with it atomically, so an unlocked reader observes either the pre- or post-mutation file in full, never a partial write. A scratch directory on a *different* filesystem is refused rather than degraded to a copy-plus-unlink.

**Exit codes.** Unlike `post-openai.py`, `msg-util.py` carries only a binary contract: 0 is success, any non-zero (in practice always 1) is failure — there is no finer-grained code for "usage error" vs. "lock timeout" vs. "validation failure" beyond what the stderr text names. The help forms (`help`, `-h`, `--help`) print the usage block and exit 1 like any other usage error.

**Not established by the code:** there is no upper bound on turn `content` size, no check that `user`/`assistant` turns alternate after the initial system turn, and no schema version field — a caller cannot distinguish "this file's shape predates a future format change" from "this file is simply well-formed" beyond the fields checked above.

**Encoding is UTF-8, uniformly.** Every file all three modes touch — the system-prompt, instructions and content files, and the messages file itself — is read and written as UTF-8 explicitly, so the three modes cannot disagree about whether the same file is well-formed text on a platform whose default encoding is something else. A source file that is not valid UTF-8 is a failure naming the file, not a silent substitution.

## `relay-driver.py` — CLI Contract

`relay-driver.py` is invoked directly (not dispatched as an agent). Its contract:

- **Required flags:** `--corpus-root`, `--system-prompt`, one of `--question`/`--questions-file`, `--output-dir`.
- **Connection parameters** (`API_BASE_URL`, `API_KEY_FILE`, `MODEL`, and `post-openai.py`'s optional connection flags) are inherited from the process environment, or injected via `--env-file` (`KEY=VALUE` lines). An `--env-file` may set only the keys in `CONNECTION_ENV_KEYS` — `API_BASE_URL`, `API_KEY_FILE`, `MODEL`, `MAX_TOKENS`, `ENABLE_THINKING`, `TEMPERATURE`, `DEBUG_POST`, `DEBUG_RESPONSE` — any other key is a hard refusal naming the offending key(s), never a silent pass-through.
- **Path confinement is a trust boundary.** Every `READ`/`LIST`/`GREP` request the guest model issues is resolved against the corpus root and required (resolve-then-`relative_to`) to stay inside it; a path that cannot even be resolved (embedded NUL, over `PATH_MAX`) is refused the same way a traversal or symlink escape is. This holds per-file during a `GREP` walk as well, not only at the top-level path argument.
- **Output layout**, entirely under `--output-dir`:
  ```
  session/qNN/messages.json    full conversation (audit-permanent)
  session/qNN/usage.jsonl      USAGE_STATS_FILE side channel, one line per call
  session/qNN/tmp/             scratch (msg-util.py content files, TMPDIR)
  answers/qNN.md               the guest's FINAL report, or a liaison note if none was produced
  answers/stats.csv            per-question round trips, token totals, outcome
  ```
  This driver creates no files anywhere else.
- **Exit codes:** 0 if every question completed without a halting transport failure; 1 if any question halted the run (transport failure exhausted its retries — remaining questions are skipped); argument-parsing errors (`argparse`) exit 2 by argparse's own convention.

## Session Directory Layout — a caller convention

The `guest-session/<topic>/` layout (`messages.json` + `tmp/`) is documented in the `guest-liaison` agent definition; `mad-guest-liaison` uses a different one over the same tools — `liaison-messages.json` + `tmp/` inside whatever run directory its referee hands it, which differs between a review run and a design run. **Neither directory name, nor the `<topic>` segment, nor the file name `messages.json` vs. `liaison-messages.json`, is asserted or checked by any file in this package.** `msg-util.py` and `post-openai.py` operate on whatever path they are given — with the one derived name a caller must expect: a mutation creates `<messages-file>.lock` beside its target and leaves it there (Concurrency, above). A future caller chooses its own shape without touching this package, and nothing here constrains that choice.
