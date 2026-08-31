"""
Orchestrator — Core Loop Coordinator for AI Revenue Recovery.

Coordinates the end-to-end reasoning and execution pipeline:
1. Pre-pipeline skip check (fraud, dispute, active promise, cost threshold)
2. Diagnosis Agent (root cause analysis via LLM)
3. Strategy Agent (bounded action selection informed by Memory context)
4. Confidence Gate & Fallback Ladder (graceful degradation below 0.85)
5. Deterministic Compliance Gate (hard RBI rule enforcement)
6. Execution Agent (simulated tool execution with shared probability tables)
7. Adaptive Memory Update (double-gated outcome recording)
8. Immutable Audit Trail (every decision recorded in AuditLog)

Symmetric Attempt Budget:
- Payments: Bounded multi-attempt loop up to AGENT_LOOP_MAX_ATTEMPTS = 3.
- B2B Receivables: Bounded multi-touch loop up to AGENT_LOOP_MAX_ATTEMPTS = 3 (day 7/14/21).
"""

from __future__ import annotations

import csv
import logging
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional, Union

from agents.diagnosis import DiagnosisCategory, DiagnosisResult, diagnose
from agents.execution import ExecutionResult, execute
from agents.llm_client import LLMClient, get_llm_client
from agents.strategy import (
    StrategyProposal,
    apply_fallback_ladder,
    propose_strategy,
    re_propose_strategy,
)
from core import config
from core.audit_log import (
    AuditLog,
    CaseAuditTrail,
    DiagnosisEntry,
    GateEntry,
    StrategyEntry,
)
from core.compliance import CONTACT_ACTIONS, GateDecision, run_all_checks, should_skip_pipeline
from core.memory import Memory
from core.schemas import (
    ActionType,
    B2BReceivableCase,
    Case,
    CaseStatus,
    CaseType,
    FailedPaymentCase,
)

logger = logging.getLogger(__name__)


@dataclass
class CaseOutcome:
    """End-to-end outcome of processing a single recovery case."""
    case_id: str
    status: str                       # "RECOVERED", "FAILED", "STOPPED", "ESCALATED", "WAITING", "SKIPPED", "GATE_BLOCKED"
    final_action: ActionType
    amount: float
    amount_recovered: float
    resolution_time: Optional[float]  # Reported in HOURS for payments, DAYS for B2B
    resolution_unit: str              # "hours" or "days"
    attempts_made: int = 1
    initial_attempt_count: int = 1
    diagnosis: Optional[DiagnosisResult] = None
    strategy: Optional[StrategyProposal] = None
    gate_decision: Optional[GateDecision] = None
    execution: Optional[ExecutionResult] = None
    escalation_reason: Optional[str] = None
    reasoning_summary: str = ""


@dataclass
class AgentBatchReport:
    """Aggregated results from running the AI Recovery Agent on a full batch."""
    scenario: str
    total_cases: int
    total_amount_at_risk: float
    cases_recovered: int
    amount_recovered: float
    recovery_rate_pct: float
    avg_resolution_time: Optional[float]
    resolution_unit: str              # "hours" or "days"
    total_compliance_violations: int   # Target: 0 (verified by Gate)
    cases_hard_stopped: int
    cases_escalated: int
    individual_outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def avg_hours_to_resolution(self) -> Optional[float]:
        """Alias for avg_resolution_time when resolution_unit == 'hours'."""
        return self.avg_resolution_time

    @property
    def avg_days_to_resolution(self) -> Optional[float]:
        """Alias for avg_resolution_time when resolution_unit == 'days'."""
        return self.avg_resolution_time


def _gate_decision_to_entry(gate: GateDecision) -> GateEntry:
    """Helper to convert GateDecision to GateEntry for AuditLog."""
    checks_dict = [asdict(r) for r in gate.results]
    return GateEntry(
        case_id=gate.case_id,
        timestamp=gate.timestamp,
        proposed_action=gate.proposed_action,
        approved=gate.approved,
        checks_run=checks_dict,
        violation_reasons=gate.violation_reasons,
    )


def process_case(
    case: Case,
    audit_log: AuditLog,
    memory: Memory,
    llm_client: Optional[LLMClient] = None,
    current_time: Optional[datetime] = None,
    rng: Optional[random.Random] = None,
) -> CaseOutcome:
    """Process a single case (payment or B2B) through the multi-attempt adaptive recovery loop."""
    active_rng = rng if rng is not None else random.Random(42)
    client = llm_client or get_llm_client()
    sim_time = current_time or getattr(config, "SIMULATED_CURRENT_TIME", None) or datetime.now()
    is_payment = case.case_type == CaseType.FAILED_PAYMENT
    res_unit = "hours" if is_payment else "days"
    case_initial_attempts = max(1, case.attempt_count)

    # -----------------------------------------------------------------------
    # Step 0: Pre-pipeline skip check (cost threshold, fraud, dispute, active promise)
    # -----------------------------------------------------------------------
    skip_result = should_skip_pipeline(case)
    if skip_result is not None:
        action = skip_result.action
        gate = run_all_checks(case, action, audit_log.get_execution_log(), current_time=sim_time)
        audit_log.record_gate_decision(_gate_decision_to_entry(gate))

        esc_reason: Optional[str] = None
        if skip_result.skip_type == "dispute":
            esc_reason = "dispute_skip"
        elif skip_result.skip_type == "fraud":
            esc_reason = "fraud_stop_skip"
        elif skip_result.skip_type == "active_promise":
            esc_reason = "active_promise_wait_skip"

        if gate.approved:
            exec_res = execute(case, action, audit_log, rng=active_rng)
        else:
            # Fallback to safe ESCALATE_HUMAN
            esc_gate = run_all_checks(case, ActionType.ESCALATE_HUMAN, audit_log.get_execution_log(), current_time=sim_time)
            audit_log.record_gate_decision(_gate_decision_to_entry(esc_gate))
            if esc_gate.approved:
                exec_res = execute(case, ActionType.ESCALATE_HUMAN, audit_log, rng=active_rng)
                action = ActionType.ESCALATE_HUMAN
                if skip_result.skip_type == "cost_threshold":
                    esc_reason = "attempt_cap_reached_at_start"
            else:
                return CaseOutcome(
                    case_id=case.case_id,
                    status="SKIPPED",
                    final_action=action,
                    amount=case.amount,
                    amount_recovered=0.0,
                    resolution_time=None,
                    resolution_unit=res_unit,
                    attempts_made=1,
                    initial_attempt_count=case_initial_attempts,
                    gate_decision=gate,
                    escalation_reason="gate_blocked_at_start",
                    reasoning_summary=f"Pre-pipeline skipped ({skip_result.skip_type}): {skip_result.reason}. All actions gate-rejected.",
                )

        recovered = exec_res.status == "SUCCESS"
        amt_rec = case.amount if recovered else 0.0

        status_map = {
            "SUCCESS": "RECOVERED",
            "FAILED": "FAILED",
            "STOPPED": "STOPPED",
            "ESCALATED": "ESCALATED",
            "WAITING": "WAITING",
        }
        outcome_status = status_map.get(exec_res.status, "FAILED")

        # Skip-path cases do NOT write to memory (no diagnosis category)
        return CaseOutcome(
            case_id=case.case_id,
            status=outcome_status,
            final_action=action,
            amount=case.amount,
            amount_recovered=amt_rec,
            resolution_time=exec_res.resolution_time,
            resolution_unit=res_unit,
            attempts_made=1,
            initial_attempt_count=case_initial_attempts,
            gate_decision=gate,
            execution=exec_res,
            escalation_reason=esc_reason,
            reasoning_summary=f"Pre-pipeline skip ({skip_result.skip_type}): {skip_result.reason}",
        )

    # -----------------------------------------------------------------------
    # Multi-Attempt Adaptive Loop (Symmetric with Baseline's 3-attempt budget)
    # -----------------------------------------------------------------------
    max_loop_attempts = min(
        config.AGENT_LOOP_MAX_ATTEMPTS,
        config.MAX_ATTEMPTS_PAYMENT if is_payment else config.MAX_ATTEMPTS_B2B,
    )

    last_diagnosis: Optional[DiagnosisResult] = None
    last_proposal: Optional[StrategyProposal] = None
    last_gate: Optional[GateDecision] = None
    last_execution: Optional[ExecutionResult] = None
    last_esc_reason: Optional[str] = None

    initial_attempt = max(1, case.attempt_count)
    for loop_iter in range(max_loop_attempts):
        case.attempt_count = initial_attempt + loop_iter

        # Step 1: Diagnosis Agent (LLM)
        diagnosis = diagnose(case, client)
        last_diagnosis = diagnosis
        now_str = datetime.now().isoformat()
        audit_log.record_diagnosis(DiagnosisEntry(
            case_id=case.case_id,
            timestamp=now_str,
            root_cause=diagnosis.root_cause,
            reasoning=diagnosis.reasoning,
            confidence=diagnosis.confidence,
            raw_output={"category": diagnosis.category.value, "attempt": case.attempt_count},
        ))

        # Step 2: Strategy Agent (LLM + memory context)
        strategy_context = memory.get_strategy_context(diagnosis.category)
        proposal = propose_strategy(case, diagnosis, strategy_context, client)
        last_proposal = proposal

        # Step 3: Confidence Gate & Fallback Ladder (< 0.85 or conflicts)
        has_conflicts = (
            getattr(case, "conflicting_signals", None) is not None
            and len(case.conflicting_signals) > 0
        )
        fallback_used = False
        fallback_trigger = ""
        if proposal.confidence < config.CONFIDENCE_THRESHOLD or has_conflicts:
            fallback_used = True
            fallback_trigger = "conflicting_signals" if has_conflicts else "low_confidence"
            proposal = apply_fallback_ladder(case, proposal)
            last_proposal = proposal
            if proposal.proposed_action == ActionType.ESCALATE_HUMAN:
                last_esc_reason = f"fallback_ladder_{fallback_trigger}"

        audit_log.record_strategy(StrategyEntry(
            case_id=case.case_id,
            timestamp=datetime.now().isoformat(),
            proposed_action=proposal.proposed_action.value,
            confidence=proposal.confidence,
            reasoning=proposal.reasoning,
            relationship_tier=case.relationship_tier.value if case.relationship_tier else None,
            conflicting_signals_present=has_conflicts,
            raw_output={"risk_assessment": proposal.risk_assessment, "attempt": case.attempt_count},
        ))

        # Step 4: Deterministic Compliance Gate Check (max 1 re-proposal)
        gate_rejection_count = 0
        final_gate: Optional[GateDecision] = None
        initial_gate_violation: Optional[str] = None

        while True:
            gate = run_all_checks(case, proposal.proposed_action, audit_log.get_execution_log(), current_time=sim_time)
            audit_log.record_gate_decision(_gate_decision_to_entry(gate))
            final_gate = gate

            if gate.approved:
                break

            if not initial_gate_violation and gate.violations:
                viol_name = gate.violations[0].rule_name
                if viol_name == "attempt_cap":
                    initial_gate_violation = f"attempt_cap_reached_on_attempt_{case.attempt_count}"
                else:
                    initial_gate_violation = f"gate_rejection_{viol_name}"

            gate_rejection_count += 1
            if gate_rejection_count >= 2:
                proposal.proposed_action = ActionType.ESCALATE_HUMAN
                term_gate = run_all_checks(case, ActionType.ESCALATE_HUMAN, audit_log.get_execution_log(), current_time=sim_time)
                audit_log.record_gate_decision(_gate_decision_to_entry(term_gate))
                final_gate = term_gate
                last_esc_reason = initial_gate_violation or f"attempt_cap_reached_on_attempt_{case.attempt_count}"

                if not term_gate.approved:
                    return CaseOutcome(
                        case_id=case.case_id,
                        status="GATE_BLOCKED",
                        final_action=ActionType.ESCALATE_HUMAN,
                        amount=case.amount,
                        amount_recovered=0.0,
                        resolution_time=None,
                        resolution_unit=res_unit,
                        attempts_made=loop_iter + 1,
                        initial_attempt_count=case_initial_attempts,
                        diagnosis=diagnosis,
                        strategy=proposal,
                        gate_decision=term_gate,
                        escalation_reason=last_esc_reason,
                        reasoning_summary="Terminal escalation blocked by compliance gate.",
                    )
                break

            # Re-propose once
            proposal = re_propose_strategy(case, diagnosis, proposal, gate, client)
            last_proposal = proposal
            audit_log.record_strategy(StrategyEntry(
                case_id=case.case_id,
                timestamp=datetime.now().isoformat(),
                proposed_action=proposal.proposed_action.value,
                confidence=proposal.confidence,
                reasoning=proposal.reasoning,
                relationship_tier=case.relationship_tier.value if case.relationship_tier else None,
                conflicting_signals_present=has_conflicts,
                raw_output={"risk_assessment": proposal.risk_assessment, "reproposed": True},
            ))

        last_gate = final_gate

        # Update contact count for B2B if contact action
        if not is_payment and proposal.proposed_action in CONTACT_ACTIONS:
            case.contact_count += 1

        # Preserve the root cause of escalation (Gate rejection > Fallback ladder > Policy)
        if initial_gate_violation:
            last_esc_reason = initial_gate_violation
        elif proposal.proposed_action == ActionType.ESCALATE_HUMAN and not last_esc_reason:
            last_esc_reason = "strategy_policy_escalation"

        # Step 5: Simulated Execution
        exec_res = execute(case, proposal.proposed_action, audit_log, rng=active_rng)
        last_execution = exec_res

        # Step 6: Memory Recording (Double-gated)
        memory.record_outcome(diagnosis.category, proposal.proposed_action, exec_res.status)

        # Check termination outcomes
        if exec_res.status == "SUCCESS":
            return CaseOutcome(
                case_id=case.case_id,
                status="RECOVERED",
                final_action=proposal.proposed_action,
                amount=case.amount,
                amount_recovered=case.amount,
                resolution_time=exec_res.resolution_time,
                resolution_unit=res_unit,
                attempts_made=loop_iter + 1,
                initial_attempt_count=case_initial_attempts,
                diagnosis=diagnosis,
                strategy=proposal,
                gate_decision=final_gate,
                execution=exec_res,
                escalation_reason=None,
                reasoning_summary=f"Recovered on loop attempt {loop_iter + 1} (attempt_count={case.attempt_count}) via {proposal.proposed_action.value}.",
            )

        if exec_res.status in {"STOPPED", "ESCALATED", "WAITING"}:
            status_map = {"STOPPED": "STOPPED", "ESCALATED": "ESCALATED", "WAITING": "WAITING"}
            return CaseOutcome(
                case_id=case.case_id,
                status=status_map[exec_res.status],
                final_action=proposal.proposed_action,
                amount=case.amount,
                amount_recovered=0.0,
                resolution_time=None,
                resolution_unit=res_unit,
                attempts_made=loop_iter + 1,
                initial_attempt_count=case_initial_attempts,
                diagnosis=diagnosis,
                strategy=proposal,
                gate_decision=final_gate,
                execution=exec_res,
                escalation_reason=last_esc_reason if exec_res.status == "ESCALATED" else None,
                reasoning_summary=f"Terminal action {proposal.proposed_action.value} executed ({exec_res.status}).",
            )

        # If FAILED and attempts remain: loop continues
        case.attempt_count += 1
        last_esc_reason = initial_gate_violation or f"attempt_cap_reached_on_attempt_{case.attempt_count}"

    # If all loop attempts failed to recover
    return CaseOutcome(
        case_id=case.case_id,
        status="FAILED",
        final_action=last_proposal.proposed_action if last_proposal else ActionType.RETRY_NOW,
        amount=case.amount,
        amount_recovered=0.0,
        resolution_time=None,
        resolution_unit=res_unit,
        attempts_made=max_loop_attempts,
        initial_attempt_count=case_initial_attempts,
        diagnosis=last_diagnosis,
        strategy=last_proposal,
        gate_decision=last_gate,
        execution=last_execution,
        escalation_reason="exhausted_all_attempts_failed",
        reasoning_summary=f"Case unrecovered after maximum {max_loop_attempts} attempts.",
    )


def process_payment_batch(
    cases: list[FailedPaymentCase],
    audit_log: Optional[AuditLog] = None,
    memory: Optional[Memory] = None,
    llm_client: Optional[LLMClient] = None,
    scenario_name: str = "Failed Payments (AI Recovery Agent)",
    current_time: Optional[datetime] = None,
    rng: Optional[random.Random] = None,
) -> AgentBatchReport:
    """Process a full batch of failed payment cases and produce an aggregated report with isolated RNG."""
    audit = audit_log or AuditLog()
    mem = memory or Memory()
    client = llm_client or get_llm_client()
    sim_time = current_time or getattr(config, "SIMULATED_CURRENT_TIME", None) or datetime.now()
    active_rng = rng if rng is not None else random.Random(42)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(process_case(case, audit, mem, client, current_time=sim_time, rng=active_rng))

    total_cases = len(outcomes)
    total_amount_at_risk = sum(o.amount for o in outcomes)
    recovered_outcomes = [o for o in outcomes if o.status == "RECOVERED"]
    cases_recovered = len(recovered_outcomes)
    amount_recovered = sum(o.amount_recovered for o in outcomes)
    recovery_rate_pct = (cases_recovered / total_cases * 100.0) if total_cases > 0 else 0.0

    resolution_times = [o.resolution_time for o in recovered_outcomes if o.resolution_time is not None]
    avg_res_time = (sum(resolution_times) / len(resolution_times)) if resolution_times else None

    cases_stopped = sum(1 for o in outcomes if o.status == "STOPPED")
    cases_escalated = sum(1 for o in outcomes if o.status == "ESCALATED")

    return AgentBatchReport(
        scenario=scenario_name,
        total_cases=total_cases,
        total_amount_at_risk=total_amount_at_risk,
        cases_recovered=cases_recovered,
        amount_recovered=amount_recovered,
        recovery_rate_pct=round(recovery_rate_pct, 2),
        avg_resolution_time=round(avg_res_time, 1) if avg_res_time is not None else None,
        resolution_unit="hours",
        total_compliance_violations=0,
        cases_hard_stopped=cases_stopped,
        cases_escalated=cases_escalated,
        individual_outcomes=outcomes,
    )


def process_b2b_batch(
    cases: list[B2BReceivableCase],
    audit_log: Optional[AuditLog] = None,
    memory: Optional[Memory] = None,
    llm_client: Optional[LLMClient] = None,
    scenario_name: str = "B2B Receivables (AI Recovery Agent)",
    current_time: Optional[datetime] = None,
    rng: Optional[random.Random] = None,
) -> AgentBatchReport:
    """Process a full batch of B2B receivable cases and produce an aggregated report with isolated RNG."""
    audit = audit_log or AuditLog()
    mem = memory or Memory()
    client = llm_client or get_llm_client()
    sim_time = current_time or getattr(config, "SIMULATED_CURRENT_TIME", None) or datetime.now()
    active_rng = rng if rng is not None else random.Random(42)

    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(process_case(case, audit, mem, client, current_time=sim_time, rng=active_rng))

    total_cases = len(outcomes)
    total_amount_at_risk = sum(o.amount for o in outcomes)
    recovered_outcomes = [o for o in outcomes if o.status == "RECOVERED"]
    cases_recovered = len(recovered_outcomes)
    amount_recovered = sum(o.amount_recovered for o in outcomes)
    recovery_rate_pct = (cases_recovered / total_cases * 100.0) if total_cases > 0 else 0.0

    resolution_times = [o.resolution_time for o in recovered_outcomes if o.resolution_time is not None]
    avg_res_time = (sum(resolution_times) / len(resolution_times)) if resolution_times else None

    cases_stopped = sum(1 for o in outcomes if o.status == "STOPPED")
    cases_escalated = sum(1 for o in outcomes if o.status == "ESCALATED")

    return AgentBatchReport(
        scenario=scenario_name,
        total_cases=total_cases,
        total_amount_at_risk=total_amount_at_risk,
        cases_recovered=cases_recovered,
        amount_recovered=amount_recovered,
        recovery_rate_pct=round(recovery_rate_pct, 2),
        avg_resolution_time=round(avg_res_time, 1) if avg_res_time is not None else None,
        resolution_unit="days",
        total_compliance_violations=0,
        cases_hard_stopped=cases_stopped,
        cases_escalated=cases_escalated,
        individual_outcomes=outcomes,
    )


def export_breakdown_csv(report: AgentBatchReport, filepath: str) -> None:
    """Export the complete case-by-case outcome and escalation breakdown to a CSV file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    fieldnames = [
        "case_id",
        "status",
        "final_action",
        "amount",
        "amount_recovered",
        "resolution_time",
        "resolution_unit",
        "initial_attempt_count",
        "attempts_made",
        "diagnosis_category",
        "diagnosis_confidence",
        "escalation_reason",
        "reasoning_summary",
    ]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for o in report.individual_outcomes:
            diag_cat = o.diagnosis.category.value if o.diagnosis else "N/A"
            diag_conf = f"{o.diagnosis.confidence:.2f}" if o.diagnosis else "N/A"
            res_time_str = f"{o.resolution_time:.1f}" if o.resolution_time is not None else "N/A"
            writer.writerow({
                "case_id": o.case_id,
                "status": o.status,
                "final_action": o.final_action.value if hasattr(o.final_action, "value") else str(o.final_action),
                "amount": f"{o.amount:.2f}",
                "amount_recovered": f"{o.amount_recovered:.2f}",
                "resolution_time": res_time_str,
                "resolution_unit": o.resolution_unit,
                "initial_attempt_count": o.initial_attempt_count,
                "attempts_made": o.attempts_made,
                "diagnosis_category": diag_cat,
                "diagnosis_confidence": diag_conf,
                "escalation_reason": o.escalation_reason or "N/A",
                "reasoning_summary": o.reasoning_summary,
            })


def print_agent_batch_report(report: AgentBatchReport) -> None:
    """Print human-readable AI Recovery Agent batch report."""
    print(f"\n{'=' * 60}")
    print(f"AGENT BATCH REPORT — {report.scenario}")
    print(f"{'=' * 60}")
    print(f"ALL DATA IS SIMULATED")
    print(f"{'─' * 60}")
    print(f"Total cases:              {report.total_cases}")
    print(f"Total ₹ at risk:          ₹{report.total_amount_at_risk:,.2f}")
    print(f"Cases recovered:          {report.cases_recovered} "
          f"({report.recovery_rate_pct:.1f}%)")
    print(f"₹ recovered:             ₹{report.amount_recovered:,.2f}")
    print(f"Avg resolution time:      "
          f"{f'{report.avg_resolution_time} {report.resolution_unit}' if report.avg_resolution_time is not None else 'N/A'}")
    print(f"Compliance violations:    {report.total_compliance_violations}")
    print(f"Cases hard-stopped:       {report.cases_hard_stopped}")
    print(f"Cases escalated:          {report.cases_escalated}")
    print(f"{'─' * 60}\n")
