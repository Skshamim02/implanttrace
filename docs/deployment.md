# Deployment and operations

## Target setup (single plant)

```
 Tablets / PCs ──HTTPS──► Reverse proxy (TLS) ──► api (2+ workers) ──► PostgreSQL
                                                    ▲
 CMM PC ──CSV──► network share ──► watcher ─────────┘
```

A single Linux VM or Windows Server with Docker is enough for a plant of a few hundred people.

## First install

1. `cp .env.example .env` and set at least:
   - `ENVIRONMENT=production`
   - `SECRET_KEY` (48+ random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `COOKIE_SECURE=true` (the app refuses to start in production without it)
   - `POSTGRES_PASSWORD`
   - `SMTP_*` / `QUALITY_ALERT_EMAILS` and/or `TEAMS_WEBHOOK_URL` for hold alerts
2. Mount the CMM export share at `./inbox` (or change the `watcher` volume).
3. `docker compose up -d --build`. The `api` container runs database migrations on start.
4. Create the first administrator:
   `docker compose exec api ./docker-entrypoint.sh create-user admin "Plant Administrator" ADMIN`
5. Put a TLS reverse proxy in front of port 8000 (Caddy, nginx, or IIS ARR) and set
   `FORWARDED_ALLOW_IPS` to the proxy's address.

## Upgrades

```bash
git pull
docker compose build
docker compose up -d       # migrations run automatically before the new version serves traffic
```

Take a database backup first. Every schema change ships as an Alembic migration that has been
applied to a copy of production data before release (see validation.md, change control).

## Backups

```bash
docker compose exec db pg_dump -U implanttrace -Fc implanttrace > backup_$(date +%F).dump
```

Run nightly, keep off-site, and **test a restore** at least quarterly. Retention per customer
quality agreements (often device lifetime plus two years).

## Monitoring

| Signal | Where |
|---|---|
| Liveness | `GET /health` |
| Readiness (DB reachable) | `GET /ready`, also the Docker health check |
| Logs | stdout, JSON when `LOG_FORMAT=json`, every line carries `request_id`; ship to Loki/ELK/Azure Monitor |
| Import failures | watcher logs `Rejected <file>`; the file and an `.error.txt` report are moved to `inbox/failed/` |
| Alerts not sent | `ALERT (no channel configured)` or `Email alert failed` in logs |

## Support runbook

| Symptom | Check |
|---|---|
| "Account temporarily locked" | Wait 15 min, or an admin opens Users → Reactivate (clears the lock). |
| CMM file not appearing | Look in `inbox/failed/` for the file and its `.error.txt`; fix the export and drop it again. A file already imported is reported as a duplicate and ignored. |
| Operator can't finish Inspection | Results not imported yet, or an out-of-tolerance value needs a Quality disposition. |
| Order stuck ON HOLD | Quality → hold queue → record disposition. |
| Wrong data entered | Never edit the database directly. Corrections go through the app so they are audited; if a correction feature is missing, raise a change request. |
