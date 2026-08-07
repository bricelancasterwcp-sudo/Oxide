"""Linear/move checking and drop insertion (SPEC.md sections 16-18, 28,
36).

Forward ownership state machine per non-copy variable:
``Owned -> Moved(span)`` on a MOVE-context use; a READ on Owned stays
Owned; any use of a Moved value reports OX0400 (read context) or OX0401
(move context) — or OX0403 when the conflicting move happened in a
previous loop iteration — then poisons the variable (no further
diagnostics for it in that function). Assignment (``ReInit``) consumes
the old value implicitly (no DropPoint) and returns the variable to
Owned — re-initializing a Moved variable is legal, and an assignment
before a loop back edge re-establishes ownership so the loop does not
trigger OX0403. Exception: a directly iterated for-loop variable is
borrowed for the whole loop in the emitted Rust (``v.iter().cloned()``),
so moving or assigning it inside the body reports OX0406 (the Rust
equivalents are E0505/E0506).

Match arms are an N-way branch merge generalizing the if/else rules: a
var moved in one or more arms and dead after the match is dropped
(block-end) in each still-owning arm; if live after, the next use is
OX0400. Arm binders and for-loop variables are fresh owned locals
scoped to their arm/body; unconsumed ones get block-end drops anchored
at the arm body / loop body.

break/continue (section 36) are CFG edges to the innermost loop's exit
merge / next-iteration point. Loop-body-scoped vars still Owned at the
jump are dropped there (kind ``before-jump``, anchored at the jump
statement). For outer vars, break edges join the loop-exit merge under
the section-18 rules (dead after the loop -> hoisted drops on
still-owned edges; live after -> OX0400 at the next use) and continue
edges join the back edge, interacting with OX0403/OX0406 unchanged.

Drop insertion (section 18) synthesizes a DropPoint for every non-copy
value not consumed by program code: after-stmt, block-end, branch-end
(absent-else hoisting), before-return unwinding, before-jump, and the
``<temp>`` rule for discarded expression-statement values. A function
with any linear diagnostic contributes no DropPoints.

Read-mode non-copy params are caller-owned borrows (section 18 as
amended): the callee synthesizes NO DropPoints for them — the caller's
own analysis drops the value after the call. Their use classification,
error detection, and mode inference are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.diagnostics import Diagnostic, Span
from src.parser import ast
from src.sema import cfg, liveness
from src.sema.infer import InferResult
from src.sema.modes import ModeResult
from src.sema.resolve import ResolveResult


@dataclass(frozen=True, slots=True)
class DropPoint:
    """One synthesized destruction point (SPEC.md section 15)."""

    fn: str
    var_id: int
    var_name: str
    # 'after-stmt' | 'block-end' | 'branch-end' | 'before-return'
    # | 'before-jump' (section 36)
    kind: str
    anchor_span: Span


@dataclass
class LinearResult:
    """Output of :func:`check_linear` (SPEC.md section 15)."""

    use_class: dict[int, str] = field(default_factory=dict)
    drops: tuple[DropPoint, ...] = ()
    diagnostics: list[Diagnostic] = field(default_factory=list)


# Ownership states: ('owned', None, False) or ('moved', move_span, prev_iter).
_State = tuple[str, Span | None, bool]
_OWNED: _State = ("owned", None, False)

# Sentinel: this path never falls through — it ended at a return, break,
# or continue. break/continue record their outer-var state in the
# enclosing _LoopCtx before diverging, so callers need no finer split.
_DIVERGED = object()


@dataclass
class _LoopCtx:
    """Join-point collector for one loop (section 36): ``outer`` is the
    var set in scope at loop entry (everything else is loop-body-scoped);
    break/continue record the outer-var state of each jump edge."""

    outer: frozenset[int]
    break_states: list[tuple[dict[int, _State], Span]] = field(
        default_factory=list
    )
    continue_states: list[dict[int, _State]] = field(default_factory=list)


def check_linear(
    module: ast.Module,
    resolved: ResolveResult,
    inferred: InferResult,
    modes: ModeResult,
) -> LinearResult:
    """Check every function; never raises."""
    result = LinearResult()
    drops: list[DropPoint] = []
    for fn in resolved.fns.values():
        body = cfg.lower_fn(fn, resolved, inferred, modes.modes)
        result.use_class.update(body.use_class)
        caller_owned = _caller_owned_params(fn, resolved, modes.modes, body)
        checker = _Checker(body, liveness.annotate(body), resolved, caller_owned)
        checker.run()
        result.diagnostics.extend(checker.diags)
        if not checker.diags:
            # Error-free function: its drops stand.
            drops.extend(checker.drops)
    result.drops = tuple(drops)
    return result


def _caller_owned_params(
    fn: ast.FnDecl,
    resolved: ResolveResult,
    modes: dict[str, tuple[str, ...]],
    body: cfg.FnBody,
) -> frozenset[int]:
    """Var-ids of the function's read-mode NON-COPY params.

    These are caller-owned borrows (SPEC.md section 18 as amended): the
    callee synthesizes no DropPoints for them. Copy params are excluded
    (they are never state-tracked and never dropped anyway).
    """
    mode_tuple = modes.get(fn.name, ())
    non_copy_params = {d.var_id for d in body.param_defs}
    out: set[int] = set()
    for index, param in enumerate(fn.params):
        if index < len(mode_tuple) and mode_tuple[index] == "read":
            for var_id in resolved.binds_of.get(param.node_id, ()):
                if var_id in non_copy_params:
                    out.add(var_id)
    return frozenset(out)


class _Checker:
    def __init__(
        self,
        body: cfg.FnBody,
        live_after: dict[int, frozenset[int]],
        resolved: ResolveResult,
        caller_owned: frozenset[int] = frozenset(),
    ) -> None:
        self.body = body
        self.live_after = live_after
        self.resolved = resolved
        self.caller_owned = caller_owned
        self.diags: list[Diagnostic] = []
        self.drops: dict[DropPoint, None] = {}  # ordered, deduplicated
        self.poisoned: set[int] = set()
        # Vars whose after-stmt anchor statement has completed on the
        # current path: their drop already fired before any later return.
        self._anchored: set[int] = set()
        self._anchor_cache: dict[int, dict[int, tuple[int, ...]]] = {}
        # Enclosing loops, innermost last (section 36 jump targets).
        self._loop_stack: list[_LoopCtx] = []

    # ------------------------------------------------------------- plumbing

    def run(self) -> None:
        state: dict[int, _State] = {
            d.var_id: _OWNED for d in self.body.param_defs
        }
        self._run_block(self.body.block, state)

    def _var_name(self, var_id: int) -> str:
        info = self.resolved.var_info.get(var_id)
        return info.name if info is not None else "?"

    def _drop(self, var_id: int, kind: str, anchor: Span) -> None:
        if var_id in self.caller_owned:
            return  # read-mode non-copy param: caller-owned (amended §18)
        name = "<temp>" if var_id == -1 else self._var_name(var_id)
        self.drops[DropPoint(self.body.name, var_id, name, kind, anchor)] = None

    # ----------------------------------------------------------- traversal

    def _run_block(
        self, block: cfg.BlockNode, state: dict[int, _State]
    ) -> dict[int, _State] | object:
        anchors = self._block_anchors(block)
        for index, entry in enumerate(block.stmts):
            result = self._run_nodes(entry.nodes, state)
            if result is _DIVERGED:
                return _DIVERGED
            state = result  # type: ignore[assignment]
            for var_id in anchors.get(index, ()):
                self._anchored.add(var_id)
                # Synthesize the after-stmt drop the moment the anchor
                # statement completes, not at block end: if every later
                # path returns, ``_end_of_block`` never runs, yet
                # ``_unwind`` already excludes anchored vars — without
                # this the value would get NO DropPoint on any path
                # (section 18 exactly-once). ``_end_of_block`` re-adding
                # the identical DropPoint on fall-through is deduplicated.
                var_state = state.get(var_id)
                if (
                    var_state is not None
                    and var_state[0] == "owned"
                    and var_id not in self.poisoned
                ):
                    self._drop(var_id, "after-stmt", entry.span)
        self._end_of_block(block, state)
        return state

    def _block_anchors(self, block: cfg.BlockNode) -> dict[int, tuple[int, ...]]:
        """Entry index of each block-owned var's last use — the statement
        ``_end_of_block`` will anchor its after-stmt drop at."""
        cached = self._anchor_cache.get(id(block))
        if cached is not None:
            return cached
        anchors: dict[int, list[int]] = {}
        for var_id in block.owned:
            for index in range(len(block.stmts) - 1, -1, -1):
                if var_id in block.stmts[index].used_vars:
                    anchors.setdefault(index, []).append(var_id)
                    break
        frozen = {index: tuple(ids) for index, ids in anchors.items()}
        self._anchor_cache[id(block)] = frozen
        return frozen

    def _end_of_block(
        self, block: cfg.BlockNode, state: dict[int, _State]
    ) -> None:
        """Drop still-owned vars defined by this block and close their scope."""
        for var_id in block.owned:
            var_state = state.pop(var_id, None)
            if (
                var_state is None
                or var_state[0] != "owned"
                or var_id in self.poisoned
            ):
                continue
            anchor_entry = None
            for entry in reversed(block.stmts):
                if var_id in entry.used_vars:
                    anchor_entry = entry
                    break
            if anchor_entry is None:
                # Never used after its definition.
                self._drop(var_id, "block-end", block.span)
            else:
                # Final use was a read: drop after the outermost statement
                # in the defining scope at which liveness ends.
                self._drop(var_id, "after-stmt", anchor_entry.span)

    def _run_nodes(
        self, nodes: tuple[cfg.Node, ...], state: dict[int, _State]
    ) -> dict[int, _State] | object:
        for node in nodes:
            match node:
                case cfg.Use():
                    self._use(node, state)
                case cfg.Def(var_id=var_id) | cfg.ReInit(var_id=var_id):
                    # Def: fresh binding. ReInit: assignment — the old value
                    # is consumed implicitly (no DropPoint); re-initializing
                    # a Moved variable is legal (section 28).
                    state[var_id] = _OWNED
                case cfg.TempMark(span=span):
                    self._drop(-1, "after-stmt", span)
                case cfg.IfNode():
                    result = self._run_if(node, state)
                    if result is _DIVERGED:
                        return _DIVERGED
                    state = result  # type: ignore[assignment]
                case cfg.WhileNode():
                    result = self._run_while(node, state)
                    if result is _DIVERGED:
                        return _DIVERGED
                    state = result  # type: ignore[assignment]
                case cfg.ForNode():
                    result = self._run_for(node, state)
                    if result is _DIVERGED:
                        return _DIVERGED
                    state = result  # type: ignore[assignment]
                case cfg.MatchNode():
                    result = self._run_match(node, state)
                    if result is _DIVERGED:
                        return _DIVERGED
                    state = result  # type: ignore[assignment]
                case cfg.ReturnNode(value=value, span=span):
                    result = self._run_nodes(value, state)
                    if result is _DIVERGED:
                        return _DIVERGED
                    self._unwind(result, span)  # type: ignore[arg-type]
                    return _DIVERGED
                case cfg.BreakNode(span=span):
                    self._jump(state, span, is_break=True)
                    return _DIVERGED
                case cfg.ContinueNode(span=span):
                    self._jump(state, span, is_break=False)
                    return _DIVERGED
        return state

    def _jump(self, state: dict[int, _State], span: Span, is_break: bool) -> None:
        """Section 36: loop-body-scoped vars still Owned at a break or
        continue drop at the jump (``before-jump``); the outer-var state
        joins the loop-exit merge (break) or the back edge (continue).
        Vars whose after-stmt anchor already completed on this path are
        excluded — their drop already fired (as in ``_unwind``)."""
        if not self._loop_stack:
            return  # unreachable behind the parse gate (OX0105)
        ctx = self._loop_stack[-1]
        for var_id, var_state in state.items():
            if (
                var_id not in ctx.outer
                and var_state[0] == "owned"
                and var_id not in self.poisoned
                and var_id not in self._anchored
            ):
                self._drop(var_id, "before-jump", span)
        outer_state = {k: v for k, v in state.items() if k in ctx.outer}
        if is_break:
            ctx.break_states.append((outer_state, span))
        else:
            ctx.continue_states.append(outer_state)

    def _unwind(self, state: dict[int, _State], span: Span) -> None:
        """Before an early return every still-owned in-scope var drops
        (the returned value was already consumed by its move event).
        Vars whose after-stmt anchor statement already completed on this
        path are excluded: their liveness ended there and the anchored
        drop fires on this path too — dropping again would double-drop."""
        for var_id, var_state in state.items():
            if (
                var_state[0] == "owned"
                and var_id not in self.poisoned
                and var_id not in self._anchored
            ):
                self._drop(var_id, "before-return", span)

    # -------------------------------------------------------- state machine

    def _use(self, node: cfg.Use, state: dict[int, _State]) -> None:
        if node.var_id in self.poisoned:
            return
        var_state = state.get(node.var_id, _OWNED)
        if var_state[0] == "owned":
            if node.cls == "move":
                state[node.var_id] = ("moved", node.span, False)
            return
        _tag, move_span, prev_iter = var_state
        name = self._var_name(node.var_id)
        if prev_iter:
            code = "OX0403"
            message = f"value '{name}' was moved in a previous loop iteration"
        elif node.cls == "move":
            code = "OX0401"
            message = f"double move of value '{name}'"
        else:
            code = "OX0400"
            message = f"use of moved value '{name}'"
        notes: tuple[tuple[str, Span], ...] = ()
        if move_span is not None:
            notes = (("value moved here", move_span),)
        self.diags.append(Diagnostic(code, message, node.span, notes))
        self.poisoned.add(node.var_id)

    # ------------------------------------------------------------- branches

    def _run_if(
        self, node: cfg.IfNode, state: dict[int, _State]
    ) -> dict[int, _State] | object:
        result = self._run_nodes(node.cond, state)
        if result is _DIVERGED:
            return _DIVERGED
        pre: dict[int, _State] = result  # type: ignore[assignment]
        then_state = self._run_block(node.then_blk, dict(pre))
        else_state = (
            self._run_block(node.else_blk, dict(pre))
            if node.else_blk is not None
            else dict(pre)
        )
        if then_state is _DIVERGED and else_state is _DIVERGED:
            return _DIVERGED
        if then_state is _DIVERGED:
            return else_state
        if else_state is _DIVERGED:
            return then_state
        return self._merge_if(node, pre, then_state, else_state)  # type: ignore[arg-type]

    def _merge_if(
        self,
        node: cfg.IfNode,
        pre: dict[int, _State],
        then_state: dict[int, _State],
        else_state: dict[int, _State],
    ) -> dict[int, _State]:
        merged: dict[int, _State] = {}
        live = self.live_after.get(node.merge_key, frozenset())
        for var_id in pre:
            a = then_state.get(var_id, pre[var_id])
            b = else_state.get(var_id, pre[var_id])
            a_owned = a[0] == "owned"
            b_owned = b[0] == "owned"
            if var_id in self.poisoned or a_owned == b_owned:
                # Same ownership on both edges (or already reported).
                merged[var_id] = a
                continue
            moved_state = b if a_owned else a
            if var_id in live:
                # Live past the merge: the next use reports use-after-move.
                merged[var_id] = moved_state
                continue
            # Dead after the merge: the still-owning edge drops it.
            if a_owned:
                self._drop(var_id, "block-end", node.then_blk.span)
            elif node.else_blk is not None:
                self._drop(var_id, "block-end", node.else_blk.span)
            else:
                self._drop(var_id, "branch-end", node.span)
            merged[var_id] = moved_state
        return merged

    # -------------------------------------------------------------- matches

    def _run_match(
        self, node: cfg.MatchNode, state: dict[int, _State]
    ) -> dict[int, _State] | object:
        result = self._run_nodes(node.scrut, state)
        if result is _DIVERGED:
            return _DIVERGED
        pre: dict[int, _State] = result  # type: ignore[assignment]
        if not node.arms:
            return pre
        exits: list[tuple[cfg.ArmNode, dict[int, _State]]] = []
        for arm in node.arms:
            arm_state = dict(pre)
            for binder in arm.binders:
                arm_state[binder] = _OWNED
            out = self._run_block(arm.block, arm_state)
            if out is _DIVERGED:
                continue
            arm_exit: dict[int, _State] = out  # type: ignore[assignment]
            self._drop_arm_binders(arm, arm_exit)
            exits.append((arm, arm_exit))
        if not exits:
            return _DIVERGED
        return self._merge_match(node, pre, exits)

    def _drop_arm_binders(
        self, arm: cfg.ArmNode, arm_exit: dict[int, _State]
    ) -> None:
        """Unconsumed arm binders get block-end drops anchored at the arm
        body (section 28); consumed ones leave nothing behind."""
        for binder in arm.binders:
            binder_state = arm_exit.pop(binder, None)
            if (
                binder_state is not None
                and binder_state[0] == "owned"
                and binder not in self.poisoned
            ):
                self._drop(binder, "block-end", arm.block.span)

    def _merge_match(
        self,
        node: cfg.MatchNode,
        pre: dict[int, _State],
        exits: list[tuple[cfg.ArmNode, dict[int, _State]]],
    ) -> dict[int, _State]:
        """N-way generalization of the section-18 if/else join rules."""
        merged: dict[int, _State] = {}
        live = self.live_after.get(node.merge_key, frozenset())
        for var_id in pre:
            states = [exit.get(var_id, pre[var_id]) for _arm, exit in exits]
            owned_flags = [s[0] == "owned" for s in states]
            if var_id in self.poisoned or len(set(owned_flags)) == 1:
                # Same ownership on every edge (or already reported).
                merged[var_id] = states[0]
                continue
            moved_state = next(s for s in states if s[0] != "owned")
            if var_id not in live:
                # Dead after the merge: each still-owning arm drops it.
                for (arm, _exit), owned in zip(exits, owned_flags):
                    if owned:
                        self._drop(var_id, "block-end", arm.block.span)
            # Live past the merge: the next use reports use-after-move.
            merged[var_id] = moved_state
        return merged

    # ---------------------------------------------------------------- loops

    def _run_while(
        self, node: cfg.WhileNode, state: dict[int, _State]
    ) -> dict[int, _State] | object:
        entry = state
        pre = dict(state)
        ctx = _LoopCtx(outer=frozenset(state))
        self._loop_stack.append(ctx)
        try:
            while True:
                # Re-collect jump edges each pass; the converged pass wins.
                ctx.break_states.clear()
                ctx.continue_states.clear()
                probe = self._run_nodes(node.cond, dict(entry))
                if probe is _DIVERGED:
                    return _DIVERGED
                body_exit = self._run_block(node.body, probe)  # type: ignore[arg-type]
                back_states = list(ctx.continue_states)
                if body_exit is not _DIVERGED:
                    back_states.append(body_exit)  # type: ignore[arg-type]
                new_entry = self._merge_backedge(entry, back_states)
                if new_entry == entry:
                    break
                entry = new_entry
        finally:
            self._loop_stack.pop()
        # Normal exit: the condition evaluates once more and fails.
        exit_probe = self._run_nodes(node.cond, dict(entry))
        normal = None if exit_probe is _DIVERGED else exit_probe
        merged = self._merge_loop_exit(node, pre, normal, ctx.break_states)  # type: ignore[arg-type]
        if merged is _DIVERGED:
            return _DIVERGED
        self._drop_skip_edge(pre, merged, node.span)  # type: ignore[arg-type]
        return merged

    def _run_for(
        self, node: cfg.ForNode, state: dict[int, _State]
    ) -> dict[int, _State] | object:
        # The iterable's events fire once, before the loop (section 28).
        result = self._run_nodes(node.iter, state)
        if result is _DIVERGED:
            return _DIVERGED
        entry: dict[int, _State] = result  # type: ignore[assignment]
        pre = dict(entry)
        # The loop variable is NOT in ``outer``: it is body-scoped, so a
        # jump drops it (before-jump) exactly like a body-local binding.
        ctx = _LoopCtx(outer=frozenset(entry))
        self._loop_stack.append(ctx)
        try:
            while True:
                ctx.break_states.clear()
                ctx.continue_states.clear()
                body_state = dict(entry)
                if node.var_id is not None:
                    # Fresh owned clone of the element each iteration.
                    body_state[node.var_id] = _OWNED
                body_exit = self._run_block(node.body, body_state)
                back_states = list(ctx.continue_states)
                if body_exit is not _DIVERGED:
                    exit_state: dict[int, _State] = body_exit  # type: ignore[assignment]
                    self._drop_loop_var(node, exit_state)
                    back_states.append(exit_state)
                new_entry = self._merge_backedge(entry, back_states)
                if new_entry == entry:
                    break
                entry = new_entry
        finally:
            self._loop_stack.pop()
        self._check_iter_borrow(node)
        # The loop may run zero times or exhaust: the normal exit edge is
        # the entry fixpoint; break edges join it (section 36).
        merged = self._merge_loop_exit(node, pre, dict(entry), ctx.break_states)
        if merged is _DIVERGED:
            return _DIVERGED
        self._drop_skip_edge(pre, merged, node.span)  # type: ignore[arg-type]
        return merged

    def _merge_loop_exit(
        self,
        node: cfg.WhileNode | cfg.ForNode,
        pre: dict[int, _State],
        normal: dict[int, _State] | None,
        break_states: list[tuple[dict[int, _State], Span]],
    ) -> dict[int, _State] | object:
        """Join the loop's exit edges — the normal exit plus one edge per
        break — generalizing the section-18 merge rules (section 36): an
        outer var moved on some edge and dead after the loop is dropped
        on each still-owning edge (before-jump at that break, branch-end
        at the loop for the normal edge); live after, the next use
        reports the use-after-move."""
        edges: list[tuple[dict[int, _State], str, Span]] = []
        if normal is not None:
            edges.append((normal, "loop", node.span))
        for break_state, span in break_states:
            edges.append((break_state, "break", span))
        if not edges:
            return _DIVERGED  # cond always returns and nothing breaks
        live = self.live_after.get(node.merge_key, frozenset())
        merged: dict[int, _State] = {}
        for var_id in pre:
            states = [st.get(var_id, pre[var_id]) for st, _kind, _span in edges]
            owned_flags = [s[0] == "owned" for s in states]
            if var_id in self.poisoned or len(set(owned_flags)) == 1:
                # Same ownership on every edge (or already reported).
                merged[var_id] = states[0]
                continue
            moved_state = next(s for s in states if s[0] != "owned")
            if var_id not in live:
                # Dead after the loop: each still-owning edge drops it.
                for (_st, kind, span), owned in zip(edges, owned_flags):
                    if not owned:
                        continue
                    if kind == "break":
                        self._drop(var_id, "before-jump", span)
                    else:
                        self._drop(var_id, "branch-end", node.span)
            # Live past the merge: the next use reports use-after-move.
            merged[var_id] = moved_state
        return merged

    def _check_iter_borrow(self, node: cfg.ForNode) -> None:
        """A directly iterated variable is borrowed for the whole loop in
        the emitted Rust (``VAR.iter().cloned()``): a body event that
        moves or re-initializes it emits Rust rustc rejects (E0505 /
        E0506), so it must be a diagnostic here (accepted-implies-
        compiles). The state machine alone misses it because ``ReInit``
        legally re-owns before each back edge. Runs after the body
        fixpoint so a plain move keeps reporting OX0403 (poisoned vars
        are skipped)."""
        if node.borrowed is None or node.borrowed in self.poisoned:
            return
        conflict = cfg.iter_borrow_conflict(node.body, node.borrowed)
        if conflict is None:
            return
        kind, span = conflict
        name = self._var_name(node.borrowed)
        verb = "assign to" if kind == "assign" else "move out of"
        iter_span = next(
            (
                n.span
                for n in node.iter
                if isinstance(n, cfg.Use) and n.var_id == node.borrowed
            ),
            node.span,
        )
        self.diags.append(
            Diagnostic(
                "OX0406",
                f"cannot {verb} '{name}' while it is being iterated",
                span,
                (("borrowed by this for loop", iter_span),),
            )
        )
        self.poisoned.add(node.borrowed)

    def _drop_skip_edge(
        self,
        pre: dict[int, _State],
        exit_state: dict[int, _State],
        span: Span,
    ) -> None:
        """A var owned at loop entry but moved at the loop exit (net
        reinit-before-move body) is consumed by program code on every
        >=1-iteration path, yet still owned on the zero-iteration path:
        section 18 exactly-once needs a drop on that loop-skipped edge —
        the branch-end analog of the absent-else rule, anchored at the
        loop. Codegen leaves it to the target's conditional scope drop
        (an unconditional ``drop`` after the loop would not compile). If
        the var is live after the loop, the next use reports OX0403 and
        the function's drops are suppressed anyway."""
        for var_id, before in pre.items():
            if before[0] != "owned" or var_id in self.poisoned:
                continue
            after = exit_state.get(var_id, before)
            if after[0] == "moved":
                self._drop(var_id, "branch-end", span)

    def _drop_loop_var(self, node: cfg.ForNode, exit_state: dict[int, _State]) -> None:
        """An unconsumed loop variable gets a per-iteration block-end drop
        anchored at the loop body (section 28); moving it is legal."""
        if node.var_id is None:
            return
        var_state = exit_state.pop(node.var_id, None)
        if (
            var_state is not None
            and var_state[0] == "owned"
            and node.var_id not in self.poisoned
        ):
            self._drop(node.var_id, "block-end", node.body.span)

    @staticmethod
    def _merge_backedge(
        entry: dict[int, _State], back_states: list[dict[int, _State]]
    ) -> dict[int, _State]:
        """Join the loop back edges — the body fall-through plus one edge
        per continue (section 36) — into the next iteration's entry."""
        if not back_states:
            return entry  # no back edge: every body path returns or breaks
        merged: dict[int, _State] = {}
        for var_id, before in entry.items():
            moved: _State | None = None
            if before[0] == "owned":
                for back_state in back_states:
                    after = back_state.get(var_id, before)
                    if after[0] == "moved":
                        moved = after
                        break
            if moved is not None:
                # Moved on some back edge: in the next iteration the
                # conflicting move happened in a previous iteration.
                merged[var_id] = ("moved", moved[1], True)
            else:
                merged[var_id] = before
        return merged
