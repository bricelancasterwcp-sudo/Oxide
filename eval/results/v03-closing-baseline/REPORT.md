# v0.3 closing baseline — the fine-tune track's comparison point

**Date:** 2026-08-11
**Code state:** `g3-to-str` branch at `2de2b89` (SPEC §56 field assignment
+ §57 `to_str` alias both landed; language final).
**Design:** constrained grid only, 3 families × 3 arms × 20 tasks × 10
seeds = **1,800 sessions**, prefix `v03c`, SPEC §48 pinned parameters.
This is **not** primarily a test of `to_str` or `s.f = e` — it is the
measured starting point the next phase (a token-matched LoRA fine-tune
vs. shipped Black Oxide) will compare against. That is why it runs
regardless of whether the two riders move anything.

## Pre-registered endpoints (quoted verbatim from the task brief, written before this run)

| endpoint | prediction |
|---|---|
| v0.3 baseline table (all arms, all families) | descriptive; this is the deliverable |
| g2: ExprStmt-position `f.x == e`, constrained oxide | **18 → 0**, cited as a **lower bound** |
| g2: the same signature, unconstrained | 0 → 0 |
| g3: `to_str` unresolved plain calls | **→ 0**, mechanically — the name now resolves |
| g3: `fn to_str` self-definitions | should fall from 15 across 6 programs |
| aggregate first-attempt pass rates | **no detectable change** — both changes sit at ~1.5–2% prevalence; apparent movement is noise |
| rust arm | flat at the first-attempt **rate** level, never the byte level |

No family-ordered dose-response was predicted for either change (both
sit below this design's ~5pp resolution floor, SPEC §47).

## Method note: the four-hour gap

qwen7b and codegemma7b ran back to back. After codegemma7b finished, the
campaign sat **idle for roughly four hours** before granite8b started.
Two independent causes: a background completion-wait on this agent's
side did not fire, and a separate progress monitor used `pgrep -f
"eval.driver"`, which matches its own command line and so reported the
driver alive indefinitely. Brice noticed the GPU idling at 8% / 690 MHz
/ 17.8 W and drove the granite8b rotation manually — stopped the stale
state, resolved granite's own blob, started its server at its own
G0-pinned `num_ctx` (4096), verified health and `model_path`, and ran
the grid. **No data was affected**: qwen7b and codegemma7b were already
complete and untouched; granite8b ran afterward on a freshly loaded
server. The cost was wall-clock only, recorded here because a four-hour
gap in the middle of a campaign is the kind of thing a later reader of
timestamps should not have to discover on their own.

## The v0.3 baseline table

Constrained decoding, all three arms, 10 seeds, 20 tasks (n=200 per
arm per family):

| family | arm | first-compile | first-pass | final-pass |
|---|---|---|---|---|
| qwen | oxide | 36.0% | 30.5% | 37.0% |
| qwen | explicit | 24.5% | 21.5% | 33.0% |
| qwen | rust | 98.0% | 56.5% | 58.5% |
| codegemma | oxide | 23.5% | 16.5% | 18.5% |
| codegemma | explicit | 20.0% | 12.0% | 13.5% |
| codegemma | rust | 84.5% | 45.0% | 52.0% |
| granite | oxide | 33.0% | 9.5% | 12.0% |
| granite | explicit | 20.0% | 8.5% | 11.0% |
| granite | rust | 80.0% | 42.0% | 52.0% |

Paired first-pass deltas (oxide − explicit, `eval.rollup`): qwen
**+9.0pp** (SE 6.8), codegemma **+4.5pp** (SE 3.7), granite **+1.0pp**
(SE 4.6). None clears 2 SE. `OX04xx` gate, **first attempts only**
(`eval.g0_report`'s gate counter): qwen 1 occurrence/1 session,
codegemma 0/0, granite 2/2. Over **all** attempts (`eval.g0_report`'s
`stage_hist_all`, linearity bucket): qwen 7, codegemma 0, granite 7 —
the two figures are not the same measurement and are labeled here so a
reader re-running either tool is not confused by the mismatch. Either
way the linearity gate remains essentially unpopulated, unchanged in
kind from G0. Context exhaustion: qwen 1/600,
codegemma 1/600, granite **99/600 (16.5%)** — granite's window covariate
(SPEC §48, `num_ctx` 4096) applies throughout, consistent with G0's
16.7%.

## Endpoint scorecard

**1. v0.3 baseline table — delivered.** Above.

**2. g2, constrained statement-position signature: CONFIRMED, exactly
18 → 0.** `python -m eval.deformation eval/results/v03-closing-baseline`:

```
family          progs   stmt   tail  stmt progs
codegemma7b       200      0      0           0
granite8b         200      0      0           0
qwen7b            200      0      0           0
```

Re-running the identical tool against the committed G0 constrained root
reproduces the cited pre-change count exactly (codegemma 10, granite 6,
qwen 2 → 18 statement occurrences in 9 programs), confirming both the
tool and the "18" are real before comparing anything. Post-change: 0
statement occurrences AND 0 tail occurrences, all three families, all
600 oxide-arm first attempts. The signature is eliminated outright, not
merely reduced — stronger than the pre-registered lower-bound framing
required.

**3. g2, unconstrained: not re-measured this campaign, and stated as
such rather than assumed.** The brief's Step 2 runs the constrained grid
only; there is no `v03u` root. What can be said: the committed G0
*unconstrained* root (pre-change) already scores 0/600 on this
signature under the identical tool —

```
family          progs   stmt   tail  stmt progs
codegemma7b       200      0      0           0
granite8b         200      0      0           0
qwen7b            200      0      0           0
```

— consistent with the mechanism SPEC §56 gives for why the signature
exists at all (grammar-forced substitution of `=` for `==` under
*constrained* decoding specifically; nothing about the fix should make
it appear where the forcing mechanism was never present). This is
supporting context, not a new measurement of the `0 → 0` cell at v0.3
HEAD.

**4. g3, `to_str` unresolved plain calls: VACUOUS — structurally true by
construction, not a measurement. Recorded, not deleted.** The reading is
0, and `eval.demand.scan_oxide_arm` on this root does not list `to_str`
in `unresolved_calls` at all (only `to_int: 2` occurrences / 1 program
appear). But that 0 is not evidence, and it should not have been scored
in the same column as endpoints 2, 5, 6 and 7, which are.

*Why it cannot fail.* `unresolved_calls` filters each candidate on `name
not in BUILTINS` against the **live** table. The moment `to_str` entered
`BUILTINS` its unresolved count became 0 for every possible input — this
corpus, any earlier corpus, any future one. Run against the two
**pre-change** corpora at current HEAD it duly reads **0 for `g0c` and 0
for `g1c`** as well, corpora collected before `to_str` existed as a name
the language had. No corpus can produce a nonzero value, so nothing about
the change is being tested.

It is vacuous a second time over, independently of that filter. Removing
the `BUILTINS` clause entirely and re-running against all three corpora
still gives **0 / 0 / 0**, because `unresolved_calls` also excludes names
the program defines itself, and in this corpus *every* program that
writes a plain `to_str(` call also writes `fn to_str` in the same file.
There was never a population of bare, undefined `to_str` calls for the
alias to rescue. (See "The g3 null is mechanical" below, which is the
same fact seen from the other side.)

*An undisclosed endpoint substitution sits underneath this.* The design
document (`docs/superpowers/specs/2026-08-10-v03-g3-to-str-design.md`,
its own pre-registered table) registered a **different** endpoint: "g3:
`to_str`-shaped `OX0306` → 0". The implementation plan
(`docs/superpowers/plans/2026-08-10-v03-g3-to-str.md`) replaced it with
the demand-counter endpoint quoted at the top of this report, and did not
say it had done so. Both documents predate the run, so this is **not**
post-hoc endpoint selection — but the substitution was undisclosed, and
it is disclosed here. It also bought nothing: the original
operationalisation was equally vacuous. `OX0306` diagnostics mentioning
`to_str` number **0 in `g0c`, 0 in `g1c`, 0 in `v03c`** — already sitting
at their predicted value before the change landed, across every corpus
this project has.

The endpoint is kept on the scorecard rather than quietly dropped,
marked for what it is. A prediction that no possible observation could
have falsified is not a confirmed prediction, and the g3 code change
therefore has **no** surviving endpoint that measures it: endpoint 5, the
one that could have moved, missed.

**5. g3, `fn to_str` self-definitions: prediction MISSED — flat, and
within the counter's own demonstrated run-to-run noise.** Measured this
campaign: **16 occurrences across 6 programs**. The pre-change G0
baseline (re-verified directly from the committed G0 root, matching
SPEC §57's cited numbers exactly) is 15 occurrences across 6 programs.
Between those two points sits a third, previously unreported
measurement: `g1-vec-literal` (`g1c`, the constrained grid measured
after vec-literal but before either g2 or g3 landed) scores **14
occurrences across 7 programs** — re-verified directly against its own
committed root the same way. The series across three independent
campaigns is **15 → 14 → 16** occurrences and **6 → 7 → 6** programs.
That is a ±1–2 occurrence, ±1 program band of run-to-run variation in a
counter that saw no change to `to_str`'s status between G0 and g1c, and
this run's movement (+1 occurrence, 0 program change, relative to G0)
sits inside that band. The honest reading is therefore not "the count
moved in the wrong direction" — it is **flat, within noise this run can
now bound, and still a miss against the prediction**, because the
prediction was that it should fall and across three measurements it has
never fallen below its own opening value net of the demonstrated ±1–2
wobble. The likely reason it never falls: `to_str` was deliberately
added as a **resolver-only** alias and never added to
`LANGUAGE_CARD.md` (verified — the card still teaches only
`int_to_str`; SPEC §57 and its own design doc frame this explicitly as
"narrowed to a to_str alias on measurement"). A model that has never
seen `to_str` on its card has no new information telling it the name
now exists, so nothing about its own decision to self-define `fn
to_str` when it wants that spelling changes.

**A correction to how that was previously put here.** An earlier version
of this paragraph said the fix "resolves the *call* mechanically without
touching the *demand* signal at all". The second half is false. Adding
`to_str` to `BUILTINS` makes the name **reserved at top level**: a
program that writes `fn to_str(...)` is now `OX0203 duplicate top-level
name` (SPEC §16, §57) where before the change it was a legal user
function. The demand signal is not untouched — it is **relocated**, out
of taxonomy dossier 3 (models call conversions that don't exist) and into
dossier 4 (builtin reimplementation → `OX0203`). Same programs, new
friction, different filing cabinet. The corpus records the move directly:
`duplicate top-level name 'to_str'` fires **1×** in the 600 v03c
constrained oxide first attempts (5 occurrences across 5 attempts in 2
sessions once all four repair attempts are counted), against **0× in
`g0c` and 0× in `g1c`**. The cleanest instance is a controlled one —
`v03c-codegemma7b-0shot-s8` t08's first attempt is **byte-identical** to
its `g0c` counterpart, and the only difference in outcome is one extra
diagnostic: 36 diagnostics became 37, the addition being
`OX0203 duplicate top-level name 'to_str'`.

Reported as a miss, not reinterpreted as a partial success; the brief's
pre-registration governs, and the miss verdict is unchanged by either
correction — only the over-reading of a +1 as directional, and the claim
that the demand signal was untouched, are retracted.

**6. Aggregate first-attempt pass rates: CONFIRMED — no detectable
change, verified against the correct predecessor.** The immediate
predecessor for isolating g2+g3's own contribution is **not** G0 — it
is `g1-vec-literal` (`g1c`), the last full-grid measurement before g2/g3
landed. `git log 52c7487..2de2b89 -- src/ SPEC.md eval/grammar/
LANGUAGE_CARD.md LANGUAGE_CARD_EXPLICIT.md` returns **13 commits**, not
two — run it and check the count against this paragraph. Of those 13:
six touch only `SPEC.md` prose unrelated to generation behavior
(`2de2b89` a taxonomy/dossier and §55 mechanism-claim correction,
`48d1768`/`ddff7df`/`3270b66` deepseek-16b-lite registration and
quantization-pin bookkeeping — a family outside this campaign's three —
`8e80eab` the project rename, and `593c71b` a §55 rationale correction
with no normative change); one (`846014f`) touches `src/sema/cfg.py`
and `src/sema/destructure.py` but only corrects docstrings and deletes
a write to a dict key the commit's own message shows is
mutation-confirmed unreachable through the public API
(`analyze.use_classes()` resolves keys through `resolve.use_of`, which
holds `Var` ids only, never the statement id the deleted line wrote
under) — verified by reading its diff, not the commit message alone;
and the remaining six implement the two features under test: `1cea2a6`
(§56 SPEC text), `87e6b65` (§56 implementation — parser/AST/codegen/
sema), `f54cf22` (§56 grammar), `440f062` (§56 lower-bound
qualification, SPEC-only), `6871255` (§57 SPEC text), and `2c91240`
(§57 implementation). `8e80eab`'s own message additionally names
leaving model-facing card strings untouched as deliberate, precisely
because a renamed card retokenizes the prompt and breaks comparability.
v03c vs. g1c, all nine arm-rows, all three metrics (27 cells):

| family | arm | first-compile Δ | first-pass Δ | final-pass Δ |
|---|---|---|---|---|
| qwen | oxide | +1.5pp (+3/200) | +1.5pp (+3/200) | +2.5pp (+5/200) |
| qwen | explicit | −0.5pp (−1/200) | −0.5pp (−1/200) | +0.5pp (+1/200) |
| qwen | rust | 0 | −0.5pp (−1/200) | −0.5pp (−1/200) |
| codegemma | oxide | 0 | 0 | 0 |
| codegemma | explicit | 0 | 0 | 0 |
| codegemma | rust | 0 | 0 | 0 |
| granite | oxide | 0 | 0 | 0 |
| granite | explicit | 0 | −0.5pp (−1/200) | −0.5pp (−1/200) |
| granite | rust | 0 | 0 | −0.5pp (−1/200) |

**16 of 27 cells show zero movement; the other 11 move nonzero.** Of
those 11, eight move by exactly ±1 session (±0.5pp): qwen explicit (all
three metrics), qwen rust (first-pass, final-pass), granite explicit
(first-pass, final-pass), and granite rust (final-pass). The remaining
three are qwen's oxide row in full — first-compile +3, first-pass +3,
and final-pass +5 sessions (+1.5pp / +1.5pp / +2.5pp) — the largest
mover in the table and still well short of the ±5pp floor below. Every
value is an order of magnitude below
SPEC §47's own ±5pp floor for this corpus, and the residual wobble
matches the byte-level, cross-server-instance sampling non-determinism
at temperature 0.8 that g1's own REPORT already documented (48 of
~1,470 rust raw files differed between two runs with zero code change
between them). **This is what the pre-registered null looks like when
measured against the right baseline.** For context only — not as a
measurement of g2/g3 — v03c vs. the *original* G0 (`g0c`) shows larger
movement (e.g. qwen oxide first-pass 26.0% → 30.5%), but that delta is
overwhelmingly g1's own already-published vec-literal effect (g1c
reported qwen oxide first-pass 26.0% → 29.0% on its own), not anything
new in this campaign.

**7. Rust arm: CONFIRMED flat at the rate level, with the wobble stated
precisely rather than capped uniformly.** Rust first-compile is
identical (0 movement) in all three families against both g1c and g0c.
First-pass is identical against both except qwen, which moves −1
session (−0.5pp) against both comparisons alike. Final-pass wobbles by
at most **one** session against g1c (qwen −1, granite −1, codegemma 0)
but by up to **two** sessions against g0c (qwen 59.5%→58.5% = −2
sessions, granite 53.0%→52.0% = −2 sessions, codegemma −1 session) — a
larger wobble than the g1c comparison shows, and "at most one session"
does not hold for that pairing. Both figures are still small in
absolute terms (the largest is 1.0pp, one order of magnitude under the
±5pp floor) and the conclusion is unchanged: rust stays flat at the
rate level against every baseline this run is compared to. Byte-
identical is explicitly **not** claimed — see point 6's cross-server
sampling note — and the pre-registration only committed to the rate
level.

## The g3 null is mechanical, not statistical: the demand never reaches the resolver

This is the most useful thing this campaign produced about `to_str`, and
it was available in data already on disk rather than requiring a new run.
The pre-registration explained the expected null on **power** grounds
(~1.5–2% prevalence against a ±5pp resolution floor). That argument is
correct but weaker than what the corpus actually shows. The stronger
statement is that the alias changed the outcome of **exactly zero
sessions, and could not have**.

Classify every oxide-arm **first attempt** whose extracted program
contains the token `to_str` (`triples.jsonl`, `arm == "oxide"`,
`attempt == 1`; `int_to_str` excluded by word boundary):

| corpus | programs reaching for `to_str` | compiled | passed |
|---|---|---|---|
| `g0c` (pre-change) | 9 / 600 | **0** | **0** |
| `g1c` (pre-change) | 11 / 600 | **0** | **0** |
| `v03c` (this run, post-change) | 11 / 600 | **0** | **0** |

**Not one of the 9 pre-change programs failed because `to_str` was
missing.** Their first diagnostics:

| first diagnostic | n | stage | what it actually says |
|---|---|---|---|
| `OX0101` expected token | 4 | parse | `expected '}' / ')' / field name, found EOF` |
| `OX0103` expected type | 1 | parse | `expected type, found EOF` |
| `OX0203` duplicate top-level name | 3 | resolve | `'main'`, `'push'`, and one 36-diagnostic degenerate cascade |
| `OX0200` unknown identifier | 1 | resolve | **`unknown identifier 'Vec'`** — not `to_str` |

Five of the nine never reach name resolution at all: they die in the
parser, truncated mid-program. The remaining four reach the resolver and
fail there on something unrelated — a `Vec` type name, a duplicate
`main`, a duplicate `push` followed by an unknown `Stack`. Searching
every diagnostic of all nine programs, **no diagnostic anywhere reports
`to_str` as an unresolved name.** The only `to_str`-substring diagnostic
in the set is `duplicate top-level name 'int_to_str'`, inside the
degenerate cascade — a self-definition clash, not a missing conversion.

The composition of the nine explains why. Six self-define `fn to_str`
(the 15 occurrences of endpoint 5); four use the receiver form
`x.to_str()` (10 occurrences); one does both. **Zero make a bare plain
call to an undefined `to_str`.** A model that reaches for this spelling
either brings its own definition or writes it as a method — so there was
no unresolved-call population for the alias to convert, which is the same
fact endpoint 4 reports from the counter's side.

Post-change the picture is identical in kind: 11 programs, 0 compiled, 0
passed, first diagnostics `OX0101` ×4, `OX0103` ×2, `OX0203` ×4,
`OX0200` ×1 — with one of those `OX0203`s now being the new
`duplicate top-level name 'to_str'` clash described in endpoint 5.

**What this means, and it is a real result rather than a null.** The wall
these programs hit is not the language's builtin set. It is the lexer and
the parser, and behind them a handful of unrelated top-level name
clashes. Resolution-stage ergonomics — which is what a builtin alias
buys — is downstream of where these models are actually failing, so
alias-shaped fixes cannot pay off until the upstream failures are fixed.
That bears directly on whether the remaining conversion-name candidates
in taxonomy dossier 3 are worth spending SPEC surface on: on this
evidence, they are not, and the marginal fix should be aimed at
truncation and parse failure instead. It also sets the ceiling the power
argument only estimated: at 9–11 carrier programs per 600, and 0 of them
compiling in any corpus before or after, the largest possible effect on
first-compile rate was **0.0pp**, not "below the ±5pp floor".

## Comparability statement, verified against each cited run's own manifest

| run | manifest `backend` (top-level) | oxide/explicit constrained? | rust constrained? | `num_ctx` (qwen/codegemma/granite) | grammar SHA (oxide / explicit) | `preflight.build_info` |
|---|---|---|---|---|---|---|
| `g0c` (G0 baseline) | `llamacpp` (no dot, literal) | yes | **never** | 8192 / 8192 / 4096 | `3a6c5ff1…` / `c68d335f…` | `b1-4988f6e` |
| `g1c` (vec-literal) | `llama.cpp` | yes | **never** | 8192 / 8192 / 4096 | `3a6c5ff1…` / `c68d335f…` (identical to g0c) | `b1-4988f6e` |
| `v03c` (this run) | `llama.cpp` | yes | **never** | 8192 / 8192 / 4096 | `051e8cc5…` / `d40a4923…` (**different** — §56 widened both grammars) | `b1-4988f6e` |

The top-level `backend` string genuinely differs across these three
runs' manifests, verbatim — `g0c`'s literally reads `"llamacpp"` (no
dot), `g1c`'s and `v03c`'s both read `"llama.cpp"`. This is not a
different backend: `manifest["preflight"]["backend"]` reads
`"llama.cpp"` in all three (including g0c), and `preflight.build_info`
matches (`b1-4988f6e`) across all three, confirming the identical
llama-server/llama.cpp system throughout. The top-level field's
spelling changed because `eval.driver.BACKEND_LABELS` (a fix that
normalizes the CLI's `"llamacpp"` token to the manifest's canonical
`"llama.cpp"`, added specifically so the top-level field and
`preflight.backend` cannot read as two different backends) landed after
G0 and was already in place for g1c and v03c. Printed here exactly as
each manifest reads, per this table's own header, rather than
normalized.

Read directly from each run's own `manifest.json` (`grammar_sha256`,
`num_ctx`, `preflight.build_info`, `preflight.server_n_ctx`), not
recalled. All three runs are constrained on the oxide and explicit arms
and never constrained on rust — the rust-vs-oxide/explicit comparability
that matters for the pre-registration's rust-flat claim holds
throughout. The one thing that is **not** identical: v03c's GBNF differs
from g0c's/g1c's, because §56 added field-assignment production rules to
both grammars (§57 needed no grammar change — `to_str` is an ordinary
call, already admitted). That grammar delta is exactly the mechanism
under test in endpoint 2, not an incidental confound — it is why the
elimination is attributable to §56 rather than to noise. g0c and g1c
share byte-identical grammars (vec-literal is pure parse-time desugar,
no new admitted syntax), so the g1c comparison in endpoint 6 is not
carrying a hidden grammar change on top of the language change.

## What this does not show

- **One shot condition, one 20-task corpus.** SPEC §47's own power
  analysis: this design cannot resolve a true effect below roughly
  ±5pp. Both `s.f = e` and `to_str` sit at an estimated ~1.5–2%
  prevalence in generated programs — mechanically incapable of moving
  an aggregate pass rate by anything this design could detect, which is
  exactly why the null in endpoint 6 is read as a successful
  confirmation and not a disappointment. For the `to_str` half this is
  the **secondary** argument only: the primary explanation is
  mechanical, not statistical — see "The g3 null is mechanical" above,
  where every one of the 9/11 carrier programs is shown to fail in the
  parser or on an unrelated name clash, so the true effect was bounded
  at 0.0pp rather than merely below the floor. The power argument is
  retained because it is what the pre-registration committed to and it
  still governs the `s.f = e` half.
- **The self-definition counter is textual, not parse-gated** (by
  design — see `eval/demand.py`'s own docstring): it matches `fn
  to_str(` in raw text including inside comments or strings, and is an
  upper bound on genuine source occurrences, not an exact count.
- **granite carries its 4096-window covariate throughout** (SPEC §48 —
  8192 exceeds granite's own training context) plus a ~16.5% context-
  exhaustion rate in this run, both inherited unchanged from G0 and
  g1c.
- **This run is constrained-only.** The `0 → 0` unconstrained g2 cell is
  supported by G0's pre-change unconstrained root and a stated
  mechanism, not by a new v0.3-HEAD unconstrained measurement.
- **The primary purpose of this campaign is the baseline itself** — the
  comparison point for the token-matched LoRA fine-tune track, not a
  further language-design finding. The g2/g3 scorecard above is a
  secondary, pre-registered check riding along on data that was going
  to be collected anyway.
- **Four hours of the campaign's wall-clock were idle** (see Method
  note); this affected timing only, not any recorded session.

## Provenance

| | qwen | codegemma | granite |
|---|---|---|---|
| model tag | `qwen2.5-coder:7b-instruct-q8_0` | `codegemma:7b-instruct-q8_0` | `granite-code:8b-instruct-q8_0` |
| GGUF blob digest | `sha256:24b532e5276503b147d0eea0e47cb1d2bcce7c9034edd657b624261862ca54a1` | `sha256:20b20ee7b4265a5872bd58d669c394206f50418f3523b47563c9b1d4a78f37cb` | `sha256:7f84501f2a7043104d50d8387b0a5c5ea0dd8149996b08b15103ceb28427eb9f` |
| quantization | q8_0 | q8_0 | q8_0 |
| `num_ctx` | 8192 | 8192 | **4096** (own training context, SPEC §48) |

Resolved from ollama's own store manifests
(`/mnt/extra/ollama-models/manifests/registry.ollama.ai/library/<model>/<tag>`,
the `application/vnd.ollama.image.model` layer's digest) and confirmed
against each run's own `preflight.model_path` before generation started
(`--expect-model-path`, the stale-server guard). `manifest.json`'s
top-level `digest`/`quantization_level` fields read `null` for every
run in this campaign — expected, not a gap: those fields are populated
from Ollama's `/api/tags` and this campaign runs the llamacpp backend
throughout (SPEC §48), so quantization is asserted instead via the
pinned tag string and the resolved blob above.

Shared across all three families: backend `llama.cpp` (Vulkan build
`b1-4988f6e`, `~/llama.cpp/build-vk/bin/llama-server`, `-ngl 99`),
temperature 0.8, top_p 0.95, `num_predict` 2048, shots 0, seeds 1–10,
attempt cap 4, run-id prefix `v03c`, results root
`eval/results/v03-closing-baseline/` (flat — no constrained/unconstrained
split; this campaign is constrained-only). Grammar SHA256: oxide
`051e8cc5fc14e50276ae020cb41c07533bde269a7be419be1bede0c8ae977d84`,
explicit `d40a4923c4d519fa2a6c57ffc920a49268243e77bf584b67e1ac54d00bf5f8c3`,
rust `null` (never constrained, by design — rustc's own diagnostics are
the control).

Raw: 30 run dirs (`v03c-<slug>-0shot-s1`…`s10`), `cells.jsonl` +
`triples.jsonl` + `manifest.json` + `raw/` each, ~31MB total. Analysis:
`python -m eval.g0_report --root eval/results/v03-closing-baseline
--models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix v03c`;
`python -m eval.deformation eval/results/v03-closing-baseline`;
`eval.demand.scan_oxide_arm(Path('eval/results/v03-closing-baseline'))`.
The carrier-program and first-diagnostic breakdowns in "The g3 null is
mechanical" are read from each run dir's `triples.jsonl`, filtered to
`arm == "oxide"` and `attempt == 1`, selecting programs whose `code`
matches `(?<![A-Za-z_0-9])to_str(?![A-Za-z_0-9])` (which excludes
`int_to_str`) and tallying `diagnostics[0]["code"]`; the same filter over
`g0c` and `g1c` produces their rows. Endpoint 4's cross-corpus zeros are
`scan_oxide_arm` re-run at current HEAD against
`eval/results/g0-generation-baseline/constrained` and
`eval/results/g1-vec-literal/constrained`.
The paired first-pass deltas quoted above (`eval.rollup`) were read via
`eval.g0_report`'s own call into `rollup.paired_delta`/`paired_se`, not
`eval.rollup`'s CLI. If reproducing them by invoking
`python -m eval.rollup` directly against this root, pass `--out`
explicitly: its default is `<results-root>/6a-rollup/`, which would
otherwise write an untracked directory into this committed data root.

## What happens next

This baseline is the fine-tune track's comparison point. Shipping
v0.3 itself (synthesis REPORT, README results section, SPEC version
bump v0.2.2 → v0.3, tag) is deliberately out of scope for this task —
it follows this campaign as its own piece of work, per the plan.
