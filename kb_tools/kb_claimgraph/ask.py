"""This package's two inferences: what is asked, how it is asked, what is read back.

**Inference arrives by injection, as a seat and a prompt** —
:class:`SeatAsk`, satisfied in production by
:func:`kb_tools.inference.ask_seat`. That signature is the whole of this
module's dependency on the machinery behind it: a seat name and a prompt go in,
response text and an :class:`~kb_tools.inference.Outcome` come back, and nothing
about argv, a subprocess, a process group or a stream format is visible here.
The layer that satisfies it knows the agent set and nothing about this KB; the
layer under *that* knows neither.

**Neither ask is written here.** Both are templates under
``kb_driver/prompt-templates/``, filled through
:func:`kb_tools.kb_driver.prompt_templates.render`, so an ask lands in a diff as
prose and can be read end to end by the person who writes prose. Two mechanisms
carry what varies, and which one a condition takes turns on what it does to the
ask. Where a specific needs tailoring and the question is unchanged, the
alternatives are **fragments** and this module picks one **by name**, the
composer loading and resolving whichever it named: C-inf's display-maths
section, the correction a re-ask of either stage carries, and the evidence
C-inf's per-claim re-ask shows. Where the question itself differs, there is an
**alternate template**: stage D's acyclicity re-ask, and C-inf's per-claim
re-ask, which asks about one claim where the ask before it asked about a
document. No template carries a conditional, and nothing here composes prose or
holds any — a choice is all that leaves this module for a prompt, beside the
values a call computes and the markers in :data:`MARKER_SLOTS`.

**Nothing usage-specific happens on the way back, either.** The response is
text, and each parse below is its stage's own —
:mod:`kb_tools.kb_driver.envelope`'s record toolkit, which extracts a marked
block, decodes it, checks a closed vocabulary in both directions, and reads
every key carrying words out of a raw block beside the JSON. Parse only, never
quality. Neither answer has a verdict field, a confidence field or free text,
because there is nothing in either for a model to say about its own work.

**Nothing either seat composes or quotes travels inside JSON**, and each ask
declares which transport each of its keys travels in (:data:`LEVELS`, proved a
partition of the key vocabulary at import). Stage D declares JSON and nothing
else: its answer is a claim id and an array of candidate ids, and nothing there
is composed. That declaration is still work rather than a formality — it is
**an empty prose vocabulary**, and what it buys is that a stray prose block in a
stage-D answer is a refusal instead of something nobody looked for. C-inf
declares the opposite: no JSON at all, and a closed set of single-line fields in
a raw block (:data:`CLAIM_FIELDS`).

**Two failure paths, and they are different failures.** A call that did not
complete is re-issued *identically* while the layer below calls its outcome
retryable, bounded by :data:`TRANSPORT_ATTEMPTS` — the same shape the write
path applies to the write API's retry code, and not a fix loop: the prompt does
not change and nothing is asked about the failure. A call the layer below calls
unretryable stops the stage at once. Neither is a stage's re-ask, which is spent
on an answer that **arrived**. A parse refusal is
:class:`~.report.AnswerFormatError`, declared in :mod:`report` because this
module imports from both of the stages that catch it.

**A re-ask carries a fact computed after the previous answer, or it is a spent
call.** Re-issuing the question with the rule restated went out twice against
one live document and came back with the same slip in the same place. So stage
D's acyclicity re-ask carries the cycle the answers so far close, and C-inf's
per-claim re-ask carries the sentences the tool actually found — with an
explicit "none of these", because a forced choice over a narrowed window gets
confidently answered even when the right sentence lies outside it.

**Stage D's ask is a selection from an enumerated set.** One source claim, the
candidate targets that stage D's mechanical narrowing offered it, and the
reference lines those candidates were enumerated from. What comes back is a
subset of the ids that were handed over.

**Stage C-inf's ask is the one open-ended reading in this build.** One document,
rendered one labelled sentence per line (:mod:`label`), and what results it
states — each as a self-contained block carrying the **quote** the result begins
at, the **label** of that sentence, and a **title** the seat authors.

**The model quotes; the tool addresses.** The quote is a lookup key and never
content: it resolves to a sentence, and then it is discarded and the tool cuts
the bytes, so no model-typed byte reaches the KB and there is still nothing for
a verbatimness check to establish. What changes against the shape before it is
that the address is now **verified** rather than trusted — a label the seat
named alone was a pointer, and a wrong pointer is a well-formed record aimed at
the wrong span that no downstream check can tell from a right one. The label
survives as the cross-check that catches exactly that divergence
(:mod:`identify`).

**Both go to the same seat, and it is a constructor parameter in both.** The
seat's native discipline is verbatim correspondence, which is the one property
either stage can check, and a seat whose instincts and whose gate agree fails
visibly rather than plausibly. It stays injected so a run can move it.

**The seam is what makes both testable.** Every check over either stage runs
against a fixed :class:`SeatAsk`, because a check that needs a model to be
reachable is a check that does not run.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from ..inference import Outcome, ask_seat
from ..kb_driver import envelope, prompt_templates
from . import label
from .attribute import Question
from .graph import ClaimNode
from .identify import Answer, Reading, Record, Trigger, Unresolved
from .report import AnswerFormatError, ClaimGraphError

#: The block stage D's answer travels in. One JSON document between two marker
#: lines, matched as whole lines, so the markers can be named in prose without
#: opening a block.
ANSWER_NAME = "KB-CLAIMGRAPH-DEPENDS"
ANSWER_OPEN = f"<<<{ANSWER_NAME}"
ANSWER_CLOSE = ANSWER_NAME

#: Closed and total, in both directions: a key left out is refused by name and
#: so is a key added.
ANSWER_KEYS: tuple[str, ...] = ("claim", "depends-on")

#: The block stage D's own words would travel in, one per value, numbered — the
#: transport an answer declaring no prose key must still refuse rather than
#: ignore. A prose block's opener carries a number after it, so the marker is
#: the name with the delimiter on it and nothing more.
PROSE_NAME = "KB-CLAIMGRAPH-PROSE"
PROSE_OPEN = f"<<<{PROSE_NAME}"

#: One result C-inf reports: a raw block carrying a closed, fixed set of
#: single-line fields, and **no number**. The answer is a flat sequence of these
#: in any order, so nothing in it points at anything else in it.
CLAIM_NAME = "KB-CLAIMGRAPH-CLAIM"
CLAIM_OPEN = f"<<<{CLAIM_NAME}"
CLAIM_CLOSE = CLAIM_NAME

#: Closed and total, in both directions, on :func:`envelope.check_keys`' terms.
#: Each is one line: ``label.render`` emits one sentence per line with the wraps
#: collapsed, so a quoted sentence cannot contain a newline.
CLAIM_FIELDS: tuple[str, ...] = ("quote", "label", "title")

#: The block a document stating no result of its own returns instead, holding
#: the one sentence saying so. Zero claim blocks *alone* is not an answer — it
#: is indistinguishable from a failed one — and this block is what makes
#: "states none" a positive assertion.
NO_CLAIM_NAME = "KB-CLAIMGRAPH-NO-CLAIM"
NO_CLAIM_OPEN = f"<<<{NO_CLAIM_NAME}"
NO_CLAIM_CLOSE = NO_CLAIM_NAME

#: The answer a per-claim re-ask's menu must always admit: none of the sentences
#: offered is the one the result begins at. It ends that claim as unresolved
#: rather than re-asking, because a forced choice over a narrowed window gets
#: confidently answered even when the right sentence lies outside it.
NONE_OF_THESE_NAME = "KB-CLAIMGRAPH-NONE-OF-THESE"
NONE_OF_THESE_OPEN = f"<<<{NONE_OF_THESE_NAME}"
NONE_OF_THESE_CLOSE = NONE_OF_THESE_NAME

#: Which transport each key of each ask travels in. Stage D declares an **empty
#: prose vocabulary** and that is a check rather than a formality: nothing in
#: its answer is composed, and a prose block arriving in one is refused rather
#: than ignored. C-inf declares the other direction — no JSON at all.
LEVELS: tuple[envelope.Level, ...] = (
    envelope.Level("depends", ANSWER_KEYS, json=ANSWER_KEYS, prose=()),
    envelope.Level("identify claim", CLAIM_FIELDS, fields=CLAIM_FIELDS),
)

envelope.check_levels(LEVELS)

#: How many times a call the layer below calls retryable is re-issued
#: **identically** before the stage stops. The layer below retries nothing by
#: design — policy is the caller's — so a bound belongs here or nowhere, and a
#: writer that never yields is a wedge rather than a retry.
TRANSPORT_ATTEMPTS = 3

#: The templates each ask is composed from — a template being a whole ask,
#: dispatched as it stands.
DEPENDS_TEMPLATE = "depends.tmpl"
DEPENDS_CYCLE_TEMPLATE = "depends-cycle-reask.tmpl"
IDENTIFY_TEMPLATE = "identify.tmpl"
IDENTIFY_REASK_TEMPLATE = "identify-claim-reask.tmpl"

#: This package's composer constants — the analogue of ``steps.CONSTANT_SLOTS``
#: for the two asks, drawn on only where a template names one. Every value here
#: is a marker literal :func:`parse_answer` or :func:`parse_identify_answer`
#: matches an answer against, so it reaches a prompt as a slot rather than as
#: template text: a template spelling one would be a second definition of a
#: string the parse holds the seat to, and a rename would leave the ask asking
#: for what the parse no longer accepts. They are constants and never per-call
#: values, which is why they travel through ``constants`` and not through the
#: caller's own slots.
MARKER_SLOTS: Mapping[str, str] = MappingProxyType(
    {
        "answer-open": ANSWER_OPEN,
        "answer-close": ANSWER_CLOSE,
        "prose-open": PROSE_OPEN,
        "prose-name": PROSE_NAME,
        "claim-open": CLAIM_OPEN,
        "claim-close": CLAIM_CLOSE,
        "no-claim-open": NO_CLAIM_OPEN,
        "no-claim-close": NO_CLAIM_CLOSE,
        "none-of-these-open": NONE_OF_THESE_OPEN,
        "none-of-these-close": NONE_OF_THESE_CLOSE,
    }
)

#: The slots whose alternatives this module chooses between, and the choices it
#: makes. A choice travels as the alternative's registered name and never as its
#: prose: where a fragment lives, and what it says, are
#: :mod:`kb_tools.kb_driver.prompt_templates`' knowledge and the prompt
#: engineer's respectively, and neither is this module's to hold.
#:
#: ``CORRECTION`` serves both asks — the same subject, the same question, and
#: the mechanical failure the previous answer produced supplied beneath it,
#: chosen against a first ask's ``None``, which fills the slot with nothing. The
#: other two are C-inf's display-maths section, whichever of the two a document
#: takes. Each is spliced into a slot sitting inside a line of its template,
#: which is why none of their files ends in a newline.
#: ``CLAIM_EVIDENCE`` is the third, and it belongs to the per-claim re-ask alone:
#: what the tool found differs by *how* the quote failed to settle, and the
#: framing of the menu differs with it. It is registered here rather than edited
#: into ``ask-correction`` because that fragment is **shared** — stage D fills
#: the same slot from it — so tailoring it for C-inf would change stage D's
#: re-ask.
CORRECTION_SLOT = "correction"
DISPLAY_MATHS_SLOT = "display-maths"
CLAIM_EVIDENCE_SLOT = "claim-evidence"
CORRECTION = "ask-correction"
DISPLAY_MATHS = "identify-display-maths"
NO_DISPLAY_MATHS = "identify-no-display-maths"

#: Which evidence fragment each trigger's re-ask is composed from. The mapping
#: is here and the trigger is :mod:`identify`'s, because what happened is the
#: stage's knowledge and how it is put to a seat is this module's.
CLAIM_EVIDENCE: Mapping[Trigger, str] = MappingProxyType(
    {
        Trigger.NOWHERE: "identify-reask-nowhere",
        Trigger.SEVERAL: "identify-reask-several",
        Trigger.DISAGREED: "identify-reask-disagreed",
    }
)

#: The seat both asks are put to. It is KB-agnostic — nothing it is told
#: presumes a knowledge base — which is the property both asks need: each
#: carries its whole subject in the prompt, and every property of what comes
#: back is checked against that subject rather than trusted. Stage D asks it
#: which of a set of claims another rests on; C-inf asks it what a document
#: states. It is a default here and a constructor parameter on both consumers,
#: so a run can move it without a code change.
SEAT = "applied-mathematician"


class AskError(ClaimGraphError):
    """The call did not come back. No answer arrived, so no re-ask can be spent on one.

    What came back and does not carry the answer is
    :class:`~.report.AnswerFormatError` instead, and the difference is the whole
    of why there are two: one of them is a stage's to retry.
    """


class SeatAsk(Protocol):
    """One call to a named seat. The seam this package reaches inference through.

    :func:`kb_tools.inference.ask_seat` is what satisfies it in production.
    Keyword-only, deliberately: ``seat`` and ``prompt`` are two strings of the
    same type that would swap silently if either were positional.
    """

    def __call__(
        self,
        *,
        seat: str,
        prompt: str,
        cwd: Path | None = None,
        capture_path: Path | None = None,
    ) -> tuple[str, Outcome]:
        """Put ``prompt`` to ``seat``; return what came back and how the call ended."""


def _record_ask(workspace: Path | None, stem: str, prompt: str) -> Path | None:
    """Land one ask's prompt beside where its captured stream will land.

    A run's inference is on disk as it happens, so a failed answer can be read
    rather than described.
    """
    if workspace is None:
        return None
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / f"{stem}.prompt.md").write_text(prompt, encoding="utf-8")
    return workspace / f"{stem}.capture.jsonl"


def _put(ask: SeatAsk, *, seat: str, prompt: str, cwd: Path, capture_path: Path | None, subject: str) -> str:
    """One call, re-issued identically while the layer below calls its outcome retryable."""
    for attempt in range(TRANSPORT_ATTEMPTS):
        response, outcome = ask(seat=seat, prompt=prompt, cwd=cwd, capture_path=capture_path)
        if outcome.ok:
            return response
        if not outcome.retryable or attempt == TRANSPORT_ATTEMPTS - 1:
            break
    raise AskError(
        "inference-failed",
        f"{subject}: the call to {seat!r} ended {outcome.value} and "
        f"{'was re-issued identically to no effect' if outcome.retryable else 'is not retryable'}. "
        f"Nothing was written, and no answer is assumed for a call that did not complete"
        + (f". Capture: {capture_path}" if capture_path is not None else ""),
    )


# --- stage D: which of these claims does this claim rest on ------------------


def _claim_line(node: ClaimNode) -> str:
    where = node.locator or node.document
    return f"- `{node.id}` — {node.title} (stated in `{node.document}`: {where})"


def _correction(report: str | None) -> tuple[dict[str, str], dict[str, str | None]]:
    """The re-ask correction as the composer takes it: the choice, and what it needs.

    A first ask chooses nothing and supplies nothing; a re-ask chooses the
    correction and supplies the report the chosen body's own slot names. The two
    move together, which is why one call answers for both.
    """
    if report is None:
        return {}, {CORRECTION_SLOT: None}
    return {"report": report}, {CORRECTION_SLOT: CORRECTION}


def _question_slots(question: Question) -> dict[str, str]:
    """What either stage-D template's per-call slots are: the claim, its candidates, their lines."""
    return {
        "claim-line": _claim_line(question.source),
        "candidate-lines": "\n".join(_claim_line(candidate) for candidate in question.candidates),
        "reference-lines": "\n".join(f"- {line}" for line in question.evidence),
        "claim-id": question.source.id,
    }


def compose_prompt(question: Question, *, report: str | None) -> str:
    """The prompt for one source claim. Everything the question needs, and no document to read."""
    slots, alternatives = _correction(report)
    return prompt_templates.render(
        DEPENDS_TEMPLATE,
        slots={**_question_slots(question), **slots},
        constants=MARKER_SLOTS,
        alternatives=alternatives,
    )


def compose_cycle_prompt(question: Question, *, cycle: str) -> str:
    """The acyclicity re-ask: the same claim, with the cycle the answers so far close.

    A template of its own rather than a correction on the one above, because what
    is asked differs: this is a constrained revision, and it is the only ask in
    this build that puts another question's answer in front of the seat.
    """
    return prompt_templates.render(
        DEPENDS_CYCLE_TEMPLATE,
        slots={**_question_slots(question), "cycle-path": cycle},
        constants=MARKER_SLOTS,
    )


def parse_answer(text: str, *, claim: str) -> tuple[str, ...]:
    """The selected ids, or :class:`~.report.AnswerFormatError`. Parse only — structure, never quality.

    Every key here is an id, so the prose declaration is empty and
    ``check_exhausted`` is the whole of what it buys: a prose block in this
    answer is a seat composing something this ask did not request, and that is
    refused on the same argument :func:`envelope.check_keys` makes about an
    invented key.
    """
    try:
        payload, prose = envelope.parse_record(
            text, open_marker=ANSWER_OPEN, close_marker=ANSWER_CLOSE, prose_name=PROSE_NAME, label="depends"
        )
        if not isinstance(payload, dict):
            raise envelope.ParseError(f"depends: the block must be a JSON object, got {type(payload).__name__}")
        envelope.check_keys(payload, ANSWER_KEYS, label="depends")
        declared = envelope.string_field(payload, "claim", label="depends")
        if declared != claim:
            raise envelope.ParseError(f"depends: the block answers for {declared!r}, but this ask is {claim!r}")
        selected = envelope.string_array(payload["depends-on"], label="depends: depends-on")
        prose.check_exhausted()
        return selected
    except envelope.ParseError as error:
        raise AnswerFormatError(str(error)) from error


class ModelSelector:
    """:class:`~.attribute.Selector` over a :class:`SeatAsk`.

    ``workspace`` is where each call's prompt and captured stream land, so a
    run's inference is on disk as it happens and a failed answer can be read
    rather than described. ``cwd`` is what decides which project's installed
    agent set the seat name resolves against.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        ask: SeatAsk = ask_seat,
        workspace: Path | None = None,
        seat: str = SEAT,
    ) -> None:
        self._cwd = cwd
        self._ask = ask
        self._workspace = workspace
        self._seat = seat
        self._calls = 0

    def select(self, question: Question, *, report: str | None, cycle: str | None = None) -> tuple[str, ...]:
        prompt = (
            compose_cycle_prompt(question, cycle=cycle)
            if cycle is not None
            else compose_prompt(question, report=report)
        )
        capture_path = _record_ask(self._workspace, f"{self._calls:03d}-{question.source.id}", prompt)
        self._calls += 1
        response = _put(
            self._ask,
            seat=self._seat,
            prompt=prompt,
            cwd=self._cwd,
            capture_path=capture_path,
            subject=question.source.id,
        )
        return parse_answer(response, claim=question.source.id)


def answer_block(claim: str, depends_on: Sequence[str]) -> str:
    """The answer block for ``claim``, composed rather than typed.

    Used to state an expected answer without hand-writing the format the parser
    reads — the same demand this module refuses to make of a model's output is
    one no caller should make of itself either.
    """
    payload = json.dumps({"claim": claim, "depends-on": list(depends_on)}, ensure_ascii=False)
    return f"{ANSWER_OPEN}\n{payload}\n{ANSWER_CLOSE}\n"


# --- stage C-inf: what results does this document state ----------------------


def _fence_labels(reading: Reading, rendered: label.Render) -> list[str]:
    """The labels whose lines sit inside a display-maths fence.

    Stated as labels rather than as line numbers because labels are the only
    coordinates the seat is given: a line range names nothing it can see.
    """
    spans = [range(fence.start, fence.end) for fence in reading.fences]
    return [sentence.label for sentence in rendered.sentences if any(sentence.line in span for span in spans)]


def compose_identify_prompt(reading: Reading, *, report: str | None) -> str:
    """The prompt for one document. The document, and nothing else in the corpus.

    No other document, no previous answer and no other document's answer: the
    question is what *this* file states, and anything else in front of it is a
    second document's result available to be attributed to this one.
    """
    rendered = reading.render
    fenced = _fence_labels(reading, rendered)
    slots, alternatives = _correction(report)
    return prompt_templates.render(
        IDENTIFY_TEMPLATE,
        slots={
            "document": reading.document,
            "body": rendered.text.rstrip("\n"),
            **({"fenced-labels": ", ".join(fenced)} if fenced else {}),
            **slots,
        },
        constants=MARKER_SLOTS,
        alternatives={DISPLAY_MATHS_SLOT: DISPLAY_MATHS if fenced else NO_DISPLAY_MATHS, **alternatives},
    )


def _claim_records(text: str, *, label: str) -> envelope.FieldBlocks:
    return envelope.extract_field_blocks(text, name=CLAIM_NAME, keys=CLAIM_FIELDS, label=label)


def _records_of(blocks: envelope.FieldBlocks) -> tuple[Record, ...]:
    return tuple(
        Record(quote=fields["quote"], label=fields["label"], title=fields["title"]) for fields in blocks.blocks
    )


def parse_identify_answer(text: str, *, document: str) -> Answer:
    """The claim blocks and the reason, or :class:`~.report.AnswerFormatError`. Parse only, never quality.

    **A malformed claim block costs that block and nothing else**, so it comes
    back in :attr:`Answer.refusals` rather than as an exception. What *is* an
    exception is an answer this parse cannot read at all: a block left unclosed,
    which swallows every block behind it, or a returned text carrying neither
    kind of block, which is indistinguishable from a call that said nothing.

    ``document`` names the ask in a refusal and is not compared against anything
    the answer declares: there is no declaration to compare it to. The answer's
    blocks carry no path, and the check that used to be made against one is made
    more strongly downstream — a quote lifted from another document resolves
    nowhere in this one.
    """
    label = f"identify {document}"
    try:
        found = _claim_records(text, label=label)
        reasons = envelope.extract_blocks(text, open_marker=NO_CLAIM_OPEN, close_marker=NO_CLAIM_CLOSE, label=label)
        if len(reasons) > 1:
            raise envelope.ParseError(
                f"{label}: {len(reasons)} {NO_CLAIM_OPEN!r} blocks. A document states no result once or not "
                f"at all, and two reasons are two answers"
            )
        if not found.blocks and not found.refusals and not reasons:
            raise envelope.ParseError(
                f"{label}: the returned text carries no {CLAIM_OPEN!r} block and no {NO_CLAIM_OPEN!r} block, "
                f"so it states neither a result nor that there is none. Every answer is one or the other"
            )
    except envelope.ParseError as error:
        raise AnswerFormatError(str(error)) from error
    return Answer(claims=_records_of(found), no_claim=reasons[0].strip() if reasons else "", refusals=found.refusals)


def identify_answer_block(claims: Sequence[Record] = (), no_claim: str = "") -> str:
    """C-inf's answer as a seat returns it: one block per claim, or the one reason block.

    Composed rather than typed, wherever a caller needs to *state* an answer —
    the demand this format refuses to make of a model's output is one no caller
    should make of itself either.
    """
    blocks = [
        envelope.field_block({"quote": record.quote, "label": record.label, "title": record.title}, name=CLAIM_NAME)
        for record in claims
    ]
    if no_claim:
        blocks.append(f"{NO_CLAIM_OPEN}\n{no_claim}\n{NO_CLAIM_CLOSE}\n")
    return "".join(blocks)


# --- C-inf's per-claim re-ask ------------------------------------------------


def compose_claim_reask_prompt(reading: Reading, unresolved: Unresolved) -> str:
    """The re-ask for one claim: what the seat named, and the sentences the tool found.

    **One call per unresolved claim, not one per document.** Isolating the ask
    is what raises the odds on each, and every value below was computed *after*
    the previous answer — which is the property that distinguishes a re-ask from
    a re-issue.
    """
    record = unresolved.record
    return prompt_templates.render(
        IDENTIFY_REASK_TEMPLATE,
        slots={
            "document": reading.document,
            "quote": record.quote,
            "label": record.label,
            "title": record.title,
            "candidate-lines": "\n".join(f"- {line}" for line in unresolved.candidates),
        },
        constants=MARKER_SLOTS,
        alternatives={CLAIM_EVIDENCE_SLOT: CLAIM_EVIDENCE[unresolved.trigger]},
    )


def parse_claim_reask_answer(text: str, *, document: str) -> Record | None:
    """The one claim block a re-ask settled on, or ``None`` for "none of these".

    ``None`` is an answer and not a failure: it ends that claim as unresolved
    without a further call, which is what keeps a narrowed menu from forcing a
    confident wrong choice.
    """
    label = f"identify re-ask {document}"
    try:
        declined = envelope.extract_blocks(
            text, open_marker=NONE_OF_THESE_OPEN, close_marker=NONE_OF_THESE_CLOSE, label=label
        )
        found = _claim_records(text, label=label)
        records = _records_of(found)
        if declined and not records:
            return None
        if len(records) != 1 or declined:
            raise envelope.ParseError(
                f"{label}: a re-ask about one claim is answered by exactly one {CLAIM_OPEN!r} block or by one "
                f"{NONE_OF_THESE_OPEN!r} block; this answer carries {len(records)} and {len(declined)}"
                + (f". {found.refusals[0]}" if found.refusals else "")
            )
    except envelope.ParseError as error:
        raise AnswerFormatError(str(error)) from error
    return records[0]


class ModelIdentifier:
    """:class:`~.identify.Identifier` over a :class:`SeatAsk`.

    ``workspace`` is where each call's prompt and captured stream land, so a
    run's inference is on disk as it happens. ``cwd`` is what decides which
    project's installed agent set the seat name resolves against.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        ask: SeatAsk = ask_seat,
        workspace: Path | None = None,
        seat: str = SEAT,
    ) -> None:
        self._cwd = cwd
        self._ask = ask
        self._workspace = workspace
        self._seat = seat
        self._calls = 0

    def _put_identify(self, prompt: str, *, subject: str, stem: str) -> str:
        capture_path = _record_ask(self._workspace, f"{self._calls:03d}-{stem}", prompt)
        self._calls += 1
        return _put(
            self._ask,
            seat=self._seat,
            prompt=prompt,
            cwd=self._cwd,
            capture_path=capture_path,
            subject=subject,
        )

    def identify(self, reading: Reading, *, report: str | None) -> Answer:
        stem = reading.document.replace("/", "_")
        response = self._put_identify(
            compose_identify_prompt(reading, report=report), subject=reading.document, stem=stem
        )
        return parse_identify_answer(response, document=reading.document)

    def re_ask(self, reading: Reading, unresolved: Unresolved) -> Record | None:
        stem = f"{reading.document.replace('/', '_')}-{unresolved.trigger}"
        response = self._put_identify(
            compose_claim_reask_prompt(reading, unresolved), subject=reading.document, stem=stem
        )
        return parse_claim_reask_answer(response, document=reading.document)
