"""
Run audits in their own process:  python -m app.worker

Use this when the API runs with RUN_AUDIT_WORKERS=false, so the web server
only accepts audits and this process runs the browsers. Start as many as the
machine can hold; the database claim in app/tasks.py guarantees each audit
runs once. Stop with Ctrl-C: a job in progress is picked up again by the
next worker once its heartbeat goes stale.
"""
import logging
import signal
import threading
import time

from . import config
from .db import init_db
from .tasks import recover_jobs

logger = logging.getLogger("ulterior.worker")


def main(poll_seconds: float = 3.0) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except (AttributeError, ValueError):
        pass   # not available on every platform
    logger.info("Audit worker started: %s threads, heartbeat %ss, stale after %ss",
                config.AUDIT_WORKERS, config.JOB_HEARTBEAT_SECONDS, config.JOB_STALE_SECONDS)
    while not stop.is_set():
        try:
            recover_jobs()
        except Exception:
            logger.exception("Worker sweep failed")
        stop.wait(poll_seconds)
    logger.info("Audit worker stopping")
    time.sleep(0.2)


if __name__ == "__main__":
    main()
