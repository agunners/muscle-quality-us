"""What this build currently believes, said out loud on the page.

Akadir 2026-08-04: *"I don't have anything against making notes onto the surface of the app — so
that things/decisions don't get lost and we know the direction just by running the app; it is almost
like an experimenting surface and it needs to support that, verbally."*

So this is the one place the app states its own standing decisions and open caveats. Three rules keep
it honest (the third added 2026-09-02, after Akadir asked whether the notes "really make sense"):

  * **Every entry is dated and says whether it is SETTLED or PROVISIONAL or OPEN.** A note that
    cannot say which is not ready to be on screen.
  * **It is a handle, not an archive.** Retire an entry when it stops governing what the page does;
    the trail lives in git, `HANDOFF.md` and the memory topic files, not here.
  * **Every number on it is current for the served freeze, and every term is one a stakeholder can
    read without a glossary.** A note that quotes a superseded n, or leans on an acronym the page
    never defines, is retired or rewritten — it is what a reviewer sees first.

Pure data + one render function, no streamlit import at module level, so it stays unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Note:
    date: str
    status: Literal["SETTLED", "PROVISIONAL", "OPEN"]
    title: str
    body: str


# Newest first. Keep this SHORT — if it needs scrolling it has stopped being read.
NOTES: tuple[Note, ...] = (
    Note("2026-09-02", "SETTLED",
         "The score's breakdown shows the three axes that actually score it",
         "Since 2026-08-04 the served recipe reads muscle size in millimetres, but the breakdown's "
         "Size row still showed muscle-over-BMI — an axis that never enters the served score — while "
         "raw thickness, which does, was absent and its push invisible. Fixed: the three rows follow "
         "the recipe that produced the headline (the millimetre recipe, or the muscle-over-BMI one "
         "only when the headline had to fall back to it), each row quotes the same same-sex "
         "percentile its card leads with, and the reference the bars are read against is named "
         "right under them."),
    Note("2026-09-02", "OPEN",
         "Two rulers on one page — the score and the percentiles measure different things",
         "The 0–100 score is a probability on the legacy doctors' scale: its band boundaries were "
         "learned on the older cohort's grades, and on that cohort's scale new-round images read "
         "about three-quarters of a standard deviation higher on size and on texture and a third "
         "lower on fat — every axis reads better. The percentiles are ranks inside the selected "
         "reference. So a new-round woman can sit at p6 on size and p9 on texture among same-sex "
         "peers and still read INTERMEDIATE · 39/100 (AKA001-A7CC, 2026-09-01) — both numbers are "
         "right, each on its own ruler. Nothing can be refit: no new-round study carries a doctors' "
         "grade yet. Decision pending: lead the condition line with the placement and keep the "
         "probability as the calibration detail."),
    Note("2026-08-31", "OPEN",
         "Old and new rounds differ within the same person — cross-era placement stays off",
         "Eleven people were scanned in both rounds: full thickness reads +2.3 mm and QMT +8.5 mm "
         "on the new protocol, and today's code reproduces the old values to the decimal, so the "
         "software is not the cause. What remains is acquisition — probe pressure, site, device "
         "rendering — and a short re-scan of two or three of these people separates them. Until "
         "that cross-calibration map is published, compare a patient to their own round; the Mixed "
         "reference is for exploration only."),
    Note("2026-08-23", "PROVISIONAL",
         "Strength is on the page — measured torque next to what the profile supports",
         "The dynamometer value (shown with its own session repeatability, never bare) sits next "
         "to an 8-in-10 expected range from a frozen demographics+geometry model, refrozen at "
         "freeze r5 (out-of-fold R² 0.70, RMSE 41 Nm, n=143; the range spans about ±67 Nm for men "
         "and ±35 Nm for women around the prediction). Force stays OUT of the condition score. "
         "Provisional because the model ranks well but individual prediction is honest at ±41 Nm, "
         "and demographics carry most of it — the range means 'this body and this muscle', never "
         "'muscle alone'."),
    Note("2026-08-14", "SETTLED",
         "Measurement trust is one surface — and it acts",
         "The trust strip states what Size / Texture / Strength each rest on, with the numbers at "
         "two levels (the canonical top-down check and this run's selected frames). Beneath it, "
         "Measurement evidence opens the automatic pipeline's black box — canonical vs this-run "
         "values side by side — and carries the actions: a withheld texture can be released from "
         "an accepted run, a views-disagree size caveat can be confirmed as reviewed, and vetted "
         "frames can join the canonical pool. Every action is a signed ledger entry bound to a real "
         "run and honoured at the next freeze; flags stay visible forever."),
    Note("2026-08-04", "PROVISIONAL",
         "The headline recipe now reads millimetres, not muscle-over-BMI",
         "Dividing muscle by BMI removed much of the signal it was measuring: against dynamometer "
         "torque raw thickness reaches rho 0.74 where the BMI-normalised form manages 0.42 (n=143, "
         "freeze r5). The millimetre recipe also uses more than twice as much of the 0-100 scale and "
         "needs no femur. Provisional because the model behind it is still calibrated on the older "
         "cohort — see the two-rulers note above."),
    Note("2026-08-04", "SETTLED",
         "Placement is a ranking again — and it leads with same-sex peers",
         "The 0-100 score is a grade probability, and probabilities flatten at their ends: 32 of the "
         "166 scored new-round studies read exactly 100 at freeze r5 on the served millimetre recipe "
         "(49 on the muscle-over-BMI recipe). Percentiles rank the model's "
         "own index instead, which separates every study; nothing was refitted — the same number, "
         "read one step earlier. Sex moves these measurements more than most things we adjust for, "
         "so every card leads with the same-sex placement; overall and age-matched are one hover "
         "away. The headline itself still leads with the probability — that is the open decision "
         "above."),
)

_COLOUR = {"SETTLED": "green", "PROVISIONAL": "orange", "OPEN": "red"}


def render(st, *, expanded: bool = False) -> None:
    """Draw the standing-notes panel. `st` is injected so this module stays headless-importable."""
    n_open = sum(1 for x in NOTES if x.status != "SETTLED")
    with st.expander(f"Where this build stands — {len(NOTES)} standing notes, "
                     f"{n_open} still open", expanded=expanded):
        st.caption("What the page currently believes and why, so the direction is legible from the "
                   "app itself. Newest first. Full trail: HANDOFF.md + git.")
        for note in NOTES:
            st.markdown(
                f":{_COLOUR[note.status]}[**{note.status}**] · `{note.date}` · **{note.title}**")
            st.caption(note.body)
