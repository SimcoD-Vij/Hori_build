"""Red-team agent -- runs periodically against the live detection layer to find blind spots, not per-case."""
import json
import os
import pandas as pd
import uuid
from datetime import datetime, timedelta
from detection.rules import run_all_rules

REDTEAM_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "redteam_history.jsonl")


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
    blind_spot_found = not bool(flags)
    result = {
        "blind_spot_found": blind_spot_found,
        "run_at": datetime.now().isoformat(),
        "probe_type": "structuring_patient_variant",
        "detail": (
            "20x $4,750 deposits over 40 days evaded the structuring rule (which requires "
            "amounts near the $10K threshold) -- current rule only catches near-threshold "
            "clustering, not this lower-amount patient variant. Recommend adding a "
            "total-volume-over-window check independent of per-transaction amount."
        ) if blind_spot_found else f"Probe was caught by: {[f['rule'] for f in flags]}",
    }
    # Append to history file so Regulatory Agent can verify cadence
    try:
        os.makedirs(os.path.dirname(REDTEAM_HISTORY_PATH), exist_ok=True)
        with open(REDTEAM_HISTORY_PATH, "a") as f:
            f.write(json.dumps(result) + "\n")
        # Update system card with last redteam date
        card_path = os.path.join(os.path.dirname(__file__), "..", "data", "system_card.json")
        try:
            with open(card_path) as f:
                card = json.load(f)
            card["last_redteam_date"] = datetime.now().date().isoformat()
            with open(card_path, "w") as f:
                json.dump(card, f, indent=2)
        except Exception:
            pass
    except Exception:
        pass
    return result


def get_stats() -> dict:
    """Return red-team run history stats for the visual interface."""
    history = []
    try:
        with open(REDTEAM_HISTORY_PATH) as f:
            for line in f:
                history.append(json.loads(line))
    except FileNotFoundError:
        pass

    total = len(history)
    blind_spots = sum(1 for r in history if r.get("blind_spot_found"))
    last_run = history[-1].get("run_at") if history else None
    return {
        "total_probes": total,
        "blind_spots_found": blind_spots,
        "attack_success_rate": round(blind_spots / total, 3) if total else 0.0,
        "last_run": last_run,
        "history": history[-10:],  # last 10 runs for chart
    }

