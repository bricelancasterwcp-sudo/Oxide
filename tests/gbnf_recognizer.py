"""Recognizer for the generated GBNF subset. Test helper only.

Raises GbnfError on GBNF syntax this subset does not model, so a
grammar change that outgrows the recognizer fails loudly instead of
silently mis-judging admission.
"""

from __future__ import annotations


class GbnfError(ValueError):
    pass


# Node shapes: ("lit", str) ("cls", tuple[(lo, hi), ...]) ("ref", name)
# ("seq", [node]) ("alt", [node]) ("star", node) ("plus", node) ("opt", node)

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "[": "[", "]": "]", "-": "-"}


def _parse_literal(text: str, i: int) -> tuple[tuple, int]:
    out = []
    i += 1  # opening quote
    while text[i] != '"':
        if text[i] == "\\":
            ch = text[i + 1]
            if ch not in _ESCAPES:
                raise GbnfError(f"unknown escape \\{ch}")
            out.append(_ESCAPES[ch])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ("lit", "".join(out)), i + 1


def _parse_class(text: str, i: int) -> tuple[tuple, int]:
    i += 1  # opening bracket
    if text[i] == "^":
        raise GbnfError("negated classes not in the generated subset")
    ranges = []
    while text[i] != "]":
        if text[i] == "\\":
            ch = _ESCAPES.get(text[i + 1])
            if ch is None:
                raise GbnfError(f"unknown class escape \\{text[i + 1]}")
            i += 2
        else:
            ch = text[i]
            i += 1
        if text[i] == "-" and text[i + 1] != "]":
            hi = text[i + 1]
            if hi == "\\":
                hi = _ESCAPES[text[i + 2]]
                i += 3
            else:
                i += 2
            ranges.append((ch, hi))
        else:
            ranges.append((ch, ch))
    return ("cls", tuple(ranges)), i + 1


def _parse_alt(text: str, i: int) -> tuple[tuple, int]:
    seqs = []
    while True:
        seq, i = _parse_seq(text, i)
        seqs.append(seq)
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == "|":
            i += 1
            while text[i] == " ":
                i += 1
            continue
        break
    return ("alt", seqs) if len(seqs) > 1 else (seqs[0], i)[0], i


def _parse_seq(text: str, i: int) -> tuple[tuple, int]:
    items = []
    while i < len(text) and text[i] not in "|)":
        if text[i] == " ":
            i += 1
            continue
        if text[i] == '"':
            node, i = _parse_literal(text, i)
        elif text[i] == "[":
            node, i = _parse_class(text, i)
        elif text[i] == "(":
            node, i = _parse_alt(text, i + 1)
            if i >= len(text) or text[i] != ")":
                raise GbnfError("unclosed group")
            i += 1
        else:
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] in "-_"):
                j += 1
            if j == i:
                raise GbnfError(f"unexpected char {text[i]!r} at {i}")
            node, i = ("ref", text[i:j]), j
        if i < len(text) and text[i] in "*+?":
            node = ({"*": "star", "+": "plus", "?": "opt"}[text[i]], node)
            i += 1
        items.append(node)
    return ("seq", items) if len(items) != 1 else items[0], i


def load_gbnf(path) -> dict[str, tuple]:
    rules: dict[str, tuple] = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, sep, body = line.partition(" ::= ")
        if not sep:
            raise GbnfError(f"not a rule line: {line[:60]!r}")
        node, i = _parse_alt(body, 0)
        if i != len(body):
            raise GbnfError(f"trailing junk in rule {name!r}: {body[i:][:40]!r}")
        rules[name.strip()] = node
    return rules


def admits(rules: dict[str, tuple], start: str, text: str) -> bool:
    memo: dict[tuple, frozenset[int]] = {}
    in_progress: set[tuple] = set()

    def match(node: tuple, pos: int) -> frozenset[int]:
        kind = node[0]
        if kind == "lit":
            s = node[1]
            return frozenset([pos + len(s)]) if text.startswith(s, pos) else frozenset()
        if kind == "cls":
            if pos < len(text) and any(lo <= text[pos] <= hi for lo, hi in node[1]):
                return frozenset([pos + 1])
            return frozenset()
        if kind == "ref":
            key = (node[1], pos)
            if key in memo:
                return memo[key]
            if key in in_progress:  # left recursion: refuse, do not hang
                return frozenset()
            in_progress.add(key)
            result = match(rules[node[1]], pos)
            in_progress.discard(key)
            memo[key] = result
            return result
        if kind == "seq":
            ends = frozenset([pos])
            for item in node[1]:
                ends = frozenset(e for p in ends for e in match(item, p))
                if not ends:
                    return ends
            return ends
        if kind == "alt":
            return frozenset(e for alt in node[1] for e in match(alt, pos))
        if kind == "opt":
            return frozenset([pos]) | match(node[1], pos)
        if kind in ("star", "plus"):
            ends: set[int] = set() if kind == "plus" else {pos}
            frontier = {pos}
            while frontier:
                new = {e for p in frontier for e in match(node[1], p)}
                new -= ends
                ends |= new
                frontier = new
            return frozenset(ends)
        raise GbnfError(f"unknown node kind {kind!r}")

    return len(text) in match(rules[start], 0)
