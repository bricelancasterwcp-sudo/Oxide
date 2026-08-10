# DeepSeek-Coder-V2-Lite ownership probe — capability-window test

**Date:** 2026-08-09
**Design:** 20 classes × 3 arms × 10 seeds = 600 repairs. Subject:
`deepseek-coder-v2:16b-lite-instruct-q5_K_M`, served via llama.cpp
(`bin/llama-server`, `-ngl 99 -c 8192`, no grammar constraint on any arm —
see the Results footnote and Provenance for what that means for
comparability). This is the fifth point on the capability-window curve; its
current, correct reference figures are the post-§53 (method syntax) and
post-§54 (`mut` accepted) measurements in the README's "Where the evidence
stands" section and `eval/results/mut-fix/REPORT.md`. (The addendum table
in `eval/results/ownership-probe-10seed/REPORT.md` predates both fixes and
gives different, superseded figures for the same three subjects — e.g.
qwen at 25.5/15.5/89.0, delta +10.0 — so it is not the source for the table
below and would contradict it if read as one.) The pre-registration below
was written before the model was downloaded.

## The pre-registration — quoted verbatim, before any results

> **If** DeepSeek's `rust` arm scores **above qwen's 89.0**, **then** its
> `oxide − explicit` delta scores **below qwen's +59.0**.
>
> **If** the `rust` arm scores **at or below 89.0**, this run does not test
> the window and is reported **INCONCLUSIVE** — not reinterpreted.

## Results

| subject | rust | oxide | explicit | delta |
|---|---|---|---|---|
| granite-code-8b | 73.0 | 20.5 | 11.0 | +9.5 |
| codegemma-7b | 84.5 | 46.5 | 11.5 | +35.0 |
| qwen2.5-coder-7b | 89.0 | 73.0 | 14.0 | **+59.0** |
| Claude Opus 5 | 100 | 92 | 92 | 0.0 |
| **DeepSeek-Coder-V2-Lite** | **82.0** | 21.5<sup>†</sup> | 7.5<sup>†</sup> | not computed — see verdict |

<sup>†</sup> **Not comparable to the four reference rows above.** The
reference `oxide`/`explicit` figures were produced under
grammar-constrained decoding for those two arms ("same corpus, same
grammars, same seeds" — `eval/results/mut-fix/REPORT.md`). This run used
**no grammar on any arm** (see Provenance): `client_factory` supplied a
plain `LlamaCppClient` with no grammar set, for every arm, including
`oxide` and `explicit`. Corroborating evidence: all 200/200 of this run's
raw `oxide` outputs and all 200/200 of its `explicit` outputs contain
markdown code fences, which `eval/grammar/oxide.gbnf` cannot emit; this
run's `oxide` arm parses 76/200 against mut-fix qwen's grammar-constrained
199/200. The **`rust` column is comparable** — `rust` is never
grammar-constrained under either configuration (`eval/driver.py`: "the rust
arm is NEVER constrained"), so 82.0 vs. 89.0 is like-for-like. See
"What this run does not show" below for why this is a second, independent
reason the run does not test the window.

DeepSeek raw counts (out of 200 per arm, 10 seeds × 20 classes):

| arm | strict pass | rate |
|---|---|---|
| rust | 164/200 | **82.0%** |
| oxide | 43/200 | 21.5% |
| explicit | 15/200 | 7.5% |

## Verdict: INCONCLUSIVE

Applying Step 5 of the pre-registration in order: the `rust` arm is read
**first**. DeepSeek's `rust` arm scored **82.0**, which is **at or below**
qwen's 89.0. By the pre-registration's own second clause, this run **does
not test the window** and is reported **INCONCLUSIVE**.

Per the pre-registration's explicit instruction, the `oxide − explicit`
delta is **deliberately not computed as a test of anything** and is not
narrated as evidence for or against the capability window. (The two raw
rates are reported above in full for transparency — 21.5% and 7.5% — so
nothing is hidden; the arithmetic difference between them is simply not
being treated as a measurement, because the precondition that would make it
one was not met.) DeepSeek was not established as stronger than qwen on
this probe's `rust` arm, so the capability window this experiment set out
to stress-test was not, in fact, tested by this run.

This is a successful experiment in the sense that matters for this
project: the pre-registration bound the analysis before the `rust` number
was known, and the number came back on the side that halts the analysis.
That is not a disappointing outcome to be reinterpreted around — it is the
discipline working as designed.

**The verdict survives the grammar difference noted above.** The gate is
decided entirely by the `rust` arm (Step 5 reads it first, alone), and
`rust` is never grammar-constrained in this run or in any of the four
reference runs — it is the one arm where "no grammar constraint" was
already true on both sides. 82.0 vs. 89.0 is therefore a like-for-like
comparison despite `oxide`/`explicit` running unconstrained here against
constrained reference figures, and INCONCLUSIVE stands regardless of that
difference.

## Quantization caveat

This subject ran at **q5_K_M**, while the other three families in the
reference table ran at **q8_0**. The reason is hardware, not choice: this
model's `q8_0` GGUF is **16.70 GB**, and the card has **16.30 GB** of VRAM
— it does not fit. `q5_K_M` was the fallback path named in this task's
pre-registration for exactly this contingency, and it was used.

Quantization is a capability reduction. On a non-monotonic curve, the
direction of the bias this introduces depends on which side of the peak
the subject sits. If DeepSeek sits above the peak (post-qwen, moving toward
the Opus end), quantizing it moves its measured position back **toward**
qwen — i.e., **against** the direction the pre-registration predicts —
which would make a confirmation of the falling-delta prediction
conservative (harder to obtain by the bias, not easier). If DeepSeek sits
at or below the peak, the same quantization could bias in the other
direction.

**This is an argument, not a measurement.** No second quantization was run
to isolate the effect, and none should be inferred from this report. The
clean settlement — running both `q5_K_M` and `q8_0` (or a smaller model
that fits `q8_0` natively) and comparing — was scoped out of this task.

The most direct place this caveat bears on *this* run is the `rust` arm
itself, not the delta discussed above. `q5_K_M` is a capability reduction,
and it is a plausible — **not established** — candidate explanation for
why `rust` landed at 82.0 rather than clearing qwen's 89.0: quantization
may have cost DeepSeek some of the headroom it would have had at `q8_0`.
The equally plausible alternative is that DeepSeek's `rust` arm would score
82.0 at full precision too — i.e., that it simply is not stronger than
qwen at this task, independent of quantization. This report contains no
measurement that distinguishes those two explanations, and it does not
adjudicate between them; both stay open, and neither should be treated as
the default reading. This is exactly why the two-quantization control
named above is the natural next step rather than an optional one.

## What this run does not show

- **One MoE subject cannot separate "MoE" from "stronger" from "different
  pretraining mix."** DeepSeek-Coder-V2-Lite differs from the other four
  subjects on multiple axes at once (architecture, training data, scale
  routing); this run cannot attribute its 82.0 `rust` score, or the
  oxide/explicit spread, to any one of those axes.
- **The capability window remains descriptive over five points, not
  causal.** Five (subject, rust-score, delta) pairs are not enough to fit
  a causal model of *why* the delta shrinks as capability rises; the
  window is still an observed pattern across independently-trained models,
  not a mechanism.
- **The three-family results are untouched by this run.** Nothing under
  `eval/results/ownership-probe-10seed/` or the other established results
  directories was modified. This adds a fifth, separate data point; it
  supersedes nothing.
- Because the verdict is INCONCLUSIVE, this run in particular does not
  confirm, falsify, or otherwise bear on the capability-window prediction
  at all — it only establishes that DeepSeek-Coder-V2-Lite-q5_K_M's `rust`
  arm, at these settings, does not clear qwen's `rust` arm.
- **A second, independent reason this run does not test the window:** even
  had the `rust` gate cleared 89.0, the `oxide − explicit` delta it would
  have gated would not have been comparable to the four reference deltas.
  This run's `oxide` and `explicit` arms were unconstrained (no grammar),
  while the reference figures for those same two arms were produced under
  grammar-constrained decoding. The `rust` arm alone is unaffected — it is
  never grammar-constrained in any of the five runs — which is why the
  INCONCLUSIVE verdict itself is unaffected by this issue. But a future run
  that clears the `rust` gate on an unconstrained setup still cannot
  compute a delta against these particular reference figures without first
  either constraining `oxide`/`explicit` to match, or re-deriving
  unconstrained reference figures for the other four subjects.

## Method note: VRAM headroom

This card has ~16.30 GB of VRAM and this configuration measured roughly
1.0 GB of free headroom once the model was resident — the tightest margin
of any subject run for this project so far. The first launch attempt of
this campaign died with `ErrorOutOfDeviceMemory`: `ollama` had silently
loaded `qwen2.5-coder:7b-instruct-q8_0` in the background, holding 9,590
MiB, so `llama-server` saw only 4,947 MiB free and aborted. `ollama` was
stopped and the server was relaunched cleanly with `ollama ps` confirmed
empty; that clean instance is the one this campaign ran against
throughout, and `ollama ps` was empty for the full duration of the run.

## Provenance

| field | value |
|---|---|
| backend | llama.cpp (`bin/llama-server`) |
| tag | `deepseek-coder-v2:16b-lite-instruct-q5_K_M` |
| ollama digest | `6065d4880bf9` |
| quantization | `q5_K_M` |
| num_ctx (pinned) | 8192 |
| server `n_ctx` (measured at preflight) | 8192 |
| llama.cpp build | `b1-4988f6e` |
| grammar | none, on **every** arm of this run. For `rust` this matches all five subjects (never grammar-constrained anywhere). For `oxide`/`explicit` this does **not** match the three reference families, which ran those two arms grammar-constrained — see the Results footnote. |
| model path | `/mnt/extra/ollama-models/blobs/sha256-bc286970a24072cf23a4c905f28adb9f6a28c71743b07790185275a86dc72406` |

Full machine-readable provenance: `provenance.json`. Raw per-cell results:
`{oxide,explicit,rust}-s{1..10}/probe_results.jsonl`.
