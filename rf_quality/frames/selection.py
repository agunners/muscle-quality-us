"""Frame selection with anchor-by-definition.

``center_frame_base`` from the decision log is the anchor frame by
definition. No temporal heuristics. If a completed study's center frame
does not resolve in transport, the study is logged to the QA report as a
fail-fast case.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameSelection:
    anchor_frame_base: str
    all_frame_bases: list[str]

    @property
    def non_anchor_frames(self) -> list[str]:
        return [fb for fb in self.all_frame_bases if fb != self.anchor_frame_base]


def build_selection(anchor_frame_base: str, frame_bases: list[str]) -> FrameSelection:
    return FrameSelection(anchor_frame_base=anchor_frame_base, all_frame_bases=list(frame_bases))
