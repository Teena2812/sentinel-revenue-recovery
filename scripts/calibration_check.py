"""
Calibration Check — Evaluates confidence calibration and fallback ladder effectiveness.
Reads strictly from reports/payment_batch_breakdown.csv and reports/b2b_batch_breakdown.csv.
"""

import csv
import os
import sys

# Ensure working directory is project root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

RECOVERY_ACTIONS = {
    "RETRY_NOW",
    "RETRY_LATER",
    "SUGGEST_ALTERNATE_METHOD",
    "SEND_REMINDER",
    "OFFER_PAYMENT_PLAN",
    "ESCALATE_TONE",
}


def analyze_file(filepath: str, label: str) -> None:
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return

    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_count = len(rows)

    # 2. Low confidence fallback (< 0.85 threshold)
    low_conf_cases = [
        r["case_id"]
        for r in rows
        if r.get("escalation_reason") == "fallback_ladder_low_confidence"
    ]

    # 3. Conflicting signals fallback
    conflict_cases = [
        r["case_id"]
        for r in rows
        if r.get("escalation_reason") == "fallback_ladder_conflicting_signals"
    ]

    # 4. Other cases that went through normal recovery actions with status RECOVERED or FAILED
    auto_recovered = []
    auto_failed = []

    for r in rows:
        reason = r.get("escalation_reason", "")
        status = r.get("status", "")
        action = r.get("final_action", "")

        # Exclude fallback ladder escalations
        if reason in {"fallback_ladder_low_confidence", "fallback_ladder_conflicting_signals"}:
            continue

        if status in {"RECOVERED", "FAILED"} and action in RECOVERY_ACTIONS:
            if status == "RECOVERED":
                auto_recovered.append(r["case_id"])
            elif status == "FAILED":
                auto_failed.append(r["case_id"])

    total_auto = len(auto_recovered) + len(auto_failed)
    rec_pct = (len(auto_recovered) / total_auto * 100.0) if total_auto > 0 else 0.0
    fail_pct = (len(auto_failed) / total_auto * 100.0) if total_auto > 0 else 0.0

    print("=" * 70)
    print(f"CALIBRATION & CONFIDENCE AUDIT: {label.upper()}")
    print(f"Source File: {filepath}")
    print("=" * 70)
    print(f"1. Total Case Count: {total_count}")
    print(f"\n2. Fallback Ladder (Low Confidence < 0.85):")
    print(f"   Count:    {len(low_conf_cases)}")
    print(f"   Case IDs: {low_conf_cases if low_conf_cases else 'None'}")
    print(f"\n3. Fallback Ladder (Conflicting Signals):")
    print(f"   Count:    {len(conflict_cases)}")
    print(f"   Case IDs: {conflict_cases if conflict_cases else 'None'}")
    print(f"\n4. High-Confidence Auto-Executed Recovery Actions:")
    print(f"   Recovery Actions: {', '.join(sorted(RECOVERY_ACTIONS))}")
    print(f"   Total Auto-Executed (RECOVERED + FAILED): {total_auto}")
    print(f"   - Actually RECOVERED: {len(auto_recovered)} ({rec_pct:.1f}%)")
    print(f"   - Ended up FAILED:    {len(auto_failed)} ({fail_pct:.1f}%)")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    analyze_file("reports/payment_batch_breakdown.csv", "Failed Payments")
    analyze_file("reports/b2b_batch_breakdown.csv", "B2B Receivables")
