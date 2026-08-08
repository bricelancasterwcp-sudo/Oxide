# Ownership Probe — cross-family replication

> **SUPERSEDED (2026-08-07).** Every number below is from 3 seeds per cell.
> All three families were re-run at 10 seeds on matched code:
> qwen **+10.0**, codegemma **+4.0** (class signs +4/−6, leaning the other
> way), granite **+10.0**. No family resolves; the pooled sign test fell to
> **23 of 34, p = 0.058**. granite's `no-signal-at-floor` verdict here was
> itself a 3-seed artifact — at 10 seeds it is 23.5%/13.5%, clear of the
> floor. See [`../ownership-probe-10seed/`](../ownership-probe-10seed/).

**Date:** 2026-08-07
**Question:** the +18.3pp result rested entirely on qwen2.5-coder-7b. Is the
ordering a property of implicit linearity, or a quirk of one model family?
**Design:** identical protocol — same 20-class corpus, same grammar constraint
on the Oxide arms, same 3 seeds, 180 repairs per model. Three independent
families, all instruct-tuned, all code-focused, all uniform `q8_0`.

## Results

| Model | family | oxide | explicit | rust | paired delta | SE | 2-SE | classes +/− |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder-7b | Alibaba | 33.3% | 15.0% | 88.3% | **+18.3** | 7.0 | `[+4.3, +32.4]` | 8 / 1 |
| codegemma-7b | Google | 16.7% | 3.3% | 88.3% | **+13.3** | 7.4 | `[−1.5, +28.2]` | 5 / 1 |
| granite-code-8b | IBM | 8.3% | 1.7% | 71.7% | **+6.7** | 3.9 | `[−1.1, +14.5]` | 5 / 1 |

**The direction replicates in all three families.** Every model favours
implicit linearity, and in every model the split is lopsided: 5–8 classes
favour Oxide against exactly **1** favouring explicit, the same class each
time.

**Pooled sign test across all three families**, per class × model:
**18 of 21 non-tied comparisons favour implicit linearity, two-sided exact
p = 0.0015.**

> **Updated 2026-08-07.** Re-running qwen at 10 seeds changed its signs from
> +8/−1 to +10/−4. Recomputed with the better estimate, the pooled test is
> **20 of 26, p = 0.0094** — still significant, weaker than stated here.
> codegemma and granite remain at 3 seeds and are provisional for the same
> reason qwen's 3-seed number was. See `../ownership-probe-10seed/`.

## Applying this project's own guards honestly

Neither replication clears 2 SE on its own, and both intervals include zero
— barely (−1.5 and −1.1). Under the pre-registered rules from `cb250ab`:

| Model | verdict under our own rules |
|---|---|
| qwen2.5-coder-7b | **supports** |
| codegemma-7b | **no-detectable-difference** (direction consistent, interval spans zero) |
| granite-code-8b | **no-signal-at-floor** — both arms ≤10%, so the ±5pp band has no resolution here and this point carries no reading |

Granite's +6.7pp must not be counted as support. Its arms sit at 8.3% and
1.7%; that is exactly the floor regime the guard exists for, and the guard
was written before these numbers existed.

## The effect has a capability window

Ordering the four subjects by how well they do the task at all:

| Subject | oxide strict | paired delta |
|---|---|---|
| frontier (Opus 5) | 92% | **0.0** — ceiling, arms identical |
| qwen2.5-coder-7b | 33% | **+18.3** |
| codegemma-7b | 17% | **+13.3** |
| granite-code-8b | 8% | **+6.7** — at floor |

The delta is largest in the middle and compresses at both ends. That is what
a real effect looks like when it is bounded by a floor and a ceiling: a model
that always succeeds cannot show a difference, and neither can a model that
never succeeds. It is also why single-point measurements of this thesis have
been so uninformative — run 1 landed on the ceiling, Phase 6a's whole-program
eval landed below the floor.

## What this supports

*Implicit linearity is an accessibility win, and the win is real across model
families.* The direction is consistent in three independent families and the
pooled test is strong. The magnitude is only individually resolved in the one
subject that sits in the measurable band.

## What it does not support

- **Not "makes LLMs more reliable" without qualification.** At frontier the
  delta is 0.0.
- **Not a magnitude claim.** +18.3 / +13.3 / +6.7 are three different numbers
  from three models, two of them floor- or interval-limited. The honest
  summary is "positive in every family tested", not "≈15pp".
- **Still repair, not authorship.** None of these models can write Oxide from
  scratch; Phase 6a measured 2/20 first-compile at 7B.
- **Still one seed-triple per model, one shot condition.**

## Method note

These runs were only possible after freeing 7GB of VRAM held by an unrelated
idle process; codegemma at `q8_0` is 9.1GB and does not fit beside it. An
earlier attempt at partial GPU offload was abandoned — it does not change
outputs, but it was slow enough to time out. Both models here were fully
resident (13.4GB and 10.4GB peak), 337s and 365s for 180 repairs each.

Raw per-repair records: `codegemma-7b.json`, `granite-code-8b.json`.
