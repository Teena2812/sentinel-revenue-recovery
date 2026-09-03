"""
Configuration — single source of truth for all tunable parameters.

All thresholds here are starting parameters, not validated numbers.
The 0.85 confidence threshold in particular is explicitly configurable
and will be tested/calibrated in Phase 5 (see Design Decisions Addendum §3).

ALL DATA IN THIS PROJECT IS SIMULATED — never imply real Razorpay data.
"""


import os
from datetime import datetime


def _load_dotenv() -> None:
    """Auto-load variables from .env into os.environ if present, without overriding existing vars."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(base_dir, ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass


_load_dotenv()


# --- Simulation Time ---
# PERMANENT ARBITRARY SIMULATION ANCHOR:
# Fixed reference timestamp (2026-08-24 12:00:00 IST) for deterministic benchmark evaluation,
# contact-hour checks, and invoice days_overdue calculations.
# CRITICAL: This is a permanent, arbitrary simulation anchor and MUST NEVER be updated to match
# real wall-clock time, ensuring zero calendar drift across different runs or sessions.
SIMULATED_CURRENT_TIME = datetime(2026, 8, 24, 12, 0, 0)

# --- Confidence & Auto-Execute ---
# Auto-execute only when ALL of: confidence >= threshold, no conflicting signal,
# gate passes, action is reversible/low-risk. (Addendum §3)
CONFIDENCE_THRESHOLD = 0.85

# --- Cost Thresholds ---
# Below these amounts, skip the full LLM diagnosis pipeline — use cheap
# automatic path only. Separate thresholds because the B2B invoice range
# (₹10,000+) makes ₹500 meaningless for that scenario.
MIN_RECOVERY_AMOUNT_PAYMENT = 500    # ₹ — for failed payment cases
MIN_RECOVERY_AMOUNT_B2B = 5000       # ₹ — for B2B receivable cases

# --- Attempt / Retry Caps ---
MAX_ATTEMPTS_PAYMENT = 5   # Maximum retry attempts for failed payments
MAX_ATTEMPTS_B2B = 4       # Maximum recovery attempts for B2B receivables

# --- Contact Hour Window (RBI Fair Practices Code) ---
CONTACT_HOUR_START = 8     # 8:00 AM IST — earliest permitted contact
CONTACT_HOUR_END = 19      # 7:00 PM IST — latest permitted contact

# --- Fatigue Override Caps ---
# If a case's contact/retry count exceeds this cap, downgrade the
# relationship tier by one level regardless of score. (Addendum §2)
FATIGUE_CAP_PAYMENT = 2    # For failed payment cases
FATIGUE_CAP_B2B = 3        # For B2B receivable cases

# --- Relationship Tier Weights & Cutoffs ---
# Relationship Score (0–100) = W_V×V + W_H×H + W_T×T
# V = value percentile, H = historical reliability, T = engagement tenure
# Each term normalized 0–1, score scaled to 0–100. (Addendum §2)
RELATIONSHIP_WEIGHT_VALUE = 0.40       # Value percentile weight
RELATIONSHIP_WEIGHT_HISTORY = 0.35     # Historical reliability weight
RELATIONSHIP_WEIGHT_TENURE = 0.25      # Engagement tenure weight
RELATIONSHIP_TIER_HIGH = 70            # Score >= 70 → HIGH
RELATIONSHIP_TIER_MEDIUM = 40          # 40 <= Score < 70 → MEDIUM
# Score < 40 → LOW

# Default historical reliability for customers/debtors with no prior history.
# Neutral 0.5, not 0 or 1 — a new relationship isn't assumed good or bad.
RELATIONSHIP_DEFAULT_HISTORY = 0.5

# --- Simulation Outcome Probabilities ---
# Shared between the baseline and the agent system so the comparison is fair.
# The agent wins by picking better actions, not by having more generous odds.

# Per-retry success probability by failure code (for failed payments)
PAYMENT_RETRY_SUCCESS_PROB = {
    "INSUFFICIENT_FUNDS": 0.20,
    "BANK_TIMEOUT": 0.60,
    "AUTH_FAILURE": 0.30,
    "GATEWAY_ERROR": 0.50,
    "FRAUD_REJECTION": 0.00,   # Never succeeds — must hard-stop
}

# Per-reminder success probability by overdue bucket (for B2B receivables)
# Decreases with days overdue — stale cases are harder to recover.
B2B_REMINDER_SUCCESS_PROB = {
    "early": 0.40,     # 1–10 days overdue
    "mid": 0.25,       # 11–30 days overdue
    "late": 0.15,      # 31–60 days overdue
    "stale": 0.08,     # 61+ days overdue
}

# Bonus modifier context given to Strategy prompt (prompt-context ONLY, never in Execution ground-truth simulation).
# Applied in Phase 2+ to inform action ranking — the baseline never gets this bonus.
AGENT_STRATEGY_MATCH_BONUS = 0.15

# --- LLM Configuration ---
LLM_MODE = "mock"                          # "mock" or "live" — single switch for demo/testing
GEMINI_MODEL = "gemini-flash-lite-latest"  # Google Gemini model for live pipeline
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"      # Environment variable for Gemini API key

# --- Orchestrator Settings ---
AGENT_LOOP_MAX_ATTEMPTS = 3           # Attempt budget per case (strictly symmetric with baseline's 3-attempt budget)
MAX_GATE_REPROPOSALS = 1              # After 1 re-proposal rejection (2 total), auto-escalate
RETRY_LATER_DELAY_HOURS = 6           # Simulated flat delay for resolution-time calculation

# --- Memory Cold Start ---
MEMORY_DEFAULT_SUCCESS_RATE = 0.5     # Neutral default success rate when no outcomes recorded

