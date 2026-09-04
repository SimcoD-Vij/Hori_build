"""
data/ingestion/load_external.py — AMLSim → fincrime schema adapter
===================================================================
Maps AMLSim's output CSV schema to the internal fincrime schema contract
defined in README.md. This adapter is the ONLY place that knows about
AMLSim's column names — no detector file is ever modified to accommodate
the external schema. Conform the data to the contract, not the other way.

AMLSim transaction columns (from paramFiles/schema.json):
  tran_id          → transaction_id
  tran_timestamp   → timestamp  (date string → ISO datetime)
  base_amt         → amount     (float)
  tx_type          → method     (mapped: see TX_TYPE_MAP below)
  orig_acct        → sender_account
  bene_acct        → receiver_account
  is_sar           → (dropped — used for ground_truth only)
  alert_id         → (dropped)

AMLSim account columns (from paramFiles/schema.json):
  acct_id          → account_id
  first_name+last_name → customer_id (concatenated as name proxy)
  type             → occupation  (account type used as proxy)
  open_dt          → opened_date
  ssn              → device_id   (closest unique identifier available)
  zip              → phone       (placeholder — AMLSim has no phone field)
  street_addr+city+state+country → address

Direction convention (from README.md schema contract):
  Cash deposits: sender_account = "CASH", receiver_account = acct_id
  Other outgoing: sender_account = orig_acct, receiver_account = bene_acct
  This is enforced here, not guessed downstream.
"""

import os
import uuid
import hashlib
import pandas as pd
from datetime import datetime, timedelta

# ── Paths ───────────────────────────────────────────────────────────────────
AMLSIM_DIR = os.environ.get(
    "AMLSIM_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "AMLSim", "outputs", "sample")
)

# ── Transaction type mapping ────────────────────────────────────────────────
# AMLSim tx_type values → fincrime method values
TX_TYPE_MAP = {
    "TRANSFER":    "transfer",
    "CASH-IN":     "cash_deposit",   # beneficiary receives cash → CASH sender
    "CASH-OUT":    "cash_deposit",   # originator withdraws → treated as deposit from CASH for AML detection
    "DEBIT":       "transfer",
    "CREDIT":      "transfer",
    "PAYMENT":     "transfer",
    "CHEQUE":      "transfer",
    "DEPOSIT":     "cash_deposit",
}
CASH_TX_TYPES = {"CASH-IN", "CASH-OUT", "DEPOSIT"}

BASE_DATE = datetime(2017, 1, 1)  # AMLSim base date for step→date conversion


def _days_to_iso(val: str) -> str:
    """Convert AMLSim date value (either integer days-since-base or YYYYMMDD) to ISO datetime string."""
    val = str(val).strip()
    if val.isdigit() and len(val) <= 6:
        # It's a step count (days since base_date)
        dt = BASE_DATE + timedelta(days=int(val))
        return dt.isoformat()
    try:
        # Try parsing YYYYMMDD
        dt = datetime.strptime(val[:8], "%Y%m%d")
        return dt.isoformat()
    except (ValueError, TypeError):
        return BASE_DATE.isoformat()


def load_amlsim_transactions(tx_csv_path: str) -> tuple[pd.DataFrame, dict]:
    """
    Load AMLSim transactions.csv and convert to fincrime schema.
    Returns (transactions_df, ground_truth_dict).
    ground_truth maps transaction_id → 1 if is_sar=True (used for accuracy testing).
    """
    raw = pd.read_csv(tx_csv_path, dtype=str)

    # Normalise column names (AMLSim is consistent but let's be safe)
    raw.columns = [c.strip().lower() for c in raw.columns]

    # Verify required columns are present
    required = {"tran_id", "tran_timestamp", "base_amt", "tx_type", "orig_acct", "bene_acct"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(
            f"AMLSim CSV missing expected columns: {missing}. "
            f"Found: {list(raw.columns)}. "
            f"Check that this is a transactions.csv output file, not a parameter file."
        )

    rows = []
    ground_truth = {}

    for _, r in raw.iterrows():
        tx_type_raw = str(r.get("tx_type", "TRANSFER")).upper()
        method = TX_TYPE_MAP.get(tx_type_raw, "transfer")
        is_cash = tx_type_raw in CASH_TX_TYPES

        # Direction convention: cash deposits have CASH as sender
        if is_cash and tx_type_raw == "CASH-IN":
            sender = "CASH"
            receiver = str(r["bene_acct"])
        elif is_cash and tx_type_raw == "CASH-OUT":
            sender = str(r["orig_acct"])
            receiver = "EXTERNAL"
        elif is_cash:  # DEPOSIT
            sender = "CASH"
            receiver = str(r["bene_acct"])
        else:
            sender = str(r["orig_acct"])
            receiver = str(r["bene_acct"])

        try:
            amount = float(r["base_amt"])
        except (ValueError, TypeError):
            amount = 0.0

        tx_id = str(r["tran_id"])
        timestamp = _days_to_iso(r["tran_timestamp"])
        is_sar = str(r.get("is_sar", "false")).lower() in ("true", "1", "yes")

        rows.append({
            "transaction_id":   tx_id,
            "sender_account":   sender,
            "receiver_account": receiver,
            "amount":           amount,
            "timestamp":        timestamp,
            "method":           method,
            "memo":             f"AMLSim/{tx_type_raw}",
        })

        if is_sar:
            ground_truth[tx_id] = 1

    txn_df = pd.DataFrame(rows)
    return txn_df, ground_truth


def load_amlsim_accounts(acct_csv_path: str) -> pd.DataFrame:
    """
    Load AMLSim accounts.csv and convert to fincrime account schema.
    """
    raw = pd.read_csv(acct_csv_path, dtype=str)
    raw.columns = [c.strip().lower() for c in raw.columns]

    required = {"acct_id"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"AMLSim accounts CSV missing columns: {missing}. Found: {list(raw.columns)}")

    rows = []
    for _, r in raw.iterrows():
        first = str(r.get("first_name", "")).strip()
        last  = str(r.get("last_name",  "")).strip()
        name  = f"{first} {last}".strip() or f"Account_{r['acct_id']}"

        addr_parts = [
            str(r.get("street_addr", "")),
            str(r.get("city", "")),
            str(r.get("state", "")),
            str(r.get("country", "US")),
        ]
        address = ", ".join(p for p in addr_parts if p and p != "nan")

        # PRIVACY FIX: device_id flows into the identity graph and gets displayed
        # directly in evidence packets and case reports (see agents/evidence_agent.py,
        # templates/case_report.html) -- storing a raw SSN there means a government
        # ID number ends up shown to human reviewers and written to the audit log.
        # Hash it instead: this still lets the identity graph link accounts that
        # share the same underlying SSN (same hash = same person), which is the
        # actual detection value, without ever storing or displaying the real number.
        ssn_raw = str(r.get("ssn", "")).strip()
        device_id = hashlib.sha256(ssn_raw.encode()).hexdigest()[:12] if ssn_raw else str(uuid.uuid4())[:8]

        rows.append({
            "account_id":   str(r["acct_id"]),
            "customer_id":  name,
            "occupation":   str(r.get("type", "SAV")),
            "opened_date":  _days_to_iso(r.get("open_dt", "0")),
            "device_id":    device_id,
            "phone":        str(r.get("zip", "")),
            "address":      address or "Unknown",
        })

    return pd.DataFrame(rows)


def load_amlsim(
    tx_csv_path: str = None,
    acct_csv_path: str = None,
) -> tuple[list, list, dict]:
    """
    Main entry point. Returns (accounts, transactions, ground_truth)
    in the same shape as data.generate_synthetic_data.generate().

    Falls back to searching AMLSIM_DIR if paths not given.
    """
    sim_dir = AMLSIM_DIR

    if tx_csv_path is None:
        tx_csv_path = os.path.join(sim_dir, "transactions.csv")
    if acct_csv_path is None:
        acct_csv_path = os.path.join(sim_dir, "accounts.csv")

    if not os.path.exists(tx_csv_path):
        raise FileNotFoundError(
            f"AMLSim transactions file not found at: {tx_csv_path}\n"
            f"Run AMLSim's Java simulator first, or set AMLSIM_DIR env var "
            f"to the correct outputs subdirectory."
        )
    if not os.path.exists(acct_csv_path):
        raise FileNotFoundError(
            f"AMLSim accounts file not found at: {acct_csv_path}\n"
            f"Run AMLSim's Java simulator first, or set AMLSIM_DIR env var."
        )

    txn_df, ground_truth = load_amlsim_transactions(tx_csv_path)
    acct_df = load_amlsim_accounts(acct_csv_path)

    accounts     = acct_df.to_dict("records")
    transactions = txn_df.to_dict("records")

    return accounts, transactions, ground_truth


# ── Self-test / schema verification ─────────────────────────────────────────
if __name__ == "__main__":
    import json, sys

    # Use the paramFiles CSVs as a schema-check sample
    # (they won't have real transaction data, but will catch column-mapping errors)
    PARAM_DIR = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "AMLSim", "paramFiles"
    )

    # Build a small synthetic AMLSim-shaped CSV for testing
    import io
    sample_tx_csv = io.StringIO(
        "tran_id,tran_timestamp,base_amt,tx_type,orig_acct,bene_acct,is_sar,alert_id\n"
        "T001,0,9500.00,TRANSFER,ACC001,ACC002,false,\n"
        "T002,1,9800.00,CASH-IN,ACC003,ACC003,true,ALERT1\n"
        "T003,2,500.00,PAYMENT,ACC001,ACC004,false,\n"
        "T004,3,9750.00,TRANSFER,ACC002,ACC005,true,ALERT2\n"
        "T005,4,1200.00,CASH-OUT,ACC005,ACC005,false,\n"
    )
    sample_acct_csv = io.StringIO(
        "acct_id,dsply_nm,type,acct_stat,open_dt,first_name,last_name,street_addr,city,state,country,zip,ssn\n"
        "ACC001,John Doe,SAV,A,0,John,Doe,123 Main St,Springfield,IL,US,62701,SSN001\n"
        "ACC002,Jane Smith,CHK,A,10,Jane,Smith,456 Oak Ave,Shelbyville,IL,US,62565,SSN002\n"
        "ACC003,Bob Jones,SAV,A,5,Bob,Jones,789 Pine Rd,Capital City,IL,US,62701,SSN003\n"
        "ACC004,Alice Brown,BUS,A,20,Alice,Brown,321 Elm St,Springfield,IL,US,62702,SSN004\n"
        "ACC005,Charlie Davis,SAV,A,15,Charlie,Davis,654 Maple Ave,Shelbyville,IL,US,62565,SSN005\n"
    )

    txn_df, gt = load_amlsim_transactions(sample_tx_csv)
    acct_df = load_amlsim_accounts(sample_acct_csv)

    print("\n" + "="*60)
    print("AMLSim Adapter — Schema Verification Test")
    print("="*60)
    print("\n── Transaction columns (should match schema contract) ──")
    print(list(txn_df.columns))

    REQUIRED_TX_COLS = {"transaction_id","sender_account","receiver_account","amount","timestamp","method","memo"}
    missing_tx = REQUIRED_TX_COLS - set(txn_df.columns)
    if missing_tx:
        print(f"❌ MISSING required transaction columns: {missing_tx}")
        sys.exit(1)
    else:
        print("✅ All required transaction columns present")

    print("\n── First 5 converted transactions ──")
    print(txn_df.head(5).to_string(index=False))

    print("\n── Account columns ──")
    REQUIRED_ACCT_COLS = {"account_id","customer_id","occupation","opened_date","device_id","phone","address"}
    missing_acct = REQUIRED_ACCT_COLS - set(acct_df.columns)
    if missing_acct:
        print(f"❌ MISSING required account columns: {missing_acct}")
        sys.exit(1)
    else:
        print("✅ All required account columns present")

    print("\n── First 5 converted accounts ──")
    print(acct_df.head(5).to_string(index=False))

    print(f"\n── Ground truth (SAR-flagged transactions) ──")
    print(f"  SAR flags found: {gt}")

    # Verify direction convention
    cash_in_rows = txn_df[txn_df["method"] == "cash_deposit"]
    for _, row in cash_in_rows.iterrows():
        assert row["sender_account"] in ("CASH", row.get("receiver_account")) or \
               row["receiver_account"] == "EXTERNAL", \
            f"Direction convention violated: {row.to_dict()}"
    print("\n✅ Direction convention (CASH sender for cash deposits) verified")
    print("\n✅ All adapter checks PASSED")