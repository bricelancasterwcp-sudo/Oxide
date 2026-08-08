# 10-seed A/B — the 3-seed result does not survive

**Date:** 2026-08-07
**Design:** 20 classes × 3 arms × **10 seeds** = 600 repairs, run twice. The
only difference between the two runs is `src/cli.py` and `src/sema/linear.py`
at the `OX0403` fix (`a9f336c`) versus its parent. The probe corpus is
byte-identical across both, so the sole thing either run sees differently is
the diagnostic text. Subject: qwen2.5-coder-7b-instruct-q8_0.

## This supersedes the 3-seed estimate

| | paired delta | SE | 2-SE | clears 2 SE | sign test |
|---|---|---|---|---|---|
| 3 seeds | +18.3pp | 7.0 | `[+4.3, +32.4]` | **yes** | +8/−1, p = 0.039 |
| **10 seeds** | **+10.0pp** | **5.8** | **`[−1.7, +21.7]`** | **no** | +10/−4, p = 0.18 |

The point estimate nearly halved and the interval now includes zero.

**The earlier claim that the primary comparison was "statistically resolved"
was an artifact of three seeds.** It is withdrawn. At 10 seeds, this subject
alone does not resolve the Oxide-vs-explicit difference.

The direction is unchanged — 10 of 14 non-tied classes still favour implicit
linearity — but the magnitude was overstated and the significance was not
real.

## What survives

Pooling across the three model families, using the best available estimate per
family (qwen now at 10 seeds, the other two still at 3):

| Family | delta | classes +/− |
|---|---|---|
| qwen2.5-coder-7b (10 seeds) | +10.0 | 10 / 4 |
| codegemma-7b (3 seeds) | +13.3 | 5 / 1 |
| granite-code-8b (3 seeds) | +6.7 | 5 / 1 |

**20 of 26 non-tied comparisons favour implicit linearity, two-sided exact
p = 0.0094.** Previously reported as 18 of 21, p = 0.0015 — weaker with better
data, still significant.

The honest position: *the direction replicates across families; no single
family resolves it, and the magnitude is not established.* The two replication
families are still at 3 seeds and should be re-run at 10 before their numbers
are relied on.

## The OX0403 fix has no measurable effect at 7B

| | oxide strict |
|---|---|
| pre-fix | 51/200 — **25.5%** |
| post-fix | 51/200 — **25.5%** |

Identical. Two of twenty classes moved and they cancel exactly:

| class | pre | post | |
|---|---|---|---|
| accumulate-without-reassign | 10.0 | 30.0 | +20.0 |
| loop-carried-move | 30.0 | 10.0 | −20.0 |
| *18 other classes* | | | 0.0 |

Two programs each way at 10 seeds. Rust is byte-identical at 178/200 in both
runs, as expected — its diagnostic did not change.

So the fix is **causal at frontier** (fail → pass in both Oxide dialects, on a
prompt differing only in that text, with the pre-fix behaviour observed across
two independent prior runs) and **inert at 7B**. Both are true. The plausible
reading is that a 7B model does not extract enough from a note position to
change its repair strategy, while a frontier model does — but this run cannot
distinguish that from the fix simply not mattering.

The fix stands on its own merits regardless: the old diagnostic named one
location twice and omitted the use that makes the move fatal, which is wrong
independently of whether any particular model notices.

## Method note

The pre-fix arm was produced by checking out the two source files from the
parent commit. `git checkout <sha> -- <files>` *stages* the reverted version,
so a cleanup of `git checkout -- <files>` restores from the index and silently
leaves the revert in place. The working tree was left holding pre-fix code
after this run; caught by `git status` and repaired with
`git restore --source=HEAD --staged --worktree`. Anyone reproducing this
should use `git restore --source=HEAD --staged --worktree` for the cleanup.

Raw: `post-fix.json`, `pre-fix.json`.

---

## Addendum — all three families at 10 seeds, matched code

The two replication families were re-run at 10 seeds on current HEAD, so all
three are now on **matched code at matched seed count**: 600 repairs each,
1800 total.

| Model | oxide | explicit | rust | delta | SE | 2-SE | clears 2 SE | classes +/− | was (3 seeds) |
|---|---|---|---|---|---|---|---|---|---|
| qwen2.5-coder-7b | 25.5% | 15.5% | 89.0% | **+10.0** | 5.8 | `[−1.7, +21.7]` | no | +10/−4 | +18.3 |
| codegemma-7b | 12.5% | 8.5% | 84.5% | **+4.0** | 4.7 | `[−5.3, +13.3]` | no | **+4/−6** | +13.3 |
| granite-code-8b | 23.5% | 13.5% | 73.0% | **+10.0** | 5.0 | `[−0.1, +20.1]` | no | +9/−1 | +6.7 |

**No family individually resolves.** Every interval includes zero — granite's
only barely (`−0.1`). codegemma's class-level signs now lean the *other* way:
4 classes favour implicit, 6 favour explicit, despite a positive mean.

### The evidence weakened monotonically as data was added

| Stage | pooled sign test |
|---|---|
| 3 seeds, all three families | 18 of 21, **p = 0.0015** |
| 10 seeds qwen, others at 3 | 20 of 26, **p = 0.0094** |
| **10 seeds, all three, matched code** | **23 of 34, p = 0.0576** |

Three rounds, each adding data, each weakening the result. That trajectory is
the signature of an effect that is smaller than the first measurement
suggested — possibly much smaller. **The pooled test no longer clears
p = 0.05.**

### What can honestly be said

The combined estimate over all 60 (family × class) paired differences is
**+8.0pp, SE 3.0, 2-SE `[+2.0, +14.0]`** — this does exclude zero, and it is
the strongest defensible statement available. But it pools across three
subjects of differing capability and treats 60 correlated cells as
independent, so it should be read as a summary, not a significance test. The
sign test, which makes fewer assumptions, sits at p = 0.058.

**Current position: the direction is consistently positive across three model
families and 23 of 34 non-tied classes, the combined interval excludes zero,
and the non-parametric test is marginal. The effect is not established at
conventional significance, and it is not refuted. It is smaller than this
project's earlier claims.**

Also corrected: granite was previously reported as `no-signal-at-floor`
because both arms sat at or below 10% at 3 seeds. At 10 seeds it is 23.5% /
13.5% — well clear of the floor, and the family with the cleanest sign split
(+9/−1). **That floor verdict was itself a 3-seed artifact.**

### ~~The finding that did not weaken~~ — WITHDRAWN

**The table below is wrong.** It was computed as `lenient AND NOT strict`,
which does not require the program to COMPILE. Most of those repairs traded
an ownership error for a type error and never ran. True rates, requiring
compilation:

| Model | oxide | explicit | rust |
|---|---|---|---|
| qwen2.5-coder-7b | **0.0%** | **0.0%** | 3.0% |
| codegemma-7b | 2.5% | 2.5% | 5.0% |
| granite-code-8b | **34.0%** | 25.5% | 2.0% |

Two families put Rust highest, one puts Oxide highest. No consistent
direction, no order-of-magnitude gap. `score()` now emits an explicit
`degenerate` field requiring compilation, with tests pinning that a type
error cannot be counted as one.

What the mislabelled repairs actually were: in the qwen Oxide arms the
dominant codes are `OX0304` (94) and `OX0200` (86) — `.clone()` method
syntax, which Oxide does not have, and undefined names. Even in a repair
task with syntax, names and types supplied, these models reach for Rust
idioms the language lacks. That is consistent with the whole-program result
and is the durable observation.

The original (incorrect) table follows for the record:

| Model | oxide | explicit | rust |
|---|---|---|---|
| qwen2.5-coder-7b | 60.0% | 68.0% | **3.0%** |
| codegemma-7b | 42.5% | 38.5% | **7.0%** |
| granite-code-8b | 48.0% | 52.5% | **4.0%** |

Between 38% and 68% of Oxide-arm "repairs" silence the ownership diagnostic
while changing what the program does. Rust does it 3–7% of the time. That is
an order-of-magnitude gap that held under every increase in data, and it is
the most solid empirical result this instrument has produced.

Raw: `codegemma-7b.json`, `granite-code-8b.json`.
