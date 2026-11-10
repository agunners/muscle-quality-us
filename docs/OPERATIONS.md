# Operations

## Production Paths

- App releases: `/opt/muscle_quality_labeling/releases`
- Active symlink: `/opt/muscle_quality_labeling/current`
- Corpus: `/srv/muscle_quality_labeling/transport`
- Private map: `/data/muscle_quality_labeling/private/study_codes.csv`
- Runtime DB: `/data/muscle_quality_labeling/runtime/labeling.db`
- Reviewer-facing export: `/data/muscle_quality_labeling/exports/decision_log.csv`
- Backups: `/data/muscle_quality_labeling/backups`
- Server config: `/etc/muscle_quality_labeling/labeling.yaml`
- Service unit: `/etc/systemd/system/muscle-quality-labeler.service`

## Daily Checks

```bash
systemctl status muscle-quality-labeler.service --no-pager -l | sed -n '1,40p'
journalctl -u muscle-quality-labeler.service -n 120 --no-pager
curl -I http://172.18.0.1:8503 | head -n 1
curl -kI https://127.0.0.1 -H 'Host: muscle-quality.example.com' | head -n 1
curl -kI https://127.0.0.1 -H 'Host: annotations.example.com' | head -n 1
curl -kI https://127.0.0.1 -H 'Host: cvat.example.com' | head -n 1
```

## Weekly Checks

```bash
df -h / /data
du -sh /srv/muscle_quality_labeling/transport
find /opt/muscle_quality_labeling/releases -mindepth 1 -maxdepth 1 -type d | wc -l
find /data/muscle_quality_labeling/backups -mindepth 1 -maxdepth 1 -type d | wc -l
```

## Backup

### Runtime State

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /data/muscle_quality_labeling/backups/$TS
cp -a /data/muscle_quality_labeling/runtime/labeling.db \
  /data/muscle_quality_labeling/backups/$TS/ 2>/dev/null || true
cp -a /data/muscle_quality_labeling/exports/decision_log.csv \
  /data/muscle_quality_labeling/backups/$TS/ 2>/dev/null || true
cp -a /data/muscle_quality_labeling/private/study_codes.csv \
  /data/muscle_quality_labeling/backups/$TS/ 2>/dev/null || true
```

## Rollback Cookbook

1. Identify the previous release:

```bash
find /opt/muscle_quality_labeling/releases -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort
```

2. Re-point the active symlink:

```bash
ln -sfn /opt/muscle_quality_labeling/releases/<PREVIOUS_RELEASE_ID> /opt/muscle_quality_labeling/current
systemctl restart muscle-quality-labeler.service
```

3. Verify:

```bash
curl -I http://172.18.0.1:8503 | head -n 1
curl -kI https://127.0.0.1 -H 'Host: muscle-quality.example.com' | head -n 1
systemctl is-active muscle-quality-labeler.service
```
