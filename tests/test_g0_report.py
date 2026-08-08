"""Tests for the G0 profiler (eval/g0_report.py).

The discipline this module enforces on itself: it must reproduce the
published 6a-pilot numbers (eval/results/6a-pilot/REPORT.md) before it is
trusted to read new G0 data. ``test_profiler_reproduces_the_pilot_7b_row``
is that self-check, made a first-class test rather than a one-off manual
run.
"""

import json
from pathlib import Path

from eval import g0_report
from eval.driver import build_run_id


def test_profiler_reproduces_the_pilot_7b_row():
    # Published in eval/results/6a-pilot/REPORT.md: 7B 0-shot first-compile
    # oxide 2/20, explicit 0/20, rust 20/20; rust final-pass 12/20.
    out = g0_report.profile(
        root=Path("eval/results/6a-pilot"),
        models=["qwen7b"], seeds=[1], prefix="6a",
    )
    row = out["qwen7b"]
    assert row["oxide"]["first_compiled"] == 2 / 20
    assert row["explicit"]["first_compiled"] == 0 / 20
    assert row["rust"]["first_compiled"] == 20 / 20
    assert row["rust"]["final_passed"] == 12 / 20


def _write_run(root, slug, seed, cells, triples, prefix="g0c"):
    run_dir = root / build_run_id(slug, 0, seed, prefix=prefix)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "cells.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in cells), encoding="utf-8"
    )
    (run_dir / "triples.jsonl").write_text(
        "".join(json.dumps(t) + "\n" for t in triples), encoding="utf-8"
    )
    return run_dir


def _minimal_cell(task, arm, passed=False):
    """Only the fields profile()'s by_arm block and rollup.paired_delta
    actually read -- first_compiled/first_passed/final_passed plus task
    and arm."""
    return {
        "task": task, "arm": arm,
        "first_compiled": passed, "first_passed": passed,
        "final_passed": passed,
    }


def test_profiler_buckets_first_attempt_codes_by_stage(tmp_path):
    # OX0203 is a resolve-stage code (OX02xx); OX0400 is linearity
    # (OX04xx) -- the stage buckets pinned in the task brief.
    cells = [
        _minimal_cell("t01", "oxide"),
        _minimal_cell("t01", "explicit"),
        _minimal_cell("t01", "rust", passed=True),
    ]
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0203"}]},
        {"task": "t01", "arm": "explicit", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}]},
        # rust diagnostics must never land in the stage histogram.
        {"task": "t01", "arm": "rust", "attempt": 1, "diagnostics": []},
    ]
    _write_run(tmp_path, "qwen7b", 1, cells, triples)

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    stages = out["qwen7b"]["stage_hist_first"]
    assert stages["resolve"] == 1
    assert stages["linearity"] == 1
    assert stages["lexer"] == 0
    assert stages["parser"] == 0
    assert stages["types"] == 0


def test_profiler_counts_gate_occurrences_and_sessions_separately(tmp_path):
    # One session (t01/oxide) carries TWO OX04xx diagnostics; a second
    # session (t02/oxide) carries ONE. Occurrences must sum the raw
    # diagnostic count (3); sessions must count distinct sessions (2) --
    # the two numbers diverge exactly when a session emits more than one
    # linearity diagnostic, which is the case this test pins down.
    cells = [
        _minimal_cell("t01", "oxide"),
        _minimal_cell("t02", "oxide"),
        _minimal_cell("t01", "explicit"),
        _minimal_cell("t01", "rust", passed=True),
    ]
    triples = [
        {"task": "t01", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0400"}, {"code": "OX0401"}]},
        {"task": "t02", "arm": "oxide", "attempt": 1,
         "diagnostics": [{"code": "OX0402"}]},
        # a second (repair) attempt on the same session must not double
        # count -- the gate metric is pooled FIRST-attempt only.
        {"task": "t01", "arm": "oxide", "attempt": 2,
         "diagnostics": [{"code": "OX0400"}]},
    ]
    _write_run(tmp_path, "qwen7b", 1, cells, triples)

    out = g0_report.profile(
        root=tmp_path, models=["qwen7b"], seeds=[1], prefix="g0c"
    )
    gate = out["qwen7b"]["gate"]
    assert gate["occurrences"] == 3
    assert gate["sessions"] == 2
