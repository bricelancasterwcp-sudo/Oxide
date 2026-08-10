"""Phase 6a run driver (SPEC Part X, sections 5 and 6.4).

Each (model, shots, seed) combination is its own harness run_id. That is
what makes this phase additive: harness._claim_session locks on
(run_id, task, arm) and the pinned triple schema carries no model or seed
field, so sharing a run_id across the grid would silently conflate cells.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from eval import harness, rustc_adapter
from eval.extract import extract
from eval.llamacpp import DEFAULT_HOST as LLAMACPP_DEFAULT_HOST
from eval.llamacpp import LlamaCppClient, grammar_digest
from eval.llamacpp import load_grammar as _load_grammar  # patchable seam
from eval.models import (
    DEFAULT_NUM_CTX,
    ContextOverflowError,
    ModelClient,
    ModelError,
    OllamaClient,
)
from eval.repair import build_repair_prompt


def run_session(
    client: ModelClient,
    *,
    run_id: str,
    task_id: str,
    arm: str,
    shots: int,
    results_root: Path,
    raw_dir: Path,
    tasks_path: Path | None = None,
    seed: int = 1,
) -> dict:
    """Drive one task/arm to a verdict or the attempt cap.

    ModelError is deliberately NOT caught: infrastructure failures must
    never be recorded as model failures (section 7). ``ContextOverflowError``
    (raised either by the client's own pre-request ``check_context``
    estimate, or by its ``ServerContextOverflowError`` subclass when the
    server's real tokenizer rejects a prompt that passed that estimate --
    see ``eval.llamacpp``) is the one exception, and it is gated on
    EVIDENCE, not on which of the two raised it (SPEC section 45/51):

    - ``session.attempts >= 1`` (at least one attempt already submitted
      THIS session): a RESULT, not infrastructure. The estimate is
      deliberately crude (see ``eval.models.estimate_tokens``) and a
      repair prompt that grows across attempts can overflow either check
      well after real evidence exists -- the cross-attempt sibling of
      truncation at ``num_predict``, which is already a result. The
      session ends there with whatever was submitted so far, marked
      ``context_exhausted``, and the caller's loop (``run_one``)
      continues to the next task/arm -- no run abort.
    - ``session.attempts == 0`` (fires before ANY submission this
      session): re-raised. There is no evidence to lose by aborting, and
      at 8192 tokens this is rare, but at a smaller per-family window
      (e.g. granite8b's native 4096, SPEC section 48) an oversized
      INITIAL prompt would otherwise repeat identically across every
      seed of a (task, arm, shots) triple -- fabricating a full grid of
      zero-attempt "results" with no abort and no manifest cause,
      precisely what routing ``ContextOverflowError`` through
      ``ModelError`` exists to prevent.
    """
    session = harness.new_session(
        task_id,
        arm,
        run_id,
        tasks_path=tasks_path,
        results_root=results_root,
    )
    prompt = harness.build_prompt(arm, task_id, shots=shots, tasks_path=tasks_path)
    raw_dir.mkdir(parents=True, exist_ok=True)

    compliant: list[bool] = []
    truncated: list[bool] = []
    tokens_in = tokens_out = elapsed_ms = 0
    first: dict | None = None
    verdict: dict = {}
    attempts_to_pass = harness.MAX_ATTEMPTS + 1
    context_exhausted = False

    for attempt in range(1, harness.MAX_ATTEMPTS + 1):
        try:
            generation = client.generate(prompt, seed=seed)
        except ContextOverflowError:
            if session.attempts == 0:
                # Zero evidence: a configuration failure (this session
                # never produced anything to preserve), regardless of
                # which check caught it -- re-raise so it escapes as a
                # ModelError and aborts the run id, cause recorded in
                # the manifest. See the docstring above.
                raise
            context_exhausted = True
            break
        (raw_dir / f"{task_id}.{arm}.{attempt}.txt").write_text(
            generation.text, encoding="utf-8"
        )
        tokens_in += generation.tokens_in
        tokens_out += generation.tokens_out
        elapsed_ms += generation.ms
        truncated.append(generation.truncated)

        candidate = extract(generation.text)
        compliant.append(candidate.contract_compliant)
        verdict = session.submit(candidate.source)
        if first is None:
            first = verdict
        if verdict["passed"]:
            attempts_to_pass = attempt
            break
        prompt = build_repair_prompt(
            arm,
            candidate.source,
            verdict,
            task_id=task_id,
            shots=shots,
            tasks_path=tasks_path,
        )

    # first/verdict are always populated by this point -- the
    # ContextOverflowError branch above re-raises whenever
    # session.attempts == 0, so zero evidence never reaches here. The
    # `is not None`/truthiness guards below are kept anyway as a cheap
    # defensive fallback, not for reachability: insurance against a
    # future loop change silently breaking that invariant.
    cell = {
        "task": task_id,
        "arm": arm,
        "attempts": session.attempts,
        "first_compiled": bool(first["compiled"]) if first is not None else False,
        "first_passed": bool(first["passed"]) if first is not None else False,
        "final_passed": bool(verdict["passed"]) if verdict else False,
        "attempts_to_pass": attempts_to_pass,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "ms": elapsed_ms,
        "contract_compliant": compliant,
        "truncated": truncated,
    }
    if context_exhausted:
        cell["context_exhausted"] = True
    return cell


def run_one(
    clients: dict[str, ModelClient],
    *,
    run_id: str,
    shots: int,
    seed: int,
    results_root: Path,
    tasks_path: Path | None = None,
) -> None:
    """All 60 sessions (20 tasks x 3 arms) for one run id.

    ``clients`` maps each arm to the client that answers it. The ollama
    path passes the SAME client instance under all three keys (legacy
    behavior, byte-identical prompts and calls); llamacpp passes a
    distinct grammar-bearing client per arm.
    """
    run_dir = Path(results_root) / run_id
    cells_path = run_dir / "cells.jsonl"
    raw_dir = run_dir / "raw"
    tasks = harness.load_tasks(tasks_path)
    for task_id in sorted(tasks):
        for arm in harness.ARMS:
            cell = run_session(
                clients[arm],
                run_id=run_id,
                task_id=task_id,
                arm=arm,
                shots=shots,
                results_root=results_root,
                raw_dir=raw_dir,
                tasks_path=tasks_path,
                seed=seed,
            )
            cells_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cells_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(cell, sort_keys=True) + "\n")


MODELS = {
    "qwen0_5b": "qwen2.5-coder:0.5b-instruct-q8_0",
    "qwen1_5b": "qwen2.5-coder:1.5b-instruct-q8_0",
    "qwen7b": "qwen2.5-coder:7b-instruct-q8_0",
    "codegemma7b": "codegemma:7b-instruct-q8_0",
    "granite8b": "granite-code:8b-instruct-q8_0",
    "deepseek16b_lite": "deepseek-coder-v2:16b-lite-instruct-q5_K_M",
}

# Per-slug context window pin (SPEC section 48): min(DEFAULT_NUM_CTX, the
# model's OWN advertised training context). Every slug defaults to
# DEFAULT_NUM_CTX (8192) EXCEPT granite-code:8b, whose training context is
# 4096 -- llama-server refuses (caps the slot) at anything larger than a
# model was actually trained on ("the slot context (8192) exceeds the
# training context of the model (4096) -- capping"), so 8192 is physically
# unsatisfiable for that one model, not a policy choice. Never rope-scaled
# past what the model was trained on; treated instead as a per-family
# covariate, arm-fair within granite's own runs (SPEC section 48).
NUM_CTX = {
    "granite8b": 4096,
}

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
# The CLI's ``--backend`` token, translated to the human-readable label
# recorded in the manifest's top-level "backend" field.
# LlamaCppClient.preflight() independently writes "llama.cpp" (with the
# dot) into the provenance payload embedded at manifest["preflight"]
# (section 49) -- without this mapping the top-level field would read
# the bare CLI token "llamacpp" for the identical run, two spellings of
# the same fact that a reader could take as two different backends.
BACKEND_LABELS = {"ollama": "ollama", "llamacpp": "llama.cpp"}
SEEDS = (1, 2, 3, 4, 5)
SHOT_COUNTS = (0, 3)
SESSIONS_PER_RUN = 60
MAX_CONSECUTIVE_ABORTS = 3
HEALTH_WAIT_CAP_S = 600
RUSTC_PROBE_TIMEOUT_S = 60


def build_run_id(slug: str, shots: int, seed: int, prefix: str = "6a") -> str:
    return f"{prefix}-{slug}-{shots}shot-s{seed}"


def unknown_slugs(slugs: list[str]) -> list[str]:
    """Model slugs with no pinned tag. Shared with the rollup CLI so both
    entry points reject the same typos the same way."""
    return [slug for slug in slugs if slug not in MODELS]


def make_arm_clients(
    backend: str, slug: str, *, constrained: bool, host: str
) -> dict[str, ModelClient]:
    """One client per arm. Ollama: a single shared client (legacy
    behavior, byte-identical prompts and calls). llamacpp: per-arm
    grammar when constrained. The rust arm is NEVER constrained --
    rustc's own diagnostics are the control (SPEC section 45).

    ``num_ctx`` is pinned per SLUG, not uniformly: every arm of one slug
    shares the same window (``NUM_CTX``, defaulting to
    ``DEFAULT_NUM_CTX``), so the pin stays arm-fair within a model while
    still tracking that model's own capability (SPEC section 48).
    """
    num_ctx = NUM_CTX.get(slug, DEFAULT_NUM_CTX)
    if backend == "ollama":
        if constrained:
            raise ModelError(
                "--constrained requires --backend llamacpp: Ollama accepts "
                "a grammar option and silently ignores it"
            )
        client = OllamaClient(MODELS[slug], num_ctx=num_ctx)
        return {arm: client for arm in harness.ARMS}
    clients: dict[str, ModelClient] = {}
    for arm in harness.ARMS:
        grammar = _load_grammar(arm) if constrained and arm != "rust" else None
        clients[arm] = LlamaCppClient(
            MODELS[slug], grammar=grammar, host=host, num_ctx=num_ctx
        )
    return clients


def is_complete(run_dir: Path) -> bool:
    """A run is complete only with all 60 cell records on disk."""
    cells = Path(run_dir) / "cells.jsonl"
    if not cells.exists():
        return False
    with open(cells, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip()) >= SESSIONS_PER_RUN


def reset_run(run_dir: Path) -> None:
    """Drop an incomplete run wholesale (section 6.4)."""
    shutil.rmtree(run_dir, ignore_errors=True)


def wait_for_health(client: object, *, cap_s: int = HEALTH_WAIT_CAP_S,
                    sleep: Callable[[float], None] = time.sleep) -> None:
    """Poll between run ids so a daemon restart costs no work. Applied
    BETWEEN runs only -- mid-session waiting would collide with the
    O_EXCL locks and half-written triples section 6.4 avoids."""
    deadline = cap_s
    while deadline > 0:
        if client.healthy():
            return
        sleep(5)
        deadline -= 5
    raise ModelError(f"ollama did not become healthy within {cap_s}s")


def run_grid(
    make_clients: Callable[[str], dict[str, ModelClient]],
    *,
    slugs: list[str],
    shot_counts: list[int],
    seeds: list[int],
    results_root: Path,
    health_check: Callable[[object], None] | None = None,
    tasks_path: Path | None = None,
    preflight: dict[str, dict] | None = None,
    prefix: str = "6a",
    backend: str = "ollama",
) -> dict:
    """Walk the grid, one run id at a time. ``make_clients(slug)`` returns
    the per-arm client dict for that slug (see ``make_arm_clients``);
    ``preflight`` maps slug -> the provenance payload from
    ``ModelClient.preflight``; section 48 requires it in every manifest,
    the only artifact proving which weights produced the result."""
    completed: list[str] = []
    aborted: list[str] = []
    consecutive = 0
    for slug in slugs:
        clients = make_clients(slug)
        for shots in shot_counts:
            for seed in seeds:
                run_id = build_run_id(slug, shots, seed, prefix=prefix)
                run_dir = Path(results_root) / run_id
                if is_complete(run_dir):
                    continue
                reset_run(run_dir)
                exc = _run_grid_cell(
                    clients,
                    run_id=run_id,
                    run_dir=run_dir,
                    slug=slug,
                    shots=shots,
                    seed=seed,
                    results_root=results_root,
                    tasks_path=tasks_path,
                    health_check=health_check,
                    preflight=(preflight or {}).get(slug),
                    backend=backend,
                )
                if exc is not None:
                    aborted.append(run_id)
                    consecutive += 1
                    if consecutive >= MAX_CONSECUTIVE_ABORTS:
                        raise RuntimeError(
                            f"{consecutive} consecutive run aborts "
                            f"(last: {run_id}: {exc}) -- stopping the grid "
                            f"rather than leaving a partial grid that reads "
                            f"as complete"
                        ) from exc
                    continue
                consecutive = 0
                completed.append(run_id)
    return {"completed": completed, "aborted": aborted}


def _run_grid_cell(
    clients: dict[str, ModelClient],
    *,
    run_id: str,
    run_dir: Path,
    slug: str,
    shots: int,
    seed: int,
    results_root: Path,
    tasks_path: Path | None,
    health_check: Callable[[object], None] | None,
    preflight: dict | None = None,
    backend: str = "ollama",
) -> Exception | None:
    """Run one grid cell (one run id) start to finish.

    ``clients["rust"]`` stands in as the primary/representative client
    wherever exactly one is needed (health check, manifest sampling
    params): for ollama it IS the single shared instance, and for
    llamacpp it is the one arm that is never grammar-constrained, so its
    params match the server-level configuration common to all three.

    Ordering is load-bearing (section 6.4): health check, THEN the
    manifest, THEN the sessions, so an interrupted run still records what
    it was running. The health check runs INSIDE the try: ``wait_for_health``
    raises ModelError once its 600s cap expires, and section 51 scopes that
    abort to this run id with the cause in this run's manifest -- letting it
    escape would lose the grid entirely and count nothing as aborted.
    On abort the manifest is rewritten with the reason, and the exception is
    returned (not raised) so the caller decides whether the
    consecutive-abort backstop fires.

    ``HarnessError`` is caught alongside ``ModelError``: an unreadable
    language card or a session-claim collision is a per-run environment
    fault, not a reason to end an unattended multi-hour grid. Preflight
    now checks the corpus, the shots, and rustc, so this is a backstop
    rather than an expected path.

    ``RepairPromptError`` is deliberately NOT caught. It fires only when
    the frozen harness stops ending prompts with its own OUTPUT_CONTRACT,
    which means every subsequent repair prompt would be malformed -- that
    must stop the grid loudly, not abort one run and carry on.
    """
    fields = _manifest_fields(clients, preflight, backend)
    started_at = _timestamp()

    def write(**extra: object) -> None:
        _write_manifest(run_dir, run_id, slug, shots, seed, fields=fields,
                        started_at=started_at, **extra)

    try:
        if health_check is not None:
            health_check(clients["rust"])
        write()
        run_one(
            clients,
            run_id=run_id,
            shots=shots,
            seed=seed,
            results_root=results_root,
            tasks_path=tasks_path,
        )
    except (ModelError, harness.HarnessError) as exc:
        write(ended_at=_timestamp(), aborted_reason=str(exc))
        return exc
    write(ended_at=_timestamp())
    return None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _manifest_fields(
    clients: dict[str, ModelClient], preflight: dict | None,
    backend: str = "ollama",
) -> dict:
    """Sampling params and provenance for the manifest.

    Absent attributes read as ``None``, never as the pinned defaults.
    ``run_grid`` takes any ``ModelClient`` and the Protocol declares only
    ``generate``; an API-backed client carrying ``max_tokens`` instead of
    ``num_predict`` would otherwise have '2048' recorded against it with
    total confidence. ``null`` is an honest 'unknown'.

    ``num_ctx`` and ``model_context_length`` are DIFFERENT numbers and are
    named so they cannot be confused. ``model_context_length`` is the
    model's advertised capability read off /api/tags (32768). ``num_ctx``
    is the window the run actually used -- pinned by the client, because
    Ollama's own default is 4096 and would silently truncate the Oxide
    arms' prompts from the front (section 48).

    ``grammar_sha256`` is recorded per arm (section 49): a result produced
    under a grammar cannot be traced without knowing which one, and the
    rust arm's is always ``None`` by design (it is never constrained).
    ``preflight`` is embedded alongside the fields already extracted from
    it, since llamacpp's payload carries provenance (e.g. ``model_path``)
    that has no equivalent top-level key here -- MINUS its own
    ``grammar_sha256``, when present. That key (``LlamaCppClient.
    preflight``'s own payload) reflects whichever client preflight was
    run against, which is always ``clients["rust"]`` -- never constrained
    -- so embedded verbatim it would read a permanently-``None`` grammar
    digest inside ``manifest["preflight"]`` on every run, constrained or
    not, sitting right next to the correctly-populated PER-ARM
    ``grammar_sha256`` two keys up. A reader who checks only the nested
    copy could misread a constrained run as unconstrained; dropped from
    the embedded copy so the top-level per-arm field is the one place
    this run's grammar provenance lives.

    ``backend`` is recorded under ``BACKEND_LABELS``' canonical spelling,
    not the raw CLI token, so this field and ``preflight["backend"]``
    (llamacpp only) always agree.
    """
    client = clients["rust"]
    info = preflight or {}
    embedded_preflight = (
        {k: v for k, v in preflight.items() if k != "grammar_sha256"}
        if preflight is not None else None
    )
    return {
        "temperature": getattr(client, "temperature", None),
        "top_p": getattr(client, "top_p", None),
        "num_predict": getattr(client, "num_predict", None),
        "num_ctx": getattr(client, "num_ctx", None),
        "digest": info.get("digest"),
        "quantization_level": info.get("quantization_level"),
        "model_context_length": info.get("context_length"),
        "ollama_version": info.get("ollama_version"),
        "backend": BACKEND_LABELS.get(backend, backend),
        "preflight": embedded_preflight,
        "grammar_sha256": {
            arm: grammar_digest(getattr(clients[arm], "grammar", None))
            for arm in harness.ARMS
        },
    }


def _write_manifest(run_dir: Path, run_id: str, slug: str, shots: int,
                    seed: int, *, fields: dict, started_at: str,
                    ended_at: str | None = None,
                    aborted_reason: str | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "model_slug": slug,
        "model": MODELS[slug],
        "shots": shots,
        "seed": seed,
        "started_at": started_at,
        "ended_at": ended_at,
        "aborted_reason": aborted_reason,
        **fields,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_seeds(text: str) -> list[int]:
    if "-" in text:
        low, high = text.split("-", 1)
        return list(range(int(low), int(high) + 1))
    return [int(part) for part in text.split(",") if part]


def _rustc_problem() -> str | None:
    """rustc must be invocable BEFORE any generation.

    ``eval/rustc_adapter`` never raises: on OSError or TimeoutExpired it
    returns a fallback diagnostic, which flows through run_file ->
    session.submit -> cells.jsonl as ``first_compiled: false``. That is an
    infrastructure failure recorded as a model failure -- section 51's
    governing rule, in the direction that biases every arm toward the null.
    An absent rustc gives all-zeros and would be noticed; a compile
    timeout under memory pressure would be partial, plausible, and
    non-randomly correlated with the 7B rung.
    """
    rustc = rustc_adapter.find_rustc()
    try:
        proc = subprocess.run(
            [rustc, "--version"],
            capture_output=True,
            timeout=RUSTC_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"rustc is not invocable ({rustc}): {exc}"
    if proc.returncode != 0:
        return f"rustc exited {proc.returncode} ({rustc}): rust arm cannot run"
    return None


def _corpus_problems(tasks_path: Path | None = None) -> list[str]:
    """Corpus loads, and its size still matches the pinned run length."""
    try:
        tasks = harness.load_tasks(tasks_path)
    except Exception as exc:  # HarnessError, OSError, JSON decode
        return [f"task corpus does not load: {exc}"]
    if not tasks:
        return ["task corpus is empty"]
    sessions = len(tasks) * len(harness.ARMS)
    if sessions != SESSIONS_PER_RUN:
        return [
            f"corpus is {len(tasks)} tasks x {len(harness.ARMS)} arms = "
            f"{sessions} sessions, but SESSIONS_PER_RUN is "
            f"{SESSIONS_PER_RUN}; is_complete would mis-judge every run"
        ]
    return []


def _shot_problems(shot_counts: list[int]) -> list[str]:
    """Every arm must carry enough shots for the deepest shot condition."""
    needed = max(shot_counts) if shot_counts else 0
    if needed <= 0:
        return []
    problems: list[str] = []
    for arm in harness.ARMS:
        try:
            pairs = harness.load_shots(arm)
        except Exception as exc:
            problems.append(f"arm '{arm}' shots do not load: {exc}")
            continue
        if len(pairs) < needed:
            problems.append(
                f"arm '{arm}' has {len(pairs)} shot(s), needs {needed}"
            )
    return problems


def preflight_environment(shot_counts: list[int],
                          tasks_path: Path | None = None) -> list[str]:
    """Section 50.4's non-Ollama preflight checks, as a problem list."""
    problems: list[str] = []
    rustc = _rustc_problem()
    if rustc is not None:
        problems.append(rustc)
    problems.extend(_corpus_problems(tasks_path))
    problems.extend(_shot_problems(shot_counts))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.driver")
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--shots", default="0,3")
    parser.add_argument("--seeds", default="1-5")
    parser.add_argument("--results-root", default=str(harness.RESULTS_ROOT))
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--run-prefix", default="6a")
    parser.add_argument("--backend", choices=["ollama", "llamacpp"],
                        default="ollama")
    parser.add_argument("--constrained", action="store_true")
    parser.add_argument("--host", default=LLAMACPP_DEFAULT_HOST)
    parser.add_argument("--expect-model-path", default=None)
    args = parser.parse_args(argv)

    slugs = [s for s in args.models.split(",") if s]
    unknown = unknown_slugs(slugs)
    if unknown:
        print(f"unknown model slug(s): {unknown}; known: {sorted(MODELS)}",
              file=sys.stderr)
        return 2

    if args.backend == "llamacpp" and len(slugs) > 1:
        # One llama-server instance serves exactly one model (SPEC section
        # 48/49): unlike Ollama, which can hold multiple pulled tags and
        # route by name per request, llama-server is started on ONE set of
        # weights and every request goes to whatever it currently has
        # loaded. --models defaults to ALL SIX slugs (see MODELS above),
        # so the unguarded default would silently run every slug's
        # sessions against a single server's weights -- a full grid of
        # plausible-looking results attributed to the wrong models, with
        # no abort and no manifest cause. Refusing here, before any
        # generation, is the only place that mismatch can be caught.
        print(
            f"--backend llamacpp refuses more than one slug per invocation "
            f"(got {len(slugs)}: {','.join(slugs)}) -- one llama-server "
            f"instance serves one model; run each slug as its own "
            f"invocation, pointed at a server started on that slug's "
            f"weights",
            file=sys.stderr,
        )
        return 2

    shot_counts = [int(s) for s in args.shots.split(",") if s]
    problems: list[str] = []
    preflight: dict[str, dict] = {}
    clients: dict[str, dict[str, ModelClient]] = {}
    for slug in slugs:
        try:
            arm_clients = make_arm_clients(
                args.backend, slug, constrained=args.constrained,
                host=args.host,
            )
            info = arm_clients["rust"].preflight()
        except ModelError as exc:
            problems.append(str(exc))
            continue
        # Stale-server guard: a llama-server left running from a previous
        # slug (or restarted on the wrong weights) would otherwise serve
        # every session under the WRONG model with no visible symptom.
        model_path = info.get("model_path") or ""
        if args.expect_model_path and args.expect_model_path not in model_path:
            problems.append(
                f"{slug}: --expect-model-path {args.expect_model_path!r} "
                f"not found in served model_path {model_path!r} -- stale "
                f"server guard tripped, restart the server on the intended "
                f"weights"
            )
            continue
        clients[slug] = arm_clients
        preflight[slug] = info
    problems.extend(preflight_environment(shot_counts))
    if problems:
        # dict.fromkeys dedupes while preserving order: a CLI-level
        # misconfiguration (e.g. --constrained without --backend llamacpp)
        # raises the identical ModelError once per slug in the loop above.
        for problem in dict.fromkeys(problems):
            print(problem, file=sys.stderr)
        return 2
    if args.preflight_only:
        print("preflight ok")
        return 0

    result = run_grid(
        lambda slug: clients[slug],
        slugs=slugs,
        shot_counts=shot_counts,
        seeds=parse_seeds(args.seeds),
        results_root=Path(args.results_root),
        health_check=wait_for_health,
        preflight=preflight,
        prefix=args.run_prefix,
        backend=args.backend,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["aborted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
