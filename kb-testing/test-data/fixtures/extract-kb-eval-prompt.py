#!/usr/bin/env python3
"""Extract and substitute the KB inferential-quality eval instrument's prompt body.

Used by `kb-headless-eval` (kb-testing/justfile). A standalone script rather
than an inline heredoc: `just` requires every line of a recipe body —
including a heredoc's terminator — to carry the recipe's own leading-
whitespace prefix, which a bash heredoc's own terminator rules cannot
satisfy. Keeping this logic in a real file also makes it runnable and
testable on its own, outside any recipe.

Argv: <brief_path> <kb_root_path> <report_path> <corpus_note>
Stdout: the substituted prompt body. Stderr + exit 1: any refusal below.
"""

import sys

START_MARKER = "---- PROMPT BODY BELOW"
END_MARKER = "---- PROMPT BODY ABOVE"

# The header's "<run-tag>" (angle brackets, in its output-filename naming
# convention prose) is documentation only, never a fourth placeholder —
# this list is exhaustive and deliberately does not include it.
PLACEHOLDERS = ("{kb-root-path}", "{output-report-path}", "{corpus-note}")


def fail(message: str) -> None:
    sys.stderr.write(f"error: {message}\n")
    sys.exit(1)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        fail(f"expected 4 arguments (brief_path kb_root_path report_path corpus_note), got {len(argv)}")
    brief_path, kb_root, report_path, corpus_note = argv

    lines = open(brief_path, encoding="utf-8").readlines()

    start_idx = next((i for i, line in enumerate(lines) if line.startswith(START_MARKER)), None)
    if start_idx is None:
        fail(f"no line matching '^{START_MARKER}' in {brief_path} — the fixture's shape is a contract; refusing.")

    # The end boundary is optional today and forward-compatible: a future
    # fixture revision may add a closing marker so content appended after
    # it (changelog, version table) stays out of the prompt. Until then,
    # extraction runs to EOF. Searched only after start_idx so header prose
    # above the start marker can never be mistaken for it.
    end_idx = next((i for i, line in enumerate(lines) if i > start_idx and line.startswith(END_MARKER)), None)

    body = "".join(lines[start_idx + 1 : end_idx]).lstrip("\n")

    # Substitution is single-pass and values are never rescanned: a value
    # that itself contains a placeholder-shaped string would sit there
    # unexpanded, or — depending on substitution order — swallow another
    # placeholder's slot, rather than error. A stray "{" or "}" in any
    # value refuses instead of risking a silently corrupted prompt.
    for name, value in (("kb-root-path", kb_root), ("output-report-path", report_path), ("corpus-note", corpus_note)):
        if "{" in value or "}" in value:
            brace = "{" if "{" in value else "}"
            fail(f"{name} value contains a stray '{brace}' — refusing. Value: {value!r}")

    missing = [p for p in PLACEHOLDERS if p not in body]
    if missing:
        fail(f"prompt body missing placeholder(s): {', '.join(missing)} — refusing rather than launch a malformed eval.")

    body = body.replace("{kb-root-path}", kb_root)
    body = body.replace("{output-report-path}", report_path)
    body = body.replace("{corpus-note}", corpus_note)
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
