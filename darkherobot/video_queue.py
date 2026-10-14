"""First-come, first-served video queue: at most one video per interval.

If 5-6 videos arrive at once, the first is processed right away (when the
interval since the previous video has passed) and the rest wait their turn,
one every VIDEO_INTERVAL_SECONDS. The queue lives in memory, so pending
videos are dropped on restart.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("diddy.video_queue")


@dataclass
class VideoJob:
    file_id: str
    file_unique_id: str
    user_id: int
    chat_id: int
    payload: Any = None          # whatever the processor needs (message, question, ...)
    queued_at: float = field(default_factory=time.monotonic)


class QueueFull(Exception):
    pass


class AlreadyQueued(Exception):
    pass


class VideoQueue:
    def __init__(self, process: Callable[[VideoJob], Awaitable[None]], *, interval: float, max_pending: int,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._process = process
        self.interval = interval
        self.max_pending = max_pending
        self._clock = clock
        self._sleep = sleep
        self._pending: list[VideoJob] = []
        self._current: VideoJob | None = None
        self._last_start: float | None = None
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- public -----------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def submit(self, job: VideoJob) -> tuple[int, float]:
        """Queue a job. Returns (videos ahead of it, estimated seconds until it starts)."""
        if any(j.file_unique_id == job.file_unique_id and j.chat_id == job.chat_id
               for j in self._jobs()):
            raise AlreadyQueued
        if len(self._pending) >= self.max_pending:
            raise QueueFull
        ahead = len(self._pending) + (1 if self._current else 0)
        self._pending.append(job)
        self._wakeup.set()
        return ahead, self.eta_for(len(self._pending) - 1)

    def eta_for(self, index: int) -> float:
        """Seconds until pending[index] starts (approximate)."""
        now = self._clock()
        next_slot = 0.0 if self._last_start is None else max(0.0, self._last_start + self.interval - now)
        return next_slot + index * self.interval

    def pending_count(self) -> int:
        return len(self._pending)

    def busy(self) -> bool:
        return self._current is not None

    # ---- worker -----------------------------------------------------------
    def _jobs(self) -> list[VideoJob]:
        return ([self._current] if self._current else []) + self._pending

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            if self._last_start is not None:
                wait = self._last_start + self.interval - self._clock()
                if wait > 0:
                    await self._sleep(wait)
                    continue
            job = self._pending.pop(0)
            self._current = job
            self._last_start = self._clock()
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Video job failed chat=%s", job.chat_id)
            finally:
                self._current = None
