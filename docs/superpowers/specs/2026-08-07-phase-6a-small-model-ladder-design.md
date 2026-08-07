# Phase 6a — Small-Model Capability Ladder (design)

**Date:** 2026-08-07
**Status:** design, pending approval. On approval this is transcribed into
`SPEC.md` as **Part X — Phase 6a Contract** (normative), per project
convention that contracts become binding only as numbered SPEC parts.

## 1. Motivation

Run `demo-fable-0shot` saturated: 60/60 first-attempt passes in all three
arms. The per-error-code distribution that gates the v0.3 value-semantics
inversion (SPEC §32) is therefore **empty**. The eval report named two ways
to create discrimination; this phase takes the first — drop subject
capability — because it is the operating point at which the
ownership-bookkeeping-in-the-compiler hypothesis actually predicts a gap,
and because a base-model baseline is a prerequisite for attributing any
future LoRA lift.

## 2. Scope

**In:** inference-only evaluation of local small models across the existing
20-task corpus and three existing arms.

**Out (explicitly):** fine-tuning, data-factory construction, new tasks, a
`hard2` tier, any language or pipeline change. `src/` is not modified.
`eval/harness.py` is not modified.

**Success:** a populated per-error-code distribution and a defensible
pass@1 curve with variance estimates — *whatever direction they point*. A
result showing no Oxide advantage is a successful run of this phase.

## 3. Pre-registered analysis plan

Recorded before any generation, because the thesis under test is the
author's own.

**Primary comparison.** Oxide vs explicit-Oxide first-attempt pass
(pass@1) at each capability point, read as the **paired-by-task delta**
defined under *Statistics* below. These two arms are matched on novelty —
both are languages the subject saw zero times in pretraining, both taught
only by a card of comparable length — and differ only in whether ownership
is implicit or written out. This isolates the thesis claim.

**Secondary.** Repair lift (final pass rate − first-attempt pass rate) per
arm, which measures whether an arm's diagnostics teach; and mean
attempts-to-pass.

**Reference, not headline.** The Rust arm carries a large, unquantified
pretraining-exposure advantage at this scale. Rust numbers are reported as
a descriptive reference point with that advantage stated inline. Any
Oxide-vs-Rust difference at 0.5B/1.5B is **not** evidence about language
design and must not be reported as such.

**Statistics.** Tasks are a fixed corpus, not a sample; generalization
beyond the corpus is not claimed.

The primary statistic is the **paired-by-task** delta: for each task,
subtract explicit-Oxide's pass rate (over 5 seeds) from Oxide's, then
average those 20 per-task differences.

**Precisely what pairing buys.** With every task present in both arms,
the paired mean difference is *algebraically identical* to the difference
of marginal arm rates. Pairing does **not** change the point estimate.
What it changes is the **interval**: the paired standard error is
`SD(per-task differences) / √20`, which shrinks in proportion to how
strongly the two arms' per-task performance correlates. That correlation
will be high — a task hard in Oxide is hard in explicit-Oxide — so the
paired SE is expected to be roughly half the unpaired one. The delta is
therefore reported with its **paired SE**, and quoting the delta without
it is prohibited. (The point estimates diverge only when a task is
missing from one arm, which should not occur in a complete grid.)

Pooling all 100 task×seed trials into a single binomial CI is likewise
**prohibited** — it treats fixed tasks as random draws and understates
the interval. Reported alongside: per-task pass counts (so task-level
effects stay visible) and the across-seed SE (n=5) as a sampling-noise
check.

**Power — a pre-registered limit, not a finding.** With 20 tasks and 5
seeds, a per-seed pass rate moves in 5-point steps. At p≈0.5 (worst case
for variance) the per-seed SD is ≈11pp and the across-seed SE of the mean
is ≈5pp, so an *unpaired* comparison needs a ~10pp delta — two whole
tasks — to clear two SE. Pairing by task roughly halves that, to ~5pp.
**This design cannot detect a true effect smaller than about 5
percentage points.** That is a property of a 20-task corpus, not evidence
of absence, and every report from this phase must say so.

**Directional predictions.** Stated in advance, on the paired-by-task
pass@1 delta (Oxide − explicit-Oxide), as an exhaustive and
non-overlapping partition:

| Paired delta | Pre-registered reading |
|---|---|
| **≥ +5pp** | Consistent with the implicit-linearity ergonomics claim. Strengthened further if the delta widens monotonically as capability drops. |
| **−5pp … +5pp** | **No detectable difference.** Below this design's resolution; supports neither direction and must not be reported as either. |
| **≤ −5pp** | Disconfirming: implicit linearity *costs* accuracy at small scale. Part VI's ownership-default inversion should be revisited on that basis. |

Mixed signs across capability points (e.g. positive at 0.5B, negative at
7B) are reported as such and read as **no coherent directional effect** —
not as selective support from whichever rung agrees.

The ±5pp band is a floor imposed by 20 tasks, chosen from the power
calculation above rather than from taste. It is not a claim that 4pp
would be scientifically uninteresting. Resolving effects below it
requires a larger corpus; that is a Phase 6b decision, and the band must
not be renegotiated after seeing results.

## 4. Pinned run parameters

| Parameter | Value |
|---|---|
| Models | `qwen2.5-coder` **instruct**, 0.5B / 1.5B / 7B |
| Quantization | uniform `q8_0` across the ladder |
| Backend | Ollama HTTP (`http://localhost:11434`), version recorded |
| Temperature | 0.8 |
| top_p | 0.95 |
| `num_predict` (max gen tokens) | 2048 |
| Seeds | 1, 2, 3, 4, 5 |
| Shot conditions | 0 and 3 |
| Attempt cap | 4 (existing `MAX_ATTEMPTS`) |
| Exec timeout | 10s (existing) |

Base (non-instruct) variants are prohibited: they do not follow the output
contract, and the resulting failures would measure format compliance
rather than language competence.

Quantization is held constant so the capability curve is not confounded
with precision. Exact tags **and digests** are recorded in the run
manifest at preflight.

Temperature is deliberately non-zero. At temperature 0 all five seeds
produce identical output and the variance estimate is vacuous.

`num_predict` is **load-bearing, not a nicety.** Degenerate repetition
loops are the most characteristic small-model failure mode. Without a
token cap, a looping 0.5B generation runs until the HTTP timeout and gets
classified as a *transport* error — so the run would abort on precisely
the behavior the phase exists to measure, and the grid would end up
systematically missing its worst-performing cells. With the cap, runaway
generation terminates as a **model** result: the truncated output fails
to compile and counts as a real failed attempt. 2048 tokens is generous
against reference solutions of 50–150 tokens. Truncation (`done_reason ==
"length"`) is recorded per attempt so its frequency is auditable.

**Grid:** 3 models × 2 shot conditions × 5 seeds × 20 tasks × 3 arms =
**1800 sessions**, at most **7200 generations**. Estimated 8–14h wall
clock; small models exhaust the attempt cap more often than they pass
early, so the worst case is close to the expected case.

## 5. Run identity and layout

`harness._claim_session` locks on `(run_id, task_id, arm)` and the pinned
triple schema carries no model, seed, or shot field. Therefore each
(model, shots, seed) combination **must** occupy its own `run_id`. This is
what makes the phase additive: the existing session, triple, and report
layers work unchanged.

```
run_id  ::=  6a-<model_slug>-<shots>shot-s<seed>
             e.g.  6a-qwen1_5b-0shot-s3
model_slug ::= qwen0_5b | qwen1_5b | qwen7b
```

30 run ids × 60 sessions each.

```
eval/results/<run_id>/
  manifest.json     # pinned params, ollama version, model digests, start/end
  triples.jsonl     # written by the existing harness Session
  cells.jsonl       # appended per completed session (resume ledger)
  raw/<task>.<arm>.<attempt>.txt   # verbatim model output, pre-extraction
  .sessions/        # existing O_EXCL locks
eval/results/6a-rollup/
  grid.json         # all cells, all runs
  REPORT.md
```

`cells.jsonl` record:

```json
{"task": "t01", "arm": "oxide", "attempts": 2,
 "first_compiled": false, "first_passed": false, "final_passed": true,
 "attempts_to_pass": 2, "tokens_in": 1531, "tokens_out": 88, "ms": 4210,
 "contract_compliant": [false, true], "truncated": [false, false]}
```

`contract_compliant` and `truncated` are one boolean **per attempt**, in
attempt order; each length always equals `attempts`. `truncated` records
`done_reason == "length"` so runaway-generation frequency is auditable
per arm and per model. `tokens_in`/`tokens_out`/`ms` are summed across
the session's attempts.

## 6. Module contracts

All additive, under `eval/`. No edits to `harness.py`.

### 6.1 `eval/models.py`

```python
class ModelClient(Protocol):
    def generate(self, prompt: str, *, seed: int) -> Generation: ...

@dataclass(frozen=True)
class Generation:
    text: str
    tokens_in: int
    tokens_out: int
    ms: int
    truncated: bool          # done_reason == "length"

class OllamaClient:
    def __init__(self, model: str, *, temperature: float, top_p: float,
                 host: str = "http://localhost:11434",
                 timeout_s: int = 120, retries: int = 3) -> None: ...
    def preflight(self) -> dict: ...   # version + model digest; raises if absent
```

Protocol-first so a future API-backed client drops in without touching the
driver. Uses `urllib` from the stdlib — the eval venv stays
dependency-free (Python 3.14 has no clean PyTorch story, and none is
needed for inference through Ollama).

### 6.2 `eval/extract.py`

```python
@dataclass(frozen=True)
class Extraction:
    source: str
    contract_compliant: bool

def extract(raw: str) -> Extraction: ...
```

Pinned, arm-identical, deliberately **not** syntax-aware:

1. Normalize line endings to `\n`.
2. If the text contains a ``` fence, take the content of the **first**
   fenced block, dropping the fence lines and any language tag.
3. If that fence is never closed — the characteristic shape of a
   generation cut off at `num_predict` — take everything after the
   opener. Salvaging it is arm-neutral, and the truncated source then
   fails to compile on its own merits rather than being discarded.
4. Otherwise use the text with leading/trailing blank lines stripped.
5. `contract_compliant = (raw.strip() == source.strip())`. Note this
   makes empty output trivially "compliant"; it is a formatting metric
   only, and empty submissions still fail compilation as model failures.

No prose-stripping heuristics. Unfenced commentary simply fails to
compile, which is honest and arm-neutral; any smarter recovery risks
differentially favoring one arm's syntax. The raw output is always
persisted, so the strict-verbatim number stays recoverable post-hoc.

### 6.3 `eval/repair.py`

```python
def build_repair_prompt(
    arm: str,
    source: str,
    verdict: dict,
    *,
    task_id: str,
    shots: int = 0,
    tasks_path: str | Path | None = None,
) -> str: ...
```

A repair prompt is **the arm's own initial prompt with its tail
swapped**: call `harness.build_prompt(arm, task_id, shots=shots,
tasks_path=tasks_path)`, strip the trailing `harness.OUTPUT_CONTRACT`
constant, append the attempt block.

```
<the arm's full initial prompt, minus its output contract>

The program below was rejected. Fix it.

Program:
<source>

Diagnostics:
<rendered>

Reply with ONLY the complete corrected program source, no fences, no commentary.
```

The carried-over lead is the language card (oxide / explicit) or the
pinned Rust preamble, plus any few-shot examples, plus the task
statement — exactly what the arm was given on attempt 1. Reusing the
frozen harness rather than reconstructing a lead makes the symmetry
structural instead of asserted. Stripping a *known constant suffix* is
deterministic and testable; its absence raises
`repair.RepairPromptError`, so a frozen-harness change fails loudly
instead of silently emitting a prompt with a stale contract.

**Why the lead is carried.** Every generation is a standalone HTTP call
with no conversation history, so anything the repair prompt omits is
gone. Measured on the earlier template (program + diagnostics + fix
instruction only):

| Arm | Initial | Repair | Retained |
|---|---|---|---|
| oxide (0-shot) | 5305 ch | 271 | **5.1%** |
| explicit (0-shot) | 5593 ch | 271 | **4.8%** |
| rust (0-shot) | 245 ch | 271 | **110.6%** |

Rust *gained* context on repair — it lives in the model's weights and
its preamble is one line — while the Oxide arms lost 95% of theirs, and
the language card is the only place Oxide syntax ever appears.
Worse, the task statement appeared in **no** repair prompt, so after a
runtime failure the model was told its output was wrong with no
statement of what it should have produced; it could only guess.

That asymmetry would have turned §7's repair-lift *secondary* metric
("whether an arm's diagnostics teach") into a measure of card recall
for the Oxide arms. The *primary* pass@1 metric is first-attempt-only
and was never at stake. The change was decided by the project owner
before the grid ran, blind to any results, as the pre-registration
requires.

Diagnostics render as `line:col: CODE: message`, notes indented two
spaces, then `suggestion: <text>` when non-empty. Oxide arms therefore
supply OX codes with suggestions; the Rust arm supplies rustc's full help
text verbatim (SPEC §45 already folds rustc's children into `message`).
Giving each arm its strongest native diagnostics is the fair form of the
test. The attempt block's *structure* stays arm-identical; its *content*,
and the lead above it, stay arm-native.

**Runtime failure** (compiled, wrong stdout) has no diagnostics. The
`Diagnostics:` block is replaced by:

```
The program compiled and ran, but produced incorrect output.
Its output was:
<stdout>
```

The task's `expected_stdout` is **never** disclosed. Disclosing it would
let a weak model pass by hard-coding a print of the expected string, which
would silently corrupt the headline metric. It is not a parameter of
`build_repair_prompt`, and `harness.build_prompt` does not include it
either — the carried-over task statement says what the program must
produce without quoting the answer.

No transcript accumulation: a repair prompt carries the arm's fixed
initial context plus exactly one program and one verdict. Prior attempts
are never appended. Growing transcripts would confound repair skill with
long-context ability, which 0.5B lacks; a fixed-size prompt does not.

### 6.4 `eval/driver.py`

Preflight (whole grid, before any generation): Ollama reachable, all three
tags present, `rustc` invocable, corpus loads, shots available for every
arm at 3-shot. Fail fast, listing everything missing.

Preflight reads `/api/tags` and records each model's `digest`,
`details.quantization_level`, and `details.context_length` into the
manifest. It **asserts `quantization_level == "Q8_0"` for all three
models** — this is what actually enforces §4's uniform-quantization
control, rather than trusting that the right tag was pulled. (The
`qwen2.5-coder:1.5b` currently on this machine is Q4_K_M and must be
rejected by name.)

Per run id: health-check Ollama (poll until healthy, cap 10 min) → write
`manifest.json` → 60 sessions → mark the run complete. On persistent
transport failure, record the cause in the manifest, abort this run id,
and continue with the next; three consecutive aborts stop the grid with a
non-zero exit (§7).

Per session: `harness.build_prompt(arm, task, shots)` → `generate` →
`extract` → `session.submit` → on failure `build_repair_prompt` →
generate → … up to the cap. Append raw output per attempt; append one
`cells.jsonl` record per completed session.

**Resume granularity is the whole `run_id`.** A run dir whose
`cells.jsonl` is short of 60 records is deleted and redone (~minutes).
Partial-state surgery across O_EXCL locks and half-written triples is more
bug-prone than the rerun costs.

CLI selects grid subsets so the 8–14h run can be split across sittings and
re-entered safely:

```
python -m eval.driver --models qwen1_5b,qwen7b --shots 0,3 --seeds 1-5
python -m eval.driver --preflight-only
```

Completed run ids are skipped on re-entry; the default is the full grid.

### 6.5 `eval/rollup.py`

Aggregates the 30 run dirs into `grid.json` + `REPORT.md`.

**Primary readout:** the paired-by-task Oxide − explicit-Oxide pass@1
delta per (model, shots), classified against the §3 partition
(≥+5pp / −5…+5pp / ≤−5pp) with the band printed alongside the number, so
an inconclusive result cannot be read as a positive one.

Also reported: pass@1 per (model, arm, shots) with across-seed SE, final
pass rate, repair lift, mean attempts-to-pass, per-code histograms (**the
v0.3 gate deliverable**), tokens and wall-clock per cell, prompt token
counts (the §9 asymmetry), and contract-compliance and truncation rates
as their own metrics.

The rollup refuses to emit a report for an incomplete grid unless passed
`--partial`, which stamps the missing run ids into `REPORT.md`. A grid
silently missing aborted runs is the failure mode most likely to be
misread as a finished result.

## 7. Error handling

The governing rule: **infrastructure failures must never be recorded as
model failures, and model failures must never be classified as
infrastructure.** The first biases every arm toward the null; the second
silently drops the worst-performing cells. Both corrupt the primary
comparison, in opposite directions.

| Condition | Classification | Behavior |
|---|---|---|
| Ollama down / tag missing at start | infrastructure | preflight abort, before any generation |
| Transport error or HTTP timeout | infrastructure | 3 retries with backoff, then **abort this `run_id`** (below) |
| Generation hits `num_predict` | **model** | truncated source submitted; real failed attempt; `truncated: true` logged |
| Empty or malformed generation | **model** | real failure, consumes an attempt |
| Non-UTF8 source | **model** | existing `_unencodable_source_verdict` |
| Program nontermination | **model** | existing `timeout 10` |

**Run-id-scoped abort.** A persistent transport failure aborts only the
current `run_id` — at most 60 sessions, ~20–30 min — records the cause in
that run's `manifest.json`, and the driver proceeds to the next run id.
Resume later redoes the aborted run dir whole. The grid degrades in
throughput instead of dying overnight, and no partial-state surgery is
needed.

Cells are **never** individually quarantined or excluded. Under memory
pressure (7B-q8 is ~8GB on a 16GB card) infrastructure failures would
correlate with long generations on hard tasks, so per-cell exclusion is
non-random and would bias pass rates upward. Whole-run redo preserves the
no-non-random-exclusion property.

**Health-check wait, between run ids only.** Before starting each
`run_id`, poll Ollama until healthy, capped at 10 minutes. This survives
transient restarts with zero lost work. It is deliberately **not** applied
mid-session: mid-session resumption would interact with the O_EXCL locks
and half-written triples that §6.4 exists to avoid.

**Consecutive-abort backstop.** Three consecutive `run_id` aborts stop
the whole grid with a non-zero exit. Without it, a systematically broken
configuration (7B OOM, a corrupt tag) would burn silently through every
remaining run id and leave a grid that looks complete but is not.

## 8. Test plan

New `tests/test_6a.py`, plus the existing 717 staying green (nothing in
`harness.py` or `src/` is touched).

1. **Extraction** — fenced, fenced-with-language-tag, multiple fences
   (first wins), unfenced, empty, whitespace-only, CRLF; and
   `contract_compliant` correct in each.
2. **Repair prompt** — compile-failure shape; runtime-failure shape;
   the arm's full initial prompt (lead, shots, task statement) is
   carried and its output contract dropped; a moved harness tail raises;
   **asserts `expected_stdout` never appears in any repair prompt** —
   structurally (not a parameter of `build_repair_prompt` nor of
   `harness.build_prompt`) and empirically, over every real corpus task
   x arm x shot count, where neither the whole expected output nor any
   single line of it may appear as a line of the prompt; arm-identical
   attempt-block structure across all three arms; rustc help text
   preserved verbatim.
3. **Model client** — protocol conformance against a stub; retry-then-
   abort on transport error; preflight raises on a missing tag;
   `num_predict` passed through; `done_reason == "length"` surfaced as
   `truncated`.
4. **Failure classification** (§7's governing rule, both directions) — a
   generation truncated at `num_predict` is submitted as a **model**
   failure and does **not** abort; an HTTP timeout **does** abort the run
   id and is **never** written to `cells.jsonl` as a failed attempt.
5. **Driver** — stub-model end-to-end over a 2-task subset; attempt cap
   respected; resume deletes and redoes a short run dir; raw outputs
   persisted per attempt; run-id abort continues to the next run id;
   three consecutive aborts stop the grid non-zero; health-check waits
   then proceeds when Ollama returns.
6. **Rollup** — paired-by-task delta computed per §3 on synthetic run
   dirs; **paired SE** = `SD(per-task differences)/√n`, asserted smaller
   than the unpaired SE on a positively-correlated fixture (this, not a
   point-estimate difference, is what pairing actually buys); the two
   estimators asserted *equal* on a balanced fixture and *divergent* only
   when a task is missing from one arm; partition classification correct
   at the ±5pp boundaries; pooled-binomial CI absent; incomplete grid
   refused without `--partial`.
7. **Live smoke** — one task, 0.5B. Carries `@pytest.mark.live`, which
   `pytest.ini` deselects by default (`addopts = -m "not live"`), so a
   full-suite run never burns a real generation; run it with
   `pytest -m live`. It still skips cleanly when the daemon is down or
   the model is not pulled.

## 9. Risks

- **Floor effect.** 0.5B may score 0 across all arms at both shot counts,
  making the smallest rung uninformative. Mitigated by the 3-shot
  condition and by 1.5B/7B carrying the curve. Accepted.
- **Card length vs context.** Measured prompt sizes (t01, ~4 chars/token):

  | Arm | 0-shot | 3-shot |
  |---|---|---|
  | oxide | ~1326 tok | ~1531 tok |
  | explicit | ~1398 tok | ~1613 tok |
  | rust | ~61 tok | ~251 tok |

  The Oxide arms carry a **~22× larger prompt** than Rust at 0-shot. All
  fit Qwen2.5-Coder's context window, so this is not a truncation risk —
  but attention dilution over a 1.3k-token card is a real burden at 0.5B
  that the one-line Rust preamble does not pay. This asymmetry favors
  Rust, compounds with the pretraining-exposure advantage in the same
  direction, and is a further reason Oxide-vs-Rust is not the headline.
  Per-cell prompt token counts are logged and the comparison is restated
  in the report rather than hand-waved.
- **Ollama chat templating** may differ across model sizes. Templates are
  recorded in the manifest and confirmed identical in family before the
  run.

## 10. Deliverables

1. `eval/models.py`, `extract.py`, `repair.py`, `driver.py`, `rollup.py`
2. `tests/test_6a.py`
3. SPEC.md Part X (this design, transcribed as normative)
4. A completed grid under `eval/results/` + `6a-rollup/REPORT.md`
5. Memory update recording phase status and the run's outcome
