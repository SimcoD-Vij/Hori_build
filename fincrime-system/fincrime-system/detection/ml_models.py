"""
ML detection layer.
- Supervised: RandomForest trained on known patterns (stand-in for XGBoost,
  lighter dependency footprint for a fast Docker build; swap in xgboost
  later per the build spec).
- Unsupervised: IsolationForest trained WITHOUT labels, run as a parallel
  stream to catch novel ('pattern B') fraud the supervised model has never
  seen -- this is the gap fix added after the banking-AML source comparison.
"""
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier, IsolationForest


def _feature_frame(transactions: pd.DataFrame) -> pd.DataFrame:
    df = transactions.copy()
    df["amount"] = df["amount"].astype(float)
    df["hour"] = pd.to_datetime(df["timestamp"]).dt.hour
    df["is_cash"] = (df["method"] == "cash_deposit").astype(int)
    df["is_cnp"] = (df["method"] == "card_not_present").astype(int)
    return df


def train_supervised(transactions: pd.DataFrame, labels: dict):
    """labels: {transaction_id: 0/1}. In the prototype we bootstrap labels from rule/graph hits."""
    df = _feature_frame(transactions)
    df["label"] = df["transaction_id"].map(labels).fillna(0).astype(int)
    X = df[["amount", "hour", "is_cash", "is_cnp"]]
    y = df["label"]
    if y.sum() == 0 or y.sum() == len(y):
        return None  # not enough label diversity yet
    clf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight="balanced")
    clf.fit(X, y)
    return clf


def supervised_flags(transactions: pd.DataFrame, clf):
    if clf is None:
        return []
    df = _feature_frame(transactions)
    X = df[["amount", "hour", "is_cash", "is_cnp"]]
    proba = clf.predict_proba(X)[:, 1]
    df["fraud_proba"] = proba
    flags = []
    for _, row in df[df["fraud_proba"] >= 0.6].iterrows():
        flags.append({
            "account_id": row["sender_account"], "rule": "supervised_ml",
            "evidence": f"RandomForest fraud probability {row['fraud_proba']:.2f} on transaction {row['amount']:.2f}",
            "transaction_ids": [row["transaction_id"]], "severity_hint": int(row["fraud_proba"] * 10),
        })
    return flags


def unsupervised_flags(transactions: pd.DataFrame, contamination=0.05):
    """No labels used at all -- this is what catches genuinely novel patterns."""
    df = _feature_frame(transactions)
    X = df[["amount", "hour", "is_cash", "is_cnp"]]
    if len(X) < 20:
        return []
    iso = IsolationForest(contamination=contamination, random_state=42)
    scores = iso.fit_predict(X)
    df["anomaly"] = scores
    flags = []
    for _, row in df[df["anomaly"] == -1].iterrows():
        flags.append({
            "account_id": row["sender_account"], "rule": "unsupervised_novel_pattern",
            "evidence": f"Flagged by unsupervised anomaly model on transaction {row['amount']:.2f} -- "
                        f"does not match any known rule or trained fraud label; treat as a NOVEL "
                        f"pattern candidate, not a typology match",
            "transaction_ids": [row["transaction_id"]], "severity_hint": 6,
            "is_novel_pattern_candidate": True,
        })
    return flags
