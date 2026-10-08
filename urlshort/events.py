"""Event-driven click analytics: transactional outbox -> relay -> event bus -> idempotent aggregator.

With `analytics_mode = "events"` a redirect writes its click as an event to the outbox table in the SAME
transaction that updates the click counter (counts and caps stay synchronous and exact). The relay publishes
pending events to the bus; the aggregator applies them to the clicks table, which the stats endpoint reads.

Delivery is at-least-once (a relay crash between publishing and marking re-publishes), so applying an event is
idempotent: `event_id` is unique in the clicks table and a duplicate inserts nothing. Malformed events go to the
dead-letter queue at once; events whose processing keeps failing are retried, then dead-lettered. Replaying the
whole stream is therefore always safe.

The bus is a port: Redis Streams in production (urlshort.adapters.redis_events), an in-memory bus in tests. A
Kafka adapter would implement the same four methods.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from .storage import Click, Repository

log = logging.getLogger("urlshort.events")
CLICK_EVENT = "click.v1"


class InvalidEvent(ValueError):
    """The message is not a well-formed event of a known type: it can never succeed, so it is dead-lettered."""


def new_event_id() -> str:
    return uuid.uuid4().hex


def encode_click(click: Click) -> str:
    return json.dumps({"type": CLICK_EVENT, "event_id": click.event_id, "code": click.code,
                       "ts": click.ts.astimezone(UTC).isoformat(), "referrer_host": click.referrer_host,
                       "agent_family": click.agent_family, "is_bot": click.is_bot, "ip_id": click.ip_id,
                       "ip_key_id": click.ip_key_id})


def decode_click(payload: str) -> Click:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidEvent(f"not JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or data.get("type") != CLICK_EVENT:
        raise InvalidEvent(f"unknown event type: {data.get('type') if isinstance(data, dict) else type(data).__name__}")
    try:
        ts = datetime.fromisoformat(data["ts"])
        if not (isinstance(data["event_id"], str) and data["event_id"] and isinstance(data["code"], str)
                and isinstance(data["is_bot"], bool) and isinstance(data["agent_family"], str)):
            raise InvalidEvent("missing or mistyped fields")
        return Click(code=data["code"], ts=ts, referrer_host=data["referrer_host"], agent_family=data["agent_family"],
                     is_bot=data["is_bot"], ip_id=data["ip_id"], ip_key_id=data["ip_key_id"],
                     event_id=data["event_id"])
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InvalidEvent):
            raise
        raise InvalidEvent(f"malformed click event: {exc}") from exc


@dataclass(frozen=True)
class Message:
    id: str               # bus-assigned position (stream entry ID / offset)
    payload: str
    deliveries: int       # how many times this message has been handed to a consumer


class EventBus(Protocol):
    def publish(self, payloads: list[str]) -> None: ...
    def read(self, consumer: str, count: int) -> list[Message]: ...
    def ack(self, ids: list[str]) -> None: ...
    def dead_letter(self, message: Message, reason: str) -> None: ...


@dataclass
class InMemoryBus:
    """A single-process bus with the same semantics as the Redis adapter: unacknowledged messages are
    delivered again (with a higher delivery count) until acknowledged."""
    entries: list[tuple[str, str]] = field(default_factory=list)
    pending: dict[str, int] = field(default_factory=dict)     # id -> deliveries
    dead: list[tuple[Message, str]] = field(default_factory=list)
    _next: int = 0

    def publish(self, payloads: list[str]) -> None:
        for payload in payloads:
            self.entries.append((f"{len(self.entries) + 1}-0", payload))

    def read(self, consumer: str, count: int) -> list[Message]:
        out: list[Message] = []
        for msg_id, payload in self.entries:
            if len(out) == count:
                break
            new = int(msg_id.split("-")[0]) > self._next
            if new or msg_id in self.pending:
                self.pending[msg_id] = self.pending.get(msg_id, 0) + 1
                self._next = max(self._next, int(msg_id.split("-")[0]))
                out.append(Message(msg_id, payload, self.pending[msg_id]))
        return out

    def ack(self, ids: list[str]) -> None:
        for msg_id in ids:
            self.pending.pop(msg_id, None)

    def dead_letter(self, message: Message, reason: str) -> None:
        self.dead.append((message, reason))

    def rewind(self) -> None:
        """Replay from the beginning (Redis: a new consumer group at ID 0)."""
        self._next = 0


@dataclass
class Relay:
    """Publishes outbox events to the bus, then marks them published (at-least-once)."""
    repo: Repository
    bus: EventBus
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    batch: int = 500

    def run_once(self) -> int:
        pending = self.repo.outbox_pending(self.batch)
        if not pending:
            return 0
        self.bus.publish([payload for _, payload in pending])
        self.repo.mark_published([row_id for row_id, _ in pending], self.clock())
        return len(pending)


@dataclass
class Aggregator:
    """Applies click events idempotently; dead-letters malformed events and ones that keep failing."""
    repo: Repository
    bus: EventBus
    consumer: str = "aggregator-1"
    batch: int = 500
    max_deliveries: int = 5
    stats: dict[str, int] = field(default_factory=lambda: {"applied": 0, "duplicates": 0, "dead": 0, "failed": 0})

    def run_once(self) -> int:
        messages = self.bus.read(self.consumer, self.batch)
        done: list[str] = []
        for message in messages:
            outcome = self._process(message)
            if outcome != "failed":
                done.append(message.id)
            self.stats[outcome] += 1
        if done:
            self.bus.ack(done)
        return len(messages)

    def _process(self, message: Message) -> str:
        try:
            click = decode_click(message.payload)
        except InvalidEvent as exc:
            self.bus.dead_letter(message, str(exc))
            log.warning("event dead-lettered", extra={"message_id": message.id, "reason": str(exc)})
            return "dead"
        try:
            return "applied" if self.repo.apply_click_event(click) else "duplicates"
        except Exception as exc:  # transient storage errors: leave unacknowledged so it is delivered again
            if message.deliveries >= self.max_deliveries:
                self.bus.dead_letter(message, f"failed {message.deliveries} times: {exc}")
                log.error("event dead-lettered after retries", extra={"message_id": message.id})
                return "dead"
            log.warning("event failed; will be redelivered", extra={"message_id": message.id}, exc_info=True)
            return "failed"


def drain(relay: Relay, aggregator: Aggregator, *, max_rounds: int = 100) -> dict[str, Any]:
    """Run relay and aggregator until both are idle (used by tests and the one-shot worker command)."""
    for _ in range(max_rounds):
        moved, consumed = relay.run_once(), aggregator.run_once()
        if not moved and not consumed:
            break
    return dict(aggregator.stats)
