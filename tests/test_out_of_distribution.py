"""
tests/test_out_of_distribution.py — Out-of-Distribution Robustness Suite.

Verifies system behavior on unseen, malformed, and out-of-distribution inputs
through the full end-to-end pipeline (process_case(), not diagnosis in isolation).

Tests three core scenarios:
1. Unseen failure code / feature combinations
2. Extreme and malformed amounts (negative and extreme positive values)
3. Unknown case_type handling
"""

from __future__ import annotations

from datetime import datetime
import os
import unittest

from agents.llm_client import MockLLMClient
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import process_case
from core.schemas import (
    ActionType,
    CaseStatus,
    CaseType,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    RelationshipTier,
    case_to_dict,
    dict_to_failed_payment,
)


class TestOutOfDistribution(unittest.TestCase):
    """Robustness tests for out-of-distribution, malformed, and unseen inputs."""

    def setUp(self) -> None:
        self.tmp_audit_path = "data/tmp_audit_ood.json"
        self.tmp_memory_path = "data/tmp_memory_ood.json"
        self.audit_log = AuditLog(self.tmp_audit_path)
        self.memory = Memory(self.tmp_memory_path)
        self.client = MockLLMClient()

    def tearDown(self) -> None:
        for path in [self.tmp_audit_path, self.tmp_memory_path]:
            if os.path.exists(path):
                os.remove(path)

    def test_unseen_failure_code_or_combination(self) -> None:
        """Scenario 1: Unseen failure code and novel feature tuple.

        APPROACH & RATIONALE:
        ---------------------
        1. Schema Deserialization:
           In core/schemas.py, FailureCode is a strict enum.Enum containing exactly
           5 members (INSUFFICIENT_FUNDS, BANK_TIMEOUT, AUTH_FAILURE, GATEWAY_ERROR,
           FRAUD_REJECTION). When an external payload with an unseen failure code string
           (e.g. 'CRYPTO_NETWORK_REJECT') enters dict_to_failed_payment(), Python strictly
           rejects it at schema construction with ValueError. We assert this clean schema
           rejection occurs.

        2. Constructible End-to-End Pipeline Case:
           Because unknown enum strings cannot instantiate a FailedPaymentCase, we evaluate
           a case whose combination of fields has NEVER appeared together in the 80-case
           benchmark dataset:
             - failure_code: FailureCode.GATEWAY_ERROR
             - relationship_tier: RelationshipTier.HIGH
             - attempt_count: 4
             - amount: 42,500.0
           In the 80-case dataset, GATEWAY_ERROR on HIGH tier only occurs at attempt 1
           (PAY-3170f437-a8f). Attempt 4 is NEVER paired with HIGH tier or GATEWAY_ERROR
           anywhere in the dataset.

        3. Full Pipeline Verification (process_case):
           Asserts the system:
             - Does not crash.
             - Does not execute an action on ambiguous ground (does not blindly retry).
             - Flows cleanly to a low-confidence / policy escalation (ESCALATE_HUMAN).
        """
        # Step 1: Verify clean schema rejection of unknown enum string
        bad_payload = {
            "case_id": "PAY-OOD-SCHEMA-001",
            "amount": 2500.0,
            "failure_code": "CRYPTO_NETWORK_REJECT",
            "timestamp": "2026-08-24T10:00:00",
            "attempt_count": 1,
            "customer_id": "CUST-OOD-001",
        }
        with self.assertRaises(ValueError):
            dict_to_failed_payment(bad_payload)

        # Step 2: Full pipeline execution of unseen feature combination
        unseen_combo_case = FailedPaymentCase(
            case_id="PAY-OOD-COMBO-001",
            amount=42500.0,
            failure_code=FailureCode.GATEWAY_ERROR,
            timestamp=datetime(2026, 8, 24, 10, 0, 0),
            attempt_count=4,
            customer_id="CUST-OOD-001",
            customer_history=CustomerHistory(
                reliability_ratio=0.95,
                total_transactions=120,
                total_amount=850000.0,
                has_history=True,
            ),
            status=CaseStatus.OPEN,
            fraud_flag=False,
            relationship_tier=RelationshipTier.HIGH,
        )

        outcome = process_case(
            unseen_combo_case,
            self.audit_log,
            self.memory,
            llm_client=self.client,
        )

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome.escalation_reason, "strategy_policy_escalation")

    def test_extreme_and_malformed_amounts(self) -> None:
        """Scenario 2: Extreme positive amount and negative amount.

        APPROACH & RATIONALE:
        ---------------------
        1. Extreme Positive Amount:
           Evaluates an amount several orders of magnitude larger than anything in the
           dataset (₹500,000,000,000.0 / ₹500 Billion vs max dataset ₹44.9K payment /
           ₹4.15M B2B).
           Asserts the system does not crash, preserves precision, and handles boundedly.

        2. Negative Amount:
           Evaluates a malformed amount of -₹1,500.0.
           The prompt requires asserting that the system:
             - Does not crash.
             - Does not confidently execute an action on ambiguous ground.
             - Either gets rejected cleanly at schema validation or flows to a
               low-confidence escalation / explicit unknown-category path.

           ARCHITECTURAL GAP DISCOVERY:
           Neither FailedPaymentCase dataclass nor check_cost_threshold validates
           amount > 0. In core/compliance.py, check_cost_threshold evaluates:
             case.amount >= threshold (₹500)
           Because -1500.0 >= 500 is False, should_skip_pipeline() mistakenly classifies
           the negative amount as a cheap sub-threshold micro-case and assigns
           ActionType.RETRY_NOW. The pipeline then executes RETRY_NOW on a negative balance.

           The assertion below strictly checks that the system does NOT route a negative
           amount to an automated recovery action (must be STOP or ESCALATE_HUMAN).
           If this assertion fails, it reports this genuine gap as required by the prompt.
        """
        # Sub-scenario 2A: Extreme positive amount (₹500 Billion)
        case_pos = FailedPaymentCase(
            case_id="PAY-OOD-POS-EXTREME-001",
            amount=500_000_000_000.0,
            failure_code=FailureCode.INSUFFICIENT_FUNDS,
            timestamp=datetime(2026, 8, 24, 10, 0, 0),
            attempt_count=1,
            customer_id="CUST-OOD-POS-001",
            customer_history=CustomerHistory(
                reliability_ratio=0.7,
                total_transactions=5,
                total_amount=50000.0,
                has_history=True,
            ),
            status=CaseStatus.OPEN,
            fraud_flag=False,
            relationship_tier=RelationshipTier.HIGH,
        )
        outcome_pos = process_case(
            case_pos,
            self.audit_log,
            self.memory,
            llm_client=self.client,
        )
        self.assertIsNotNone(outcome_pos)
        self.assertEqual(outcome_pos.amount, 500_000_000_000.0)

        # Sub-scenario 2B: Negative and Non-Positive Amounts (-₹1,500.0, ₹0.0)
        # 1. Strict Schema-Level Validation (core/schemas.py __post_init__):
        # A failed payment or overdue invoice can never legitimately have a zero or negative amount.
        # Construction must reject non-positive amounts immediately with ValueError.
        with self.assertRaises(ValueError):
            FailedPaymentCase(
                case_id="PAY-OOD-NEG-001",
                amount=-1500.0,
                failure_code=FailureCode.BANK_TIMEOUT,
                timestamp=datetime(2026, 8, 24, 10, 0, 0),
                attempt_count=1,
                customer_id="CUST-OOD-NEG-001",
                customer_history=CustomerHistory(
                    reliability_ratio=0.8,
                    total_transactions=10,
                    total_amount=15000.0,
                    has_history=True,
                ),
            )

        with self.assertRaises(ValueError):
            FailedPaymentCase(
                case_id="PAY-OOD-ZERO-001",
                amount=0.0,
                failure_code=FailureCode.BANK_TIMEOUT,
                timestamp=datetime(2026, 8, 24, 10, 0, 0),
                attempt_count=1,
                customer_id="CUST-OOD-ZERO-001",
                customer_history=CustomerHistory(),
            )

        # 2. Defense-in-Depth Gate Escalation (core/compliance.py should_skip_pipeline):
        # Even if a malformed non-positive amount bypasses schema construction (e.g. via mock object
        # or unchecked dict deserialization), the compliance gate MUST NEVER route it to RETRY_NOW.
        # It must route strictly to ESCALATE_HUMAN with an INVALID_AMOUNT_ESCALATED reason.
        class MockBypassedNegativeCase:
            def __init__(self) -> None:
                self.case_id = "PAY-OOD-BYPASS-NEG"
                self.case_type = CaseType.FAILED_PAYMENT
                self.amount = -1500.0
                self.attempt_count = 1
                self.idempotency_key = "PAY-OOD-BYPASS-NEG_1"
                self.status = CaseStatus.OPEN
                self.fraud_flag = False
                self.dispute_flag = False
                self.relationship_tier = None
                self.conflicting_signals = []

        bypassed_case = MockBypassedNegativeCase()
        outcome_bypassed = process_case(
            bypassed_case,  # type: ignore[arg-type]
            self.audit_log,
            self.memory,
            llm_client=self.client,
        )
        self.assertIsNotNone(outcome_bypassed)
        self.assertEqual(outcome_bypassed.final_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(outcome_bypassed.status, "ESCALATED")
        self.assertIn("INVALID_AMOUNT_ESCALATED", outcome_bypassed.escalation_reason or "")

    def test_unknown_case_type(self) -> None:
        """Scenario 3: Unknown case_type handling.

        APPROACH & RATIONALE:
        ---------------------
        1. Schema-Level Rejection:
           CaseType is an Enum with members FAILED_PAYMENT and B2B_RECEIVABLE.
           Attempting to construct an invalid CaseType raises ValueError.
           Attempting to serialize an unknown case class via case_to_dict() raises TypeError.

        2. Mock Ingestion Through Full Pipeline:
           Tests a mock case object with case_type='CRYPTO_ARBITRAGE' entering process_case().
           Confirms the system does NOT silently treat it as a known type and successfully execute.
           Because process_case assumes non-payment objects are B2B, a mock missing B2B
           attributes raises AttributeError, blocking silent recovery execution.
        """
        # Step 1: Schema-level rejection of unknown case_type enum
        with self.assertRaises(ValueError):
            CaseType("CRYPTO_ARBITRAGE")

        # Step 2: Serialization rejection of unknown case object
        class MockUnknownCase:
            def __init__(self) -> None:
                self.case_id = "UNK-001"
                self.case_type = "CRYPTO_ARBITRAGE"
                self.amount = 5000.0
                self.attempt_count = 1
                self.idempotency_key = "UNK-001_1"
                self.status = CaseStatus.OPEN
                self.fraud_flag = False
                self.dispute_flag = False
                self.relationship_tier = None
                self.conflicting_signals = []

        mock_case = MockUnknownCase()
        with self.assertRaises(TypeError):
            case_to_dict(mock_case)  # type: ignore[arg-type]

        # Step 3: Confirm it cannot silently complete execution as a known type
        with self.assertRaises(ValueError):
            process_case(
                mock_case,  # type: ignore[arg-type]
                self.audit_log,
                self.memory,
                llm_client=self.client,
            )


if __name__ == "__main__":
    unittest.main()
