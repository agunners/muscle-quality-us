# Kontrol watcher — persisting the operator self-check

**What it does.** `scripts/check_studies.py --watch --apply` polls the `hasta_kayit`
datasheet every 45 s; for each row the operator has filled (`Study Adı` + `Operator`)
but not yet checked, it resolves the study's Drive zip, runs the staged completeness/accuracy
check, and writes a one-line verdict back to `Kontrol Sonucu` (+ detail in `Kontrol Detayı`).
So the operator gets the recording verdict within a poll of finishing the row. `deploy/check-studies.service`
keeps that loop running across reboots and crashes (`Restart=always`).

**Install (system-wide, `/etc/systemd/system`):**

```bash
sudo cp deploy/check-studies.service /etc/systemd/system/   # edit User= to the gws-owning user first
sudo systemctl daemon-reload
sudo systemctl enable --now check-studies
```

**Install (per-user, no root — `~/.config/systemd/user/`):** `User=` is ignored here.

```bash
mkdir -p ~/.config/systemd/user && cp deploy/check-studies.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now check-studies
```

**Logs:** `journalctl -u check-studies -f` (add `--user` for the per-user install).

**Caveat — credentials.** The watcher writes to the sheet through the same authenticated `gws`
CLI that `sync_records` uses (keyring-backed). The `gws` credentials must be reachable by the
user the unit runs as — so set `User=` to that account for a system install, or use the per-user
unit. A root/system unit with no creds fails auth every poll (visible in the journal).
