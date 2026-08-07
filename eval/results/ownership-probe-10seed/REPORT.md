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
