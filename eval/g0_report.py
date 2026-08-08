"""G0 diagnostic profiler (SPEC Part X, section 50.5's gate deliverable).

``rollup.py`` answers whether Oxide beats explicit-Oxide on pass rate.
This module answers a narrower, prior question: WHERE do small-model
Oxide generations actually fail -- lexer, parser, resolve, types, or the
OX04xx linearity gate the whole grid exists to measure? The 6a pilot
found every failure below the semantic layer (zero OX04xx across ~480
Oxide-arm attempts); this profiler is what turns that from a one-off
observation into a repeatable measurement over the real G0 grid.

Self-validation is load-bearing, not decorative: ``profile()`` must
reproduce the pilot's published numbers (``validate_pilot`` / the
``test_profiler_reproduces_the_pilot_7b_row`` test) before it is trusted
to read G0 data that hasn't been hand-checked yet. That is the ordering
Task 6 exists to enforce -- the profiler earns trust on known data first.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

from eval import rollup
from eval.driver import MODELS, build_run_id, is_complete, parse_seeds, unknown_slugs

_REPO_ROOT = Path(__file__).resolve().parent.parent
PILOT_ROOT = _REPO_ROOT / "eval" / "results" / "6a-pilot"

# The published 7B 0-shot pilot numbers this module must reproduce before
# it is trusted against new data (eval/results/6a-pilot/REPORT.md).
PILOT_MODEL = "qwen7b"
PILOT_SEEDS = [1]
PILOT_PREFIX = "6a"
PILOT_EXPECTATIONS = {
    ("oxide", "first_compiled"): 2 / 20,
    ("explicit", "first_compiled"): 0 / 20,
    ("rust", "first_compiled"): 20 / 20,
    ("rust", "final_passed"): 12 / 20,
}

STAGES = (
    ("lexer", lambda c: c == "OX0001"),
    ("parser", lambda c: c.startswith("OX01")),
    ("resolve", lambda c: c.startswith("OX02")),
    ("types", lambda c: c.startswith("OX03")),
    ("linearity", lambda c: c.startswith("OX04")),
)

ARMS = ("oxide", "explicit", "rust")
SAMPLES_PER_FAMILY = 5


def _stage(code: str) -> str | None:
    for name, pred in STAGES:
        if pred(code):
            return name
    return None


def missing_runs(
    root: Path, models: list[str], seeds: list[int], prefix: str
) -> list[str]:
    """Run ids among (models x seeds) that eval.driver.is_complete
    rejects -- missing entirely, or short of the pinned 60-cell session
    count. Mirrors eval.rollup.aggregate's grid-completeness guard, for
    the same reason: profiling an in-progress G0 root as though it were
    finished silently reports a partial measurement as a complete one.
    """
    return [
        run_id
        for slug in models
        for seed in seeds
        for run_id in (build_run_id(slug, 0, seed, prefix=prefix),)
        if not is_complete(root / run_id)
    ]


def profile(
    *, root: Path, models: list[str], seeds: list[int], prefix: str,
    partial: bool = False,
) -> dict:
    """Per-model diagnostic profile: arm rates, stage histograms, the
    OX04xx gate count, and the paired oxide/explicit delta.

    Only the oxide and explicit arms are profiled for diagnostic codes
    (SPEC's stage buckets are defined over those two arms); rust is
    still included in the per-arm rate table since first_compiled/
    final_passed on the rust arm is the reference point the pilot report
    cites (20/20, 12/20).

    Refuses to profile an incomplete root (see ``missing_runs``) unless
    ``partial=True``, in which case the missing/incomplete runs are
    skipped rather than raising. Either way, an arm left with zero cells
    after the runs that WERE read is a named, actionable error -- not a
    ``ZeroDivisionError`` -- since dividing by an empty denominator means
    the profile itself is malformed, not just incomplete.
    """
    missing = missing_runs(root, models, seeds, prefix)
    if missing and not partial:
        raise RuntimeError(
            f"incomplete G0 root {root}: {len(missing)} run(s) missing or "
            f"incomplete (first: {missing[0]}). Pass --partial to profile "
            f"the complete runs only -- a root silently missing "
            f"in-progress runs reads as a finished measurement."
        )
    missing_set = set(missing)
    out: dict[str, dict] = {}
    for slug in models:
        cells: list[dict] = []
        first_codes: Counter[str] = Counter()
        all_codes: Counter[str] = Counter()
        gate_occurrences = 0
        gate_sessions = 0
        for seed in seeds:
            run_id = build_run_id(slug, 0, seed, prefix=prefix)
            if run_id in missing_set:
                continue  # --partial: already validated above
            run_dir = root / run_id
            with open(run_dir / "cells.jsonl", encoding="utf-8") as handle:
                cells += [json.loads(line) for line in handle if line.strip()]
            with open(run_dir / "triples.jsonl", encoding="utf-8") as handle:
                for row in map(json.loads, (line for line in handle if line.strip())):
                    if row["arm"] == "rust":
                        continue
                    codes = [str(d.get("code", "?")) for d in row["diagnostics"]]
                    all_codes.update(codes)
                    if row["attempt"] == 1:
                        first_codes.update(codes)
                        ox04 = sum(1 for c in codes if c.startswith("OX04"))
                        gate_occurrences += ox04
                        gate_sessions += bool(ox04)
        by_arm: dict[str, dict] = {}
        for arm in ARMS:
            rows = [c for c in cells if c["arm"] == arm]
            if not rows:
                raise RuntimeError(
                    f"{slug}: zero cells for arm {arm!r} under {root} -- "
                    f"cannot compute a rate against an empty denominator "
                    f"({'complete runs only, --partial' if partial else 'all requested runs'})"
                )
            by_arm[arm] = {
                "n": len(rows),
                "first_compiled": sum(c["first_compiled"] for c in rows) / len(rows),
                "first_passed": sum(c["first_passed"] for c in rows) / len(rows),
                "final_passed": sum(c["final_passed"] for c in rows) / len(rows),
            }
        oxide = [c for c in cells if c["arm"] == "oxide"]
        explicit = [c for c in cells if c["arm"] == "explicit"]
        out[slug] = {
            **by_arm,
            "stage_hist_first": {
                s: sum(v for c, v in first_codes.items() if _stage(c) == s)
                for s, _ in STAGES
            },
            "stage_hist_all": {
                s: sum(v for c, v in all_codes.items() if _stage(c) == s)
                for s, _ in STAGES
            },
            "code_hist_first": dict(first_codes.most_common()),
            "gate": {"occurrences": gate_occurrences, "sessions": gate_sessions},
            "paired_delta": rollup.paired_delta(oxide, explicit),
            "paired_se": rollup.paired_se(oxide, explicit),
        }
    return out


def validate_pilot(root: Path = PILOT_ROOT) -> list[str]:
    """Reproduce the published 6a-pilot 7B row. Returns a list of mismatch
    descriptions; empty means the profiler reproduces the pilot exactly.

    This is the SAME check as ``test_profiler_reproduces_the_pilot_7b_row``,
    run inline so the CLI can gate itself without a test runner.
    """
    out = profile(
        root=root, models=[PILOT_MODEL], seeds=PILOT_SEEDS, prefix=PILOT_PREFIX
    )
    row = out[PILOT_MODEL]
    problems = []
    for (arm, key), expected in PILOT_EXPECTATIONS.items():
        actual = row[arm][key]
        if actual != expected:
            problems.append(
                f"{arm}.{key}: expected {expected!r} ({expected:.0%}), "
                f"got {actual!r}"
            )
    return problems


def _top_codes_by_family(code_hist_first: dict[str, int]) -> dict[str, list[str]]:
    """The top-5 codes within each stage bucket, in descending-count order
    (``code_hist_first`` is already sorted that way by ``profile``)."""
    by_family: dict[str, list[str]] = {}
    for code in code_hist_first:
        family = _stage(code)
        if family is None:
            continue
        bucket = by_family.setdefault(family, [])
        if len(bucket) < SAMPLES_PER_FAMILY:
            bucket.append(code)
    return by_family


def write_samples(
    *, root: Path, models: list[str], seeds: list[int], prefix: str,
    profiled: dict, n: int,
) -> int:
    """Copy up to ``n`` failing first-attempt raw sources per (stage,
    code) into ``<root>/samples/<stage>/<code>/`` for Task 7's manual
    review. Returns the number of files copied.

    Walks triples a second time (rather than threading source paths
    through ``profile``) because most invocations never pass
    ``--samples`` -- the copy is opt-in, expensive-ish I/O that the
    profiling pass itself should not pay for.
    """
    copied = 0
    for slug in models:
        wanted = {
            (family, code)
            for family, codes in _top_codes_by_family(
                profiled[slug]["code_hist_first"]
            ).items()
            for code in codes
        }
        if not wanted:
            continue
        counts: Counter[tuple[str, str]] = Counter()
        for seed in seeds:
            run_dir = root / build_run_id(slug, 0, seed, prefix=prefix)
            triples_path = run_dir / "triples.jsonl"
            if not triples_path.exists():
                continue
            with open(triples_path, encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            for row in rows:
                if row["arm"] == "rust" or row["attempt"] != 1 or row["passed"]:
                    continue
                codes_here = {str(d.get("code", "?")) for d in row["diagnostics"]}
                for family, code in wanted:
                    key = (family, code)
                    if code not in codes_here or counts[key] >= n:
                        continue
                    raw = run_dir / "raw" / f"{row['task']}.{row['arm']}.1.txt"
                    if not raw.exists():
                        continue
                    dest_dir = root / "samples" / family / code
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    # run_dir.name (the run id) already carries the slug,
                    # shots, and seed -- e.g. "g0c-qwen7b-0shot-s3" -- so
                    # prefixing with it alone (not slug + run id) keeps
                    # every sample attributable to its exact run without
                    # a redundant leading model-slug segment.
                    dest = dest_dir / f"{run_dir.name}.{row['task']}.{row['arm']}.txt"
                    shutil.copy2(raw, dest)
                    counts[key] += 1
                    copied += 1
    return copied


def _pct(rate: float) -> str:
    return f"{rate:.1%}"


def _print_report(out: dict, models: list[str]) -> None:
    """Per-model arm rates, stage histograms, the OX04xx gate count, and
    the paired oxide/explicit delta -- the read a human takes before
    reaching for the raw samples."""
    for slug in models:
        row = out[slug]
        print(f"\n== {slug} ({MODELS.get(slug, slug)}) ==")
        print(f"{'arm':<10}{'n':>4}{'first_compiled':>16}"
              f"{'first_passed':>15}{'final_passed':>15}")
        for arm in ARMS:
            stats = row[arm]
            print(
                f"{arm:<10}{stats['n']:>4}"
                f"{_pct(stats['first_compiled']):>16}"
                f"{_pct(stats['first_passed']):>15}"
                f"{_pct(stats['final_passed']):>15}"
            )

        print("\nstage histogram, first attempts (oxide+explicit):")
        for name, _ in STAGES:
            print(f"  {name:<10}{row['stage_hist_first'][name]:>6}")
        print("stage histogram, all attempts:")
        for name, _ in STAGES:
            print(f"  {name:<10}{row['stage_hist_all'][name]:>6}")

        gate = row["gate"]
        print(
            f"\nOX04xx gate: {gate['occurrences']} occurrence(s) across "
            f"{gate['sessions']} session(s)"
        )

        delta, se = row["paired_delta"], row["paired_se"]
        if delta is None:
            print("paired delta (oxide - explicit, first_passed): "
                  "insufficient-data")
        else:
            print(
                f"paired delta (oxide - explicit, first_passed): "
                f"{delta:+.1f}pp (SE {se:.1f}pp)"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.g0_report")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--models", default=None)
    parser.add_argument("--seeds", default=None)
    parser.add_argument("--run-prefix", default="g0c")
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--validate-pilot", action="store_true")
    parser.add_argument("--partial", action="store_true",
                        help="profile the complete runs only, skipping "
                             "any missing or in-progress ones instead of "
                             "refusing to run")
    args = parser.parse_args(argv)

    if args.validate_pilot:
        problems = validate_pilot()
        if problems:
            print("PILOT VALIDATION FAILED -- profiler does not reproduce "
                  "the published 6a pilot numbers:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print("pilot validation OK -- reproduces the published 6a pilot "
              "7B row exactly")
        if args.root is None:
            return 0

    if args.root is None:
        parser.error("--root is required unless only --validate-pilot is given")
    if not args.models:
        parser.error("--models is required")
    if not args.seeds:
        parser.error("--seeds is required")

    slugs = [s for s in args.models.split(",") if s]
    unknown = unknown_slugs(slugs)
    if unknown:
        print(f"unknown model slug(s): {unknown}; known: {sorted(MODELS)}",
              file=sys.stderr)
        return 2

    seeds = parse_seeds(args.seeds)
    if args.partial:
        skipped = missing_runs(args.root, slugs, seeds, args.run_prefix)
        if skipped:
            print(f"--partial: skipping {len(skipped)} incomplete run(s): "
                  + ", ".join(skipped), file=sys.stderr)
    out = profile(root=args.root, models=slugs, seeds=seeds,
                  prefix=args.run_prefix, partial=args.partial)
    _print_report(out, slugs)

    if args.samples:
        copied = write_samples(
            root=args.root, models=slugs, seeds=seeds, prefix=args.run_prefix,
            profiled=out, n=args.samples,
        )
        print(f"\nwrote {copied} sample file(s) to {args.root / 'samples'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
