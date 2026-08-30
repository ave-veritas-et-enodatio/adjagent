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
        "POST_OPENAI_TEST",
    )

    def _base_env(self):
        env = {k: v for k, v in os.environ.items() if k not in self._SCRIPT_ENV_VARS}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def _run_against_fixture(self, fixture_name):
        server = _start_sse_server((_THIS_DIR / fixture_name).read_bytes())
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
            return subprocess.run(
                [sys.executable, str(_SCRIPT), str(messages_file)],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

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


if __name__ == "__main__":
    unittest.main()
