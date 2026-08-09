"""Blind tests for the explicit-Oxide dialect (SPEC Part VIII, sections 41/43).

All dialect behavior is exercised through the only contractual surface:
``python3 main.py --dialect=explicit [--json] [--check] <file.ox>``.
The byte-identical check compares the dialect pipeline's emitted Rust for the
hand-annotated E1 program against ``src.codegen.rust.transpile`` on core W1.

Written blind against SPEC.md Part VIII; not executed by the author.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.codegen.rust import transpile

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN = REPO_ROOT / "main.py"

# ---------------------------------------------------------------------------
# Program sources
# ---------------------------------------------------------------------------

# Core W1 (SPEC section 36) — the reference program for the byte-identical check.
W1_CORE = """\
fn first_big(v: Vec<Int>) -> Int {
    let found = -1
    for x in v {
        if x > 10 {
            found = x
            break
        }
    }
    found
}

fn main() {
    print(first_big(push(push(push(vec(), 3), 42), 99)))
}
"""

# E1 (SPEC section 41): W1 hand-annotated per core analysis of W1.
# param_modes(first_big) == ('read',)  ->  v declared &Vec<Int>.
# The only read-class use of non-copy v is the for-iterable  ->  &v.
# found / x are Int (copy)  ->  always bare.  Core W1 synthesizes no
# named-var DropPoints, so E1 contains no drop statements.
E1 = """\
fn first_big(v: &Vec<Int>) -> Int {
    let found = -1
    for x in &v {
        if x > 10 {
            found = x
            break
        }
    }
    found
}

fn main() {
    print(first_big(push(push(push(vec(), 3), 42), 99)))
}
"""

# Drop-kind battery: one correctly-annotated program per DropPoint kind.

# after-stmt: v's final use is a read inside the print statement.
AFTER_STMT = """\
fn main() {
    let v = push(vec(), 1)
    print(len(&v))
    drop v
}
"""

# block-end: v never used after its definition.
BLOCK_END = """\
fn main() {
    let v = push(vec(), 1)
    drop v
}
"""

# branch-end via absent else (core S4 shape): v moved only in the then arm,
# dead after the merge.  The drop for the still-owned (else) edge is written
# as an explicit `else { drop v }` (which strips to core's absent else);
# the alternative reading places `drop v` after the if statement.
BRANCH_END_ELSE_FORM = """\
fn g(c: Bool, v: Vec<Int>) {
    if c {
        let w = push(v, 1)
        drop w
    } else {
        drop v
    }
}
"""

BRANCH_END_AFTER_IF_FORM = """\
fn g(c: Bool, v: Vec<Int>) {
    if c {
        let w = push(v, 1)
        drop w
    }
    drop v
}
"""

# before-return: v still owned at the early return (plus one after-stmt drop
# after v's final read; the tail is copy-typed so every drop is writable).
BEFORE_RETURN = """\
fn f(c: Bool) -> Int {
    let v = push(vec(), 1)
    if c {
        drop v
        return 0
    }
    let n = len(&v)
    drop v
    n
}
"""

# before-jump: loop-body-scoped v still owned at the break (plus its
# block-end drop on the fall-through path).
BEFORE_JUMP = """\
fn main() {
    for i in range(0, 4) {
        let v = push(vec(), i)
        if i > 1 {
            drop v
            break
        }
        drop v
    }
}
"""

# Synthetic drop anchors (regressions). Upstream root causes: sema/cfg.py
# wraps a chained `else if` (_if) and an expression-body match arm (_match)
# in a SYNTHETIC BlockNode carrying the chained If's / arm expression's
# span, and sema/linear.py anchors block-end drops there (_merge_if's
# merge-hoisted still-owned edge; _drop_arm_binders / _merge_match for
# arms). No drop statement is syntactically writable at those anchors, so
# the same-shape annotation must be accepted WITHOUT a written drop —
# codegen synthesizes it from the stripped AST (nested-else / block-arm
# rewrite). Previously EX0003 demanded a drop exactly where EX0004
# rejected every placement, making the chain / expr-arm shapes unwritable.

# v is moved in the then arm only; core hoists its drop onto the chain
# edge as block-end anchored at the synthetic `if c2` block.
CHAIN_HOISTED = """\
fn f(c1: Bool, c2: Bool, v: Vec<Int>) {
    if c1 {
        let a = push(v, 1)
        drop a
    } else if c2 {
        print(1)
    } else {
        print(2)
    }
}
"""

# The same program in core Oxide (annotations stripped): the byte-identical
# reference for the chain emission.
CHAIN_HOISTED_CORE = """\
fn f(c1: Bool, c2: Bool, v: Vec<Int>) {
    if c1 {
        let a = push(v, 1)
    } else if c2 {
        print(1)
    } else {
        print(2)
    }
}
"""

# xs is an unconsumed binder of an expression-body arm; core anchors its
# block-end drop at the arm EXPRESSION's span.
EXPR_ARM_BINDER = """\
enum Box2 {
    Full(Vec<Int>),
    Empty2,
}

fn size(b: Box2) -> Int {
    match b {
        Full(xs) => len(&xs),
        Empty2 => 0,
    }
}
"""

EXPR_ARM_BINDER_CORE = """\
enum Box2 {
    Full(Vec<Int>),
    Empty2,
}

fn size(b: Box2) -> Int {
    match b {
        Full(xs) => len(xs),
        Empty2 => 0,
    }
}
"""

# Same mechanism for a merge-hoisted OUTER var: v is moved in the Some
# arm; the still-owned None arm's drop anchors at the `vec()` expression.
EXPR_ARM_OUTER = """\
fn pick(o: Option<Int>, v: Vec<Int>) -> Vec<Int> {
    match o {
        Some(x) => push(v, x),
        None => vec(),
    }
}
"""

# Copy vars are always bare and never dropped.
COPY_BARE = """\
fn main() {
    let n = 5
    let m = n + 1
    if m > n {
        print(m)
    }
    print(n)
}
"""

# Pinned dialect suggestion strings (SPEC section 41).
SUGGESTIONS = {
    "EX0001": "This use consumes the value; remove the &.",
    "EX0002": "This use only reads the value; write &name.",
    "EX0003": "This value's last use is here; add 'drop name' at the required point.",
    "EX0004": (
        "No drop belongs here: the value is not owned/dead at this point. "
        "Remove or move this drop."
    ),
    "EX0005": (
        "Parameter mode is wrong: read-only parameters are declared name: "
        "&Type, consumed parameters name: Type."
    ),
}

GARBAGE_INPUTS = ["&", "drop", "&&x", "drop 5"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, source: str, name: str = "prog.ox") -> Path:
    path = Path(tmp_path) / name
    path.write_text(source, encoding="utf-8")
    return path


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        timeout=120,
    )


def _dialect_json(
    tmp_path: Path, source: str, *flags: str, name: str = "prog.ox"
) -> tuple[subprocess.CompletedProcess[str], dict]:
    path = _write(tmp_path, source, name)
    proc = _run_cli(["--dialect=explicit", "--json", *flags, str(path)])
    # SPEC section 39: diagnostics never go to stderr in json mode.
    assert proc.stderr == "", f"json mode wrote to stderr: {proc.stderr!r}"
    try:
        obj = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure detail
        raise AssertionError(f"stdout is not one JSON object: {proc.stdout!r}") from exc
    return proc, obj


def _codes(obj: dict) -> list[str]:
    return [d["code"] for d in obj["diagnostics"]]


def _mutate(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"mutation target not unique: {old!r}"
    return source.replace(old, new)


# ---------------------------------------------------------------------------
# 1. E1 golden: accept + byte-identical Rust vs core W1
# ---------------------------------------------------------------------------


def test_e1_accepted_clean(tmp_path: Path) -> None:
    proc, obj = _dialect_json(tmp_path, E1)
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    assert isinstance(obj["rust"], str)


def test_e1_rust_byte_identical_to_core_w1(tmp_path: Path) -> None:
    core_rust, core_diags = transpile(W1_CORE)
    assert core_diags == []
    assert core_rust is not None
    _, obj = _dialect_json(tmp_path, E1)
    assert obj["ok"] is True
    assert obj["rust"] == core_rust


def test_e1_variadic_vec_literal_is_byte_identical_to_the_push_chain(
    tmp_path: Path,
) -> None:
    """SPEC.md §55: the variadic `vec(...)` desugar lives in the shared
    front-end mixin (`_ExprParserMixin`), inherited unchanged by
    `ExplicitParser` -- so `vec(3, 42, 99)` written in the explicit
    dialect must emit the same Rust as the hand-annotated push-chain
    form (E1), which is itself already proven byte-identical to core."""
    e1_vec = _mutate(
        E1, "push(push(push(vec(), 3), 42), 99)", "vec(3, 42, 99)"
    )
    core_rust, core_diags = transpile(W1_CORE)
    assert core_diags == []
    assert core_rust is not None
    _, obj = _dialect_json(tmp_path, e1_vec)
    assert obj["ok"] is True
    assert obj["rust"] == core_rust


def test_e1_text_mode_success(tmp_path: Path) -> None:
    path = _write(tmp_path, E1)
    proc = _run_cli(["--dialect=explicit", str(path)])
    assert proc.returncode == 0
    assert "fn first_big" in proc.stdout
    assert proc.stderr == ""


# ---------------------------------------------------------------------------
# 2. E1 single mutations -> exact EX codes
# ---------------------------------------------------------------------------

E1_MUTATIONS = [
    pytest.param("found = x", "found = &x", "EX0001", id="EX0001-amp-on-consuming-use"),
    pytest.param("for x in &v {", "for x in v {", "EX0002", id="EX0002-bare-read"),
    pytest.param(
        "    found\n}", "    drop v\n    found\n}", "EX0004", id="EX0004-extra-drop"
    ),
    pytest.param(
        "fn first_big(v: &Vec<Int>)",
        "fn first_big(v: Vec<Int>)",
        "EX0005",
        id="EX0005-own-instead-of-read-param",
    ),
]


@pytest.mark.parametrize(("old", "new", "code"), E1_MUTATIONS)
def test_e1_single_mutation_exact_code(
    tmp_path: Path, old: str, new: str, code: str
) -> None:
    proc, obj = _dialect_json(tmp_path, _mutate(E1, old, new))
    assert proc.returncode == 1
    assert obj["ok"] is False
    assert obj["rust"] is None
    assert _codes(obj) == [code]
    assert obj["diagnostics"][0]["suggestion"] == SUGGESTIONS[code]


def test_missing_drop_reports_ex0003(tmp_path: Path) -> None:
    # Note: literal E1/W1 has no named-var DropPoints, so section 41's
    # "required drop removed" mutation is exercised on the after-stmt
    # program, which requires exactly one drop.
    src = _mutate(AFTER_STMT, "    drop v\n", "")
    proc, obj = _dialect_json(tmp_path, src)
    assert proc.returncode == 1
    assert obj["ok"] is False
    assert _codes(obj) == ["EX0003"]
    assert obj["diagnostics"][0]["suggestion"] == SUGGESTIONS["EX0003"]


def test_e1_mutation_text_mode_renders_ex_code(tmp_path: Path) -> None:
    src = _mutate(E1, "found = x", "found = &x")
    path = _write(tmp_path, src)
    proc = _run_cli(["--dialect=explicit", str(path)])
    assert proc.returncode == 1
    assert "EX0001" in proc.stderr
    assert proc.stdout == ""


# ---------------------------------------------------------------------------
# 3. One correctly-annotated program per drop kind
# ---------------------------------------------------------------------------

DROP_KIND_PROGRAMS = [
    pytest.param(AFTER_STMT, id="after-stmt"),
    pytest.param(BLOCK_END, id="block-end"),
    pytest.param(BEFORE_RETURN, id="before-return"),
    pytest.param(BEFORE_JUMP, id="before-jump"),
]


@pytest.mark.parametrize("source", DROP_KIND_PROGRAMS)
def test_drop_kind_program_accepted(tmp_path: Path, source: str) -> None:
    proc, obj = _dialect_json(tmp_path, source)
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    assert isinstance(obj["rust"], str)


def test_branch_end_absent_else_program_accepted(tmp_path: Path) -> None:
    # The written position of a branch-end drop is not pinned by section 41:
    # "in the still-owned arm" suggests materializing `else { drop v }`
    # (stripping back to core's absent else), while "after the anchor" would
    # place `drop v` after the if.  A conforming dialect must accept at
    # least one of these forms.
    outcomes = []
    forms = (BRANCH_END_ELSE_FORM, BRANCH_END_AFTER_IF_FORM)
    for index, source in enumerate(forms):
        _, obj = _dialect_json(tmp_path, source, name=f"branch{index}.ox")
        outcomes.append(obj["ok"] is True and obj["diagnostics"] == [])
    assert any(outcomes), "neither branch-end drop annotation form was accepted"


# ---------------------------------------------------------------------------
# 3b. Synthetic drop anchors: chain-/arm-shape programs need no written drop
# ---------------------------------------------------------------------------


def test_else_if_chain_with_hoisted_drop_accepted(tmp_path: Path) -> None:
    # Regression: the merge-hoisted drop of v anchors block-end at the
    # synthetic block wrapping the chained `if c2` — an unwritable point.
    # The chain-preserving annotation (no drop for v) must be accepted and
    # emit byte-identical Rust to the core chain program.
    proc, obj = _dialect_json(tmp_path, CHAIN_HOISTED)
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    core_rust, core_diags = transpile(CHAIN_HOISTED_CORE)
    assert core_diags == []
    assert obj["rust"] == core_rust


def test_expr_body_arm_drops_accepted_without_written_drop(
    tmp_path: Path,
) -> None:
    # Regression: block-end drops anchored at an expression-body arm's
    # expression span (unconsumed binder xs; merge-hoisted outer var v)
    # are unwritable; the same-shape annotation must be accepted.
    proc, obj = _dialect_json(tmp_path, EXPR_ARM_BINDER)
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    core_rust, core_diags = transpile(EXPR_ARM_BINDER_CORE)
    assert core_diags == []
    assert obj["rust"] == core_rust
    _, outer_obj = _dialect_json(tmp_path, EXPR_ARM_OUTER, name="outer.ox")
    assert outer_obj["ok"] is True
    assert outer_obj["diagnostics"] == []


# ---------------------------------------------------------------------------
# 4. Copy-var bareness
# ---------------------------------------------------------------------------


def test_copy_vars_bare_accepted(tmp_path: Path) -> None:
    proc, obj = _dialect_json(tmp_path, COPY_BARE)
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["diagnostics"] == []


def test_amp_on_copy_use_is_ex0001(tmp_path: Path) -> None:
    src = _mutate(COPY_BARE, "print(n)", "print(&n)")
    proc, obj = _dialect_json(tmp_path, src)
    assert proc.returncode == 1
    assert _codes(obj) == ["EX0001"]
    assert obj["diagnostics"][0]["suggestion"] == SUGGESTIONS["EX0001"]


# ---------------------------------------------------------------------------
# 5. --dialect=explicit composes with --json / --check
# ---------------------------------------------------------------------------


def test_ex_codes_share_json_schema(tmp_path: Path) -> None:
    src = _mutate(E1, "for x in &v {", "for x in v {")
    proc, obj = _dialect_json(tmp_path, src)
    assert proc.returncode == 1
    assert set(obj) == {"ok", "rust", "diagnostics"}
    assert obj["ok"] is False
    assert obj["rust"] is None
    assert len(obj["diagnostics"]) == 1
    diag = obj["diagnostics"][0]
    for key in (
        "code",
        "message",
        "line",
        "col",
        "end_line",
        "end_col",
        "notes",
        "suggestion",
    ):
        assert key in diag, f"missing diagnostic key {key!r}"
    assert diag["code"] == "EX0002"
    assert isinstance(diag["message"], str) and diag["message"] != ""
    assert isinstance(diag["line"], int) and diag["line"] >= 1
    assert isinstance(diag["col"], int) and diag["col"] >= 1
    assert isinstance(diag["end_line"], int) and diag["end_line"] >= 1
    assert isinstance(diag["end_col"], int) and diag["end_col"] >= 1
    assert isinstance(diag["notes"], list)


def test_check_composes_clean(tmp_path: Path) -> None:
    proc, obj = _dialect_json(tmp_path, E1, "--check")
    assert proc.returncode == 0
    assert obj["ok"] is True
    assert obj["rust"] is None
    assert obj["diagnostics"] == []


def test_check_composes_on_error(tmp_path: Path) -> None:
    src = _mutate(E1, "found = x", "found = &x")
    proc, obj = _dialect_json(tmp_path, src, "--check")
    assert proc.returncode == 1
    assert obj["ok"] is False
    assert obj["rust"] is None
    assert _codes(obj) == ["EX0001"]


# ---------------------------------------------------------------------------
# 6. Dialect garbage never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", GARBAGE_INPUTS)
def test_dialect_garbage_never_raises_json(tmp_path: Path, source: str) -> None:
    path = _write(tmp_path, source + "\n", name="garbage.ox")
    proc = _run_cli(["--dialect=explicit", "--json", str(path)])
    assert proc.returncode in (0, 1)
    assert proc.stderr == ""
    obj = json.loads(proc.stdout)
    assert obj["ok"] is False


@pytest.mark.parametrize("source", GARBAGE_INPUTS)
def test_dialect_garbage_never_raises_text(tmp_path: Path, source: str) -> None:
    path = _write(tmp_path, source + "\n", name="garbage.ox")
    proc = _run_cli(["--dialect=explicit", str(path)])
    assert proc.returncode in (0, 1)
    assert "Traceback" not in proc.stderr
