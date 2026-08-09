"""Codegen tests for §56 field assignment (`s.f = e`).

Split out of ``tests/test_codegen.py`` as a cohesive group when that
module reached the project's 800-line cap. Same conventions: golden
assertions on the emitted text, plus a ``@requires_rustc`` compile check
wherever the claim is "and it compiles" -- accepted-implies-compiles is
the governing invariant, and every defect pinned here emits Rust that
``rustc`` rejects.

Per the section 25 test-author constraints, the only project imports are
``src.codegen.rust`` and the rustc locator.
"""

import shutil
import subprocess

import pytest

from eval.rustc_adapter import find_rustc
from src.codegen.rust import transpile

RUSTC: str | None = shutil.which(find_rustc())
requires_rustc = pytest.mark.skipif(RUSTC is None, reason="rustc not available")


# ---- Sources ----

# A COPY field. §36's implicit `.clone()` on a field READ is
# type-conditional, so this source cannot see a target routed through the
# field-access emitter -- FA_NONCOPY_SOURCE is what does that.
FA_SOURCE = (
    "struct P { x: Int, y: Int }\n"
    "fn main() { let p = P { x: 1, y: 2 }\n p.x = 5\n print(p.x) }"
)

# A NON-COPY field: `.clone()` on the read path is unconditional here, so
# a target built by the field-access emitter emits `p.name.clone() = s;`.
FA_NONCOPY_SOURCE = (
    "struct P { name: Str, n: Int }\n"
    "fn main() { let p = P { name: \"a\", n: 1 }\n"
    " let s = \"hello\"\n"
    " p.name = s\n"
    " print_str(p.name) }"
)

# A Rust-keyword field name as the assignment TARGET. The struct decl
# escapes it either way; only the assignment path exercises `escape` on
# the path segments.
FA_KEYWORD_SOURCE = (
    "struct S { move: Int }\n"
    "fn main() { let s = S { move: 1 }\n s.move = 8\n print(s.move) }"
)

# `==` occurring ONLY inside a field-assignment right-hand side. The
# operand struct needs `PartialEq`, which requires the derive scan to
# descend through the FieldAssign node.
FA_EQ_RHS_SOURCE = (
    "struct K { a: Int }\n"
    "struct F { flag: Bool }\n"
    "fn main() { let k1 = K { a: 1 }\n"
    " let k2 = K { a: 2 }\n"
    " let f = F { flag: false }\n"
    " f.flag = k1 == k2\n"
    " print(f.flag) }"
)


# ---- Helpers ----


def _transpile_ok(source: str) -> str:
    rust, diags = transpile(source)
    assert diags == [], diags
    assert rust is not None
    return rust


def _compile(rust: str, tmp_dir) -> str:
    rs_file = tmp_dir / "prog.rs"
    rs_file.write_text(rust, encoding="utf-8")
    exe = str(tmp_dir / "prog_bin")
    proc = subprocess.run(
        [RUSTC, "--edition", "2021", str(rs_file), "-o", exe],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return exe


def _run(exe: str) -> str:
    proc = subprocess.run([exe], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ---- 1. The place write is not a value read ----


def test_field_assignment_emits_a_place_write_not_a_clone():
    """§56: the target is a PLACE. Routing it through the field-access
    emitter would append §36's `.clone()` and write into a temporary,
    silently losing the assignment."""
    rust = _transpile_ok(FA_SOURCE)
    assert "p.x = 5;" in rust
    assert "p.x.clone() = " not in rust


def test_field_assigned_binding_is_emitted_mut():
    """The base joins the `assigned` set via assign_of, so `mut` falls out
    of the existing inference."""
    rust = _transpile_ok(FA_SOURCE)
    assert "let mut p: P = " in rust


@requires_rustc
def test_field_assignment_compiles_under_rustc(tmp_path):
    """Accepted-implies-compiles."""
    _compile(_transpile_ok(FA_SOURCE), tmp_path)


def test_noncopy_field_assignment_target_is_a_bare_place():
    """The real content of the not-routed-through-FieldAccess guard.

    §36's implicit clone is TYPE-CONDITIONAL, so on a Copy field the
    defective and the correct emitters agree byte for byte. On a NON-COPY
    field they diverge: the target becomes `p.name.clone()`, which is not
    a place -- rustc E0070, invalid left-hand side of assignment.
    """
    rust = _transpile_ok(FA_NONCOPY_SOURCE)
    assert "p.name = s;" in rust
    assert "p.name.clone() = " not in rust
    assert ".clone() = " not in rust


@requires_rustc
def test_noncopy_field_assignment_compiles_and_runs(tmp_path):
    """Accepted-implies-compiles for the non-copy target, plus the
    runtime proof that the write landed in the struct rather than in a
    temporary."""
    assert _run(_compile(_transpile_ok(FA_NONCOPY_SOURCE), tmp_path)) == "hello\n"


# ---- 2. Path segments escape as raw identifiers ----


def test_rust_keyword_field_assignment_target_escapes():
    """`struct S { move: Int }` is accepted Oxide and the struct decl
    escapes the field, so only the assignment TARGET exercises `escape`
    over the path. Unescaped, `s.move = 8;` is a rustc parse error --
    expected identifier, found keyword `move`."""
    rust = _transpile_ok(FA_KEYWORD_SOURCE)
    assert "r#move: i64," in rust  # the decl, escaped either way
    assert "s.r#move = 8;" in rust  # the target, the actual guard
    assert "s.move = " not in rust


@requires_rustc
def test_rust_keyword_field_assignment_compiles_and_runs(tmp_path):
    """Accepted-implies-compiles with a keyword field name as target."""
    assert _run(_compile(_transpile_ok(FA_KEYWORD_SOURCE), tmp_path)) == "8\n"


# ---- 3. The derive scan descends into the right-hand side ----


def test_eq_only_inside_a_field_assign_rhs_still_derives_partial_eq():
    """§29's derive scan walks the AST for `==`/`!=` operands. If the
    traversal does not descend through a FieldAssign's value, a `==`
    whose ONLY occurrence is a field-assignment RHS is invisible, the
    operand struct derives `(Debug, Clone)` without `PartialEq`, and the
    comparison is rustc E0369."""
    rust = _transpile_ok(FA_EQ_RHS_SOURCE)
    assert "#[derive(Debug, Clone, PartialEq)]\nstruct K {" in rust
    # F is never compared: it must NOT pick up PartialEq by accident,
    # which would make this test pass for the wrong reason.
    assert "#[derive(Debug, Clone)]\nstruct F {" in rust


@requires_rustc
def test_eq_only_inside_a_field_assign_rhs_compiles(tmp_path):
    """Accepted-implies-compiles: without the derive, E0369 `binary
    operation `==` cannot be applied to type `K`."""
    assert _run(_compile(_transpile_ok(FA_EQ_RHS_SOURCE), tmp_path)) == "false\n"
