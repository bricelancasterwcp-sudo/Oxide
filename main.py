"""Oxide transpiler CLI entry point (SPEC.md sections 21 and 39).

Thin wrapper: the implementation lives in :mod:`src.cli`.

Usage: ``python3 main.py [--json] [--check] [--dialect=explicit] <file.ox>``

Text mode matches the section-21 behavior exactly: generated Rust to
stdout and exit 0 on success; diagnostics rendered to stderr as
``error[OXnnnn] <line>:<col>: <message>`` (plus one
``  note <line>:<col>`` line per notes entry) with exit 1; a missing or
unreadable file reports to stderr and exits 2. Sections 39-41 add
``--json``, ``--check``, and ``--dialect=explicit``.
"""

from __future__ import annotations

import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
