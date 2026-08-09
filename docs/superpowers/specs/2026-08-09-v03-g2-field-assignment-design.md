# g2 — field assignment (`s.f = e`), design

**Date:** 2026-08-09
**Track:** v0.3 generation ergonomics, taxonomy dossier 2.
**Class:** demand-gated FEATURE. The SPEC extension (Part XI §56) lands
before any implementation, per the change loop in the g0 plan's Appendix A
and the direction memo's "SPEC extension FIRST".
**Status:** design approved in session; the SPEC amendment and the
implementation plan follow from it.

## Why this exists

Black Oxide has no field assignment. `p.x = 5` is `OX0101` at HEAD
(`expected end of statement, found EQ`) — the only way to change a field
is the functional update `P { x: 5, ..p }`. Models write the assignment
form anyway, and under grammar-constrained decoding the want does not
fail loudly: `=` is inadmissible after a field path, so the decoder
settles on the nearest valid continuation, `==`, and emits a **discarded
comparison**. That is SPEC §54's lesson in its third demonstrated
instance.

The hole is worth closing on language grounds alone, independent of any
eval: the language already embraces `let mut` and whole-value
reassignment, and place-assignment into an owned struct is its natural
completion.

## The measured demand, stated honestly up front

A mechanical scan of the committed G0 raws (first attempts, oxide arm)
counting expression-statement-position `f.x == e` — a *discarded*
comparison, which no model writes deliberately:

| family | constrained | unconstrained |
|---|---|---|
| codegemma7b | 10 occ / 3 progs | 0 / 0 |
| granite8b | 6 occ / 4 progs | 0 / 0 |
| qwen7b | 2 occ / 2 progs | 0 / 0 |
| **total (of 600)** | **18 occ / 9 progs (1.5%)** | **0 / 0** |

Tail-position hits are reported as a separate column and excluded from
the count, because the tail column is ambiguous in **both** directions.
In one direction, a tail `f.x == e` can be a legitimate `Bool` return
(`c.r == c.g && c.g == c.b` in `C/g0c-granite8b-0shot-s1` t13 is correct
code), so pooling the two positions would inflate the count with true
positives of the model's own intent. In the other direction, tail
conversion is syntactic and unconditional — it applies to any block
regardless of the enclosing function's return type — and an un-braced
match-arm body (`pat => expr,`) is parsed as a bare expression rather
than a block, so a deformed assignment that happens to fall LAST in
either position lands in the tail column too and is never counted.

**Consequently `18` is a lower bound on deformation, not an exact
count.** Pooling the columns would overcount; the statement column alone
undercounts. The two are kept separate for exactly this reason, and every
claim built on the number below is a lower-bound claim.

Direct `p.x = e` in the unconstrained condition, where nothing deforms it
and it is plain `OX0101`: **4 programs of 600** (granite 3, qwen 1,
codegemma 0).

Two consequences, both load-bearing for what follows.

**The unconstrained condition supplies a clean control.** 18 occurrences
under constraint, exactly 0 without it, with a fully explicable
mechanism: `=` is inadmissible after a field path, `==` is the nearest
valid continuation, and the result is a statement whose value is
discarded. The claim here is only about this signature in this pair of
conditions — no comparison to §54's artifact is asserted, since that
work ran a before/after of the fix rather than a constrained/
unconstrained contrast. This datum belongs in the deformation write-up
(direction memo item 4) regardless of what the ergonomics delta turns
out to be.

**The demand is too small to measure on its own.** ~1.5% of constrained
programs. A dedicated 1,800-session campaign would return an aggregate
null indistinguishable from noise — the exact shape of withdrawn claims
#1 and #4. This does not contradict the taxonomy, which ranked dossier 2
on being a real language hole plus an instrument artifact and whose
evidence file already records "Small in count (4) but unambiguous in
intent". It changes only the measurement plan (below).

## The design

### 1. Surface and grammar

```ebnf
stmt              := let_stmt | assign_stmt | field_assign_stmt
                   | return_stmt | while_stmt | for_stmt | expr_stmt
field_assign_stmt := IDENT ("." IDENT)+ "=" expr TERM
```

Arbitrary path depth (`a.b.c = e`), base restricted to a bare name.
Depth is admitted rather than capped at one level because a grammar that
admits only `p.x = e` leaves `a.b.c = e` deforming under constrained
decoding — partial admission would leave open the very hole this section
closes. The base stays a name: an arbitrary-expression base (`f().x = e`)
drags resolve and codegen into temporaries, and index assignment
(`v[0] = e`) needs an indexing decision the SPEC has not made — the
taxonomy files `.set(i, v)` as a deferred non-candidate.

Parser lookahead at statement start: on `IDENT`, scan `(DOT IDENT)+` and
require a following `EQ`. `EQEQ` is a distinct token kind, so `p.x == y`
remains a comparison — the same guarantee §26's existing `IDENT EQ`
lookahead rests on. If the scan does not land on `EQ`, restore `self.pos`
and fall through to `_expr_stmt`. The scan must not see through a
`NEWLINE`, for the reason `_peek_next` documents: an identifier at end of
line is an expression statement, never the start of an assignment.

GBNF (`eval/grammar/build.py::_flat_stmts`) — the existing assignment
alternative gains a star, so plain assignment is still admitted by the
same rule, and **both dialects inherit it** from the one builder:

```python
seq(Ref("lname"), star(seq(Lit("."), Ref("lname"))), Lit(" = "), Ref(f"value-{t}"))
```

The grammar change ships in the same commit as the parser change
(parity invariant: what the language accepts, the grammar admits).

### 2. AST and resolve

```python
@dataclass(frozen=True, slots=True)
class FieldAssign:
    node_id: int
    span: Span
    base: str
    path: tuple[str, ...]
    value: Expr
```

`FieldAssign` joins the `type Stmt` union rather than `Assign` growing an
optional `path` field. The reason is failure mode, not taste: `cfg._stmt`
matches `case ast.Assign(value=value)` and emits a `ReInit`, which
re-establishes ownership of the target. That is exactly wrong for a field
write, and a widened `Assign` would keep matching there — compiling,
passing, and silently re-owning a moved base. A distinct node forces each
of the ~8 match sites to decide deliberately.

Span runs from the base token to the end of `value`.

Resolve looks up `base`, reusing the existing `OX0200` wording
(`unknown identifier '<n>' in assignment`) when unbound, and records it in
**`assign_of`, keyed by the `FieldAssign` node's own id**. One entry makes
three consumers correct at once, because all three already read
`resolve.assign_of`:

- `src/sema/modes.py` seeds `assigned_vars` from `.values()`, so a
  field-assigned parameter becomes mode `own`. **This is a soundness
  requirement.** A read-mode non-copy param emits `p: &Point`, and
  `p.x = 5` through a `&T` is rustc E0594 — accepted-implies-compiles
  would break. §28's existing rationale ("the callee overwrites the
  caller's value, so a borrow cannot do") carries over unchanged.
- `src/codegen/rust.py:169` builds the `mut` set from the same
  `.values()`, giving `let mut p`.
- The for-loop binder path (`rust.py:520`) reads that same set, giving
  `for mut x in …` when a body writes `x.f`.

`assign_of`'s docstring becomes "Assign/FieldAssign node_id → the
assigned variable's var_id (the base, for a field write)".

### 3. Typing

Walk the path left to right through the existing `_field_check`, reusing
its codes exactly: unknown field → `OX0304`, non-struct at any step →
`OX0306`. Unify the final field's type with the RHS type; mismatch →
`OX0300`. The statement itself is `Unit`.

### 4. Linearity

Three rules, each an existing rule applied to a new node rather than a
new concept:

- **The RHS is a MOVE context**, as in `Assign` (§28).
- **The base is a READ use and emits no `ReInit`.** §36 already fixes
  `p.x` as a read of the base with an implicit clone of a non-copy field
  value; writing into an owned struct is that rule's completion. A field
  write into a *moved* base therefore reports **`OX0400`** (read-context
  use-after-move) and must not re-own — which is precisely the behaviour
  an `Assign`-shaped node would have gotten wrong.
- **The overwritten field value is consumed implicitly, with no
  `DropPoint`** — §28's existing rule for the old value of an assigned
  variable. Rust's assignment drops the old field; synthesizing a drop
  would double-free.

Two interactions, checked against the code rather than assumed:

- **`OX0406` (iterated-borrow) is vacuous here.** `infer._for` unifies the
  iterable with `Vec<T>`, so a directly iterated bare variable is always a
  `Vec`, which has no fields — `v.f = e` is `OX0306` long before
  linearity runs. A field-access iterable (`for x in s.items`) emits
  `.clone()` under §36, so nothing stays borrowed across the loop.
- **Writing into a loop binder** (`for p in ps { p.x = 1 }`) is
  well-defined: the binder is a fresh owned clone per iteration
  (`.iter().cloned()`), so the write is local and discarded at iteration
  end. `for mut p` falls out of the `assigned` set automatically.

### 5. Codegen

`{base}.{f1}.{f2} = {value};`, built textually from `base` and `path`.
Deliberately **not** routed through the `FieldAccess` emitter, which
appends `.clone()` to non-copy field values (§36). A place is not a
value: cloning the target would write into a temporary and silently lose
the assignment.

### 6. Diagnostics

No new codes, and §40's suggestion table is untouched. `OX0200` unbound
base, `OX0304` no such field, `OX0306` non-struct in the path, `OX0300`
type mismatch, `OX0400` write into a moved base. A precision split (a
distinct "write into a moved value") is recorded as available if samples
later show reader confusion — which is how the `OX0304` → `OX0306` split
earned its way in, on 10 of 29 measured misdirections rather than on
anticipation.

### 7. Cards

**Unchanged, in both dialects.** This measures pure acceptance, exactly
as g1 did and for the same reason: models already write `p.x = e`
unprompted, so a card line would confound the language change with
teaching. It is also free — the core card is at the 900-word cap, and any
addition would have to be mirrored in the explicit card to hold the 10%
band, perturbing a treatment every prior run was conditioned on.

## Verification

### Test plan

Mirrors the §55 commit's footprint, plus grammar (which §55 did not need):

- `tests/test_parser.py` — lookahead accepts `p.x = e` and `a.b.c = e`;
  `p.x == y` still parses as a comparison; a failed scan restores
  position and yields an expression statement; span runs base→value;
  `p.x` at end of line is not an assignment start.
- `tests/test_sema_types.py` — `OX0304` unknown field, `OX0306`
  non-struct at each path position, `OX0300` RHS mismatch, statement
  types as `Unit`.
- `tests/test_linear.py` — `OX0400` on a moved base; no `DropPoint` for
  the overwritten field; loop-binder write is clean; a field-assigned
  param is forced to mode `own`.
- `tests/test_codegen.py` — no `.clone()` on the assignment target;
  `let mut` / `for mut` emission; nested path emits `a.b.c = …;`.
- `tests/test_explicit.py` — dialect parity (the `ExplicitParser`
  subclass inherits the statement path; `strip` handles the node).
- `tests/test_6a.py` and `tests/test_grammar_admission.py` — the GBNF
  admits the form, and the soundness direction (grammar output parses)
  still holds.

### The signature detector is a committed tool, not a scratch script

The expression-statement `f.x == e` scan is g2's pre-registered primary
endpoint, so it ships in the repo with a pinned definition and its own
test — not in a job tmp dir. This follows the precedent set when the 6a
pilot's demand table proved irreproducible and the pinned definition
moved into `eval/g0_report.py` behind `--demand-histogram`.

Pinned definition: parse the submission with the dialect-appropriate
parser; count `ExprStmt` nodes whose expression is a `BinOp` with
`op == "=="` and a `FieldAccess` left-hand side. Tail-position
occurrences are reported as a separate column and never pooled into the
signature count.

### Measurement plan

**No dedicated g2 campaign.** g2's endpoint is folded into the g3
(conversion builtins) run: one campaign, two changes, distinct
pre-registered endpoints that stay attributable at the level each is
actually read.

Pre-registered predictions, written before implementation:

| endpoint | prediction |
|---|---|
| ExprStmt-position `f.x == e`, constrained oxide | 18 → **0** (the form is admitted, so the deformation has nowhere to go). **A lower-bound endpoint**: 18 counts statement position only, and a deformation falling last lands in the tail column, which is ambiguous in both directions. Read the prediction as "the lower bound goes to 0", and read a residual tail count as uninterpretable rather than as surviving deformation. |
| the same signature, unconstrained | 0 → 0 (unchanged; there was never anything to deform) |
| aggregate first-attempt pass rate, g2 alone | **no detectable change** — 1.5% prevalence cannot move it, and any apparent movement should be read as noise, not effect |
| `OX0300` Bool-vs-Unit sub-class | small fall in the families carrying the signature (codegemma, granite), plausibly zero net |
| rust arm | flat at the first-attempt rate level (never at the byte level — g1 established that llama-server is not byte-deterministic across server instances) |

The honest framing of a null: at this prevalence the campaign cannot
distinguish a small real effect from zero, and the pre-registration says
so in advance rather than after the fact.

## Scope boundary

In: the `s.f = e` statement form at arbitrary field depth, its grammar,
and the analysis and codegen that follow.

Out, and recorded rather than silently dropped: index assignment
(`v[0] = e`) and `.set(i, v)`, pending an indexing decision; an
arbitrary-expression assignment base; compound assignment (`p.x += 1`),
which no sample reached for; `if let` and `mut` in parameter position,
which the taxonomy's dossier 6 already routes to v0.4 and to a card
clause respectively.
