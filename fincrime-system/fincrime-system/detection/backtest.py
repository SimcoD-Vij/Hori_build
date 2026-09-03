"""
Backtest / accuracy layer.

Answers the "how do we know the dashboard is showing accurate results"
question directly: the synthetic data generator returns ground truth for
every seeded pattern. This compares detection output against that ground
truth and reports real precision/recall per pattern type -- not just a
raw flag count, which by itself tells you nothing about accuracy.

In production (real data, no generator-supplied ground truth), swap the
ground_truth argument for analyst-confirmed case outcomes accumulated
over time -- the accuracy computation below doesn't change at all.
"""


def compute_accuracy(ground_truth: dict, all_flags: list) -> dict:
    flagged_accounts = {f["account_id"] for f in all_flags}
    truth_accounts = set(ground_truth.keys())

    true_positives = flagged_accounts & truth_accounts
    false_positives = flagged_accounts - truth_accounts
    false_negatives = truth_accounts - flagged_accounts

    precision = len(true_positives) / len(flagged_accounts) if flagged_accounts else 0.0
    recall = len(true_positives) / len(truth_accounts) if truth_accounts else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    by_pattern = {}
    for acct, pattern in ground_truth.items():
        by_pattern.setdefault(pattern, {"total": 0, "caught": 0})
        by_pattern[pattern]["total"] += 1
        if acct in flagged_accounts:
            by_pattern[pattern]["caught"] += 1

    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "true_positives": len(true_positives),
        "false_positives": len(false_positives),
        "false_negatives": len(false_negatives),
        "false_positive_accounts": sorted(false_positives)[:10],
        "by_pattern": by_pattern,
        "note": "Ground truth here comes from the synthetic data generator's known seeded patterns. "
                "In production, replace get_ground_truth() with analyst-confirmed case outcomes "
                "accumulated over time -- the accuracy computation itself doesn't change.",
    }
