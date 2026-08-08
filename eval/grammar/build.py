"""Build the GBNF grammars that constrain decoding to parseable Oxide.

Why this file exists rather than a hand-written ``.gbnf``: the grammar has
two properties that are impossible to hold by hand and cheap to hold
mechanically.

1. **Keyword exclusion.** GBNF has no negative lookahead, so an identifier
   rule spelled ``[a-z][a-z0-9_]*`` can emit ``match`` or ``let``, which the
   lexer turns into a keyword and the parser rejects (OX01xx). The only
   sound spelling is the *complement* of the keyword set -- a trie of ~60
   rules that no one should maintain by hand.

2. **Bounded nesting.** ``src/parser/parser.py`` guards recursion with
   ``_MAX_DEPTH = 100`` and reports OX0101 "nesting too deep" past it, and
   nested ``while`` bodies recurse in Python without touching that guard at
   all. An unbounded recursive grammar can therefore emit strings that do
   not parse. Every descending construct here instead steps a *tier*
   counter, so the language is finite in nesting depth by construction.
   Each tier costs at most 7 parser frames (the full precedence cascade
   ``|| && == + * -`` is 6 frames, plus 1 to enter the nested expression),
   so ``TIERS`` tiers bound the parser at ~7*TIERS, well under 100.

The same rule IR drives two back ends: :func:`to_gbnf`, which writes the
file llama.cpp consumes, and :func:`sample`, which draws random strings
from the *same* definition so soundness can be tested offline against the
real front end instead of asserted.

Deliberately omitted, because an omitted construct costs expressiveness
while a loosely-approximated one costs soundness:

* Comments. Safe (they never update ``prev_kind``) but they invite a
  degenerate comment loop against the token cap and buy nothing.
* Non-decimal and separated numerals -- ``0x1F``, ``1_000``, ``2e3``.
* Multi-line expressions. Every expression is emitted on one line, so no
  NEWLINE token can ever land mid-expression. Legal continuations (an
  operator at end of line, a newline inside ``(...)``) are simply not
  generated.
* Struct literals, ``if``-as-value and ``match``-as-value anywhere except
  value positions (``let`` init, assignment RHS, ``return``, call args,
  struct field values, arm bodies, parenthesised subexpressions). Every one
  of those positions is a place the parser clears ``_no_struct_lit``, which
  is what makes the SPEC section 6 / section 26 condition restriction hold
  by construction instead of needing a duplicated restricted tier. The cost
  is that ``1 + if c { 1 } else { 2 }`` and ``P { x: 1 }.x`` cannot be said.
* ``else if`` chains longer than three branches at one nesting tier (deeper
  chains are still reachable by nesting inside the ``else`` block).
* ``break``/``continue`` inside a ``match`` arm that sits in a loop body --
  OX0105 is a parser diagnostic, and tracking loop depth through arm bodies
  as well as blocks was not worth the extra rule tier.
* Unannotated parameters; empty blocks; a ``main`` anywhere but last.

One caveat the grammar cannot cover: soundness is a claim about *complete*
derivations. A generation stopped at ``num_predict`` is a prefix of one,
and a prefix can end mid-production -- it shows up as "expected ')', found
EOF". Constrained decoding makes that *more* likely, not less, because
every continuation of a repetition stays legal, so a degenerate loop runs
to the cap rather than derailing into a stop token.

Regenerate with ``python -m eval.grammar.build``; ``tests/test_6a.py``
fails if the committed files drift from this module.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- IR

Node = "Lit | Ref | Seq | Alt | Rep | Chars"


@dataclass(frozen=True)
class Lit:
    """Literal text emitted verbatim."""

    text: str


@dataclass(frozen=True)
class Ref:
    """Reference to another rule by name."""

    name: str


@dataclass(frozen=True)
class Seq:
    """Concatenation."""

    items: tuple


@dataclass(frozen=True)
class Alt:
    """Alternation."""

    options: tuple


@dataclass(frozen=True)
class Rep:
    """Repetition. ``op`` is the GBNF quantifier; ``sample_max`` bounds the
    *sampler* only, so llama.cpp still sees an unbounded ``*``/``+``."""

    node: object
    op: str  # "?" | "*" | "+"
    sample_max: int

    @property
    def min_count(self) -> int:
        return 1 if self.op == "+" else 0


@dataclass(frozen=True)
class Chars:
    """A character class, as inclusive codepoint ranges."""

    ranges: tuple


def seq(*items: object) -> Seq:
    return Seq(tuple(items))


def alt(*options: object) -> Alt:
    return Alt(tuple(options))


def opt(node: object) -> Rep:
    return Rep(node, "?", 1)


def star(node: object, sample_max: int = 2) -> Rep:
    return Rep(node, "*", sample_max)


def plus(node: object, sample_max: int = 2) -> Rep:
    return Rep(node, "+", sample_max)


def chars(spec: str) -> Chars:
    """``chars("a-z0-9_")`` -> a Chars node. ``-`` is a range separator."""
    ranges: list[tuple[str, str]] = []
    i = 0
    while i < len(spec):
        if i + 2 < len(spec) and spec[i + 1] == "-":
            ranges.append((spec[i], spec[i + 2]))
            i += 3
        else:
            ranges.append((spec[i], spec[i]))
            i += 1
    return Chars(tuple(ranges))


def chars_from_set(allowed: set[str]) -> Chars:
    """Compress a character set into contiguous ranges."""
    ordered = sorted(allowed)
    ranges: list[tuple[str, str]] = []
    for ch in ordered:
        if ranges and ord(ch) == ord(ranges[-1][1]) + 1:
            ranges[-1] = (ranges[-1][0], ch)
        else:
            ranges.append((ch, ch))
    return Chars(tuple(ranges))


# ------------------------------------------------------------ GBNF back end

_LIT_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
_CLASS_ESCAPES = {
    "\\": "\\\\",
    "]": "\\]",
    "[": "\\[",
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "-": "\\x2d",
}


def _escape_literal(text: str) -> str:
    return "".join(_LIT_ESCAPES.get(ch, ch) for ch in text)


def _escape_class(ch: str) -> str:
    return _CLASS_ESCAPES.get(ch, ch)


def _gbnf(node: object, *, nested: bool) -> str:
    """Render one node. ``nested`` means "an alternation here needs parens"."""
    if isinstance(node, Lit):
        return f'"{_escape_literal(node.text)}"'
    if isinstance(node, Ref):
        return node.name
    if isinstance(node, Chars):
        body = "".join(
            _escape_class(lo) if lo == hi else f"{_escape_class(lo)}-{_escape_class(hi)}"
            for lo, hi in node.ranges
        )
        return f"[{body}]"
    if isinstance(node, Seq):
        # Empty literals exist only as zero-width indentation at level 0;
        # GBNF has no useful spelling for them, so drop them here.
        parts = [_gbnf(i, nested=True) for i in node.items if not (isinstance(i, Lit) and not i.text)]
        rendered = " ".join(parts)
        return f"({rendered})" if nested and len(parts) > 1 else rendered
    if isinstance(node, Alt):
        rendered = " | ".join(_gbnf(o, nested=False) for o in node.options)
        return f"({rendered})" if nested else rendered
    if isinstance(node, Rep):
        return f"{_gbnf(node.node, nested=True)}{node.op}"
    raise TypeError(f"unknown node {node!r}")


def to_gbnf(rules: list[tuple[str, object]], header: str) -> str:
    """Render an ordered rule list as a GBNF document."""
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]
    lines.append("")
    for name, body in rules:
        lines.append(f"{name} ::= {_gbnf(body, nested=False)}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------- sampler back end


class _Sampler:
    """Draws a random string from the rule set, biased to stay small once the
    budget is spent so a tiered-but-branching grammar cannot explode."""

    def __init__(self, rules: dict, rng: random.Random, budget: int) -> None:
        self.rules = rules
        self.rng = rng
        self.budget = budget
        self._min_len: dict[int, int] = {}

    def min_len(self, node: object) -> int:
        key = id(node)
        cached = self._min_len.get(key)
        if cached is not None:
            return cached
        if isinstance(node, Lit):
            value = len(node.text)
        elif isinstance(node, Chars):
            value = 1
        elif isinstance(node, Ref):
            value = self.min_len(self.rules[node.name])
        elif isinstance(node, Seq):
            value = sum(self.min_len(item) for item in node.items)
        elif isinstance(node, Alt):
            value = min(self.min_len(o) for o in node.options)
        elif isinstance(node, Rep):
            value = node.min_count * self.min_len(node.node)
        else:
            raise TypeError(f"unknown node {node!r}")
        self._min_len[key] = value
        return value

    def emit(self, node: object) -> str:
        if isinstance(node, Lit):
            self.budget -= len(node.text)
            return node.text
        if isinstance(node, Chars):
            self.budget -= 1
            lo, hi = self.rng.choice(node.ranges)
            return chr(self.rng.randint(ord(lo), ord(hi)))
        if isinstance(node, Ref):
            return self.emit(self.rules[node.name])
        if isinstance(node, Seq):
            return "".join(self.emit(item) for item in node.items)
        if isinstance(node, Alt):
            if self.budget <= 0:
                cheapest = min(self.min_len(o) for o in node.options)
                pool = [o for o in node.options if self.min_len(o) == cheapest]
            else:
                pool = list(node.options)
            return self.emit(self.rng.choice(pool))
        if isinstance(node, Rep):
            count = node.min_count
            if self.budget > 0:
                count = self.rng.randint(node.min_count, node.sample_max)
            return "".join(self.emit(node.node) for _ in range(count))
        raise TypeError(f"unknown node {node!r}")


def sample(
    rules: list[tuple[str, object]],
    rng: random.Random,
    *,
    budget: int = 900,
    root: str = "root",
) -> str:
    """Draw one random string from the grammar's language."""
    table = dict(rules)
    return _Sampler(table, rng, budget).emit(Ref(root))


# -------------------------------------------------------- identifier trie

_REST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
_LOWER_START = "abcdefghijklmnopqrstuvwxyz_"


def _ident_rules(base: str, keywords: frozenset[str]) -> list[tuple[str, object]]:
    """Rules for a lowercase-initial identifier that is never a keyword.

    GBNF cannot say "any word except these"; it can only say it structurally.
    Each rule is one trie node: at prefix ``p`` an identifier may stop (unless
    ``p`` is itself a keyword), step to a child that continues some keyword,
    or take any other continuation character and then run free.
    """
    rest = chars_from_set(set(_REST))
    rules: list[tuple[str, object]] = []

    def node_name(prefix: str) -> str:
        return f"{base}-t-{prefix}"

    def children(prefix: str) -> set[str]:
        return {k[len(prefix)] for k in keywords if k.startswith(prefix) and len(k) > len(prefix)}

    def build(prefix: str) -> None:
        kids = children(prefix)
        options: list[object] = []
        others = set(_REST) - kids
        if others:
            options.append(seq(chars_from_set(others), star(rest, 3)))
        for ch in sorted(kids):
            options.append(seq(Lit(ch), Ref(node_name(prefix + ch))))
            build(prefix + ch)
        body = options[0] if len(options) == 1 else Alt(tuple(options))
        rules.append((node_name(prefix), body if prefix in keywords else opt(body)))

    risky = sorted({k[0] for k in keywords})
    safe_starts = set(_LOWER_START) - set(risky)
    top: list[object] = [seq(chars_from_set(safe_starts), star(rest, 3))]
    for ch in risky:
        top.append(seq(Lit(ch), Ref(node_name(ch))))
        build(ch)
    rules.insert(0, (base, Alt(tuple(top))))
    return rules


# --------------------------------------------------------- the Oxide grammar

CORE_KEYWORDS = frozenset(
    {
        "fn", "let", "if", "else", "while", "return", "struct", "match",
        "true", "false", "for", "in", "enum", "break", "continue",
    }
)
EXPLICIT_KEYWORDS = CORE_KEYWORDS | {"drop"}

# Expression nesting tiers. 10 tiers x <=7 parser frames each leaves ~30
# frames of headroom under _MAX_DEPTH=100, and still admits a nine-deep
# `push(push(...))` chain -- the idiom every reference solution uses to
# build a literal vector.
TIERS = 10
# Deepest tier that may open a `{ ... }` block. Beyond it a tier carries
# expressions only, which is where the deep tiers are actually spent.
BLOCK_TIERS = 6


def _indent(level: int) -> str:
    return " " * (4 * level)


class _OxideGrammar:
    """Assembles the tiered rule list. ``explicit`` adds the SPEC section 41
    dialect surface: ``&name`` reads, ``name: &Type`` params, ``drop name``."""

    def __init__(self, *, explicit: bool) -> None:
        self.explicit = explicit
        self.keywords = EXPLICIT_KEYWORDS if explicit else CORE_KEYWORDS
        self.rules: list[tuple[str, object]] = []

    def add(self, name: str, body: object) -> None:
        self.rules.append((name, body))

    # -- leaves ----------------------------------------------------------
    def _leaves(self) -> None:
        self.add("uname", seq(chars("A-Z"), star(chars("A-Za-z0-9_"), 3)))
        self.add("int-lit", plus(chars("0-9"), 3))
        self.add("float-lit", seq(plus(chars("0-9"), 2), Lit("."), plus(chars("0-9"), 2)))
        # Every byte here is legal inside an Oxide string: the class spans
        # printable ASCII minus `"` and `\`, and the escapes are exactly the
        # ones SPEC section 3.5 accepts, so OX0005/OX0006 are unreachable.
        self.add(
            "str-char",
            alt(
                Chars(((" ", "!"), ("#", "["), ("]", "~"))),
                Lit("\\n"),
                Lit("\\t"),
                Lit('\\"'),
                Lit("\\\\"),
            ),
        )
        self.add("str-lit", seq(Lit('"'), star(Ref("str-char"), 6), Lit('"')))
        self.add("cmp-op", alt(*[Lit(f" {o} ") for o in ("==", "!=", "<", "<=", ">", ">=")]))
        self.add("add-op", alt(Lit(" + "), Lit(" - ")))
        self.add("mul-op", alt(Lit(" * "), Lit(" / "), Lit(" % ")))

    # -- types and patterns ------------------------------------------------
    def _types_and_patterns(self) -> None:
        # Generic arguments bottom out at three levels: `_type` recurses in
        # Python without a depth guard, so an unbounded rule could reach a
        # RecursionError rather than a diagnostic.
        self.add("type-3", Ref("uname"))
        self.add(
            "type-2",
            seq(
                Ref("uname"),
                opt(seq(Lit("<"), Ref("type-3"), star(seq(Lit(", "), Ref("type-3")), 1), Lit(">"))),
            ),
        )
        self.add(
            "type-1",
            seq(
                Ref("uname"),
                opt(seq(Lit("<"), Ref("type-2"), star(seq(Lit(", "), Ref("type-2")), 1), Lit(">"))),
            ),
        )
        self.add(
            "pattern",
            alt(
                Ref("lname"),
                seq(
                    Ref("uname"),
                    Lit(" { "),
                    Ref("lname"),
                    star(seq(Lit(", "), Ref("lname")), 2),
                    Lit(" }"),
                ),
            ),
        )
        # `_` is a wildcard only as a whole arm pattern (SPEC section 26).
        self.add(
            "arm-pat",
            alt(
                Lit("_"),
                seq(
                    Ref("uname"),
                    opt(
                        seq(
                            Lit("("),
                            Ref("lname"),
                            star(seq(Lit(", "), Ref("lname")), 2),
                            Lit(")"),
                        )
                    ),
                ),
            ),
        )

    # -- expression tiers -------------------------------------------------
    def _expr_tier(self, t: int) -> None:
        nxt = t + 1
        deepest = t == TIERS

        options: list[object] = [Ref(f"or-{t}")]
        if not deepest:
            options += [Ref(f"structlit-{t}"), Ref(f"ifval-{t}"), Ref(f"match-{t}")]
        self.add(f"value-{t}", Alt(tuple(options)))

        self.add(f"or-{t}", seq(Ref(f"and-{t}"), star(seq(Lit(" || "), Ref(f"and-{t}")), 1)))
        self.add(f"and-{t}", seq(Ref(f"cmp-{t}"), star(seq(Lit(" && "), Ref(f"cmp-{t}")), 1)))
        self.add(f"cmp-{t}", seq(Ref(f"sum-{t}"), opt(seq(Ref("cmp-op"), Ref(f"sum-{t}")))))
        self.add(f"sum-{t}", seq(Ref(f"prod-{t}"), star(seq(Ref("add-op"), Ref(f"prod-{t}")), 2)))
        self.add(f"prod-{t}", seq(Ref(f"un-{t}"), star(seq(Ref("mul-op"), Ref(f"un-{t}")), 1)))
        self.add(f"un-{t}", seq(opt(alt(Lit("-"), Lit("!"))), Ref(f"post-{t}")))
        self._expr_atoms(t)
        if not deepest:
            self._expr_composites(t)

    def _expr_atoms(self, t: int) -> None:
        """Postfix chain and atoms. At the deepest tier an atom cannot open a
        nested expression, which is what terminates the tier chain."""
        nxt = t + 1
        deepest = t == TIERS
        if self.explicit:
            # `&name` is a nud-position read marker; keeping it off the
            # postfix chain avoids emitting `&f(x)`, which parses as a call
            # on `&f` and means nothing in the dialect.
            self.add(f"post-{t}", alt(seq(Lit("&"), Ref("lname")), Ref(f"chain-{t}")))
            self.add(f"chain-{t}", seq(Ref(f"atom-{t}"), star(Ref(f"pfx-{t}"), 2)))
        else:
            self.add(f"post-{t}", seq(Ref(f"atom-{t}"), star(Ref(f"pfx-{t}"), 2)))

        call = (
            Lit("()")
            if deepest
            else seq(
                Lit("("),
                opt(seq(Ref(f"value-{nxt}"), star(seq(Lit(", "), Ref(f"value-{nxt}")), 2))),
                Lit(")"),
            )
        )
        self.add(f"pfx-{t}", alt(seq(Lit("."), Ref("lname")), call, Lit("?")))

        atoms: list[object] = [
            Ref("int-lit"),
            Ref("float-lit"),
            Ref("str-lit"),
            Lit("true"),
            Lit("false"),
            Ref("lname"),
            Ref("uname"),
        ]
        if not deepest:
            atoms.append(seq(Lit("("), Ref(f"value-{nxt}"), Lit(")")))
        self.add(f"atom-{t}", Alt(tuple(atoms)))

    def _expr_composites(self, t: int) -> None:
        """The three value-position forms: struct literal, `if` as a value,
        `match`. They exist only where the parser clears ``_no_struct_lit``,
        which is what keeps the condition restriction sound without a second
        restricted expression tier."""
        nxt = t + 1
        # Struct literal: single-line, so no NEWLINE token can be emitted
        # inside the braces at all (the lexer emits one only after a
        # terminator kind, and every line here ends at `{`, `,` or `}`).
        self.add(f"finit-{t}", seq(Ref("lname"), Lit(": "), Ref(f"value-{nxt}")))
        self.add(f"rest-{t}", seq(Lit(".."), Ref(f"or-{nxt}")))
        self.add(
            f"structlit-{t}",
            seq(
                Ref("uname"),
                Lit(" { "),
                alt(
                    seq(
                        Ref(f"finit-{t}"),
                        star(seq(Lit(", "), Ref(f"finit-{t}")), 2),
                        opt(seq(Lit(", "), Ref(f"rest-{t}"))),
                    ),
                    Ref(f"rest-{t}"),
                ),
                Lit(" }"),
            ),
        )
        # An `if` in value position stays on one line; `{ e }` is a block
        # whose tail is `e` (SPEC section 6 tail rule).
        self.add(
            f"ifval-{t}",
            seq(
                Lit("if "),
                Ref(f"or-{nxt}"),
                Lit(" { "),
                Ref(f"value-{nxt}"),
                Lit(" } else { "),
                Ref(f"value-{nxt}"),
                Lit(" }"),
            ),
        )
        self._match_rules(t)

    def _match_rules(self, t: int) -> None:
        nxt = t + 1
        arm_bodies: list[object] = [Ref(f"value-{nxt}")]
        if t + 2 <= BLOCK_TIERS + 2:
            arm_bodies.append(Ref(f"block-{t + 2}"))
        self.add(
            f"arm-{t}",
            seq(
                Lit(_indent(t + 1)),
                Ref("arm-pat"),
                Lit(" => "),
                Alt(tuple(arm_bodies)) if len(arm_bodies) > 1 else arm_bodies[0],
                Lit(",\n"),
            ),
        )
        # NEWLINEs are skipped inside match braces (SPEC section 26), and
        # every arm ends in `,` -- which is not a terminator kind, so the
        # newline after it produces no token at all.
        self.add(
            f"match-{t}",
            seq(
                Lit("match "),
                Ref(f"or-{nxt}"),
                Lit(" {\n"),
                plus(Ref(f"arm-{t}"), 3),
                Lit(_indent(t)),
                Lit("}"),
            ),
        )

    # -- statement tiers --------------------------------------------------
    def _flat_stmts(self, t: int) -> None:
        """Statements that terminate on their own line. Every one of them ends
        in a token the lexer counts as a terminator, so the trailing newline
        always becomes the NEWLINE that TERM requires."""
        nxt = t + 1
        opens_blocks = t <= BLOCK_TIERS
        flat: list[object] = [
            seq(
                # `let mut x` is admitted because SPEC 54 accepts and ignores
                # `mut`. Before that, GBNF could not reject the token models
                # reflexively emit -- it steered to the nearest valid string,
                # gluing `let mut acc` into `let mutacc` and turning every
                # later use of `acc` into OX0200. That artifact was 44% of
                # OX0200-carrying submissions across three families.
                Lit("let "),
                opt(Lit("mut ")),
                Ref("pattern"),
                opt(seq(Lit(": "), Ref("type-1"))),
                Lit(" = "),
                Ref(f"value-{t}"),
            ),
            seq(Ref("lname"), Lit(" = "), Ref(f"value-{t}")),
            Lit("return"),
            seq(Lit("return "), Ref(f"value-{t}")),
            Ref(f"or-{t}"),
        ]
        if t < TIERS:
            flat.append(Ref(f"match-{t}"))
        if self.explicit:
            flat.append(seq(Lit("drop "), Ref("lname")))
        if opens_blocks:
            flat += [
                seq(Lit("while "), Ref(f"or-{nxt}"), Lit(" "), Ref(f"loop-block-{nxt}")),
                seq(
                    Lit("for "),
                    Ref("lname"),
                    Lit(" in "),
                    Ref(f"or-{nxt}"),
                    Lit(" "),
                    Ref(f"loop-block-{nxt}"),
                ),
            ]
        self.add(f"flat-{t}", Alt(tuple(flat)))

    def _stmt_tier(self, t: int) -> None:
        opens_blocks = t <= BLOCK_TIERS
        self._flat_stmts(t)
        if opens_blocks:
            self._if_chain(t, "if", "block")
            self._if_chain(t, "loop-if", "loop-block")

        body: list[object] = [Ref(f"flat-{t}")]
        if opens_blocks:
            body.append(Ref(f"if-{t}-0"))
        self.add(f"stmt-{t}", seq(Lit(_indent(t)), Alt(tuple(body)), Lit("\n")))

        loop_body: list[object] = [Ref(f"flat-{t}"), Lit("break"), Lit("continue")]
        if opens_blocks:
            loop_body.append(Ref(f"loop-if-{t}-0"))
        self.add(f"loop-stmt-{t}", seq(Lit(_indent(t)), Alt(tuple(loop_body)), Lit("\n")))

        self.add(f"block-{t}", seq(Lit("{\n"), plus(Ref(f"stmt-{t}"), 3), Lit(_indent(t - 1)), Lit("}")))
        self.add(
            f"loop-block-{t}",
            seq(Lit("{\n"), plus(Ref(f"loop-stmt-{t}"), 3), Lit(_indent(t - 1)), Lit("}")),
        )

    def _if_chain(self, t: int, prefix: str, block: str) -> None:
        """`if / else if / else if / else`, unrolled twice at the same tier.

        Unrolled rather than recursive because each `else if` link costs two
        more parser frames; a bounded chain keeps the per-tier cost bounded.
        """
        for link in (0, 1, 2):
            tail: object
            if link == 2:
                tail = opt(seq(Lit(" else "), Ref(f"{block}-{t + 1}")))
            else:
                tail = opt(
                    seq(
                        Lit(" else "),
                        alt(Ref(f"{block}-{t + 1}"), Ref(f"{prefix}-{t}-{link + 1}")),
                    )
                )
            self.add(
                f"{prefix}-{t}-{link}",
                seq(Lit("if "), Ref(f"or-{t + 1}"), Lit(" "), Ref(f"{block}-{t + 1}"), tail),
            )

    # -- items ------------------------------------------------------------
    def _items(self) -> None:
        amp = opt(Lit("&")) if self.explicit else None
        param_ty: object = seq(amp, Ref("type-1")) if amp is not None else Ref("type-1")
        self.add("param", seq(Ref("lname"), Lit(": "), param_ty))
        self.add(
            "fn-decl",
            seq(
                Lit("fn "),
                Ref("lname"),
                Lit("("),
                opt(seq(Ref("param"), star(seq(Lit(", "), Ref("param")), 2))),
                Lit(")"),
                opt(seq(Lit(" -> "), Ref("type-1"))),
                Lit(" "),
                Ref("block-1"),
                Lit("\n"),
            ),
        )
        self.add("field-line", seq(Lit("\n    "), Ref("lname"), Lit(": "), Ref("type-1"), Lit(",")))
        self.add(
            "struct-decl",
            seq(Lit("struct "), Ref("uname"), Lit(" {"), plus(Ref("field-line"), 3), Lit("\n}\n")),
        )
        self.add(
            "variant-line",
            seq(
                Lit("\n    "),
                Ref("uname"),
                opt(seq(Lit("("), Ref("type-1"), star(seq(Lit(", "), Ref("type-1")), 1), Lit(")"))),
                Lit(","),
            ),
        )
        self.add(
            "enum-decl",
            seq(Lit("enum "), Ref("uname"), Lit(" {"), plus(Ref("variant-line"), 3), Lit("\n}\n")),
        )
        self.add(
            "item",
            seq(alt(Ref("struct-decl"), Ref("enum-decl"), Ref("fn-decl")), Lit("\n")),
        )
        # `main` is mandatory and last: rustc needs it, and pinning its
        # position is what makes "the grammar emits a whole program" true
        # rather than hopeful.
        self.add("root", seq(star(Ref("item"), 2), Lit("fn main() "), Ref("block-1"), Lit("\n")))

    def build(self) -> list[tuple[str, object]]:
        self._items()
        self._leaves()
        self._types_and_patterns()
        for t in range(1, TIERS + 1):
            self._expr_tier(t)
        for t in range(1, BLOCK_TIERS + 3):
            self._stmt_tier(t)
        self.rules.extend(_ident_rules("lname", self.keywords))
        return self.rules


_HEADER = """GENERATED by eval/grammar/build.py -- do not edit by hand.

Grammar-constrained decoding for the {dialect} arm (SPEC.md sections 3, 6,
26, 34{extra}). SOUND, not complete: every string this grammar can generate
lexes and parses without an OX0001 or OX01xx diagnostic, so a constrained
model can only fail semantically. It deliberately does not accept every
valid Oxide program -- see the module docstring for what was left out.
"""


def build_grammar(*, explicit: bool = False) -> list[tuple[str, object]]:
    return _OxideGrammar(explicit=explicit).build()


def render(*, explicit: bool = False) -> str:
    header = _HEADER.format(
        dialect="explicit-Oxide" if explicit else "core Oxide",
        extra=", 41" if explicit else "",
    )
    return to_gbnf(build_grammar(explicit=explicit), header)


GRAMMAR_DIR = Path(__file__).resolve().parent
OXIDE_PATH = GRAMMAR_DIR / "oxide.gbnf"
EXPLICIT_PATH = GRAMMAR_DIR / "explicit.gbnf"


def main() -> None:
    OXIDE_PATH.write_text(render(explicit=False), encoding="utf-8")
    EXPLICIT_PATH.write_text(render(explicit=True), encoding="utf-8")
    print(f"wrote {OXIDE_PATH}")
    print(f"wrote {EXPLICIT_PATH}")


if __name__ == "__main__":
    main()
