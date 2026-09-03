"""
Strategy Agent — Bounded Action Proposal for Failed Payments & B2B Receivables.

Proposes exactly one compliant action from the bounded menu based on:
- Diagnosis result (root cause and category)
- Relationship tier and engagement history
- Promise-to-pay track record (B2B)
- Case context (attempt/contact count, amount)
- Historical strategy success rates from Memory (as prompt context only)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Union

from agents.diagnosis import DiagnosisCategory, DiagnosisResult
from agents.llm_client import LLMClient
from core import config
from core.compliance import GateDecision
from core.schema_validation import SchemaValidationError, validate_strategy_output
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    Case,
    CaseType,
    FailedPaymentCase,
    RelationshipTier,
)

logger = logging.getLogger(__name__)

# Bounded menu of allowed actions across both scenarios
ALL_ACTION_MENU = [
    ActionType.RETRY_NOW,
    ActionType.RETRY_LATER,
    ActionType.SUGGEST_ALTERNATE_METHOD,
    ActionType.SEND_REMINDER,
    ActionType.OFFER_PAYMENT_PLAN,
    ActionType.ESCALATE_TONE,
    ActionType.WAIT,
    ActionType.ESCALATE_HUMAN,
    ActionType.STOP,
]

PAYMENT_ACTION_MENU = [
    ActionType.RETRY_NOW,
    ActionType.RETRY_LATER,
    ActionType.SUGGEST_ALTERNATE_METHOD,
    ActionType.WAIT,
    ActionType.ESCALATE_HUMAN,
    ActionType.STOP,
]

B2B_ACTION_MENU = [
    ActionType.SEND_REMINDER,
    ActionType.OFFER_PAYMENT_PLAN,
    ActionType.ESCALATE_TONE,
    ActionType.WAIT,
    ActionType.ESCALATE_HUMAN,
    ActionType.STOP,
]


@dataclass
class StrategyProposal:
    """Structured proposal from the Strategy Agent."""
    proposed_action: ActionType
    confidence: float
    reasoning: str
    risk_assessment: str = "LOW"  # "LOW" | "MEDIUM" | "HIGH"


STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "proposed_action": {
            "type": "string",
            "enum": [a.value for a in ALL_ACTION_MENU],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
        "risk_assessment": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
    },
    "required": ["proposed_action", "confidence", "reasoning", "risk_assessment"],
}


def _format_strategy_context(strategy_context: Optional[dict[Any, Any]]) -> str:
    """Format memory outcome statistics into prompt context."""
    if not strategy_context:
        return "No historical strategy statistics available (Cold Start default: 50% success rate)."

    lines = []
    for action, stats in strategy_context.items():
        act_name = action.value if hasattr(action, "value") else str(action)
        if getattr(stats, "cold_start", False):
            lines.append(f"- {act_name}: 50.0% expected success (Cold Start default, 0 prior samples)")
        else:
            lines.append(
                f"- {act_name}: {stats.success_rate * 100:.1f}% historical success "
                f"({stats.sample_count} samples)"
            )
    return "\n".join(lines)


def _build_strategy_prompt(
    case: Case,
    diagnosis: DiagnosisResult,
    strategy_context: Optional[dict[Any, Any]],
) -> str:
    tier_val = case.relationship_tier.value if case.relationship_tier else "MEDIUM"
    formatted_context = _format_strategy_context(strategy_context)

    if case.case_type == CaseType.FAILED_PAYMENT:
        pay_case: FailedPaymentCase = case  # type: ignore
        menu_desc = """Bounded Action Menu:
- RETRY_NOW: Immediate retry via alternate acquiring switch
- RETRY_LATER: Schedule retry after liquidity/maintenance delay (e.g. +6h)
- SUGGEST_ALTERNATE_METHOD: Send customer SMS/WhatsApp payment link for alternate method
- WAIT: Stand down temporarily without contact
- ESCALATE_HUMAN: Hand over case to human operations/risk queue
- STOP: Terminate all recovery activity permanently"""

        return f"""STRATEGY PROPOSAL REQUEST
Case ID: {pay_case.case_id}
Case Type: FAILED_PAYMENT
Amount: ₹{pay_case.amount:,.2f}
Failure Code: {pay_case.failure_code.value}
Attempt Count: {pay_case.attempt_count}
Relationship Tier: {tier_val}
Diagnosed Root Cause: {diagnosis.root_cause}
Diagnosed Category: {diagnosis.category.value}
Diagnosis Confidence: {diagnosis.confidence:.2f}

Historical Strategy Performance Context for category '{diagnosis.category.value}':
{formatted_context}

{menu_desc}

Task: Propose exactly ONE action from the menu above.
Provide confidence (0.0-1.0), reasoning, and risk assessment (LOW, MEDIUM, HIGH).
"""
    else:
        b2b_case: B2BReceivableCase = case  # type: ignore
        promise_info = "None"
        if b2b_case.promise_to_pay:
            p = b2b_case.promise_to_pay
            status_str = "ACTIVE" if p.kept is None else ("KEPT" if p.kept else "BROKEN")
            promise_info = f"Promised Date: {p.promised_date.strftime('%Y-%m-%d')}, Amount: ₹{p.promised_amount:,.2f}, Status: {status_str}"

        menu_desc = """Bounded Action Menu:
- SEND_REMINDER: Professional reminder with attached invoice copy
- OFFER_PAYMENT_PLAN: Propose structured milestone installment plan
- ESCALATE_TONE: Send firm formal notice of overdue obligation
- WAIT: Stand down for 48h cooling-off window
- ESCALATE_HUMAN: Route to human dispute resolution / collections queue
- STOP: Terminate recovery activity permanently"""

        return f"""STRATEGY PROPOSAL REQUEST
Case ID: {b2b_case.case_id}
Case Type: B2B_RECEIVABLE
Invoice ID: {b2b_case.invoice_id}
Amount: ₹{b2b_case.amount:,.2f}
Days Overdue: {b2b_case.days_overdue}
Attempt Count: {b2b_case.attempt_count}
Contact Count: {b2b_case.contact_count}
Relationship Tier: {tier_val}
Promise-to-Pay: {promise_info}
Diagnosed Root Cause: {diagnosis.root_cause}
Diagnosed Category: {diagnosis.category.value}
Diagnosis Confidence: {diagnosis.confidence:.2f}

Historical Strategy Performance Context for category '{diagnosis.category.value}':
{formatted_context}

{menu_desc}

Task: Propose exactly ONE action from the menu above.
Calibrate tone appropriately: gentle for High Tier, firm for missed commitments and Low Tier.
Provide confidence (0.0-1.0), reasoning, and risk assessment (LOW, MEDIUM, HIGH).
"""


def propose_strategy(
    case: Case,
    diagnosis: DiagnosisResult,
    strategy_context: Optional[dict[Any, Any]],
    llm_client: LLMClient,
) -> StrategyProposal:
    """Propose an action using the Strategy LLM agent."""
    prompt = _build_strategy_prompt(case, diagnosis, strategy_context)
    allowed_menu = PAYMENT_ACTION_MENU if case.case_type == CaseType.FAILED_PAYMENT else B2B_ACTION_MENU

    raw_response: Optional[dict[str, Any]] = None
    try:
        raw_response = llm_client.call(prompt, STRATEGY_SCHEMA)
    except Exception as e:
        is_timeout = isinstance(e, TimeoutError) or "timeout" in str(e).lower()
        if is_timeout:
            logger.error("Strategy LLM call timed out: %s. Escalating.", e)
            return StrategyProposal(
                proposed_action=ActionType.ESCALATE_HUMAN,
                confidence=0.0,
                reasoning=f"LLM_TIMEOUT: Upstream Strategy LLM call timed out: {e}",
                risk_assessment="HIGH",
            )
        logger.warning("Strategy LLM call attempt 1 failed: %s. Retrying...", e)
        retry_prompt = prompt + "\n\nCRITICAL: Respond ONLY with valid JSON conforming to the schema."
        try:
            raw_response = llm_client.call(retry_prompt, STRATEGY_SCHEMA)
        except Exception as retry_err:
            is_retry_timeout = isinstance(retry_err, TimeoutError) or "timeout" in str(retry_err).lower()
            if is_retry_timeout:
                return StrategyProposal(
                    proposed_action=ActionType.ESCALATE_HUMAN,
                    confidence=0.0,
                    reasoning=f"LLM_TIMEOUT: Upstream Strategy LLM retry timed out: {retry_err}",
                    risk_assessment="HIGH",
                )
            logger.error("Strategy LLM retry failed: %s. Falling back to ladder.", retry_err)
            return StrategyProposal(
                proposed_action=ActionType.ESCALATE_HUMAN,
                confidence=0.0,
                reasoning=f"LLM_RESPONSE_UNPARSEABLE: Automated fallback due to Strategy LLM error: {retry_err}",
                risk_assessment="LOW",
            )

    try:
        # validate_strategy_output raises SchemaValidationError (subclass of Exception)
        # if the response has wrong types, missing fields, or invalid enum values.
        # Caught by the enclosing except Exception below — same fallback path as unparseable JSON.
        validate_strategy_output(raw_response)
        act_str = raw_response.get("proposed_action", "ESCALATE_HUMAN")
        is_out_of_menu = False
        try:
            action = ActionType(act_str)
            if action not in allowed_menu:
                is_out_of_menu = True
        except ValueError:
            is_out_of_menu = True

        if is_out_of_menu:
            logger.warning(
                "Unrecognized or out-of-menu action '%s' rejected for %s. Escalating.",
                act_str, case.case_type.value,
            )
            return StrategyProposal(
                proposed_action=ActionType.ESCALATE_HUMAN,
                confidence=0.0,
                reasoning=(
                    f"INVALID_ACTION_REJECTED: Proposed action '{act_str}' is not permitted "
                    f"in the bounded action menu for {case.case_type.value}."
                ),
                risk_assessment="HIGH",
            )

        confidence = float(raw_response.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        risk = str(raw_response.get("risk_assessment", "MEDIUM")).upper()
        if risk not in {"LOW", "MEDIUM", "HIGH"}:
            risk = "MEDIUM"

        return StrategyProposal(
            proposed_action=action,
            confidence=confidence,
            reasoning=str(raw_response.get("reasoning", "No reasoning provided.")),
            risk_assessment=risk,
        )
    except SchemaValidationError as schema_err:
        logger.error("Strategy response schema invalid: %s. Falling back.", schema_err)
        return StrategyProposal(
            proposed_action=ActionType.ESCALATE_HUMAN,
            confidence=0.0,
            reasoning=f"LLM_RESPONSE_INVALID_SCHEMA: {schema_err}",
            risk_assessment="HIGH",
        )
    except Exception as parse_err:
        logger.error("Failed to parse strategy output: %s. Falling back.", parse_err)
        return StrategyProposal(
            proposed_action=ActionType.ESCALATE_HUMAN,
            confidence=0.0,
            reasoning=f"LLM_RESPONSE_UNPARSEABLE: Parsing error fallback: {parse_err}",
            risk_assessment="LOW",
        )


def apply_fallback_ladder(
    case: Case,
    proposal: StrategyProposal,
) -> StrategyProposal:
    """Apply the graceful degradation Fallback Ladder (Addendum §3)."""
    # 1. Payments Fallback Ladder
    if case.case_type == CaseType.FAILED_PAYMENT:
        if proposal.proposed_action in {ActionType.RETRY_NOW, ActionType.RETRY_LATER}:
            if case.relationship_tier == RelationshipTier.HIGH:
                return StrategyProposal(
                    proposed_action=ActionType.SUGGEST_ALTERNATE_METHOD,
                    confidence=proposal.confidence,
                    reasoning=(
                        f"Fallback Ladder: Action '{proposal.proposed_action.value}' confidence ({proposal.confidence:.2f}) "
                        f"< {config.CONFIDENCE_THRESHOLD}. Stepping down to safe alternate payment link."
                    ),
                    risk_assessment="LOW",
                )
            else:
                return StrategyProposal(
                    proposed_action=ActionType.ESCALATE_HUMAN,
                    confidence=proposal.confidence,
                    reasoning=f"Fallback Ladder: Confidence ({proposal.confidence:.2f}) < {config.CONFIDENCE_THRESHOLD}. Stepping down to human review.",
                    risk_assessment="LOW",
                )

        if proposal.proposed_action == ActionType.SUGGEST_ALTERNATE_METHOD:
            return StrategyProposal(
                proposed_action=ActionType.ESCALATE_HUMAN,
                confidence=proposal.confidence,
                reasoning="Fallback Ladder: Stepping down from alternate method to human review.",
                risk_assessment="LOW",
            )

    # 2. B2B Fallback Ladder
    else:
        if proposal.proposed_action == ActionType.ESCALATE_TONE:
            return StrategyProposal(
                proposed_action=ActionType.SEND_REMINDER,
                confidence=proposal.confidence,
                reasoning="Fallback Ladder: Stepping down from tone escalation to standard reminder.",
                risk_assessment="LOW",
            )
        if proposal.proposed_action in {ActionType.SEND_REMINDER, ActionType.OFFER_PAYMENT_PLAN}:
            return StrategyProposal(
                proposed_action=ActionType.ESCALATE_HUMAN,
                confidence=proposal.confidence,
                reasoning="Fallback Ladder: Low confidence / conflicting signals stepping down to human account manager.",
                risk_assessment="LOW",
            )

    return proposal


def re_propose_strategy(
    case: Case,
    diagnosis: DiagnosisResult,
    previous_proposal: StrategyProposal,
    gate_decision: GateDecision,
    llm_client: LLMClient,
) -> StrategyProposal:
    """Re-propose a strategy action given compliance gate rejection context."""
    rejection_reasons = "\n".join(f"- {r}" for r in gate_decision.violation_reasons)
    prompt = f"""RE-PROPOSAL REQUEST (Compliance Gate Feedback)
Case ID: {case.case_id}
Original Proposed Action: {previous_proposal.proposed_action.value}
Gate Decision: REJECTED
Violations:
{rejection_reasons}

Diagnosed Root Cause: {diagnosis.root_cause} ({diagnosis.category.value})

Task: The previous action was blocked by compliance rules.
Propose a DIFFERENT, compliant action from the menu.
"""
    allowed_menu = PAYMENT_ACTION_MENU if case.case_type == CaseType.FAILED_PAYMENT else B2B_ACTION_MENU

    try:
        response = llm_client.call(prompt, STRATEGY_SCHEMA)
        act_str = response.get("proposed_action", "ESCALATE_HUMAN")
        action = ActionType(act_str)
        if action == previous_proposal.proposed_action or action not in allowed_menu:
            action = ActionType.ESCALATE_HUMAN

        return StrategyProposal(
            proposed_action=action,
            confidence=float(response.get("confidence", 0.85)),
            reasoning=f"Re-proposed following gate rejection: {response.get('reasoning', '')}",
            risk_assessment=str(response.get("risk_assessment", "LOW")),
        )
    except Exception as e:
        logger.warning("Re-proposal LLM call failed: %s. Defaulting to ESCALATE_HUMAN.", e)
        return StrategyProposal(
            proposed_action=ActionType.ESCALATE_HUMAN,
            confidence=0.90,
            reasoning="Automated fallback to human queue after gate rejection.",
            risk_assessment="LOW",
        )
