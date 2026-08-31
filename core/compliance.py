"""
Compliance Rules — Deterministic Gate Checks.

Every check here is plain, testable Python code — NEVER an LLM judgment call.
(Master prompt §4, rule 1: "If you find yourself asking an LLM 'should we
allow this action,' stop — that logic belongs in the gate, not the prompt.")

Each rule returns a ComplianceResult with (passed, rule_name, reason).
run_all_checks() aggregates all applicable rules for a given case and
proposed action.

Grounded in RBI Fair Practices Code principles:
- Contact only within reasonable hours (8 AM – 7 PM IST)
- No contact/action on disputed cases (debtor's right to dispute)
- No retry/contact on fraud-flagged cases (hard stop)
- Attempt/contact caps to prevent excessive outreach
- Cost-threshold gate for economically unviable recovery
- Idempotency: never execute the same action twice for the same case+attempt

ALL DATA IS SIMULATED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Union

from core import config
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    Case,
    CaseType,
    FailedPaymentCase,
)

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    """Result of a single compliance check."""
    passed: bool
    rule_name: str
    reason: str


@dataclass
class GateDecision:
    """Aggregated result of all compliance checks for one proposed action."""
    approved: bool
    results: list[ComplianceResult]
    case_id: str
    proposed_action: str
    timestamp: str

    @property
    def violations(self) -> list[ComplianceResult]:
        return [r for r in self.results if not r.passed]

    @property
    def violation_reasons(self) -> list[str]:
        return [r.reason for r in self.violations]


# ---------------------------------------------------------------------------
# Individual Rule Checks
# ---------------------------------------------------------------------------

def check_contact_hours(current_time: Optional[datetime] = None) -> ComplianceResult:
    """RBI Fair Practices: contact only between 8 AM and 7 PM IST.

    Actions that don't involve outbound contact (e.g. RETRY_NOW for a payment,
    WAIT, STOP) are exempt — this check should only be applied to contact actions.
    The caller (run_all_checks) handles that gating.
    """
    if current_time is None:
        current_time = getattr(config, "SIMULATED_CURRENT_TIME", None) or datetime.now()

    hour = current_time.hour
    if config.CONTACT_HOUR_START <= hour < config.CONTACT_HOUR_END:
        return ComplianceResult(
            passed=True,
            rule_name="contact_hours",
            reason=f"Current hour {hour}:00 is within permitted window "
                   f"({config.CONTACT_HOUR_START}:00–{config.CONTACT_HOUR_END}:00).",
        )
    return ComplianceResult(
        passed=False,
        rule_name="contact_hours",
        reason=f"BLOCKED: Current hour {hour}:00 is outside permitted contact window "
               f"({config.CONTACT_HOUR_START}:00–{config.CONTACT_HOUR_END}:00 IST). "
               f"RBI Fair Practices Code prohibits contact outside reasonable hours.",
    )


def check_attempt_cap(case: Case) -> ComplianceResult:
    """Enforce maximum attempt/retry count."""
    if case.case_type == CaseType.FAILED_PAYMENT:
        max_attempts = config.MAX_ATTEMPTS_PAYMENT
    else:
        max_attempts = config.MAX_ATTEMPTS_B2B

    if case.attempt_count < max_attempts:
        return ComplianceResult(
            passed=True,
            rule_name="attempt_cap",
            reason=f"Attempt {case.attempt_count + 1} of {max_attempts} — within limit.",
        )
    return ComplianceResult(
        passed=False,
        rule_name="attempt_cap",
        reason=f"BLOCKED: Attempt count ({case.attempt_count}) has reached the "
               f"maximum ({max_attempts}) for {case.case_type.value} cases. "
               f"No further automated attempts permitted.",
    )


# Actions that are always permitted on flagged cases — these are the system's
# correct responses to fraud/dispute, not recovery actions against the customer.
SAFE_ACTIONS_FOR_FLAGGED_CASES = {ActionType.STOP, ActionType.ESCALATE_HUMAN}


def check_dispute_stop(case: Case, proposed_action: ActionType) -> ComplianceResult:
    """Hard stop: no recovery action on a disputed case.
    RBI Fair Practices: debtor's right to dispute must be honored immediately.

    STOP and ESCALATE_HUMAN are always permitted — they are the system's correct
    response to a dispute (stop recovery / route to human), not actions against
    the customer.
    """
    dispute_flag = getattr(case, "dispute_flag", False)
    if not dispute_flag:
        return ComplianceResult(
            passed=True,
            rule_name="dispute_stop",
            reason="No active dispute on this case.",
        )
    if proposed_action in SAFE_ACTIONS_FOR_FLAGGED_CASES:
        return ComplianceResult(
            passed=True,
            rule_name="dispute_stop",
            reason=f"Active dispute on this case, but proposed action "
                   f"'{proposed_action.value}' is a safe response to a dispute "
                   f"(not a recovery action). Permitted.",
        )
    return ComplianceResult(
        passed=False,
        rule_name="dispute_stop",
        reason="HARD STOP: Active dispute flag on this case. All automated "
               "recovery actions are halted. Case must be routed to human "
               "dispute resolution queue. (RBI Fair Practices: debtor's right "
               "to dispute)",
    )


def check_fraud_stop(case: Case, proposed_action: ActionType) -> ComplianceResult:
    """Hard stop: no retry or contact on a fraud-flagged case.
    Fraud cases are routed to risk/security, not recovered by this system.

    STOP and ESCALATE_HUMAN are always permitted — they are the system's correct
    response to a fraud flag (stop / route to risk queue), not recovery actions.
    """
    if not case.fraud_flag:
        return ComplianceResult(
            passed=True,
            rule_name="fraud_stop",
            reason="No fraud flag on this case.",
        )
    if proposed_action in SAFE_ACTIONS_FOR_FLAGGED_CASES:
        return ComplianceResult(
            passed=True,
            rule_name="fraud_stop",
            reason=f"Fraud flag on this case, but proposed action "
                   f"'{proposed_action.value}' is a safe response to fraud "
                   f"(not a recovery action). Permitted.",
        )
    return ComplianceResult(
        passed=False,
        rule_name="fraud_stop",
        reason="HARD STOP: Fraud/risk flag on this case. No retry, no contact. "
               "Case must be flagged to human risk queue. Retrying a fraud-flagged "
               "transaction could constitute a compliance violation.",
    )


def check_cost_threshold(case: Case) -> ComplianceResult:
    """Check whether a case is below the cost-effectiveness threshold.
    Uses separate thresholds for payment vs B2B (₹500 vs ₹5,000).

    IMPORTANT — architectural note:
    This check is deliberately NOT part of run_all_checks() / the Gate.
    The Gate runs AFTER the LLM-based Diagnosis and Strategy agents have
    already been called. If we checked cost threshold inside the Gate, we'd
    have already paid for the LLM calls the threshold was supposed to help
    us avoid — and we'd permanently block every action on every low-value
    case (since amount never changes), which is the opposite of "use a
    cheap automatic path."

    Instead, the Phase 2 orchestrator calls should_use_cheap_path() BEFORE
    invoking Diagnosis/Strategy. Below threshold → skip straight to a cheap
    deterministic action, no LLM call. Above threshold → proceed through
    the normal pipeline and then through the Gate (which no longer includes
    this check).
    """
    if case.case_type == CaseType.FAILED_PAYMENT:
        threshold = config.MIN_RECOVERY_AMOUNT_PAYMENT
    else:
        threshold = config.MIN_RECOVERY_AMOUNT_B2B

    if case.amount >= threshold:
        return ComplianceResult(
            passed=True,
            rule_name="cost_threshold",
            reason=f"Case amount ₹{case.amount:,.2f} is above the "
                   f"{case.case_type.value} threshold (₹{threshold:,.2f}). "
                   f"Full pipeline permitted.",
        )
    return ComplianceResult(
        passed=False,
        rule_name="cost_threshold",
        reason=f"COST GATE: Case amount ₹{case.amount:,.2f} is below the "
               f"{case.case_type.value} recovery threshold (₹{threshold:,.2f}). "
               f"Use cheap automatic path only — skip full LLM diagnosis pipeline "
               f"to avoid spending more on recovery than the case is worth.",
    )


@dataclass
class SkipResult:
    """Result of the pre-pipeline skip check."""
    should_skip: bool
    action: ActionType
    reason: str
    skip_type: str  # "fraud" | "dispute" | "cost_threshold"


def should_skip_pipeline(case: Case) -> SkipResult | None:
    """Pre-pipeline check: should this case skip Diagnosis/Strategy?

    Checks in strict priority order before spending LLM tokens:
    1. Fraud flag -> STOP (hard stop, outcome is predetermined)
    2. Dispute flag -> ESCALATE_HUMAN (route to dispute queue, outcome predetermined)
    3. Cost threshold -> cheap deterministic action (e.g., RETRY_NOW for payments)

    Returns SkipResult if the case should skip LLM pipeline, or None if full pipeline is required.
    NOTE: All actions returned still pass through run_all_checks() before executing.
    """
    # 1. Fraud flag hard-stop
    if getattr(case, "fraud_flag", False):
        return SkipResult(
            should_skip=True,
            action=ActionType.STOP,
            reason="Fraud flag detected — skipping LLM pipeline for immediate STOP hard-stop.",
            skip_type="fraud",
        )

    # 2. Dispute flag routing
    if getattr(case, "dispute_flag", False):
        return SkipResult(
            should_skip=True,
            action=ActionType.ESCALATE_HUMAN,
            reason="Active dispute flag detected — skipping LLM pipeline for immediate dispute queue routing.",
            skip_type="dispute",
        )

    # 3. Active unexpired promise-to-pay (kept is None)
    promise = getattr(case, "promise_to_pay", None)
    if promise is not None and getattr(promise, "kept", None) is None:
        return SkipResult(
            should_skip=True,
            action=ActionType.WAIT,
            reason="Active unexpired promise-to-pay exists (kept=None). Stand down contact to respect debtor commitment.",
            skip_type="active_promise",
        )

    # 4. Cost threshold check
    cost_res = check_cost_threshold(case)
    if not cost_res.passed:
        # For payments below ₹500, cheap deterministic action is RETRY_NOW; for B2B it's SEND_REMINDER
        cheap_action = ActionType.RETRY_NOW if case.case_type == CaseType.FAILED_PAYMENT else ActionType.SEND_REMINDER
        return SkipResult(
            should_skip=True,
            action=cheap_action,
            reason=cost_res.reason,
            skip_type="cost_threshold",
        )

    return None


def should_use_cheap_path(case: Case) -> tuple[bool, str]:
    """Backward-compatible helper for cost threshold check only."""
    result = check_cost_threshold(case)
    if not result.passed:
        return True, result.reason
    return False, result.reason


def check_idempotency(
    case: Case,
    execution_log: dict[str, dict],
) -> ComplianceResult:
    """Prevent duplicate execution of the same action for the same case+attempt.

    execution_log: a dict keyed by idempotency_key, where each value contains
    at minimum {"status": "SUCCESS" | "FAILED" | "PENDING", ...}.

    A prior successful execution for this key blocks re-execution.
    """
    key = case.idempotency_key
    prior = execution_log.get(key)

    if prior is None:
        return ComplianceResult(
            passed=True,
            rule_name="idempotency",
            reason=f"No prior execution found for key '{key}'. Safe to proceed.",
        )

    if prior.get("status") == "SUCCESS":
        return ComplianceResult(
            passed=False,
            rule_name="idempotency",
            reason=f"BLOCKED: Action already successfully executed for key '{key}'. "
                   f"Re-execution would risk a duplicate action (e.g. double-charge). "
                   f"Prior execution: {prior}",
        )

    # Prior execution exists but was not successful — allow retry
    return ComplianceResult(
        passed=True,
        rule_name="idempotency",
        reason=f"Prior execution for key '{key}' exists but status is "
               f"'{prior.get('status')}' — retry is permitted.",
    )


# ---------------------------------------------------------------------------
# Contact-requiring actions (for contact-hours check applicability)
# ---------------------------------------------------------------------------

CONTACT_ACTIONS = {
    ActionType.SEND_REMINDER,
    ActionType.OFFER_PAYMENT_PLAN,
    ActionType.ESCALATE_TONE,
    ActionType.SUGGEST_ALTERNATE_METHOD,
    # NOTE: ESCALATE_HUMAN is intentionally NOT here. Escalating to a human
    # queue is internal routing, not outbound customer contact. You should be
    # able to escalate a case at 2 AM; you just shouldn't be able to message
    # the debtor at 2 AM.
}


# ---------------------------------------------------------------------------
# Aggregate Gate Check
# ---------------------------------------------------------------------------

def run_all_checks(
    case: Case,
    proposed_action: ActionType,
    execution_log: dict[str, dict],
    current_time: Optional[datetime] = None,
) -> GateDecision:
    """Run every applicable compliance check for a proposed action.

    This is the Deterministic Gate — the single most important architectural
    rule in the project. It is plain code, never an LLM call.

    Returns a GateDecision with all results and an overall approved/rejected verdict.
    """
    results: list[ComplianceResult] = []

    # 1. Fraud hard stop (always checked — but STOP/ESCALATE_HUMAN pass through)
    results.append(check_fraud_stop(case, proposed_action))

    # 2. Dispute hard stop (always checked — but STOP/ESCALATE_HUMAN pass through)
    results.append(check_dispute_stop(case, proposed_action))

    # 3. Attempt cap (for actions that increment attempts)
    if proposed_action not in {ActionType.WAIT, ActionType.STOP, ActionType.ESCALATE_HUMAN}:
        results.append(check_attempt_cap(case))

    # 4. Contact hours (only for outbound-contact actions)
    if proposed_action in CONTACT_ACTIONS:
        results.append(check_contact_hours(current_time))

    # 5. Idempotency (always checked)
    #
    # NOTE: check_cost_threshold is deliberately NOT here. It runs pre-pipeline
    # in the orchestrator via should_use_cheap_path(), before any LLM calls.
    # Including it here would (a) fire after we've already paid for the LLM
    # calls it's supposed to avoid, and (b) permanently block every action on
    # low-value cases since amount never changes. See check_cost_threshold's
    # docstring for the full rationale.
    results.append(check_idempotency(case, execution_log))

    approved = all(r.passed for r in results)

    decision = GateDecision(
        approved=approved,
        results=results,
        case_id=case.case_id,
        proposed_action=proposed_action.value,
        timestamp=datetime.now().isoformat(),
    )

    if not approved:
        logger.warning(
            "Gate REJECTED action '%s' for case %s. Violations: %s",
            proposed_action.value,
            case.case_id,
            decision.violation_reasons,
        )
    else:
        logger.info(
            "Gate APPROVED action '%s' for case %s.",
            proposed_action.value,
            case.case_id,
        )

    return decision
