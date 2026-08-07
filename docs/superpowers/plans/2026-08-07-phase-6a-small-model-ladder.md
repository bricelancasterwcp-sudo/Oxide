# Phase 6a — Small-Model Capability Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing three-arm Oxide eval against a ladder of local small models so the per-error-code distribution that gates v0.3 stops being empty.

**Architecture:** Five additive modules under `eval/` that sit *on top of* the frozen `eval/harness.py`. Each (model, shots, seed) combination becomes its own harness `run_id`, which is what lets the whole phase land without editing `harness.py` or `src/`. A driver drives generate → extract → submit → repair loops through Ollama over HTTP; a rollup aggregates 30 run directories into a paired-by-task comparison.

**Tech Stack:** Python 3.14 (stdlib only — `urllib.request`, `json`, `dataclasses`), pytest, Ollama HTTP API, rustc as the compile oracle.

## Global Constraints

- **Do not modify `eval/harness.py`, `src/`, `main.py`, `eval/tasks.jsonl`, `eval/shots/`, or either `LANGUAGE_CARD*.md`.** They are frozen. The phase is purely additive.
- The existing **717 tests must stay green** after every task.
- Test runner is `.venv/bin/pytest` — system pip is PEP-668 locked. Never `pip install` into the system Python.
- **Stdlib only.** No new third-party dependencies. No `requests`, no `torch` (Python 3.14 has no clean PyTorch build; a future fine-tuning phase gets its own 3.12 venv).
- rustc lives at `~/.cargo/bin/rustc` and is **not on PATH**. `eval/rustc_adapter.py` already handles this — never shell out to `rustc` directly.
- Type-annotate every function signature (project rule). `from __future__ import annotations` at the top of each new module.
- Files stay under 800 lines; functions under 50.
- Commit after every task with a conventional-commit message. **No Claude attribution in commit messages.**
- Binding contract is `docs/superpowers/specs/2026-08-07-phase-6a-small-model-ladder-design.md`. Where this plan and the spec disagree, the spec wins — stop and report the conflict.

### Pinned values (copy verbatim; never invent)

| Constant | Value |
|---|---|
| Models | `qwen2.5-coder:0.5b-instruct-q8_0`, `:1.5b-instruct-q8_0`, `:7b-instruct-q8_0` |
| Model slugs | `qwen0_5b`, `qwen1_5b`, `qwen7b` |
| Temperature / top_p / num_predict | `0.8` / `0.95` / `2048` |
| Seeds / shot conditions | `1,2,3,4,5` / `0,3` |
| Ollama host / HTTP timeout / retries | `http://localhost:11434` / `120`s / `3` |
| run_id format | `6a-<slug>-<shots>shot-s<seed>` e.g. `6a-qwen1_5b-0shot-s3` |
| Sessions per run_id | `60` (20 tasks × 3 arms) |
| Decision band | `±5pp` on the paired-by-task delta |
| Consecutive-abort backstop | `3` |
| Health-check wait cap | `600`s |

---

## File Structure

| File | Responsibility |
|---|---|
| `eval/extract.py` | Raw model text → candidate source. Pure, arm-neutral. |
| `eval/repair.py` | Repair-prompt construction. Pure. Never sees `expected_stdout`. |
| `eval/models.py` | `ModelClient` protocol, `Generation`, `OllamaClient` (retries, preflight, health). Sole owner of HTTP. |
| `eval/driver.py` | Session loop, run-id loop, grid loop, resume, abort policy, CLI. |
| `eval/rollup.py` | Paired-by-task aggregation, §3 partition, `REPORT.md`. |
| `tests/test_6a.py` | All tests for the above. |
| `SPEC.md` | Gains Part X (normative transcription). |

Order is dependency-driven: the two pure modules first, then the HTTP client, then the driver in two halves (one run, then the grid), then rollup, then the spec and the live run.

---

### Task 1: Output extraction

**Files:**
- Create: `eval/extract.py`
- Test: `tests/test_6a.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Extraction(source: str, contract_compliant: bool)` frozen dataclass; `extract(raw: str) -> Extraction`.

Spec §6.2. The rule is deliberately *not* syntax-aware — any cleverness risks favouring one arm's syntax, and the raw text is persisted anyway so a stricter number stays recoverable.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py
from eval.extract import Extraction, extract


def test_extract_returns_unfenced_text_verbatim():
    assert extract("fn main() {}\n").source == "fn main() {}"


def test_extract_unfenced_text_is_contract_compliant():
    assert extract("fn main() {}").contract_compliant is True


def test_extract_takes_content_of_first_fenced_block():
    raw = "Here you go:\n```\nfn main() {}\n```\nHope that helps!"
    assert extract(raw).source == "fn main() {}"


def test_extract_strips_language_tag_from_fence():
    raw = "```rust\nfn main() {}\n```"
    assert extract(raw).source == "fn main() {}"


def test_extract_fenced_output_is_not_contract_compliant():
    assert extract("```\nfn main() {}\n```").contract_compliant is False


def test_extract_takes_first_of_multiple_fenced_blocks():
    raw = "```\nfirst\n```\nand also\n```\nsecond\n```"
    assert extract(raw).source == "first"


def test_extract_salvages_unterminated_fence():
    # The characteristic shape of a generation cut off at num_predict.
    raw = "```rust\nfn main() {\n    let x = 1;"
    assert extract(raw).source == "fn main() {\n    let x = 1;"


def test_extract_normalizes_crlf():
    assert extract("a\r\nb\r\n").source == "a\nb"


def test_extract_handles_empty_and_whitespace_only():
    assert extract("").source == ""
    assert extract("   \n\n  ").source.strip() == ""


def test_extract_empty_output_is_trivially_compliant():
    # Documented consequence of the pinned formula (spec 6.2 step 5):
    # contract_compliant is a FORMATTING metric only. Empty submissions
    # still fail compilation as genuine model failures.
    assert extract("").contract_compliant is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.extract'`

- [ ] **Step 3: Write the implementation**

```python
# eval/extract.py
"""Model output -> candidate source (SPEC Part X, section 6.2).

Deliberately not syntax-aware: any smarter recovery risks differentially
favouring one arm's syntax, which would bias the primary comparison. Raw
output is persisted by the driver, so a strict-verbatim number stays
recoverable after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

FENCE = "```"


@dataclass(frozen=True)
class Extraction:
    """A candidate program plus whether the model obeyed the contract."""

    source: str
    contract_compliant: bool


def _first_fenced_block(lines: list[str]) -> str | None:
    """Content of the first ``` block, or None when there is no fence."""
    opener: int | None = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(FENCE):
            opener = index
            break
    if opener is None:
        return None
    for index in range(opener + 1, len(lines)):
        if lines[index].lstrip().startswith(FENCE):
            return "\n".join(lines[opener + 1 : index])
    # Unterminated fence: a generation cut off at num_predict. Salvaging
    # it is arm-neutral; the truncated source then fails to compile on
    # its own merits instead of being silently discarded.
    return "\n".join(lines[opener + 1 :])


def extract(raw: str) -> Extraction:
    """Apply the pinned, arm-identical extraction rule to model output."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    block = _first_fenced_block(text.split("\n"))
    source = text.strip("\n") if block is None else block
    return Extraction(
        source=source,
        contract_compliant=raw.strip() == source.strip(),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 10 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: 727 passed

- [ ] **Step 6: Commit**

```bash
git add eval/extract.py tests/test_6a.py
git commit -m "feat(eval): add arm-neutral model-output extraction"
```

---

### Task 2: Repair prompt

**Files:**
- Create: `eval/repair.py`
- Modify: `tests/test_6a.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `build_repair_prompt(arm: str, source: str, verdict: dict) -> str`. `verdict` is exactly what `harness.run_file` returns: `{"compiled": bool, "passed": bool, "stdout": str, "diagnostics": list[dict]}`. Diagnostic dicts carry `code`, `message`, `line`, `col`, `end_line`, `end_col`, `notes` (list of `{"line", "col"}`), `suggestion`.

Spec §6.3. This prompt did not previously exist — run 1 passed everything first try, so it was never written or exercised. At 0.5B it fires constantly, and it is where "do Oxide's diagnostics teach better than rustc's?" is actually decided.

**The signature deliberately excludes `expected_stdout`.** Leaking it would let a weak model pass by hard-coding a print of the expected string, silently corrupting the headline metric. Structural exclusion beats a filter.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py (append)
import inspect

import pytest

from eval.repair import build_repair_prompt

_BAD_DIAG = {
    "code": "OX0400",
    "message": "value moved here",
    "line": 4,
    "col": 15,
    "end_line": 4,
    "end_col": 16,
    "notes": [{"line": 3, "col": 18}],
    "suggestion": "Keep it available by cloning at the move site.",
}
_COMPILE_FAIL = {
    "compiled": False,
    "passed": False,
    "stdout": "",
    "diagnostics": [_BAD_DIAG],
}
_RUNTIME_FAIL = {
    "compiled": True,
    "passed": False,
    "stdout": "41\n",
    "diagnostics": [],
}


def test_repair_prompt_includes_program_and_diagnostics():
    out = build_repair_prompt("oxide", "let x = 1", _COMPILE_FAIL)
    assert "let x = 1" in out
    assert "4:15: OX0400: value moved here" in out


def test_repair_prompt_renders_notes_and_suggestion_indented():
    out = build_repair_prompt("oxide", "let x = 1", _COMPILE_FAIL)
    assert "  note: line 3, col 18" in out
    assert "  suggestion: Keep it available by cloning at the move site." in out


def test_repair_prompt_omits_empty_suggestion():
    diag = dict(_BAD_DIAG, suggestion="")
    verdict = dict(_COMPILE_FAIL, diagnostics=[diag])
    assert "suggestion:" not in build_repair_prompt("rust", "x", verdict)


def test_repair_prompt_ends_with_output_contract():
    out = build_repair_prompt("oxide", "let x = 1", _COMPILE_FAIL)
    assert out.rstrip().endswith(
        "Reply with ONLY the complete corrected program source, "
        "no fences, no commentary."
    )


def test_repair_prompt_runtime_failure_reports_own_output():
    out = build_repair_prompt("oxide", "print(41)", _RUNTIME_FAIL)
    assert "compiled and ran, but produced incorrect output" in out
    assert "41" in out


def test_repair_prompt_runtime_failure_has_no_diagnostics_block():
    assert "Diagnostics:" not in build_repair_prompt(
        "oxide", "print(41)", _RUNTIME_FAIL
    )


def test_repair_prompt_cannot_leak_expected_stdout():
    # Structural guarantee: expected_stdout is not a parameter, so there
    # is no path by which it could reach the model. A weak model that
    # learned the expected string could pass by hard-coding a print of
    # it, which would silently corrupt the headline metric.
    assert "expected" not in inspect.signature(build_repair_prompt).parameters
    out = build_repair_prompt("oxide", "print(41)", _RUNTIME_FAIL)
    assert "42" not in out  # the task's real expected output


def test_repair_prompt_structure_is_arm_identical():
    def skeleton(text: str) -> list[str]:
        return [ln for ln in text.split("\n") if ln.endswith(":") or not ln]

    shapes = {
        arm: skeleton(build_repair_prompt(arm, "src", _COMPILE_FAIL))
        for arm in ("oxide", "explicit", "rust")
    }
    assert shapes["oxide"] == shapes["explicit"] == shapes["rust"]


def test_repair_prompt_preserves_rustc_help_text_verbatim():
    # Section 45 folds rustc's help/children into `message`; giving each
    # arm its strongest native diagnostics is the fair form of the test.
    message = "borrow of moved value\nhelp: consider cloning the value"
    diag = dict(_BAD_DIAG, code="E0382", message=message, suggestion="")
    verdict = dict(_COMPILE_FAIL, diagnostics=[diag])
    assert message in build_repair_prompt("rust", "fn main(){}", verdict)


def test_repair_prompt_rejects_unknown_arm():
    with pytest.raises(ValueError):
        build_repair_prompt("python", "x", _COMPILE_FAIL)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k repair`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.repair'`

- [ ] **Step 3: Write the implementation**

```python
# eval/repair.py
"""Repair-prompt construction (SPEC Part X, section 6.3).

Arm-identical STRUCTURE, arm-native CONTENT: Oxide arms supply OX codes
with suggestions, the Rust arm supplies rustc's full help text verbatim.

`expected_stdout` is deliberately not a parameter. Disclosing it would
let a weak model pass by hard-coding a print of the expected string,
silently corrupting the headline metric.
"""

from __future__ import annotations

ARMS = ("oxide", "explicit", "rust")

FIX_INSTRUCTION = (
    "Reply with ONLY the complete corrected program source, "
    "no fences, no commentary."
)


def render_diagnostics(diagnostics: list[dict]) -> str:
    """One `line:col: CODE: message` per diagnostic, notes/suggestion
    indented two spaces beneath it."""
    lines: list[str] = []
    for diag in diagnostics:
        lines.append(
            f"{diag['line']}:{diag['col']}: {diag['code']}: {diag['message']}"
        )
        for note in diag.get("notes", []):
            lines.append(f"  note: line {note['line']}, col {note['col']}")
        if diag.get("suggestion"):
            lines.append(f"  suggestion: {diag['suggestion']}")
    return "\n".join(lines)


def build_repair_prompt(arm: str, source: str, verdict: dict) -> str:
    """The next-attempt prompt for a rejected program."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm '{arm}'")
    if verdict["compiled"]:
        # No diagnostics exist for a wrong-output run. Report only the
        # program's own observed output -- never the task's expected one.
        body = (
            "The program compiled and ran, but produced incorrect output.\n"
            "Its output was:\n" + verdict["stdout"]
        )
    else:
        body = "Diagnostics:\n" + render_diagnostics(verdict["diagnostics"])
    return (
        "The program below was rejected. Fix it.\n\n"
        f"Program:\n{source}\n\n"
        f"{body}\n\n"
        f"{FIX_INSTRUCTION}\n"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 20 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: 737 passed

- [ ] **Step 6: Commit**

```bash
git add eval/repair.py tests/test_6a.py
git commit -m "feat(eval): add arm-identical repair prompt"
```

---

### Task 3: Ollama model client

**Files:**
- Create: `eval/models.py`
- Modify: `tests/test_6a.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class ModelError(Exception)` — signals **infrastructure** failure only.
  - `Generation(text: str, tokens_in: int, tokens_out: int, ms: int, truncated: bool)` frozen dataclass.
  - `ModelClient` Protocol with `generate(self, prompt: str, *, seed: int) -> Generation`.
  - `OllamaClient(model, *, temperature=0.8, top_p=0.95, num_predict=2048, host="http://localhost:11434", timeout_s=120, retries=3, backoff_s=2.0, sleep=time.sleep)`, with `.generate(...)`, `.preflight() -> dict`, `.healthy() -> bool`.

The response shape below is **verified live** against Ollama 0.32.4 — `/api/chat` returns `message.content`, `done_reason`, `prompt_eval_count`, `eval_count`, `total_duration` (ns). `/api/tags` returns `models[].digest` and `models[].details.quantization_level`.

`sleep` is injectable purely so retry tests don't actually wait.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py (append)
import json
import urllib.error

from eval.models import Generation, ModelError, OllamaClient


def _chat_response(content: str = "ok", done_reason: str = "stop") -> bytes:
    return json.dumps(
        {
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": done_reason,
            "prompt_eval_count": 34,
            "eval_count": 12,
            "total_duration": 2_406_012_500,
        }
    ).encode()


class _FakeHTTP:
    """Scripted replacement for eval.models._post/_get."""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, url: str, payload: dict | None = None) -> dict:
        self.calls.append((url, payload))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(monkeypatch, http: _FakeHTTP) -> OllamaClient:
    monkeypatch.setattr("eval.models._request", http)
    return OllamaClient(
        "qwen2.5-coder:1.5b-instruct-q8_0", sleep=lambda _s: None
    )


def test_generate_returns_populated_generation(monkeypatch):
    http = _FakeHTTP(json.loads(_chat_response("hello")))
    gen = _client(monkeypatch, http).generate("hi", seed=3)
    assert gen == Generation(
        text="hello", tokens_in=34, tokens_out=12, ms=2406, truncated=False
    )


def test_generate_sends_pinned_sampling_options(monkeypatch):
    http = _FakeHTTP(json.loads(_chat_response()))
    _client(monkeypatch, http).generate("hi", seed=4)
    options = http.calls[0][1]["options"]
    assert options == {
        "temperature": 0.8,
        "top_p": 0.95,
        "seed": 4,
        "num_predict": 2048,
    }


def test_generate_marks_length_stop_as_truncated(monkeypatch):
    http = _FakeHTTP(json.loads(_chat_response(done_reason="length")))
    assert _client(monkeypatch, http).generate("hi", seed=1).truncated is True


def test_generate_retries_then_succeeds(monkeypatch):
    http = _FakeHTTP(
        urllib.error.URLError("connection refused"),
        json.loads(_chat_response("recovered")),
    )
    gen = _client(monkeypatch, http).generate("hi", seed=1)
    assert gen.text == "recovered"
    assert len(http.calls) == 2


def test_generate_raises_model_error_after_exhausting_retries(monkeypatch):
    http = _FakeHTTP(*[urllib.error.URLError("down")] * 3)
    with pytest.raises(ModelError):
        _client(monkeypatch, http).generate("hi", seed=1)
    assert len(http.calls) == 3


def test_preflight_returns_digest_and_quantization(monkeypatch):
    tags = {
        "models": [
            {
                "name": "qwen2.5-coder:1.5b-instruct-q8_0",
                "digest": "abc123def456",
                "details": {
                    "quantization_level": "Q8_0",
                    "context_length": 32768,
                },
            }
        ]
    }
    info = _client(monkeypatch, _FakeHTTP(tags)).preflight()
    assert info["digest"] == "abc123def456"
    assert info["quantization_level"] == "Q8_0"


def test_preflight_rejects_missing_model(monkeypatch):
    tags = {"models": [{"name": "other:latest", "digest": "x", "details": {}}]}
    with pytest.raises(ModelError, match="not pulled"):
        _client(monkeypatch, _FakeHTTP(tags)).preflight()


def test_preflight_rejects_wrong_quantization(monkeypatch):
    # This is what actually enforces the uniform-quantization control:
    # the 1.5b already on this machine is Q4_K_M and must be rejected.
    tags = {
        "models": [
            {
                "name": "qwen2.5-coder:1.5b-instruct-q8_0",
                "digest": "abc",
                "details": {"quantization_level": "Q4_K_M"},
            }
        ]
    }
    with pytest.raises(ModelError, match="Q4_K_M"):
        _client(monkeypatch, _FakeHTTP(tags)).preflight()


def test_healthy_is_false_when_unreachable(monkeypatch):
    http = _FakeHTTP(urllib.error.URLError("down"))
    assert _client(monkeypatch, http).healthy() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k "generate or preflight or healthy"`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.models'`

- [ ] **Step 3: Write the implementation**

```python
# eval/models.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 29 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: 746 passed

- [ ] **Step 6: Commit**

```bash
git add eval/models.py tests/test_6a.py
git commit -m "feat(eval): add Ollama model client with quantization preflight"
```

---

### Task 4: Driver — one session, one run id

**Files:**
- Create: `eval/driver.py`
- Modify: `tests/test_6a.py` (append)

**Interfaces:**
- Consumes: `extract.extract`, `repair.build_repair_prompt`, `models.ModelClient`/`ModelError`, and from `eval.harness`: `build_prompt`, `new_session`, `load_tasks`, `ARMS`, `MAX_ATTEMPTS`, `HarnessError`.
- Produces:
  - `run_session(client, run_id, task_id, arm, shots, *, results_root, raw_dir) -> dict` — one cell record.
  - `run_one(client, run_id, shots, *, results_root, tasks_path=None) -> None` — 60 sessions.

Spec §5, §6.4. `harness.Session.submit` already appends the triple and enforces the 4-attempt cap; this task only orchestrates around it.

**Do not catch `ModelError` here.** It must propagate to the grid loop in Task 5, which is what scopes an abort to one run id and guarantees infrastructure failures never land in `cells.jsonl`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py (append)
from pathlib import Path

from eval.driver import run_one, run_session


class _StubClient:
    """Returns scripted texts; records the prompts it was given."""

    def __init__(self, *texts: str, truncated: bool = False) -> None:
        self.texts = list(texts)
        self.prompts: list[str] = []
        self._truncated = truncated

    def generate(self, prompt: str, *, seed: int) -> Generation:
        self.prompts.append(prompt)
        text = self.texts.pop(0) if self.texts else self.texts_default()
        return Generation(text, 10, 5, 100, self._truncated)

    def texts_default(self) -> str:
        return "not a program"


_GOOD_OXIDE = 'fn main() {\n    print("hi")\n}\n'


def test_run_session_records_a_pass_on_first_attempt(tmp_path):
    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    cell = run_session(
        _StubClient(_GOOD_OXIDE),
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=tmp_path / "raw",
        tasks_path=tasks,
    )
    assert cell["first_passed"] is True
    assert cell["final_passed"] is True
    assert cell["attempts"] == 1
    assert cell["truncated"] == [False]


def test_run_session_repairs_after_a_failure(tmp_path):
    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    client = _StubClient("this is not a program", _GOOD_OXIDE)
    cell = run_session(
        client,
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=tmp_path / "raw",
        tasks_path=tasks,
    )
    assert cell["first_passed"] is False
    assert cell["final_passed"] is True
    assert cell["attempts_to_pass"] == 2
    # The second prompt must be a repair prompt, not the original.
    assert "The program below was rejected" in client.prompts[1]


def test_run_session_stops_at_the_attempt_cap(tmp_path):
    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    cell = run_session(
        _StubClient(),  # always returns garbage
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=tmp_path / "raw",
        tasks_path=tasks,
    )
    assert cell["attempts"] == 4
    assert cell["final_passed"] is False
    assert cell["attempts_to_pass"] == 5  # cap + 1


def test_run_session_persists_raw_output_per_attempt(tmp_path):
    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    run_session(
        _StubClient("garbage", _GOOD_OXIDE),
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=raw_dir,
        tasks_path=tasks,
    )
    assert (raw_dir / "tX.oxide.1.txt").read_text() == "garbage"
    assert (raw_dir / "tX.oxide.2.txt").read_text() == _GOOD_OXIDE


def test_run_session_records_truncation_as_a_model_failure(tmp_path):
    # Section 7's governing rule, direction one: a generation cut off at
    # num_predict is a MODEL result. It must be submitted and counted,
    # never raised as infrastructure.
    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    cell = run_session(
        _StubClient("fn main() { print(", truncated=True),
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=tmp_path / "raw",
        tasks_path=tasks,
    )
    assert cell["truncated"][0] is True
    assert cell["final_passed"] is False


def test_run_session_lets_model_error_propagate(tmp_path):
    # Section 7's governing rule, direction two: infrastructure failure
    # must NOT be written to cells.jsonl as a failed attempt. It escapes
    # to the grid loop, which scopes the abort to one run id.
    class _Broken:
        def generate(self, prompt: str, *, seed: int) -> Generation:
            raise ModelError("ollama down")

    task = {"id": "tX", "prompt": "Print hi.", "expected_stdout": "hi\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    with pytest.raises(ModelError):
        run_session(
            _Broken(),
            run_id="6a-test-0shot-s1",
            task_id="tX",
            arm="oxide",
            shots=0,
            results_root=tmp_path / "results",
            raw_dir=tmp_path / "raw",
            tasks_path=tasks,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k run_session`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.driver'`

- [ ] **Step 3: Write the implementation**

```python
# eval/driver.py
"""Phase 6a run driver (SPEC Part X, sections 5 and 6.4).

Each (model, shots, seed) combination is its own harness run_id. That is
what makes this phase additive: harness._claim_session locks on
(run_id, task, arm) and the pinned triple schema carries no model or seed
field, so sharing a run_id across the grid would silently conflate cells.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval import harness
from eval.extract import extract
from eval.models import ModelClient
from eval.repair import build_repair_prompt


def run_session(
    client: ModelClient,
    *,
    run_id: str,
    task_id: str,
    arm: str,
    shots: int,
    results_root: Path,
    raw_dir: Path,
    tasks_path: Path | None = None,
    seed: int = 1,
) -> dict:
    """Drive one task/arm to a verdict or the attempt cap.

    ModelError is deliberately NOT caught: infrastructure failures must
    never be recorded as model failures (section 7).
    """
    session = harness.new_session(
        task_id,
        arm,
        run_id,
        tasks_path=tasks_path,
        results_root=results_root,
    )
    prompt = harness.build_prompt(arm, task_id, shots=shots, tasks_path=tasks_path)
    raw_dir.mkdir(parents=True, exist_ok=True)

    compliant: list[bool] = []
    truncated: list[bool] = []
    tokens_in = tokens_out = elapsed_ms = 0
    first: dict | None = None
    verdict: dict = {}
    attempts_to_pass = harness.MAX_ATTEMPTS + 1

    for attempt in range(1, harness.MAX_ATTEMPTS + 1):
        generation = client.generate(prompt, seed=seed)
        (raw_dir / f"{task_id}.{arm}.{attempt}.txt").write_text(
            generation.text, encoding="utf-8"
        )
        tokens_in += generation.tokens_in
        tokens_out += generation.tokens_out
        elapsed_ms += generation.ms
        truncated.append(generation.truncated)

        candidate = extract(generation.text)
        compliant.append(candidate.contract_compliant)
        verdict = session.submit(candidate.source)
        if first is None:
            first = verdict
        if verdict["passed"]:
            attempts_to_pass = attempt
            break
        prompt = build_repair_prompt(arm, candidate.source, verdict)

    assert first is not None
    return {
        "task": task_id,
        "arm": arm,
        "attempts": session.attempts,
        "first_compiled": bool(first["compiled"]),
        "first_passed": bool(first["passed"]),
        "final_passed": bool(verdict["passed"]),
        "attempts_to_pass": attempts_to_pass,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "ms": elapsed_ms,
        "contract_compliant": compliant,
        "truncated": truncated,
    }


def run_one(
    client: ModelClient,
    *,
    run_id: str,
    shots: int,
    seed: int,
    results_root: Path,
    tasks_path: Path | None = None,
) -> None:
    """All 60 sessions (20 tasks x 3 arms) for one run id."""
    run_dir = Path(results_root) / run_id
    cells_path = run_dir / "cells.jsonl"
    raw_dir = run_dir / "raw"
    tasks = harness.load_tasks(tasks_path)
    for task_id in sorted(tasks):
        for arm in harness.ARMS:
            cell = run_session(
                client,
                run_id=run_id,
                task_id=task_id,
                arm=arm,
                shots=shots,
                results_root=results_root,
                raw_dir=raw_dir,
                tasks_path=tasks_path,
                seed=seed,
            )
            cells_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cells_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(cell, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 35 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: 752 passed

- [ ] **Step 6: Commit**

```bash
git add eval/driver.py tests/test_6a.py
git commit -m "feat(eval): add 6a session driver with repair loop"
```

---

### Task 5: Driver — grid orchestration, resume, abort policy

**Files:**
- Modify: `eval/driver.py`
- Modify: `tests/test_6a.py` (append)

**Interfaces:**
- Consumes: `run_one` from Task 4; `OllamaClient.healthy/preflight` from Task 3.
- Produces:
  - `MODELS: dict[str, str]` mapping slug → pinned tag.
  - `build_run_id(slug: str, shots: int, seed: int) -> str`.
  - `is_complete(run_dir: Path) -> bool`.
  - `reset_run(run_dir: Path) -> None`.
  - `run_grid(make_client, *, slugs, shot_counts, seeds, results_root, wait_for_health=None, tasks_path=None) -> dict` returning `{"completed": [...], "aborted": [...]}`.
  - `main(argv: list[str] | None = None) -> int` — the CLI.

Spec §6.4, §7. This is where the abort policy lives.

**Resume granularity is the whole run id.** A run dir short of 60 cells is deleted and redone. Partial-state surgery across O_EXCL locks and half-written triples is more bug-prone than the ~30-minute rerun costs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py (append)
from eval.driver import (
    MODELS,
    build_run_id,
    is_complete,
    reset_run,
    run_grid,
)


def test_model_slugs_map_to_pinned_q8_tags():
    assert MODELS == {
        "qwen0_5b": "qwen2.5-coder:0.5b-instruct-q8_0",
        "qwen1_5b": "qwen2.5-coder:1.5b-instruct-q8_0",
        "qwen7b": "qwen2.5-coder:7b-instruct-q8_0",
    }


def test_build_run_id_matches_pinned_format():
    assert build_run_id("qwen1_5b", 0, 3) == "6a-qwen1_5b-0shot-s3"


def test_is_complete_requires_sixty_cells(tmp_path):
    run_dir = tmp_path / "6a-qwen1_5b-0shot-s1"
    run_dir.mkdir()
    (run_dir / "cells.jsonl").write_text(
        "".join('{"task":"t"}\n' for _ in range(59)), encoding="utf-8"
    )
    assert is_complete(run_dir) is False
    (run_dir / "cells.jsonl").write_text(
        "".join('{"task":"t"}\n' for _ in range(60)), encoding="utf-8"
    )
    assert is_complete(run_dir) is True


def test_reset_run_removes_locks_and_partial_cells(tmp_path):
    run_dir = tmp_path / "6a-qwen1_5b-0shot-s1"
    (run_dir / ".sessions").mkdir(parents=True)
    (run_dir / ".sessions" / "t01.oxide.lock").touch()
    (run_dir / "cells.jsonl").write_text("{}\n", encoding="utf-8")
    reset_run(run_dir)
    assert not run_dir.exists()


def test_run_grid_skips_completed_runs(tmp_path):
    done = tmp_path / build_run_id("qwen1_5b", 0, 1)
    done.mkdir(parents=True)
    (done / "cells.jsonl").write_text(
        "".join('{"task":"t"}\n' for _ in range(60)), encoding="utf-8"
    )
    calls: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        calls.append(run_id)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    result = run_grid(
        lambda tag: _StubClient(),
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
    )
    monkeypatch.undo()
    assert calls == [build_run_id("qwen1_5b", 0, 2)]
    assert result["completed"] == [build_run_id("qwen1_5b", 0, 2)]


def test_run_grid_aborts_one_run_and_continues(tmp_path):
    seen: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        seen.append(run_id)
        if run_id.endswith("s1"):
            raise ModelError("transport down")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    result = run_grid(
        lambda tag: _StubClient(),
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
    )
    monkeypatch.undo()
    assert len(seen) == 2  # did not stop at the failure
    assert result["aborted"] == [build_run_id("qwen1_5b", 0, 1)]
    assert result["completed"] == [build_run_id("qwen1_5b", 0, 2)]


def test_run_grid_stops_after_three_consecutive_aborts(tmp_path):
    # Without this backstop a systematically broken configuration burns
    # silently through every remaining run id and leaves a grid that
    # looks complete but is not.
    def fake_run_one(client, *, run_id, **kwargs):
        raise ModelError("7b will not load")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    with pytest.raises(RuntimeError, match="consecutive"):
        run_grid(
            lambda tag: _StubClient(),
            slugs=["qwen7b"],
            shot_counts=[0],
            seeds=[1, 2, 3, 4, 5],
            results_root=tmp_path,
        )
    monkeypatch.undo()


def test_run_grid_waits_for_health_between_runs(tmp_path):
    waits: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    run_grid(
        lambda tag: _StubClient(),
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
        health_check=lambda client: waits.append("checked"),
    )
    monkeypatch.undo()
    assert waits == ["checked", "checked"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k "grid or run_id or is_complete or reset_run or slugs"`
Expected: FAIL — `ImportError: cannot import name 'MODELS' from 'eval.driver'`

- [ ] **Step 3: Add the grid layer to `eval/driver.py`**

Append to the existing module (keep `run_session` and `run_one` unchanged):

```python
import argparse
import shutil
import sys
import time

MODELS = {
    "qwen0_5b": "qwen2.5-coder:0.5b-instruct-q8_0",
    "qwen1_5b": "qwen2.5-coder:1.5b-instruct-q8_0",
    "qwen7b": "qwen2.5-coder:7b-instruct-q8_0",
}
SEEDS = (1, 2, 3, 4, 5)
SHOT_COUNTS = (0, 3)
SESSIONS_PER_RUN = 60
MAX_CONSECUTIVE_ABORTS = 3
HEALTH_WAIT_CAP_S = 600


def build_run_id(slug: str, shots: int, seed: int) -> str:
    return f"6a-{slug}-{shots}shot-s{seed}"


def is_complete(run_dir: Path) -> bool:
    """A run is complete only with all 60 cell records on disk."""
    cells = Path(run_dir) / "cells.jsonl"
    if not cells.exists():
        return False
    with open(cells, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip()) >= SESSIONS_PER_RUN


def reset_run(run_dir: Path) -> None:
    """Drop an incomplete run wholesale (section 6.4)."""
    shutil.rmtree(run_dir, ignore_errors=True)


def wait_for_health(client: object, *, cap_s: int = HEALTH_WAIT_CAP_S,
                    sleep: object = time.sleep) -> None:
    """Poll between run ids so a daemon restart costs no work. Applied
    BETWEEN runs only -- mid-session waiting would collide with the
    O_EXCL locks and half-written triples section 6.4 avoids."""
    deadline = cap_s
    while deadline > 0:
        if client.healthy():
            return
        sleep(5)
        deadline -= 5
    raise ModelError(f"ollama did not become healthy within {cap_s}s")


def run_grid(
    make_client,
    *,
    slugs: list[str],
    shot_counts: list[int],
    seeds: list[int],
    results_root: Path,
    health_check: Callable[[object], None] | None = None,
    tasks_path: Path | None = None,
) -> dict:
    """Walk the grid, one run id at a time."""
    completed: list[str] = []
    aborted: list[str] = []
    consecutive = 0
    for slug in slugs:
        client = make_client(MODELS[slug])
        for shots in shot_counts:
            for seed in seeds:
                run_id = build_run_id(slug, shots, seed)
                run_dir = Path(results_root) / run_id
                if is_complete(run_dir):
                    continue
                reset_run(run_dir)
                if health_check is not None:
                    health_check(client)
                # Section 6.4 order: manifest BEFORE the sessions, so an
                # interrupted run still records what it was running.
                _write_manifest(run_dir, run_id, slug, shots, seed)
                try:
                    run_one(
                        client,
                        run_id=run_id,
                        shots=shots,
                        seed=seed,
                        results_root=results_root,
                        tasks_path=tasks_path,
                    )
                except ModelError as exc:
                    aborted.append(run_id)
                    consecutive += 1
                    _write_manifest(run_dir, run_id, slug, shots, seed,
                                    aborted_reason=str(exc))
                    if consecutive >= MAX_CONSECUTIVE_ABORTS:
                        raise RuntimeError(
                            f"{consecutive} consecutive run aborts "
                            f"(last: {run_id}: {exc}) -- stopping the grid "
                            f"rather than leaving a partial grid that reads "
                            f"as complete"
                        ) from exc
                    continue
                consecutive = 0
                completed.append(run_id)
    return {"completed": completed, "aborted": aborted}


def _write_manifest(run_dir: Path, run_id: str, slug: str, shots: int,
                    seed: int, *, aborted_reason: str | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "model_slug": slug,
        "model": MODELS[slug],
        "shots": shots,
        "seed": seed,
        "temperature": 0.8,
        "top_p": 0.95,
        "num_predict": 2048,
        "aborted_reason": aborted_reason,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
```

Then the CLI:

```python
def _parse_seeds(text: str) -> list[int]:
    if "-" in text:
        low, high = text.split("-", 1)
        return list(range(int(low), int(high) + 1))
    return [int(part) for part in text.split(",") if part]


def main(argv: list[str] | None = None) -> int:
    from eval.models import ModelError as _ModelError, OllamaClient

    parser = argparse.ArgumentParser(prog="python -m eval.driver")
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--shots", default="0,3")
    parser.add_argument("--seeds", default="1-5")
    parser.add_argument("--results-root", default=str(harness.RESULTS_ROOT))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)

    slugs = [s for s in args.models.split(",") if s]
    unknown = [s for s in slugs if s not in MODELS]
    if unknown:
        print(f"unknown model slug(s): {unknown}; known: {sorted(MODELS)}",
              file=sys.stderr)
        return 2

    problems: list[str] = []
    for slug in slugs:
        try:
            OllamaClient(MODELS[slug]).preflight()
        except _ModelError as exc:
            problems.append(str(exc))
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 2
    if args.preflight_only:
        print("preflight ok")
        return 0

    result = run_grid(
        lambda tag: OllamaClient(tag),
        slugs=slugs,
        shot_counts=[int(s) for s in args.shots.split(",") if s],
        seeds=_parse_seeds(args.seeds),
        results_root=Path(args.results_root),
        health_check=wait_for_health,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["aborted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `from eval.models import ModelError` to the module's imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 42 passed

- [ ] **Step 5: Confirm nothing regressed and preflight reports honestly**

Run: `.venv/bin/pytest tests/ -q`
Expected: 759 passed

Run: `.venv/bin/python -m eval.driver --preflight-only`
Expected: exit 2, listing the three models as not pulled (they are not pulled yet — Task 7 does that). This is the correct answer right now; do not pull to make it pass.

- [ ] **Step 6: Commit**

```bash
git add eval/driver.py tests/test_6a.py
git commit -m "feat(eval): add 6a grid orchestration with run-scoped abort policy"
```

---

### Task 6: Rollup and report

**Files:**
- Create: `eval/rollup.py`
- Modify: `tests/test_6a.py` (append)

**Interfaces:**
- Consumes: `driver.MODELS`, `driver.build_run_id`, `driver.is_complete`.
- Produces:
  - `paired_delta(oxide_cells, explicit_cells) -> float` — percentage points.
  - `paired_se(oxide_cells, explicit_cells) -> float` — `SD(per-task differences)/√n`, in pp. **The primary interval.**
  - `unpaired_se(oxide_cells, explicit_cells) -> float` — difference-of-means SE, reported only as the contrast showing what pairing saved.
  - `classify(delta_pp: float) -> str` — `"supports"` | `"no-detectable-difference"` | `"disconfirms"`.
  - `aggregate(results_root, *, slugs, shot_counts, seeds, partial=False) -> dict`.
  - `render_report(grid: dict) -> str`.
  - `main(argv=None) -> int`.

Spec §3, §6.5.

**The paired-by-task delta is the primary readout**, always quoted with its paired SE. For each task, subtract explicit-Oxide's pass rate across seeds from Oxide's, then average those 20 per-task differences.

Note carefully: on a balanced grid that delta is *algebraically identical* to the difference of marginal arm rates. Pairing does not move the point estimate — it shrinks the interval, because shared task difficulty cancels inside each per-task difference. Quoting the delta without its SE is prohibited.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_6a.py (append)
from eval.rollup import (
    aggregate,
    classify,
    paired_delta,
    paired_se,
    render_report,
    unpaired_se,
)


def _cell(task: str, arm: str, passed: bool) -> dict:
    return {
        "task": task, "arm": arm, "attempts": 1,
        "first_compiled": passed, "first_passed": passed,
        "final_passed": passed, "attempts_to_pass": 1 if passed else 5,
        "tokens_in": 10, "tokens_out": 5, "ms": 100,
        "contract_compliant": [True], "truncated": [False],
    }


def test_paired_delta_is_zero_when_arms_match():
    ox = [_cell("t01", "oxide", True), _cell("t02", "oxide", False)]
    ex = [_cell("t01", "explicit", True), _cell("t02", "explicit", False)]
    assert paired_delta(ox, ex) == 0.0


def test_paired_delta_positive_when_oxide_wins_a_task():
    ox = [_cell("t01", "oxide", True), _cell("t02", "oxide", True)]
    ex = [_cell("t01", "explicit", True), _cell("t02", "explicit", False)]
    assert paired_delta(ox, ex) == 50.0


def test_paired_delta_equals_marginal_difference_on_balanced_grid():
    # With every task present in both arms these are algebraically the
    # same number. Pairing does NOT change the point estimate -- it
    # changes the interval (see the paired_se tests below). Asserting
    # this equality documents the fact so nobody "fixes" it later.
    ox = [_cell("t01", "oxide", True), _cell("t02", "oxide", False)]
    ex = [_cell("t01", "explicit", False), _cell("t02", "explicit", True)]
    marginal = 100.0 * (
        sum(c["first_passed"] for c in ox) / len(ox)
        - sum(c["first_passed"] for c in ex) / len(ex)
    )
    assert paired_delta(ox, ex) == marginal == 0.0


def test_paired_delta_diverges_from_marginal_when_a_task_is_unpaired():
    # The only case where the two estimators genuinely disagree.
    ox = [_cell("t01", "oxide", True), _cell("t02", "oxide", True)]
    ex = [_cell("t01", "explicit", False)]
    assert paired_delta(ox, ex) == 100.0  # only t01 is paired
    marginal = 100.0 * (2 / 2 - 0 / 1)
    assert marginal == 100.0  # coincides here; the SE is what differs


def test_paired_se_is_smaller_than_unpaired_when_arms_correlate():
    # THIS is what pairing buys. Both arms find t01/t02 easy and
    # t03/t04 hard, so the per-task differences are near-constant and
    # their SD collapses, even though each arm's own rate varies a lot.
    ox, ex = [], []
    for task, both_pass in (("t01", True), ("t02", True),
                            ("t03", False), ("t04", False)):
        ox.append(_cell(task, "oxide", both_pass))
        ex.append(_cell(task, "explicit", both_pass))
    assert paired_se(ox, ex) == 0.0  # differences are all zero
    assert unpaired_se(ox, ex) > 0.0


def test_paired_se_is_zero_for_a_single_paired_task():
    ox = [_cell("t01", "oxide", True)]
    ex = [_cell("t01", "explicit", False)]
    assert paired_se(ox, ex) == 0.0  # n=1: no spread to estimate


def test_classify_partitions_at_the_five_point_boundaries():
    assert classify(5.0) == "supports"
    assert classify(5.1) == "supports"
    assert classify(4.9) == "no-detectable-difference"
    assert classify(0.0) == "no-detectable-difference"
    assert classify(-4.9) == "no-detectable-difference"
    assert classify(-5.0) == "disconfirms"
    assert classify(-5.1) == "disconfirms"


def test_aggregate_refuses_incomplete_grid_without_partial(tmp_path):
    with pytest.raises(RuntimeError, match="incomplete"):
        aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1])


def test_aggregate_reports_missing_runs_with_partial(tmp_path):
    grid = aggregate(
        tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1], partial=True
    )
    assert grid["missing"] == ["6a-qwen1_5b-0shot-s1"]


def test_render_report_states_band_alongside_delta():
    grid = {
        "missing": [],
        "points": [
            {
                "model_slug": "qwen1_5b", "shots": 0,
                "paired_delta_pp": 1.0, "paired_se_pp": 2.4,
                "unpaired_se_pp": 4.8,
                "verdict": "no-detectable-difference",
                "arms": {},
            }
        ],
    }
    out = render_report(grid)
    assert "no-detectable-difference" in out
    assert "±5pp" in out
    assert "2.4" in out  # the interval is never omitted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k "paired or classify or aggregate or render"`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.rollup'`

- [ ] **Step 3: Write the implementation**

```python
# eval/rollup.py
"""Phase 6a grid aggregation (SPEC Part X, sections 3 and 6.5).

The primary readout is the PAIRED-BY-TASK delta. Both arms run the same
20 tasks and task difficulty dominates the variance, so pairing cancels
it and roughly halves the detectable effect. Comparing marginal arm
rates instead is prohibited as the primary statistic (section 3).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from eval.driver import MODELS, build_run_id, is_complete

BAND_PP = 5.0


def _by_task(cells: list[dict]) -> dict[str, list[bool]]:
    grouped: dict[str, list[bool]] = defaultdict(list)
    for cell in cells:
        grouped[cell["task"]].append(bool(cell["first_passed"]))
    return grouped


def _per_task_differences(
    oxide_cells: list[dict], explicit_cells: list[dict]
) -> list[float]:
    """(oxide - explicit) pass rate for each task present in BOTH arms."""
    left, right = _by_task(oxide_cells), _by_task(explicit_cells)
    diffs: list[float] = []
    for task in sorted(set(left) & set(right)):
        lo, ro = left[task], right[task]
        diffs.append((sum(lo) / len(lo)) - (sum(ro) / len(ro)))
    return diffs


def paired_delta(oxide_cells: list[dict], explicit_cells: list[dict]) -> float:
    """Mean per-task (oxide - explicit) first-attempt pass rate, in pp.

    On a balanced grid this equals the difference of marginal arm rates
    -- pairing does not move the point estimate. It moves the INTERVAL;
    see paired_se.
    """
    diffs = _per_task_differences(oxide_cells, explicit_cells)
    if not diffs:
        return 0.0
    return round(100.0 * sum(diffs) / len(diffs), 4)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def paired_se(oxide_cells: list[dict], explicit_cells: list[dict]) -> float:
    """SD(per-task differences)/sqrt(n), in pp -- the primary interval.

    This is what pairing actually buys: shared task difficulty cancels
    inside each difference, so the SD collapses in proportion to how
    strongly the arms correlate across tasks.
    """
    diffs = _per_task_differences(oxide_cells, explicit_cells)
    if len(diffs) < 2:
        return 0.0
    return round(100.0 * _stdev(diffs) / (len(diffs) ** 0.5), 4)


def unpaired_se(oxide_cells: list[dict], explicit_cells: list[dict]) -> float:
    """Difference-of-means SE, ignoring the pairing. Reported only as the
    contrast that shows what pairing saved; never the primary interval."""
    left = [sum(v) / len(v) for v in _by_task(oxide_cells).values()]
    right = [sum(v) / len(v) for v in _by_task(explicit_cells).values()]
    if len(left) < 2 or len(right) < 2:
        return 0.0
    variance = _stdev(left) ** 2 / len(left) + _stdev(right) ** 2 / len(right)
    return round(100.0 * variance**0.5, 4)


def classify(delta_pp: float) -> str:
    """The section-3 partition: exhaustive and non-overlapping."""
    if delta_pp >= BAND_PP:
        return "supports"
    if delta_pp <= -BAND_PP:
        return "disconfirms"
    return "no-detectable-difference"


def _load_cells(run_dir: Path) -> list[dict]:
    path = run_dir / "cells.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _arm_stats(cells: list[dict]) -> dict:
    if not cells:
        return {"n": 0}
    total = len(cells)
    return {
        "n": total,
        "first_pass_rate": round(
            100.0 * sum(c["first_passed"] for c in cells) / total, 2
        ),
        "final_pass_rate": round(
            100.0 * sum(c["final_passed"] for c in cells) / total, 2
        ),
        "mean_attempts_to_pass": round(
            sum(c["attempts_to_pass"] for c in cells) / total, 3
        ),
        "truncation_rate": round(
            100.0 * sum(any(c["truncated"]) for c in cells) / total, 2
        ),
        "contract_compliance_rate": round(
            100.0 * sum(all(c["contract_compliant"]) for c in cells) / total, 2
        ),
        "tokens_out": sum(c["tokens_out"] for c in cells),
    }


def aggregate(
    results_root: Path,
    *,
    slugs: list[str],
    shot_counts: list[int],
    seeds: list[int],
    partial: bool = False,
) -> dict:
    """Roll the grid up into points, one per (model, shots)."""
    root = Path(results_root)
    missing = [
        build_run_id(slug, shots, seed)
        for slug in slugs
        for shots in shot_counts
        for seed in seeds
        if not is_complete(root / build_run_id(slug, shots, seed))
    ]
    if missing and not partial:
        raise RuntimeError(
            f"incomplete grid: {len(missing)} run(s) missing "
            f"(first: {missing[0]}). Pass --partial to report anyway; a grid "
            f"silently missing aborted runs reads as a finished result."
        )
    points: list[dict] = []
    for slug in slugs:
        for shots in shot_counts:
            cells: list[dict] = []
            for seed in seeds:
                cells.extend(_load_cells(root / build_run_id(slug, shots, seed)))
            by_arm = {
                arm: [c for c in cells if c["arm"] == arm]
                for arm in ("oxide", "explicit", "rust")
            }
            arms = {arm: _arm_stats(rows) for arm, rows in by_arm.items()}
            oxide, explicit = by_arm["oxide"], by_arm["explicit"]
            delta = paired_delta(oxide, explicit)
            points.append(
                {
                    "model_slug": slug,
                    "model": MODELS.get(slug, slug),
                    "shots": shots,
                    "paired_delta_pp": delta,
                    "paired_se_pp": paired_se(oxide, explicit),
                    "unpaired_se_pp": unpaired_se(oxide, explicit),
                    "verdict": classify(delta),
                    "arms": arms,
                }
            )
    return {"missing": missing, "points": points}


def render_report(grid: dict) -> str:
    """Markdown report. The band is printed beside every delta so an
    inconclusive result cannot be read as a positive one."""
    lines = [
        "# Oxide Phase 6a — Small-Model Capability Ladder",
        "",
        "Primary comparison: **Oxide − explicit-Oxide, paired by task**.",
        "Decision band: **±5pp** (pre-registered; 20 tasks cannot resolve",
        "smaller effects — that is absence of resolution, not evidence of",
        "absence). Rust is a reference arm carrying a large pretraining-",
        "exposure advantage and a ~22× smaller prompt; Oxide-vs-Rust is not",
        "evidence about language design.",
        "",
    ]
    if grid["missing"]:
        lines += [
            f"> **PARTIAL GRID** — {len(grid['missing'])} run(s) missing: "
            + ", ".join(grid["missing"]),
            "",
        ]
    lines += [
        "| Model | Shots | Paired Δ (pp) | ± SE | Verdict | Oxide | Explicit | Rust |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for point in grid["points"]:
        arms = point["arms"]

        def rate(name: str) -> str:
            return f"{arms.get(name, {}).get('first_pass_rate', 0):.0f}%"

        lines.append(
            f"| {point['model_slug']} | {point['shots']} | "
            f"{point['paired_delta_pp']:+.1f} | "
            f"{point.get('paired_se_pp', 0.0):.1f} | {point['verdict']} | "
            f"{rate('oxide')} | {rate('explicit')} | {rate('rust')} |"
        )
    lines += [
        "",
        "Δ is the mean per-task (Oxide − explicit-Oxide) first-attempt pass",
        "rate; SE is `SD(per-task differences)/√n`. On a balanced grid the Δ",
        "equals the difference of marginal arm rates — pairing buys the",
        "narrower interval, not a different point estimate.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from eval import harness

    parser = argparse.ArgumentParser(prog="python -m eval.rollup")
    parser.add_argument("--results-root", default=str(harness.RESULTS_ROOT))
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--shots", default="0,3")
    parser.add_argument("--seeds", default="1-5")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    low_high = args.seeds.split("-")
    seeds = (
        list(range(int(low_high[0]), int(low_high[1]) + 1))
        if len(low_high) == 2
        else [int(s) for s in args.seeds.split(",") if s]
    )
    grid = aggregate(
        Path(args.results_root),
        slugs=[s for s in args.models.split(",") if s],
        shot_counts=[int(s) for s in args.shots.split(",") if s],
        seeds=seeds,
        partial=args.partial,
    )
    out_dir = Path(args.out or Path(args.results_root) / "6a-rollup")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "grid.json").write_text(
        json.dumps(grid, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "REPORT.md").write_text(render_report(grid), encoding="utf-8")
    print(render_report(grid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_6a.py -q`
Expected: 52 passed

- [ ] **Step 5: Confirm nothing regressed**

Run: `.venv/bin/pytest tests/ -q`
Expected: 769 passed

- [ ] **Step 6: Commit**

```bash
git add eval/rollup.py tests/test_6a.py
git commit -m "feat(eval): add paired-by-task rollup with pre-registered band"
```

---

### Task 7: SPEC Part X, model pulls, live smoke

**Files:**
- Modify: `SPEC.md` (append Part X)
- Modify: `tests/test_6a.py` (append the live smoke test)

**Interfaces:**
- Consumes: everything above.
- Produces: a normative SPEC part and a deselectable live test.

Project convention: contracts become binding only as numbered SPEC parts. The design doc is the source text.

- [ ] **Step 1: Transcribe the design into SPEC.md as Part X**

Append to `SPEC.md`, following the existing Part heading style (`# Part X — Phase 6a Contract (small-model capability ladder)`) and continuing section numbering from §46 — so the design's sections 3–9 become **§47 Pre-registered analysis plan**, **§48 Pinned run parameters**, **§49 Run identity and layout**, **§50 Module contracts**, **§51 Error handling and failure classification**, **§52 Test plan**.

Copy the content from `docs/superpowers/specs/2026-08-07-phase-6a-small-model-ladder-design.md` verbatim where it is already normative (the parameter table, the ±5pp partition table, the failure-classification table, the extraction rule, the repair-prompt template). Drop the design-doc-only framing (§1 Motivation, §10 Deliverables).

- [ ] **Step 2: Verify SPEC section numbering is unbroken**

Run: `grep -n '^## [0-9]' SPEC.md | tail -12`
Expected: §44, §45, §46 then §47–§52 in order, no duplicates or gaps.

- [ ] **Step 3: Commit the spec**

```bash
git add SPEC.md
git commit -m "docs: add SPEC Part X — Phase 6a contract"
```

- [ ] **Step 4: Pull the three pinned models**

```bash
ollama pull qwen2.5-coder:0.5b-instruct-q8_0
ollama pull qwen2.5-coder:1.5b-instruct-q8_0
ollama pull qwen2.5-coder:7b-instruct-q8_0
```

Roughly 0.5GB + 1.6GB + 8GB. All three tags were verified present in the registry (HTTP 200).

- [ ] **Step 5: Verify preflight now passes**

Run: `.venv/bin/python -m eval.driver --preflight-only`
Expected: `preflight ok`, exit 0.

If it reports a quantization mismatch, the wrong tag was pulled — pull the `-q8_0` tag explicitly rather than relaxing the check. The check *is* the uniform-quantization control.

- [ ] **Step 6: Write the live smoke test**

```python
# tests/test_6a.py (append)
import shutil
import subprocess

live = pytest.mark.skipif(
    shutil.which("ollama") is None,
    reason="ollama not installed",
)


@live
def test_live_smoke_one_task_on_smallest_model(tmp_path):
    """One real generation end to end. Asserts the plumbing works, NOT
    that the model succeeds -- a 0.5B failure is a valid result."""
    from eval.models import ModelError, OllamaClient

    client = OllamaClient(MODELS["qwen0_5b"])
    if not client.healthy():
        pytest.skip("ollama daemon not running")
    try:
        client.preflight()
    except ModelError as exc:
        pytest.skip(f"model not pulled: {exc}")

    cell = run_session(
        client,
        run_id="6a-smoke",
        task_id="t01",
        arm="oxide",
        shots=0,
        results_root=tmp_path,
        raw_dir=tmp_path / "raw",
    )
    assert cell["task"] == "t01"
    assert 1 <= cell["attempts"] <= 4
    assert isinstance(cell["final_passed"], bool)
    assert len(cell["truncated"]) == cell["attempts"]
    assert (tmp_path / "raw" / "t01.oxide.1.txt").exists()
```

- [ ] **Step 7: Run the smoke test**

Run: `.venv/bin/pytest tests/test_6a.py -q -k live_smoke -v`
Expected: PASS (or a clean skip if the daemon is down).

- [ ] **Step 8: Full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: 770 passed

- [ ] **Step 9: Commit**

```bash
git add tests/test_6a.py
git commit -m "test(eval): add live smoke test for the 6a driver"
```

---

## Running the grid (after Task 7)

Not part of the plan's test cycle — this is the experiment itself, ~8–14h.

```bash
# Cheapest rungs first, so plumbing problems surface in minutes not hours.
.venv/bin/python -m eval.driver --models qwen0_5b --shots 0,3 --seeds 1-5
.venv/bin/python -m eval.driver --models qwen1_5b --shots 0,3 --seeds 1-5
.venv/bin/python -m eval.driver --models qwen7b   --shots 0,3 --seeds 1-5

# Re-entry is safe: completed run ids are skipped, incomplete ones redone.
.venv/bin/python -m eval.rollup
```

Watch for: a `truncation_rate` above ~20% at 0.5B (consider whether `num_predict` is too tight — but change it only with a spec amendment, never mid-grid), and any `aborted` entries in the driver's JSON output.

---

## Self-Review

**Spec coverage.** §2 scope → Global Constraints. §3 analysis plan → Task 6 (`paired_delta`, `paired_se`, `classify`, band in the report). §4 pinned params → Tasks 3 and 5. §5 layout → Tasks 4 and 5. §6.1 → Task 3. §6.2 → Task 1. §6.3 → Task 2. §6.4 → Tasks 4 and 5. §6.5 → Task 6. §7 → Tasks 3, 4, 5 (both directions of the governing rule). §8 test plan → all tasks. §9 risks → the truncation-rate watch above and the report's stated caveats. §10 deliverables → Tasks 1–7.

**Placeholder scan.** No TBD/TODO. Every code step carries runnable code; every test step carries real assertions.

**Type consistency.** `Generation` is 5 fields everywhere (Tasks 3, 4, 7). `run_session` is keyword-only after `client` in the implementation and in every test. `build_repair_prompt(arm, source, verdict)` matches its call in `driver.run_session`. `is_complete`/`reset_run`/`build_run_id`/`MODELS` are defined in Task 5 and imported by Task 6. Cell-record keys are identical in Task 4's producer, Task 6's `_arm_stats` consumer, and Task 6's `_cell` fixture.

**Known deviation to flag at execution:** Task 4's test `test_run_session_records_a_pass_on_first_attempt` uses a hand-written `_GOOD_OXIDE` program. If it does not compile under the real pipeline the test will fail for the wrong reason — the implementer should substitute a verified program from `eval/solutions/oxide/` rather than editing the assertion.

**Correction made during self-review (spec amended to match).** An earlier draft claimed the paired-by-task delta would differ from the marginal-rate comparison, and the spec's test plan asked for a fixture proving it. That is false: on a balanced grid the two are algebraically identical. Pairing's benefit is entirely in the **interval** — `SD(per-task differences)/√n` shrinks as the arms correlate across tasks. `paired_se`/`unpaired_se` and their tests exist to make that concrete, and the spec's §3 and §8 were corrected. If a future reviewer sees the delta match the marginal number, that is the design working, not a bug.
