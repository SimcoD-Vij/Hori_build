"""
Synthetic data generator for the financial crime investigation system.

Produces accounts + transactions with embedded, labeled patterns matching
real typologies (structuring, mule fan-out, round-tripping, synthetic
identity rings) so the detection layer has something real to catch.
This stands in for PaySim/AMLSim in the runnable prototype -- swap in
those real repos later by pointing data/ingestion at their CSVs instead.
"""
import random
from datetime import datetime, timedelta

random.seed(42)

OCCUPATIONS = ["salaried_engineer", "small_business_owner", "student", "retired", "self_employed_trader"]


def _rid(n=8):
    """Seeded ID generator -- uuid.uuid4() ignores random.seed(), which made IDs
    non-reproducible across runs. This fixes that so the same account IDs appear
    every time the same seed is used, which matters for testing and demoing a
    specific case."""
    return "".join(random.choice("0123456789abcdef") for _ in range(n))


FIRST_NAMES = ["John", "Mary", "James", "Sarah", "Michael", "Emma", "David", "Lisa", "Tom", "Anna", "Paul", "Mark", "Karen"]
LAST_NAMES = ["Smith", "Jones", "Brown", "Davis", "White", "Clark", "Hall", "King", "Green", "Baker", "Hill", "Scott", "Adams"]

def _new_account(occupation=None, opened_days_ago=None, device_id=None, phone=None, address=None):
    return {
        "account_id": _rid(),
        "customer_id": f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
        "occupation": occupation or random.choice(OCCUPATIONS),
        "opened_date": (datetime.now() - timedelta(days=opened_days_ago if opened_days_ago is not None else random.randint(30, 2000))).isoformat(),
        "device_id": device_id or f"dev-{random.randint(1000,9999)}",
        "phone": phone or f"+91-9{random.randint(100000000,999999999)}",
        "address": address or f"{random.randint(1,999)} MG Road, Chennai",
    }


def _txn(sender, receiver, amount, days_ago, method="transfer", memo=""):
    return {
        "transaction_id": _rid(n=10),
        "sender_account": sender,
        "receiver_account": receiver,
        "amount": round(amount, 2),
        "timestamp": (datetime.now() - timedelta(days=days_ago, hours=random.randint(0, 23))).isoformat(),
        "method": method,
        "memo": memo,
    }


def generate(n_normal_accounts=120, n_normal_txns=600):
    accounts = []
    transactions = []
    ground_truth = {}  # account_id -> pattern name, for accuracy backtesting

    # --- Normal population ---
    normal_accts = [_new_account() for _ in range(n_normal_accounts)]
    accounts += normal_accts
    for _ in range(n_normal_txns):
        a, b = random.sample(normal_accts, 2)
        transactions.append(_txn(a["account_id"], b["account_id"], round(random.uniform(200, 15000), 2),
                                  random.randint(0, 60), memo="routine payment"))

    # --- Pattern 1: Structuring (cash deposits just under threshold) ---
    struct_acct = _new_account(occupation="self_employed_trader")
    accounts.append(struct_acct)
    ground_truth[struct_acct["account_id"]] = "structuring"
    for i in range(11):
        transactions.append(_txn("CASH", struct_acct["account_id"], random.uniform(9500, 9900),
                                  20 - i, method="cash_deposit", memo="business cash deposit"))

    # --- Pattern 2: Mule fan-in / fan-out hub ---
    hub = _new_account(occupation="student")
    accounts.append(hub)
    ground_truth[hub["account_id"]] = "mule_hub"
    sources = [_new_account() for _ in range(5)]
    dests = [_new_account() for _ in range(5)]
    accounts += sources + dests
    for s in sources:
        transactions.append(_txn(s["account_id"], hub["account_id"], random.uniform(40000, 90000), 5))
    for d in dests:
        transactions.append(_txn(hub["account_id"], d["account_id"], random.uniform(30000, 80000), 4))

    # --- Pattern 3: Round-tripping (cycle) ---
    ring = [_new_account() for _ in range(4)]
    accounts += ring
    for acct in ring:
        ground_truth[acct["account_id"]] = "round_tripping"
    for i in range(4):
        transactions.append(_txn(ring[i]["account_id"], ring[(i + 1) % 4]["account_id"],
                                  round(random.uniform(50000, 60000), 2), 3))

    # --- Pattern 4: Synthetic identity ring (shared device/phone/address, opened close together) ---
    shared_device = "dev-9999"
    shared_phone = "+91-9000000000"
    shared_address = "12 Anna Salai, Chennai"
    sif_ring = [_new_account(device_id=shared_device, phone=shared_phone, address=shared_address,
                              opened_days_ago=random.randint(1, 10)) for _ in range(5)]
    accounts += sif_ring
    for acc in sif_ring:
        ground_truth[acc["account_id"]] = "synthetic_identity_ring"
        # looks clean for a while, then bursts
        for i in range(3):
            transactions.append(_txn(random.choice(normal_accts)["account_id"], acc["account_id"],
                                      random.uniform(1000, 3000), 15 - i * 3, memo="normal-looking activity"))
        transactions.append(_txn(acc["account_id"], "EXTERNAL", random.uniform(80000, 150000), 1,
                                  memo="bust-out withdrawal"))

    # --- Pattern 5: Fraud-branch case (card-not-present, safe to call the customer) ---
    victim = _new_account()
    accounts.append(victim)
    ground_truth[victim["account_id"]] = "card_not_present_fraud"
    transactions.append(_txn(victim["account_id"], "MERCHANT-UNKNOWN-8831", 47500.00, 0,
                              method="card_not_present", memo="electronics purchase - unrecognized merchant"))

    return accounts, transactions, ground_truth


if __name__ == "__main__":
    import json
    accts, txns, truth = generate()
    print(f"Generated {len(accts)} accounts, {len(txns)} transactions, {len(truth)} ground-truth labels")
    import os
    base_dir = os.path.dirname(__file__)
    with open(os.path.join(base_dir, "accounts.json"), "w") as f:
        json.dump(accts, f, indent=2)
    with open(os.path.join(base_dir, "transactions.json"), "w") as f:
        json.dump(txns, f, indent=2)
