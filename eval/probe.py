"""Ownership probe: prompt construction, scoring, and runner.

The whole-program instrument cannot reach linearity. `src/sema/analyze.py`
is strictly staged -- lexing, parsing, name resolution and type checking
all gate it -- so across ~530 Oxide-arm attempts in five Phase 6a
configurations no `OX04xx` diagnostic has ever fired. Small models never
clear the first two stages, grammar constraint moved the population to
the third, and frontier models clear all four and score 20/20. The
feature the language exists for is reached by nobody.

This instrument hands the model a program that is complete and correct
EXCEPT for one ownership defect, together with the compiler's own
diagnostic, and scores the repair. Everything that is not ownership --
syntax, names, types -- is supplied and correct, so the only thing the
model can get wrong is the thing under test.

`expected_stdout` is never disclosed, exactly as in `eval/repair.py`: a
model told the expected output could pass by printing it, silently
corrupting the headline metric.

Stdlib only. The corpus lives in `eval/probes.jsonl` and every record is
mechanically verified by `tests/test_probes.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # direct `python3 eval/probe.py` runs
    sys.path.insert(0, str(_REPO_ROOT))

from eval import harness, repair
from eval.extract import extract
from eval.models import ModelClient, ModelError, OllamaClient

PROBES_PATH = _REPO_ROOT / "eval" / "probes.jsonl"

PROBE_INSTRUCTION = (
    "The program below has exactly one ownership error. Everything else "
    "about it is correct. Fix the ownership error."
)

# Section-39 codes for the Oxide linearity checker. The lenient score asks
# whether the ownership diagnostic CLASS is gone, not merely whether the
# one code we injected is: a repair that turns OX0400 into OX0401 has not
# understood the ownership fix, and scoring it as a pass would inflate
# exactly the number this instrument exists to measure.
OXIDE_OWNERSHIP_PREFIX = "OX04"

# rustc's borrow/move-check family. Pinned as an explicit set rather than
# a prefix because rustc's E-codes are a flat namespace: E0308 (type
# mismatch) and E0382 (use of moved value) share no distinguishing prefix,
# so a prefix rule would either miss ownership errors or swallow type
# errors. Every code here is a borrowck/moveck rejection.
RUST_OWNERSHIP_CODES = frozenset(
    {
        "E0382",  # use/borrow of moved value
        "E0499",  # two mutable borrows at once
        "E0500",  # closure borrow conflicts with an existing borrow
        "E0501",  # closure borrow conflicts with a prior borrow
        "E0502",  # mutable borrow while immutably borrowed (and inverse)
        "E0503",  # use of a value while mutably borrowed
        "E0504",  # move out of a value while borrowed
        "E0505",  # move out of a value while borrowed
        "E0506",  # assign to a borrowed value
        "E0507",  # move out of a shared reference / index
        "E0508",  # move out of an array/slice index
        "E0509",  # move out of a type implementing Drop
        "E0594",  # assign to an immutable binding/place
        "E0596",  # borrow as mutable through an immutable binding
        "E0716",  # temporary dropped while borrowed
    }
)


class ProbeError(RuntimeError):
    """A probe-corpus or probe-prompt fault, raised loudly.

    Never swallowed into a degraded prompt: a probe prompt missing its
    language card, or carrying no diagnostic, silently measures something
    other than ownership repair.
    """


# ------------------------------------------------------------------ corpus


def load_probes(path: str | Path | None = None) -> list[dict]:
    """Every probe record, in file order."""
    probes_path = Path(path) if path is not None else PROBES_PATH
    try:
        text = probes_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProbeError(f"cannot read probes file '{probes_path}': {exc}") from exc
    records: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ProbeError(
                f"malformed probe at {probes_path}:{line_no}: {exc}"
            ) from exc
    return records


def probe_key(record: dict) -> str:
    """The `(id, arm)` identity of one record, as a flat string."""
    return f"{record['id']}.{record['arm']}"


# ------------------------------------------------------------------ prompt


def language_card(arm: str) -> str:
    """The arm's lead material: its language card, or the Rust preamble.

    Read through the frozen harness's own constants (`CARD_FILES`,
    `RUST_PREAMBLE`) so the probe cannot drift from what the whole-program
    instrument shows the same arm. The card is included deliberately: this
    probe isolates ownership, so card recall must not become a second
    variable.
    """
    if arm not in harness.ARMS:
        raise ProbeError(f"unknown arm '{arm}' (expected one of {harness.ARMS})")
    if arm == "rust":
        return harness.RUST_PREAMBLE
    card_path = _REPO_ROOT / harness.CARD_FILES[arm]
    try:
        return card_path.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise ProbeError(f"cannot read language card '{card_path}': {exc}") from exc


# rustc's ``rendered`` text -- which the adapter pins as the rust arm's
# message, help output and all -- embeds the absolute path of the file it
# compiled, and the harness stages every program into a FRESH
# ``oxide-eval-XXXXXXXX`` temp directory. Left alone that has two effects,
# both on the rust arm only: the same probe yields a different prompt on
# every invocation (so a run is not reproducible and prompts cannot be
# diffed across runs), and the path literally contains the word "oxide",
# which the control arm must not see. Stripping the directory leaves
# rustc's own uniform ``program.rs`` stem and changes nothing else.
_TMP_DIR_RE = re.compile(r"[^\s\"'()]*/oxide-(?:eval|probe)-[A-Za-z0-9_]+/")


def _scrub_paths(diagnostics: list[dict]) -> list[dict]:
    """Fresh diagnostics with harness temp paths removed from messages."""
    return [
        {**diag, "message": _TMP_DIR_RE.sub("", str(diag.get("message", "")))}
        for diag in diagnostics
    ]


def diagnose(arm: str, source: str) -> list[dict]:
    """The compiler's own section-39 diagnostics for a source string.

    Deterministic: the harness temp path rustc renders into its messages
    is scrubbed, so two calls on the same source are byte-identical.
    """
    if arm not in harness.ARMS:
        raise ProbeError(f"unknown arm '{arm}' (expected one of {harness.ARMS})")
    with tempfile.TemporaryDirectory(prefix="oxide-probe-") as work:
        path = Path(work) / f"program{harness.SOURCE_SUFFIX[arm]}"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)
        return _scrub_paths(harness.check_file(arm, path)["diagnostics"])


def build_probe_prompt(record: dict, diagnostics: list[dict] | None = None) -> str:
    """The repair prompt for one probe: card, program, diagnostics, task.

    `diagnostics` defaults to the real compiler output for `broken`;
    passing them in is for tests and for callers that already ran the
    checker. `expected_stdout` and `fix` are never referenced here -- the
    prompt is built from `broken` alone.
    """
    arm = record["arm"]
    diags = diagnose(arm, record["broken"]) if diagnostics is None else diagnostics
    if not diags:
        raise ProbeError(
            f"probe {probe_key(record)} has no diagnostics for its broken "
            f"program; a probe prompt with an empty failure block would ask "
            f"the model to repair a program it is told nothing is wrong with"
        )
    return (
        f"{language_card(arm)}\n\n"
        f"{PROBE_INSTRUCTION}\n\n"
        f"Program:\n{record['broken']}\n"
        f"Diagnostics:\n{repair.render_diagnostics(diags)}\n\n"
        f"{repair.FIX_INSTRUCTION}\n"
    )


# ------------------------------------------------------------------ scoring


def is_ownership_code(arm: str, code: str) -> bool:
    """Whether a diagnostic code belongs to the arm's ownership family."""
    if arm == "rust":
        return code in RUST_OWNERSHIP_CODES
    return code.startswith(OXIDE_OWNERSHIP_PREFIX)


def score(record: dict, submitted_source: str) -> dict:
    """Both design scores for one submitted repair.

    `strict` -- compiles AND stdout matches `expected_stdout`. The output
    check is load-bearing, not belt-and-braces: the degenerate "repair" is
    to delete the offending use, which compiles cleanly and would
    otherwise be counted as a repair while silently changing what the
    program does.

    `lenient` -- the ownership diagnostic class is gone, whatever else the
    repair broke. This separates "understood the ownership fix" from
    "could also still write valid code", which matters at small scale. It
    is a LOOSE bound by construction: a submission that fails to parse
    also emits no ownership diagnostic and so scores lenient-pass. Read it
    only alongside `strict`, never on its own.
    """
    arm = record["arm"]
    if arm not in harness.ARMS:
        raise ProbeError(f"unknown arm '{arm}' (expected one of {harness.ARMS})")
    with tempfile.TemporaryDirectory(prefix="oxide-probe-") as work:
        path = Path(work) / f"submission{harness.SOURCE_SUFFIX[arm]}"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(submitted_source)
        verdict = harness.run_file(arm, path, record["expected_stdout"])
    codes = [str(diag.get("code", "?")) for diag in verdict["diagnostics"]]
    return {
        "id": record["id"],
        "arm": arm,
        "defect": record["defect"],
        "strict": bool(verdict["compiled"] and verdict["passed"]),
        "lenient": not any(is_ownership_code(arm, code) for code in codes),
        "compiled": bool(verdict["compiled"]),
        "stdout": verdict["stdout"],
        "codes": codes,
    }


def summarize(results: list[dict]) -> dict:
    """Per-arm strict/lenient rates plus the repaired-program diagnostic
    distribution the design asks to be reported alongside them."""
    by_arm: dict[str, list[dict]] = {}
    for result in results:
        by_arm.setdefault(result["arm"], []).append(result)
    arms: dict[str, dict] = {}
    for arm, rows in sorted(by_arm.items()):
        histogram: Counter[str] = Counter()
        for row in rows:
            histogram.update(row["codes"])
        arms[arm] = {
            "probes": len(rows),
            "strict": sum(bool(row["strict"]) for row in rows) / len(rows),
            "lenient": sum(bool(row["lenient"]) for row in rows) / len(rows),
            "diagnostic_histogram": dict(sorted(histogram.items())),
        }
    return {"arms": arms, "probes": len(results)}


# ------------------------------------------------------------------ runner


def run_probe(
    client: ModelClient,
    record: dict,
    *,
    raw_dir: Path | None = None,
    seed: int = 1,
) -> dict:
    """One generation against one probe, scored.

    ModelError is deliberately NOT caught: an infrastructure failure
    recorded as a model failure biases every arm toward the null.
    """
    prompt = build_probe_prompt(record)
    generation = client.generate(prompt, seed=seed)
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{probe_key(record)}.txt").write_text(
            generation.text, encoding="utf-8"
        )
    candidate = extract(generation.text)
    result = score(record, candidate.source)
    result.update(
        {
            "contract_compliant": candidate.contract_compliant,
            "truncated": generation.truncated,
            "tokens_in": generation.tokens_in,
            "tokens_out": generation.tokens_out,
            "ms": generation.ms,
        }
    )
    return result


def run_corpus(
    client: ModelClient,
    records: list[dict],
    *,
    out_dir: Path,
    seed: int = 1,
) -> dict:
    """Every probe in `records`, appending one result line per probe."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "probe_results.jsonl"
    if results_path.exists():
        # Appending would interleave two runs in one file, and any later
        # aggregation over it would silently average them together --
        # the same hazard harness._claim_session exists to prevent.
        raise ProbeError(
            f"'{results_path}' already exists; use a fresh --out directory "
            f"rather than appending a second run to the first"
        )
    results: list[dict] = []
    for record in records:
        result = run_probe(client, record, raw_dir=out_dir / "raw", seed=seed)
        results.append(result)
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, sort_keys=True) + "\n")
    summary = summarize(results)
    (out_dir / "probe_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


# ------------------------------------------------------------------ CLI


def _select(records: list[dict], probe_id: str | None, arm: str | None) -> list[dict]:
    chosen = [
        rec
        for rec in records
        if (probe_id is None or rec["id"] == probe_id)
        and (arm is None or rec["arm"] == arm)
    ]
    if not chosen:
        raise ProbeError(f"no probe matches id={probe_id!r} arm={arm!r}")
    return chosen


def _cmd_list(args: argparse.Namespace) -> int:
    for rec in _select(load_probes(args.probes), args.id, args.arm):
        print(
            f"{probe_key(rec)}\t{rec['defect']}\t{rec['expected_code']}"
            f"\trust_equivalent={rec['rust_equivalent']}"
        )
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    chosen = _select(load_probes(args.probes), args.id, args.arm)
    if len(chosen) != 1:
        raise ProbeError("prompt needs exactly one probe: pass --id and --arm")
    sys.stdout.write(build_probe_prompt(chosen[0]))
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    chosen = _select(load_probes(args.probes), args.id, args.arm)
    if len(chosen) != 1:
        raise ProbeError("score needs exactly one probe: pass --id and --arm")
    with open(args.file, encoding="utf-8", newline="") as handle:
        source = handle.read()
    print(json.dumps(score(chosen[0], source), sort_keys=True))
    return 0


def _make_client(args: argparse.Namespace) -> ModelClient:
    """Build the requested client. Reuses eval/models.py and
    eval/llamacpp.py rather than reimplementing either.

    A grammar is per-arm, so ``--grammar`` without ``--arm`` is refused
    rather than quietly ignored: a run that *looks* constrained but is
    not would be indistinguishable from one that is.
    """
    if args.backend == "ollama":
        if args.grammar:
            raise ProbeError(
                "--grammar needs --backend llamacpp: Ollama accepts a grammar "
                "option and silently ignores it"
            )
        return OllamaClient(args.model)
    from eval.llamacpp import LlamaCppClient, load_grammar

    grammar = None
    if args.grammar:
        if args.arm is None:
            raise ProbeError("--grammar requires --arm (a grammar is per-arm)")
        try:
            grammar = load_grammar(args.arm)
        except ValueError as exc:  # the rust arm has none, by design
            raise ProbeError(str(exc)) from exc
    return LlamaCppClient(args.model, grammar=grammar)


def _cmd_run(args: argparse.Namespace) -> int:
    records = _select(load_probes(args.probes), args.id, args.arm)
    summary = run_corpus(
        _make_client(args),
        records,
        out_dir=Path(args.out),
        seed=args.seed,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _add_selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", default=None, help="probe id, e.g. p01")
    parser.add_argument("--arm", default=None, choices=harness.ARMS)
    parser.add_argument("--probes", default=None, help="probes.jsonl override")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m eval.probe",
        description="Ownership probe corpus: prompts, scoring, and runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list probe records")
    _add_selectors(listing)
    listing.set_defaults(handler=_cmd_list)

    prompt = sub.add_parser("prompt", help="emit one probe's repair prompt")
    _add_selectors(prompt)
    prompt.set_defaults(handler=_cmd_prompt)

    scoring = sub.add_parser("score", help="score a submitted repair file")
    _add_selectors(scoring)
    scoring.add_argument("--file", required=True)
    scoring.set_defaults(handler=_cmd_score)

    run = sub.add_parser("run", help="run a model over the probe corpus")
    _add_selectors(run)
    run.add_argument("--backend", default="llamacpp", choices=("llamacpp", "ollama"))
    run.add_argument("--model", default="local")
    run.add_argument("--grammar", action="store_true")
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--out", required=True)
    run.set_defaults(handler=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (ProbeError, harness.HarnessError, ModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
