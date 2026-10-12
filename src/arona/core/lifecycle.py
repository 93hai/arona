from __future__ import annotations

from enum import Enum
from threading import Lock
from typing import Callable


class Readiness(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class Lifecycle:
    """Idempotent lifecycle coordinator; dependency functions own their resources."""

    def __init__(self, start: Callable[[], None], stop: Callable[[], None], *, degraded_ok: bool = True) -> None:
        self._start = start
        self._stop = stop
        self._degraded_ok = degraded_ok
        self._state = Readiness.STARTING
        self._lock = Lock()
        self._operation = Lock()
        self._cleaned = False
        self._cleanup_failed = False

    @property
    def readiness(self) -> Readiness:
        with self._lock:
            return self._state

    @property
    def healthy(self) -> bool:
        return self.readiness in (Readiness.READY, Readiness.DEGRADED)

    def start(self, *, degraded: bool = False) -> None:
        with self._operation:
            self._start_once(degraded=degraded)

    def _start_once(self, *, degraded: bool) -> None:
        with self._lock:
            if self._state in (Readiness.READY, Readiness.DEGRADED):
                return
            if self._state in (Readiness.STOPPING, Readiness.STOPPED, Readiness.FAILED):
                raise RuntimeError("Lifecycle cannot be restarted")
        try:
            if degraded and not self._degraded_ok:
                raise RuntimeError("Degraded startup is not allowed")
            self._start()
        except BaseException as exc:
            try:
                self._cleanup()
            except BaseException:
                pass
            with self._lock:
                self._state = Readiness.FAILED
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError("Arona startup failed") from None
        with self._lock:
            if degraded and self._degraded_ok:
                self._state = Readiness.DEGRADED
            else:
                self._state = Readiness.READY

    def shutdown(self) -> None:
        with self._operation:
            self._shutdown_once()

    def _cleanup(self) -> None:
        if self._cleanup_failed:
            raise RuntimeError("Previous cleanup failed")
        if not self._cleaned:
            self._cleaned = True
            try:
                self._stop()
            except BaseException:
                self._cleanup_failed = True
                raise

    def _shutdown_once(self) -> None:
        with self._lock:
            if self._state == Readiness.STOPPED:
                return
            self._state = Readiness.STOPPING
        try:
            self._cleanup()
        except BaseException as exc:
            with self._lock:
                self._state = Readiness.FAILED
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError("Arona shutdown failed") from None
        else:
            with self._lock:
                self._state = Readiness.STOPPED
