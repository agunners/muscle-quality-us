"""L3b — Playwright headless screenshot walkthrough of the assessment app.

Drives the REAL app (real model, real segmenter, real pipeline) end-to-end in
a headless browser and screenshots each phase + the N4 draft / finalized /
back-edit states + dev-mode → `data/test_artifacts/<UTC-ts>/*.png` with a
`manifest.json`, and symlinks `data/test_artifacts/latest`.

Playwright's Python package is not in the venv, but its browser binaries are
already cached at ~/.cache/ms-playwright/ — so this self-installs the package
with one `pip install playwright` (no browser download).

After this runs, the implementing agent MUST `Read` every screenshot and write
a per-screenshot assessment — "headline cards render stacked, cohort viz shows
the case marker, no fullscreen icon" — concrete observations, not "I assume it
rendered". That is the "screenshot and look" + "assess the assessment" step.

Usage:
    source scripts/env.sh && .venv/bin/python scripts/ui_screenshot_walkthrough.py
    [--port 8765] [--study AGZ855] [--keep-server]
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- launcher preamble — bridge sys.path so env imports resolve.
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import preflight_helpers.env_bootstrap  # noqa: F401,E402  side-effect

from preflight_helpers import env  # noqa: E402

APP = REPO / "labeling_app" / "assessment_app.py"
# The picker keys its add/remove buttons per ROLE (p03_frame_picker._pick_btn_key):
# femur -> pick_femur_<fb>, centre-RF -> pick_rf_<fb>; only the legacy SWEEP kept the bare
# pick_<fb>. Matching the bare form alone found nothing on every v8 study (the default tab is
# femur), so the walk died at phase 3 with "frame keys: []" — 2026-09-05.
FRAME_BASE_RE = re.compile(r"st-key-pick_(?:femur_|rf_)?(\d{8}_\d{9})")
_ROLE_KEY = {"femur": "pick_femur_", "rf": "pick_rf_", "sweep": "pick_"}


# ---------------------------------------------------------------------------
# Playwright self-install
# ---------------------------------------------------------------------------

def ensure_playwright():
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("[setup] playwright not in venv — installing the package "
              "(browsers are already cached, no download) …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
        importlib.invalidate_caches()
    from playwright.sync_api import sync_playwright  # noqa: E402
    return sync_playwright


# ---------------------------------------------------------------------------
# Streamlit server lifecycle
# ---------------------------------------------------------------------------

def launch_streamlit(port: int) -> subprocess.Popen:
    sub_env = dict(os.environ)
    bridge = ":".join([str(Path(__file__).resolve().parents[1] / "private" / "muscle_quality_exploration"), str(Path(__file__).resolve().parents[1] / "private" / "muscle_model_training/src")])
    sub_env["PYTHONPATH"] = bridge + ":" + sub_env.get("PYTHONPATH", "")
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.headless", "true",
        "--server.port", str(port),
        "--server.address", "127.0.0.1",
        "--browser.gatherUsageStats", "false",
    ]
    print(f"[server] launching: {' '.join(cmd)}")
    return subprocess.Popen(cmd, env=sub_env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def wait_healthy(port: int, timeout_s: float = 90.0) -> bool:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200 and r.read().strip() == b"ok":
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


# ---------------------------------------------------------------------------
# Walkthrough
# ---------------------------------------------------------------------------

class Walk:
    """Step runner — screenshots + a manifest, resilient to a step failing."""

    def __init__(self, page, outdir: Path):
        self.page = page
        self.outdir = outdir
        self.shots: list[dict] = []
        self._idx = 0

    # Streamlit keeps the document at viewport height with an internal scroll
    # container, so Playwright's full_page=True silently captures only the
    # viewport. This CSS unlocks the inner containers so the document grows to
    # its real content height — then a viewport screenshot sized to that
    # height captures the whole long assessment page.
    _UNLOCK_CSS = """
      [data-testid="stMain"], [data-testid="stAppViewContainer"],
      section.main, .main, [data-testid="stAppScrollToBottomContainer"] {
        height: auto !important; max-height: none !important;
        overflow: visible !important;
      }
      [data-testid="stSidebar"], [data-testid="stSidebarContent"] {
        height: auto !important; overflow: visible !important;
      }
    """

    def unlock_scroll(self) -> None:
        try:
            self.page.add_style_tag(content=self._UNLOCK_CSS)
        except Exception as e:
            print(f"[warn] unlock_scroll: {e!r}")

    def shot(self, name: str, note: str = "", ok: bool = True) -> None:
        self._idx += 1
        fname = f"{self._idx:02d}_{name}.png"
        try:
            self.unlock_scroll()
            self.page.wait_for_timeout(250)
            content_h = self.page.evaluate(
                "Math.max(document.body.scrollHeight, "
                "document.documentElement.scrollHeight, "
                "(document.querySelector('[data-testid=\"stMain\"]')||{}).scrollHeight||0)"
            )
            shot_h = min(max(int(content_h) + 40, 1050), 9000)
            self.page.set_viewport_size({"width": 1680, "height": shot_h})
            self.page.wait_for_timeout(450)
            self.page.screenshot(path=str(self.outdir / fname), full_page=True)
            self.page.set_viewport_size({"width": 1680, "height": 1050})
        except Exception as e:
            note = f"{note}  [screenshot fallback: {e!r}]"
            try:
                self.page.screenshot(path=str(self.outdir / fname))
            except Exception:
                ok = False
        self.shots.append({"idx": self._idx, "name": name, "file": fname,
                           "note": note, "ok": ok})
        print(f"[shot] {fname}  {note}")

    # -- low-level interactions ------------------------------------------
    def click_key(self, key: str, settle_ms: int = 1500) -> None:
        self.page.locator(f".st-key-{key}").locator("button").first.click()
        self.page.wait_for_timeout(settle_ms)

    def set_radio(self, key: str, value: str, settle_ms: int = 1200) -> None:
        self.page.locator(f".st-key-{key}").get_by_text(value, exact=True).click()
        self.page.wait_for_timeout(settle_ms)

    def expand(self, label_substr: str, settle_ms: int = 900) -> None:
        """Open a collapsed st.expander by a substring of its header label —
        so the screenshot captures the panel content, not just its header.

        Streamlit renders the expander as <details><summary>…; we click the
        <summary> only when its <details> parent is not already `open`.
        """
        try:
            summ = (self.page.locator('[data-testid="stExpander"]')
                    .filter(has_text=label_substr)
                    .locator("summary").first)
            if not summ.count():
                return
            details = summ.locator("xpath=..")
            if not details.evaluate("el => el.open"):
                summ.click()
                self.page.wait_for_timeout(settle_ms)
        except Exception as e:
            print(f"[warn] expand({label_substr!r}): {e!r}")

    def frame_keys(self, n: int, role: str = "femur") -> list[str]:
        """First n frame_base tokens from the picker's `+ add` buttons in the ACTIVE tab."""
        prefix = _ROLE_KEY[role]
        rx = re.compile(r"st-key-" + prefix + r"(\d{8}_\d{9})")
        html = self.page.content()
        seen: list[str] = []
        for m in rx.finditer(html):
            if m.group(1) not in seen:
                seen.append(m.group(1))
            if len(seen) >= n:
                break
        return seen

    def pick_frame(self, role: str, fb: str, settle_ms: int = 1500) -> None:
        """Click one thumbnail's add button in the given role's tab."""
        self.click_key(f"{_ROLE_KEY[role]}{fb}", settle_ms=settle_ms)

    def switch_capture(self, label: str, settle_ms: int = 2500) -> None:
        """Move the picker's capture radio (Femur / Center-RF / Sweep)."""
        self.page.locator(".st-key-picker_modality").get_by_text(label, exact=False).first.click()
        self.page.wait_for_timeout(settle_ms)


def walkthrough(page, outdir: Path, study: str) -> list[dict]:
    w = Walk(page, outdir)
    page.set_viewport_size({"width": 1680, "height": 1050})
    page.wait_for_timeout(2500)
    w.unlock_scroll()

    # ---- Phase 1: study selector
    w.shot("study_selector", "Phase 1 — study list; PHI guardrail (no surnames)")
    try:
        w.click_key(f"pick_{study}", settle_ms=2000)
    except Exception as e:
        w.shot("study_select_FAILED", f"could not pick {study}: {e!r}", ok=False)
        return w.shots

    # ---- Phase 2: model selector
    w.shot("model_selector", "Phase 2 — bundle list + default selection")
    w.click_key("load_model_continue", settle_ms=12000)   # real model load

    # ---- Phase 3: frame picker (sticky sidebar — F2/F3)
    w.shot("frame_picker", "Phase 3 — full-width grid; sidebar carries counter "
                           "+ Segment button + selection panel (F2 sticky fix)")
    # v8 needs a pick from BOTH measurement captures — the sidebar blocks Segment until the
    # femur AND the centre-RF have one ("still need: femur, center-RF"). The sweep is optional
    # (own-study echo cross-check only), so the walk covers the two that gate the score.
    fem = w.frame_keys(3, "femur")
    print(f"[walk] femur frame keys: {fem}")
    if len(fem) < 3:
        w.shot("frame_picker_NO_FRAMES", f"femur tab only found {fem}", ok=False)
        return w.shots
    for fb in fem[:2]:
        w.pick_frame("femur", fb)

    w.switch_capture("Center-RF")
    rf = w.frame_keys(1, "rf")
    print(f"[walk] centre-RF frame keys: {rf}")
    if not rf:
        w.shot("frame_picker_NO_RF_FRAMES", "centre-RF tab has no pickable frame", ok=False)
        return w.shots
    w.pick_frame("rf", rf[0])
    fbs = fem[:2] + rf[:1]          # every picked frame — these are what phase 4 gates

    w.shot("frame_picker_picked", "Phase 3 — 2 femur + 1 centre-RF picked; selection panel "
                                  "+ anchor marking visible in sidebar")
    w.click_key("seg_continue", settle_ms=25000)          # real segmenter run

    # ---- Phase 4: frame review + 3-stage gates
    w.shot("frame_review", "Phase 4 — raw + overlay + magenta eroded contour; "
                           "3-gate cascade per frame")
    for fb in fbs:
        try:
            w.set_radio(f"g1_{fb}", "Yes")
        except Exception as e:
            print(f"[warn] g1_{fb}: {e!r}")
    for fb in fbs:
        try:
            w.set_radio(f"g2_{fb}", "Yes")
            w.set_radio(f"g3_{fb}", "Yes")
        except Exception as e:
            print(f"[warn] g2/g3_{fb}: {e!r}")
    w.shot("frame_review_gated", "Phase 4 — gates set usable/perfect/analysis=Yes")
    w.click_key("lock_gates", settle_ms=20000)            # real pipeline run

    # ---- Phase 5: assessment view — DRAFT state (N4)
    # Open the expanders so the screenshot captures the panel content (the
    # F5 run-comparison panel + the N6 secondary-feature box-plots).
    for _label in ("Run comparison", "Secondary features — cohort", "Secondary features"):
        w.expand(_label)
    w.shot("assessment_draft", "Phase 5 DRAFT — amber 'Draft run' banner + "
                               "Accept; F9 stacked headline cards + F7 direction "
                               "lines; F10a transparency caption; cohort viz; "
                               "F5 run-comparison + study-bootstrap panel")
    try:
        w.click_key("accept_run", settle_ms=4000)
        w.shot("assessment_finalized", "Phase 5 FINALIZED — green 'Run accepted' "
                                       "banner; same numbers as the draft")
    except Exception as e:
        w.shot("accept_run_FAILED", f"{e!r}", ok=False)

    # ---- stale-cache: back to picker, add a frame, return
    try:
        w.click_key("step_2", settle_ms=2000)             # stepper → picker
        w.switch_capture("Femur")                         # the stepper reopens on the last tab
        w.pick_frame("femur", fem[2])                     # add a 3rd femur frame
        w.click_key("seg_continue", settle_ms=25000)
        w.set_radio(f"g1_{fem[2]}", "Yes")
        w.set_radio(f"g2_{fem[2]}", "Yes")
        w.set_radio(f"g3_{fem[2]}", "Yes")
        w.click_key("lock_gates", settle_ms=20000)
        w.shot("assessment_back_edit", "Phase 5 BACK-EDIT — editing inputs after "
                                       "Accept branched a fresh DRAFT (stale "
                                       "cache invalidated → new run_id)")
    except Exception as e:
        w.shot("back_edit_FAILED", f"{e!r}", ok=False)

    # ---- dev-mode toggle
    try:
        page.locator(".st-key-dev_mode_toggle").click()
        page.wait_for_timeout(1500)
        w.shot("dev_mode", "Dev-mode ON — raw paths + provenance expanders "
                           "available (must be OFF for the boss demo)")
    except Exception as e:
        w.shot("dev_mode_FAILED", f"{e!r}", ok=False)

    return w.shots


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--study", default="AGZ855")
    ap.add_argument("--keep-server", action="store_true",
                    help="leave the streamlit server running after the walk")
    args = ap.parse_args()

    sync_playwright = ensure_playwright()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = env.TEST_ARTIFACTS_DIR / ts
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[out] {outdir}")

    server = launch_streamlit(args.port)
    shots: list[dict] = []
    try:
        if not wait_healthy(args.port):
            print("[fatal] streamlit did not become healthy in time")
            out = server.stdout.read() if server.stdout else ""
            print(out[-2000:])
            return 2
        print("[server] healthy")
        time.sleep(2.0)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{args.port}", wait_until="networkidle",
                      timeout=60000)
            shots = walkthrough(page, outdir, args.study)
            browser.close()
    finally:
        if not args.keep_server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    manifest = {
        "generated_at_utc": ts,
        "app": str(APP),
        "port": args.port,
        "study": args.study,
        "n_screenshots": len(shots),
        "n_failed": sum(1 for s in shots if not s["ok"]),
        "screenshots": shots,
    }
    with open(outdir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Symlink data/test_artifacts/latest → this run.
    latest = env.TEST_ARTIFACTS_DIR / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(outdir.name)
    except OSError as e:
        print(f"[warn] could not symlink latest: {e!r}")

    print(f"\n[done] {len(shots)} screenshots, {manifest['n_failed']} failed")
    print(f"[done] {outdir}")
    print(f"[done] manifest: {outdir / 'manifest.json'}")
    return 0 if manifest["n_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
