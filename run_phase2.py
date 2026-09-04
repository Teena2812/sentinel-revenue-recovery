"""
Phase 2 Verification Script — Runs all unit tests and benchmarks AI Recovery Agent vs Baseline.

Usage:
    python run_phase2.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import unittest

from baseline.baseline import export_baseline_csv, run_baseline_batch
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import (
    AgentBatchReport,
    export_breakdown_csv,
    print_agent_batch_report,
    process_payment_batch,
)
from core.schemas import dict_to_failed_payment


def main():
    print("=" * 60)
    print("PHASE 2 VERIFICATION & BENCHMARK SCRIPT")
    print("=" * 60)

    # 1. Run all unit tests
    print("\n--- Running Full Test Suite (Phase 1 + Phase 2) ---")
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    if not test_result.wasSuccessful():
        print("\n[ERROR] Tests failed! Please fix test failures before benchmarking.")
        sys.exit(1)

    print("\n[OK] All unit tests passed successfully.")

    # 2. Load synthetic payment cases
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    pay_path = os.path.join(data_dir, "failed_payments.json")

    if not os.path.exists(pay_path):
        print(f"\n[ERROR] Data file not found: {pay_path}. Running generator...")
        from data.generator import generate_all
        generate_all()

    with open(pay_path, "r", encoding="utf-8") as f:
        pay_data = json.load(f)

    payment_cases = [dict_to_failed_payment(c) for c in pay_data["cases"]]

    # 3. Run Naive Baseline (with structurally isolated RNG)
    print("\n" + "=" * 60)
    print("RUNNING NAIVE BASELINE (Failed Payments)")
    print("=" * 60)
    baseline_report = run_baseline_batch(payment_cases, "Failed Payments (Baseline)", rng=random.Random(42))
    print(f"Total cases:              {baseline_report.total_cases}")
    print(f"Total ₹ at risk:          ₹{baseline_report.total_amount_at_risk:,.2f}")
    print(f"Cases recovered:          {baseline_report.cases_recovered} ({baseline_report.recovery_rate_pct:.1f}%)")
    print(f"₹ recovered:             ₹{baseline_report.amount_recovered:,.2f}")
    print(f"Avg hours to resolution:  {baseline_report.avg_hours_to_resolution} hours")
    print(f"Compliance violations:    {baseline_report.total_compliance_violations}")
    print(f"Cases hard-stopped:       {baseline_report.cases_hard_stopped}")

    base_csv_path = "reports/payment_baseline.csv"
    export_baseline_csv(baseline_report, base_csv_path)
    print(f"Baseline breakdown exported to: {base_csv_path}")

    # 4. Run AI Recovery Agent (with structurally isolated RNG)
    print("\n" + "=" * 60)
    print("RUNNING AI RECOVERY AGENT (Failed Payments)")
    print("=" * 60)
    audit = AuditLog("data/agent_audit_log.json")
    memory = Memory("data/agent_memory.json")
    memory.clear()

    agent_report = process_payment_batch(
        payment_cases,
        audit,
        memory,
        current_time=config.SIMULATED_CURRENT_TIME,
        rng=random.Random(42),
    )
    print_agent_batch_report(agent_report)

    # Export detailed case-by-case breakdown
    csv_path = "reports/payment_batch_breakdown.csv"
    export_breakdown_csv(agent_report, csv_path)
    print(f"Detailed case breakdown exported to: {csv_path}")

    # Print Escalation Breakdown
    escalated_cases = [o for o in agent_report.individual_outcomes if o.status == "ESCALATED"]
    if escalated_cases:
        print("\n" + "=" * 60)
        print(f"ESCALATION AUDIT BREAKDOWN ({len(escalated_cases)} Escalated Cases)")
        print("=" * 60)
        print(f"{'Case ID':<18} | {'Init Att':<8} | {'Diag Category':<18} | {'Conf':<5} | {'Escalation Reason'}")
        print("-" * 60)
        for o in escalated_cases:
            diag_cat = o.diagnosis.category.value if o.diagnosis else "N/A (Skip)"
            diag_conf = f"{o.diagnosis.confidence:.2f}" if o.diagnosis else "N/A"
            print(f"{o.case_id:<18} | {o.initial_attempt_count:<8} | {diag_cat:<18} | {diag_conf:<5} | {o.escalation_reason}")
        print("=" * 60)

    # 5. Side-by-Side Comparison
    print("\n" + "=" * 60)
    print("HEAD-TO-HEAD COMPARISON: BASELINE vs AI AGENT")
    print("=" * 60)
    print(f"{'Metric':<30} | {'Baseline':<12} | {'AI Agent':<12}")
    print("-" * 60)
    print(f"{'Recovery Rate (%)':<30} | {baseline_report.recovery_rate_pct:>10.1f}% | {agent_report.recovery_rate_pct:>10.1f}%")
    print(f"{'Amount Recovered (₹)':<30} | ₹{baseline_report.amount_recovered:>9,.2f} | ₹{agent_report.amount_recovered:>9,.2f}")
    print(f"{'Avg Resolution Time':<30} | {str(baseline_report.avg_hours_to_resolution) + ' hrs':>11} | {str(agent_report.avg_hours_to_resolution) + ' hrs':>11}")
    print(f"{'Compliance Violations':<30} | {baseline_report.total_compliance_violations:>11} | {agent_report.total_compliance_violations:>11}")
    print(f"{'Cases Hard-Stopped':<30} | {baseline_report.cases_hard_stopped:>11} | {agent_report.cases_hard_stopped:>11}")
    print(f"{'Cases Escalated':<30} | {0:>11} | {agent_report.cases_escalated:>11}")
    print("=" * 60)

    # Clean up test artifacts
    if os.path.exists("data/agent_audit_log.json"):
        os.remove("data/agent_audit_log.json")
    memory.clear()

    print("\nPHASE 2 BUILD AND VERIFICATION COMPLETE")


if __name__ == "__main__":
    main()
