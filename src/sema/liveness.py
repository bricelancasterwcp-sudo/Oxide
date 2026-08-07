"""Backward liveness over the lowered event tree (SPEC.md sections 18,
28, 36).

Computes, for every ``IfNode``/``MatchNode``/``WhileNode``/``ForNode``
merge point, the set of var_ids that are live immediately AFTER the
merge (used later along some path). The linear checker consults this at
branch and loop-exit merges to decide between hoisting a drop into the
still-owning edge (var dead after the merge) and reporting OX0400 at
the next use (var live after the merge).

Loops run to a backward fixpoint so a use anywhere in a while/for body
keeps the variable live around the back edge. ``ReInit`` (assignment)
is a kill, never a gen: the old value is consumed by the assignment, so
a re-initialized variable is NOT live before it. ``BreakNode`` /
``ContinueNode`` (section 36) are jumps: at the jump the live set is
REPLACED by the jump target's — the loop's live-after set for break,
the next-iteration point's live-in for continue — discarding whatever
the unreachable statements after the jump would have contributed.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.sema import cfg


@dataclass(frozen=True, slots=True)
class _LoopLive:
    """Jump-target live sets for one enclosing loop (innermost last)."""

    break_live: frozenset[int]
    continue_live: frozenset[int]


def annotate(body: cfg.FnBody) -> dict[int, frozenset[int]]:
    """Return ``merge_key -> live-after set`` for every merge in ``body``."""
    live_after: dict[int, frozenset[int]] = {}
    _bwd_block(body.block, frozenset(), live_after, [])
    return live_after


def _bwd_block(
    block: cfg.BlockNode,
    live: frozenset[int],
    out: dict[int, frozenset[int]],
    loops: list[_LoopLive],
) -> frozenset[int]:
    for entry in reversed(block.stmts):
        live = _bwd_nodes(entry.nodes, live, out, loops)
    return live


def _bwd_nodes(
    nodes: tuple[cfg.Node, ...],
    live: frozenset[int],
    out: dict[int, frozenset[int]],
    loops: list[_LoopLive],
) -> frozenset[int]:
    for node in reversed(nodes):
        match node:
            case cfg.Use(var_id=var_id):
                live = live | {var_id}
            case cfg.Def(var_id=var_id) | cfg.ReInit(var_id=var_id):
                live = live - {var_id}
            case cfg.IfNode(
                merge_key=key, cond=cond, then_blk=then_blk, else_blk=else_blk
            ):
                out[key] = live
                then_in = _bwd_block(then_blk, live, out, loops)
                else_in = (
                    _bwd_block(else_blk, live, out, loops)
                    if else_blk is not None
                    else live
                )
                live = _bwd_nodes(cond, then_in | else_in, out, loops)
            case cfg.WhileNode(merge_key=key, cond=cond, body=body):
                after = live
                out[key] = after
                # live-out(body) = live-in(cond); live-out(cond) covers both
                # the loop-entry edge and the exit edge. A continue jumps to
                # the cond (current live-in estimate), a break to the loop
                # exit. Iterate to fixpoint; the converging pass
                # re-annotates inner merges with the final sets.
                cond_in: frozenset[int] = frozenset()
                while True:
                    loops.append(_LoopLive(after, cond_in))
                    body_in = _bwd_block(body, cond_in, out, loops)
                    loops.pop()
                    new_cond_in = _bwd_nodes(cond, body_in | after, out, loops)
                    if new_cond_in == cond_in:
                        break
                    cond_in = new_cond_in
                live = cond_in
            case cfg.ForNode(
                merge_key=key, iter=iter_nodes, var_id=var_id, body=body
            ):
                # The iterable's events fire once before the loop; the loop
                # variable is killed at each iteration's start (fresh
                # clone). A continue jumps to the next-iteration point
                # (live set = the body_out estimate), a break to the exit.
                after = live
                out[key] = after
                body_out: frozenset[int] = after
                while True:
                    loops.append(_LoopLive(after, body_out))
                    body_in = _bwd_block(body, body_out, out, loops)
                    loops.pop()
                    if var_id is not None:
                        body_in = body_in - {var_id}
                    new_out = body_in | after
                    if new_out == body_out:
                        break
                    body_out = new_out
                live = _bwd_nodes(iter_nodes, body_out, out, loops)
            case cfg.MatchNode(merge_key=key, scrut=scrut, arms=arms):
                out[key] = live
                if arms:
                    arms_in: frozenset[int] = frozenset()
                    for arm in arms:
                        arm_in = _bwd_block(arm.block, live, out, loops)
                        arms_in = arms_in | (arm_in - frozenset(arm.binders))
                else:
                    arms_in = live
                live = _bwd_nodes(scrut, arms_in, out, loops)
            case cfg.ReturnNode(value=value):
                # Nothing after a return executes on this path.
                live = _bwd_nodes(value, frozenset(), out, loops)
            case cfg.BreakNode():
                live = loops[-1].break_live if loops else frozenset()
            case cfg.ContinueNode():
                live = loops[-1].continue_live if loops else frozenset()
            case _:
                pass
    return live
