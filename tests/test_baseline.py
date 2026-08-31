"""
Tests for baseline/baseline.py — smoke tests verifying the baseline
runs and produces the expected output structure.

Also verifies the key design choice: the baseline deliberately generates
compliance violations on dispute/fraud cases, so the agent's "zero
violations" metric is a genuine comparison.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime, timedelta

from baseline.baseline import (
    BaselineBatchReport,
    BaselineResult,
    run_baseline_batch,
    _run_baseline_payment,
    _run_baseline_b2b,
)
from core import config
from core.schemas import (
    B2BReceivableCase,
    CaseStatus,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
)


def _make_payment(**overrides) -> FailedPaymentCase:
    defaults = dict(
        case_id="PAY-BL-001",
        amount=5000.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 20, 10, 0),
        attempt_count=1,
        customer_id="CUST-001",
        customer_history=CustomerHistory(has_history=True, reliability_ratio=0.8,
                                          total_transactions=10, total_amount=50000),
    )
    defaults.update(overrides)
    return FailedPaymentCase(**defaults)


def _make_b2b(**overrides) -> B2BReceivableCase:
    now = getattr(config, "SIMULATED_CURRENT_TIME", datetime(2026, 8, 24, 12, 0, 0))
    defaults = dict(
        case_id="B2B-BL-001",
        invoice_id="INV-BL-001",
        amount=50000.0,
        invoice_date=now - timedelta(days=60),
        due_date=now - timedelta(days=15),  # 15 days overdue
        debtor_id="DBT-001",
        debtor_history=CustomerHistory(has_history=True, reliability_ratio=0.7,
                                        total_transactions=20, total_amount=500000),
    )
    defaults.update(overrides)
    return B2BReceivableCase(**defaults)


class TestBaselinePayment(unittest.TestCase):

    def test_produces_result(self):
        case = _make_payment()
        result = _run_baseline_payment(case)
        self.assertIsInstance(result, BaselineResult)
        self.assertEqual(result.case_type, "FAILED_PAYMENT")
        self.assertEqual(result.resolution_unit, "hours")
        self.assertGreater(result.attempts_made, 0)

    def test_fraud_case_generates_violation(self):
        """Baseline does NOT hard-stop on fraud — should generate a violation."""
        case = _make_payment(fraud_flag=True, failure_code=FailureCode.FRAUD_REJECTION)
        result = _run_baseline_payment(case)
        self.assertGreater(len(result.compliance_violations), 0)
        self.assertIn("VIOLATION", result.compliance_violations[0])

    def test_fraud_case_never_recovers(self):
        """Fraud rejection has 0% success probability."""
        case = _make_payment(fraud_flag=True, failure_code=FailureCode.FRAUD_REJECTION)
        result = _run_baseline_payment(case)
        self.assertFalse(result.recovered)
        self.assertEqual(result.amount_recovered, 0.0)


class TestBaselineB2B(unittest.TestCase):

    def test_produces_result(self):
        case = _make_b2b()
        result = _run_baseline_b2b(case)
        self.assertIsInstance(result, BaselineResult)
        self.assertEqual(result.case_type, "B2B_RECEIVABLE")
        self.assertEqual(result.resolution_unit, "days")

    def test_disputed_case_generates_violation(self):
        """Baseline ignores dispute flags — should generate a violation."""
        case = _make_b2b(dispute_flag=True)
        result = _run_baseline_b2b(case)
        self.assertGreater(len(result.compliance_violations), 0)
        self.assertIn("dispute", result.compliance_violations[0].lower())

    def test_early_overdue_no_reminders(self):
        """If only 3 days overdue, no reminders should fire (first at day 7)."""
        now = getattr(config, "SIMULATED_CURRENT_TIME", datetime(2026, 8, 24, 12, 0, 0))
        case = _make_b2b(due_date=now - timedelta(days=3))
        result = _run_baseline_b2b(case)
        self.assertEqual(result.attempts_made, 0)
        self.assertFalse(result.recovered)


class TestBatchReport(unittest.TestCase):

    def test_batch_report_structure(self):
        cases = [_make_payment(), _make_payment(case_id="PAY-BL-002")]
        report = run_baseline_batch(cases, "Test Batch")
        self.assertIsInstance(report, BaselineBatchReport)
        self.assertEqual(report.total_cases, 2)
        self.assertGreater(report.total_amount_at_risk, 0)
        self.assertEqual(report.cases_hard_stopped, 0)  # Baseline never stops

    def test_batch_with_violations(self):
        cases = [
            _make_payment(fraud_flag=True, failure_code=FailureCode.FRAUD_REJECTION),
            _make_b2b(dispute_flag=True),
        ]
        report = run_baseline_batch(cases, "Violation Test")
        self.assertGreater(report.total_compliance_violations, 0)

    def test_recovery_rate_calculation(self):
        """Recovery rate should be between 0 and 100."""
        cases = [_make_payment() for _ in range(10)]
        report = run_baseline_batch(cases, "Rate Test")
        self.assertGreaterEqual(report.recovery_rate_pct, 0)
        self.assertLessEqual(report.recovery_rate_pct, 100)


if __name__ == "__main__":
    unittest.main()
