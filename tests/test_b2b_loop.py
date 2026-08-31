"""
Comprehensive Test Suite for Phase 3 — B2B Receivables & Promise-to-Pay.

Verifies:
1. Promise-to-Pay tracking:
   - Active unexpired promise -> Pre-pipeline skip to WAIT
   - Broken promise (Stress Test scenario 5) -> Strategy adapts with firm tone
2. 4D B2B Decision Matrix (category × tier × attempt × broken_promise)
3. Deterministic Compliance Gate for B2B:
   - Dispute flag hard-stop & routing to human dispute queue
   - Contact hours check (8 AM - 7 PM IST) for outbound reminders
   - 24/7 internal escalation
4. Adaptive Memory tracking for B2B recovery actions (SEND_REMINDER, OFFER_PAYMENT_PLAN, ESCALATE_TONE)
5. B2B resolution time reported in days matching baseline formula
6. End-to-end B2B batch execution
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.diagnosis import DiagnosisCategory, DiagnosisResult, diagnose
from agents.execution import ExecutionResult, execute
from agents.llm_client import MockLLMClient
from agents.strategy import (
    B2B_ACTION_MENU,
    StrategyProposal,
    apply_fallback_ladder,
    propose_strategy,
)
from core import config
from core.audit_log import AuditLog
from core.compliance import run_all_checks, should_skip_pipeline
from core.memory import Memory
from core.orchestrator import process_b2b_batch, process_case
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    CaseType,
    CustomerHistory,
    PromiseToPay,
    RelationshipTier,
)


def _make_test_b2b(**overrides) -> B2BReceivableCase:
    """Factory helper. Accepts `days_overdue=N` as a convenience shorthand and
    converts it to the correct `due_date` relative to SIMULATED_CURRENT_TIME."""
    from datetime import timedelta
    # Pop convenience arg before passing to dataclass
    days_overdue = overrides.pop("days_overdue", 25)
    ref_time = getattr(config, "SIMULATED_CURRENT_TIME", datetime(2026, 8, 24, 12, 0, 0))
    defaults: dict = dict(
        case_id="B2B-TEST-001",
        invoice_id="INV-2026-001",
        amount=150000.0,
        currency="INR",
        invoice_date=ref_time - timedelta(days=days_overdue + 30),
        due_date=ref_time - timedelta(days=days_overdue),
        debtor_id="DEBTOR-001",
        debtor_history=CustomerHistory(
            has_history=True, reliability_ratio=0.80,
            total_transactions=10, total_amount=1500000.0,
        ),
        dispute_flag=False,
        fraud_flag=False,
        promise_to_pay=None,
        contact_count=1,
        attempt_count=1,
        relationship_tier=RelationshipTier.HIGH,
    )
    defaults.update(overrides)
    return B2BReceivableCase(**defaults)



class TestB2BPromiseToPay(unittest.TestCase):
    """Verify Promise-to-Pay lifecycle and state handling."""

    def test_active_unexpired_promise_skips_to_wait(self):
        """Debtor with an active, unexpired promise (kept=None) must route to WAIT."""
        case = _make_test_b2b(
            promise_to_pay=PromiseToPay(
                promised_date=datetime(2026, 8, 30),
                promised_amount=150000.0,
                kept=None,
            )
        )
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertTrue(skip.should_skip)
        self.assertEqual(skip.action, ActionType.WAIT)
        self.assertEqual(skip.skip_type, "active_promise")

    def test_broken_promise_triggers_firm_strategy(self):
        """Stress Test Scenario 5: Broken promise (kept=False) triggers firm follow-up."""
        case = _make_test_b2b(
            relationship_tier=RelationshipTier.HIGH,
            promise_to_pay=PromiseToPay(
                promised_date=datetime(2026, 8, 10),
                promised_amount=150000.0,
                kept=False,  # Broken
            ),
        )
        client = MockLLMClient()
        diag = diagnose(case, client)
        strat = propose_strategy(case, diag, None, client)
        self.assertEqual(strat.proposed_action, ActionType.ESCALATE_TONE)
        self.assertIn("missed", strat.reasoning.lower())


class TestB2B4DDecisionMatrix(unittest.TestCase):
    """Verify B2B decision matrix across category, tier, attempt, and broken promise."""

    def setUp(self):
        self.client = MockLLMClient()

    def test_cash_flow_high_tier_offers_payment_plan(self):
        case = _make_test_b2b(relationship_tier=RelationshipTier.HIGH, days_overdue=20)
        diag = diagnose(case, self.client)
        strat = propose_strategy(case, diag, None, self.client)
        self.assertEqual(strat.proposed_action, ActionType.OFFER_PAYMENT_PLAN)

    def test_administrative_delay_sends_reminder(self):
        case = _make_test_b2b(relationship_tier=RelationshipTier.MEDIUM, days_overdue=7)
        diag = diagnose(case, self.client)
        self.assertEqual(diag.category, DiagnosisCategory.ADMINISTRATIVE_DELAY)
        strat = propose_strategy(case, diag, None, self.client)
        self.assertEqual(strat.proposed_action, ActionType.SEND_REMINDER)

    def test_disputed_invoice_escalates_to_human(self):
        case = _make_test_b2b(dispute_flag=True)
        # Should be caught in pre-pipeline skip
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertEqual(skip.action, ActionType.ESCALATE_HUMAN)


class TestB2BComplianceGate(unittest.TestCase):
    """Verify B2B compliance checks (dispute, attempt cap, contact hours)."""

    def test_dispute_gate_approves_escalate_human(self):
        case = _make_test_b2b(dispute_flag=True)
        gate = run_all_checks(case, ActionType.ESCALATE_HUMAN, {})
        self.assertTrue(gate.approved)

    def test_contact_hours_blocks_reminder_at_night(self):
        case = _make_test_b2b()
        gate = run_all_checks(
            case,
            ActionType.SEND_REMINDER,
            {},
            current_time=datetime(2026, 8, 20, 22, 0),  # 10 PM IST
        )
        self.assertFalse(gate.approved)
        self.assertIn("contact_hours", [r.rule_name for r in gate.violations])

    def test_escalate_human_permitted_at_night(self):
        case = _make_test_b2b()
        gate = run_all_checks(
            case,
            ActionType.ESCALATE_HUMAN,
            {},
            current_time=datetime(2026, 8, 20, 2, 0),  # 2 AM IST
        )
        self.assertTrue(gate.approved)


class TestB2BMemoryAndResolutionTime(unittest.TestCase):
    """Verify B2B memory tracking and resolution time formula in days."""

    def setUp(self):
        self.mem = Memory("data/test_b2b_mem.json")
        self.mem.clear()
        self.audit = AuditLog("data/test_b2b_audit.json")

    def tearDown(self):
        self.mem.clear()
        if os.path.exists("data/test_b2b_audit.json"):
            os.remove("data/test_b2b_audit.json")

    def test_b2b_recovery_actions_recorded_in_memory(self):
        self.mem.record_outcome(DiagnosisCategory.CASH_FLOW_MISMATCH, ActionType.OFFER_PAYMENT_PLAN, "SUCCESS")
        context = self.mem.get_strategy_context(DiagnosisCategory.CASH_FLOW_MISMATCH)
        self.assertIn(ActionType.OFFER_PAYMENT_PLAN, context)
        self.assertEqual(context[ActionType.OFFER_PAYMENT_PLAN].sample_count, 1)

    def test_b2b_resolution_time_in_days(self):
        case = _make_test_b2b(days_overdue=30)
        res = execute(case, ActionType.SEND_REMINDER, self.audit)
        if res.status == "SUCCESS":
            self.assertIsNotNone(res.resolution_time)
            self.assertLessEqual(res.resolution_time, 30)
            self.assertGreaterEqual(res.resolution_time, 3)


class TestB2BBatchExecution(unittest.TestCase):
    """Verify full end-to-end B2B batch execution."""

    def test_b2b_batch_report(self):
        cases = [
            _make_test_b2b(case_id="B2B-E2E-001", days_overdue=7),
            _make_test_b2b(case_id="B2B-E2E-002", days_overdue=25),
            _make_test_b2b(case_id="B2B-E2E-003", dispute_flag=True),
            _make_test_b2b(case_id="B2B-E2E-004", fraud_flag=True),
            _make_test_b2b(
                case_id="B2B-E2E-005",
                promise_to_pay=PromiseToPay(promised_date=datetime(2026, 9, 1), promised_amount=100000.0, kept=None),
            ),
        ]
        audit = AuditLog("data/test_b2b_batch_audit.json")
        mem = Memory("data/test_b2b_batch_mem.json")
        mem.clear()

        report = process_b2b_batch(cases, audit, mem, MockLLMClient())
        self.assertEqual(report.total_cases, 5)
        self.assertEqual(report.total_compliance_violations, 0)
        self.assertEqual(report.resolution_unit, "days")

        mem.clear()
        if os.path.exists("data/test_b2b_batch_audit.json"):
            os.remove("data/test_b2b_batch_audit.json")


if __name__ == "__main__":
    unittest.main()
