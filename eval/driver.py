"""Phase 6a run driver (SPEC Part X, sections 5 and 6.4).

Each (model, shots, seed) combination is its own harness run_id. That is
what makes this phase additive: harness._claim_session locks on
(run_id, task, arm) and the pinned triple schema carries no model or seed
field, so sharing a run_id across the grid would silently conflate cells.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

from eval import harness
from eval.extract import extract
from eval.models import ModelClient, ModelError
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
    make_client: Callable[[str], ModelClient],
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
