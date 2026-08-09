# Field Assignment (SPEC §56) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `s.f = e` (arbitrary field depth, bare-name base) to Black Oxide, closing a real language hole and removing a demonstrated grammar-deformation artifact.

**Architecture:** A new `FieldAssign` statement node — deliberately NOT a widened `Assign`, because `cfg._stmt`'s existing `case ast.Assign(value=value)` emits a `ReInit` that re-establishes ownership, which is exactly wrong for a field write and would keep matching silently. The RHS moves; the base is a READ per §36; the overwritten field is consumed with no `DropPoint`. Routing the base var through the existing `resolve.assign_of` map makes parameter modes, `let mut`, and `for mut` correct from one entry.

**Tech Stack:** Python 3.14, pytest, GBNF (llama.cpp) for the constrained-decoding grammar. No new dependencies.

## Global Constraints

- **SPEC.md is the binding contract.** It is amended in Task 1, before any implementation. Any deviation between SPEC and code is a bug in the deviating side.
- Run tests with `.venv/bin/pytest tests/ -q` from the repo root. `pytest.ini` sets `addopts=-m "not live"`.
- **Every stage's `match` statement has NO catch-all.** An unhandled `FieldAssign` is silently dropped — codegen would emit nothing for the statement and produce wrong Rust with no error. This is why Task 2 is a full vertical slice rather than one stage per task: no intermediate commit may leave the compiler silently wrong.
- **Accepted-implies-compiles.** Any program the front end accepts must produce Rust that `rustc` accepts. Task 4 covers the one place this is at risk (parameter modes / E0594).
- **Do not rename these surfaces** (SPEC §0): both `LANGUAGE_CARD*.md`, the `OX0306` suggestion string, `ARMS`/`arm` data keys, the `__oxide_` codegen prefix, `.ox`, and `eval/grammar/oxide.gbnf`. The generated GBNF header's `"core Oxide"` / `"explicit-Oxide"` text is part of that frozen grammar file — **leave it as-is**; do not "fix" it while editing `build.py`.
- **Cards stay unchanged.** This change measures pure acceptance (design §7).
- Commit messages omit Claude attribution.
- Files stay under 800 lines. **`src/sema/infer.py` is at exactly 799**, and Task 2 Step 9 adds ~18 lines to it — it WILL cross. Task 2 must therefore land the §56 typing walk as a helper method (`_field_assign_place(self, stmt: ast.FieldAssign) -> None`) called from a one-line `_stmt` case, and extract enough to stay under the cap. Do not raise the cap. Re-check with `wc -l src/sema/infer.py` before committing Task 2 and Task 3.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `SPEC.md` | §56 contract | 1 |
| `src/parser/ast.py` | `FieldAssign` node, `Stmt` union, dump case | 2 |
| `src/parser/parser.py` | `_peek_raw`, statement lookahead, `_try_field_assign` | 2 |
| `src/sema/resolve.py` | base lookup, `assign_of` entry, `OX0200` | 2 |
| `src/sema/infer.py` | path walk, `OX0304`/`OX0306`/`OX0300` | 2, 3 |
| `src/sema/cfg.py` | RHS `_MOVE` + base `_READ`, no `ReInit` | 2 |
| `src/codegen/rust.py` | emit `base.f1.f2 = value;` | 2 |
| `src/codegen/support.py` | expression-walker case | 2 |
| `src/explicit/strip.py` | dialect strip case | 2 |
| `eval/grammar/build.py` | `star` on the assign alternative | 5 |
| `eval/grammar/{oxide,explicit}.gbnf` | regenerated | 5 |
| `eval/deformation.py` | pinned signature detector (new) | 7 |
| `tests/test_deformation.py` | detector tests (new) | 7 |

---

### Task 1: SPEC §56 — the contract

**Files:**
- Modify: `SPEC.md` (append a new section after §55, which ends just before EOF)

**Interfaces:**
- Consumes: nothing.
- Produces: the binding text every later task implements against. Later tasks cite `§56`.

- [ ] **Step 1: Read the end of SPEC.md to find the insertion point**

Run: `grep -n "^## 55\." SPEC.md && tail -5 SPEC.md`

§56 is appended at end of file, after §55's closing paragraph.

- [ ] **Step 2: Append §56**

Append to `SPEC.md`:

````markdown
## 56. Field assignment (`s.f = e`)

Assignment targets extend from a bare name to a **place**: a name followed
by one or more field selectors.

```ebnf
stmt              := let_stmt | assign_stmt | field_assign_stmt
                   | return_stmt | while_stmt | for_stmt | expr_stmt
field_assign_stmt := IDENT ("." IDENT)+ "=" expr TERM
```

The base is a bare name, not an arbitrary expression: `f().x = e` and
`v[0] = e` remain `OX0101`. Index assignment awaits an indexing decision
this document has not made.

**Parsing.** At statement start, an `IDENT` followed by `DOT` begins a
scan of `(DOT IDENT)+`; the statement is a field assignment only if that
run is followed by `EQ`. `EQEQ` is a distinct token kind, so `p.x == y`
remains a comparison — the same guarantee §26's `IDENT EQ` lookahead
rests on. A failed scan restores the cursor and the statement is parsed
as an expression statement. The scan does not see through a `NEWLINE`,
for the reason §26 gives: an identifier at end of line is an expression
statement, never the start of an assignment.

**Resolution.** The base must be an existing local or parameter;
otherwise `OX0200`, with the same wording §28 uses for assignment. The
base's `var_id` is recorded in `assign_of` under the `FieldAssign` node's
own id — the same map whole-variable assignment uses, so §28's rule "a
param that is ever assigned gets mode `own`" applies to a field-assigned
param unchanged. That is a **soundness requirement**, not a convenience:
a read-mode non-copy param emits `p: &Point`, and `p.x = 5` through a
`&T` is rustc E0594.

**Typing.** The path is walked left to right through the same field
lookup §36 uses for field access: an unknown field is `OX0304`, a
non-struct at any step is `OX0306`. The final field's type unifies with
the right-hand side (`OX0300` on mismatch). The statement itself is
`Unit`.

**Linearity.** Three rules, each an existing rule applied to a new node:

- The right-hand side is a **MOVE** context, as in §28.
- The base is a **READ** use and emits **no `ReInit`**. §36 already fixes
  `p.x` as a read of the base; writing into an owned struct is that
  rule's completion. A field write into a moved base is therefore
  `OX0400`, and it does **not** re-establish ownership — unlike `p = e`,
  which does.
- The overwritten field value is **consumed implicitly, with no
  `DropPoint`** — §28's rule for the old value of an assigned variable.
  Rust's assignment drops the old field; synthesizing a drop would
  double-free.

`OX0406` cannot arise here: §28 unifies a `for` iterable with `Vec<T>`,
so a directly iterated bare variable has no fields (`v.f = e` is
`OX0306`), and a field-access iterable is cloned under §36, so nothing
stays borrowed. Writing into a loop binder (`for p in ps { p.x = 1 }`) is
well defined: the binder is a fresh owned clone per iteration, so the
write is local and discarded at iteration end.

**Codegen.** `base.f1.f2 = value;`, built from the recorded base rename
and the path. It is **not** routed through the field-access emitter: that
appends `.clone()` to a non-copy field value (§36), and a place is not a
value — cloning the target would write into a temporary and lose the
assignment.

```
struct P { x: Int, y: Int }
fn main() {
    let p = P { x: 1, y: 2 }
    p.x = 5                     // let mut p: P = P { x: 1, y: 2 };
    print(p.x)                  //   p.x = 5;
}
```

**Why this exists.** In the v0.3 generation-friction taxonomy (dossier
2), models assign struct fields in place and the language had no such
form. Under constrained decoding the want does not fail loudly: `=` is
inadmissible after a field path, so the decoder settles on `==` and emits
a **discarded comparison**. Measured over the committed G0 first attempts
(oxide arm), that signature appears 18 times in 9 of 600 constrained
programs and **exactly 0 of 600 unconstrained** — the §54 lesson in its
third demonstrated instance, with a clean control. The demand is real but
small (~1.5%), so no aggregate pass-rate change is predicted from this
section alone; see
`docs/superpowers/specs/2026-08-09-v03-g2-field-assignment-design.md`.
````

- [ ] **Step 3: Verify the suite still passes (SPEC is documentation; nothing should move)**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: `1354 passed, 3 deselected`

- [ ] **Step 4: Commit**

```bash
git add SPEC.md
git commit -m "spec: section 56 — field assignment (s.f = e)

Extends assignment targets from a bare name to a place: IDENT (DOT IDENT)+.
Base stays a bare name; index assignment awaits an indexing decision.

The three linearity rules are each an existing rule applied to a new node:
RHS moves (28); the base is a READ per 36 and emits NO ReInit, so a moved
base is OX0400 and is never silently re-owned; the overwritten field is
consumed with no DropPoint (28's rule -- Rust drops it, and synthesizing
one would double-free). Routing the base through assign_of makes 28's
'assigned param gets mode own' apply unchanged, which is a soundness
requirement: a field write through &T is rustc E0594.

Dossier 2 of the v0.3 taxonomy. Under constrained decoding the want
deforms to a discarded comparison -- 18 occurrences in 9 of 600
constrained first attempts, exactly 0 of 600 unconstrained."
```

---

### Task 2: The vertical slice — `p.x = 5` end to end

**Files:**
- Modify: `src/parser/ast.py` (add node after `Assign` at ~line 108; `Stmt` union at ~line 285; dump at ~line 367)
- Modify: `src/parser/parser.py` (`_peek_raw` near `_peek_next` ~line 121; `_statement` lookahead ~line 428; `_try_field_assign` after `_assign_stmt` ~line 555)
- Modify: `src/sema/resolve.py` (`_stmt` match ~line 212)
- Modify: `src/sema/infer.py` (`_stmt` match ~line 367)
- Modify: `src/sema/cfg.py` (`_stmt` match ~line 440)
- Modify: `src/codegen/rust.py` (`_emit_stmt` match ~line 370)
- Modify: `src/codegen/support.py` (walker match ~line 261)
- Modify: `src/explicit/strip.py` (`_stmt` match ~line 181)
- Test: `tests/test_parser.py`, `tests/test_codegen.py`

**Interfaces:**
- Consumes: SPEC §56 from Task 1.
- Produces:
  - `ast.FieldAssign(node_id: int, span: Span, base: str, path: tuple[str, ...], value: Expr)` — a frozen slotted dataclass in the `Stmt` union.
  - Dump form: `(field-assign p.x <value>)` — the path joined with dots as a single token.
  - `resolve.assign_of[field_assign.node_id] -> base var_id` (same map as `Assign`).
  - `Parser._try_field_assign() -> FieldAssign | None` — returns `None` with `self.pos` restored when the scan fails.

**Why one task:** no stage's `match` has a catch-all, so an unhandled `FieldAssign` is silently dropped and codegen emits nothing. Splitting this by pipeline stage would leave intermediate commits that compile programs to wrong Rust with no diagnostic.

- [ ] **Step 1: Write the failing parser test**

Append to `tests/test_parser.py`:

```python
# ---------------------------------------------------------------------------
# §56 field assignment: `s.f = e` at arbitrary field depth
# ---------------------------------------------------------------------------


class TestFieldAssignment:
    """Models assign struct fields in place and the language had no such
    form. Under constrained decoding the want deformed into a discarded
    `==` comparison (18 occurrences in 9 of 600 constrained first attempts,
    0 of 600 unconstrained) -- the §54 lesson, third instance.
    """

    def test_single_field_assignment_parses(self):
        assert d("fn f() { p.x = 5 }") == mod_f(
            "(block (field-assign p.x (lit int 5)))"
        )

    def test_nested_path_parses(self):
        assert d("fn f() { a.b.c = 5 }") == mod_f(
            "(block (field-assign a.b.c (lit int 5)))"
        )

    def test_double_equals_is_still_a_comparison(self):
        """EQEQ is a distinct token kind, so the scan never fires here."""
        assert "field-assign" not in d("fn f() { p.x == 5 }")

    def test_plain_field_access_statement_is_unchanged(self):
        """A failed scan restores the cursor: `p.x` alone stays an expr."""
        assert "field-assign" not in d("fn f() { p.x }")

    def test_scan_does_not_see_through_a_newline(self):
        """`p.x` at end of line is an expression statement, never the start
        of an assignment -- the rule _peek_next documents for §26."""
        assert "field-assign" not in d("fn f() { p.x\n y = 5 }")

    def test_whole_variable_assignment_is_untouched(self):
        assert d("fn f() { p = 5 }") == mod_f("(block (assign p (lit int 5)))")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_parser.py::TestFieldAssignment -q`
Expected: FAIL — the first test errors because `p.x = 5` currently yields `OX0101` and no `field-assign` node exists.

- [ ] **Step 3: Add the AST node**

In `src/parser/ast.py`, immediately after the `Assign` dataclass:

```python
@dataclass(frozen=True, slots=True)
class FieldAssign:
    """`a.b.c = e` (SPEC.md section 56).

    A distinct node rather than a widened ``Assign``: ``cfg`` matches
    ``ast.Assign`` to emit a ``ReInit`` that re-establishes ownership,
    which is exactly wrong for a field write. A separate node forces every
    match site to decide, instead of silently inheriting that behaviour.
    """

    node_id: int
    span: Span
    base: str
    path: tuple[str, ...]
    value: Expr
```

Update the union:

```python
type Stmt = (
    Let | Assign | FieldAssign | Return | Break | Continue | ExprStmt | ErrorStmt
)
```

Add the dump case immediately after `case Assign(...)`:

```python
        case FieldAssign(base=base, path=path, value=value):
            return _sexp("field-assign", ".".join((base, *path)), (yield value))
```

- [ ] **Step 4: Add the parser lookahead and scan**

In `src/parser/parser.py`, add next to `_peek_next`:

```python
    def _peek_raw(self) -> Token:
        """The token at the cursor with NO NEWLINE skipping.

        The §56 field-assignment scan must stop at end of line, for the
        same reason ``_peek_next`` must: an identifier at end of line is an
        expression statement, never the start of an assignment.
        """
        return self.tokens[self.pos]
```

In `_statement`, immediately **before** the existing `IDENT EQ` branch:

```python
        # Field assignment (SPEC.md §56): IDENT (DOT IDENT)+ EQ. Tried
        # before the §26 IDENT-EQ branch because it starts the same way;
        # the scan restores the cursor and returns None when the run is
        # not an assignment, so `p.x` and `p.x == y` stay expressions.
        if kind is TokenKind.IDENT and self._peek_next().kind is TokenKind.DOT:
            field_assign = self._try_field_assign()
            if field_assign is not None:
                return field_assign
```

Add after `_assign_stmt`:

```python
    def _try_field_assign(self) -> FieldAssign | None:
        """`a.b.c = e` (SPEC.md §56), or None with the cursor restored.

        Unbounded lookahead: the statement is a field assignment only if
        the whole `IDENT (DOT IDENT)+` run is followed by EQ. EQEQ is a
        distinct token kind, so `p.x == y` fails the scan and falls through
        to an expression statement.
        """
        start = self.pos
        base_tok = self._advance()  # IDENT (verified by _statement)
        path: list[str] = []
        while self._peek_raw().kind is TokenKind.DOT:
            self._advance()  # DOT
            if self._peek_raw().kind is not TokenKind.IDENT:
                self.pos = start
                return None
            path.append(self._advance().lexeme)
        if not path or self._peek_raw().kind is not TokenKind.EQ:
            self.pos = start
            return None
        self._advance()  # EQ
        value = self._parse_expr(0)
        self._expect_term()
        span = Span(base_tok.span.start, value.span.end)
        return FieldAssign(
            self._new_id(), span, base_tok.lexeme, tuple(path), value
        )
```

Add `FieldAssign` to the `from src.parser.ast import (...)` block at the top of the file (alphabetically beside `Assign`).

- [ ] **Step 5: Run the parser tests**

Run: `.venv/bin/pytest tests/test_parser.py::TestFieldAssignment -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Write the failing codegen test**

Append to `tests/test_codegen.py`:

```python
# ---- §56 field assignment ----

FA_SOURCE = (
    "struct P { x: Int, y: Int }\n"
    "fn main() { let p = P { x: 1, y: 2 }\n p.x = 5\n print(p.x) }"
)


def test_field_assignment_emits_a_place_write_not_a_clone():
    """§56: the target is a PLACE. Routing it through the field-access
    emitter would append §36's `.clone()` and write into a temporary,
    silently losing the assignment."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    assert "p.x = 5;" in rust
    assert "p.x.clone() = " not in rust


def test_field_assigned_binding_is_emitted_mut():
    """The base joins the `assigned` set via assign_of, so `mut` falls out
    of the existing inference."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    assert "let mut p: P = " in rust


@requires_rustc
def test_field_assignment_compiles_under_rustc(tmp_path):
    """Accepted-implies-compiles."""
    rust, diags = transpile(FA_SOURCE)
    assert diags == [], diags
    src = tmp_path / "prog.rs"
    src.write_text(rust, encoding="utf-8")
    proc = subprocess.run(
        [RUSTC, "--edition", "2021", "-o", str(tmp_path / "prog"), str(src)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
```

- [ ] **Step 7: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_codegen.py -q -k field_assign`
Expected: FAIL — the statement is parsed but every downstream stage silently ignores it, so `p.x = 5;` never appears in the emitted Rust. (This failure IS the silent-drop hazard the Global Constraints describe.)

- [ ] **Step 8: Handle the node in resolve**

In `src/sema/resolve.py`, in `_stmt`'s match, after the `ast.Assign` case:

```python
            case ast.FieldAssign(base=base, value=value):
                # Section 56: the base must be an existing local/param, and
                # goes in the SAME map as whole-variable assignment so §28's
                # "an assigned param gets mode own" applies unchanged -- a
                # soundness requirement, since a field write through &T is
                # rustc E0594.
                var_id = self._lookup(base)
                if var_id is not None:
                    self.result.assign_of[stmt.node_id] = var_id
                else:
                    self._diag(
                        "OX0200",
                        f"unknown identifier '{base}' in assignment",
                        stmt.span,
                    )
                self._expr(value)
```

Update the `assign_of` docstring comment (~line 66) to:

```python
    # Assign/FieldAssign node_id -> the assigned variable's var_id
    # (the BASE, for a field write)
```

- [ ] **Step 9: Handle the node in typing**

`src/sema/infer.py` is at exactly 799 lines against the project's 800 cap, so this lands as a **helper method** with a one-line call site, not as an inline case.

In `_stmt`'s match, after the `ast.Assign` case:

```python
            case ast.FieldAssign():
                self._field_assign(stmt)
```

And as a new method, placed immediately after `_stmt`:

```python
    def _field_assign(self, stmt: ast.FieldAssign) -> None:
        """Section 56: walk the place left to right through the same field
        lookup section 36 uses for reads, then unify the final field's type
        with the RHS."""
        value_ty = self._expr(stmt.value)
        var_id = self.resolved.assign_of.get(stmt.node_id)
        if var_id is None:
            return  # unbound base: resolve already reported OX0200
        ty = self.var_tv[var_id]
        for fname in stmt.path:
            checked = self._field_check(ty, fname, stmt.span)
            if checked is None:
                # Base type unknown so far: defer to the global solve,
                # exactly as _field_access does.
                tv = self._fresh()
                self._pending_fields.append((ty, fname, stmt.span, tv))
                checked = tv
            ty = checked
        self.unify(value_ty, ty, stmt.value.span)
```

Then run `wc -l src/sema/infer.py`. If it is over 800, extract an existing unrelated block (the `_destructure` helper is the natural candidate) into a sibling module rather than raising the cap.

- [ ] **Step 10: Handle the node in the CFG**

In `src/sema/cfg.py`, in `_stmt`'s match, after the `ast.Assign` case:

```python
            case ast.FieldAssign(value=value):
                # Section 56: the RHS moves first; the base is a READ (§36
                # already fixes `p.x` as a read of the base) and emits NO
                # ReInit -- writing a field must not re-establish ownership
                # of a moved struct, unlike `p = e`, which does. The
                # overwritten field is consumed implicitly: no DropPoint,
                # because Rust's assignment drops it.
                nodes = self._expr(value, _MOVE)
                var_id = self.resolved.assign_of.get(stmt.node_id)
                if var_id is not None and not self._is_copy_var(var_id):
                    self.use_class[stmt.node_id] = _READ
                    nodes.append(Use(var_id, stmt.node_id, stmt.span, _READ))
                return nodes
```

- [ ] **Step 11: Handle the node in codegen and its walker**

In `src/codegen/rust.py`, in `_emit_stmt`'s match, after the `ast.Assign` case:

```python
            case ast.FieldAssign(path=path, value=value):
                # Section 56: a PLACE write. Deliberately not routed through
                # the FieldAccess emitter, which appends §36's `.clone()` to
                # a non-copy field value -- that would write into a
                # temporary and lose the assignment.
                var_id = self.assign_of[stmt.node_id]
                target = ".".join(
                    (self.rename[var_id], *(escape(f) for f in path))
                )
                lines.append(f"{pad}{target} = {self._expr(value, indent)};")
```

In `src/codegen/support.py`, in the walker's match, after the `ast.Assign` case:

```python
            case ast.FieldAssign(value=value):
                stack.append(value)
```

- [ ] **Step 12: Handle the node in the explicit dialect's strip pass**

In `src/explicit/strip.py`, in `_stmt`'s match, after the `ast.Assign` case:

```python
            case ast.FieldAssign(value=value):
                return replace(stmt, value=self._expr(value))
```

- [ ] **Step 13: Run the codegen tests**

Run: `.venv/bin/pytest tests/test_codegen.py -q -k field_assign`
Expected: PASS (3 passed, or 2 passed + 1 skipped if rustc is unavailable)

- [ ] **Step 14: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green, count = 1354 + 9 new = 1363 passed

If anything else fails, it is a real interaction — fix it before committing rather than adjusting the new tests.

- [ ] **Step 15: Commit**

```bash
git add src/parser/ast.py src/parser/parser.py src/sema/resolve.py \
        src/sema/infer.py src/sema/cfg.py src/codegen/rust.py \
        src/codegen/support.py src/explicit/strip.py \
        tests/test_parser.py tests/test_codegen.py
git commit -m "feat(lang): field assignment s.f = e — SPEC section 56

A distinct FieldAssign node, not a widened Assign: cfg._stmt matches
ast.Assign to emit a ReInit that re-establishes ownership, which is wrong
for a field write and would have kept matching silently. No stage's match
has a catch-all, so the node is handled in all eight sites in one commit --
an unhandled FieldAssign is dropped without a diagnostic and codegen emits
nothing for the statement.

RHS moves; the base is a READ per 36 and emits no ReInit; the overwritten
field is consumed with no DropPoint. The base goes in resolve.assign_of,
which makes parameter modes, let-mut, and for-mut correct from one entry.
Codegen builds the place textually rather than through the FieldAccess
emitter, whose 36 .clone() would write to a temporary and lose the write."
```

---

### Task 3: Path diagnostics

**Files:**
- Test: `tests/test_sema_types.py`

**Interfaces:**
- Consumes: `ast.FieldAssign` and the typing walk from Task 2.
- Produces: nothing new — this task pins behaviour that Task 2's code already produces, and fixes it where it does not.

- [ ] **Step 1: Write the failing diagnostics tests**

Append to `tests/test_sema_types.py`:

```python
# ---------------------------------------------------------------------------
# §56 field assignment: the place is walked with the same lookup §36 uses
# ---------------------------------------------------------------------------


def test_field_assign_unknown_field_is_ox0304() -> None:
    src = "struct P { x: Int }\nfn main() { let p = P { x: 1 }\n p.z = 5 }"
    assert "OX0304" in codes(src)


def test_field_assign_into_non_struct_is_ox0306() -> None:
    src = "fn main() { let n = 1\n n.x = 5 }"
    assert "OX0306" in codes(src)


def test_field_assign_type_mismatch_is_ox0300() -> None:
    src = (
        "struct P { x: Int }\n"
        'fn main() { let p = P { x: 1 }\n p.x = "s" }'
    )
    assert "OX0300" in codes(src)


def test_field_assign_unbound_base_is_ox0200() -> None:
    assert "OX0200" in codes("fn main() { nope.x = 5 }")


def test_nested_path_walks_every_step() -> None:
    """The intermediate field must itself be a struct: the second step of
    `a.b.c` is checked against `b`'s type, not `a`'s."""
    src = (
        "struct Inner { v: Int }\n"
        "struct Outer { i: Inner }\n"
        "fn main() { let o = Outer { i: Inner { v: 1 } }\n o.i.nope = 5 }"
    )
    assert "OX0304" in codes(src)


def test_nested_path_accepts_a_valid_walk() -> None:
    src = (
        "struct Inner { v: Int }\n"
        "struct Outer { i: Inner }\n"
        "fn main() { let o = Outer { i: Inner { v: 1 } }\n o.i.v = 5\n"
        " print(o.i.v) }"
    )
    assert codes(src) == []
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/pytest tests/test_sema_types.py -q -k "field_assign or nested_path"`
Expected: most PASS from Task 2's implementation. Any FAIL is a real gap — most likely the deferral branch or an intermediate non-struct step.

- [ ] **Step 3: Fix whatever failed**

If `test_nested_path_walks_every_step` fails, the loop is re-checking against the base type instead of threading `ty` through each step — confirm the `ty = checked` reassignment at the end of the loop body in `src/sema/infer.py`.

If a test that should be clean reports a spurious code, check that `_field_check`'s `None` return (type not yet known) is deferred via `_pending_fields` rather than treated as an error.

- [ ] **Step 4: Check the file-length constraint**

Run: `wc -l src/sema/infer.py`
Expected: under 800. If it crossed, extract the path walk into a `_field_assign_place(self, stmt) -> Type | None` helper method in the same file — that keeps the change local and drops `_stmt` back to a single call.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add tests/test_sema_types.py src/sema/infer.py
git commit -m "test(lang): pin section 56 place-walk diagnostics

OX0304 unknown field, OX0306 non-struct at any step, OX0300 RHS mismatch,
OX0200 unbound base. The nested case pins that each step is checked against
the PREVIOUS step's type, not the base's -- the bug a single _field_check
call would have."
```

---

### Task 4: Linearity semantics

**Files:**
- Test: `tests/test_linear.py`

**Interfaces:**
- Consumes: the CFG handling from Task 2.
- Produces: nothing new — pins the three §56 linearity rules and the E0594 soundness case.

- [ ] **Step 1: Write the failing linearity tests**

Append to `tests/test_linear.py`:

```python
# ---------------------------------------------------------------------------
# §56 field assignment: the base is a READ, and never a ReInit
# ---------------------------------------------------------------------------


def test_field_write_into_a_moved_base_is_use_after_move():
    """§56: the base is a READ use (§36), so writing a field of a moved
    struct is OX0400 -- not a silent re-ownership.

    `let q = p` is the move. Passing to a function does NOT move here: a
    param that is never assigned and never move-used infers mode `read`
    (§15/§28), so `take(p)` emits `&p` and the base is still owned.
    """
    src = (
        "struct P { v: Vec<Int> }\n"
        "fn main() { let p = P { v: vec() }\n let q = p\n p.v = vec() }"
    )
    assert "OX0400" in codes_of(src)


def test_whole_assignment_re_owns_a_moved_base_but_a_field_write_does_not():
    """The contrast that motivates the distinct node, as one paired check:
    after the same move, `p = e` re-establishes ownership and the program is
    clean, while `p.f = e` must NOT re-own and stays an error. A widened
    `Assign` node would have made the second line behave like the first.
    """
    moved = (
        "struct P { v: Vec<Int> }\n"
        "fn main() { let p = P { v: vec() }\n let q = p\n"
    )
    assert codes_of(moved + " p = P { v: vec() }\n print(len(p.v)) }") == []
    assert "OX0400" in codes_of(moved + " p.v = vec()\n print(len(p.v)) }")


def test_field_assigned_param_gets_own_mode():
    """SOUNDNESS (§56/§28): a read-mode param emits `p: &Point`, and a
    field write through &T is rustc E0594. An assigned param must be own."""
    src = (
        "struct P { x: Int }\n"
        "fn bump(p: P) -> Int { p.x = 5\n p.x }\n"
        "fn main() { let p = P { x: 1 }\n print(bump(p)) }"
    )
    res = analyze(src)
    assert res.diagnostics == [], res.diagnostics
    assert param_modes(res, "bump") == ("own",)


def test_field_write_adds_no_drop_for_the_overwritten_value():
    """§56: the old field value is consumed implicitly. Rust's assignment
    drops it; a synthesized DropPoint would double-free."""
    src = (
        "struct P { v: Vec<Int> }\n"
        "fn main() { let p = P { v: vec() }\n p.v = push(vec(), 1)\n"
        " print(len(p.v)) }"
    )
    res = analyze(src)
    assert res.diagnostics == [], res.diagnostics
    # exactly one drop: `p` itself at block end, none for the replaced field
    assert len(drop_list(res)) == 1, drop_list(res)


def test_write_into_a_loop_binder_is_clean():
    """The binder is a fresh owned clone per iteration, so the write is
    local and discarded at iteration end."""
    src = (
        "struct P { x: Int }\n"
        "fn main() { let ps = push(vec(), P { x: 1 })\n"
        " for p in ps { p.x = 5\n print(p.x) } }"
    )
    assert codes_of(src) == []


def test_field_write_on_a_vec_is_a_type_error_not_a_borrow_error():
    """OX0406 cannot arise for §56: a directly iterated variable unifies
    with Vec<T>, which has no fields."""
    src = "fn main() { let v = vec()\n for x in v { v.f = 1 } }"
    assert "OX0306" in codes_of(src)
    assert "OX0406" not in codes_of(src)
```

- [ ] **Step 2: Run them**

Run: `.venv/bin/pytest tests/test_linear.py -q -k "field_write or field_assigned or loop_binder"`
Expected: most PASS from Task 2. `test_field_write_adds_no_drop_for_the_overwritten_value` is the likeliest genuine failure if the CFG accidentally emits a `TempMark` or `ReInit`.

- [ ] **Step 3: Fix whatever failed**

If a spurious drop appears, confirm the CFG case appends only the RHS `_MOVE` nodes plus one `Use(..., _READ)` — no `ReInit`, no `TempMark`.

If `param_modes` reports `read` for `bump`, the base is not reaching `assign_of`; re-check the resolve case from Task 2 Step 8, since `modes.py` seeds `assigned_vars` from `resolve.assign_of.values()`.

- [ ] **Step 4: Verify the E0594 case actually compiles**

Run:

```bash
.venv/bin/python -c "
from src.codegen.rust import transpile
src = ('struct P { x: Int }\n'
       'fn bump(p: P) -> Int { p.x = 5\n p.x }\n'
       'fn main() { let p = P { x: 1 }\n print(bump(p)) }')
rust, diags = transpile(src)
assert diags == [], diags
print(rust)
" > /tmp/e0594_check.rs.txt; grep -n "fn bump" /tmp/e0594_check.rs.txt
```

Expected: the signature is `fn bump(mut p: P)` — by value with `mut`, **not** `p: &P`. If it shows `&P`, the mode fix in Step 3 is incomplete and the emitted Rust will fail rustc with E0594.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add tests/test_linear.py src/sema/cfg.py
git commit -m "test(lang): pin section 56 linearity — READ base, never ReInit

The contrast that motivates the distinct node: `p = e` re-owns a moved
variable and is legal; `p.f = e` must not, so a later use still reports
OX0400. Also pins the E0594 soundness case (a field-assigned param must
infer mode own, or codegen emits &T and rustc rejects the write), that no
DropPoint is synthesized for the overwritten field, and that OX0406 cannot
arise since an iterated variable unifies with Vec<T> and has no fields."
```

---

### Task 5: GBNF grammar parity

**Files:**
- Modify: `eval/grammar/build.py` (`_flat_stmts`, ~line 667)
- Modify: `eval/grammar/oxide.gbnf`, `eval/grammar/explicit.gbnf` (regenerated, never hand-edited)
- Test: `tests/test_grammar_admission.py`

**Interfaces:**
- Consumes: the parser from Task 2.
- Produces: a grammar admitting `IDENT (DOT IDENT)* " = " value`, so constrained decoding can emit field assignment instead of deforming it.

- [ ] **Step 1: Write the failing admission test**

Append to `tests/test_grammar_admission.py`:

```python
# ---------------------------------------------------------------------------
# §56 field assignment: what the language accepts, the grammar must admit
# ---------------------------------------------------------------------------

FIELD_ASSIGN_PROGRAM = "fn main() {\n    p.x = 5\n}\n"
NESTED_ASSIGN_PROGRAM = "fn main() {\n    a.b.c = 5\n}\n"


def test_oxide_grammar_admits_field_assignment():
    """Before §56 the decoder could not emit `=` after a field path and
    settled on `==`, producing a discarded comparison -- 18 occurrences in
    9 of 600 constrained first attempts, 0 of 600 unconstrained."""
    assert admits(OXIDE_RULES, "root", FIELD_ASSIGN_PROGRAM)
    assert admits(OXIDE_RULES, "root", NESTED_ASSIGN_PROGRAM)


def test_explicit_grammar_admits_field_assignment():
    assert admits(EXPLICIT_RULES, "root", FIELD_ASSIGN_PROGRAM)
    assert admits(EXPLICIT_RULES, "root", NESTED_ASSIGN_PROGRAM)


def test_grammar_still_admits_plain_assignment():
    """The star includes zero, so §26's form is admitted by the same rule."""
    assert admits(OXIDE_RULES, "root", "fn main() {\n    p = 5\n}\n")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_grammar_admission.py -q -k field_assignment`
Expected: FAIL — the current alternative is `lname " = " value`, which cannot produce a dot.

- [ ] **Step 3: Widen the grammar rule**

In `eval/grammar/build.py::_flat_stmts`, replace:

```python
            seq(Ref("lname"), Lit(" = "), Ref(f"value-{t}")),
```

with:

```python
            # SPEC §56: assignment targets are places, not just names. The
            # star includes zero, so §26's plain `x = e` is admitted by this
            # same alternative. Before this, `=` was inadmissible after a
            # field path and GBNF steered to `==`, emitting a DISCARDED
            # comparison -- 18 occurrences in 9 of 600 constrained first
            # attempts, and exactly 0 of 600 unconstrained.
            seq(
                Ref("lname"),
                star(seq(Lit("."), Ref("lname"))),
                Lit(" = "),
                Ref(f"value-{t}"),
            ),
```

Confirm `star` is already imported in the module (it is defined at `eval/grammar/build.py:147` and used elsewhere in the file).

**Do not touch** the `_HEADER` dialect strings (`"core Oxide"` / `"explicit-Oxide"`) — SPEC §0 freezes the grammar file's naming.

- [ ] **Step 4: Regenerate both grammar files**

Run: `.venv/bin/python -m eval.grammar.build`

- [ ] **Step 5: Verify the checked-in files match the builder**

Run: `.venv/bin/pytest tests/test_6a.py -q -k grammar`
Expected: PASS — `tests/test_6a.py:2318` asserts the checked-in `.gbnf` equals `render()`, so a stale file fails here.

- [ ] **Step 6: Run the admission and soundness tests**

Run: `.venv/bin/pytest tests/test_grammar_admission.py tests/test_6a.py -q`
Expected: all green. The soundness direction (every string the grammar emits parses) is enforced in `test_6a.py` and must stay green — if it fails, the new alternative admits something the parser rejects.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green

- [ ] **Step 8: Commit**

```bash
git add eval/grammar/build.py eval/grammar/oxide.gbnf \
        eval/grammar/explicit.gbnf tests/test_grammar_admission.py
git commit -m "feat(eval): admit field assignment in both GBNF grammars

Parity invariant: what the language accepts, the grammar admits. The assign
alternative gains a star over (DOT lname), so §26's plain form is still
admitted by the same rule and both dialects inherit the change from the one
builder. Grammar files regenerated, never hand-edited.

This is the artifact removal: pre-§56 the decoder could not emit '=' after a
field path and steered to '==', producing a discarded comparison in 9 of 600
constrained first attempts and 0 of 600 unconstrained."
```

---

### Task 6: Explicit dialect parity

**Files:**
- Test: `tests/test_explicit.py`

**Interfaces:**
- Consumes: `strip.py` handling from Task 2 Step 12.
- Produces: nothing new — pins that both dialects agree.

- [ ] **Step 1: Write the failing parity test**

Append to `tests/test_explicit.py`:

```python
# ---- §56 field assignment: the dialect inherits it unchanged ----

FA_CORE = (
    "struct P { x: Int, y: Int }\n"
    "fn main() { let p = P { x: 1, y: 2 }\n p.x = 5\n print(p.x) }"
)

# The dialect is the matched-novelty control: the model must WRITE what core
# infers. The SAME program therefore needs the `&` read marker and the
# explicit `drop`. Feeding it the bare core source is EX0003 + EX0002 -- the
# dialect working correctly, not a §56 failure.
FA_EXPLICIT = (
    "struct P { x: Int, y: Int }\n"
    "fn main() { let p = P { x: 1, y: 2 }\n p.x = 5\n print(&p.x)\n drop p }"
)


def test_field_assignment_accepted_in_the_explicit_dialect(tmp_path: Path) -> None:
    """ExplicitParser subclasses the core parser, so §56 arrives for free;
    this pins that it did, rather than assuming it."""
    obj = _dialect_json(tmp_path, FA_EXPLICIT)
    assert _codes(obj) == [], obj


def test_field_assignment_rust_is_byte_identical_across_dialects() -> None:
    """§41: the dialect emits byte-identical Rust to the core program -- the
    annotations are surface only, and strip must put the FieldAssign node
    back exactly as the core parser produced it."""
    from src.explicit.pipeline import run as explicit_run

    core_rust, core_diags = transpile(FA_CORE)
    assert core_diags == [], core_diags
    dialect_rust, dialect_diags = explicit_run(FA_EXPLICIT)
    assert dialect_diags == [], dialect_diags
    assert dialect_rust == core_rust


def test_bare_core_source_is_rejected_by_the_dialect() -> None:
    """Guards the fixture above: if the dialect ever stopped demanding its
    annotations, the parity test would pass for the wrong reason."""
    from src.explicit.pipeline import run as explicit_run

    _rust, diags = explicit_run(FA_CORE)
    assert [d.code for d in diags] == ["EX0003", "EX0002"], diags
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/test_explicit.py -q -k field_assignment`
Expected: PASS from Task 2's strip case. A FAIL here means `strip._stmt` is dropping the node — its match has no catch-all either, so an unhandled `FieldAssign` would fall through and return the statement unstripped.

- [ ] **Step 3: Verify the helper signature before relying on it**

Run: `grep -n "def _dialect_json" -A 12 tests/test_explicit.py`

Adjust the call in Step 1 to the helper's actual parameters if it takes more than `(tmp_path, source)`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add tests/test_explicit.py
git commit -m "test(explicit): pin section 56 dialect parity

ExplicitParser subclasses the core parser and strip._stmt has no catch-all,
so this pins that the dialect actually inherited field assignment and emits
byte-identical Rust, rather than silently dropping the node."
```

---

### Task 7: The deformation-signature detector, as a committed tool

**Files:**
- Create: `eval/deformation.py`
- Create: `tests/test_deformation.py`

**Interfaces:**
- Consumes: `src.parser.parser.parse_source`, `src.parser.ast`.
- Produces: `field_assign_deformations(source: str) -> tuple[int, int]` returning `(stmt_position_count, tail_position_count)`, and a `main()` CLI over a results directory.

**Why this exists:** this count is g2's pre-registered primary endpoint, folded into the g3 run. The 6a pilot's demand table became irreproducible because its filter lived only in a scratch script; the pinned definition now lives in the repo with a test.

- [ ] **Step 1: Write the failing detector test**

Create `tests/test_deformation.py`:

```python
"""The §56 deformation signature, pinned (SPEC §56, g2 design).

An EXPRESSION-STATEMENT `f.x == e` is a DISCARDED comparison: no model
writes one deliberately, so it is the grammar deforming an intended field
assignment. A TAIL-position one may be a legitimate Bool return, so the two
are counted separately and never pooled.
"""

from eval.deformation import field_assign_deformations


def test_discarded_field_comparison_is_the_signature():
    src = "fn main() {\n    a.values == 5\n}"
    assert field_assign_deformations(src) == (1, 0)


def test_tail_position_is_counted_separately_not_pooled():
    """`c.r == c.g` as a Bool return is CORRECT code, not deformation."""
    src = "fn is_zero(c: P) -> Bool {\n    c.r == c.g\n}"
    assert field_assign_deformations(src) == (0, 1)


def test_a_condition_is_not_a_signature():
    src = "fn main() {\n    if p.x == 5 {\n        print(1)\n    }\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_non_field_comparison_is_not_a_signature():
    src = "fn main() {\n    x == 5\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_real_field_assignment_is_not_a_signature():
    """Post-§56 the form parses as an assignment, so the count is 0 -- this
    is the endpoint: 18 -> 0."""
    src = "fn main() {\n    a.values = 5\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_unparseable_source_does_not_raise():
    assert field_assign_deformations("&&& not a program") == (0, 0)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/pytest tests/test_deformation.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.deformation'`

- [ ] **Step 3: Write the detector**

Create `eval/deformation.py`:

```python
"""The §56 field-assignment deformation signature, with a pinned definition.

A grammar-constrained decoder never rejects a token -- it steers to the
nearest valid string (SPEC §54). Before §56, `=` was inadmissible after a
field path, so `s.f = e` became `s.f == e`: a comparison whose value is
discarded.

PINNED DEFINITION. Parse the submission; count `ExprStmt` nodes whose
expression is a `BinOp` with ``op == "=="`` and a `FieldAccess` left-hand
side. Tail-position occurrences are returned separately and MUST NOT be
pooled into the signature count: a tail `f.x == e` can be a legitimate Bool
return, and pooling them counts the model's own intent as artifact.

Measured over the committed G0 first attempts (oxide arm): 18 statement
occurrences in 9 of 600 constrained programs, and exactly 0 of 600
unconstrained.

This lives in the repo rather than a scratch script because the 6a pilot's
demand table became irreproducible when its filter did not.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.parser import ast
from src.parser.parser import parse_source


def _children(node: object) -> list[object]:
    """Every child of *node* that can contain a statement or expression."""
    match node:
        case ast.Module(items=items):
            return list(items)
        case ast.FnDecl(body=body):
            return [body]
        case ast.Block(stmts=stmts, tail=tail):
            return [*stmts, *([tail] if tail is not None else [])]
        case ast.ExprStmt(expr=expr):
            return [expr]
        case ast.Let(init=init):
            return [init]
        case ast.Assign(value=value):
            return [value]
        case ast.FieldAssign(value=value):
            return [value]
        case ast.Return(value=value):
            return [value] if value is not None else []
        case ast.If(cond=cond, then_blk=then_blk, else_blk=else_blk):
            return [cond, then_blk, *([else_blk] if else_blk is not None else [])]
        case ast.While(cond=cond, body=body):
            return [cond, body]
        case ast.For(iterable=iterable, body=body):
            return [iterable, body]
        case ast.Match(scrutinee=scrutinee, arms=arms):
            return [scrutinee, *(arm.body for arm in arms)]
        case ast.Call(callee=callee, args=args):
            return [callee, *args]
        case ast.BinOp(lhs=lhs, rhs=rhs):
            return [lhs, rhs]
        case ast.UnOp(operand=operand):
            return [operand]
        case ast.FieldAccess(obj=obj):
            return [obj]
        case ast.Try(operand=operand):
            return [operand]
        case ast.StructLit(fields=fields, rest=rest):
            return [e for _n, e in fields] + ([rest] if rest is not None else [])
    return []


def _is_signature(expr: object) -> bool:
    return (
        isinstance(expr, ast.BinOp)
        and expr.op == "=="
        and isinstance(expr.lhs, ast.FieldAccess)
    )


def field_assign_deformations(source: str) -> tuple[int, int]:
    """``(statement_position, tail_position)`` counts for *source*.

    The FIRST element is the signature. The second is reported for honesty
    and must never be pooled into it. Never raises: a submission that does
    not parse contributes ``(0, 0)``.
    """
    try:
        module, _ = parse_source(source)
    except Exception:  # a malformed submission is not a signature
        return (0, 0)
    stmt_hits = tail_hits = 0
    stack: list[object] = [module]
    while stack:
        node = stack.pop()
        match node:
            case ast.ExprStmt(expr=expr) if _is_signature(expr):
                stmt_hits += 1
            case ast.Block(tail=tail) if tail is not None and _is_signature(tail):
                tail_hits += 1
        stack.extend(_children(node))
    return (stmt_hits, tail_hits)


def main() -> None:
    """Scan first attempts under a run root: `python -m eval.deformation DIR`."""
    if len(sys.argv) != 2:
        print("usage: python -m eval.deformation <results-dir>", file=sys.stderr)
        raise SystemExit(2)
    root = Path(sys.argv[1])
    totals: dict[str, list[int]] = {}
    for raw in sorted(root.glob("*/raw/*.oxide.1.txt")):
        family = raw.parent.parent.name.split("-")[1]
        stmt, tail = field_assign_deformations(
            raw.read_text(encoding="utf-8", errors="replace")
        )
        row = totals.setdefault(family, [0, 0, 0, 0])
        row[0] += 1
        row[1] += stmt
        row[2] += tail
        row[3] += 1 if stmt else 0
    print(f"{'family':<14}{'progs':>7}{'stmt':>7}{'tail':>7}{'stmt progs':>12}")
    for family, (progs, stmt, tail, hit) in sorted(totals.items()):
        print(f"{family:<14}{progs:>7}{stmt:>7}{tail:>7}{hit:>12}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the detector tests**

Run: `.venv/bin/pytest tests/test_deformation.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Reproduce the committed baseline**

Run: `.venv/bin/python -m eval.deformation eval/results/g0-generation-baseline/constrained`

Expected, exactly — this is the pre-registered "before" number:

```
family         progs   stmt   tail  stmt progs
codegemma7b      200     10      1           3
granite8b        200      6     12           4
qwen7b           200      2      4           2
```

If these differ, the detector's definition has drifted from the one the design pre-registered. Stop and reconcile rather than editing the expected numbers.

- [ ] **Step 6: Confirm the unconstrained control**

Run: `.venv/bin/python -m eval.deformation eval/results/g0-generation-baseline/unconstrained`
Expected: `stmt` and `stmt progs` are `0` for all three families.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: all green

- [ ] **Step 8: Commit**

```bash
git add eval/deformation.py tests/test_deformation.py
git commit -m "feat(eval): pin the section 56 deformation signature as a tool

g2's pre-registered endpoint, in the repo rather than a scratch script --
the 6a pilot's demand table became irreproducible precisely because its
filter did not survive its session.

Pinned definition: ExprStmt nodes whose expression is a '==' BinOp with a
FieldAccess LHS -- a DISCARDED comparison, which no model writes on purpose.
Tail-position hits are returned separately and must never be pooled: a tail
'c.r == c.g' is a legitimate Bool return, and pooling would count the
model's own intent as artifact.

Reproduces the committed G0 baseline: 18 statement occurrences in 9 of 600
constrained oxide first attempts, 0 of 600 unconstrained."
```

---

## After the plan

The measurement is **not** a task here. Per the design, g2 gets no dedicated campaign: its endpoint folds into the g3 (conversion builtins) run, where the pre-registered predictions are

- statement-position signature, constrained: **18 → 0**
- the same signature, unconstrained: **0 → 0**
- aggregate first-attempt pass rate from g2 alone: **no detectable change** — 1.5% prevalence cannot move it, and apparent movement is noise
- rust arm: flat at the first-attempt **rate** level, never the byte level

g3 (dossier 3, conversion builtins) is the next design, and it carries the combined campaign.
