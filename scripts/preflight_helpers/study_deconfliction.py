"""Study-level artifact deconfliction (v8): group one study's recordings into CAPTURES
and classify them, tolerating the real-world mess found in the anonacl pilot zip.

Why this exists (pilot-verified, see /tmp investigation 2026-06-09):
* The atomic unit is a CAPTURE = one MAKE-VIDEO = (mp4 + RawImages folder). That pair is
  the only reliably co-created thing (8 mp4 <-> 8 RawImages, each within ~3-40 s).
* The SAVE-IMAGE `.dcm` is a SECONDARY, OPTIONAL attachment. The pilot had an ORPHAN
  `.dcm` (no capture near it) AND a capture with NO `.dcm` (a retake where SAVE-IMAGE was
  skipped). So `.dcm` count == capture count is COINCIDENTAL; never assume 1:1:1 triplets.
* Depth setting comes from the RawImages jpg WIDTH (present on every capture, unlike the
  `.dcm`) -> a missing `.dcm` never blocks classification. Sweep-vs-still comes from clip
  length (frame count). Identity is length-FIRST (long -> sweep), then depth -> the only
  rule that holds for both v7 (sweep at the deep setting) and v8 (sweep at the shallow one).

What this does NOT decide: LEFT vs RIGHT leg (needs segmentation or the datasheet's
Dominant Bacak / an explicit record) and RF-1 vs RF-2 (only capture order, which is
fragile) — both are returned as flags, not guesses.

CAVEAT: jpg-width and `.dcm` PixelSpacing AGREE on ordering but DISAGREE on absolute cm
via the leltek constants, so width is used as a DISCRIMINATOR (deep vs shallow), not for an
absolute-cm claim, until the constants are re-derived.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = ["Capture", "DeconflictResult", "deconflict", "read_study_records", "deconflict_zip"]

_TS = re.compile(r"_(\d{8})_(\d{6})(\d{0,3})")
_DCM_IDX = re.compile(r"_(\d+)\.dcm$", re.IGNORECASE)
# Frame counts are CONTENT frames (.jpg), NOT all folder members: the device writes 2 aux PNGs
# (_raw / _beforeDSC) beside every .jpg, so an all-members count is ~3x inflated. The old
# _SWEEP_MIN_FRAMES = 200 only cleared by luck of that 3x margin (a real 77-frame sweep is 231
# members) and would silently break if a firmware change altered the aux-png count. Bars are in
# CONTENT units: real sweeps are 77-116 frames, short cines 15.
_SWEEP_MIN_FRAMES = 40         # >= this many content frames = sweep
_AMBIG_FRAMES = (30, 60)       # content frames near the sweep/still boundary -> "verify length" flag
_SWEEP_REL_FACTOR = 2.0        # fallback when none clear the bar: longest >= this x next-longest = sweep
_SWEEP_REL_FLOOR = 30          # ...and at least this many frames (guards a firmware frame-rate drop)
_PAIR_MAX_S = 120.0            # an mp4 farther than this from a RawImages is never its pair
_DCM_WINDOW_S = 120.0          # capture <-> .dcm attachment window


def _seconds(name: str) -> Optional[float]:
    m = _TS.search(name.rsplit("/", 1)[-1])
    if not m:
        return None
    g, ms = m.group(2), int(m.group(3) or 0)
    return int(g[:2]) * 3600 + int(g[2:4]) * 60 + int(g[4:6]) + ms / 1000.0


def _img_width(data: bytes) -> Optional[int]:
    """Frame WIDTH in px, via PIL. (PIL is a robustness choice, NOT a correctness fix — the old
    struct JPEG parser read content JPGs correctly. The width-collapse-to-256 bug was READ-ORDER:
    read_study_records grabbed a 256px auxiliary _raw.png FIRST; its content-frame filter below is
    the actual fix.)"""
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            return int(im.width)
    except Exception:
        return None


def _is_content_frame(name: str) -> bool:
    """A real displayed device frame (a .jpg), NOT the 256px auxiliary PNGs (_raw / _beforeDSC) the
    device writes beside each frame. Filter by extension (jpg vs the aux png) rather than a
    timestamp-digit whitelist, so a firmware tweak to the frame name can never silently fall back to
    reading an aux PNG first (the width-collapse bug)."""
    return name.rsplit("/", 1)[-1].lower().endswith((".jpg", ".jpeg"))


@dataclass
class _Rec:
    kind: str            # 'mp4' | 'raw' | 'dcm'
    name: str
    t: Optional[float]
    width: Optional[int] = None
    nframes: int = 0
    dcm_index: Optional[str] = None


@dataclass
class Capture:
    t: Optional[float]
    raw_folder: str
    mp4: Optional[str]
    dcm: Optional[str]
    width: Optional[int]
    nframes: int
    depth_setting: str           # 'deep' | 'shallow' | 'unknown'
    is_sweep: bool
    identity: str                # 'sweep' | 'femur' | 'rf' | 'unknown'
    pairing: str = "unpaired"    # 'paired' | 'orphan' | 'unpaired' — mp4<->RawImages mutual-nearest
    flags: List[str] = field(default_factory=list)


@dataclass
class DeconflictResult:
    captures: List[Capture]
    orphan_dcms: List[str]
    warnings: List[str]

    def summary(self) -> str:
        by = {}
        for c in self.captures:
            by[c.identity] = by.get(c.identity, 0) + 1
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(by.items()))
        return (f"{len(self.captures)} captures ({parts}); "
                f"{sum(1 for c in self.captures if c.dcm)} with .dcm, "
                f"{len(self.orphan_dcms)} orphan .dcm; {len(self.warnings)} warning(s)")


def _classify_modalities(captures: List[Capture], warnings: List[str]) -> None:
    """Assign each capture's identity + depth_setting RELATIVE to the study's own SWEEP.

    Modality is a RANK within the study, never an absolute width (Akadir 2026-06-25): probe-829's
    femur (~285 px) and probe-831's center-RF (~285 px) share a width but are different modalities,
    so only the RELATION holds. The longest clip is the SWEEP and fixes the shallow-depth anchor; a
    cine meaningfully narrower (deeper) than the sweep is the FEMUR; a cine at ~sweep width is the
    center-RF. Femur is always the deepest (Akadir). The three groups need NOT share a session (a
    femur may be borrowed from a cleaner shot). An ORPHAN capture (RawImages with no paired mp4) never
    anchors the depth scale, so a stray shallow take can't hijack the anchor and displace the real RF
    into a false "femur" (AKA017: a 285px/6.9cm stills group with no video was the widest capture → it
    set the shallow anchor and cascaded both real cines into "femur"). The orphan still gets a relative
    role, so a duplicate one is auto-excluded downstream (materialize `_classify_orphans`) while a lone
    borrowed one is kept. Mutates captures in place; records gaps in `warnings` — a missing femur => no
    geometry; a missing RF => no echo-intensity."""
    sweeps = [c for c in captures if c.is_sweep]
    the_sweep = max(sweeps, key=lambda c: c.nframes) if sweeps else None
    widths = [c.width for c in captures if c.width is not None and c.pairing != "orphan"]
    shallow_w = max(widths) if widths else None   # widest frame = shallowest plane = the RF/sweep depth

    def _is_femur(c: Capture) -> bool:
        # Femur = the DEEPEST cine: STRICTLY narrower than the study's shallowest capture (the
        # RF/sweep plane). A capture AT the shallowest width is the RF/sweep, never the femur — there
        # is NO absolute-width fallback (probe-dependent widths made it mislabel a shallow RF as femur
        # on the deeper-probe studies). The sweep settles deep in v7 / shallow in v8, so the anchor is
        # the shallowest width, not the sweep.
        return (not c.is_sweep and c.width is not None and shallow_w is not None
                and c.width < shallow_w)

    femur_cines = [c for c in captures if _is_femur(c)]
    for c in captures:
        if c.is_sweep:
            c.identity, c.depth_setting = "sweep", "shallow"
        elif c.width is None:
            c.identity, c.depth_setting = "unknown", "unknown"
        elif _is_femur(c):
            c.identity, c.depth_setting = "femur", "deep"
        else:
            c.identity, c.depth_setting = "rf", "shallow"

    roles = {c.identity for c in captures}
    if len(femur_cines) > 1:
        warnings.append(f"{len(femur_cines)} deep cines — ALL kept as femur (their geometry frames "
                        f"are surfaced for the manual femur pick); disambiguate in selection")
    if "femur" not in roles:
        warnings.append("no femur (deep) cine — geometry measures unavailable for this study")
    if "rf" not in roles:
        warnings.append("no center-RF (shallow) cine — echo-intensity (int_cv) unavailable")
    if the_sweep is None:
        warnings.append("no sweep capture")


def _pair_mutual(raws: List[_Rec], mp4s: List[_Rec],
                 warnings: List[str]) -> Dict[int, Tuple[Optional[str], str]]:
    """Pair each RawImages folder with its mp4 by MUTUAL NEAREST in time (within _PAIR_MAX_S): a
    RawImages and an mp4 pair iff each is the other's closest. This is stable — an orphaned leftover
    take (its mp4 deleted, the RawImages forgotten) is no mp4's nearest RawImages, so it stays
    UNPAIRED (orphan) instead of stealing a real capture's video and cascading every later capture
    onto the wrong mp4 (the 26jun26_020 failure, where time-order-nearest orphaned the real sweep).
    Returns {raw_index: (mp4_name|None, 'paired'|'orphan')}; appends an orphan warning per side."""
    def _nearest(t: Optional[float], others: List[_Rec]) -> Optional[int]:
        cand = [(abs(o.t - t), j) for j, o in enumerate(others)
                if t is not None and o.t is not None and abs(o.t - t) <= _PAIR_MAX_S]
        return min(cand)[1] if cand else None

    r2m = {i: _nearest(r.t, mp4s) for i, r in enumerate(raws)}
    m2r = {j: _nearest(m.t, raws) for j, m in enumerate(mp4s)}
    out: Dict[int, Tuple[Optional[str], str]] = {}
    paired_mp4: set = set()
    for i, r in enumerate(raws):
        j = r2m[i]
        if j is not None and m2r.get(j) == i:
            out[i] = (mp4s[j].name, "paired")
            paired_mp4.add(j)
        else:
            out[i] = (None, "orphan")
            warnings.append(f"RawImages {r.name.rsplit('/',1)[-1]} has no mp4 "
                            f"(no mutually-nearest video — deleted/re-recorded take?)")
    for j, m in enumerate(mp4s):
        if j not in paired_mp4:
            warnings.append(f"mp4 {m.name.rsplit('/',1)[-1]} has no RawImages folder")
    return out


def _mark_sweeps(captures: List[Capture], sweep_min_frames: int) -> None:
    """Set is_sweep per capture: a content-frame count >= sweep_min_frames is a sweep. Fallback for a
    firmware frame-rate change that drops every clip below the bar — if NONE clear it, the longest
    clip is the sweep provided it dominates (>= _SWEEP_REL_FACTOR x the next-longest and >=
    _SWEEP_REL_FLOOR), so a relatively-clear sweep is never lost to an absolute threshold alone."""
    for c in captures:
        c.is_sweep = c.nframes >= sweep_min_frames
    if not any(c.is_sweep for c in captures) and len(captures) >= 2:
        longest = max(captures, key=lambda c: c.nframes)
        nxt = max(c.nframes for c in captures if c is not longest)
        if nxt and longest.nframes >= _SWEEP_REL_FACTOR * nxt and longest.nframes >= _SWEEP_REL_FLOOR:
            longest.is_sweep = True
            longest.flags.append(f"sweep by relative length ({longest.nframes} frames vs next {nxt})")


def deconflict(records: List[_Rec], *, sweep_min_frames: int = _SWEEP_MIN_FRAMES) -> DeconflictResult:
    """Pure core: group artifact records into captures + flag edge cases."""
    raws = sorted([r for r in records if r.kind == "raw"], key=lambda r: r.t or 0)
    mp4s = sorted([r for r in records if r.kind == "mp4"], key=lambda r: r.t or 0)
    dcms = sorted([r for r in records if r.kind == "dcm"], key=lambda r: r.t or 0)
    warnings: List[str] = []

    # 1) pair each RawImages folder with its mp4 (MAKE-VIDEO co-product) by mutual nearest.
    pairing = _pair_mutual(raws, mp4s, warnings)
    captures: List[Capture] = []
    for i, r in enumerate(raws):
        mp4, status = pairing[i]
        # identity + depth_setting are assigned RELATIVELY once every capture is built (see
        # _classify_modalities below) — a single capture's width is meaningless without the
        # study's own sweep to anchor deep-vs-shallow.
        captures.append(Capture(t=r.t, raw_folder=r.name, mp4=mp4, dcm=None, width=r.width,
                                nframes=r.nframes, depth_setting="unknown", is_sweep=False,
                                identity="unknown", pairing=status, flags=[]))

    # 1a) sweep determination (content-frame bar + relative-longest fallback) + the ambiguity flag.
    _mark_sweeps(captures, sweep_min_frames)
    for c in captures:
        if _AMBIG_FRAMES[0] <= c.nframes <= _AMBIG_FRAMES[1]:
            c.flags.append(f"length-ambiguous ({c.nframes} frames near sweep/still boundary)")

    # 1b) relative modality classification (needs every capture's width first).
    _classify_modalities(captures, warnings)

    # 2) attach each .dcm to the LATEST PRECEDING capture (SAVE-IMAGE follows its
    #    MAKE-VIDEO), dcm-less and within the window; leftovers are orphans.
    orphan_dcms: List[str] = []
    cap_by_t = sorted(captures, key=lambda c: c.t or 0)
    for d in dcms:
        match = None
        for c in cap_by_t:
            if c.dcm is not None or c.t is None or d.t is None:
                continue
            if c.t <= d.t and (d.t - c.t) <= _DCM_WINDOW_S:
                match = c  # keep advancing -> the latest qualifying preceding capture
        if match is None:
            orphan_dcms.append(d.name)
        else:
            match.dcm = d.name
    for c in captures:
        if c.mp4 and c.dcm is None:
            c.flags.append("no .dcm (SAVE-IMAGE skipped — depth still from frame width)")
    if orphan_dcms:
        warnings.append(f"{len(orphan_dcms)} orphan .dcm (SAVE-IMAGE with no capture)")
    return DeconflictResult(captures=sorted(captures, key=lambda c: c.t or 0),
                            orphan_dcms=orphan_dcms, warnings=warnings)


def read_study_records(zip_path: str) -> List[_Rec]:
    """Read a study zip into artifact records (one frame read per RawImages folder for width)."""
    def _is_junk(n):
        return any(p.startswith(".trashed-") or p.startswith(".") or p == "__MACOSX"
                   for p in n.split("/") if p)
    recs: List[_Rec] = []
    folders: dict = {}
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not _is_junk(n)]
        for n in names:
            low = n.lower()
            if low.endswith(".mp4"):
                recs.append(_Rec("mp4", n, _seconds(n)))
            elif low.endswith(".dcm"):
                idx = _DCM_IDX.search(n.rsplit("/", 1)[-1])
                recs.append(_Rec("dcm", n, _seconds(n), dcm_index=idx.group(1) if idx else None))
            else:
                parts = n.split("/")
                for i, p in enumerate(parts):
                    if p.lower().startswith("rawimages"):
                        folders.setdefault("/".join(parts[:i + 1]), []).append(n)
                        break
        for folder, members in folders.items():
            frames = [m for m in members if not m.endswith("/")]
            # Width MUST come from a CONTENT frame: a RawImages folder also holds 256-px auxiliary
            # PNGs (_raw / _beforeDSC) that, read first, collapse every capture to width 256 and
            # break the deep/shallow split (Akadir 2026-06-25). _is_content_frame keeps the .jpg
            # frames, drops the aux PNGs; fall back to any frame only if (degenerately) none are jpg.
            content = [m for m in frames if _is_content_frame(m)]
            width = None
            for fr in (content or frames):
                try:
                    width = _img_width(z.read(fr))
                except Exception:
                    width = None
                if width:
                    break
            # nframes = CONTENT frames (.jpg), not all members — the 2 aux PNGs per frame would
            # otherwise 3x-inflate the count the sweep/still bar is measured against.
            recs.append(_Rec("raw", folder, _seconds(folder), width=width,
                             nframes=len(content) if content else len(frames)))
    return recs


def deconflict_zip(zip_path: str) -> DeconflictResult:
    return deconflict(read_study_records(zip_path))
