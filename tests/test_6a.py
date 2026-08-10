import inspect
import io
import json
import subprocess
import urllib.error
from datetime import datetime

import pytest

from eval import driver, harness, repair, rollup
from eval.driver import (
    MODELS,
    build_run_id,
    is_complete,
    parse_seeds,
    preflight_environment,
    reset_run,
    run_grid,
    run_one,
    run_session,
    wait_for_health,
)
from eval.extract import Extraction, extract
from eval.llamacpp import ServerContextOverflowError
from eval.models import (
    ContextOverflowError,
    Generation,
    ModelError,
    OllamaClient,
    _parse_http_error_body,
    estimate_tokens,
)
from eval.repair import RepairPromptError, build_repair_prompt
from eval.rollup import (
    AT_CEILING,
    AT_FLOOR,
    INSUFFICIENT,
    _verdict,
    across_seed_se,
    aggregate,
    classify,
    diagnostic_histogram,
    paired_delta,
    paired_se,
    render_report,
    unpaired_se,
)


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


_REPAIR_TASK = "t01"  # a real corpus task: repair prompts are task-bound


def _repair(arm: str, source: str, verdict: dict, **kwargs) -> str:
    return build_repair_prompt(
        arm, source, verdict, task_id=_REPAIR_TASK, **kwargs
    )


def _attempt_block(prompt: str) -> str:
    """The repair-specific tail, after the carried-over initial prompt."""
    marker = "The program below was rejected."
    assert marker in prompt
    return prompt[prompt.index(marker):]


def test_repair_prompt_includes_program_and_diagnostics():
    out = _repair("oxide", "let x = 1", _COMPILE_FAIL)
    assert "let x = 1" in out
    assert "4:15: OX0400: value moved here" in out


def test_repair_prompt_renders_notes_and_suggestion_indented():
    out = _repair("oxide", "let x = 1", _COMPILE_FAIL)
    assert "  note: line 3, col 18" in out
    assert "  suggestion: Keep it available by cloning at the move site." in out


def test_repair_prompt_omits_empty_suggestion():
    diag = dict(_BAD_DIAG, suggestion="")
    verdict = dict(_COMPILE_FAIL, diagnostics=[diag])
    assert "suggestion:" not in _attempt_block(_repair("rust", "x", verdict))


def test_repair_prompt_ends_with_output_contract():
    out = _repair("oxide", "let x = 1", _COMPILE_FAIL)
    assert out.rstrip().endswith(
        "Reply with ONLY the complete corrected program source, "
        "no fences, no commentary."
    )


def test_repair_prompt_drops_the_initial_output_contract():
    # Exactly one instruction survives: the corrected-program one. The
    # initial prompt's contract would otherwise trail in mid-prompt.
    out = _repair("oxide", "let x = 1", _COMPILE_FAIL)
    assert harness.OUTPUT_CONTRACT not in out


def test_repair_prompt_carries_the_arms_initial_prompt():
    # The whole point of the section-6.3 change: each arm re-enters the
    # repair turn with the context it started with, so section 47's
    # repair lift measures diagnostics rather than card recall.
    for arm in ("oxide", "explicit", "rust"):
        initial = harness.build_prompt(arm, _REPAIR_TASK)
        carried = initial[: initial.rstrip("\n").rindex(harness.OUTPUT_CONTRACT)]
        assert carried.strip() in _repair(arm, "src", _COMPILE_FAIL)


def test_repair_prompt_carries_the_task_statement():
    task = harness.load_tasks()[_REPAIR_TASK]
    for arm in ("oxide", "explicit", "rust"):
        assert task["prompt"].rstrip("\n") in _repair(arm, "src", _COMPILE_FAIL)


def test_repair_prompt_carries_few_shot_examples():
    out = _repair("oxide", "src", _COMPILE_FAIL, shots=3)
    assert out.count("Example task:") == 3
    assert len(out) > len(_repair("oxide", "src", _COMPILE_FAIL))


def test_repair_prompt_raises_if_harness_tail_moves(monkeypatch):
    # A frozen-harness change must fail loudly, not silently emit a
    # prompt whose tail was never stripped.
    monkeypatch.setattr(
        "eval.harness.build_prompt", lambda *a, **k: "no contract here\n"
    )
    with pytest.raises(RepairPromptError):
        _repair("oxide", "src", _COMPILE_FAIL)


def test_repair_prompt_raises_if_the_contract_is_empty(monkeypatch):
    # An empty contract satisfies both clauses of the tail check, and
    # then prompt[:-0] is "" -- a zero-length context shipped silently
    # as the arm's retained material.
    monkeypatch.setattr("eval.harness.OUTPUT_CONTRACT", "")
    with pytest.raises(RepairPromptError, match="empty"):
        _repair("oxide", "src", _COMPILE_FAIL)


def test_repair_prompt_arms_track_the_frozen_harness():
    # repair must not keep its own copy of the arm list: a harness arm
    # added later would be rejected here as "unknown".
    assert not hasattr(repair, "ARMS")


def test_repair_prompt_runtime_failure_reports_own_output():
    out = _repair("oxide", "print(41)", _RUNTIME_FAIL)
    assert "compiled and ran, but produced incorrect output" in out
    assert "41" in out


def test_repair_prompt_runtime_failure_has_no_diagnostics_block():
    assert "Diagnostics:" not in _attempt_block(
        _repair("oxide", "print(41)", _RUNTIME_FAIL)
    )


def test_repair_prompt_cannot_leak_expected_stdout():
    # Structural guarantee: expected_stdout is not a parameter, so there
    # is no path by which it could reach the model. A weak model that
    # learned the expected string could pass by hard-coding a print of
    # it, which would silently corrupt the headline metric.
    assert "expected" not in inspect.signature(build_repair_prompt).parameters
    assert (
        "expected" not in inspect.signature(harness.build_prompt).parameters
    )


def test_repair_prompt_never_discloses_a_real_tasks_expected_output():
    # The literal check, over the real corpus x arms x shot conditions.
    # "no substring" cannot be taken character-literally: bare digits and
    # words recur innocently (t03 expects "21\n" and the Rust preamble
    # says "edition 2021"; t13 expects "false" and the card documents the
    # `false` literal). The enforced form is the one a model could
    # actually copy: neither the whole expected_stdout nor any single
    # LINE of it ever appears as a line of the prompt.
    for task_id, task in sorted(harness.load_tasks().items()):
        expected = task["expected_stdout"]
        want_lines = {line for line in expected.split("\n") if line}
        for arm in ("oxide", "explicit", "rust"):
            for shots in (0, 3):
                out = build_repair_prompt(
                    arm,
                    "src",
                    _RUNTIME_FAIL,
                    task_id=task_id,
                    shots=shots,
                )
                assert expected not in out, (task_id, arm, shots)
                got_lines = {line.strip() for line in out.split("\n")}
                assert not (want_lines & got_lines), (task_id, arm, shots)


def test_repair_prompt_attempt_block_is_arm_identical():
    # The carried-over lead is arm-NATIVE by construction (each arm gets
    # its own initial prompt back). The repair-specific tail stays
    # arm-identical in structure, arm-native in content.
    def skeleton(text: str) -> list[str]:
        return [ln for ln in text.split("\n") if ln.endswith(":") or not ln]

    shapes = {
        arm: skeleton(_attempt_block(_repair(arm, "src", _COMPILE_FAIL)))
        for arm in ("oxide", "explicit", "rust")
    }
    assert shapes["oxide"] == shapes["explicit"] == shapes["rust"]


def test_repair_prompt_preserves_rustc_help_text_verbatim():
    # Section 45 folds rustc's help/children into `message`; giving each
    # arm its strongest native diagnostics is the fair form of the test.
    message = "borrow of moved value\nhelp: consider cloning the value"
    diag = dict(_BAD_DIAG, code="E0382", message=message, suggestion="")
    verdict = dict(_COMPILE_FAIL, diagnostics=[diag])
    assert message in _repair("rust", "fn main(){}", verdict)


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

    def __call__(self, url: str, payload: dict | None = None, timeout_s: int = 120) -> dict:
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
        # Section 48: pinned explicitly because Ollama's own default is
        # 4096 -- not the model's 32768 capability -- and an overflowing
        # prompt is truncated from the FRONT, dropping the language card
        # from the oxide and explicit arms only.
        "num_ctx": 8192,
    }


def test_generate_sends_truncate_false_at_the_top_level(monkeypatch):
    http = _FakeHTTP(json.loads(_chat_response()))
    _client(monkeypatch, http).generate("hi", seed=4)
    payload = http.calls[0][1]
    # Section 51: the daemon's DEFAULT is to accept an oversized prompt,
    # silently discard the FRONT of it, and answer anyway -- reproduced
    # on :11434, a 3160-token prompt into a 256-token window returned
    # 200 with prompt_eval_count 130 and only the TAIL canary visible.
    # Under that default there is nothing for either overflow check to
    # catch: no exception, no error field, and a plausible answer built
    # on a prompt whose language card is gone.
    assert payload["truncate"] is False
    # Top-level ONLY. Also reproduced on :11434: the identical flag
    # inside `options` is silently ignored (200, front-truncated), so
    # moving it there to sit alongside num_ctx would disable the guard
    # without failing anything.
    assert "truncate" not in payload["options"]


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
    http = _FakeHTTP(tags, {"version": "0.6.8"})
    info = _client(monkeypatch, http).preflight()
    assert info["digest"] == "abc123def456"
    assert info["quantization_level"] == "Q8_0"
    assert info["context_length"] == 32768
    # Section 48 pins "Backend: Ollama HTTP, version recorded".
    assert info["ollama_version"] == "0.6.8"
    assert http.calls[1][0].endswith("/api/version")


def test_preflight_rejects_missing_model(monkeypatch):
    tags = {"models": [{"name": "other:latest", "digest": "x", "details": {}}]}
    with pytest.raises(ModelError, match="not pulled"):
        _client(monkeypatch, _FakeHTTP(tags)).preflight()


def test_preflight_rejects_wrong_quantization(monkeypatch):
    # This is what actually enforces SPEC section 48's per-family pin:
    # the 1.5b already on this machine is Q4_K_M, is pinned at q8_0 like
    # the rest of the ladder, and must be rejected.
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


def _quant_client(monkeypatch, http, tag, quantization):
    monkeypatch.setattr("eval.models._request", http)
    return OllamaClient(tag, quantization=quantization, sleep=lambda _s: None)


def test_preflight_accepts_the_slugs_own_pinned_quantization(monkeypatch):
    """The bug this pins: the guard hard-coded Q8_0, so the SPEC-registered
    deepseek16b_lite subject could not preflight on the default backend at
    all -- and the refusal cited an invariant SPEC section 48 had already
    retired. Ollama reports Q5_K_M where the pin reads q5_K_M, so the
    comparison must also be case-insensitive."""
    tag = "deepseek-coder-v2:16b-lite-instruct-q5_K_M"
    tags = {
        "models": [
            {
                "name": tag,
                "digest": "6065d4880bf9",
                "details": {
                    "quantization_level": "Q5_K_M",
                    "context_length": 163840,
                },
            }
        ]
    }
    http = _FakeHTTP(tags, {"version": "0.6.8"})
    info = _quant_client(monkeypatch, http, tag, "q5_K_M").preflight()
    assert info["quantization_level"] == "Q5_K_M"
    assert info["digest"] == "6065d4880bf9"


def test_preflight_still_rejects_a_mismatch_against_a_non_default_pin(
    monkeypatch,
):
    """A guard that stopped guarding would be worse than the bug it
    replaced. Pinned at q5_K_M, a q8_0 blob is still refused -- the check
    tracks the slug's pin in BOTH directions, it does not merely widen."""
    tag = "deepseek-coder-v2:16b-lite-instruct-q5_K_M"
    tags = {
        "models": [
            {
                "name": tag,
                "digest": "abc",
                "details": {"quantization_level": "Q8_0"},
            }
        ]
    }
    http = _FakeHTTP(tags, {"version": "0.6.8"})
    with pytest.raises(ModelError, match="expected q5_K_M"):
        _quant_client(monkeypatch, http, tag, "q5_K_M").preflight()


def test_preflight_defaults_to_q8_0_when_no_pin_is_supplied(monkeypatch):
    """Every existing slug behaves exactly as before the pin was plumbed:
    an unparameterised client still demands q8_0."""
    assert OllamaClient("qwen2.5-coder:7b-instruct-q8_0").quantization == "q8_0"
    tags = {
        "models": [
            {
                "name": "qwen2.5-coder:1.5b-instruct-q8_0",
                "digest": "abc",
                "details": {"quantization_level": "Q5_K_M"},
            }
        ]
    }
    with pytest.raises(ModelError, match="expected q8_0"):
        _client(monkeypatch, _FakeHTTP(tags)).preflight()


def test_generate_rejects_a_malformed_200_body(monkeypatch):
    # Section 51's governing rule: a 200 that is not a well-formed chat
    # completion is INFRASTRUCTURE. Defaulting it to "" would ship an
    # empty Generation through extract -> submit -> cells.jsonl as a
    # genuine model failure, biasing the arm toward the null.
    http = _FakeHTTP({"error": "model runner has terminated"})
    with pytest.raises(ModelError, match="malformed"):
        _client(monkeypatch, http).generate("hi", seed=1)


def test_generate_rejects_a_200_body_with_no_message(monkeypatch):
    http = _FakeHTTP({"done": True, "eval_count": 0})
    with pytest.raises(ModelError, match="malformed"):
        _client(monkeypatch, http).generate("hi", seed=1)


def test_generate_rejects_a_non_dict_message(monkeypatch):
    # {"message": "text"} passes a bare `"message" in body` and then
    # raises AttributeError on .get -- which is NOT a ModelError, so it
    # escapes _run_grid_cell's handler and kills the whole grid instead
    # of aborting one run id.
    http = _FakeHTTP({"message": "just a string"})
    with pytest.raises(ModelError, match="malformed"):
        _client(monkeypatch, http).generate("hi", seed=1)


def test_generate_rejects_a_null_message(monkeypatch):
    http = _FakeHTTP({"message": None})
    with pytest.raises(ModelError, match="malformed"):
        _client(monkeypatch, http).generate("hi", seed=1)


def test_generate_rejects_a_non_string_content(monkeypatch):
    http = _FakeHTTP({"message": {"content": None}})
    with pytest.raises(ModelError, match="malformed"):
        _client(monkeypatch, http).generate("hi", seed=1)


def test_malformed_body_is_not_retried_as_transport(monkeypatch):
    # It is a hard stop, not a transient: one call, then ModelError.
    http = _FakeHTTP({"error": "boom"})
    with pytest.raises(ModelError):
        _client(monkeypatch, http).generate("hi", seed=1)
    assert len(http.calls) == 1


def test_generate_refuses_a_prompt_that_overflows_the_window(monkeypatch):
    # The defect this guard exists for, reproduced at Ollama's own
    # default window: ~2000 prompt tokens + num_predict 2048 > 4096.
    # llama.cpp would truncate from the FRONT and drop the language card
    # from the oxide/explicit arms only -- wrong-but-plausible numbers
    # with nothing in the artifacts to reveal it.
    http = _FakeHTTP(json.loads(_chat_response()))
    monkeypatch.setattr("eval.models._request", http)
    client = OllamaClient("m", num_ctx=4096, num_predict=2048,
                          sleep=lambda _s: None)
    with pytest.raises(ContextOverflowError, match="num_ctx"):
        client.generate("x" * 9000, seed=1)
    # Refused BEFORE the request: no generation burned, no retry storm.
    assert http.calls == []


def test_context_overflow_is_a_model_error():
    # Subclassing ModelError is what scopes the abort to one run id with
    # the cause in that run's manifest, and lets three in a row trip the
    # consecutive-abort backstop. A bare Exception would kill the grid.
    assert issubclass(ContextOverflowError, ModelError)


def test_overflow_guard_counts_num_predict_not_just_the_prompt(monkeypatch):
    # A prompt that fits on its own but cannot fit alongside the
    # generation it reserves. Ignoring num_predict is the subtle version
    # of this bug.
    http = _FakeHTTP(json.loads(_chat_response()))
    monkeypatch.setattr("eval.models._request", http)
    client = OllamaClient("m", num_ctx=1000, num_predict=900,
                          sleep=lambda _s: None)
    client.check_context("x" * 396)          # 99 + 900 <= 1000
    with pytest.raises(ContextOverflowError):
        client.check_context("x" * 800)      # 200 + 900 > 1000


_WORST_REJECTED_PROGRAM = "x" * (2048 * 4)
"""A generation that ran to num_predict and was fed back as the rejected
program -- the largest thing a repair prompt can ever carry, and the
characteristic small-model failure mode (degenerate repetition)."""


def _worst_repair_prompt() -> str:
    """The largest prompt this grid can send: the explicit arm's 3-shot
    repair prompt around a num_predict-truncated program."""
    return _repair(
        "explicit", _WORST_REJECTED_PROGRAM, _COMPILE_FAIL, shots=3
    )


def test_pinned_window_clears_the_worst_real_repair_prompt(monkeypatch):
    # The pin is only worth anything if 8192 actually fits the worst
    # case: ~1670 tok of carried context + a 2048-tok rejected program +
    # 2048 reserved for the fix.
    http = _FakeHTTP(json.loads(_chat_response()))
    monkeypatch.setattr("eval.models._request", http)
    OllamaClient("m", sleep=lambda _s: None).generate(
        _worst_repair_prompt(), seed=1
    )
    assert http.calls[0][1]["options"]["num_ctx"] == 8192


def test_the_ollama_default_window_would_have_overflowed():
    # The measured defect: that same prompt against the daemon's
    # unpinned 4096 default is refused. If this ever stops holding, the
    # pin's justification in section 48 has gone stale.
    with pytest.raises(ContextOverflowError):
        OllamaClient("m", num_ctx=4096).check_context(_worst_repair_prompt())


def test_the_default_window_biases_against_the_oxide_arms_specifically():
    # The heart of the defect. For a rejected program in the realistic
    # ~1.6k-7k char band, the carried language card is exactly what
    # pushes the oxide/explicit arms over the daemon's 4096 default
    # while the one-line Rust preamble stays under it. The lost tokens
    # come off the FRONT -- the card itself. That is a non-random bias
    # against the two arms section 47 makes primary, not a shared
    # overhead that cancels in the comparison.
    program = "x" * 2000
    at_default = OllamaClient("m", num_ctx=4096)
    for arm in ("oxide", "explicit"):
        prompt = _repair(arm, program, _COMPILE_FAIL, shots=3)
        with pytest.raises(ContextOverflowError):
            at_default.check_context(prompt)
    at_default.check_context(_repair("rust", program, _COMPILE_FAIL, shots=3))


def test_pinned_window_clears_that_same_case_for_every_arm():
    program = "x" * 2000
    pinned = OllamaClient("m")
    for arm in harness.ARMS:
        pinned.check_context(_repair(arm, program, _COMPILE_FAIL, shots=3))


def test_configured_timeout_reaches_the_request_layer(monkeypatch):
    # The defect class this plan already caught once: a non-default value
    # that never leaves the constructor. Only a non-default proves it.
    seen: list[int] = []

    def fake_request(url: str, payload: dict | None = None,
                     timeout_s: int = 120) -> dict:
        seen.append(timeout_s)
        return json.loads(_chat_response())

    monkeypatch.setattr("eval.models._request", fake_request)
    client = OllamaClient("m", timeout_s=37, sleep=lambda _s: None)
    client.generate("hi", seed=1)
    assert seen == [37]


def test_healthy_is_false_when_unreachable(monkeypatch):
    http = _FakeHTTP(urllib.error.URLError("down"))
    assert _client(monkeypatch, http).healthy() is False


def test_repair_prompt_rejects_unknown_arm():
    with pytest.raises(ValueError):
        build_repair_prompt("python", "x", _COMPILE_FAIL, task_id=_REPAIR_TASK)


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


# Verified against the real pipeline: Oxide's print() quotes STRINGS, so
# print("hi") emits '"hi"\n', not 'hi\n'. Printing an Int avoids that.
_GOOD_OXIDE = "fn main() {\n    print(42)\n}\n"


def test_run_session_records_a_pass_on_first_attempt(tmp_path):
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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

    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
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


class _ContextExhaustsClient:
    """Answers with an always-failing program up to (not including) call
    number ``fires_on_attempt``, then raises
    ``ServerContextOverflowError`` on that call -- the shape of a repair
    prompt that grew across attempts, passed the client's OWN pre-request
    estimate, and overflowed the server's real context window anyway.
    ``ContextOverflowError`` (the client's own pre-request check) is a
    DIFFERENT raise site, exercised separately below by
    ``_ClientSideOverflowAfterOneAttempt``/``_OverflowingClient`` -- the
    evidence gate (SPEC section 45/51) treats both identically, but the
    two raise sites are kept distinct in these fixtures so a regression
    in either one shows up on its own test."""

    def __init__(self, fires_on_attempt: int) -> None:
        self.fires_on_attempt = fires_on_attempt
        self.calls = 0

    def generate(self, prompt: str, *, seed: int) -> Generation:
        self.calls += 1
        if self.calls == self.fires_on_attempt:
            raise ServerContextOverflowError("prompt exceeds num_ctx 8192")
        return Generation("this is not a program", 10, 5, 100, False)


def test_run_session_ends_at_context_exhaustion_with_evidence_so_far(tmp_path):
    # Quadrant: server-side raise, attempts >= 1 -- a RESULT, not
    # infrastructure (SPEC section 45/51). The session ends with the N-1
    # attempts already submitted recorded, not the 4-attempt cap, and
    # does not raise.
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    cell = run_session(
        _ContextExhaustsClient(fires_on_attempt=3),
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=raw_dir,
        tasks_path=tasks,
    )
    assert set(cell.keys()) == _CELL_SCHEMA | {"context_exhausted"}
    assert cell["attempts"] == 2  # two submissions happened before attempt 3
    assert cell["context_exhausted"] is True
    assert cell["first_compiled"] is False
    assert cell["first_passed"] is False
    assert cell["final_passed"] is False  # last submitted verdict: a failure
    # Only the two attempts that actually generated wrote raw output.
    assert (raw_dir / "tX.oxide.1.txt").is_file()
    assert (raw_dir / "tX.oxide.2.txt").is_file()
    assert not (raw_dir / "tX.oxide.3.txt").exists()


class _ClientSideOverflowAfterOneAttempt:
    """Attempt 1 is a real, failing submission (evidence). The repair
    attempt raises the BASE ``ContextOverflowError`` -- the client's own
    ``check_context`` refusal -- not the server subclass. This is the
    exact granite8b failure mode: a repair prompt grown past attempt 1
    that overflows check_context's own estimate at a small per-family
    window (native 4096), AFTER a real submission already happened."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, seed: int) -> Generation:
        self.calls += 1
        if self.calls == 1:
            return Generation("this is not a program", 10, 5, 100, False)
        raise ContextOverflowError(
            "prompt is ~5097 tok and num_predict is 2048, which together "
            "exceed num_ctx 4096"
        )


def test_run_session_treats_client_side_overflow_as_a_result_with_evidence(tmp_path):
    # Quadrant: client-side raise (check_context), attempts >= 1 -- also
    # a RESULT under the evidence gate. This is the case an earlier,
    # type-based version of the rule got wrong: granite8b's native 4096
    # window means check_context itself (not just the server) routinely
    # rejects a grown repair prompt on a session that already has real
    # evidence, and that must not abort the whole run.
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    raw_dir = tmp_path / "raw"
    cell = run_session(
        _ClientSideOverflowAfterOneAttempt(),
        run_id="6a-test-0shot-s1",
        task_id="tX",
        arm="oxide",
        shots=0,
        results_root=tmp_path / "results",
        raw_dir=raw_dir,
        tasks_path=tasks,
    )
    assert set(cell.keys()) == _CELL_SCHEMA | {"context_exhausted"}
    assert cell["attempts"] == 1
    assert cell["context_exhausted"] is True
    assert cell["final_passed"] is False
    assert (raw_dir / "tX.oxide.1.txt").is_file()
    assert not (raw_dir / "tX.oxide.2.txt").exists()


def test_run_session_aborts_when_context_exhausts_before_any_submission(tmp_path):
    # Quadrant: server-side raise, attempts == 0 -- now an ABORT, not a
    # recorded result (this is the change from the previous, type-based
    # rule). Zero evidence is a configuration failure regardless of which
    # check caught it: at a small per-family window, an oversized INITIAL
    # prompt would otherwise repeat identically across every seed of a
    # (task, arm, shots) triple, fabricating a full grid of zero-attempt
    # "results" with no abort and no manifest cause -- exactly what
    # ContextOverflowError's ModelError inheritance exists to prevent.
    # (The client-side, attempts == 0 quadrant is covered by
    # test_context_overflow_aborts_the_run_id_and_records_it below.)
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    with pytest.raises(ServerContextOverflowError):
        run_session(
            _ContextExhaustsClient(fires_on_attempt=1),
            run_id="6a-test-0shot-s1",
            task_id="tX",
            arm="oxide",
            shots=0,
            results_root=tmp_path / "results",
            raw_dir=tmp_path / "raw",
            tasks_path=tasks,
        )


@pytest.mark.parametrize(
    "overflow_cls",
    [ContextOverflowError, ServerContextOverflowError],
    ids=["client_side", "server_side"],
)
def test_run_one_continues_to_the_next_session_past_context_exhaustion(
    tmp_path, overflow_cls
):
    # The RUN must continue past an exhausted session THAT HAS EVIDENCE
    # (attempts >= 1): no exception escapes run_session for that case, so
    # run_one's task x arm loop keeps going and every other cell still
    # gets recorded. The except block in run_session is type-agnostic, so
    # this is parametrized over BOTH raise sites -- the client's own
    # check_context refusal (base ContextOverflowError) and the server's
    # real-tokenizer rejection (ServerContextOverflowError) -- rather than
    # asserted structurally identical: this file's own history
    # (test_context_overflow_aborts_the_run_id_and_records_it's comment)
    # records that exactly a single-flavor, stubbed-run_one test going
    # vacuous is how a real gap went unremarked once before. The stub's
    # overflow fires on the REPAIR attempt of the very first session
    # processed (tA/oxide), after that session's own attempt 1 already
    # produced a real, failed submission.
    class _ExhaustsOnRepairThenArmAware:
        _PROGRAMS = {
            "rust": 'fn main() { println!("42"); }\n',
            "explicit": "fn main() {\n    print(42)\n}\n",
            "oxide": "fn main() {\n    print(42)\n}\n",
        }

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt: str, *, seed: int) -> Generation:
            self.calls += 1
            if self.calls == 1:
                return Generation("this is not a program", 10, 5, 100, False)
            if self.calls == 2:
                raise overflow_cls("prompt exceeds num_ctx")
            if harness.RUST_PREAMBLE in prompt:
                arm = "rust"
            elif "Oxide Explicit" in prompt:
                arm = "explicit"
            else:
                arm = "oxide"
            return Generation(self._PROGRAMS[arm], 10, 5, 100, False)

    tasks = [
        {"id": "tA", "prompt": "Print 42.", "expected_stdout": "42\n"},
        {"id": "tB", "prompt": "Print 42, again.", "expected_stdout": "42\n"},
    ]
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(
        "\n".join(json.dumps(task) for task in tasks) + "\n", encoding="utf-8"
    )
    results_root = tmp_path / "results"
    run_id = "6a-test-context-exhaustion-continues"
    client = _ExhaustsOnRepairThenArmAware()

    run_one(
        {arm: client for arm in harness.ARMS},
        run_id=run_id,
        shots=0,
        seed=1,
        results_root=results_root,
        tasks_path=tasks_path,
    )

    cells_path = results_root / run_id / "cells.jsonl"
    cells = [json.loads(line) for line in cells_path.read_text(encoding="utf-8").splitlines()]
    # Every task x arm pair still produced a cell: one exhausted session
    # (with real evidence) did not stop the loop or abort anything.
    assert len(cells) == len(tasks) * len(harness.ARMS)
    exhausted = [c for c in cells if c.get("context_exhausted")]
    assert len(exhausted) == 1
    assert exhausted[0]["attempts"] == 1  # real evidence, not zero


class _ArmAwareClient:
    """Returns a program valid for whichever arm's prompt it receives, so
    every session passes on its first attempt. This keeps rustc
    invocations across run_one's full task x arm grid to a bare minimum
    (one per session) instead of running each arm to the 4-attempt cap.
    """

    _PROGRAMS = {
        "rust": 'fn main() { println!("42"); }\n',
        "explicit": "fn main() {\n    print(42)\n}\n",
        "oxide": "fn main() {\n    print(42)\n}\n",
    }

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, seed: int) -> Generation:
        self.prompts.append(prompt)
        if harness.RUST_PREAMBLE in prompt:
            arm = "rust"
        elif "Oxide Explicit" in prompt:
            arm = "explicit"
        else:
            arm = "oxide"
        return Generation(self._PROGRAMS[arm], 10, 5, 100, False)


_CELL_SCHEMA = {
    "task",
    "arm",
    "attempts",
    "first_compiled",
    "first_passed",
    "final_passed",
    "attempts_to_pass",
    "tokens_in",
    "tokens_out",
    "ms",
    "contract_compliant",
    "truncated",
}


def test_run_one_writes_one_well_formed_cell_per_task_arm_pair(tmp_path):
    tasks = [
        {"id": "tA", "prompt": "Print 42.", "expected_stdout": "42\n"},
        {"id": "tB", "prompt": "Print 42, again.", "expected_stdout": "42\n"},
    ]
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(
        "\n".join(json.dumps(task) for task in tasks) + "\n", encoding="utf-8"
    )
    results_root = tmp_path / "results"
    run_id = "6a-test-run-one"
    client = _ArmAwareClient()

    run_one(
        {arm: client for arm in harness.ARMS},
        run_id=run_id,
        shots=0,
        seed=1,
        results_root=results_root,
        tasks_path=tasks_path,
    )

    cells_path = results_root / run_id / "cells.jsonl"
    lines = cells_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(tasks) * len(harness.ARMS)

    seen_tasks: set[str] = set()
    seen_arms: set[str] = set()
    for line in lines:
        cell = json.loads(line)
        assert set(cell.keys()) == _CELL_SCHEMA
        seen_tasks.add(cell["task"])
        seen_arms.add(cell["arm"])

    assert seen_tasks == {"tA", "tB"}
    assert seen_arms == set(harness.ARMS)

    raw_dir = results_root / run_id / "raw"
    assert raw_dir.is_dir()
    for task in tasks:
        for arm in harness.ARMS:
            assert (raw_dir / f"{task['id']}.{arm}.1.txt").is_file()


# ---------------------------------------------------- per-arm client routing
# Phase 6c: the llamacpp backend needs a distinct, per-arm grammar-bearing
# client (oxide/explicit constrained, rust never), while the ollama path must
# keep sharing exactly one client instance across all three arms -- an
# existing test (above) relies on that shared identity for prompt ordering.


def test_constrained_requires_llamacpp():
    with pytest.raises(ModelError):
        driver.make_arm_clients("ollama", "qwen7b", constrained=True,
                                host="http://localhost:8081")


def test_llamacpp_constrained_grammars_by_arm(monkeypatch):
    monkeypatch.setattr(driver, "_load_grammar", lambda arm: f"G::{arm}")
    clients = driver.make_arm_clients("llamacpp", "codegemma7b",
                                      constrained=True,
                                      host="http://localhost:8081")
    assert clients["oxide"].grammar == "G::oxide"
    assert clients["explicit"].grammar == "G::explicit"
    assert clients["rust"].grammar is None  # rustc is the control, never constrained


def test_ollama_backend_shares_one_client():
    clients = driver.make_arm_clients("ollama", "qwen7b", constrained=False,
                                      host="http://localhost:8081")
    assert clients["oxide"] is clients["rust"]  # unchanged legacy behavior


def test_make_arm_clients_pins_granite_to_its_native_4096_window():
    # granite-code:8b's OWN training context is 4096; llama-server caps
    # any larger slot request to that ceiling ("the slot context (8192)
    # exceeds the training context of the model (4096) -- capping"), so
    # the shared 8192 pin is physically unsatisfiable for this one model.
    # All three arms must share the SAME window -- arm-fair within the
    # family, per SPEC section 48.
    clients = driver.make_arm_clients("llamacpp", "granite8b",
                                      constrained=False,
                                      host="http://localhost:8081")
    assert {client.num_ctx for client in clients.values()} == {4096}


def test_make_arm_clients_pins_other_slugs_to_the_shared_default_window():
    for slug in ("qwen0_5b", "qwen1_5b", "qwen7b", "codegemma7b"):
        clients = driver.make_arm_clients("llamacpp", slug, constrained=False,
                                          host="http://localhost:8081")
        assert {client.num_ctx for client in clients.values()} == {8192}, slug


def test_make_arm_clients_threads_num_ctx_through_the_ollama_path_too():
    # OllamaClient takes a num_ctx constructor arg; pinned the same way
    # for consistency even though granite's actual G0 runs are
    # llamacpp-only (ollama is the legacy 6a path, all qwen, unaffected
    # in practice).
    granite = driver.make_arm_clients("ollama", "granite8b", constrained=False,
                                      host="http://localhost:8081")
    assert granite["rust"].num_ctx == 4096
    qwen = driver.make_arm_clients("ollama", "qwen7b", constrained=False,
                                   host="http://localhost:8081")
    assert qwen["rust"].num_ctx == 8192


def test_make_arm_clients_threads_the_quantization_pin_through_too():
    """SPEC section 48 amended quantization from a universal q8_0 to a
    per-family pin, but the amendment was inert: QUANT/quant_for were
    read by tests only, and the one place quantization is enforced still
    hard-coded Q8_0. `--models deepseek16b_lite` on the default backend
    therefore raised ModelError against a SPEC-registered subject. The
    pin must reach the client the same way num_ctx does."""
    deepseek = driver.make_arm_clients("ollama", "deepseek16b_lite",
                                       constrained=False,
                                       host="http://localhost:8081")
    assert {c.quantization for c in deepseek.values()} == {"q5_K_M"}
    for slug in ("qwen0_5b", "qwen1_5b", "qwen7b", "codegemma7b", "granite8b"):
        clients = driver.make_arm_clients("ollama", slug, constrained=False,
                                          host="http://localhost:8081")
        assert {c.quantization for c in clients.values()} == {"q8_0"}, slug


def test_registered_deepseek_subject_preflights_on_the_default_backend(
    monkeypatch,
):
    """The end-to-end failure scenario, not just its parts: build the
    clients the way the driver does for the default backend, then
    preflight. Before the fix this raised."""
    tag = MODELS["deepseek16b_lite"]
    tags = {
        "models": [
            {
                "name": tag,
                "digest": "6065d4880bf9",
                "details": {
                    "quantization_level": "Q5_K_M",
                    "context_length": 163840,
                },
            }
        ]
    }
    monkeypatch.setattr(
        "eval.models._request", _FakeHTTP(tags, {"version": "0.6.8"})
    )
    clients = driver.make_arm_clients("ollama", "deepseek16b_lite",
                                      constrained=False,
                                      host="http://localhost:8081")
    assert clients["oxide"].preflight()["quantization_level"] == "Q5_K_M"


def test_every_pinned_tag_agrees_with_its_quantization_pin():
    """MODELS and QUANT are two independent statements of the same fact.
    Nothing else stops them drifting: a slug re-pinned to a new tag while
    QUANT still names the old quantization would preflight-fail at run
    time, hours after the edit, on a machine with the GPU already busy."""
    for slug, tag in MODELS.items():
        assert tag.endswith("-" + driver.quant_for(slug)), (slug, tag)


def test_granite_preflight_passes_against_its_own_capped_4096_server(monkeypatch):
    # LlamaCppClient.preflight() already refuses when served n_ctx is
    # LESS than the client's own num_ctx pin -- verified here rather than
    # assumed. The boundary case matters: served == pin (both 4096, not
    # served < pin) must pass, not just "not obviously broken".
    clients = driver.make_arm_clients("llamacpp", "granite8b",
                                      constrained=False,
                                      host="http://localhost:8081")
    props = {
        "default_generation_settings": {"n_ctx": 4096},
        "model_path": "/blobs/sha256-granite8b",
        "build_info": "b1-granite",
    }
    monkeypatch.setattr("eval.llamacpp._request", _FakeHTTP(props))
    info = clients["rust"].preflight()
    assert info["server_n_ctx"] == 4096
    assert info["num_ctx"] == 4096


def test_grid_cell_routes_the_arm_to_its_client(tmp_path):
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    clients = {
        "oxide": _StubClient("fn main() {\n    print(42)\n}\n"),
        "explicit": _StubClient("fn main() {\n    print(42)\n}\n"),
        "rust": _StubClient('fn main() { println!("42"); }\n'),
    }
    driver.run_one(
        clients,
        run_id="g0c-test-0shot-s1",
        shots=0,
        seed=1,
        results_root=tmp_path,
        tasks_path=tasks,
    )
    # Every stub answered only its own arm. "Oxide" alone cannot
    # discriminate oxide from explicit -- LANGUAGE_CARD_EXPLICIT.md's own
    # title is "Oxide Explicit", so both cards contain "Oxide" and a
    # swapped assignment would pass a plain substring check. Assert the
    # marker unique to each card is present on its own client's prompts
    # and ABSENT from the other's, so a swap fails loudly.
    assert clients["oxide"].prompts and all(
        "Oxide" in p and "Oxide Explicit" not in p
        for p in clients["oxide"].prompts
    )
    assert clients["explicit"].prompts and all(
        "Oxide Explicit" in p for p in clients["explicit"].prompts
    )
    assert clients["rust"].prompts and all(
        "You are writing Rust" in p for p in clients["rust"].prompts
    )


class _StaleServerStub:
    """A ``.preflight()`` that reports a model_path NOT matching the one
    the caller expects -- the shape a llama-server left running from a
    previous slug (or restarted on the wrong weights) actually returns."""

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path

    def preflight(self) -> dict:
        return {"model_path": self._model_path, "build_info": "b1-test"}


def test_main_exits_2_when_expect_model_path_does_not_match(monkeypatch, capsys):
    # The headline safety feature: a stale llama-server serving the WRONG
    # weights must never run a single session. main() must catch this at
    # preflight, before run_grid touches anything.
    stub_clients = {
        arm: _StaleServerStub("/blobs/sha256-deadbeef0000")
        for arm in harness.ARMS
    }
    monkeypatch.setattr(
        driver, "make_arm_clients",
        lambda backend, slug, *, constrained, host: stub_clients,
    )
    code = driver.main([
        "--backend", "llamacpp",
        "--models", "qwen1_5b",
        "--shots", "0",
        "--expect-model-path", "sha256-24b532e5",
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "--expect-model-path" in err
    assert "sha256-24b532e5" in err
    assert "/blobs/sha256-deadbeef0000" in err


def test_main_proceeds_when_expect_model_path_matches(monkeypatch):
    # The inverse: a served model_path that DOES contain the expected
    # substring must not trip the guard. Preflight-only exits 0 without
    # ever reaching run_grid, so nothing beyond make_arm_clients/preflight
    # needs stubbing.
    stub_clients = {
        arm: _StaleServerStub("/blobs/sha256-24b532e52765abcd")
        for arm in harness.ARMS
    }
    monkeypatch.setattr(
        driver, "make_arm_clients",
        lambda backend, slug, *, constrained, host: stub_clients,
    )
    code = driver.main([
        "--backend", "llamacpp",
        "--models", "qwen1_5b",
        "--shots", "0",
        "--expect-model-path", "sha256-24b532e5",
        "--preflight-only",
    ])
    assert code == 0


def test_main_refuses_llamacpp_with_more_than_one_slug(capsys):
    # One llama-server instance serves ONE model: unlike Ollama, which
    # can hold multiple pulled tags and route by name per request,
    # llama-server is started on ONE set of weights, and every request
    # goes to whatever it currently has loaded. --models defaults to ALL
    # SIX slugs (MODELS), so the unguarded default would silently run
    # every slug's sessions against a single server's weights -- a full
    # grid of plausible-looking results attributed to the wrong models,
    # with no abort and no manifest cause. The guard must fire before ANY
    # client is constructed -- no monkeypatching needed; if it reached
    # make_arm_clients this test would try to hit a real server.
    code = driver.main([
        "--backend", "llamacpp",
        "--models", "qwen1_5b,qwen7b",
        "--shots", "0",
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "llamacpp" in err
    assert "qwen1_5b" in err and "qwen7b" in err


def test_main_allows_llamacpp_with_a_single_slug(monkeypatch):
    # The inverse: exactly one slug must not trip the guard. Stubbed
    # exactly like test_main_proceeds_when_expect_model_path_matches so
    # this stays hermetic -- --preflight-only stops before run_grid.
    stub_clients = {
        arm: _StaleServerStub("/blobs/sha256-anything") for arm in harness.ARMS
    }
    monkeypatch.setattr(
        driver, "make_arm_clients",
        lambda backend, slug, *, constrained, host: stub_clients,
    )
    code = driver.main([
        "--backend", "llamacpp",
        "--models", "qwen1_5b",
        "--shots", "0",
        "--preflight-only",
    ])
    assert code == 0


def test_main_allows_ollama_with_multiple_slugs(monkeypatch):
    # The guard is llamacpp-specific: Ollama can hold multiple pulled
    # tags and route by model name per request, so multiple slugs in one
    # invocation is the normal, supported (legacy 6a) path there.
    stub_clients = {
        arm: _StaleServerStub("/blobs/sha256-anything") for arm in harness.ARMS
    }
    monkeypatch.setattr(
        driver, "make_arm_clients",
        lambda backend, slug, *, constrained, host: stub_clients,
    )
    code = driver.main([
        "--models", "qwen1_5b,qwen7b",
        "--shots", "0",
        "--preflight-only",
    ])
    assert code == 0


def test_model_slugs_map_to_pinned_tags():
    assert MODELS == {
        "qwen0_5b": "qwen2.5-coder:0.5b-instruct-q8_0",
        "qwen1_5b": "qwen2.5-coder:1.5b-instruct-q8_0",
        "qwen7b": "qwen2.5-coder:7b-instruct-q8_0",
        "codegemma7b": "codegemma:7b-instruct-q8_0",
        "granite8b": "granite-code:8b-instruct-q8_0",
        "deepseek16b_lite": "deepseek-coder-v2:16b-lite-instruct-q5_K_M",
    }


def test_quantization_is_q8_except_where_vram_forbids_it():
    """SPEC section 48: quantization was uniform q8_0; it is now a
    per-family pin, for the same reason num_ctx is. DeepSeek-V2-Lite's
    q8_0 weights are 15926 MiB and its measured runtime overhead ~2160
    MiB, for ~18090 MiB against a 16303 MiB card -- physically forced,
    not a policy choice. (VRAM in MiB throughout: the earlier "16.70 GB
    vs 16.30 GB" mixed a decimal-GB GGUF size with a relabelled MiB
    figure, and in consistent units those weights fit the raw card.)"""
    assert driver.DEFAULT_QUANT == "q8_0"
    assert driver.QUANT == {"deepseek16b_lite": "q5_K_M"}
    for slug in ("qwen0_5b", "qwen1_5b", "qwen7b", "codegemma7b", "granite8b"):
        assert driver.quant_for(slug) == "q8_0"
    assert driver.quant_for("deepseek16b_lite") == "q5_K_M"


def test_deepseek_takes_the_default_context_window():
    """The OPPOSITE of granite: DeepSeek-V2 trains at 163840, so 8192 is
    satisfiable and no cap applies. llama-server prints an under-use
    notice, not the 'exceeds the training context ... capping' line."""
    assert "deepseek16b_lite" not in driver.NUM_CTX


def test_g0_model_slugs_are_pinned():
    assert driver.MODELS["codegemma7b"] == "codegemma:7b-instruct-q8_0"
    assert driver.MODELS["granite8b"] == "granite-code:8b-instruct-q8_0"


def test_build_run_id_matches_pinned_format():
    assert build_run_id("qwen1_5b", 0, 3) == "6a-qwen1_5b-0shot-s3"


def test_build_run_id_default_prefix_is_unchanged():
    assert driver.build_run_id("qwen7b", 0, 3) == "6a-qwen7b-0shot-s3"


def test_build_run_id_takes_a_prefix():
    assert driver.build_run_id("granite8b", 0, 7, prefix="g0c") == "g0c-granite8b-0shot-s7"


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
        lambda tag: {arm: _StubClient() for arm in harness.ARMS},
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
    )
    monkeypatch.undo()
    assert calls == [build_run_id("qwen1_5b", 0, 2)]
    assert result["completed"] == [build_run_id("qwen1_5b", 0, 2)]


def test_run_grid_honors_a_custom_run_prefix(tmp_path):
    calls: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        calls.append(run_id)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    result = run_grid(
        lambda tag: {arm: _StubClient() for arm in harness.ARMS},
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1],
        results_root=tmp_path,
        prefix="g0c",
    )
    monkeypatch.undo()
    assert calls == ["g0c-qwen1_5b-0shot-s1"]
    assert result["completed"] == ["g0c-qwen1_5b-0shot-s1"]


def test_run_grid_aborts_one_run_and_continues(tmp_path):
    seen: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        seen.append(run_id)
        if run_id.endswith("s1"):
            raise ModelError("transport down")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    result = run_grid(
        lambda tag: {arm: _StubClient() for arm in harness.ARMS},
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
    )
    monkeypatch.undo()
    assert len(seen) == 2  # did not stop at the failure
    assert result["aborted"] == [build_run_id("qwen1_5b", 0, 1)]
    assert result["completed"] == [build_run_id("qwen1_5b", 0, 2)]


def test_run_grid_aborts_one_run_on_harness_error_not_the_whole_grid(tmp_path):
    # A HarnessError (unreadable card, session-claim collision) is a
    # per-run environment fault. Letting it escape would end an unattended
    # multi-hour grid on a fault that costs one run id to retry.
    seen: list[str] = []

    def fake_run_one(client, *, run_id, **kwargs):
        seen.append(run_id)
        if run_id.endswith("s1"):
            raise harness.HarnessError("cannot read language card")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    result = run_grid(
        lambda tag: {arm: _StubClient() for arm in harness.ARMS},
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
    )
    monkeypatch.undo()
    assert len(seen) == 2  # did not stop at the failure
    assert result["aborted"] == [build_run_id("qwen1_5b", 0, 1)]
    assert result["completed"] == [build_run_id("qwen1_5b", 0, 2)]
    aborted_dir = tmp_path / build_run_id("qwen1_5b", 0, 1)
    manifest = json.loads((aborted_dir / "manifest.json").read_text())
    assert "cannot read language card" in manifest["aborted_reason"]


def test_run_grid_does_not_swallow_a_repair_prompt_error(tmp_path):
    # RepairPromptError means the frozen harness stopped ending prompts
    # with OUTPUT_CONTRACT, so EVERY later repair prompt would be
    # malformed. That must stop the grid loudly rather than abort one run.
    from eval.repair import RepairPromptError

    def fake_run_one(client, *, run_id, **kwargs):
        raise RepairPromptError("OUTPUT_CONTRACT missing from build_prompt")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", fake_run_one)
    with pytest.raises(RepairPromptError):
        run_grid(
            lambda tag: {arm: _StubClient() for arm in harness.ARMS},
            slugs=["qwen1_5b"],
            shot_counts=[0],
            seeds=[1, 2],
            results_root=tmp_path,
        )
    monkeypatch.undo()


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
            lambda tag: {arm: _StubClient() for arm in harness.ARMS},
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
        lambda tag: {arm: _StubClient() for arm in harness.ARMS},
        slugs=["qwen1_5b"],
        shot_counts=[0],
        seeds=[1, 2],
        results_root=tmp_path,
        health_check=lambda client: waits.append("checked"),
    )
    monkeypatch.undo()
    assert waits == ["checked", "checked"]


def _drive_one_cell(tmp_path, client, *, preflight=None, health_check=None,
                    seeds=(1,), fake_run_one=None, stub_run_one=True):
    """Walk one grid cell. By default ``run_one`` is stubbed out (no real
    generation) for speed, since most callers only care about manifest/
    abort bookkeeping. Pass ``stub_run_one=False`` to exercise the REAL
    ``run_one``/``run_session`` pipeline instead -- e.g. to prove an
    exception genuinely escapes them, not a stand-in for the code under
    test (review finding: a stubbed ``run_one`` cannot prove anything
    about what real code does or doesn't catch)."""
    monkeypatch = pytest.MonkeyPatch()
    if stub_run_one:
        monkeypatch.setattr(
            "eval.driver.run_one", fake_run_one or (lambda client, **kw: None)
        )
    try:
        return run_grid(
            lambda tag: {arm: client for arm in harness.ARMS},
            slugs=["qwen1_5b"],
            shot_counts=[0],
            seeds=list(seeds),
            results_root=tmp_path,
            preflight=preflight,
            health_check=health_check,
        )
    finally:
        monkeypatch.undo()


def _manifest(tmp_path, seed: int = 1) -> dict:
    path = tmp_path / build_run_id("qwen1_5b", 0, seed) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_records_the_client_s_real_sampling_params(tmp_path):
    # Non-default values are the point: the pinned defaults would pass
    # against a getattr fallback literal and prove nothing.
    client = OllamaClient(
        MODELS["qwen1_5b"],
        temperature=0.3,
        top_p=0.5,
        num_predict=99,
        num_ctx=1234,
    )
    _drive_one_cell(tmp_path, client)
    m = _manifest(tmp_path)
    assert (m["temperature"], m["top_p"], m["num_predict"]) == (0.3, 0.5, 99)
    # num_ctx is the window ACTUALLY USED. Recorded off the client, not
    # assumed, and distinct from model_context_length (the capability).
    assert m["num_ctx"] == 1234


def test_manifest_records_the_pinned_num_ctx_by_default(tmp_path):
    _drive_one_cell(tmp_path, OllamaClient(MODELS["qwen1_5b"]))
    assert _manifest(tmp_path)["num_ctx"] == 8192


def test_manifest_keeps_num_ctx_and_model_context_length_distinct(tmp_path):
    # The two numbers a reader could conflate: 32768 is what the weights
    # can do, 8192 is what the run did. Conflating them is how a
    # front-truncated Oxide prompt would look fine in the artifacts.
    info = {"context_length": 32768, "quantization_level": "Q8_0"}
    _drive_one_cell(
        tmp_path, OllamaClient(MODELS["qwen1_5b"]),
        preflight={"qwen1_5b": info},
    )
    m = _manifest(tmp_path)
    assert m["model_context_length"] == 32768
    assert m["num_ctx"] == 8192
    assert "context_length" not in m


def test_manifest_records_the_preflight_provenance_payload(tmp_path):
    # Section 48: "Exact tags AND digests are recorded in the run manifest
    # at preflight." The manifest is the only artifact proving which
    # weights produced a 14-hour result.
    info = {
        "model": MODELS["qwen1_5b"],
        "digest": "deadbeefcafe",
        "quantization_level": "Q8_0",
        "context_length": 32768,
        "ollama_version": "0.6.8",
    }
    _drive_one_cell(tmp_path, _StubClient(), preflight={"qwen1_5b": info})
    m = _manifest(tmp_path)
    assert m["digest"] == "deadbeefcafe"
    assert m["quantization_level"] == "Q8_0"
    # The model's CAPABILITY, under a name that cannot be mistaken for
    # num_ctx (the window actually used). Section 48 requires both.
    assert m["model_context_length"] == 32768
    assert m["ollama_version"] == "0.6.8"


def test_manifest_records_backend_and_per_arm_grammar_sha256(tmp_path):
    # Section 49: a constrained result cannot be traced without knowing
    # which grammar (and which backend) produced it. The rust arm is
    # never constrained, so its digest must read None even though the
    # other two arms carry one.
    oxide_client, explicit_client, rust_client = (
        _StubClient(), _StubClient(), _StubClient()
    )
    oxide_client.grammar = 'root ::= "oxide"'
    explicit_client.grammar = 'root ::= "explicit"'
    rust_client.grammar = None
    clients = {
        "oxide": oxide_client, "explicit": explicit_client, "rust": rust_client,
    }
    # info["grammar_sha256"] is what LlamaCppClient.preflight() actually
    # returns in production: the digest of whichever client preflight was
    # run against, which is always clients["rust"] -- never constrained,
    # so always None in practice. Embedded verbatim it would sit right
    # next to the correctly-populated per-arm field below and read as
    # "unconstrained" even on a constrained run (review finding 6).
    info = {
        "backend": "llama.cpp", "model_path": "/blobs/sha256-deadbeef",
        "build_info": "b1-abc", "grammar_sha256": None,
    }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("eval.driver.run_one", lambda clients, **kw: None)
    try:
        run_grid(
            lambda tag: clients,
            slugs=["qwen1_5b"],
            shot_counts=[0],
            seeds=[1],
            results_root=tmp_path,
            preflight={"qwen1_5b": info},
            backend="llamacpp",
        )
    finally:
        monkeypatch.undo()

    m = _manifest(tmp_path)
    # The CLI token "llamacpp" is normalized to the same spelling
    # LlamaCppClient.preflight() reports, so the two fields can never
    # drift apart (review finding 5).
    assert m["backend"] == "llama.cpp"
    assert m["backend"] == m["preflight"]["backend"]
    # The embedded preflight payload's OWN grammar_sha256 is dropped --
    # the top-level per-arm field (below) is the one place this run's
    # grammar provenance lives (review finding 6).
    assert "grammar_sha256" not in m["preflight"]
    assert m["preflight"] == {
        "backend": "llama.cpp", "model_path": "/blobs/sha256-deadbeef",
        "build_info": "b1-abc",
    }
    assert m["grammar_sha256"] == {
        "oxide": grammar_digest('root ::= "oxide"'),
        "explicit": grammar_digest('root ::= "explicit"'),
        "rust": None,
    }


def test_manifest_defaults_backend_to_ollama_and_preflight_to_none(tmp_path):
    _drive_one_cell(tmp_path, _StubClient())
    m = _manifest(tmp_path)
    assert m["backend"] == "ollama"
    assert m["preflight"] is None
    assert m["grammar_sha256"] == {"oxide": None, "explicit": None, "rust": None}


def test_manifest_records_start_and_end_timestamps(tmp_path):
    _drive_one_cell(tmp_path, _StubClient())
    m = _manifest(tmp_path)
    # Section 49: the manifest carries "start/end".
    assert datetime.fromisoformat(m["started_at"]).tzinfo is not None
    assert datetime.fromisoformat(m["ended_at"]) >= datetime.fromisoformat(
        m["started_at"]
    )


def test_manifest_records_null_not_a_guess_for_unknown_client_params(tmp_path):
    # run_grid takes any ModelClient and the Protocol declares only
    # generate. An API-backed client carrying max_tokens instead of
    # num_predict must not have "2048" recorded against it with total
    # confidence: null is an honest "unknown", 0.8 is a lie.
    _drive_one_cell(tmp_path, _StubClient())
    m = _manifest(tmp_path)
    assert m["temperature"] is None
    assert m["top_p"] is None
    assert m["num_predict"] is None
    assert m["num_ctx"] is None
    assert m["digest"] is None


def test_context_overflow_aborts_the_run_id_and_records_it(tmp_path):
    # End to end, through the REAL run_session/run_one pipeline (not a
    # stub of the code under test -- a stubbed run_one cannot prove
    # anything about what run_session does or doesn't catch, which is
    # exactly how this case going vacuous went unremarked once). Quadrant:
    # client-side raise (check_context), attempts == 0 -- the evidence
    # gate (SPEC section 45/51) still aborts here regardless of which
    # check fired, because there is no session evidence yet to lose. The
    # other three quadrants are covered elsewhere:
    # test_run_session_treats_client_side_overflow_as_a_result_with_evidence
    # (client-side, attempts >= 1),
    # test_run_session_ends_at_context_exhaustion_with_evidence_so_far
    # (server-side, attempts >= 1), and
    # test_run_session_aborts_when_context_exhausts_before_any_submission
    # (server-side, attempts == 0).
    class _OverflowingClient:
        def generate(self, prompt: str, *, seed: int) -> Generation:
            raise ContextOverflowError("prompt exceeds num_ctx 8192")

    result = _drive_one_cell(
        tmp_path, _OverflowingClient(), stub_run_one=False
    )
    assert result["completed"] == []
    assert result["aborted"] == [build_run_id("qwen1_5b", 0, 1)]
    assert "num_ctx" in _manifest(tmp_path)["aborted_reason"]


def test_estimate_tokens_rounds_up():
    # Rounding down would let a prompt sit exactly on the boundary and
    # still overflow. Crude is fine; optimistic is not.
    assert estimate_tokens("") == 0
    assert estimate_tokens("x") == 1
    assert estimate_tokens("x" * 4) == 1
    assert estimate_tokens("x" * 5) == 2


def test_health_check_timeout_aborts_the_run_id_and_records_it(tmp_path):
    # Section 51: a transport-class failure aborts THIS run id, records
    # the cause in THAT run's manifest, and the driver proceeds. Raised
    # outside the try it would lose the grid dict entirely, count nothing
    # as aborted, and write no manifest.
    def timed_out(client: object) -> None:
        raise ModelError("ollama did not become healthy within 600s")

    result = _drive_one_cell(
        tmp_path, _StubClient(), health_check=timed_out, seeds=(1, 2)
    )
    assert result["aborted"] == [
        build_run_id("qwen1_5b", 0, 1),
        build_run_id("qwen1_5b", 0, 2),
    ]
    assert result["completed"] == []
    assert "healthy" in _manifest(tmp_path)["aborted_reason"]


def test_three_health_check_timeouts_hit_the_consecutive_abort_backstop(tmp_path):
    def timed_out(client: object) -> None:
        raise ModelError("ollama did not become healthy within 600s")

    with pytest.raises(RuntimeError, match="consecutive"):
        _drive_one_cell(
            tmp_path, _StubClient(), health_check=timed_out, seeds=(1, 2, 3)
        )


def test_completed_run_manifest_carries_no_abort_reason(tmp_path):
    _drive_one_cell(tmp_path, _StubClient())
    assert _manifest(tmp_path)["aborted_reason"] is None


def test_preflight_environment_passes_on_the_real_corpus_and_shots():
    problems = [p for p in preflight_environment([0, 3]) if "rustc" not in p]
    assert problems == []


def test_preflight_reports_an_uninvocable_rustc(monkeypatch):
    # rustc_adapter never raises: it returns a fallback diagnostic that
    # lands in cells.jsonl as first_compiled=false, i.e. infrastructure
    # recorded as a model failure. Preflight is where that gets caught.
    monkeypatch.setattr(
        "eval.driver.rustc_adapter.find_rustc", lambda: "/nonexistent/rustc"
    )
    problems = preflight_environment([0])
    assert any("rustc is not invocable" in p for p in problems)


def test_preflight_reports_a_failing_rustc(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 101, b"", b"boom")

    monkeypatch.setattr("eval.driver.subprocess.run", fake_run)
    assert any("rustc exited 101" in p for p in preflight_environment([0]))


def test_preflight_reports_a_corpus_that_does_not_load(tmp_path):
    problems = preflight_environment([0], tasks_path=tmp_path / "gone.jsonl")
    assert any("corpus" in p for p in problems)


def test_preflight_reports_a_corpus_size_decoupled_from_sessions_per_run(tmp_path):
    # SESSIONS_PER_RUN=60 is what is_complete judges a run against. If the
    # corpus ever changes size, every run would be mis-judged complete.
    tasks_path = tmp_path / "tasks.jsonl"
    tasks_path.write_text(
        json.dumps({"id": "tA", "prompt": "p", "expected_stdout": "1\n"}) + "\n",
        encoding="utf-8",
    )
    problems = preflight_environment([0], tasks_path=tasks_path)
    assert any("SESSIONS_PER_RUN" in p for p in problems)


def test_preflight_reports_arms_short_of_shots_for_the_3shot_condition(monkeypatch):
    monkeypatch.setattr("eval.driver.harness.load_shots", lambda arm: [("t", "s")])
    problems = preflight_environment([0, 3])
    assert sorted(p for p in problems if "shot(s)" in p) == [
        f"arm '{arm}' has 1 shot(s), needs 3" for arm in sorted(harness.ARMS)
    ]


def test_preflight_skips_the_shot_check_when_no_shot_condition_needs_it(monkeypatch):
    monkeypatch.setattr("eval.driver.harness.load_shots", lambda arm: [])
    assert not any("shot(s)" in p for p in preflight_environment([0]))


def test_parse_seeds_accepts_ranges_and_lists():
    assert parse_seeds("1-5") == [1, 2, 3, 4, 5]
    assert parse_seeds("2,4") == [2, 4]
    assert parse_seeds("3") == [3]


class _AlwaysHealthy:
    def healthy(self) -> bool:
        return True


class _HealthyAfterProbes:
    """Reports unhealthy for the scripted results, then healthy."""

    def __init__(self, *results: bool) -> None:
        self.results = list(results)
        self.calls = 0

    def healthy(self) -> bool:
        self.calls += 1
        return self.results.pop(0)


class _NeverHealthy:
    def healthy(self) -> bool:
        return False


def test_wait_for_health_returns_immediately_when_already_healthy():
    sleeps: list[float] = []
    wait_for_health(_AlwaysHealthy(), sleep=sleeps.append)
    assert sleeps == []


def test_wait_for_health_polls_then_returns_once_healthy():
    sleeps: list[float] = []
    client = _HealthyAfterProbes(False, False, True)
    wait_for_health(client, sleep=sleeps.append)
    assert client.calls == 3
    assert sleeps == [5, 5]


def test_wait_for_health_raises_after_cap_exhausted_without_looping_forever():
    # Pins the exact poll-interval/cap arithmetic: 600s cap / 5s interval
    # is 120 probes, never more -- this is the backstop that keeps an
    # overnight run from stalling forever on a dead daemon.
    sleeps: list[float] = []
    with pytest.raises(ModelError, match="600s"):
        wait_for_health(_NeverHealthy(), sleep=sleeps.append)
    assert sleeps == [5] * 120


def _arms(oxide_rate: float, explicit_rate: float, n: int = 20) -> dict:
    return {
        "oxide": {"n": n, "first_pass_rate": oxide_rate},
        "explicit": {"n": n, "first_pass_rate": explicit_rate},
        "rust": {"n": n, "first_pass_rate": 60.0},
    }


def test_verdict_refuses_a_reading_when_both_arms_are_at_the_floor():
    # The 6a pilot's real numbers: 7B 0-shot scored oxide 2/20 vs
    # explicit 0/20 first-compile. That is a +10pp delta, which clears
    # the pre-registered +5pp band and would have printed "supports" off
    # two programs. The band was derived at p~=0.5 and has no resolution
    # here, so the point must carry no reading.
    assert classify(10.0) == "supports"          # the partition alone
    assert _verdict(10.0, _arms(10.0, 0.0)) == AT_FLOOR


def test_verdict_refuses_a_reading_when_both_arms_are_at_the_ceiling():
    # The mirror case, and the shape run 1 actually produced: everything
    # saturated, so a delta between two near-perfect arms is noise.
    assert _verdict(-10.0, _arms(95.0, 100.0)) == AT_CEILING


def test_verdict_classifies_normally_away_from_the_extremes():
    assert _verdict(12.0, _arms(50.0, 38.0)) == "supports"
    assert _verdict(-12.0, _arms(38.0, 50.0)) == "disconfirms"
    assert _verdict(1.0, _arms(50.0, 49.0)) == "no-detectable-difference"


def test_verdict_floor_guard_needs_BOTH_arms_low():
    # One arm at the floor and the other genuinely working is a real
    # signal, not a resolution failure — it must still be classified.
    assert _verdict(40.0, _arms(45.0, 5.0)) == "supports"


def test_insufficient_data_still_takes_precedence_over_the_floor_guard():
    assert _verdict(None, _arms(0.0, 0.0)) == INSUFFICIENT
    empty = {"oxide": {"n": 0}, "explicit": {"n": 0}, "rust": {"n": 0}}
    assert _verdict(0.0, empty) == INSUFFICIENT


def _cell(task: str, arm: str, passed: bool, *, final: bool | None = None,
          compiled: bool | None = None) -> dict:
    return {
        "task": task, "arm": arm, "attempts": 1,
        "first_compiled": passed if compiled is None else compiled,
        "first_passed": passed,
        "final_passed": passed if final is None else final,
        "attempts_to_pass": 1 if passed else 5,
        "tokens_in": 10, "tokens_out": 5, "ms": 100,
        "contract_compliant": [True], "truncated": [False],
    }


def test_rollup_fixture_matches_the_drivers_real_cell_schema():
    # Binds this fixture to the schema run_one actually writes, so a
    # driver-side field change cannot leave the rollup tests passing
    # against a stale record shape.
    assert set(_cell("t", "oxide", True)) == _CELL_SCHEMA


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


def test_paired_delta_is_none_on_an_empty_pairing():
    # Emptiness must propagate, not be laundered into 0.0 -- which
    # classify() then reads as a pre-registered "no-detectable-difference".
    assert paired_delta([], []) is None
    assert paired_delta([_cell("t01", "oxide", True)], []) is None


def test_paired_se_is_none_on_an_empty_pairing():
    assert paired_se([], []) is None


def test_across_seed_se_measures_seed_to_seed_spread():
    # Section 47 requires it alongside the paired SE: different question,
    # different denominator.
    steady = [[_cell("t01", "oxide", True)] for _ in range(5)]
    assert across_seed_se(steady) == 0.0
    mixed = [[_cell("t01", "oxide", i % 2 == 0)] for i in range(4)]
    assert across_seed_se(mixed) > 0.0
    assert across_seed_se([]) is None
    assert across_seed_se([[]]) is None


def test_diagnostic_histogram_counts_codes_per_arm():
    triples = [
        {"arm": "oxide", "diagnostics": [{"code": "OX0400"}, {"code": "OX0401"}]},
        {"arm": "oxide", "diagnostics": [{"code": "OX0400"}]},
        {"arm": "rust", "diagnostics": [{"code": "E0382"}]},
        {"arm": "rust", "diagnostics": []},
    ]
    hist = diagnostic_histogram(triples)
    assert hist["oxide"] == {"OX0400": 2, "OX0401": 1}
    assert hist["rust"] == {"E0382": 1}


def _write_run(root, slug, shots, seed, cells, triples=(), prefix="6a") -> None:
    run_dir = root / build_run_id(slug, shots, seed, prefix=prefix)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cells.jsonl").write_text(
        "".join(json.dumps(c, sort_keys=True) + "\n" for c in cells),
        encoding="utf-8",
    )
    if triples:
        (run_dir / "triples.jsonl").write_text(
            "".join(json.dumps(t, sort_keys=True) + "\n" for t in triples),
            encoding="utf-8",
        )


def _full_run(**overrides) -> list[dict]:
    """60 cells: the pinned 20 tasks x 3 arms, all failing by default."""
    return [
        _cell(f"t{i:02d}", arm, False, **overrides)
        for i in range(1, 21)
        for arm in harness.ARMS
    ]


def _one_point(tmp_path, cells, triples=()) -> dict:
    _write_run(tmp_path, "qwen1_5b", 0, 1, cells, triples)
    grid = aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1])
    return grid["points"][0]


def test_aggregate_honors_a_custom_run_prefix(tmp_path):
    _write_run(tmp_path, "qwen1_5b", 0, 1, _full_run(compiled=True, final=True),
               prefix="g0u")
    grid = aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1],
                      prefix="g0u")
    assert grid["missing"] == []
    assert grid["points"][0]["model_slug"] == "qwen1_5b"


def test_aggregate_reports_first_compile_rate(tmp_path):
    # At 0.5B pass@1 saturates at zero long before compile rate does, so
    # this is the only metric that can show whether the arms differ there.
    cells = _full_run(compiled=True)
    point = _one_point(tmp_path, cells)
    assert point["arms"]["oxide"]["first_pass_rate"] == 0.0
    assert point["arms"]["oxide"]["first_compile_rate"] == 100.0


def test_aggregate_reports_repair_lift(tmp_path):
    point = _one_point(tmp_path, _full_run(final=True))
    oxide = point["arms"]["oxide"]
    assert (oxide["first_pass_rate"], oxide["final_pass_rate"]) == (0.0, 100.0)
    assert oxide["repair_lift_pp"] == 100.0


def test_aggregate_reports_across_seed_se_per_arm(tmp_path):
    for seed, passing in ((1, True), (2, False)):
        _write_run(
            tmp_path, "qwen1_5b", 0, seed,
            [_cell(f"t{i:02d}", arm, passing)
             for i in range(1, 21) for arm in harness.ARMS],
        )
    grid = aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1, 2])
    assert grid["points"][0]["arms"]["oxide"]["across_seed_se_pp"] == 50.0


def test_aggregate_reports_per_task_pass_counts(tmp_path):
    cells = [
        _cell(f"t{i:02d}", arm, arm == "rust" and i <= 5)
        for i in range(1, 21)
        for arm in harness.ARMS
    ]
    per_task = _one_point(tmp_path, cells)["arms"]["rust"]["per_task"]
    assert per_task["t01"] == {"trials": 1, "first_passed": 1, "final_passed": 1}
    assert per_task["t20"]["first_passed"] == 0
    assert len(per_task) == 20


def test_aggregate_reports_prompt_tokens_and_wall_clock(tmp_path):
    oxide = _one_point(tmp_path, _full_run())["arms"]["oxide"]
    assert oxide["tokens_in"] == 20 * 10  # collected in cells, was dropped
    assert oxide["ms"] == 20 * 100
    assert oxide["mean_tokens_in"] == 10.0
    assert oxide["mean_ms"] == 100.0


def test_aggregate_builds_the_diagnostic_histogram_from_triples(tmp_path):
    # Section 50.5 calls the per-code histogram the v0.3 gate deliverable.
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}], "compiled": False,
         "passed": False},
        {"task": "t02", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}, {"code": "OX0101"}],
         "compiled": False, "passed": False},
    ]
    point = _one_point(tmp_path, _full_run(), triples)
    assert point["diagnostics"]["oxide"] == {"OX0400": 2, "OX0101": 1}


def test_aggregate_covers_every_harness_arm(tmp_path):
    point = _one_point(tmp_path, _full_run())
    assert set(point["arms"]) == set(harness.ARMS)


def test_aggregate_refuses_incomplete_grid_without_partial(tmp_path):
    with pytest.raises(RuntimeError, match="incomplete"):
        aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1])


def test_aggregate_reports_missing_runs_with_partial(tmp_path):
    grid = aggregate(
        tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1], partial=True
    )
    assert grid["missing"] == ["6a-qwen1_5b-0shot-s1"]


def test_a_point_with_no_data_is_not_a_completed_null_result(tmp_path):
    # Verified failure before the fix: paired_delta([], []) -> 0.0,
    # classify(0.0) -> "no-detectable-difference", and an {"n": 0} arm
    # printed 0% -- a row asserting a pre-registered verdict on zero
    # observations, indistinguishable from a genuine 0% pass rate.
    grid = aggregate(
        tmp_path, slugs=["qwen7b"], shot_counts=[0], seeds=[1], partial=True
    )
    point = grid["points"][0]
    assert point["verdict"] == INSUFFICIENT
    assert point["paired_delta_pp"] is None
    assert point["paired_se_pp"] is None
    assert point["arms"]["oxide"]["n"] == 0


def test_render_report_dashes_an_empty_point_instead_of_printing_zeros():
    grid = {
        "missing": ["6a-qwen7b-0shot-s1"],
        "points": [
            {
                "model_slug": "qwen7b", "shots": 0,
                "paired_delta_pp": None, "paired_se_pp": None,
                "unpaired_se_pp": 0.0, "verdict": INSUFFICIENT,
                "arms": {arm: {"n": 0} for arm in harness.ARMS},
                "diagnostics": {},
            }
        ],
    }
    row = [ln for ln in render_report(grid).splitlines()
           if ln.startswith("| qwen7b | 0 |")][0]
    assert "0%" not in row
    assert "+0.0" not in row
    assert row.count("—") == 5  # delta, SE, and all three arm rates


def test_render_report_surfaces_the_new_metrics(tmp_path):
    triples = [{"task": "t01", "arm": "oxide", "attempt": 1,
                "diagnostics": [{"code": "OX0400"}], "compiled": False,
                "passed": False}]
    _write_run(tmp_path, "qwen1_5b", 0, 1, _full_run(compiled=True, final=True),
               triples)
    grid = aggregate(tmp_path, slugs=["qwen1_5b"], shot_counts=[0], seeds=[1])
    out = render_report(grid)
    assert "first-compile" in out
    assert "v0.3 gate deliverable" in out
    assert "OX0400" in out
    assert "Per-task first-attempt passes" in out
    assert "seed SE" in out
    assert "repair lift" in out
    # The pre-existing primary presentation is untouched.
    assert "Paired Δ (pp)" in out
    assert "±5pp" in out


def test_rollup_rejects_an_unknown_model_slug(tmp_path, capsys):
    code = rollup.main(["--models", "qwen3b", "--results-root", str(tmp_path)])
    assert code == 2
    assert "unknown model slug" in capsys.readouterr().err


def test_rollup_cli_threads_the_run_prefix(tmp_path):
    _write_run(tmp_path, "qwen1_5b", 0, 1, _full_run(compiled=True, final=True),
               prefix="g0c")
    code = rollup.main([
        "--models", "qwen1_5b",
        "--shots", "0",
        "--seeds", "1",
        "--run-prefix", "g0c",
        "--results-root", str(tmp_path),
    ])
    assert code == 0
    grid = json.loads((tmp_path / "6a-rollup" / "grid.json").read_text())
    assert grid["missing"] == []


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


@pytest.mark.live
def test_live_smoke_one_task_on_smallest_model(tmp_path):
    """One real generation end to end, against the real transpiler and
    rustc. Asserts the plumbing works, NOT that the model succeeds -- a
    0.5B failure is a valid experimental result (section 47)."""
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


# ------------------------------------------------------- grammar-constrained
# Phase 6b: GBNF grammars + the llama.cpp client that enforces them.
# Imports are local to this section so the block stays purely additive.

import collections
import random

from eval.grammar import build as grammar_build
from eval.llamacpp import (
    GRAMMAR_DIR,
    LlamaCppClient,
    grammar_digest,
    load_grammar,
)
from src.explicit.lexer import ExplicitLexer
from src.explicit.parser import ExplicitParser
from src.lexer.lexer import Lexer
from src.lexer.tokens import KEYWORDS
from src.parser.parser import _MAX_DEPTH, Parser

# The property under test: nothing the grammar can emit reaches the lexer's
# "unexpected character" or any parser diagnostic. Semantic codes
# (OX02xx/OX03xx/OX04xx) are the signal the whole exercise exists to reach
# and must never fail a soundness assertion.
def _is_syntax_code(code: str) -> bool:
    return code == "OX0001" or code.startswith("OX01")


def _depth_probing(parser_cls):
    """A parser subclass that records the deepest recursion it reached."""

    class _Probe(parser_cls):
        def __init__(self, tokens) -> None:
            super().__init__(tokens)
            self.max_depth: int = 0

        def _enter_nested(self) -> None:
            super()._enter_nested()
            self.max_depth = max(self.max_depth, self._depth)

    return _Probe


def _front_end(source: str, *, explicit: bool = False) -> tuple[list, int]:
    """Lex + parse ``source`` with the real front end for the given dialect;
    returns (diagnostics, max parser depth)."""
    lexer_cls = ExplicitLexer if explicit else Lexer
    parser_cls = _depth_probing(ExplicitParser if explicit else Parser)
    lexer = lexer_cls(source)
    tokens = lexer.tokenize()
    parser = parser_cls(tokens)
    parser.parse_module()
    return [*lexer.diagnostics, *parser.diagnostics], parser.max_depth


def _sampled_programs(count: int, *, explicit: bool = False) -> list[str]:
    rules = grammar_build.build_grammar(explicit=explicit)
    rng = random.Random(20260807)
    return [
        grammar_build.sample(rules, rng, budget=rng.choice([150, 700, 3000]))
        for _ in range(count)
    ]


def test_committed_grammar_matches_the_generator():
    """The .gbnf files are build artefacts; drift would mean the tested
    grammar and the served grammar are different objects."""
    assert grammar_build.render(explicit=False) == (
        GRAMMAR_DIR / "oxide.gbnf"
    ).read_text(encoding="utf-8")
    assert grammar_build.render(explicit=True) == (
        GRAMMAR_DIR / "explicit.gbnf"
    ).read_text(encoding="utf-8")


def test_sampled_oxide_programs_never_produce_a_syntax_diagnostic():
    offenders = []
    for source in _sampled_programs(400):
        diagnostics, _ = _front_end(source)
        offenders += [
            (d.code, d.message, source)
            for d in diagnostics
            if _is_syntax_code(d.code)
        ]
    assert offenders == [], offenders[:1]


def test_sampled_explicit_programs_never_produce_a_syntax_diagnostic():
    offenders = []
    for source in _sampled_programs(400, explicit=True):
        diagnostics, _ = _front_end(source, explicit=True)
        offenders += [
            (d.code, d.message, source)
            for d in diagnostics
            if _is_syntax_code(d.code)
        ]
    assert offenders == [], offenders[:1]


def test_sampled_programs_stay_under_the_parser_depth_guard():
    deepest = max(_front_end(src)[1] for src in _sampled_programs(400))
    assert deepest < _MAX_DEPTH


def test_worst_case_nesting_stays_under_the_parser_depth_guard():
    """The bound the tier count buys, exercised rather than argued.

    A full precedence cascade at every tier is the deepest shape the
    grammar admits; the unbounded recursive grammar this replaces would
    trip OX0101 'nesting too deep' here instead.
    """
    inner = "a || a && a == a + a * -a"
    for _ in range(grammar_build.TIERS - 1):
        inner = f"a || a && a == a + a * -({inner})"
    diagnostics, depth = _front_end("fn main() {\n    let z = " + inner + "\n}\n")
    assert [d.code for d in diagnostics] == []
    assert depth < _MAX_DEPTH


def test_sampled_programs_always_declare_main():
    assert all("fn main() {" in src for src in _sampled_programs(60))


def _end_positions(rules: dict, node, text: str, pos: int) -> set[int]:
    """Every position ``node`` can consume ``text`` up to, starting at ``pos``.

    A tiny backtracking recognizer over the same IR the GBNF is rendered
    from. Sampling can only ever show that keywords are *unlikely*; this
    shows the identifier rule cannot derive them at all.
    """
    if isinstance(node, grammar_build.Lit):
        return {pos + len(node.text)} if text.startswith(node.text, pos) else set()
    if isinstance(node, grammar_build.Chars):
        inside = pos < len(text) and any(
            lo <= text[pos] <= hi for lo, hi in node.ranges
        )
        return {pos + 1} if inside else set()
    if isinstance(node, grammar_build.Ref):
        return _end_positions(rules, rules[node.name], text, pos)
    if isinstance(node, grammar_build.Seq):
        positions = {pos}
        for item in node.items:
            positions = {
                nxt
                for cur in positions
                for nxt in _end_positions(rules, item, text, cur)
            }
            if not positions:
                return set()
        return positions
    if isinstance(node, grammar_build.Alt):
        found: set[int] = set()
        for option in node.options:
            found |= _end_positions(rules, option, text, pos)
        return found
    if isinstance(node, grammar_build.Rep):
        found = set() if node.op == "+" else {pos}
        frontier = {pos}
        for _ in range(len(text) - pos + 1):
            frontier = {
                nxt
                for cur in frontier
                for nxt in _end_positions(rules, node.node, text, cur)
                if nxt > cur
            }
            if not frontier:
                break
            found |= frontier
            if node.op == "?":
                break
        return found
    raise TypeError(node)


def _derives(rules: list, text: str, root: str = "lname") -> bool:
    table = dict(rules)
    return len(text) in _end_positions(table, grammar_build.Ref(root), text, 0)


def test_identifier_rule_cannot_derive_any_keyword():
    """GBNF has no negative lookahead, so keyword exclusion is structural;
    an identifier that lexed as a keyword would be a parse error."""
    rules = grammar_build.build_grammar()
    assert [kw for kw in KEYWORDS if _derives(rules, kw)] == []


def test_identifier_rule_still_derives_ordinary_names():
    """The complement must exclude the keywords and nothing else: prefixes,
    extensions and near-misses all stay legal identifiers."""
    rules = grammar_build.build_grammar()
    legal = (
        "x", "_", "i", "l", "le", "lets", "letter", "iffy", "inn", "forx",
        "fn2", "matched", "structs", "count", "first", "is_prime", "sum",
        "breaks", "continued", "elsewhere", "returns", "truey", "whilst",
    )
    assert [name for name in legal if not _derives(rules, name)] == []


def test_explicit_grammar_excludes_the_dialect_keyword():
    # `drop` is a keyword only in the dialect (SPEC section 41), so the two
    # grammars must disagree about it -- and only about it.
    assert _derives(grammar_build.build_grammar(), "drop")
    assert not _derives(grammar_build.build_grammar(explicit=True), "drop")


def test_sampled_identifiers_are_never_keywords():
    rules = grammar_build.build_grammar()
    rng = random.Random(4)
    names = {
        grammar_build.sample(rules, rng, budget=12, root="lname")
        for _ in range(3000)
    }
    assert names.isdisjoint(KEYWORDS)
    assert len(names) > 500  # the sampler really is exploring the trie


def test_load_grammar_reads_the_committed_file():
    assert load_grammar("oxide").startswith("# GENERATED")
    assert "root ::=" in load_grammar("explicit")


def test_load_grammar_refuses_an_arm_that_has_none():
    # Not a ModelError: constraining the rust arm would change the control.
    with pytest.raises(ValueError):
        load_grammar("rust")


def test_grammar_digest_is_stable_and_optional():
    assert grammar_digest(None) is None
    assert grammar_digest("root ::= \"a\"") == grammar_digest("root ::= \"a\"")
    assert grammar_digest("a") != grammar_digest("b")


# -------------------------------------------------------- llama.cpp client


def _oai_response(
    content: str = "ok",
    finish_reason: str = "stop",
    **overrides: object,
) -> dict:
    body: dict = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 41, "completion_tokens": 17},
        "timings": {"prompt_ms": 52.1, "predicted_ms": 1099.4},
    }
    body.update(overrides)
    return body


def _llama(monkeypatch, http: _FakeHTTP, **kwargs) -> LlamaCppClient:
    monkeypatch.setattr("eval.llamacpp._request", http)
    kwargs.setdefault("sleep", lambda _s: None)
    return LlamaCppClient("qwen0_5b", **kwargs)


def test_llamacpp_generate_returns_populated_generation(monkeypatch):
    http = _FakeHTTP(_oai_response("hello"))
    gen = _llama(monkeypatch, http).generate("hi", seed=3)
    assert gen == Generation(
        text="hello", tokens_in=41, tokens_out=17, ms=1151, truncated=False
    )


def test_llamacpp_generate_sends_the_pinned_sampling_options(monkeypatch):
    http = _FakeHTTP(_oai_response())
    _llama(monkeypatch, http).generate("hi", seed=4)
    url, payload = http.calls[0]
    assert url.endswith("/v1/chat/completions")
    assert payload["temperature"] == 0.8
    assert payload["top_p"] == 0.95
    assert payload["seed"] == 4
    assert payload["max_tokens"] == 2048
    assert payload["stream"] is False


def test_llamacpp_generate_attaches_the_grammar(monkeypatch):
    http = _FakeHTTP(_oai_response())
    client = _llama(monkeypatch, http, grammar='root ::= "PURPLE"')
    client.generate("hi", seed=1)
    assert http.calls[0][1]["grammar"] == 'root ::= "PURPLE"'


def test_llamacpp_generate_omits_grammar_when_unconstrained(monkeypatch):
    http = _FakeHTTP(_oai_response())
    _llama(monkeypatch, http).generate("hi", seed=1)
    assert "grammar" not in http.calls[0][1]


def test_llamacpp_marks_length_stop_as_truncated(monkeypatch):
    http = _FakeHTTP(_oai_response(finish_reason="length"))
    assert _llama(monkeypatch, http).generate("hi", seed=1).truncated is True


def test_llamacpp_retries_then_succeeds(monkeypatch):
    http = _FakeHTTP(urllib.error.URLError("refused"), _oai_response("late"))
    assert _llama(monkeypatch, http).generate("hi", seed=1).text == "late"
    assert len(http.calls) == 2


def test_llamacpp_raises_model_error_after_exhausting_retries(monkeypatch):
    http = _FakeHTTP(*[urllib.error.URLError("down")] * 3)
    with pytest.raises(ModelError):
        _llama(monkeypatch, http).generate("hi", seed=1)


def _http_error(code: int, body: dict) -> urllib.error.HTTPError:
    """A real ``HTTPError`` with a readable JSON body, matching what
    ``urllib.request.urlopen`` raises for a non-2xx response."""
    payload = json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError(
        url="http://localhost:8081/v1/chat/completions",
        code=code,
        msg="Bad Request",
        hdrs=None,
        fp=io.BytesIO(payload),
    )


# Bodies reproduced against the real llama-server running on :8081
# (SPEC section 45/51): an oversized prompt and a malformed request,
# respectively. See eval.models._parse_http_error_body's docstring for
# the exact probe transcripts, including Ollama's deeper wrapping of the
# identical objects.
_OVERFLOW_400_BODY = {
    "error": {
        "code": 400,
        "message": (
            "request (15430 tokens) exceeds the available context size "
            "(8192 tokens), try increasing it"
        ),
        "type": "exceed_context_size_error",
        "n_prompt_tokens": 15430,
        "n_ctx": 8192,
    }
}
_MALFORMED_400_BODY = {
    "error": {"code": 400, "message": "'messages' is required",
              "type": "invalid_request_error"}
}


def test_llamacpp_classifies_overflow_400_as_context_overflow_without_retrying(
    monkeypatch,
):
    http = _FakeHTTP(_http_error(400, _OVERFLOW_400_BODY))
    client = _llama(monkeypatch, http)
    with pytest.raises(ServerContextOverflowError) as excinfo:
        client.generate("hi", seed=1)
    # The DISTINCT subclass, not the base ContextOverflowError raised by
    # check_context -- but run_session's evidence gate (SPEC section
    # 45/51) catches BOTH identically, via the shared ContextOverflowError
    # base, and branches on whether the session already has a submitted
    # attempt, not on which of the two fired.
    assert isinstance(excinfo.value, ContextOverflowError)
    assert type(excinfo.value) is ServerContextOverflowError
    # Deterministic -- retrying would reproduce the identical 400 three
    # times for nothing. Exactly one call, not `client.retries`.
    assert len(http.calls) == 1


def test_llamacpp_classifies_non_overflow_400_as_model_error_after_retries(
    monkeypatch,
):
    # A fresh HTTPError per retry (not `[x] * 3`, which would share one
    # exhausted BytesIO across pops) -- each retry against a real server
    # gets its own readable response body.
    http = _FakeHTTP(*[_http_error(400, _MALFORMED_400_BODY) for _ in range(3)])
    client = _llama(monkeypatch, http)
    with pytest.raises(ModelError) as excinfo:
        client.generate("hi", seed=1)
    assert not isinstance(excinfo.value, ContextOverflowError)
    # Not overflow-shaped -- retried like any other transport failure.
    assert len(http.calls) == 3
    # The server's own message is surfaced, not discarded.
    assert "'messages' is required" in str(excinfo.value)


def _ollama_http_error(code: int, body: dict) -> urllib.error.HTTPError:
    """Ollama's shape for the SAME upstream failure: it proxies
    llama.cpp's error object as an opaque JSON *string*, one level deeper
    than llama.cpp's own body. Reproduced against the daemon on :11434
    with ``truncate: false``::

        {"error": "{\\"error\\": {\\"code\\": 400, \\"message\\":
        \\"request (3160 tokens) exceeds the available context size (256
        tokens), try increasing it\\", \\"type\\":
        \\"exceed_context_size_error\\", \\"n_prompt_tokens\\": 3160,
        \\"n_ctx\\": 256}}"}
    """
    return _http_error(code, {"error": json.dumps(body)})


def test_parse_http_error_body_unwraps_ollamas_double_encoded_error():
    # Both daemons must yield the SAME error object, or one shared
    # classifier cannot serve both backends and the two paths would
    # drift in how they classify an identical failure.
    assert (
        _parse_http_error_body(_ollama_http_error(400, _OVERFLOW_400_BODY))
        == _OVERFLOW_400_BODY["error"]
    )
    assert (
        _parse_http_error_body(_http_error(400, _OVERFLOW_400_BODY))
        == _OVERFLOW_400_BODY["error"]
    )


def test_parse_http_error_body_keeps_a_plain_string_error_as_a_message():
    # Ollama's own errors (a missing tag, say) are plain strings, not
    # nested JSON. Returning None for them would discard the daemon's
    # only explanation from the eventual ModelError.
    error = _parse_http_error_body(
        _http_error(404, {"error": "model not found"})
    )
    assert error == {"message": "model not found"}


def test_ollama_classifies_overflow_400_as_context_overflow_without_retrying(
    monkeypatch,
):
    http = _FakeHTTP(_ollama_http_error(400, _OVERFLOW_400_BODY))
    with pytest.raises(ServerContextOverflowError) as excinfo:
        _client(monkeypatch, http).generate("hi", seed=1)
    # The same guarantees the llamacpp path already has: the DISTINCT
    # subclass, so run_session's evidence gate sees an overflow rather
    # than a generic ModelError that would abort the run outright...
    assert type(excinfo.value) is ServerContextOverflowError
    # ...and no retry, because the daemon would reject the identical
    # prompt the same way three more times.
    assert len(http.calls) == 1


def test_ollama_classifies_non_overflow_400_as_model_error_after_retries(
    monkeypatch,
):
    http = _FakeHTTP(
        *[_ollama_http_error(400, _MALFORMED_400_BODY) for _ in range(3)]
    )
    with pytest.raises(ModelError) as excinfo:
        _client(monkeypatch, http).generate("hi", seed=1)
    assert not isinstance(excinfo.value, ContextOverflowError)
    assert len(http.calls) == 3
    assert "'messages' is required" in str(excinfo.value)


@pytest.mark.live
def test_live_ollama_refuses_a_prompt_the_estimate_let_through():
    """The one thing a scripted body cannot prove: that the real daemon
    refuses a prompt ``check_context`` ACCEPTED.

    Punctuation-dense text tokenizes far worse than 4 chars/token, so the
    crude estimate under-counts it badly -- measured on :11434, 1845
    estimated tokens against the daemon's real 6648, a 3.6x miss. Before
    ``truncate: false`` that prompt returned a normal 200 with its front
    silently discarded and an answer built on the remainder.
    """
    client = OllamaClient(
        MODELS["qwen0_5b"],
        num_ctx=2048,
        num_predict=8,
        sleep=lambda _s: None,
    )
    if not client.healthy():
        pytest.skip("ollama daemon not running")
    try:
        client.preflight()
    except ModelError as exc:
        pytest.skip(f"model not pulled: {exc}")

    prompt = "; ".join(f"x{i}={i}" for i in range(760))
    # Precondition, asserted rather than assumed: if the client-side
    # estimate were the thing that fired, this test would pass without
    # ever exercising the daemon -- the exact hole it exists to cover.
    client.check_context(prompt)
    with pytest.raises(ServerContextOverflowError):
        client.generate(prompt, seed=1)


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "context shift disabled"}},
        {"choices": []},
        {"choices": [{"message": "just a string"}]},
        {"choices": [{"message": None}]},
        {"object": "chat.completion"},
    ],
)
def test_llamacpp_refuses_a_malformed_200_body(monkeypatch, body):
    # Infrastructure misclassified as a model failure would bias toward the
    # null; an empty Generation must never be manufactured from a bad 200.
    with pytest.raises(ModelError):
        _llama(monkeypatch, _FakeHTTP(body)).generate("hi", seed=1)


def test_llamacpp_refuses_an_overflowing_prompt_before_requesting(monkeypatch):
    http = _FakeHTTP()
    client = _llama(monkeypatch, http)
    with pytest.raises(ContextOverflowError):
        client.generate("x" * (4 * 8192), seed=1)
    assert http.calls == []


def test_llamacpp_preflight_returns_provenance(monkeypatch):
    props = {
        "default_generation_settings": {"n_ctx": 8192},
        "model_path": "/blobs/sha256-828125",
        "build_info": "b1-4988f6e",
    }
    client = _llama(monkeypatch, _FakeHTTP(props), grammar="root ::= \"a\"")
    info = client.preflight()
    assert info["server_n_ctx"] == 8192
    assert info["num_ctx"] == 8192
    assert info["model_path"] == "/blobs/sha256-828125"
    assert info["build_info"] == "b1-4988f6e"
    assert info["grammar_sha256"] == grammar_digest("root ::= \"a\"")


def test_llamacpp_preflight_refuses_a_server_with_a_smaller_window(monkeypatch):
    # n_ctx is a launch flag here, not a request field: a smaller window
    # truncates from the front and drops the language card from the oxide
    # arms only, which is exactly the bias section 48 pins against.
    props = {"default_generation_settings": {"n_ctx": 4096}}
    with pytest.raises(ModelError):
        _llama(monkeypatch, _FakeHTTP(props)).preflight()


def test_llamacpp_preflight_refuses_props_without_a_window(monkeypatch):
    with pytest.raises(ModelError):
        _llama(monkeypatch, _FakeHTTP({})).preflight()


def test_llamacpp_version_reports_the_build(monkeypatch):
    http = _FakeHTTP({"build_info": "b1-4988f6e"})
    assert _llama(monkeypatch, http).version() == "b1-4988f6e"


def test_llamacpp_healthy_is_false_when_the_server_is_down(monkeypatch):
    http = _FakeHTTP(urllib.error.URLError("down"))
    assert _llama(monkeypatch, http).healthy() is False


def test_llamacpp_healthy_is_true_when_the_server_answers(monkeypatch):
    assert _llama(monkeypatch, _FakeHTTP({"status": "ok"})).healthy() is True


_LIVE_GRAMMAR_TASKS = ("t01", "t02", "t03", "t05", "t07", "t09", "t12", "t19")


@pytest.mark.live
def test_live_constrained_generation_emits_no_syntax_diagnostics(tmp_path):
    """The soundness property, tested against the real front end.

    Eight real constrained generations from the real 0.5B, each run through
    ``harness.check_file``. Zero OX0001 and zero OX01xx is the assertion;
    OX02xx/OX03xx/OX04xx are expected and are the point -- the pilot saw
    ~5000 diagnostics and not one linearity code, because nothing survived
    the lexer.

    A generation stopped at ``num_predict`` is the one exception, and it is
    not a grammar failure: soundness is a claim about complete derivations,
    and a prefix of one is not one. Truncation is a RESULT (section 51), so
    it must not fail the test -- but it may only ever surface as premature
    end of input, so those are asserted to be EOF errors and nothing else.
    Grammar constraint makes hitting the cap *more* likely, not less: every
    continuation of a repetition stays legal, so a degenerate loop runs to
    the cap instead of derailing into a stop token.
    """
    client = LlamaCppClient(
        "qwen2.5-coder-0.5b-instruct-q8_0", grammar=load_grammar("oxide")
    )
    if not client.healthy():
        pytest.skip(f"no llama-server on {client.host}")
    try:
        client.preflight()
    except ModelError as exc:
        pytest.skip(f"llama-server unusable: {exc}")

    codes: collections.Counter = collections.Counter()
    syntax: list[tuple[str, str, str]] = []
    at_eof: list[tuple[str, str, str]] = []
    complete = 0
    for index, task_id in enumerate(_LIVE_GRAMMAR_TASKS, start=1):
        generation = client.generate(
            harness.build_prompt("oxide", task_id, shots=0), seed=index
        )
        complete += not generation.truncated
        path = tmp_path / f"{task_id}.ox"
        path.write_text(extract(generation.text).source, encoding="utf-8")
        for diagnostic in harness.check_file("oxide", path)["diagnostics"]:
            code = str(diagnostic["code"])
            codes[code] += 1
            if not _is_syntax_code(code):
                continue
            found = (task_id, code, str(diagnostic["message"]))
            bucket = at_eof if generation.truncated and "found EOF" in found[2] else syntax
            bucket.append(found)
    print(
        f"\nconstrained diagnostic distribution: {dict(sorted(codes.items()))}"
        f"\ncomplete generations: {complete}/{len(_LIVE_GRAMMAR_TASKS)}"
        f"; truncation-only EOF errors: {len(at_eof)}"
    )
    assert complete > 0, "every generation hit the token cap; result is vacuous"
    assert syntax == [], syntax
