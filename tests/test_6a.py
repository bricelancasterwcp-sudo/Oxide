import json
import urllib.error

from eval.extract import Extraction, extract
from eval.models import Generation, ModelError, OllamaClient


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


def test_repair_prompt_rejects_unknown_arm():
    with pytest.raises(ValueError):
        build_repair_prompt("python", "x", _COMPILE_FAIL)
