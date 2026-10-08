"""Redis Streams implementation of the EventBus port.

* one stream (`urlshort:events:clicks`), one consumer group; several aggregators can share it
* new messages: XREADGROUP; messages a crashed consumer never acknowledged: reclaimed with XAUTOCLAIM after
  `claim_idle_ms`, with their real delivery count (so retry limits work across consumers)
* dead letters: a separate stream with the reason, original ID and delivery count
* replay: XGROUP SETID to 0 (the aggregator is idempotent, so re-reading everything is safe)
* retention: the stream is trimmed approximately to `maxlen` entries; replay reaches back that far
A Kafka adapter would map these onto topics, consumer groups, offsets and a DLQ topic.
"""

from __future__ import annotations

from typing import Any

from redis import Redis
from redis.exceptions import ResponseError

from ..events import Message

STREAM = "urlshort:events:clicks"
GROUP = "aggregators"


class RedisStreamBus:
    def __init__(self, client: Redis, *, stream: str = STREAM, group: str = GROUP, claim_idle_ms: int = 30_000,
                 maxlen: int = 1_000_000) -> None:
        self._r, self.stream, self.group = client, stream, group
        self.dlq = stream + ":dlq"
        self._idle, self._maxlen = claim_idle_ms, maxlen
        try:
            self._r.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:            # the group already exists: fine, that is the normal case
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, payloads: list[str]) -> None:
        pipe = self._r.pipeline(transaction=False)
        for payload in payloads:
            pipe.xadd(self.stream, {"payload": payload}, maxlen=self._maxlen, approximate=True)
        pipe.execute()

    def read(self, consumer: str, count: int) -> list[Message]:
        claimed: Any = self._r.xautoclaim(self.stream, self.group, consumer, min_idle_time=self._idle,
                                          start_id="0-0", count=count)
        out = [self._message(msg_id, fields) for msg_id, fields in claimed[1] if fields]
        if len(out) < count:
            fresh: Any = self._r.xreadgroup(self.group, consumer, {self.stream: ">"}, count=count - len(out))
            out += [self._message(msg_id, fields) for _, entries in fresh for msg_id, fields in entries]
        return out

    def _message(self, msg_id: Any, fields: dict[Any, Any]) -> Message:
        info: Any = self._r.xpending_range(self.stream, self.group, min=msg_id, max=msg_id, count=1)
        deliveries = int(info[0]["times_delivered"]) if info else 1
        return Message(id=_text(msg_id), payload=_text(fields[b"payload"]), deliveries=deliveries)

    def ack(self, ids: list[str]) -> None:
        self._r.xack(self.stream, self.group, *ids)

    def dead_letter(self, message: Message, reason: str) -> None:
        self._r.xadd(self.dlq, {"payload": message.payload, "reason": reason, "original_id": message.id,
                                "deliveries": message.deliveries})

    def rewind(self) -> None:
        self._r.xgroup_setid(self.stream, self.group, id="0")

    def lag(self) -> int:
        """Messages not yet delivered to the group (Redis 7 reports this per group)."""
        groups: Any = self._r.xinfo_groups(self.stream)
        mine = [g for g in groups if _text(g["name"]) == self.group]
        return int(mine[0].get("lag") or 0) if mine else 0


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
