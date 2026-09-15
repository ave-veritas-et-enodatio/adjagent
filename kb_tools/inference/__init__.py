"""On-demand inference, in two layers, for any tooling that wants some.

* :mod:`claude` — one headless ``claude`` call: spawn, stream capture,
  watchdog, process-group kill, and the one classification of how a call ended.
  It knows the CLI's shape and nothing else — not this agent set, not a KB, not
  this repository. :func:`invoke` returns the whole :class:`CallResult`;
  :func:`call_claude` builds the argv, spools the prompt, and returns the
  response text and the outcome.
* :mod:`seat` — one call to a named seat of this agent set, built on the
  first. It knows what a seat is and how to name one, and nothing about what a
  seat is being asked to do.

Neither parses what comes back beyond taking the response text out of the
stream, and neither retries: policy of every kind — retry, a re-ask, where a
bound's value comes from, one call at a time — is the caller's. Both are tested
against a substituted invoker, so nothing here needs a reachable model.
"""

from .claude import (
    NO_PROCESS_STATUS,
    STREAM_FLAGS,
    CallResult,
    Invoker,
    Outcome,
    SubprocessInvoker,
    build_argv,
    call_claude,
    invoke,
)
from .seat import ask_seat

#: The logger tree this package writes to. It attaches no handler of its own —
#: an application wanting a call's spawn, kill and classification lines in its
#: own log attaches its handlers here, and one that attaches none gets
#: ``logging``'s default handling of a warning.
LOGGER_NAME = __name__

__all__ = [
    "LOGGER_NAME",
    "NO_PROCESS_STATUS",
    "STREAM_FLAGS",
    "CallResult",
    "Invoker",
    "Outcome",
    "SubprocessInvoker",
    "ask_seat",
    "build_argv",
    "call_claude",
    "invoke",
]
