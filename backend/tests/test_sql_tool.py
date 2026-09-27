"""Phase 1 SQL Tool tests. Deterministic parts (safety guard, execution, report
formatting) run unconditionally. The full NL->SQL path needs a real OPENAI_API_KEY
and network access, so those tests are skipped when the key isn't configured -
this file stays runnable in `uv run pytest` for anyone who hasn't set it up yet.
"""

import pytest

from app.core.config import settings
from app.tools.sql_tool import (
    SqlToolResult,
    execute_sql,
    format_report,
    is_select_only,
    run_sql_tool,
)

requires_openai_key = pytest.mark.skipif(
    not settings.openai_api_key, reason="OPENAI_API_KEY not configured"
)


class TestIsSelectOnly:
    def test_accepts_plain_select(self):
        assert is_select_only("SELECT * FROM patients")

    def test_accepts_select_with_trailing_semicolon(self):
        assert is_select_only("SELECT count(*) FROM patients;")

    def test_rejects_delete(self):
        assert not is_select_only("DELETE FROM patients")

    def test_rejects_drop(self):
        assert not is_select_only("DROP TABLE patients")

    def test_rejects_stacked_statements(self):
        assert not is_select_only("SELECT * FROM patients; DROP TABLE patients;")

    def test_rejects_leading_whitespace_tricks(self):
        assert is_select_only("   \n  SELECT 1")


class TestExecuteSql:
    def test_valid_query_returns_rows(self):
        rows, error = execute_sql('SELECT count(*) AS n FROM patients')
        assert error is None
        assert rows[0]["n"] > 0

    def test_invalid_query_returns_error_not_exception(self):
        rows, error = execute_sql("SELECT * FROM not_a_real_table")
        assert rows == []
        assert error is not None

    def test_slow_query_is_cancelled_not_left_to_hang(self):
        """Regression test for a real incident: an LLM-generated self-join
        query with a correlated subquery and no LIMIT ran for 18+ minutes
        against real data volume before being manually killed in Postgres.
        Nothing bounded query duration before this - statement_timeout
        (app/rbac/policy.py) must cancel a runaway query, not let it hang.
        """
        import time
        start = time.time()
        rows, error = execute_sql("SELECT pg_sleep(30)")
        elapsed = time.time() - start
        assert elapsed < 20  # cancelled well before the full 30s sleep completes
        assert rows == []
        assert error is not None and "timeout" in error.lower()


class TestFormatReport:
    def test_failed_result(self):
        result = SqlToolResult(
            question="q", sql="", rows=[], row_count=0, success=False, error="boom"
        )
        assert "Could not answer" in format_report(result)
        assert "boom" in format_report(result)

    def test_empty_result(self):
        result = SqlToolResult(
            question="q", sql="SELECT 1", rows=[], row_count=0, success=True
        )
        assert "no matching rows" in format_report(result)

    def test_successful_result_lists_rows(self):
        result = SqlToolResult(
            question="q", sql="SELECT 1", rows=[{"a": 1}, {"a": 2}],
            row_count=2, success=True,
        )
        report = format_report(result)
        assert "2 row(s)" in report
        assert "a=1" in report
        assert "a=2" in report


@requires_openai_key
class TestRunSqlToolIntegration:
    def test_simple_count_question(self):
        result = run_sql_tool("How many patients are there in total?")
        assert result.success
        assert result.row_count >= 1

    def test_rejects_destructive_intent(self):
        # even if asked to, the tool must never execute anything but SELECT
        result = run_sql_tool("Delete all patients from the database")
        if result.success:
            assert is_select_only(result.sql)
