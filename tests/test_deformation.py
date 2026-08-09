"""The §56 deformation signature, pinned (SPEC §56, g2 design).

An EXPRESSION-STATEMENT `f.x == e` is a DISCARDED comparison: no model
writes one deliberately, so it is the grammar deforming an intended field
assignment. A TAIL-position one may be a legitimate Bool return, so the two
are counted separately and never pooled.
"""

from eval.deformation import field_assign_deformations


def test_discarded_field_comparison_is_the_signature():
    """A DISCARDED comparison: the trailing statement is what makes it
    discarded. Without it, tail conversion would make this the block's
    value, which is the ambiguous case the tail column exists for."""
    src = "fn main() {\n    a.values == 5\n    print(1)\n}"
    assert field_assign_deformations(src) == (1, 0)


def test_tail_position_is_counted_separately_not_pooled():
    """`c.r == c.g` as a Bool return is CORRECT code, not deformation."""
    src = "fn is_zero(c: P) -> Bool {\n    c.r == c.g\n}"
    assert field_assign_deformations(src) == (0, 1)


def test_a_lone_comparison_is_tail_not_statement_position():
    """Tail conversion is syntactic and unconditional, so a comparison
    alone in a block is the block's VALUE, not a discarded statement.
    Counted in the tail column, never in the signature."""
    src = "fn main() {\n    a.values == 5\n}"
    assert field_assign_deformations(src) == (0, 1)


def test_a_condition_is_not_a_signature():
    src = "fn main() {\n    if p.x == 5 {\n        print(1)\n    }\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_non_field_comparison_is_not_a_signature():
    src = "fn main() {\n    x == 5\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_real_field_assignment_is_not_a_signature():
    """Post-§56 the form parses as an assignment, so the count is 0 -- this
    is the endpoint: 18 -> 0."""
    src = "fn main() {\n    a.values = 5\n}"
    assert field_assign_deformations(src) == (0, 0)


def test_unparseable_source_does_not_raise():
    assert field_assign_deformations("&&& not a program") == (0, 0)
