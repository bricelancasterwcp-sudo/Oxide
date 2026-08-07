"""Explicit-Oxide dialect (SPEC.md section 41): the matched-novelty control.

The dialect makes ownership explicit — ``&name`` read uses, ``name: &Type``
read-mode params, and ``drop name`` statements — and verifies the written
annotations against the unchanged core analysis instead of inferring them.
"""

from src.explicit.pipeline import run, transpile
from src.explicit.verify import EX_SUGGESTIONS

__all__ = ["run", "transpile", "EX_SUGGESTIONS"]
