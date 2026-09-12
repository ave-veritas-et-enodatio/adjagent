"""A minimal survey manifest, for fixtures that need one to derive a tree from.

``skeleton.derive`` is a mechanical way to obtain a legal set of KB paths, and a
fixture wanting a small tree that a running tool would accept builds one here
rather than typing the paths out. Nothing in the shipped toolchain writes a
manifest and nothing requires one; this is fixture scaffolding, not a stand-in
for an input any op takes.
"""

from collections.abc import Sequence

from kb_tools.kb_survey import manifest as survey_manifest

#: The entry file the surveyed manifest below is run over, and the sections it
#: declares. Two of each kind of unit is the least that distinguishes "one was
#: covered" from "all of them were".
ENTRY_FILE = "sources/volume-one.tex"
SECTION_IDS = ("mf:1-introduction", "mf:2-the-model")


def _section(section_id: str, entry_file: str, ordinal: int) -> survey_manifest.Section:
    return survey_manifest.Section(
        id=section_id,
        entry_file=entry_file,
        level="section",
        title=section_id.removeprefix(survey_manifest.ID_PREFIX),
        starred=False,
        in_appendix=False,
        parent_id=None,
        sibling_ordinal=ordinal,
        origin_runs=[survey_manifest.OriginRun(file=entry_file, line_start=ordinal, line_end=ordinal + 1)],
        composed_span=survey_manifest.ComposedSpan(start=ordinal, end=ordinal + 1),
        profile=survey_manifest.Profile(stripped_chars=100, result_count=0, subsection_count=0, display_math_count=0),
    )


def surveyed_manifest(
    *,
    entry_files: Sequence[str] = (ENTRY_FILE,),
    section_ids: Sequence[str] = SECTION_IDS,
) -> survey_manifest.Manifest:
    """A manifest that declares units: the sections a build surveys.

    Every section is attributed to the first entry file, which is what a
    single-volume corpus produces; a fixture that needs a section on a second
    volume replaces the record it cares about.
    """
    sections = [_section(section_id, entry_files[0], ordinal) for ordinal, section_id in enumerate(section_ids, 1)]
    return survey_manifest.Manifest(
        run=survey_manifest.Run(source_root="sources", entry_files=list(entry_files), invocation_flags=[]),
        vocabulary=survey_manifest.Vocabulary(theorem_envs=[]),
        files=[survey_manifest.FileRecord(path=path, included_by=None, include_origin=None) for path in entry_files],
        sections=sections,
        results=[],
        edges=[],
        flags=[],
        protected_spans=[],
        worklist=[
            survey_manifest.WorklistEntry(section_id=section.id, subdivision=survey_manifest.Subdivision.TERMINAL)
            for section in sections
        ],
    )
