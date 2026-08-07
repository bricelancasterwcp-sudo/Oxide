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

**Primary comparison.** Oxide vs explicit-Oxide first-attempt pass rate
(pass@1) at each capability point. These two arms are matched on novelty —
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

**Directional predictions.** If the hypothesis holds, the Oxide −
explicit-Oxide pass@1 delta is ≥ 0 and widens as capability drops. If the
delta is ≤ 0 at every point, the implicit-linearity ergonomics claim is
not supported at small scale, and Part VI's inversion should be revisited
on that basis.

**Statistics.** Tasks are a fixed corpus, not a sample; generalization
beyond the corpus is not claimed. The primary interval is the across-seed
standard error (n=5) of the per-seed pass rate. Pooling all 100
task×seed trials into a single binomial CI is **prohibited** — it treats
fixed tasks as random draws and understates the interval. Per-task pass
counts are reported alongside so task-level effects stay visible.

## 4. Pinned run parameters

| Parameter | Value |
|---|---|
| Models | `qwen2.5-coder` **instruct**, 0.5B / 1.5B / 7B |
| Quantization | uniform `q8_0` across the ladder |
| Backend | Ollama HTTP (`http://localhost:11434`), version recorded |
| Temperature | 0.8 |
| top_p | 0.95 |
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
 "contract_compliant": [false, true]}
```

`contract_compliant` is one boolean **per attempt**, in attempt order;
its length always equals `attempts`. `tokens_in`/`tokens_out`/`ms` are
summed across the session's attempts.

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
3. Otherwise use the text with leading/trailing blank lines stripped.
4. `contract_compliant = (raw.strip() == source.strip())`.

No prose-stripping heuristics. Unfenced commentary simply fails to
compile, which is honest and arm-neutral; any smarter recovery risks
differentially favoring one arm's syntax. The raw output is always
persisted, so the strict-verbatim number stays recoverable post-hoc.

### 6.3 `eval/repair.py`

```python
def build_repair_prompt(arm: str, source: str, verdict: dict) -> str: ...
```

Arm-identical *structure*, arm-native *content*. Compile failure:

```
The program below was rejected. Fix it.

Program:
<source>

Diagnostics:
<rendered>

Reply with ONLY the complete corrected program source, no fences, no commentary.
```

Diagnostics render as `line:col: CODE: message`, notes indented two
spaces, then `suggestion: <text>` when non-empty. Oxide arms therefore
supply OX codes with suggestions; the Rust arm supplies rustc's full help
text verbatim (SPEC §45 already folds rustc's children into `message`).
Giving each arm its strongest native diagnostics is the fair form of the
test.

**Runtime failure** (compiled, wrong stdout) has no diagnostics. The
`Diagnostics:` block is replaced by:

```
The program compiled and ran, but produced incorrect output.
Its output was:
<stdout>
```

The task's `expected_stdout` is **never** disclosed. Disclosing it would
let a weak model pass by hard-coding a print of the expected string, which
would silently corrupt the headline metric. The task prompt already states
what the program must produce.

No transcript accumulation: each repair prompt contains one program and
one verdict. Growing transcripts would confound repair skill with
long-context ability, which 0.5B lacks.

### 6.4 `eval/driver.py`

Preflight (whole grid, before any generation): Ollama reachable, all three
tags present with digests, `rustc` invocable, corpus loads, shots
available for every arm at 3-shot. Fail fast, listing everything missing.

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

Aggregates the 30 run dirs into `grid.json` + `REPORT.md`: pass@1 per
(model, arm, shots) with across-seed SE, final pass rate, repair lift,
mean attempts-to-pass, per-code histograms (**the v0.3 gate
deliverable**), tokens and wall-clock per cell, and contract-compliance
rate reported as its own metric.

## 7. Error handling

| Condition | Behavior |
|---|---|
| Ollama down / tag missing | preflight abort, before any generation |
| Transport error, timeout | 3 retries with backoff, then **abort the run** |
| Empty or malformed generation | counts as a real failure, consumes an attempt |
| Non-UTF8 source | existing `_unencodable_source_verdict` |
| Program nontermination | existing `timeout 10` |

Transport failures must never be recorded as model failures. Doing so
would bias every arm toward the null and silently corrupt the primary
comparison. Aborting is the conservative choice.

## 8. Test plan

New `tests/test_6a.py`, plus the existing 717 staying green (nothing in
`harness.py` or `src/` is touched).

1. **Extraction** — fenced, fenced-with-language-tag, multiple fences
   (first wins), unfenced, empty, whitespace-only, CRLF; and
   `contract_compliant` correct in each.
2. **Repair prompt** — compile-failure shape; runtime-failure shape;
   **asserts `expected_stdout` never appears in any repair prompt**;
   arm-identical structure across all three arms; rustc help text
   preserved verbatim.
3. **Model client** — protocol conformance against a stub; retry-then-
   abort on transport error; preflight raises on a missing tag.
4. **Driver** — stub-model end-to-end over a 2-task subset; attempt cap
   respected; resume deletes and redoes a short run dir; raw outputs
   persisted per attempt.
5. **Rollup** — aggregates correctly on synthetic run dirs; across-seed
   SE computed as specified; pooled-binomial CI absent.
6. **Live smoke** — one task, 0.5B, marked so it can be deselected when
   Ollama is unavailable.

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
