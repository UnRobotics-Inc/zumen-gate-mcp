"""One API call at a time for this process.

The API runs one call per account at a time and answers a second one with 429. Lining the calls
up here keeps an AI that calls several tools at once from hitting that.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class LaneBusy(Exception):
    def __init__(self, holder: str | None) -> None:
        super().__init__(holder or "another call")
        self.holder = holder


class Lane:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.holder: str | None = None

    @contextmanager
    def hold(self, label: str, timeout: float) -> Iterator[None]:
        if not self._lock.acquire(timeout=max(0.0, timeout)):
            raise LaneBusy(self.holder)
        self.holder = label
        try:
            yield
        finally:
            self.holder = None
            self._lock.release()
