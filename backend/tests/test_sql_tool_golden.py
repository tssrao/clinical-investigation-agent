"""Phase 1 SQL Tool golden question set - a small, targeted eval, not the full
~25-scenario RAGAS golden set (that's Phase 8). Every expected value here was
computed independently via direct SQL against the real DB (see the session that
built this file), never through sql_tool itself - otherwise a bug in the tool
and a bug in the "ground truth" could agree with each other and both pass.

All questions target one real patient (23d14605-b881-65ba-c09e-0ecf40ebfeff, a
59-year-old female with records in all 13 table categories checked - including
allergies, which only ~17% of patients have any of) chosen specifically because
her data exercises several of join_reference.md's documented traps: claims are
NOT 1:1 with her encounters (92 claims / 67 encounters), her claims_transactions
have the TYPE-conditional AMOUNT/PAYMENTS split, and she has a real
medications.REASONCODE causal chain (Simvastatin -> Hyperlipidemia).

Requires OPENAI_API_KEY - skipped entirely otherwise (see test_sql_tool.py).
"""

from decimal import Decimal

import pytest

from app.core.config import settings
from app.tools.sql_tool import run_sql_tool

pytestmark = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)

PATIENT_ID = "23d14605-b881-65ba-c09e-0ecf40ebfeff"


def _numbers_in(rows: list[dict]) -> set[float]:
    """All numeric values across every cell, so we don't have to guess what
    column name the LLM chose to alias its result as.
    """
    values = set()
    for row in rows:
        for v in row.values():
            if isinstance(v, (int, float, Decimal)):
                values.add(float(v))
    return values


def _assert_contains(result, expected: float, tolerance: float = 0.01):
    assert result.success, f"query failed: {result.error}\nSQL: {result.sql}"
    numbers = _numbers_in(result.rows)
    assert any(abs(n - expected) <= tolerance for n in numbers), (
        f"expected {expected} among {numbers}\nSQL: {result.sql}\nrows: {result.rows}"
    )


GOLDEN_QUESTIONS = [
    ("How many conditions does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have in total?", 48),
    ("How many medications has patient 23d14605-b881-65ba-c09e-0ecf40ebfeff been prescribed?", 25),
    (
        "Of patient 23d14605-b881-65ba-c09e-0ecf40ebfeff's medications, how many have a "
        "documented reason code (REASONCODE)?",
        20,
    ),
    ("How many allergies does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 12),
    ("How many distinct imaging studies does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 4),
    ("How many claims does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have in total?", 92),
    (
        "How many distinct encounters does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff's "
        "claims correspond to?",
        67,
    ),
    ("How many encounters does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have in total?", 67),
    ("How many devices does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 5),
    ("How many careplans does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 4),
    ("How many immunizations does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 15),
    ("How many supply records does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 24),
    ("How many payer transitions does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have?", 57),
    ("How many observations does patient 23d14605-b881-65ba-c09e-0ecf40ebfeff have in total?", 764),
    (
        "What is the total PAYMENT amount recorded in claims_transactions for patient "
        "23d14605-b881-65ba-c09e-0ecf40ebfeff? Only count rows where TYPE is PAYMENT.",
        132458.23,
    ),
]


@pytest.mark.parametrize("question,expected", GOLDEN_QUESTIONS)
def test_golden_question(question, expected):
    result = run_sql_tool(question)
    _assert_contains(result, expected)


def test_causal_chain_medication_reason():
    """medications.REASONCODE causal-chain question - the real data backing the
    anchor 'why did X change' scenario (design doc). Text match, not a count.
    """
    result = run_sql_tool(
        "What condition is patient 23d14605-b881-65ba-c09e-0ecf40ebfeff's "
        "Simvastatin prescription linked to (its REASONDESCRIPTION)?"
    )
    assert result.success, f"query failed: {result.error}\nSQL: {result.sql}"
    all_text = " ".join(str(v) for row in result.rows for v in row.values()).lower()
    assert "hyperlipidemia" in all_text, f"expected 'hyperlipidemia' in answer: {result.rows}"


def test_current_payer():
    """Most recent payer via payer_transitions - a text match, not a count."""
    result = run_sql_tool(
        "Who is patient 23d14605-b881-65ba-c09e-0ecf40ebfeff's current (most recent) "
        "insurance payer?"
    )
    assert result.success, f"query failed: {result.error}\nSQL: {result.sql}"
    all_text = " ".join(str(v) for row in result.rows for v in row.values()).lower()
    assert "medicare" in all_text, f"expected 'medicare' in answer: {result.rows}"


def test_nonexistent_patient_returns_no_rows_not_an_error():
    """Edge case: a syntactically fine question about a patient id that doesn't
    exist should succeed with zero rows, not error out.
    """
    result = run_sql_tool(
        "How many conditions does patient 00000000-0000-0000-0000-000000000000 have?"
    )
    assert result.success, f"query failed: {result.error}\nSQL: {result.sql}"
    numbers = _numbers_in(result.rows)
    assert numbers == {0.0} or result.row_count == 0
