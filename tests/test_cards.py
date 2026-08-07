"""Blind card-validation tests for Phase 5b (SPEC.md sections 42-43, third file).

Every fenced code block in LANGUAGE_CARD.md and LANGUAGE_CARD_EXPLICIT.md
must validate clean in its card's dialect: core-card blocks through
``src.codegen.rust.transpile``, explicit-card blocks through
``src.explicit.pipeline.run``. The pinned harness rule wraps non-main
blocks by prepending NOTHING (blocks are validated verbatim; codegen's
synthesized ``fn main() {}`` covers non-main modules), and blocks fenced
as ```text are exempt. The two cards' word counts must be within 10% of
each other (SPEC section 42 baselines the explicit card against the core
card), and the core card stays under 900 words.

Blind TDD: written against SPEC.md only; not executed by its author.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.codegen.rust import transpile

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_CARD = REPO_ROOT / "LANGUAGE_CARD.md"
EXPLICIT_CARD = REPO_ROOT / "LANGUAGE_CARD_EXPLICIT.md"

CORE_WORD_LIMIT = 900
WORD_COUNT_TOLERANCE = 0.10


# ---------------------------------------------------------------- harness

def extract_blocks(markdown: str) -> list[tuple[str, str]]:
    """Return (info_string, content) for every fenced block, in order.

    A fence is a line whose stripped form starts with three backticks;
    the opener's remainder is the info string. Content lines are kept
    verbatim (no dedent, nothing prepended) and joined with a trailing
    newline.
    """
    blocks: list[tuple[str, str]] = []
    in_block = False
    info = ""
    content: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_block:
                blocks.append((info, "\n".join(content) + "\n"))
                in_block = False
            else:
                in_block = True
                info = stripped[3:].strip()
                content = []
        elif in_block:
            content.append(line)
    return blocks


def is_text_block(info: str) -> bool:
    """The pinned skip rule: blocks marked ```text are not validated."""
    words = info.split()
    return bool(words) and words[0].lower() == "text"


def checkable_blocks(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        body
        for info, body in extract_blocks(path.read_text(encoding="utf-8"))
        if not is_text_block(info)
    ]


def block_params(path: Path) -> list:
    return [
        pytest.param(body, id=f"block{i}")
        for i, body in enumerate(checkable_blocks(path))
    ]


def word_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").split())


# ------------------------------------------------- harness self-checks

SAMPLE_MD = """# sample

```text
not a program < > && this must never be transpiled
```

```
fn main() {
    print(1)
}
```

```oxide
fn f() -> Int {
    2
}
```
"""


def test_extractor_finds_all_fenced_blocks() -> None:
    blocks = extract_blocks(SAMPLE_MD)
    assert len(blocks) == 3
    infos = [info for info, _ in blocks]
    assert infos == ["text", "", "oxide"]
    assert "fn main() {" in blocks[1][1]
    assert blocks[1][1].endswith("}\n")


def test_extractor_skips_text_blocks_only() -> None:
    blocks = extract_blocks(SAMPLE_MD)
    kept = [body for info, body in blocks if not is_text_block(info)]
    assert len(kept) == 2
    assert all("not a program" not in body for body in kept)


# ------------------------------------------------------ card integrity

def test_core_card_exists() -> None:
    assert CORE_CARD.is_file(), f"missing {CORE_CARD}"


def test_explicit_card_exists() -> None:
    assert EXPLICIT_CARD.is_file(), f"missing {EXPLICIT_CARD}"


def test_core_card_has_checkable_blocks() -> None:
    assert len(checkable_blocks(CORE_CARD)) >= 1


def test_explicit_card_has_checkable_blocks() -> None:
    assert len(checkable_blocks(EXPLICIT_CARD)) >= 1


# ------------------------------------------------- block validation

@pytest.mark.parametrize("src", block_params(CORE_CARD))
def test_core_card_block_transpiles_clean(src: str) -> None:
    rust, diags = transpile(src)
    codes = [d.code for d in diags]
    assert codes == [], (
        f"core card block failed with {codes}:\n{src}"
    )
    assert rust is not None


@pytest.mark.parametrize("src", block_params(EXPLICIT_CARD))
def test_explicit_card_block_checks_clean(src: str) -> None:
    from src.explicit.pipeline import run

    rust, diags = run(src)
    codes = [d.code for d in diags]
    assert codes == [], (
        f"explicit card block failed with {codes}:\n{src}"
    )
    assert rust is not None


# --------------------------------------------------------- word counts

def test_card_word_counts_within_ten_percent() -> None:
    core = word_count(CORE_CARD)
    explicit = word_count(EXPLICIT_CARD)
    assert core > 0
    assert explicit > 0
    assert abs(explicit - core) <= WORD_COUNT_TOLERANCE * core, (
        f"core={core} words, explicit={explicit} words"
    )


def test_core_card_under_900_words() -> None:
    assert word_count(CORE_CARD) < CORE_WORD_LIMIT
