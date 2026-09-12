"""The integration chain's contract, as tests.

The contract is one sentence with three clauses — *the three-way chain
green, the second refresh byte-identical, and the solidity line untouched by a
score update* — and this module is that sentence, run once as one chain.

**What makes this different from the unit tests.** Those
prove the ops against the semantics module, in process, with ``kb_root`` handed
in. Nothing there exercises the artifact an agent actually invokes. Every call
here goes through ``python3 -m kb_tools.kb_util <op> --values FILE`` as a
subprocess, from a working directory inside a synthetic consumer repo, with no
root override anywhere — argparse, subcommand dispatch, root discovery, the
adapter, the semantics and the process exit code, in the shape the briefs
sanction. ``refresh`` and ``verify`` are subprocesses of their own shipped
entry points for the same reason.

**The chain is the unit, so the fixture is the chain.** :func:`chain` runs the
ten ops in the order a distiller wave runs them — the endcap's work entry and
the citation-carrying rationale a later pass writes onto it among them — then
``refresh``, then the three gates, then a second ``refresh``, then one score
update, capturing what each step produced. The tests below read that capture. A
failure anywhere in the sequence fails the fixture and every test with it,
which is the honest report: a chain does not have independently passing links.

**Only one of the three gates is asserted clean.** ``mini-kb`` is a
reader-test fixture and its own prose names claim ids outside a sanctioned
channel, so ``verify_citations`` and ``verify_md_links`` are red over it before
this chain writes anything. What is asserted of those two is the claim that is
this chain's: neither reports a finding against the register the chain wrote.

**The corpus is one this program did not compose.** ``fixtures/mini-kb`` is the
hand-built KB the reader tests use — stale solidity lines, framework
dependencies, co-hosted supports — copied into a tmp tree and written there.
The one document whose *body* this module authors is the fresh leaf, which is
what the boundary says: the tool owns the metadata, the agent owns the prose.
Not one metadata byte below is typed.

**Two clauses are proven elsewhere and cited rather than rebuilt.**
``just kb-driver-dryrun`` exiting 0 is guarded by
``test_kb_driver_dryrun.py``; the census refusal on a legacy KB is
``test_kb_write_ops.py::TestRefusalFixtures::test_a_register_whose_census_already_fails``,
which drives the frozen 2026-09-02 corpus at a live op. Restating either here
would be a second copy of a proof, not a second proof.
"""

import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kb_tools import kb_index_lib
from kb_tools.kb_write import render

_THIS_DIR = Path(__file__).resolve().parent
# The directory holding the ``kb_tools`` package — the subprocess PYTHONPATH,
# never a source of the consumer repo root, which is discovered from the cwd.
_PKG_PARENT = _THIS_DIR.parent.parent
_FIXTURE_SRC = _THIS_DIR / "fixtures" / "mini-kb"

_REGISTER = "part-new/claim-quality.md"
_LEAF = "part-new/fresh-leaf.md"

# The off-graph endcap's node, and the authority its rationale ends up citing.
# The cited document is one of the fixture's own leaves: the excerpt is a
# clause of the section the anchor names, so `render-citation` can verify it
# and the citation gate can verify it again where the citation lands.
_WORK_KEY = "nobody2026"
_WORK_ID = f"work-{_WORK_KEY}"
_CITED_DOCUMENT = "common/leaf-single.md"
_CITED_ANCHOR = "anchor-result-restated"
_EXCERPT = "This leaf restates the anchor result A in a second location"

# The sentence ``mark-claim-in-leaf`` is asked to find. It is a whole line of
# the body below, and appears once.
_LOCATOR = "The saturation bound is restated here in full."

# The leaf body: prose, and only prose. It carries no frontmatter block and no
# marker when it is written — both arrive from the ops.
_LEAF_BODY = f"""[↑ Mini-KB Entry Point](../index.md)

# Freshly Distilled Leaf

A leaf whose body is a translation of its source and whose metadata this
program's ops composed.

{_LOCATOR}
"""


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(repo: Path, module: str, *args: str) -> subprocess.CompletedProcess:
    """Run a shipped entry point as a subprocess with the cwd inside ``repo``.

    No ``--kb-root`` and no ``--values`` path trickery: the three entry points
    discover the root by walking up from the working directory, exactly as they
    do under a brief.
    """
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot(kb_root: Path) -> dict[str, str]:
    """Every file under ``kb_root``, keyed by relative path, valued by digest.

    Byte-level by intent: the claim under test is *byte*-identity across a
    second refresh, over the whole tree — authored Markdown and the ``.index/``
    JSONL layer alike — so a text-level comparison would be a weaker claim than
    the one the row makes.
    """
    return {
        str(path.relative_to(kb_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(kb_root.rglob("*"))
        if path.is_file()
    }


def _entry_lines(register: Path, node_id: str) -> list[str]:
    """The lines of ``node_id``'s own entry, from its marker to the next rule.

    Read positionally rather than by scanning the whole file: a register holds
    several entries carrying the same field names, and an assertion that landed
    on a neighbour's line would pass on a byte the op never went near.
    """
    lines = register.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"<!-- id: {node_id} -->")
    end = next((i for i, line in enumerate(lines[start:], start) if line.startswith("---")), len(lines))
    return lines[start:end]


def _solidity_line(register: Path, node_id: str) -> str:
    """The one ``- solidity:`` line inside ``node_id``'s entry, verbatim."""
    return next(line for line in _entry_lines(register, node_id) if line.startswith("- solidity:"))


@dataclass(frozen=True)
class Chain:
    """What the chain produced, captured step by step as it ran."""

    repo: Path
    kb_root: Path
    register: Path
    claim_id: str
    support_id: str
    refresh: subprocess.CompletedProcess
    verify: subprocess.CompletedProcess
    md_links: subprocess.CompletedProcess
    citations: subprocess.CompletedProcess
    second_refresh: subprocess.CompletedProcess
    before_refresh: dict[str, str]
    after_refresh: dict[str, str]
    after_second_refresh: dict[str, str]
    # Read at the moment `verify` was green, so the later score update cannot
    # change what the first proof is asserting over.
    entry_at_green: kb_index_lib.ClaimEntry
    leaf_at_green: kb_index_lib.LeafRecord
    support_edges_at_green: tuple[tuple[str, object], ...]
    #: The `sup-id:` block step 8 stamped, as the production leaf reader returns
    #: it — the node the declaration originates, not the register's staging copy.
    hosted_support_at_green: kb_index_lib.SupportNode
    staged_at_green: tuple[tuple[str, object], ...]
    #: The citation `render-citation` composed, and the external work whose
    #: rationale was rewritten to carry it — the one register entry kind whose
    #: prose is composed mechanically at mint time and has no other author.
    citation: str
    work_at_green: kb_index_lib.ExternalWork
    work_rationale_at_mint: str
    # The score update: the same authored field, rewritten over a
    # solidity value refresh computed rather than one planted by the test.
    solidity_before_update: str
    solidity_after_update: str
    entry_after_update: kb_index_lib.ClaimEntry


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> Chain:
    repo = tmp_path_factory.mktemp("consumer")
    (repo / ".git").mkdir()
    kb_root = repo / "kb-root"
    shutil.copytree(_FIXTURE_SRC, kb_root)
    values_dir = repo / "values"
    values_dir.mkdir()

    def write_op(op: str, body: str, *extra: str) -> None:
        values = values_dir / f"{op}.toml"
        values.write_text(body, encoding="utf-8")
        result = _run(repo, "kb_tools.kb_util", op, "--values", str(values), *extra)
        assert result.returncode == 0, f"{op}: rc={result.returncode}\n{result.stdout}\n{result.stderr}"

    def read_op(op: str, body: str) -> str:
        """Run the one op that prints instead of writing, returning its stdout.

        Its answer is a *value* the next op consumes, which is the whole reason
        it exists: the citation's link target is spelled relative to the file
        the citation will sit in, and nothing in this chain types it.
        """
        values = values_dir / f"{op}.toml"
        values.write_text(body, encoding="utf-8")
        result = _run(repo, "kb_tools.kb_util", op, "--values", str(values))
        assert result.returncode == 0, f"{op}: rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        return result.stdout.strip()

    def minted(before: set[str]) -> str:
        """The id the last op brought into being, read off the store.

        Taken as the difference of the authored inventory across the call
        rather than parsed out of a report line: what the op minted is a fact
        about the KB, not a report string to lean on.
        """
        new = set(kb_index_lib.scan_authored_ids(kb_root)) - before
        assert len(new) == 1, f"expected exactly one new id, got {sorted(new)}"
        return new.pop()

    # The subtree and the leaf body, written before any op runs — the one
    # authored act in the chain (the tool owns the metadata, the agent
    # owns the prose). ``--create`` brings a register into being, never a
    # directory: a path whose parent does not exist is a refusal, so the
    # subtree a distiller is writing into exists before its metadata does.
    (kb_root / _LEAF).parent.mkdir(parents=True)
    (kb_root / _LEAF).write_text(_LEAF_BODY, encoding="utf-8")

    # 1 — a claim entry into a register that does not exist yet. --create is
    # the explicit acknowledgment required; without it this is a refusal.
    before = set(kb_index_lib.scan_authored_ids(kb_root))
    write_op(
        "insert-claim-entry",
        f"""
[[entry]]
register = "{_REGISTER}"
title = "Fresh Register Anchor Claim"
rigor = 0.8
rationale = "Written into a register the op itself brought into being."
strengthen-by = ["Derive the bound end to end."]
  [[entry.depends-on]]
  id = "clm-aa1111"
  context = "the anchor claim"
""",
        "--create",
    )
    claim_id = minted(before)

    # 2 — a support whose beneficiary fan-out is staged in the register entry:
    # the leaf that will host it does not exist yet.
    before = set(kb_index_lib.scan_authored_ids(kb_root))
    write_op(
        "insert-support-entry",
        f"""
[[entry]]
register = "{_REGISTER}"
title = "Fresh Support For The Anchor"
rigor = 0.7
rationale = "A support staged a stage before its hosting leaf is written."
  [[entry.supports]]
  id = "{claim_id}"
  fraction = 0.5
""",
    )
    support_id = minted(before)

    # 3 — a second dependency onto the entry written in step 1, appended
    # beside the first rather than rewritten over it.
    write_op(
        "add-depends-on",
        f"""
[[entry]]
id = "{claim_id}"
  [[entry.depends-on]]
  id = "clm-bb2222"
  context = "the mid-band claim"
""",
    )

    # 4 and 5 — the leaf's metadata: the frontmatter block, then the Tier-2
    # marker located by an excerpt of the body rather than placed by hand.
    write_op(
        "set-frontmatter",
        f"""
[[entry]]
document = "{_LEAF}"
kind = "leaf"
claims = ["{claim_id}"]
""",
    )
    write_op(
        "mark-claim-in-leaf",
        f"""
[[entry]]
document = "{_LEAF}"
id = "{claim_id}"
locator = "{_LOCATOR}"
""",
    )

    # 6 and 7 — the two update ops a scoring wave runs.
    write_op(
        "set-rigor",
        f"""
[[entry]]
id = "{claim_id}"
rigor = 0.85
""",
    )
    write_op(
        "set-on-point-fraction",
        f"""
[[entry]]
id = "{support_id}"
claim = "{claim_id}"
fraction = 0.6
""",
    )

    # 8 — the fan-out reaches its canonical home: a second `set-frontmatter`
    # over the same leaf, now stamping the `sup-id:` block that ORIGINATES the
    # support node. This is the leaf-side prover's only end-to-end
    # exercise through the shipped CLI — a declaration the renderer emitted and
    # the production reader could not find is a destroyed node, not a missing
    # attribute, and the readback runs on the temp before anything is replaced.
    #
    # It runs AFTER the staged re-score deliberately, and restates the same
    # fraction: once both ends are live they are meant to agree, and staging a
    # 0.5 against a hosted 0.6 is the unreconciled state `verify` fails on. It
    # also restates `claims`, because a block replace is total and a member
    # dropped here would leave the step-5 marker for the reader to harvest and
    # discard.
    write_op(
        "set-frontmatter",
        f"""
[[entry]]
document = "{_LEAF}"
kind = "leaf"
claims = ["{claim_id}"]
  [[entry.support-node]]
  sup-id = "{support_id}"
    [[entry.support-node.supports]]
    id = "{claim_id}"
    fraction = 0.6
""",
    )

    # 9 — the off-graph endcap's node. It mints nothing: the id is a function
    # of the citation key, and the rationale a build writes here is composed
    # mechanically from what the bibliography did or did not answer.
    write_op(
        "insert-work-entry",
        f"""
[[entry]]
register = "{_REGISTER}"
key = "{_WORK_KEY}"
title = "Nobody. 2026. A Work This Corpus Does Not Contain."
strength = "*pending*"
rationale = "Titled by its citation key: no bibliography answered it."
""",
    )
    work_rationale_at_mint = next(
        work for work in kb_index_lib.parse_work_entries(kb_root / _REGISTER, kb_root) if work.id == _WORK_ID
    ).rationale

    # 10 and 11 — the edit no mint-time composer can make. Choosing the excerpt
    # and the document it is quoted from is a judgement, so it arrives on a
    # later pass: `render-citation` verifies the excerpt against its target and
    # spells the link relative to the register, and `set-rationale` lands it.
    citation = read_op(
        "render-citation",
        f"""
[[entry]]
excerpt = "{_EXCERPT}"
cited-document = "{_CITED_DOCUMENT}"
anchor = "{_CITED_ANCHOR}"
citing-document = "{_REGISTER}"
""",
    )
    write_op(
        "set-rationale",
        f"""
[[entry]]
id = "{_WORK_ID}"
rationale = '''Standing judged against {citation}, which is what this corpus restates of the result the work is cited for.'''
""",
    )

    before_refresh = _snapshot(kb_root)
    refresh = _run(repo, "kb_tools.refresh_kb_metadata")
    verify = _run(repo, "kb_tools.verify_kb_metadata")
    md_links = _run(repo, "kb_tools.verify_md_links")
    citations = _run(repo, "kb_tools.verify_citations")
    after_refresh = _snapshot(kb_root)
    second_refresh = _run(repo, "kb_tools.refresh_kb_metadata")
    after_second_refresh = _snapshot(kb_root)

    register = kb_root / _REGISTER
    entry_at_green = next(e for e in kb_index_lib.parse_claim_quality_file(register, kb_root) if e.id == claim_id)
    leaf_at_green = kb_index_lib.parse_leaf(kb_root / _LEAF, kb_root)
    assert leaf_at_green is not None, "the stamped document does not read back as a leaf"
    support_edges_at_green = kb_index_lib.scan_authored_support_edges(kb_root)[support_id]
    hosted = kb_index_lib.parse_support_leaf(kb_root / _LEAF, kb_root)
    assert len(hosted) == 1, f"the stamped sup-id: block originates no node: {hosted}"
    hosted_support_at_green = hosted[0]
    staged_at_green = tuple(kb_index_lib.parse_register_staged_supports(register).get(support_id, ()))
    work_at_green = next(work for work in kb_index_lib.parse_work_entries(register, kb_root) if work.id == _WORK_ID)

    solidity_before_update = _solidity_line(register, claim_id)
    write_op(
        "set-rigor",
        f"""
[[entry]]
id = "{claim_id}"
rigor = 0.55
""",
    )
    solidity_after_update = _solidity_line(register, claim_id)
    entry_after_update = next(e for e in kb_index_lib.parse_claim_quality_file(register, kb_root) if e.id == claim_id)

    return Chain(
        repo=repo,
        kb_root=kb_root,
        register=register,
        claim_id=claim_id,
        support_id=support_id,
        refresh=refresh,
        verify=verify,
        md_links=md_links,
        citations=citations,
        second_refresh=second_refresh,
        before_refresh=before_refresh,
        after_refresh=after_refresh,
        after_second_refresh=after_second_refresh,
        entry_at_green=entry_at_green,
        leaf_at_green=leaf_at_green,
        support_edges_at_green=support_edges_at_green,
        hosted_support_at_green=hosted_support_at_green,
        staged_at_green=staged_at_green,
        citation=citation,
        work_at_green=work_at_green,
        work_rationale_at_mint=work_rationale_at_mint,
        solidity_before_update=solidity_before_update,
        solidity_after_update=solidity_after_update,
        entry_after_update=entry_after_update,
    )


# ---------------------------------------------------------------------------
# Clause 1 — the three-way chain green
# ---------------------------------------------------------------------------


def test_refresh_and_verify_are_green_over_the_written_kb(chain: Chain) -> None:
    """write → refresh → verify, each through its own shipped entry point.

    The ten ops are asserted green inside the fixture, where a failure names
    the op that failed; this is the other two thirds of the chain.
    """
    assert chain.refresh.returncode == 0, chain.refresh.stdout + chain.refresh.stderr
    assert chain.verify.returncode == 0, chain.verify.stdout + chain.verify.stderr


def test_every_value_the_chain_supplied_is_in_the_green_kb(chain: Chain) -> None:
    """The control on the clause above: green over the *intended* KB.

    A verifier passing proves the KB is coherent, not that it is the one the
    chain asked for — an op that silently wrote nothing would leave a KB just
    as green as this one. Every value below was supplied to a different op, and
    is read back through the production parsers.
    """
    entry = chain.entry_at_green
    assert [edge.target for edge in entry.depends_on] == ["clm-aa1111", "clm-bb2222"]
    assert entry.confidence == 0.85
    assert chain.leaf_at_green.claims == (chain.claim_id,)
    assert chain.claim_id in chain.leaf_at_green.tier2_marked
    # Asserted as a SET of pairs, because once step 8 lands, both authored ends
    # carry this fan-out and `scan_authored_support_edges` concatenates the two
    # homes without de-duplicating — a doubly-authored pair comes back twice.
    # That is the function's own shape ("what has been authored", both homes,
    # NOT a graph source), and the per-end assertions live in the two tests
    # below; what this line is for is that the fraction in the green KB is the
    # one the chain supplied.
    assert set(chain.support_edges_at_green) == {(chain.claim_id, 0.6)}


def test_the_stamped_sup_id_block_originates_the_node_the_reader_returns(chain: Chain) -> None:
    """Step 8, and the leaf-side prover's end-to-end proof.

    ``store``'s readback is register-shaped, so a leaf write proves nothing
    there — the op carries its own, run inside the splice on a temp beside the
    target, through the same ``parse_support_leaf`` the claim graph is built
    from. What that prover exists to catch is a declaration the renderer emitted
    and the reader cannot see: for a ``sup-`` id that is worse than total loss,
    because the register's staging copy leaves the graph looking plausible while
    the canonical home the graph actually reads is empty.

    So this asserts node-hood, not bytes: the reader returns a node, keyed by
    the id the op minted, carrying the pair.
    """
    node = chain.hosted_support_at_green
    assert node.id == chain.support_id
    assert tuple(node.supports) == ((chain.claim_id, 0.6),)


def test_the_two_authored_ends_of_the_fan_out_agree(chain: Chain) -> None:
    """Both ends are live once step 8 lands, and a live pair must agree.

    The register stages the fan-out while the hosting leaf does not exist; the
    leaf's own ``sup-id:`` block is canonical once it does. This is the state in
    which the two are meant to be read together, and it is a state the chain
    reaches through two different ops writing two different files.
    """
    assert chain.staged_at_green == ((chain.claim_id, 0.6),)
    assert tuple(chain.hosted_support_at_green.supports) == chain.staged_at_green


def test_refresh_computed_the_derived_fields_the_ops_left_as_placeholders(chain: Chain) -> None:
    """The division of labour, observed at the seam.

    The ops wrote the canonical placeholder and never a value; refresh replaced
    it. Solidity is ``min`` over the entry's own rigor and its dependencies'
    solidities, so 0.85 against a 0.60 dependency lands on 0.60 — a number no
    op in the chain could have supplied.
    """
    assert chain.entry_at_green.solidity == 0.60
    assert render.SOLIDITY_PENDING_LINE not in chain.solidity_before_update
    # The other derived field the insert stamped at its placeholder: the
    # leaf-references footer, which refresh filled in from the citing leaf the
    # chain went on to write. The expected bytes come from refresh's own
    # renderer rather than a literal typed here.
    entry = _entry_lines(chain.register, chain.claim_id)
    assert render.LEAF_REFERENCES_PENDING_FOOTER not in entry
    assert kb_index_lib.render_leaf_references(_REGISTER, [_LEAF]) in entry


def test_the_work_entrys_rationale_carries_the_citation_the_chain_composed(chain: Chain) -> None:
    """The endcap node's prose, rewritten after the entry was minted.

    A build composes a work's rationale mechanically and there is no later
    moment in it at which an excerpt and its source could be chosen — so
    without this edit the field is write-once and the shipped convention's
    "quoted excerpt linked to a durable path" has no author. What the chain
    shows is that there is one: the rationale read out of the green KB is the
    one the edit supplied, citation included, and it is not the minted text.
    """
    assert chain.citation == f'["{_EXCERPT}"]({Path("../") / _CITED_DOCUMENT}#{_CITED_ANCHOR})'
    assert chain.citation in chain.work_at_green.rationale
    assert chain.work_at_green.rationale != chain.work_rationale_at_mint
    # And the edit moved that field alone: the other two the entry carries are
    # the ones the insert supplied, and a work carries no others.
    assert chain.work_at_green.title == "Nobody. 2026. A Work This Corpus Does Not Contain."
    assert chain.work_at_green.strength is None


def test_the_citation_gate_and_the_link_gate_pass_the_register_it_was_written_into(chain: Chain) -> None:
    """The two gates SPEC.md's citation grammar is enforced by, over the result.

    Scoped to the register rather than asserted as a clean run: ``mini-kb`` is
    a reader-test fixture whose own prose names ids outside a sanctioned
    channel, so both gates are red over it before this chain writes a byte.
    The claim under test is the one that is this chain's — that the citation
    the edit landed raises nothing in either gate, which is the excerpt checked
    a second time where it was written rather than where it was composed.
    """
    for gate in (chain.citations, chain.md_links):
        reported = gate.stdout + gate.stderr
        # The vacuity guard, stated where the comparison is made: a gate that
        # never ran also names no register. Both name the fixture's own, which
        # is what makes the absence of this one a fact about this one.
        assert "common/claim-quality.md" in reported, reported
        assert _REGISTER not in reported, reported


# ---------------------------------------------------------------------------
# Clause 2 — the second refresh is byte-identical
# ---------------------------------------------------------------------------


def test_a_second_refresh_changes_no_byte_of_the_kb(chain: Chain) -> None:
    """The placeholders-are-identity proof, over the whole tree.

    If a placeholder were anything other than the value refresh computes for an
    unresolved field, the second pass would rewrite what the first wrote.
    """
    assert chain.second_refresh.returncode == 0, chain.second_refresh.stdout + chain.second_refresh.stderr
    assert chain.after_second_refresh == chain.after_refresh


def test_the_first_refresh_did_change_the_kb(chain: Chain) -> None:
    """The teeth on the clause above.

    An idempotent tool already at its fixed point writes nothing, and an empty
    diff also holds for a refresh that never ran. This names the files the
    first pass moved, so the identity of the second is a claim about a tool
    that demonstrably had work to do.
    """
    changed = {
        path
        for path in chain.before_refresh.keys() | chain.after_refresh.keys()
        if chain.before_refresh.get(path) != chain.after_refresh.get(path)
    }
    assert _REGISTER in changed
    assert any(path.startswith(".index/") for path in changed)


# ---------------------------------------------------------------------------
# Clause 3 — a score update leaves the computed solidity line alone
# ---------------------------------------------------------------------------


def test_set_rigor_over_a_computed_solidity_leaves_that_line_byte_unchanged(chain: Chain) -> None:
    """The update op rewrites the field it names and nothing derived.

    The solidity here was computed by ``refresh`` in this same chain rather
    than planted by the test: rewriting the whole ``### Quality`` block would
    reset it to its placeholder, refresh would call that drift and verify would
    fail on it.
    """
    # The vacuity guard, stated where the comparison is made: two placeholder
    # lines also compare equal, and that equality would prove nothing.
    assert render.SOLIDITY_PENDING_LINE not in chain.solidity_before_update
    assert chain.solidity_after_update == chain.solidity_before_update
    # And the update landed: the authored field moved, the derived one did not.
    assert chain.entry_after_update.confidence == 0.55
    assert chain.entry_after_update.solidity == 0.60
