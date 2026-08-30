from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from backend.app.adapters.base import EVENT_TYPES, EventSink
from backend.app.db import SessionLocal
from backend.app.models import ComparisonRun, RunEvent
from backend.app.security import redact_secrets


class EventHub:
    def __init__(self) -> None:
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._queues: defaultdict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    @asynccontextmanager
    async def subscribe(
        self,
        run_id: int,
        watermark_loader: Callable[[], Awaitable[int]],
    ) -> AsyncIterator[tuple[asyncio.Queue[dict[str, Any]], int]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        lock = self._locks[run_id]
        async with lock:
            self._queues[run_id].add(queue)
            watermark = await watermark_loader()
        try:
            yield queue, watermark
        finally:
            async with lock:
                self._queues[run_id].discard(queue)

    async def publish(self, run_id: int, event: dict[str, Any]) -> None:
        async with self._locks[run_id]:
            for queue in tuple(self._queues[run_id]):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._queues[run_id].discard(queue)


class EventWriter:
    def __init__(self, hub: EventHub) -> None:
        self.hub = hub

    async def emit(
        self,
        run_id: int,
        event_type: str,
        level: str,
        payload: dict[str, Any],
        *,
        message: str = "",
        model_run_id: int | None = None,
        case_run_id: int | None = None,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        safe_payload = redact_secrets(payload)
        safe_message = str(redact_secrets(message))
        created_at = datetime.now(UTC)
        async with SessionLocal.begin() as session:
            result = await session.execute(
                update(ComparisonRun)
                .where(ComparisonRun.id == run_id)
                .values(next_event_seq=ComparisonRun.next_event_seq + 1)
                .returning(ComparisonRun.next_event_seq)
            )
            seq = int(result.scalar_one())
            event = RunEvent(
                comparison_run_id=run_id,
                model_run_id=model_run_id,
                case_run_id=case_run_id,
                seq=seq,
                level=level,
                event_type=event_type,
                message=safe_message,
                payload_json=safe_payload,
                created_at=created_at,
            )
            session.add(event)
        public = {
            "seq": seq,
            "event_type": event_type,
            "level": level,
            "created_at": created_at.isoformat(),
            "model_run_id": model_run_id,
            "case_run_id": case_run_id,
            "message": safe_message,
            "payload": safe_payload,
        }
        await self.hub.publish(run_id, public)
        return public

    async def watermark(self, run_id: int) -> int:
        async with SessionLocal() as session:
            value = await session.scalar(
                select(ComparisonRun.next_event_seq).where(ComparisonRun.id == run_id)
            )
        if value is None:
            raise LookupError(f"Run not found: {run_id}")
        return int(value)


class BufferedEventSink:
    def __init__(self, sink: EventSink, interval: float = 0.25) -> None:
        self.sink = sink
        self.interval = interval
        self._chunks: list[str] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def __call__(self, event_type: str, level: str, payload: dict[str, Any]) -> None:
        if event_type != "provider.delta":
            await self.flush()
            await self.sink(event_type, level, payload)
            return
        async with self._lock:
            self._chunks.append(str(payload.get("text", "")))
            if self._flush_task is None:
                self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self.interval)
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            text = "".join(self._chunks)
            self._chunks.clear()
            current = asyncio.current_task()
            if self._flush_task is not None and self._flush_task is not current:
                self._flush_task.cancel()
            self._flush_task = None
        if text:
            await self.sink("provider.delta", "info", {"text": text})


event_hub = EventHub()
event_writer = EventWriter(event_hub)
