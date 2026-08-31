#!/usr/bin/env python3
"""relay_driver.py — the corpus-relay eval instrument.

Runs budgeted, fresh-history question/answer conversations against a guest
model reached through ``post-openai.sh``, servicing READ/LIST/GREP requests
against a read-only corpus. Promoted from the docent-headroom retrieval-test
driver; deliberately the concrete corpus-relay instrument, not a generalized
pluggable-protocol conversation framework.

Contract highlights:

- **The instrument owns the protocol grammar, both sides.** The relay
  protocol text lives in exactly one place — ``PROTOCOL_BLOCK`` below. The
  driver appends that block (clearly delimited) to the caller's system
  prompt, and its classifier services exactly the grammar the block
  describes. Callers supply navigation doctrine only.
- **Token usage** comes from ``post-openai.py``'s ``USAGE_STATS_FILE`` side
  channel (one JSON line per successful call), set per question inside the
  question's session directory and aggregated into ``stats.csv``. Debug
  streams are never scraped.
- **Composition**: ``post-openai.sh`` is the only transport, ``msg-util.sh``
  the only messages-file mutator; both are discovered as siblings of this
  file. All session state lands under ``--output-dir`` — this driver creates
  no files anywhere else.
- **Path confinement**: every serviced path is resolved and required to stay
  inside the resolved corpus root (a trust boundary — traversal and symlink
  escapes are refused).

INVARIANT: stdlib only. No third-party dependencies. Ever.

usage sketch:
  relay_driver.py --corpus-root kb-root --system-prompt doctrine.md \\
      --questions-file questions.md --question-number all \\
      --output-dir eval-out --env-file guest.env

Connection env vars (``API_BASE_URL``, ``MODEL``, ``API_KEY_FILE``) are
``post-openai.py``'s contract, inherited from the environment or injected
via ``--env-file`` (KEY=VALUE lines). The API key file path is passed
through opaquely; this driver never reads or prints key material.

Output layout under ``--output-dir`` (the as-run qNN structure):
  session/qNN/messages.json    full conversation (audit-permanent)
  session/qNN/usage.jsonl      USAGE_STATS_FILE side channel, one line/call
  session/qNN/tmp/             scratch (msg-util content files, TMPDIR)
  answers/qNN.md               the guest's FINAL report (or a liaison note)
  answers/stats.csv            per-question round trips, token totals, outcome
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__version__ = "1.0.0"

_TOOLS_DIR = Path(__file__).resolve().parent
MSG_UTIL = _TOOLS_DIR / "msg-util.sh"
POST_OPENAI = _TOOLS_DIR / "post-openai.sh"

# --- Protocol (single home, both sides) -------------------------------------
#
# REQUEST_LINE_RE (the classifier grammar) and PROTOCOL_BLOCK (the text the
# guest is shown) are both derived from _REQUEST_VERBS / FINAL_MARKER /
# MIN_REQUEST_LINES / MAX_REQUEST_LINES below, so they agree by construction
# and cannot drift apart. Change the constants, never one side alone.

FINAL_MARKER = "FINAL"
TOOL_CALLS_MARKER = "TOOL_CALLS"
MIN_REQUEST_LINES = 1
MAX_REQUEST_LINES = 3
_REQUEST_VERBS = ("READ", "LIST", "GREP")

REQUEST_LINE_RE = re.compile(rf"^({'|'.join(_REQUEST_VERBS)}):\s*(.*)$")

PROTOCOL_DELIMITER = "=== RELAY PROTOCOL (appended and serviced by relay_driver) ==="

PROTOCOL_BLOCK = f"""{PROTOCOL_DELIMITER}
Every reply you send must be exactly one of the following two shapes.

1. A request: {MIN_REQUEST_LINES} to {MAX_REQUEST_LINES} lines, nothing else, each line exactly one of
   {_REQUEST_VERBS[0]}: <path>      — the contents of one file (path relative to the corpus root)
   {_REQUEST_VERBS[1]}: <dir>       — a one-level listing of one directory (relative to the corpus root)
   {_REQUEST_VERBS[2]}: <pattern>   — a literal (non-regex), case-sensitive substring search over the whole corpus

2. A final report: the first non-empty line is exactly {FINAL_MARKER}, followed by your complete answer.

Requests are serviced from a limited budget of request-replies; you will be
warned when the budget runs low and cut off when it is exhausted. A reply
that is neither a well-formed request nor a {FINAL_MARKER} report is not
serviced — you will be shown this protocol again and asked to retry."""

# Forced-FINAL attempts after budget exhaustion before the driver gives up
# on a question (as-run semantics, preserved).
FORCED_FINAL_ATTEMPTS = 2

# Base delay for exponential backoff between transport retries (seconds);
# attempt N waits RETRY_BACKOFF_SECONDS * 2**N.
RETRY_BACKOFF_SECONDS = 1.0


def build_system_prompt(doctrine_text: str) -> str:
    """Caller doctrine + the delimited protocol block — the only system
    prompt shape this driver ever sends."""
    return doctrine_text.rstrip("\n") + "\n\n" + PROTOCOL_BLOCK + "\n"


def malformed_reminder() -> str:
    """The retry nudge for unparseable replies. Quotes the same
    PROTOCOL_BLOCK constant the system prompt carries — never a copy."""
    return (
        "Liaison: could not parse your reply as a valid request or "
        f"{FINAL_MARKER} report. Reminder of the relay protocol:\n\n" + PROTOCOL_BLOCK
    )


def classify(response: str):
    """Classify one guest reply.

    Returns one of:
      ('tool_calls', [name, ...])       — raw reply starts with TOOL_CALLS
      ('final', None)                   — first non-empty line is FINAL
      ('request', [(verb, arg), ...])   — 1..MAX_REQUEST_LINES request lines
      ('malformed', None)               — anything else
    """
    if response.startswith(TOOL_CALLS_MARKER):
        names = re.findall(r'"name"\s*:\s*"([^"]+)"', response)
        return "tool_calls", names or ["<unknown>"]
    nonempty = [ln for ln in response.strip("\n").split("\n") if ln.strip() != ""]
    if nonempty and nonempty[0].strip() == FINAL_MARKER:
        return "final", None
    if MIN_REQUEST_LINES <= len(nonempty) <= MAX_REQUEST_LINES:
        parsed = []
        for ln in nonempty:
            m = REQUEST_LINE_RE.match(ln.strip())
            if not m:
                break
            parsed.append((m.group(1), m.group(2).strip()))
        else:
            return "request", parsed
    return "malformed", None


# --- Corpus servicing (path confinement is a trust boundary) -----------------


def resolve_corpus_path(root: Path, raw: str) -> tuple[Path | None, str | None]:
    """Resolve a request path against the corpus root. Returns (path, error).

    Resolve-then-relative_to against the resolved root: traversal and
    symlink escapes both normalize outside the root and are refused.
    """
    root = root.resolve()
    raw = raw.strip()
    if not raw:
        return None, "empty path"
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"path '{raw}' resolves outside the corpus root; refused"
    return candidate, None


def service_read(root: Path, path_raw: str, max_read_bytes: int) -> str:
    p, err = resolve_corpus_path(root, path_raw)
    if err:
        return f"READ: {path_raw}\nError: {err}"
    if not p.exists():
        return f"READ: {path_raw}\nError: not found in corpus."
    if p.is_dir():
        return f"READ: {path_raw}\nError: '{path_raw}' is a directory, not a " f"file. Use LIST: {path_raw} instead."
    try:
        data = p.read_bytes()
    except OSError as e:
        return f"READ: {path_raw}\nError: could not read file: {e}"
    truncated = len(data) > max_read_bytes
    if truncated:
        data = data[:max_read_bytes]
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        text = data.decode("latin-1", errors="replace")
    suffix = (
        f"\n\n[TRUNCATED — file exceeds {max_read_bytes} bytes, " f"showing first {max_read_bytes} bytes]"
        if truncated
        else ""
    )
    return f"Here is the content of {path_raw}:\n\n{text}{suffix}"


def service_list(root: Path, dir_raw: str) -> str:
    p, err = resolve_corpus_path(root, dir_raw)
    if err:
        return f"LIST: {dir_raw}\nError: {err}"
    if not p.exists():
        return f"LIST: {dir_raw}\nError: not found in corpus."
    if not p.is_dir():
        return f"LIST: {dir_raw}\nError: '{dir_raw}' is a file, not a " f"directory. Use READ: {dir_raw} instead."
    entries = sorted(p.iterdir(), key=lambda e: e.name)
    lines = [e.name + "/" if e.is_dir() else e.name for e in entries]
    body = "\n".join(lines) if lines else "(empty directory)"
    return f"Listing of {dir_raw}:\n\n{body}"


def service_grep(root: Path, pattern: str, max_grep_matches: int) -> str:
    root = root.resolve()
    pattern = pattern.strip()
    if not pattern:
        return "GREP: \nError: empty pattern"
    matches: list[str] = []
    for walk_root, dirs, files in os.walk(root):
        dirs.sort()
        for fname in sorted(files):
            fpath = Path(walk_root) / fname
            try:
                with open(fpath, "r", encoding="utf-8", errors="strict") as f:
                    for lineno, line in enumerate(f, start=1):
                        if pattern in line:
                            relp = fpath.relative_to(root).as_posix()
                            matches.append(f"{relp}:{lineno}: {line.rstrip(chr(10))}")
                            if len(matches) >= max_grep_matches:
                                break
            except (UnicodeDecodeError, OSError):
                continue
            if len(matches) >= max_grep_matches:
                break
        if len(matches) >= max_grep_matches:
            break
    if not matches:
        return f"GREP: {pattern}\nNo matches found."
    header = f'GREP results for pattern "{pattern}" ' f"(showing up to {max_grep_matches} matches):"
    return header + "\n\n" + "\n".join(matches)


# --- Question / env-file / usage plumbing ------------------------------------

_QUESTION_MARK_RE = re.compile(r"^\*\*Q(\d+)\.\*\*", re.M)


def load_questions(text: str) -> dict[int, str]:
    """Extract ``**Qn.** ...`` blocks from a questions file: number → text."""
    marks = list(_QUESTION_MARK_RE.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[int(m.group(1))] = text[m.end() : end].strip()
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines (blank / ``#`` comment lines skipped, optional
    leading ``export``, optional matching surrounding quotes on the value)."""
    env: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{lineno}: not a KEY=VALUE line: {raw!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            raise ValueError(f"{path}:{lineno}: empty key: {raw!r}")
        env[key] = value
    return env


def aggregate_usage(usage_path: Path) -> tuple[int | None, int | None]:
    """Sum prompt/completion tokens over a USAGE_STATS_FILE JSONL.

    A total is None (reported as "NA") only when no line contributed a
    numeric value for that field.
    """
    prompt_total: int | None = None
    completion_total: int | None = None
    if not usage_path.exists():
        return None, None
    for line in usage_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = obj.get("prompt_tokens")
        c = obj.get("completion_tokens")
        if isinstance(p, int):
            prompt_total = (prompt_total or 0) + p
        if isinstance(c, int):
            completion_total = (completion_total or 0) + c
    return prompt_total, completion_total


# --- The driver --------------------------------------------------------------

# post_fn(messages_file, tmpdir, env) -> (returncode, stdout, stderr)
PostFn = Callable[[Path, Path, dict], tuple[int, str, str]]


def _sh(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _post_openai_subprocess(msgs_file: Path, tmpdir: Path, env: dict) -> tuple[int, str, str]:
    r = _sh([str(POST_OPENAI), str(msgs_file)], env)
    return r.returncode, r.stdout, r.stderr


@dataclass
class RunConfig:
    corpus_root: Path
    system_prompt_file: Path
    output_dir: Path
    budget: int = 12
    warn_at: int = 3
    max_read_bytes: int = 40960
    max_grep_matches: int = 50
    max_round_trips: int = 40
    retries: int = 3
    pace: float = 0.0
    base_env: dict = field(default_factory=lambda: dict(os.environ))


@dataclass
class QuestionResult:
    tag: str
    round_trips: int
    wall_seconds: float
    outcome: str
    final_text: str | None
    halt_run: bool
    anomalies: list


class RelayDriver:
    """One relay run: N fresh-history questions over one corpus/endpoint."""

    def __init__(self, config: RunConfig, post_fn: PostFn | None = None):
        self._config = config
        self._post_fn = post_fn or _post_openai_subprocess
        self._transport_calls = 0
        self.session_dir = config.output_dir / "session"
        self.answers_dir = config.output_dir / "answers"

    # -- subprocess plumbing (msg-util.sh is the only messages mutator) ------

    def _env(self, tmpdir: Path) -> dict:
        env = dict(self._config.base_env)
        env["TMPDIR"] = str(tmpdir)
        return env

    def _msg_init(self, msgs_file: Path, sys_prompt_file: Path, instructions_file: Path, tmpdir: Path) -> None:
        r = _sh(
            [
                str(MSG_UTIL),
                "init",
                f"--system-prompt={sys_prompt_file}",
                f"--instructions={instructions_file}",
                str(msgs_file),
            ],
            self._env(tmpdir),
        )
        if r.returncode != 0:
            raise RuntimeError(f"msg-util init failed: {r.stderr}")

    def _msg_append(self, msgs_file: Path, role: str, content: str, tmpdir: Path) -> None:
        tmpdir.mkdir(parents=True, exist_ok=True)
        cf = tmpdir / f"append-{role}-{time.time_ns()}.txt"
        cf.write_text(content)
        r = _sh(
            [str(MSG_UTIL), "append", f"--role={role}", str(msgs_file), str(cf)],
            self._env(tmpdir),
        )
        if r.returncode != 0:
            raise RuntimeError(f"msg-util append failed: {r.stderr}")

    def _post(self, msgs_file: Path, tmpdir: Path, usage_file: Path) -> tuple[int, str, str]:
        env = self._env(tmpdir)
        env["USAGE_STATS_FILE"] = str(usage_file)
        if self._config.pace > 0 and self._transport_calls > 0:
            time.sleep(self._config.pace)
        self._transport_calls += 1
        return self._post_fn(msgs_file, tmpdir, env)

    def _post_with_retries(
        self, msgs_file: Path, tmpdir: Path, usage_file: Path, qtag: str, round_trip: int
    ) -> tuple[int, str, str]:
        """One logical transport call, retried with exponential backoff on
        non-zero exit. Transient failures burn retries; after the last
        attempt fails, the failure is returned and the run halts."""
        retries = self._config.retries
        rc, stdout, stderr = self._post(msgs_file, tmpdir, usage_file)
        attempt = 0
        while rc != 0 and attempt < retries:
            delay = RETRY_BACKOFF_SECONDS * (2**attempt)
            attempt += 1
            sys.stderr.write(
                f"warning: transport failure on {qtag} round {round_trip} "
                f"(attempt {attempt} of {retries + 1}); retrying in {delay:g}s\n"
            )
            time.sleep(delay)
            rc, stdout, stderr = self._post(msgs_file, tmpdir, usage_file)
        return rc, stdout, stderr

    # -- the conversation loop ----------------------------------------------

    def run_question(self, qtag: str, question_text: str) -> QuestionResult:
        cfg = self._config
        qdir = self.session_dir / qtag
        tmpdir = qdir / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        msgs_file = qdir / "messages.json"
        usage_file = qdir / "usage.jsonl"

        sys_prompt_file = tmpdir / "sys-prompt.md"
        sys_prompt_file.write_text(build_system_prompt(cfg.system_prompt_file.read_text()))
        init_msg_file = tmpdir / "init-msg.md"
        init_msg_file.write_text(
            f"All {'/'.join(_REQUEST_VERBS)} paths are relative to the corpus "
            f"root. You have a budget of {cfg.budget} request-replies for "
            f"this question. Question: {question_text}"
        )
        self._msg_init(msgs_file, sys_prompt_file, init_msg_file, tmpdir)

        round_trips = 0
        budget_used = 0
        forced_final_attempts = 0
        outcome = None
        final_text = None
        halt_run = False
        anomalies: list[str] = []
        t0 = time.time()

        while True:
            # LOOP-TOP guard: bounds every conversation shape — including
            # permanently-malformed and TOOL_CALLS loops, which never touch
            # the request budget. (The as-run driver only guarded the
            # serviced-request branch.)
            if round_trips >= cfg.max_round_trips:
                outcome = "exhausted"
                break
            round_trips += 1

            rc, stdout, stderr = self._post_with_retries(msgs_file, tmpdir, usage_file, qtag, round_trips)
            if stderr.strip():
                anomalies.append(f"{qtag} round {round_trips}: {stderr.strip()}")
            if rc != 0:
                outcome = "error"
                halt_run = True
                break

            self._msg_append(msgs_file, "agent", stdout, tmpdir)

            kind, payload = classify(stdout)

            if kind == "final":
                outcome = "final"
                final_text = stdout.strip("\n")
                break

            if kind == "tool_calls":
                # Stub-and-continue, verbatim from the as-run driver.
                # Load-bearing: a guest fabricated never-relayed file content
                # after a stubbed tool-call turn in live runs — the stub text
                # is the hardening layer, do not "improve" it away.
                stub = "\n".join(f"Tool call {nm} is not available in this environment." for nm in payload)
                self._msg_append(msgs_file, "user", stub, tmpdir)
                continue

            if kind == "malformed":
                # Does not consume request budget.
                self._msg_append(msgs_file, "user", malformed_reminder(), tmpdir)
                continue

            # kind == "request"
            if budget_used >= cfg.budget:
                if forced_final_attempts >= FORCED_FINAL_ATTEMPTS:
                    outcome = "exhausted"
                    break
                forced_final_attempts += 1
                demand = (
                    f"Liaison: budget exhausted ({cfg.budget}/{cfg.budget} "
                    "request-replies used). No further file requests will be "
                    f"serviced. Provide your {FINAL_MARKER} report now, based "
                    "solely on what you have already been shown."
                )
                self._msg_append(msgs_file, "user", demand, tmpdir)
                continue

            parts = []
            for verb, arg in payload:
                if verb == "READ":
                    parts.append(service_read(cfg.corpus_root, arg, cfg.max_read_bytes))
                elif verb == "LIST":
                    parts.append(service_list(cfg.corpus_root, arg))
                elif verb == "GREP":
                    parts.append(service_grep(cfg.corpus_root, arg, cfg.max_grep_matches))
            budget_used += 1
            remaining = cfg.budget - budget_used
            if remaining == 0:
                parts.append(
                    "[Liaison: budget exhausted — that was your last "
                    f"request-reply. Your next reply must be {FINAL_MARKER}.]"
                )
            elif remaining == cfg.warn_at:
                parts.append(f"[Liaison: {remaining} request-replies remain out of " f"your budget of {cfg.budget}.]")
            self._msg_append(msgs_file, "user", "\n\n---\n\n".join(parts), tmpdir)

        return QuestionResult(
            tag=qtag,
            round_trips=round_trips,
            wall_seconds=round(time.time() - t0, 2),
            outcome=outcome or "exhausted",
            final_text=final_text,
            halt_run=halt_run,
            anomalies=anomalies,
        )

    # -- the run --------------------------------------------------------------

    def run(self, questions: list[tuple[str, str]]) -> int:
        cfg = self._config
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.answers_dir.mkdir(parents=True, exist_ok=True)

        stats_path = self.answers_dir / "stats.csv"
        write_header = not stats_path.exists() or stats_path.stat().st_size == 0
        summary = []
        anomalies: list[str] = []
        halted = False

        with open(stats_path, "a", newline="") as stats_f:
            stats_w = csv.writer(stats_f)
            if write_header:
                stats_w.writerow(
                    [
                        "question",
                        "round_trips",
                        "prompt_tokens_total",
                        "completion_tokens_total",
                        "wall_seconds",
                        "outcome",
                    ]
                )
                stats_f.flush()

            for qtag, question_text in questions:
                res = self.run_question(qtag, question_text)
                anomalies.extend(res.anomalies)

                prompt_total, completion_total = aggregate_usage(self.session_dir / qtag / "usage.jsonl")

                report_path = self.answers_dir / f"{qtag}.md"
                if res.final_text is not None:
                    report_path.write_text(res.final_text + "\n")
                else:
                    report_path.write_text(
                        f"[Liaison note: no {FINAL_MARKER} report was produced "
                        f"for {qtag}. outcome={res.outcome}, "
                        f"round_trips={res.round_trips}.]\n"
                    )

                stats_w.writerow(
                    [
                        qtag,
                        res.round_trips,
                        "NA" if prompt_total is None else prompt_total,
                        "NA" if completion_total is None else completion_total,
                        res.wall_seconds,
                        res.outcome,
                    ]
                )
                stats_f.flush()
                summary.append((qtag, res.round_trips, res.wall_seconds, res.outcome))

                if res.halt_run:
                    halted = True
                    sys.stderr.write(
                        f"HALT after {qtag}: transport failed after "
                        f"{cfg.retries + 1} attempt(s) — presuming the "
                        "transport is broken; remaining questions skipped.\n"
                    )
                    break

        print("=== SUMMARY ===")
        for row in summary:
            print(row)
        if anomalies:
            print("=== TRANSPORT ANOMALIES ===")
            for a in anomalies:
                print(a)
        return 1 if halted else 0


# --- CLI ---------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="relay_driver.py",
        description=(
            "Corpus-relay eval instrument: budgeted, fresh-history Q&A "
            "sessions against a guest model over a read-only corpus, with "
            "the READ/LIST/GREP relay protocol owned (appended and serviced) "
            "by this driver."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--corpus-root",
        required=True,
        type=Path,
        help="read-only directory READ/LIST/GREP requests resolve against",
    )
    p.add_argument(
        "--system-prompt",
        required=True,
        type=Path,
        metavar="FILE",
        help=(
            "navigation doctrine only — the relay protocol block is appended "
            "by this driver, never written by the caller"
        ),
    )
    qsrc = p.add_mutually_exclusive_group(required=True)
    qsrc.add_argument("--question", metavar="TEXT", help="a single question, verbatim")
    qsrc.add_argument(
        "--questions-file",
        type=Path,
        metavar="FILE",
        help="markdown file of '**Qn.** ...' blocks",
    )
    p.add_argument(
        "--question-number",
        metavar="N|all",
        default=None,
        help="with --questions-file: one question number, or 'all' (default)",
    )
    p.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="all session state and answers land here (session/ + answers/)",
    )
    p.add_argument("--budget", type=int, default=12, help="request-replies per question (default 12)")
    p.add_argument("--warn-at", type=int, default=3, help="warn when this many request-replies remain (default 3)")
    p.add_argument("--max-read-bytes", type=int, default=40960, help="READ truncation threshold (default 40960)")
    p.add_argument("--max-grep-matches", type=int, default=50, help="GREP match cap (default 50)")
    p.add_argument(
        "--max-round-trips", type=int, default=40, help="hard cap on transport round trips per question (default 40)"
    )
    p.add_argument("--retries", type=int, default=3, help="transport retries with exponential backoff (default 3)")
    p.add_argument(
        "--pace", type=float, default=0.0, metavar="SECONDS", help="minimum delay between transport calls (default 0)"
    )
    p.add_argument(
        "--env-file",
        type=Path,
        metavar="FILE",
        help="KEY=VALUE lines injected into the subprocess environment (e.g. API_BASE_URL, MODEL, API_KEY_FILE)",
    )
    return p


def _select_questions(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.question is not None:
        if args.question_number is not None:
            parser.error("--question-number only applies with --questions-file")
        return [("q01", args.question)]
    try:
        text = args.questions_file.read_text(encoding="utf-8")
    except OSError as e:
        parser.error(f"cannot read --questions-file: {e}")
    questions = load_questions(text)
    if not questions:
        parser.error(f"no '**Qn.**' blocks found in {args.questions_file}")
    selector = args.question_number or "all"
    if selector == "all":
        return [(f"q{n:02d}", questions[n]) for n in sorted(questions)]
    try:
        n = int(selector)
    except ValueError:
        parser.error(f"--question-number must be an integer or 'all' (got '{selector}')")
    if n not in questions:
        parser.error(f"question {n} not found in {args.questions_file}")
    return [(f"q{n:02d}", questions[n])]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.corpus_root.is_dir():
        parser.error(f"--corpus-root is not a directory: {args.corpus_root}")
    if not args.system_prompt.is_file():
        parser.error(f"--system-prompt file not found: {args.system_prompt}")
    for name in ("budget", "max_read_bytes", "max_grep_matches", "max_round_trips"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be >= 1")
    for name in ("warn_at", "retries"):
        if getattr(args, name) < 0:
            parser.error(f"--{name.replace('_', '-')} must be >= 0")
    if args.pace < 0:
        parser.error("--pace must be >= 0")

    questions = _select_questions(parser, args)

    base_env = dict(os.environ)
    if args.env_file is not None:
        if not args.env_file.is_file():
            parser.error(f"--env-file not found: {args.env_file}")
        try:
            base_env.update(parse_env_file(args.env_file))
        except ValueError as e:
            parser.error(str(e))

    config = RunConfig(
        corpus_root=args.corpus_root.resolve(),
        system_prompt_file=args.system_prompt,
        output_dir=args.output_dir,
        budget=args.budget,
        warn_at=args.warn_at,
        max_read_bytes=args.max_read_bytes,
        max_grep_matches=args.max_grep_matches,
        max_round_trips=args.max_round_trips,
        retries=args.retries,
        pace=args.pace,
        base_env=base_env,
    )
    return RelayDriver(config).run(questions)


if __name__ == "__main__":
    sys.exit(main())
