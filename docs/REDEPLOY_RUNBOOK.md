# Redeploy Runbook

Use this for all code-only updates after the first deployment.

The current production host baseline is Ubuntu 22.04 with `python3 = 3.10`.
If a future release needs a newer interpreter, treat that as a deliberate
platform change and document it before redeploying.

## 1) Local Preflight

```bash
.venv/bin/pytest -q
.venv/bin/python -m labeling_app.cli doctor
.venv/bin/python -m labeling_app.cli smoke
```

## 2) Build a New Immutable Release

```bash
RELEASE_ID=$(date -u +%Y%m%dT%H%M%SZ)
./scripts/prepare_release.sh "$RELEASE_ID"
```

## 3) Upload the New Release

```bash
rsync -avh --delete dist/releases/$RELEASE_ID/ \
  root@203.0.113.10:/opt/muscle_quality_labeling/releases/$RELEASE_ID/
```

## 4) Build the Release Venv on Server

```bash
RELEASE_ID=<RELEASE_ID>
cd /opt/muscle_quality_labeling/releases/$RELEASE_ID
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 5) Back Up Runtime State

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /data/muscle_quality_labeling/backups/$TS
cp -a /data/muscle_quality_labeling/runtime/labeling.db \
  /data/muscle_quality_labeling/backups/$TS/ 2>/dev/null || true
cp -a /data/muscle_quality_labeling/exports/decision_log.csv \
  /data/muscle_quality_labeling/backups/$TS/ 2>/dev/null || true
```

## 6) Promote the Release

```bash
ln -sfn /opt/muscle_quality_labeling/releases/$RELEASE_ID /opt/muscle_quality_labeling/current
systemctl restart muscle-quality-labeler.service
```

## 7) Verify

```bash
systemctl status muscle-quality-labeler.service --no-pager -l | sed -n '1,40p'
curl -I http://172.18.0.1:8503 | head -n 1
curl -kI https://127.0.0.1 -H 'Host: muscle-quality.example.com' | sed -n '1,12p'
```

## 8) Rollback

If the new release fails:

```bash
ln -sfn /opt/muscle_quality_labeling/releases/<PREVIOUS_RELEASE_ID> /opt/muscle_quality_labeling/current
systemctl restart muscle-quality-labeler.service
```

Then re-run the same health checks.
