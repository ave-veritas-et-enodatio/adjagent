"""On-demand inference, in two layers, for any tooling that wants some.

* :mod:`claude` — one headless ``claude`` call as a Python function. Named
  parameters, not argv; the response text and a result code back. It knows the
  CLI's shape and nothing else — not this agent set, not a KB, not this
  repository.
* :mod:`seat` — one call to a named seat of this agent set, built on the
  first. It knows what a seat is and how to name one, and nothing about what a
  seat is being asked to do.

Neither parses what comes back beyond taking the response text out of the
stream, and neither retries: policy of every kind is the caller's. Both are
tested against a substituted invoker, so nothing here needs a reachable model.
"""

from .claude import Invoker, Outcome, SubprocessInvoker, call_claude
from .seat import ask_seat

__all__ = ["Invoker", "Outcome", "SubprocessInvoker", "ask_seat", "call_claude"]
