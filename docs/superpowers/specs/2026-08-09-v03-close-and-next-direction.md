# Direction memo — closing v0.3, and what the next phase should be

**Date:** 2026-08-09
**Status:** recommended direction, endorsed by Brice in session; individual
items still get their own gates (SPEC extensions, ship decision) before
becoming binding.

## Assessment the recommendation rests on

- The project's strongest asset is the METHOD: the matched-novelty control
  (explicit Black Oxide), pre-registered dose-response predictions, three-family
  replication, the withdrawn-claims log, and instrument-artifact hunting.
- Supported findings: implicit linearity beats explicit for repair (~+10pp
  clean); surface ergonomics dominate ownership semantics ~4× for
  writability; grammar-constrained decoding deforms rather than rejects
  (three demonstrated instances); diagnostics are a first-class lever.
- NOT supported: "LLMs write Black Oxide more reliably than Rust." G0: rust
  first-pass 57/45/42% vs oxide 26/14.5/9%; frontier writes Rust better
  (100 vs 92). Zero-shot from a card, pretraining exposure dominates
  language design. The linearity gate did not populate and the remaining
  ergonomics dossiers are unlikely to populate it — the resolve/types wall
  is substantially a knowledge problem, not a design problem.
- Standing caveats: single-authored probe corpus (authorship bias nearly
  justified inverting the ownership default once); 20 tasks with t19/t20
  driving 66% of OX0203.

## The plan

1. **Finish the ergonomics sweep, briefly.**
   - g2 = dossier 2, field assignment (`p.x = 5`) — a real language hole
     independent of any eval. SPEC extension FIRST (feature-class).
   - g3 = dossier 3, conversion builtins (`to_str`, `to_int`) — cheap,
     demand proven and still growing (codegemma OX0306 179→198).
   - Then STOP the loop via the design's competence-wall exit. Do not
     grind dossiers 4-5 hoping the gate moves.
2. **Ship v0.3**: synthesis REPORT (what each change bought, per family),
   README results section, SPEC version bump v0.2.2 → v0.3, tag.
3. **Pivot to the fine-tune track (SPEC §32.4)** — the real experiment,
   where the thesis is falsifiable rather than pre-decided by pretraining
   exposure: compiler-filtered data factory (the grammar + harness already
   generate and verify Black Oxide at scale; triples.jsonl is the verified
   repair dataset by design), token-matched LoRA (Qwen-class ~1.5B/~7B)
   Black Oxide vs Rust, same eval as endpoint. Corpus expansion matters here
   (address the t19/t20 concentration and single-author bias).
4. **In parallel (no GPU): write up two publishable findings** —
   (a) constrained decoding deforms rather than rejects (all error counts
   under grammar constraint are contaminated; three instances, fixes, and
   A/Bs); (b) the ergonomics-vs-ownership decomposition under the
   matched-novelty method. Publish the withdrawn-claims log as part of
   the story — it is a credibility asset.
5. **Reframe the README thesis** to the defensible claims: implicit beats
   explicit ownership under matched novelty; ergonomics dominate semantics
   for writability; zero-shot generation is pretraining-bound; the open
   question is whether the advantage holds when exposure is equalized.
   Alternate positioning worth considering: Black Oxide as the language built
   for verifier-in-the-loop repair (its fail-closed diagnostics are where
   models shine), rather than the language models write best cold.

## Deferred demand ledger (from G0/g1, for the fine-tune-era corpus)

`if let` / pattern-in-condition; type-based overloading; `2.to(n)` range
methods; `.set(i, v)` index assignment; unwrap_or (dossier 5, unmeasured);
builtin-shadowing card line (dossier 4, unmeasured).
