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
