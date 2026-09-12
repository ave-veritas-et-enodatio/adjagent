"""The regression fixture's freeze, as a standing guard.

Two questions over ``fixtures/writeapi-regression/``.

**Is the corpus still the corpus?** ``PROVENANCE.md`` records a sha256 per frozen
register. Re-hashing against it turns the freeze from a claim made once
into a standing guard — the source repository no longer exists, so once these
bytes drift there is nothing left to compare them against.

**Does it still carry the defect it was frozen for?** A failure corpus that
someone tidied proves nothing: a sibling test replays these registers' values
through the write API and asserts 23 records where the 2026-09-02 build
produced 18. That arithmetic is only meaningful while the on-disk fixture
still parses to 18, so it is asserted here, through the production parser,
rather than assumed.
"""

import hashlib
import re
from pathlib import Path

from kb_tools import kb_index_lib, kb_schema

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "writeapi-regression"

#: One row of PROVENANCE.md's frozen-file table: fixture name, source path, bytes, sha256.
_PROVENANCE_ROW = re.compile(r"^\| `([^`]+\.md)` \| `([^`]+)` \| (\d+) \| `([0-9a-f]{64})` \|$", re.MULTILINE)

#: An authored claim marker, on its own line as the registers write them.
_MARKER = re.compile(rf"^<!-- id: ({kb_schema.id_body('clm')}) -->$", re.MULTILINE)

#: The entry each register lost to the marker-above-heading layout, one per file
#: (PROVENANCE.md, "The defect they carry"). ``clm-07687j`` is the one whose only
#: reference is an in-register bullet the reader drops, and is a sibling test's
#: named assertion.
_LOST_IDS = {
    "part1-claim-quality.md": "clm-07687j",
    "part2-claim-quality.md": "clm-3ig11l",
    "part3-claim-quality.md": "clm-k970j7",
    "part4-claim-quality.md": "clm-424074",
    "part5-claim-quality.md": "clm-gi3glh",
}

#: The 2026-09-02 arithmetic, over the registers kept here: 23 authored claim
#: markers, 18 records.
_MARKERS = 23
_RECORDS = 18


def _registers() -> list[Path]:
    return sorted(_FIXTURE.glob("*-claim-quality.md"))


def test_the_frozen_registers_still_match_their_recorded_hashes() -> None:
    """Bytes, not text: the recorded sha256 is over the file as stored."""
    doc = (_FIXTURE / "PROVENANCE.md").read_text(encoding="utf-8")
    recorded = {name: (int(size), digest) for name, _source, size, digest in _PROVENANCE_ROW.findall(doc)}

    assert recorded, "PROVENANCE.md carries no frozen-file table; the freeze record is unreadable"
    assert sorted(recorded) == [path.name for path in _registers()]
    for name, (size, digest) in sorted(recorded.items()):
        raw = (_FIXTURE / name).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (size, digest), name


def test_the_frozen_registers_still_carry_the_2026_09_02_defect() -> None:
    """Every register loses its first entry, read through the parser that lost it."""
    markers = 0
    records = 0
    for path in _registers():
        authored = _MARKER.findall(path.read_text(encoding="utf-8"))
        entries = kb_index_lib.parse_claim_quality_file(path, _FIXTURE)
        parsed = [entry.id for entry in entries if entry.id.startswith("clm-")]

        assert set(authored) - set(parsed) == {_LOST_IDS[path.name]}, path.name
        markers += len(authored)
        records += len(parsed)

    assert (markers, records) == (_MARKERS, _RECORDS)
