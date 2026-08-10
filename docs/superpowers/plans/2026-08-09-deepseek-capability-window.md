# DeepSeek Capability-Window Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run DeepSeek-Coder-V2-Lite through the existing ownership probe to test — falsifiably — whether the oxide−explicit repair advantage decays as subject capability rises.

**Architecture:** Three tasks. Register the subject and amend SPEC §48's uniform-quantization invariant into a per-family pin; ship a committed campaign runner whose resume is tested before it is needed; then run 600 repairs and write a REPORT that evaluates a pre-registration written before the data existed.

**Tech Stack:** Python 3.14, pytest, llama.cpp (Vulkan build) serving a GGUF resolved from ollama's blob store. No new dependencies.

## Global Constraints

- **SPEC.md is the binding contract.** It is amended in Task 1, before the run. Any deviation between SPEC and code is a bug in the deviating side.
- Run tests with `.venv/bin/pytest tests/ -q` from `/home/brice/workspace/oxide`. Baseline at plan time: **1410 passed, 3 deselected**.
- **Nothing under `eval/results/` may be modified.** It is the committed experimental record. Task 3 *adds* a new directory; it changes nothing existing.
- **Do not edit** `LANGUAGE_CARD.md`, `LANGUAGE_CARD_EXPLICIT.md`, the `OX0306` suggestion string, the `ARMS`/`arm` data keys, the `__oxide_` codegen prefix, `.ox`, or `eval/grammar/oxide.gbnf` (SPEC §0 frozen surfaces).
- Files stay under 800 lines. **Exception, pre-existing and out of scope:** `eval/grammar/build.py` is 857 lines; do not touch it and do not "fix" it.
- Commit messages omit any Claude/AI attribution.
- **The pinned subject:** `deepseek-coder-v2:16b-lite-instruct-q5_K_M`, ollama digest `6065d4880bf9`, GGUF blob `sha256-bc286970a24072cf23a4c905f28adb9f6a28c71743b07790185275a86dc72406` under `/mnt/extra/ollama-models/blobs/`. Already pulled; do not re-pull.
- **The pre-registration is binding and was written before any data existed.** It is reproduced in Task 3 and must not be softened after seeing results.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `SPEC.md` §48 | quantization: uniform → per-family pin | 1 |
| `eval/driver.py` | `MODELS` entry, new `QUANT` dict | 1 |
| `tests/test_6a.py` | rename the q8 roster test, add `QUANT` test, correct stale "FIVE slugs" comments | 1 |
| `eval/probe_campaign.py` (new) | 30-cell campaign, provenance, skip-complete resume | 2 |
| `tests/test_probe_campaign.py` (new) | resume logic + a stub-client end-to-end | 2 |
| `eval/results/ownership-probe-deepseek/` (new) | run output + REPORT | 3 |

---

### Task 1: Register the subject, amend §48

**Files:**
- Modify: `SPEC.md` (§48's parameter table and the quantization paragraph)
- Modify: `eval/driver.py` (`MODELS` ~line 196; new `QUANT` beside `NUM_CTX` ~line 214; stale comment ~line 627)
- Modify: `tests/test_6a.py` (roster test ~line 1283; stale comment ~line 1226)

**Interfaces:**
- Consumes: nothing.
- Produces: `driver.MODELS["deepseek16b_lite"] -> "deepseek-coder-v2:16b-lite-instruct-q5_K_M"`, and `driver.QUANT: dict[str, str]` mapping slug → quantization string, with `DEFAULT_QUANT = "q8_0"` for absent slugs.

- [ ] **Step 1: Write the failing tests**

In `tests/test_6a.py`, **replace** `test_model_slugs_map_to_pinned_q8_tags` entirely. Its name encodes the invariant being amended — a test called `..._q8_tags` that admits a `q5_K_M` entry is a lie the next reader will believe.

```python
def test_model_slugs_map_to_pinned_tags():
    assert MODELS == {
        "qwen0_5b": "qwen2.5-coder:0.5b-instruct-q8_0",
        "qwen1_5b": "qwen2.5-coder:1.5b-instruct-q8_0",
        "qwen7b": "qwen2.5-coder:7b-instruct-q8_0",
        "codegemma7b": "codegemma:7b-instruct-q8_0",
        "granite8b": "granite-code:8b-instruct-q8_0",
        "deepseek16b_lite": "deepseek-coder-v2:16b-lite-instruct-q5_K_M",
    }


def test_quantization_is_q8_except_where_vram_forbids_it():
    """SPEC section 48: quantization was uniform q8_0; it is now a
    per-family pin, for the same reason num_ctx is. DeepSeek-V2-Lite's
    q8_0 GGUF is 16.70 GB against a 16.30 GB card -- it does not fit the
    card at all, so this is physical, not a policy choice."""
    assert driver.DEFAULT_QUANT == "q8_0"
    assert driver.QUANT == {"deepseek16b_lite": "q5_K_M"}
    for slug in ("qwen0_5b", "qwen1_5b", "qwen7b", "codegemma7b", "granite8b"):
        assert driver.quant_for(slug) == "q8_0"
    assert driver.quant_for("deepseek16b_lite") == "q5_K_M"


def test_deepseek_takes_the_default_context_window():
    """The OPPOSITE of granite: DeepSeek-V2 trains at 163840, so 8192 is
    satisfiable and no cap applies. llama-server prints an under-use
    notice, not the 'exceeds the training context ... capping' line."""
    assert "deepseek16b_lite" not in driver.NUM_CTX
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_6a.py -q -k "pinned_tags or quantization_is_q8 or default_context_window"`
Expected: FAIL — `MODELS` has no `deepseek16b_lite`, and `driver.QUANT` / `driver.DEFAULT_QUANT` / `driver.quant_for` do not exist.

- [ ] **Step 3: Add the roster entry and the QUANT pin**

In `eval/driver.py`, add to `MODELS`:

```python
    "deepseek16b_lite": "deepseek-coder-v2:16b-lite-instruct-q5_K_M",
```

Immediately after the `NUM_CTX` dict, add:

```python
# Per-slug quantization pin (SPEC section 48). Quantization WAS uniform
# q8_0 across the ladder, held constant so the capability curve was not
# confounded. DeepSeek-V2-Lite breaks that physically rather than
# editorially: MoE activates 2.4B of ~16B parameters per token but every
# expert must be VRAM-resident, so the whole weight set must fit, and its
# q8_0 GGUF is 16.70 GB against a 16.30 GB card -- it does not fit the
# card even with nothing else running. This is the roster's growth path,
# not a DeepSeek quirk: on 16 GB, any subject stronger than this ladder
# needs sub-q8. Treated exactly as NUM_CTX treats granite's 4096 -- pinned
# per family, arm-fair WITHIN the slug, recorded, and read as a covariate.
DEFAULT_QUANT = "q8_0"
QUANT = {
    "deepseek16b_lite": "q5_K_M",
}


def quant_for(slug: str) -> str:
    """The pinned quantization for one slug (SPEC section 48)."""
    return QUANT.get(slug, DEFAULT_QUANT)
```

- [ ] **Step 4: Correct the two stale "FIVE slugs" comments**

There are six slugs now. In `eval/driver.py` (~line 627) and `tests/test_6a.py` (~line 1226), change "ALL FIVE slugs" to "ALL SIX slugs". The driver comment documents a real guard — llama-server serves one model, so multi-slug llamacpp runs are refused — and that guard's test must still pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: green. Baseline 1410 + 2 net new (one test replaced, two added) = **1412 passed**.

If `test_main_refuses_llamacpp_with_more_than_one_slug` fails, the new slug changed the default `--models` list — read the guard, do not weaken the test.

- [ ] **Step 6: Amend SPEC §48**

Two edits inside §48. In the parameter table, change the Quantization row from:

```
| Quantization | uniform `q8_0` across the ladder |
```

to:

```
| Quantization | `q8_0`, **per-family** — see below (`q5_K_M` for `deepseek-coder-v2:16b-lite`) |
```

And extend the paragraph that currently reads "Quantization is held constant so the capability curve is not confounded" with:

```markdown
**The quantization pin is per-family, not universal — for the same reason
`num_ctx` is.** `deepseek-coder-v2:16b-lite` is served at `q5_K_M`
(`eval.driver.QUANT`; every other slug takes `DEFAULT_QUANT = q8_0`). MoE
activates 2.4B of ~16B parameters per token, but every expert must be
VRAM-resident, so the full weight set is what must fit: its `q8_0` GGUF is
**16.70 GB** against a **16.30 GB** card. It does not fit the card even
with nothing else running, so this is physically forced, not a policy
choice — exactly the shape of granite's 4096 window. The pin is applied
uniformly across all three arms of that slug, so it stays arm-fair
*within* the model, and it is recorded per run and read as a per-family
covariate. Quantization is a capability reduction, so on a non-monotonic
capability curve its direction of bias depends on which side of the peak
the subject sits; the run's REPORT must state that rather than assume it.
```

- [ ] **Step 7: Re-run the suite and commit**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -1`

```bash
git add SPEC.md eval/driver.py tests/test_6a.py
git commit -m "feat(eval): register deepseek-coder-v2:16b-lite, pin quantization per family

Section 48 held quantization uniform at q8_0 so the capability curve was
not confounded. That invariant cannot survive this subject, and the reason
is physical: MoE activates 2.4B of ~16B parameters but every expert must be
VRAM-resident, and the q8_0 GGUF is 16.70 GB against a 16.30 GB card. It
does not fit the card with nothing else running.

This is the roster's growth path rather than a DeepSeek quirk -- on 16 GB,
any subject stronger than the current ladder needs sub-q8 -- so the pin
copies the shape section 48 already uses for granite's 4096 num_ctx:
per-family, arm-fair within the slug, recorded, read as a covariate.

The roster test's NAME encoded the old invariant, so it is renamed rather
than extended: a test called ..._q8_tags that admits a q5_K_M entry is a
lie the next reader believes. DeepSeek deliberately gets NO NUM_CTX entry
-- it trains at 163840, so 8192 is satisfiable and llama-server prints an
under-use notice, the opposite of granite's capping line."
```

---

### Task 2: The campaign runner, with resume tested before it is needed

**Files:**
- Create: `eval/probe_campaign.py`
- Test: `tests/test_probe_campaign.py`

**Interfaces:**
- Consumes: `eval.probe.run_corpus`, `eval.probe.load_probes`, `eval.probe._select`, `eval.llamacpp.LlamaCppClient`, `eval.harness.ARMS`.
- Produces:
  - `cell_dir(root: Path, arm: str, seed: int) -> Path` → `root / f"{arm}-s{seed}"`
  - `is_complete(cell: Path) -> bool` — true iff `probe_summary.json` exists
  - `pending_cells(root: Path, arms: tuple[str, ...], seeds: tuple[int, ...]) -> list[tuple[str, int]]`
  - `reset_partial(cell: Path) -> bool` — removes a started-but-unfinished cell, returns whether it removed anything

**Why a runner at all:** `eval/probe.py run` takes ONE seed per invocation and runs every probe for one arm, so a 3-arm × 10-seed campaign is 30 invocations. Measured headroom is **0.51 GB** after one repair, on a build that throws intermittent `vk::DeviceLostError` under sustained load — a failure that has already killed a 600-repair run on this machine. And a scratch script that dies with the session is how the 6a pilot's demand table became permanently irreproducible.

**The resume design, and why it is shaped this way:** `run_corpus` already appends one line per probe to `probe_results.jsonl`, and it deliberately **raises** if that file exists, to stop two runs interleaving in one file. Work with that guard, not around it:

- `probe_summary.json` is written only after every probe in a cell finishes, so **its presence is the completion marker**.
- A cell with `probe_results.jsonl` but no summary died mid-cell; delete the cell directory and redo it.
- Worst case loses one cell — 20 repairs, a minute or two — never the campaign.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_probe_campaign.py`:

```python
"""Resume coverage for the probe campaign runner.

Checkpoint logic that has never been resumed is checkpoint logic that does
not work. These tests exercise the resume path directly rather than
assuming it, because the run it protects costs 600 repairs on a GPU with
0.51 GB of headroom.
"""

import json

from eval import probe_campaign as pc


def test_cell_dir_is_arm_and_seed(tmp_path):
    assert pc.cell_dir(tmp_path, "oxide", 3) == tmp_path / "oxide-s3"


def test_a_cell_is_complete_only_with_a_summary(tmp_path):
    """probe_summary.json is written only after every probe in the cell
    finishes, so it -- not the results file -- is the completion marker."""
    cell = pc.cell_dir(tmp_path, "oxide", 1)
    cell.mkdir(parents=True)
    assert not pc.is_complete(cell)

    # died mid-cell: results present, no summary
    (cell / "probe_results.jsonl").write_text('{"id": "p01"}\n', encoding="utf-8")
    assert not pc.is_complete(cell)

    (cell / "probe_summary.json").write_text("{}", encoding="utf-8")
    assert pc.is_complete(cell)


def test_pending_skips_complete_cells_only(tmp_path):
    arms, seeds = ("oxide", "explicit"), (1, 2)
    assert len(pc.pending_cells(tmp_path, arms, seeds)) == 4

    done = pc.cell_dir(tmp_path, "oxide", 1)
    done.mkdir(parents=True)
    (done / "probe_summary.json").write_text("{}", encoding="utf-8")

    pending = pc.pending_cells(tmp_path, arms, seeds)
    assert ("oxide", 1) not in pending
    assert len(pending) == 3


def test_reset_partial_clears_a_half_finished_cell(tmp_path):
    """run_corpus REFUSES to append into an existing results file, so a
    half-finished cell must be cleared before it can be redone."""
    cell = pc.cell_dir(tmp_path, "oxide", 1)
    cell.mkdir(parents=True)
    (cell / "probe_results.jsonl").write_text('{"id": "p01"}\n', encoding="utf-8")
    assert pc.reset_partial(cell) is True
    assert not cell.exists()


def test_reset_partial_refuses_to_touch_a_complete_cell(tmp_path):
    """Deleting a finished cell would silently discard real results."""
    cell = pc.cell_dir(tmp_path, "oxide", 1)
    cell.mkdir(parents=True)
    (cell / "probe_summary.json").write_text("{}", encoding="utf-8")
    assert pc.reset_partial(cell) is False
    assert (cell / "probe_summary.json").is_file()


def test_resume_runs_each_cell_exactly_once(tmp_path, monkeypatch):
    """The property that matters: after an interruption, resume runs the
    unfinished cells and NO finished one."""
    ran: list[tuple[str, int]] = []

    def fake_run_cell(root, arm, seed, client_factory):
        ran.append((arm, seed))
        cell = pc.cell_dir(root, arm, seed)
        cell.mkdir(parents=True, exist_ok=True)
        (cell / "probe_summary.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pc, "run_cell", fake_run_cell)

    arms, seeds = ("oxide", "explicit"), (1, 2)
    pc.run_campaign(tmp_path, arms, seeds, client_factory=lambda arm: None)
    assert sorted(ran) == [("explicit", 1), ("explicit", 2),
                           ("oxide", 1), ("oxide", 2)]

    ran.clear()
    pc.run_campaign(tmp_path, arms, seeds, client_factory=lambda arm: None)
    assert ran == []  # everything already complete


def test_provenance_is_written_once_per_campaign(tmp_path):
    pc.write_provenance(tmp_path, {"model": "deepseek", "n_ctx": 8192})
    obj = json.loads((tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert obj["model"] == "deepseek"
    assert obj["n_ctx"] == 8192
```

- [ ] **Step 2: Run them to confirm they fail**

Run: `.venv/bin/pytest tests/test_probe_campaign.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval.probe_campaign'`

- [ ] **Step 3: Write the runner**

Create `eval/probe_campaign.py`:

```python
"""Drive the ownership probe across arms and seeds, resumably.

``eval.probe run`` takes ONE seed and runs every probe for ONE arm, so a
3-arm x 10-seed campaign is 30 invocations. This lives in the repo rather
than a scratch script for the reason the 6a pilot's demand table is now
permanently irreproducible: its filter existed only in a session that
ended.

RESUME. ``run_corpus`` appends one line per probe to
``probe_results.jsonl`` and deliberately RAISES if that file already
exists, so two runs cannot interleave in one file. This module works with
that guard rather than around it:

  * ``probe_summary.json`` is written only after every probe in a cell
    finishes -- its presence is the completion marker.
  * A cell with results but no summary died mid-cell: delete it and redo.
  * Worst case loses one cell (20 repairs), never the campaign.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from eval.probe import _select, load_probes, run_corpus

SEEDS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def cell_dir(root: Path, arm: str, seed: int) -> Path:
    """One (arm, seed) cell's output directory."""
    return Path(root) / f"{arm}-s{seed}"


def is_complete(cell: Path) -> bool:
    """True iff this cell finished. ``probe_summary.json`` is written only
    after the last probe, so a results file alone does NOT count."""
    return (Path(cell) / "probe_summary.json").is_file()


def reset_partial(cell: Path) -> bool:
    """Remove a started-but-unfinished cell so ``run_corpus`` will accept
    it again. Refuses to touch a complete cell -- deleting one would
    silently discard real results."""
    cell = Path(cell)
    if not cell.exists() or is_complete(cell):
        return False
    shutil.rmtree(cell)
    return True


def pending_cells(
    root: Path, arms: tuple[str, ...], seeds: tuple[int, ...]
) -> list[tuple[str, int]]:
    """Every (arm, seed) that has not finished, in run order."""
    return [
        (arm, seed)
        for arm in arms
        for seed in seeds
        if not is_complete(cell_dir(root, arm, seed))
    ]


def write_provenance(root: Path, payload: dict) -> Path:
    """Persist the run's provenance. The probe CLI never calls
    ``LlamaCppClient.preflight()``, so without this a probe run records
    nothing about which weights produced it."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "provenance.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def run_cell(
    root: Path, arm: str, seed: int, client_factory: Callable[[str], object]
) -> None:
    """One (arm, seed): every probe in the corpus for that arm."""
    cell = cell_dir(root, arm, seed)
    reset_partial(cell)
    records = _select(load_probes(None), None, arm)
    run_corpus(client_factory(arm), records, out_dir=cell, seed=seed)


def run_campaign(
    root: Path,
    arms: tuple[str, ...],
    seeds: tuple[int, ...],
    *,
    client_factory: Callable[[str], object],
) -> list[tuple[str, int]]:
    """Run every unfinished cell. Returns the cells it ran."""
    ran: list[tuple[str, int]] = []
    for arm, seed in pending_cells(root, arms, seeds):
        run_cell(root, arm, seed, client_factory)
        ran.append((arm, seed))
    return ran
```

- [ ] **Step 4: Run the runner tests**

Run: `.venv/bin/pytest tests/test_probe_campaign.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Prove resume works against the REAL `run_corpus`, not a stub**

The stub test proves the loop skips finished cells. This proves the interaction with `run_corpus`'s append-guard, which is the part that actually bites. Add to `tests/test_probe_campaign.py`:

```python
def test_reset_partial_unblocks_run_corpus_append_guard(tmp_path):
    """run_corpus RAISES on an existing results file. Resuming a cell that
    died mid-way therefore depends on reset_partial having cleared it --
    this pins that interaction, which the stub test cannot see."""
    import pytest

    from eval.models import Generation
    from eval.probe import ProbeError, load_probes, _select, run_corpus

    class StubClient:
        def generate(self, prompt: str, *, seed: int) -> Generation:
            return Generation(text="fn main() { }", tokens_in=1, tokens_out=1,
                              ms=1, truncated=False)

    records = _select(load_probes(None), "p01", "oxide")
    cell = pc.cell_dir(tmp_path, "oxide", 1)

    run_corpus(StubClient(), records, out_dir=cell, seed=1)
    assert pc.is_complete(cell)

    # a second run into the same directory is refused, by design
    with pytest.raises(ProbeError):
        run_corpus(StubClient(), records, out_dir=cell, seed=1)

    # and reset_partial must REFUSE to clear it, because it is complete
    assert pc.reset_partial(cell) is False
```

- [ ] **Step 6: Run and commit**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -1`
Expected: 1412 + 8 = **1420 passed**

```bash
git add eval/probe_campaign.py tests/test_probe_campaign.py
git commit -m "feat(eval): resumable ownership-probe campaign runner

eval.probe run takes one seed and one arm, so a 3-arm x 10-seed campaign is
30 invocations. Measured headroom for the DeepSeek subject is 0.51 GB after
one repair, on a build that throws intermittent device-lost under sustained
load -- a failure that has already killed a 600-repair run here.

Resume works WITH run_corpus's append-guard rather than around it.
run_corpus writes probe_summary.json only after the last probe in a cell,
so the summary is the completion marker; a cell with results but no summary
died mid-way and is deleted and redone. Worst case loses 20 repairs.

reset_partial refuses to delete a COMPLETE cell -- that would silently
discard real results -- and one test drives the real run_corpus to pin that
interaction, since the stub test cannot see the append-guard at all.

Also carries provenance: the probe CLI never calls preflight(), so without
this a probe run records nothing about which weights produced it."
```

---

### Task 3: Run it, and report against the pre-registration

**Files:**
- Create: `eval/results/ownership-probe-deepseek/` (campaign output)
- Create: `eval/results/ownership-probe-deepseek/REPORT.md`

**Interfaces:**
- Consumes: `eval.probe_campaign.run_campaign`, `eval.llamacpp.LlamaCppClient`.
- Produces: the run record and the REPORT. Nothing downstream consumes these programmatically.

**THE PRE-REGISTRATION — written before any data existed. Do not soften it.**

> **If** DeepSeek's `rust` arm scores **above qwen's 89.0**, **then** its
> `oxide − explicit` delta scores **below qwen's +59.0**.
>
> **If** the `rust` arm scores **at or below 89.0**, this run does not test
> the window and is reported **INCONCLUSIVE** — not reinterpreted.

Reference points, from `eval/results/ownership-probe-10seed/`:

| subject | rust | oxide | explicit | delta |
|---|---|---|---|---|
| granite-code-8b | 73.0 | 20.5 | 11.0 | +9.5 |
| codegemma-7b | 84.5 | 46.5 | 11.5 | +35.0 |
| qwen2.5-coder-7b | 89.0 | 73.0 | 14.0 | **+59.0** |
| Claude Opus 5 | 100 | 92 | 92 | 0.0 |

- [ ] **Step 1: Start the server and confirm it is YOURS**

```bash
pgrep -af "bin/llama-server" || echo "no server"
ss -lntp | grep ":8081 " || echo "port free"
```

`pgrep -f llama-server` **self-matches** (the pgrep's own command line contains the string) — always match `bin/llama-server` or check the port. A stale server holds the port and answers the health check from the WRONG weights; that has silently killed a 600-repair run here before.

Then start it in the background (never with a foreground `sleep`, which the harness kills):

```bash
GGUF=/mnt/extra/ollama-models/blobs/sha256-bc286970a24072cf23a4c905f28adb9f6a28c71743b07790185275a86dc72406
~/llama.cpp/build-vk/bin/llama-server -m "$GGUF" --port 8081 -ngl 99 -c 8192
```

**Measured headroom is 0.51 GB after one repair** (15.33 GB used of 16.30 GB). It has loaded and generated correctly at these settings, so expect it to work — but if the server fails to load, OOMs, or throws `vk::DeviceLostError` repeatedly:

> **Fall back on QUANTIZATION, never on `num_ctx`.** Re-pull at `q4_K_M`
> (`ollama pull deepseek-coder-v2:16b-lite-instruct-q4_K_M`, 10.36 GB,
> ≈1.5 GB more headroom), update `driver.QUANT["deepseek16b_lite"]` to
> `"q4_K_M"`, update the SPEC §48 sentence and the Task 1 test, and record
> the change in the REPORT.
>
> Reducing `num_ctx` instead would deviate on a **second** axis — stacking
> two confounds where this design budgeted for one — and would break
> comparability with the 8192 the other three families ran at. `num_ctx`
> stays 8192 whatever happens.

- [ ] **Step 2: Verify the window and capture provenance BEFORE spending 600 repairs**

```bash
.venv/bin/python -c "
from eval.llamacpp import LlamaCppClient
from eval.probe_campaign import write_provenance
from pathlib import Path
c = LlamaCppClient('deepseek16b_lite')
p = c.preflight()
p.update({
    'slug': 'deepseek16b_lite',
    'tag': 'deepseek-coder-v2:16b-lite-instruct-q5_K_M',
    'ollama_digest': '6065d4880bf9',
    'quantization': 'q5_K_M',
    'num_ctx': 8192,
})
print(write_provenance(Path('eval/results/ownership-probe-deepseek'), p))
print(p)
"
```

`preflight()` raises if the server's `n_ctx` is below the pinned 8192 — the one place a wrong `-c` is caught before the run is built on it. If it raises, restart the server with `-c 8192`; do not lower the pin.

- [ ] **Step 3: Run the campaign**

```bash
.venv/bin/python -c "
from pathlib import Path
from eval.llamacpp import LlamaCppClient
from eval.probe_campaign import run_campaign, SEEDS
from eval.harness import ARMS
ran = run_campaign(Path('eval/results/ownership-probe-deepseek'), ARMS, SEEDS,
                   client_factory=lambda arm: LlamaCppClient('deepseek16b_lite'))
print(f'ran {len(ran)} cells')
"
```

30 cells × 20 probes = **600 repairs**, roughly 20–25 minutes. **No grammar constraint** — the probe supplies a syntactically correct program and only the ownership defect is wrong, so a grammar would test nothing here (`--grammar` is not passed, matching how the other three families were run).

If it dies mid-run, re-run the identical command. Finished cells are skipped; the half-finished one is redone.

- [ ] **Step 4: Aggregate the three arms**

```bash
.venv/bin/python -c "
import json
from pathlib import Path
root = Path('eval/results/ownership-probe-deepseek')
for arm in ('oxide', 'explicit', 'rust'):
    strict = tot = 0
    for seed in range(1, 11):
        for line in (root / f'{arm}-s{seed}' / 'probe_results.jsonl').read_text().splitlines():
            r = json.loads(line)
            strict += bool(r['strict']); tot += 1
    print(f'{arm:9} {100*strict/tot:5.1f}%  ({strict}/{tot})')
"
```

- [ ] **Step 5: Apply the pre-registration, in this order**

1. **Read the `rust` arm first.** If it is **≤ 89.0**, the verdict is **INCONCLUSIVE** — write that, and stop. Do not compute a delta and narrate it. The subject was not established as stronger than qwen, so the window was not tested.
2. If the `rust` arm is **> 89.0**, compute `oxide − explicit`. **Below +59.0** confirms; **at or above +59.0** falsifies the window and that must be stated as plainly as a confirmation would be.

- [ ] **Step 6: Write the REPORT**

Create `eval/results/ownership-probe-deepseek/REPORT.md` following the house style of `eval/results/ownership-probe-10seed/REPORT.md` — a report that states its own limits. It must contain:

- The pre-registration **quoted verbatim from this plan**, before the results.
- The three arm rates and the delta, with the four reference subjects for context.
- The verdict: CONFIRMED / FALSIFIED / INCONCLUSIVE, by Step 5's rule.
- **The quantization caveat, in these terms:** this subject ran at `q5_K_M` against the other three families' `q8_0`, because its `q8_0` GGUF is 16.70 GB against a 16.30 GB card. Quantization is a capability reduction, and on a non-monotonic curve the direction of bias depends on which side of the peak the subject sits — if DeepSeek is above the peak, quantizing moves it *toward* qwen's position and therefore *against* the prediction, making a confirmation conservative. **State plainly that this is an argument, not a measurement**, and that the clean settlement (running two quantizations) was scoped out.
- **What this does not show:** one MoE subject cannot separate "MoE" from "stronger" from "different pretraining mix"; the window remains descriptive over five points, not causal; and the three-family results are untouched by this run.
- The provenance: tag, digest, quantization, `num_ctx`, llama.cpp build.

- [ ] **Step 7: Commit**

```bash
git add eval/results/ownership-probe-deepseek/
git commit -m "data(eval): DeepSeek-Coder-V2-Lite ownership probe — capability-window test

600 repairs, 20 classes x 3 arms x 10 seeds, q5_K_M, num_ctx 8192, no
grammar constraint -- matching how the other three families were run.

The pre-registration was written before the model was pulled and is quoted
in the REPORT ahead of the results: if the rust arm beats qwen's 89.0 the
oxide-explicit delta must land below qwen's +59.0, and if the rust arm does
not beat 89.0 the run is INCONCLUSIVE rather than reinterpreted.

The REPORT states the q5_K_M-vs-q8_0 confound as an argument rather than a
measurement, and records that one MoE subject cannot separate MoE from
stronger from different-pretraining-mix."
```

---

## After the plan

The three-family results and the g3 campaign are untouched by this work. g3 remains the next track: its endpoint folds in g2's deformation signature, with the aggregate null pre-registered and the `18 → 0` figure cited as a lower bound.

If the run comes back CONFIRMED, the natural follow-up is the two-quantization control that would convert the confound argument into a measurement. If it comes back FALSIFIED, the capability-window framing in the README's "Where the evidence stands" section needs revisiting before v0.3 ships.
