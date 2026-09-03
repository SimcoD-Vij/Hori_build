"""Append-only audit log. Never update or delete rows -- write new entries only."""
import json
import os
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "audit_log.jsonl")


def log(case_id: str, actor: str, action: str, details: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "case_id": case_id,
        "actor": actor,
        "action": action,
        "details": details,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def read_log(case_id: str = None):
    if not os.path.exists(LOG_PATH):
        return []
    entries = [json.loads(line) for line in open(LOG_PATH)]
    if case_id:
        entries = [e for e in entries if e["case_id"] == case_id]
    return entries
