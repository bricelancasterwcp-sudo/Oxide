import json
import urllib.error

from eval import harness
from eval.driver import (
    MODELS,
    build_run_id,
    is_complete,
    reset_run,
    run_grid,
    run_one,
    run_session,
    wait_for_health,
)
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

    run_one(
        _ArmAwareClient(),
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
