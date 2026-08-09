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

# The window we PIN, not the window the model could support. Ollama
# defaults num_ctx to 4096 regardless of the model's advertised
# context_length (32768 for qwen2.5-coder), and a repair prompt that
# carries the language card plus a failing program plus 2048 reserved
# generation tokens reaches that default. 8192 clears the largest
# observed prompt (~1660 tok) plus num_predict with ~2x headroom while
# keeping the KV cache small enough that 7B-q8 still fits the card.
DEFAULT_NUM_CTX = 8192

# Deliberately crude. A real tokenizer would be a dependency the eval
# venv does not have and cannot gain (PEP-668, no PyTorch on 3.14). The
# guard exists to catch a prompt that overruns the window by a wide
# margin, not to shave the last token.
CHARS_PER_TOKEN = 4


class ModelError(Exception):
    """Infrastructure failure: transport, timeout, a missing model, or a
    prompt that cannot fit the pinned context window."""


class ContextOverflowError(ModelError):
    """The prompt plus its reserved generation exceeds ``num_ctx`` --
    either caught BEFORE the request is ever sent (the client's own
    ``check_context`` estimate refusing a prompt it judges too large), or
    via the ``eval.llamacpp.ServerContextOverflowError`` subclass, when
    the server's real tokenizer rejects a prompt that PASSED that
    estimate.

    A ModelError subclass on purpose. Overflow is a *configuration*
    failure, never a model result: llama.cpp silently truncates an
    oversized prompt from the FRONT, which drops the language card --
    the only source of Oxide syntax -- from the oxide and explicit arms
    while the one-line rust preamble never overflows at all. That is a
    non-random bias against exactly the two arms whose comparison is
    section 47's primary metric, and it leaves no trace in the
    artifacts. Inheriting from ModelError routes it through
    ``_run_grid_cell``: the abort is scoped to one run id with the cause
    written into that run's manifest, and three in a row trip the
    grid-stop backstop rather than producing 1800 plausible-looking
    sessions built on truncated prompts.

    ``run_session`` gates on EVIDENCE, not on which of the two raised it
    (section 45/51): with at least one attempt already submitted this
    session, it is a RESULT -- attempts-so-far recorded, cell marked
    ``context_exhausted``, run continues. With zero attempts submitted,
    it still aborts the run id exactly as described above, because there
    is nothing to lose by aborting a session with no evidence -- and at a
    small per-family window (section 48) an oversized INITIAL prompt
    would otherwise repeat identically across every seed, fabricating a
    full grid of zero-attempt "results" with no abort and no manifest
    cause.
    """


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


def estimate_tokens(text: str) -> int:
    """Rounded-up ~4-chars-per-token estimate. See CHARS_PER_TOKEN."""
    return -(-len(text) // CHARS_PER_TOKEN)


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
        num_ctx: int = DEFAULT_NUM_CTX,
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
        self.num_ctx = num_ctx
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

    def check_context(self, prompt: str) -> None:
        """Refuse a prompt that cannot fit ``num_ctx`` alongside its own
        reserved generation. Raised BEFORE the request, so ``_call``'s
        retry loop never sees it -- an overflow is deterministic and
        retrying it three times would only waste the backoff."""
        estimated = estimate_tokens(prompt)
        if estimated + self.num_predict <= self.num_ctx:
            return
        raise ContextOverflowError(
            f"{self.model}: prompt is ~{estimated} tok "
            f"({len(prompt)} ch / {CHARS_PER_TOKEN}) and num_predict is "
            f"{self.num_predict}, which together exceed num_ctx "
            f"{self.num_ctx}. llama.cpp would truncate the prompt from the "
            f"front, silently dropping the language card from the oxide "
            f"and explicit arms only. Refusing to generate."
        )

    def generate(self, prompt: str, *, seed: int) -> Generation:
        """One completion. Truncation at num_predict is a RESULT, not an
        error: without the cap a degenerate repetition loop would run to
        the HTTP timeout and be misread as an infrastructure failure,
        aborting the run on the very behaviour we are measuring.

        Truncation of the *prompt* is the opposite case and is refused
        outright -- see ``check_context``."""
        self.check_context(prompt)
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
                    "num_ctx": self.num_ctx,
                },
            },
        )
        return self._decode(body)

    def _decode(self, body: dict) -> Generation:
        """Turn a chat body into a Generation, or refuse it.

        A 200 that is not a well-formed chat completion would otherwise
        become an empty Generation: extracted, submitted, failed to
        compile, and written as a genuine MODEL failure. That is
        infrastructure misclassified as model -- section 7's governing
        rule, in the direction that biases toward the null. The
        isinstance checks matter: ``{"message": null}`` and
        ``{"message": "text"}`` both pass a bare ``"message" in body``
        and then raise AttributeError, which is not a ModelError and so
        escapes ``_run_grid_cell`` and kills the whole grid instead of
        aborting one run id.
        """
        message = body.get("message")
        content = (
            message.get("content", "") if isinstance(message, dict) else None
        )
        if not isinstance(content, str) or body.get("error"):
            raise ModelError(
                f"{self.model}: malformed 200 response: {body!r}"
            )
        return Generation(
            text=content,
            tokens_in=int(body.get("prompt_eval_count", 0)),
            tokens_out=int(body.get("eval_count", 0)),
            ms=int(body.get("total_duration", 0) // 1_000_000),
            truncated=body.get("done_reason") == "length",
        )

    def preflight(self) -> dict:
        """Assert the model is pulled at the pinned quantization.

        Returns the provenance payload section 49 requires in the run
        manifest: which weights, at what precision, served by which
        daemon. Without it a 14-hour result cannot be traced back to the
        bytes that produced it.

        ``context_length`` here is the model's *capability* as advertised
        by /api/tags (32768 for qwen2.5-coder). It is NOT the window the
        run uses -- that is ``self.num_ctx``, and the two are recorded
        under distinct manifest keys precisely so they cannot be read as
        the same number.
        """
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
                "ollama_version": self.version(),
            }
        raise ModelError(f"{self.model} is not pulled: ollama pull {self.model}")

    def version(self) -> str | None:
        """The daemon's reported version (section 48: 'version recorded')."""
        return self._call(f"{self.host}/api/version").get("version")

    def healthy(self) -> bool:
        """True when the daemon answers. Never raises."""
        try:
            _request(f"{self.host}/api/tags", None, self.timeout_s)
        except Exception:
            return False
        return True
