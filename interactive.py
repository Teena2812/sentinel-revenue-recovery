"""
Interactive Recovery Agent Test Harness.
Allows judges and reviewers to input a custom, novel case and observe
the end-to-end multi-agent recovery loop with verified isolation.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

# Ensure root in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents.llm_client import GeminiLLMClient, MockLLMClient
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import process_case
from core.schemas import (
    CaseStatus,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    RelationshipTier,
)


def _prompt_choice(prompt_text: str, options: list[str], default_idx: int = 0) -> str:
    print(f"\n{prompt_text}")
    for i, opt in enumerate(options, 1):
        mark = " (default)" if (i - 1) == default_idx else ""
        print(f"  [{i}] {opt}{mark}")
    val = input(f"Select (1-{len(options)}) [default: {default_idx + 1}]: ").strip()
    if not val:
        return options[default_idx]
    try:
        idx = int(val) - 1
        if 0 <= idx < len(options):
            return options[idx]
    except ValueError:
        pass
    print(f"Invalid choice, using default: {options[default_idx]}")
    return options[default_idx]


def main():
    print("=" * 70)
    print("SENTINEL — INTERACTIVE AI REVENUE RECOVERY HARNESS")
    print("Test custom payment failure cases through the live multi-agent loop")
    print("=" * 70)

    # 1. Detect Mode
    api_key = os.environ.get(config.GEMINI_API_KEY_ENV)
    if api_key:
        client = GeminiLLMClient()
        print(f"\n[MODE]: LIVE MODE — using Gemini ({config.GEMINI_MODEL})")
    else:
        client = MockLLMClient()
        print("\n[MODE]: MOCK MODE — set GEMINI_API_KEY for live reasoning")

    # 2. Collect Custom Case Inputs
    print("\n" + "-" * 70)
    print("ENTER CUSTOM CASE PARAMETERS (press Enter to accept defaults)")
    print("-" * 70)

    # Amount
    amt_in = input("Payment Amount (₹) [default: 12500.00]: ").strip()
    try:
        amount = float(amt_in) if amt_in else 12500.00
    except ValueError:
        print("Invalid amount entered, using default ₹12,500.00")
        amount = 12500.00

    # Failure Code
    codes = [c.value for c in FailureCode]
    code_choice = _prompt_choice("Failure Code:", codes, default_idx=3)  # default: GATEWAY_ERROR
    failure_code = FailureCode(code_choice)

    # Attempt Count
    att_in = input("\nCurrent Attempt Count (1-5) [default: 1]: ").strip()
    try:
        attempt_count = int(att_in) if att_in else 1
    except ValueError:
        attempt_count = 1

    # Relationship Tier
    tiers = [t.value for t in RelationshipTier]
    tier_choice = _prompt_choice("Relationship Tier:", tiers, default_idx=1)  # default: MEDIUM
    rel_tier = RelationshipTier(tier_choice)

    # Fraud Flag
    fraud_in = input("\nSimulate Active Fraud Flag? (y/N) [default: N]: ").strip().lower()
    fraud_flag = fraud_in in ("y", "yes")

    case_id = f"PAY-CUSTOM-{uuid.uuid4().hex[:6].upper()}"

    # 3. Construct Case Object
    case = FailedPaymentCase(
        case_id=case_id,
        amount=amount,
        failure_code=failure_code,
        timestamp=config.SIMULATED_CURRENT_TIME,
        attempt_count=attempt_count,
        customer_id="CUST-INTERACTIVE",
        customer_history=CustomerHistory(
            reliability_ratio=0.85 if rel_tier == RelationshipTier.HIGH else (0.50 if rel_tier == RelationshipTier.MEDIUM else 0.20),
            total_transactions=15,
            total_amount=amount * 4,
            has_history=True,
        ),
        status=CaseStatus.OPEN,
        fraud_flag=fraud_flag,
        relationship_tier=rel_tier,
    )

    # 4. Verified Isolation Setup (NEVER calls .save())
    audit_log = AuditLog(path="interactive_audit_tmp.json")
    memory = Memory(storage_path="nonexistent_interactive_memory.json")

    print("\n" + "=" * 70)
    print(f"EXECUTING MULTI-AGENT RECOVERY PIPELINE FOR: {case.case_id}")
    print(f"Amount: ₹{case.amount:,.2f} | Code: {case.failure_code.value} | Tier: {case.relationship_tier.value} | Attempt: {case.attempt_count} | Fraud: {case.fraud_flag}")
    print("=" * 70)

    # 5. Execute Core Pipeline
    outcome = process_case(
        case,
        audit_log=audit_log,
        memory=memory,
        llm_client=client,
        current_time=config.SIMULATED_CURRENT_TIME,
    )

    # 6. Render Full Trace (Beat 2 style)
    if outcome.diagnosis:
        print("\n1. [DIAGNOSIS AGENT]")
        print(f"   Root Cause: {outcome.diagnosis.root_cause}")
        print(f"   Category:   {outcome.diagnosis.category.value} (Confidence: {outcome.diagnosis.confidence:.2f})")
        print(f"   Reasoning:  {outcome.diagnosis.reasoning}")

    if outcome.strategy:
        print("\n2. [STRATEGY AGENT]")
        print(f"   Proposed Action: {outcome.strategy.proposed_action.value} (Confidence: {outcome.strategy.confidence:.2f})")
        print(f"   Risk Assessment: {outcome.strategy.risk_assessment}")
        print(f"   Reasoning:       {outcome.strategy.reasoning}")

    if outcome.gate_decision:
        verdict_str = "APPROVED" if outcome.gate_decision.approved else "BLOCKED"
        print(f"\n3. [DETERMINISTIC GATE]")
        print(f"   Verdict: {verdict_str}")
        for r in outcome.gate_decision.results:
            status_str = "PASS" if r.passed else "VIOLATION"
            print(f"   • {r.rule_name:<16}: [{status_str}] {r.reason}")

    if outcome.execution:
        print(f"\n4. [EXECUTION AGENT]")
        print(f"   Outcome: {outcome.execution.status} ({outcome.execution.detail})")
        if outcome.execution.resolution_time is not None:
            print(f"   Resolution Time: {outcome.execution.resolution_time} {outcome.resolution_unit}")

    # Summary
    print("\n" + "=" * 70)
    print("FINAL RECOVERY OUTCOME")
    print("=" * 70)
    print(f"Status:            {outcome.status}")
    print(f"Final Action:      {outcome.final_action.value}")
    print(f"Amount Recovered:  ₹{outcome.amount_recovered:,.2f} of ₹{outcome.amount:,.2f}")
    print(f"Attempts Made:     {outcome.attempts_made}")
    if outcome.escalation_reason:
        print(f"Routing Reason:    {outcome.escalation_reason}")
    print(f"Summary:           {outcome.reasoning_summary}")
    print("=" * 70)


if __name__ == "__main__":
    main()
