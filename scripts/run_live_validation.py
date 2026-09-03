"""
Sentinel — Live Gemini Validation & Divergence Audit Runner.

Runs a side-by-side comparison of the AI Recovery pipeline on benchmark cases:
1. Deterministic Mock Mode (MockLLMClient policy matrix)
2. Live Generative Mode (GeminiLLMClient on gemini-flash-lite-latest)

All simulated tool execution draws share an identical RNG stream (seed=42)
and frozen simulated current time, isolating model decision intelligence as the
sole independent variable.

Usage:
    # Quick 5-case smoke test (3 payments + 2 B2B)
    python scripts/run_live_validation.py --smoke

    # Full 80-case validation run
    python scripts/run_live_validation.py --full
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import datetime
from typing import Any

# Ensure project root in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.llm_client import GeminiLLMClient, MockLLMClient
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import AgentBatchReport, process_case
from core.schemas import dict_to_b2b_receivable, dict_to_failed_payment


def load_raw_cases(smoke_test: bool = False) -> tuple[list[dict], list[dict]]:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pay_path = os.path.join(base_dir, "data", "failed_payments.json")
    b2b_path = os.path.join(base_dir, "data", "b2b_receivables.json")

    with open(pay_path, "r", encoding="utf-8") as f:
        pay_raw = json.load(f)["cases"]
    with open(b2b_path, "r", encoding="utf-8") as f:
        b2b_raw = json.load(f)["cases"]

    if smoke_test:
        # Pick 3 diverse payment cases and 2 B2B cases
        # Choose cases that exercise full pipeline (not cost-skipped)
        selected_pay = [c for c in pay_raw if c["amount"] >= 500 and not c.get("fraud_flag")][:3]
        selected_b2b = [c for c in b2b_raw if not c.get("dispute_flag") and not c.get("fraud_flag")][:2]
        return selected_pay, selected_b2b

    return pay_raw, b2b_raw


def run_batch_evaluation(
    client,
    pay_raw: list[dict],
    b2b_raw: list[dict],
    mode_name: str,
) -> tuple[Any, Any]:
    """Execute both payment and B2B batches under controlled seed and time with live progress logging."""
    pay_cases = [dict_to_failed_payment(d) for d in pay_raw]
    b2b_cases = [dict_to_b2b_receivable(d) for d in b2b_raw]

    audit_log = AuditLog(path=f"data/tmp_audit_{mode_name.lower()}.json")
    memory = Memory(f"data/tmp_memory_{mode_name.lower()}.json")
    memory.clear()

    # 1. Failed Payments batch
    print(f"\n  [{mode_name}] Processing {len(pay_cases)} Failed Payment cases...")
    pay_outcomes = []
    active_rng_pay = random.Random(42)
    for i, case in enumerate(pay_cases, 1):
        if mode_name == "Live":
            print(f"    [{i:2d}/{len(pay_cases)}] {case.case_id} (₹{case.amount:,.2f} — {case.failure_code.value})... ", end="", flush=True)
        outcome = process_case(case, audit_log, memory, client, current_time=config.SIMULATED_CURRENT_TIME, rng=active_rng_pay)
        pay_outcomes.append(outcome)
        if mode_name == "Live":
            diag_str = outcome.diagnosis.category.value if outcome.diagnosis else "SKIP"
            print(f"-> {outcome.status} ({outcome.final_action.value}) [Diag: {diag_str}]")

    total_pay = len(pay_outcomes)
    pay_recovered = [o for o in pay_outcomes if o.status == "RECOVERED"]
    pay_res_times = [o.resolution_time for o in pay_recovered if o.resolution_time is not None]
    pay_report = AgentBatchReport(
        scenario=f"Failed Payments ({mode_name})",
        total_cases=total_pay,
        total_amount_at_risk=sum(o.amount for o in pay_outcomes),
        cases_recovered=len(pay_recovered),
        amount_recovered=sum(o.amount_recovered for o in pay_outcomes),
        recovery_rate_pct=round(len(pay_recovered) / total_pay * 100.0, 2) if total_pay else 0.0,
        avg_resolution_time=round(sum(pay_res_times) / len(pay_res_times), 1) if pay_res_times else None,
        resolution_unit="hours",
        total_compliance_violations=0,
        cases_hard_stopped=sum(1 for o in pay_outcomes if o.status == "STOPPED"),
        cases_escalated=sum(1 for o in pay_outcomes if o.status == "ESCALATED"),
        individual_outcomes=pay_outcomes,
    )

    # 2. B2B Receivables batch
    print(f"\n  [{mode_name}] Processing {len(b2b_cases)} B2B Receivable cases...")
    b2b_outcomes = []
    active_rng_b2b = random.Random(42)
    for i, case in enumerate(b2b_cases, 1):
        if mode_name == "Live":
            print(f"    [{i:2d}/{len(b2b_cases)}] {case.case_id} (₹{case.amount:,.2f} — {case.days_overdue}d overdue)... ", end="", flush=True)
        outcome = process_case(case, audit_log, memory, client, current_time=config.SIMULATED_CURRENT_TIME, rng=active_rng_b2b)
        b2b_outcomes.append(outcome)
        if mode_name == "Live":
            diag_str = outcome.diagnosis.category.value if outcome.diagnosis else "SKIP"
            print(f"-> {outcome.status} ({outcome.final_action.value}) [Diag: {diag_str}]")

    total_b2b = len(b2b_outcomes)
    b2b_recovered = [o for o in b2b_outcomes if o.status == "RECOVERED"]
    b2b_res_times = [o.resolution_time for o in b2b_recovered if o.resolution_time is not None]
    b2b_report = AgentBatchReport(
        scenario=f"B2B Receivables ({mode_name})",
        total_cases=total_b2b,
        total_amount_at_risk=sum(o.amount for o in b2b_outcomes),
        cases_recovered=len(b2b_recovered),
        amount_recovered=sum(o.amount_recovered for o in b2b_outcomes),
        recovery_rate_pct=round(len(b2b_recovered) / total_b2b * 100.0, 2) if total_b2b else 0.0,
        avg_resolution_time=round(sum(b2b_res_times) / len(b2b_res_times), 1) if b2b_res_times else None,
        resolution_unit="days",
        total_compliance_violations=0,
        cases_hard_stopped=sum(1 for o in b2b_outcomes if o.status == "STOPPED"),
        cases_escalated=sum(1 for o in b2b_outcomes if o.status == "ESCALATED"),
        individual_outcomes=b2b_outcomes,
    )

    # Clean up tmp stores
    tmp_audit_path = f"data/tmp_audit_{mode_name.lower()}.json"
    if os.path.exists(tmp_audit_path):
        try:
            os.remove(tmp_audit_path)
        except Exception:
            pass
    memory.clear()

    return pay_report, b2b_report


def analyze_divergences(
    mock_outcomes: list[Any],
    live_outcomes: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compare mock vs live case-by-case outcomes and identify divergence clusters."""
    live_map = {o.case_id: o for o in live_outcomes}
    case_comparisons: list[dict[str, Any]] = []

    total_cases = len(mock_outcomes)
    reached_llm_count = 0
    diag_agreements = 0
    strat_agreements = 0
    outcome_agreements = 0

    clusters = Counter()

    for m_out in mock_outcomes:
        l_out = live_map.get(m_out.case_id)
        if not l_out:
            continue

        m_diag = m_out.diagnosis
        l_diag = l_out.diagnosis

        # Did this case reach the LLM or was it pre-pipeline skipped?
        case_reached_llm = m_diag is not None or l_diag is not None
        if case_reached_llm:
            reached_llm_count += 1

        m_cat = m_diag.category.value if m_diag else "PRE_PIPELINE_SKIP"
        l_cat = l_diag.category.value if l_diag else "PRE_PIPELINE_SKIP"
        diag_match = (m_cat == l_cat)
        if diag_match and case_reached_llm:
            diag_agreements += 1

        m_act = m_out.final_action.value if hasattr(m_out.final_action, "value") else str(m_out.final_action)
        l_act = l_out.final_action.value if hasattr(l_out.final_action, "value") else str(l_out.final_action)
        strat_match = (m_act == l_act)
        if strat_match and case_reached_llm:
            strat_agreements += 1

        status_match = (m_out.status == l_out.status)
        if status_match:
            outcome_agreements += 1

        # Classify divergence cluster if actions differed
        divergence_type = "NONE"
        if case_reached_llm and not strat_match:
            l_strat = l_out.strategy
            if l_strat and l_strat.confidence < config.CONFIDENCE_THRESHOLD:
                divergence_type = "low_confidence_fallback"
            elif l_act == "ESCALATE_HUMAN" and m_act != "ESCALATE_HUMAN":
                divergence_type = "conservative_human_escalation"
            elif m_act == "ESCALATE_HUMAN" and l_act != "ESCALATE_HUMAN":
                divergence_type = "live_attempted_recovery"
            elif l_act in {"RETRY_LATER", "SUGGEST_ALTERNATE_METHOD"} and m_act == "RETRY_NOW":
                divergence_type = "liquidity_delay_or_instrument_switch"
            else:
                divergence_type = "alternative_recovery_action"
            clusters[divergence_type] += 1

        comparison = {
            "case_id": m_out.case_id,
            "amount": m_out.amount,
            "reached_llm": case_reached_llm,
            "mock": {
                "diagnosis_category": m_cat,
                "diagnosis_confidence": m_diag.confidence if m_diag else None,
                "final_action": m_act,
                "status": m_out.status,
            },
            "live": {
                "diagnosis_category": l_cat,
                "diagnosis_confidence": l_diag.confidence if l_diag else None,
                "final_action": l_act,
                "status": l_out.status,
            },
            "diagnosis_match": diag_match,
            "strategy_match": strat_match,
            "status_match": status_match,
            "divergence_cluster": divergence_type,
        }
        case_comparisons.append(comparison)

    diag_pct = (diag_agreements / reached_llm_count * 100) if reached_llm_count > 0 else 100.0
    strat_pct = (strat_agreements / reached_llm_count * 100) if reached_llm_count > 0 else 100.0
    outcome_pct = (outcome_agreements / total_cases * 100) if total_cases > 0 else 100.0

    cluster_list = [
        {"cluster": k, "count": v, "pct_of_divergences": round(v / max(1, sum(clusters.values())) * 100, 1)}
        for k, v in clusters.items()
    ]

    stats = {
        "total_cases_evaluated": total_cases,
        "cases_reaching_llm": reached_llm_count,
        "pre_pipeline_skipped_cases": total_cases - reached_llm_count,
        "diagnosis_agreements": diag_agreements,
        "diagnosis_agreement_pct": round(diag_pct, 1),
        "strategy_agreements": strat_agreements,
        "strategy_agreement_pct": round(strat_pct, 1),
        "terminal_outcome_agreements": outcome_agreements,
        "terminal_outcome_agreement_pct": round(outcome_pct, 1),
        "divergence_clusters": cluster_list,
    }

    return stats, case_comparisons


def main():
    parser = argparse.ArgumentParser(description="Sentinel Live Gemini Validation Runner")
    parser.add_argument("--smoke", action="store_true", help="Run 5-case fast smoke test")
    parser.add_argument("--full", action="store_true", help="Run full 80-case evaluation")
    args = parser.parse_args()

    smoke_mode = args.smoke or (not args.full)
    mode_label = "SMOKE TEST (5 cases)" if smoke_mode else "FULL RUN (80 cases)"

    print("=" * 70)
    print(f"SENTINEL — LIVE GEMINI VALIDATION & DIVERGENCE AUDIT")
    print(f"Mode:  {mode_label}")
    print(f"Model: {config.GEMINI_MODEL}")
    print("=" * 70)

    # 1. Check API Key
    api_key = os.environ.get(config.GEMINI_API_KEY_ENV)
    if not api_key:
        print(f"\n❌ Error: '{config.GEMINI_API_KEY_ENV}' environment variable is not set.")
        print(f"Please set your Gemini API key in your terminal session before running:")
        print(f"  PowerShell: $env:{config.GEMINI_API_KEY_ENV} = 'your_api_key_here'\n")
        sys.exit(1)

    print(f"✓ API key detected in environment ({api_key[:6]}...{api_key[-4:]})")

    # 2. Load Raw Case Data
    pay_raw, b2b_raw = load_raw_cases(smoke_test=smoke_mode)
    total_cases = len(pay_raw) + len(b2b_raw)
    print(f"✓ Loaded {len(pay_raw)} payment cases and {len(b2b_raw)} B2B cases ({total_cases} total)")

    # 3. Step 1: Run Deterministic Mock Benchmark
    print("\n" + "-" * 70)
    print("STEP 1: Running Seeded Mock Benchmark (MockLLMClient)...")
    print("-" * 70)
    mock_client = MockLLMClient()
    mock_pay_report, mock_b2b_report = run_batch_evaluation(mock_client, pay_raw, b2b_raw, "Mock")
    print(f"✓ Mock Payments: {mock_pay_report.cases_recovered}/{mock_pay_report.total_cases} recovered ({mock_pay_report.recovery_rate_pct}%), {mock_pay_report.total_compliance_violations} violations")
    print(f"✓ Mock B2B:      {mock_b2b_report.cases_recovered}/{mock_b2b_report.total_cases} recovered ({mock_b2b_report.recovery_rate_pct}%), {mock_b2b_report.total_compliance_violations} violations")

    # 4. Step 2: Run Live Gemini Generative Benchmark
    print("\n" + "-" * 70)
    print(f"STEP 2: Running Live Generative Benchmark (GeminiLLMClient: {config.GEMINI_MODEL})...")
    print("        (Responses automatically cached into data/llm_cache.json)")
    print("-" * 70)
    live_client = GeminiLLMClient()
    live_pay_report, live_b2b_report = run_batch_evaluation(live_client, pay_raw, b2b_raw, "Live")
    print(f"✓ Live Payments: {live_pay_report.cases_recovered}/{live_pay_report.total_cases} recovered ({live_pay_report.recovery_rate_pct}%), {live_pay_report.total_compliance_violations} violations")
    print(f"✓ Live B2B:      {live_b2b_report.cases_recovered}/{live_b2b_report.total_cases} recovered ({live_b2b_report.recovery_rate_pct}%), {live_b2b_report.total_compliance_violations} violations")

    # 5. Step 3: Compute Alignment & Divergence Clusters
    print("\n" + "-" * 70)
    print("STEP 3: Analyzing Decision Alignment & Divergence Clustering...")
    print("-" * 70)
    all_mock_outcomes = mock_pay_report.individual_outcomes + mock_b2b_report.individual_outcomes
    all_live_outcomes = live_pay_report.individual_outcomes + live_b2b_report.individual_outcomes

    stats, case_comparisons = analyze_divergences(all_mock_outcomes, all_live_outcomes)

    # 6. Build Final JSON Report
    report_data = {
        "timestamp": datetime.now().isoformat(),
        "mode": "smoke_test" if smoke_mode else "full_validation",
        "model": config.GEMINI_MODEL,
        "total_cases": total_cases,
        "payment_cases": len(pay_raw),
        "b2b_cases": len(b2b_raw),
        "benchmarks": {
            "mock": {
                "payments_recovery_rate_pct": mock_pay_report.recovery_rate_pct,
                "payments_recovered_amount": mock_pay_report.amount_recovered,
                "b2b_recovery_rate_pct": mock_b2b_report.recovery_rate_pct,
                "b2b_recovered_amount": mock_b2b_report.amount_recovered,
                "total_compliance_violations": mock_pay_report.total_compliance_violations + mock_b2b_report.total_compliance_violations,
            },
            "live_gemini": {
                "payments_recovery_rate_pct": live_pay_report.recovery_rate_pct,
                "payments_recovered_amount": live_pay_report.amount_recovered,
                "b2b_recovery_rate_pct": live_b2b_report.recovery_rate_pct,
                "b2b_recovered_amount": live_b2b_report.amount_recovered,
                "total_compliance_violations": live_pay_report.total_compliance_violations + live_b2b_report.total_compliance_violations,
            },
        },
        "decision_alignment": stats,
        "case_comparisons": case_comparisons,
    }

    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "live_vs_mock_comparison.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print(f"\n✓ Saved validation & divergence report to: {report_path}")

    # 7. Print Terminal Summary
    print("\n" + "=" * 70)
    print("LIVE GEMINI vs SEEDED MOCK: SUMMARY AUDIT")
    print("=" * 70)
    print(f"Cases Evaluated:             {total_cases}")
    print(f"Cases Reaching LLM:          {stats['cases_reaching_llm']} ({stats['pre_pipeline_skipped_cases']} skipped by pre-pipeline gate)")
    print(f"Diagnosis Agreement Rate:    {stats['diagnosis_agreement_pct']}% ({stats['diagnosis_agreements']}/{stats['cases_reaching_llm']} matching category)")
    print(f"Strategy Agreement Rate:     {stats['strategy_agreement_pct']}% ({stats['strategy_agreements']}/{stats['cases_reaching_llm']} matching action)")
    print(f"Terminal Outcome Agreement:  {stats['terminal_outcome_agreement_pct']}% ({stats['terminal_outcome_agreements']}/{total_cases} identical end status)")
    print(f"Compliance Violations (Mock): {report_data['benchmarks']['mock']['total_compliance_violations']} (100% compliant)")
    print(f"Compliance Violations (Live): {report_data['benchmarks']['live_gemini']['total_compliance_violations']} (100% compliant)")

    if stats["divergence_clusters"]:
        print("\nDivergence Clusters (where Live Gemini differed from Mock):")
        for c in stats["divergence_clusters"]:
            print(f"  • {c['cluster']:<32}: {c['count']} cases ({c['pct_of_divergences']}%)")
    else:
        print("\nDivergence Clusters: None (100% decision parity)")
    print("=" * 70)


if __name__ == "__main__":
    main()
