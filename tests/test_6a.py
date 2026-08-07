import inspect
import json
import subprocess
import urllib.error
from datetime import datetime

import pytest

from eval import harness, rollup
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
from eval.models import Generation, ModelError, OllamaClient
from eval.repair import RepairPromptError, build_repair_prompt
from eval.rollup import (
    INSUFFICIENT,
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


def test_malformed_body_is_not_retried_as_transport(monkeypatch):
    # It is a hard stop, not a transient: one call, then ModelError.
    http = _FakeHTTP({"error": "boom"})
    with pytest.raises(ModelError):
        _client(monkeypatch, http).generate("hi", seed=1)
    assert len(http.calls) == 1


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


def _drive_one_cell(tmp_path, client, *, preflight=None, health_check=None,
                    seeds=(1,), fake_run_one=None):
    """Walk one grid cell with run_one stubbed out (no generation)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "eval.driver.run_one", fake_run_one or (lambda client, **kw: None)
    )
    try:
        return run_grid(
            lambda tag: client,
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
        MODELS["qwen1_5b"], temperature=0.3, top_p=0.5, num_predict=99
    )
    _drive_one_cell(tmp_path, client)
    m = _manifest(tmp_path)
    assert (m["temperature"], m["top_p"], m["num_predict"]) == (0.3, 0.5, 99)


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
    assert m["context_length"] == 32768
    assert m["ollama_version"] == "0.6.8"


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
    assert m["digest"] is None


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


def _write_run(root, slug, shots, seed, cells, triples=()) -> None:
    run_dir = root / build_run_id(slug, shots, seed)
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
