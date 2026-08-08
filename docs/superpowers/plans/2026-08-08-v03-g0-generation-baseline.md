# v0.3 G0 Generation Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the v0.3 G0 baseline — whole-program generation measured at HEAD across 3 families × 3 arms × 20 tasks × 10 seeds under constrained and unconstrained decoding — plus the taxonomy that ranks what to fix first.

**Architecture:** Everything reuses the Part X session machinery (`eval/driver.py` grid → `eval/harness.py` sessions → cells/triples/manifest). New work: a grammar *admission* test (the completeness half of parity), two model-roster slugs, a llamacpp backend with per-arm grammar clients in the driver, a self-validating G0 profiler, and the taxonomy document. The change loop that follows G0 is a repeating protocol, not pre-planned tasks (Appendix A).

**Tech Stack:** Python 3.14 (`.venv/bin/python`), pytest, llama.cpp Vulkan build (`~/llama.cpp/build-vk/bin/llama-server`), GGUF blobs from ollama's store at `/mnt/extra/ollama-models/`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-08-v03-generation-ergonomics-design.md`. SPEC.md is the binding contract — features need a SPEC extension before implementation.
- 10 seeds minimum for any headline number; all three families before believing anything (project method lesson: 3 seeds false-positived twice).
- 0-shot only; grid shape 3 arms × 20 tasks (`SESSIONS_PER_RUN = 60` per run_id holds).
- Primary comparison: oxide vs explicit paired by task; rust is the capability control and is NEVER grammar-constrained.
- `ModelError` = infrastructure: aborts loudly, never recorded as a model failure.
- Analysis scripts validate against already-published numbers before touching new data.
- Full suite green before every commit (`.venv/bin/pytest tests/ -q`, currently 1262 passed / 2 deselected).
- The repo is public: anything committed is published. Commits carry no Claude attribution.
- llama-server: pinned `-c 8192 -ngl 99`, preflight asserted, port ownership verified before trusting a health check (a stale server once answered a probe's health check and killed a 600-repair run).

---

### Task 1: GBNF admission recognizer + grammar completeness test

The soundness direction of grammar parity is already enforced (`test_committed_grammar_matches_the_generator`, `test_sampled_*_never_produce_a_syntax_diagnostic` in `tests/test_6a.py`). This task adds the missing *completeness* direction: every construct the card teaches must be admitted by the grammar, or constrained decoding deforms it (the §54 gluing mechanism). Verified fact: receiver syntax IS in the committed grammar (`pfx-1 ::= "." lname | ...`), so this is a durable gate, not a known-bug hunt.

**Files:**
- Create: `tests/gbnf_recognizer.py` (test helper, not shipped code)
- Create: `tests/test_grammar_admission.py`

**Interfaces:**
- Consumes: `eval/grammar/oxide.gbnf`, `eval/grammar/explicit.gbnf`, `LANGUAGE_CARD.md`, `LANGUAGE_CARD_EXPLICIT.md`, `eval.probe.diagnose(arm, source)` (existing; returns diagnostics for a source string).
- Produces: `load_gbnf(path) -> dict[str, Node]`, `admits(rules: dict, start: str, text: str) -> bool` — used only by this test file.

- [ ] **Step 1: Write the recognizer helper**

A packrat CFG recognizer over the *generated* GBNF subset (rules one per line, literals with `\n \t \" \\` escapes, positive char classes with ranges, groups, `| * + ?`). It raises on any GBNF syntax outside that subset — silent mis-parsing must not fake admission. The generated grammar is deliberately non-left-recursive (that is what its `flat-N` leveling is for), and the in-progress guard turns accidental left recursion into a test failure, not a hang.

```python
"""Recognizer for the generated GBNF subset. Test helper only.

Raises GbnfError on GBNF syntax this subset does not model, so a
grammar change that outgrows the recognizer fails loudly instead of
silently mis-judging admission.
"""

from __future__ import annotations


class GbnfError(ValueError):
    pass


# Node shapes: ("lit", str) ("cls", tuple[(lo, hi), ...]) ("ref", name)
# ("seq", [node]) ("alt", [node]) ("star", node) ("plus", node) ("opt", node)

_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "[": "[", "]": "]", "-": "-"}


def _parse_literal(text: str, i: int) -> tuple[tuple, int]:
    out = []
    i += 1  # opening quote
    while text[i] != '"':
        if text[i] == "\\":
            ch = text[i + 1]
            if ch not in _ESCAPES:
                raise GbnfError(f"unknown escape \\{ch}")
            out.append(_ESCAPES[ch])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ("lit", "".join(out)), i + 1


def _parse_class(text: str, i: int) -> tuple[tuple, int]:
    i += 1  # opening bracket
    if text[i] == "^":
        raise GbnfError("negated classes not in the generated subset")
    ranges = []
    while text[i] != "]":
        if text[i] == "\\":
            ch = _ESCAPES.get(text[i + 1])
            if ch is None:
                raise GbnfError(f"unknown class escape \\{text[i + 1]}")
            i += 2
        else:
            ch = text[i]
            i += 1
        if text[i] == "-" and text[i + 1] != "]":
            hi = text[i + 1]
            if hi == "\\":
                hi = _ESCAPES[text[i + 2]]
                i += 3
            else:
                i += 2
            ranges.append((ch, hi))
        else:
            ranges.append((ch, ch))
    return ("cls", tuple(ranges)), i + 1


def _parse_alt(text: str, i: int) -> tuple[tuple, int]:
    seqs = []
    while True:
        seq, i = _parse_seq(text, i)
        seqs.append(seq)
        while i < len(text) and text[i] == " ":
            i += 1
        if i < len(text) and text[i] == "|":
            i += 1
            while text[i] == " ":
                i += 1
            continue
        break
    return ("alt", seqs) if len(seqs) > 1 else (seqs[0], i)[0], i


def _parse_seq(text: str, i: int) -> tuple[tuple, int]:
    items = []
    while i < len(text) and text[i] not in "|)":
        if text[i] == " ":
            i += 1
            continue
        if text[i] == '"':
            node, i = _parse_literal(text, i)
        elif text[i] == "[":
            node, i = _parse_class(text, i)
        elif text[i] == "(":
            node, i = _parse_alt(text, i + 1)
            if i >= len(text) or text[i] != ")":
                raise GbnfError("unclosed group")
            i += 1
        else:
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] in "-_"):
                j += 1
            if j == i:
                raise GbnfError(f"unexpected char {text[i]!r} at {i}")
            node, i = ("ref", text[i:j]), j
        if i < len(text) and text[i] in "*+?":
            node = ({"*": "star", "+": "plus", "?": "opt"}[text[i]], node)
            i += 1
        items.append(node)
    return ("seq", items) if len(items) != 1 else items[0], i


def load_gbnf(path) -> dict[str, tuple]:
    rules: dict[str, tuple] = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, sep, body = line.partition(" ::= ")
        if not sep:
            raise GbnfError(f"not a rule line: {line[:60]!r}")
        node, i = _parse_alt(body, 0)
        if i != len(body):
            raise GbnfError(f"trailing junk in rule {name!r}: {body[i:][:40]!r}")
        rules[name.strip()] = node
    return rules


def admits(rules: dict[str, tuple], start: str, text: str) -> bool:
    memo: dict[tuple, frozenset[int]] = {}
    in_progress: set[tuple] = set()

    def match(node: tuple, pos: int) -> frozenset[int]:
        kind = node[0]
        if kind == "lit":
            s = node[1]
            return frozenset([pos + len(s)]) if text.startswith(s, pos) else frozenset()
        if kind == "cls":
            if pos < len(text) and any(lo <= text[pos] <= hi for lo, hi in node[1]):
                return frozenset([pos + 1])
            return frozenset()
        if kind == "ref":
            key = (node[1], pos)
            if key in memo:
                return memo[key]
            if key in in_progress:  # left recursion: refuse, do not hang
                return frozenset()
            in_progress.add(key)
            result = match(rules[node[1]], pos)
            in_progress.discard(key)
            memo[key] = result
            return result
        if kind == "seq":
            ends = frozenset([pos])
            for item in node[1]:
                ends = frozenset(e for p in ends for e in match(item, p))
                if not ends:
                    return ends
            return ends
        if kind == "alt":
            return frozenset(e for alt in node[1] for e in match(alt, pos))
        if kind == "opt":
            return frozenset([pos]) | match(node[1], pos)
        if kind in ("star", "plus"):
            ends: set[int] = set() if kind == "plus" else {pos}
            frontier = {pos}
            while frontier:
                new = {e for p in frontier for e in match(node[1], p)}
                new -= ends
                ends |= new
                frontier = new
            return frozenset(ends)
        raise GbnfError(f"unknown node kind {kind!r}")

    return len(text) in match(rules[start], 0)
```

- [ ] **Step 2: Write the failing admission tests**

```python
"""Grammar completeness: what the card teaches, the grammar must admit.

The soundness direction (grammar output parses) is enforced in
tests/test_6a.py. This file enforces the other direction for the
constructs models are TAUGHT: a card construct the grammar cannot emit
gets deformed by constrained decoding into something else (SPEC section
54 records the general hazard), invisibly.
"""

import re
from pathlib import Path

import pytest

from eval.probe import diagnose
from tests.gbnf_recognizer import admits, load_gbnf

REPO = Path(__file__).resolve().parent.parent
OXIDE_RULES = load_gbnf(REPO / "eval" / "grammar" / "oxide.gbnf")
EXPLICIT_RULES = load_gbnf(REPO / "eval" / "grammar" / "explicit.gbnf")


def _fenced_programs(card_path: Path) -> list[str]:
    text = card_path.read_text(encoding="utf-8")
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)
    return [b if b.endswith("\n") else b + "\n" for b in blocks if "fn main" in b]


def test_oxide_card_programs_are_admitted():
    programs = _fenced_programs(REPO / "LANGUAGE_CARD.md")
    assert programs, "card has no fn-main snippet; test would be vacuous"
    for program in programs:
        assert admits(OXIDE_RULES, "root", program), program


def test_explicit_card_programs_are_admitted():
    programs = _fenced_programs(REPO / "LANGUAGE_CARD_EXPLICIT.md")
    assert programs, "explicit card has no fn-main snippet; test would be vacuous"
    for program in programs:
        assert admits(EXPLICIT_RULES, "root", program), program


# One canonically-formatted exemplar per taught construct. Each must BOTH
# parse under the real pipeline (so the exemplar cannot rot) AND be
# admitted by the grammar (the completeness gate). Formatting is the
# grammar's canonical shape: 4-space indent, single spaces, no semicolons.
EXEMPLARS = [
    ("let-mut", 'fn main() {\n    let mut acc = 0\n    acc = acc + 1\n    print(acc)\n}\n'),
    ("for-vec", 'fn main() {\n    let v = push(vec(), 1)\n    for x in v {\n        print(x)\n    }\n}\n'),
    ("while-break-continue", 'fn main() {\n    let mut i = 0\n    while i < 9 {\n        i = i + 1\n        if i == 2 {\n            continue\n        }\n        if i > 4 {\n            break\n        }\n        print(i)\n    }\n}\n'),
    ("method-receiver", 'fn main() {\n    let v = push(vec(), 1)\n    print(v.len())\n}\n'),
    ("struct-and-field", 'struct Point {\n    x: Int,\n    y: Int,\n}\n\nfn main() {\n    let p = Point { x: 1, y: 2 }\n    print(p.x)\n}\n'),
    ("functional-update", 'struct Point {\n    x: Int,\n    y: Int,\n}\n\nfn main() {\n    let p = Point { x: 1, y: 2 }\n    let q = Point { x: 5, ..p }\n    print(q.y)\n}\n'),
    ("enum-match", 'enum Shape {\n    Dot,\n    Box(Int),\n}\n\nfn main() {\n    let s = Box(3)\n    match s {\n        Dot => print(0),\n        Box(n) => print(n),\n    }\n}\n'),
    ("fn-decl-and-call", 'fn double(n: Int) -> Int {\n    n * 2\n}\n\nfn main() {\n    print(double(21))\n}\n'),
    ("string-escape", 'fn main() {\n    print("a\\nb")\n}\n'),
    ("float-mix", 'fn main() {\n    let x = to_float(3)\n    print(trunc(x * 2.5))\n}\n'),
]


@pytest.mark.parametrize("name,program", EXEMPLARS, ids=[e[0] for e in EXEMPLARS])
def test_exemplar_parses_in_the_real_pipeline(name, program):
    codes = [d["code"] for d in diagnose("oxide", program)]
    syntax = [c for c in codes if c == "OX0001" or c.startswith("OX01")]
    assert not syntax, f"{name} is not valid Oxide at the parse layer: {syntax}"


@pytest.mark.parametrize("name,program", EXEMPLARS, ids=[e[0] for e in EXEMPLARS])
def test_exemplar_is_admitted_by_the_grammar(name, program):
    assert admits(OXIDE_RULES, "root", program)
```

- [ ] **Step 3: Run the tests, expect real signal**

Run: `.venv/bin/pytest tests/test_grammar_admission.py -x -q`

Expected: `test_exemplar_parses_in_the_real_pipeline` must pass for every exemplar (if one fails, the exemplar is wrong Oxide — fix the exemplar, e.g. enum variant construction syntax, match-arm commas, against `SPEC.md` and `tests/test_parser.py` fixtures). Admission tests may fail in two distinct ways: (a) the exemplar's *formatting* is off canon (fix the exemplar — the grammar pins exact spacing like `" -> "`, `"\n    "` field indent); (b) a construct that parses in the pipeline is NOT admitted by the grammar — that is a genuine completeness gap: record it (it becomes a taxonomy candidate), and if it is a card-taught construct, fix `eval/grammar/build.py` and regenerate in this task.

- [ ] **Step 4: Reconcile until green, then run the full suite**

Run: `.venv/bin/pytest tests/test_grammar_admission.py tests/test_6a.py -q` then `.venv/bin/pytest tests/ -q`
Expected: all green, incl. the existing `test_committed_grammar_matches_the_generator` (if build.py changed, regenerate both .gbnf files via `.venv/bin/python -m eval.grammar.build` — check its `__main__` for the exact invocation — and commit them together).

- [ ] **Step 5: Commit**

```bash
git add tests/gbnf_recognizer.py tests/test_grammar_admission.py
git commit -m "test(eval): grammar completeness gate — card constructs must be admitted"
```

(Include `eval/grammar/*.gbnf` and `eval/grammar/build.py` in the commit iff step 3 found a genuine gap.)

---

### Task 2: Model roster + run-id prefix

**Files:**
- Modify: `eval/driver.py:137-141` (MODELS), `eval/driver.py:150-151` (build_run_id), `eval/driver.py:440-` (argparse: `--run-prefix`)
- Modify: `eval/rollup.py:298,339` (thread the prefix; add `--run-prefix` to its CLI at `eval/rollup.py:530-537`)
- Test: extend `tests/test_6a.py`

**Interfaces:**
- Produces: `MODELS` gains `"codegemma7b": "codegemma:7b-instruct-q8_0"` and `"granite8b": "granite-code:8b-instruct-q8_0"`; `build_run_id(slug, shots, seed, prefix="6a") -> f"{prefix}-{slug}-{shots}shot-s{seed}"`. Tasks 4-6 rely on prefixes `g0c` (constrained) and `g0u` (unconstrained).

- [ ] **Step 1: Write the failing tests**

```python
def test_build_run_id_default_prefix_is_unchanged():
    assert driver.build_run_id("qwen7b", 0, 3) == "6a-qwen7b-0shot-s3"


def test_build_run_id_takes_a_prefix():
    assert driver.build_run_id("granite8b", 0, 7, prefix="g0c") == "g0c-granite8b-0shot-s7"


def test_g0_model_slugs_are_pinned():
    assert driver.MODELS["codegemma7b"] == "codegemma:7b-instruct-q8_0"
    assert driver.MODELS["granite8b"] == "granite-code:8b-instruct-q8_0"
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_6a.py -q -k "run_id or slugs"` → FAIL (missing kwarg / missing keys)

- [ ] **Step 3: Implement** — MODELS entries as above; `build_run_id` gains `prefix="6a"`; driver and rollup CLIs gain `--run-prefix` (default `"6a"`) passed to every `build_run_id` call site (driver: grid loop; rollup: lines 298 and 339).

- [ ] **Step 4: Full suite** — `.venv/bin/pytest tests/ -q` → green (default prefix keeps every existing run-id byte-identical).

- [ ] **Step 5: Commit**

```bash
git add eval/driver.py eval/rollup.py tests/test_6a.py
git commit -m "feat(eval): G0 roster and run-id prefixes — codegemma/granite slugs, g0c/g0u runs"
```

---

### Task 3: llamacpp backend in the driver — per-arm clients, constrained mode, stale-server guard

**Files:**
- Modify: `eval/driver.py` (`make_arm_clients` new; `run_grid`/`_run_grid_cell` take per-arm clients; argparse `--backend`, `--constrained`, `--host`, `--expect-model-path`; preflight path)
- Test: extend `tests/test_6a.py` (reuse the `_StubClient` pattern at `tests/test_6a.py:580`)

**Interfaces:**
- Consumes: `eval.llamacpp.LlamaCppClient(model, *, grammar, host)`, `eval.llamacpp.load_grammar(arm)` (raises ValueError for "rust"), `LlamaCppClient.preflight() -> {"model_path": ..., "grammar_sha256": ...}`.
- Produces: `make_arm_clients(backend, slug, *, constrained, host) -> dict[arm, ModelClient]`; `run_grid(..., clients: dict[str, ModelClient], ...)` — Task 4's command line depends on the new flags.

- [ ] **Step 1: Write the failing tests**

```python
def test_constrained_requires_llamacpp():
    with pytest.raises(ModelError):
        driver.make_arm_clients("ollama", "qwen7b", constrained=True,
                                host="http://localhost:8081")


def test_llamacpp_constrained_grammars_by_arm(monkeypatch):
    seen = []
    monkeypatch.setattr(driver, "_load_grammar", lambda arm: f"G::{arm}")
    clients = driver.make_arm_clients("llamacpp", "codegemma7b",
                                      constrained=True,
                                      host="http://localhost:8081")
    assert clients["oxide"].grammar == "G::oxide"
    assert clients["explicit"].grammar == "G::explicit"
    assert clients["rust"].grammar is None  # rustc is the control, never constrained


def test_ollama_backend_shares_one_client():
    clients = driver.make_arm_clients("ollama", "qwen7b", constrained=False,
                                      host="http://localhost:8081")
    assert clients["oxide"] is clients["rust"]  # unchanged legacy behavior


def test_grid_cell_routes_the_arm_to_its_client(tmp_path):
    task = {"id": "tX", "prompt": "Print 42.", "expected_stdout": "42\n"}
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(task) + "\n", encoding="utf-8")
    clients = {
        "oxide": _StubClient("fn main() {\n    print(42)\n}\n"),
        "explicit": _StubClient("fn main() {\n    print(42)\n}\n"),
        "rust": _StubClient('fn main() { println!("42"); }\n'),
    }
    driver.run_one(
        clients,
        run_id="g0c-test-0shot-s1",
        shots=0,
        seed=1,
        results_root=tmp_path,
        tasks_path=tasks,
    )
    # Every stub answered only its own arm: prompts are non-empty and
    # carry that arm's lead material.
    assert clients["oxide"].prompts and all(
        "Oxide" in p for p in clients["oxide"].prompts
    )
    assert clients["rust"].prompts and all(
        "You are writing Rust" in p for p in clients["rust"].prompts
    )
    assert len(clients["explicit"].prompts) >= 1
```

(Adjust `run_one`'s exact keyword list to its real signature at `eval/driver.py:105` when writing the test — the assertion structure is the point: three distinct stubs, each seeing only its own arm's prompts.)

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_6a.py -q -k "arm_client or routes or shares"` → FAIL (`make_arm_clients` undefined)

- [ ] **Step 3: Implement**

```python
from eval.llamacpp import LlamaCppClient
from eval.llamacpp import load_grammar as _load_grammar  # patchable seam


def make_arm_clients(
    backend: str, slug: str, *, constrained: bool, host: str
) -> dict[str, ModelClient]:
    """One client per arm. Ollama: a single shared client (legacy
    behavior, byte-identical prompts and calls). llamacpp: per-arm
    grammar when constrained. The rust arm is NEVER constrained --
    rustc's own diagnostics are the control (SPEC section 45)."""
    if backend == "ollama":
        if constrained:
            raise ModelError(
                "--constrained requires --backend llamacpp: Ollama accepts "
                "a grammar option and silently ignores it"
            )
        client = OllamaClient(MODELS[slug])
        return {arm: client for arm in harness.ARMS}
    clients: dict[str, ModelClient] = {}
    for arm in harness.ARMS:
        grammar = _load_grammar(arm) if constrained and arm != "rust" else None
        clients[arm] = LlamaCppClient(MODELS[slug], grammar=grammar, host=host)
    return clients
```

Threading: `run_grid` and `run_one` accept `clients: dict[str, ModelClient]` instead of one client; `_run_grid_cell` looks up `clients[arm]`. Preflight in `main`: for llamacpp, call `clients["rust"].preflight()` once per slug (one server, one model — preflighting one client covers all three); when `--expect-model-path` is given and not a substring of the returned `model_path`, print the mismatch and exit 2 — this is the stale-server guard. Manifest (`_manifest_fields`): record `backend`, the preflight dict, and per-arm `grammar_sha256` (via `eval.llamacpp.grammar_digest(clients[arm].grammar)`).

- [ ] **Step 4: Full suite** — `.venv/bin/pytest tests/ -q` → green.

- [ ] **Step 5: Commit**

```bash
git add eval/driver.py tests/test_6a.py
git commit -m "feat(eval): llamacpp grid backend — per-arm grammar clients and a stale-server guard"
```

---

### Task 4: G0 constrained baseline — run it

Operational task; no product code. One family at a time (16GB VRAM), the driver's own `is_complete`/`reset_run` machinery provides resume across server crashes (`vk::DeviceLostError` is expected under sustained load — just restart the server and rerun the same command).

- [ ] **Step 1: Resolve blob paths** (per family; codegemma shown, qwen tag `7b-instruct-q8_0`, granite tag `8b-instruct-q8_0`)

```bash
.venv/bin/python - <<'EOF'
import json
for name, tag in [("qwen2.5-coder", "7b-instruct-q8_0"),
                  ("codegemma", "7b-instruct-q8_0"),
                  ("granite-code", "8b-instruct-q8_0")]:
    m = json.load(open(f"/mnt/extra/ollama-models/manifests/registry.ollama.ai/library/{name}/{tag}"))
    layer = [l for l in m["layers"] if "model" in l["mediaType"]][0]
    print(name, "/mnt/extra/ollama-models/blobs/" + layer["digest"].replace(":", "-"))
EOF
```

- [ ] **Step 2: Per family — start the server, verify ownership, run, tear down**

```bash
# start (repeat per family with its blob path)
nohup ~/llama.cpp/build-vk/bin/llama-server -m <BLOB> --port 8081 -ngl 99 -c 8192 \
  > /tmp/llama-g0.log 2>&1 &
until curl -sf http://localhost:8081/health >/dev/null; do sleep 2; done
ss -tlnp | grep 8081   # MUST show the llama-server PID just started

# run (slug per family: qwen7b / codegemma7b / granite8b)
.venv/bin/python -m eval.driver --backend llamacpp --models codegemma7b \
  --shots 0 --seeds 1-10 --constrained --run-prefix g0c \
  --expect-model-path <BLOB-SHA-SUBSTRING> \
  --results-root eval/results/g0-generation-baseline/constrained

# tear down before the next family
kill <SERVER_PID> && sleep 2 && ! (ss -tln | grep -q 8081)
```

Expected: 10 run dirs per family (`g0c-<slug>-0shot-s1..10`), each `is_complete` (60 cells). On a crash mid-run: restart server, rerun the identical command — completed run_ids are skipped, the incomplete one is reset and redone.

Note on resume granularity: the driver resumes per *run* (`reset_run` drops an incomplete run wholesale — SPEC §6.4's pinned behavior), not per session as the design doc loosely words it. A worst-case device-lost crash redoes one run of 60 sessions, ~10–20 min at observed generation speeds. That bounded cost does not justify new checkpointing machinery; do not build any.

- [ ] **Step 3: Timing gate** — after the first family, project the total. If constrained+unconstrained projects past ~8h GPU, stop and surface to Brice with the projection (the design's trim lever is dropping the unconstrained condition to one family; that is Brice's call, not the implementer's).

- [ ] **Step 4: Commit the constrained baseline**

Precedent: the 6a pilot commits `cells.jsonl`, `triples.jsonl`, `manifest.json`, AND `raw/` (3.9M total). G0 is ~10× the sessions; if `du -sh` of the G0 root exceeds ~100MB, stop and ask Brice before committing raw/ (public repo). Otherwise:

```bash
du -sh eval/results/g0-generation-baseline/constrained
git add eval/results/g0-generation-baseline/constrained
git commit -m "data(eval): G0 constrained generation baseline — 3 families x 10 seeds at HEAD"
```

---

### Task 5: G0 unconstrained baseline — run it

Same operational shape as Task 4, same servers, same seeds — only the flags change: drop `--constrained`, prefix `g0u`, results root `.../unconstrained`. Same backend (llamacpp) for both conditions, deliberately: the pilot's unconstrained numbers came through Ollama, and re-basing both conditions on one backend removes the backend as a confound in the constrained-vs-unconstrained comparison.

- [ ] **Step 1: Run all three families** (server lifecycle identical to Task 4 Step 2)

```bash
.venv/bin/python -m eval.driver --backend llamacpp --models <slug> \
  --shots 0 --seeds 1-10 --run-prefix g0u \
  --expect-model-path <BLOB-SHA-SUBSTRING> \
  --results-root eval/results/g0-generation-baseline/unconstrained
```

- [ ] **Step 2: Commit** (same size check as Task 4 Step 4)

```bash
git add eval/results/g0-generation-baseline/unconstrained
git commit -m "data(eval): G0 unconstrained generation baseline — the demand channel"
```

---

### Task 6: G0 profiler + REPORT

**Files:**
- Create: `eval/g0_report.py`
- Test: `tests/test_g0_report.py`
- Create: `eval/results/g0-generation-baseline/REPORT.md` (hand-written from the profiler's output)

**Interfaces:**
- Consumes: run dirs (`cells.jsonl` rows with `task/arm/first_compiled/first_passed/final_passed/attempts_to_pass`; `triples.jsonl` rows with `task/arm/attempt/diagnostics`), `eval.rollup.paired_delta`, `eval.rollup.paired_se` (operate on cells with `task` + `first_passed`).
- Produces: `python -m eval.g0_report --root DIR --models a,b --seeds 1-10 --run-prefix g0c [--samples N] [--validate-pilot]` printing per-family/arm rates, the stage histogram, the `OX04xx` gate count, and paired deltas; `--samples N` dumps N failing first-attempt sources per (family, top-5 code) into `<root>/samples/` for Task 7.

**Stage buckets** (first attempts only, oxide+explicit arms): `lexer` = `OX0001`; `parser` = `OX01xx`; `resolve` = `OX02xx`; `types` = `OX03xx`; `linearity` = `OX04xx` — plus, separately, the same histogram over ALL attempts. The gate metric is the pooled first-attempt `OX04xx` occurrence count and the count of distinct sessions whose first attempt carries one.

- [ ] **Step 1: Write the failing validation test** — the discipline: the profiler must reproduce published pilot numbers before it may read new data.

```python
def test_profiler_reproduces_the_pilot_7b_row():
    # Published in eval/results/6a-pilot/REPORT.md: 7B 0-shot first-compile
    # oxide 2/20, explicit 0/20, rust 20/20; rust final-pass 12/20.
    out = g0_report.profile(
        root=Path("eval/results/6a-pilot"),
        models=["qwen7b"], seeds=[1], prefix="6a",
    )
    row = out["qwen7b"]
    assert row["oxide"]["first_compiled"] == 2 / 20
    assert row["explicit"]["first_compiled"] == 0 / 20
    assert row["rust"]["first_compiled"] == 20 / 20
    assert row["rust"]["final_passed"] == 12 / 20
```

Plus two synthetic-dir tests (tmp_path fixtures writing 2-3 hand-built cells/triples rows): one asserting stage bucketing (`OX0203` → resolve, `OX0400` → linearity), one asserting the gate counter counts occurrences AND sessions separately.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_g0_report.py -q` → FAIL (module missing)

- [ ] **Step 3: Implement `eval/g0_report.py`** — core shape (fill in argparse and printing around it; stdlib only, matching the repo's eval modules):

```python
STAGES = (
    ("lexer", lambda c: c == "OX0001"),
    ("parser", lambda c: c.startswith("OX01")),
    ("resolve", lambda c: c.startswith("OX02")),
    ("types", lambda c: c.startswith("OX03")),
    ("linearity", lambda c: c.startswith("OX04")),
)


def _stage(code: str) -> str | None:
    for name, pred in STAGES:
        if pred(code):
            return name
    return None


def profile(*, root: Path, models: list[str], seeds: list[int], prefix: str) -> dict:
    out: dict[str, dict] = {}
    for slug in models:
        cells: list[dict] = []
        first_codes: Counter[str] = Counter()
        all_codes: Counter[str] = Counter()
        gate_occurrences = 0
        gate_sessions = 0
        for seed in seeds:
            run_dir = root / build_run_id(slug, 0, seed, prefix=prefix)
            cells += [json.loads(l) for l in open(run_dir / "cells.jsonl")]
            for row in map(json.loads, open(run_dir / "triples.jsonl")):
                if row["arm"] == "rust":
                    continue
                codes = [str(d.get("code", "?")) for d in row["diagnostics"]]
                all_codes.update(codes)
                if row["attempt"] == 1:
                    first_codes.update(codes)
                    ox04 = sum(1 for c in codes if c.startswith("OX04"))
                    gate_occurrences += ox04
                    gate_sessions += bool(ox04)
        by_arm: dict[str, dict] = {}
        for arm in ("oxide", "explicit", "rust"):
            rows = [c for c in cells if c["arm"] == arm]
            by_arm[arm] = {
                "n": len(rows),
                "first_compiled": sum(c["first_compiled"] for c in rows) / len(rows),
                "first_passed": sum(c["first_passed"] for c in rows) / len(rows),
                "final_passed": sum(c["final_passed"] for c in rows) / len(rows),
            }
        oxide = [c for c in cells if c["arm"] == "oxide"]
        explicit = [c for c in cells if c["arm"] == "explicit"]
        out[slug] = {
            **by_arm,
            "stage_hist_first": {
                s: sum(v for c, v in first_codes.items() if _stage(c) == s)
                for s, _ in STAGES
            },
            "stage_hist_all": {
                s: sum(v for c, v in all_codes.items() if _stage(c) == s)
                for s, _ in STAGES
            },
            "code_hist_first": dict(first_codes.most_common()),
            "gate": {"occurrences": gate_occurrences, "sessions": gate_sessions},
            "paired_delta": rollup.paired_delta(oxide, explicit),
            "paired_se": rollup.paired_se(oxide, explicit),
        }
    return out
```

`--samples N` walks failing first-attempt triples for the top-5 `code_hist_first` codes per family and copies the matching `raw/<task>.<arm>.1.txt` into `<root>/samples/<family>/<code>/`. `--validate-pilot` runs the Step-1 assertions inline and exits non-zero on mismatch.

- [ ] **Step 4: Full suite green, then profile G0 for real**

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -m eval.g0_report --root eval/results/g0-generation-baseline/constrained \
  --models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix g0c
.venv/bin/python -m eval.g0_report --root eval/results/g0-generation-baseline/unconstrained \
  --models qwen7b,codegemma7b,granite8b --seeds 1-10 --run-prefix g0u
```

- [ ] **Step 5: Write `eval/results/g0-generation-baseline/REPORT.md`** — the numbers from Step 4, plus: comparison against the stale pre-§53/§54 constrained probes (77 `OX02xx` at 7B, compile-clean 3/24, zero `OX04xx`); the gate-metric statement (`OX04xx` count, per family); §47 no-signal annotations where floors apply; an explicit limits section (0-shot only, q8_0 quants, one grammar, backend switched from the pilot's Ollama — pilot numbers are directional context, not a matched before/after).

- [ ] **Step 6: Commit**

```bash
git add eval/g0_report.py tests/test_g0_report.py eval/results/g0-generation-baseline/REPORT.md
git commit -m "feat(eval): G0 profiler and baseline report — the generation gate measured at HEAD"
```

---

### Task 7: Taxonomy + dossiers

**Files:**
- Create: `docs/superpowers/specs/2026-08-XX-v03-taxonomy.md` (date of writing)

**Interfaces:**
- Consumes: Task 6's profiler output and `--samples` dumps.
- Produces: the ranked candidate list the change loop (Appendix A) executes against.

- [ ] **Step 1: Generate samples** — `--samples 5` on both conditions.
- [ ] **Step 2: Read them.** For each (family, top code): read the 5 samples, name the concrete friction (not the code — the *behavior*: what did the model write, what did it want). For the unconstrained condition also collect the reached-for-syntax histogram (the pilot's `;`/`[`/`|` table, recomputed at HEAD). The snippet, kept in the taxonomy doc as a fenced block; per the validated-analysis discipline, first point it at `eval/results/6a-pilot` and confirm it reproduces the pilot's published `;` count (1575) before trusting its HEAD numbers:

```python
from collections import Counter
from pathlib import Path

REJECTS = ";[]'|&#"
hist: Counter[str] = Counter()
for txt in Path(ROOT).glob("g0u-*/raw/*.oxide.1.txt"):
    for ch in txt.read_text(encoding="utf-8"):
        if ch in REJECTS:
            hist[ch] += 1
for txt in Path(ROOT).glob("g0u-*/raw/*.explicit.1.txt"):
    for ch in txt.read_text(encoding="utf-8"):
        if ch in REJECTS:
            hist[ch] += 1
print(hist.most_common())
```

(For the pilot-validation pass, the glob prefix is `6a-*` and the counts cover all attempts across both Oxide arms — match the pilot REPORT's stated denominator before comparing.)
- [ ] **Step 3: Write the dossiers.** For every candidate, one paragraph in the template:

```markdown
### <candidate-name>
- **Friction:** <what models do / want, with counts: N occurrences, M sessions, families>
- **Evidence:** <unconstrained demand | constrained deformation | both — cite samples>
- **Class:** sugar | card | diagnostic | SPEC-named feature
- **Fix:** <one sentence>
- **Prediction:** <which code falls, in which families, and which should NOT move>
- **Design fit:** <why this serves Oxide's design, not imitation — the semicolon test>
```

- [ ] **Step 4: Rank** by (sessions affected × families affected), gate-relevance first (a resolve-stage fix that unblocks reaching linearity outranks a cosmetic parser one at equal counts). State the ranking rule in the doc.
- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-XX-v03-taxonomy.md
git commit -m "docs: v0.3 taxonomy — ranked generation-friction dossiers from G0"
```

---

## Appendix A — the change loop (repeating protocol, not pre-planned tasks)

Each loop iteration is planned as its own miniature task set at execution time (the change is data-dependent), but every iteration follows this fixed shape, per the design doc:

1. **Pick** the top-ranked dossier. Sugar/card/diagnostic → proceed. SPEC-named feature → write and commit the SPEC extension first.
2. **Implement** TDD (failing test → minimal code → suite green). Surface changes update `eval/grammar/build.py` + regenerated `.gbnf` in the same commit; `tests/test_grammar_admission.py` and the generator-match test are the parity invariant.
3. **Re-measure** the affected condition only, all three families, same seeds, `--run-prefix` versioned (`g1c`, `g2c`, ...). Before/after via `eval/g0_report.py` on both roots.
4. **Verify the prediction** from the dossier: named code falls in named families; rust flat; explicit-arm effects reported. A miss lands only with independent justification (the `mut` precedent), else revert. Either way the decision is recorded in the iteration's REPORT.md (`eval/results/g<N>-<change-slug>/REPORT.md`).
5. **Stop** per the design doc: gate populates (`OX04xx` in ≥2 families, pooled ≥30 first-attempt occurrences) or two consecutive dry iterations → ship v0.3 (synthesis REPORT, README, SPEC bump to v0.3, tag, memory).
