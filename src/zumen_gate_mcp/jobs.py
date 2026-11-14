"""Long conversions waited for locally.

A conversion runs on its own thread. If it has not finished after the wait (40 s), the tool
returns a job ID and the thread keeps the connection to the API open until the result arrives.
get_job only reads the state kept here; it never calls the API.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .queue import Lane, LaneBusy

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
# A job's thread waits this long for the lane (a conversion may run up to 300 s)
LANE_WAIT_SEC = 330.0


@dataclass
class Job:
    id: str
    label: str
    started: float = field(default_factory=time.monotonic)
    state: str = RUNNING
    result: Any = None
    error: BaseException | None = None
    finished: float | None = None
    progress: str = ""
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def elapsed(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return round(end - self.started, 1)

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout)


class JobRunner:
    def __init__(self, lane: Lane, wait_sec: float) -> None:
        self.lane = lane
        self.wait_sec = wait_sec
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, label: str, work: Callable[[Job], Any]) -> Job:
        """Run work(job) on a thread inside the lane; return once done or after the wait."""
        job = Job(id=f"job_{uuid.uuid4().hex[:10]}", label=label)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            try:
                job.progress = "waiting for the previous call"
                with self.lane.hold(label, LANE_WAIT_SEC):
                    job.progress = "sending"
                    job.result = work(job)
                job.state = SUCCEEDED
            except LaneBusy as exc:
                job.error = exc
                job.state = FAILED
            except BaseException as exc:  # noqa: BLE001 - kept for get_job
                job.error = exc
                job.state = FAILED
            finally:
                job.finished = time.monotonic()
                job._done.set()

        threading.Thread(target=run, name=job.id, daemon=True).start()
        job.wait(self.wait_sec)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def running(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state == RUNNING]
