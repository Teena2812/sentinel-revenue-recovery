"""
Tests for Diagnosis Self-Consistency Check (Prompt 6).

Validates multi-sample self-consistency diagnostic voting:
1. Unanimous (3/3) agreement preserves full consensus confidence.
2. Majority (2/3) agreement adopts majority category with confidence capped at 0.80 (< 0.85).
3. Split (1/1/1) agreement returns UNKNOWN at 0.50 confidence with SELF_CONSISTENCY_DISAGREEMENT.
4. Generic conflicting signal handling in MockLLMClient triggers 2/3 majority with 0.80 cap without case IDs.
5. End-to-end integration test: 0.80-capped majority case flows through orchestrator and gate into
   CaseOutcome.escalation_reason == 'LOW_CONFIDENCE_BLOCKED'.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.diagnosis import DiagnosisCategory, DiagnosisResult, diagnose
from agents.llm_client import MockLLMClient
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import process_case
from core.schemas import (
    ActionType,
    CaseStatus,
    ConflictingSignal,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    RelationshipTier,
)


def _make_test_payment_case(case_id: str = "PAY-SELF-CONSIST-001") -> FailedPaymentCase:
    return FailedPaymentCase(
        case_id=case_id,
        amount=5000.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 24, 10, 0, 0),
        attempt_count=1,
        customer_id="CUST-CONSIST-001",
        customer_history=CustomerHistory(
            reliability_ratio=0.95,
            total_transactions=20,
            total_amount=50000.0,
            has_history=True,
        ),
        status=CaseStatus.OPEN,
        fraud_flag=False,
        relationship_tier=RelationshipTier.HIGH,
    )


class TestDiagnosisSelfConsistency(unittest.TestCase):
    """Test suite for Prompt 6 multi-sample diagnosis self-consistency."""

    def setUp(self):
        self.audit_log = AuditLog(path="data/tmp_audit_self_consistency.json")
        self.audit_log.clear()
        self.memory = Memory("data/tmp_memory_self_consistency.json")
        self.memory.clear()

    def tearDown(self):
        self.audit_log.clear()
        self.memory.clear()
        for p in ["data/tmp_audit_self_consistency.json", "data/tmp_memory_self_consistency.json"]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

    def test_unanimous_samples_retain_high_confidence(self):
        """Scenario 1: 3/3 samples agree -> full consensus confidence (>= 0.85)."""
        case = _make_test_payment_case()
        client = MockLLMClient(override_responses={
            "PERSPECTIVE: FACTUAL": (
                '{"root_cause": "Network switch drop", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.95, "reasoning": "Factual evidence confirms gateway drop"}'
            ),
            "PERSPECTIVE: COUNTER_INDICATOR": (
                '{"root_cause": "Bank timeout confirmation", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.93, "reasoning": "No counter-indications found"}'
            ),
            "PERSPECTIVE: CONSERVATIVE": (
                '{"root_cause": "Verified switch glitch", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.94, "reasoning": "Reliable customer with zero fraud risk"}'
            ),
        })

        res = diagnose(case, client, num_samples=3)
        self.assertEqual(res.category, DiagnosisCategory.TRANSIENT_NETWORK)
        self.assertGreaterEqual(res.confidence, 0.85)
        self.assertIn("Consensus diagnosis (3/3 agreement", res.reasoning)

    def test_majority_samples_capped_at_eighty(self):
        """Scenario 2: 2/3 samples agree -> majority adopted, confidence strictly capped at 0.80."""
        case = _make_test_payment_case()
        # Even with individual confidences of 0.98 and 0.96 (which would otherwise average 0.97 * 0.90 = 0.873),
        # the cap guarantees it cannot exceed 0.80 due to the dissenting sample.
        client = MockLLMClient(override_responses={
            "PERSPECTIVE: FACTUAL": (
                '{"root_cause": "Balance insufficient", "category": "FUNDS_UNAVAILABLE", '
                '"confidence": 0.98, "reasoning": "Factual sample"}'
            ),
            "PERSPECTIVE: COUNTER_INDICATOR": (
                '{"root_cause": "Possible mandate expiry", "category": "AUTH_EXPIRED", '
                '"confidence": 0.85, "reasoning": "Dissenting sample"}'
            ),
            "PERSPECTIVE: CONSERVATIVE": (
                '{"root_cause": "Depleted balance", "category": "FUNDS_UNAVAILABLE", '
                '"confidence": 0.96, "reasoning": "Conservative sample"}'
            ),
        })

        res = diagnose(case, client, num_samples=3)
        self.assertEqual(res.category, DiagnosisCategory.FUNDS_UNAVAILABLE)
        self.assertEqual(res.confidence, 0.80)
        self.assertIn("Majority diagnosis (2/3 agreement", res.reasoning)
        self.assertIn("strictly capped at 0.80", res.reasoning)

    def test_split_samples_route_to_unknown_low_confidence(self):
        """Scenario 3: 1/1/1 split -> UNKNOWN at 0.50 with SELF_CONSISTENCY_DISAGREEMENT."""
        case = _make_test_payment_case()
        client = MockLLMClient(override_responses={
            "PERSPECTIVE: FACTUAL": (
                '{"root_cause": "Network issue", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.90, "reasoning": "Sample 1"}'
            ),
            "PERSPECTIVE: COUNTER_INDICATOR": (
                '{"root_cause": "Account balance low", "category": "FUNDS_UNAVAILABLE", '
                '"confidence": 0.88, "reasoning": "Sample 2"}'
            ),
            "PERSPECTIVE: CONSERVATIVE": (
                '{"root_cause": "Token expired", "category": "AUTH_EXPIRED", '
                '"confidence": 0.86, "reasoning": "Sample 3"}'
            ),
        })

        res = diagnose(case, client, num_samples=3)
        self.assertEqual(res.category, DiagnosisCategory.UNKNOWN)
        self.assertEqual(res.confidence, 0.50)
        self.assertIn("SELF_CONSISTENCY_DISAGREEMENT", res.reasoning)

    def test_generic_conflicting_signals_triggers_disagreement_without_case_ids(self):
        """Scenario 4: Case with generic ConflictingSignals triggers 2/3 majority disagreement via prompt text."""
        case = _make_test_payment_case("NOVEL-CASE-999")  # Novel case ID never seen before
        case.conflicting_signals = [
            ConflictingSignal(
                signal_a="Safe switch",
                signal_b="Customer complaint",
                source_a="risk_engine",
                source_b="support_ticket",
                description="Contradictory support ticket requesting stop debit.",
            )
        ]

        # Use standard MockLLMClient with zero overrides
        client = MockLLMClient()
        res = diagnose(case, client, num_samples=3)

        # Counter-indicator perspective detected the conflicting signal, producing 2/3 agreement
        self.assertEqual(res.category, DiagnosisCategory.TRANSIENT_NETWORK)
        self.assertEqual(res.confidence, 0.80)  # Capped at 0.80
        self.assertIn("Majority diagnosis (2/3 agreement", res.reasoning)

    def test_majority_capped_case_flows_to_low_confidence_blocked_end_to_end(self):
        """Prompt 6 Integration Test (Natural Flow): Manufactured 2/3-majority case with 0.80 cap applied.

        Runs through the full pipeline end-to-end (not diagnosis in isolation) WITHOUT patching.
        The 0.80 confidence cap from 2/3 majority voting propagates through the strategy proposal.
        The soft ladder steps down RETRY_NOW to SUGGEST_ALTERNATE_METHOD while retaining 0.80 confidence.
        The Deterministic Compliance Gate independently evaluates check_confidence_threshold, rejects the
        sub-threshold action (0.80 < 0.85), and terminates end-to-end in:
        CaseOutcome.escalation_reason == 'LOW_CONFIDENCE_BLOCKED'.
        """
        case = _make_test_payment_case("PAY-INT-CAP-001")

        # Mock LLM provides 2-vs-1 diagnosis and active RETRY_NOW strategy proposal with 0.80 confidence
        client = MockLLMClient(override_responses={
            "PERSPECTIVE: FACTUAL": (
                '{"root_cause": "Transient network issue", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.95, "reasoning": "Factual sample"}'
            ),
            "PERSPECTIVE: COUNTER_INDICATOR": (
                '{"root_cause": "Possible risk escalation", "category": "SYSTEMIC_RISK", '
                '"confidence": 0.85, "reasoning": "Counter sample"}'
            ),
            "PERSPECTIVE: CONSERVATIVE": (
                '{"root_cause": "Network switch delay", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.92, "reasoning": "Conservative sample"}'
            ),
            "STRATEGY PROPOSAL REQUEST": (
                '{"proposed_action": "RETRY_NOW", "confidence": 0.80, '
                '"reasoning": "Proposing retry based on majority diagnosis with 0.80 confidence cap", '
                '"risk_assessment": "MEDIUM"}'
            ),
        })

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
        self.assertEqual(outcome.diagnosis.confidence, 0.80)
        self.assertIn("strictly capped at 0.80", outcome.diagnosis.reasoning)

    def test_majority_capped_case_flows_to_low_confidence_blocked_bypass_ladder(self):
        """Prompt 6 Integration Test (Bypass Pattern): Independent gate backstop verification.

        Bypasses the soft fallback ladder to prove the Deterministic Compliance Gate independently
        blocks the raw 0.80-capped proposal directly, terminating in:
        CaseOutcome.escalation_reason == 'LOW_CONFIDENCE_BLOCKED'.
        """
        case = _make_test_payment_case("PAY-INT-CAP-BYPASS-002")

        client = MockLLMClient(override_responses={
            "PERSPECTIVE: FACTUAL": (
                '{"root_cause": "Transient network issue", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.95, "reasoning": "Factual sample"}'
            ),
            "PERSPECTIVE: COUNTER_INDICATOR": (
                '{"root_cause": "Possible risk escalation", "category": "SYSTEMIC_RISK", '
                '"confidence": 0.85, "reasoning": "Counter sample"}'
            ),
            "PERSPECTIVE: CONSERVATIVE": (
                '{"root_cause": "Network switch delay", "category": "TRANSIENT_NETWORK", '
                '"confidence": 0.92, "reasoning": "Conservative sample"}'
            ),
            "STRATEGY PROPOSAL REQUEST": (
                '{"proposed_action": "RETRY_NOW", "confidence": 0.80, '
                '"reasoning": "Proposing retry based on majority diagnosis with 0.80 confidence cap", '
                '"risk_assessment": "MEDIUM"}'
            ),
        })

        with patch("core.orchestrator.apply_fallback_ladder", side_effect=lambda c, p: p):
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
        self.assertEqual(outcome.diagnosis.confidence, 0.80)
        self.assertIn("strictly capped at 0.80", outcome.diagnosis.reasoning)


if __name__ == "__main__":
    unittest.main()
