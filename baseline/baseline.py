"""
Naive Fixed-Rule Baseline — for comparison against the agent system.

This is a deliberately simple, one-size-fits-all recovery system that:
- Uses a flat retry schedule (payments) or flat reminder cadence (B2B)
- Does NOT diagnose root causes
- Does NOT adapt strategy based on outcomes
- Does NOT respect relationship sensitivity
- Does NOT enforce compliance rules (dispute/fraud stops) — this is
  intentional: the baseline's compliance violation count should be > 0,
  making the agent's "zero violations" metric a genuine comparison,
  not a number that's zero by construction.

Uses the SAME probability tables as the agent system (from config.py)
so the comparison is fair. The agent wins by picking better actions
for the diagnosed cause, not by having more generous success odds.

ALL DATA IS SIMULATED.
"""

from __future__ import annotations

import csv
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Union

from core import config
from core.schemas import (
    B2BReceivableCase,
    CaseStatus,
    FailedPaymentCase,
    FailureCode,
)

# Reproducible randomness matching the generator
_rng = random.Random(42)

Case = Union[FailedPaymentCase, B2BReceivableCase]


@dataclass
class BaselineResult:
    """Result of running the baseline on a single case."""
    case_id: str
    case_type: str
    amount: float
    initial_status: str
    final_status: str
    attempts_made: int
    recovered: bool
    amount_recovered: float
    resolution_time: float | int | None     # None if unresolved; hours for payment, days for B2B
    resolution_unit: str                    # "hours" for payment, "days" for B2B
    compliance_violations: list[str]   # List of violation descriptions
    actions_taken: list[str]           # Log of actions taken


@dataclass
class BaselineBatchReport:
    """Aggregated results from running the baseline on a full batch."""
    scenario: str
    total_cases: int
    total_amount_at_risk: float
    cases_recovered: int
    amount_recovered: float
    recovery_rate_pct: float
    avg_resolution_time: float | None
    resolution_unit: str                    # "hours" for payment, "days" for B2B
    total_compliance_violations: int
    violation_details: list[str]
    cases_hard_stopped: int        # Should be 0 for baseline (it doesn't do this)
    individual_results: list[BaselineResult] = field(default_factory=list)

    @property
    def avg_hours_to_resolution(self) -> float | None:
        return self.avg_resolution_time if self.resolution_unit == "hours" else None

    @property
    def avg_days_to_resolution(self) -> float | None:
        return self.avg_resolution_time if self.resolution_unit == "days" else None


# ---------------------------------------------------------------------------
# Baseline: Failed Payments
# ---------------------------------------------------------------------------

def _run_baseline_payment(
    case: FailedPaymentCase,
    rng: Optional[random.Random] = None,
) -> BaselineResult:
    """Run the naive baseline on a single failed payment case.

    Strategy: retry every 4 hours, up to 3 times, regardless of failure code.
    No diagnosis, no adaptation, no compliance enforcement.
    """
    active_rng = rng if rng is not None else _rng
    violations: list[str] = []
    actions: list[str] = []
    recovered = False
    amount_recovered = 0.0
    attempts_made = 0

    # The baseline does NOT check fraud flags — this is a deliberate flaw
    if case.fraud_flag:
        violations.append(
            f"VIOLATION: Attempted recovery on fraud-flagged case {case.case_id}. "
            f"A compliant system would hard-stop."
        )

    # Success probability from shared config
    success_prob = config.PAYMENT_RETRY_SUCCESS_PROB.get(
        case.failure_code.value, 0.20
    )

    # Fixed retry schedule: up to 3 retries, 4 hours apart
    max_baseline_retries = 3
    for attempt in range(max_baseline_retries):
        if case.fraud_flag and case.failure_code == FailureCode.FRAUD_REJECTION:
            # Still attempts (violation already recorded), but will always fail
            actions.append(f"Attempt {attempt + 1}: retry (fraud — will fail)")
            attempts_made += 1
            continue

        actions.append(f"Attempt {attempt + 1}: retry at +{(attempt + 1) * 4}h")
        attempts_made += 1

        if active_rng.random() < success_prob:
            recovered = True
            amount_recovered = case.amount
            actions.append(f"  → SUCCESS: Recovered ₹{case.amount:,.2f}")
            break
        else:
            actions.append(f"  → FAILED")

    hours_to_resolution = None
    if recovered:
        # Simulated: each retry is 4 hours apart (hours)
        hours_to_resolution = attempts_made * 4

    return BaselineResult(
        case_id=case.case_id,
        case_type="FAILED_PAYMENT",
        amount=case.amount,
        initial_status=case.status.value,
        final_status="RESOLVED" if recovered else "OPEN",
        attempts_made=attempts_made,
        recovered=recovered,
        amount_recovered=amount_recovered,
        resolution_time=hours_to_resolution,
        resolution_unit="hours",
        compliance_violations=violations,
        actions_taken=actions,
    )


# ---------------------------------------------------------------------------
# Baseline: B2B Receivables
# ---------------------------------------------------------------------------

def _get_b2b_overdue_bucket(days_overdue: int) -> str:
    """Map days overdue to the probability bucket."""
    if days_overdue <= 10:
        return "early"
    elif days_overdue <= 30:
        return "mid"
    elif days_overdue <= 60:
        return "late"
    return "stale"


def _run_baseline_b2b(
    case: B2BReceivableCase,
    rng: Optional[random.Random] = None,
) -> BaselineResult:
    """Run the naive baseline on a single B2B receivable case.

    Strategy: reminder at day 7, 14, 21 overdue, then escalate.
    No tone adjustment, no relationship awareness, no promise tracking.
    DELIBERATELY ignores dispute flags — generates violations.
    """
    active_rng = rng if rng is not None else _rng
    violations: list[str] = []
    actions: list[str] = []
    recovered = False
    amount_recovered = 0.0
    attempts_made = 0

    # The baseline does NOT check dispute or fraud flags — deliberate flaw
    if case.dispute_flag:
        violations.append(
            f"VIOLATION: Sent recovery contact on disputed case {case.case_id}. "
            f"A compliant system would hard-stop and route to dispute resolution. "
            f"(RBI Fair Practices: debtor's right to dispute)"
        )
    if case.fraud_flag:
        violations.append(
            f"VIOLATION: Attempted recovery on fraud-flagged case {case.case_id}. "
            f"A compliant system would hard-stop."
        )

    # Overdue bucket for success probability
    days_overdue = case.days_overdue
    bucket = _get_b2b_overdue_bucket(days_overdue)
    success_prob = config.B2B_REMINDER_SUCCESS_PROB.get(bucket, 0.08)

    # Fixed reminder cadence: day 7, 14, 21, then escalate
    reminder_days = [7, 14, 21]
    for rd in reminder_days:
        if days_overdue < rd:
            # Not yet time for this reminder
            continue

        actions.append(f"Reminder at day {rd}: generic payment reminder sent")
        attempts_made += 1

        if active_rng.random() < success_prob:
            recovered = True
            amount_recovered = case.amount
            actions.append(f"  → SUCCESS: Payment received, ₹{case.amount:,.2f}")
            break
        else:
            actions.append(f"  → No response")

    if not recovered and days_overdue > 21:
        actions.append("Escalated: sent to generic escalation queue (no outcome)")
        attempts_made += 1

    days_to_resolution = None
    if recovered:
        # Simulated: resolved within the reminder window (resolution time in days)
        days_to_resolution = min(days_overdue, active_rng.randint(3, 21))

    return BaselineResult(
        case_id=case.case_id,
        case_type="B2B_RECEIVABLE",
        amount=case.amount,
        initial_status=case.status.value,
        final_status="RESOLVED" if recovered else "OPEN",
        attempts_made=attempts_made,
        recovered=recovered,
        amount_recovered=amount_recovered,
        resolution_time=days_to_resolution,
        resolution_unit="days",
        compliance_violations=violations,
        actions_taken=actions,
    )


# ---------------------------------------------------------------------------
# Batch Runner
# ---------------------------------------------------------------------------

def run_baseline_batch(
    cases: list[Case],
    scenario_name: str = "batch",
    rng: Optional[random.Random] = None,
) -> BaselineBatchReport:
    """Run the naive baseline on a full batch of cases with isolated RNG state."""
    active_rng = rng if rng is not None else random.Random(42)
    results: list[BaselineResult] = []

    for case in cases:
        if isinstance(case, FailedPaymentCase):
            result = _run_baseline_payment(case, rng=active_rng)
        elif isinstance(case, B2BReceivableCase):
            result = _run_baseline_b2b(case, rng=active_rng)
        else:
            continue
        results.append(result)

    # Aggregate
    total_cases = len(results)
    total_at_risk = sum(r.amount for r in results)
    cases_recovered = sum(1 for r in results if r.recovered)
    amount_recovered = sum(r.amount_recovered for r in results)
    recovery_pct = (cases_recovered / total_cases * 100) if total_cases > 0 else 0.0

    resolved_times = [r.resolution_time for r in results if r.resolution_time is not None]
    avg_res_time = (sum(resolved_times) / len(resolved_times)) if resolved_times else None
    res_unit = results[0].resolution_unit if results else "days"

    all_violations = []
    for r in results:
        all_violations.extend(r.compliance_violations)

    return BaselineBatchReport(
        scenario=scenario_name,
        total_cases=total_cases,
        total_amount_at_risk=total_at_risk,
        cases_recovered=cases_recovered,
        amount_recovered=amount_recovered,
        recovery_rate_pct=round(recovery_pct, 2),
        avg_resolution_time=round(avg_res_time, 1) if avg_res_time is not None else None,
        resolution_unit=res_unit,
        total_compliance_violations=len(all_violations),
        violation_details=all_violations,
        cases_hard_stopped=0,  # Baseline never hard-stops — that's the point
        individual_results=results,
    )


def print_baseline_report(report: BaselineBatchReport) -> None:
    """Print a human-readable baseline report."""
    print(f"\n{'=' * 60}")
    print(f"BASELINE REPORT — {report.scenario}")
    print(f"{'=' * 60}")
    print(f"ALL DATA IS SIMULATED")
    print(f"{'─' * 60}")
    print(f"Total cases:              {report.total_cases}")
    print(f"Total ₹ at risk:          ₹{report.total_amount_at_risk:,.2f}")
    print(f"Cases recovered:          {report.cases_recovered} "
          f"({report.recovery_rate_pct:.1f}%)")
    print(f"₹ recovered:             ₹{report.amount_recovered:,.2f}")

    if report.resolution_unit == "hours":
        time_str = f"{report.avg_resolution_time} hours" if report.avg_resolution_time is not None else "N/A"
        print(f"Avg hours to resolution:  {time_str}")
    else:
        time_str = f"{report.avg_resolution_time} days" if report.avg_resolution_time is not None else "N/A"
        print(f"Avg days to resolution:   {time_str}")

    print(f"Compliance violations:    {report.total_compliance_violations}")
    print(f"Cases hard-stopped:       {report.cases_hard_stopped}")
    print(f"{'─' * 60}")

    if report.violation_details:
        print(f"\nViolation details:")
        for v in report.violation_details:
            print(f"  • {v}")

    print()


def export_baseline_csv(report: BaselineBatchReport, filepath: str) -> None:
    """Export the complete baseline outcomes to a CSV file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    fieldnames = [
        "case_id",
        "case_type",
        "amount",
        "recovered",
        "amount_recovered",
        "attempts_made",
        "resolution_time",
        "resolution_unit",
        "compliance_violations_count",
        "compliance_violations",
    ]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in report.individual_results:
            writer.writerow({
                "case_id": r.case_id,
                "case_type": r.case_type,
                "amount": f"{r.amount:.2f}",
                "recovered": "TRUE" if r.recovered else "FALSE",
                "amount_recovered": f"{r.amount_recovered:.2f}",
                "attempts_made": r.attempts_made,
                "resolution_time": f"{r.resolution_time:.1f}" if r.resolution_time is not None else "N/A",
                "resolution_unit": r.resolution_unit,
                "compliance_violations_count": len(r.compliance_violations),
                "compliance_violations": " | ".join(r.compliance_violations) if r.compliance_violations else "NONE",
            })


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import os
    from core.schemas import dict_to_failed_payment, dict_to_b2b_receivable

    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    # Load payment cases
    pay_path = os.path.join(data_dir, "failed_payments.json")
    if os.path.exists(pay_path):
        with open(pay_path, "r") as f:
            pay_data = json.load(f)
        payment_cases = [dict_to_failed_payment(c) for c in pay_data["cases"]]
        pay_report = run_baseline_batch(payment_cases, "Failed Payments (Baseline)")
        print_baseline_report(pay_report)

    # Load B2B cases
    b2b_path = os.path.join(data_dir, "b2b_receivables.json")
    if os.path.exists(b2b_path):
        with open(b2b_path, "r") as f:
            b2b_data = json.load(f)
        b2b_cases = [dict_to_b2b_receivable(c) for c in b2b_data["cases"]]
        b2b_report = run_baseline_batch(b2b_cases, "B2B Receivables (Baseline)")
        print_baseline_report(b2b_report)
