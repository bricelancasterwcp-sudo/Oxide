"""Source file representation and byte-offset to line/column mapping."""

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    """An in-memory source file plus a precomputed table of line start offsets."""

    text: str
    line_starts: tuple[int, ...]

    @staticmethod
    def from_text(text: str) -> "SourceFile":
        """Build a SourceFile, scanning once to record the start offset of each line."""
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        return SourceFile(text=text, line_starts=tuple(starts))

    def line_col(self, offset: int) -> tuple[int, int]:
        """Map a byte offset to a 1-based (line, column) pair via binary search."""
        line_index = bisect_right(self.line_starts, offset) - 1
        line = line_index + 1
        col = offset - self.line_starts[line_index] + 1
        return line, col
