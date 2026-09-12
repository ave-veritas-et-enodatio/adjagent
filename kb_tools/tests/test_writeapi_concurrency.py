"""A wave of real writers against one register loses nothing.

This module drives three concurrent `insert-claim-entry` processes into one
register, twenty-five times, against the code written to prevent exactly that:

* **22 of 25 rounds silently lost an entry** — three writers, three ids printed
  to their callers, one `## ` heading on disk;
* **34 ids were reported `FACT minted` with no entry anywhere in the KB**, which
  is the ghost-id state that must be unconstructible;
* **every writer exited 0** and **every register was left census-consistent**,
  so nothing downstream had anything to notice.

The cause was that the check and the `os.replace` it guards were two adjacent
but unguarded loops: every writer read the same baseline, proved its own temp,
found the baseline still live, and replaced. Whoever replaced last won.

**Why this test drives processes and not `apply_edits`.** The defect is a window
between two statements. An in-process test can only enter that window through a
seam the code offers it, and the one seam `store` offers — the caller-supplied
splice — sits *before* the check, which is the one interleaving the old check
already caught (`test_kb_write_store.py::TestForcedInterleaving`). That test was
green throughout the 22 lost rounds. Nothing short of real processes racing on a
real filesystem puts a second writer *between* the check and the replace, so
that is what this module does: `python3 -m kb_tools.kb_util insert-claim-entry`,
N at a time, from a working directory inside a synthetic consumer repo, with no
root override — the shape the distiller briefs sanction.

`test_kb_write_store.py::TestTheReplaceRunsUnderAnExclusion` is the other half
of the proof and the deterministic one: it asserts the exclusion itself. This
module asserts the outcome the exclusion exists for.

**The writers retry, because that is the contract.** Exit 8 tells a caller the
values were right and to re-run the identical invocation; a wave in
which every seat honours that contract must end with every seat's entry on disk.
So each writer here runs the bounded retry loop the brief bodies ask an agent
for, and the assertion is the strong one: all N entries land, and the register
holds exactly the entries its writers were told they had written.

**Rounds.** The measured loss rate was 22/25 per round, so a round survives the
defect with probability ≈ 0.12 and :data:`ROUNDS` rounds survive with ≈ 0.12**N
— at ten rounds, about six in ten billion. That is the sizing argument: enough
rounds that a green here is a statement about the code rather than about the
scheduler, and few enough to cost a couple of seconds.
"""

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from kb_tools import kb_index_lib
from kb_tools.kb_write import store

_THIS_DIR = Path(__file__).resolve().parent
# The directory holding the ``kb_tools`` package — the subprocess PYTHONPATH,
# never a source of the consumer repo root, which is discovered from the cwd.
_PKG_PARENT = _THIS_DIR.parent.parent
_FIXTURE_SRC = _THIS_DIR / "fixtures" / "mini-kb"

#: Concurrent writers per round, and rounds. Three writers is the probe's shape,
#: which is also a distiller wave's: several seats, one register.
WRITERS = 3
ROUNDS = 10

#: The bound on the exit-8 retry loop. Every retry is a *guaranteed*-progress
#: retry — a writer only answers 8 because some other writer committed, and
#: there are ``WRITERS`` commits in a round — so this is generous by a wide
#: margin rather than a number tuned until the test passed.
MAX_ATTEMPTS = 8

_REGISTER = "claim-quality.md"
_MINTED_RE = re.compile(r"FACT minted\s+(\S+)")


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(_PKG_PARENT), "PYTHONDONTWRITEBYTECODE": "1"}


@dataclass(frozen=True)
class Attempt:
    """One `insert-claim-entry` process: its code, and what it told its caller."""

    returncode: int
    minted: tuple[str, ...]


def _insert(repo: Path, values: Path) -> Attempt:
    """Run one insert as a subprocess and read the ids it reported minting.

    Only a run that exits 0 has claimed anything: on 7 and 8 nothing is written
    and no ``FACT minted`` line is composed. The ids are read off stdout rather
    than off the disk because a *claim to the caller* is the promise at stake
    — an id an agent was handed and cannot find is the failure, whatever
    the file says.
    """
    result = subprocess.run(
        [sys.executable, "-m", "kb_tools.kb_util", "insert-claim-entry", "--values", str(values)],
        cwd=repo,
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    return Attempt(returncode=result.returncode, minted=tuple(_MINTED_RE.findall(result.stdout)))


def _writer(repo: Path, values: Path) -> list[Attempt]:
    """One seat: insert, and on exit 8 re-run the identical invocation.

    The values file is never re-authored between attempts — re-asking a model
    for values that were already right is the duplicate-id path this contract
    exists to close, and a test that re-authored them would be proving a
    contract nobody is asked to keep.
    """
    attempts: list[Attempt] = []
    for _ in range(MAX_ATTEMPTS):
        attempts.append(_insert(repo, values))
        if attempts[-1].returncode != 8:
            break
    return attempts


def _make_repo(tmp_path: Path, name: str) -> Path:
    """A throwaway consumer repo with ``mini-kb`` as its kb-root."""
    repo = tmp_path / name
    (repo / ".git").mkdir(parents=True)
    (repo / "values").mkdir()
    shutil.copytree(_FIXTURE_SRC, repo / "kb-root")
    return repo


def _values_for(repo: Path, tag: str) -> Path:
    path = repo / "values" / f"{tag}.toml"
    path.write_text(
        "[[entry]]\n"
        f'register = "{_REGISTER}"\n'
        f'title = "Racer {tag}"\n'
        "rigor = 0.5\n"
        f'rationale = "Written by racer {tag}."\n',
        encoding="utf-8",
    )
    return path


def _claim_ids(register: Path, kb_root: Path) -> list[str]:
    return [record.id for record in kb_index_lib.parse_claim_quality_file(register, kb_root)]


def test_a_wave_of_concurrent_writers_loses_no_entry(tmp_path: Path):
    """N writers, one register, N rounds: every id reported minted is on disk.

    The four assertions are the four things the defect broke, and they are made
    per round so a failure names the round it happened in:

    1. every writer ends on 0 or 8 — the ladder has no fourth answer;
    2. the register's record count is its starting count plus the number of
       runs that claimed a write — the count is what a lost update moves;
    3. every id any run reported minting has an entry — no ghosts;
    4. the register's census passes and no temp survives — a lost update leaves
       both of these green, so they are the controls rather than the evidence.

    And once over the whole run: every writer of every round eventually landed
    its entry, which is what makes the exit-8 contract a mechanism rather than
    advice.
    """
    landed_all = True
    for round_no in range(ROUNDS):
        repo = _make_repo(tmp_path, f"round-{round_no}")
        kb_root = (repo / "kb-root").resolve()
        register = kb_root / _REGISTER
        before = len(_claim_ids(register, kb_root))
        values = [_values_for(repo, f"{round_no}-{seat}") for seat in range(WRITERS)]

        with ThreadPoolExecutor(max_workers=WRITERS) as pool:
            waves = list(pool.map(lambda path: _writer(repo, path), values))

        attempts = [attempt for seat in waves for attempt in seat]
        codes = sorted({attempt.returncode for attempt in attempts})
        assert codes and set(codes) <= {0, 8}, f"round {round_no}: unexpected exit codes {codes}"

        claimed = [attempt for attempt in attempts if attempt.returncode == 0]
        ids = _claim_ids(register, kb_root)
        assert len(ids) == before + len(claimed), (
            f"round {round_no}: {len(claimed)} runs reported writing an entry but the register "
            f"grew by {len(ids) - before}"
        )

        minted = [node_id for attempt in claimed for node_id in attempt.minted]
        inventory = set(kb_index_lib.scan_authored_ids(kb_root))
        ghosts = [node_id for node_id in minted if node_id not in inventory]
        assert ghosts == [], f"round {round_no}: reported minted, no entry in the KB: {ghosts}"
        assert sorted(node_id for node_id in ids if node_id in minted) == sorted(minted)

        assert store.take_census(register, kb_root).consistent, f"round {round_no}: census"
        assert [p for p in register.parent.iterdir() if p.name.endswith(store.TEMP_SUFFIX)] == []

        landed_all &= len(claimed) == WRITERS

    assert landed_all, "a writer exhausted its exit-8 retries without landing its entry"
