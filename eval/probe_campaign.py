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

from eval.probe import ProbeError, _select, load_probes, run_corpus

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
    provenance: dict | None = None,
) -> list[tuple[str, int]]:
    """Run every unfinished cell. Returns the cells it ran.

    PROVENANCE IS REQUIRED, not optional. Pass ``provenance`` and it is
    written before the first cell runs; omit it and ``provenance.json``
    must already exist under ``root``, or the campaign refuses to start.
    There is no third path in which 600 repairs land on disk with no
    record of which weights produced them -- that is exactly how the 6a
    pilot's demand table became permanently irreproducible, and this
    module's own docstring cites that as its reason for existing. A
    provenance step that a caller can simply forget to invoke reproduces
    the hazard it was added to prevent.

    The check runs on resume too, and that is deliberate: a resumed
    campaign is where a forgotten first step is least likely to be
    noticed.
    """
    root = Path(root)
    if provenance is not None:
        write_provenance(root, provenance)
    elif not (root / "provenance.json").is_file():
        raise ProbeError(
            f"refusing to start: no provenance for this campaign. Pass "
            f"provenance=... or write {root / 'provenance.json'} first. "
            f"A run whose weights cannot be identified afterwards is not "
            f"a result, it is 600 repairs of unattributable text."
        )
    ran: list[tuple[str, int]] = []
    for arm, seed in pending_cells(root, arms, seeds):
        run_cell(root, arm, seed, client_factory)
        ran.append((arm, seed))
    return ran
