"""Evaluation harness package (SPEC.md Part IX, sections 44-46).

Modules:

- ``eval.harness`` — the importable harness + CLI (section 45): the
  ``check``/``run``/``prompt``/``report`` subcommands and the session
  API (``new_session`` / ``Session.submit``).
- ``eval.rustc_adapter`` — the ``rustc --error-format=json`` adapter
  that maps rustc diagnostics onto the section-39 JSON diagnostic
  shape, plus compile/execute helpers shared by all three arms.

Data files (authored separately; schema in section 44):

- ``eval/tasks.jsonl`` — the 20-task corpus.
- ``eval/shots/<arm>/`` — few-shot task/solution pairs per arm.
- ``eval/solutions/{oxide,explicit,rust}/`` — reference solutions.
- ``eval/results/<run_id>/triples.jsonl`` — session submission logs.
"""
