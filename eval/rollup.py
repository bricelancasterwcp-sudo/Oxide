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
