# DeepSeek-Coder-V2-Lite ownership probe — capability-window test

**Date:** 2026-08-09
**Design:** 20 classes × 3 arms × 10 seeds = 600 repairs. Subject:
`deepseek-coder-v2:16b-lite-instruct-q5_K_M`, served via llama.cpp
(`bin/llama-server`, `-ngl 99 -c 8192`, no grammar constraint on any arm —
see the Results footnote and Provenance for what that means for
comparability). This is the fifth point on the capability-window curve.

**The four reference rows below come from two different sources, and they
are not interchangeable.**

- **The three local families** (`granite-code-8b`, `codegemma-7b`,
  `qwen2.5-coder-7b`): the post-§53 (method syntax) and post-§54 (`mut`
  accepted) measurements in the README's "Where the evidence stands"
  section and `eval/results/mut-fix/REPORT.md`. (The addendum table in
  `eval/results/ownership-probe-10seed/REPORT.md` predates both fixes and
  gives different, superseded figures for those same three subjects —
  e.g. qwen at 25.5/15.5/89.0, delta +10.0 — so it is not the source for
  the table below and would contradict it if read as one.)
- **Claude Opus 5**: `eval/results/ownership-probe-frontier/REPORT.md`,
  which is cited nowhere else in this report and appears in **neither**
  source named above. That run is commit `bce5c04` (2026-08-07 15:58) —
  **earlier the same day than §53 (`d5ac69a`, 20:44) and §54 (`d5be042`,
  23:35), so the Opus row is pre-§53 AND pre-§54.** The README is careful
  about this ("All **three local families** are post-`mut`"); this report
  previously dropped the qualifier and described all four reference rows
  as post-§53/post-§54, which is false for Opus. The Opus row also
  differs from every other row in *design*, not just in date — see the
  <sup>‡</sup> footnote under the table.

The pre-registration below was written before the model was downloaded.

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
| Claude Opus 5<sup>‡</sup> | 100 | 92 | 92 | 0.0 |
| **DeepSeek-Coder-V2-Lite** | **82.0** | 21.5<sup>†</sup> | 7.5<sup>†</sup> | not computed — see verdict |

<sup>†</sup> **Not comparable to the three LOCAL reference rows above.**
Those three families' `oxide`/`explicit` figures were produced under
grammar-constrained decoding for those two arms ("same corpus, same
grammars, same seeds" — `eval/results/mut-fix/REPORT.md`). This run used
**no grammar on any arm** (see Provenance): `client_factory` supplied a
plain `LlamaCppClient` with no grammar set, for every arm, including
`oxide` and `explicit`. Corroborating evidence: all 200/200 of this run's
raw `oxide` outputs and all 200/200 of its `explicit` outputs contain
markdown code fences, which **neither** `eval/grammar/oxide.gbnf` **nor**
`eval/grammar/explicit.gbnf` can emit — neither grammar contains a
backtick at all, and the `explicit` arm is the one governed by
`explicit.gbnf`; this run's `oxide` arm parses 76/200 against mut-fix
qwen's grammar-constrained 199/200. The **`rust` column is comparable** —
`rust` is never grammar-constrained under either configuration
(`eval/driver.py`: "the rust arm is NEVER constrained"), so 82.0 vs. 89.0
is like-for-like.

**This paragraph does not apply to the Opus row.** That run was
unconstrained on **every** arm — "No grammar constraint in any arm — the
frontier subject does not need it"
(`eval/results/ownership-probe-frontier/REPORT.md`, verbatim). Grammar is
therefore not what separates this run from that one; the <sup>‡</sup>
footnote below records what does. See "What this run does not show" for
why the grammar difference is a second, independent reason the run does
not test the window.

<sup>‡</sup> **The Opus row is a different experiment, not a fifth
like-for-like measurement**, and it is annotated here because nothing else
in this report says so. Source:
`eval/results/ownership-probe-frontier/REPORT.md` (commit `bce5c04`).
Every axis on which it differs from the other rows:

- **36 repairs, not 600.** Its design is **12 defect classes × 3 arms × 1
  seed = 36 repairs**, against **200 repairs per arm over all 20 classes**
  for every other row. So its `100 | 92 | 92` above (rust | oxide |
  explicit) is **12/12, 11/12, 11/12** — a count out of twelve, rounded,
  not a rate over two hundred.
- **A deliberately hard, non-random subset of the corpus.** `p01`–`p08`
  were run first; when those stood at 23/23 the remaining four were
  chosen as the *hardest-for-Rust* classes at 7B, and the other eight of
  the 20 were deliberately never run. That report states the stopping
  rule explicitly and describes the selected classes as "deliberately the
  *hardest* ones at 7B".
- **One seed.** Every other row in the table is 10 seeds, and this
  project's own seed-count lesson is unusually pointed:
  `eval/results/ownership-probe-10seed/REPORT.md` is titled "the 3-seed
  result does not survive" and withdrew a headline on exactly that basis.
- **Unconstrained on all three arms** (quoted above).
- **Run via isolated agents**, one per (class, arm), each given a
  self-contained prompt file outside the repository — not a served local
  model at a pinned quantization and `num_ctx`.
- **Pre-§53 and pre-§54**, as established above.

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

This subject ran at **q5_K_M**, while the three **local** families in the
reference table ran at **q8_0**. (Quantization does not apply to the Opus
row at all — it is not a served local model.) The reason is hardware, not
choice — but not for the reason this report originally gave.

**All VRAM figures below are MiB.** Mixing units is what produced the
original error: the ollama registry quotes GGUF sizes in **decimal GB**,
`llama-server` quotes the card in **MiB**. This report previously set a
"16.70 GB" `q8_0` GGUF against a "16.30 GB" card and concluded the
weights "do not fit the card even with nothing else running". The "16.30
GB" was `llama-server`'s own **16303 MiB** with the decimal point moved.
In consistent units that is **15926 MiB** of `q8_0` weights against a
**16303 MiB** card — the weights **do** fit the raw card, by roughly 380
MiB. The original justification was arithmetic, not physics.

**The real constraint is weights plus runtime overhead, and this project
measured that overhead rather than estimating it.** Serving a model also
costs a KV cache and compute buffers the GGUF size does not advertise. On
this card, with ~**1760 MiB** held by the desktop session (~**14544 MiB**
actually free), `llama-server` needed **13459 MiB** to serve the **11302
MiB** `q5_K_M` weight set at `num_ctx` 8192 — about **2160 MiB** of
overhead on top of the weights — leaving the **1085 MiB** free recorded
in the Method note below. Against that measurement:

| tag | weights | + measured overhead | verdict |
|---|---|---|---|
| `q8_0` | 15926 MiB | ~18090 MiB | exceeds the **entire card** (16303 MiB) |
| `q6_K` | 13418 MiB | ~15580 MiB | exceeds free VRAM (~14544 MiB) |
| `q5_K_M` | 11302 MiB | **13459 MiB, measured** | fits — 1085 MiB free |

So `q5_K_M` remains **physically forced, not a policy choice**. The
conclusion is unchanged and the pin is unchanged; only the stated reason
was wrong.

**And `q5_K_M` was the primary pin, not a fallback.** This report
previously said it "was the fallback path named in this task's
pre-registration for exactly this contingency". That is false twice over:
the pre-registration quoted above names no quantization contingency of
any kind, and `q5_K_M` was not a fallback. It was chosen at design time
from the registry manifest sizes and is recorded as **chosen** in
`docs/superpowers/specs/2026-08-09-deepseek-capability-window-design.md`'s
tag table. The named fallback in that same table — and in the plan's
contingency section, which says to fall back on quantization and never on
`num_ctx` — is **`q4_K_M`**, to be used only if the server failed to load
or OOMed at `q5_K_M`. **It never fired.**

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
  have gated would not have been comparable to the **three local**
  reference deltas. This run's `oxide` and `explicit` arms were
  unconstrained (no grammar), while those three families' figures for the
  same two arms were produced under grammar-constrained decoding. The
  `rust` arm alone is unaffected — it is never grammar-constrained in any
  of the five runs — which is why the INCONCLUSIVE verdict itself is
  unaffected by this issue. A future run that clears the `rust` gate on an
  unconstrained setup still cannot compute a delta against those three
  reference figures without first either constraining `oxide`/`explicit`
  to match, or re-deriving unconstrained figures for **those three local
  subjects**. **Not four**: Opus's unconstrained figures already exist —
  that run had no grammar on any arm — so there is nothing to re-derive
  there. What makes the Opus row unusable as a delta reference is its
  design (36 repairs, 12 hand-picked classes, 1 seed, isolated agents,
  pre-§53/§54), not its decoding, and no re-run of this project's local
  harness would fix that.

## Method note: VRAM headroom

This card has **16303 MiB** of VRAM and this configuration measured
**1085 MiB** free once the model was resident — the tightest margin
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
