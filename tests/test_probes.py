"""Verification of the ownership-probe corpus (docs/superpowers/specs/
2026-08-07-ownership-probe-design.md).

The corpus's whole claim to validity is mechanical, not editorial. Every
record is checked against the real transpiler and the real rustc:

1. `broken` FAILS to compile and its diagnostics contain `expected_code`.
2. `broken` fails for the ownership reason ONLY. No `OX0001`, no
   `OX01xx`/`OX02xx`/`OX03xx`, no `EX00xx`, and for the rust arm no error
   other than the expected one. A probe carrying an incidental syntax or
   type error reintroduces exactly the confound this instrument removes.
3. `fix` compiles AND produces `expected_stdout` exactly.
4. For one id, every arm shares the same `expected_stdout` -- the three
   programs must do the same observable thing.

These run the real toolchain and are therefore slow. That is correct and
deliberate: a stubbed check would verify the stub, not the corpus.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import harness, probe  # noqa: E402

ARMS = ("oxide", "explicit", "rust")
RECORD_KEYS = {
    "id",
    "arm",
    "defect",
    "expected_code",
    "expected_stdout",
    "rust_equivalent",
    "broken",
    "fix",
}
#: Optional. Records why an arm's `expected_code` diverges from its
#: siblings' -- p04 is OX0406/E0502 and p20 is OX0406/E0505, because the
#: two languages disagree about WHICH rule an iterate-and-consume breaks.
#: That disagreement is a finding about the languages, so it belongs in
#: the corpus rather than being smoothed away or left unexplained.
OPTIONAL_RECORD_KEYS = {"note"}
REQUIRED_DEFECTS = {
    "use-after-move",
    "double-consume",
    "loop-carried-move",
    "assign-to-iterated-vec",
    "move-then-read-field",
    "move-inside-branch",
}
# The non-ownership Oxide families this instrument exists to exclude.
FORBIDDEN_OXIDE_PREFIXES = ("OX0001", "OX01", "OX02", "OX03", "EX00")

PROBES = probe.load_probes()
IDS = sorted({rec["id"] for rec in PROBES})
CASES = [(rec, probe.probe_key(rec)) for rec in PROBES]
PARAMS = [pytest.param(rec, id=key) for rec, key in CASES]


def _run(arm: str, source: str, expected_stdout: str) -> dict:
    """Full verdict for a source string through the frozen harness."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="oxide-probe-test-") as work:
        path = Path(work) / f"program{harness.SOURCE_SUFFIX[arm]}"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(source)
        return harness.run_file(arm, path, expected_stdout)


# ------------------------------------------------------------- corpus shape


class TestCorpusShape:
    def test_corpus_is_not_empty(self):
        assert PROBES, "eval/probes.jsonl is empty"

    def test_every_record_has_the_pinned_keys(self):
        for rec in PROBES:
            keys = set(rec)
            assert RECORD_KEYS <= keys, probe.probe_key(rec)
            assert keys <= RECORD_KEYS | OPTIONAL_RECORD_KEYS, probe.probe_key(rec)

    def test_divergent_expected_codes_are_explained(self):
        """A record whose expected_code differs from its siblings' must
        say why. Silent divergence reads as a corpus defect; explained
        divergence is a finding about where the two languages disagree."""
        by_id: dict[str, list[dict]] = {}
        for rec in PROBES:
            by_id.setdefault(rec["id"], []).append(rec)
        for probe_id, recs in sorted(by_id.items()):
            oxide = {r["expected_code"] for r in recs if r["arm"] != "rust"}
            for rec in recs:
                if rec["arm"] != "rust":
                    continue
                # Rust codes always differ in form (E-codes vs OX-codes);
                # what needs explaining is a rust code that is not the
                # corpus's usual E0382 partner for an OX04xx move error.
                if rec["expected_code"] not in {"E0382"}:
                    assert rec.get("note"), (
                        f"{probe_id}: rust expects {rec['expected_code']} "
                        f"against {oxide} but carries no note explaining why"
                    )

    def test_ids_and_arms_are_unique(self):
        keys = [probe.probe_key(rec) for rec in PROBES]
        assert len(keys) == len(set(keys)), "duplicate (id, arm) record"

    def test_all_six_defect_classes_are_covered(self):
        assert {rec["defect"] for rec in PROBES} >= REQUIRED_DEFECTS

    def test_one_defect_class_per_id(self):
        by_id: dict[str, set[str]] = {}
        for rec in PROBES:
            by_id.setdefault(rec["id"], set()).add(rec["defect"])
        for probe_id, defects in by_id.items():
            assert len(defects) == 1, (probe_id, defects)

    def test_oxide_and_explicit_records_exist_for_every_id(self):
        for probe_id in IDS:
            arms = {rec["arm"] for rec in PROBES if rec["id"] == probe_id}
            assert {"oxide", "explicit"} <= arms, probe_id

    def test_rust_record_present_exactly_when_claimed_equivalent(self):
        """A class with no faithful Rust analogue must SAY so rather than
        ship a strained approximation -- a forced analogue would silently
        make the arms incomparable."""
        for probe_id in IDS:
            group = [rec for rec in PROBES if rec["id"] == probe_id]
            claimed = {rec["rust_equivalent"] for rec in group}
            assert len(claimed) == 1, f"{probe_id}: inconsistent rust_equivalent"
            has_rust = any(rec["arm"] == "rust" for rec in group)
            assert has_rust is claimed.pop(), probe_id

    def test_broken_and_fix_differ(self):
        for rec in PROBES:
            assert rec["broken"] != rec["fix"], probe.probe_key(rec)

    def test_expected_code_matches_arm_family(self):
        for rec in PROBES:
            assert probe.is_ownership_code(rec["arm"], rec["expected_code"]), rec


# ------------------------------- 4. matched triples share observable output


@pytest.mark.parametrize("probe_id", IDS)
def test_arms_agree_on_expected_stdout(probe_id):
    outputs = {
        rec["expected_stdout"] for rec in PROBES if rec["id"] == probe_id
    }
    assert len(outputs) == 1, f"{probe_id}: arms disagree on output {outputs}"


# ------------------------------------ 1 & 2. broken fails, for ownership only


@pytest.mark.parametrize("record", PARAMS)
def test_broken_fails_with_the_expected_ownership_code(record):
    verdict = _run(
        record["arm"], record["broken"], record["expected_stdout"]
    )
    assert verdict["compiled"] is False, (
        f"{probe.probe_key(record)}: broken program compiled"
    )
    codes = [diag["code"] for diag in verdict["diagnostics"]]
    assert record["expected_code"] in codes, codes


@pytest.mark.parametrize("record", PARAMS)
def test_broken_fails_for_the_ownership_reason_only(record):
    arm = record["arm"]
    codes = [diag["code"] for diag in probe.diagnose(arm, record["broken"])]
    assert codes, f"{probe.probe_key(record)}: broken program is clean"
    assert set(codes) == {record["expected_code"]}, codes
    for code in codes:
        assert probe.is_ownership_code(arm, code), code
        if arm != "rust":
            assert not code.startswith(FORBIDDEN_OXIDE_PREFIXES), code


# --------------------------------- 3. fix compiles and produces the output


@pytest.mark.parametrize("record", PARAMS)
def test_fix_compiles_and_matches_expected_stdout(record):
    verdict = _run(record["arm"], record["fix"], record["expected_stdout"])
    assert verdict["compiled"] is True, verdict["diagnostics"]
    assert verdict["stdout"] == record["expected_stdout"], repr(verdict["stdout"])
    assert verdict["passed"] is True


# ------------------------------------------------------ prompt construction


class TestPrompt:
    """The probe prompt must carry the card, the program, and the real
    diagnostic -- and must never leak the answer."""

    def test_prompt_never_discloses_expected_stdout(self):
        """Same integrity requirement as the repair prompt: a model told
        the expected output could pass by printing it."""
        for rec in PROBES:
            prompt = probe.build_probe_prompt(rec)
            assert rec["expected_stdout"] not in prompt, probe.probe_key(rec)

    def test_prompt_never_discloses_the_fix(self):
        for rec in PROBES:
            prompt = probe.build_probe_prompt(rec)
            assert rec["fix"] not in prompt, probe.probe_key(rec)

    def test_prompt_carries_program_and_diagnostic(self):
        for rec in PROBES:
            prompt = probe.build_probe_prompt(rec)
            assert rec["broken"] in prompt, probe.probe_key(rec)
            assert rec["expected_code"] in prompt, probe.probe_key(rec)
            assert harness.OUTPUT_CONTRACT not in prompt, probe.probe_key(rec)

    @pytest.mark.parametrize("arm", ("oxide", "explicit"))
    def test_prompt_embeds_the_arms_language_card(self, arm):
        card = (ROOT / harness.CARD_FILES[arm]).read_text(encoding="utf-8")
        rec = next(r for r in PROBES if r["arm"] == arm)
        assert card.rstrip("\n") in probe.build_probe_prompt(rec)

    def test_rust_prompt_never_mentions_oxide(self):
        """Fairness: the control arm must not see the word (mirrors the
        whole-program instrument's own check)."""
        for rec in PROBES:
            if rec["arm"] != "rust":
                continue
            prompt = probe.build_probe_prompt(rec)
            assert "oxide" not in prompt.lower(), probe.probe_key(rec)
            assert harness.RUST_PREAMBLE in prompt

    @pytest.mark.parametrize("arm", ARMS)
    def test_prompt_is_byte_identical_across_builds(self, arm):
        """rustc renders the absolute path of the file it compiled, and the
        harness stages every program into a fresh temp directory. Without
        scrubbing, the rust arm's prompt would differ on every invocation
        -- unreproducible, and un-diffable across runs."""
        rec = next(r for r in PROBES if r["arm"] == arm)
        assert probe.build_probe_prompt(rec) == probe.build_probe_prompt(rec)

    def test_prompt_carries_no_harness_temp_path(self):
        for rec in PROBES:
            prompt = probe.build_probe_prompt(rec)
            assert "oxide-eval-" not in prompt, probe.probe_key(rec)
            assert "oxide-probe-" not in prompt, probe.probe_key(rec)

    def test_diagnostics_render_exactly_as_repair_renders_them(self):
        from eval import repair

        rec = PROBES[0]
        diags = probe.diagnose(rec["arm"], rec["broken"])
        assert repair.render_diagnostics(diags) in probe.build_probe_prompt(rec)

    def test_empty_diagnostics_are_refused(self):
        """A probe prompt with an empty failure block would ask the model
        to repair a program it is told nothing is wrong with."""
        with pytest.raises(probe.ProbeError):
            probe.build_probe_prompt(PROBES[0], diagnostics=[])


# ------------------------------------------------------------------ scoring


@pytest.mark.parametrize("record", PARAMS)
def test_fix_scores_strict_and_lenient_pass(record):
    result = probe.score(record, record["fix"])
    assert result["strict"] is True, result
    assert result["lenient"] is True, result


@pytest.mark.parametrize("record", PARAMS)
def test_broken_scores_strict_and_lenient_fail(record):
    result = probe.score(record, record["broken"])
    assert result["strict"] is False, result
    assert result["lenient"] is False, result
    assert record["expected_code"] in result["codes"]


# The degenerate "repair": delete the second use. It compiles cleanly and
# emits no ownership diagnostic, so it scores lenient-pass -- but it has
# silently changed what the program does, which is precisely why strict
# requires the output and not merely a clean compile.
DEGENERATE = {
    "oxide": """fn main() {
    let a = push(push(vec(), 1), 2)
    let b = a
    print(len(b))
}
""",
    "explicit": """fn main() {
    let a = push(push(vec(), 1), 2)
    let b = a
    print(len(&b))
    drop b
}
""",
    "rust": """fn main() {
    let a = vec![1, 2];
    let b = a;
    println!("{}", b.len());
}
""",
}


@pytest.mark.parametrize("arm", ARMS)
def test_deleting_the_offending_use_is_lenient_pass_strict_fail(arm):
    record = next(r for r in PROBES if r["id"] == "p01" and r["arm"] == arm)
    result = probe.score(record, DEGENERATE[arm])
    assert result["compiled"] is True, result["codes"]
    assert result["lenient"] is True, result
    assert result["strict"] is False, (
        "a repair that deletes the offending use must not score strict-pass"
    )
    assert result["stdout"] != record["expected_stdout"]


class TestScoringHelpers:
    def test_ownership_code_classification(self):
        assert probe.is_ownership_code("oxide", "OX0400")
        assert probe.is_ownership_code("explicit", "OX0406")
        assert not probe.is_ownership_code("oxide", "OX0302")
        assert not probe.is_ownership_code("explicit", "EX0003")
        assert probe.is_ownership_code("rust", "E0382")
        assert probe.is_ownership_code("rust", "E0502")
        assert not probe.is_ownership_code("rust", "E0308")

    def test_lenient_is_class_wide_not_code_specific(self):
        """Turning OX0400 into OX0401 is not an understood ownership fix."""
        record = next(
            r for r in PROBES if r["id"] == "p01" and r["arm"] == "oxide"
        )
        other = next(
            r for r in PROBES if r["id"] == "p02" and r["arm"] == "oxide"
        )
        result = probe.score(record, other["broken"])  # OX0401, not OX0400
        assert result["codes"] == ["OX0401"]
        assert result["lenient"] is False

    def test_summarize_reports_rates_and_histogram(self):
        rows = [
            {"arm": "oxide", "strict": True, "lenient": True, "codes": []},
            {
                "arm": "oxide",
                "strict": False,
                "lenient": False,
                "codes": ["OX0400"],
            },
            {"arm": "rust", "strict": True, "lenient": True, "codes": []},
        ]
        summary = probe.summarize(rows)
        assert summary["probes"] == 3
        assert summary["arms"]["oxide"]["strict"] == 0.5
        assert summary["arms"]["oxide"]["lenient"] == 0.5
        assert summary["arms"]["oxide"]["diagnostic_histogram"] == {"OX0400": 1}
        assert summary["arms"]["rust"]["strict"] == 1.0

    def test_unknown_arm_is_refused(self):
        with pytest.raises(probe.ProbeError):
            probe.score({"arm": "nope", "expected_stdout": ""}, "fn main() {}")
        with pytest.raises(probe.ProbeError):
            probe.language_card("nope")


# The lenient score's parse-and-non-blank preconditions. Without them the
# metric is trivially gameable in exactly the regime it exists for: a
# small model emitting nothing usable produces no ownership diagnostic and
# would have read ~100% lenient. Verified against the pre-guard code --
# an empty string and "!!! not a program !!!" both scored lenient-pass.
@pytest.mark.parametrize("arm", ["oxide", "explicit", "rust"])
@pytest.mark.parametrize(
    "junk", ["", "   \n\n  ", "!!! not a program !!!", "%%%%"],
    ids=["empty", "blank", "garbage", "symbols"],
)
def test_degenerate_submissions_never_score_lenient(arm, junk):
    record = next(r for r in PROBES if r["arm"] == arm)
    result = probe.score(record, junk)
    assert result["lenient"] is False, result
    assert result["strict"] is False, result


def test_empty_submission_is_judged_identically_across_arms():
    # The Oxide transpiler emits `fn main() {}` for empty input, so empty
    # parses cleanly there while rustc rejects it with E0601. Left
    # unguarded that asymmetry inflates the Oxide arms specifically, in
    # the primary comparison -- worse than a symmetric loophole.
    verdicts = {
        arm: probe.score(
            next(r for r in PROBES if r["arm"] == arm), ""
        )["lenient"]
        for arm in ("oxide", "explicit", "rust")
    }
    assert set(verdicts.values()) == {False}, verdicts


class TestP17Disambiguation:
    """p17 originally admitted two valid repairs with different behaviour.

    The class is accumulate-without-reassign. Its original form printed
    INSIDE the loop, so a clone-based repair could redirect the print to
    the clone and satisfy it locally -- and cloning is precisely what
    OX0403's own suggestion text ("...or clone it") and rustc's
    "help: consider cloning" recommend. The frontier subject took that
    route and strict-failed in both Oxide arms, which made the class
    unable to distinguish a model that misunderstood ownership from one
    that followed the compiler's advice.

    The rewrite prints before and after the loop on the accumulator
    itself. These tests pin that the ambiguity is closed.
    """

    def _p17(self, arm: str) -> dict:
        return next(r for r in PROBES if r["id"] == "p17" and r["arm"] == arm)

    def test_accumulator_is_only_read_outside_the_loop(self):
        """No print inside the loop body -- that is what let the original
        be satisfied by a clone."""
        for arm in ("oxide", "explicit"):
            body = self._p17(arm)["fix"]
            loop = body.split("for ")[1].split("}")[0]
            assert "print" not in loop, f"{arm}: p17 fix prints inside the loop again"

    @pytest.mark.parametrize("arm", ["oxide", "explicit"])
    def test_clone_repair_compiles_but_fails_strict(self, arm):
        """The clone route must remain compilable -- it is legal code, and
        pretending otherwise would test the wrong thing -- while failing
        strict because it does not accumulate."""
        record = self._p17(arm)
        read = "&acc" if arm == "explicit" else "acc"
        clone_repair = record["broken"].replace(
            "let grown = push(acc, i)", f"let grown = push(clone({read}), i)"
        )
        if arm == "explicit":
            clone_repair = clone_repair.replace("drop grown", "drop grown")
        result = probe.score(record, clone_repair)
        assert result["strict"] is False, "clone repair must not score strict"
        assert result["compiled"] is True, (
            "clone repair must still COMPILE -- if it fails to compile the "
            "probe is testing syntax, not the accumulate-vs-clone choice"
        )

    @pytest.mark.parametrize("arm", ["oxide", "explicit", "rust"])
    def test_reference_fix_is_the_accumulating_one(self, arm):
        record = self._p17(arm)
        result = probe.score(record, record["fix"])
        assert result["strict"] is True
        assert record["expected_stdout"] == "10\n13\n", (
            "the before/after pair is what makes accumulation observable"
        )


class TestDegenerateRequiresCompilation:
    """`degenerate` must mean "runs, and silently does the wrong thing".

    This was previously derived as `lenient and not strict`, which is not
    the same: `lenient` requires only that the submission parses and
    carries no ownership code, so it also counts repairs that traded an
    ownership error for a TYPE error. Those never run. The conflation
    overstated the reported rate by up to 60 points and inverted the
    ranking between arms -- qwen's Oxide arms were reported at 60%/68%
    against Rust's 3% when the true figures are 0%/0% against 3%.
    """

    def _p(self, arm: str) -> dict:
        return next(r for r in PROBES if r["id"] == "p01" and r["arm"] == arm)

    @pytest.mark.parametrize("arm", ["oxide", "explicit", "rust"])
    def test_a_type_error_is_not_degenerate(self, arm):
        """The exact shape that caused the error: ownership code gone,
        replaced by a type error. Lenient may pass; degenerate must not."""
        record = self._p(arm)
        broken = record["broken"]
        # `.clone()` is Rust method syntax; Oxide has only `clone(x)`.
        submitted = (
            broken.replace("let b = a", "let b = a.clone()")
            if arm != "rust" else broken.replace("let b = a;", "let b = a.qqq();")
        )
        result = probe.score(record, submitted)
        assert result["compiled"] is False
        assert result["degenerate"] is False, (
            "a submission that does not compile cannot be a degenerate "
            "repair -- it never runs"
        )

    @pytest.mark.parametrize("arm", ["oxide", "explicit", "rust"])
    def test_the_reference_fix_is_not_degenerate(self, arm):
        record = self._p(arm)
        result = probe.score(record, record["fix"])
        assert result["strict"] is True
        assert result["degenerate"] is False

    def test_degenerate_implies_compiled(self):
        """Structural: no scoring path may report degenerate without
        compilation."""
        for arm in ("oxide", "explicit", "rust"):
            record = self._p(arm)
            for candidate in ("", "!!! junk !!!", record["broken"], record["fix"]):
                result = probe.score(record, candidate)
                if result["degenerate"]:
                    assert result["compiled"], (arm, candidate[:40])
