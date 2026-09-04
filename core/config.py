"""
Configuration — single source of truth for all tunable parameters.

All thresholds here are starting parameters, not validated numbers.
The 0.85 confidence threshold in particular is explicitly configurable
and will be tested/calibrated in Phase 5 (see Design Decisions Addendum §3).

ALL DATA IN THIS PROJECT IS SIMULATED — never imply real Razorpay data.
"""


import json
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


def _load_rules_config() -> dict:
    """Load external business rules from config/rules_config.json.

    Loaded once at startup — no hot-reload. Returns hardcoded defaults
    if the file is missing or malformed, so the application always starts.
    """
    _DEFAULTS = {
        "attempt_caps":    {"payment": 5, "b2b": 4},
        "fatigue_caps":    {"payment": 2, "b2b": 3},
        "cost_thresholds": {"payment_min_recovery": 500, "b2b_min_recovery": 5000},
        "contact_hours":   {"start_hour": 8, "end_hour": 19},
        "confidence":      {"threshold": 0.85},
    }
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config", "rules_config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        # Merge: loaded values override defaults section-by-section
        merged = {**_DEFAULTS}
        for section, values in loaded.items():
            if section.startswith("_"):
                continue  # skip _comment keys
            if isinstance(values, dict):
                merged[section] = {**_DEFAULTS.get(section, {}), **values}
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return _DEFAULTS


_RULES = _load_rules_config()


# --- Simulation Time ---
# PERMANENT ARBITRARY SIMULATION ANCHOR:
# Fixed reference timestamp (2026-08-24 12:00:00 IST) for deterministic benchmark evaluation,
# contact-hour checks, and invoice days_overdue calculations.
# CRITICAL: This is a permanent, arbitrary simulation anchor and MUST NEVER be updated to match
# real wall-clock time, ensuring zero calendar drift across different runs or sessions.
SIMULATED_CURRENT_TIME = datetime(2026, 8, 24, 12, 0, 0)

# --- Confidence & Auto-Execute (backed by config/rules_config.json) ---
# Auto-execute only when ALL of: confidence >= threshold, no conflicting signal,
# gate passes, action is reversible/low-risk. (Prompt 7, Addendum §3)
CONFIDENCE_THRESHOLD = _RULES["confidence"]["threshold"]

# --- Cost Thresholds (backed by config/rules_config.json) ---
# Below these amounts, skip the full LLM diagnosis pipeline — use cheap
# automatic path only. Separate thresholds because the B2B invoice range
# (₹10,000+) makes ₹500 meaningless for that scenario.
MIN_RECOVERY_AMOUNT_PAYMENT = _RULES["cost_thresholds"]["payment_min_recovery"]
MIN_RECOVERY_AMOUNT_B2B     = _RULES["cost_thresholds"]["b2b_min_recovery"]

# --- Attempt / Retry Caps (backed by config/rules_config.json) ---
MAX_ATTEMPTS_PAYMENT = _RULES["attempt_caps"]["payment"]
MAX_ATTEMPTS_B2B     = _RULES["attempt_caps"]["b2b"]

# --- Contact Hour Window (backed by config/rules_config.json — RBI Fair Practices Code) ---
CONTACT_HOUR_START = _RULES["contact_hours"]["start_hour"]
CONTACT_HOUR_END   = _RULES["contact_hours"]["end_hour"]

# --- Fatigue Override Caps (backed by config/rules_config.json) ---
# If a case's contact/retry count exceeds this cap, downgrade the
# relationship tier by one level regardless of score. (Addendum §2)
FATIGUE_CAP_PAYMENT = _RULES["fatigue_caps"]["payment"]
FATIGUE_CAP_B2B     = _RULES["fatigue_caps"]["b2b"]

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

