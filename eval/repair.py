"""Repair-prompt construction (SPEC Part X, section 6.3).

A repair prompt is the arm's own initial prompt with its tail swapped:
everything `harness.build_prompt` produces except the trailing output
contract, then the rejected program, then the failure block, then the
fix instruction. Reusing the frozen harness -- rather than
reconstructing a lead here -- is what makes context retention
structurally equal across arms instead of merely asserted.

`expected_stdout` is deliberately not a parameter, and
`harness.build_prompt` does not disclose it either. Disclosing it would
let a weak model pass by hard-coding a print of the expected string,
silently corrupting the headline metric.
"""

from __future__ import annotations

from pathlib import Path

from eval import harness

ARMS = ("oxide", "explicit", "rust")

FIX_INSTRUCTION = (
    "Reply with ONLY the complete corrected program source, "
    "no fences, no commentary."
)


class RepairPromptError(RuntimeError):
    """The frozen harness prompt no longer has the shape we strip.

    Raised loudly rather than silently emitting a malformed prompt: a
    repair prompt that still carried the initial output contract would
    end with two conflicting instructions.
    """


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


def initial_context(
    arm: str,
    task_id: str,
    shots: int = 0,
    tasks_path: str | Path | None = None,
) -> str:
    """The arm's initial prompt minus its trailing output contract.

    Stripping a known constant suffix is deterministic and testable; a
    harness change that moves or renames the contract raises instead of
    quietly producing a prompt with a stale tail.
    """
    prompt = harness.build_prompt(
        arm, task_id, shots=shots, tasks_path=tasks_path
    )
    contract = harness.OUTPUT_CONTRACT
    if contract not in prompt or not prompt.rstrip("\n").endswith(contract):
        raise RepairPromptError(
            "harness.build_prompt no longer ends with harness.OUTPUT_CONTRACT; "
            "eval/repair.py cannot swap the prompt tail safely"
        )
    return prompt.rstrip("\n")[: -len(contract)].rstrip("\n")


def _failure_block(verdict: dict) -> str:
    """What the judge observed -- never what it expected."""
    if verdict["compiled"]:
        # No diagnostics exist for a wrong-output run. Report only the
        # program's own observed output -- never the task's expected one.
        return (
            "The program compiled and ran, but produced incorrect output.\n"
            "Its output was:\n" + verdict["stdout"]
        )
    return "Diagnostics:\n" + render_diagnostics(verdict["diagnostics"])


def build_repair_prompt(
    arm: str,
    source: str,
    verdict: dict,
    *,
    task_id: str,
    shots: int = 0,
    tasks_path: str | Path | None = None,
) -> str:
    """The next-attempt prompt for a rejected program.

    Carries the arm's full initial context (language card or Rust
    preamble, few-shot examples, task statement) so that no arm loses
    the material it was given to start with. Section 47's repair-lift
    metric is only about diagnostic quality if the context an arm
    retains between attempts does not itself vary by arm.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm '{arm}'")
    context = initial_context(arm, task_id, shots=shots, tasks_path=tasks_path)
    return (
        f"{context}\n\n"
        "The program below was rejected. Fix it.\n\n"
        f"Program:\n{source}\n\n"
        f"{_failure_block(verdict)}\n\n"
        f"{FIX_INSTRUCTION}\n"
    )
