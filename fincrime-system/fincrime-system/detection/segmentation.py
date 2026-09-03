"""
Dynamic peer-group segmentation.

Closes the gap identified against the banking-AML source: comparing an
account only to its own history, or to a global population, misses cases
where behavior is normal for one customer type and abnormal for another
(a business owner's cash volume vs a student's). This clusters accounts
into peer groups and flags deviation from the GROUP norm.
"""
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


def build_peer_groups(accounts: pd.DataFrame, transactions: pd.DataFrame, k=4):
    features = []
    for _, acc in accounts.iterrows():
        acct_id = acc["account_id"]
        out_txns = transactions[transactions["sender_account"] == acct_id]
        in_txns = transactions[transactions["receiver_account"] == acct_id]
        features.append({
            "account_id": acct_id,
            "avg_monthly_volume": (out_txns["amount"].sum() + in_txns["amount"].sum()),
            "txn_frequency": len(out_txns) + len(in_txns),
            "account_age_days": (pd.Timestamp.now() - pd.to_datetime(acc["opened_date"])).days,
        })
    feat_df = pd.DataFrame(features).fillna(0)
    if len(feat_df) < k:
        feat_df["peer_group"] = 0
        return feat_df

    X = StandardScaler().fit_transform(feat_df[["avg_monthly_volume", "txn_frequency", "account_age_days"]])
    feat_df["peer_group"] = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
    return feat_df


def peer_deviation_flags(feat_df: pd.DataFrame, z_threshold=2.5):
    """For each account, how many std devs its volume is from its OWN peer group's mean."""
    flags = []
    for group_id, grp in feat_df.groupby("peer_group"):
        mean, std = grp["avg_monthly_volume"].mean(), grp["avg_monthly_volume"].std()
        if std == 0 or np.isnan(std) or len(grp) < 3:
            continue
        grp = grp.copy()
        grp["peer_zscore"] = (grp["avg_monthly_volume"] - mean) / std
        for _, row in grp[grp["peer_zscore"].abs() >= z_threshold].iterrows():
            flags.append({
                "account_id": row["account_id"],
                "rule": "peer_group_deviation",
                "evidence": f"Volume {row['avg_monthly_volume']:.2f} is {row['peer_zscore']:.1f} std devs from "
                            f"its peer group's average ({mean:.2f}) -- unusual for accounts like this one, "
                            f"even if not unusual globally",
                "transaction_ids": [],
                "severity_hint": min(10, int(abs(row["peer_zscore"])) + 3),
            })
    return flags
