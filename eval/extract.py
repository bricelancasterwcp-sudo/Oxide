"""Model output -> candidate source (SPEC Part X, section 6.2).

Deliberately not syntax-aware: any smarter recovery risks differentially
favouring one arm's syntax, which would bias the primary comparison. Raw
output is persisted by the driver, so a strict-verbatim number stays
recoverable after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

FENCE = "```"


@dataclass(frozen=True)
class Extraction:
    """A candidate program plus whether the model obeyed the contract."""

    source: str
    contract_compliant: bool


def _first_fenced_block(lines: list[str]) -> str | None:
    """Content of the first ``` block, or None when there is no fence."""
    opener: int | None = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(FENCE):
            opener = index
            break
    if opener is None:
        return None
    for index in range(opener + 1, len(lines)):
        if lines[index].lstrip().startswith(FENCE):
            return "\n".join(lines[opener + 1 : index])
    # Unterminated fence: a generation cut off at num_predict. Salvaging
    # it is arm-neutral; the truncated source then fails to compile on
    # its own merits instead of being silently discarded.
    return "\n".join(lines[opener + 1 :])


def extract(raw: str) -> Extraction:
    """Apply the pinned, arm-identical extraction rule to model output."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    block = _first_fenced_block(text.split("\n"))
    source = text.strip("\n") if block is None else block
    return Extraction(
        source=source,
        contract_compliant=raw.strip() == source.strip(),
    )
