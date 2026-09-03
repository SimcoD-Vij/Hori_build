"""
SEC EDGAR company lookup -- an evidence-agent tool for researching
publicly traded companies (filings, ownership, executives) named in an
investigation, per the "OSINT/research assistant" Tier-1 use case.

HONEST LIMITATION: this sandbox's network egress does not include
data.sec.gov (confirmed directly -- see INSTRUCTIONS.md), so the live
HTTP call below has NOT been tested against the real API from here. The
parsing and error-handling logic HAS been tested, using a mocked
response shaped like SEC EDGAR's real documented format. Test the live
call yourself after cloning -- your machine's Docker container will have
normal internet access. If the response shape has changed since this was
written, the parsing logic (not the request logic) is what to check
first.

SEC EDGAR's public API requires a descriptive User-Agent header
identifying your organization -- requests without one are rejected.
Replace the placeholder below with your own contact info before using
this in anything beyond local testing.
"""
import requests

USER_AGENT = "fincrime-system-prototype contact@example.com"  # REQUIRED: replace before real use
EDGAR_BASE = "https://data.sec.gov"


def lookup_company_by_cik(cik: str) -> dict:
    """cik: SEC's Central Index Key, e.g. '0000320193' for Apple. Zero-padded to 10 digits."""
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/submissions/CIK{cik_padded}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "found": True,
            "company_name": data.get("name"),
            "sic_description": data.get("sicDescription"),
            "addresses": data.get("addresses"),
            "former_names": [n.get("name") for n in data.get("formerNames", [])],
            "recent_filings_count": len(data.get("filings", {}).get("recent", {}).get("form", [])),
        }
    except requests.exceptions.RequestException as e:
        return {"found": False, "error": str(e), "note": "SEC EDGAR lookup failed -- network issue, "
                                                           "invalid CIK, or rate limiting. Not a code bug per se."}
    except (KeyError, ValueError) as e:
        return {"found": False, "error": f"Unexpected response shape: {e}",
                "note": "SEC EDGAR's response format may have changed -- check their current API docs."}
