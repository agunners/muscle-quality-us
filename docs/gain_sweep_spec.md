# Specification — the gain sweep

*Authored 2026-08-05. Approved by Akadir the same day. **We write this; the acquisition team executes it.**
One subject, one session, roughly twenty minutes of scanner time. It needs no ethics amendment beyond
whatever already covers a volunteer scan, and it can be done on the next available day.*

## Why this is being asked for

Three features that the analysis leans on are tagged with a **scanner-risk** level — how much the value
moves when the operator turns the gain dial. Those tags were assigned *"principled-but-pre-empirical at
registration time, not by an algorithm inferring risk from data"*, in the source whitepaper's own words.
Nobody has ever measured them.

That would be a small debt except for what was measured on 2026-08-05: `int_cv_within`, which carries
**33.6% of the served score**, correlates with plain mean brightness at **−0.91**, and substituting
brightness for it changes the model's agreement with the doctors' grade by −0.0007 ± 0.0093 — a coin flip.
Brightness is exactly the quantity a gain dial moves. So a third of the score may be partly an acquisition
setting, and we cannot tell from the data we hold.

**It cannot be recovered retrospectively.** `OutputPower`, `TransducerData` and `SoftwareVersions` are
absent from **all 20** recordings in the DICOM coverage audit. There is no gain value stored anywhere in
the archive. The only way to learn this is to vary gain deliberately and write the number down.

## What we are asking for

**One healthy volunteer. One session. One operator. One probe. One site.**

The site is the standard protocol site — the centre-RF plane at 50% of the ASIS-to-patella distance, at
the standard 4.9 cm depth, exactly as in the current protocol. Nothing about the anatomy or the landmark
changes. **The only thing that varies is the gain setting.**

### The grid

| gain setting | what it means | repeats |
|---|---|---|
| **low** | the lowest gain at which the fascia is still clearly readable | 3 |
| **standard** | whatever the operator would normally use for this patient | 3 |
| **high** | the highest gain before the image visibly washes out | 3 |

**Nine acquisitions in total, in five blocks.** The sequence **standard → low → standard → high →
standard** names the *blocks*, not the acquisitions: the three standard repeats are deliberately spread
to the start, middle and end of the session, while the three low and the three high repeats sit
contiguous inside their single blocks — acquisition by acquisition that is
**S1 · L1 L2 L3 · S2 · H1 H2 H3 · S3**. The probe is fully lifted and re-placed between every one of
the nine. Bracketing the session with standards is what makes drift over the session separable from
the gain effect; ordering low-to-high would confound the two.

### The three repeats are not optional, and here is why

We already know the texture measure's own re-placement noise: **MDC95 0.196, with the noise slightly
exceeding the between-subject spread (MDC ÷ SD = 1.07)**. A single reading per gain setting cannot be
compared against that. **The three repeats at each setting — probe fully lifted and re-placed between
each — are what let us say whether gain moves the number by more or less than simply picking the probe up
and putting it down again.** Without them the whole exercise is uninterpretable.

### What must be recorded by hand

**The actual gain value or dial position for every acquisition**, written into the session sheet. This is
the load-bearing instruction. The scanner does not store it, so if it is not written down the session is
wasted and cannot be repeated retrospectively. If the device shows gain in dB, record dB; if it shows an
arbitrary index, record the index and photograph the panel once per setting.

Also record, as usual: operator, probe, depth, and the standard study-name fields.

## What we will do with it

For each of the nine acquisitions we compute `int_cv_within`, `int_mean` and `int_std` on the top-five
largest-RF frames, exactly as the pipeline does today. Then:

1. **Regress each feature on gain**, and express the slope in the feature's own units.
2. **Compare that slope against the feature's MDC95** across the low-to-high range. This is the whole
   question in one number: *does turning the dial move the measurement further than re-placing the probe
   does?*
3. **Compare `int_cv_within` against `int_mean`.** If the ratio moves as much as the raw brightness does,
   the gain-invariance argument is dead empirically as well as algebraically, and the scanner-risk tags
   are simply wrong.

## What each outcome decides

- **If gain moves `int_cv_within` by less than its MDC95** across the full clinical range: the axis is
  acquisition-robust in practice, the taxonomy stands, and this is closed cheaply.
- **If it moves it by more**: a third of the score is partly an operator setting. Two responses become
  live, and the size of the effect chooses between them — pin gain in the acquisition protocol (cheap, and
  a natural addition to the standing protocol document), or carry a gain-corrected variant (more work, and
  it needs the recorded gain values this session will be the first to produce).

Either way the taxonomy stops being an assertion. That is the deliverable.

## Cost and dependencies

Twenty minutes of scanner time, one volunteer, no new equipment, no software change, no ethics amendment
beyond a volunteer scan. **No dependency on any other team's approval.** The analysis afterwards is a few
hours and runs on the existing pipeline unchanged.

---

*Evidence behind the request: `scripts/analysis/texture_substitution_test.py` and
`scripts/analysis/texture_force_test.py`, results beside them; the correction filed at the head of
`muscle_quality_exploration/WHITE_PAPER_audited_2026-05-11.md`; repeatability figures in
`scripts/analysis/results/repeatability_n116.csv`.*
