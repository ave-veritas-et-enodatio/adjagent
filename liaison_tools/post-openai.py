#!/usr/bin/env python3
"""POST a messages array to an OpenAI-compatible
chat completions endpoint using SSE streaming, reassemble delta content /
tool_calls, and emit the canonical stdout contract (text body, or
"TOOL_CALLS\\n<json>"). See ../../tools/test_inference.py for the urllib+SSE
pattern this script mirrors.

INVARIANT: stdlib only. No third-party dependencies. Ever.
Adding `pip install` of anything is not on the table — if you reach for one,
stop and find a stdlib path, or talk it through with the maintainer first.

usage:
  API_BASE_URL=<url> API_KEY_FILE=<api-key-file> MODEL=<model> \\
      post-openai.py [--allow-http] <messages.json>

env vars:
  API_BASE_URL       (required) OpenAI-compatible base URL, e.g. https://api.openai.com/v1
  API_KEY_FILE       (required) path to a file containing only the API key. Its
                                entire content, with leading and trailing
                                whitespace trimmed, is the key; the key must
                                contain no internal whitespace.
  MODEL              (required) model id; substring resolution is attempted on
                                pre-stream model-not-found errors.
  ALLOW_HTTP         (optional) "1" to accept a plaintext http URL to a
                                non-loopback host, for a known-trusted private
                                network; any other value, including unset or
                                empty, means no (default: unset). The
                                --allow-http command-line flag is the other
                                spelling of the same opt-in; either alone is
                                enough.
  MAX_TOKENS         (optional) integer max response tokens (default: 32768)
  ENABLE_THINKING    (optional) boolean (exactly true or false) (default: true)
  TEMPERATURE        (optional) float in [0.0, 2.0] (default: 0.0)
  DEBUG_POST         (optional) "true" to dump the request payload to stderr
  DEBUG_RESPONSE     (optional) "true" to tee the raw SSE stream + reassembled output to stderr
  USAGE_STATS_FILE   (optional) path to a token-usage side-channel file. When
                                set, one JSON line is appended per successful
                                call (any of exit 0, 3, or 4): {"prompt_tokens": ...,
                                "completion_tokens": ..., "total_tokens": ...,
                                "model": ...} — token fields from the
                                response's `usage` object, model from the
                                response object; null where the API omits a
                                field. Usage never goes to stdout. A failed
                                write is a stderr warning only, never a
                                transport failure. Unset: no file is touched
                                and behavior is unchanged.

The API key is read from API_KEY_FILE into process memory. It is never placed
on the command line or into an environment variable, so it is not exposed
through argv or the process environment.

Transport rules that protect that containment:
  - API_BASE_URL must be https, except for loopback hosts (localhost,
    127.0.0.1, ::1), where http is accepted for local inference, or any host
    when ALLOW_HTTP=1 or --allow-http is given — an explicit opt-in for an
    operator who knows their endpoint is on a trusted private network.
  - Redirects are never followed. urllib copies request headers — including
    Authorization — across a redirect, so a 3xx from the endpoint would hand
    the key to whatever host the Location header names. A 3xx is a hard error
    naming the refused target.

exit codes:
  0  a complete reply; stdout carries the stdout contract above
  1  usage / configuration / transport failure (retryable by the caller)
  3  the endpoint completed the call but the reply is incomplete — a
     finish_reason other than stop/tool_calls (e.g. "length"). Whatever
     arrived is still written to stdout for the audit trail, but it must not
     be recorded as a complete reply. Retrying the same request will not help
  4  the endpoint returned an empty completion with a normal finish reason: a
     protocol-level empty result, not a transport failure. stdout is empty
Exit 3 and 4 are protocol events, not transport failures: a caller retries
neither. Both still append to USAGE_STATS_FILE — the tokens were spent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import IO, Iterable

DEFAULT_MAX_TOKENS = 1024 * 32

# Exit codes. 0/1 are the historical contract; 3 and 4 are protocol events the
# caller must not retry (see the module docstring).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INCOMPLETE = 3
EXIT_EMPTY = 4

# finish_reason values that mean the endpoint stopped of its own accord.
# Anything else (notably "length") means the reply was cut off.
COMPLETE_FINISH_REASONS = frozenset({"stop", "tool_calls", "function_call"})

# http is accepted only for these — local inference has no key to protect on
# the wire, and requiring https there would break every localhost endpoint.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_TEMPERATURE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")

_MODEL_ERROR_CODE_RE = re.compile(r"model_not_found|invalid_model|model_not_allowed", re.I)
_MODEL_ERROR_MSG_RE = re.compile(r"does not exist|not available|no such model", re.I)
_MODEL_ERROR_FALLBACK_RE = re.compile(
    r"model_not_found|invalid_model|model_not_allowed|" r"does not exist|not available|no such model",
    re.I,
)


class RedirectRefused(urllib.error.URLError):
    """A 3xx response from the configured endpoint, refused rather than followed."""

    def __init__(self, url: str, code: int, location: str) -> None:
        super().__init__(
            f"endpoint returned an HTTP {code} redirect from {url} to '{location}'; "
            "refused — following it would carry the Authorization header, and "
            "with it the API key, to the redirect target"
        )
        self.code = code
        self.location = location


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect. Installed in place of urllib's default handler,
    which copies request headers (Authorization included) to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectRefused(req.full_url, code, newurl)


# The one opener every request in this module goes through.
_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def validate_base_url(url: str, *, allow_http: bool) -> str | None:
    """Return an error message if `url` is not an acceptable API root, else None.

    `allow_http` is the resolved ALLOW_HTTP/--allow-http opt-in: when set, an
    http URL to a non-loopback host validates too.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return None
    if not parts.scheme or not parts.netloc:
        return f"API_BASE_URL must be an absolute http(s) URL (got '{url}')"
    if parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS:
        return None
    if parts.scheme == "http" and allow_http:
        return None
    return (
        f"API_BASE_URL must use https (got scheme '{parts.scheme}' for host "
        f"'{parts.hostname or ''}'); plain http is accepted only for loopback "
        f"({', '.join(sorted(LOOPBACK_HOSTS))}), where the key stays on the machine"
        "; set ALLOW_HTTP=1 or pass --allow-http to accept that risk for a "
        "known-trusted private network"
    )


def _usage(msg: str = "") -> None:
    script = Path(sys.argv[0]).name or "post-openai.py"
    if msg:
        sys.stderr.write(f"error: {msg}\n")
    sys.stderr.write(
        f"usage: API_BASE_URL=<url> API_KEY_FILE=<api-key-file> " f"MODEL=<model> {script} <messages.json>\n"
    )
    sys.stderr.write(
        f"optional envar param MAX_TOKENS=<max-response-token-count> " f"(default: {DEFAULT_MAX_TOKENS})\n"
    )
    sys.stderr.write("optional envar param TEMPERATURE=<float in [0.0, 2.0]> (default: 0.0)\n")
    sys.stderr.write(
        "API_KEY_FILE file format: the file contains only the API key "
        "(leading/trailing whitespace trimmed; no internal whitespace).\n"
    )
    sys.exit(1)


def _parse_temperature(raw: str) -> float:
    if not _TEMPERATURE_RE.match(raw):
        sys.stderr.write(f"error: TEMPERATURE must be a non-negative number (got '{raw}')\n")
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


def extract_usage(chunks: list[dict]) -> dict:
    """Extract token usage and model id from parsed response objects.

    Covers both response shapes with one scan: a non-streaming full completion
    object (usage at the top level beside `choices`), and SSE streams — where
    usage typically rides a late chunk with empty `choices` (the shape
    `reassemble_stream` skips) or on the final delta chunk. The last object
    carrying each field wins; fields the API never provided are None.
    """
    usage: dict = {}
    model = None
    for obj in chunks:
        u = obj.get("usage")
        if isinstance(u, dict):
            usage = u
        m = obj.get("model")
        if isinstance(m, str) and m:
            model = m
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "model": model,
    }


def extract_finish_reason(chunks: list[dict]) -> str | None:
    """Return the last non-empty ``choices[0].finish_reason``, or None.

    None means the endpoint never said why it stopped, which is treated as
    complete: some servers omit the field entirely.
    """
    reason = None
    for obj in chunks:
        choices = obj.get("choices") or []
        if not choices:
            continue
        value = choices[0].get("finish_reason")
        if isinstance(value, str) and value:
            reason = value
    return reason


def write_usage_stats(stats_path: str, stats: dict) -> None:
    """Append one JSON line to the USAGE_STATS_FILE side channel.

    Advisory only: any write failure is a stderr warning, never a transport
    failure, and nothing about the side channel ever reaches stdout.
    """
    try:
        with open(stats_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(stats) + "\n")
    except OSError as e:
        sys.stderr.write(f"warning: could not write USAGE_STATS_FILE '{stats_path}': {e}\n")


def list_models(base_url: str, token: str) -> list[str]:
    """GET /models — return the data[].id list. Raises on transport errors."""
    req = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    with _OPENER.open(req, timeout=30) as resp:
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
        with _OPENER.open(req, timeout=600) as resp:
            line_iter = (line.decode("utf-8", errors="replace") for line in resp)
            chunks, status = demux_sse(
                line_iter,
                debug_sink=debug_sink,
                raw_buffer=raw_buffer,
                chunk_payloads=chunk_payloads,
            )
    except RedirectRefused as e:
        sys.stderr.write(f"error: {e.reason}\n")
        return [], [], "", 1
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


def main() -> int:
    api_base_url = os.environ.get("API_BASE_URL", "")
    api_key_file = os.environ.get("API_KEY_FILE", "")
    model = os.environ.get("MODEL", "")

    args = sys.argv[1:]
    allow_http_flag = "--allow-http" in args
    if allow_http_flag:
        args = [a for a in args if a != "--allow-http"]
    allow_http = allow_http_flag or os.environ.get("ALLOW_HTTP", "") == "1"

    if not api_base_url:
        _usage("API_BASE_URL must be set")
    url_error = validate_base_url(api_base_url, allow_http=allow_http)
    if url_error:
        _usage(url_error)
    if not api_key_file:
        _usage("API_KEY_FILE must be set")
    key_path = Path(api_key_file)
    if not key_path.is_file():
        _usage(f"API_KEY_FILE not found: {api_key_file}")

    token = _read_api_key(key_path)
    if token is None:
        _usage(f"API_KEY_FILE has invalid format: {api_key_file}")
        token = ""  # shut the linter up. it's not catching that _usage exits.

    if not model:
        _usage("MODEL must be set")

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
    usage_stats_file = os.environ.get("USAGE_STATS_FILE", "")

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
        sys.stderr.write(f"warning: model '{model}' not found — attempting substring resolution\n")
        resolved = resolve_model(model, api_base_url, token)
        if resolved is None:
            sys.stderr.write("error: update MODEL to a valid model name\n")
            return 1
        sys.stderr.write(f"warning: resolved '{model}' → '{resolved}' — " "update MODEL to avoid this fallback\n")
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
        sys.stderr.write(reassembled if reassembled.endswith("\n") else reassembled + "\n")

    finish_reason = extract_finish_reason(chunks)

    # The call completed and the tokens were spent, whatever the verdict below
    # says about the reply's usability — so the side channel records it first.
    if usage_stats_file:
        write_usage_stats(usage_stats_file, extract_usage(chunks))

    incomplete = finish_reason is not None and finish_reason not in COMPLETE_FINISH_REASONS

    if not reassembled:
        # A protocol event, not a transport failure: retrying an empty
        # completion burns the caller's retries and halts its run.
        sys.stderr.write(f"error: endpoint returned an empty completion (finish_reason={finish_reason!r})\n")
        if raw_text:
            sys.stderr.write("--- raw body ---\n")
            sys.stderr.write(raw_text if raw_text.endswith("\n") else raw_text + "\n")
        return EXIT_INCOMPLETE if incomplete else EXIT_EMPTY

    if incomplete:
        # Emitted anyway: the caller needs the partial text for the audit
        # trail, and the exit code is what stops it being read as complete.
        sys.stderr.write(
            f"error: reply is incomplete — finish_reason={finish_reason!r} "
            "(expected one of " + ", ".join(sorted(COMPLETE_FINISH_REASONS)) + "); "
            "the text on stdout is a partial reply\n"
        )
        _emit(reassembled)
        return EXIT_INCOMPLETE

    _emit(reassembled)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
