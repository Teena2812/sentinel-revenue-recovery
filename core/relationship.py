"""
Relationship-Value Tier — Deterministic Computation.

This is plain code, never an LLM call. (Master prompt §4, rule 3; Addendum §2)

Formula:
  Relationship Score (0–100) = 0.40×V + 0.35×H + 0.25×T
  V = value percentile (this case's amount ranked against the portfolio)
  H = historical reliability (proportion of past cases resolved cleanly)
  T = engagement tenure (normalized transaction count)

Tier cutoffs:
  Score >= 70 → HIGH
  40 <= Score < 70 → MEDIUM
  Score < 40 → LOW

Fatigue override:
  If contact/retry count > cap, downgrade tier by one level.
  HIGH → MEDIUM, MEDIUM → LOW, LOW stays LOW.

Cold-start:
  If no prior history, H defaults to 0.5 (neutral). Logged explicitly.

Value percentile (V) requires the full batch — compute_tiers_for_batch()
does a second pass over all cases after the batch is fully generated.

Relationship-value and recovery-likelihood are deliberately separate.
(Addendum §2: "Why relationship-value and recovery-likelihood are kept separate")

ALL DATA IS SIMULATED.
"""

from __future__ import annotations

import logging

from core import config
from core.schemas import (
    B2BReceivableCase,
    Case,
    CaseType,
    CustomerHistory,
    FailedPaymentCase,
    RelationshipTier,
)

logger = logging.getLogger(__name__)


def _get_fatigue_cap(case: Case) -> int:
    """Return the fatigue cap for this case type."""
    if case.case_type == CaseType.FAILED_PAYMENT:
        return config.FATIGUE_CAP_PAYMENT
    return config.FATIGUE_CAP_B2B


def _get_contact_retry_count(case: Case) -> int:
    """Return the relevant contact/retry count for fatigue check."""
    if isinstance(case, B2BReceivableCase):
        return case.contact_count
    return case.attempt_count


def _get_history(case: Case) -> CustomerHistory:
    """Return the customer/debtor history for this case."""
    if isinstance(case, FailedPaymentCase):
        return case.customer_history
    return case.debtor_history


def _normalize_tenure(total_transactions: int, max_transactions: int = 50) -> float:
    """Normalize engagement tenure to 0–1 range.
    50 transactions is considered fully mature; above is capped at 1.0.
    """
    if max_transactions <= 0:
        return 0.0
    return min(1.0, total_transactions / max_transactions)


def compute_value_percentile(amount: float, all_amounts: list[float]) -> float:
    """Compute this case's value percentile against the portfolio.

    Returns a value in 0–1.  Requires the full batch's amounts.
    Uses the fraction of the batch that this amount is >= to.
    """
    if not all_amounts:
        return 0.5  # no context — neutral
    count_below = sum(1 for a in all_amounts if amount >= a)
    return count_below / len(all_amounts)


def compute_relationship_score(
    value_percentile: float,
    historical_reliability: float,
    tenure_normalized: float,
) -> float:
    """Compute the raw relationship score (0–100).

    Each input is 0–1. Output is scaled to 0–100.
    """
    w_v = config.RELATIONSHIP_WEIGHT_VALUE
    w_h = config.RELATIONSHIP_WEIGHT_HISTORY
    w_t = config.RELATIONSHIP_WEIGHT_TENURE

    score = (w_v * value_percentile + w_h * historical_reliability + w_t * tenure_normalized) * 100
    return round(score, 2)


def score_to_tier(score: float) -> RelationshipTier:
    """Map a raw score to a tier label."""
    if score >= config.RELATIONSHIP_TIER_HIGH:
        return RelationshipTier.HIGH
    elif score >= config.RELATIONSHIP_TIER_MEDIUM:
        return RelationshipTier.MEDIUM
    else:
        return RelationshipTier.LOW


def _downgrade_tier(tier: RelationshipTier) -> RelationshipTier:
    """Downgrade a tier by one level: HIGH→MEDIUM, MEDIUM→LOW, LOW→LOW."""
    if tier == RelationshipTier.HIGH:
        return RelationshipTier.MEDIUM
    elif tier == RelationshipTier.MEDIUM:
        return RelationshipTier.LOW
    return RelationshipTier.LOW


def compute_tier_for_case(
    case: Case,
    all_amounts: list[float],
) -> tuple[RelationshipTier, float, dict]:
    """Compute the relationship tier for a single case.

    Args:
        case: The case to evaluate.
        all_amounts: All case amounts in the same batch (for percentile).

    Returns:
        (tier, raw_score, debug_info) where debug_info contains the breakdown
        for audit logging.
    """
    history = _get_history(case)

    # --- V: value percentile ---
    v = compute_value_percentile(case.amount, all_amounts)

    # --- H: historical reliability ---
    h = history.reliability_ratio
    h_defaulted = not history.has_history
    if h_defaulted:
        h = config.RELATIONSHIP_DEFAULT_HISTORY
        logger.info("Case %s: H defaulted — no prior history (using %.1f)", case.case_id, h)

    # --- T: engagement tenure ---
    t = _normalize_tenure(history.total_transactions)

    # --- Raw score ---
    raw_score = compute_relationship_score(v, h, t)
    tier = score_to_tier(raw_score)

    # --- Fatigue override ---
    fatigue_cap = _get_fatigue_cap(case)
    contact_count = _get_contact_retry_count(case)
    fatigue_applied = False
    original_tier = tier

    if contact_count > fatigue_cap:
        tier = _downgrade_tier(tier)
        fatigue_applied = True
        logger.info(
            "Case %s: Fatigue override applied (contacts=%d > cap=%d): %s → %s",
            case.case_id, contact_count, fatigue_cap,
            original_tier.value, tier.value,
        )

    debug_info = {
        "value_percentile": round(v, 4),
        "historical_reliability": round(h, 4),
        "h_defaulted_no_history": h_defaulted,
        "tenure_normalized": round(t, 4),
        "raw_score": raw_score,
        "tier_before_fatigue": original_tier.value,
        "fatigue_applied": fatigue_applied,
        "contact_retry_count": contact_count,
        "fatigue_cap": fatigue_cap,
        "final_tier": tier.value,
    }

    return tier, raw_score, debug_info


def compute_tiers_for_batch(cases: list[Case]) -> list[tuple[Case, float, dict]]:
    """Compute relationship tiers for a full batch.

    IMPORTANT: This is a second-pass computation — the value percentile (V)
    requires all case amounts to exist before any tier can be computed.
    Calling this on a partial batch would quietly break every tier assignment.

    Returns a list of (case, raw_score, debug_info) with case.relationship_tier
    set in place.
    """
    all_amounts = [c.amount for c in cases]
    results = []

    for case in cases:
        tier, raw_score, debug_info = compute_tier_for_case(case, all_amounts)
        case.relationship_tier = tier
        results.append((case, raw_score, debug_info))

    return results
