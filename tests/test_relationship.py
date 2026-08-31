"""
Tests for core/relationship.py — deterministic relationship tier computation.

Validates:
- Known inputs → expected tier (hand-calculated)
- Cold-start handling (no history → H defaults to 0.5)
- Fatigue override (high-score case with >cap contacts → tier downgraded)
- Edge cases at tier boundaries (scores exactly 40 and 70)
- Second-pass batch computation
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from datetime import datetime

from core.relationship import (
    compute_relationship_score,
    compute_tier_for_case,
    compute_tiers_for_batch,
    compute_value_percentile,
    score_to_tier,
    _normalize_tenure,
)
from core.schemas import (
    B2BReceivableCase,
    CustomerHistory,
    FailedPaymentCase,
    FailureCode,
    RelationshipTier,
)


class TestValuePercentile(unittest.TestCase):
    """Value percentile computation against a portfolio."""

    def test_highest_amount(self):
        pct = compute_value_percentile(1000, [100, 200, 500, 1000])
        self.assertEqual(pct, 1.0)

    def test_lowest_amount(self):
        pct = compute_value_percentile(100, [100, 200, 500, 1000])
        self.assertEqual(pct, 0.25)

    def test_empty_portfolio(self):
        pct = compute_value_percentile(500, [])
        self.assertEqual(pct, 0.5)  # Neutral fallback

    def test_middle_amount(self):
        pct = compute_value_percentile(500, [100, 200, 500, 800, 1000])
        self.assertEqual(pct, 0.6)  # 3 of 5 amounts are <= 500


class TestRelationshipScore(unittest.TestCase):
    """Raw score computation: 0.40×V + 0.35×H + 0.25×T, scaled to 0–100."""

    def test_all_max(self):
        score = compute_relationship_score(1.0, 1.0, 1.0)
        self.assertEqual(score, 100.0)

    def test_all_zero(self):
        score = compute_relationship_score(0.0, 0.0, 0.0)
        self.assertEqual(score, 0.0)

    def test_known_values(self):
        # V=0.8, H=0.6, T=0.4 → (0.40*0.8 + 0.35*0.6 + 0.25*0.4)*100
        # = (0.32 + 0.21 + 0.10)*100 = 63.0
        score = compute_relationship_score(0.8, 0.6, 0.4)
        self.assertEqual(score, 63.0)


class TestScoreToTier(unittest.TestCase):
    """Tier cutoff mapping."""

    def test_high(self):
        self.assertEqual(score_to_tier(75), RelationshipTier.HIGH)
        self.assertEqual(score_to_tier(100), RelationshipTier.HIGH)

    def test_high_boundary(self):
        self.assertEqual(score_to_tier(70), RelationshipTier.HIGH)

    def test_medium(self):
        self.assertEqual(score_to_tier(50), RelationshipTier.MEDIUM)

    def test_medium_boundary(self):
        self.assertEqual(score_to_tier(40), RelationshipTier.MEDIUM)

    def test_low(self):
        self.assertEqual(score_to_tier(20), RelationshipTier.LOW)

    def test_low_boundary(self):
        self.assertEqual(score_to_tier(39.99), RelationshipTier.LOW)

    def test_zero(self):
        self.assertEqual(score_to_tier(0), RelationshipTier.LOW)


class TestNormalizeTenure(unittest.TestCase):
    """Tenure normalization to 0–1."""

    def test_zero_transactions(self):
        self.assertEqual(_normalize_tenure(0), 0.0)

    def test_max_transactions(self):
        self.assertEqual(_normalize_tenure(50), 1.0)

    def test_above_max(self):
        self.assertEqual(_normalize_tenure(100), 1.0)  # Capped

    def test_mid(self):
        self.assertEqual(_normalize_tenure(25), 0.5)


class TestComputeTierForCase(unittest.TestCase):
    """Full tier computation for a single case."""

    def _make_payment_case(self, **overrides) -> FailedPaymentCase:
        defaults = dict(
            case_id="PAY-TIER-001",
            amount=5000.0,
            failure_code=FailureCode.BANK_TIMEOUT,
            timestamp=datetime(2026, 8, 20, 10, 0),
            attempt_count=1,
            customer_id="CUST-001",
            customer_history=CustomerHistory(
                reliability_ratio=0.8,
                total_transactions=30,
                total_amount=100000,
                has_history=True,
            ),
        )
        defaults.update(overrides)
        return FailedPaymentCase(**defaults)

    def _make_b2b_case(self, **overrides) -> B2BReceivableCase:
        defaults = dict(
            case_id="B2B-TIER-001",
            invoice_id="INV-TIER-001",
            amount=200000.0,
            invoice_date=datetime(2026, 7, 1),
            due_date=datetime(2026, 7, 31),
            debtor_id="DBT-001",
            debtor_history=CustomerHistory(
                reliability_ratio=0.9,
                total_transactions=40,
                total_amount=2000000,
                has_history=True,
            ),
            contact_count=1,
        )
        defaults.update(overrides)
        return B2BReceivableCase(**defaults)

    def test_cold_start_defaults_h_to_half(self):
        """No prior history → H defaults to 0.5, logged as defaulted."""
        case = self._make_payment_case(
            customer_history=CustomerHistory(
                reliability_ratio=0.5,
                total_transactions=0,
                total_amount=0,
                has_history=False,
            )
        )
        all_amounts = [1000, 3000, 5000, 8000, 10000]
        tier, score, debug = compute_tier_for_case(case, all_amounts)
        self.assertTrue(debug["h_defaulted_no_history"])
        self.assertEqual(debug["historical_reliability"], 0.5)

    def test_fatigue_override_payment(self):
        """Payment case with attempt_count > 2 → downgrade tier."""
        case = self._make_payment_case(
            amount=50000.0,
            attempt_count=3,  # > FATIGUE_CAP_PAYMENT (2)
            customer_history=CustomerHistory(
                reliability_ratio=0.95,
                total_transactions=50,
                total_amount=500000,
                has_history=True,
            ),
        )
        all_amounts = [1000, 5000, 10000, 50000, 100000]
        tier, score, debug = compute_tier_for_case(case, all_amounts)
        self.assertTrue(debug["fatigue_applied"])
        # Score should be high, but tier should be downgraded
        self.assertNotEqual(debug["tier_before_fatigue"], debug["final_tier"])

    def test_fatigue_override_b2b(self):
        """B2B case with contact_count > 3 → downgrade tier."""
        case = self._make_b2b_case(
            contact_count=4,  # > FATIGUE_CAP_B2B (3)
        )
        all_amounts = [50000, 100000, 200000, 500000]
        tier, score, debug = compute_tier_for_case(case, all_amounts)
        self.assertTrue(debug["fatigue_applied"])

    def test_no_fatigue_below_cap(self):
        """Contact count within cap → no downgrade."""
        case = self._make_b2b_case(contact_count=2)
        all_amounts = [50000, 100000, 200000, 500000]
        tier, score, debug = compute_tier_for_case(case, all_amounts)
        self.assertFalse(debug["fatigue_applied"])

    def test_low_value_low_history_gives_low_tier(self):
        case = self._make_payment_case(
            amount=100.0,
            customer_history=CustomerHistory(
                reliability_ratio=0.1,
                total_transactions=1,
                total_amount=100,
                has_history=True,
            ),
        )
        all_amounts = [100, 5000, 10000, 50000, 100000]
        tier, score, debug = compute_tier_for_case(case, all_amounts)
        self.assertEqual(tier, RelationshipTier.LOW)


class TestBatchComputation(unittest.TestCase):
    """Verify second-pass batch computation."""

    def test_batch_assigns_tiers_to_all_cases(self):
        """All cases in a batch should have relationship_tier set after compute."""
        cases = [
            FailedPaymentCase(
                case_id=f"PAY-BATCH-{i}",
                amount=1000 * (i + 1),
                failure_code=FailureCode.BANK_TIMEOUT,
                timestamp=datetime(2026, 8, 20),
                attempt_count=1,
                customer_id=f"CUST-{i}",
                customer_history=CustomerHistory(
                    reliability_ratio=0.5 + i * 0.1,
                    total_transactions=i * 10,
                    total_amount=1000 * (i + 1),
                    has_history=True,
                ),
            )
            for i in range(5)
        ]
        results = compute_tiers_for_batch(cases)
        for case, score, debug in results:
            self.assertIsNotNone(case.relationship_tier)
            self.assertIn(case.relationship_tier, list(RelationshipTier))

    def test_percentile_uses_full_batch(self):
        """The highest-amount case should have the highest value percentile."""
        cases = [
            FailedPaymentCase(
                case_id=f"PAY-PCT-{i}",
                amount=amt,
                failure_code=FailureCode.BANK_TIMEOUT,
                timestamp=datetime(2026, 8, 20),
                attempt_count=1,
                customer_id=f"CUST-{i}",
                customer_history=CustomerHistory(has_history=False),
            )
            for i, amt in enumerate([100, 500, 1000, 5000, 50000])
        ]
        results = compute_tiers_for_batch(cases)
        # The ₹50,000 case (index 4) should have the highest value percentile
        self.assertEqual(results[4][2]["value_percentile"], 1.0)
        # The ₹100 case (index 0) should have the lowest
        self.assertEqual(results[0][2]["value_percentile"], 0.2)


if __name__ == "__main__":
    unittest.main()
