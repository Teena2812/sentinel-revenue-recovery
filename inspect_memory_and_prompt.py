"""
Standalone Inspection Script:
1. Runs the payment batch and exports data/agent_memory.json.
2. Prints all per-(category, action) memory entries (demonstrating dynamic weights vs 0.50 default and absence of STOP/ESCALATE_HUMAN/WAIT).
3. Generates and prints a real, fully-formatted Strategy Agent prompt containing the real _format_strategy_context() output.
"""

import json
import os
import random
from core import config
from core.audit_log import AuditLog
from core.memory import Memory
from core.orchestrator import process_payment_batch
from core.schemas import dict_to_failed_payment, ActionType
from agents.diagnosis import diagnose
from agents.strategy import _build_strategy_prompt
from agents.llm_client import MockLLMClient

def main():
    # 1. Load payment cases
    with open("data/failed_payments.json", "r", encoding="utf-8") as f:
        cases_data = json.load(f)["cases"]
    payment_cases = [dict_to_failed_payment(c) for c in cases_data]

    # 2. Run payment batch with clean memory
    audit = AuditLog("data/agent_audit_log.json")
    memory = Memory("data/agent_memory.json")
    memory.clear()

    client = MockLLMClient()
    report = process_payment_batch(
        payment_cases,
        audit_log=audit,
        memory=memory,
        llm_client=client,
        current_time=config.SIMULATED_CURRENT_TIME,
        rng=random.Random(42),
    )

    # Persist memory to file
    memory.save()

    print("=" * 70)
    print("1. RESULTING data/agent_memory.json CONTENTS (POST-PAYMENT BATCH)")
    print("=" * 70)
    with open("data/agent_memory.json", "r", encoding="utf-8") as f:
        mem_json = json.load(f)
    print(json.dumps(mem_json, indent=2))

    print("\n" + "=" * 70)
    print("2. COMPUTED SUCCESS RATES & DOUBLE-GATING AUDIT")
    print("=" * 70)
    routing_actions = {ActionType.STOP, ActionType.ESCALATE_HUMAN, ActionType.WAIT}
    routing_names = {a.value for a in routing_actions}

    raw_stats = memory.get_all_stats()
    found_routing = []

    for cat_name, actions in raw_stats.items():
        print(f"\nDiagnosis Category: [{cat_name}]")
        for act_name, data in actions.items():
            if act_name in routing_names:
                found_routing.append((cat_name, act_name))
            succ = data["successes"]
            tot = data["total"]
            rate = (succ / tot * 100.0) if tot > 0 else 50.0
            print(f"  • Action: {act_name:<26} -> {succ}/{tot} recovered ({rate:>5.1f}%) [Recent: {data.get('recent_history', [])}]")

    print("\nDouble-Gating Verification:")
    if not found_routing:
        print("  ✓ CONFIRMED: STOP, ESCALATE_HUMAN, and WAIT have ZERO entries in memory.")
    else:
        print(f"  ❌ Routing actions found in memory: {found_routing}")

    print("\n" + "=" * 70)
    print("3. REAL FULLY-FORMATTED STRATEGY PROMPT WITH COMPUTED MEMORY CONTEXT")
    print("=" * 70)

    # Pick a payment case to demonstrate real prompt generation
    sample_case = payment_cases[4]  # PAY-6dadd6c7-95a or another
    sample_diag = diagnose(sample_case, client)
    sample_context = memory.get_strategy_context(sample_diag.category)

    full_prompt = _build_strategy_prompt(sample_case, sample_diag, sample_context)
    print(full_prompt)
    print("=" * 70)

if __name__ == "__main__":
    main()
