"""
Memory & Strategy Analytics — Adaptive Outcome Tracking.

Tracks historical recovery performance per (diagnosis_category, action_type).
Provides recency-weighted success rates to inform Strategy agent prompt context.

CRITICAL DESIGN CONSTRAINTS:
1. Double-gating: Only records outcomes for recovery actions (RETRY_NOW, RETRY_LATER,
   SUGGEST_ALTERNATE_METHOD) with terminal outcomes (SUCCESS or FAILED).
2. Routing actions (STOP, ESCALATE_HUMAN, WAIT) are never recorded and never presented as success rates.
3. Cold Start: Returns neutral 0.50 (50%) default with cold_start=True when no samples exist.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from agents.diagnosis import DiagnosisCategory
from core import config
from core.schemas import ActionType

logger = logging.getLogger(__name__)

RECOVERY_ACTIONS = {
    # Payment recovery actions
    ActionType.RETRY_NOW,
    ActionType.RETRY_LATER,
    ActionType.SUGGEST_ALTERNATE_METHOD,
    # B2B recovery actions
    ActionType.SEND_REMINDER,
    ActionType.OFFER_PAYMENT_PLAN,
    ActionType.ESCALATE_TONE,
}

TERMINAL_OUTCOMES = {"SUCCESS", "FAILED"}


@dataclass
class StrategyStats:
    """Strategy performance summary for a specific category and action."""
    success_rate: float
    sample_count: int
    cold_start: bool = False


class Memory:
    """Persistent outcome store and adaptive analytics engine."""

    def __init__(self, storage_path: Optional[str] = None):
        if storage_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_path = os.path.join(base_dir, "data", "memory.json")
        self.storage_path = storage_path
        # Schema: {category_str: {action_str: {"successes": int, "total": int, "recent_history": list[int]}}}
        self._stats: dict[str, dict[str, dict[str, Any]]] = {}
        self.load()

    def record_outcome(
        self,
        category: Optional[DiagnosisCategory],
        action: ActionType,
        status: str,
    ) -> None:
        """Record an outcome for strategy adaptation.

        Double-gated: Only records if action is in RECOVERY_ACTIONS and status is SUCCESS/FAILED.
        Skip-path cases or non-recovery actions are silently ignored.
        """
        if category is None or category == DiagnosisCategory.UNKNOWN:
            return

        if action not in RECOVERY_ACTIONS:
            return

        if status not in TERMINAL_OUTCOMES:
            return

        cat_key = category.value
        act_key = action.value

        if cat_key not in self._stats:
            self._stats[cat_key] = {}
        if act_key not in self._stats[cat_key]:
            self._stats[cat_key][act_key] = {
                "successes": 0,
                "total": 0,
                "recent_history": [],  # 1 for SUCCESS, 0 for FAILED
            }

        entry = self._stats[cat_key][act_key]
        # "Learning" in Sentinel means updating this per-(category, action) win-rate lookup table
        # from observed outcomes; it does NOT retrain the model or update weights. Introducing a
        # genuinely new case category requires an engineer to add it to DiagnosisCategory and
        # MockLLMClient explicitly.
        entry["total"] += 1
        is_success = 1 if status == "SUCCESS" else 0
        if is_success:
            entry["successes"] += 1

        # Keep rolling window of last 20 outcomes for recency weighting
        entry["recent_history"].append(is_success)
        if len(entry["recent_history"]) > 20:
            entry["recent_history"] = entry["recent_history"][-20:]

    def get_strategy_context(
        self,
        category: DiagnosisCategory,
    ) -> dict[ActionType, StrategyStats]:
        """Get strategy success rate context for all recovery actions.

        Returns neutral 0.50 (cold_start=True) when no historical outcomes exist.
        """
        cat_key = category.value
        result: dict[ActionType, StrategyStats] = {}

        for act in RECOVERY_ACTIONS:
            act_key = act.value
            cat_data = self._stats.get(cat_key, {})
            entry = cat_data.get(act_key)

            if not entry or entry.get("total", 0) == 0:
                result[act] = StrategyStats(
                    success_rate=config.MEMORY_DEFAULT_SUCCESS_RATE,
                    sample_count=0,
                    cold_start=True,
                )
            else:
                # Recency-weighted rate if history exists, else simple average
                recent = entry.get("recent_history", [])
                if recent:
                    # Exponential recency weighting: recent attempts have higher weight
                    weights = [1.1 ** i for i in range(len(recent))]
                    weighted_sum = sum(w * r for w, r in zip(weights, recent))
                    rate = weighted_sum / sum(weights)
                else:
                    rate = entry["successes"] / entry["total"]

                result[act] = StrategyStats(
                    success_rate=round(rate, 3),
                    sample_count=entry["total"],
                    cold_start=False,
                )

        return result

    def save(self) -> None:
        """Persist memory stats to JSON."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self._stats, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save memory: %s", e)

    def load(self) -> None:
        """Load memory stats from JSON."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self._stats = json.load(f)
            except Exception as e:
                logger.warning("Failed to load memory: %s", e)
                self._stats = {}
        else:
            self._stats = {}

    def get_all_stats(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Return the raw recorded outcome statistics for display."""
        return self._stats

    def clear(self) -> None:
        """Reset memory stats (used in testing)."""
        self._stats = {}
        if os.path.exists(self.storage_path):
            try:
                os.remove(self.storage_path)
            except Exception as e:
                logger.warning("Failed to remove memory file: %s", e)
