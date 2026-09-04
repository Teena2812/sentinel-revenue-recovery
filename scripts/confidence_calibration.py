"""
scripts/confidence_calibration.py — Confidence Calibration Check (Prompt 9)

Evaluates the empirical calibration of Sentinel's LLM confidence scores across
all 61 cases that actually reached the Diagnosis Agent (diagnose()).

Exclusion Note: Exactly 19 cases in the 80-case benchmark are pre-pipeline safety
skips (fraud flags, dispute holds, initial attempt-cap ceilings, active promises).
These cases are filtered purely by statutory rules before the AI pipeline, meaning
no LLM diagnosis is ever invoked (logged as diagnosis_confidence = "N/A"). They
are excluded from calibration analysis to prevent artificial contamination of the
0.0–0.2 bucket with non-model placeholder values.

Buckets cases by model-assessed confidence:
- 0.0 – 0.2
- 0.2 – 0.4
- 0.4 – 0.6 (Self-consistency split disagreements / cold starts)
- 0.6 – 0.8 (Sub-threshold / majority-vote capped at 0.80)
- 0.8 – 1.0 (Autonomous compliant execution tier)

Outputs:
1. Summary calibration table to stdout
2. CSV report to reports/confidence_calibration.csv
3. Visualization chart to reports/confidence_calibration.png
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mtick
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

# Bucket definitions: (label, lower_inclusive, upper_exclusive_or_inclusive)
BUCKETS = [
    ("0.0 - 0.2", 0.0, 0.2),
    ("0.2 - 0.4", 0.2, 0.4),
    ("0.4 - 0.6", 0.4, 0.6),
    ("0.6 - 0.8", 0.6, 0.8),
    ("0.8 - 1.0", 0.8, 1.0001),  # Include 1.0
]


def load_all_benchmark_cases(base_dir: Path) -> list[dict]:
    """Load case outcomes from breakdown CSVs."""
    rows = []
    for filename in ["payment_batch_breakdown.csv", "b2b_batch_breakdown.csv"]:
        filepath = base_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Required report {filepath} not found. Run benchmark first.")
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    return rows


def is_diagnosed_case(row: dict) -> bool:
    """Check if the case actually ran through diagnose().

    Pre-pipeline skipped cases (fraud, dispute, initial attempt caps, active promises)
    were evaluated purely by deterministic rules before the AI pipeline, so they have
    no model-assessed diagnosis confidence to calibrate against.
    """
    conf_str = row.get("diagnosis_confidence", "").strip()
    cat_str = row.get("diagnosis_category", "").strip()
    if not conf_str or conf_str in {"N/A", "None", ""}:
        return False
    if not cat_str or cat_str in {"N/A", "None", "", "N/A (Pre-Pipeline Skip)"}:
        return False
    return True


def parse_confidence(val_str: str) -> float:
    """Parse confidence string into float, returning 0.0 on failure."""
    try:
        val = float(val_str)
        return max(0.0, min(1.0, val))
    except (ValueError, TypeError):
        return 0.0


def run_calibration_analysis() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    reports_dir = repo_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    all_rows = load_all_benchmark_cases(reports_dir)
    total_benchmark_cases = len(all_rows)

    # Filter to cases that actually ran through diagnose()
    diagnosed_rows = [r for r in all_rows if is_diagnosed_case(r)]
    skipped_rows = [r for r in all_rows if not is_diagnosed_case(r)]

    diagnosed_count = len(diagnosed_rows)
    skipped_count = len(skipped_rows)

    # Initialize bucket metrics
    calibration_data = {
        b[0]: {
            "bucket": b[0],
            "lower": b[1],
            "upper": b[2],
            "total_cases": 0,
            "recovered": 0,
            "escalated": 0,
            "stopped": 0,
            "failed": 0,
            "waiting": 0,
            "confidences": [],
            "case_ids": [],
        }
        for b in BUCKETS
    }

    for r in diagnosed_rows:
        conf = parse_confidence(r.get("diagnosis_confidence", ""))
        status = r.get("status", "").upper()
        cid = r.get("case_id", "")

        # Find matching bucket
        for label, lower, upper in BUCKETS:
            if lower <= conf < upper or (upper >= 1.0 and conf == 1.0):
                b_dict = calibration_data[label]
                b_dict["total_cases"] += 1
                b_dict["confidences"].append(conf)
                b_dict["case_ids"].append(cid)
                if status == "RECOVERED":
                    b_dict["recovered"] += 1
                elif status == "ESCALATED":
                    b_dict["escalated"] += 1
                elif status == "STOPPED":
                    b_dict["stopped"] += 1
                elif status == "WAITING":
                    b_dict["waiting"] += 1
                else:
                    b_dict["failed"] += 1
                break

    # Calculate rates
    results = []
    for label, _, _ in BUCKETS:
        d = calibration_data[label]
        cnt = d["total_cases"]
        rec = d["recovered"]
        win_rate = (rec / cnt * 100.0) if cnt > 0 else 0.0
        avg_conf = (sum(d["confidences"]) / cnt) if cnt > 0 else 0.0
        results.append({
            "confidence_bucket": label,
            "total_cases": cnt,
            "recovered_cases": rec,
            "win_rate_pct": round(win_rate, 1),
            "escalated_cases": d["escalated"],
            "stopped_cases": d["stopped"],
            "avg_confidence": round(avg_conf, 2),
            "case_ids": ", ".join(d["case_ids"]) if cnt <= 3 else f"{len(d['case_ids'])} cases",
        })

    # Print Table to stdout
    print("\n" + "=" * 78)
    print(f"SENTINEL CONFIDENCE CALIBRATION ANALYSIS (N={diagnosed_count} Diagnosed Cases)")
    print("=" * 78)
    print(f"Total benchmark cases:          {total_benchmark_cases}")
    print(f"Pre-pipeline skips (excluded):  {skipped_count} (no diagnosis invoked; placeholder 0.0 removed)")
    print(f"Diagnosed cases evaluated:      {diagnosed_count}")
    print("-" * 78)
    print(f"{'Confidence Bucket':<18} | {'Cases':<6} | {'Recovered':<10} | {'Win Rate (%)':<13} | {'Avg Conf':<8} | {'Escalated / Stopped'}")
    print("-" * 78)
    for res in results:
        print(
            f"{res['confidence_bucket']:<18} | "
            f"{res['total_cases']:<6} | "
            f"{res['recovered_cases']:<10} | "
            f"{res['win_rate_pct']:>11.1f}% | "
            f"{res['avg_confidence']:>8.2f} | "
            f"{res['escalated_cases']} esc / {res['stopped_cases']} stop"
        )
    print("=" * 78)
    total_rec = sum(r["recovered_cases"] for r in results)
    overall_diag_rate = (total_rec / diagnosed_count * 100.0) if diagnosed_count > 0 else 0.0
    print(f"Overall recovery rate among diagnosed cases: {overall_diag_rate:.1f}% ({total_rec}/{diagnosed_count})\n")

    # Export CSV
    csv_path = reports_dir / "confidence_calibration.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "confidence_bucket",
            "total_cases",
            "recovered_cases",
            "win_rate_pct",
            "avg_confidence",
            "escalated_cases",
            "stopped_cases",
            "case_ids",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"--> Calibration CSV exported to: {csv_path}")

    # Generate Chart if matplotlib is available
    if HAS_MATPLOTLIB:
        chart_path = reports_dir / "confidence_calibration.png"
        _render_chart(results, chart_path, diagnosed_count, skipped_count)
        print(f"--> Calibration plot exported to: {chart_path}")
    else:
        print("--> Matplotlib not installed; skipping chart generation.")


def _render_chart(results: list[dict], output_path: Path, diagnosed_count: int, skipped_count: int) -> None:
    """Render a clean calibration bar chart for diagnosed cases."""
    fig, ax1 = plt.subplots(figsize=(9.5, 5.8), dpi=300)
    fig.patch.set_facecolor("#0F172A")
    ax1.set_facecolor("#0F172A")

    labels = [r["confidence_bucket"] for r in results]
    case_counts = [r["total_cases"] for r in results]
    win_rates = [r["win_rate_pct"] for r in results]

    x = range(len(labels))
    width = 0.42

    # Bar chart for case counts
    bars = ax1.bar(
        [i - width / 2 for i in x],
        case_counts,
        width=width,
        color="#38BDF8",
        alpha=0.85,
        label="Diagnosed Cases (N)",
        edgecolor="#0284C7",
        linewidth=1.2,
    )

    # Annotate bars
    for bar in bars:
        height = bar.get_height()
        if height > 0:
            ax1.annotate(
                f"{int(height)}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#F8FAFC",
                fontweight="bold",
            )

    # Secondary axis for Empirical Win Rate
    ax2 = ax1.twinx()
    bars2 = ax2.bar(
        [i + width / 2 for i in x],
        win_rates,
        width=width,
        color="#10B981",
        alpha=0.85,
        label="Empirical Win Rate (%)",
        edgecolor="#059669",
        linewidth=1.2,
    )

    for bar in bars2:
        height = bar.get_height()
        if height > 0:
            ax2.annotate(
                f"{height:.1f}%",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#10B981",
                fontweight="bold",
            )

    # Styling
    ax1.set_xlabel("Diagnosis Confidence Score Bucket", fontsize=11, color="#E2E8F0", labelpad=10)
    ax1.set_ylabel("Diagnosed Case Volume", fontsize=11, color="#38BDF8", labelpad=10)
    ax2.set_ylabel("Empirical Recovery Rate (%)", fontsize=11, color="#10B981", labelpad=10)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels, fontsize=10, color="#CBD5E1")
    ax1.tick_params(colors="#94A3B8")
    ax2.tick_params(colors="#94A3B8")
    ax1.set_ylim(0, max(case_counts) * 1.25)
    ax2.set_ylim(0, 100)
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter())

    ax1.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax1.spines["bottom"].set_color("#334155")
    ax1.spines["left"].set_color("#334155")
    ax2.spines["right"].set_color("#334155")
    ax1.grid(axis="y", linestyle="--", alpha=0.15, color="#FFFFFF")

    # Titles & Legend
    plt.title(
        f"Sentinel — Confidence Calibration on Diagnosed Cases (N={diagnosed_count})\n"
        f"[Excludes {skipped_count} pre-pipeline safety skips with no model-assessed confidence]",
        fontsize=12,
        color="#F8FAFC",
        fontweight="bold",
        pad=14,
    )
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", facecolor="#1E293B", edgecolor="#334155", labelcolor="#F8FAFC")

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close()


if __name__ == "__main__":
    run_calibration_analysis()
