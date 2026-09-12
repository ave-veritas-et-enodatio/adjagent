# ARCHITECTURE – liaison_tools

How the helpers in `liaison_tools/` are built to satisfy SPEC.md. Cites SPEC's requirements rather than restating them; where this document and SPEC disagree, SPEC wins and this document is the defect.

## Module Inventory

| Module | Role |
|---|---|
| `post-openai.py` | The wire transport. Builds and POSTs one SSE-streamed chat-completions request, demuxes and reassembles the stream, classifies the outcome (complete / incomplete / empty / error), and emits the canonical stdout contract. |
| `msg-util.py` | The messages-file mutator. Three modes (`init`/`append`/`validate`) over a JSON turn array, with a lock + scratch-file + atomic-replace discipline protecting concurrent read-modify-write. |
| `relay-driver.py` | The corpus-relay eval instrument. Composes `post-openai.py` and `msg-util.py` into a scripted, budgeted READ/LIST/GREP question-answering loop against a read-only corpus. |
| `__init__.py` | Package marker only — the tools are shell-invoked, never imported, and this file exists so `tests/` collects under a stable module path. |
| `tests/` | The verification suite (see Test Layout, below); excluded from the shipped-package install per root `SPEC.md`. |

Root `ARCHITECTURE.md`'s "Liaisons + liaison_tools" bullet (Subsystem Map) states the one-paragraph version of the composition this document expands: `guest-liaison.md` and `mad-guest-liaison.md` share these helpers rather than each reimplementing the wire protocol or the messages-file format, and `relay-driver.py` composes the same two helpers into a separate, scripted loop.

## Wire Transport Internals (`post-openai.py`)

**One opener, redirects refused.** `_OPENER = urllib.request.build_opener(_NoRedirectHandler)` is the single `urllib` opener every request in the module goes through. `_NoRedirectHandler.redirect_request` raises `RedirectRefused` unconditionally rather than following a `3xx` — the mechanism behind SPEC's "redirects are never followed" guarantee, because `urllib`'s default handler copies request headers, `Authorization` included, to the redirect target.

**SSE demux (`demux_sse`).** Reads a line iterator, ignores blank lines and `:`-prefixed comments, and only inspects `data:`-prefixed lines. `[DONE]` ends the stream cleanly; a line whose parsed JSON carries an `error` object is reported to stderr immediately and short-circuits with status 2. Returns `(chunks, status)` where status is 0 (clean), 1 (no data events at all), 2 (mid-stream error), or 3 (stream ended without `[DONE]` but had data). `chunk_payloads`, when supplied, collects each payload string verbatim for `DEBUG_RESPONSE` dumps; `raw_buffer` collects every raw line for error reporting.

**Reassembly (`reassemble_stream`).** Accumulates `delta.content` strings into one buffer and `delta.tool_calls` into a dict keyed by the API's own `index`, merging `id`/`type`/`function.name` (last-non-null wins) and concatenating `function.arguments` fragments in arrival order. If any tool-call slots were populated, the output is `TOOL_CALLS\n` plus the JSON-serialized ordered list; otherwise it is the concatenated content text. Its docstring names the bash embedded-python implementation it ports; this is now the only reassembler in the tree.

**Usage and finish-reason extraction.** `extract_usage` and `extract_finish_reason` both do a last-object-wins scan over every parsed chunk, which is what lets one function cover both response shapes without branching: a non-streaming full completion (usage beside `choices` at the top level) and an SSE stream, where usage typically rides a late chunk with empty `choices` — the shape `reassemble_stream` itself skips — or the final delta.

**Model-not-found retry.** `is_model_error_text` sniffs a pre-stream HTTP body for a model-error shape (JSON-aware first: `error.code`/`error.message` or `detail`; falls back to a plain-text regex). On an exit-1 result whose body matches, `main()` calls `resolve_model` (a `GET /models` lookup, exact-or-unambiguous-substring match) and retries the whole streaming call once against the resolved name, emitting warnings either way. This retry is not attempted a second time if it also fails.

**Exit-code assembly.** `COMPLETE_FINISH_REASONS = {"stop", "tool_calls", "function_call"}` is the sole table `main()` checks a `finish_reason` against; anything else marks the reply `incomplete`. The usage side-channel write happens before the incomplete/empty branch is evaluated — deliberately, per the inline comment, because the call completed and the tokens were spent regardless of the reply's usability.

## Messages-File Mutator Internals (`msg-util.py`)

**Lock: `flock`, released by the kernel.** `exclusive_lock` opens `<target>.lock` and takes `fcntl.flock(fd, LOCK_EX | LOCK_NB)` in a poll bounded at `LOCK_TIMEOUT_SECONDS`, holding it for the whole read-modify-write and releasing it by closing the descriptor in a `finally`. The choice is about what happens when a mutator *dies*: the operating system drops the lock with the process, so there is no stale-lock apparatus to carry — no orphan to detect, and no diagnostic asking an operator to remove something by hand. Three outcomes stay distinguished, which is the part worth keeping from the shell it replaces: an `os.open` that fails is a missing or unwritable parent directory and is reported immediately; an `flock` that fails with anything but `EAGAIN`/`EWOULDBLOCK`/`EACCES` is a filesystem that cannot lock and is reported immediately; only genuine contention waits, and only until the timeout. The lock file is created once and never unlinked — unlinking it would let a waiter hold the lock on an unlinked inode while a newcomer creates and locks a fresh one, putting two mutators in the same critical section.

**Scratch file placement: `TMPDIR` override.** `scratch_dir` returns `TMPDIR` when the caller set one, otherwise the messages file's own directory — which is always on the target's filesystem. A scratch file here carries the *entire* messages array, so routing it through system-wide temp is both off-limits scratch and typically a different filesystem. `write_messages` closes with `os.replace`, which refuses a cross-filesystem move outright rather than degrading to the copy-plus-unlink `mv` would perform — the operation that can leave a full transcript behind when interrupted. The scratch file is removed in a `finally`, which fires on the error paths and on an unhandled exception alike (this is the shell EXIT trap's replacement; neither survives `SIGKILL`, and under `flock` neither needs to).

**One implementation, three modes.** `init`, `append` and `validate` share `read_text`, `write_messages` and `load_turns`; all JSON goes through `json.load`/`json.dump`, never string concatenation. Every read and write pins `encoding="utf-8"`, so the three modes cannot disagree about whether a file is well-formed text (SPEC.md, Encoding).

**Role mapping (`MAP_ROLE`).** `user` → `user`, `agent` → `assistant`; any other value is a hard error. This is the only place the CLI-facing role name `agent` and the JSON-facing role name `assistant` are translated — `FILE_ROLES` (`system`, `user`, `assistant`), which `validate` checks against, never includes `agent`, because `agent` never appears inside the file.

**Hand-rolled argument parsing, deliberately.** The three modes are dispatched and their flags parsed by hand rather than by `argparse`, because the argv surface is a contract two agent definitions invoke by command line: `argparse` would accept spellings this tool never did (space-separated flag values, prefix abbreviations), exit 2 where the contract says 1, and replace the diagnostics with its own. `UsageError` (reported with the usage block) and `MsgUtilError` (reported alone) are the two exit paths, and `main` is the only place either becomes an exit status — which is what makes "a failed write must not exit 0" (`LC-S1`, below) structural rather than a discipline every branch has to remember.

## `relay-driver.py`: Composition and the Conversation Loop

**Composition, not reimplementation.** `_TOOLS_DIR = Path(__file__).resolve().parent` locates `msg-util.py` and `post-openai.py` as siblings; every messages-file mutation goes through `_msg_init`/`_msg_append` (thin `subprocess.run` wrappers around `msg-util.py`), and every model call goes through `_post`/`_post_with_retries` (wrapping `post-openai.py` by default, with `post_fn` injectable for tests). The module docstring states this as a contract highlight: `post-openai.py` is the *only* transport and `msg-util.py` the *only* messages-file mutator this driver uses. The mutator is now importable, but the driver still spawns it: one invocation shape for every caller, and the subprocess boundary is what keeps the driver honest about using the same argv contract the liaison definitions do.

**One protocol definition, two consumers.** `FINAL_MARKER`, `TOOL_CALLS_MARKER`, `MIN_REQUEST_LINES`/`MAX_REQUEST_LINES`, and `_REQUEST_VERBS` are the sole source constants; `REQUEST_LINE_RE` (what `classify()` parses) and `PROTOCOL_BLOCK` (the text shown to the guest model, appended to the caller's system prompt by `build_system_prompt`) are both derived from them, so the classifier grammar and the text describing that grammar cannot drift apart by construction.

**Framing corpus content against forgery.** `frame_serviced` wraps every serviced READ/LIST/GREP result in `SERVICED_OPEN_DELIMITER`/`SERVICED_CLOSE_DELIMITER` lines and neutralizes any literal occurrence of those delimiter strings *inside* the corpus content itself (replacing `=` with `≡` in an in-content match), so a corpus file cannot forge a frame boundary and impersonate liaison-authored text from inside its own content. Everything the driver authors itself (budget notices, refusals, tool-call stubs) stays outside the frame — that separation is what keeps a delivered file quoting a liaison notice distinguishable from the real notice.

**Bounding guest-chosen text.** `sanitize_echo` flattens control characters (newlines included) to spaces and truncates to `MAX_ECHO_CHARS` (200) before any guest-chosen string — a requested path, a GREP pattern, a tool-call name — is echoed back into driver-authored prose. This is what stops an attacker-chosen string from spanning lines and forging a plausible-looking liaison sentence around itself.

**Path confinement (`resolve_corpus_path`).** Resolves the candidate path against the *resolved* corpus root and requires `relative_to(root)` to succeed — traversal and symlink escapes both normalize outside the root under this check and are refused identically. A path the OS cannot resolve at all (embedded NUL, over `PATH_MAX`) is refused the same way, not treated as an exception to propagate. `service_grep`'s directory walk re-applies this same check per file rather than trusting `os.walk`'s traversal, because a symlink inside the tree can resolve outside it exactly as a crafted path argument can — confinement here is per-file, not per-verb.

**The conversation loop (`run_question`).** A `while True:` loop bounded at its top by `round_trips >= cfg.max_round_trips` — the comment notes this bounds *every* conversation shape, including a permanently-malformed or TOOL_CALLS-looping guest, neither of which touches the request budget at all (a gap the prior driver this was promoted from did not close). Within one round: a transport call (`_post_with_retries`, exponential backoff `RETRY_BACKOFF_SECONDS * 2**attempt`, retried only when `is_transport_failure` — excluding the protocol exits 3/4); classification (`final` / `tool_calls` / `malformed` / `request`); and, on `request`, budget bookkeeping (`budget_used`, a warn line at `cfg.warn_at` remaining, a forced-final demand after exhaustion capped at `FORCED_FINAL_ATTEMPTS` = 2 before giving up).

**TOOL_CALLS stub-and-continue.** A `tool_calls` classification is answered with a stub line per named call (`Tool call <name> is not available in this environment.`) rather than being serviced. The inline comment marks this as load-bearing, not a placeholder: a guest model fabricated never-relayed file content after a stubbed tool-call turn in a live run, so the stub text is a hardening layer against exactly that, not a TODO to "improve away."

**`--env-file` allowlist (`CONNECTION_ENV_KEYS`).** `load_connection_env` parses `KEY=VALUE` lines and refuses any key outside the closed set (`API_BASE_URL`, `API_KEY_FILE`, `MODEL`, `MAX_TOKENS`, `ENABLE_THINKING`, `TEMPERATURE`, `DEBUG_POST`, `DEBUG_RESPONSE`) — the inline comment states the reason: an env file is exactly the kind of thing that gets pasted around, and without this list a "connection env file" could set `PATH`/`PYTHONPATH`/`PYTHONSTARTUP` on the very process that reads the API key.

## Test Layout

| Module | Covers |
|---|---|
| `test_post_openai.py` | SSE demux/reassembly and API-key-format validation against `test-fixture-*.txt` fixtures (offline); an end-to-end subprocess class against a stubbed local `http.server` endpoint, proving the stdout contract byte-for-byte in both shapes. Documented gaps: `/models` listing and substring model-resolution retry, and real mid-stream connection drops — `is_model_error_text`, the pure sniffing those paths pivot on, is covered. |
| `test_msg-util.py` | The three properties an adversarial review found missing (module docstring's own naming): `LC-S1` a failed write must not exit 0, `LC-S2` concurrent appends must not lose turns, `LC-S3` scratch must land on the target's filesystem — plus `LC-M3`, the `validate` verb, and a killed lock holder not blocking the next mutation. |
| `test_relay_driver.py` | The reply classifier, path confinement (traversal/absolute/symlink-escape), the protocol-block append contract, the budget state machine, the loop-top round-trip guard, and READ/LIST/GREP servicing against a fixture corpus; an end-to-end subprocess class against a stubbed SSE endpoint proving a full scripted run (answer file, `stats.csv` with real token totals, `messages.json` shape). |

Per-suite case counts are not recorded here — nothing would check them; `just test liaison_tools` reports the live numbers.

All three suites are stdlib-only and never touch the network beyond `127.0.0.1` (per each module's own docstring). Run via `just test liaison_tools` (or `just test` for the full tooling suite: `kb_tools` + `liaison_tools` + `gen-defs`), which sets `PYTHONPATH` to the repository root so the shipped packages import as top-level packages — the same import shape a consumer gets with `PYTHONPATH=.claude/agents` after install.

## Caller Composition

| Caller | Tools it invokes |
|---|---|
| `guest-liaison.md`, `mad-guest-liaison.md` | `post-openai.py`, `msg-util.py` — invoked directly from the definition body. Frontmatter stripping is not a tool here: each definition runs the `sed` range itself. |
| `relay-driver.py` | `post-openai.py` (sole transport), `msg-util.py` (sole messages-file mutator). |
