from __future__ import annotations

import logging
import signal
from threading import Event

from arona.app import StartupError, create_application
from arona.config.settings import Settings


def run_service(settings: Settings) -> int:
    logger = logging.getLogger("arona.service")
    try:
        application = create_application(settings)
    except StartupError as exc:
        logger.error("Core startup failed before readiness: %s", exc)
        return 1
    stopped = Event()
    previous: dict[int, object] = {}

    def request_shutdown(signum, _frame) -> None:
        logger.info("Shutdown requested by signal %s", signum)
        stopped.set()

    try:
        for signum in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
            previous[signum] = signal.signal(signum, request_shutdown)
        logger.info("Arona CORE readiness: %s; EXTERNAL TRANSPORT READY: false", application.readiness.value)
        stopped.wait()
        return 0
    finally:
        try:
            application.shutdown()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
