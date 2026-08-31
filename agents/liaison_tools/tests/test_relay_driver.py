"""Unit tests for the corpus-relay eval instrument (``liaison_tools/relay_driver.py``).

Covers the reply classifier (FINAL / request / malformed / TOOL_CALLS), path
confinement (traversal, absolute, and symlink-escape attempts), the
protocol-block append contract (system prompt = caller doctrine + the one
delimited ``PROTOCOL_BLOCK`` constant, which the classifier grammar is
derived from), the budget state machine (warn line, exhaustion demand,
2-attempt give-up), the loop-top round-trip guard, and the READ/LIST/GREP
servicing functions against a fixture corpus tree.

Conversation-loop tests inject a scripted ``post_fn`` in place of the real
transport; ``msg-util.sh`` still performs every messages-file mutation, so
the audited ``messages.json`` shape is the real one. The end-to-end class
runs the script as a subprocess against a stubbed local SSE endpoint (the
same ``http.server`` harness pattern as ``test_post_openai.py``), proving a
scripted request → serviced reply → FINAL run end to end: answer file,
``stats.csv`` with real token totals from the ``USAGE_STATS_FILE`` side
channel, and ``messages.json`` shape.

All tests are stdlib-only and never touch the network beyond 127.0.0.1.
"""

import contextlib
import http.server
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

_THIS_DIR = Path(__file__).resolve().parent
_SCRIPT = _THIS_DIR.parent / "relay_driver.py"


def _load_relay_driver():
    """Load relay_driver.py from its file path (house importlib pattern).

    Registered in sys.modules before exec because the module defines
    dataclasses, whose field-type resolution looks the module up by name.
    """
    spec = importlib.util.spec_from_file_location("relay_driver", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rd = _load_relay_driver()

_TOOL_CALLS_REPLY = (
    'TOOL_CALLS\n[{"id": "call_1", "type": "function", ' '"function": {"name": "read_file", "arguments": "{}"}}]\n'
)


def _make_corpus(base: Path) -> Path:
    """A small fixture corpus tree for confinement/servicing tests."""
    root = base / "corpus"
    (root / "sub").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "notes.txt").write_text("alpha line\nliteral a.c here\nabc decoy\n")
    (root / "sub" / "inner.txt").write_text("inner content\n")
    outside = base / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n")
    (root / "escape-link.txt").symlink_to(outside / "secret.txt")
    return root


class TestClassifier(unittest.TestCase):
    """classify() — the driver-side half of the protocol grammar."""

    def test_final_marker_alone_on_first_nonempty_line(self):
        self.assertEqual(rd.classify("FINAL\nThe answer is 42.\n"), ("final", None))

    def test_final_after_leading_blank_lines(self):
        self.assertEqual(rd.classify("\n\nFINAL\nreport body\n"), ("final", None))

    def test_final_with_suffix_is_not_final(self):
        kind, _ = rd.classify("FINAL: the answer\n")
        self.assertEqual(kind, "malformed")

    def test_single_request_line(self):
        self.assertEqual(rd.classify("READ: notes.txt\n"), ("request", [("READ", "notes.txt")]))

    def test_three_mixed_request_lines(self):
        kind, payload = rd.classify("READ: a.md\nLIST: sub\nGREP: needle\n")
        self.assertEqual(kind, "request")
        self.assertEqual(payload, [("READ", "a.md"), ("LIST", "sub"), ("GREP", "needle")])

    def test_request_lines_tolerate_surrounding_blank_lines(self):
        kind, payload = rd.classify("\nREAD: a.md\n\nLIST: sub\n\n")
        self.assertEqual(kind, "request")
        self.assertEqual(payload, [("READ", "a.md"), ("LIST", "sub")])

    def test_four_request_lines_exceed_the_limit(self):
        reply = "READ: a\nREAD: b\nREAD: c\nREAD: d\n"
        self.assertEqual(rd.classify(reply), ("malformed", None))

    def test_mixed_request_and_prose_is_malformed(self):
        self.assertEqual(rd.classify("READ: a.md\nplease and thank you\n"), ("malformed", None))

    def test_unknown_verb_is_malformed(self):
        self.assertEqual(rd.classify("WRITE: a.md\n"), ("malformed", None))

    def test_prose_is_malformed(self):
        self.assertEqual(rd.classify("I think I should look around.\n"), ("malformed", None))

    def test_empty_reply_is_malformed(self):
        self.assertEqual(rd.classify(""), ("malformed", None))

    def test_tool_calls_extracts_function_names(self):
        kind, names = rd.classify(_TOOL_CALLS_REPLY)
        self.assertEqual(kind, "tool_calls")
        self.assertEqual(names, ["read_file"])

    def test_tool_calls_without_parseable_names_stubs_unknown(self):
        kind, names = rd.classify("TOOL_CALLS\n[not json]\n")
        self.assertEqual(kind, "tool_calls")
        self.assertEqual(names, ["<unknown>"])


class TestPathConfinement(unittest.TestCase):
    """resolve_corpus_path — the corpus root is a trust boundary."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = _make_corpus(self.base)

    def test_in_root_relative_path_is_allowed(self):
        p, err = rd.resolve_corpus_path(self.root, "sub/inner.txt")
        self.assertIsNone(err)
        self.assertEqual(p, (self.root / "sub" / "inner.txt").resolve())

    def test_empty_path_is_an_error(self):
        p, err = rd.resolve_corpus_path(self.root, "   ")
        self.assertIsNone(p)
        self.assertEqual(err, "empty path")

    def test_traversal_is_refused(self):
        p, err = rd.resolve_corpus_path(self.root, "../outside/secret.txt")
        self.assertIsNone(p)
        self.assertIn("resolves outside the corpus root", err)

    def test_absolute_path_outside_root_is_refused(self):
        p, err = rd.resolve_corpus_path(self.root, "/etc/passwd")
        self.assertIsNone(p)
        self.assertIn("resolves outside the corpus root", err)

    def test_symlink_escape_is_refused(self):
        p, err = rd.resolve_corpus_path(self.root, "escape-link.txt")
        self.assertIsNone(p)
        self.assertIn("resolves outside the corpus root", err)

    def test_symlink_escape_is_refused_through_service_read(self):
        out = rd.service_read(self.root, "escape-link.txt", 40960)
        self.assertIn("Error:", out)
        self.assertIn("resolves outside the corpus root", out)
        self.assertNotIn("secret", out)


class TestProtocolBlock(unittest.TestCase):
    """One protocol home: the appended block, the reminder, and the
    classifier grammar all come from the same constants."""

    def test_system_prompt_is_doctrine_plus_delimited_protocol(self):
        doctrine = "Navigate top-down. Prefer summaries.\n"
        built = rd.build_system_prompt(doctrine)
        self.assertEqual(
            built,
            doctrine.rstrip("\n") + "\n\n" + rd.PROTOCOL_BLOCK + "\n",
        )
        self.assertTrue(built.startswith("Navigate top-down."))
        self.assertEqual(built.count(rd.PROTOCOL_DELIMITER), 1)

    def test_malformed_reminder_quotes_the_same_constant(self):
        reminder = rd.malformed_reminder()
        self.assertIn(rd.PROTOCOL_BLOCK, reminder)
        self.assertIn("could not parse", reminder)

    def test_classifier_grammar_agrees_with_protocol_text(self):
        # Both sides are derived from _REQUEST_VERBS / FINAL_MARKER /
        # MAX_REQUEST_LINES; this pins the by-construction agreement.
        for verb in rd._REQUEST_VERBS:
            self.assertIn(f"{verb}: <", rd.PROTOCOL_BLOCK)
            m = rd.REQUEST_LINE_RE.match(f"{verb}: some/arg")
            self.assertIsNotNone(m)
            self.assertEqual(m.group(1), verb)
        self.assertIn(rd.FINAL_MARKER, rd.PROTOCOL_BLOCK)
        self.assertIn(str(rd.MAX_REQUEST_LINES), rd.PROTOCOL_BLOCK)
        self.assertIsNone(rd.REQUEST_LINE_RE.match("WRITE: some/arg"))


class TestServicing(unittest.TestCase):
    """service_read / service_list / service_grep over the fixture tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = _make_corpus(self.base)

    def test_read_returns_framed_content(self):
        out = rd.service_read(self.root, "sub/inner.txt", 40960)
        self.assertEqual(out, "Here is the content of sub/inner.txt:\n\ninner content\n")

    def test_read_truncates_with_marker_at_max_read_bytes(self):
        (self.root / "big.txt").write_text("x" * 100)
        out = rd.service_read(self.root, "big.txt", 10)
        self.assertIn("x" * 10, out)
        self.assertNotIn("x" * 11, out)
        self.assertIn("[TRUNCATED — file exceeds 10 bytes, showing first 10 bytes]", out)

    def test_read_of_directory_redirects_to_list(self):
        out = rd.service_read(self.root, "sub", 40960)
        self.assertIn("is a directory, not a file", out)
        self.assertIn("Use LIST: sub instead", out)

    def test_read_of_missing_file_reports_not_found(self):
        out = rd.service_read(self.root, "no-such.txt", 40960)
        self.assertIn("not found in corpus", out)

    def test_list_sorts_and_marks_directories(self):
        out = rd.service_list(self.root, ".")
        body = out.split("\n\n", 1)[1]
        self.assertEqual(
            body.split("\n"),
            ["empty/", "escape-link.txt", "notes.txt", "sub/"],
        )

    def test_list_of_empty_directory(self):
        out = rd.service_list(self.root, "empty")
        self.assertIn("(empty directory)", out)

    def test_list_of_file_redirects_to_read(self):
        out = rd.service_list(self.root, "notes.txt")
        self.assertIn("is a file, not a directory", out)
        self.assertIn("Use READ: notes.txt instead", out)

    def test_list_of_missing_dir_reports_not_found(self):
        out = rd.service_list(self.root, "no-such-dir")
        self.assertIn("not found in corpus", out)

    def test_grep_is_literal_not_regex(self):
        # "a.c" must match only the literal "a.c" line, not "abc".
        out = rd.service_grep(self.root, "a.c", 50)
        self.assertIn("notes.txt:2: literal a.c here", out)
        self.assertNotIn("abc decoy", out)

    def test_grep_caps_matches(self):
        (self.root / "many.txt").write_text("needle\n" * 20)
        out = rd.service_grep(self.root, "needle", 5)
        self.assertIn("showing up to 5 matches", out)
        self.assertEqual(out.count("many.txt:"), 5)

    def test_grep_no_matches(self):
        out = rd.service_grep(self.root, "zzz-not-there", 50)
        self.assertIn("No matches found.", out)

    def test_grep_empty_pattern_is_an_error(self):
        out = rd.service_grep(self.root, "  ", 50)
        self.assertIn("empty pattern", out)


class _ScriptedDriverMixin:
    """Build a RelayDriver whose transport is a scripted reply list.

    msg-util.sh still performs every messages-file mutation, so assertions
    read the real messages.json.
    """

    def _driver(self, replies, **config_overrides):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = _make_corpus(base)
        doctrine = base / "doctrine.md"
        doctrine.write_text("Navigate carefully.\n")
        config = rd.RunConfig(
            corpus_root=root,
            system_prompt_file=doctrine,
            output_dir=base / "out",
            retries=0,
            **config_overrides,
        )
        reply_iter = iter(replies)
        calls = []

        def post_fn(msgs_file, tmpdir, env):
            calls.append(dict(env))
            try:
                return next(reply_iter)
            except StopIteration:  # pragma: no cover - scripting bug guard
                return 1, "", "scripted replies exhausted"

        driver = rd.RelayDriver(config, post_fn=post_fn)
        return driver, calls

    @staticmethod
    def _messages(driver, qtag="q01"):
        msgs_file = driver.session_dir / qtag / "messages.json"
        return json.loads(msgs_file.read_text())

    @staticmethod
    def _user_turns(messages):
        return [m["content"] for m in messages if m["role"] == "user"]


_REQ = (0, "READ: notes.txt\n", "")


class TestBudgetStateMachine(_ScriptedDriverMixin, unittest.TestCase):
    """Warn at warn-at remaining, exhaustion demand, 2-attempt give-up."""

    def test_warn_last_notice_demands_and_give_up(self):
        driver, _ = self._driver([_REQ] * 5, budget=2, warn_at=1)
        result = driver.run_question("q01", "What is alpha?")

        self.assertEqual(result.outcome, "exhausted")
        self.assertEqual(result.round_trips, 5)
        self.assertIsNone(result.final_text)

        users = self._user_turns(self._messages(driver))
        # init + 2 serviced replies + 2 forced-final demands (the 3rd
        # over-budget request breaks without a reply).
        self.assertEqual(len(users), 5)
        self.assertIn("budget of 2 request-replies", users[0])
        self.assertIn("[Liaison: 1 request-replies remain out of your budget of 2.]", users[1])
        self.assertIn("budget exhausted — that was your last request-reply", users[2])
        for demand in users[3:]:
            self.assertIn("budget exhausted (2/2 request-replies used)", demand)
            self.assertIn("Provide your FINAL report now", demand)

    def test_final_after_forced_demand_is_accepted(self):
        replies = [_REQ] * 3 + [(0, "FINAL\nAnswer from memory.\n", "")]
        driver, _ = self._driver(replies, budget=2, warn_at=1)
        result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "final")
        self.assertEqual(result.final_text, "FINAL\nAnswer from memory.")

    def test_malformed_does_not_consume_budget(self):
        replies = [(0, "let me think...\n", ""), _REQ, (0, "FINAL\nok\n", "")]
        driver, _ = self._driver(replies, budget=1, warn_at=0)
        result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "final")
        users = self._user_turns(self._messages(driver))
        # init, protocol reminder, serviced reply (with last-one notice).
        self.assertEqual(len(users), 3)
        self.assertIn(rd.PROTOCOL_BLOCK, users[1])
        self.assertIn("Your next reply must be FINAL", users[2])


class TestLoopTopGuard(_ScriptedDriverMixin, unittest.TestCase):
    """max-round-trips is enforced at loop top, so conversations that never
    touch the request budget (malformed, TOOL_CALLS) are still bounded."""

    def test_permanently_malformed_conversation_is_bounded(self):
        driver, calls = self._driver(
            [(0, "not a request, not FINAL\njust chatter\n", "")] * 50,
            max_round_trips=4,
        )
        result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "exhausted")
        self.assertEqual(result.round_trips, 4)
        self.assertEqual(len(calls), 4)
        users = self._user_turns(self._messages(driver))
        self.assertEqual(len(users), 5)  # init + 4 protocol reminders
        for reminder in users[1:]:
            self.assertIn(rd.PROTOCOL_BLOCK, reminder)

    def test_permanent_tool_calls_conversation_is_bounded(self):
        driver, calls = self._driver([(0, _TOOL_CALLS_REPLY, "")] * 50, max_round_trips=3)
        result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "exhausted")
        self.assertEqual(result.round_trips, 3)
        self.assertEqual(len(calls), 3)


class TestToolCallsStub(_ScriptedDriverMixin, unittest.TestCase):
    """Stub-and-continue is preserved verbatim (load-bearing hardening)."""

    def test_stub_text_and_continue_to_final(self):
        replies = [(0, _TOOL_CALLS_REPLY, ""), (0, "FINAL\nDone.\n", "")]
        driver, _ = self._driver(replies)
        result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "final")
        users = self._user_turns(self._messages(driver))
        self.assertEqual(users[1], "Tool call read_file is not available in this environment.")


class TestRetriesAndHalt(_ScriptedDriverMixin, unittest.TestCase):
    """Transient transport failures retry with backoff; exhaustion halts."""

    def test_transient_failures_are_retried_within_one_round_trip(self):
        replies = [(1, "", "boom"), (1, "", "boom"), (0, "FINAL\nok\n", "")]
        driver, calls = self._driver(replies)
        driver._config.retries = 2
        with mock.patch.object(rd, "RETRY_BACKOFF_SECONDS", 0):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                result = driver.run_question("q01", "What is alpha?")
        self.assertEqual(result.outcome, "final")
        self.assertEqual(result.round_trips, 1)
        self.assertEqual(len(calls), 3)
        self.assertIn("retrying", err.getvalue())

    def test_exhausted_retries_record_error_and_halt_the_run(self):
        driver, calls = self._driver([(1, "", "connection refused")] * 10)
        driver._config.retries = 1
        with mock.patch.object(rd, "RETRY_BACKOFF_SECONDS", 0):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = driver.run([("q01", "What is alpha?"), ("q02", "never reached")])
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(calls), 2)  # 1 attempt + 1 retry, then halt
        self.assertIn("HALT after q01", err.getvalue())
        # The failed question gets an error row + liaison note; q02 is skipped.
        stats = (driver.answers_dir / "stats.csv").read_text().splitlines()
        self.assertEqual(len(stats), 2)
        self.assertIn("q01,1,NA,NA", stats[1])
        self.assertTrue(stats[1].endswith(",error"))
        note = (driver.answers_dir / "q01.md").read_text()
        self.assertIn("no FINAL report was produced", note)
        self.assertFalse((driver.answers_dir / "q02.md").exists())

    def test_transport_env_carries_per_question_usage_stats_file(self):
        driver, calls = self._driver([(0, "FINAL\nok\n", "")])
        driver.run_question("q01", "What is alpha?")
        expected = driver.session_dir / "q01" / "usage.jsonl"
        self.assertEqual(calls[0]["USAGE_STATS_FILE"], str(expected))


class TestQuestionAndEnvFileParsing(unittest.TestCase):
    """load_questions, parse_env_file, aggregate_usage plumbing."""

    _QUESTIONS = (
        "# Questions\n\n"
        "**Q1.** What is alpha?\n\n"
        "**Q2.** What is beta,\nacross two lines?\n\n"
        "**Q10.** What is kappa?\n"
    )

    def test_load_questions_extracts_all_blocks(self):
        qs = rd.load_questions(self._QUESTIONS)
        self.assertEqual(sorted(qs), [1, 2, 10])
        self.assertEqual(qs[1], "What is alpha?")
        self.assertEqual(qs[2], "What is beta,\nacross two lines?")
        self.assertEqual(qs[10], "What is kappa?")

    def test_load_questions_empty_when_no_markers(self):
        self.assertEqual(rd.load_questions("no questions here"), {})

    def test_parse_env_file_key_value_comments_export_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "guest.env"
            env_file.write_text(
                "# connection\n"
                "API_BASE_URL=http://127.0.0.1:9\n"
                "\n"
                "export MODEL=stub-model\n"
                'API_KEY_FILE="/keys/k.txt"\n'
            )
            parsed = rd.parse_env_file(env_file)
        self.assertEqual(
            parsed,
            {
                "API_BASE_URL": "http://127.0.0.1:9",
                "MODEL": "stub-model",
                "API_KEY_FILE": "/keys/k.txt",
            },
        )

    def test_parse_env_file_rejects_non_assignment_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "guest.env"
            env_file.write_text("this is not an assignment\n")
            with self.assertRaises(ValueError):
                rd.parse_env_file(env_file)

    def test_aggregate_usage_sums_lines_and_skips_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.jsonl"
            usage.write_text(
                '{"prompt_tokens": 40, "completion_tokens": 5, "total_tokens": 45, "model": "m"}\n'
                '{"prompt_tokens": 60, "completion_tokens": null, "total_tokens": null, "model": "m"}\n'
            )
            self.assertEqual(rd.aggregate_usage(usage), (100, 5))

    def test_aggregate_usage_missing_file_is_na(self):
        self.assertEqual(rd.aggregate_usage(Path("/no/such/usage.jsonl")), (None, None))


def _sse_body(text: str, prompt_tokens: int, completion_tokens: int) -> bytes:
    """One scripted SSE response: a content delta, a usage chunk (the
    empty-``choices`` stream_options shape), and the [DONE] terminator."""
    chunks = [
        {"choices": [{"delta": {"content": text}, "index": 0}]},
        {
            "model": "stub-model",
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    ]
    body = b"".join(b"data: " + json.dumps(c).encode("utf-8") + b"\n\n" for c in chunks)
    return body + b"data: [DONE]\n\n"


def _start_scripted_sse_server(bodies):
    """Serve each queued SSE body to one POST, in order; 500 when exhausted.

    Same http.server harness pattern as test_post_openai.py, extended to a
    scripted multi-response conversation.
    """
    queue = list(bodies)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if not queue:
                self.send_response(500)
                self.end_headers()
                return
            body = queue.pop(0)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep test output clean
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, queue


class TestEndToEndStubbedEndpoint(unittest.TestCase):
    """Run relay_driver.py as a subprocess against a scripted stub endpoint:
    request → serviced reply → FINAL, connection env injected via --env-file."""

    # Env vars the transport reads; stripped from the inherited env so
    # ambient settings can never leak into a test subprocess.
    _SCRIPT_ENV_VARS = (
        "API_BASE_URL",
        "API_KEY_FILE",
        "MODEL",
        "MESSAGES_FILE",
        "MAX_TOKENS",
        "ENABLE_THINKING",
        "TEMPERATURE",
        "DEBUG_POST",
        "DEBUG_RESPONSE",
        "USAGE_STATS_FILE",
    )

    def _base_env(self):
        env = {k: v for k, v in os.environ.items() if k not in self._SCRIPT_ENV_VARS}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def test_single_question_request_then_final(self):
        bodies = [
            _sse_body("READ: notes.txt", 42, 7),
            _sse_body("FINAL\nThe launch code is 42.", 50, 9),
        ]
        server, queue = _start_scripted_sse_server(bodies)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            corpus.mkdir()
            (corpus / "notes.txt").write_text("The launch code is 42.\n")
            doctrine = base / "doctrine.md"
            doctrine.write_text("You are a retrieval scout. Answer from the corpus only.\n")
            key_file = base / "key.txt"
            key_file.write_text("test-key\n")
            env_file = base / "guest.env"
            env_file.write_text(
                f"API_BASE_URL=http://127.0.0.1:{port}\n" "MODEL=stub-model\n" f"API_KEY_FILE={key_file}\n"
            )
            out_dir = base / "out"

            result = subprocess.run(
                [
                    sys.executable,
                    str(_SCRIPT),
                    "--corpus-root",
                    str(corpus),
                    "--system-prompt",
                    str(doctrine),
                    "--question",
                    "What is the launch code?",
                    "--output-dir",
                    str(out_dir),
                    "--env-file",
                    str(env_file),
                    "--retries",
                    "0",
                ],
                env=self._base_env(),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(queue, [])  # both scripted turns consumed

            # Answer file: the FINAL report, verbatim.
            answer = (out_dir / "answers" / "q01.md").read_text()
            self.assertEqual(answer, "FINAL\nThe launch code is 42.\n")

            # stats.csv: real token totals from the usage side channel.
            stats_lines = (out_dir / "answers" / "stats.csv").read_text().splitlines()
            self.assertEqual(len(stats_lines), 2)
            self.assertEqual(
                stats_lines[0],
                "question,round_trips,prompt_tokens_total,completion_tokens_total,wall_seconds,outcome",
            )
            row = stats_lines[1].split(",")
            self.assertEqual(row[0], "q01")
            self.assertEqual(row[1], "2")
            self.assertEqual(row[2], "92")  # 42 + 50
            self.assertEqual(row[3], "16")  # 7 + 9
            self.assertEqual(row[5], "final")

            # Usage side channel: one JSON line per transport call.
            usage_lines = (out_dir / "session" / "q01" / "usage.jsonl").read_text().splitlines()
            self.assertEqual(len(usage_lines), 2)
            self.assertEqual(json.loads(usage_lines[0])["prompt_tokens"], 42)
            self.assertEqual(json.loads(usage_lines[1])["prompt_tokens"], 50)

            # messages.json shape: system+init from msg-util init, then
            # assistant request, serviced user reply, assistant FINAL.
            messages = json.loads((out_dir / "session" / "q01" / "messages.json").read_text())
            self.assertEqual(
                [m["role"] for m in messages],
                ["system", "user", "assistant", "user", "assistant"],
            )
            self.assertTrue(messages[0]["content"].startswith("You are a retrieval scout."))
            self.assertIn(rd.PROTOCOL_BLOCK, messages[0]["content"])
            self.assertIn("What is the launch code?", messages[1]["content"])
            self.assertEqual(messages[2]["content"], "READ: notes.txt\n")
            self.assertTrue(
                messages[3]["content"].startswith("Here is the content of notes.txt:\n\nThe launch code is 42.\n")
            )
            self.assertEqual(messages[4]["content"], "FINAL\nThe launch code is 42.\n")


if __name__ == "__main__":
    unittest.main()
