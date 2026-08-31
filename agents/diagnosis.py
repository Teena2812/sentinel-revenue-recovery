"""
Diagnosis Agent — Root Cause Diagnosis for Failed Payments & B2B Receivables.

Analyzes case context (failure codes, days overdue, debtor/customer history,
promise-keeping track record, attempt count, and relationship tier) to diagnose
the root cause of revenue at risk.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from agents.llm_client import LLMClient, LLMError
from core.schemas import B2BReceivableCase, Case, CaseType, CustomerHistory, FailedPaymentCase

logger = logging.getLogger(__name__)


class DiagnosisCategory(str, enum.Enum):
    """Standardized root-cause categories for Payments and B2B Receivables."""
    # Failed Payment categories
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"   # Bank timeout, switch drop, gateway hiccup
    FUNDS_UNAVAILABLE = "FUNDS_UNAVAILABLE"   # Balance shortage, daily limit exceeded
    AUTH_EXPIRED = "AUTH_EXPIRED"             # 2FA timeout, expired token/mandate
    SYSTEMIC_RISK = "SYSTEMIC_RISK"           # Fraud alert, chargeback velocity

    # B2B Receivable categories
    CASH_FLOW_MISMATCH = "CASH_FLOW_MISMATCH"         # Liquidity timing mismatch
    ADMINISTRATIVE_DELAY = "ADMINISTRATIVE_DELAY"     # AP approval lag, lost invoice
    DISPUTED_DELIVERABLE = "DISPUTED_DELIVERABLE"     # Commercial / invoice dispute
    CHRONIC_DELINQUENCY = "CHRONIC_DELINQUENCY"       # Habitual late payer
    COMMUNICATION_BREAKDOWN = "COMMUNICATION_BREAKDOWN" # Ghosting / unresponsive

    # Universal Fallback
    UNKNOWN = "UNKNOWN"                       # Unparseable / inconclusive diagnosis


@dataclass
class DiagnosisResult:
    """Structured output from the Diagnosis Agent."""
    case_id: str
    root_cause: str
    category: DiagnosisCategory
    confidence: float
    reasoning: str


DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "category": {
            "type": "string",
            "enum": [c.value for c in DiagnosisCategory],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
    },
    "required": ["root_cause", "category", "confidence", "reasoning"],
}


def _build_diagnosis_prompt(case: Case) -> str:
    tier_val = case.relationship_tier.value if case.relationship_tier else "MEDIUM"

    if case.case_type == CaseType.FAILED_PAYMENT:
        pay_case: FailedPaymentCase = case  # type: ignore
        cust_hist = pay_case.customer_history
        hist_summary = "No prior history"
        if cust_hist.has_history:
            hist_summary = (
                f"Reliability: {cust_hist.reliability_ratio:.2f}, "
                f"Transactions: {cust_hist.total_transactions}, "
                f"Total volume: ₹{cust_hist.total_amount:,.2f}"
            )

        return f"""DIAGNOSIS REQUEST
Case ID: {pay_case.case_id}
Case Type: {pay_case.case_type.value}
Amount: ₹{pay_case.amount:,.2f}
Failure Code: {pay_case.failure_code.value}
Attempt Count: {pay_case.attempt_count}
Customer History: {hist_summary}
Relationship Tier: {tier_val}
Fraud Flag: {pay_case.fraud_flag}

Task: Diagnose the root cause of this failed payment.
Classify category into: TRANSIENT_NETWORK, FUNDS_UNAVAILABLE, AUTH_EXPIRED, SYSTEMIC_RISK, or UNKNOWN.
Provide confidence (0.0-1.0) and reasoning.
"""
    else:
        b2b_case: B2BReceivableCase = case  # type: ignore
        debtor_hist: CustomerHistory = b2b_case.debtor_history
        hist_summary = "No prior history"
        if debtor_hist.has_history:
            hist_summary = (
                f"Reliability: {debtor_hist.reliability_ratio:.2f}, "
                f"Transactions: {debtor_hist.total_transactions}, "
                f"Total volume: ₹{debtor_hist.total_amount:,.2f}"
            )

        promise_info = "None"
        if b2b_case.promise_to_pay:
            p = b2b_case.promise_to_pay
            status_str = "ACTIVE" if p.kept is None else ("KEPT" if p.kept else "BROKEN")
            promise_info = f"Promised Date: {p.promised_date.strftime('%Y-%m-%d')}, Amount: ₹{p.promised_amount:,.2f}, Status: {status_str}"

        return f"""DIAGNOSIS REQUEST
Case ID: {b2b_case.case_id}
Case Type: {b2b_case.case_type.value}
Invoice ID: {b2b_case.invoice_id}
Amount: ₹{b2b_case.amount:,.2f}
Days Overdue: {b2b_case.days_overdue}
Attempt Count: {b2b_case.attempt_count}
Contact Count: {b2b_case.contact_count}
Debtor History: {hist_summary}
Relationship Tier: {tier_val}
Dispute Flag: {b2b_case.dispute_flag}
Fraud Flag: {b2b_case.fraud_flag}
Promise-to-Pay: {promise_info}

Task: Diagnose the root cause of this overdue B2B invoice.
Classify category into: CASH_FLOW_MISMATCH, ADMINISTRATIVE_DELAY, DISPUTED_DELIVERABLE, CHRONIC_DELINQUENCY, COMMUNICATION_BREAKDOWN, or UNKNOWN.
Provide confidence (0.0-1.0) and reasoning.
"""


def diagnose(case: Case, llm_client: LLMClient) -> DiagnosisResult:
    """Diagnose the root cause of a failed payment or B2B receivable case."""
    prompt = _build_diagnosis_prompt(case)

    # Attempt 1
    raw_response: Optional[dict[str, Any]] = None
    try:
        raw_response = llm_client.call(prompt, DIAGNOSIS_SCHEMA)
    except Exception as e:
        logger.warning("Diagnosis LLM call attempt 1 failed: %s. Retrying...", e)
        retry_prompt = prompt + "\n\nCRITICAL: Ensure output strictly matches the specified JSON schema."
        try:
            raw_response = llm_client.call(retry_prompt, DIAGNOSIS_SCHEMA)
        except Exception as retry_err:
            logger.error("Diagnosis LLM retry also failed: %s. Falling back to UNKNOWN.", retry_err)
            return DiagnosisResult(
                case_id=case.case_id,
                root_cause="LLM diagnosis call failed or returned malformed content.",
                category=DiagnosisCategory.UNKNOWN,
                confidence=0.0,
                reasoning="Automated fallback due to LLM invocation/parsing failure.",
            )

    # Parse and validate schema fields
    try:
        cat_str = raw_response.get("category", "UNKNOWN")
        try:
            category = DiagnosisCategory(cat_str)
        except ValueError:
            category = DiagnosisCategory.UNKNOWN

        confidence = float(raw_response.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        return DiagnosisResult(
            case_id=case.case_id,
            root_cause=str(raw_response.get("root_cause", "Unspecified root cause.")),
            category=category,
            confidence=confidence,
            reasoning=str(raw_response.get("reasoning", "No reasoning provided.")),
        )
    except Exception as parse_err:
        logger.error("Failed to parse diagnosis output: %s. Falling back.", parse_err)
        return DiagnosisResult(
            case_id=case.case_id,
            root_cause="Diagnosis response parsing error.",
            category=DiagnosisCategory.UNKNOWN,
            confidence=0.0,
            reasoning=f"Parsing error: {parse_err}",
        )
