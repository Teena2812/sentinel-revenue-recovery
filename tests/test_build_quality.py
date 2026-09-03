"""
tests/test_build_quality.py — Build Quality, Schema Validation & Rules-as-Data Tests

Verifies:
1. Rules-as-data external JSON configuration (config/rules_config.json) loading and hardcoded fallback.
2. Missing-file fallback path when rules_config.json is absent.
3. Strict typed schema validation (core/schema_validation.py) raising SchemaValidationError.
4. End-to-end diagnosis and strategy pipeline handling of validly-parsed-but-schema-invalid LLM responses.
5. End-to-end orchestrator case processing without uncaught exceptions when LLM returns invalid schema.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from agents.diagnosis import DiagnosisCategory, DiagnosisResult, diagnose
from agents.llm_client import MockLLMClient
from agents.strategy import propose_strategy
from core import config
from core.audit_log import AuditLog
from core.config import _load_rules_config
from core.memory import Memory
from core.orchestrator import process_case
from core.schema_validation import (
    FieldMissingError,
    FieldTypeError,
    InvalidEnumError,
    SchemaValidationError,
    validate_diagnosis_output,
    validate_strategy_output,
)
from core.schemas import (
    ActionType,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    RelationshipTier,
)


def _make_test_case(case_id: str = "PAY-TEST-SCHEMA-001") -> FailedPaymentCase:
    return FailedPaymentCase(
        case_id=case_id,
        amount=5000.0,
        failure_code=FailureCode.BANK_TIMEOUT,
        timestamp=datetime(2026, 8, 24, 10, 0),
        attempt_count=1,
        customer_id="CUST-SCHEMA-001",
        customer_history=CustomerHistory(
            has_history=True,
            reliability_ratio=0.85,
            total_transactions=15,
            total_amount=75000.0,
        ),
        relationship_tier=RelationshipTier.MEDIUM,
    )


class TestRulesAsData(unittest.TestCase):
    """Verify business policy thresholds externalization into config/rules_config.json."""

    def test_rules_config_values_loaded(self):
        """Verify that core.config values match the externalized JSON specifications."""
        self.assertEqual(config.MAX_ATTEMPTS_PAYMENT, 5)
        self.assertEqual(config.MAX_ATTEMPTS_B2B, 4)
        self.assertEqual(config.FATIGUE_CAP_PAYMENT, 2)
        self.assertEqual(config.FATIGUE_CAP_B2B, 3)
        self.assertEqual(config.MIN_RECOVERY_AMOUNT_PAYMENT, 500)
        self.assertEqual(config.MIN_RECOVERY_AMOUNT_B2B, 5000)
        self.assertEqual(config.CONTACT_HOUR_START, 8)
        self.assertEqual(config.CONTACT_HOUR_END, 19)

    def test_missing_file_fallback_path(self):
        """Verify that _load_rules_config() returns default thresholds if config file is missing."""
        with patch("builtins.open", side_effect=FileNotFoundError("Mock missing file")):
            defaults = _load_rules_config()
            self.assertEqual(defaults["attempt_caps"]["payment"], 5)
            self.assertEqual(defaults["attempt_caps"]["b2b"], 4)
            self.assertEqual(defaults["fatigue_caps"]["payment"], 2)
            self.assertEqual(defaults["fatigue_caps"]["b2b"], 3)
            self.assertEqual(defaults["cost_thresholds"]["payment_min_recovery"], 500)
            self.assertEqual(defaults["cost_thresholds"]["b2b_min_recovery"], 5000)
            self.assertEqual(defaults["contact_hours"]["start_hour"], 8)
            self.assertEqual(defaults["contact_hours"]["end_hour"], 19)


class TestSchemaValidationIsolated(unittest.TestCase):
    """Verify isolated unit validation behavior of core/schema_validation.py."""

    def test_diagnosis_missing_field(self):
        """Missing required field raises FieldMissingError."""
        payload = {"category": "TRANSIENT_NETWORK", "confidence": 0.9}
        with self.assertRaises(FieldMissingError):
            validate_diagnosis_output(payload)

    def test_diagnosis_wrong_confidence_type(self):
        """Non-numeric confidence string raises FieldTypeError."""
        payload = {
            "category": "TRANSIENT_NETWORK",
            "confidence": "high",
            "reasoning": "Valid text",
        }
        with self.assertRaises(FieldTypeError):
            validate_diagnosis_output(payload)

    def test_diagnosis_confidence_out_of_bounds(self):
        """Confidence outside [0.0, 1.0] raises FieldTypeError."""
        payload = {
            "category": "TRANSIENT_NETWORK",
            "confidence": 1.5,
            "reasoning": "Valid text",
        }
        with self.assertRaises(FieldTypeError):
            validate_diagnosis_output(payload)

    def test_diagnosis_invalid_enum_category(self):
        """Unrecognized category string raises InvalidEnumError."""
        payload = {
            "category": "UNRECOGNIZED_CATEGORY",
            "confidence": 0.85,
            "reasoning": "Valid text",
        }
        with self.assertRaises(InvalidEnumError):
            validate_diagnosis_output(payload)

    def test_strategy_missing_field(self):
        """Missing required field in strategy raises FieldMissingError."""
        payload = {
            "proposed_action": "RETRY_NOW",
            "confidence": 0.85,
            "reasoning": "Valid text",
        }
        with self.assertRaises(FieldMissingError):
            validate_strategy_output(payload)

    def test_strategy_wrong_confidence_type(self):
        """String confidence in strategy raises FieldTypeError."""
        payload = {
            "proposed_action": "RETRY_NOW",
            "confidence": "very_confident",
            "reasoning": "Valid text",
            "risk_assessment": "LOW",
        }
        with self.assertRaises(FieldTypeError):
            validate_strategy_output(payload)

    def test_strategy_invalid_risk_assessment(self):
        """Invalid risk assessment string raises InvalidEnumError."""
        payload = {
            "proposed_action": "RETRY_NOW",
            "confidence": 0.85,
            "reasoning": "Valid text",
            "risk_assessment": "CRITICAL_EXTREME",
        }
        with self.assertRaises(InvalidEnumError):
            validate_strategy_output(payload)


class TestPipelineSchemaIntegration(unittest.TestCase):
    """Verify that schema errors are cleanly handled by the agents and orchestrator."""

    def test_diagnosis_pipeline_catches_invalid_schema(self):
        """Validly parsed JSON with wrong schema types/enums falls back to LLM_RESPONSE_INVALID_SCHEMA."""
        case = _make_test_case()
        invalid_payload = {
            "category": "MADE_UP_CATEGORY",
            "confidence": "high",
            "reasoning": "Valid JSON but invalid schema",
        }
        client = MockLLMClient(override_responses={"DIAGNOSIS REQUEST": invalid_payload})
        res = diagnose(case, client)

        self.assertIsInstance(res, DiagnosisResult)
        self.assertEqual(res.category, DiagnosisCategory.UNKNOWN)
        self.assertEqual(res.confidence, 0.0)
        self.assertIn("LLM_RESPONSE_INVALID_SCHEMA", res.reasoning)

    def test_strategy_pipeline_catches_invalid_schema(self):
        """Validly parsed JSON with invalid confidence string falls back to LLM_RESPONSE_INVALID_SCHEMA."""
        case = _make_test_case()
        diag = DiagnosisResult(
            case_id=case.case_id,
            root_cause="Network timeout",
            category=DiagnosisCategory.TRANSIENT_NETWORK,
            confidence=0.9,
            reasoning="Valid diagnosis",
        )
        invalid_strategy_payload = {
            "proposed_action": "RETRY_NOW",
            "confidence": "high",
            "reasoning": "Valid string",
            "risk_assessment": "LOW",
        }
        client = MockLLMClient(override_responses={"STRATEGY PROPOSAL REQUEST": invalid_strategy_payload})
        proposal = propose_strategy(case, diag, None, client)

        self.assertEqual(proposal.proposed_action, ActionType.ESCALATE_HUMAN)
        self.assertEqual(proposal.confidence, 0.0)
        self.assertIn("LLM_RESPONSE_INVALID_SCHEMA", proposal.reasoning)

    def test_full_orchestrator_pipeline_handles_invalid_schema_cleanly(self):
        """Full case processing with invalid schema JSON degrades gracefully without crashing."""
        case = _make_test_case("PAY-SCHEMA-PIPE-001")
        audit_log = AuditLog()
        memory = Memory()
        invalid_strategy_payload = {
            "proposed_action": "RETRY_NOW",
            "confidence": "high",
            "reasoning": "String instead of float confidence",
            "risk_assessment": "LOW",
        }
        client = MockLLMClient(override_responses={"STRATEGY PROPOSAL REQUEST": invalid_strategy_payload})

        outcome = process_case(case, audit_log, memory, llm_client=client)

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, "ESCALATED")
        self.assertEqual(outcome.final_action, ActionType.ESCALATE_HUMAN)
        self.assertIn("LLM_RESPONSE_INVALID_SCHEMA", outcome.strategy.reasoning)


if __name__ == "__main__":
    unittest.main()
