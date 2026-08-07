"""Repair-prompt construction (SPEC Part X, section 6.3).

Arm-identical STRUCTURE, arm-native CONTENT: Oxide arms supply OX codes
with suggestions, the Rust arm supplies rustc's full help text verbatim.

`expected_stdout` is deliberately not a parameter. Disclosing it would
let a weak model pass by hard-coding a print of the expected string,
silently corrupting the headline metric.
"""

from __future__ import annotations

ARMS = ("oxide", "explicit", "rust")

FIX_INSTRUCTION = (
    "Reply with ONLY the complete corrected program source, "
    "no fences, no commentary."
)


def render_diagnostics(diagnostics: list[dict]) -> str:
    """One `line:col: CODE: message` per diagnostic, notes/suggestion
    indented two spaces beneath it."""
    lines: list[str] = []
    for diag in diagnostics:
        lines.append(
            f"{diag['line']}:{diag['col']}: {diag['code']}: {diag['message']}"
        )
        for note in diag.get("notes", []):
            lines.append(f"  note: line {note['line']}, col {note['col']}")
        if diag.get("suggestion"):
            lines.append(f"  suggestion: {diag['suggestion']}")
    return "\n".join(lines)


def build_repair_prompt(arm: str, source: str, verdict: dict) -> str:
    """The next-attempt prompt for a rejected program."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm '{arm}'")
    if verdict["compiled"]:
        # No diagnostics exist for a wrong-output run. Report only the
        # program's own observed output -- never the task's expected one.
        body = (
            "The program compiled and ran, but produced incorrect output.\n"
            "Its output was:\n" + verdict["stdout"]
        )
    else:
        body = "Diagnostics:\n" + render_diagnostics(verdict["diagnostics"])
    return (
        "The program below was rejected. Fix it.\n\n"
        f"Program:\n{source}\n\n"
        f"{body}\n\n"
        f"{FIX_INSTRUCTION}\n"
    )
