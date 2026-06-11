#!/usr/bin/env python3
"""1:1 port of post-openai.sh — POST a messages array to an OpenAI-compatible
chat completions endpoint using SSE streaming, reassemble delta content /
tool_calls, and emit the canonical stdout contract (text body, or
"TOOL_CALLS\\n<json>"). See ../../tools/test_inference.py for the urllib+SSE
pattern this script mirrors.

INVARIANT: stdlib only. No third-party dependencies. Ever.
Adding `pip install` of anything is not on the table — if you reach for one,
stop and find a stdlib path, or talk it through with the maintainer first.

usage:
  API_BASE_URL=<url> API_KEY_FILE=<api-key-file> MODEL=<model> \\
      post-openai.py <messages.json>

env vars:
  API_BASE_URL       (required) OpenAI-compatible base URL, e.g. https://api.openai.com/v1
  API_KEY_FILE       (required) path to a file containing only the API key. Its
                                entire content, with leading and trailing
                                whitespace trimmed, is the key; the key must
                                contain no internal whitespace.
  MODEL              (required) model id; substring resolution is attempted on
                                pre-stream model-not-found errors.
  MAX_TOKENS         (optional) integer max response tokens (default: 32768)
  ENABLE_THINKING    (optional) boolean (exactly true or false) (default: true)
  TEMPERATURE        (optional) float in [0.0, 2.0] (default: 0.0)
  DEBUG_POST         (optional) "true" to dump the request payload to stderr
  DEBUG_RESPONSE     (optional) "true" to tee the raw SSE stream + reassembled output to stderr
  POST_OPENAI_TEST   (optional) "1" runs the offline self-test suite (SSE
                                demux/reassembly + API key validation against
                                the fixtures in tests/) instead of contacting
                                the network.

The API key is read from API_KEY_FILE into process memory. It is never placed
on the command line or into an environment variable, so it is not exposed
through argv or the process environment.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Iterable

DEFAULT_MAX_TOKENS = 1024 * 32

_TEMPERATURE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")

_MODEL_ERROR_CODE_RE = re.compile(r"model_not_found|invalid_model|model_not_allowed", re.I)
_MODEL_ERROR_MSG_RE = re.compile(r"does not exist|not available|no such model", re.I)
_MODEL_ERROR_FALLBACK_RE = re.compile(
    r"model_not_found|invalid_model|model_not_allowed|"
    r"does not exist|not available|no such model",
    re.I,
)


def _usage(msg: str = "") -> None:
    script = Path(sys.argv[0]).name or "post-openai.py"
    if msg:
        sys.stderr.write(f"error: {msg}\n")
    sys.stderr.write(
        f"usage: API_BASE_URL=<url> API_KEY_FILE=<api-key-file> "
        f"MODEL=<model> {script} <messages.json>\n"
    )
    sys.stderr.write(
        f"optional envar param MAX_TOKENS=<max-response-token-count> "
        f"(default: {DEFAULT_MAX_TOKENS})\n"
    )
    sys.stderr.write(
        "optional envar param TEMPERATURE=<float in [0.0, 2.0]> (default: 0.0)\n"
    )
    sys.stderr.write(
        "API_KEY_FILE file format: the file contains only the API key "
        "(leading/trailing whitespace trimmed; no internal whitespace).\n"
    )
    sys.exit(1)


def _parse_temperature(raw: str) -> float:
    if not _TEMPERATURE_RE.match(raw):
        sys.stderr.write(
            f"error: TEMPERATURE must be a non-negative number (got '{raw}')\n"
        )
        sys.exit(1)
    v = float(raw)
    if v > 2.0:
        sys.stderr.write(f"error: TEMPERATURE must be in [0.0, 2.0] (got '{raw}')\n")
        sys.exit(1)
    return v


def _read_api_key(key_path: Path) -> str | None:
    """Read the API key file and return the key, or None on invalid format.

    The file must contain a single contiguous string. Leading and trailing
    whitespace is trimmed; the result is the key. The key is invalid (None) if
    it is empty after trimming or contains any internal whitespace.
    """
    try:
        text = key_path.read_text(encoding="utf-8")
    except OSError:
        return None
    key = text.strip()
    if not key or any(ch.isspace() for ch in key):
        return None
    return key


def demux_sse(
    line_iter: Iterable,
    debug_sink: IO | None = None,
    raw_buffer: list[str] | None = None,
    chunk_payloads: list[str] | None = None,
) -> tuple[list[dict], int]:
    """Read SSE bytes/text from `line_iter`, return (parsed_chunks, status).

    Status codes mirror the bash demux_sse return values:
        0 — clean: ≥1 data event AND [DONE] seen
        1 — no data events
        2 — mid-stream error event (already reported to stderr by this fn)
        3 — stream ended without [DONE] but had data events

    `debug_sink` (if set) receives every raw line in real time.
    `raw_buffer` (if set) collects every raw line text for later error reporting.
    `chunk_payloads` (if set) collects each `data:`-prefix-stripped payload
    string verbatim, matching the bash chunks-file format used for DEBUG dumps.
    """
    chunks: list[dict] = []
    saw_data = False
    saw_done = False
    for raw in line_iter:
        if isinstance(raw, bytes):
            raw_str = raw.decode("utf-8", errors="replace")
        else:
            raw_str = raw
        if raw_buffer is not None:
            raw_buffer.append(raw_str)
        if debug_sink is not None:
            out = raw_str if raw_str.endswith("\n") else raw_str + "\n"
            debug_sink.write(out)
            debug_sink.flush()
        line = raw_str.rstrip("\r\n")
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].lstrip(" ")
        if payload == "[DONE]":
            saw_done = True
            break
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("error"), dict):
            sys.stderr.write("error: mid-stream error event:\n")
            sys.stderr.write(payload + "\n")
            return chunks, 2
        if chunk_payloads is not None:
            chunk_payloads.append(payload)
        chunks.append(obj)
        saw_data = True
    if not saw_data:
        return chunks, 1
    if not saw_done:
        return chunks, 3
    return chunks, 0


def reassemble_stream(chunks: list[dict]) -> str:
    """Reassemble SSE delta chunks into either text or 'TOOL_CALLS\\n<json>'.

    Direct port of the bash embedded-python reassembler: accumulate
    `delta.content` strings into a content buffer, accumulate `delta.tool_calls`
    by index into a slot map, then emit content text OR (if any tool_calls
    were seen) a TOOL_CALLS marker + JSON-serialized ordered list.
    """
    content_parts: list[str] = []
    tc_map: dict[int, dict] = {}
    for obj in chunks:
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        c = delta.get("content")
        if isinstance(c, str):
            content_parts.append(c)
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index")
            if idx is None:
                continue
            slot = tc_map.setdefault(
                idx,
                {
                    "id": None,
                    "type": "function",
                    "function": {"name": None, "arguments": ""},
                },
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            if tc.get("type"):
                slot["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] = fn["name"]
            if isinstance(fn.get("arguments"), str):
                slot["function"]["arguments"] += fn["arguments"]
    if tc_map:
        ordered = [tc_map[k] for k in sorted(tc_map.keys())]
        return "TOOL_CALLS\n" + json.dumps(ordered)
    return "".join(content_parts)


def list_models(base_url: str, token: str) -> list[str]:
    """GET /models — return the data[].id list. Raises on transport errors."""
    req = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    obj = json.loads(body)
    return [m["id"] for m in obj.get("data", [])]


def resolve_model(name: str, base_url: str, token: str) -> str | None:
    """Resolve `name` to an exact model id via /models. Accepts exact match
    or unambiguous substring. Returns None on failure (errors already on stderr).
    """
    try:
        models = list_models(base_url, token)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        sys.stderr.write(f"error: failed querying models: {e}\n")
        return None

    if name in models:
        return name

    matches = [m for m in models if name in m]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.stderr.write(f"error: no model matching '{name}'. available models:\n")
        sys.stderr.write("\n".join(models) + ("\n" if models else ""))
        return None
    sys.stderr.write(f"error: '{name}' is ambiguous — {len(matches)} candidates:\n")
    sys.stderr.write("\n".join(matches) + "\n")
    return None


def is_model_error_text(raw: str) -> bool:
    """Return True if `raw` (a pre-stream HTTP body or non-SSE response body)
    looks like a model-name error. JSON-aware first, textual fallback after.
    """
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            err = body.get("error") or body.get("detail") or {}
            if isinstance(err, dict):
                code = err.get("code", "") or ""
                msg = err.get("message", "") or ""
                if isinstance(code, str) and _MODEL_ERROR_CODE_RE.search(code):
                    return True
                if isinstance(msg, str) and _MODEL_ERROR_MSG_RE.search(msg):
                    return True
    except (json.JSONDecodeError, ValueError):
        pass
    return bool(_MODEL_ERROR_FALLBACK_RE.search(raw))


def do_post_streaming(
    *,
    model: str,
    base_url: str,
    token: str,
    messages: object,
    max_tokens: int,
    enable_thinking: bool | None,
    temperature: float,
    debug_post: bool,
    debug_response: bool,
) -> tuple[list[dict], list[str], str, int]:
    """POST one streaming completions request.

    Returns (chunks, chunk_payloads, raw_text, status):
        status 0 — clean stream, [DONE] received
        status 1 — pre-stream HTTP/connection error (raw_text may contain body)
        status 2 — mid-stream SSE error event (already on stderr)
        status 3 — connection drop before [DONE] (had partial data)
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "top_p": 1.0,
        "temperature": temperature,
        "stream": True,
        "messages": messages,
    }
    if enable_thinking is not None:
      payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}
    payload_str = json.dumps(payload)

    if debug_post:
        sys.stderr.write(f"POST {base_url}/chat/completions payload:\n")
        sys.stderr.write(payload_str + "\n")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload_str.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    raw_buffer: list[str] = []
    chunk_payloads: list[str] = []
    debug_sink = sys.stderr if debug_response else None

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            line_iter = (line.decode("utf-8", errors="replace") for line in resp)
            chunks, status = demux_sse(
                line_iter,
                debug_sink=debug_sink,
                raw_buffer=raw_buffer,
                chunk_payloads=chunk_payloads,
            )
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        sys.stderr.write(f"error: HTTP {e.code} from {base_url}/chat/completions\n")
        if body:
            sys.stderr.write("--- raw body ---\n")
            sys.stderr.write(body if body.endswith("\n") else body + "\n")
        return [], [], body, 1
    except (urllib.error.URLError, OSError) as e:
        sys.stderr.write(f"error: request failed before any SSE data: {e}\n")
        return [], [], "", 1

    raw_text = "".join(raw_buffer)

    if status == 1:
        sys.stderr.write("error: no SSE data events received\n")
        if raw_text:
            sys.stderr.write("--- raw body ---\n")
            sys.stderr.write(raw_text if raw_text.endswith("\n") else raw_text + "\n")
        return chunks, chunk_payloads, raw_text, 1

    if status == 2:
        return chunks, chunk_payloads, raw_text, 2

    if status == 3:
        sys.stderr.write("error: stream terminated before [DONE]\n")
        return chunks, chunk_payloads, raw_text, 3

    return chunks, chunk_payloads, raw_text, 0


def _emit(reassembled: str) -> None:
    """Match the bash stdout contract: trailing newline either way."""
    sys.stdout.write(reassembled + "\n")


def _self_test_main() -> int:
    """POST_OPENAI_TEST=1 path: run the full offline self-test suite — SSE
    demux/reassembly plus API key validation — against the fixtures in the
    tests/ directory beside this script. No env validation, no auth, no
    network. Prints one PASS/FAIL line per case; returns non-zero if any fail.
    """
    tests_dir = Path(__file__).resolve().parent / "tests"
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    def reassemble_fixture(fixture: str) -> tuple[int, str]:
        with (tests_dir / fixture).open("r", encoding="utf-8") as f:
            with contextlib.redirect_stderr(io.StringIO()):
                chunks, status = demux_sse(f)
        return status, reassemble_stream(chunks)

    status, text = reassemble_fixture("test-fixture-stream.txt")
    check(
        "stream: plain content reassembled",
        status == 0 and text == "Hello, world!",
        f"status={status} text={text!r}",
    )

    status, text = reassemble_fixture("test-fixture-tool-calls.txt")
    ok = False
    if status == 0 and text.startswith("TOOL_CALLS\n"):
        calls = json.loads(text[len("TOOL_CALLS\n") :])
        ok = (
            len(calls) == 1
            and calls[0]["id"] == "call_abc"
            and calls[0]["function"]["name"] == "read_file"
            and calls[0]["function"]["arguments"] == '{"path":"src/foo.py"}'
        )
    check("tool-calls: single call reassembled", ok, f"status={status} text={text!r}")

    status, text = reassemble_fixture("test-fixture-multi-tc.txt")
    ok = False
    if status == 0 and text.startswith("TOOL_CALLS\n"):
        calls = json.loads(text[len("TOOL_CALLS\n") :])
        ok = (
            len(calls) == 2
            and calls[0]["function"]["name"] == "first"
            and calls[0]["function"]["arguments"] == '{"a":1}'
            and calls[1]["function"]["name"] == "second"
            and calls[1]["function"]["arguments"] == '{"b":2}'
        )
    check("multi-tc: two calls ordered by index", ok, f"status={status} text={text!r}")

    with (tests_dir / "test-fixture-error.txt").open("r", encoding="utf-8") as f:
        with contextlib.redirect_stderr(io.StringIO()):
            _, err_status = demux_sse(f)
    check(
        "error: mid-stream error event detected",
        err_status == 2,
        f"status={err_status}",
    )

    good = _read_api_key(tests_dir / "test-fixture-good-key.txt")
    check(
        "key: surrounding whitespace trimmed",
        good == "this-is_an-0-AccePtable-key-1",
        f"got {good!r}",
    )

    bad = _read_api_key(tests_dir / "test-fixture-bad-key.txt")
    check("key: internal whitespace rejected", bad is None, f"got {bad!r}")

    passed = sum(1 for _, case_ok, _ in results if case_ok)
    for name, case_ok, detail in results:
        line = f"{'PASS' if case_ok else 'FAIL'}  {name}"
        if not case_ok and detail:
            line += f"  ({detail})"
        sys.stdout.write(line + "\n")
    sys.stdout.write(f"{passed}/{len(results)} passed\n")
    return 0 if passed == len(results) else 1


def main() -> int:
    if os.environ.get("POST_OPENAI_TEST") == "1":
        return _self_test_main()

    api_base_url = os.environ.get("API_BASE_URL", "")
    api_key_file = os.environ.get("API_KEY_FILE", "")
    model = os.environ.get("MODEL", "")

    if not api_base_url:
        _usage("API_BASE_URL must be set")
    if not api_key_file:
        _usage("API_KEY_FILE must be set")
    key_path = Path(api_key_file)
    if not key_path.is_file():
        _usage(f"API_KEY_FILE not found: {api_key_file}")

    token = _read_api_key(key_path)
    if token is None:
        _usage(f"API_KEY_FILE has invalid format: {api_key_file}")
        token = "" # shut the linter up. it's not catching that _usage exits.

    if not model:
        _usage("MODEL must be set")

    args = sys.argv[1:]
    messages_arg = os.environ.get("MESSAGES_FILE") or (args[0] if args else "")
    if not messages_arg or not Path(messages_arg).is_file():
        _usage(f"messages file not found: {messages_arg}")
    messages_file = Path(messages_arg)

    max_tokens_raw = os.environ.get("MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
    try:
        max_tokens = int(max_tokens_raw)
    except ValueError:
        _usage(f"MAX_TOKENS must be an integer (got '{max_tokens_raw}')")
        return 1

    temperature = _parse_temperature(os.environ.get("TEMPERATURE", "0.0"))
    enable_thinking = os.environ.get("ENABLE_THINKING", "true").lower() == "true"

    debug_post = os.environ.get("DEBUG_POST", "false").lower() == "true"
    debug_response = os.environ.get("DEBUG_RESPONSE", "false").lower() == "true"

    try:
        messages = json.loads(messages_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.stderr.write(f"error: messages file is not valid JSON: {e}\n")
        return 1

    chunks, chunk_payloads, raw_text, rc = do_post_streaming(
        model=model,
        base_url=api_base_url,
        token=token,
        messages=messages,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        temperature=temperature,
        debug_post=debug_post,
        debug_response=debug_response,
    )

    if rc == 1 and raw_text and is_model_error_text(raw_text):
        sys.stderr.write(
            f"warning: model '{model}' not found — attempting substring resolution\n"
        )
        resolved = resolve_model(model, api_base_url, token)
        if resolved is None:
            sys.stderr.write("error: update MODEL to a valid model name\n")
            return 1
        sys.stderr.write(
            f"warning: resolved '{model}' → '{resolved}' — "
            "update MODEL to avoid this fallback\n"
        )
        chunks, chunk_payloads, raw_text, rc = do_post_streaming(
            model=resolved,
            base_url=api_base_url,
            token=token,
            messages=messages,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
            temperature=temperature,
            debug_post=debug_post,
            debug_response=debug_response,
        )

    if rc != 0:
        return 1

    if debug_response:
        sys.stderr.write("--- raw SSE stream ---\n")
        sys.stderr.write(raw_text if raw_text.endswith("\n") else raw_text + "\n")
        sys.stderr.write("--- chunks ---\n")
        for cp in chunk_payloads:
            sys.stderr.write(cp + "\n")

    reassembled = reassemble_stream(chunks)

    if debug_response:
        sys.stderr.write("--- reassembled ---\n")
        sys.stderr.write(
            reassembled if reassembled.endswith("\n") else reassembled + "\n"
        )

    if not reassembled:
        sys.stderr.write("error: no content in stream — raw response follows\n")
        sys.stderr.write(raw_text if raw_text.endswith("\n") else raw_text + "\n")
        return 1

    _emit(reassembled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
