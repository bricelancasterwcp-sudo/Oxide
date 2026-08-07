"""Evaluation harness (SPEC.md section 45): importable module + CLI.

CLI grammar (every subcommand also accepts ``--json``)::

    python3 -m eval.harness check  --arm oxide|explicit|rust --file F
    python3 -m eval.harness run    --arm A --file F --task ID
    python3 -m eval.harness prompt --arm A --task ID [--shots N]
    python3 -m eval.harness report --results DIR

- ``check``  — structured diagnostics only: oxide/explicit via the
  Part VIII pipeline, rust via the ``rustc --error-format=json``
  adapter. JSON payload: ``{"ok": bool, "diagnostics": [...]}``.
- ``run``    — full verdict: compile (oxide arms transpile then rustc;
  rust arm rustc), execute under ``timeout 10``, diff stdout against
  the task's expected_stdout. JSON payload:
  ``{"compiled": bool, "passed": bool, "stdout": "...",
  "diagnostics": [...]}``.
- ``prompt`` — the complete solver prompt: language card (oxide /
  explicit) or the pinned Rust preamble, optional few-shot examples
  from ``eval/shots/<arm>/``, the task prompt, the output contract.
- ``report`` — aggregates over ``triples.jsonl`` files under a results
  directory (first-attempt rates, mean attempts, per-code histogram).

Exit codes mirror the main CLI: 0 clean/passed, 1 diagnostics/failed,
2 usage or unreadable input.

Importable session API (the driver loop)::

    session = new_session(task_id, arm, run_id)
    verdict = session.submit(source)   # at most 4 submissions

Each submission appends one line to
``eval/results/<run_id>/triples.jsonl``:
``{"task", "arm", "attempt", "code", "diagnostics", "compiled",
"passed"}`` — the verified-repair-triple dataset.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # direct `python3 eval/harness.py` runs
    sys.path.insert(0, str(_REPO_ROOT))

from eval.rustc_adapter import run_binary, rustc_build, rustc_check

# Section 39 pins the diagnostic JSON shape and section 40 the
# suggestion table; src.cli._diagnostic_json is the frozen, green
# implementation of exactly that mapping. Reusing it keeps harness
# diagnostics byte-identical to the CLI's (a fairness property), at the
# cost of a private-name import from our own frozen module.
from src.cli import _diagnostic_json
from src.codegen.rust import transpile as core_transpile
from src.explicit.pipeline import run as explicit_run
from src.source import SourceFile

ARMS = ("oxide", "explicit", "rust")
MAX_ATTEMPTS = 4  # section 45: max 4 submissions per session

RUST_PREAMBLE = (
    "You are writing Rust (edition 2021), std only, no external crates. "
    "Provide a complete program with fn main."
)
OUTPUT_CONTRACT = (
    "Reply with ONLY the complete program source, no fences, no commentary."
)
CARD_FILES = {
    "oxide": "LANGUAGE_CARD.md",
    "explicit": "LANGUAGE_CARD_EXPLICIT.md",
}
SOURCE_SUFFIX = {"oxide": ".ox", "explicit": ".ox", "rust": ".rs"}

TASKS_PATH = _REPO_ROOT / "eval" / "tasks.jsonl"
SHOTS_ROOT = _REPO_ROOT / "eval" / "shots"
RESULTS_ROOT = _REPO_ROOT / "eval" / "results"


class HarnessError(Exception):
    """A usage-level harness error (maps to exit code 2 on the CLI)."""


# ------------------------------------------------------------------ tasks


def load_tasks(tasks_path: str | Path | None = None) -> dict[str, dict]:
    """Load the section-44 corpus, keyed by task id."""
    path = Path(tasks_path) if tasks_path is not None else TASKS_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HarnessError(f"cannot read tasks file '{path}': {exc}") from exc
    tasks: dict[str, dict] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"malformed tasks line {line_no} in '{path}': {exc}"
            ) from exc
        tasks[obj["id"]] = obj
    return tasks


def _get_task(task_id: str, tasks_path: str | Path | None = None) -> dict:
    tasks = load_tasks(tasks_path)
    if task_id not in tasks:
        raise HarnessError(f"unknown task id '{task_id}'")
    return tasks[task_id]


# ------------------------------------------------------------------ check/run


def _require_arm(arm: str) -> None:
    if arm not in ARMS:
        raise HarnessError(f"unknown arm '{arm}' (expected one of {ARMS})")


def _read_source(path: str | Path) -> str:
    # newline="" mirrors src.cli: the pipeline sees the file's actual
    # characters (lone \r is skippable whitespace per SPEC section 3).
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise HarnessError(f"cannot read '{path}': {exc}") from exc


def _oxide_pipeline(arm: str, source: str) -> tuple[str | None, list[dict]]:
    """Run the Part VIII pipeline for an oxide-family arm; returns
    (rust_text_or_None, section-39-shaped diagnostic dicts)."""
    pipeline = core_transpile if arm == "oxide" else explicit_run
    rust, diagnostics = pipeline(source)
    source_file = SourceFile.from_text(source)
    return rust, [_diagnostic_json(d, source_file) for d in diagnostics]


def _stage_rust_source(path: str | Path, work_dir: Path) -> Path:
    """Copy a rust-arm file to a fixed-stem ``program.rs`` in work_dir.

    rustc derives the crate name from the file stem, so a caller path
    like ``s01.solution.rs`` fails before parsing ("invalid character
    '.' in crate name"). The oxide arms already compile from a temp
    ``program.rs``; this mirrors that for the rust arm.
    """
    if not Path(path).is_file():
        raise HarnessError(f"cannot read '{path}': no such file")
    staged = work_dir / "program.rs"
    try:
        shutil.copyfile(path, staged)
    except OSError as exc:
        raise HarnessError(f"cannot read '{path}': {exc}") from exc
    return staged


def check_file(arm: str, path: str | Path) -> dict:
    """``check`` verdict: ``{"ok": bool, "diagnostics": [...]}``."""
    _require_arm(arm)
    if arm == "rust":
        with tempfile.TemporaryDirectory(prefix="oxide-eval-") as work_dir:
            staged = _stage_rust_source(path, Path(work_dir))
            ok, diagnostics = rustc_check(staged, work_dir)
        return {"ok": ok and not diagnostics, "diagnostics": diagnostics}
    _, diagnostics = _oxide_pipeline(arm, _read_source(path))
    return {"ok": not diagnostics, "diagnostics": diagnostics}


def run_file(arm: str, path: str | Path, expected_stdout: str) -> dict:
    """``run`` verdict: compile, execute under ``timeout 10``, and diff
    stdout against expected_stdout (section 45)."""
    _require_arm(arm)
    verdict = {
        "compiled": False,
        "passed": False,
        "stdout": "",
        "diagnostics": [],
    }
    with tempfile.TemporaryDirectory(prefix="oxide-eval-") as work:
        work_dir = Path(work)
        if arm == "rust":
            rs_path = _stage_rust_source(path, work_dir)
        else:
            rust, diagnostics = _oxide_pipeline(arm, _read_source(path))
            if diagnostics or rust is None:
                verdict["diagnostics"] = diagnostics
                return verdict
            rs_path = work_dir / "program.rs"
            rs_path.write_text(rust, encoding="utf-8")
        binary = work_dir / "program"
        ok, diagnostics = rustc_build(rs_path, binary)
        if not ok:
            verdict["diagnostics"] = diagnostics
            return verdict
        verdict["compiled"] = True
        finished, stdout = run_binary(binary)
        verdict["stdout"] = stdout
        verdict["passed"] = finished and stdout == expected_stdout
    return verdict


def run_task(
    arm: str,
    path: str | Path,
    task_id: str,
    tasks_path: str | Path | None = None,
) -> dict:
    """``run`` against a corpus task's expected_stdout."""
    task = _get_task(task_id, tasks_path)
    return run_file(arm, path, task["expected_stdout"])


# ------------------------------------------------------------------ prompt


def load_shots(arm: str) -> list[tuple[str, str]]:
    """Ordered (task_text, solution_source) pairs from eval/shots/<arm>/."""
    _require_arm(arm)
    shots_dir = SHOTS_ROOT / arm
    pairs: list[tuple[str, str]] = []
    for task_file in sorted(shots_dir.glob("*.task.txt")):
        stem = task_file.name[: -len(".task.txt")]
        solutions = sorted(shots_dir.glob(stem + ".solution.*"))
        if not solutions:
            continue
        pairs.append(
            (
                task_file.read_text(encoding="utf-8"),
                solutions[0].read_text(encoding="utf-8"),
            )
        )
    return pairs


def build_prompt(
    arm: str,
    task_id: str,
    shots: int = 0,
    tasks_path: str | Path | None = None,
) -> str:
    """The complete solver prompt for one task in one arm (section 45)."""
    _require_arm(arm)
    if shots < 0:
        raise HarnessError("--shots must be >= 0")
    task = _get_task(task_id, tasks_path)
    if arm == "rust":
        lead = RUST_PREAMBLE
    else:
        card_path = _REPO_ROOT / CARD_FILES[arm]
        try:
            lead = card_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HarnessError(
                f"cannot read language card '{card_path}': {exc}"
            ) from exc
    sections = [lead.rstrip("\n")]
    if shots:
        pairs = load_shots(arm)
        if shots > len(pairs):
            raise HarnessError(
                f"arm '{arm}' has only {len(pairs)} shots (requested {shots})"
            )
        for task_text, solution in pairs[:shots]:
            sections.append(
                "Example task:\n"
                + task_text.rstrip("\n")
                + "\n\nExample solution:\n"
                + solution.rstrip("\n")
            )
    sections.append("Task:\n" + task["prompt"].rstrip("\n"))
    sections.append(OUTPUT_CONTRACT)
    return "\n\n".join(sections) + "\n"


# ------------------------------------------------------------------ sessions


def _unencodable_source_verdict(exc: UnicodeEncodeError) -> dict:
    """Compile-failure verdict for source that is not encodable UTF-8,
    with one section-39-shaped diagnostic."""
    return {
        "compiled": False,
        "passed": False,
        "stdout": "",
        "diagnostics": [
            {
                "code": "E????",
                "message": f"submission is not valid UTF-8 text: {exc}",
                "line": 1,
                "col": 1,
                "end_line": 1,
                "end_col": 1,
                "notes": [],
                "suggestion": "",
            }
        ],
    }


class Session:
    """One task/arm attempt sequence with the section-45 submission cap.

    Every ``submit`` runs the full ``run`` verdict and appends a repair
    triple to ``<results_root>/<run_id>/triples.jsonl``.
    """

    def __init__(
        self,
        task: dict,
        arm: str,
        run_id: str,
        triples_path: Path,
    ) -> None:
        self.task_id: str = task["id"]
        self.expected_stdout: str = task["expected_stdout"]
        self.arm = arm
        self.run_id = run_id
        self.triples_path = triples_path
        self.attempts = 0

    def submit(self, source: str) -> dict:
        """Judge one candidate program; raises HarnessError past the cap."""
        if self.attempts >= MAX_ATTEMPTS:
            raise HarnessError(
                f"attempt cap reached ({MAX_ATTEMPTS} submissions)"
            )
        self.attempts += 1
        try:
            source.encode("utf-8")
        except UnicodeEncodeError as exc:
            # A lone surrogate (a valid JSON escape, so solver-reachable)
            # cannot be written as UTF-8 source. Judge it a compile
            # failure so the attempt is still logged (section 45: every
            # submission is recorded), instead of crashing unlogged.
            verdict = _unencodable_source_verdict(exc)
        else:
            suffix = SOURCE_SUFFIX[self.arm]
            with tempfile.TemporaryDirectory(prefix="oxide-eval-") as work:
                path = Path(work) / f"submission{suffix}"
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(source)
                verdict = run_file(self.arm, path, self.expected_stdout)
        record = {
            "task": self.task_id,
            "arm": self.arm,
            "attempt": self.attempts,
            "code": source,
            "diagnostics": verdict["diagnostics"],
            "compiled": verdict["compiled"],
            "passed": verdict["passed"],
        }
        self.triples_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.triples_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return verdict


def _claim_session(run_dir: Path, task_id: str, arm: str) -> None:
    """Atomically claim (run_id, task, arm) via an O_EXCL lock file.

    The pinned triple schema carries no session id, so a second session
    for the same (run_id, task, arm) would be silently conflated with
    the first by ``report`` (attempts summed past the cap, inconsistent
    rates). Refuse the duplicate up front instead.
    """
    locks = run_dir / ".sessions"
    lock_path = locks / f"{task_id}.{arm}.lock"
    try:
        locks.mkdir(parents=True, exist_ok=True)
        lock_path.touch(exist_ok=False)
    except FileExistsError:
        raise HarnessError(
            f"a session for task '{task_id}' arm '{arm}' already exists "
            f"in run '{run_dir.name}'; use a fresh run_id"
        ) from None
    except OSError as exc:
        raise HarnessError(
            f"cannot create session lock '{lock_path}': {exc}"
        ) from exc


def new_session(
    task_id: str,
    arm: str,
    run_id: str,
    *,
    tasks_path: str | Path | None = None,
    results_root: str | Path | None = None,
) -> Session:
    """Open a session (section 45): max 4 submissions, triples logged to
    ``eval/results/<run_id>/triples.jsonl``."""
    _require_arm(arm)
    if (
        not run_id
        or run_id in (".", "..")
        or "/" in run_id
        or "\\" in run_id
        or "\x00" in run_id
    ):
        raise HarnessError(f"invalid run_id '{run_id}'")
    task = _get_task(task_id, tasks_path)
    root = Path(results_root) if results_root is not None else RESULTS_ROOT
    triples_path = root / run_id / "triples.jsonl"
    _claim_session(triples_path.parent, task_id, arm)
    return Session(task, arm, run_id, triples_path)


# ------------------------------------------------------------------ report


def _first(records: list[dict]) -> dict:
    return min(records, key=lambda rec: rec["attempt"])


def _attempts_to(records: list[dict], key: str) -> int:
    """First attempt number achieving `key`; failures count as cap+1."""
    for rec in sorted(records, key=lambda r: r["attempt"]):
        if rec.get(key):
            return rec["attempt"]
    return MAX_ATTEMPTS + 1


def _stats(sessions: list[list[dict]]) -> dict:
    """Section-45 aggregates over a list of sessions (attempt lists)."""
    count = len(sessions)
    attempts = sum(len(records) for records in sessions)
    histogram: Counter[str] = Counter()
    for records in sessions:
        for rec in records:
            for diag in rec.get("diagnostics", []):
                histogram[str(diag.get("code", "?"))] += 1
    if count:
        first_compile = sum(
            bool(_first(r).get("compiled")) for r in sessions
        ) / count
        first_pass = sum(bool(_first(r).get("passed")) for r in sessions) / count
        mean_to_compile = (
            sum(_attempts_to(r, "compiled") for r in sessions) / count
        )
        mean_to_pass = sum(_attempts_to(r, "passed") for r in sessions) / count
    else:
        first_compile = first_pass = mean_to_compile = mean_to_pass = 0.0
    return {
        "sessions": count,
        "tasks": count,
        "attempts": attempts,
        "first_attempt_compile_rate": first_compile,
        "first_attempt_pass_rate": first_pass,
        "mean_attempts_to_compile": mean_to_compile,
        "mean_attempts_to_pass": mean_to_pass,
        "diagnostic_histogram": dict(sorted(histogram.items())),
    }


def _validate_triple(rec: object, triples_file: Path, line_no: int) -> dict:
    """Reject corrupt triple shapes with a HarnessError (exit-2 JSON on
    the CLI) instead of a raw traceback deep in aggregation."""

    def bad(reason: str) -> HarnessError:
        return HarnessError(
            f"malformed triple at {triples_file}:{line_no}: {reason}"
        )

    if not isinstance(rec, dict):
        raise bad("not a JSON object")
    for key in ("task", "arm"):
        if key not in rec:
            raise bad(f"missing key '{key}'")
    attempt = rec.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise bad("'attempt' must be an integer")
    diagnostics = rec.get("diagnostics", [])
    if not isinstance(diagnostics, list) or not all(
        isinstance(diag, dict) for diag in diagnostics
    ):
        raise bad("'diagnostics' must be a list of objects")
    return rec


def aggregate_report(results_dir: str | Path) -> dict:
    """``report`` aggregates over every triples.jsonl under results_dir."""
    root = Path(results_dir)
    if not root.is_dir():
        raise HarnessError(f"results directory '{results_dir}' not found")
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for triples_file in sorted(root.rglob("triples.jsonl")):
        try:
            text = triples_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise HarnessError(
                f"cannot read triples file '{triples_file}': {exc}"
            ) from exc
        for line_no, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HarnessError(
                    f"malformed triple at {triples_file}:{line_no}: {exc}"
                ) from exc
            rec = _validate_triple(rec, triples_file, line_no)
            key = (str(triples_file), rec["task"], rec["arm"])
            grouped.setdefault(key, []).append(rec)
    by_arm: dict[str, list[list[dict]]] = {}
    for (_file, _task, arm), records in sorted(grouped.items()):
        by_arm.setdefault(arm, []).append(records)
    totals = _stats([s for arm in by_arm.values() for s in arm])
    return {
        "arms": {arm: _stats(sessions) for arm, sessions in by_arm.items()},
        "codes": dict(totals["diagnostic_histogram"]),
        "totals": totals,
    }


# ------------------------------------------------------------------ CLI


def _emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def _print_diagnostics(diagnostics: list[dict]) -> None:
    for diag in diagnostics:
        print(
            f"error[{diag['code']}] {diag['line']}:{diag['col']}: "
            f"{diag['message']}"
        )
        for note in diag.get("notes", []):
            print(f"  note {note['line']}:{note['col']}")


def _cmd_check(args: argparse.Namespace) -> int:
    verdict = check_file(args.arm, args.file)
    if args.json:
        _emit_json(verdict)
    else:
        _print_diagnostics(verdict["diagnostics"])
    return 0 if verdict["ok"] else 1


def _cmd_run(args: argparse.Namespace) -> int:
    verdict = run_task(args.arm, args.file, args.task)
    if args.json:
        _emit_json(verdict)
    else:
        print(f"compiled: {'yes' if verdict['compiled'] else 'no'}")
        print(f"passed: {'yes' if verdict['passed'] else 'no'}")
        if verdict["diagnostics"]:
            _print_diagnostics(verdict["diagnostics"])
        if verdict["stdout"]:
            print("--- stdout ---")
            sys.stdout.write(verdict["stdout"])
    return 0 if verdict["passed"] else 1


def _cmd_prompt(args: argparse.Namespace) -> int:
    prompt = build_prompt(args.arm, args.task, shots=args.shots)
    if args.json:
        _emit_json({"prompt": prompt})
    else:
        sys.stdout.write(prompt)
    return 0


def _format_report_arm(name: str, stats: dict) -> str:
    lines = [
        f"{name}: sessions={stats['sessions']} attempts={stats['attempts']}"
        f" first_compile={stats['first_attempt_compile_rate']:.2f}"
        f" first_pass={stats['first_attempt_pass_rate']:.2f}"
        f" mean_to_compile={stats['mean_attempts_to_compile']:.2f}"
        f" mean_to_pass={stats['mean_attempts_to_pass']:.2f}"
    ]
    for code, count in stats["diagnostic_histogram"].items():
        lines.append(f"  {code} x{count}")
    return "\n".join(lines)


def _cmd_report(args: argparse.Namespace) -> int:
    report = aggregate_report(args.results)
    if args.json:
        _emit_json(report)
    else:
        for arm, stats in report["arms"].items():
            print(_format_report_arm(f"arm {arm}", stats))
        print(_format_report_arm("totals", report["totals"]))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.harness",
        description="Oxide evaluation harness (SPEC.md section 45).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="structured diagnostics for a file")
    check.add_argument("--arm", required=True, choices=ARMS)
    check.add_argument("--file", required=True)
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=_cmd_check)

    run = sub.add_parser("run", help="full verdict against a corpus task")
    run.add_argument("--arm", required=True, choices=ARMS)
    run.add_argument("--file", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=_cmd_run)

    prompt = sub.add_parser("prompt", help="emit the complete solver prompt")
    prompt.add_argument("--arm", required=True, choices=ARMS)
    prompt.add_argument("--task", required=True)
    prompt.add_argument("--shots", type=int, default=0)
    prompt.add_argument("--json", action="store_true")
    prompt.set_defaults(handler=_cmd_prompt)

    report = sub.add_parser("report", help="aggregate a results directory")
    report.add_argument("--results", required=True)
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except HarnessError as exc:
        if getattr(args, "json", False):
            _emit_json({"ok": False, "error": str(exc)})
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
