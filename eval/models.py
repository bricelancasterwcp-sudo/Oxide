"""Model clients for the Phase 6a ladder (SPEC Part X, section 6.1).

Protocol-first so an API-backed client can drop in without touching the
driver. Stdlib only: the eval venv is Python 3.14, which has no clean
PyTorch build, and none is needed to talk to Ollama over HTTP.

ModelError means INFRASTRUCTURE failure and nothing else. A model that
rambles, truncates, or emits garbage is a *result*, not an error --
conflating the two in either direction corrupts the primary comparison
(section 7).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

DEFAULT_HOST = "http://localhost:11434"


class ModelError(Exception):
    """Infrastructure failure: transport, timeout, or a missing model."""


@dataclass(frozen=True)
class Generation:
    """One completed generation and its accounting."""

    text: str
    tokens_in: int
    tokens_out: int
    ms: int
    truncated: bool


class ModelClient(Protocol):
    def generate(self, prompt: str, *, seed: int) -> Generation: ...


def _request(
    url: str, payload: dict | None = None, timeout_s: int = 120
) -> dict:
    """POST json (or GET when payload is None) and decode the reply."""
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())


class OllamaClient:
    """One pinned model served by a local Ollama daemon."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.8,
        top_p: float = 0.95,
        num_predict: int = 2048,
        host: str = DEFAULT_HOST,
        timeout_s: int = 120,
        retries: int = 3,
        backoff_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.num_predict = num_predict
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep

    def _call(self, url: str, payload: dict | None = None) -> dict:
        """Retry transient transport failures, then give up loudly."""
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return _request(url, payload, self.timeout_s)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if attempt < self.retries - 1:
                    self._sleep(self.backoff_s * (2**attempt))
        raise ModelError(
            f"{self.model}: {self.retries} attempts failed against {url}: {last}"
        )

    def generate(self, prompt: str, *, seed: int) -> Generation:
        """One completion. Truncation at num_predict is a RESULT, not an
        error: without the cap a degenerate repetition loop would run to
        the HTTP timeout and be misread as an infrastructure failure,
        aborting the run on the very behaviour we are measuring."""
        body = self._call(
            f"{self.host}/api/chat",
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "top_p": self.top_p,
                    "seed": seed,
                    "num_predict": self.num_predict,
                },
            },
        )
        return Generation(
            text=body.get("message", {}).get("content", ""),
            tokens_in=int(body.get("prompt_eval_count", 0)),
            tokens_out=int(body.get("eval_count", 0)),
            ms=int(body.get("total_duration", 0) // 1_000_000),
            truncated=body.get("done_reason") == "length",
        )

    def preflight(self) -> dict:
        """Assert the model is pulled at the pinned quantization."""
        body = self._call(f"{self.host}/api/tags")
        for entry in body.get("models", []):
            if entry.get("name") != self.model:
                continue
            details = entry.get("details", {})
            quant = details.get("quantization_level", "?")
            if quant != "Q8_0":
                raise ModelError(
                    f"{self.model} is {quant}, expected Q8_0 -- uniform "
                    f"quantization is the control that keeps the capability "
                    f"curve from being confounded with precision"
                )
            return {
                "model": self.model,
                "digest": entry.get("digest", ""),
                "quantization_level": quant,
                "context_length": details.get("context_length"),
            }
        raise ModelError(f"{self.model} is not pulled: ollama pull {self.model}")

    def healthy(self) -> bool:
        """True when the daemon answers. Never raises."""
        try:
            _request(f"{self.host}/api/tags", None, self.timeout_s)
        except Exception:
            return False
        return True
