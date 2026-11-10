# Deployment Runbook

This is the first-deploy runbook for `muscle-quality.example.com`.

It follows the proven pattern already used by the sibling annotation review app:

- Streamlit runs on the host under `systemd`
- Traefik in the CVAT Docker stack terminates TLS and routes traffic
- production state lives outside the code release tree

## 1) Preconditions

- DNS `A` record for `muscle-quality.example.com` points to the Hetzner server IP
- CVAT and Traefik are already healthy on the host
- `annotation-review.service` remains healthy before any change
- the current Hetzner host baseline is Ubuntu 22.04 with `python3 = 3.10`; do not
  assume a newer interpreter is present unless you install and document it first
- local preflight succeeds:
  - `.venv/bin/pytest -q`
  - `.venv/bin/python -m labeling_app.cli doctor`
  - `.venv/bin/python -m labeling_app.cli smoke`

## 2) Server Layout

Create directories:

```bash
mkdir -p /opt/muscle_quality_labeling/releases
mkdir -p /srv/muscle_quality_labeling/transport
mkdir -p /data/muscle_quality_labeling/private
mkdir -p /data/muscle_quality_labeling/runtime
mkdir -p /data/muscle_quality_labeling/exports
mkdir -p /data/muscle_quality_labeling/backups
```

Verify capacity:

```bash
df -h / /data
```

## 3) Copy Corpus and Private Map

Run from the local machine:

```bash
rsync -avh --delete /home/akadir/dev/muscle_quality_labeling_exercise_claude_PROMPTED/transport/ \
  root@203.0.113.10:/srv/muscle_quality_labeling/transport/

scp /home/akadir/dev/muscle_quality_labeling_exercise_claude_PROMPTED/configs/study_codes.csv \
  root@203.0.113.10:/data/muscle_quality_labeling/private/study_codes.csv
```

## 4) Prepare Release

Use the release-prep helper locally:

```bash
RELEASE_ID=$(date -u +%Y%m%dT%H%M%SZ)
./scripts/prepare_release.sh "$RELEASE_ID"
```

This creates:

- `dist/releases/<RELEASE_ID>/`

It intentionally excludes:

- `transport/`
- `runtime/`
- `exports/`
- `runs/`
- `.venv/`
- temp and cache directories

## 5) Upload Code Release

```bash
RELEASE_ID=<RELEASE_ID>
rsync -avh --delete dist/releases/$RELEASE_ID/ \
  root@203.0.113.10:/opt/muscle_quality_labeling/releases/$RELEASE_ID/
```

## 6) Install Release Runtime on Server

```bash
RELEASE_ID=<RELEASE_ID>
cd /opt/muscle_quality_labeling/releases/$RELEASE_ID
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
ln -sfn /opt/muscle_quality_labeling/releases/$RELEASE_ID /opt/muscle_quality_labeling/current
```

## 7) Install Server Config and Service

Copy server config template:

```bash
install -d /etc/muscle_quality_labeling
cp /opt/muscle_quality_labeling/current/deploy/config/labeling.server.yaml \
  /etc/muscle_quality_labeling/labeling.yaml
```

The shipped server config pins `palette_path` to the stable symlink
`/opt/muscle_quality_labeling/current/label_palette.json` so release assets and
externalized data can live in different roots.

Install service unit:

```bash
cp /opt/muscle_quality_labeling/current/deploy/systemd/muscle-quality-labeler.service \
  /etc/systemd/system/muscle-quality-labeler.service
systemctl daemon-reload
systemctl enable --now muscle-quality-labeler.service
```

Verify backend:

```bash
systemctl status muscle-quality-labeler.service --no-pager -l | sed -n '1,40p'
ss -ltnp | grep 8503 || true
curl -I http://172.18.0.1:8503 | head -n 1
```

## 8) Add Traefik Route and Basic Auth

Back up the current mounted Traefik rules file:

```bash
cp -a /data/cvat/compose/components/analytics/grafana_conf.yml \
  /data/cvat/compose/components/analytics/grafana_conf.yml.bak.$(date +%F-%H%M%S)
```

Generate bcrypt entries with one user per human:

```bash
htpasswd -nbB <username> <password>
```

Add to `/data/cvat/compose/components/analytics/grafana_conf.yml`:

- middleware `muscle_quality_auth`
- router `muscle_quality_https`
- service `muscle_quality`

Reference shape:

```yaml
    muscle_quality_https:
      entryPoints:
      - websecure
      middlewares:
      - muscle_quality_auth
      service: muscle_quality
      rule: Host(`muscle-quality.example.com`)
      tls:
        certResolver: lets-encrypt

    muscle_quality_auth:
      basicAuth:
        users:
        - "user:$2y$..."

    muscle_quality:
      loadBalancer:
        servers:
        - url: http://172.18.0.1:8503
        passHostHeader: true
```

Verify Traefik sees the file:

```bash
docker exec traefik cat /etc/traefik/rules/grafana_conf.yml | sed -n '1,260p'
docker logs --tail 120 traefik | egrep -i 'error|warn|muscle_quality|letsencrypt' || true
```

## 9) Health Checks

```bash
curl -kI https://127.0.0.1 -H 'Host: muscle-quality.example.com' | sed -n '1,12p'
curl -kI https://127.0.0.1 -H 'Host: annotations.example.com' | sed -n '1,12p'
curl -kI https://127.0.0.1 -H 'Host: cvat.example.com' | sed -n '1,12p'
systemctl is-active muscle-quality-labeler.service
systemctl is-active annotation-review.service
```

Expected:

- new route responds over HTTPS
- existing routes remain healthy
- both services stay `active`
