# Black Oxide Transpiler — Phase 1 Contract (Scaffolding + Lexer)

This file is the **binding contract** for Phase 1. Implementation and tests are
written independently against this document; any deviation is a bug in the
deviating side.

## 0. Naming, and the surfaces deliberately NOT renamed

The language and project are **Black Oxide** (repo slug `black-oxide`),
renamed from "Oxide" on 2026-08-09 to resolve a collision with the
unrelated `oxide-lang` project on GitHub. Prose throughout this document
says Black Oxide.

Four classes of surface keep the bare string `oxide`. This is deliberate
and recorded here so a later reader does not "finish" the rename and
break something:

1. **Model-facing treatment text.** `LANGUAGE_CARD.md` and
   `LANGUAGE_CARD_EXPLICIT.md`, and the `OX0306` suggestion string in
   §40. Every committed baseline (`g0c`, `g0u`, `g1c`) was generated
   conditioned on these exact strings; editing them retokenizes the
   prompt, so a renamed card is no longer comparable to the baseline the
   v0.3 dossiers are measured against. `OX0306`'s text is additionally
   the endpoint g3 measures — renaming it in the run that measures it
   would confound the result outright.
2. **Arm data keys.** `ARMS = ("oxide", "explicit", "rust")`, the `arm`
   field in every `cells.jsonl` / `triples.jsonl`, raw filenames
   (`t01.oxide.1.txt`), `eval/solutions/{oxide,explicit,rust}/`, and
   `check --arm oxide`. These are written into the whole committed
   experimental record.
3. **Emitted-code identifiers.** The reserved `__oxide_` prefix (§22),
   including `__oxide_ret` and `__oxide_self`. Changing it changes
   generated Rust.
4. **Filenames and literals.** The `.ox` source extension,
   `eval/grammar/oxide.gbnf`, and the corpus-validation check that
   prompts contain no occurrence of `"oxide"`/`"rust"` — which still
   does the right thing, since "black oxide" contains "oxide".

The natural moment to revisit (1) is the fine-tune track (§32.4), where
the corpus is regenerated and comparability resets anyway. (2), (3) and
(4) are internal identifiers with no user-visible cost to leaving alone.

## 1. Project layout (create exactly this)

```
black-oxide/                # repo root (local checkout dir may differ)
├── main.py                 # minimal entry stub: docstring + main() that passes
├── conftest.py             # empty (makes repo root importable under pytest)
├── SPEC.md                 # this file
├── src/
│   ├── __init__.py
│   ├── source.py           # SourceFile
│   ├── diagnostics.py      # Span, Diagnostic
│   ├── lexer/
│   │   ├── __init__.py
│   │   ├── tokens.py       # TokenKind, Token, KEYWORDS, TERMINATOR_SET
│   │   └── lexer.py        # class Lexer
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── ast.py          # docstring-only stub (Phase 2)
│   │   └── parser.py       # docstring-only stub (Phase 2)
│   ├── sema/
│   │   ├── __init__.py
│   │   ├── resolve.py      # stub
│   │   ├── types.py        # stub
│   │   ├── infer.py        # stub
│   │   ├── modes.py        # stub
│   │   ├── cfg.py          # stub
│   │   ├── liveness.py     # stub
│   │   └── linear.py       # stub
│   └── codegen/
│       ├── __init__.py
│       └── rust.py         # stub
└── tests/
    ├── __init__.py
    └── test_lexer.py       # written by the test agent ONLY
```

Style: `@dataclass(frozen=True, slots=True)` for all value types, full type
hints, no prints, no file over 800 lines. Python 3.14.

## 2. Public API (exact names — tests import these)

```python
# src/diagnostics.py
@dataclass(frozen=True, slots=True)
class Span:
    start: int              # byte offset, inclusive
    end: int                # byte offset, exclusive

@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str               # e.g. "OX0006"
    message: str
    span: Span

# src/source.py
@dataclass(frozen=True, slots=True)
class SourceFile:
    text: str
    line_starts: tuple[int, ...]
    @staticmethod
    def from_text(text: str) -> "SourceFile": ...
    def line_col(self, offset: int) -> tuple[int, int]:  # 1-based, via bisect
```

```python
# src/lexer/tokens.py
class TokenKind(Enum):
    # literals
    INT; FLOAT; STRING
    # identifiers & keywords
    IDENT; KW_FN; KW_LET; KW_IF; KW_ELSE; KW_WHILE; KW_RETURN
    KW_STRUCT; KW_MATCH; KW_TRUE; KW_FALSE
    # operators
    ARROW      # ->
    FATARROW   # =>
    EQEQ; NEQ; LEQ; GEQ; ANDAND; OROR
    EQ; LT; GT; PLUS; MINUS; STAR; SLASH; PERCENT; BANG; DOT
    # delimiters
    LPAREN; RPAREN; LBRACE; RBRACE; COMMA; COLON
    PATH_SEP   # ::
    # structure
    NEWLINE; EOF; ERROR

@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    lexeme: str             # sys.intern'd for IDENT
    span: Span
    value: object = None    # int / float / unescaped str for literals

KEYWORDS: dict[str, TokenKind]        # "fn" -> KW_FN, ... incl. true/false
TERMINATOR_SET: frozenset[TokenKind]  # see §3.2
```

```python
# src/lexer/lexer.py
class Lexer:
    def __init__(self, source: str): ...
    def tokenize(self) -> list[Token]:   # ALWAYS ends with exactly one EOF token
    # after tokenize(): self.diagnostics: list[Diagnostic]  (in source order)
```

The lexer **never raises** on any input. Errors become `ERROR` tokens plus
queued Diagnostics; lexing always continues.

## 3. Lexing rules (normative)

### 3.1 Whitespace & comments
- Skip space, tab, `\r`.
- `//` line comment: skip to (not including) `\n`.
- `/* ... */` block comment: **nested** (depth counter). Skipped entirely;
  never affects NEWLINE emission or `prev_kind`. Unterminated at EOF →
  Diagnostic **OX0002**, emit one ERROR token spanning from `/*` to EOF.

### 3.2 Go-style implicit statement termination
Emit a `NEWLINE` token for a `\n` **iff** the previously *emitted* token's kind
is in:

```
TERMINATOR_SET = { IDENT, INT, FLOAT, STRING, KW_TRUE, KW_FALSE,
                   KW_RETURN, RPAREN, RBRACE }
```

Otherwise the `\n` is plain whitespace. `prev_kind` is simply the kind of the
last emitted token (so a run of blank lines yields at most one NEWLINE, since
NEWLINE ∉ TERMINATOR_SET). Comments do not update `prev_kind`.
**At EOF:** if `prev_kind ∈ TERMINATOR_SET`, emit one final NEWLINE before the
EOF token. EOF token: kind EOF, lexeme "", span `(len, len)`.
ERROR ∉ TERMINATOR_SET.

### 3.3 Identifiers & keywords
`[A-Za-z_][A-Za-z0-9_]*`, maximal munch, then dict lookup in KEYWORDS
(`fn let if else while return struct match true false`). Non-keywords are
IDENT with `sys.intern`'d lexeme.

### 3.4 Numbers (`scan_number`, first char is a digit)
1. `0x`/`0o`/`0b` prefix → munch digits of that radix plus `_`. Empty digit
   run → **OX0003**, ERROR token. Radix literals are **integers only** (a
   following `.` is a DOT token, not a float).
2. Else munch `[0-9_]*`.
3. Decimal only: if next is `.` **and the char after it is a digit**, consume
   the `.` and munch `[0-9_]*` → float. (`1.` lexes as INT then DOT; `x.0`
   after an ident lexes IDENT DOT INT.)
4. Decimal only: `e`/`E` [+|-] digits → float. Missing exponent digits →
   OX0003 ERROR.
5. **Adjacency rule:** if the char after the literal is a letter or `_`,
   munch the whole alnum run into ONE ERROR token → **OX0004**
   (e.g. `123abc` is a single ERROR token with lexeme "123abc").
6. `value` = `int(digits.replace("_",""), radix)` or `float(...)`.

### 3.5 Strings
Delimited by `"`. Escapes: `\n \t \\ \" \0` and `\u{H}`…`\u{HHHHHH}` (1–6 hex
digits, braces required). Invalid escape → **OX0005**, substitute U+FFFD into
the value, KEEP scanning; the token is still kind STRING. A raw `\n` (or EOF)
before the closing quote → **OX0006** "unterminated string", ERROR token
spanning from the opening quote to end of line, then resume lexing at the
next line. `value` = the unescaped string.

### 3.6 Operators & delimiters — maximal munch
Two-char first: `-> => == != <= >= && || ::`, then one-char:
`= < > + - * / % ! . ( ) { } , :`. A lone `&` or `|` or any other unknown
char → **OX0001** "unexpected character", ERROR token of length 1, continue.

### 3.7 Error codes
| Code | Meaning |
|---|---|
| OX0001 | unexpected character |
| OX0002 | unterminated block comment |
| OX0003 | malformed numeric literal (empty radix digits / empty exponent) |
| OX0004 | invalid suffix on numeric literal |
| OX0005 | invalid escape sequence |
| OX0006 | unterminated string literal |

## 4. Golden examples (normative — tests assert these exactly)

### G1 — canonical program
Source (trailing newline present):
```
fn main() {
    let x = 42
    print(x)
}
```
Token kind sequence:
```
KW_FN IDENT LPAREN RPAREN LBRACE
KW_LET IDENT EQ INT NEWLINE
IDENT LPAREN IDENT RPAREN NEWLINE
RBRACE NEWLINE EOF
```
(no NEWLINE after `{` — LBRACE is not a terminator). INT value == 42.

### G2 — operators & literals
Source: `let y = 1.5 * (2 + x) >= 0x1F && a != b` (no trailing newline)
```
KW_LET IDENT EQ FLOAT STAR LPAREN INT PLUS IDENT RPAREN
GEQ INT ANDAND IDENT NEQ IDENT NEWLINE EOF
```
FLOAT value == 1.5; the two INT values == 2 and 31. The final NEWLINE is the
EOF-injection rule (prev IDENT is a terminator).

### G3 — path separator
`Vec::new()` → `IDENT PATH_SEP IDENT LPAREN RPAREN NEWLINE EOF`

## 5. Test plan (tests/test_lexer.py — pytest)

Import as `from src.lexer.lexer import Lexer`,
`from src.lexer.tokens import TokenKind`, run from repo root.
Helper: `kinds(src) -> list[TokenKind]` via `Lexer(src).tokenize()`.

Required tests:
1. G1, G2, G3 exact kind sequences + literal values as above.
2. Newline rules: no NEWLINE after `{` or a binary operator at line end;
   blank-line runs collapse to one NEWLINE; NEWLINE injected before EOF only
   after terminator kinds.
3. `x = 1 // trailing comment\n` still emits NEWLINE after INT.
4. Nested block comment `/* a /* b */ c */` skipped entirely (and does not
   trigger NEWLINE emission).
5. Unterminated block comment → ERROR token + one OX0002 diagnostic.
6. String escapes: `"a\n\t\\\"\u{48}b"` value == 'a\n\t\\"Hb'.
7. Invalid escape `"\q"` → STRING token, value contains '�', one OX0005.
8. Unterminated string: `let s = "abc\nlet t = 1` → ERROR + OX0006, and
   lexing resumes: later tokens include KW_LET IDENT EQ INT.
9. Numbers: `1_000` == 1000; `0b1010` == 10; `0o17` == 15; `2e3` == 2000.0;
   `1.` → INT DOT; `x.0` → IDENT DOT INT.
10. `123abc` → single ERROR token (lexeme "123abc") + OX0004.
11. `0x` → ERROR + OX0003.
12. `@` → ERROR + OX0001; lexing continues to a following token.
13. Lone `&` → ERROR + OX0001.
14. Maximal munch: `a->b`, `a=>b`, `a<=b`, `a::b`, `a&&b` produce the 2-char
    kinds; `a<b`, `a=b` produce 1-char kinds.
15. Every keyword maps to its KW_*; `fnx`/`letter`/`iffy` are IDENT.
16. Spans: for `let x = 1`, token spans are (0,3) (4,5) (6,7) (8,9) and the
    EOF span is (len,len).
17. Never raises: tokenize a handful of garbage inputs
    (`'"\\u{'`, `'/*/*/*'`, `'\x00\xff@#$'`, `'0x 0b2 9e'`) — assert only
    that tokenize() returns and last token is EOF.

---

# Part II — Phase 2 Contract (Parser + AST)

Phase 1 rules above remain binding. Part II governs `src/parser/ast.py`,
`src/parser/parser.py`, and `tests/test_parser.py`.

## 6. Grammar (normative; TERM = NEWLINE or lookahead RBRACE or EOF)

```ebnf
module     := item* EOF
item       := fn_decl | struct_decl
fn_decl    := "fn" IDENT "(" [param ("," param)* [","]] ")" ["->" type] block
param      := IDENT [":" type]
struct_decl:= "struct" IDENT "{" [field ("," field)* [","]] "}"
field      := IDENT ":" type
type       := IDENT ["<" type ("," type)* ">"]        # e.g. Vec<Vec<Int>>

block      := "{" stmt* "}"
stmt       := let_stmt | return_stmt | while_stmt | expr_stmt
let_stmt   := "let" pattern [":" type] "=" expr TERM
pattern    := IDENT | IDENT "{" IDENT ("," IDENT)* [","] "}"
return_stmt:= "return" [expr] TERM
while_stmt := "while" expr block
expr_stmt  := expr TERM

expr       := if_expr | pratt_expr
if_expr    := "if" expr block ["else" (block | if_expr)]
```

**Tail rule:** after parsing a block's statements, if the LAST statement is an
expression statement and the next token is `}`, it becomes the block's `tail`
(the block's value) instead of a statement. A NEWLINE before `}` does not
prevent this.

**NEWLINE handling:** NEWLINE tokens are significant only as statement
terminators inside blocks and as item separators at module level (where runs
are skipped). They are skipped freely: inside `( ... )` groups (call args,
param lists, parenthesized exprs), inside struct-declaration braces, inside
struct-literal braces, and inside `< ... >` type argument lists. An operator
at end of line continues the expression naturally (the lexer emits no NEWLINE
after non-terminator tokens).

**Struct-literal restriction:** `IDENT { ... }` is NOT parsed as a struct
literal at the top level of an `if`/`while` condition (the `{` starts the
body). Inside any parenthesized subexpression the restriction lifts.

## 7. AST (src/parser/ast.py — exact names)

All nodes are `@dataclass(frozen=True, slots=True)` with fields
`node_id: int` and `span: Span` FIRST, then their own fields. Sequences are
tuples. Node catalog:

```
Module(items: tuple)                       FnDecl(name, params: tuple,
Param(name, ty)                                   ret_ty, body)
StructDecl(name, fields: tuple)            FieldDef(name, ty)
TypeExpr(name, args: tuple)
Block(stmts: tuple, tail)                  Let(pattern, ty, init)
BindPat(name)                              DestructPat(struct_name,
Return(value)                                         field_names: tuple[str])
While(cond, body)                          ExprStmt(expr)
If(cond, then_blk, else_blk)               # else_blk: Block | If | None
Call(callee, args: tuple)                  BinOp(op: str, lhs, rhs)
UnOp(op: str, operand)                     FieldAccess(obj, field: str)
StructLit(name, fields: tuple[tuple[str, expr]])
Var(name)                                  Lit(value, kind: str)
ErrorExpr()                                ErrorStmt()
```

`Lit.kind` ∈ {"int","float","str","bool"}. `BinOp.op`/`UnOp.op` are the
operator lexemes ("+", "==", "&&", "-", "!", …). `node_id` is assigned by the
Parser from a per-instance counter starting at 0; all ids in one parse are
unique. Optional fields are `None` when absent.

## 8. Canonical dump (ast.py: `def dump(node) -> str`) — golden-test format

Space-separated S-expressions; `node_id`/`span` excluded. Exact productions:

```
Module      (module I1 I2 ...)
FnDecl      (fn NAME (params P1 ...) (ret TY)? BLOCK)     # (ret …) omitted if None
Param       (param NAME TY?)
StructDecl  (struct NAME (field N1 TY1) ...)
TypeExpr    (type NAME A1 ...)
Block       (block S1 ... (tail E)?)                       # (block) if empty
Let         (let PAT TY? E)
BindPat     (bind NAME)
DestructPat (destruct SNAME F1 F2 ...)
Return      (return E?)
While       (while COND BLOCK)
ExprStmt    (exprstmt E)
If          (if COND THEN ELSE?)                           # ELSE dumps as block or if
Call        (call CALLEE A1 ...)
BinOp       (bin OP L R)
UnOp        (un OP X)
FieldAccess (field OBJ NAME)
StructLit   (structlit NAME (F1 E1) (F2 E2) ...)
Var         (var NAME)
Lit         (lit KIND V)   # int: str(v); float: repr(v); bool: true/false;
                           # str: Python repr(v)
ErrorExpr   (error)
ErrorStmt   (error)
```

## 9. Parser API (src/parser/parser.py)

```python
class Parser:
    def __init__(self, tokens: list[Token]): ...
    def parse_module(self) -> Module: ...
    # after parse_module(): self.diagnostics: list[Diagnostic]

def parse_source(source: str) -> tuple[Module, list[Diagnostic]]:
    """Lex + parse. Diagnostics = lexer's, then parser's. Never raises."""
```

The parser NEVER raises on any token stream and always returns a Module.

## 10. Expressions — Pratt binding powers

| Operator | lbp | rbp | Assoc |
|---|---|---|---|
| `\|\|` | 1 | 2 | left |
| `&&` | 3 | 4 | left |
| `== != < <= > >=` | 5 | 6 | **non-assoc** (see below) |
| `+ -` | 7 | 8 | left |
| `* / %` | 9 | 10 | left |
| prefix `- !` | — | 11 | — |
| postfix `.field` `call(...)` | 13 | — | — |

Non-assoc rule: inside one `parse_expr` loop, a second comparison operator
after a comparison → diagnostic **OX0110** (exactly one), then parsing
CONTINUES left-associatively: `a < b < c` yields
`(bin < (bin < (var a) (var b)) (var c))` + one OX0110. `(a < b) < c` is
legal (the parenthesized lhs is a fresh loop). Postfix binds tighter than
prefix: `-a.b` → `(un - (field (var a) b))`; `f(x)(y)` →
`(call (call (var f) (var x)) (var y))`.

nud dispatch: INT/FLOAT/STRING/KW_TRUE/KW_FALSE → Lit; IDENT → StructLit if
next is `{` and struct-literals allowed here, else Var; `-`/`!` → UnOp;
`(` expr `)`; KW_IF → if-expr. Anything else → **OX0100** "expected
expression" + ErrorExpr. **Exception:** an ERROR token in nud position is
consumed into ErrorExpr with NO new diagnostic (the lexer already reported
it — no cascades).

## 11. Errors & recovery

Codes: **OX0100** expected expression · **OX0101** expected token (generic
`expect` failure; message names expected & found) · **OX0102** expected item
at module level · **OX0103** expected type · **OX0104** expected pattern ·
**OX0110** chained comparison.

Panic-mode recovery on any failure:
`sync = {NEWLINE, RBRACE, KW_LET, KW_RETURN, KW_WHILE, KW_IF, KW_FN,
KW_STRUCT, EOF}` — skip tokens until a sync kind; consume it if NEWLINE. The
failed production yields ErrorExpr/ErrorStmt (real node_id + span). One
diagnostic per error region; later items/statements must still parse.

## 12. Golden examples (normative)

**P1** — source of Phase 1 G1 (fn main / let x = 42 / print(x)):
```
(module (fn main (params) (block (let (bind x) (lit int 42)) (tail (call (var print) (var x))))))
```

**P2** — `fn f() { let y = 1 + 2 * 3 == 7 && !flag }`:
body block = `(block (let (bind y) (bin && (bin == (bin + (lit int 1) (bin * (lit int 2) (lit int 3))) (lit int 7)) (un ! (var flag)))))`

**P3** —
```
struct Point { x: Int, y: Int }

fn add(p: Point) -> Int {
    let Point { x, y } = p
    x + y
}
```
```
(module (struct Point (field x (type Int)) (field y (type Int))) (fn add (params (param p (type Point))) (ret (type Int)) (block (let (destruct Point x y) (var p)) (tail (bin + (var x) (var y))))))
```

**P4** —
```
fn f(a: Int) -> Int {
    while a < 10 {
        step()
    }
    if a > 0 {
        a
    } else if a == 0 {
        make(Point { x: 1, y: 2 }).x
    } else {
        -a
    }
}
```
body block =
```
(block (exprstmt (while (bin < (var a) (lit int 10)) (block (tail (call (var step)))))) (tail (if (bin > (var a) (lit int 0)) (block (tail (var a))) (if (bin == (var a) (lit int 0)) (block (tail (field (call (var make) (structlit Point (x (lit int 1)) (y (lit int 2)))) x))) (block (tail (un - (var a))))))))
```

## 13. Test plan (tests/test_parser.py — pytest)

Import `from src.parser.parser import parse_source` and
`from src.parser.ast import dump` (plus node classes as needed). Helper
`d(src)` → `dump(parse_source(src)[0])`; `codes(src)` → diagnostic codes.

1. P1–P4 exact dumps; each with zero diagnostics.
2. Tail rule: block ending in `let` has no tail; single-line `fn f() { 1 }`
   has tail; NEWLINE before `}` does not block tail conversion.
3. Params/args/fields: empty `()`, trailing commas in params, call args,
   struct-decl fields, struct-lit fields; `struct S {}` legal.
4. Types: `Vec<Vec<Int>>` → `(type Vec (type Vec (type Int)))`;
   `let x: Int = 1` annotation dumps.
5. Precedence & assoc (parametrized dumps of the body tail): `a - b - c`
   left; `a && b || c` → `(bin || (bin && …) …)`; `-a * b` →
   `(bin * (un - (var a)) (var b))`; `-a.b`; `f(x)(y)`; `a.b.c`.
6. Chained comparison: `a < b < c` → exactly one OX0110 AND the P10-specified
   left-assoc dump.
7. If/else-if chains nest as If in else_blk; `let m = if c { 1 } else { 2 }`.
8. Struct-lit restriction: `if x { }` → cond is `(var x)`, empty block;
   `while p { }` same; parenthesized struct-lit in condition parses.
9. Multi-line call `f(\n  x,\n  y\n)` parses; operator at line end continues.
10. `return` and `return x` both parse (dump forms).
11. Recovery: `fn f() { let = 5 }` → OX0104, body contains `(error)`, and a
    FOLLOWING `fn g() {}` in the same source still parses;
    `fn f() { (1 + ) }` → one OX0100, tail `(bin + (lit int 1) (error))`;
    top-level `42` then `fn g() {}` → OX0102 and g survives.
12. No cascade: source `fn f() { let x = 123abc }` → lexer OX0004 present,
    and NO parser OX0100 for that ERROR token.
13. parse_source diagnostic ordering: lexer codes precede parser codes.
14. node_id uniqueness: collect all node_ids from a P4 parse (walk
    dataclass fields) — all distinct.
15. Spans: for `let x = 1 + 2`, the Let span covers the whole statement and
    the BinOp span covers `1 + 2` exactly.
16. Never raises: parse_source on the Phase 1 garbage inputs plus
    `'fn'`, `'fn f('`, `'{'`, `'}}}'`, `'fn f() -> {'` — returns a Module,
    last-resort ErrorStmt/ErrorExpr nodes allowed, no exception.

---

# Part III — Phase 3 Contract (Semantic Analysis)

Parts I–II remain binding. Part III governs `src/sema/*` and the two Phase 3
test files. Pipeline: resolve → infer → modes → linear (cfg/liveness are
internal to the linear checker; organize them in `cfg.py`/`liveness.py` per
the §1 layout, but only the APIs below are contractual).

## 14. Language semantics fixed for Phase 3

- **Types:** `Int`, `Float`, `Bool`, `Str`, `Unit`, `Vec<T>`, user structs
  (non-generic). **Copy types:** Int, Float, Bool, Unit. Str, Vec, and ALL
  structs are linear (regardless of field types).
- **Builtins** (the only polymorphic functions; instantiated fresh per use):
  `print: forall a. fn(a) -> Unit` modes `('read',)` ·
  `len: forall a. fn(Vec<a>) -> Int` modes `('read',)` ·
  `push: forall a. fn(Vec<a>, a) -> Vec<a>` modes `('own','own')` ·
  `vec: forall a. fn() -> Vec<a>` modes `()`.
- **User functions are monomorphic**, inferred whole-program: all fn
  signatures start as metavariables, all bodies and call sites constrain
  them, one global solve. Recursion needs no annotation.
- Functions are second-class: a global fn/builtin name may appear ONLY as a
  `Call` callee. Local bindings shadow global fn names.
- Literals type directly: int→Int, float→Float, string→Str, bool→Bool. No
  numeric defaulting exists.
- Blocks: value = tail's type, else Unit. `if` arms unify (missing else ⇒
  then-block must be Unit). `while`: cond Bool, value Unit; a while is never
  a value. `return e` unifies e (Unit if absent) with the fn return.
- Operators: `+ - * /` operands unify with each other, then must solve to
  Int or Float (`%` Int only) — result same type; `< <= > >=` likewise
  Int/Float, result Bool; `== !=` operands unify, any type, result Bool;
  `&& || !` Bool; unary `-` Int/Float.
- Struct literal: every declared field exactly once. Destructuring must name
  ALL fields of the struct. Field access `s.f` is legal ONLY when the
  field's type is Copy (non-copy field access → OX0405: destructure
  instead; applies in every context, even read positions).

## 15. Public APIs (exact)

`src/diagnostics.py` — ADD field `notes: tuple[tuple[str, Span], ...] = ()`
to `Diagnostic` (additive; Phase 1/2 construction sites unchanged and all
existing tests must stay green).

```python
# src/sema/types.py
@dataclass(frozen=True, slots=True)
class TVar:  id: int
@dataclass(frozen=True, slots=True)
class TCon:  name: str; args: tuple = ()          # TCon('Vec', (TCon('Int'),))
@dataclass(frozen=True, slots=True)
class TFn:   params: tuple; ret: object
Type = TVar | TCon | TFn
ERROR_TYPE = TCon('Error')                        # unifies with everything
def is_copy(ty) -> bool                           # Int/Float/Bool/Unit/Error
def type_str(ty) -> str   # 'Int', 'Vec<Int>', 'fn(Int, Str) -> Unit', TVar → '?'
BUILTINS: dict[str, BuiltinSig]  # per §14; BuiltinSig(params, ret, modes, generics)
```

```python
# src/sema/resolve.py
@dataclass(frozen=True, slots=True)
class VarInfo: var_id: int; name: str; fn: str; def_span: Span
@dataclass
class ResolveResult:
    use_of:   dict[int, int]              # Var node_id -> var_id (local uses only)
    binds_of: dict[int, tuple[int, ...]]  # Param/BindPat/DestructPat node_id -> var_ids
    var_info: dict[int, VarInfo]
    callee_of: dict[int, str]             # Call node_id -> global fn/builtin name
    fns:      dict[str, object]           # name -> FnDecl
    structs:  dict[str, object]           # name -> StructDecl
    diagnostics: list[Diagnostic]
def resolve(module) -> ResolveResult
```
var_ids: one per-module counter from 0, assigned in source order of binding
sites (each fn: params left-to-right, then body binders in pre-order).
Shadowing = fresh var_id. Destructure binds fields in declaration order of
the PATTERN's field list.

```python
# src/sema/infer.py
@dataclass
class InferResult:
    types: dict[int, Type]        # expr node_id -> solved type
    var_types: dict[int, Type]    # var_id -> solved type
    diagnostics: list[Diagnostic]
def infer(module, resolved) -> InferResult
```
Unsolved TVars after the global solve → OX0302 at the binding/expr, type
becomes ERROR_TYPE. ERROR_TYPE unifies with everything and suppresses
downstream diagnostics on the same node/var.

```python
# src/sema/modes.py
@dataclass
class ModeResult: modes: dict[str, tuple[str, ...]]   # fn -> 'own'|'read' per param; includes builtins
def infer_modes(module, resolved, inferred) -> ModeResult
```
Fixpoint over the call graph, optimistic init `read`, monotone read→own.
A param is `own` iff some path uses it in a MOVE context (§17 table) under
current assumptions. Copy-typed params are ALWAYS `read`. Recursion with no
other evidence converges to `read`.

```python
# src/sema/linear.py
@dataclass(frozen=True, slots=True)
class DropPoint:
    fn: str; var_id: int; var_name: str
    kind: str          # 'after-stmt' | 'block-end' | 'branch-end' | 'before-return'
    anchor_span: Span
@dataclass
class LinearResult:
    use_class: dict[int, str]     # Var node_id -> 'copy'|'read'|'move'
    drops: tuple[DropPoint, ...]
    diagnostics: list[Diagnostic]
def check_linear(module, resolved, inferred, modes) -> LinearResult
```

```python
# src/sema/analyze.py — full pipeline + the blind-test surface
@dataclass
class SemaResult:
    module; resolve; infer; modes; linear
    diagnostics: list[Diagnostic]   # lex, parse, resolve, infer, linear — phase order
def analyze(source: str) -> SemaResult          # NEVER raises
def diag_codes(res) -> list[str]
def var_types_by_name(res, fn, name) -> list[str]   # type_str per binding, binding order
def use_classes(res, fn, name) -> list[str]         # classes of that name's uses, source order
def param_modes(res, fn) -> tuple[str, ...]
def drop_list(res) -> list[tuple[str, str, str]]    # sorted (fn, var_name, kind)
```
**Gates:** parse errors ⇒ skip sema entirely. Resolve errors ⇒ skip
infer/modes/linear. Infer errors ⇒ skip modes/linear. A skipped phase
contributes empty results. Additionally, a function with any linear
diagnostic contributes NO DropPoints (its drops are suppressed).

## 16. Error codes

| Code | Phase | Meaning |
|---|---|---|
| OX0200 | resolve | unknown identifier |
| OX0201 | resolve | function/builtin name used as a value (non-callee) |
| OX0202 | resolve/infer | unknown type or struct name, or wrong type arity |
| OX0203 | resolve | duplicate top-level name (incl. clash with a builtin) |
| OX0204 | resolve | duplicate binder (params or one pattern) |
| OX0300 | infer | type mismatch (unification failure) |
| OX0301 | infer | infinite type (occurs check) |
| OX0302 | infer | ambiguous type (unconstrained after solve) |
| OX0303 | infer | not callable / wrong argument count |
| OX0304 | infer | struct shape: unknown/missing/duplicate field, incomplete destructure |
| OX0306 | infer | field access on a non-struct type |
| OX0305 | infer | invalid operand type for operator (post-solve check) |
| OX0400 | linear | use after move (READ-context use of a moved value) |
| OX0401 | linear | double move (MOVE-context use of a moved value) |
| OX0403 | linear | value moved in a previous loop iteration |
| OX0405 | linear | cannot use non-copy field through field access; destructure instead |

OX0400/OX0401/OX0403 diagnostics carry ≥ 1 entry in `notes` referencing the
conflicting move's span. **Poisoning:** after one linear diagnostic for a
variable, that variable produces no further diagnostics in that function.

## 17. Use-context classification (normative table)

For each `Var` use of a NON-COPY local (Copy locals are always `'copy'`,
never state-tracked):

| Position | class |
|---|---|
| argument to an `own` param | move |
| argument to a `read` param | read |
| `let` initializer (`let y = x`) | move |
| returned expression / fn-body tail value / `return e` | move |
| struct-literal field value | move |
| destructure scrutinee | move |
| any operator operand (`== != < …`), `if`/`while` condition | read |
| base of a field access (`s.f`) | read (but see OX0405 for the field itself) |
| block tail feeding a `let`/arg (the if-expr value chain) | move |

State machine per var: `Owned → Moved(span)` on move; READ on Owned stays
Owned; any use on Moved → OX0400 (read ctx) / OX0401 (move ctx), then
poison. Loop bodies run to fixpoint; a conflict whose original move
happened in a previous iteration reports OX0403 (once, then poison).

## 18. Drop insertion (automatic destruction)

For error-free functions, every non-copy value is consumed exactly once per
path — by program code or a synthesized DropPoint.

**Read-mode non-copy params are caller-owned borrows** (amended for Phase 4):
the callee synthesizes NO DropPoints for them — the caller's own analysis
drops the value after the call. Exactly-once therefore holds program-wide:
each value is destroyed once, by its owner. Placement kinds:

- **after-stmt** — var whose FINAL use is a read: dropped after the
  outermost statement (in its defining scope) at which liveness ends. The
  fn-body tail expression counts as a statement position. A var last read
  inside a `while` body (live around the back edge) is dropped after the
  while statement itself.
- **block-end** — var never used after its definition: dropped at its
  defining block's end. Also: at an if/else merge where one REAL arm moved
  the var, the still-owning arm drops it at that arm's block end (only when
  the var is dead after the merge; if it is live after the merge, the next
  use is OX0400 instead).
- **branch-end** — same hoisting when the non-moving edge is an ABSENT else:
  anchor_span = the If node's span.
- **before-return** — every still-owned in-scope var (except the returned
  value) drops immediately before an early `return`.
- **`<temp>`** — an expression-statement (non-tail) whose value is non-copy
  discards a temporary: DropPoint(var_id=-1, var_name='<temp>',
  kind='after-stmt'). Other temporaries are out of scope for v0.1.

## 19. Golden examples (normative — `analyze` helper outputs)

**S1** `fn main() { let v = vec()\n let v2 = push(v, 1)\n print(len(v2)) }`
→ codes `[]`; `var_types_by_name(main,'v')==['Vec<Int>']`, same for v2;
`use_classes(main,'v')==['move']`, `(main,'v2')==['read']`;
`drop_list==[('main','v2','after-stmt')]`.

**S2** S1 but final line `print(len(v))` after `let w = push(v, 1)`
→ codes `['OX0400']` (notes non-empty); `drop_list==[]` (error fn).

**S3** `fn f(v: Vec<Int>) -> Vec<Int> { let a = push(v, 1)\n let b = push(v, 2)\n a }`
→ codes `['OX0401']`; `param_modes(f)==('own',)`; `drop_list==[]`.

**S4** `fn g(c: Bool, v: Vec<Int>) { if c { let w = push(v, 1) } }`
→ codes `[]`; `param_modes(g)==('read','own')`;
`drop_list==[('g','v','branch-end'),('g','w','block-end')]`.

**S5** `fn h(v: Vec<Int>) { while true { let w = push(v, 1) } }`
→ codes `['OX0403']`; `drop_list==[]`.

**S6** `fn k(c: Bool, v: Vec<Int>) -> Int { if c { return 0 }\n len(v) }`
→ codes `[]`; `drop_list==[]` (v is a read-mode param ⇒ caller-owned;
amended with §18).

**S7**
```
struct Point { x: Int, y: Int }
fn area(p: Point) -> Int { let Point { x, y } = p\n x * y }
```
→ codes `[]`; `param_modes(area)==('own',)`; `use_classes(area,'x')==['copy']`;
`var_types_by_name(area,'p')==['Point']`; `drop_list==[]`.

**S8** `fn wrap(v: Vec<Int>) -> Vec<Int> { push(v, 1) }` +
`fn caller(v: Vec<Int>) { let w = wrap(v)\n print(len(w)) }`
→ codes `[]`; modes: wrap `('own',)`, caller `('own',)`;
`drop_list==[('caller','w','after-stmt')]`.

**S9** `fn bad() { let x = 1 + true }` → codes `['OX0300']`; drop_list `[]`.

**S10** `fn f() { print(g)\n print(len) }` → codes `['OX0200','OX0201']`.

**S11** `fn h3(v: Vec<Int>) { while true { print(len(v)) } }`
→ codes `[]`; `drop_list==[]` (read-mode param ⇒ caller-owned; amended
with §18).

**S12** `fn t(v: Vec<Int>) { push(v, 1)\n print(0) }`
→ codes `[]`; `drop_list==[('t','<temp>','after-stmt')]`.

## 20. Test plan — two files

**tests/test_sema_types.py** (resolve + infer + modes):
1. Goldens S7–S10 (codes, types, modes as stated).
2. Literal typing; unannotated param inferred from body (`fn double(x) -> Int { x + x }` → x Int) and from call site across functions.
3. vec/push/len chain types (S1's `var_types_by_name`).
4. if-expr unification, arm mismatch OX0300, missing-else non-Unit OX0300, non-Bool cond OX0300.
5. Operators: `true + false` → OX0305; `1 % 2` ok Int; float `%` → OX0305; `1 < 1.5` → OX0300; `!1` → OX0300; `== `on Vec ok (result Bool).
6. Struct shapes: field type mismatch OX0300; missing/extra/duplicate literal field OX0304; incomplete destructure OX0304; unknown struct OX0202; unknown field access OX0304; field access on Int OX0304.
7. Annotations: good `Vec<Int>`; `let x: Int = 1.5` OX0300; unknown name OX0202; `Vec<Int, Int>` OX0202; `Int<Int>` OX0202.
8. `let v = vec()` alone → OX0302.
9. `len()` and calling an Int local → OX0303.
10. Resolution: OX0200; OX0201; duplicate fn OX0203; fn named `print` OX0203; dup param OX0204; dup destructure binder OX0204; shadowing legal with independent types (`['Int','Bool']`).
11. Modes: S3/S4/S8 goldens; returned copy param stays `read`; pure recursion stays `read` (`fn r(v: Vec<Int>) { r(v) }` → `('read',)`); destructured param `own` (S7).
12. Gates: resolve error suppresses infer codes; parse error yields only lex/parse codes; analyze never raises on Part II garbage inputs.

**tests/test_linear.py**:
1. Goldens S1–S6, S11, S12 exactly as §19.
2. `let y = x` then a use of `x` → OX0400 with non-empty notes; using `y` instead is clean.
3. Poisoning: two later uses of a moved var → exactly one diagnostic.
4. Real-else hoisting: `if c { let a = push(v, 1) } else { }` → drops `{(fn,'v','block-end'),(fn,'a','block-end')}`, no codes.
5. Conditional move then later use → codes `['OX0400']`.
6. Both arms consume → no v drop, no codes.
7. Loop-local binding is clean (`while true { let w = push(vec(), 1) }` → w block-end drop, no codes).
8. Copy exemption: Int var used 3× → all `'copy'`, no drops, no codes.
9. Shadowing chain `let v = push(vec(), 1)\n let v = push(v, 2)\n print(len(v))` → codes `[]`, `use_classes==['move','read']`, one after-stmt drop.
10. Unused linear param → NO drops (caller-owned read borrow, per amended
    §18), mode `read`.
11. OX0405: struct with a `Vec<Int>` field, `s.v` in any position → `['OX0405']`.
12. Suppression: type-error source ⇒ `drop_list==[]` and no OX04xx codes.

Test authors: import ONLY `src.sema.analyze` helpers (plus pytest); do not
import other sema modules; do NOT run the tests (blind TDD — implementation
lands concurrently).

---

# Part IV — Phase 4 Contract (Rust Codegen)

Parts I–III (as amended) remain binding. Part IV governs `src/codegen/rust.py`,
`main.py`, and `tests/test_codegen.py`. rustc 1.96.0 is at
the toolchain's rustc, located via `eval/rustc_adapter.find_rustc()` (it is not necessarily on PATH; the reference machine has it at
`~/.cargo/bin/rustc`).

## 21. API

```python
# src/codegen/rust.py
def emit_rust(res: SemaResult) -> str
    # precondition: res.diagnostics == []; raises ValueError otherwise
def transpile(source: str) -> tuple[str | None, list[Diagnostic]]
    # analyze + emit; (rust_text, []) on success, (None, diags) otherwise; NEVER raises
```

`main.py` becomes a minimal CLI: `python3 main.py <file.ox>` → Rust to
stdout, exit 0; on diagnostics → render each to stderr as
`error[OXnnnn] <line>:<col>: <message>` (one `  note <line>:<col>` line per
notes entry), exit 1, using `SourceFile.line_col`; missing/unreadable file →
message to stderr, exit 2.

## 22. Mapping rules (normative)

**Types:** Int→`i64`, Float→`f64`, Bool→`bool`, Str→`String`, Unit→`()`,
`Vec<T>`→`Vec<T'>`, struct→its name.

**Items:** structs emit `#[derive(Debug)]` ONLY (never Clone/Copy —
linearity is preserved in the target); fields one per line with trailing
comma. Functions: own param → `name: T`; read non-copy param → `name: &T`
(the var is *ref-bound*); read copy param → `name: T`. Unit return → no
`->` clause. Items in source order, one blank line between; if the module
has no `fn main`, append `fn main() {}` last.

**Statements:** `let` → `let name: T = expr;` (always annotated, T from
var_types); destructure `let Point { x, y } = expr;` (never annotated).
ExprStmt: non-copy value → `drop(expr);`, else `expr;`. `return e;`,
`while cond { }`, `if`/`else` direct. Blocks map tail-to-tail.

**Uses:** move-class → bare `name`. copy-class → bare. read-class: bare in
operator/condition/field-base positions; **ref-form** when the position
requires a reference. Ref-form(E): ref-bound Var → `name`; owned Var →
`&name`; call or literal → `&E`; anything else → `&(E)`.
Positions requiring ref-form: args to `print` (always, any type), args to
`len`, args to user read-mode NON-COPY params (read-mode copy params take
bare values), and BOTH operands of `==`/`!=` when the operand type is
non-copy. Ordering operators only ever see copy types (§14) → bare.

**Drops:** after-stmt DropPoint → `drop(name);` immediately after the
anchor statement. block-end → `drop(name);` at the end of that block's
statements (before its tail, if any). branch-end → synthesize
`else { drop(name); }` on the anchor If. before-return → `drop(name);`
lines immediately before the `return`. Multiple drops at one anchor:
descending var_id (reverse declaration order). When drops anchor at/after a
block's TAIL: Unit-typed tail → emit it as a statement, then the drops;
otherwise → `let __oxide_ret: T = TAIL;` + drops + `__oxide_ret`.

**Names:** per function, if a source name binds more than once, emitted
names are `name`, `name__2`, `name__3`, … in binding (var_id) order.
Idents that are Rust keywords emit as raw `r#name`; `self`/`Self`/`super`/
`crate` emit as `__oxide_self` etc. Names beginning `__oxide_` are reserved.

**Formatting:** 4-space indent; single spaces around binary operators; no
trailing whitespace; file ends with one newline. Parenthesize a BinOp
operand iff it is a BinOp of lower precedence than its parent, or equal
precedence on the right; unary operands that are BinOps are always
parenthesized.

## 23. Prelude (byte-exact, after the header line and a blank line)

```rust
#![allow(dead_code)]

fn print<T: std::fmt::Debug>(x: &T) {
    println!("{:?}", x);
}

fn len<T>(v: &Vec<T>) -> i64 {
    v.len() as i64
}

fn push<T>(mut v: Vec<T>, x: T) -> Vec<T> {
    v.push(x);
    v
}

fn vec<T>() -> Vec<T> {
    Vec::new()
}
```

## 24. Golden emissions (normative)

**R1** — source S1 (`fn main` / vec / push / print(len)). Full output =
header + prelude + one blank line +
```rust
fn main() {
    let v: Vec<i64> = vec();
    let v2: Vec<i64> = push(v, 1);
    print(&len(&v2));
    drop(v2);
}
```
Compiled and run: stdout `1`.

**R2** — source S4. Emitted item exactly:
```rust
fn g(c: bool, v: Vec<i64>) {
    if c {
        let w: Vec<i64> = push(v, 1);
        drop(w);
    } else {
        drop(v);
    }
}
```

**R3** — source
`fn m(c: Bool, v: Vec<Int>) -> Int { let w = push(v, 1)\n if c { return 0 }\n len(w) }`.
Emitted item exactly:
```rust
fn m(c: bool, v: Vec<i64>) -> i64 {
    let w: Vec<i64> = push(v, 1);
    if c {
        drop(w);
        return 0;
    }
    let __oxide_ret: i64 = len(&w);
    drop(w);
    __oxide_ret
}
```

**R4** — source: struct Point{x,y:Int} + `fn area(p: Point) -> Int` via
destructure `x * y` + `fn main` with `Point { x: 6, y: 7 }` and
`print(area(p))`. Emitted struct/area exactly:
```rust
#[derive(Debug)]
struct Point {
    x: i64,
    y: i64,
}

fn area(p: Point) -> i64 {
    let Point { x, y } = p;
    x * y
}
```
Compiled and run: stdout `42`.

## 25. Test plan (tests/test_codegen.py — pytest)

`RUSTC = shutil.which('rustc') or '~/.cargo/bin/rustc' (expanded) if it
exists`; rustc-dependent tests use
`@pytest.mark.skipif(RUSTC is None, ...)`. Compile via
`[RUSTC, '--edition', '2021', file, '-o', out]` in tmp_path; assert
returncode 0 (warnings tolerated).

1. R1 full-output byte-exact; R2/R3/R4 item-exact (substring on the full
   output bounded by blank lines).
2. Oracle battery: sources S1, S4, S7, S8, S12, R3, R4 all transpile with
   no diagnostics AND rustc-compile cleanly.
3. Runtime: R1 executes with stdout `1\n`; R4 with stdout `42\n`.
4. `transpile` on each Part-III error golden (S2, S3, S5, S9, S10) →
   `(None, diags)` with the same codes analyze reports.
5. Empty source → synthesized `fn main() {}`; compiles.
6. Keyword escaping: a var named `impl` emits `r#impl`; compiles.
7. Shadow renaming: the §20 file-2 item-9 chain emits `v` and `v__2`, and
   the final drop is `drop(v__2);`; compiles.
8. Ref-form: read-mode non-copy user param call site emits `&arg`; inside
   the callee, forwarding that ref-bound param to another read position
   emits it bare; compiles.
9. `drop(expr);` for the S12 discarded temp (substring `drop(push(`).
10. Unit-tail vs value-tail drop placement (R1 covers Unit; R3 covers
    `__oxide_ret`).
11. CLI: valid file → Rust on stdout, exit 0; error file → stderr contains
    `error[OX`, exit 1; missing file → exit 2.
12. Never raises: transpile on Part-II garbage inputs and on every Part-III
    error golden returns `(None, [...])`.

Test author: import from `src.codegen.rust` (transpile, emit_rust) and
`src.sema.analyze` (analyze) only; subprocess for rustc/CLI; do NOT run the
tests (blind TDD).

---

# Part V — Phase 5a Contract (Language v0.2: enums/match, for, assignment)

Parts I–IV (as amended) remain binding except where this part explicitly
amends them. Motivation: make standard benchmark tasks expressible so the
AI-writability thesis becomes testable.

## 26. Surface amendments

**Lexer (amends §3.3):** three new keywords — `for` → KW_FOR, `in` → KW_IN,
`enum` → KW_ENUM. TERMINATOR_SET unchanged. (Phase 1 keyword tests must be
extended, not weakened.)

**Grammar (amends §6):**
```ebnf
item        := fn_decl | struct_decl | enum_decl
enum_decl   := "enum" IDENT "{" [variant ("," variant)* [","]] "}"
variant     := IDENT ["(" type ("," type)* ")"]

stmt        := let_stmt | assign_stmt | return_stmt | while_stmt
             | for_stmt | expr_stmt
assign_stmt := IDENT "=" expr TERM          # lookahead: IDENT EQ (not EQEQ)
for_stmt    := "for" IDENT "in" expr block

expr        := if_expr | match_expr | pratt_expr
match_expr  := "match" expr "{" [arm ("," arm)* [","]] "}"
arm         := arm_pat "=>" (expr | block)
arm_pat     := IDENT ["(" IDENT ("," IDENT)* ")"] | "_"
```
The §6 struct-literal condition restriction also applies to `match`
scrutinees and `for` iterables. `_` is a wildcard ONLY as a whole arm_pat;
everywhere else it stays an ordinary identifier. `for`/`while` statements
are both excluded from tail conversion. NEWLINEs are skipped inside enum
braces, match-arm braces (between arms), and variant parens.

## 27. AST + dump amendments (§7/§8)

New nodes (same conventions): `EnumDecl(name, variants: tuple[(str,
tuple[TypeExpr,...])])`, `Match(scrutinee, arms: tuple)`,
`MatchArm(pattern, body)` (body: Expr | Block),
`VariantPat(name: str | None, binders: tuple[str])` (name None = wildcard),
`For(var: str, iterable, body: Block)`, `Assign(name: str, value)`.

Dump productions:
```
EnumDecl   (enum NAME (variant VNAME TY*)*)
Match      (match SCRUT (arm PAT BODY)*)
VariantPat (vpat VNAME B1 ...)   |   (vpat _)
For        (for NAME ITER BLOCK)      # statement: wrapped in (exprstmt …)
Assign     (assign NAME EXPR)
```

## 28. Semantics

**Namespaces:** variant names live in the single top-level namespace
(collisions → OX0203) and must be globally unique. Builtin generic enums:
`Option<a>` (variants `Some(a)`, `None`) and `Result<a, b>` (`Ok(a)`,
`Err(b)`); their variant names are reserved (user redefinition → OX0203).
User enums are non-generic, always linear. A payload variant is used ONLY
as a callee (arity per payload, OX0303); a nullary variant is used ONLY as
a bare value. `type_str`: `Option<Int>`, `Result<Int, Str>`.

**Match typing:** scrutinee must solve to an enum type; every arm's variant
must belong to it; binder count = payload arity; arms must be exhaustive
(all variants or a `_` arm) with no duplicate/unreachable arms. ALL these
shape violations → **OX0307** (new code, infer phase). Arm bodies unify →
the match's type.

**Assignment:** target must be an existing local/param (OX0200 otherwise);
value unifies with the variable's type (OX0300). Linear semantics: the
previous value is consumed implicitly by the assignment (NO DropPoint) —
assigning to a var in Moved state is LEGAL re-initialization; after the
assignment the var is Owned. `acc = push(acc, 1)` is the accumulation
idiom (RHS move happens first, then re-own). A param that is ever assigned
gets mode `own`. In loops, an assignment before the back edge
re-establishes ownership, so such loops do not trigger OX0403.

**For:** iterable must solve to `Vec<T>`; the iterable expression is a READ
use. The loop variable is a FRESH OWNED CLONE of the element each
iteration (clone-on-iterate is deliberate v0.2 policy), scoped to the
body: unconsumed → block-end drop at the body; moving it is legal. Outer
vars moved in the body without reassignment still trigger OX0403. The
iterable var stays Owned after the loop (normal liveness placement, e.g.
after-stmt anchored at the for statement).

**Match linearity:** the scrutinee is a MOVE use. Arm binders are fresh
owned locals scoped to their arm; unconsumed binders → block-end drops
anchored at the arm body. Unbound payloads (wildcard/nullary arms over
payload variants) are consumed by the match itself (no DropPoints). Arms
are an N-way branch merge: the §18 if/else join rules generalize — a var
moved in ≥1 arm and dead after the match is dropped (block-end) in each
still-owned arm; if live after, the next use is OX0400.

**New builtins** (with modes; polymorphic like §14's):
`clone: forall a. fn(a) -> a` (read) · `get: forall a. fn(Vec<a>, Int) ->
Option<a>` (read, read) · `range: fn(Int, Int) -> Vec<Int>` (read, read) ·
`print_str: fn(Str) -> Unit` (read) · `str_len: fn(Str) -> Int` (read) ·
`concat: fn(Str, Str) -> Str` (own, own) · `chars: fn(Str) -> Vec<Str>`
(read) · `int_to_str: fn(Int) -> Str` (read) · `parse_int: fn(Str) ->
Option<Int>` (read).

## 29. Codegen amendments

**Derives (amends §22/§24):** structs AND enums emit
`#[derive(Debug, Clone)]` (plus `, PartialEq` under the existing
eq-reachability rule). R4's golden derive line becomes
`#[derive(Debug, Clone)]`.

**Prelude (REPLACES §23):** the §23 prelude keeps its existing four
functions verbatim and appends, in this order, each separated by one blank
line:
```rust
fn clone<T: Clone>(x: &T) -> T {
    x.clone()
}

fn get<T: Clone>(v: &Vec<T>, i: i64) -> Option<T> {
    if i < 0 {
        return None;
    }
    v.get(i as usize).cloned()
}

fn range(a: i64, b: i64) -> Vec<i64> {
    (a..b).collect()
}

fn print_str(s: &String) {
    println!("{}", s);
}

fn str_len(s: &String) -> i64 {
    s.chars().count() as i64
}

fn concat(a: String, b: String) -> String {
    a + &b
}

fn chars(s: &String) -> Vec<String> {
    s.chars().map(|c| c.to_string()).collect()
}

fn int_to_str(x: i64) -> String {
    x.to_string()
}

fn parse_int(s: &String) -> Option<i64> {
    s.trim().parse::<i64>().ok()
}
```
(R1's byte-exact golden is defined as header + prelude + main, so it
amends automatically; the emitted `fn main` part is unchanged. Phase 4
tests asserting the old prelude/derives must be updated, not weakened.)

**Emission:** enums → Rust enums, variants qualified `Shape::Circle(x)`;
`Option`/`Result` variants emit BARE (`Some`, `None`, `Ok`, `Err` — std
prelude). Match: expr-body arms `PAT => EXPR,`; block-body arms
`PAT => { … }`; wildcard `_ => …`. For: `ITER.iter().cloned()` where ITER
emits bare for Var and Call expressions, parenthesized otherwise;
`for x in …
{ … }` body as a normal block. Assignment: `name = expr;`; any var ever
assigned makes its binding `let mut name: T = …;` (assigned params:
`mut name: T`). String literals continue to emit `String::from("…")`.

## 30. Golden examples

**R5** — source:
```
enum Shape {
    Circle(Float),
    Rect(Float, Float),
    Empty,
}

fn describe(s: Shape) -> Float {
    match s {
        Circle(r) => r * r,
        Rect(w, h) => w * h,
        Empty => 0.0,
    }
}

fn main() {
    print(describe(Rect(3.0, 4.0)))
}
```
Emitted enum + describe exactly:
```rust
#[derive(Debug, Clone)]
enum Shape {
    Circle(f64),
    Rect(f64, f64),
    Empty,
}

fn describe(s: Shape) -> f64 {
    match s {
        Shape::Circle(r) => r * r,
        Shape::Rect(w, h) => w * h,
        Shape::Empty => 0.0,
    }
}
```
Compiled and run: stdout `12.0`.

**R6** — source:
```
fn sum_squares(n: Int) -> Int {
    let total = 0
    for i in range(0, n) {
        total = total + i * i
    }
    total
}

fn main() {
    print(sum_squares(5))
}
```
Emitted sum_squares exactly:
```rust
fn sum_squares(n: i64) -> i64 {
    let mut total: i64 = 0;
    for i in range(0, n).iter().cloned() {
        total = total + i * i;
    }
    total
}
```
Compiled and run: stdout `30`.

**R7** (runtime golden only) — source:
```
fn main() {
    let v = push(push(vec(), 10), 20)
    match get(v, 1) {
        Some(x) => print(x),
        None => print(-1),
    }
}
```
Compiled and run: stdout `20`. `analyze` reports zero diagnostics and one
after-stmt drop for `v`.

**Front-end goldens (analyze helpers):**
- **V1** R5's source → codes `[]`; `param_modes(describe)==('own',)`;
  `var_types_by_name(describe,'s')==['Shape']`, `(describe,'r')==['Float']`.
- **V2** R6's source → codes `[]`; `use_classes(sum_squares,'total')==
  ['read','move']` is NOT required (assignment internals unpinned) but
  codes MUST be `[]` and `param_modes(sum_squares)==('read',)`.
- **V3** `fn f(v: Vec<Int>) { for x in v { print(x) } }` → codes `[]`;
  `param_modes(f)==('read',)`; `drop_list==[]`.
- **V4** non-exhaustive: R5's enum with a match missing `Empty` and no
  wildcard → codes `['OX0307']`.
- **V5** `fn f(o: Option<Int>) -> Int { match o { Some(x) => x, None => 0 } }`
  → codes `[]`; `var_types_by_name(f,'o')==['Option<Int>']`.
- **V6** conditional arm move + later use:
  `fn f(c: Bool, v: Vec<Int>) { match c { … } }` is ill-typed (Bool is not
  an enum → OX0307); instead pin: an Option match where `Some`-arm moves an
  OUTER vec and the var is used after the match → codes `['OX0400']`.
- **V7** assignment re-init: `fn f() { let v = push(vec(), 1)\n let w = v\n
  v = push(vec(), 2)\n print(len(v))\n print(len(w)) }` → codes `[]`.

## 31. Test plan — two files

**tests/test_v02_front.py** (lexer/parser/sema/linear; import
src.sema.analyze helpers + src.parser for dumps):
1. New keywords lex as KW_FOR/KW_IN/KW_ENUM; `fnx`-style prefixes stay
   IDENT.
2. Parse dumps for: enum decl (incl. nullary + trailing comma), match with
   expr and block arms + wildcard, for stmt (wrapped in exprstmt, excluded
   from tail), assignment (and `x == y` still parses as comparison
   ExprStmt).
3. V1–V7 exactly.
4. Match shape matrix → OX0307: non-exhaustive; duplicate arm; arm from
   wrong enum; wrong binder arity; unreachable arm after `_`; match on Int.
5. Variant namespace: user variant colliding with a struct/fn name and
   with `Some` → OX0203; unknown variant in arm → OX0307; unknown bare
   variant value → OX0200; nullary variant called / payload variant used
   bare → OX0303.
6. Assignment: unknown target → OX0200; type mismatch → OX0300; assigned
   param → mode `own`; `acc = push(acc, 1)` in a while loop → codes `[]`
   (no OX0403); the SAME loop without the assignment → `['OX0403']`.
7. Option/Result: `parse_int`/`get` return types; `let x = None` alone →
   OX0302; Ok/Err both arms unify.
8. For: non-Vec iterable → OX0300; loop var is per-iteration (moving it in
   the body → codes `[]`); iterable Var stays usable after the loop.
9. New builtin modes all as pinned (`concat` own/own, rest read).
10. Never raises on all new-construct garbage (`enum`, `match x {`,
    `for x in`, `x =`).

**tests/test_v02_codegen.py** (import src.codegen.rust + subprocess rustc):
1. R5/R6 item-exact; R5/R6/R7 compile and run with pinned stdout.
2. Amended derives: R4's struct emits `#[derive(Debug, Clone)]`; eq-used
   struct emits `#[derive(Debug, Clone, PartialEq)]`.
3. Amended prelude present verbatim (spot: `fn get<`, `fn parse_int`).
4. `mut` inference: assigned var → `let mut`; unassigned → plain `let`;
   assigned param emits `mut n: i64`.
5. Wildcard arm emits `_ =>`; Option variants emit bare; user variants
   emit qualified.
6. rustc battery over: V3, V5, V7, an enum+match+for+assign combined
   program, `chars`/`concat`/`print_str` usage, match-as-value in a let.
7. Runtime: combined program produces its expected stdout.
8. transpile still returns (None, codes) for V4/V6 sources.

Test authors: blind TDD as always (implementations land concurrently; do
not run tests). The Phase 1/4 amendment agent (not the blind authors) owns
updating existing keyword/prelude/derive assertions.

---

# Part VI — Adopted Direction & Spec Debts (recorded 2026-08-07)

Decisions adopted from the design-review fork. Sections 32–33 are DIRECTION
and DEBT RECORDS, not yet normative contracts; each becomes normative only
when pinned as a numbered contract part.

## 32. Adopted decisions

1. **Three-way eval.** Alongside Black Oxide and Rust, a matched-novelty control
   language ("explicit Black Oxide"): same grammar/builtins/diagnostics, but
   ownership EXPLICIT — borrow/move annotations and visible drops the
   checker VERIFIES rather than infers (reusing the existing checker's
   computed moves/drops). Equal-sized language cards. Black Oxide vs explicit
   isolates the implicit-vs-explicit axis familiarity-free; Black Oxide vs Rust
   is the deployment measurement (Rust arm uses `rustc
   --error-format=json`). Measure learning curves (0/few/many-shot) and
   per-OX-code error distributions, plus tokens-to-green; persist repair
   loops as (broken program → diagnostic → verified fix) triples.
2. **v0.2.1 "eval-readiness" ergonomics** — to land as a pinned contract
   (Part VII) BEFORE the eval: (a) lift OX0405 — non-copy field access
   becomes an implicit clone (destructuring stays the move form); (b) a
   `?`-style propagation operator for Option/Result; (c) functional struct
   update `Point { x: 5, ..p }` (consumes p); (d) `to_float(Int) -> Float`
   and `trunc(Float) -> Int`; (e) `break`/`continue` (multi-exit loop
   merges in the linear checker — largest item, first); (f) allow a
   newline before `{` and before `else` where unambiguous.
3. **v0.3 direction — GATE RESOLVED 2026-08-07: REJECTED.** The proposal
   below was to invert the ownership default. The eval's OX-code
   distribution was collected (163 OX04xx of 464 diagnostics, 35.1%)
   and the inversion is **not adopted**: the supporting evidence that
   cloning is the correct repair proved to be an artifact of how the
   probe corpus was authored (reordering passes equally), value
   semantics would execute accumulation bugs as written rather than
   catching them, the isolated ownership benefit is only ~+10pp and
   0.0pp at frontier, and the largest measured win in the project came
   from ERGONOMICS (Part XI method syntax, +42pp) at no semantic cost.
   Full reasoning: docs/superpowers/specs/2026-08-07-v03-gate-decision.md.
   The original proposal is retained below for the record:
   ~~invert the
   ownership default — value semantics for plain data (implicit clone at
   would-be use-after-move), linearity OPT-IN per type (`resource struct`)
   with OX04xx machinery applied only there. The eval's OX-code error
   distribution confirms or kills this. Also v0.3-track: restricted
   closures, enum-scoped variants (leading-dot), module system with
   required signatures at module boundaries.
4. **Small-model track** (after v0.2.1 + harness): compiler-filtered data
   factory; token-MATCHED LoRA fine-tunes (Qwen-Coder-class ~1.5B/~7B) on
   Black Oxide vs Rust; eval pass@1, pass@N-with-`--check`-verifier,
   repair-iterations-to-green. Headline: small-model-on-Black-Oxide-with-verifier
   vs a much larger model on Rust. Fine-tuning is the entry ticket at
   small scale.
5. **Backend:** the Rust transpiler stays. Any future native compiler is a
   Cranelift/LLVM backend behind the existing front end, differentially
   tested against the transpiler as reference oracle — never a rewrite,
   and only after the eval validates the thesis AND §33 is frozen.

## 33. Spec debts (semantics currently inherited silently from Rust — MUST
be pinned before any custom-compiler work)

Integer overflow behavior · division-by-zero (currently
`#![allow(unconditional_panic)]`) · evaluation order · string encoding and
`chars()` semantics · temporary drop order.

---

# Part VII — Phase 5a.1 Contract (v0.2.1 eval-readiness ergonomics)

Normative. Amends Parts I–V where stated. v0.2 (Part V) is complete: 438
tests green including OX0406 (assignment to an iterated variable is an
error) and Unit-typed loop bodies (non-Unit while/for body tail → OX0300).

## 34. Surface

**Lexer:** new keywords `break` → KW_BREAK, `continue` → KW_CONTINUE (both
ADDED to TERMINATOR_SET, like KW_RETURN); new one-char token `?` →
QUESTION (no longer OX0001).

**Grammar:**
```ebnf
stmt         := … | break_stmt | continue_stmt
break_stmt   := "break" TERM
continue_stmt:= "continue" TERM
postfix      := call | field_access | "?"          # '?' at the postfix tier (lbp 13)
struct_lit   := IDENT "{" [field_init ("," field_init)*] ["," ".." expr] ["," ] "}"
             #  `..rest` must be LAST; `Point { ..p }` (no fields) is legal
```
`break`/`continue` outside a `while`/`for` body → **OX0105** (parser;
tracked via loop-depth, function boundaries reset it).

**Newline tolerance (amends §6/finding-era rulings):** a NEWLINE run is
skipped between a fn/if/while/for/match header and its `{`, and between
`}` and `else`. Everything else about NEWLINE handling is unchanged.

## 35. AST + dump

`Break()`, `Continue()` → `(break)` / `(continue)`. `Try(operand)` →
`(try E)`. `StructLit` gains field `rest: Expr | None` (dump:
`(structlit NAME (F1 E1) … (rest E)?)`).

## 36. Semantics

**break/continue:** CFG edges to the loop exit / the next-iteration point.
Loop-BODY-scoped vars still Owned at the jump are dropped there —
DropPoint kind **`before-jump`** (new, fifth kind), anchor_span = the
break/continue statement. For OUTER vars, break edges join the loop-exit
merge under the §18 rules (dead-after → hoisted drops on still-owned
edges; live-after → OX0400 at the next use); continue edges join the back
edge and interact with OX0403/OX0406 unchanged.

**Field access (SUPERSEDES OX0405, which is retired):** `s.f` is legal for
every field type. The base use stays `read`; a non-copy field value is an
IMPLICIT CLONE — a fresh owned value (consistent with clone-on-iterate).
Destructuring remains the consuming form.

**`?` (Try):** operand must solve to `Option<T>` with the enclosing fn
returning `Option<U>`, or `Result<T, E1>` with the fn returning
`Result<U, E2>` where E1 unifies with E2; result type T. Anything else →
**OX0308** (infer). The operand is a MOVE use. The implicit early-return
path's cleanup is delegated to Rust semantics (no DropPoints), like match
unbound payloads.

**Functional update:** `S { f: e1, ..rest }` — rest must solve to the same
struct type and is a MOVE use; listed fields must be a subset with no
duplicates (OX0304, but missing fields are of course allowed); result is a
full S.

**New builtins:** `to_float: fn(Int) -> Float` (read) · `trunc: fn(Float)
-> Int` (read).

**Goldens (analyze + runtime):**
- **W1** `fn first_big(v: Vec<Int>) -> Int { let found = -1\n for x in v { if x > 10 { found = x\n break } }\n found }`
  + main printing `first_big(push(push(push(vec(), 3), 42), 99))` →
  codes `[]`, `param_modes(first_big)==('read',)`, runtime stdout `42`.
- **W2** `fn second(v: Vec<Int>) -> Option<Int> { let x = get(v, 1)?\n Some(x + 1) }`
  + main matching `second(push(push(vec(), 5), 6))` → codes `[]`,
  runtime `7`.
- **W3** `struct Bag { items: Vec<Int> }` + main:
  `let b = Bag { items: push(vec(), 4) }\n let c = b.items\n print(len(c))\n print(len(b.items))`
  → codes `[]`, runtime `1` then `1` (b still usable: field access clones).
- **W4** `struct Point { x: Int, y: Int }` + main:
  `let p = Point { x: 1, y: 2 }\n let q = Point { x: 5, ..p }\n let Point { x, y } = q\n print(x + y)`
  → codes `[]`, runtime `7`.
- **W5** `fn sum_odds(n: Int) -> Int { let s = 0\n for i in range(0, n) { if i % 2 == 0 { continue }\n s = s + i }\n s }`
  + main printing `sum_odds(6)` → codes `[]`, runtime `9`.
- **W6** `fn f()\n{\n    1\n}` parses clean; if/else with `else` on its own
  line parses clean.
- **W7** main printing `trunc(to_float(7) / 2.0)` → runtime `3`.
- **Negatives:** top-level-in-fn `break` → `['OX0105']`; `let x = get(v, 0)?`
  in an Int-returning fn → `['OX0308']`; `1?` → `['OX0308']`; update with an
  unknown field → `['OX0304']`; `S1 { ..p }` where p: S2 → `['OX0300']`.

## 37. Codegen

`break;` / `continue;` with any before-jump drops emitted immediately
before them. Non-copy `s.f` → `s.f.clone()` (copy fields unchanged).
`expr?` → `expr?` verbatim. Functional update → identical Rust syntax.
Prelude appends (same style, in order):
```rust
fn to_float(x: i64) -> f64 {
    x as f64
}

fn trunc(x: f64) -> i64 {
    x as i64
}

fn to_str(x: i64) -> String {
    x.to_string()
}
```

## 38. Test plan

**tests/test_v021_front.py**: W1–W7 front-end halves + all negatives; dump
forms for break/continue/try/rest; `?` precedence (`get(v, 0)? + 1`
parses as `(bin + (try …) (lit int 1))`); break-in-while and
break-in-nested-loop scoping (inner loop only); before-jump drops appear
in `drop_list` for a loop-local vec live at a break; OX0403 still fires
when a continue skips the reassignment; OX0406 unchanged; newline
tolerance cases; never-raises garbage (`break`, `?`, `..`) inputs.

**tests/test_v021_codegen.py**: W1–W5, W7 compile via rustc and produce
the pinned stdout; W3 emits `.clone()` for the field access; `?` emits
verbatim; before-jump drop text appears before `break;`; prelude
additions verbatim; a combined break+continue+`?`+update program compiles
and runs.

Blind TDD as before. The amend agent (not blind authors) owns: lexer
keyword/token updates + test_lexer extensions, retiring OX0405 assertions
in test_linear/test_sema_types (those cases now assert clean accepts), and
any §20 item updates this part supersedes.

---

# Part VIII — Phase 5b Contract (AI interface + explicit Black Oxide control)

Normative. v0.2.1 (Part VII) is complete: 541 tests green.

## 39. CLI: `--json` and `--check`

`main.py` grammar: `python3 main.py [--json] [--check] <file.ox>`.
Behavior matrix (exit codes unchanged: 0 clean / 1 diagnostics / 2 usage-
or-unreadable):
- default: as today (Rust to stdout, rendered diagnostics to stderr).
- `--check`: run the pipeline WITHOUT emitting Rust; stdout empty in text
  mode; diagnostics/exit codes as usual.
- `--json` (with or without `--check`): stdout is EXACTLY one JSON object,
  nothing else (diagnostics never go to stderr in json mode):
```json
{"ok": true|false,
 "rust": "<emitted program>" | null,
 "diagnostics": [
   {"code": "OX0400", "message": "…", "line": 4, "col": 15,
    "end_line": 4, "end_col": 16,
    "notes": [{"line": 3, "col": 18}],
    "suggestion": "…"}]}
```
`rust` is null under `--check` or when diagnostics exist. line/col are
1-based from `SourceFile.line_col`; end_* derive from span.end. `ok` ⇔
diagnostics list empty. Implementation lives in `src/cli.py` (main.py
becomes a thin wrapper); JSON via `json.dumps(..., sort_keys=True)`.

## 40. Suggestion table (exact strings, keyed by code)

| Code | suggestion |
|---|---|
| OX0105 | `break/continue only work inside while/for loops.` |
| OX0200 | `Unknown name. Check spelling; variables must be defined by let or as parameters before use.` |
| OX0300 | `The two sides have incompatible types. Check operand/annotation types; Int and Float never mix implicitly (use to_float / trunc).` |
| OX0302 | `The type here is ambiguous. Add a use that pins it (e.g. push an element) or an annotation: let x: Vec<Int> = vec().` |
| OX0303 | `Not callable or wrong argument count. Check the function name and arity.` |
| OX0304 | `Struct shape mismatch: check field names, duplicates, and that destructuring names every field.` |
| OX0306 | `This value is not a struct, so it has no fields. Oxide has no user-defined methods: only builtins take receiver syntax like v.len(), and anything else must be called as a plain function, f(x).` |
| OX0307 | `This match must cover every variant of the enum. Add the missing arms or a final _ => arm.` |
| OX0308 | `? requires the function to return the same wrapper: Option-returning fns for Option values, Result-returning fns (matching error type) for Result values.` |
| OX0400 | `This value was moved at the noted location. Keep it available by cloning at the move site (clone(x)), or reorder so reads happen before the move.` |
| OX0401 | `This value was already consumed at the noted location. Clone at the first consuming use if both are needed.` |
| OX0403 | `This value is consumed by a previous loop iteration. Reassign it inside the loop (x = ...) before the iteration ends. If the value is read after the loop (see the later-use note), cloning inside the loop will not help — the original never grows.` |
| OX0406 | `The loop is iterating this vector; assigning to it inside the body is not allowed. Accumulate into a separate variable and reassign after the loop.` |
| other | `` (empty string) |

## 41. Explicit Black Oxide dialect (the matched-novelty control)

A dialect where the model must WRITE what core Black Oxide infers. New package
`src/explicit/` + CLI flag `--dialect=explicit` (composes with
--json/--check). Surface deltas (dialect-only):
- `&name` at use sites: a read-class use of a NON-COPY variable MUST be
  written `&name`; a move MUST be bare. (Copy-typed uses are always bare.)
- Read-mode non-copy params MUST be declared `name: &Type`; own-mode
  params bare `name: Type`. (`&` in types is dialect syntax, not a Rust
  reference — semantics identical to core.)
- `drop name` statement: REQUIRED exactly where core synthesizes a
  DropPoint for a named var — same variable, same anchor (after the
  anchor statement / at block end / in the still-owned arm / before the
  return or jump). `<temp>` drops and delegated cleanup (match payloads,
  `?` paths) need NO drop statement.
- Lexer: single `&` (AMP) and keyword `drop` exist only in the dialect.

Pipeline: dialect-parse → STRIP annotations to a core AST (recording
where they were) → run the UNCHANGED core analysis → diff written
annotations against analysis truth → dialect diagnostics; on success,
codegen runs on the stripped AST (byte-identical Rust to the core
program). Diagnostic codes (same JSON shape; suggestions pinned here):
- **EX0001** `&` on a consuming use — `This use consumes the value; remove the &.`
- **EX0002** bare read of a non-copy value — `This use only reads the value; write &name.`
- **EX0003** missing drop — `This value's last use is here; add 'drop name' at the required point.`
- **EX0004** wrong/extra drop — `No drop belongs here: the value is not owned/dead at this point. Remove or move this drop.`
- **EX0005** param mode mismatch — `Parameter mode is wrong: read-only parameters are declared name: &Type, consumed parameters name: Type.`

Golden **E1**: W1's program hand-annotated correctly (reads `&v`… per the
core analysis of W1) is accepted and emits byte-identical Rust to core W1.
E1 with one `&` added on the `found = x` value → EX0001; with the required
drop removed → EX0003; with `v: Vec<Int>` (own) instead of `&Vec<Int>` →
EX0005.

## 42. Language cards

- Update `LANGUAGE_CARD.md` for v0.2.1: field access legal (implicit
  clone), `?`, `break`/`continue`, `S { f: e, ..rest }`,
  `to_float`/`trunc`, and drop the OX0405 bullet. Keep it under 900 words.
- New `LANGUAGE_CARD_EXPLICIT.md`: same structure/length (±10% word
  count), teaching the dialect (the `&`/bare distinction, param modes,
  where `drop` statements go, EX codes).
- EVERY fenced code block in both cards must be mechanically validated:
  blocks marked complete programs analyze clean (and dialect-check clean
  for the explicit card); the cards may not contain uncheckable fragments
  (illustrative snippets must be full programs or omitted).

## 43. Test plan — three files

**tests/test_cli_json.py**: JSON schema exactness on a clean program
(ok/rust/diagnostics shapes, sorted keys), an OX0400 program (notes +
pinned suggestion string), `--check` (rust null, no stderr), exit codes,
json-mode never writes to stderr, every §40 code's suggestion string via
crafted error programs (parametrized), unknown-file exit 2 with json
error object `{"ok": false, "error": "..."}`.

**tests/test_explicit.py**: E1 goldens (accept + byte-identical Rust +
each single-mutation EX code exactly); a correctly-annotated program for
each drop kind (after-stmt, block-end, branch-end via absent else,
before-return, before-jump); copy vars always bare; `&` on copy use →
EX0001; dialect flag composes with --json (EX codes appear in the same
schema); never-raises on dialect garbage (`&`, `drop`, `&&x`, `drop 5`).

**tests/test_cards.py**: extract every fenced block from both cards;
complete programs (containing `fn main`) must transpile clean in their
dialect; non-main blocks are wrapped per a pinned harness rule (prepend
nothing; skip blocks marked ```text). Cards' word counts within ±10% of
each other.

Blind TDD as always; the card-update agent is not blind (mechanical
validation loop) but may not alter test files.

---

# Part IX — Phase 5c Contract (Evaluation harness + task corpus)

Normative. Phase 5b is complete: 616 tests green.

## 44. Task corpus — `eval/tasks.jsonl`

20 tasks, one JSON object per line:
`{"id": "t01", "title": "...", "prompt": "...", "expected_stdout": "...",
"difficulty": "intro"|"core"|"hard"}`.
Mix: 6 arithmetic/control-flow, 5 vector/accumulation, 3 strings, 4
enums/Option/Result-shaped, 2 structs. Prompts are LANGUAGE-NEUTRAL
(describe behavior + exact required stdout; never mention Black Oxide/Rust or
any syntax) and each requires a full program whose entry point prints the
results. expected_stdout is exact (trailing newline included). Three
pinned examples (the corpus agent designs the other 17 in the same
style):
- t01/intro: "Print the sum of the squares of the integers 0 through 9."
  expected `285\n`.
- t08/core: "A list contains 3, 8, -2, 12, 7. Print how many values are
  positive, then the largest value." expected `4\n12\n`.
- t15/hard: "Parse the strings \"12\", \"x\", \"30\" as integers; print
  the sum of those that parse, then the count that failed." expected
  `42\n1\n`.

Every task MUST be demonstrably solvable in all three arms: reference
solutions live at `eval/solutions/{oxide,explicit,rust}/<id>.{ox,rs}` and
are mechanically verified (compile + run + exact stdout) by the test
suite. Rust references: std only, no crates, `--edition 2021`.

## 45. Harness — `eval/harness.py` (importable module + CLI)

CLI subcommands (all support `--json`):
- `check --arm oxide|explicit|rust --file F` — structured diagnostics:
  oxide/explicit via the Part VIII pipeline; rust via `rustc --edition
  2021 --error-format=json --emit=metadata` adapted to the same shape
  (`code` = rustc code or "E????", message = rendered message INCLUDING
  rustc's help/children text verbatim, 1-based positions, suggestion "").
- `run --arm A --file F --task ID` — full verdict: compile (oxide arms:
  transpile then rustc; rust: rustc) then execute with `timeout 10`
  (nontermination = fail) and diff stdout against the task's
  expected_stdout → `{"compiled": bool, "passed": bool, "stdout": "...",
  "diagnostics": [...]}`.
- `prompt --arm A --task ID [--shots N]` — emits the complete solver
  prompt: the arm's language card (oxide/explicit) or the pinned Rust
  preamble (`You are writing Rust (edition 2021), std only, no external
  crates. Provide a complete program with fn main.`), the task prompt,
  the output contract (`Reply with ONLY the complete program source, no
  fences, no commentary.`), and, with `--shots N`, N solved examples
  from `eval/shots/<arm>/` (task+solution pairs disjoint from the
  corpus; 5 authored per arm).
- `report --results DIR` — aggregates: per-arm first-attempt compile
  rate, first-attempt pass rate, mean attempts-to-compile and -to-pass
  (failures count as cap+1), per-code diagnostic histogram, and totals.

Importable session API (the driver loop): `new_session(task_id, arm,
run_id)` → `session.submit(source) -> verdict` (max 4 submissions;
each attempt appended to `eval/results/<run_id>/triples.jsonl`:
`{"task", "arm", "attempt", "code", "diagnostics", "compiled",
"passed"}`) — this file is the verified-repair-triple dataset.

**Context-budget exhaustion is gated on EVIDENCE, not on which check
caught it** (§51): with at least one attempt already submitted this
session, an overflow — from either the client's own pre-request estimate
or the server's real tokenizer — is a session RESULT: it ends there with
attempts-so-far recorded and the cell marked `context_exhausted`, and the
grid proceeds to the next session, not a run abort. With ZERO attempts
submitted this session, it still aborts the run id exactly as before,
because there is nothing to lose by aborting a session that produced no
evidence — and at a small per-family window (§48) an oversized initial
prompt would otherwise repeat identically across every seed, fabricating
a whole grid of zero-attempt "results" with no abort and no manifest
cause.

Fairness pins: identical task text across arms; caps on attempts (4) and
exec time identical; the Rust arm receives rustc's own full diagnostic
text (its help output is part of the null hypothesis).

## 46. Test plan — `tests/test_eval.py`

1. Corpus well-formed: 20 unique ids, pinned difficulty mix, nonempty
   exact expected_stdout, t01/t08/t15 exactly as §44, prompts contain no
   occurrence of "oxide"/"rust" (case-insensitive).
2. ALL 60 reference solutions verified: `run` on each → compiled, passed
   (this is the three-arm solvability proof; rustc-heavy — keep timeouts).
3. `check` JSON shapes per arm incl. the rustc adapter on a known-bad
   Rust file (E0382 program) and a known-bad Black Oxide file.
4. Session API: cap enforced, triples.jsonl schema, verdict correctness
   on a scripted good/bad/good sequence.
5. `prompt`: contains card/preamble + task text + output contract;
   `--shots 2` includes exactly 2 examples; shots are disjoint from
   corpus ids.
6. `report`: correct aggregates on a synthetic results dir fixture.

The corpus/solutions agents are NOT blind (their work is mechanically
oracle-checked); the harness test author IS blind to the harness
implementation but may read this contract and the corpus schema.

# Part X — Phase 6a Contract (small-model capability ladder)

Normative. Phase 5c (Part IX) is complete: 774 tests green.

## 47. Pre-registered analysis plan

Recorded before any generation, because the thesis under test is the
author's own.

**Primary comparison.** Black Oxide vs explicit Black Oxide first-attempt pass
(pass@1) at each capability point, read as the **paired-by-task delta**
defined under *Statistics* below. These two arms are matched on novelty —
both are languages the subject saw zero times in pretraining, both taught
only by a card of comparable length — and differ only in whether
ownership is implicit or written out. This isolates the thesis claim.

**Secondary.** Repair lift (final pass rate − first-attempt pass rate)
per arm, which measures whether an arm's diagnostics teach; and mean
attempts-to-pass.

**Reference, not headline.** The Rust arm carries a large, unquantified
pretraining-exposure advantage at this scale. Rust numbers are reported
as a descriptive reference point with that advantage stated inline. Any
Black Oxide vs Rust difference at 0.5B/1.5B is **not** evidence about language
design and must not be reported as such.

**Statistics.** Tasks are a fixed corpus, not a sample; generalization
beyond the corpus is not claimed.

The primary statistic is the **paired-by-task** delta: for each task,
subtract explicit Black Oxide's pass rate (over 5 seeds) from Black Oxide's, then
average those 20 per-task differences.

**Precisely what pairing buys.** With every task present in both arms,
the paired mean difference is *algebraically identical* to the
difference of marginal arm rates. Pairing does **not** change the point
estimate. What it changes is the **interval**: the paired standard error
is `SD(per-task differences) / √20`, which shrinks in proportion to how
strongly the two arms' per-task performance correlates. That correlation
will be high — a task hard in Black Oxide is hard in explicit Black Oxide — so the
paired SE is expected to be roughly half the unpaired one. The delta is
therefore reported with its **paired SE**, and quoting the delta without
it is prohibited. (The point estimates diverge only when a task is
missing from one arm, which should not occur in a complete grid.)

Pooling all 100 task×seed trials into a single binomial CI is likewise
**prohibited** — it treats fixed tasks as random draws and understates
the interval. Reported alongside: per-task pass counts (so task-level
effects stay visible) and the across-seed SE (n=5) as a sampling-noise
check.

**Power — a pre-registered limit, not a finding.** With 20 tasks and 5
seeds, a per-seed pass rate moves in 5-point steps. At p≈0.5 (worst case
for variance) the per-seed SD is ≈11pp and the across-seed SE of the
mean is ≈5pp, so an *unpaired* comparison needs a ~10pp delta — two
whole tasks — to clear two SE. Pairing by task roughly halves that, to
~5pp. **This design cannot detect a true effect smaller than about 5
percentage points.** That is a property of a 20-task corpus, not
evidence of absence, and every report from this phase must say so.

**Directional predictions.** Stated in advance, on the paired-by-task
pass@1 delta (Black Oxide − explicit Black Oxide), as an exhaustive and
non-overlapping partition:

| Paired delta | Pre-registered reading |
|---|---|
| **≥ +5pp** | Consistent with the implicit-linearity ergonomics claim. Strengthened further if the delta widens monotonically as capability drops. |
| **−5pp … +5pp** | **No detectable difference.** Below this design's resolution; supports neither direction and must not be reported as either. |
| **≤ −5pp** | Disconfirming: implicit linearity *costs* accuracy at small scale. Part VI's ownership-default inversion should be revisited on that basis. |

Mixed signs across capability points (e.g. positive at 0.5B, negative at
7B) are reported as such and read as **no coherent directional effect**
— not as selective support from whichever rung agrees.

The ±5pp band is a floor imposed by 20 tasks, chosen from the power
calculation above rather than from taste. It is not a claim that 4pp
would be scientifically uninteresting. Resolving effects below it
requires a larger corpus; that is a Phase 6b decision, and the band must
not be renegotiated after seeing results.

## 48. Pinned run parameters

| Parameter | Value |
|---|---|
| Models | Phase 6a rungs: `qwen2.5-coder` **instruct**, 0.5B / 1.5B / 7B (slugs `qwen0_5b` / `qwen1_5b` / `qwen7b`). G0 adds two more **instruct** families at `q8_0`: `codegemma:7b` (slug `codegemma7b`) and `granite-code:8b` (slug `granite8b`). The capability-window probe adds a sixth **instruct** family, `deepseek-coder-v2:16b-lite` (slug `deepseek16b_lite`), at `q5_K_M` rather than `q8_0` — see the Quantization row. Six slugs total; `eval.driver.MODELS` is the authority on the pinned tags and is the roster this row must agree with. |
| Quantization | `q8_0`, **per-family** — see below (`q5_K_M` for `deepseek-coder-v2:16b-lite`) |
| Backend | Phase 6a: Ollama HTTP (`http://localhost:11434`), version recorded. G0 (`qwen7b`, `codegemma7b`, `granite8b`) runs on llama.cpp (`llama-server`, `http://localhost:8081`) for both the constrained and unconstrained conditions instead — Ollama accepts a GBNF `grammar` option and silently ignores it, so constrained decoding requires llama.cpp (§50.4/`eval.llamacpp`). `deepseek16b_lite` also runs on llama.cpp (its capability-window probe was unconstrained on every arm). Both backends remain available to every slug; preflight asserts the slug's pinned quantization either way (§49). |
| Temperature | 0.8 |
| top_p | 0.95 |
| `num_predict` (max gen tokens) | 2048 |
| `num_ctx` (context window) | 8192, **per-family** — see below (4096 for `granite-code:8b`) |
| Seeds | 1, 2, 3, 4, 5 |
| Shot conditions | 0 and 3 |
| Attempt cap | 4 (existing `MAX_ATTEMPTS`) |
| Exec timeout | 10s (existing) |

Base (non-instruct) variants are prohibited: they do not follow the
output contract, and the resulting failures would measure format
compliance rather than language competence.

Quantization is held constant **within each family** so the capability
curve is not confounded with precision inside a subject's own runs — see
below for the one family where the pinned value differs from the rest of
the ladder. Exact tags **and digests** are recorded in the run manifest
at preflight.

**The quantization pin is per-family, not universal — for the same reason
`num_ctx` is.** `deepseek-coder-v2:16b-lite` is served at `q5_K_M`
(`eval.driver.QUANT`; every other slug takes `DEFAULT_QUANT = q8_0`). MoE
activates 2.4B of ~16B parameters per token, but every expert must be
VRAM-resident, so the full weight set is what must fit — **and the weights
are not the whole bill.** Serving a model also costs a KV cache and
compute buffers, which this project measured rather than estimated: on
this **16303 MiB** card, with ~**1760 MiB** held by the desktop session
(~**14544 MiB** actually free), `llama-server` needed **13459 MiB** to
serve the **11302 MiB** `q5_K_M` weight set at `num_ctx` 8192 — about
**2160 MiB** of runtime overhead on top of the weights, leaving 1085 MiB
free.

Against that measured overhead, `q8_0` is out of reach: its **15926 MiB**
of weights already exceed the ~14544 MiB free, and **~18090 MiB** of
weights-plus-overhead exceeds the **entire** card. `q6_K` (**13418 MiB**)
fails the same way once overhead is counted (~15580 MiB against ~14544
MiB free). So `q5_K_M` is physically forced, not a policy choice —
exactly the shape of granite's 4096 window.

**Quote VRAM in MiB (or GiB) throughout, never in the registry's decimal
GB.** The two are not interchangeable and mixing them has already
produced a false justification here: the ollama registry's "16.70 GB"
`q8_0` figure is decimal (15926 MiB), while the "16.30 GB" it was
compared against was `llama-server`'s **16303 MiB** with the decimal
point moved (15.92 GiB). Read in consistent units those weights *fit* the
raw card by ~380 MiB, and the earlier "it does not fit the card even with
nothing else running" was arithmetic, not physics. The conclusion is
unchanged; only the reason is.

The pin is applied
uniformly across all three arms of that slug, so it stays arm-fair
*within* the model, and it is recorded per run and read as a per-family
covariate. Quantization is a capability reduction, so on a non-monotonic
capability curve its direction of bias depends on which side of the peak
the subject sits; the run's REPORT must state that rather than assume it.

Temperature is deliberately non-zero. At temperature 0 all five seeds
produce identical output and the variance estimate is vacuous.

`num_predict` is **load-bearing, not a nicety.** Degenerate repetition
loops are the most characteristic small-model failure mode. Without a
token cap, a looping 0.5B generation runs until the HTTP timeout and gets
classified as a *transport* error — so the run would abort on precisely
the behavior the phase exists to measure, and the grid would end up
systematically missing its worst-performing cells. With the cap, runaway
generation terminates as a **model** result: the truncated output fails
to compile and counts as a real failed attempt. 2048 tokens is generous
against reference solutions of 50–150 tokens. Truncation (`done_reason
== "length"`) is recorded per attempt so its frequency is auditable.

`num_ctx` is **load-bearing for the opposite reason**, and must be
pinned explicitly. Ollama does *not* default it to the model's
advertised capability: with `OLLAMA_CONTEXT_LENGTH` unset the daemon
serves qwen2.5-coder at **4096** tokens, not its 32768 maximum
(confirmed against `/api/ps` after load). Repair prompts carry each
arm's full initial context — language card, few-shot examples, task
statement (§50.3) — so the Black Oxide arms' carried context runs
~1400–1660 tokens against the Rust arm's ~110–300. A repair prompt also
carries the rejected program, so the binding quantity is
context + program + `num_predict`. Measured: for a rejected program
anywhere in the ~1.6k–7k character band — the realistic range for a
small model's failing output — the `oxide` and `explicit` arms exceed
4096 and `rust` does not. The carried card is exactly what pushes them
over. On overflow llama.cpp truncates the prompt **from the front**,
dropping that card — the only place Black Oxide syntax ever appears — from
those two arms specifically. That is a silent, non-random bias against
exactly the pair §47 names as the primary comparison, and nothing
in the artifacts would reveal it: wrong-but-plausible numbers, the worst
failure mode this project recognises.

8192 clears the true worst case this grid can produce — the largest
carried context (~1670 tok) wrapped around a `num_predict`-truncated
2048-token program, plus 2048 reserved for the fix, ≈5740 tokens — with
~30% headroom still free. It is deliberately **not** raised to 32768,
which would inflate the KV cache for no benefit and risk OOM on the 7B
rung, §51's memory-pressure case. The pinned value is recorded in
every manifest as `num_ctx`, kept lexically distinct from
`model_context_length` (the capability read off `/api/tags`) so the two
cannot be confused, and `OllamaClient.generate` **refuses** any prompt
whose estimated tokens plus `num_predict` exceed it — and separately
forbids the daemon from truncating one that slips past that estimate —
rather than letting the daemon truncate silently (§51).

**The window pin is per-family, not universal.** `num_ctx` is
`min(8192, that model's OWN advertised training context)`, applied
uniformly across all three arms of one slug (`eval.driver.NUM_CTX`) so
the pin stays arm-fair *within* a model. Every model in this ladder gets
8192 except `granite-code:8b`, whose training context is 4096 —
llama-server refuses (caps) a slot requesting a window larger than the
model was actually trained on, so 8192 is physically unsatisfiable for
that one model, not a policy choice, and rope-scaling past it to force
parity is explicitly rejected. The pin's PURPOSE from the two paragraphs
above — no front-truncation, the full card always carried — is preserved
per family at whatever window that family can actually serve. It is NOT
a claim that granite is otherwise on equal footing with the 8192-pinned
models: a smaller window is a real capability constraint, granite's
`num_ctx` is recorded in its own manifests exactly like any other slug,
and any cross-family comparison involving granite must state the window
difference as a covariate rather than pooling it silently with the
8192-pinned rows.

**Grid:** 3 models × 2 shot conditions × 5 seeds × 20 tasks × 3 arms =
**1800 sessions**, at most **7200 generations** (Phase 6a's own grid;
G0's grid is defined by its own design doc and uses the `qwen7b` /
`codegemma7b` / `granite8b` slugs named above). Estimated 8–14h wall
clock; small models exhaust the attempt cap more often than they pass
early, so the worst case is close to the expected case.

## 49. Run identity and layout

`harness._claim_session` locks on `(run_id, task_id, arm)` and the
pinned triple schema carries no model, seed, or shot field. Therefore
each (model, shots, seed) combination **must** occupy its own `run_id`.
This is what makes the phase additive: the existing session, triple, and
report layers work unchanged.

```
run_id  ::=  <prefix>-<model_slug>-<shots>shot-s<seed>
             e.g.  6a-qwen1_5b-0shot-s3, g0c-granite8b-0shot-s7
prefix     ::= 6a (default, `eval.driver.build_run_id`) | g0c (G0 constrained)
              | g0u (G0 unconstrained)
model_slug ::= qwen0_5b | qwen1_5b | qwen7b | codegemma7b | granite8b
```

30 run ids × 60 sessions each (Phase 6a's own grid; G0's grid is defined
by its own design doc and uses the `g0c`/`g0u` prefixes above over the
`qwen7b` / `codegemma7b` / `granite8b` slugs).

```
eval/results/<run_id>/
  manifest.json     # pinned params, backend, preflight payload (verbatim,
                     # minus its own grammar_sha256 -- see below), ollama
                     # version, model digests, per-arm grammar_sha256,
                     # start/end
  triples.jsonl     # written by the existing harness Session
  cells.jsonl       # appended per completed session (resume ledger)
  raw/<task>.<arm>.<attempt>.txt   # verbatim model output, pre-extraction
  .sessions/        # existing O_EXCL locks
eval/results/6a-rollup/
  grid.json         # all cells, all runs
  REPORT.md
```

`cells.jsonl` record:

```json
{"task": "t01", "arm": "oxide", "attempts": 2,
 "first_compiled": false, "first_passed": false, "final_passed": true,
 "attempts_to_pass": 2, "tokens_in": 1531, "tokens_out": 88, "ms": 4210,
 "contract_compliant": [false, true], "truncated": [false, false]}
```

`contract_compliant` and `truncated` are one boolean **per attempt**, in
attempt order; each length always equals `attempts`. `truncated` records
`done_reason == "length"` so runaway-generation frequency is auditable
per arm and per model. `tokens_in`/`tokens_out`/`ms` are summed across
the session's attempts.

An optional `context_exhausted: true` field is present when the session
ended on §45/§51's evidence-gated overflow rule — attempts-so-far
recorded, no further attempt made — rather than a normal pass or an
attempt-cap exhaustion; absent (not `false`) on every other session.

## 50. Module contracts

All additive, under `eval/`. No edits to `harness.py`.

### 50.1 `eval/models.py`

```python
class ModelClient(Protocol):
    def generate(self, prompt: str, *, seed: int) -> Generation: ...

@dataclass(frozen=True)
class Generation:
    text: str
    tokens_in: int
    tokens_out: int
    ms: int
    truncated: bool          # done_reason == "length"

class OllamaClient:
    def __init__(self, model: str, *, temperature: float, top_p: float,
                 num_predict: int = 2048, num_ctx: int = 8192,
                 host: str = "http://localhost:11434",
                 timeout_s: int = 120, retries: int = 3) -> None: ...
    def check_context(self, prompt: str) -> None: ...  # raises ContextOverflowError
    def preflight(self) -> dict: ...   # version + model digest; raises if absent
```

Protocol-first so a future API-backed client drops in without touching
the driver. Uses `urllib` from the stdlib — the eval venv stays
dependency-free (Python 3.14 has no clean PyTorch story, and none is
needed for inference through Ollama).

### 50.2 `eval/extract.py`

```python
@dataclass(frozen=True)
class Extraction:
    source: str
    contract_compliant: bool

def extract(raw: str) -> Extraction: ...
```

Pinned, arm-identical, deliberately **not** syntax-aware:

1. Normalize line endings to `\n`.
2. If the text contains a ``` fence, take the content of the **first**
   fenced block, dropping the fence lines and any language tag.
3. If that fence is never closed — the characteristic shape of a
   generation cut off at `num_predict` — take everything after the
   opener. Salvaging it is arm-neutral, and the truncated source then
   fails to compile on its own merits rather than being discarded.
4. Otherwise use the text with leading/trailing blank lines stripped.
5. `contract_compliant = (raw.strip() == source.strip())`. Note this
   makes empty output trivially "compliant"; it is a formatting metric
   only, and empty submissions still fail compilation as model failures.

No prose-stripping heuristics. Unfenced commentary simply fails to
compile, which is honest and arm-neutral; any smarter recovery risks
differentially favoring one arm's syntax. The raw output is always
persisted, so the strict-verbatim number stays recoverable post-hoc.

### 50.3 `eval/repair.py`

```python
def build_repair_prompt(
    arm: str,
    source: str,
    verdict: dict,
    *,
    task_id: str,
    shots: int = 0,
    tasks_path: str | Path | None = None,
) -> str: ...
```

A repair prompt is **the arm's own initial prompt with its tail
swapped**. It is built by calling `harness.build_prompt(arm, task_id,
shots=shots, tasks_path=tasks_path)`, stripping the trailing
`harness.OUTPUT_CONTRACT` constant, and appending the attempt block:

```
<the arm's full initial prompt, minus its output contract>

The program below was rejected. Fix it.

Program:
<source>

Diagnostics:
<rendered>

Reply with ONLY the complete corrected program source, no fences, no commentary.
```

The carried-over lead is therefore the language card (oxide /
explicit) or the pinned Rust preamble, plus any few-shot examples, plus
the task statement — exactly what the arm was given on attempt 1.
Reusing the frozen harness rather than reconstructing a lead here is
what makes the property structural: no arm can drift out of step with
its own initial prompt. Stripping a *known constant suffix* is
deterministic and testable, and its absence raises
`repair.RepairPromptError` — a change to the frozen harness must fail
loudly rather than silently emit a prompt carrying a stale contract.

*Why the lead is carried.* Every generation is a standalone HTTP call
with no conversation history, so whatever the repair prompt omits is
simply gone. The earlier template — program, diagnostics, and the fix
instruction only — retained this much of each arm's initial context:

| Arm | Initial | Repair | Retained |
|---|---|---|---|
| oxide (0-shot) | 5305 ch | 271 | **5.1%** |
| explicit (0-shot) | 5593 ch | 271 | **4.8%** |
| rust (0-shot) | 245 ch | 271 | **110.6%** |

Rust *gained* context on repair, because Rust lives in the model's
weights and its preamble is one line; the Black Oxide arms lost 95% of
theirs, and the language card is the only place Black Oxide syntax ever
appears. The task statement appeared in **no** repair prompt at all, so
on a runtime failure the model was told its output was wrong without
being told what it should have been — it could not repair except by
guessing. That would have made §47's repair-lift secondary metric
("whether an arm's diagnostics teach") measure card recall for the
Black Oxide arms instead of diagnostic quality. §47's primary pass@1 metric
is first-attempt-only and was never affected. The change was decided
before the grid ran, blind to any results, as §47 requires.

Diagnostics render as `line:col: CODE: message`, notes indented two
spaces, then `suggestion: <text>` when non-empty. Black Oxide arms therefore
supply OX codes with suggestions; the Rust arm supplies rustc's full
help text verbatim (SPEC §45 already folds rustc's children into
`message`). Giving each arm its strongest native diagnostics is the fair
form of the test. The attempt block's *structure* stays arm-identical;
its *content*, and the lead above it, stay arm-native.

**Runtime failure** (compiled, wrong stdout) has no diagnostics. The
`Diagnostics:` block is replaced by:

```
The program compiled and ran, but produced incorrect output.
Its output was:
<stdout>
```

The task's `expected_stdout` is **never** disclosed. Disclosing it would
let a weak model pass by hard-coding a print of the expected string,
which would silently corrupt the headline metric. It is not a parameter
of `build_repair_prompt`, and `harness.build_prompt` does not include it
either — the carried-over task statement says what the program must
produce without ever quoting the answer.

No transcript accumulation: a repair prompt carries the arm's fixed
initial context plus exactly one program and one verdict. Prior
attempts are never appended. Growing transcripts would confound repair
skill with long-context ability, which 0.5B lacks; a fixed-size prompt
does not.

### 50.4 `eval/driver.py`

Preflight (whole grid, before any generation): Ollama reachable, all
three tags present, `rustc` invocable, corpus loads, shots available for
every arm at 3-shot. Fail fast, listing everything missing.

Preflight reads `/api/tags` and records each model's `digest`,
`details.quantization_level`, and `details.context_length` into the
manifest. The last is recorded as **`model_context_length`** — the
model's *capability* — and is not to be confused with **`num_ctx`**, the
window the run actually used, which is recorded separately from the
client. Both appear in every manifest. It **asserts that
`quantization_level` matches the quantization pinned for that slug**
(`eval.driver.quant_for`, i.e. `QUANT[slug]` falling back to
`DEFAULT_QUANT = q8_0`), compared case-insensitively because Ollama
reports `Q5_K_M` where the pin reads `q5_K_M`. This is what actually
enforces §48's **per-family** quantization pin, rather than trusting
that the right tag was pulled. It is deliberately not a hard-coded
`Q8_0`: §48's control is constancy *within* a family, so a universal
literal would reject `deepseek16b_lite` — a subject §48 itself registers
— and would assert an invariant §48 no longer has. Every slug on the
q8_0 ladder still gets exactly the check it always got. (The
`qwen2.5-coder:1.5b` currently on this machine is Q4_K_M, is pinned at
`q8_0` like the rest of the ladder, and must therefore still be
rejected.)

Per run id: health-check Ollama (poll until healthy, cap 10 min) → write
`manifest.json` → 60 sessions → mark the run complete. On persistent
transport failure, record the cause in the manifest, abort this run id,
and continue with the next; three consecutive aborts stop the grid with
a non-zero exit (§51).

Per session: `harness.build_prompt(arm, task, shots)` → `generate` →
`extract` → `session.submit` → on failure `build_repair_prompt` →
generate → … up to the cap. Append raw output per attempt; append one
`cells.jsonl` record per completed session.

**Resume granularity is the whole `run_id`.** A run dir whose
`cells.jsonl` is short of 60 records is deleted and redone (~minutes).
Partial-state surgery across O_EXCL locks and half-written triples is
more bug-prone than the rerun costs.

CLI selects grid subsets so the 8–14h run can be split across sittings
and re-entered safely:

```
python -m eval.driver --models qwen1_5b,qwen7b --shots 0,3 --seeds 1-5
python -m eval.driver --preflight-only
```

Completed run ids are skipped on re-entry; the default is the full
grid.

### 50.5 `eval/rollup.py`

Aggregates the 30 run dirs into `grid.json` + `REPORT.md`.

**Primary readout:** the paired-by-task Black Oxide − explicit Black Oxide pass@1
delta per (model, shots), classified against the §47 partition
(≥+5pp / −5…+5pp / ≤−5pp) with the band printed alongside the number, so
an inconclusive result cannot be read as a positive one.

Also reported: pass@1 per (model, arm, shots) with across-seed SE, final
pass rate, repair lift, mean attempts-to-pass, per-code histograms
(**the v0.3 gate deliverable**), tokens and wall-clock per cell, prompt
token counts (the prompt-length asymmetry across arms), and
contract-compliance and truncation rates as their own metrics.

The rollup refuses to emit a report for an incomplete grid unless passed
`--partial`, which stamps the missing run ids into `REPORT.md`. A grid
silently missing aborted runs is the failure mode most likely to be
misread as a finished result.

## 51. Error handling and failure classification

The governing rule: **infrastructure failures must never be recorded as
model failures, and model failures must never be classified as
infrastructure.** The first biases every arm toward the null; the second
silently drops the worst-performing cells. Both corrupt the primary
comparison, in opposite directions.

| Condition | Classification | Behavior |
|---|---|---|
| Ollama down / tag missing at start | infrastructure | preflight abort, before any generation |
| Transport error or HTTP timeout | infrastructure | 3 retries with backoff, then **abort this `run_id`** (below) |
| Prompt + `num_predict` exceeds `num_ctx` (`ContextOverflowError`, from either the client's pre-request estimate or its `ServerContextOverflowError` subclass), with **ZERO** attempts submitted this session | infrastructure | **abort this `run_id`**, cause in its manifest — no evidence to lose |
| Same overflow, with **≥1** attempt already submitted this session | **model** | session ends; attempts-so-far recorded; cell marked `context_exhausted` (§45) |
| Generation hits `num_predict` | **model** | truncated source submitted; real failed attempt; `truncated: true` logged |
| Empty or malformed generation | **model** | real failure, consumes an attempt |
| Non-UTF8 source | **model** | existing `_unencodable_source_verdict` |
| Program nontermination | **model** | existing `timeout 10` |

**Prompt overflow is refused, never truncated.** `OllamaClient.generate`
estimates the prompt at ~4 chars/token and refuses when that estimate plus
`num_predict` exceeds `num_ctx`. The estimate is deliberately crude: it
exists to catch a 2x overrun, not to shave a token, and a real tokenizer
is a dependency the eval venv does not have. Classifying overflow as
infrastructure is the whole point — a silently front-truncated prompt
loses the language card from the `oxide` and `explicit` arms only, and
would be recorded as an ordinary model failure in exactly the two arms
the primary comparison rests on. Refusing before the request means the
retry loop never sees it (overflow is deterministic) and the
consecutive-abort backstop below still fires if it is systematic.

**Both backends must be *told* not to truncate, and only one of them
volunteers.** llama-server rejects an oversized prompt on its own with a
400 (`exceed_context_size_error`). Ollama's default is the opposite: it
accepts the prompt, silently discards the **front** of it, and returns a
normal 200. Reproduced on :11434 — a 3160-token prompt into a 256-token
window answered with `prompt_eval_count: 130`, the tail canary present
and the head canary gone. Under that default the crude estimate above is
the *only* guard on the Ollama path, and any prompt it under-counts
becomes a plausible answer built on a card-less prompt, recorded as an
ordinary model failure in exactly the two arms §47 rests on — the same
silent, non-random bias §48 pins `num_ctx` against, arriving through a
different door and leaving the same absence of evidence.

`OllamaClient.generate` therefore sends **`truncate: false`**, and it is
**top-level, not inside `options`**: nested there the daemon ignores it
and front-truncates anyway (also reproduced on :11434), so the flag would
read as present in the payload while doing nothing at all. With it set,
Ollama returns the same `exceed_context_size_error` 400 llama-server
does — wrapped one level deeper, as a JSON *string* under `error`, which
`eval.models._parse_http_error_body` unwraps. Classification then runs
through one shared `raise_if_context_overflow`, so an identical 400
cannot be classified two different ways depending on which daemon served
the run, and the guarantee above holds for the whole harness rather than
per backend.

**The evidence gate decides session-result vs. abort — not which check
raised the exception.** A repair prompt can grow across attempts and
overflow either check well after real evidence already exists: the
client's own estimate on a later attempt, or — since that estimate is
deliberately crude — the server's real tokenizer via
`ServerContextOverflowError`, a DISTINCT subclass `_call` raises so the
failure is never retried (it is deterministic once the server has
rejected it) and so manifests/logs can tell which check caught it.
`run_session` gates on `session.attempts`, not on exception type: with
**≥1** attempt already submitted, EITHER exception is a session result;
with **zero** attempts submitted, EITHER exception still aborts the run
exactly as above. Gating on exception type alone — an earlier iteration
of this rule — fabricated a full grid of zero-attempt "results" whenever
the CLIENT's own check fired on a repair prompt at a small per-family
window (§48): `granite-code:8b`'s native 4096 hit this in practice,
mid-session, on attempts that already had real evidence the type-based
rule discarded. Gating on evidence instead closes that gap while still
refusing to fabricate a result for a prompt that never had a chance to
produce one — e.g. a 3-shot condition whose card and shots alone exceed
`num_ctx` on attempt 1 is still a loud, manifest-recorded abort.

**Run-id-scoped abort.** A persistent transport failure aborts only the
current `run_id` — at most 60 sessions, ~20–30 min — records the cause
in that run's `manifest.json`, and the driver proceeds to the next run
id. Resume later redoes the aborted run dir whole. The grid degrades in
throughput instead of dying overnight, and no partial-state surgery is
needed.

Cells are **never** individually quarantined or excluded. Under memory
pressure (7B-q8 is ~8GB on a 16GB card) infrastructure failures would
correlate with long generations on hard tasks, so per-cell exclusion is
non-random and would bias pass rates upward. Whole-run redo preserves
the no-non-random-exclusion property.

**Health-check wait, between run ids only.** Before starting each
`run_id`, poll Ollama until healthy, capped at 10 minutes. This survives
transient restarts with zero lost work. It is deliberately **not**
applied mid-session: mid-session resumption would interact with the
O_EXCL locks and half-written triples that §50.4 exists to avoid.

**Consecutive-abort backstop.** Three consecutive `run_id` aborts stop
the whole grid with a non-zero exit. Without it, a systematically broken
configuration (7B OOM, a corrupt tag) would burn silently through every
remaining run id and leave a grid that looks complete but is not.

## 52. Test plan

New `tests/test_6a.py`, plus the existing suite staying green (nothing
in `harness.py` or `src/` is touched).

1. **Extraction** — fenced, fenced-with-language-tag, multiple fences
   (first wins), unfenced, empty, whitespace-only, CRLF; and
   `contract_compliant` correct in each.
2. **Repair prompt** — compile-failure shape; runtime-failure shape;
   the arm's full initial prompt (lead, shots, task statement) is
   carried and its output contract dropped; a moved harness tail raises;
   **asserts `expected_stdout` never appears in any repair prompt** —
   structurally (not a parameter of `build_repair_prompt` nor of
   `harness.build_prompt`) and empirically, over every real corpus task
   x arm x shot count, where neither the whole expected output nor any
   single line of it may appear as a line of the prompt; arm-identical
   attempt-block structure across all three arms; rustc help text
   preserved verbatim.
3. **Model client** — protocol conformance against a stub; retry-then-
   abort on transport error; preflight raises on a missing tag;
   `num_predict` passed through; `done_reason == "length"` surfaced as
   `truncated`; `truncate: false` sent **top-level** and asserted absent
   from `options` (where the daemon ignores it); an
   `exceed_context_size_error` 400 classified as
   `ServerContextOverflowError` without retry, and a non-overflow 400
   retried with the daemon's own message surfaced — both asserted on
   **each** backend against that backend's own error wrapping (§51).
4. **Failure classification** (§51's governing rule, both directions) —
   a generation truncated at `num_predict` is submitted as a **model**
   failure and does **not** abort; an HTTP timeout **does** abort the
   run id and is **never** written to `cells.jsonl` as a failed attempt.
5. **Driver** — stub-model end-to-end over a 2-task subset; attempt cap
   respected; resume deletes and redoes a short run dir; raw outputs
   persisted per attempt; run-id abort continues to the next run id;
   three consecutive aborts stop the grid non-zero; health-check waits
   then proceeds when Ollama returns.
6. **Rollup** — paired-by-task delta computed per §47 on synthetic run
   dirs; **paired SE** = `SD(per-task differences)/√n`, asserted smaller
   than the unpaired SE on a positively-correlated fixture (this, not a
   point-estimate difference, is what pairing actually buys); the two
   estimators asserted *equal* on a balanced fixture and *divergent*
   only when a task is missing from one arm; partition classification
   correct at the ±5pp boundaries; pooled-binomial CI absent; incomplete
   grid refused without `--partial`.
7. **Live smoke** — one task, 0.5B. Carries `@pytest.mark.live`, which
   `pytest.ini` deselects by default (`addopts = -m "not live"`), so a
   full-suite run never burns a real generation; run it with
   `pytest -m live`. It still skips cleanly when the daemon is down or
   the model is not pulled.

# Part XI — Builtin Method Syntax (v0.2.2)

## 53. Receiver-first calls to builtins

`recv.name(args)` parses as `name(recv, args)` when `name` is a builtin and
a `(` follows. This is **sugar only**: the parser emits an ordinary `Call`
node, so name resolution, use-context classification, linear checking, and
codegen see exactly what they would have seen for the prefix form. No
semantics change.

```
v.clone()          ==  clone(v)          # a read; v stays usable
v.push(x)          ==  push(v, x)        # consumes v
vec().push(1).push(2)  ==  push(push(vec(), 1), 2)
```

Restrictions:
- Only the names in `src.sema.types.BUILTINS`. `p.area()` is not a method
  call — Black Oxide has no user-defined methods and no callable fields, so it
  remains a field access followed by a call, i.e. an error.
- Only the call form. `p.clone` without parentheses stays a field access.
- The parser mirrors the builtin name set rather than importing sema (which
  would invert the layering); `tests/test_parser.py` asserts the two stay in
  sync.

**Why this exists.** Measured on the ownership probe (Part X): 82% of failing
Black Oxide repairs contained `.clone()` method syntax, producing `OX0304 field
access on non-struct type` — the single largest failure mode. The other Rust
idioms appeared **zero** times across 120 failures (`let mut`, `;`, `vec![]`,
indexing), so the language card successfully taught everything except this.
Receiver-first calls are near-universal across languages, not specific to
Rust; a prefix-only builtin surface fights that convention for no
expressiveness gain.

## 54. `mut` accepted and ignored on `let`

`let mut x = e` parses identically to `let x = e`. Black Oxide has no mutability
distinction — every binding is reassignable — so `mut` carries no meaning
and is discarded at the parser.

`mut` is a **contextual** keyword, not a reserved word: it is consumed only
when an identifier follows, so `let mut = 1` still binds a variable named
`mut`.

**Why this exists.** Models write `let mut x` reflexively. Under
grammar-constrained decoding this was worse than a plain error: GBNF cannot
reject a token, only steer generation to the nearest valid string, so
`let mut acc` was silently glued into `let mutacc` and every later use of
`acc` became `OX0200`. Measured across three model families, that single
artifact accounted for **44% of OX0200-carrying submissions** — the largest
cause of the largest remaining error class.

Note the general hazard this exposes, which is not specific to `mut`: a
constrained decoder never rejects, it *deforms*. Any token the grammar lacks
is absorbed into an adjacent one, producing a program that parses and means
something the model did not write. Error counts collected under grammar
constraint therefore include artifacts of the grammar's own gaps, and should
be read with that in mind.

## 55. `vec(...)` variadic list literal

`vec(a, b, c)` parses as `push(push(push(vec(), a), b), c)`. This is
**sugar only**: the parser emits ordinary `Call` nodes — one per synthesized
`push`, wrapping the original 0-arg `vec()` call — so name resolution, type
inference, linear checking, and codegen see exactly what they would have
seen for the hand-written push-chain. No semantics change, and the grammar
is unamended: calls were already generic (`IDENT "(" args ")"`), so
`vec(1, 2, 3)` was already admitted syntactically before this section —
only what the front end does with it is new.

```
vec(3, 8, -2)  ==  push(push(push(vec(), 3), 8), -2)
vec(x, x)      ==  push(push(vec(), x), x)  # double move of x -- OX0401,
                                            # same as the hand-written chain
```

Restrictions:
- `vec()` with zero arguments is **unchanged**: the desugar is a no-op, and
  the §16-pinned annotation-or-use ambiguity rule (`OX0302`) still governs
  it exactly as before this section.
- Only the plain-call spelling `vec(...)` triggers the desugar. This
  section's rewrite is a parse-time rewrite of that surface spelling, fired
  inside `_postfix`'s call-building branch, keyed on the parsed callee being
  a bare `Var("vec")`. The receiver-form `x.vec(...)` (§53; `vec` is in the
  parser's builtin-method name set, which is hand-maintained in parallel
  with `src.sema.types.BUILTINS` — `tests/test_parser.py` asserts the two
  stay in sync) builds its flat `vec(x, ...)` Call directly inside §53's
  own method-desugar path and is never re-entered by this section's
  rewrite — the two Part XI rewrites are parse-time rewrites of different
  surface spellings and deliberately do not compose. `x.vec(...)`
  therefore keeps its pre-§55 `OX0303` arity failure.
- Synthesized nodes carry the *original* `vec(...)` call's span (mirroring
  §53's precedent of never inventing a misleading span for a generated
  node): the intermediate `push` Calls and their `push` callee Vars have no
  real source token, so a diagnostic anywhere in the desugared chain still
  lands within the source call the model actually wrote. Argument
  expressions keep their own real spans untouched, and the innermost
  `vec()` call reuses the real, already-parsed `vec` token.

**Why this exists.** In the v0.3 generation-friction taxonomy (dossier 1),
`vec(...)` called with arguments is the dominant `OX0303` sub-class in ALL
THREE model families under constrained decoding — qwen 69/91, codegemma
59/69, granite 20/27 of first-attempt `OX0303` — with the call's arity
always matching the task's list length. The intent is unambiguous: models
treat `vec(...)` as a list literal, and the language's 0-arity constructor
was the friction, not the model. Each element push is a linear
consume-and-return exactly like a hand-written push-chain, so the sugar
composes with linearity for free — the same design-fit reasoning §53
already established for receiver-first calls.

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

**AST + dump amendment (§7/§8, §27, §35).** New node (same conventions):
`FieldAssign(base: str, path: tuple[str,...], value)`, with `path`
non-empty. It is a **distinct node, not a widened `Assign`**: §28 has
`Assign` emit a `ReInit` that re-establishes ownership, which the
Linearity rules below make exactly wrong for a field write, so a separate
node forces every match site to decide rather than silently inherit.

```
FieldAssign  (field-assign PATH EXPR)   # PATH = BASE "." F1 ["." F2 …]
```

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
third demonstrated instance, with a clean control.

**18 is a LOWER BOUND, not an exact count.** The signature is counted in
statement position only. Tail conversion is syntactic and unconditional,
and an un-braced match-arm body is a bare expression rather than a block,
so a deformed assignment falling last in either position is not an
`ExprStmt` and is never counted. The tail column is therefore ambiguous
in **both** directions — a tail `f.x == e` may be a legitimate `Bool`
return, or a deformation that happened to land last — so pooling the two
columns would overcount and the statement count alone undercounts. The
counting tool and its pinned definition are `eval/deformation.py`.

The demand is real but small (~1.5%), so no aggregate pass-rate change is
predicted from this section alone; see
`docs/superpowers/specs/2026-08-09-v03-g2-field-assignment-design.md`.

## 57. `to_str` — a second name for `int_to_str`

`to_str: fn(Int) -> Str` (read). Identical in signature, semantics, modes
and emitted Rust to `int_to_str` (§29), which is unchanged and remains the
card's spelling.

```
to_str(42)        ==  int_to_str(42)      // "42"
n.to_str()        ==  to_str(n)           // §53 receiver form, free
to_str(trunc(x))                          // Float goes through trunc
```

**`Int` only, not `Int|Float`.** The language has no type-based
overloading, so one name cannot accept both. `Float -> Str` composes as
`to_str(trunc(x))` and no observed call site asked for it directly.

**Codegen (amends §29's prelude).** One more prelude function, appended
after `trunc`:

```rust
fn to_str(x: i64) -> String {
    x.to_string()
}
```

The prelude is emitted whole and unconditionally, so no per-builtin
machinery changes. `BUILTIN_REF["to_str"] = (False,)` — it takes its
argument by value, exactly as `int_to_str` does.

**`to_str` is now a reserved top-level name.** Like every other entry in
`BUILTINS` (§16, `OX0203` "duplicate top-level name (incl. clash with a
builtin)"), a user program that writes `fn to_str(...)` is now an
`OX0203` error where before this section it was a legal user function.
This is a real, if small, behavioural cost of the alias and not a
side-effect worth leaving undocumented: the corpus evidence for adding
the name is *models defining it themselves*, so the population that
motivated the addition is exactly the population that now collides with
it. Measured on the closing-baseline corpus, `duplicate top-level name
'to_str'` fires in **1 of 600** constrained oxide first attempts (0 in
both pre-change corpora, `g0c` and `g1c`), and in 5 attempts across 2
sessions counting all four repair attempts.

**Why this exists.** In the v0.3 taxonomy (dossier 3) models were said to
"call conversions that don't exist". Measured over the 600 constrained
oxide first attempts of the G0 baseline, that is not what the corpus
shows: `int_to_str` and `parse_int` both already exist AND are both
already on the card. What models lack is the shorter *spelling*. Of the
three names reached for, only `to_str` is a genuine conversion demand.

**The three percentages below each use a different denominator**, stated
here because none of them is reconstructable from the figure alone.
`to_str` occurs **46 times across 9 programs**, split 21 plain calls
`to_str(x)` / 15 `fn to_str` definitions / 10 receiver-form
`x.to_str()`. The **85.7%** is `36/42`: the numerator is the 21 plain
calls **together with** the 15 definitions, and the denominator is all 46
occurrences less the 4 string-literal receivers. The definitions are
therefore *inside* that 85.7% and must not be added on top of it — the
share of `to_str` sites that are plain calls and nothing else is
`21/46` = **45.7%**. It is the definitions that carry the argument in any
case: the model writes `fn to_str` **itself 15 times across 6 programs**,
which is a language telling you it lacks a name its users want.

The other two names in that dossier were dropped on the same evidence and
are deliberately NOT added. `to_string` is **63.9%** string-literal
receiver (`"lit".to_string()`) — `46/72`, over receiver occurrences only,
with its 4 plain calls and 5 definitions outside the denominator
entirely — which is Rust's `&str -> String` and an identity function here
because `Str` is already owned. `to_int` is **70.6%** *numeric-literal*
receiver inside malformed `for` headers (`for i in 2.to_int().range(x)`)
— `36/51`, again over receiver occurrences only, and after dropping the
single degenerate 291-occurrence program that raw occurrence counting
would otherwise let dominate. (Two notes on that figure, recorded rather
than quietly reconciled. Its numerator was previously described as
*integer*-literal; the 36 is 32 integer-literal receivers **plus** 4
float-literal ones (`0.0.to_int()`, `1.0.to_int()`), so *numeric*-literal
is the label the arithmetic supports — strictly integer-literal would be
32. And re-derived from the committed corpus the denominator lands on
52, not 51: 343 receiver occurrences less the degenerate program's 291.
Neither point changes the conclusion; the one-site difference moves the
share to 69.2%.) Either way `to_int` is the deferred `2.to(n)` range
demand wearing a conversion's name, not parsing — and `parse_int` already
covers parsing.
