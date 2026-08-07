"""Oxide command-line interface (SPEC.md sections 21 and 39-41).

Grammar: ``python3 main.py [--json] [--check] [--dialect=explicit] <file.ox>``

Behavior matrix (exit codes: 0 clean / 1 diagnostics / 2 usage-or-unreadable):

- default: Rust to stdout; diagnostics rendered to stderr as
  ``error[OXnnnn] <line>:<col>: <message>`` with one ``  note <line>:<col>``
  line per notes entry.
- ``--check``: run the pipeline without emitting Rust; stdout empty in text
  mode; diagnostics/exit codes as usual.
- ``--json`` (with or without ``--check``): stdout is exactly one JSON
  object; diagnostics never go to stderr in json mode. An unreadable file
  or usage error in json mode emits ``{"ok": false, "error": "..."}``.
- ``--dialect=explicit``: dispatch to the explicit-Oxide pipeline
  (section 41); a clean exit-2 error if the dialect is not yet available.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from src.codegen.rust import transpile
from src.diagnostics import Diagnostic
from src.sema.analyze import analyze
from src.source import SourceFile

USAGE = "usage: python3 main.py [--json] [--check] [--dialect=explicit] <file.ox>"

# Section 40 suggestion table (exact strings, keyed by code); section 41
# pins the EX-code suggestions for the explicit dialect. Any other code
# gets the empty string.
SUGGESTIONS: dict[str, str] = {
    "OX0105": "break/continue only work inside while/for loops.",
    "OX0200": (
        "Unknown name. Check spelling; variables must be defined by let "
        "or as parameters before use."
    ),
    "OX0300": (
        "The two sides have incompatible types. Check operand/annotation "
        "types; Int and Float never mix implicitly (use to_float / trunc)."
    ),
    "OX0302": (
        "The type here is ambiguous. Add a use that pins it (e.g. push an "
        "element) or an annotation: let x: Vec<Int> = vec()."
    ),
    "OX0303": (
        "Not callable or wrong argument count. Check the function name "
        "and arity."
    ),
    "OX0304": (
        "Struct shape mismatch: check field names, duplicates, and that "
        "destructuring names every field."
    ),
    "OX0307": (
        "This match must cover every variant of the enum. Add the missing "
        "arms or a final _ => arm."
    ),
    "OX0308": (
        "? requires the function to return the same wrapper: "
        "Option-returning fns for Option values, Result-returning fns "
        "(matching error type) for Result values."
    ),
    "OX0400": (
        "This value was moved at the noted location. Keep it available by "
        "cloning at the move site (clone(x)), or reorder so reads happen "
        "before the move."
    ),
    "OX0401": (
        "This value was already consumed at the noted location. Clone at "
        "the first consuming use if both are needed."
    ),
    "OX0403": (
        "This value is consumed by a previous loop iteration. Reassign it "
        "inside the loop (x = ...) before the iteration ends, or clone it."
    ),
    "OX0406": (
        "The loop is iterating this vector; assigning to it inside the "
        "body is not allowed. Accumulate into a separate variable and "
        "reassign after the loop."
    ),
    "EX0001": "This use consumes the value; remove the &.",
    "EX0002": "This use only reads the value; write &name.",
    "EX0003": (
        "This value's last use is here; add 'drop name' at the required "
        "point."
    ),
    "EX0004": (
        "No drop belongs here: the value is not owned/dead at this point. "
        "Remove or move this drop."
    ),
    "EX0005": (
        "Parameter mode is wrong: read-only parameters are declared "
        "name: &Type, consumed parameters name: Type."
    ),
}


@dataclass(frozen=True, slots=True)
class CliArgs:
    """Parsed command-line arguments."""

    json_mode: bool
    check: bool
    dialect: str
    path: str


def _parse_args(args: list[str]) -> CliArgs | None:
    """Parse argv (flags in any position); None on any usage error."""
    json_mode = False
    check = False
    dialect = "core"
    path: str | None = None
    for arg in args:
        if arg == "--json":
            json_mode = True
        elif arg == "--check":
            check = True
        elif arg.startswith("--dialect="):
            value = arg[len("--dialect=") :]
            if value != "explicit":
                return None
            dialect = "explicit"
        elif arg.startswith("-"):
            return None
        elif path is None:
            path = arg
        else:
            return None
    if path is None:
        return None
    return CliArgs(json_mode=json_mode, check=check, dialect=dialect, path=path)


def _emit_json(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def _fail(message: str, json_mode: bool) -> int:
    """Report a usage error (exit 2, section 39)."""
    if json_mode:
        _emit_json({"ok": False, "error": message})
    else:
        print(message, file=sys.stderr)
    return 2


def _fail_error(message: str, json_mode: bool) -> int:
    """Report an exit-2 error; text mode adds the ``error: `` prefix."""
    if json_mode:
        _emit_json({"ok": False, "error": message})
    else:
        print(f"error: {message}", file=sys.stderr)
    return 2


def _diagnostic_json(
    diag: Diagnostic, source_file: SourceFile
) -> dict[str, object]:
    line, col = source_file.line_col(diag.span.start)
    end_line, end_col = source_file.line_col(diag.span.end)
    notes = []
    for _note_message, note_span in diag.notes:
        note_line, note_col = source_file.line_col(note_span.start)
        notes.append({"line": note_line, "col": note_col})
    return {
        "code": diag.code,
        "message": diag.message,
        "line": line,
        "col": col,
        "end_line": end_line,
        "end_col": end_col,
        "notes": notes,
        "suggestion": SUGGESTIONS.get(diag.code, ""),
    }


def _render_text_diagnostics(
    diagnostics: list[Diagnostic], source_file: SourceFile
) -> None:
    """Render diagnostics to stderr exactly as section 21 pins them."""
    for diag in diagnostics:
        line, col = source_file.line_col(diag.span.start)
        print(
            f"error[{diag.code}] {line}:{col}: {diag.message}",
            file=sys.stderr,
        )
        for _message, note_span in diag.notes:
            note_line, note_col = source_file.line_col(note_span.start)
            print(f"  note {note_line}:{note_col}", file=sys.stderr)


def _run_pipeline(
    source: str, dialect: str, check: bool
) -> tuple[str | None, list[Diagnostic]] | str:
    """Run the requested pipeline; a str return is an exit-2 error message."""
    if dialect == "explicit":
        try:
            from src.explicit.pipeline import run as explicit_run
        except ImportError:
            return (
                "dialect 'explicit' is not available "
                "(src.explicit.pipeline not found)"
            )
        rust, diagnostics = explicit_run(source)
        return (None if check else rust), list(diagnostics)
    if check:
        # --check runs the pipeline without emitting Rust (section 39).
        result = analyze(source)
        return None, list(result.diagnostics)
    return transpile(source)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    json_mode_hint = "--json" in args
    parsed = _parse_args(args)
    if parsed is None:
        return _fail(USAGE, json_mode_hint)
    try:
        # newline="" disables universal-newline translation: the pinned
        # library surface (analyze/transpile) sees the file's actual
        # characters, and SPEC sections 3.1/3.5 give lone \r its own
        # meaning (skippable whitespace; never a string terminator).
        with open(parsed.path, encoding="utf-8", newline="") as handle:
            source = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        # A file that cannot be decoded is unreadable (section 21): exit 2.
        message = f"cannot read '{parsed.path}': {exc}"
        return _fail_error(message, parsed.json_mode)
    outcome = _run_pipeline(source, parsed.dialect, parsed.check)
    if isinstance(outcome, str):
        return _fail_error(outcome, parsed.json_mode)
    rust, diagnostics = outcome
    source_file = SourceFile.from_text(source)
    if parsed.json_mode:
        clean = not diagnostics
        payload: dict[str, object] = {
            "ok": clean,
            "rust": rust if (clean and not parsed.check) else None,
            "diagnostics": [
                _diagnostic_json(diag, source_file) for diag in diagnostics
            ],
        }
        _emit_json(payload)
        return 0 if clean else 1
    if diagnostics:
        _render_text_diagnostics(diagnostics, source_file)
        return 1
    if not parsed.check and rust is not None:
        sys.stdout.write(rust)
    return 0
