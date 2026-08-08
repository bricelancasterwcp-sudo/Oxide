"""Grammar completeness: what the card teaches, the grammar must admit.

The soundness direction (grammar output parses) is enforced in
tests/test_6a.py. This file enforces the other direction for the
constructs models are TAUGHT: a card construct the grammar cannot emit
gets deformed by constrained decoding into something else (SPEC section
54 records the general hazard), invisibly.
"""

import re
from pathlib import Path

import pytest

from eval.probe import diagnose
from tests.gbnf_recognizer import admits, load_gbnf

REPO = Path(__file__).resolve().parent.parent
OXIDE_RULES = load_gbnf(REPO / "eval" / "grammar" / "oxide.gbnf")
EXPLICIT_RULES = load_gbnf(REPO / "eval" / "grammar" / "explicit.gbnf")


def _fenced_programs(card_path: Path) -> list[str]:
    text = card_path.read_text(encoding="utf-8")
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)
    return [b if b.endswith("\n") else b + "\n" for b in blocks if "fn main" in b]


def test_oxide_card_programs_are_admitted():
    programs = _fenced_programs(REPO / "LANGUAGE_CARD.md")
    assert programs, "card has no fn-main snippet; test would be vacuous"
    for program in programs:
        assert admits(OXIDE_RULES, "root", program), program


def test_explicit_card_programs_are_admitted():
    programs = _fenced_programs(REPO / "LANGUAGE_CARD_EXPLICIT.md")
    assert programs, "explicit card has no fn-main snippet; test would be vacuous"
    for program in programs:
        assert admits(EXPLICIT_RULES, "root", program), program


# One canonically-formatted exemplar per taught construct. Each must BOTH
# parse under the real pipeline (so the exemplar cannot rot) AND be
# admitted by the grammar (the completeness gate). Formatting is the
# grammar's canonical shape: 4-space indent, single spaces, no semicolons.
EXEMPLARS = [
    ("let-mut", 'fn main() {\n    let mut acc = 0\n    acc = acc + 1\n    print(acc)\n}\n'),
    ("for-vec", 'fn main() {\n    let v = push(vec(), 1)\n    for x in v {\n        print(x)\n    }\n}\n'),
    ("while-break-continue", 'fn main() {\n    let mut i = 0\n    while i < 9 {\n        i = i + 1\n        if i == 2 {\n            continue\n        }\n        if i > 4 {\n            break\n        }\n        print(i)\n    }\n}\n'),
    ("method-receiver", 'fn main() {\n    let v = push(vec(), 1)\n    print(v.len())\n}\n'),
    ("struct-and-field", 'struct Point {\n    x: Int,\n    y: Int,\n}\n\nfn main() {\n    let p = Point { x: 1, y: 2 }\n    print(p.x)\n}\n'),
    ("functional-update", 'struct Point {\n    x: Int,\n    y: Int,\n}\n\nfn main() {\n    let p = Point { x: 1, y: 2 }\n    let q = Point { x: 5, ..p }\n    print(q.y)\n}\n'),
    ("enum-match", 'enum Shape {\n    Dot,\n    Box(Int),\n}\n\nfn main() {\n    let s = Box(3)\n    match s {\n        Dot => print(0),\n        Box(n) => print(n),\n    }\n}\n'),
    ("fn-decl-and-call", 'fn double(n: Int) -> Int {\n    n * 2\n}\n\nfn main() {\n    print(double(21))\n}\n'),
    ("string-escape", 'fn main() {\n    print("a\\nb")\n}\n'),
    ("float-mix", 'fn main() {\n    let x = to_float(3)\n    print(trunc(x * 2.5))\n}\n'),
]


@pytest.mark.parametrize("name,program", EXEMPLARS, ids=[e[0] for e in EXEMPLARS])
def test_exemplar_parses_in_the_real_pipeline(name, program):
    codes = [d["code"] for d in diagnose("oxide", program)]
    syntax = [c for c in codes if c == "OX0001" or c.startswith("OX01")]
    assert not syntax, f"{name} is not valid Oxide at the parse layer: {syntax}"


@pytest.mark.parametrize("name,program", EXEMPLARS, ids=[e[0] for e in EXEMPLARS])
def test_exemplar_is_admitted_by_the_grammar(name, program):
    assert admits(OXIDE_RULES, "root", program)
