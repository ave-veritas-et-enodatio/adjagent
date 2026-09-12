"""The write-op contract's two sources, held to one vocabulary (A5.4 W3, M13).

The same agent-facing contract is stated twice, deliberately:

* ``kb_tools/kb_driver/prompt-templates/fragments/write-op-contract.tmpl`` — the fragment the
  driver composes into every brief that names a write op, read by a distiller
  mid-wave;
* ``templates/shared-chunks.toml``'s ``kb-metadata-write`` chunk — expanded
  into the distiller and maintainer definitions, read by a seat that may have
  no brief at all.

The A5.4 ruling on that duplication was **blessed, sync obligation refused**:
the two bodies serve different readers and are meant to stay separately worded.
What it refused to leave standing was the obligation that a human *notice* the
second copy when editing the first — an exhaustiveness guarantee left on
inference. This module is what replaces it. It is the only place naming both
sources, and it asserts one token vocabulary of each, so editing either body
into silence about a load-bearing token fails the suite rather than shipping.

Tokens, not sentences: the assertions have to survive a rewording of either
body, because separate wording is the ruling. What they may not survive is a
body that has stopped carrying the fact. Where the two bodies spell a fact
differently — "re-run the identical call" against "Re-run the identical
invocation" — the token is the part they share.

The fragment's *composition-side* guard lives with the driver, in
``kb_tools/tests/test_kb_driver_prompt_templates.py``: that pair asserts the fragment
still states the exit-8 rule and that every composed brief naming a write op
carries the fragment. It is the teeth for a different property — that the
contract reaches the brief — and it can say nothing about the chunk, which the
driver never loads.
"""

import tomllib
from pathlib import Path

import pytest

from kb_tools.kb_driver import prompt_templates

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHARED_CHUNKS = _REPO_ROOT / "templates" / "shared-chunks.toml"
_CHUNK_NAME = "kb-metadata-write"

#: What both bodies must carry, and what each token is load-bearing *for*. Every
#: one of these is a fact a seat acts on mid-call, which is why a body that has
#: quietly lost one is a defect rather than a style drift.
CONTRACT_TOKENS: tuple[tuple[str, str], ...] = (
    # M11's transport. A seat that thinks values travel on the command line
    # writes a call the op refuses.
    ("values file", "values travel in a file the caller writes"),
    # M13's exit ladder. 7 and 8 are separated precisely so the caller branches
    # differently on them; a body naming one and not the other collapses that.
    ("**7**", "refused, nothing written — correct the field and call again"),
    ("**8**", "contended — the outcome a *correct* set of values can still get"),
    # The whole point of separating 8 from 7: re-run, do not re-author. Both
    # bodies carry the word in their own sentence.
    ("identical", "the re-run is of the same call on the same file"),
    ("three times", "the retry bound, so a contended file is reported not looped"),
)


def _chunk_text() -> str:
    """The ``kb-metadata-write`` chunk body, as the generator resolves it."""
    data = tomllib.loads(_SHARED_CHUNKS.read_text(encoding="utf-8"))
    chunks = data["chunks"]
    assert _CHUNK_NAME in chunks, f"{_CHUNK_NAME} is gone from {_SHARED_CHUNKS.name}"
    return chunks[_CHUNK_NAME]["text"]


@pytest.mark.parametrize(("token", "carries"), CONTRACT_TOKENS, ids=[token for token, _ in CONTRACT_TOKENS])
def test_the_definition_chunk_carries_the_contract(token: str, carries: str) -> None:
    """The half W3 found unguarded: the chunk the distiller and maintainer expand."""
    assert token in _chunk_text(), carries


@pytest.mark.parametrize(("token", "carries"), CONTRACT_TOKENS, ids=[token for token, _ in CONTRACT_TOKENS])
def test_the_brief_fragment_carries_the_same_contract(token: str, carries: str) -> None:
    """The other half, asserted here so one file fails on an edit to either source.

    Loaded raw rather than composed: the fragment's own slot (``@!values-flag!@``)
    is the invocation's flag, and no token above is inside it.
    """
    assert token in prompt_templates.load(prompt_templates.FRAGMENTS["write-op-contract"]), carries
