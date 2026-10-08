"""LLM access for agents: live (Anthropic Messages API), replay (cassette) and record.

CI and tests never call the network: they replay cassettes. Each cassette entry records where it came
from ("recorded" from a real model, or a clearly labelled "hand-written fixture"), and agents carry that
provenance into their artifacts, so a run report always says whether a model was really involved.

Errors map onto the engine's semantics:
  * rate limits, server errors, timeouts -> TransientError (the engine retries with backoff)
  * bad requests, missing cassette entries, exhausted budget -> AgentError (the stage falls back)
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .errors import AgentError, TransientError

API_HOST = "api.anthropic.com"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
FIXTURE = "hand-written fixture"


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    source: str        # "live", "recorded", or "hand-written fixture"


class LLMClient(Protocol):
    def complete(self, system: str, prompt: str) -> Completion: ...


def cassette_key(model: str, system: str, prompt: str) -> str:
    return hashlib.sha256(json.dumps([model, system, prompt]).encode()).hexdigest()


class AnthropicClient:
    """Minimal Messages API client on the standard library (no SDK dependency, no URL-scheme surprises)."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, *, max_tokens: int = 1024,
                 timeout: float = 60.0, max_calls: int = 10) -> None:
        if not api_key:
            raise ValueError("an API key is required for live calls")
        self._api_key = api_key
        self.model = model
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._calls_left = max_calls

    def _connection(self) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection(API_HOST, timeout=self._timeout)

    def complete(self, system: str, prompt: str) -> Completion:
        if self._calls_left <= 0:
            raise AgentError("LLM call budget for this run is exhausted")
        self._calls_left -= 1
        body = json.dumps({"model": self.model, "max_tokens": self._max_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]})
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        conn = self._connection()
        try:
            conn.request("POST", "/v1/messages", body=body, headers=headers)
            response = conn.getresponse()
            status, payload = response.status, response.read().decode("utf-8", errors="replace")
        except (TimeoutError, OSError) as exc:
            raise TransientError(f"LLM request failed: {exc}") from exc
        finally:
            conn.close()
        if status == 429 or status >= 500:
            raise TransientError(f"LLM API returned {status}")
        if status != 200:
            raise AgentError(f"LLM API returned {status}: {payload[:200]}")
        data = json.loads(payload)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        return Completion(text=text, model=str(data.get("model", self.model)), source="live")


class CassetteClient:
    """Replays recorded responses; with `record_with`, calls the live client on a miss and saves the answer."""

    def __init__(self, path: Path, model: str = DEFAULT_MODEL, *, record_with: LLMClient | None = None) -> None:
        self.path = Path(path)
        self.model = model
        self._live = record_with
        self._data: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}
        self._entries: dict[str, Any] = self._data.setdefault("entries", {})

    def complete(self, system: str, prompt: str) -> Completion:
        key = cassette_key(self.model, system, prompt)
        entry = self._entries.get(key)
        if entry is not None:
            return Completion(text=entry["response"], model=entry["model"], source=entry["source"])
        if self._live is None:
            raise AgentError(f"no recorded LLM response for this prompt in {self.path.name}")
        fresh = self._live.complete(system, prompt)
        self._entries[key] = {"model": fresh.model, "response": fresh.text, "source": "recorded",
                              "recorded_at": datetime.now(UTC).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")   # keeps the file's _note
        return Completion(text=fresh.text, model=fresh.model, source="recorded")


def client_from_env(cassette: Path, env: dict[str, str] | None = None) -> LLMClient:
    """Live with ANTHROPIC_API_KEY (recording into the cassette when SDLC_LLM_RECORD=1); replay otherwise."""
    env = dict(os.environ) if env is None else env
    model = env.get("SDLC_LLM_MODEL", DEFAULT_MODEL)
    key = env.get("ANTHROPIC_API_KEY", "")
    if key and env.get("SDLC_LLM_RECORD") == "1":
        return CassetteClient(cassette, model, record_with=AnthropicClient(key, model))
    if key:
        return AnthropicClient(key, model)
    return CassetteClient(cassette, model)
