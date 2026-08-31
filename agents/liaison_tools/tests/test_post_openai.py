"""Unit tests for the ``post-openai`` transport (``liaison_tools/post-openai.py``).

Ports the tool's former embedded self-test suite (the removed
``POST_OPENAI_TEST=1`` mode) into the house pytest layout: the SSE
demux/reassembly and API-key-validation cases run the real functions against
the ``test-fixture-*.txt`` fixtures beside this file, offline. The script's
filename is hyphenated (it is shell-invoked, never imported by the liaisons),
so the module is loaded via ``importlib`` from its file path.

Beyond the ported cases, the end-to-end class runs the script as a subprocess
against a stubbed local HTTP endpoint (stdlib ``http.server``) serving fixture
bytes, proving the stdout contract byte-for-byte in both shapes: plain text
body and ``TOOL_CALLS\\n<json>``.

Residual gaps (require a live or stateful endpoint; not covered): the
``/models`` listing and substring model-resolution retry in ``main()``
(``list_models`` / ``resolve_model`` and the rc==1 re-post), and real
mid-stream connection drops. ``is_model_error_text`` — the pure sniffing those
paths pivot on — is covered.

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

_THIS_DIR = Path(__file__).resolve().parent
_SCRIPT = _THIS_DIR.parent / "post-openai.py"


def _load_post_openai():
    """Load post-openai.py as a module despite its hyphenated filename."""
    spec = importlib.util.spec_from_file_location("post_openai", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


post_openai = _load_post_openai()


def _demux_fixture(name):
    """Run the real demux over a fixture file, stderr suppressed."""
    with (_THIS_DIR / name).open("r", encoding="utf-8") as f:
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            chunks, status = post_openai.demux_sse(f)
    return chunks, status, captured.getvalue()


class TestSseDemuxAndReassembly(unittest.TestCase):
    """demux_sse status contract + reassemble_stream over the SSE fixtures."""

    def test_stream_fixture_reassembles_plain_content(self):
        # Also exercises skipping of SSE comment lines (": OPENROUTER
        # PROCESSING") and blank keep-alive lines present in the fixture.
        chunks, status, _ = _demux_fixture("test-fixture-stream.txt")
        self.assertEqual(status, 0)
        self.assertEqual(post_openai.reassemble_stream(chunks), "Hello, world!")

    def test_tool_calls_fixture_reassembles_single_call(self):
        chunks, status, _ = _demux_fixture("test-fixture-tool-calls.txt")
        self.assertEqual(status, 0)
        text = post_openai.reassemble_stream(chunks)
        self.assertTrue(text.startswith("TOOL_CALLS\n"))
        calls = json.loads(text[len("TOOL_CALLS\n") :])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "call_abc")
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(calls[0]["function"]["arguments"], '{"path":"src/foo.py"}')

    def test_multi_tc_fixture_orders_two_calls_by_index(self):
        # The fixture delivers index 1 before index 0; reassembly must order
        # the emitted list by index, not arrival.
        chunks, status, _ = _demux_fixture("test-fixture-multi-tc.txt")
        self.assertEqual(status, 0)
        text = post_openai.reassemble_stream(chunks)
        self.assertTrue(text.startswith("TOOL_CALLS\n"))
        calls = json.loads(text[len("TOOL_CALLS\n") :])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["function"]["name"], "first")
        self.assertEqual(calls[0]["function"]["arguments"], '{"a":1}')
        self.assertEqual(calls[1]["function"]["name"], "second")
        self.assertEqual(calls[1]["function"]["arguments"], '{"b":2}')

    def test_error_fixture_reports_mid_stream_error(self):
        chunks, status, stderr_text = _demux_fixture("test-fixture-error.txt")
        self.assertEqual(status, 2)
        self.assertIn("mid-stream error event", stderr_text)
        self.assertIn("context_length_exceeded", stderr_text)
        # The pre-error data chunk was still collected.
        self.assertEqual(len(chunks), 1)

    def test_no_data_events_returns_status_1(self):
        lines = [": keep-alive comment\n", "\n", "event: ping\n"]
        chunks, status = post_openai.demux_sse(iter(lines))
        self.assertEqual(status, 1)
        self.assertEqual(chunks, [])

    def test_missing_done_returns_status_3(self):
        lines = ['data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n']
        chunks, status = post_openai.demux_sse(iter(lines))
        self.assertEqual(status, 3)
        self.assertEqual(post_openai.reassemble_stream(chunks), "hi")


class TestApiKeyValidation(unittest.TestCase):
    """_read_api_key file-format contract."""

    def test_good_key_fixture_trims_surrounding_whitespace(self):
        key = post_openai._read_api_key(_THIS_DIR / "test-fixture-good-key.txt")
        self.assertEqual(key, "this-is_an-0-AccePtable-key-1")

    def test_bad_key_fixture_rejects_internal_whitespace(self):
        key = post_openai._read_api_key(_THIS_DIR / "test-fixture-bad-key.txt")
        self.assertIsNone(key)

    def test_missing_key_file_returns_none(self):
        key = post_openai._read_api_key(_THIS_DIR / "no-such-key-file.txt")
        self.assertIsNone(key)


class TestUsageExtraction(unittest.TestCase):
    """extract_usage / write_usage_stats — the USAGE_STATS_FILE side channel.

    The usage-bearing SSE chunk rides with empty ``choices`` (the
    ``stream_options`` shape), which ``reassemble_stream`` skips; capture must
    see it anyway, and must equally handle a non-streaming full completion
    object where ``usage`` sits at the top level beside ``choices``.
    """

    _EXPECTED = {
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "total_tokens": 49,
        "model": "test-model-v1",
    }

    def test_usage_fixture_demux_extract_and_unchanged_reassembly(self):
        chunks, status, _ = _demux_fixture("test-fixture-usage.txt")
        self.assertEqual(status, 0)
        # The empty-choices usage chunk must not alter the reassembled text.
        self.assertEqual(post_openai.reassemble_stream(chunks), "Hi there")
        self.assertEqual(post_openai.extract_usage(chunks), self._EXPECTED)

    def test_extract_from_non_streaming_completion_object(self):
        obj = {
            "id": "chatcmpl-ns",
            "model": "test-model-v1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}, "index": 0}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
        }
        self.assertEqual(post_openai.extract_usage([obj]), self._EXPECTED)

    def test_extract_nulls_when_api_provides_no_usage(self):
        chunks, status, _ = _demux_fixture("test-fixture-stream.txt")
        self.assertEqual(status, 0)
        self.assertEqual(
            post_openai.extract_usage(chunks),
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None, "model": None},
        )

    def test_extract_nulls_individual_missing_usage_fields(self):
        chunks = [{"model": "m1", "choices": [], "usage": {"total_tokens": 5}}]
        self.assertEqual(
            post_openai.extract_usage(chunks),
            {"prompt_tokens": None, "completion_tokens": None, "total_tokens": 5, "model": "m1"},
        )

    def test_write_usage_stats_appends_one_json_line_per_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "stats.jsonl")
            post_openai.write_usage_stats(path, self._EXPECTED)
            post_openai.write_usage_stats(path, self._EXPECTED)
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(ln) for ln in lines], [self._EXPECTED] * 2)

    def test_write_usage_stats_bad_path_warns_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = str(Path(tmp) / "no-such-dir" / "stats.jsonl")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                post_openai.write_usage_stats(bad, self._EXPECTED)
        self.assertIn("warning: could not write USAGE_STATS_FILE", captured.getvalue())
        self.assertIn(bad, captured.getvalue())


class TestModelErrorSniffing(unittest.TestCase):
    """is_model_error_text — the pivot for the substring-resolution fallback."""

    def test_json_error_code_matches(self):
        raw = json.dumps({"error": {"code": "model_not_found", "message": "nope"}})
        self.assertTrue(post_openai.is_model_error_text(raw))

    def test_json_error_message_matches(self):
        raw = json.dumps({"error": {"code": "bad_request", "message": "The model `x` does not exist"}})
        self.assertTrue(post_openai.is_model_error_text(raw))

    def test_textual_fallback_matches_non_json(self):
        self.assertTrue(post_openai.is_model_error_text("404: no such model here"))

    def test_ordinary_error_body_is_not_a_model_error(self):
        raw = json.dumps({"error": {"code": "rate_limited", "message": "slow down"}})
        self.assertFalse(post_openai.is_model_error_text(raw))


def _start_sse_server(body_bytes):
    """Serve `body_bytes` as the response to any POST, on an ephemeral port."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body_bytes)

        def log_message(self, *args):  # keep test output clean
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class TestEndToEndStubbedEndpoint(unittest.TestCase):
    """Run the real script as a subprocess against a local stub endpoint.

    Proves the full stdout contract byte-for-byte — exactly what the liaisons
    and MAD referees consume — without live network.
    """

    # Env vars main() reads; stripped from the inherited env so ambient
    # settings can never leak into a test subprocess.
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
        "POST_OPENAI_TEST",
    )

    # An SSE usage event in the ``stream_options`` shape (empty ``choices``),
    # spliced into a fixture stream just before its [DONE] terminator.
    _USAGE_EVENT = (
        b'data: {"model":"test-model-v1","choices":[],'
        b'"usage":{"prompt_tokens":42,"completion_tokens":7,"total_tokens":49}}'
    )
    _EXPECTED_STATS = {
        "prompt_tokens": 42,
        "completion_tokens": 7,
        "total_tokens": 49,
        "model": "test-model-v1",
    }

    @classmethod
    def _fixture_with_usage(cls, fixture_name):
        body = (_THIS_DIR / fixture_name).read_bytes()
        return body.replace(b"data: [DONE]", cls._USAGE_EVENT + b"\n\ndata: [DONE]")

    def _base_env(self):
        env = {k: v for k, v in os.environ.items() if k not in self._SCRIPT_ENV_VARS}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def _run_stub(self, body_bytes, stats_rel=None, set_stats_env=True):
        """Run the script against a stub serving `body_bytes`.

        `stats_rel` (if set) names a stats-file path relative to the run's
        temp dir; it is exported as USAGE_STATS_FILE unless `set_stats_env`
        is False (which still computes the path so the caller can assert the
        file was NOT created). Returns (result, stats_exists, stats_text).
        """
        server = _start_sse_server(body_bytes)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key.txt"
            key_file.write_text("test-key\n", encoding="utf-8")
            messages_file = Path(tmp) / "messages.json"
            messages_file.write_text('[{"role":"user","content":"hi"}]', encoding="utf-8")
            env = self._base_env()
            env.update(
                {
                    "API_BASE_URL": f"http://127.0.0.1:{port}",
                    "API_KEY_FILE": str(key_file),
                    "MODEL": "test-model",
                }
            )
            stats_path = Path(tmp) / stats_rel if stats_rel is not None else None
            if stats_path is not None and set_stats_env:
                env["USAGE_STATS_FILE"] = str(stats_path)
            result = subprocess.run(
                [sys.executable, str(_SCRIPT), str(messages_file)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            stats_exists = stats_path.exists() if stats_path is not None else None
            stats_text = stats_path.read_text(encoding="utf-8") if stats_exists else None
        return result, stats_exists, stats_text

    def _run_against_fixture(self, fixture_name):
        result, _, _ = self._run_stub((_THIS_DIR / fixture_name).read_bytes())
        return result

    def test_text_stream_stdout_contract(self):
        result = self._run_against_fixture("test-fixture-stream.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Hello, world!\n")

    def test_tool_calls_stdout_contract(self):
        result = self._run_against_fixture("test-fixture-tool-calls.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_calls = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"src/foo.py"}'},
            }
        ]
        self.assertEqual(result.stdout, "TOOL_CALLS\n" + json.dumps(expected_calls) + "\n")

    def test_missing_env_prints_usage_and_exits_nonzero(self):
        # POST_OPENAI_TEST=1 is deliberately set: the removed self-test mode
        # must no longer short-circuit env validation.
        env = self._base_env()
        env["POST_OPENAI_TEST"] = "1"
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)
        self.assertIn("API_BASE_URL must be set", result.stderr)

    def _assert_stats_line(self, stats_text):
        lines = stats_text.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), self._EXPECTED_STATS)

    def test_usage_stats_written_on_text_shape(self):
        body = self._fixture_with_usage("test-fixture-stream.txt")
        result, stats_exists, stats_text = self._run_stub(body, stats_rel="stats.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Usage never reaches stdout; the text contract is unchanged.
        self.assertEqual(result.stdout, "Hello, world!\n")
        self.assertTrue(stats_exists)
        self._assert_stats_line(stats_text)

    def test_usage_stats_written_on_tool_calls_shape(self):
        body = self._fixture_with_usage("test-fixture-tool-calls.txt")
        result, stats_exists, stats_text = self._run_stub(body, stats_rel="stats.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_calls = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"src/foo.py"}'},
            }
        ]
        self.assertEqual(result.stdout, "TOOL_CALLS\n" + json.dumps(expected_calls) + "\n")
        self.assertTrue(stats_exists)
        self._assert_stats_line(stats_text)

    def test_usage_stats_written_from_usage_fixture(self):
        body = (_THIS_DIR / "test-fixture-usage.txt").read_bytes()
        result, stats_exists, stats_text = self._run_stub(body, stats_rel="stats.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Hi there\n")
        self.assertTrue(stats_exists)
        self._assert_stats_line(stats_text)

    def test_usage_stats_env_unset_no_file_and_stdout_identical(self):
        # Usage events present in the stream, env var unset: stdout stays
        # byte-identical to the plain run and no stats file is created.
        body = self._fixture_with_usage("test-fixture-stream.txt")
        result, stats_exists, _ = self._run_stub(body, stats_rel="stats.jsonl", set_stats_env=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Hello, world!\n")
        self.assertFalse(stats_exists)

    def test_usage_stats_unwritable_path_warns_but_call_succeeds(self):
        body = self._fixture_with_usage("test-fixture-stream.txt")
        result, stats_exists, _ = self._run_stub(body, stats_rel="no-such-dir/stats.jsonl")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "Hello, world!\n")
        self.assertFalse(stats_exists)
        self.assertIn("warning: could not write USAGE_STATS_FILE", result.stderr)


if __name__ == "__main__":
    unittest.main()
