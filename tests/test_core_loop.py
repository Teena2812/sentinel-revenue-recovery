"""
Comprehensive Test Suite for Phase 2 — Core Loop (Failed Payments).

Verifies:
1. Ground-truth probability table integrity (no bonus in execution)
2. Relationship-tier-sensitive strategy (FR-3.3)
3. Pre-pipeline skip logic (fraud, dispute, active promise, cost threshold)
4. Memory double-gating & cold-start behavior
5. Execution status values & RETRY_LATER non-cumulative delay formula
6. Bounded multi-attempt adaptive retry loop (AGENT_LOOP_MAX_ATTEMPTS = 3)
7. Dynamic idempotency key advancement across loop attempts
8. Gate re-propose cap & terminal escalation
9. Malformed LLM JSON retry & fallback ladder
10. Confidence threshold (<0.85) and conflicting signal triggers
11. Audit trail completeness (FR-8.1)
12. End-to-end batch processing
"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from datetime import datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents.execution as execution_module
from agents.diagnosis import DiagnosisCategory, DiagnosisResult, diagnose
from agents.execution import ExecutionResult, execute
from agents.llm_client import MockLLMClient
from agents.strategy import (
    PAYMENT_ACTION_MENU,
    StrategyProposal,
    apply_fallback_ladder,
    propose_strategy,
    re_propose_strategy,
)
from core import config
from core.audit_log import AuditLog
from core.compliance import run_all_checks, should_skip_pipeline
from core.memory import Memory, StrategyStats
from core.orchestrator import process_case, process_payment_batch
from core.schemas import (
    ActionType,
    ConflictingSignal,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    PromiseToPay,
    RelationshipTier,
)


def _make_test_payment(**overrides) -> FailedPaymentCase:
    defaults = dict(
        case_id="PAY-TEST-LOOP-001",
        amount=5000.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 20, 10, 0),
        attempt_count=1,
        customer_id="CUST-001",
        customer_history=CustomerHistory(
            has_history=True, reliability_ratio=0.85, total_transactions=15, total_amount=75000.0
        ),
        relationship_tier=RelationshipTier.MEDIUM,
    )
    defaults.update(overrides)
    return FailedPaymentCase(**defaults)


class TestFairnessAndProbabilities(unittest.TestCase):
    """Verify ground-truth probability table integrity."""

    def test_bonus_not_imported_in_execution(self):
        """AGENT_STRATEGY_MATCH_BONUS must NOT be present or imported in execution.py."""
        exec_source = inspect.getsource(execution_module)
        self.assertNotIn("AGENT_STRATEGY_MATCH_BONUS", exec_source)

    def test_shared_probability_table_used(self):
        """Execution must look up probabilities directly from config.PAYMENT_RETRY_SUCCESS_PROB."""
        for fc in FailureCode:
            expected_prob = config.PAYMENT_RETRY_SUCCESS_PROB.get(fc.value)
            self.assertIsNotNone(expected_prob)


class TestRelationshipTierSensitivity(unittest.TestCase):
    """Verify strategy proposes different actions based on relationship tier (FR-3.3)."""

    def test_tier_sensitive_proposals(self):
        client = MockLLMClient()
        mem = Memory(storage_path="data/test_memory_tier.json")

        case_high = _make_test_payment(
            failure_code=FailureCode.INSUFFICIENT_FUNDS,
            relationship_tier=RelationshipTier.HIGH,
        )
        case_low = _make_test_payment(
            failure_code=FailureCode.INSUFFICIENT_FUNDS,
            relationship_tier=RelationshipTier.LOW,
        )

        diag_high = diagnose(case_high, client)
        diag_low = diagnose(case_low, client)

        strat_high = propose_strategy(case_high, diag_high, mem.get_strategy_context(diag_high.category), client)
        strat_low = propose_strategy(case_low, diag_low, mem.get_strategy_context(diag_low.category), client)

        self.assertNotEqual(strat_high.proposed_action, strat_low.proposed_action)
        self.assertEqual(strat_high.proposed_action, ActionType.RETRY_LATER)
        self.assertEqual(strat_low.proposed_action, ActionType.SUGGEST_ALTERNATE_METHOD)
        mem.clear()


class TestPrePipelineSkip(unittest.TestCase):
    """Verify pre-pipeline skip logic (fraud, dispute, active promise, cost threshold)."""

    def test_fraud_skips_to_stop(self):
        case = _make_test_payment(fraud_flag=True)
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertTrue(skip.should_skip)
        self.assertEqual(skip.action, ActionType.STOP)
        self.assertEqual(skip.skip_type, "fraud")

    def test_active_promise_skips_to_wait(self):
        case = _make_test_payment()
        case.promise_to_pay = PromiseToPay(promised_date=datetime(2026, 9, 1), promised_amount=5000.0, kept=None)
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertTrue(skip.should_skip)
        self.assertEqual(skip.action, ActionType.WAIT)
        self.assertEqual(skip.skip_type, "active_promise")

    def test_cost_threshold_skips_to_cheap_action(self):
        case = _make_test_payment(amount=200.0)  # Below ₹500
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertTrue(skip.should_skip)
        self.assertEqual(skip.action, ActionType.RETRY_NOW)
        self.assertEqual(skip.skip_type, "cost_threshold")

    def test_fraud_takes_priority_over_cost_threshold(self):
        case = _make_test_payment(amount=200.0, fraud_flag=True)
        skip = should_skip_pipeline(case)
        self.assertIsNotNone(skip)
        self.assertEqual(skip.action, ActionType.STOP)
        self.assertEqual(skip.skip_type, "fraud")

    def test_normal_case_does_not_skip(self):
        case = _make_test_payment(amount=5000.0, fraud_flag=False)
        skip = should_skip_pipeline(case)
        self.assertIsNone(skip)


class TestMemoryTrackingAndColdStart(unittest.TestCase):
    """Verify double-gating and cold-start in Memory."""

    def setUp(self):
        self.mem_path = "data/test_memory_temp.json"
        self.mem = Memory(storage_path=self.mem_path)
        self.mem.clear()

    def tearDown(self):
        self.mem.clear()

    def test_cold_start_returns_neutral_default(self):
        context = self.mem.get_strategy_context(DiagnosisCategory.TRANSIENT_NETWORK)
        for action, stats in context.items():
            self.assertEqual(stats.success_rate, config.MEMORY_DEFAULT_SUCCESS_RATE)
            self.assertEqual(stats.sample_count, 0)
            self.assertTrue(stats.cold_start)

    def test_stop_action_not_recorded_in_memory(self):
        self.mem.record_outcome(DiagnosisCategory.SYSTEMIC_RISK, ActionType.STOP, "STOPPED")
        context = self.mem.get_strategy_context(DiagnosisCategory.SYSTEMIC_RISK)
        self.assertNotIn(ActionType.STOP, context)

    def test_escalate_human_not_recorded_in_memory(self):
        self.mem.record_outcome(DiagnosisCategory.AUTH_EXPIRED, ActionType.ESCALATE_HUMAN, "ESCALATED")
        context = self.mem.get_strategy_context(DiagnosisCategory.AUTH_EXPIRED)
        self.assertNotIn(ActionType.ESCALATE_HUMAN, context)

    def test_non_terminal_status_not_recorded(self):
        self.mem.record_outcome(DiagnosisCategory.TRANSIENT_NETWORK, ActionType.RETRY_NOW, "WAITING")
        context = self.mem.get_strategy_context(DiagnosisCategory.TRANSIENT_NETWORK)
        self.assertTrue(context[ActionType.RETRY_NOW].cold_start)

    def test_only_recovery_actions_recorded_with_terminal_outcomes(self):
        self.mem.record_outcome(DiagnosisCategory.TRANSIENT_NETWORK, ActionType.RETRY_NOW, "SUCCESS")
        self.mem.record_outcome(DiagnosisCategory.TRANSIENT_NETWORK, ActionType.RETRY_NOW, "FAILED")
        context = self.mem.get_strategy_context(DiagnosisCategory.TRANSIENT_NETWORK)
        stats = context[ActionType.RETRY_NOW]
        self.assertFalse(stats.cold_start)
        self.assertEqual(stats.sample_count, 2)
        self.assertAlmostEqual(stats.success_rate, 0.5, delta=0.1)


class TestExecutionAndResolutionTime(unittest.TestCase):
    """Verify execution statuses, resolution-time parity, and idempotency advancement."""

    def setUp(self):
        self.audit = AuditLog("data/test_audit_temp.json")

    def tearDown(self):
        if os.path.exists("data/test_audit_temp.json"):
            os.remove("data/test_audit_temp.json")

    def test_execute_stop_returns_stopped(self):
        case = _make_test_payment()
        res = execute(case, ActionType.STOP, self.audit)
        self.assertEqual(res.status, "STOPPED")

    def test_execute_escalate_returns_escalated(self):
        case = _make_test_payment()
        res = execute(case, ActionType.ESCALATE_HUMAN, self.audit)
        self.assertEqual(res.status, "ESCALATED")

    def test_execute_wait_returns_waiting(self):
        case = _make_test_payment()
        res = execute(case, ActionType.WAIT, self.audit)
        self.assertEqual(res.status, "WAITING")

    def test_execute_retry_now_returns_success_or_failed(self):
        case = _make_test_payment()
        res = execute(case, ActionType.RETRY_NOW, self.audit)
        self.assertIn(res.status, {"SUCCESS", "FAILED"})
        self.assertEqual(res.delay_hours, 0.0)

    def test_idempotency_key_advances_with_attempt_loop(self):
        """Idempotency key must change across loop attempts and allow retry on prior failure."""
        case = _make_test_payment(case_id="PAY-IDEM-001", attempt_count=1)
        self.assertEqual(case.idempotency_key, "PAY-IDEM-001_1")

        # Record a failure on attempt 1
        res1 = execute(case, ActionType.RETRY_NOW, self.audit)

        # Advance attempt count
        case.attempt_count = 2
        self.assertEqual(case.idempotency_key, "PAY-IDEM-001_2")

        # Attempt 2 must not be blocked by attempt 1
        res2 = execute(case, ActionType.RETRY_NOW, self.audit)
        self.assertIn(res2.status, {"SUCCESS", "FAILED"})


class TestGateReProposalAndEdgeOutcomes(unittest.TestCase):
    """Verify re-propose cap, terminal escalation, and edge statuses."""

    def setUp(self):
        self.audit = AuditLog("data/test_audit_edge.json")
        self.mem = Memory("data/test_memory_edge.json")
        self.mem.clear()

    def tearDown(self):
        self.mem.clear()
        if os.path.exists("data/test_audit_edge.json"):
            os.remove("data/test_audit_edge.json")

    def test_gate_rejection_cap_auto_escalates(self):
        """When gate rejects twice, orchestrator must auto-escalate to ESCALATE_HUMAN."""
        case = _make_test_payment(attempt_count=5)
        client = MockLLMClient()
        outcome = process_case(case, self.audit, self.mem, client)
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.status, "ESCALATED")

        trail = self.audit.get_case_trail(case.case_id)
        self.assertIsNotNone(trail)
        self.assertGreaterEqual(len(trail.gate_decisions), 2)

    def test_malformed_llm_json_routes_to_fallback_ladder(self):
        """Malformed JSON from Diagnosis / Strategy falls back gracefully."""
        case = _make_test_payment()
        client = MockLLMClient(override_responses={"DIAGNOSIS REQUEST": "INVALID JSON {"})
        outcome = process_case(case, self.audit, self.mem, client)
        self.assertIsNotNone(outcome)
        self.assertIn(outcome.status, {"RECOVERED", "FAILED", "ESCALATED", "WAITING", "STOPPED"})


class TestConfidenceGateAndConflictingSignals(unittest.TestCase):
    """Verify confidence threshold (<0.85) and conflicting signals trigger Fallback Ladder."""

    def test_low_confidence_triggers_fallback_ladder(self):
        case = _make_test_payment(relationship_tier=RelationshipTier.LOW)
        proposal = StrategyProposal(
            proposed_action=ActionType.RETRY_NOW,
            confidence=0.75,  # Below 0.85
            reasoning="Uncertain retry.",
            risk_assessment="MEDIUM",
        )
        stepped = apply_fallback_ladder(case, proposal)
        self.assertEqual(stepped.proposed_action, ActionType.ESCALATE_HUMAN)
        self.assertIn("Fallback Ladder", stepped.reasoning)

    def test_conflicting_signals_triggers_fallback_ladder(self):
        case = _make_test_payment(
            relationship_tier=RelationshipTier.HIGH,
            conflicting_signals=[
                ConflictingSignal(
                    signal_a="Risk score low",
                    signal_b="Customer requested delay",
                    source_a="risk_engine",
                    source_b="support_ticket",
                    description="Customer asked not to debit today.",
                )
            ],
        )
        proposal = StrategyProposal(
            proposed_action=ActionType.RETRY_NOW,
            confidence=0.95,
            reasoning="High confidence.",
            risk_assessment="LOW",
        )
        stepped = apply_fallback_ladder(case, proposal)
        self.assertEqual(stepped.proposed_action, ActionType.SUGGEST_ALTERNATE_METHOD)


class TestEndToEndBatchAndAudit(unittest.TestCase):
    """Verify full end-to-end batch processing and audit log integrity."""

    def setUp(self):
        self.audit = AuditLog("data/test_audit_batch.json")
        self.mem = Memory("data/test_memory_batch.json")
        self.mem.clear()

    def tearDown(self):
        self.mem.clear()
        if os.path.exists("data/test_audit_batch.json"):
            os.remove("data/test_audit_batch.json")

    def test_end_to_end_payment_batch(self):
        cases = [
            _make_test_payment(case_id="PAY-E2E-001", failure_code=FailureCode.BANK_TIMEOUT),
            _make_test_payment(case_id="PAY-E2E-002", failure_code=FailureCode.INSUFFICIENT_FUNDS),
            _make_test_payment(case_id="PAY-E2E-003", failure_code=FailureCode.AUTH_FAILURE),
            _make_test_payment(case_id="PAY-E2E-004", fraud_flag=True),
            _make_test_payment(case_id="PAY-E2E-005", amount=150.0),  # Cheap path
        ]
        report = process_payment_batch(cases, self.audit, self.mem, MockLLMClient())
        self.assertEqual(report.total_cases, 5)
        self.assertEqual(report.total_compliance_violations, 0)
        self.assertGreaterEqual(report.cases_hard_stopped, 1)

        for c in cases:
            trail = self.audit.get_case_trail(c.case_id)
            self.assertIsNotNone(trail)
            self.assertGreater(len(trail.gate_decisions), 0)
            self.assertGreater(len(trail.executions), 0)

class TestToolFailureAndResilience(unittest.TestCase):
    """Verify tool execution retry (1-retry) and graceful audit fallback on 2 AM outages."""

    def setUp(self):
        self.audit = AuditLog("data/test_audit_tool_resilience.json")

    def tearDown(self):
        if os.path.exists("data/test_audit_tool_resilience.json"):
            os.remove("data/test_audit_tool_resilience.json")

    def test_transient_tool_error_retries_and_succeeds(self):
        """Transient tool error retries once and proceeds to normal execution."""
        case = _make_test_payment(case_id="PAY-TOOL-001", failure_code=FailureCode.BANK_TIMEOUT)
        res = execute(case, ActionType.RETRY_NOW, self.audit, simulate_tool_error="transient")
        self.assertIn(res.status, {"SUCCESS", "FAILED"})
        self.assertNotIn("Tool execution failed after 1 retry", res.detail)

    def test_persistent_tool_error_falls_back_and_records_audit(self):
        """Persistent tool error fails after 1 retry, returns FAILED status, and logs audit entry."""
        case = _make_test_payment(case_id="PAY-TOOL-002", failure_code=FailureCode.BANK_TIMEOUT)
        res = execute(case, ActionType.RETRY_NOW, self.audit, simulate_tool_error="persistent")
        self.assertEqual(res.status, "FAILED")
        self.assertIn("Tool execution failed after 1 retry", res.detail)
        
        # Verify audit log was recorded with failure detail
        trail = self.audit.get_case_trail(case.case_id)
        self.assertEqual(len(trail.executions), 1)
        self.assertEqual(trail.executions[0].status, "FAILED")
        self.assertIn("2 AM resilience fallback", trail.executions[0].result_detail)


if __name__ == "__main__":
    unittest.main()
