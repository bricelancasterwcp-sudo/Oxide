"""A ``ModelClient`` backed by llama.cpp's ``llama-server``.

Ollama cannot do this job. It accepts a ``grammar`` option and silently
ignores it, returning free text -- so a run that *looks* constrained is not,
and the whole point of constrained decoding (no OX0001/OX01xx, therefore
every remaining failure is semantic) would be lost with no visible symptom.
llama-server takes a top-level ``"grammar"`` string and enforces it.

Everything else mirrors :mod:`eval.models` deliberately: the same frozen
:class:`~eval.models.Generation`, the same sampling pins (section 48), the
same retry shape, and above all the same failure classification --
``ModelError`` means INFRASTRUCTURE and nothing else. A model that rambles,
truncates at ``num_predict``, or emits garbage is a *result*.

One asymmetry is forced by the backend and is the reason
:meth:`LlamaCppClient.preflight` exists: ``num_ctx`` is fixed when
llama-server starts (``-c``), not per request. It therefore cannot be
pinned in the request body the way Ollama's ``options.num_ctx`` is, and a
server started with a smaller window would truncate oversized prompts from
the FRONT -- dropping the language card from the oxide and explicit arms
only, exactly the silent non-random bias section 48 pins against. Preflight
refuses to run against such a server.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
from pathlib import Path
from typing import Callable

from eval.models import (
    DEFAULT_NUM_CTX,
    ContextOverflowError,
    Generation,
    ModelError,
    estimate_tokens,
)

# Reused, not reimplemented: identical transport means an identical
# exception surface, and therefore an identical ModelError boundary.
from eval.models import _request as _request

DEFAULT_HOST = "http://localhost:8081"

GRAMMAR_DIR = Path(__file__).resolve().parent / "grammar"
GRAMMAR_FILES = {"oxide": "oxide.gbnf", "explicit": "explicit.gbnf"}


def load_grammar(arm: str) -> str:
    """The GBNF source for one arm.

    The rust arm has none by design: rustc's own diagnostics are the
    control, and constraining Rust syntax would change what is being
    compared. Asking for it is a bug in the caller, not a run-time
    condition, so it raises ``ValueError`` rather than ``ModelError``.
    """
    if arm not in GRAMMAR_FILES:
        raise ValueError(
            f"no grammar for arm '{arm}' (have {sorted(GRAMMAR_FILES)})"
        )
    path = GRAMMAR_DIR / GRAMMAR_FILES[arm]
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelError(f"cannot read grammar '{path}': {exc}") from exc


def grammar_digest(grammar: str | None) -> str | None:
    """sha256 of the grammar, for the run manifest (section 49). A result
    produced under a grammar cannot be traced without knowing which one."""
    if grammar is None:
        return None
    return hashlib.sha256(grammar.encode("utf-8")).hexdigest()


class ServerContextOverflowError(ContextOverflowError):
    """The prompt PASSED the client's own pre-request estimate
    (``check_context``) but the server's real tokenizer rejected it
    anyway. ``estimate_tokens``'s chars-per-token heuristic is
    deliberately crude (section 48's own docstring) and under-counts a
    real prompt often enough that a repair prompt grown across several
    attempts can clear it and still overflow the server -- a
    cross-attempt sibling of truncation-at-``num_predict``.

    A distinct subclass of the base ``ContextOverflowError`` so ``_call``
    can raise it WITHOUT retrying (the failure is deterministic once the
    server has rejected it) and so logs/manifests can tell which check
    caught an overflow. ``eval.driver.run_session`` does NOT branch on
    this distinction, though: both this subclass and the base class are
    caught identically there and gated on whether the session already
    has submitted evidence (section 45/51), not on which one fired --
    the type axis matters for retry behavior and diagnostics, not for
    session-result-vs-abort.
    """


def _parse_http_error_body(exc: urllib.error.HTTPError) -> dict | None:
    """The server's own ``error`` object from a 400 (or other) response
    body, or ``None`` if it cannot be read/parsed or isn't this JSON
    shape. Reads the response body exactly once -- callers must not read
    it again.

    Reproduced against the running server on :8081 (SPEC section 45's
    harness host) to pin the actual shapes rather than guess them:

        oversized prompt -> HTTPError 400, body::

            {"error": {"code": 400, "message": "request (15430 tokens)
            exceeds the available context size (8192 tokens), try
            increasing it", "type": "exceed_context_size_error",
            "n_prompt_tokens": 15430, "n_ctx": 8192}}

        malformed request (missing "messages") -> HTTPError 400, body::

            {"error": {"code": 400, "message": "'messages' is required",
            "type": "invalid_request_error"}}

    ``error.type`` is the honest discriminator between the two.
    """
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except Exception:
        # The body is a live socket file, not a buffer: IncompleteRead,
        # socket.timeout, or any other OSError can fire here alongside
        # the JSON/decode failures, and an unreadable body is
        # definitionally not overflow-shaped either way. Caught broadly
        # so a read failure can never escape _call's except clause and
        # kill the whole grid instead of this one request (would
        # otherwise bypass the entire ModelError contract).
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else None


class LlamaCppClient:
    """One llama-server instance, optionally under a GBNF grammar."""

    def __init__(
        self,
        model: str = "local",
        *,
        grammar: str | None = None,
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
        self.grammar = grammar
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
        """Retry transient transport failures, then give up loudly.

        A context-overflow 400 is the one failure NOT retried: it is
        deterministic (the server would reject the identical prompt the
        same way three more times), so burning the retry budget on it
        would only delay a result that is already known. It is raised
        immediately as ``ServerContextOverflowError`` instead of
        ``ModelError`` -- see ``_parse_http_error_body`` and that class's
        docstring for why it is a DISTINCT subclass from the client-side
        ``ContextOverflowError`` raised by ``check_context``. ``HTTPError``
        must be caught ahead of the general ``URLError`` branch below: it
        IS a ``URLError`` subclass, so listing it second would let the
        general branch swallow it first.
        """
        last: Exception | str | None = None
        for attempt in range(self.retries):
            try:
                return _request(url, payload, self.timeout_s)
            except urllib.error.HTTPError as exc:
                error = _parse_http_error_body(exc)
                overflow = error is not None and (
                    error.get("type") == "exceed_context_size_error"
                )
                if overflow:
                    raise ServerContextOverflowError(
                        f"{self.model}: server rejected the prompt as "
                        f"exceeding its context window "
                        f"({error.get('message') or 'context size exceeded'}). "
                        f"estimate_tokens() under-counted this prompt "
                        f"against the server's real tokenizer."
                    ) from exc
                # Not overflow-shaped: retried like any other transport
                # failure, but surface the server's own message (if any)
                # in the eventual ModelError instead of discarding it.
                message = error.get("message") if error else None
                last = f"{exc} ({message})" if message else exc
                if attempt < self.retries - 1:
                    self._sleep(self.backoff_s * (2**attempt))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last = exc
                if attempt < self.retries - 1:
                    self._sleep(self.backoff_s * (2**attempt))
        raise ModelError(
            f"{self.model}: {self.retries} attempts failed against {url}: {last}"
        )

    def check_context(self, prompt: str) -> None:
        """Refuse a prompt that cannot fit ``num_ctx`` alongside its own
        reserved generation. Raised BEFORE the request: overflow is
        deterministic, so retrying it would only burn the backoff."""
        estimated = estimate_tokens(prompt)
        if estimated + self.num_predict <= self.num_ctx:
            return
        raise ContextOverflowError(
            f"{self.model}: prompt is ~{estimated} tok and num_predict is "
            f"{self.num_predict}, which together exceed num_ctx "
            f"{self.num_ctx}. llama.cpp would truncate the prompt from the "
            f"front, silently dropping the language card from the oxide "
            f"and explicit arms only. Refusing to generate."
        )

    def generate(self, prompt: str, *, seed: int) -> Generation:
        """One completion, grammar-constrained when a grammar is set.

        Truncation at ``num_predict`` is a RESULT: without the cap a
        degenerate repetition loop -- which grammar constraint makes *more*
        likely, not less, since every continuation stays legal -- would run
        to the HTTP timeout and be misread as infrastructure failure.
        """
        self.check_context(prompt)
        body: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": seed,
            "max_tokens": self.num_predict,
        }
        if self.grammar is not None:
            body["grammar"] = self.grammar
        return self._decode(self._call(f"{self.host}/v1/chat/completions", body))

    def _decode(self, body: dict) -> Generation:
        """Turn an OpenAI-shaped chat body into a Generation, or refuse it.

        Every lookup is isinstance-guarded for the reason section 51 gives:
        a 200 that is not a well-formed completion would otherwise become an
        empty Generation -- extracted, submitted, failed to compile, and
        written down as a genuine MODEL failure. That is infrastructure
        misclassified as model, in the direction that biases toward the null.
        """
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content", "") if isinstance(message, dict) else None
        if not isinstance(content, str) or body.get("error"):
            raise ModelError(f"{self.model}: malformed 200 response: {body!r}")
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        timings = body.get("timings")
        timings = timings if isinstance(timings, dict) else {}
        return Generation(
            text=content,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            ms=int(timings.get("prompt_ms", 0) + timings.get("predicted_ms", 0)),
            truncated=choice.get("finish_reason") == "length",
        )

    def preflight(self) -> dict:
        """Assert the server can honour the pinned window, and return the
        provenance section 49 requires in the run manifest.

        The served ``n_ctx`` is a launch flag, not a request field: if it is
        smaller than the pinned ``num_ctx`` then ``check_context`` -- which
        measures against the pin -- would wave through prompts the server
        then truncates from the front. Refusing here is the only place that
        mismatch can be caught before 1800 sessions are built on it.
        """
        props = self._call(f"{self.host}/props")
        settings = props.get("default_generation_settings")
        settings = settings if isinstance(settings, dict) else {}
        served = settings.get("n_ctx")
        if not isinstance(served, int) or served < self.num_ctx:
            raise ModelError(
                f"{self.model}: llama-server serves n_ctx={served!r} but this "
                f"run pins num_ctx={self.num_ctx}. Restart it with "
                f"`-c {self.num_ctx}`: a smaller window truncates oversized "
                f"prompts from the front, dropping the language card from the "
                f"oxide and explicit arms only."
            )
        return {
            "backend": "llama.cpp",
            "model": self.model,
            "model_path": props.get("model_path", ""),
            "build_info": props.get("build_info"),
            "server_n_ctx": served,
            "num_ctx": self.num_ctx,
            "grammar_sha256": grammar_digest(self.grammar),
        }

    def version(self) -> str | None:
        """The server's build identifier (section 48: 'version recorded')."""
        build = self._call(f"{self.host}/props").get("build_info")
        return build if isinstance(build, str) else None

    def healthy(self) -> bool:
        """True when the server answers. Never raises."""
        try:
            _request(f"{self.host}/health", None, self.timeout_s)
        except Exception:
            return False
        return True
