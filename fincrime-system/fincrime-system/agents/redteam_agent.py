"""Red-team agent -- runs periodically against the live detection layer to find blind spots, not per-case."""
import pandas as pd
import uuid
from datetime import datetime, timedelta
from detection.rules import run_all_rules


def generate_evasion_attempts():
    """Structuring spread across more accounts / longer window than the current rule checks."""
    rows = []
    base = str(uuid.uuid4())[:8]
    # Spread the same $95K across 20 tiny deposits instead of 11 medium ones -- tests whether count-based
    # rules alone would miss a more patient structuring attempt.
    for i in range(20):
        rows.append({
            "transaction_id": str(uuid.uuid4())[:10], "sender_account": "CASH", "receiver_account": base,
            "amount": 4750.0, "timestamp": (datetime.now() - timedelta(days=40 - i * 2)).isoformat(),
            "method": "cash_deposit", "memo": "evasion probe",
        })
    return pd.DataFrame(rows)


def run_redteam_probe():
    probe_txns = generate_evasion_attempts()
    flags = run_all_rules(probe_txns)
    if not flags:
        return {
            "blind_spot_found": True,
            "detail": "20x $4,750 deposits over 40 days evaded the structuring rule (which requires "
                       "amounts near the $10K threshold) -- current rule only catches near-threshold "
                       "clustering, not this lower-amount patient variant. Recommend adding a "
                       "total-volume-over-window check independent of per-transaction amount.",
        }
    return {"blind_spot_found": False, "detail": f"Probe was caught by: {[f['rule'] for f in flags]}"}
