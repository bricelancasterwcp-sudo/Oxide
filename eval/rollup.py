"""Phase 6a grid aggregation (SPEC Part X, sections 3 and 6.5).

The primary readout is the PAIRED-BY-TASK delta. Both arms run the same
20 tasks and task difficulty dominates the variance, so pairing cancels
it and roughly halves the detectable effect. Comparing marginal arm
rates instead is prohibited as the primary statistic (section 3).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from eval import harness
from eval.driver import (
    MODELS,
    build_run_id,
    is_complete,
    parse_seeds,
    unknown_slugs,
)

BAND_PP = 5.0
INSUFFICIENT = "insufficient-data"
DASH = "—"


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


def paired_delta(
    oxide_cells: list[dict], explicit_cells: list[dict]
) -> float | None:
    """Mean per-task (oxide - explicit) first-attempt pass rate, in pp.

    On a balanced grid this equals the difference of marginal arm rates
    -- pairing does not move the point estimate. It moves the INTERVAL;
    see paired_se.

    ``None`` when no task is present in both arms. Returning 0.0 there
    laundered emptiness into a number: classify(0.0) is
    "no-detectable-difference", so a point with zero observations
    rendered as a completed null result.
    """
    diffs = _per_task_differences(oxide_cells, explicit_cells)
    if not diffs:
        return None
    return round(100.0 * sum(diffs) / len(diffs), 4)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def paired_se(
    oxide_cells: list[dict], explicit_cells: list[dict]
) -> float | None:
    """SD(per-task differences)/sqrt(n), in pp -- the primary interval.

    This is what pairing actually buys: shared task difficulty cancels
    inside each difference, so the SD collapses in proportion to how
    strongly the arms correlate across tasks.

    ``None`` on an empty pairing (no interval exists); 0.0 at n=1, where
    a single difference genuinely has no spread to estimate.
    """
    diffs = _per_task_differences(oxide_cells, explicit_cells)
    if not diffs:
        return None
    if len(diffs) < 2:
        return 0.0
    return round(100.0 * _stdev(diffs) / (len(diffs) ** 0.5), 4)


def across_seed_se(cells_by_seed: list[list[dict]]) -> float | None:
    """SD(per-seed pass@1)/sqrt(seeds), in pp.

    Section 47's sampling-noise check, reported alongside the paired SE:
    the paired SE quantifies task-to-task spread, this one quantifies
    seed-to-seed spread at n=5. They answer different questions and the
    section requires both.

    ``None`` when no seed carried an observation; 0.0 at n=1, matching
    ``paired_se``: one observation has no spread to estimate.
    """
    rates = [
        100.0 * sum(c["first_passed"] for c in cells) / len(cells)
        for cells in cells_by_seed
        if cells
    ]
    if not rates:
        return None
    if len(rates) < 2:
        return 0.0
    return round(_stdev(rates) / (len(rates) ** 0.5), 4)


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


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_cells(run_dir: Path) -> list[dict]:
    return _load_jsonl(run_dir / "cells.jsonl")


def _load_triples(run_dir: Path) -> list[dict]:
    return _load_jsonl(run_dir / "triples.jsonl")


def diagnostic_histogram(triples: list[dict]) -> dict[str, dict[str, int]]:
    """arm -> code -> count over every diagnostic on every attempt.

    Section 50.5 names the per-code histogram the v0.3 gate deliverable:
    which diagnostics small models actually trip is the design signal,
    and it is invisible in a pass rate.
    """
    hist: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for record in triples:
        bucket = hist[record.get("arm", "?")]
        for diag in record.get("diagnostics") or []:
            bucket[diag.get("code", "?")] += 1
    return {
        arm: dict(sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])))
        for arm, codes in sorted(hist.items())
    }


def _per_task_counts(cells: list[dict]) -> dict[str, dict[str, int]]:
    """Per-task pass counts -- section 47 requires them reported so
    task-level effects stay visible behind the averaged delta."""
    counts: dict[str, dict[str, int]] = {}
    for cell in cells:
        row = counts.setdefault(
            cell["task"], {"trials": 0, "first_passed": 0, "final_passed": 0}
        )
        row["trials"] += 1
        row["first_passed"] += int(cell["first_passed"])
        row["final_passed"] += int(cell["final_passed"])
    return dict(sorted(counts.items()))


def _rate_of(cells: list[dict], key: str) -> float:
    return round(100.0 * sum(bool(c[key]) for c in cells) / len(cells), 2)


def _arm_stats(cells: list[dict]) -> dict:
    if not cells:
        return {"n": 0}
    total = len(cells)
    first = _rate_of(cells, "first_passed")
    final = _rate_of(cells, "final_passed")
    return {
        "n": total,
        "first_pass_rate": first,
        "final_pass_rate": final,
        # At 0.5B pass@1 saturates at zero long before compile rate does
        # (a pass needs compile AND correct output), so first-compile is
        # the most sensitive discriminator the bottom rung has.
        "first_compile_rate": _rate_of(cells, "first_compiled"),
        # Section 47 secondary: does an arm's diagnostics teach?
        "repair_lift_pp": round(final - first, 2),
        "mean_attempts_to_pass": round(
            sum(c["attempts_to_pass"] for c in cells) / total, 3
        ),
        "truncation_rate": round(
            100.0 * sum(any(c["truncated"]) for c in cells) / total, 2
        ),
        "contract_compliance_rate": round(
            100.0 * sum(all(c["contract_compliant"]) for c in cells) / total, 2
        ),
        # tokens_in is the prompt-length asymmetry across arms (section
        # 50.5); ms is the wall-clock the grid actually cost.
        "tokens_in": sum(c["tokens_in"] for c in cells),
        "tokens_out": sum(c["tokens_out"] for c in cells),
        "ms": sum(c["ms"] for c in cells),
        "mean_tokens_in": round(sum(c["tokens_in"] for c in cells) / total, 1),
        "mean_ms": round(sum(c["ms"] for c in cells) / total, 1),
        "per_task": _per_task_counts(cells),
    }


def _verdict(delta: float | None, arms: dict) -> str:
    """No observations, no verdict.

    ``paired_delta([], [])`` returning 0.0 made ``classify`` emit
    "no-detectable-difference" for a point with zero data, printed beside
    0% rates that were really absent data -- a row asserting a
    pre-registered reading on nothing at all.
    """
    if delta is None:
        return INSUFFICIENT
    if any(arms.get(arm, {}).get("n", 0) == 0 for arm in ("oxide", "explicit")):
        return INSUFFICIENT
    return classify(delta)


def _collect_point(
    root: Path, slug: str, shots: int, seeds: list[int]
) -> dict:
    """Everything section 47 and 50.5 pre-register, for one (model, shots)."""
    run_dirs = {seed: root / build_run_id(slug, shots, seed) for seed in seeds}
    by_seed = {seed: _load_cells(path) for seed, path in run_dirs.items()}
    cells = [cell for seed in seeds for cell in by_seed[seed]]
    triples = [rec for path in run_dirs.values() for rec in _load_triples(path)]

    arms: dict[str, dict] = {}
    for arm in harness.ARMS:
        rows = [c for c in cells if c["arm"] == arm]
        stats = _arm_stats(rows)
        stats["across_seed_se_pp"] = across_seed_se(
            [[c for c in by_seed[seed] if c["arm"] == arm] for seed in seeds]
        )
        arms[arm] = stats

    oxide = [c for c in cells if c["arm"] == "oxide"]
    explicit = [c for c in cells if c["arm"] == "explicit"]
    delta = paired_delta(oxide, explicit)
    return {
        "model_slug": slug,
        "model": MODELS.get(slug, slug),
        "shots": shots,
        "paired_delta_pp": delta,
        "paired_se_pp": paired_se(oxide, explicit),
        "unpaired_se_pp": unpaired_se(oxide, explicit),
        "verdict": _verdict(delta, arms),
        "arms": arms,
        "diagnostics": diagnostic_histogram(triples),
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
    points = [
        _collect_point(root, slug, shots, seeds)
        for slug in slugs
        for shots in shot_counts
    ]
    return {"missing": missing, "points": points}


def _fmt(value: float | None, spec: str = "+.1f") -> str:
    """A missing number renders as an em dash, never as zero."""
    return DASH if value is None else format(value, spec)


def _pct(value: float | None, spec: str = ".0f") -> str:
    return DASH if value is None else f"{format(value, spec)}%"


def _num(value: int | None) -> str:
    return DASH if value is None else f"{value}"


def _render_primary(points: list[dict]) -> list[str]:
    lines = [
        "| Model | Shots | Paired Δ (pp) | ± SE | Verdict "
        "| Oxide | Explicit | Rust |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for point in points:
        arms = point["arms"]
        empty = point["verdict"] == INSUFFICIENT
        delta = None if empty else point["paired_delta_pp"]
        se = None if empty else point.get("paired_se_pp")
        rates = " | ".join(
            _pct(arms.get(arm, {}).get("first_pass_rate")) for arm in harness.ARMS
        )
        lines.append(
            f"| {point['model_slug']} | {point['shots']} | {_fmt(delta)} "
            f"| {_fmt(se, '.1f')} | {point['verdict']} | {rates} |"
        )
    return lines


_ARM_COLUMNS = (
    "| Model | Shots | Arm | n | pass@1 | ± seed SE | first-compile "
    "| final | repair lift | attempts | trunc | contract "
    "| prompt tok | out tok | ms |"
)


def _render_arm_row(point: dict, arm: str) -> str:
    stats = point["arms"].get(arm, {"n": 0})
    return (
        f"| {point['model_slug']} | {point['shots']} | {arm} "
        f"| {stats.get('n', 0)} "
        f"| {_pct(stats.get('first_pass_rate'), '.1f')} "
        f"| {_fmt(stats.get('across_seed_se_pp'), '.1f')} "
        f"| {_pct(stats.get('first_compile_rate'), '.1f')} "
        f"| {_pct(stats.get('final_pass_rate'), '.1f')} "
        f"| {_fmt(stats.get('repair_lift_pp'))} "
        f"| {_fmt(stats.get('mean_attempts_to_pass'), '.2f')} "
        f"| {_pct(stats.get('truncation_rate'), '.1f')} "
        f"| {_pct(stats.get('contract_compliance_rate'), '.1f')} "
        f"| {_num(stats.get('tokens_in'))} | {_num(stats.get('tokens_out'))} "
        f"| {_num(stats.get('ms'))} |"
    )


def _render_arms(points: list[dict]) -> list[str]:
    lines = [
        "",
        "## Per-arm detail",
        "",
        "`first-compile` is the sensitive discriminator at the bottom rung:",
        "a pass needs compile *and* correct output, so pass@1 saturates at",
        "zero long before compile rate does. `repair lift` is final − first",
        "(section 47 secondary: does this arm's diagnostics teach?).",
        "`± seed SE` is the across-seed SE over the seeds that actually",
        "carried cells — a sampling-noise check, not the primary interval,",
        "which stays the paired SE above. Under `--partial` that is fewer",
        "than the pinned five, so the caption states no denominator.",
        "",
        _ARM_COLUMNS,
        "|" + "---|" * 15,
    ]
    for point in points:
        lines += [_render_arm_row(point, arm) for arm in harness.ARMS]
    return lines


def _render_diagnostics(points: list[dict]) -> list[str]:
    lines = [
        "",
        "## Diagnostic code histogram (v0.3 gate deliverable)",
        "",
        "| Model | Shots | Arm | Code | Count |",
        "|---|---|---|---|---|",
    ]
    rows = [
        f"| {point['model_slug']} | {point['shots']} | {arm} | {code} | {count} |"
        for point in points
        for arm, codes in point.get("diagnostics", {}).items()
        for code, count in codes.items()
    ]
    return lines + (rows or [f"| {DASH} | {DASH} | {DASH} | {DASH} | {DASH} |"])


def _render_per_task(points: list[dict]) -> list[str]:
    """First-attempt passes per task (section 47: keep task-level effects
    visible behind the averaged delta)."""
    lines = ["", "## Per-task first-attempt passes (k / trials)"]
    for point in points:
        lines += [
            "",
            f"### {point['model_slug']} — {point['shots']}-shot",
            "",
            "| Task | " + " | ".join(harness.ARMS) + " |",
            "|---|" + "---|" * len(harness.ARMS),
        ]
        per_task = {
            arm: point["arms"].get(arm, {}).get("per_task", {})
            for arm in harness.ARMS
        }
        tasks = sorted({task for rows in per_task.values() for task in rows})
        for task in tasks:
            cells = [
                DASH
                if task not in per_task[arm]
                else f"{per_task[arm][task]['first_passed']}"
                f"/{per_task[arm][task]['trials']}"
                for arm in harness.ARMS
            ]
            lines.append(f"| {task} | " + " | ".join(cells) + " |")
        if not tasks:
            lines.append(f"| {DASH} |" + f" {DASH} |" * len(harness.ARMS))
    return lines


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
    lines += _render_primary(grid["points"])
    lines += [
        "",
        "Δ is the mean per-task (Oxide − explicit-Oxide) first-attempt pass",
        "rate; SE is `SD(per-task differences)/√n`. On a balanced grid the Δ",
        "equals the difference of marginal arm rates — pairing buys the",
        "narrower interval, not a different point estimate.",
        f"`{INSUFFICIENT}` with `{DASH}` cells means the point has **no",
        "observations** — it is absent data, not a measured 0%.",
    ]
    lines += _render_arms(grid["points"])
    lines += _render_diagnostics(grid["points"])
    lines += _render_per_task(grid["points"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.rollup")
    parser.add_argument("--results-root", default=str(harness.RESULTS_ROOT))
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--shots", default="0,3")
    parser.add_argument("--seeds", default="1-5")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    slugs = [s for s in args.models.split(",") if s]
    unknown = unknown_slugs(slugs)
    if unknown:
        print(f"unknown model slug(s): {unknown}; known: {sorted(MODELS)}",
              file=sys.stderr)
        return 2
    grid = aggregate(
        Path(args.results_root),
        slugs=slugs,
        shot_counts=[int(s) for s in args.shots.split(",") if s],
        seeds=parse_seeds(args.seeds),
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
