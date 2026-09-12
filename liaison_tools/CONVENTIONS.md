# CONVENTIONS – liaison_tools

House rules and traps only, for someone working *inside* `liaison_tools/`.
The contract and its mechanism live in SPEC.md and ARCHITECTURE.md — this
file never restates them, only points at them. Unqualified, those names mean
this directory's copies; the repository-root documents are named as root.

- **These files are edited in place.** `liaison_tools/` is a shipped package
  — never rendered, never bannered here (root SPEC.md, Deployed Surfaces).
  The bannered-definition rule in root `CLAUDE.md` and root `CONVENTIONS.md` does
  not fire, and there is no `templates/` counterpart to go looking for.
- **A capability a caller needs and these tools lack is extended here.**
  `post-openai.py` is the only transport and `msg-util.py` the only
  messages-file mutator any caller uses (SPEC.md, Components). Add the
  capability to the tool; an inline `jq`, `sed`, or python snippet in a
  caller is never the fix, and neither is a second mutator in this package.
- **stdlib-only.** Both python modules state it in their own docstrings, and
  it binds anything added beside them: no third-party dependency, so a
  consumer never needs an out-of-band install step for these tools to run.
- **Key handling stays confined to `post-openai.py`.** SPEC.md, API Key
  Handling, states the containment; these are the edits that would break it
  — key reads added to `msg-util.py` or `relay-driver.py`; `DEBUG_POST`/`DEBUG_RESPONSE` extended to dump the key
  rather than the request payload and response stream; a synthetic
  `tests/test-fixture-*.txt` swapped for a real key file while debugging; a
  `--follow-redirects` escape hatch, which would hand the `Authorization`
  header to an endpoint this code cannot vouch for — that one is still a
  design decision to raise, not a patch to write. `ALLOW_HTTP`/`--allow-http`
  is the one sanctioned exception to the https-or-loopback rule: off by
  default, and recorded in the session's `params.env` when a caller sets it —
  the audit trail for a run that took the exception, rather than a warning on
  every accepting call whose only reader already knows what they set. What
  this bullet protects is narrower than "no plaintext, ever": it is that the
  key is never *read* anywhere but `post-openai.py`, which the http exception
  leaves untouched.
- **A new `post-openai.py` env var is two edits.** `relay-driver.py`'s
  `CONNECTION_ENV_KEYS` is a closed allowlist, so until it names the new key
  `--env-file` refuses it — loudly and correctly, which is exactly why the
  second edit gets forgotten. Make both in one change.
- **The exit codes have three consumers outside this package's tests.**
  `templates/agents/guest-liaison.md.tmpl` and
  `templates/agents/mad-guest-liaison.md.tmpl` (each under "Error Handling",
  inline in both rather than shared), and `relay-driver.py`'s
  `POST_PROTOCOL_EXITS`/`is_transport_failure`. Adding, removing, or
  renumbering a code without all three falsifies a template's "re-ask on
  exit 3" instruction, and nothing under `tests/` reaches a template.
- **In `msg-util.py`: no scratch path that bypasses `scratch_dir`, and no
  `unlink` of the lock file.** ARCHITECTURE.md, Messages-File Mutator
  Internals, gives the mechanism and the bug each one closes — a scratch file
  carries the whole transcript, and a removed lock file puts two mutators in
  one critical section. After touching the lock or scratch paths run `just
  test liaison_tools` — `LC-S1`/`LC-S2`/`LC-S3` are the regression checks for
  exactly these properties, and `just check` covers rendered definitions, not
  this package.
- **`msg-util.py`'s argv surface is a contract, not an implementation
  detail.** Two agent definitions invoke it by command line (SPEC.md, the
  three modes), so the mode names, the `=<value>` flag spellings, the binary
  exit status and the `error: ...` stderr lines are all caller-visible.
  Reaching for `argparse` changes all four at once — that is why the parsing
  is hand-rolled.
- **Nothing here may assume a session-directory shape.** The two callers run
  different layouts over the same tools (SPEC.md, Session Directory Layout);
  `msg-util.py` and `post-openai.py` each operate on whatever path they are
  handed. Baking in a directory name or file name
  breaks the other caller silently.
- **`just format-python` covers this package** — black at line length 120,
  then isort. Run it after editing any module here — every file in this
  package is python, so nothing is outside those passes.
