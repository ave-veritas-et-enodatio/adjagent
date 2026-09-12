"""The claim-graph builder's command line: run it from inside the consuming repo.

Nothing is discovered but the root: the repository root is walked up to from the
working directory, and the KB is the directory beside it — the same rule every
other tool in this toolchain resolves a root by, and the reason none of them
takes an override.

**``graph-init`` runs before this**, and this build does not seed. That verb
creates the derived-index directory and installs the runner include line, which
is the mechanism the last stage's refresh and verify targets exist through. A
run finding no spine says so and names the command that installs one, rather
than creating half of it itself.
"""

import argparse
import sys

from .. import __version__, kb_pipeline, kb_util
from . import ask, conform, depends, discover
from .build import build

#: The scope vocabulary, read from the stage table rather than declared twice.
#: What the two values distinguish is which *stage* a ``--pass 1`` invocation
#: is, and that pairing is ``kb_pipeline.ClaimgraphInvocation``'s — so this
#: module parses the flags and asks what stage they name instead of branching
#: on numbers whose meaning is stated nowhere.
SCOPES: tuple[str, ...] = (kb_pipeline.CLAIMGRAPH_SCOPE_BLOCK_HOSTED, kb_pipeline.CLAIMGRAPH_SCOPE_FULL)

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_USAGE = 2
EXIT_NO_SPINE = 3

#: Where the write passes' values files land — the project's scratch space, the
#: only place an uncommitted file may sit without failing a clean-worktree gate.
SCRATCH_RELPATH = f"{kb_util.SCRATCH_DIRNAME}/claimgraph"

#: Where the discovered pass records each ask — the prompt it put and the
#: stream the call emitted — beside the write passes' values files.
ASKS_RELPATH = f"{SCRATCH_RELPATH}/asks"

#: Every pass number the stage table declares an invocation for, in order.
#: Derived rather than typed, so a pass this tool accepts is a pass some stage
#: of the build actually is.
PASSES: tuple[int, ...] = tuple(
    dict.fromkeys(stage.claimgraph_invocation.which_pass for stage in kb_pipeline.STAGES if stage.claimgraph_invocation)
)


#: The two stage ids this module branches on, read off the table so a rename
#: reaches here. The third is the else.
_CLAIMS_DISCOVERED = kb_pipeline.claimgraph_stage(
    which_pass=1, scope=kb_pipeline.CLAIMGRAPH_SCOPE_FULL
).id  # type: ignore[union-attr]
_DEPENDS_ATTRIBUTED = kb_pipeline.claimgraph_stage(which_pass=2, scope=None).id  # type: ignore[union-attr]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb_tools.kb_claimgraph", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s (kb_tools {__version__})")
    parser.add_argument(
        "--pass",
        dest="which_pass",
        type=int,
        choices=PASSES,
        default=PASSES[0],
        help=(
            "which pass to run: 1 authors the declared graph — the claims the author marked — over a fresh "
            "tree, and is mechanical end to end; 2 authors the discovered graph's dependency edges over "
            "pass 1's output, and spends inference"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default=kb_pipeline.CLAIMGRAPH_SCOPE_BLOCK_HOSTED,
        help=(
            "pass 1 only — which claim sites to identify: 'block-hosted' authors the claims the author "
            "marked over a fresh tree and is mechanical end to end; 'full' runs claim discovery over the "
            "documents a block-hosted run left awaiting, so it takes that run's output as its input and "
            "spends one inference per document"
        ),
    )
    parser.add_argument(
        kb_util.NO_INFERENCE_FLAG,
        dest="no_inference",
        action="store_true",
        help=(
            "spend no model call: run only the part of this invocation that asks nobody anything. Pass 2 "
            "records the edges containment settles and leaves the pairs it did not as open and unrecorded; "
            "the declared pass is mechanical already and is unchanged by it; claim discovery has no such "
            "part and is refused"
        ),
    )
    arguments = parser.parse_args(argv)

    try:
        repo_root = kb_util.find_repo_root()
    except kb_util.RepoRootError as error:
        print(str(error), file=sys.stderr)
        return EXIT_USAGE

    kb_root = kb_util.kb_root(repo_root)
    if not kb_util.document_tree_present(repo_root):
        print(f"{kb_root} holds no document tree, so there is nothing to build a claim graph over", file=sys.stderr)
        return EXIT_USAGE

    absent = conform.spine_seeded(kb_root, targets_installed=kb_util.targets_installed(repo_root))
    if absent is not None:
        print(
            f"the claim-graph spine is not installed: {absent}. Seed it first:\n"
            f"    {kb_util.INVOCATION} {kb_util.OP_GRAPH_INIT}",
            file=sys.stderr,
        )
        return EXIT_NO_SPINE

    # Which stage this invocation is, resolved in the table the build's driver
    # composed the same flags from. A pass number branches nothing here: the
    # stage decides, and a combination no stage declares is a usage error rather
    # than a silent fall-through to the mechanical pass.
    scope = arguments.scope if arguments.which_pass == PASSES[0] else None
    stage = kb_pipeline.claimgraph_stage(which_pass=arguments.which_pass, scope=scope)
    if stage is None:
        print(
            f"--pass {arguments.which_pass} with --scope {arguments.scope} is no stage of the build; "
            f"the invocations this tool runs are "
            f"{', '.join(' '.join(s.claimgraph_invocation.flags) for s in kb_pipeline.STAGES if s.claimgraph_invocation)}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    precondition = kb_pipeline.precondition_of(stage)
    print(
        f"[claimgraph] FACT stage {stage.id} ({stage.display})"
        + (f"; runs over what {precondition.id} left" if precondition else "")
    )

    # The seat resolves against the consuming repository's own installed agent
    # set, which is what `cwd` decides — the same root every other tool here
    # walks up to.
    scratch = repo_root / SCRATCH_RELPATH
    asks = repo_root / ASKS_RELPATH
    if stage.id == _DEPENDS_ATTRIBUTED:
        report = depends.build(
            kb_root=kb_root,
            repo_root=repo_root,
            scratch=scratch,
            selector=None if arguments.no_inference else ask.ModelSelector(cwd=repo_root, workspace=asks),
        )
    elif stage.id == _CLAIMS_DISCOVERED:
        # Refused rather than run empty: every claim this stage mints comes out
        # of a reading a model performs, so there is no half of it that asks
        # nobody. A run of it under the flag would mint nothing and then fail
        # its own exit condition, reporting a corpus fault for a wiring one.
        if arguments.no_inference:
            print(
                f"{kb_util.NO_INFERENCE_FLAG} names no run of {stage.display}: every claim it mints comes "
                f"from a reading a model performs, so it has no part that asks nobody. A build spending "
                f"none omits this stage rather than running it.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        report = discover.build(
            kb_root=kb_root,
            repo_root=repo_root,
            scratch=scratch,
            identifier=ask.ModelIdentifier(cwd=repo_root, workspace=asks),
        )
    else:
        report = build(kb_root=kb_root, repo_root=repo_root, scratch=scratch)
    print("\n".join(report.lines()))
    return EXIT_GATE_FAILED if report.failed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
