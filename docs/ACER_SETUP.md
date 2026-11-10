# Acer setup — the single source of truth

The muscle work runs on the **Acer Predator** (`akadir@akadir-Predator-PT315-52`, `/home/akadir`,
RTX 2070 / CUDA 13, same layout as the DGX). This doc is committed, so it travels by `git pull` and
is always current. **Two channels:**

| Channel | Carries | Why |
|---|---|---|
| **git** (`git pull`) | code + this doc + the migration scripts (`deploy/acer_migration/`) | small, versioned |
| **T7 drive** (`/media/akadir/T7 Shield/muscle_migration/`) | the data tars + `decision_log.csv` | big + PHI — never git |

PHI (patient folder names, clinical fields) rides the **encrypted-optional T7 only**, never git.

**Switching between the Acer and the DGX? Go straight to [§F](#f-switching-machines--the-handover-runbook-either-direction).**
Sections A–E are the one-time DGX→Acer migration record; §F is the standing, symmetric runbook and
applies whichever machine you are leaving and whichever you are arriving at.

---

## A. Finish the current Acer (repos + venvs + apps already set up) — 3 steps

```bash
# 1. code: the env.py data-root fix + this doc
cd ~/dev/muscle_mania && git pull

# 2. data: reference cohort + the symlinked-operator studies + decision_log (connect the T7 first)
bash "/media/akadir/T7 Shield/muscle_migration/apply_fixes_acer.sh"

# 3. restart the assessment app and hit Refresh in the study picker
```

**Verify — one command, no app needed:**
```bash
source scripts/env.sh && .venv/bin/python scripts/check_parity.py
```
It reproduces the picker's counts + checks every data root against the committed baseline and prints
**`DATA PARITY: MATCH ✓`** — or names the exact gap (e.g. `roots.goruntuler_backup_frames: this=None
baseline=330`, or `studies.cohort.valid: this=0 baseline=272`). Then confirm in the app: the picker
shows the cohort validating and **AKA049 + the anonaaq studies** back — not 47/50.

### What `apply_fixes_acer.sh` does (idempotent)
- `goruntuler_backup.tar` → `~/dev/goruntuler_backup` — the **272 reference-cohort frames** (`env.py:10`).
- `studies_root_symlinked_ops.tar` → replaces the **dangling operator symlinks** (`anonaaq`,
  `anonabc`, `anonabn`, `anonabn_olcum`) with **real study data** (this is what `AKA049` needed).
- `fixes/decision_log.csv` → `configs/` (gitignored — clinical fields) + the `env.py` path fix.

---

## B. The two apps (your definition of done)

```bash
# assessment app
cd ~/dev/muscle_mania && source scripts/env.sh && ./run_app.sh          # http://localhost:8502

# june viewer — SINGLE SOURCE (same 66 studies the assessment app scores), new terminal
VIEWER_DATA_ROOT=~/dev/muscle_mania/data/studies_root_flat \
  bash ~/Downloads/june_protocol_goruntuler/_viewer/run.sh              # http://localhost:8520

# your Claude setup + login
cp -rn ~/.claude_from_dgx/.claude/* ~/.claude/ && cd ~/dev/muscle_mania && claude   # then /login
```

Rebuild a venv if needed with your own recipe — `setup_acer.sh` replicates `ignite`
(`requirements-dgx.txt` + `requirements-fastai.txt` + `requirements.txt`).

---

## C. PHI cleanup — after both apps are verified

```bash
rm -rf "/media/akadir/T7 Shield/muscle_migration"     # deletes the cleartext tars + decision_log
```

---

## D. From scratch (a fresh machine) — for the record

1. On the DGX: `bash deploy/acer_migration/push_tar.sh` (writes all tars + fixes to the T7). It
   auto-detects symlinked operators and `--dereference`s them, and includes `goruntuler_backup`.
2. On the new machine: `bash <T7>/muscle_migration/pull_tar.sh` (unpacks everything **and**
   auto-runs `apply_fixes_acer.sh`) → `setup_acer.sh` → the two apps.

### D2. Third machine (2026-08-18) — the ordered quickstart, before any folder exists

**Executable form: the T7 handover.** `deploy/t7_bootstrap/push_t7_handover.sh` (run on the departing
machine) loads the drive completely — repos with `.git`, all gitignored data, Claude project memory,
requirements pins, sha256 manifest — and puts `MUSCLE_START_HERE.md` at the drive root; on the new
machine ONE command runs the whole arrival: `bootstrap_new_machine.sh` (verify → unpack → venvs →
receipts). **The list below is the manual/reference path and the no-T7 fallback (git + rsync from
`dgx-host`).** Written the day the DGX went dormant; the paired departure state is HANDOFF.md's
2026-08-18 block.

1. **Layout first, before cloning anything: user `akadir`, repos under `~/dev/`.** §E is not a
   suggestion — `env.sh:11,22` and `env.py` hardcode `/home/akadir/dev/…`. On Linux, make the user
   match. On macOS the home is `/Users/akadir`, so either symlink `/home/akadir → /Users/akadir` or
   patch every hardcoded path (`grep -rn "/home/akadir" scripts/env.sh scripts/preflight_helpers/env.py`). Prefer
   Linux; **macOS carries a second, worse trap** — APFS normalizes Unicode in filenames, and the
   study uid is `NR + sha8(folder name)`, so Turkish characters re-encoded on copy silently orphan
   the committed cohort (§F2's byte-exact warning). If the machine must be a Mac, the copy-integrity
   check below is mandatory, not advisory.
2. **Clone the four repos** (all private under `agunners`; GitHub auth first — if `git push` later
   claims a bad token on a headless shell, that is usually the GUI-keyring gotcha, not auth):
   `muscle_mania`, `muscle_quality_exploration`, `muscle_model_training`, `brain`, each into `~/dev/`
   (brain wherever `~/projects` will point; then the §F4 loader lines).
3. **Data over, one of two routes.** Tested: §D's T7 tar bundle (run `push_tar.sh` on the DGX —
   it is dormant, not wiped). Direct: rsync from `dgx-host` while it is reachable —
   `data/studies_root/ data/study_registry.csv transport/ exports/ configs/decision_log.csv
   data/calibration_labels.jsonl goruntuler_backup` (the labels file is deliberately uncommitted —
   19 QA-calibration labels live only there; the T7 route also carries a `~/.claude` snapshot, which
   is how auto-memory travels if wanted, merged with `cp -rn`).
4. **venv** per §A/`setup_acer.sh` (Python 3.12; the fastai/torch stack; then
   `source scripts/env.sh && .venv/bin/python -c "import cv2, yaml, pywt; print('deps ok')"` —
   `pywt` missing drops 11 texture stems *silently*). Playwright + chromium for render-and-look.
5. **`gws auth login` BEFORE the substrate stage** — a dead sheet read blanks kontrol + all five
   force columns while the contract still reads COMPLETE (the 2026-08 silent-force-loss incident).
   Expect the OAuth URL bug: hand-edit `prompt=select_account+consent` → `prompt=consent`.
6. **Rebuild what git cannot carry, never the cohort:**
   `ingest_all.py --apply --only audit,freezelog,registry,substrate` → `--status` must show the SAME
   single FAIL as the DGX left (26 ready rows 127–152, held by Akadir's order) + `HANDOVER: CURRENT`
   → `check_parity.py` must read `MATCH ✓` (this is the copy-integrity proof — on any Unicode doubt,
   this line is the verdict).
7. **This machine is now PRIMARY (§F0).** The r5 refreeze — duplicate kill + all 17 releases + the
   26 arrivals in one gated `--apply` — happens here, on Akadir's go. One capability note: the
   embeddings rung is GPU work; if this machine has no CUDA, plan that run back on `dgx-host` over
   SSH, or defer it — the freeze itself is CPU-bound and fine anywhere with cores.

---

## E. Gotchas / decisions (learned the hard way this migration)

- **`git` carries almost none of the substance** here — studies, models, recordings are gitignored
  or symlinked. The T7 carries them.
- **`env.py` hardcodes data roots outside `data/`** — `goruntuler_backup` (272-cohort frames) and
  4 studies_root operators that are **symlinks into `~/dev/goruntuler`**. `tar` stores symlinks, not
  their targets, so they dangle on a new machine. Fix = ship the real data (`--dereference`) → studies_root
  is now self-contained. Any future migration: grep `env.py` for **every** hardcoded `/home/akadir/dev/…`.
- **`decision_log.csv`** (`env.py:18`, was a `..._codex_implementation/` path) is now in-repo at
  `configs/decision_log.csv` but **gitignored** (clinical `comorbidities`) → T7 only.
- **Single-source viewer**: launch with `VIEWER_DATA_ROOT=…/studies_root_flat` so both apps read the
  same studies; bare `./run.sh` reads the legacy `~/Downloads` copy.
- **Layout**: keep `akadir` + `~/dev/` — the absolute paths in `env.sh`/`env.py`/flat symlinks depend on it.
- Scoring **new** studies never needed any of the above (frozen `data/cohort_stats/`); the gaps only
  affected the reference cohort + symlinked operators.

---

## F. Switching machines — the handover runbook (either direction)

Code syncs by git; data does not — so "the files copied" is never proof. This section is symmetric:
Acer↔DGX, whichever you are leaving and whichever you are arriving at.

**Three classes, each with exactly one correct home.** Get this right and the rest follows:

| | Travels by git? | So it runs… |
|---|---|---|
| code, docs, `data/cohort_stats/` (37 tracked files) | **yes** | anywhere, once |
| the **cohort freeze** → `per_study_cohort_new.csv` + `new_round_cohort_provenance.json` | **yes** — both tracked | **once, on one machine** |
| the **substrate** → `data/qa_master.csv`, `data/qa_frames.csv` | no — gitignored (PHI) | **on every machine** |
| raw studies, `study_registry.csv`, `transport/`, `exports/`, `configs/decision_log.csv` | no — gitignored | rebuilt from Drive per machine |

The cohort stage segments every frame of every v8 study — expensive — and its output is **committed**.
So freeze once, on one machine, and the other gets it free by `git pull`. Don't pay it twice.

### F0. Who may freeze — the PRIMARY machine, and only one at a time

**The primary machine is wherever you are working. It is not a fixed machine, and it is not always the
Acer.** Whoever is primary may freeze; the other pulls the result. This is `add-machine.md` §5 —
*one machine at a time per repo* — applied to the one artifact that is both expensive and committed.

An earlier version of this file said "never run the `cohort` stage on the arriving machine", which
read as *the DGX may never freeze*. That was a rule about **arrival** — do not rebuild a reference
that just arrived by git — over-generalised into a rule about **permission**, and it is wrong. On
2026-08-03 the DGX became primary mid-session; the correct next freeze belongs there, not here.

Three consequences, all of which bite if you get the direction wrong:

- **`check_parity.py --save-baseline` runs on the PRIMARY**, after its contract reads COMPLETE. The
  baseline is a claim that *this* machine is the golden state; saving it on the dormant one inverts
  the meaning ([[reference_machine_handover]] — the two staleness directions need opposite actions).
- **The freeze is CPU-bound on frame decoding, not GPU.** On the Acer the GPU sat at 2–4% for 1h56m
  while 116 studies decoded. Core count decides this, so the faster machine is the better host —
  measure before assuming it is the one you are standing at.
- **A freeze that ADDS a study moves data git cannot carry.** The new `studies_root/` folder is
  gitignored PHI, so the other machine ends up with a cohort referencing a study it does not hold.
  Close that by re-ingesting the study there from Drive (same code, same source) rather than copying.

### F1. Before you leave a machine

```bash
source scripts/env.sh && .venv/bin/python scripts/ingest_all.py --apply   # answer the FREEZE gate
.venv/bin/python scripts/ingest_all.py --status                           # must read COMPLETE
git add data/cohort_stats/per_study_cohort_new.csv \
        data/cohort_stats/new_round_cohort_provenance.json
.venv/bin/python scripts/check_parity.py --save-baseline                  # ONLY after COMPLETE
git add scripts/parity_baseline.json && git commit && git push
```

**Order is load-bearing.** Save the baseline only after `--status` reads COMPLETE, or you freeze a
half-finished state as the golden one. Stage files **explicitly** — `data/calibration_labels.jsonl` is
deliberately uncommitted and `git add -A` sweeps it.

To run only the stages you owe (and leave the shared operator datasheet untouched):
`ingest_all.py --apply --only cohort,substrate`. The two datasheet-writing stages are gated for a
reason — other people read that sheet.

### F1b. Test the drive before you leave (2026-09-04 — the step that caught two real hazards)

After `push_t7_handover.sh`, run the drive's own bootstrap into a throwaway home on the machine you are
leaving, then take the receipts on the copy — the same commands the arrival will run:

```bash
mkdir -p ~/acer_sim && HOME=~/acer_sim USER=akadir SETUP_ACER_NOBUILD=1 \
  bash "/media/$USER/T7 Shield/muscle_migration/bootstrap_new_machine.sh"        # verify + unpack (~20 min)
# receipts on the copy: the grading table's checks, the pack's checksums, byte-equality, repo heads
(cd ~/acer_sim/dev/muscle_grading_table && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-analysis.txt \
   && HOME=~/acer_sim .venv/bin/python -m grading_app.cli doctor && HOME=~/acer_sim bash scripts/release_check.sh)
(cd ~/acer_sim/dev/muscle_mania/data/grading_pack/pack_r5_* && sha256sum -c SHA256SUMS --quiet && echo pack OK)
for r in muscle_mania muscle_quality_exploration muscle_model_training muscle_grading_table goruntuler; do
  echo "$r $(git -C ~/acer_sim/dev/$r rev-parse --short HEAD) dirty $(git -C ~/acer_sim/dev/$r status --short | grep -vc '^??')"; done
rm -rf ~/acer_sim
```

`muscle_mania`'s REPO_ROOT is hardcoded to `/home/akadir/dev/muscle_mania`, so `ingest_all.py --status` in the
throwaway home would read the real tree, not the copy — byte-equality of the data files is the receipt there.
What the first run of this test caught: a stale `studies_root_symlinked_ops.tar` from a July pack (the arrival
helper would have unpacked old folders over newer data) and mid-edit tracked files that made the first
`git pull` refuse. Both are fixed in the scripts; the test is what makes "the drive works" a claim with evidence.

### F2. When you arrive at a machine

```bash
git pull                          # muscle_mania, muscle_quality_exploration, muscle_model_training, brain
source scripts/env.sh && .venv/bin/python -c "import cv2, yaml; print('deps ok')"
# studies onto disk — COPY data/studies_root/ + data/study_registry.csv, names byte-exact (see below)
.venv/bin/python scripts/ingest_all.py --apply --only audit,freezelog,registry,substrate   # NO cohort
.venv/bin/python scripts/ingest_all.py --status    # must read COMPLETE + HANDOVER: CURRENT
.venv/bin/python scripts/check_parity.py           # must read MATCH ✓
```

**Do not re-run the `cohort` stage to reproduce a reference that already arrived by git.** It
re-segments every v8 study — over an hour, CPU-bound — to rebuild bytes you already have, and it
overwrites them. This is about *arrival*, not about permission: see F0 for who may freeze.

**Copy the studies; do not re-ingest them from Drive.** This is a deliberate departure from the
"Drive is canonical" rule above, which is written for *growing* data. Here you are reproducing a
population that has already been measured, and identity beats re-derivation: the cohort is keyed by
`study_code = "NR" + sha8(folder name)` — the SAME id as `study_registry.study_uid` (verified
2026-08-03: 93/93 cohort rows resolve to a registry row). Copy the folders and the join is exact.

**⚠ Preserve the folder names byte-exactly.** Rename anything — a re-zip, a Turkish-character
normalisation, a "cleanup" — and the sha changes, the uid changes, the committed cohort stops joining,
those studies land at `cohort=unknown`, the contract fails, and the obvious-looking fix is to re-freeze.
That would burn the whole GPU pass to rebuild a cohort you already have.

`gws` auth is per-machine and expires (see below); you only need it for `sync`/`check`, which the
copy path skips entirely.

**⚠ Seven of the eight contract items are disk-relative** — they compare registry/cohort/substrate
against *what is on this disk*. Nothing gates *datasheet rows vs studies on disk*, so a machine that is
internally consistent at a stale study count can pass items it should fail. Read this census line with
your eyes before trusting the PASS/FAIL block:

```
Drive zips  118    datasheet  118 rows    on disk  98 studies (93 on protocol v8)
```

If `on disk` has not caught up to the datasheet, the ingest did not take — whatever the contract says.

**A dead `gws` token degrades the census silently-ish.** It prints `note: sheet/Drive unreadable
(GwsError); local facts only` and the Drive invariant is then **omitted from the contract, not failed**
— deliberately, since an unknown must never score as a success, but it also means `COMPLETE` on that
run covers seven items rather than eight. The signature is a 401 `invalid_grant: Token has been expired
or revoked`. Re-auth is interactive (browser), so it cannot be done from an agent session. Seen on the
Acer 2026-08-02; expect it on any machine you return to after a gap.

### F3. The two receipts, and what each is blind to

- `ingest_all.py --status` — **is the data on THIS machine usable by analysis?** The only check that
  compares against the newest study on disk rather than a stored snapshot, so it is the one that stays
  honest as data grows. Its `HANDOVER` block reports whether the parity baseline still describes this
  machine (by content, not mtime — `git checkout` restamps mtime and would fake freshness).
- `check_parity.py` — **do two machines hold the same data?** Compares against the committed
  `scripts/parity_baseline.json`. **It is only as current as that file.** Between 2026-07-08 and 08-02
  the baseline sat 27 studies behind and `check_parity.py` reported `MATCH ✓` throughout, because a
  stale baseline and stale data agree with each other. Nothing in the repo calls `--save-baseline`; it
  is yours to run, at F1.

### F4. Machine-local things git cannot carry (set up once per machine)

The brain loaders are unversioned and machine-local — they do **not** arrive with `git pull`:

```bash
ln -sfn ~/projects/brain/.claude/skills/usta ~/.claude/skills/usta
printf '# User-level principles (all projects)\n\n@~/projects/brain/principles.md\n' > ~/.claude/CLAUDE.md
```

`~/projects` is a symlink to `~/nb` on the Acer — that bridge must exist on the other machine too, or
both lines dangle silently. Also per-machine: `gws` auth, the `.venv`, and everything in the class
table above marked "no".
