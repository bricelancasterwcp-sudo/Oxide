"""Blind Phase 5b tests: CLI --json / --check per SPEC.md Part VIII (sections 39, 40, 43).

The CLI is exercised strictly as a subprocess:

    .venv/bin/python main.py [--json] [--check] <file.ox>

Nothing from src/ is imported (blind TDD). Assertions pin:
- JSON schema exactness (top-level and per-diagnostic key sets, value types),
- sorted key order in the raw output (json.dumps(..., sort_keys=True)),
- the section-40 suggestion table verbatim (parametrized over every code,
  plus the "other -> empty string" row),
- --check behavior (rust null / empty stdout in text mode),
- exit codes 0 / 1 / 2 and json-mode stderr silence,
- the unknown-file json error object {"ok": false, "error": "..."}.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = str(Path(__file__).resolve().parent.parent)
PYTHON = ROOT + "/.venv/bin/python"
MAIN = ROOT + "/main.py"

TOP_KEYS = {"diagnostics", "ok", "rust"}
DIAG_KEYS = {
    "code",
    "col",
    "end_col",
    "end_line",
    "line",
    "message",
    "notes",
    "suggestion",
}
NOTE_KEYS = {"col", "line"}

CLEAN_SRC = "fn main() {\n    print(1)\n}\n"

# S2 golden (SPEC section 19): use-after-move. The section-39 example JSON
# (line 4, col 15, end_col 16, note at 3:18) corresponds byte-for-byte to
# this program's `v` use in print(len(v)) and its move in push(v, 1).
S2_SRC = (
    "fn main() {\n"
    "    let v = vec()\n"
    "    let w = push(v, 1)\n"
    "    print(len(v))\n"
    "}\n"
)

# Section 40 table, verbatim.
SUGGESTIONS = {
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
        "inside the loop (x = ...) before the iteration ends. If the value "
        "is read after the loop (see the later-use note), cloning inside "
        "the loop will not help \u2014 the original never grows."
    ),
    "OX0406": (
        "The loop is iterating this vector; assigning to it inside the "
        "body is not allowed. Accumulate into a separate variable and "
        "reassign after the loop."
    ),
}

# One crafted error program per section-40 code. Wherever possible these are
# the spec's own pinned goldens (S2/S3/S5, V4, the section-36 negatives).
ERROR_PROGRAMS = {
    "OX0105": "fn main() {\n    break\n}\n",
    "OX0200": "fn main() {\n    print(zzz)\n}\n",
    "OX0300": "fn main() {\n    let x = 1 + true\n}\n",
    "OX0302": "fn main() {\n    let v = vec()\n}\n",
    "OX0303": "fn main() {\n    len()\n}\n",
    "OX0304": (
        "struct P {\n"
        "    x: Int,\n"
        "    y: Int,\n"
        "}\n"
        "\n"
        "fn main() {\n"
        "    let p = P { x: 1 }\n"
        "}\n"
    ),
    "OX0307": (
        "enum Shape {\n"
        "    Circle(Float),\n"
        "    Rect(Float, Float),\n"
        "    Empty,\n"
        "}\n"
        "\n"
        "fn describe(s: Shape) -> Float {\n"
        "    match s {\n"
        "        Circle(r) => r * r,\n"
        "        Rect(w, h) => w * h,\n"
        "    }\n"
        "}\n"
        "\n"
        "fn main() {\n"
        "    print(describe(Rect(3.0, 4.0)))\n"
        "}\n"
    ),
    "OX0308": "fn main() {\n    let x = 1?\n}\n",
    "OX0400": S2_SRC,
    "OX0401": (
        "fn f(v: Vec<Int>) -> Vec<Int> {\n"
        "    let a = push(v, 1)\n"
        "    let b = push(v, 2)\n"
        "    a\n"
        "}\n"
    ),
    "OX0403": (
        "fn h(v: Vec<Int>) {\n"
        "    while true {\n"
        "        let w = push(v, 1)\n"
        "    }\n"
        "}\n"
    ),
    "OX0406": (
        "fn f(v: Vec<Int>) {\n"
        "    for x in v {\n"
        "        v = push(vec(), 2)\n"
        "    }\n"
        "}\n"
    ),
}

# Codes NOT in the section-40 table: the "other" row pins suggestion == "".
# Every diagnostic these programs produce must carry an empty suggestion.
UNTABLED_PROGRAMS = {
    "OX0001": "@\n",
    "OX0100": "fn f() { (1 + ) }\n",
    "OX0202": "fn main() {\n    let x: Zzz = 1\n}\n",
    "OX0203": "fn f() {}\nfn f() {}\n",
    "OX0305": "fn main() {\n    let x = 1.5 % 2.0\n}\n",
}

# Codes whose diagnostics must carry >= 1 notes entry (section 16).
NOTE_CODES = {"OX0400", "OX0401", "OX0403"}


def run_cli(*args):
    return subprocess.run(
        [PYTHON, MAIN, *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )


def write_prog(tmp_path, source):
    path = tmp_path / "prog.ox"
    path.write_text(source, encoding="utf-8")
    return str(path)


def run_on(tmp_path, source, *flags):
    return run_cli(*flags, write_prog(tmp_path, source))


def parse_stdout(proc):
    """The whole stdout must be exactly one JSON document."""
    obj = json.loads(proc.stdout)
    assert isinstance(obj, dict)
    return obj


def assert_plain_int(value):
    assert isinstance(value, int) and not isinstance(value, bool)


def assert_diag_shape(diag):
    assert set(diag) == DIAG_KEYS
    code = diag["code"]
    assert isinstance(code, str)
    assert len(code) == 6 and code.startswith("OX") and code[2:].isdigit()
    assert isinstance(diag["message"], str) and diag["message"] != ""
    for key in ("line", "col", "end_line", "end_col"):
        assert_plain_int(diag[key])
        assert diag[key] >= 1
    assert isinstance(diag["notes"], list)
    for note in diag["notes"]:
        assert set(note) == NOTE_KEYS
        assert_plain_int(note["line"])
        assert_plain_int(note["col"])
        assert note["line"] >= 1
        assert note["col"] >= 1
    assert isinstance(diag["suggestion"], str)


def assert_key_order(raw, quoted_keys):
    """First occurrences of the quoted keys appear in the given (sorted) order."""
    positions = []
    for key in quoted_keys:
        assert key in raw
        positions.append(raw.index(key))
    assert positions == sorted(positions)


# ---------------------------------------------------------------- clean json


def test_json_clean_schema(tmp_path):
    proc = run_on(tmp_path, CLEAN_SRC, "--json")
    assert proc.returncode == 0
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert set(obj) == TOP_KEYS
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    assert isinstance(obj["rust"], str)
    assert "fn main" in obj["rust"]
    assert obj["rust"].endswith("\n")


def test_json_clean_sorted_top_keys(tmp_path):
    proc = run_on(tmp_path, CLEAN_SRC, "--json")
    assert_key_order(proc.stdout, ['"diagnostics"', '"ok"', '"rust"'])


# ---------------------------------------------------------------- error json


def test_json_error_object_ox0400(tmp_path):
    proc = run_on(tmp_path, S2_SRC, "--json")
    assert proc.returncode == 1
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert set(obj) == TOP_KEYS
    assert obj["ok"] is False
    assert obj["rust"] is None
    diags = obj["diagnostics"]
    assert isinstance(diags, list)
    assert len(diags) == 1
    diag = diags[0]
    assert_diag_shape(diag)
    assert diag["code"] == "OX0400"
    assert (diag["line"], diag["col"]) == (4, 15)
    assert (diag["end_line"], diag["end_col"]) == (4, 16)
    assert len(diag["notes"]) >= 1
    assert diag["notes"][0] == {"col": 18, "line": 3}
    assert diag["suggestion"] == SUGGESTIONS["OX0400"]


def test_json_error_diag_sorted_keys(tmp_path):
    proc = run_on(tmp_path, S2_SRC, "--json")
    assert_key_order(
        proc.stdout,
        [
            '"code"',
            '"col"',
            '"end_col"',
            '"end_line"',
            '"line"',
            '"message"',
            '"notes"',
            '"suggestion"',
        ],
    )


# ------------------------------------------------------------------- --check


def test_check_json_clean_rust_null(tmp_path):
    proc = run_on(tmp_path, CLEAN_SRC, "--json", "--check")
    assert proc.returncode == 0
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert set(obj) == TOP_KEYS
    assert obj["ok"] is True
    assert obj["rust"] is None
    assert obj["diagnostics"] == []


def test_check_json_error(tmp_path):
    proc = run_on(tmp_path, S2_SRC, "--json", "--check")
    assert proc.returncode == 1
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert obj["ok"] is False
    assert obj["rust"] is None
    assert [d["code"] for d in obj["diagnostics"]] == ["OX0400"]


def test_check_text_clean_stdout_empty(tmp_path):
    proc = run_on(tmp_path, CLEAN_SRC, "--check")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_check_text_error_renders_to_stderr(tmp_path):
    proc = run_on(tmp_path, S2_SRC, "--check")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "error[OX0400] 4:15: " in proc.stderr
    assert "note 3:18" in proc.stderr


# ------------------------------------------------------------- default (text)


def test_default_text_clean(tmp_path):
    proc = run_on(tmp_path, CLEAN_SRC)
    assert proc.returncode == 0
    assert "fn main" in proc.stdout
    assert proc.stderr == ""


def test_default_text_error(tmp_path):
    proc = run_on(tmp_path, S2_SRC)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "error[OX0400] 4:15: " in proc.stderr


# ------------------------------------------------------------ lone-CR sources


def test_lone_cr_bytes_reach_lexer_untranslated(tmp_path):
    # Regression (root cause: src/cli.py opened the source in text mode
    # with default newline=None, so universal-newline translation rewrote
    # every lone \r to \n before the lexer ran). SPEC section 3.1 pins \r
    # as skippable whitespace and section 3.5 pins only a raw \n as a
    # string terminator, and the pinned library surface (analyze /
    # transpile) sees the raw text — the CLI must read with newline="".
    # A \r inside a string literal is clean per the language; translated,
    # it became an unterminated string (OX0006) and exit 1.
    path = tmp_path / "cr.ox"
    path.write_bytes(b'fn main() {\n    let s = "a\rb"\n    print_str(s)\n}\n')
    proc = run_cli("--json", str(path))
    assert proc.returncode == 0
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert obj["ok"] is True
    assert obj["diagnostics"] == []
    assert isinstance(obj["rust"], str)
    # Position pin for a CR-only-line-ending file: the whole file is one
    # source line, so the lone-& diagnostic sits at 1:27 per
    # SourceFile.line_col over the file's actual text (section 39), not
    # at the line 2 that newline translation fabricated.
    cr_only = tmp_path / "cronly.ox"
    cr_only.write_bytes(b"fn main() {\r    let x = 1 & 2\r    print(x)\r}\r")
    proc2 = run_cli("--json", str(cr_only))
    assert proc2.returncode == 1
    assert proc2.stderr == ""
    obj2 = parse_stdout(proc2)
    assert obj2["ok"] is False
    ox0001 = [d for d in obj2["diagnostics"] if d["code"] == "OX0001"]
    assert ox0001
    assert (ox0001[0]["line"], ox0001[0]["col"]) == (1, 27)


# ---------------------------------------------------------------- exit code 2


def test_missing_file_text_exit_2(tmp_path):
    proc = run_cli(str(tmp_path / "does_not_exist.ox"))
    assert proc.returncode == 2
    assert proc.stderr != ""


def test_missing_file_json_error_object(tmp_path):
    proc = run_cli("--json", str(tmp_path / "does_not_exist.ox"))
    assert proc.returncode == 2
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert set(obj) == {"error", "ok"}
    assert obj["ok"] is False
    assert isinstance(obj["error"], str) and obj["error"] != ""
    assert_key_order(proc.stdout, ['"error"', '"ok"'])


def test_usage_no_file_exit_2():
    proc = run_cli()
    assert proc.returncode == 2


# ------------------------------------------------------- json stderr silence


STDERR_SILENCE_CASES = [
    pytest.param(CLEAN_SRC, (), id="clean-default"),
    pytest.param(S2_SRC, (), id="error-default"),
    pytest.param(CLEAN_SRC, ("--check",), id="clean-check"),
    pytest.param(S2_SRC, ("--check",), id="error-check"),
    pytest.param("@\n", (), id="lexer-garbage"),
]


@pytest.mark.parametrize("source,flags", STDERR_SILENCE_CASES)
def test_json_mode_never_writes_stderr(tmp_path, source, flags):
    proc = run_on(tmp_path, source, "--json", *flags)
    assert proc.stderr == ""
    parse_stdout(proc)


# ------------------------------------------------- section-40 suggestion table


@pytest.mark.parametrize("code", sorted(SUGGESTIONS))
def test_suggestion_strings(tmp_path, code):
    proc = run_on(tmp_path, ERROR_PROGRAMS[code], "--json")
    assert proc.returncode == 1
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert set(obj) == TOP_KEYS
    assert obj["ok"] is False
    assert obj["rust"] is None
    diags = obj["diagnostics"]
    assert isinstance(diags, list) and diags
    for diag in diags:
        assert_diag_shape(diag)
    codes = [d["code"] for d in diags]
    assert code in codes
    matching = [d for d in diags if d["code"] == code]
    for diag in matching:
        assert diag["suggestion"] == SUGGESTIONS[code]
    if code in NOTE_CODES:
        for diag in matching:
            assert len(diag["notes"]) >= 1


@pytest.mark.parametrize("code", sorted(UNTABLED_PROGRAMS))
def test_untabled_codes_have_empty_suggestion(tmp_path, code):
    proc = run_on(tmp_path, UNTABLED_PROGRAMS[code], "--json")
    assert proc.returncode == 1
    assert proc.stderr == ""
    obj = parse_stdout(proc)
    assert obj["ok"] is False
    diags = obj["diagnostics"]
    assert diags
    codes = [d["code"] for d in diags]
    assert code in codes
    for diag in diags:
        assert_diag_shape(diag)
        assert diag["suggestion"] == ""
