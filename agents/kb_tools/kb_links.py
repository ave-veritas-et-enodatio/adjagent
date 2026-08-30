"""Shared Markdown link-scanning primitives for the KB toolchain.

Single source for the low-level Markdown scanning the repo does in more than
one place: neutralizing code spans, the inline-link regex, target cleanup, and
the file crawl. Both the repo-wide link checker (``verify_md_links.py``) and
the query CLI's on-demand reverse-find (``kb_cmd``) import these so there is
exactly one copy of each primitive.

This is the *primitive* layer only. Link classification and gating (which
links are broken, which gate the exit code) stay in ``verify_md_links.py`` —
that logic is verifier-specific and not shared.

Stdlib only.
"""

import re
from pathlib import Path

# Directories never crawled, matched as a single path segment at any depth.
#   - `.index` holds generated jsonl + format-spec docs.
#   - `.agents` is gitignored ephemeral scratch — must never be linted.
#   - `_archive` (at any depth) is a frozen archive — content is intentionally
#     stale and must not gate or warn (e.g. research/_archive/,
#     _orchestration/_archive/).
SKIP_DIRS = {".venv", "venv", ".git", "build", "node_modules", ".index", ".agents", "_archive"}

# Consecutive path-segment sequences that exclude a file from the crawl,
# matched anywhere in the file's relative path.
#   - `tests/fixtures`: test fixtures (the checker's and the KB tooling's)
#     contain deliberately broken links and placeholder ids; scanning them as
#     real content would fail a repo-wide run on intentional test data.
#   - `.claude/worktrees`: nested git worktrees (gitignored) — scanning them
#     would double-count the repo against itself.
SKIP_SEGMENT_RUNS: tuple[tuple[str, ...], ...] = (
    ("tests", "fixtures"),
    (".claude", "worktrees"),
)

# Markdown inline link: [text](target). Target captured up to first ) or space.
# We deliberately do not try to handle titles `(url "title")`; targets here
# are file paths without titles.
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)\s*\)")


def _contains_run(parts: tuple[str, ...], run: tuple[str, ...]) -> bool:
    """True if ``run`` appears as a consecutive subsequence of ``parts``."""
    return any(parts[i : i + len(run)] == run for i in range(len(parts) - len(run) + 1))


def iter_markdown_files(root: Path):
    """Yield every ``.md`` file under ``root``, skipping SKIP_DIRS at any depth."""
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        if any(_contains_run(parts, run) for run in SKIP_SEGMENT_RUNS):
            continue
        yield path


def strip_code(text: str) -> str:
    """Blank out fenced code blocks and inline code spans, preserving line count.

    Lines inside ``` / ~~~ fences become empty; inline `code` spans are replaced
    by spaces. Newlines are preserved so reported line numbers stay accurate.
    """
    out_lines: list[str] = []
    fence: str | None = None  # active fence marker, "```" or "~~~"
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                out_lines.append("")
                continue
            # Drop inline code spans on this line.
            out_lines.append(re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), raw))
        else:
            # Inside a fence: blank everything until the closing fence.
            out_lines.append("")
            if stripped.startswith(fence):
                fence = None
    return "\n".join(out_lines)


def strip_target(target: str) -> str:
    """Strip a trailing #anchor and a trailing :linenum suffix from a target."""
    target = target.split("#", 1)[0]
    # Strip a trailing :NNN line-number suffix (path/file.md:42).
    target = re.sub(r":\d+$", "", target)
    return target
