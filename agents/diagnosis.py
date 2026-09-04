"""
Diagnosis Agent — Root Cause Diagnosis for Failed Payments & B2B Receivables.

Analyzes case context (failure codes, days overdue, debtor/customer history,
promise-keeping track record, attempt count, and relationship tier) to diagnose
the root cause of revenue at risk.
"""

from __future__ import annotations

from collections import Counter
import enum
import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from agents.llm_client import LLMClient, LLMError
from core.schema_validation import SchemaValidationError, validate_diagnosis_output
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


def _build_diagnosis_prompt(case: Case, perspective: str = "FACTUAL") -> str:
    tier_val = case.relationship_tier.value if case.relationship_tier else "MEDIUM"

    conflicts_text = "None"
    if getattr(case, "conflicting_signals", None):
        conflicts_text = "; ".join(
            f"{s.source_a} ('{s.signal_a}') vs {s.source_b} ('{s.signal_b}'): {s.description}"
            for s in case.conflicting_signals
        )

    if perspective == "COUNTER_INDICATOR":
        perspective_header = (
            "PERSPECTIVE: COUNTER_INDICATOR\n"
            "Examine all contextual counter-indicators, contradictory notes, support tickets, and disputes. "
            "If contradictory evidence exists, prioritize identifying underlying friction over the primary switch code.\n\n"
        )
    elif perspective == "CONSERVATIVE":
        perspective_header = (
            "PERSPECTIVE: CONSERVATIVE\n"
            "Evaluate from a strict compliance and relationship preservation perspective, avoiding optimistic retry assumptions.\n\n"
        )
    else:
        perspective_header = (
            "PERSPECTIVE: FACTUAL\n"
            "Evaluate strictly based on confirmed technical switch codes and direct evidence.\n\n"
        )

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

        return f"""{perspective_header}DIAGNOSIS REQUEST
Case ID: {pay_case.case_id}
Case Type: {pay_case.case_type.value}
Amount: ₹{pay_case.amount:,.2f}
Failure Code: {pay_case.failure_code.value}
Attempt Count: {pay_case.attempt_count}
Customer History: {hist_summary}
Relationship Tier: {tier_val}
Fraud Flag: {pay_case.fraud_flag}
Conflicting Signals: {conflicts_text}

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

        return f"""{perspective_header}DIAGNOSIS REQUEST
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
Conflicting Signals: {conflicts_text}

Task: Diagnose the root cause of this overdue B2B invoice.
Classify category into: CASH_FLOW_MISMATCH, ADMINISTRATIVE_DELAY, DISPUTED_DELIVERABLE, CHRONIC_DELINQUENCY, COMMUNICATION_BREAKDOWN, or UNKNOWN.
Provide confidence (0.0-1.0) and reasoning.
"""


def _diagnose_single_sample(case: Case, prompt: str, llm_client: LLMClient) -> DiagnosisResult:
    """Execute a single diagnosis LLM call with retry and schema validation."""
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

    try:
        validate_diagnosis_output(raw_response)
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
    except SchemaValidationError as schema_err:
        logger.error("Diagnosis response schema invalid: %s. Falling back.", schema_err)
        return DiagnosisResult(
            case_id=case.case_id,
            root_cause="Diagnosis response schema validation failed.",
            category=DiagnosisCategory.UNKNOWN,
            confidence=0.0,
            reasoning=f"LLM_RESPONSE_INVALID_SCHEMA: {schema_err}",
        )
    except Exception as parse_err:
        logger.error("Failed to parse diagnosis output: %s. Falling back.", parse_err)
        return DiagnosisResult(
            case_id=case.case_id,
            root_cause="Diagnosis response parsing error.",
            category=DiagnosisCategory.UNKNOWN,
            confidence=0.0,
            reasoning=f"LLM_RESPONSE_UNPARSEABLE: Parsing error: {parse_err}",
        )


def diagnose(case: Case, llm_client: LLMClient, num_samples: int = 3) -> DiagnosisResult:
    """Diagnose the root cause with multi-sample self-consistency voting (Prompt 6).

    Executes num_samples perspective passes:
    - 3/3 unanimous: full consensus confidence preserved.
    - 2/3 majority: majority category adopted, but confidence is strictly capped at 0.80
      (< 0.85 threshold) because a dissenting vote indicates uncertainty requiring human oversight.
    - 1/1/1 split: category set to UNKNOWN, confidence = 0.50, and reasoning flagged with
      SELF_CONSISTENCY_DISAGREEMENT.
    """
    if num_samples <= 1:
        prompt = _build_diagnosis_prompt(case, perspective="FACTUAL")
        return _diagnose_single_sample(case, prompt, llm_client)

    perspectives = ["FACTUAL", "COUNTER_INDICATOR", "CONSERVATIVE"][:num_samples]
    samples: list[DiagnosisResult] = []
    for p in perspectives:
        prompt = _build_diagnosis_prompt(case, perspective=p)
        samples.append(_diagnose_single_sample(case, prompt, llm_client))

    # Voting & Consensus Logic
    cat_counts = Counter(s.category for s in samples)
    top_cat, top_count = cat_counts.most_common(1)[0]

    # 1. Unanimous Consensus (3/3)
    if top_count == 3:
        avg_conf = sum(s.confidence for s in samples) / 3.0
        return DiagnosisResult(
            case_id=case.case_id,
            root_cause=samples[0].root_cause,
            category=top_cat,
            confidence=round(avg_conf, 2),
            reasoning=f"Consensus diagnosis (3/3 agreement across perspectives): {samples[0].reasoning}",
        )

    # 2. Majority Consensus (2/3) — strictly capped at 0.80 due to dissenting vote
    if top_count == 2:
        agreeing = [s for s in samples if s.category == top_cat]
        dissenting = next(s for s in samples if s.category != top_cat)
        mean_conf = sum(s.confidence for s in agreeing) / len(agreeing)
        # Cap strictly at 0.80 (< 0.85 threshold) to guarantee dissenting votes cannot auto-execute
        calibrated_conf = round(min(mean_conf * 0.90, 0.80), 2)
        return DiagnosisResult(
            case_id=case.case_id,
            root_cause=agreeing[0].root_cause,
            category=top_cat,
            confidence=calibrated_conf,
            reasoning=(
                f"Majority diagnosis (2/3 agreement on {top_cat.value}, dissenting: {dissenting.category.value}). "
                f"Confidence calibrated to {calibrated_conf:.2f} (strictly capped at 0.80 due to dissenting sample)."
            ),
        )

    # 3. Split Disagreement (1/1/1 or tie) — route to UNKNOWN at 0.50
    split_details = ", ".join(f"{s.category.value} ({s.confidence:.2f})" for s in samples)
    return DiagnosisResult(
        case_id=case.case_id,
        root_cause="Diagnostic self-consistency disagreement across independent perspective samples.",
        category=DiagnosisCategory.UNKNOWN,
        confidence=0.50,
        reasoning=f"SELF_CONSISTENCY_DISAGREEMENT: Conflicting diagnostic outputs across 3 samples: {split_details}.",
    )

