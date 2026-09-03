"""
detection/temporal_graph.py — EvolveGCN data preparation
=========================================================
Implements build_temporal_snapshots() as specified in EVOLVEGCN_INTEGRATION.md.

The current graph layer (graph_analysis.py) treats the transaction network as a
single static snapshot. EvolveGCN's value is modeling how the network changes
block-by-block or day-by-day — catching a ring that looks clean today but shows
a laundering-shaped evolution over a week.

This file handles ONLY data preparation — it does NOT call EvolveGCN directly.
The inference call lives in detection/evolvegcn_service.py (separate process,
running in the conda310 GPU environment) and is wired into main.py once training
is complete (see EVOLVEGCN_INTEGRATION.md scoping note).

The flag shape returned by run_evolvegcn_inference() when it is eventually wired in:
    {"account_id": str, "rule": "evolvegcn_temporal", "evidence": str,
     "transaction_ids": list, "severity_hint": int}
This matches the shared flag shape used by every other detector, so no agent
code needs to change when the inference call is enabled.
"""

import pandas as pd
from typing import List


def build_temporal_snapshots(
    transactions: pd.DataFrame,
    window: str = "1D",
) -> List[pd.DataFrame]:
    """
    Split transactions into a sequence of graph snapshots, one per time window.
    This is the input shape EvolveGCN expects, instead of the single static graph
    that detection/graph_analysis.py builds.

    Args:
        transactions: DataFrame matching the fincrime schema contract
                      (must have 'timestamp', 'sender_account', 'receiver_account',
                      'amount', 'transaction_id' columns).
        window: Pandas frequency string for the time window size.
                '1D' = daily snapshots (default, recommended for AMLSim data).
                '1W' = weekly (better for sparse real data).
                '1H' = hourly (useful for high-frequency fraud detection).

    Returns:
        List of DataFrames, each representing one time window's transactions.
        Empty windows are skipped (no empty snapshots in the output).
        The list is ordered chronologically — feed this directly to the
        EvolveGCN training loop.

    Raises:
        ValueError: if required columns are missing (loud failure, not silent).
    """
    required_cols = {"timestamp", "sender_account", "receiver_account", "amount", "transaction_id"}
    missing = required_cols - set(transactions.columns)
    if missing:
        raise ValueError(
            f"build_temporal_snapshots(): missing required columns: {missing}. "
            f"Ensure the DataFrame uses the fincrime schema contract (README.md)."
        )

    if transactions.empty:
        return []

    df = transactions.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    # Drop rows where timestamp couldn't be parsed (loud warning, not silent drop)
    n_bad = df["timestamp"].isna().sum()
    if n_bad > 0:
        import warnings
        warnings.warn(
            f"build_temporal_snapshots(): {n_bad} rows had unparseable timestamps "
            f"and were excluded from temporal snapshots.",
            stacklevel=2,
        )
        df = df.dropna(subset=["timestamp"])

    if df.empty:
        return []

    df = df.sort_values("timestamp")
    snapshots = []

    for _, group in df.groupby(pd.Grouper(key="timestamp", freq=window)):
        if len(group) > 0:
            snapshots.append(group.reset_index(drop=True))

    return snapshots


def snapshot_summary(snapshots: List[pd.DataFrame]) -> dict:
    """
    Returns a summary dict for logging/display — how many snapshots, date range,
    average transactions per snapshot.
    Useful for verifying the snapshots look right before feeding to EvolveGCN.
    """
    if not snapshots:
        return {"n_snapshots": 0, "date_range": None, "avg_txns_per_snapshot": 0}

    total_txns = sum(len(s) for s in snapshots)
    first_ts = snapshots[0]["timestamp"].min()
    last_ts  = snapshots[-1]["timestamp"].max()

    return {
        "n_snapshots": len(snapshots),
        "date_range": f"{first_ts.date()} → {last_ts.date()}",
        "avg_txns_per_snapshot": round(total_txns / len(snapshots), 1),
        "total_transactions": total_txns,
    }


# ── Self-test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import io

    sample_csv = io.StringIO(
        "transaction_id,sender_account,receiver_account,amount,timestamp,method,memo\n"
        "T001,ACC001,ACC002,9500.0,2017-01-01T10:00:00,transfer,test\n"
        "T002,CASH,ACC003,9800.0,2017-01-01T11:00:00,cash_deposit,test\n"
        "T003,ACC001,ACC004,500.0,2017-01-02T09:00:00,transfer,test\n"
        "T004,ACC002,ACC005,9750.0,2017-01-03T14:00:00,transfer,test\n"
        "T005,ACC005,EXTERNAL,1200.0,2017-01-03T16:00:00,cash_deposit,test\n"
        "T006,ACC001,ACC003,8000.0,2017-01-05T10:00:00,transfer,test\n"
    )
    df = pd.read_csv(sample_csv)
    snapshots = build_temporal_snapshots(df, window="1D")
    summary = snapshot_summary(snapshots)

    print("\n" + "="*60)
    print("Temporal Graph — build_temporal_snapshots() Test")
    print("="*60)
    print(f"\nSummary: {summary}")
    for i, snap in enumerate(snapshots):
        print(f"\nSnapshot {i+1} ({len(snap)} transactions):")
        print(snap[["transaction_id", "sender_account", "receiver_account", "amount", "timestamp"]].to_string(index=False))

    assert len(snapshots) >= 3, f"Expected >= 3 daily snapshots, got {len(snapshots)}"
    assert summary["n_snapshots"] == len(snapshots)
    print("\n✅ build_temporal_snapshots() test PASSED")
