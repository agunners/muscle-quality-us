"""Count study artifacts (mp4 / RawImages folders / dcm) inside a study zip.

The 'Artifacts' datasheet column records 'M/R/D' (e.g. '8/8/8') so completeness is
auditable at a glance: the v8 main protocol expects 3 (a single dominant leg x 3
captures: femur / RF-still / RF-sweep), the 25-patient methodology sub-cohort
expects 8 (both legs x 4 captures).

CRITICAL: device/Android-trash leftovers (basename '.trashed-<ts>-...') get zipped
in when an operator deletes a wrong capture (v7 section 7) but the trash isn't purged.
The real anonacl pilot zip carried 10 mp4 of which 2 were '.trashed-' (true count 8).
So trashed and hidden entries are excluded — this only surfaced on the REAL zip, a
synthetic namelist would have missed it.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Sequence

__all__ = ["ArtifactCounts", "count_artifacts", "count_zip_artifacts"]


@dataclass
class ArtifactCounts:
    mp4: int
    rawimages: int
    dcm: int
    excluded: int = 0  # mp4/dcm entries dropped as trash/hidden (diagnostic)

    def as_str(self) -> str:
        """The 'M/R/D' string written to the datasheet."""
        return f"{self.mp4}/{self.rawimages}/{self.dcm}"


def _is_junk(name: str) -> bool:
    """True for device-trash / hidden / OS-cruft paths that must not be counted."""
    for part in name.split("/"):
        if not part:
            continue
        if part.startswith(".trashed-") or part.startswith(".") or part == "__MACOSX":
            return True
    return False


def count_artifacts(names: Sequence[str]) -> ArtifactCounts:
    """Count real .mp4, distinct RawImages* folders, and .dcm from a zip namelist,
    excluding trashed/hidden entries."""
    mp4 = dcm = excluded = 0
    raw_folders = set()
    for name in names:
        junk = _is_junk(name)
        low = name.lower()
        if junk:
            if low.endswith(".mp4") or low.endswith(".dcm"):
                excluded += 1
            continue
        if low.endswith(".mp4"):
            mp4 += 1
        elif low.endswith(".dcm"):
            dcm += 1
        parts = name.split("/")
        for i, part in enumerate(parts):
            if part.lower().startswith("rawimages"):
                raw_folders.add("/".join(parts[: i + 1]))
                break
    return ArtifactCounts(mp4=mp4, rawimages=len(raw_folders), dcm=dcm, excluded=excluded)


def count_zip_artifacts(zip_path: str) -> ArtifactCounts:
    """Open a study zip and count its artifacts (reads the central directory only)."""
    with zipfile.ZipFile(zip_path) as zf:
        return count_artifacts(zf.namelist())
