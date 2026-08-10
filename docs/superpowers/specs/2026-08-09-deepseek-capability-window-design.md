# DeepSeek-Coder-V2-Lite as a capability-window test — design

**Date:** 2026-08-09
**Subject under test:** `deepseek-coder-v2:16b-lite-instruct-q5_K_M` — MoE,
~16B total parameters (commonly cited 15.7B), 2.4B activated. The exact
count is not load-bearing: the VRAM conclusion below holds anywhere in the
15–17B range.
**Instrument:** the existing ownership probe (Part X), unchanged.
**Status:** design approved in session; the SPEC §48 amendment and the
implementation plan follow from it.

## What this run is for

Not a fourth replication family, and not a generation campaign. This is a
**falsifiable test of the capability-window claim** — the observation that
the oxide−explicit repair advantage is non-monotonic in subject capability:

| subject | rust arm | oxide | explicit | oxide − explicit |
|---|---|---|---|---|
| granite-code-8b | 73.0 | 20.5 | 11.0 | +9.5 |
| codegemma-7b | 84.5 | 46.5 | 11.5 | +35.0 |
| qwen2.5-coder-7b | 89.0 | 73.0 | 14.0 | **+59.0** |
| Claude Opus 5 | 100 | 92 | 92 | 0.0 |

Read as a window: too weak to exploit the help (granite, near floor), the
sweet spot (qwen), and no longer needing it (Opus, ceiling). A fifth point
placed **between qwen and Opus in capability** is the cheapest thing that
can break this story.

## The pre-registration

Written before the run, and deliberately conditional so that no outcome can
be narrated as confirmation after the fact.

> **If** DeepSeek's `rust` arm scores **above qwen's 89.0**,
> **then** its `oxide − explicit` delta scores **below qwen's +59.0**.
>
> **If** its `rust` arm scores **at or below 89.0**, this run does not test
> the window and is reported **inconclusive** — not reinterpreted.

**The `rust` arm is the capability yardstick, and it is internal.** It
measures each subject on a language it knows well, inside the same
instrument, on the same corpus, in the same session. No external benchmark
is cited and none is needed — which matters, because "DeepSeek is stronger
than qwen" sourced from a leaderboard would be an unverifiable premise
imported into a falsifiable claim.

Secondary, recorded but not load-bearing:

- **Directional:** the delta lands strictly between 0.0 and +59.0. A delta
  *above* +59.0 with a rust arm above 89.0 falsifies the window outright.
- The explicit arm stays near its floor (11.0–14.0 across all three current
  families) — that arm's binding constraint is annotation burden (`EX0002`),
  not ownership, and nothing here should move it.

## The quantization amendment (SPEC §48)

§48 currently pins:

> **Quantization** | uniform `q8_0` across the ladder
>
> *Quantization is held constant so the capability curve is not confounded.*

That invariant cannot survive this subject, and the reason is physical
rather than editorial. MoE activates 2.4B parameters per token but **every
expert must be VRAM-resident**, so the full weight set is what must fit. The
published `q8_0` GGUF is **16.70 GB** against an RTX 5080's **16.30 GB
total** — it does not fit the card even with nothing else running.

**This is the roster's growth path, not a DeepSeek quirk.** On 16 GB, *any*
subject stronger than those already in the ladder needs sub-`q8_0`. The
invariant was going to break at the next capability step regardless.

The amendment follows the precedent §48 already established for `num_ctx`,
in the same shape and for the same reason:

| | `num_ctx` (existing) | quantization (new) |
|---|---|---|
| pinned | per-family (`granite8b`: 4096) | per-family (`deepseek16b_lite`: `q5_K_M`) |
| why | 8192 exceeds granite's 4096 training context — physically unsatisfiable | `q8_0` exceeds 16 GB VRAM — physically unsatisfiable |
| fairness | arm-fair within that slug's own runs | arm-fair within that slug's own runs |
| status | recorded per-family covariate | recorded per-family covariate |

Implementation: a `QUANT` dict beside `NUM_CTX` in `eval/driver.py`,
defaulting to `q8_0`, with `deepseek16b_lite` as its sole entry, written
into the manifest exactly as `num_ctx` is.

**`q5_K_M` and not something else.** Sizes read from the ollama registry
manifests rather than estimated, against **13.95 GB free** (16.30 GB card,
1.56 GB held by the desktop session):

| tag | GGUF | headroom | verdict |
|---|---|---|---|
| `16b-lite-instruct-q8_0` | **16.70 GB** | — | **does not fit the card at all** |
| `16b-lite-instruct-q6_K` | 14.07 GB | −0.12 GB | does not fit free VRAM |
| `16b-lite-instruct-q5_K_M` | **11.85 GB** | **2.10 GB** | **chosen** |
| `16b-lite-instruct-q4_K_M` | 10.36 GB | 3.59 GB | fallback only |

`q8_0` exceeds the *entire card*, which is what makes the §48 amendment
physical rather than editorial. `q6_K` is not merely tight — at 14.07 GB it
exceeds free VRAM outright, so it was never actually available. `q4_K_M` is
the largest capability departure on the exact axis this experiment reads,
which would make a low delta unattributable between "near ceiling" and "q4
damaged it".

**Headroom is the real risk, and it is thinner than the GGUF size suggests.**
Measured on the pulled model, not projected:

| state | VRAM used | free |
|---|---|---|
| desktop only | 1.56 GB | 14.24 GB |
| model resident (8192 ctx) | 15.05 GB | **0.77 GB** |
| after one real repair | 15.33 GB | **0.51 GB** |

llama-server takes ≈13.5 GB for an 11.85 GB model — roughly **2.2 GB of KV
cache and compute buffers** that the GGUF size does not advertise, and a
further ~0.28 GB is claimed on first real inference. The design's original
"2.10 GB headroom" figure was wrong by about 4×: it subtracted the GGUF from
free VRAM and ignored runtime allocation.

DeepSeek-V2's MLA does compress the KV cache — this would be far worse
without it — but 0.51 GB is a thin margin on a build that throws
intermittent `vk::DeviceLostError` under sustained load, a failure that has
already killed a 600-repair run on this machine once.

**This promotes the campaign runner's checkpoint/resume from prudence to
load-bearing.** It is what makes a mid-run device-lost cost minutes instead
of the whole run, and its resume path must be tested before the run rather
than discovered during it.

**If it does not fit at 8192, fall back on QUANTIZATION, not context.**
Dropping to `q4_K_M` adds 1.49 GB of headroom and deviates on one axis that
is already declared a covariate. Reducing `num_ctx` instead would deviate on
a *second* axis — stacking two confounds where the design has budgeted for
one — and would additionally break comparability with the 8192 the other
three families ran at. Whichever is used gets recorded in the manifest and
named in the REPORT.

### The confound, stated rather than waved away

`q5_K_M` against the roster's `q8_0` is a real difference in the subject.
The argued direction is **conservative**: quantization reduces capability,
and a subject above qwen's peak moving *down* the capability axis moves
*toward* qwen's position — i.e. toward a **larger** delta, against the
prediction. Confirming the prediction anyway is therefore the stronger
result.

That is an argument, not a measurement, and the REPORT must say so in those
words. The clean way to settle it — running DeepSeek at two quantizations
and comparing — is explicitly **out of scope** here and recorded as a
follow-up if the result turns out to matter.

## Harness changes

Small, and mostly registration.

1. **`eval/driver.py`** — `MODELS["deepseek16b_lite"]`, plus the new `QUANT`
   dict. **No `NUM_CTX` entry:** DeepSeek-V2's training context is far past
   8192 (128K is the published figure; only "≥ 8192" is load-bearing here),
   so the default is satisfiable and no cap applies — the opposite of
   granite's situation, and worth a comment saying so, because a reader who
   has internalised granite's case will look for one. Confirm from
   llama-server's startup log that it does **not** print the
   "exceeds the training context … capping" line; that log is the ground
   truth, not the model card.
2. **`eval/probe_campaign.py` (new)** — `eval/probe.py run` takes ONE seed
   per invocation, so a 10-seed × 3-arm campaign is 30 invocations. It ships
   **in the repo**, with per-repair JSONL checkpointing and resume, because
   the GPU throws device-lost mid-run and because a scratch script that dies
   with the session is precisely how the 6a pilot's demand table became
   permanently irreproducible. Third application of that lesson, after
   `eval/deformation.py`.
3. **The ollama tag is verified.** `deepseek-coder-v2:16b-lite-instruct-q5_K_M`
   resolves (registry manifest 200), as do the `q4_K_M`, `q6_K` and `q8_0`
   siblings and a `16b-lite-base-*` line. **Pin the instruct tag
   deliberately** — the roster is all-instruct, and a silent base/instruct
   mismatch would be a capability difference read as a language effect.
   Confirm the pulled digest with `ollama show` and record it in the manifest.

## Test plan

- `tests/test_6a.py::test_model_slugs_map_to_pinned_q8_tags` asserts the
  roster **exactly**, and its NAME encodes the invariant being amended.
  Renaming it (to `test_model_slugs_map_to_pinned_tags`) and adding the
  quantization assertion is part of the amendment, not incidental cleanup —
  a test called `..._q8_tags` that admits a `q5_K_M` entry is a lie the next
  reader will believe.
- The "ALL FIVE slugs" comments at `tests/test_6a.py:1226` and
  `eval/driver.py:627` become stale at six. Both must be corrected; the
  driver one documents a real guard (llama-server serves one model, so
  multi-slug llamacpp runs are refused) and that guard's test must still
  pass with the new slug present.
- A test pinning `QUANT` defaults to `q8_0` for every pre-existing slug and
  `q5_K_M` for `deepseek16b_lite` — the amendment's own guard.
- `eval/probe_campaign.py` needs resume coverage: interrupt after N repairs,
  resume, and assert no repair is run twice and none is skipped. Checkpoint
  logic that has never been resumed is checkpoint logic that does not work.

## Scope boundary

**In:** the roster entry, the §48 quantization amendment, the committed
campaign runner, one ownership-probe run (20 classes × 3 arms × 10 seeds =
600 repairs), and a REPORT stating its own limits.

**Out, and recorded rather than dropped:**

- **The g3 campaign is untouched.** g3's before/after rests on the committed
  `g0c` baseline; introducing a fourth family mid-flight would change the
  comparison. DeepSeek is not added to it.
- **Existing claims are not retro-fitted.** The three-family results stay
  three-family results. This run adds a point to a described window; it does
  not re-open anything already published.
- **No generation grid.** Whole-program generation is the expensive
  condition and is not what this tests.
- **The MoE-vs-dense question is not answered.** One MoE subject cannot
  separate "MoE", "stronger", and "different pretraining mix". Answering it
  needs dense controls at matched active- and total-parameter counts, and is
  a different experiment.
- **The two-quantization control** described above.

## Preflight: measured, not assumed

Run before the design was finalised, on the pulled model. All of it holds.

| check | result |
|---|---|
| ollama tag resolves | ✅ `deepseek-coder-v2:16b-lite-instruct-q5_K_M`, digest `6065d4880bf9` |
| reported metadata | `deepseek2`, **15.7B**, ctx **163840**, **Q5_K_M** |
| `deepseek2` on the Vulkan build | ✅ loads, MoE experts resident |
| 8192 context satisfiable | ✅ log says `n_ctx_seq (8192) < n_ctx_train (163840)` — an **under-use** notice, the opposite of granite's "exceeds … capping" |
| chat template in the GGUF | ✅ DeepSeek `User:`/`Assistant:` with `<｜end▁of▁sentence｜>` — **load-bearing**, because `eval/llamacpp.py` posts to `/v1/chat/completions` and llama-server templates from GGUF metadata, not from ollama's Modelfile |
| end-to-end repair | ✅ `probe run --id p01 --arm oxide` → **strict 1.0, lenient 1.0** |
| Vulkan errors during that run | ✅ none |

The chat-template check is the one that could have silently ruined the
experiment: a missing or wrong template degrades the subject in a way that
reads as a capability deficit, which is precisely the axis this run
measures.

## What would make this run worthless

Recorded so it is checked before spending the GPU time, not after:

- **The rust arm lands at or below 89.0.** Then the subject is not above
  qwen on the internal yardstick and the window is untested. Inconclusive,
  by the pre-registration, with no reinterpretation.
- **Device-lost mid-run without resume.** Mitigated by the committed runner;
  the resume path must be tested before the run, not during it.
- **The tag resolves to a different model than intended** (base vs instruct,
  a different quant, or a non-Lite V2). The manifest records the model
  digest — check it against `ollama show` before trusting any output.
