"""
Live Gemini Proof — Isolated Live API Verification.
Targets case PAY-6dadd6c7-95a from data/failed_payments.json.
Runs through GeminiLLMClient for Diagnosis and Strategy.
Saves raw JSON responses to reports/live_gemini_proof.json.
"""

import hashlib
import json
import os
import sys

# Ensure project root is in path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core import config
from core.schemas import dict_to_failed_payment
from core.memory import Memory
from agents.llm_client import GeminiLLMClient, LLMError
from agents.diagnosis import diagnose
from agents.strategy import propose_strategy


def main():
    print("=" * 70)
    print("SENTINEL — LIVE GOOGLE GEMINI PROOF")
    print(f"Target Model: {config.GEMINI_MODEL}")
    print("=" * 70)

    # 1. Environment Variable Check
    api_key = os.environ.get(config.GEMINI_API_KEY_ENV)
    if not api_key:
        print(f"\n❌ Error: '{config.GEMINI_API_KEY_ENV}' environment variable is not set.")
        print(f"Please set your Gemini API key before running this script:")
        print(f"  PowerShell: $env:{config.GEMINI_API_KEY_ENV} = 'your_api_key_here'")
        print(f"  CMD:        set {config.GEMINI_API_KEY_ENV}=your_api_key_here\n")
        sys.exit(1)

    # 2. Load Target Case
    data_path = "data/failed_payments.json"
    if not os.path.exists(data_path):
        print(f"❌ Error: {data_path} not found.")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        cases_data = json.load(f)["cases"]

    target_id = "PAY-6dadd6c7-95a"
    raw_case = next((c for c in cases_data if c["case_id"] == target_id), None)
    if not raw_case:
        print(f"❌ Error: Case {target_id} not found in {data_path}.")
        sys.exit(1)

    case = dict_to_failed_payment(raw_case)
    print(f"\nTarget Case: {case.case_id}")
    print(f"  Amount:            ₹{case.amount:,.2f}")
    print(f"  Failure Code:      {case.failure_code.value}")
    print(f"  Attempt Count:     {case.attempt_count}")
    print(f"  Relationship Tier: {case.relationship_tier.value if case.relationship_tier else 'N/A'}")

    # 3. Initialize Live Gemini Client
    client = GeminiLLMClient()
    memory = Memory("data/agent_memory.json")

    # ── Cache transparency check ──────────────────────────────────────────────
    # GeminiLLMClient silently serves cached responses for prompts it has seen
    # before. Pre-check the cache so a reviewer can tell whether each step is
    # a fresh network call or a stored hit.
    def _is_cached(prompt: str) -> bool:
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return key in client._cache
    # ─────────────────────────────────────────────────────────────────────────

    # 4. Live Diagnosis
    from agents.diagnosis import _build_diagnosis_prompt
    diag_prompt = _build_diagnosis_prompt(case)
    if _is_cached(diag_prompt):
        print("\n⚠️  [CACHE HIT — serving stored response, not a fresh Gemini call] (Diagnosis)")
    print("\n" + "-" * 70)
    print("STEP 1: LIVE DIAGNOSIS (Gemini Flash)")
    print("-" * 70)
    try:
        diag = diagnose(case, client)
        print(f"  Root Cause: {diag.root_cause}")
        print(f"  Category:   {diag.category.value}")
        print(f"  Confidence: {diag.confidence:.2f}")
        print(f"  Reasoning:  {diag.reasoning}")
    except LLMError as e:
        print(f"❌ Diagnosis API call failed: {e}")
        sys.exit(1)

    # 5. Rate-Limit Pause (Free Tier RPM preservation)
    print("\nPausing 20s to preserve Gemini Free Tier rate-limit window...")
    import time
    time.sleep(20)

    # 6. Live Strategy
    from agents.strategy import _build_strategy_prompt
    strategy_context = memory.get_strategy_context(diag.category)
    strat_prompt = _build_strategy_prompt(case, diag, strategy_context)
    if _is_cached(strat_prompt):
        print("\n⚠️  [CACHE HIT — serving stored response, not a fresh Gemini call] (Strategy)")
    print("\n" + "-" * 70)
    print("STEP 2: LIVE STRATEGY PROPOSAL (Gemini Flash)")
    print("-" * 70)
    try:
        strat = propose_strategy(case, diag, strategy_context, client)
        print(f"  Proposed Action: {strat.proposed_action.value}")
        print(f"  Confidence:      {strat.confidence:.2f}")
        print(f"  Risk Assessment: {strat.risk_assessment}")
        print(f"  Reasoning:       {strat.reasoning}")
    except LLMError as e:
        print(f"❌ Strategy API call failed: {e}")
        sys.exit(1)

    # 6. Save Raw JSON Output
    output_data = {
        "case_id": case.case_id,
        "amount": case.amount,
        "failure_code": case.failure_code.value,
        "model": config.GEMINI_MODEL,
        "diagnosis": {
            "root_cause": diag.root_cause,
            "category": diag.category.value,
            "confidence": diag.confidence,
            "reasoning": diag.reasoning,
        },
        "strategy": {
            "proposed_action": strat.proposed_action.value,
            "confidence": strat.confidence,
            "risk_assessment": strat.risk_assessment,
            "reasoning": strat.reasoning,
        },
    }

    os.makedirs("reports", exist_ok=True)
    report_path = "reports/live_gemini_proof.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print("\n" + "=" * 70)
    print(f"✓ Live response saved to: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
