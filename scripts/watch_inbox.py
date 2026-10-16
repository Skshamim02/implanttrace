"""Poll a folder for inspection CSV exports and import them.

Run: python -m scripts.watch_inbox [--once] [--interval 10]
In production this runs as its own service next to the CMM export share (see docker-compose.yml).
"""

import argparse
import logging
import signal
import time
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.logging_config import setup_logging
from app.services.inspection_import import process_inbox
from app.services.notifications import notify_hold

log = logging.getLogger("implanttrace.watcher")
_stop = False


def _request_stop(*_):
    global _stop
    _stop = True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", default=settings.inbox_dir)
    parser.add_argument("--interval", type=int, default=10, help="seconds between scans")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    args = parser.parse_args()

    setup_logging()
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    log.info("Watching %s every %ss", inbox.resolve(), args.interval)
    while not _stop:
        try:
            process_inbox(SessionLocal, inbox, on_hold=notify_hold)
        except Exception:
            log.exception("Scan failed; retrying next interval")
        if args.once:
            break
        for _ in range(args.interval):
            if _stop:
                break
            time.sleep(1)
    log.info("Watcher stopped")


if __name__ == "__main__":
    main()
