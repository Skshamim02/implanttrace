#!/bin/sh
set -e

case "$1" in
  web)
    # Migrations run before the app starts; Alembic takes a lock, so this is safe to repeat.
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --workers "${WEB_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
    ;;
  watcher)
    exec python -m scripts.watch_inbox --inbox "${INBOX_DIR:-/srv/inbox}" --interval "${WATCH_INTERVAL:-10}"
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  seed)
    exec python -m scripts.seed
    ;;
  create-user)
    shift
    exec python -m scripts.create_user "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
