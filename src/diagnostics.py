"""Diagnostic reporting primitives: source spans and diagnostic records."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open byte-offset range within a source file: [start, end)."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single error/warning produced during lexing, parsing, or later phases."""

    code: str
    message: str
    span: Span
    notes: tuple[tuple[str, Span], ...] = ()
