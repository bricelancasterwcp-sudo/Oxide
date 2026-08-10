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
rather than editorial. DeepSeek-V2-Lite is 15.7B total parameters; MoE
activates 2.4B per token but **every expert must be VRAM-resident**, so the
full weight set is what must fit. At `q8_0` that is ≈16.7 GB against an
RTX 5080's 16.3 GB total (~14.7 GB free). It does not fit.

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

**`q5_K_M` and not something else.** ≈11.0 GB of ~14.7 GB free — the closest
to `q8_0` that leaves real headroom. `q6_K` (≈12.9 GB) is nearer the roster
but leaves under 2 GB for KV cache and compute buffers, and this build
throws intermittent `vk::DeviceLostError` under sustained load — a
600-repair run *is* sustained load, and that failure has already killed a
600-repair run on this machine once. `q4_K_M` is safe but is the largest
capability departure on the exact axis this experiment reads, which would
make a low delta unattributable between "near ceiling" and "q4 damaged it".

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
3. **The ollama tag must be verified, not assumed.** The exact string
   `deepseek-coder-v2:16b-lite-instruct-q5_K_M` is a plausible reconstruction
   of ollama's naming, not a verified fact. Confirm with `ollama show` before
   pinning it, and pin whatever is actually there.

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
