"""Blind Phase 5c tests: eval harness + task corpus per SPEC.md Part IX (44-46).

Written blind to eval/harness.py. CLI exercised as a subprocess
(.venv/bin/python -m eval.harness <sub> ... --json); session API via import.

Contract points pinned where section 45/46 is open:
- check --json: one JSON object, at least {"ok": bool, "diagnostics": [...]};
  diagnostics carry exactly the Part VIII key set; a "rust" key, if present,
  is null.
- run --json verdict: exactly the four section-45 keys.
- triples.jsonl: "attempt" 1-based, "code" is the submitted source text.
- 5th submit() on a session raises (cap = 4).
- report reads DIR/triples.jsonl; output has arms.<arm>.{first_attempt_
  compile_rate, first_attempt_pass_rate, mean_attempts_to_compile,
  mean_attempts_to_pass}, "codes" histogram, totals.{attempts, tasks}
  (extra keys permitted).
- Pinned difficulty mix = the frozen corpus's: 7 intro / 8 core / 5 hard.
- Prompts embed the arm's card verbatim; the rust-arm prompt never contains
  the word "oxide" (fairness: the control arm must not see it).
"""

import json
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path("/home/brice/workspace/oxide")
PYTHON = str(ROOT / ".venv" / "bin" / "python")
TASKS_PATH = ROOT / "eval" / "tasks.jsonl"
SOLUTIONS = ROOT / "eval" / "solutions"
SHOTS = ROOT / "eval" / "shots"
RESULTS = ROOT / "eval" / "results"
CARDS = {
    "oxide": ROOT / "LANGUAGE_CARD.md",
    "explicit": ROOT / "LANGUAGE_CARD_EXPLICIT.md",
}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARMS = ("oxide", "explicit", "rust")
EXT = {"oxide": "ox", "explicit": "ox", "rust": "rs"}
SUBMISSION_CAP = 4

TASK_KEYS = {"id", "title", "prompt", "expected_stdout", "difficulty"}
DIAG_KEYS = {"code", "col", "end_col", "end_line", "line", "message", "notes",
             "suggestion"}
NOTE_KEYS = {"col", "line"}
VERDICT_KEYS = {"compiled", "passed", "stdout", "diagnostics"}
TRIPLE_KEYS = {"task", "arm", "attempt", "code", "diagnostics", "compiled", "passed"}

RUST_PREAMBLE = ("You are writing Rust (edition 2021), std only, no external "
                 "crates. Provide a complete program with fn main.")
OUTPUT_CONTRACT = ("Reply with ONLY the complete program source, no fences, "
                   "no commentary.")
OX0400_SUGGESTION = ("This value was moved at the noted location. Keep it "
                     "available by cloning at the move site (clone(x)), or "
                     "reorder so reads happen before the move.")
EX0002_SUGGESTION = "This use only reads the value; write &name."

PINNED_TASKS = {
    "t01": {"difficulty": "intro", "expected_stdout": "285\n",
            "prompt": "Print the sum of the squares of the integers 0 through 9."},
    "t08": {"difficulty": "core", "expected_stdout": "4\n12\n",
            "prompt": ("A list contains 3, 8, -2, 12, 7. Print how many values "
                       "are positive, then the largest value.")},
    "t15": {"difficulty": "hard", "expected_stdout": "42\n1\n",
            "prompt": ('Parse the strings "12", "x", "30" as integers; print the '
                       "sum of those that parse, then the count that failed.")},
}
PINNED_DIFFICULTY_MIX = {"intro": 7, "core": 8, "hard": 5}

# ------------------------------------------------------- fixture programs

GOOD_T01_OX = ("fn main() {\n    let total = 0\n    for i in range(0, 10) {\n"
               "        total = total + i * i\n    }\n    print(total)\n}\n")
WRONG_T01_OX = "fn main() {\n    print(7)\n}\n"
# Unknown name -> OX0200, compile failure without touching rustc.
BAD_COMPILE_OX = "fn main() {\n    print(nope)\n}\n"
CLEAN_OX = "fn main() {\n    print(1)\n}\n"
# S2 golden (SPEC sections 19 / 39): OX0400 at 4:15-16, note at 3:18.
S2_OX = ("fn main() {\n    let v = vec()\n    let w = push(v, 1)\n"
         "    print(len(v))\n}\n")
CLEAN_EXPLICIT = ("fn main() {\n    let v = push(vec(), 1)\n"
                  "    print(len(&v))\n    drop v\n}\n")
# Bare read of non-copy v -> EX0002.
BAD_EXPLICIT = ("fn main() {\n    let v = push(vec(), 1)\n"
                "    print(len(v))\n    drop v\n}\n")
CLEAN_RS = "fn main() {}\n"
# Classic E0382: use of a moved String (primary span at 4:20).
BAD_RS_E0382 = ('fn main() {\n    let s = String::from("hi");\n    let t = s;\n'
                '    println!("{}", s);\n}\n')

# ----------------------------------------------------------------- helpers


def load_tasks() -> list:
    lines = TASKS_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


TASKS = load_tasks()
TASK_BY_ID = {t["id"]: t for t in TASKS}
TASK_IDS = [t["id"] for t in TASKS]


def harness(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([PYTHON, "-m", "eval.harness", *args],
                          capture_output=True, text=True, cwd=str(ROOT),
                          timeout=timeout)


def harness_json(*args: str, timeout: int = 60) -> dict:
    proc = harness(*args, timeout=timeout)
    assert proc.stdout.strip(), (f"harness {args} produced no stdout; "
                                 f"rc={proc.returncode} stderr={proc.stderr!r}")
    return json.loads(proc.stdout)


def import_harness():
    import importlib

    return importlib.import_module("eval.harness")


def assert_diag_shape(diag: dict) -> None:
    assert set(diag) == DIAG_KEYS, f"diagnostic keys {sorted(diag)}"
    for key in ("line", "col", "end_line", "end_col"):
        assert isinstance(diag[key], int) and diag[key] >= 1, (key, diag[key])
    assert isinstance(diag["code"], str) and diag["code"]
    assert isinstance(diag["message"], str)
    assert isinstance(diag["suggestion"], str)
    assert isinstance(diag["notes"], list)
    for note in diag["notes"]:
        assert set(note) == NOTE_KEYS
        assert isinstance(note["line"], int) and note["line"] >= 1
        assert isinstance(note["col"], int) and note["col"] >= 1


def check_json(arm: str, path: Path) -> dict:
    payload = harness_json("check", "--arm", arm, "--file", str(path), "--json")
    assert isinstance(payload, dict)
    assert isinstance(payload.get("ok"), bool)
    assert isinstance(payload.get("diagnostics"), list)
    for diag in payload["diagnostics"]:
        assert_diag_shape(diag)
    if "rust" in payload:
        assert payload["rust"] is None
    assert payload["ok"] == (payload["diagnostics"] == [])
    return payload


def run_json(arm: str, path: Path, task_id: str, timeout: int = 60) -> dict:
    return harness_json("run", "--arm", arm, "--file", str(path), "--task",
                        task_id, "--json", timeout=timeout)


def prompt_out(arm: str, task_id: str, *extra: str) -> str:
    proc = harness("prompt", "--arm", arm, "--task", task_id, *extra)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()
    return proc.stdout


def shot_pairs(arm: str) -> list:
    arm_dir = SHOTS / arm
    pairs = []
    for task_file in sorted(arm_dir.glob("*.task.txt")):
        sid = task_file.name.split(".")[0]
        pairs.append((sid, task_file, arm_dir / f"{sid}.solution.{EXT[arm]}"))
    return pairs


@pytest.fixture
def run_id():
    rid = "pytest-" + uuid.uuid4().hex[:12]
    yield rid
    shutil.rmtree(RESULTS / rid, ignore_errors=True)


def read_triples(rid: str) -> list:
    path = RESULTS / rid / "triples.jsonl"
    assert path.is_file(), f"missing {path}"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------- 1. corpus well-formed


class TestCorpus:
    def test_twenty_unique_ids_t01_to_t20(self):
        assert len(TASKS) == 20
        expected = {f"t{i:02d}" for i in range(1, 21)}
        assert set(TASK_IDS) == expected
        assert len(set(TASK_IDS)) == 20

    def test_line_schema_exact_keys(self):
        for task in TASKS:
            assert set(task) == TASK_KEYS, task.get("id")
            for key in TASK_KEYS:
                assert isinstance(task[key], str) and task[key], (task["id"], key)

    def test_difficulty_values_and_pinned_mix(self):
        for task in TASKS:
            assert task["difficulty"] in {"intro", "core", "hard"}, task["id"]
        mix = Counter(t["difficulty"] for t in TASKS)
        assert dict(mix) == PINNED_DIFFICULTY_MIX

    def test_expected_stdout_nonempty_exact_trailing_newline(self):
        for task in TASKS:
            out = task["expected_stdout"]
            assert out, task["id"]
            assert out.endswith("\n"), task["id"]

    @pytest.mark.parametrize("tid", sorted(PINNED_TASKS))
    def test_pinned_task_exact(self, tid):
        task = TASK_BY_ID[tid]
        for key, value in PINNED_TASKS[tid].items():
            assert task[key] == value, (tid, key)

    def test_prompts_language_neutral(self):
        for task in TASKS:
            lowered = task["prompt"].lower()
            assert "oxide" not in lowered, task["id"]
            assert "rust" not in lowered, task["id"]


# ------------------------------------- 2. all 60 reference solutions verified


@pytest.mark.parametrize(
    "arm,tid",
    [(arm, tid) for arm in ARMS for tid in TASK_IDS],
    ids=[f"{arm}-{tid}" for arm in ARMS for tid in TASK_IDS],
)
def test_reference_solution_compiles_and_passes(arm, tid):
    path = SOLUTIONS / arm / f"{tid}.{EXT[arm]}"
    assert path.is_file(), f"missing reference solution {path}"
    verdict = run_json(arm, path, tid, timeout=90)
    assert verdict["compiled"] is True, verdict.get("diagnostics")
    assert verdict["passed"] is True, verdict.get("stdout")
    assert verdict["stdout"] == TASK_BY_ID[tid]["expected_stdout"]


# --------------------------------------------- 3. `check` JSON shapes per arm


class TestCheck:
    def test_clean_oxide(self, tmp_path):
        f = tmp_path / "clean.ox"
        f.write_text(CLEAN_OX)
        payload = check_json("oxide", f)
        assert payload["ok"] is True
        assert payload["diagnostics"] == []

    def test_bad_oxide_ox0400(self, tmp_path):
        f = tmp_path / "s2.ox"
        f.write_text(S2_OX)
        payload = check_json("oxide", f)
        assert payload["ok"] is False
        diags = [d for d in payload["diagnostics"] if d["code"] == "OX0400"]
        assert diags, payload["diagnostics"]
        diag = diags[0]
        assert diag["suggestion"] == OX0400_SUGGESTION
        assert (diag["line"], diag["col"]) == (4, 15)
        assert (diag["end_line"], diag["end_col"]) == (4, 16)
        assert diag["notes"] == [{"col": 18, "line": 3}]

    def test_clean_explicit(self, tmp_path):
        f = tmp_path / "clean_explicit.ox"
        f.write_text(CLEAN_EXPLICIT)
        payload = check_json("explicit", f)
        assert payload["ok"] is True
        assert payload["diagnostics"] == []

    def test_bad_explicit_ex0002(self, tmp_path):
        f = tmp_path / "bad_explicit.ox"
        f.write_text(BAD_EXPLICIT)
        payload = check_json("explicit", f)
        assert payload["ok"] is False
        diags = [d for d in payload["diagnostics"] if d["code"] == "EX0002"]
        assert diags, payload["diagnostics"]
        assert diags[0]["suggestion"] == EX0002_SUGGESTION

    def test_clean_rust(self, tmp_path):
        f = tmp_path / "clean.rs"
        f.write_text(CLEAN_RS)
        payload = check_json("rust", f)
        assert payload["ok"] is True
        assert payload["diagnostics"] == []

    def test_bad_rust_e0382_adapter(self, tmp_path):
        f = tmp_path / "e0382.rs"
        f.write_text(BAD_RS_E0382)
        payload = check_json("rust", f)
        assert payload["ok"] is False
        diags = [d for d in payload["diagnostics"] if d["code"] == "E0382"]
        assert diags, payload["diagnostics"]
        diag = diags[0]
        assert diag["suggestion"] == ""
        # rendered message including rustc's help/children text verbatim
        assert "borrow of moved value" in diag["message"]
        assert "help: consider cloning" in diag["message"]
        assert (diag["line"], diag["col"]) == (4, 20)


# -------------------------------------------------- `run` verdict correctness


class TestRunVerdict:
    def test_verdict_shape_exact_keys(self, tmp_path):
        f = tmp_path / "good.ox"
        f.write_text(GOOD_T01_OX)
        verdict = run_json("oxide", f, "t01", timeout=90)
        assert set(verdict) == VERDICT_KEYS
        assert verdict["compiled"] is True
        assert verdict["passed"] is True
        assert verdict["stdout"] == "285\n"
        assert verdict["diagnostics"] == []

    def test_wrong_output_compiles_but_fails(self, tmp_path):
        f = tmp_path / "wrong.ox"
        f.write_text(WRONG_T01_OX)
        verdict = run_json("oxide", f, "t01", timeout=90)
        assert verdict["compiled"] is True
        assert verdict["passed"] is False
        assert verdict["stdout"] == "7\n"

    def test_compile_failure_verdict(self, tmp_path):
        f = tmp_path / "bad.ox"
        f.write_text(BAD_COMPILE_OX)
        verdict = run_json("oxide", f, "t01")
        assert verdict["compiled"] is False
        assert verdict["passed"] is False
        assert any(d["code"] == "OX0200" for d in verdict["diagnostics"])


# ------------------------------------------------------------- 4. session API


class TestSession:
    def test_good_bad_good_verdicts_and_triples(self, run_id):
        h = import_harness()
        session = h.new_session("t01", "oxide", run_id)

        v1 = session.submit(GOOD_T01_OX)
        assert v1["compiled"] is True and v1["passed"] is True
        assert v1["stdout"] == "285\n"

        v2 = session.submit(BAD_COMPILE_OX)
        assert v2["compiled"] is False and v2["passed"] is False
        assert any(d["code"] == "OX0200" for d in v2["diagnostics"])

        v3 = session.submit(GOOD_T01_OX)
        assert v3["compiled"] is True and v3["passed"] is True

        triples = read_triples(run_id)
        assert len(triples) == 3
        sources = [GOOD_T01_OX, BAD_COMPILE_OX, GOOD_T01_OX]
        expected_flags = [True, False, True]
        for i, triple in enumerate(triples):
            assert set(triple) == TRIPLE_KEYS, sorted(triple)
            assert triple["task"] == "t01"
            assert triple["arm"] == "oxide"
            assert triple["attempt"] == i + 1
            assert triple["code"] == sources[i]
            assert triple["compiled"] is expected_flags[i]
            assert triple["passed"] is expected_flags[i]
            assert isinstance(triple["diagnostics"], list)
        assert triples[0]["diagnostics"] == []
        assert triples[2]["diagnostics"] == []
        assert any(d["code"] == "OX0200" for d in triples[1]["diagnostics"])

    def test_cap_of_four_submissions_enforced(self, run_id):
        h = import_harness()
        session = h.new_session("t01", "oxide", run_id)
        for _ in range(SUBMISSION_CAP):
            verdict = session.submit(BAD_COMPILE_OX)
            assert verdict["compiled"] is False
        with pytest.raises(Exception):
            session.submit(BAD_COMPILE_OX)
        assert len(read_triples(run_id)) == SUBMISSION_CAP


# ------------------------------------------------------------------ 5. prompt


class TestPrompt:
    def test_oxide_prompt_card_task_contract(self):
        out = prompt_out("oxide", "t01")
        assert CARDS["oxide"].read_text(encoding="utf-8").strip() in out
        assert TASK_BY_ID["t01"]["prompt"] in out
        assert OUTPUT_CONTRACT in out

    def test_explicit_prompt_card_task_contract(self):
        out = prompt_out("explicit", "t15")
        assert CARDS["explicit"].read_text(encoding="utf-8").strip() in out
        assert TASK_BY_ID["t15"]["prompt"] in out
        assert OUTPUT_CONTRACT in out

    def test_rust_prompt_preamble_and_neutrality(self):
        out = prompt_out("rust", "t08")
        assert RUST_PREAMBLE in out
        assert TASK_BY_ID["t08"]["prompt"] in out
        assert OUTPUT_CONTRACT in out
        # fairness: the control arm must never see the word "oxide"
        assert "oxide" not in out.lower()

    def test_default_prompt_has_no_shots(self):
        out = prompt_out("oxide", "t01")
        for _sid, task_file, _sol in shot_pairs("oxide"):
            assert task_file.read_text(encoding="utf-8").strip() not in out

    def test_shots_2_includes_exactly_two_examples(self):
        out = prompt_out("oxide", "t01", "--shots", "2")
        pairs = shot_pairs("oxide")
        assert len(pairs) == 5
        included = [
            (task_file, sol_file)
            for _sid, task_file, sol_file in pairs
            if task_file.read_text(encoding="utf-8").strip() in out
        ]
        assert len(included) == 2, [str(t) for t, _ in included]
        for _task_file, sol_file in included:
            assert sol_file.read_text(encoding="utf-8").strip() in out

    @pytest.mark.parametrize("arm", ARMS)
    def test_shots_dir_five_pairs_disjoint_from_corpus(self, arm):
        arm_dir = SHOTS / arm
        assert arm_dir.is_dir(), f"missing shots dir {arm_dir}"
        pairs = shot_pairs(arm)
        assert len(pairs) == 5, [p[0] for p in pairs]
        assert len({sid for sid, _t, _s in pairs}) == 5
        corpus_prompts = {t["prompt"] for t in TASKS}
        for sid, task_file, sol_file in pairs:
            assert sid not in TASK_IDS, sid
            assert sol_file.is_file(), f"missing shot solution {sol_file}"
            assert sol_file.read_text(encoding="utf-8").strip()
            text = task_file.read_text(encoding="utf-8").strip()
            assert text
            assert text not in corpus_prompts, sid


# ------------------------------------------------------------------ 6. report


def make_triple(task, arm, attempt, compiled, passed, codes=()):
    diags = [{"code": c, "col": 1, "end_col": 2, "end_line": 1, "line": 1,
              "message": "synthetic diagnostic", "notes": [], "suggestion": ""}
             for c in codes]
    return {"task": task, "arm": arm, "attempt": attempt,
            "code": "fn main() {\n}\n", "diagnostics": diags,
            "compiled": compiled, "passed": passed}


def test_report_aggregates_on_synthetic_results(tmp_path):
    rows = [
        # oxide/t01: compiles on attempt 2, passes on attempt 3
        make_triple("t01", "oxide", 1, False, False, codes=("OX0200",)),
        make_triple("t01", "oxide", 2, True, False),
        make_triple("t01", "oxide", 3, True, True),
        # oxide/t02: first-attempt pass
        make_triple("t02", "oxide", 1, True, True),
        # rust/t01: never compiles in 4 attempts -> counts as cap+1 = 5
        make_triple("t01", "rust", 1, False, False, codes=("E0382",)),
        make_triple("t01", "rust", 2, False, False, codes=("E0382",)),
        make_triple("t01", "rust", 3, False, False, codes=("E0382",)),
        make_triple("t01", "rust", 4, False, False, codes=("E0382",)),
    ]
    (tmp_path / "triples.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )

    report = harness_json("report", "--results", str(tmp_path), "--json")

    oxide = report["arms"]["oxide"]
    assert oxide["first_attempt_compile_rate"] == pytest.approx(0.5)
    assert oxide["first_attempt_pass_rate"] == pytest.approx(0.5)
    assert oxide["mean_attempts_to_compile"] == pytest.approx(1.5)
    assert oxide["mean_attempts_to_pass"] == pytest.approx(2.0)

    rust = report["arms"]["rust"]
    assert rust["first_attempt_compile_rate"] == pytest.approx(0.0)
    assert rust["first_attempt_pass_rate"] == pytest.approx(0.0)
    assert rust["mean_attempts_to_compile"] == pytest.approx(5.0)
    assert rust["mean_attempts_to_pass"] == pytest.approx(5.0)

    assert report["codes"] == {"E0382": 4, "OX0200": 1}
    assert report["totals"]["attempts"] == 8
    assert report["totals"]["tasks"] == 3


# --------------------------------------- 7. demonstrated-defect regressions

# t01 solver used by the dotted-filename regression (crate-name defect).
RUST_T01 = ("fn main() {\n    let mut total = 0;\n    for i in 0..10 {\n"
            "        total += i * i;\n    }\n"
            '    println!("{}", total);\n}\n')
# Program whose stdout is not valid UTF-8 (solver-controlled).
NON_UTF8_STDOUT_RS = (
    "use std::io::Write;\n"
    "fn main() {\n"
    "    std::io::stdout().write_all(&[0xff, 0xfe, b'\\n']).unwrap();\n"
    "}\n"
)
# A lone surrogate is a valid JSON escape, so json.loads of an LLM API
# response can hand the harness exactly this Python str.
LONE_SURROGATE_SRC = "fn main() {\n    print(1)\n}\n\ud800"


def _corrupt_text(content):
    def build(root):
        (root / "triples.jsonl").write_text(content, encoding="utf-8")

    return build


def _corrupt_bytes(root):
    (root / "triples.jsonl").write_bytes(b"\xff\xfe not utf-8\n")


def _corrupt_dir(root):
    (root / "triples.jsonl").mkdir()


_GOOD_ROW = json.dumps(make_triple("t01", "oxide", 1, True, True),
                       sort_keys=True)
_NON_DICT_DIAG_ROW = json.dumps(
    dict(make_triple("t01", "oxide", 1, True, False), diagnostics=[5]),
    sort_keys=True,
)
_STR_ATTEMPT_ROW = json.dumps(
    dict(make_triple("t01", "oxide", 1, True, True), attempt="two"),
    sort_keys=True,
)
REPORT_CORRUPT_SHAPES = {
    "missing-keys": _corrupt_text("{}\n"),
    "non-object-line": _corrupt_text("5\n"),
    "triples-is-directory": _corrupt_dir,
    "non-utf8-bytes": _corrupt_bytes,
    "non-dict-diagnostic": _corrupt_text(_NON_DICT_DIAG_ROW + "\n"),
    "string-attempt": _corrupt_text(_GOOD_ROW + "\n" + _STR_ATTEMPT_ROW + "\n"),
}


class TestDemonstratedDefects:
    def test_rust_arm_dotted_filename_stem_compiles(self, tmp_path):
        # rustc derives the crate name from the file stem; a dotted stem
        # must not fail with "invalid character '.' in crate name".
        f = tmp_path / "t01.solution.v2.rs"
        f.write_text(RUST_T01, encoding="utf-8")
        verdict = run_json("rust", f, "t01", timeout=90)
        assert verdict["compiled"] is True, verdict["diagnostics"]
        assert verdict["passed"] is True
        payload = check_json("rust", f)
        assert payload["ok"] is True

    def test_submit_lone_surrogate_source_logged_not_crashed(self, run_id):
        h = import_harness()
        session = h.new_session("t01", "oxide", run_id)
        verdict = session.submit(LONE_SURROGATE_SRC)
        assert verdict["compiled"] is False
        assert verdict["passed"] is False
        assert verdict["diagnostics"], "expected an unencodable-source diag"
        assert_diag_shape(verdict["diagnostics"][0])
        triples = read_triples(run_id)
        assert len(triples) == 1
        assert set(triples[0]) == TRIPLE_KEYS
        assert triples[0]["attempt"] == 1
        assert triples[0]["code"] == LONE_SURROGATE_SRC
        assert session.attempts == 1

    def test_rust_arm_non_utf8_stdout_yields_verdict_json(self, tmp_path):
        f = tmp_path / "nonutf8.rs"
        f.write_text(NON_UTF8_STDOUT_RS, encoding="utf-8")
        proc = harness("run", "--arm", "rust", "--file", str(f), "--task",
                       "t01", "--json", timeout=90)
        assert proc.stdout.strip(), (proc.returncode, proc.stderr)
        verdict = json.loads(proc.stdout)  # exactly one JSON object
        assert set(verdict) == VERDICT_KEYS
        assert verdict["compiled"] is True
        assert verdict["passed"] is False
        assert verdict["stdout"] == "\ufffd\ufffd\n"
        assert proc.returncode == 1

    @pytest.mark.parametrize("shape", sorted(REPORT_CORRUPT_SHAPES))
    def test_report_corrupt_results_exit2_json_error(self, tmp_path, shape):
        REPORT_CORRUPT_SHAPES[shape](tmp_path)
        proc = harness("report", "--results", str(tmp_path), "--json")
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert isinstance(payload["error"], str) and payload["error"]

    def test_duplicate_session_same_run_task_arm_rejected(self, run_id):
        h = import_harness()
        h.new_session("t01", "oxide", run_id)
        with pytest.raises(h.HarnessError):
            h.new_session("t01", "oxide", run_id)
        # distinct task or arm within the same run is still allowed
        h.new_session("t02", "oxide", run_id)
        h.new_session("t01", "rust", run_id)

    def test_run_id_embedded_nul_rejected_up_front(self, tmp_path):
        h = import_harness()
        with pytest.raises(h.HarnessError):
            h.new_session("t01", "oxide", "run\x00id", results_root=tmp_path)
        assert list(tmp_path.iterdir()) == []


# ----------------------------- 8. pinned cross-arm observations (no fix)


class TestPinnedObservations:
    """Characterization of demonstrated observations, not harness bugs.

    SPEC section 45 pins compile = transpile+rustc for the oxide arms
    and pass = exact stdout + termination only; these tests document
    the resulting cross-arm skew so any future change is deliberate.
    """

    def test_empty_source_compile_asymmetry_across_arms(self, tmp_path):
        empty_ox = tmp_path / "empty.ox"
        empty_ox.write_text("")
        v = run_json("oxide", empty_ox, "t01", timeout=90)
        assert v["compiled"] is True and v["passed"] is False
        empty_rs = tmp_path / "empty.rs"
        empty_rs.write_text("")
        v = run_json("rust", empty_rs, "t01", timeout=90)
        assert v["compiled"] is False and v["passed"] is False

    def test_nonzero_exit_after_exact_stdout_still_passes(self, tmp_path):
        f = tmp_path / "panics.rs"
        f.write_text('fn main() { println!("285"); panic!("boom"); }\n')
        v = run_json("rust", f, "t01", timeout=90)
        assert v["compiled"] is True and v["passed"] is True
