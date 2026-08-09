"""Event lowering for the ownership analyses (SPEC.md sections 17-18,
28, 36).

Lowers each function body into a structured tree of ownership events:
``Use``/``Def``/``ReInit`` leaves plus ``IfNode``/``WhileNode``/
``MatchNode``/``ForNode``/``ReturnNode``/``BreakNode``/``ContinueNode``
control nodes, grouped into per-statement entries so drop insertion can
anchor at statement granularity (the block tail counts as a statement
position). Every ``Var`` use of a non-copy local is classified per the
section-17 context table (as amended by sections 28 and 36: match
scrutinees, variant constructor payloads, ``?`` operands, and
functional-update rests are MOVE, for-iterables are READ; a non-copy
field access is an implicit clone — the base stays a READ and the fresh
value is an ordinary temporary); Copy locals are recorded in
``use_class`` as ``'copy'`` and produce no events (they are never
state-tracked).

Only :mod:`src.sema.analyze`'s API is contractual; this module is an
internal helper shared by ``modes`` and ``linear``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.diagnostics import Span
from src.parser import ast
from src.sema.infer import InferResult
from src.sema.resolve import ResolveResult
from src.sema.types import ERROR_TYPE, is_copy

_READ = "read"
_MOVE = "move"
_COPY = "copy"


@dataclass(frozen=True, slots=True)
class Use:
    """A read or move of a non-copy local at one AST node.

    Almost always a ``Var``. The exception is §56's field assignment,
    whose base is a READ recorded against the ``FieldAssign`` STATEMENT
    node -- there is no ``Var`` node for the base to hang it on.
    """

    var_id: int
    node_id: int
    span: Span
    cls: str  # 'read' | 'move'


@dataclass(frozen=True, slots=True)
class Def:
    """A non-copy local coming into existence (param or let binder)."""

    var_id: int
    span: Span


@dataclass(frozen=True, slots=True)
class ReInit:
    """An assignment re-establishing ownership of a non-copy local
    (SPEC.md section 28): the previous value is consumed implicitly (no
    DropPoint), and assigning to a Moved variable is legal
    re-initialization. Counts as an anchor event for drop placement and
    as a liveness kill, but never as a use."""

    var_id: int
    span: Span


@dataclass(frozen=True, slots=True)
class TempMark:
    """An expression statement discarding a non-copy value (``<temp>``)."""

    span: Span


@dataclass(frozen=True, slots=True)
class IfNode:
    """A two-way branch; ``else_blk`` is None for an absent else."""

    merge_key: int
    span: Span
    cond: tuple[Node, ...]
    then_blk: BlockNode
    else_blk: BlockNode | None


@dataclass(frozen=True, slots=True)
class WhileNode:
    """A while loop; ``merge_key`` identifies its loop-exit merge point
    (section 36: break edges join it)."""

    merge_key: int
    span: Span
    cond: tuple[Node, ...]
    body: BlockNode


@dataclass(frozen=True, slots=True)
class ForNode:
    """A for-in loop (SPEC.md section 28): the iterable's events fire
    once before the loop; ``var_id`` is the non-copy loop variable (None
    when the element type is Copy), a fresh owned clone each iteration
    scoped to ``body``. ``borrowed`` is the var_id of a directly
    iterated non-copy variable: codegen emits ``VAR.iter().cloned()``,
    which borrows that variable for the whole loop, so the body must
    not move or re-initialize it (rustc E0505/E0506); None when the
    iterable is any other expression (calls return owned temporaries,
    so no local stays borrowed across the loop). ``merge_key``
    identifies the loop-exit merge point (section 36)."""

    merge_key: int
    span: Span
    iter: tuple[Node, ...]
    var_id: int | None
    body: BlockNode
    borrowed: int | None


@dataclass(frozen=True, slots=True)
class ArmNode:
    """One match arm: its non-copy binder var_ids plus the arm body."""

    binders: tuple[int, ...]
    block: BlockNode


@dataclass(frozen=True, slots=True)
class MatchNode:
    """An N-way branch (SPEC.md section 28); the scrutinee events are a
    MOVE and arm merges generalize the if/else join rules."""

    merge_key: int
    span: Span
    scrut: tuple[Node, ...]
    arms: tuple[ArmNode, ...]


@dataclass(frozen=True, slots=True)
class ReturnNode:
    span: Span
    value: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class BreakNode:
    """A break statement (section 36): an edge to the innermost loop's
    exit merge."""

    span: Span


@dataclass(frozen=True, slots=True)
class ContinueNode:
    """A continue statement (section 36): an edge to the innermost
    loop's next-iteration point (the back edge)."""

    span: Span


@dataclass(frozen=True, slots=True)
class StmtEntry:
    """One statement position of a block (a tail expression is one too)."""

    span: Span
    nodes: tuple[Node, ...]
    used_vars: frozenset[int]


@dataclass(frozen=True, slots=True)
class BlockNode:
    span: Span
    stmts: tuple[StmtEntry, ...]
    owned: tuple[int, ...]  # non-copy vars defined here, definition order


type Node = (
    Use
    | Def
    | ReInit
    | TempMark
    | IfNode
    | WhileNode
    | ForNode
    | MatchNode
    | ReturnNode
    | BreakNode
    | ContinueNode
)


@dataclass(frozen=True, slots=True)
class FnBody:
    """One lowered function body plus its Var-use classification."""

    name: str
    param_defs: tuple[Def, ...]
    block: BlockNode
    use_class: dict[int, str]


def lower_fn(
    fn: ast.FnDecl,
    resolved: ResolveResult,
    inferred: InferResult,
    modes: dict[str, tuple[str, ...]],
) -> FnBody:
    """Lower one function under the given per-callee parameter modes."""
    return _Lowerer(resolved, inferred, modes).run(fn)


def collect_uses(nodes: tuple[Node, ...]) -> frozenset[int]:
    """All var_ids with a ``Use`` anywhere inside ``nodes`` (recursively)."""
    out: set[int] = set()
    stack: list[object] = list(nodes)
    while stack:
        node = stack.pop()
        match node:
            case Use(var_id=var_id):
                out.add(var_id)
            case ReInit(var_id=var_id):
                # Not a use, but an anchorable event: a drop for a var whose
                # last event is a re-init anchors at that statement.
                out.add(var_id)
            case IfNode(cond=cond, then_blk=then_blk, else_blk=else_blk):
                stack.extend(cond)
                stack.append(then_blk)
                if else_blk is not None:
                    stack.append(else_blk)
            case WhileNode(cond=cond, body=body):
                stack.extend(cond)
                stack.append(body)
            case ForNode(iter=iter_nodes, body=body):
                stack.extend(iter_nodes)
                stack.append(body)
            case MatchNode(scrut=scrut, arms=arms):
                stack.extend(scrut)
                for arm in arms:
                    stack.append(arm.block)
            case ReturnNode(value=value):
                stack.extend(value)
            case BlockNode(stmts=stmts):
                for entry in stmts:
                    stack.extend(entry.nodes)
    return frozenset(out)


def iter_borrow_conflict(
    block: BlockNode, var_id: int
) -> tuple[str, Span] | None:
    """First body event conflicting with a for-loop's iteration borrow.

    Returns ``('assign', span)`` for a ``ReInit`` of ``var_id`` or
    ``('move', span)`` for a MOVE-class ``Use``, whichever comes first
    in source order (recursing through nested control flow), else None.
    Either event emits Rust that rustc rejects (E0506/E0505): the loop's
    ``VAR.iter().cloned()`` borrows the variable for the whole loop.
    """
    for entry in block.stmts:
        found = _conflict_in_nodes(entry.nodes, var_id)
        if found is not None:
            return found
    return None


def _conflict_in_nodes(
    nodes: tuple[Node, ...], var_id: int
) -> tuple[str, Span] | None:
    for node in nodes:
        found: tuple[str, Span] | None = None
        match node:
            case ReInit(var_id=vid, span=span) if vid == var_id:
                return ("assign", span)
            case Use(var_id=vid, span=span, cls=cls) if (
                vid == var_id and cls == _MOVE
            ):
                return ("move", span)
            case IfNode(cond=cond, then_blk=then_blk, else_blk=else_blk):
                found = _conflict_in_nodes(cond, var_id)
                if found is None:
                    found = iter_borrow_conflict(then_blk, var_id)
                if found is None and else_blk is not None:
                    found = iter_borrow_conflict(else_blk, var_id)
            case WhileNode(cond=cond, body=body):
                found = _conflict_in_nodes(cond, var_id)
                if found is None:
                    found = iter_borrow_conflict(body, var_id)
            case ForNode(iter=iter_nodes, body=body):
                found = _conflict_in_nodes(iter_nodes, var_id)
                if found is None:
                    found = iter_borrow_conflict(body, var_id)
            case MatchNode(scrut=scrut, arms=arms):
                found = _conflict_in_nodes(scrut, var_id)
                for arm in arms:
                    if found is not None:
                        break
                    found = iter_borrow_conflict(arm.block, var_id)
            case ReturnNode(value=value):
                found = _conflict_in_nodes(value, var_id)
        if found is not None:
            return found
    return None


def move_used_vars(body: FnBody) -> frozenset[int]:
    """All var_ids used in a MOVE context on some reachable path.

    Code after a guaranteed return is unreachable and contributes no
    evidence ('a param is own iff SOME PATH uses it in a MOVE context'),
    matching the linear checker's reachability treatment.
    """
    out: set[int] = set()
    _scan_moves_block(body.block, out)
    return frozenset(out)


def _scan_moves_block(block: BlockNode, out: set[int]) -> bool:
    """Collect reachable MOVE uses; True iff the block never falls
    through (every path returns or jumps)."""
    for entry in block.stmts:
        if _scan_moves(entry.nodes, out):
            return True
    return False


def _scan_moves(nodes: tuple[Node, ...], out: set[int]) -> bool:
    """Collect reachable MOVE uses; True iff execution never falls
    through (every path returns or jumps out of the enclosing loop)."""
    for node in nodes:
        match node:
            case Use(var_id=var_id, cls=cls):
                if cls == _MOVE:
                    out.add(var_id)
            case IfNode(cond=cond, then_blk=then_blk, else_blk=else_blk):
                if _scan_moves(cond, out):
                    return True
                then_returns = _scan_moves_block(then_blk, out)
                else_returns = (
                    _scan_moves_block(else_blk, out)
                    if else_blk is not None
                    else False
                )
                if then_returns and else_returns:
                    return True
            case WhileNode(cond=cond, body=blk):
                if _scan_moves(cond, out):
                    return True
                # The body may run, so its moves count — but the loop as
                # a whole never guarantees a return (cond may fail first).
                _scan_moves_block(blk, out)
            case ForNode(iter=iter_nodes, body=blk):
                if _scan_moves(iter_nodes, out):
                    return True
                # The body may run zero times: moves count as evidence but
                # the loop never guarantees a return.
                _scan_moves_block(blk, out)
            case MatchNode(scrut=scrut, arms=arms):
                if _scan_moves(scrut, out):
                    return True
                all_return = bool(arms)
                for arm in arms:
                    if not _scan_moves_block(arm.block, out):
                        all_return = False
                if all_return:
                    return True
            case ReturnNode(value=value):
                _scan_moves(value, out)
                return True
            case BreakNode() | ContinueNode():
                # The jump never falls through: later statements in this
                # block are unreachable and contribute no move evidence.
                return True
    return False


class _Lowerer:
    def __init__(
        self,
        resolved: ResolveResult,
        inferred: InferResult,
        modes: dict[str, tuple[str, ...]],
    ) -> None:
        self.resolved = resolved
        self.inferred = inferred
        self.modes = modes
        self.use_class: dict[int, str] = {}
        self._next_key = 0

    # ------------------------------------------------------------- helpers

    def _is_copy_var(self, var_id: int) -> bool:
        return is_copy(self.inferred.var_types.get(var_id, ERROR_TYPE))

    def _fresh_key(self) -> int:
        key = self._next_key
        self._next_key += 1
        return key

    @staticmethod
    def _entry(span: Span, nodes: list[Node]) -> StmtEntry:
        frozen = tuple(nodes)
        return StmtEntry(span, frozen, collect_uses(frozen))

    # ------------------------------------------------------------ top level

    def run(self, fn: ast.FnDecl) -> FnBody:
        param_defs: list[Def] = []
        for param in fn.params:
            for var_id in self.resolved.binds_of.get(param.node_id, ()):
                if not self._is_copy_var(var_id):
                    param_defs.append(Def(var_id, param.span))
        block = self._block(
            fn.body, _MOVE, pre_owned=[d.var_id for d in param_defs]
        )
        return FnBody(fn.name, tuple(param_defs), block, self.use_class)

    # --------------------------------------------------------------- blocks

    def _block(
        self,
        blk: ast.Block,
        tail_ctx: str,
        pre_owned: list[int] | None = None,
    ) -> BlockNode:
        owned: list[int] = list(pre_owned or [])
        stmts: list[StmtEntry] = []
        for stmt in blk.stmts:
            stmts.append(self._entry(stmt.span, self._stmt(stmt, owned)))
        if blk.tail is not None:
            stmts.append(self._entry(blk.tail.span, self._expr(blk.tail, tail_ctx)))
        return BlockNode(blk.span, tuple(stmts), tuple(owned))

    def _stmt(self, stmt: ast.Stmt, owned: list[int]) -> list[Node]:
        match stmt:
            case ast.Let(pattern=pattern, init=init):
                # Let initializer / destructure scrutinee: MOVE context.
                nodes = self._expr(init, _MOVE)
                for var_id in self.resolved.binds_of.get(pattern.node_id, ()):
                    if not self._is_copy_var(var_id):
                        nodes.append(Def(var_id, pattern.span))
                        owned.append(var_id)
                return nodes
            case ast.Assign(value=value):
                # Section 28: the RHS move happens first, then the target is
                # re-owned; the old value is consumed implicitly (no drop).
                nodes = self._expr(value, _MOVE)
                var_id = self.resolved.assign_of.get(stmt.node_id)
                if var_id is not None and not self._is_copy_var(var_id):
                    nodes.append(ReInit(var_id, stmt.span))
                return nodes
            case ast.FieldAssign(value=value):
                # Section 56: the RHS moves first; the base is a READ (§36
                # already fixes `p.x` as a read of the base) and emits NO
                # ReInit -- writing a field must not re-establish ownership
                # of a moved struct, unlike `p = e`, which does. The
                # overwritten field is consumed implicitly: no DropPoint,
                # because Rust's assignment drops it.
                nodes = self._expr(value, _MOVE)
                var_id = self.resolved.assign_of.get(stmt.node_id)
                if var_id is not None and not self._is_copy_var(var_id):
                    # No `use_class` entry: analyze.use_classes() resolves
                    # its keys through resolve.use_of, which holds Var ids
                    # only, so an entry under a statement id is unreachable.
                    nodes.append(Use(var_id, stmt.node_id, stmt.span, _READ))
                return nodes
            case ast.Return(value=value):
                inner = self._expr(value, _MOVE) if value is not None else []
                return [ReturnNode(stmt.span, tuple(inner))]
            case ast.Break():
                return [BreakNode(stmt.span)]
            case ast.Continue():
                return [ContinueNode(stmt.span)]
            case ast.ExprStmt(expr=expr):
                nodes = self._expr(expr, _MOVE)
                ty = self.inferred.types.get(expr.node_id, ERROR_TYPE)
                if not is_copy(ty):
                    # Non-tail statement discarding a non-copy temporary.
                    nodes.append(TempMark(stmt.span))
                return nodes
            case _:  # ErrorStmt — unreachable behind the parse gate
                return []

    # ---------------------------------------------------------- expressions

    def _expr(self, expr: ast.Expr, ctx: str) -> list[Node]:
        match expr:
            case ast.Var():
                return self._var(expr, ctx)
            case ast.Lit() | ast.ErrorExpr():
                return []
            case ast.Call():
                return self._call(expr)
            case ast.BinOp(lhs=lhs, rhs=rhs):
                return self._expr(lhs, _READ) + self._expr(rhs, _READ)
            case ast.UnOp(operand=operand):
                return self._expr(operand, _READ)
            case ast.FieldAccess(obj=obj):
                # Section 36 (supersedes OX0405): the base stays a READ and
                # a non-copy field value is an implicit clone — a fresh
                # owned temporary needing no event of its own.
                return self._expr(obj, _READ)
            case ast.Try(operand=operand):
                # Section 36: the '?' operand is a MOVE use; the implicit
                # early-return path's cleanup is delegated to Rust
                # semantics (no DropPoints), like match unbound payloads.
                return self._expr(operand, _MOVE)
            case ast.StructLit(fields=fields, rest=rest):
                nodes = []
                for _fname, fexpr in fields:
                    nodes.extend(self._expr(fexpr, _MOVE))
                if rest is not None:
                    # Functional update (section 36): rest is a MOVE use.
                    nodes.extend(self._expr(rest, _MOVE))
                return nodes
            case ast.If():
                return [self._if(expr, ctx)]
            case ast.While(cond=cond, body=body):
                return [
                    WhileNode(
                        self._fresh_key(),
                        expr.span,
                        tuple(self._expr(cond, _READ)),
                        self._block(body, _MOVE),
                    )
                ]
            case ast.For():
                return [self._for(expr)]
            case ast.Match():
                return [self._match(expr, ctx)]
            case _:
                return []

    def _for(self, expr: ast.For) -> ForNode:
        # Section 28: the iterable expression is a READ use; the loop var
        # is a fresh owned clone per iteration, scoped to the body. An
        # if/match-expression iterable's arm tails are the section-17
        # value chain — MOVE, exactly as in the call-argument case: the
        # target moves each arm value into the iterated temporary.
        iter_ctx = _MOVE if isinstance(expr.iterable, (ast.If, ast.Match)) else _READ
        iter_nodes = tuple(self._expr(expr.iterable, iter_ctx))
        bound = self.resolved.binds_of.get(expr.node_id, ())
        var_id: int | None = None
        if bound and not self._is_copy_var(bound[0]):
            var_id = bound[0]
        # A directly iterated variable stays borrowed for the loop's whole
        # duration in the emitted Rust (``v.iter().cloned()``).
        borrowed: int | None = None
        if isinstance(expr.iterable, ast.Var):
            iter_var = self.resolved.use_of.get(expr.iterable.node_id)
            if iter_var is not None and not self._is_copy_var(iter_var):
                borrowed = iter_var
        return ForNode(
            self._fresh_key(),
            expr.span,
            iter_nodes,
            var_id,
            self._block(expr.body, _MOVE),
            borrowed,
        )

    def _match(self, expr: ast.Match, ctx: str) -> MatchNode:
        # Section 28: the scrutinee is a MOVE use.
        scrut = tuple(self._expr(expr.scrutinee, _MOVE))
        arms: list[ArmNode] = []
        for arm in expr.arms:
            binders = tuple(
                var_id
                for var_id in self.resolved.binds_of.get(arm.pattern.node_id, ())
                if not self._is_copy_var(var_id)
            )
            if isinstance(arm.body, ast.Block):
                blk = self._block(arm.body, ctx)
            else:
                nodes = self._expr(arm.body, ctx)
                blk = BlockNode(
                    arm.body.span, (self._entry(arm.body.span, nodes),), ()
                )
            arms.append(ArmNode(binders, blk))
        return MatchNode(self._fresh_key(), expr.span, scrut, tuple(arms))

    def _var(self, expr: ast.Var, ctx: str) -> list[Node]:
        var_id = self.resolved.use_of.get(expr.node_id)
        if var_id is None:
            return []  # global fn as value: resolve already rejected it
        if self._is_copy_var(var_id):
            self.use_class[expr.node_id] = _COPY
            return []
        self.use_class[expr.node_id] = ctx
        return [Use(var_id, expr.node_id, expr.span, ctx)]

    def _call(self, call: ast.Call) -> list[Node]:
        if call.node_id in self.resolved.variant_refs:
            # Variant constructor: payload values are moved into the enum
            # value, like struct-literal fields (sections 17, 28).
            nodes: list[Node] = []
            for arg in call.args:
                nodes.extend(self._expr(arg, _MOVE))
            return nodes
        nodes = []
        name = self.resolved.callee_of.get(call.node_id)
        if name is None:
            # Local callee (an error the infer gate already reported).
            nodes.extend(self._expr(call.callee, _READ))
        param_modes = self.modes.get(name, ()) if name is not None else ()
        for index, arg in enumerate(call.args):
            own = index < len(param_modes) and param_modes[index] == "own"
            # Section 17: an if-expression's block tails feeding an
            # argument are the if-expr value chain — MOVE regardless of
            # the param's mode (the target moves the arm value into the
            # argument temporary even behind a reference); a match's arm
            # values feed the argument the same way (section 28).
            ctx = _MOVE if own or isinstance(arg, (ast.If, ast.Match)) else _READ
            nodes.extend(self._expr(arg, ctx))
        return nodes

    def _if(self, expr: ast.If, ctx: str) -> IfNode:
        cond = tuple(self._expr(expr.cond, _READ))
        then_blk = self._block(expr.then_blk, ctx)
        else_blk: BlockNode | None = None
        match expr.else_blk:
            case ast.Block() as blk:
                else_blk = self._block(blk, ctx)
            case ast.If() as chained:
                inner: list[Node] = [self._if(chained, ctx)]
                else_blk = BlockNode(
                    chained.span,
                    (self._entry(chained.span, inner),),
                    (),
                )
        return IfNode(self._fresh_key(), expr.span, cond, then_blk, else_blk)
