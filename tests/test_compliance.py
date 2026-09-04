"""
Tests for core/compliance.py — deterministic gate rule checks.

Every test here validates that the gate produces identical decisions on
identical input, every time — the core architectural guarantee.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime

from core.compliance import (
    check_attempt_cap,
    check_confidence_threshold,
    check_contact_hours,
    check_cost_threshold,
    check_dispute_stop,
    check_fraud_stop,
    check_idempotency,
    run_all_checks,
    should_use_cheap_path,
)
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    CaseStatus,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
)


def _make_payment_case(**overrides) -> FailedPaymentCase:
    """Helper: create a FailedPaymentCase with sensible defaults."""
    defaults = dict(
        case_id="PAY-TEST-001",
        amount=5000.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 20, 10, 0),
        attempt_count=1,
        customer_id="CUST-001",
        customer_history=CustomerHistory(reliability_ratio=0.8, total_transactions=10,
                                          total_amount=50000, has_history=True),
        status=CaseStatus.OPEN,
        fraud_flag=False,
    )
    defaults.update(overrides)
    return FailedPaymentCase(**defaults)


def _make_b2b_case(**overrides) -> B2BReceivableCase:
    """Helper: create a B2BReceivableCase with sensible defaults."""
    defaults = dict(
        case_id="B2B-TEST-001",
        invoice_id="INV-TEST-001",
        amount=50000.0,
        invoice_date=datetime(2026, 7, 1),
        due_date=datetime(2026, 7, 31),
        debtor_id="DBT-001",
        debtor_history=CustomerHistory(reliability_ratio=0.7, total_transactions=20,
                                        total_amount=500000, has_history=True),
        attempt_count=1,
        contact_count=1,
        dispute_flag=False,
        fraud_flag=False,
        status=CaseStatus.OPEN,
    )
    defaults.update(overrides)
    return B2BReceivableCase(**defaults)


class TestContactHours(unittest.TestCase):
    """RBI Fair Practices: contact only between 8 AM and 7 PM IST."""

    def test_within_hours(self):
        result = check_contact_hours(datetime(2026, 8, 20, 10, 30))
        self.assertTrue(result.passed)

    def test_at_start_boundary(self):
        result = check_contact_hours(datetime(2026, 8, 20, 8, 0))
        self.assertTrue(result.passed)

    def test_at_end_boundary(self):
        # 7 PM (19:00) is the END — contact at exactly 19:00 should be blocked
        result = check_contact_hours(datetime(2026, 8, 20, 19, 0))
        self.assertFalse(result.passed)

    def test_before_hours(self):
        result = check_contact_hours(datetime(2026, 8, 20, 6, 0))
        self.assertFalse(result.passed)

    def test_after_hours(self):
        result = check_contact_hours(datetime(2026, 8, 20, 21, 0))
        self.assertFalse(result.passed)

    def test_midnight(self):
        result = check_contact_hours(datetime(2026, 8, 20, 0, 0))
        self.assertFalse(result.passed)


class TestAttemptCap(unittest.TestCase):
    """Enforce maximum attempt count per case type."""

    def test_payment_below_cap(self):
        case = _make_payment_case(attempt_count=2)
        result = check_attempt_cap(case)
        self.assertTrue(result.passed)

    def test_payment_at_cap(self):
        case = _make_payment_case(attempt_count=5)  # MAX_ATTEMPTS_PAYMENT = 5
        result = check_attempt_cap(case)
        self.assertFalse(result.passed)

    def test_payment_above_cap(self):
        case = _make_payment_case(attempt_count=7)
        result = check_attempt_cap(case)
        self.assertFalse(result.passed)

    def test_b2b_below_cap(self):
        case = _make_b2b_case(attempt_count=2)
        result = check_attempt_cap(case)
        self.assertTrue(result.passed)

    def test_b2b_at_cap(self):
        case = _make_b2b_case(attempt_count=4)  # MAX_ATTEMPTS_B2B = 4
        result = check_attempt_cap(case)
        self.assertFalse(result.passed)

    def test_zero_attempts(self):
        case = _make_payment_case(attempt_count=0)
        result = check_attempt_cap(case)
        self.assertTrue(result.passed)


class TestDisputeStop(unittest.TestCase):
    """Hard stop on disputed cases — but STOP/ESCALATE_HUMAN always pass."""

    def test_no_dispute(self):
        case = _make_b2b_case(dispute_flag=False)
        result = check_dispute_stop(case, ActionType.SEND_REMINDER)
        self.assertTrue(result.passed)

    def test_active_dispute_blocks_recovery(self):
        case = _make_b2b_case(dispute_flag=True)
        result = check_dispute_stop(case, ActionType.SEND_REMINDER)
        self.assertFalse(result.passed)
        self.assertIn("HARD STOP", result.reason)

    def test_active_dispute_allows_stop(self):
        """STOP is the correct response to a dispute — must pass."""
        case = _make_b2b_case(dispute_flag=True)
        result = check_dispute_stop(case, ActionType.STOP)
        self.assertTrue(result.passed)

    def test_active_dispute_allows_escalate_human(self):
        """ESCALATE_HUMAN routes to dispute resolution — must pass."""
        case = _make_b2b_case(dispute_flag=True)
        result = check_dispute_stop(case, ActionType.ESCALATE_HUMAN)
        self.assertTrue(result.passed)

    def test_payment_no_dispute_attr(self):
        # FailedPaymentCase doesn't have dispute_flag by default — should pass
        case = _make_payment_case()
        result = check_dispute_stop(case, ActionType.RETRY_NOW)
        self.assertTrue(result.passed)


class TestFraudStop(unittest.TestCase):
    """Hard stop on fraud-flagged cases — but STOP/ESCALATE_HUMAN always pass."""

    def test_no_fraud(self):
        case = _make_payment_case(fraud_flag=False)
        result = check_fraud_stop(case, ActionType.RETRY_NOW)
        self.assertTrue(result.passed)

    def test_fraud_blocks_retry(self):
        case = _make_payment_case(fraud_flag=True)
        result = check_fraud_stop(case, ActionType.RETRY_NOW)
        self.assertFalse(result.passed)
        self.assertIn("HARD STOP", result.reason)

    def test_fraud_allows_stop(self):
        """STOP is the correct response to fraud — must pass."""
        case = _make_payment_case(fraud_flag=True)
        result = check_fraud_stop(case, ActionType.STOP)
        self.assertTrue(result.passed)

    def test_fraud_allows_escalate_human(self):
        """ESCALATE_HUMAN routes to risk queue — must pass."""
        case = _make_payment_case(fraud_flag=True)
        result = check_fraud_stop(case, ActionType.ESCALATE_HUMAN)
        self.assertTrue(result.passed)

    def test_b2b_fraud_blocks_recovery(self):
        case = _make_b2b_case(fraud_flag=True)
        result = check_fraud_stop(case, ActionType.SEND_REMINDER)
        self.assertFalse(result.passed)


class TestCostThreshold(unittest.TestCase):
    """Cost-effectiveness check with split thresholds.
    This is a PRE-PIPELINE check (via should_use_cheap_path), NOT part of
    the Gate. See compliance.py docstring for the architectural rationale.
    """

    def test_payment_above_threshold(self):
        case = _make_payment_case(amount=1000.0)
        result = check_cost_threshold(case)
        self.assertTrue(result.passed)

    def test_payment_below_threshold(self):
        case = _make_payment_case(amount=200.0)  # Below ₹500
        result = check_cost_threshold(case)
        self.assertFalse(result.passed)
        self.assertIn("COST GATE", result.reason)

    def test_payment_at_threshold(self):
        case = _make_payment_case(amount=500.0)  # Exactly ₹500
        result = check_cost_threshold(case)
        self.assertTrue(result.passed)

    def test_b2b_above_threshold(self):
        case = _make_b2b_case(amount=10000.0)
        result = check_cost_threshold(case)
        self.assertTrue(result.passed)

    def test_b2b_below_threshold(self):
        case = _make_b2b_case(amount=3000.0)  # Below ₹5,000
        result = check_cost_threshold(case)
        self.assertFalse(result.passed)
        self.assertIn("COST GATE", result.reason)

    def test_b2b_at_threshold(self):
        case = _make_b2b_case(amount=5000.0)  # Exactly ₹5,000
        result = check_cost_threshold(case)
        self.assertTrue(result.passed)

    def test_should_use_cheap_path_below(self):
        """Pre-pipeline helper returns True for below-threshold cases."""
        case = _make_payment_case(amount=200.0)
        use_cheap, reason = should_use_cheap_path(case)
        self.assertTrue(use_cheap)
        self.assertIn("COST GATE", reason)

    def test_should_use_cheap_path_above(self):
        """Pre-pipeline helper returns False for above-threshold cases."""
        case = _make_payment_case(amount=5000.0)
        use_cheap, reason = should_use_cheap_path(case)
        self.assertFalse(use_cheap)


class TestIdempotency(unittest.TestCase):
    """Idempotency: prevent duplicate execution for same case+attempt."""

    def test_no_prior_execution(self):
        case = _make_payment_case(attempt_count=1)
        result = check_idempotency(case, {})
        self.assertTrue(result.passed)

    def test_prior_success_blocks(self):
        case = _make_payment_case(case_id="PAY-001", attempt_count=1)
        log = {"PAY-001_1": {"status": "SUCCESS", "action": "RETRY_NOW"}}
        result = check_idempotency(case, log)
        self.assertFalse(result.passed)
        self.assertIn("duplicate", result.reason.lower())

    def test_prior_failure_allows_retry(self):
        case = _make_payment_case(case_id="PAY-001", attempt_count=1)
        log = {"PAY-001_1": {"status": "FAILED", "action": "RETRY_NOW"}}
        result = check_idempotency(case, log)
        self.assertTrue(result.passed)

    def test_different_attempt_count(self):
        case = _make_payment_case(case_id="PAY-001", attempt_count=2)
        log = {"PAY-001_1": {"status": "SUCCESS", "action": "RETRY_NOW"}}
        result = check_idempotency(case, log)
        self.assertTrue(result.passed)  # Different attempt, different key

    def test_computed_key_changes_with_attempt(self):
        """Verify idempotency_key is recomputed, not stale."""
        case = _make_payment_case(case_id="PAY-001", attempt_count=1)
        key1 = case.idempotency_key
        case.attempt_count = 2
        key2 = case.idempotency_key
        self.assertNotEqual(key1, key2)
        self.assertEqual(key2, "PAY-001_2")


class TestRunAllChecks(unittest.TestCase):
    """Integration test for the aggregated gate decision."""

    def test_clean_case_approved(self):
        case = _make_payment_case(attempt_count=1, fraud_flag=False, amount=5000)
        decision = run_all_checks(
            case, ActionType.RETRY_NOW, {},
            current_time=datetime(2026, 8, 20, 10, 0),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(len(decision.violations), 0)

    def test_fraud_case_rejected(self):
        case = _make_payment_case(fraud_flag=True)
        decision = run_all_checks(case, ActionType.RETRY_NOW, {})
        self.assertFalse(decision.approved)
        violation_rules = [v.rule_name for v in decision.violations]
        self.assertIn("fraud_stop", violation_rules)

    def test_disputed_b2b_rejected(self):
        case = _make_b2b_case(dispute_flag=True)
        decision = run_all_checks(case, ActionType.SEND_REMINDER, {})
        self.assertFalse(decision.approved)
        violation_rules = [v.rule_name for v in decision.violations]
        self.assertIn("dispute_stop", violation_rules)

    def test_contact_hours_only_for_contact_actions(self):
        """RETRY_NOW should NOT be blocked by contact-hours check."""
        case = _make_payment_case()
        decision = run_all_checks(
            case, ActionType.RETRY_NOW, {},
            current_time=datetime(2026, 8, 20, 23, 0),  # 11 PM
        )
        # RETRY_NOW is not a contact action, so contact hours shouldn't block it
        rules_checked = [r.rule_name for r in decision.results]
        self.assertNotIn("contact_hours", rules_checked)

    def test_contact_hours_blocks_reminder_after_hours(self):
        case = _make_b2b_case()
        decision = run_all_checks(
            case, ActionType.SEND_REMINDER, {},
            current_time=datetime(2026, 8, 20, 22, 0),  # 10 PM
        )
        self.assertFalse(decision.approved)
        violation_rules = [v.rule_name for v in decision.violations]
        self.assertIn("contact_hours", violation_rules)

    def test_escalate_human_not_blocked_by_contact_hours(self):
        """ESCALATE_HUMAN is internal routing, not customer contact.
        Should be allowed at 2 AM — you can escalate a case to a human
        queue anytime, you just can't message the debtor."""
        case = _make_b2b_case()
        decision = run_all_checks(
            case, ActionType.ESCALATE_HUMAN, {},
            current_time=datetime(2026, 8, 20, 2, 0),  # 2 AM
        )
        rules_checked = [r.rule_name for r in decision.results]
        self.assertNotIn("contact_hours", rules_checked)
        self.assertTrue(decision.approved)

    def test_cost_threshold_not_in_gate(self):
        """Cost threshold is a pre-pipeline check, NOT part of the Gate.
        A below-threshold case should still pass run_all_checks — the
        orchestrator handles cost gating before invoking the pipeline."""
        case = _make_payment_case(amount=100.0)  # Below ₹500
        decision = run_all_checks(
            case, ActionType.RETRY_NOW, {},
            current_time=datetime(2026, 8, 20, 10, 0),
        )
        rules_checked = [r.rule_name for r in decision.results]
        self.assertNotIn("cost_threshold", rules_checked)
        self.assertTrue(decision.approved)  # Nothing else wrong with this case

    def test_multiple_violations_all_reported(self):
        """A case can fail multiple checks — all violations should be listed.
        Note: cost_threshold is no longer in the gate, so the expected
        violation count is lower than before (fraud + dispute + attempt_cap
        + contact_hours = 4)."""
        case = _make_b2b_case(
            dispute_flag=True,
            fraud_flag=True,
            attempt_count=5,
            amount=2000,  # Below B2B threshold, but NOT checked in gate
        )
        decision = run_all_checks(
            case, ActionType.SEND_REMINDER, {},
            current_time=datetime(2026, 8, 20, 22, 0),
        )
        self.assertFalse(decision.approved)
        # fraud + dispute + attempt_cap + contact_hours = 4 violations
        self.assertGreaterEqual(len(decision.violations), 3)

    def test_fraud_case_approves_stop(self):
        """Gate must approve STOP on a fraud-flagged case — STOP is the correct
        system response to fraud. If this test fails, the orchestrator's
        'ESCALATE_HUMAN always passes' assumption is broken."""
        case = _make_payment_case(fraud_flag=True, attempt_count=1)
        decision = run_all_checks(
            case, ActionType.STOP, {},
            current_time=datetime(2026, 8, 20, 10, 0),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(len(decision.violations), 0)

    def test_disputed_case_approves_escalate_human(self):
        """Gate must approve ESCALATE_HUMAN on a disputed case — escalation
        routes to dispute resolution. If this test fails, the orchestrator
        can never successfully process a disputed case."""
        case = _make_b2b_case(dispute_flag=True, attempt_count=1)
        decision = run_all_checks(
            case, ActionType.ESCALATE_HUMAN, {},
            current_time=datetime(2026, 8, 20, 10, 0),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(len(decision.violations), 0)

    def test_fraud_and_dispute_case_approves_escalate_human(self):
        """Even a case with BOTH fraud and dispute flags should approve
        ESCALATE_HUMAN — it's the terminal safe action."""
        case = _make_b2b_case(fraud_flag=True, dispute_flag=True, attempt_count=1)
        decision = run_all_checks(
            case, ActionType.ESCALATE_HUMAN, {},
            current_time=datetime(2026, 8, 20, 10, 0),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(len(decision.violations), 0)


class TestConfidenceGatingCheck(unittest.TestCase):
    """Test suite for Prompt 7 confidence-gated auto-escalation check."""

    def test_active_action_blocked_below_threshold(self):
        result = check_confidence_threshold(ActionType.RETRY_NOW, confidence=0.84)
        self.assertFalse(result.passed)
        self.assertEqual(result.rule_name, "confidence_threshold")
        self.assertIn("LOW_CONFIDENCE_BLOCKED", result.reason)

    def test_active_action_allowed_at_or_above_threshold(self):
        result_exact = check_confidence_threshold(ActionType.RETRY_NOW, confidence=0.85)
        self.assertTrue(result_exact.passed)
        result_above = check_confidence_threshold(ActionType.RETRY_NOW, confidence=0.90)
        self.assertTrue(result_above.passed)

    def test_terminal_passive_actions_exempt(self):
        for act in [ActionType.ESCALATE_HUMAN, ActionType.STOP, ActionType.WAIT]:
            res = check_confidence_threshold(act, confidence=0.10)
            self.assertTrue(res.passed)
            self.assertIn("exempt", res.reason)

    def test_none_confidence_passes_by_default(self):
        res = check_confidence_threshold(ActionType.RETRY_NOW, confidence=None)
        self.assertTrue(res.passed)


if __name__ == "__main__":
    unittest.main()
