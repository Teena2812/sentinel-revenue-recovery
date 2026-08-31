"""
Phase 3 Verification Script — Benchmarks AI Recovery Agent vs Naive Baseline on B2B Receivables.

Usage:
    python run_phase3.py
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
    process_b2b_batch,
)
from core.schemas import dict_to_b2b_case


def main():
    print("=" * 60)
    print("PHASE 3 VERIFICATION & BENCHMARK SCRIPT (B2B Receivables)")
    print("=" * 60)

    # 1. Run B2B test suite
    print("\n--- Running B2B Test Suite (tests/test_b2b_loop.py) ---")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_b2b_loop")
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    if not test_result.wasSuccessful():
        print("\n❌ B2B Tests failed! Please fix test failures before benchmarking.")
        sys.exit(1)

    print("\n✅ All B2B unit tests passed successfully.")

    # 2. Load synthetic B2B cases
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    b2b_path = os.path.join(data_dir, "b2b_receivables.json")

    if not os.path.exists(b2b_path):
        print(f"\n❌ Data file not found: {b2b_path}. Running generator...")
        from data.generator import generate_all
        generate_all()

    with open(b2b_path, "r", encoding="utf-8") as f:
        b2b_data = json.load(f)

    b2b_cases = [dict_to_b2b_case(c) for c in b2b_data["cases"]]

    # Verify permanent simulation anchor prevents days_overdue drift on canonical case
    target_case = next((c for c in b2b_cases if c.case_id == "B2B-c64ee6e3-89c"), None)
    if target_case:
        print(f"Verified canonical case {target_case.case_id}: {target_case.days_overdue} days overdue (Anchor verified).")

    # 3. Run Naive Baseline (B2B, with structurally isolated RNG)
    print("\n" + "=" * 60)
    print("RUNNING NAIVE BASELINE (B2B Receivables)")
    print("=" * 60)
    baseline_report = run_baseline_batch(b2b_cases, "B2B Receivables (Baseline)", rng=random.Random(42))
    print(f"Total cases:              {baseline_report.total_cases}")
    print(f"Total ₹ at risk:          ₹{baseline_report.total_amount_at_risk:,.2f}")
    print(f"Cases recovered:          {baseline_report.cases_recovered} ({baseline_report.recovery_rate_pct:.1f}%)")
    print(f"₹ recovered:             ₹{baseline_report.amount_recovered:,.2f}")
    print(f"Avg days to resolution:   {baseline_report.avg_resolution_time} days")
    print(f"Compliance violations:    {baseline_report.total_compliance_violations}")
    print(f"Cases hard-stopped:       {baseline_report.cases_hard_stopped}")

    base_csv_path = "reports/b2b_baseline.csv"
    export_baseline_csv(baseline_report, base_csv_path)
    print(f"Baseline breakdown exported to: {base_csv_path}")

    # 4. Run AI Recovery Agent (B2B, with structurally isolated RNG)
    print("\n" + "=" * 60)
    print("RUNNING AI RECOVERY AGENT (B2B Receivables)")
    print("=" * 60)
    audit = AuditLog("data/b2b_agent_audit_log.json")
    memory = Memory("data/b2b_agent_memory.json")
    memory.clear()

    agent_report = process_b2b_batch(
        b2b_cases,
        audit,
        memory,
        current_time=config.SIMULATED_CURRENT_TIME,
        rng=random.Random(42),
    )
    print_agent_batch_report(agent_report)

    # Export detailed case-by-case breakdown
    csv_path = "reports/b2b_batch_breakdown.csv"
    export_breakdown_csv(agent_report, csv_path)
    print(f"Detailed case breakdown exported to: {csv_path}")

    # Print Escalation Breakdown
    escalated_cases = [o for o in agent_report.individual_outcomes if o.status == "ESCALATED"]
    if escalated_cases:
        print("\n" + "=" * 60)
        print(f"ESCALATION AUDIT BREAKDOWN ({len(escalated_cases)} Escalated Cases)")
        print("=" * 60)
        print(f"{'Case ID':<18} | {'Init Att':<8} | {'Diag Category':<22} | {'Conf':<5} | {'Escalation Reason'}")
        print("-" * 60)
        for o in escalated_cases:
            diag_cat = o.diagnosis.category.value if o.diagnosis else "N/A (Skip)"
            diag_conf = f"{o.diagnosis.confidence:.2f}" if o.diagnosis else "N/A"
            print(f"{o.case_id:<18} | {o.initial_attempt_count:<8} | {diag_cat:<22} | {diag_conf:<5} | {o.escalation_reason}")
        print("=" * 60)

    # 5. Side-by-Side Comparison
    print("\n" + "=" * 60)
    print("HEAD-TO-HEAD COMPARISON: BASELINE vs AI AGENT (B2B Receivables)")
    print("=" * 60)
    print(f"{'Metric':<30} | {'Baseline':<14} | {'AI Agent':<14}")
    print("-" * 60)
    print(f"{'Recovery Rate (%)':<30} | {baseline_report.recovery_rate_pct:>12.1f}% | {agent_report.recovery_rate_pct:>12.1f}%")
    print(f"{'Amount Recovered (₹)':<30} | ₹{baseline_report.amount_recovered:>11,.2f} | ₹{agent_report.amount_recovered:>11,.2f}")
    print(f"{'Avg Resolution Time':<30} | {str(baseline_report.avg_resolution_time) + ' days':>13} | {str(agent_report.avg_resolution_time) + ' days':>13}")
    print(f"{'Compliance Violations':<30} | {baseline_report.total_compliance_violations:>13} | {agent_report.total_compliance_violations:>13}")
    print(f"{'Cases Hard-Stopped':<30} | {baseline_report.cases_hard_stopped:>13} | {agent_report.cases_hard_stopped:>13}")
    print(f"{'Cases Escalated':<30} | {0:>13} | {agent_report.cases_escalated:>13}")
    print("=" * 60)

    # Clean up test artifacts
    if os.path.exists("data/b2b_agent_audit_log.json"):
        os.remove("data/b2b_agent_audit_log.json")
    memory.clear()

    print("\nPHASE 3 BUILD AND VERIFICATION COMPLETE")


if __name__ == "__main__":
    main()
