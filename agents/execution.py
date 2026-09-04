"""
Execution Agent — Simulated Tool Execution for Payments & B2B Receivables.

Carries out recovery and routing actions safely against simulated payment and invoice systems.
Enforces defense-in-depth idempotency verification before execution and records
the execution result into the immutable Audit Log.

IMPORTANT FAIRNESS CONSTRAINTS:
1. Ground-Truth Parity:
   - Payments: Directly uses `config.PAYMENT_RETRY_SUCCESS_PROB`.
   - B2B Receivables: Directly uses `config.B2B_REMINDER_SUCCESS_PROB` by days-overdue bucket.
2. Zero Ground-Truth Bonus: No bonus modifiers are imported or applied in simulation.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core import config
from core.audit_log import AuditLog, ExecutionEntry
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    Case,
    CaseType,
    FailedPaymentCase,
)

logger = logging.getLogger(__name__)

# Independent seeded random instance ensuring deterministic batch execution
_rng = random.Random(42)


@dataclass
class ExecutionResult:
    """Outcome of an executed recovery or routing action."""
    status: str          # "SUCCESS", "FAILED", "STOPPED", "ESCALATED", "WAITING"
    idempotency_key: str
    detail: str
    delay_hours: float = 0.0  # Simulated delay (6h for RETRY_LATER)
    resolution_time: Optional[float] = None  # Exact simulated resolution time if recovered


class ToolExecutionError(Exception):
    """Raised when a simulated downstream API or gateway tool call fails."""
    pass


def _get_b2b_overdue_bucket(days_overdue: int) -> str:
    """Map days overdue to the probability bucket (identical to baseline.py)."""
    if days_overdue <= 10:
        return "early"
    elif days_overdue <= 30:
        return "mid"
    elif days_overdue <= 60:
        return "late"
    return "stale"


def execute(
    case: Case,
    action: ActionType,
    audit_log: AuditLog,
    simulate_tool_error: Optional[str] = None,  # "transient" or "persistent" for resilience testing
    rng: Optional[random.Random] = None,
) -> ExecutionResult:
    """Execute the proposed action against simulated payment/invoice rails.

    Defense-in-depth:
    1. Checks idempotency log before firing.
    2. Tool Resilience: Catches transient API/gateway exceptions, retries once,
       and falls back gracefully with audit logging if persistent.
    3. Isolated RNG: Accepts an explicit Random instance to guarantee deterministic
       and isolated simulation outcomes across tests and benchmark runs.
    """
    active_rng = rng if rng is not None else _rng
    idempotency_key = case.idempotency_key
    exec_log = audit_log.get_execution_log()

    # Pre-execution idempotency check (defense-in-depth)
    prior_exec = exec_log.get(idempotency_key)
    if prior_exec and prior_exec.get("status") == "SUCCESS":
        logger.warning("Idempotency guard: %s was already executed successfully.", idempotency_key)
        return ExecutionResult(
            status="FAILED",
            idempotency_key=idempotency_key,
            detail=f"Blocked duplicate execution for key {idempotency_key}.",
        )

    # Tool-Failure Resilience: Simulated 2 AM gateway / API outage handling
    if simulate_tool_error:
        logger.warning("Simulated tool failure triggered (%s) on action %s for case %s.", simulate_tool_error, action.value, case.case_id)
        # Attempt 1: Transient failure
        logger.warning("Tool dispatch failed on attempt 1/2: Downstream API 503/timeout. Retrying tool call...")
        if simulate_tool_error == "persistent":
            # Retry also fails -> Fallback and audit
            detail = f"Tool execution failed after 1 retry: Upstream Gateway 503 Service Unavailable (2 AM resilience fallback)."
            audit_log.record_execution(ExecutionEntry(
                case_id=case.case_id,
                idempotency_key=idempotency_key,
                timestamp=datetime.now().isoformat(),
                action=action.value,
                status="FAILED",
                result_detail=detail,
            ))
            return ExecutionResult(
                status="FAILED",
                idempotency_key=idempotency_key,
                detail=detail,
            )
        else:
            logger.info("Tool retry succeeded on attempt 2/2. Proceeding with simulated execution.")

    now_str = datetime.now().isoformat()

    # 1. Non-recovery routing actions (No coin flip)
    if action == ActionType.STOP:
        detail = "Case permanently stopped. All recovery activity terminated."
        res = ExecutionResult(status="STOPPED", idempotency_key=idempotency_key, detail=detail)
        audit_log.record_execution(ExecutionEntry(
            case_id=case.case_id,
            idempotency_key=idempotency_key,
            timestamp=now_str,
            action=action.value,
            status="STOPPED",
            result_detail=detail,
        ))
        return res

    if action == ActionType.ESCALATE_HUMAN:
        detail = "Case escalated and routed to human operations / dispute queue."
        res = ExecutionResult(status="ESCALATED", idempotency_key=idempotency_key, detail=detail)
        audit_log.record_execution(ExecutionEntry(
            case_id=case.case_id,
            idempotency_key=idempotency_key,
            timestamp=now_str,
            action=action.value,
            status="ESCALATED",
            result_detail=detail,
        ))
        return res

    if action == ActionType.WAIT:
        detail = "Stand-down cooling off period applied."
        res = ExecutionResult(status="WAITING", idempotency_key=idempotency_key, detail=detail)
        audit_log.record_execution(ExecutionEntry(
            case_id=case.case_id,
            idempotency_key=idempotency_key,
            timestamp=now_str,
            action=action.value,
            status="WAITING",
            result_detail=detail,
        ))
        return res

    # 2. Recovery Actions (Coin flip using shared probability tables)
    if case.case_type == CaseType.FAILED_PAYMENT:
        pay_case: FailedPaymentCase = case  # type: ignore
        base_prob = config.PAYMENT_RETRY_SUCCESS_PROB.get(pay_case.failure_code.value, 0.20)
        delay_hours = float(config.RETRY_LATER_DELAY_HOURS) if action == ActionType.RETRY_LATER else 0.0

        is_success = active_rng.random() < base_prob
        status = "SUCCESS" if is_success else "FAILED"
        detail = f"Simulated {action.value} on {pay_case.failure_code.value} (prob={base_prob:.2f}): {status} (delay={delay_hours}h)"

        res_time = ((pay_case.attempt_count * 4.0) + delay_hours) if is_success else None

        audit_log.record_execution(ExecutionEntry(
            case_id=case.case_id,
            idempotency_key=idempotency_key,
            timestamp=now_str,
            action=action.value,
            status=status,
            result_detail=detail,
        ))

        return ExecutionResult(
            status=status,
            idempotency_key=idempotency_key,
            detail=detail,
            delay_hours=delay_hours,
            resolution_time=res_time,
        )

    else:
        b2b_case: B2BReceivableCase = case  # type: ignore
        bucket = _get_b2b_overdue_bucket(b2b_case.days_overdue)
        base_prob = config.B2B_REMINDER_SUCCESS_PROB.get(bucket, 0.08)

        is_success = active_rng.random() < base_prob
        status = "SUCCESS" if is_success else "FAILED"
        detail = f"Simulated B2B {action.value} on overdue bucket '{bucket}' (prob={base_prob:.2f}): {status}"

        res_time = min(b2b_case.days_overdue, active_rng.randint(3, 21)) if is_success else None

        audit_log.record_execution(ExecutionEntry(
            case_id=case.case_id,
            idempotency_key=idempotency_key,
            timestamp=now_str,
            action=action.value,
            status=status,
            result_detail=detail,
        ))

        return ExecutionResult(
            status=status,
            idempotency_key=idempotency_key,
            detail=detail,
            delay_hours=0.0,
            resolution_time=res_time,
        )
