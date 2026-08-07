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
