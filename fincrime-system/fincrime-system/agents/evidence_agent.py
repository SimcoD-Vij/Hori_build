"""Evidence agent: compiles a structured, sourced evidence packet for one account."""
# Guide Step 4: replace stub with real OpenSanctions call (this IS a replacement, not an addition)
from knowledge_base.sanctions_screen import screen_sanctions

# In-memory stats counters (reset on restart)
_stats = {"cases_processed": 0, "sanctions_hits": 0, "accounts_with_linked_identities": 0, "total_flags": 0}



def gather_evidence(account_id: str, all_flags: list, accounts_by_id: dict, identity_graph_links=None) -> dict:
    acct_flags = [f for f in all_flags if f["account_id"] == account_id]
    acct = accounts_by_id.get(account_id, {})

    # Real sanctions screen using the account holder's name as the search term.
    # screen_sanctions() fails open (returns hit=False) if the API is unreachable,
    # so this never crashes the evidence pipeline.
    customer_name = acct.get("customer_id", "")
    sanctions_result = screen_sanctions(customer_name) if customer_name else {"hit": False, "list": None, "confidence": 0.0}

    evidence = {
        "account_id": account_id,
        "occupation": acct.get("occupation", "unknown"),
        "account_age_days": None,
        "flags": acct_flags,
        "sources": sorted({f["rule"] for f in acct_flags}),
        "linked_accounts": identity_graph_links or [],
        "sanctions_hit": sanctions_result["hit"],
        "sanctions_list": sanctions_result.get("list"),
        "sanctions_confidence": sanctions_result.get("confidence", 0.0),
        "entity_resolution": "opensanctions_api",  # evidence source name for audit trail
    }
    if "opened_date" in acct:
        import pandas as pd
        evidence["account_age_days"] = (pd.Timestamp.now() - pd.to_datetime(acct["opened_date"])).days

    # Track stats
    _stats["cases_processed"] += 1
    _stats["total_flags"] += len(acct_flags)
    if sanctions_result["hit"]:
        _stats["sanctions_hits"] += 1
    if identity_graph_links:
        _stats["accounts_with_linked_identities"] += 1

    return evidence


def get_stats() -> dict:
    """Return evidence gathering statistics for the visual interface."""
    processed = _stats["cases_processed"]
    return {
        **_stats,
        "avg_flags_per_case": round(_stats["total_flags"] / processed, 2) if processed else 0.0,
    }

