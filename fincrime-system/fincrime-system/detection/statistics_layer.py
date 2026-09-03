"""Statistical detection: self-history z-scores and Benford's Law digit checks."""
import numpy as np
import pandas as pd


def zscore_flags(transactions: pd.DataFrame, z_threshold=3.0):
    """Flag transactions that deviate sharply from an account's OWN historical average."""
    flags = []
    for acct, grp in transactions.groupby("sender_account"):
        if acct in ("CASH", "EXTERNAL") or len(grp) < 3:
            continue
        mean, std = grp["amount"].mean(), grp["amount"].std()
        if std == 0 or np.isnan(std):
            continue
        grp = grp.copy()
        grp["zscore"] = (grp["amount"] - mean) / std
        outliers = grp[grp["zscore"].abs() >= z_threshold]
        for _, row in outliers.iterrows():
            flags.append({
                "account_id": acct,
                "rule": "zscore_self_history",
                "evidence": f"Transaction {row['amount']:.2f} is {row['zscore']:.1f} std devs from this account's own average ({mean:.2f})",
                "transaction_ids": [row["transaction_id"]],
                "severity_hint": min(10, int(abs(row["zscore"]))),
            })
    return flags


def benford_deviation_score(transactions: pd.DataFrame):
    """Compare leading-digit distribution of amounts against Benford's Law expectation."""
    amounts = transactions["amount"].dropna()
    amounts = amounts[amounts >= 1]
    if len(amounts) < 30:
        return {"deviation": 0.0, "note": "insufficient data for a reliable Benford check"}
    leading = amounts.astype(str).str.replace(".", "", regex=False).str.lstrip("0").str[0].astype(int)
    observed = leading.value_counts(normalize=True).sort_index()
    expected = {d: np.log10(1 + 1 / d) for d in range(1, 10)}
    deviation = sum(abs(observed.get(d, 0) - expected[d]) for d in range(1, 10))
    return {"deviation": round(deviation, 4), "note": "higher = less natural-looking digit distribution"}
