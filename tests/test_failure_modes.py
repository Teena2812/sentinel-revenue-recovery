"""
Tests for Failure Modes & Failure Recovery (Prompt 10).

Validates the system's resilience under induced adversarial conditions:
1. Out-of-menu action rejection (end-to-end pipeline) -> INVALID_ACTION_REJECTED
2. Independent compliance gate rejection of out-of-menu action (direct gate bypass)
3. Malformed / unparseable LLM output -> LLM_RESPONSE_UNPARSEABLE
4. Simulated upstream API timeout -> LLM_TIMEOUT
5. Concurrent double-processing race condition with threading.Barrier(2) -> CONCURRENT_EXECUTION_BLOCKED
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from datetime import datetime
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.llm_client import MockLLMClient
from core import config
from core.audit_log import AuditLog
from core.compliance import check_allowed_action, run_all_checks
from core.memory import Memory
from core.orchestrator import CaseOutcome, process_case
from core.schemas import (
    ActionType,
    CaseStatus,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
)


def _make_test_case(case_id: str = "PAY-FAIL-TEST-001") -> FailedPaymentCase:
    return FailedPaymentCase(
        case_id=case_id,
        amount=1500.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 24, 10, 0, 0),
        attempt_count=1,
        customer_id="CUST-FAIL-001",
        customer_history=CustomerHistory(
            reliability_ratio=0.9,
            total_transactions=15,
            total_amount=45000.0,
            has_history=True,
        ),
        status=CaseStatus.OPEN,
        fraud_flag=False,
    )


class TestFailureModes(unittest.TestCase):
    """Test suite asserting graceful degradation across induced failure modes."""

    def setUp(self):
        self.audit_log = AuditLog(path="data/tmp_audit_failure_tests.json")
        self.audit_log.clear()
        self.memory = Memory("data/tmp_memory_failure_tests.json")
        self.memory.clear()

    def tearDown(self):
        self.audit_log.clear()
        self.memory.clear()
        for p in ["data/tmp_audit_failure_tests.json", "data/tmp_memory_failure_tests.json"]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def test_out_of_menu_action_rejected_pipeline(self):
        """Scenario 1A: LLM proposes an out-of-menu action -> blocked with INVALID_ACTION_REJECTED."""
        case = _make_test_case("PAY-OUT-OF-MENU-001")
        # Simulate LLM returning a prohibited/toxic action
        client = MockLLMClient(override_responses={
            "STRATEGY PROPOSAL REQUEST": {
                "proposed_action": "SEND_THREATENING_NOTICE",
                "confidence": 0.95,
                "reasoning": "Proposing aggressive debtor intimidation.",
                "risk_assessment": "HIGH",
            }
        })

        outcome = process_case(
            case,
            self.audit_log,
            self.memory,
            llm_client=client,
            current_time=config.SIMULATED_CURRENT_TIME,
        )

        # Must not execute the illegal action; must route safely to human review
        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.escalation_reason, "INVALID_ACTION_REJECTED")
        self.assertIn("INVALID_ACTION_REJECTED", outcome.strategy.reasoning)
        self.assertEqual(outcome.strategy.confidence, 0.0)

    def test_compliance_gate_independently_rejects_out_of_menu_action(self):
        """Scenario 1B: Direct call to compliance gate with unapproved action.

        Proves the Deterministic Compliance Gate independently blocks invalid actions,
        even if the strategy LLM layer is completely bypassed.
        """
        case = _make_test_case("PAY-GATE-BYPASS-001")
        # ActionType.ESCALATE_TONE is valid for B2B, but prohibited for FAILED_PAYMENT
        gate_decision = run_all_checks(
            case,
            ActionType.ESCALATE_TONE,
            self.audit_log.get_execution_log(),
            current_time=config.SIMULATED_CURRENT_TIME,
        )

        self.assertFalse(gate_decision.approved)
        violations = [v for v in gate_decision.violations if v.rule_name == "allowed_action"]
        self.assertEqual(len(violations), 1)
        self.assertIn("INVALID_ACTION_REJECTED", violations[0].reason)

    def test_compliance_gate_independently_blocks_low_confidence_action(self):
        """Prompt 7: Direct call to compliance gate with high-friction action and sub-threshold confidence.

        Proves the Deterministic Compliance Gate independently blocks low-confidence recovery
        actions even when the strategy agent's soft Fallback Ladder is completely bypassed.
        """
        case = _make_test_case("PAY-CONF-GATE-BYPASS-001")
        # ActionType.RETRY_NOW with confidence 0.70 (< 0.85) bypassing strategy agent
        gate_decision = run_all_checks(
            case,
            ActionType.RETRY_NOW,
            self.audit_log.get_execution_log(),
            current_time=config.SIMULATED_CURRENT_TIME,
            confidence=0.70,
        )

        self.assertFalse(gate_decision.approved)
        violations = [v for v in gate_decision.violations if v.rule_name == "confidence_threshold"]
        self.assertEqual(len(violations), 1)
        self.assertIn("LOW_CONFIDENCE_BLOCKED", violations[0].reason)

    def test_compliance_gate_allows_high_confidence_action(self):
        """Prompt 7: High-confidence recovery action passes deterministic gate check."""
        case = _make_test_case("PAY-CONF-HIGH-001")
        gate_decision = run_all_checks(
            case,
            ActionType.RETRY_NOW,
            self.audit_log.get_execution_log(),
            current_time=config.SIMULATED_CURRENT_TIME,
            confidence=0.90,
        )
        self.assertTrue(gate_decision.approved)

    def test_compliance_gate_allows_human_escalation_at_low_confidence(self):
        """Prompt 7: Terminal/passive actions (ESCALATE_HUMAN) remain exempt from low-confidence blocking."""
        case = _make_test_case("PAY-CONF-ESC-001")
        gate_decision = run_all_checks(
            case,
            ActionType.ESCALATE_HUMAN,
            self.audit_log.get_execution_log(),
            current_time=config.SIMULATED_CURRENT_TIME,
            confidence=0.20,
        )
        self.assertTrue(gate_decision.approved)

    def test_low_confidence_action_blocked_and_escalated_when_ladder_bypassed(self):
        """Prompt 7 / Scenario 5: End-to-end pipeline test when soft ladder is bypassed.

        Proves that if an unvalidated component or rogue policy bypasses the soft ladder
        and sends an active recovery action with confidence < 0.85 to the execution loop,
        the Deterministic Compliance Gate independently blocks it and routes to ESCALATE_HUMAN
        with CaseOutcome.escalation_reason == 'LOW_CONFIDENCE_BLOCKED'.
        """
        import unittest.mock
        case = _make_test_case("PAY-CONF-E2E-001")
        client = MockLLMClient(override_responses={
            "STRATEGY PROPOSAL REQUEST": (
                '{"proposed_action": "RETRY_NOW", "confidence": 0.70, '
                '"reasoning": "Sub-threshold retry proposal", "risk_assessment": "MEDIUM"}'
            )
        })

        with unittest.mock.patch("core.orchestrator.apply_fallback_ladder", side_effect=lambda c, p: p):
            outcome = process_case(
                case,
                self.audit_log,
                self.memory,
                client,
                current_time=config.SIMULATED_CURRENT_TIME,
            )

        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.escalation_reason, "LOW_CONFIDENCE_BLOCKED")

    def test_malformed_unparseable_llm_response(self):
        """Scenario 2: LLM API returns corrupted/truncated JSON -> catches, zero crash, LLM_RESPONSE_UNPARSEABLE."""
        case = _make_test_case("PAY-MALFORMED-001")
        client = MockLLMClient(override_responses={
            "STRATEGY PROPOSAL REQUEST": "{'corrupted_truncated_json': true, missing_bracket..."
        })

        outcome = process_case(
            case,
            self.audit_log,
            self.memory,
            llm_client=client,
            current_time=config.SIMULATED_CURRENT_TIME,
        )

        # Handled cleanly without uncaught JSON exceptions
        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.escalation_reason, "LLM_RESPONSE_UNPARSEABLE")
        self.assertEqual(outcome.strategy.confidence, 0.0)

    def test_simulated_llm_api_timeout(self):
        """Scenario 3: Simulated API socket timeout -> no hang, zero crash, LLM_TIMEOUT."""
        case = _make_test_case("PAY-TIMEOUT-001")
        client = MockLLMClient(override_responses={
            "STRATEGY PROPOSAL REQUEST": TimeoutError("Simulated LLM gateway socket timeout after 30s")
        })

        outcome = process_case(
            case,
            self.audit_log,
            self.memory,
            llm_client=client,
            current_time=config.SIMULATED_CURRENT_TIME,
        )

        # Gracefully escalated without hanging
        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.escalation_reason, "LLM_TIMEOUT")
        self.assertEqual(outcome.strategy.confidence, 0.0)

    def test_concurrent_double_processing_race_condition(self):
        """Scenario 4: Two threads hitting process_case() at the exact same instant.

        Uses threading synchronization to guarantee Thread B hits the compliance gate
        while Thread A has reserved the key and is actively executing (IN_FLIGHT).
        Asserts:
        - Exactly ONE thread succeeds (RECOVERED).
        - The concurrent duplicate attempt is blocked with CONCURRENT_EXECUTION_BLOCKED.
        - Zero duplicate execution side-effects occur (execute() called exactly ONCE in total).
        """
        import core.orchestrator as orchestrator_mod
        from unittest.mock import patch
        import random

        client = MockLLMClient()
        execution_entered = threading.Event()
        allow_execution_to_finish = threading.Event()
        executing_threads = []
        real_execute = orchestrator_mod.execute

        def synchronized_execute(case, action, audit_log, **kwargs):
            executing_threads.append(threading.current_thread().name)
            execution_entered.set()  # Signal: Thread A has reserved the key and is inside execute()
            allow_execution_to_finish.wait(timeout=2.0)  # Hold Thread A in IN_FLIGHT state
            return real_execute(case, action, audit_log, **kwargs)

        outcomes: list[CaseOutcome] = []
        threads: list[threading.Thread] = []

        # RNG seed 1 produces random() < 0.60, guaranteeing recovery on attempt 1
        worker_rng = random.Random(1)

        def worker_a(case_instance):
            outcome = process_case(
                case_instance,
                self.audit_log,
                self.memory,
                llm_client=client,
                current_time=config.SIMULATED_CURRENT_TIME,
                rng=worker_rng,
            )
            outcomes.append(outcome)

        def worker_b(case_instance):
            # Wait until Thread A has actively reserved the key and entered execution
            execution_entered.wait(timeout=2.0)
            outcome = process_case(
                case_instance,
                self.audit_log,
                self.memory,
                llm_client=client,
                current_time=config.SIMULATED_CURRENT_TIME,
                rng=worker_rng,
            )
            outcomes.append(outcome)
            # Release Thread A to finish
            allow_execution_to_finish.set()

        case_a = _make_test_case("PAY-RACE-001")
        case_b = _make_test_case("PAY-RACE-001")

        with patch("core.orchestrator.execute", side_effect=synchronized_execute):
            t1 = threading.Thread(target=worker_a, args=(case_a,), name="Thread-A")
            t2 = threading.Thread(target=worker_b, args=(case_b,), name="Thread-B")
            threads.extend([t1, t2])

            t1.start()
            t2.start()

            for t in threads:
                t.join()

        # 1. Assert outcomes: exactly one RECOVERED, exactly one GATE_BLOCKED
        self.assertEqual(len(outcomes), 2)
        statuses = [o.status for o in outcomes]
        self.assertIn("RECOVERED", statuses)
        self.assertIn("GATE_BLOCKED", statuses)

        # 2. Assert distinct reason code on the blocked thread
        blocked_outcome = next(o for o in outcomes if o.status == "GATE_BLOCKED")
        self.assertEqual(blocked_outcome.escalation_reason, "CONCURRENT_EXECUTION_BLOCKED")

        # 3. Assert zero duplicate execution side-effects:
        # execute() was called exactly ONCE across both threads, exclusively by Thread-A!
        self.assertEqual(len(executing_threads), 1)
        self.assertEqual(executing_threads[0], "Thread-A")
        self.assertNotIn("Thread-B", executing_threads)

        # 4. Assert audit trail recorded exactly ONE execution entry overall
        trail = self.audit_log.get_case_trail(case_a.case_id)
        self.assertIsNotNone(trail)
        self.assertEqual(len(trail.executions), 1)
        self.assertEqual(trail.executions[0].status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
