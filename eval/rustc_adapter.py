"""rustc ``--error-format=json`` adapter (SPEC.md section 45).

Maps rustc diagnostics onto the section-39 JSON diagnostic shape used by
the oxide/explicit arms:

- ``code``: the rustc error code (e.g. ``E0382``) or ``"E????"`` when
  rustc attaches none.
- ``message``: rustc's ``rendered`` text verbatim — this includes the
  help/children output, which section 45 pins as part of the Rust arm's
  null hypothesis.
- ``line``/``col``/``end_line``/``end_col``: 1-based, from the primary
  span.
- ``notes``: the diagnostic's non-primary span locations (rustc's
  "moved here"/"declared here" labels), mirroring the oxide arms' notes.
- ``suggestion``: always ``""`` (pinned).

Also provides the compile/execute helpers shared by all three arms:
``rustc_check`` (type-check via ``--emit=metadata``), ``rustc_build``
(compile to a binary), and ``run_binary`` (execute under ``timeout 10``;
nontermination = fail).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

RUST_EDITION = "2021"
# Section 45 pins the execution cap; the same cap applies to every arm.
EXEC_TIMEOUT_SECONDS = 10
# Guard around rustc itself so a wedged compile cannot hang the harness.
COMPILE_TIMEOUT_SECONDS = 120


def find_rustc() -> str:
    """Locate rustc: $PATH first, then the conventional cargo home."""
    found = shutil.which("rustc")
    if found:
        return found
    return str(Path.home() / ".cargo" / "bin" / "rustc")


def _span_notes(spans: list[dict]) -> list[dict]:
    return [
        {"line": span["line_start"], "col": span["column_start"]}
        for span in spans
        if not span.get("is_primary")
    ]


def _adapt_one(obj: dict) -> dict | None:
    """Adapt a single rustc JSON diagnostic; None if it is not an error
    with a source location (skips the 'aborting due to N previous
    errors' summary and failure notes)."""
    if obj.get("level") != "error":
        return None
    spans = obj.get("spans") or []
    if not spans:
        return None
    primary = next((s for s in spans if s.get("is_primary")), spans[0])
    code_obj = obj.get("code") or {}
    code = code_obj.get("code") or "E????"
    rendered = obj.get("rendered") or obj.get("message") or ""
    return {
        "code": code,
        "message": rendered,
        "line": primary["line_start"],
        "col": primary["column_start"],
        "end_line": primary["line_end"],
        "end_col": primary["column_end"],
        "notes": _span_notes(spans),
        "suggestion": "",
    }


def adapt_diagnostics(stderr_text: str) -> list[dict]:
    """Parse rustc's JSON-lines stderr into section-39-shaped dicts."""
    diagnostics: list[dict] = []
    for line in stderr_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        adapted = _adapt_one(obj)
        if adapted is not None:
            diagnostics.append(adapted)
    return diagnostics


def _fallback_diagnostic(stderr_text: str) -> dict:
    """A location-less failure (unreadable file, driver error): surface
    rustc's own text so the model still sees the real message."""
    message = ""
    for line in stderr_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("level") != "error":
            continue
        if str(obj.get("message", "")).startswith("aborting due to"):
            continue
        message = obj.get("rendered") or obj.get("message") or ""
        break
    return {
        "code": "E????",
        "message": message or stderr_text or "rustc failed",
        "line": 1,
        "col": 1,
        "end_line": 1,
        "end_col": 1,
        "notes": [],
        "suggestion": "",
    }


def _run_rustc(args: list[str]) -> tuple[bool, list[dict]]:
    """Run rustc; (ok, adapted diagnostics). Never raises."""
    try:
        proc = subprocess.run(
            [find_rustc(), "--edition", RUST_EDITION, "--error-format=json"]
            + args,
            capture_output=True,
            text=True,
            timeout=COMPILE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [_fallback_diagnostic(f"rustc did not run: {exc}")]
    ok = proc.returncode == 0
    diagnostics = adapt_diagnostics(proc.stderr)
    if not ok and not diagnostics:
        diagnostics = [_fallback_diagnostic(proc.stderr)]
    return ok, diagnostics


def rustc_check(path: str | Path, work_dir: str | Path) -> tuple[bool, list[dict]]:
    """Type-check only (``--emit=metadata``); artifacts go to work_dir."""
    return _run_rustc(
        ["--emit=metadata", "--out-dir", str(work_dir), str(path)]
    )


def rustc_build(
    path: str | Path, out_binary: str | Path
) -> tuple[bool, list[dict]]:
    """Compile to an executable at out_binary."""
    return _run_rustc(["-o", str(out_binary), str(path)])


def run_binary(binary: str | Path) -> tuple[bool, str]:
    """Execute a generated binary under ``timeout 10``.

    Returns ``(finished, stdout)``; finished is False when the program
    was killed for nontermination (section 45: nontermination = fail).

    stdout is captured as bytes and decoded with ``errors="replace"``:
    a program emitting non-UTF-8 output must yield a verdict (it can
    never match an expected_stdout str), not an uncaught
    UnicodeDecodeError that loses the attempt.
    """
    try:
        proc = subprocess.run(
            ["timeout", str(EXEC_TIMEOUT_SECONDS), str(binary)],
            capture_output=True,
            timeout=EXEC_TIMEOUT_SECONDS + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode == 124:  # timeout(1)'s kill status
        return False, stdout
    return True, stdout
