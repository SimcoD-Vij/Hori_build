"""Deterministic rule engine. No ML, no LLM -- pure logic, cheap and explainable."""
import pandas as pd
from datetime import datetime


def detect_structuring(transactions: pd.DataFrame, threshold=10000, window_days=30, min_count=3):
    """Flag accounts with repeated deposits clustered just under a reporting threshold."""
    flags = []
    cash = transactions[transactions["method"].isin(["cash_deposit"])]
    for acct, grp in cash.groupby("receiver_account"):
        near_threshold = grp[(grp["amount"] >= threshold * 0.9) & (grp["amount"] < threshold)]
        if len(near_threshold) >= min_count:
            flags.append({
                "account_id": acct,
                "rule": "structuring",
                "evidence": f"{len(near_threshold)} deposits between {threshold*0.9:.0f}-{threshold:.0f} "
                            f"within observed window, total {near_threshold['amount'].sum():.2f}",
                "transaction_ids": near_threshold["transaction_id"].tolist(),
                "severity_hint": min(10, 5 + len(near_threshold)),
            })
    return flags


def detect_velocity(transactions: pd.DataFrame, max_txns=8, window_hours=24):
    """Flag accounts with abnormally high transaction frequency in a short window."""
    flags = []
    transactions = transactions.copy()
    transactions["timestamp"] = pd.to_datetime(transactions["timestamp"])
    for acct, grp in transactions.groupby("sender_account"):
        grp = grp.sort_values("timestamp")
        counts = grp.set_index("timestamp").rolling(f"{window_hours}h").count()["transaction_id"]
        if counts.max() >= max_txns:
            flags.append({
                "account_id": acct,
                "rule": "velocity",
                "evidence": f"{int(counts.max())} outgoing transactions within a {window_hours}h window",
                "transaction_ids": grp["transaction_id"].tolist(),
                "severity_hint": min(10, 4 + int(counts.max()) // 2),
            })
    return flags


def detect_bust_out(transactions: pd.DataFrame, ratio_threshold=10):
    """Flag accounts whose largest outgoing transaction dwarfs their typical inflow -- the 'cultivate then extract' signature."""
    flags = []
    transactions = transactions.copy()
    for acct, grp in transactions.groupby("sender_account"):
        if acct in ("CASH", "EXTERNAL"):
            continue
        inflow = transactions[transactions["receiver_account"] == acct]["amount"]
        if len(inflow) == 0:
            continue
        avg_inflow = inflow.mean()
        max_outflow = grp["amount"].max()
        if avg_inflow > 0 and max_outflow / avg_inflow >= ratio_threshold:
            flags.append({
                "account_id": acct,
                "rule": "bust_out",
                "evidence": f"Largest outflow {max_outflow:.2f} is {max_outflow/avg_inflow:.1f}x the average inflow {avg_inflow:.2f}",
                "transaction_ids": grp[grp["amount"] == max_outflow]["transaction_id"].tolist(),
                "severity_hint": min(10, 5 + int(max_outflow / avg_inflow) // 5),
            })
    return flags


def detect_cnp_anomaly(transactions: pd.DataFrame, high_value=30000):
    """
    Card-not-present transactions above a high-value threshold, flagged directly --
    this catches cases the z-score check misses when an account has too little
    history to compute a self-baseline against (e.g. a single large CNP charge).
    """
    flags = []
    cnp = transactions[transactions["method"] == "card_not_present"]
    for _, row in cnp[cnp["amount"] >= high_value].iterrows():
        flags.append({
            "account_id": row["sender_account"], "rule": "zscore_self_history",
            "evidence": f"Card-not-present transaction of {row['amount']:.2f} with insufficient account "
                        f"history to establish a baseline -- flagged directly for verification",
            "transaction_ids": [row["transaction_id"]], "severity_hint": 5,
        })
    return flags


def run_all_rules(transactions: pd.DataFrame):
    flags = []
    flags += detect_structuring(transactions)
    flags += detect_velocity(transactions)
    flags += detect_bust_out(transactions)
    flags += detect_cnp_anomaly(transactions)
    return flags
