"""Evidence agent: compiles a structured, sourced evidence packet for one account."""


def gather_evidence(account_id: str, all_flags: list, accounts_by_id: dict, identity_graph_links=None) -> dict:
    acct_flags = [f for f in all_flags if f["account_id"] == account_id]
    acct = accounts_by_id.get(account_id, {})
    evidence = {
        "account_id": account_id,
        "occupation": acct.get("occupation", "unknown"),
        "account_age_days": None,
        "flags": acct_flags,
        "sources": sorted({f["rule"] for f in acct_flags}),
        "linked_accounts": identity_graph_links or [],
        "sanctions_hit": False,  # stub -- wire to knowledge_base/sanctions in production
    }
    if "opened_date" in acct:
        import pandas as pd
        evidence["account_age_days"] = (pd.Timestamp.now() - pd.to_datetime(acct["opened_date"])).days
    return evidence
