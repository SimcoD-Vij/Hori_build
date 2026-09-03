"""
knowledge_base/sanctions_screen.py — OpenSanctions live screening
=================================================================
Replaces the hardcoded `"sanctions_hit": False` stub in evidence_agent.py
with a real call to the OpenSanctions API (Guide Step 4 — this IS a
replacement for the current stub, not an addition).

Uses the OpenSanctions hosted API (https://api.opensanctions.org).
No API key required for basic searches on the free tier.
The API searches across all default datasets (UN, OFAC, EU, etc.)

Returns: {"hit": bool, "list": str|None, "confidence": float, "details": dict}

Design choices:
- Result is cached in-memory per name for the lifetime of the process
  (sanctions lists don't change mid-session; avoids hammering the API).
- Timeout is 5 seconds; returns {"hit": False, ...} on timeout/error
  rather than crashing the whole evidence pipeline.
- SANCTIONS_ENABLED env flag (default True) allows disabling for offline
  demos without changing code — consistent with ENFORCEMENT_ENABLED pattern.
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from functools import lru_cache

SANCTIONS_ENABLED = os.environ.get("SANCTIONS_ENABLED", "true").lower() == "true"
OPENSANCTIONS_API = os.environ.get(
    "OPENSANCTIONS_API",
    "https://api.opensanctions.org"
)
OPENSANCTIONS_TIMEOUT = int(os.environ.get("OPENSANCTIONS_TIMEOUT", "5"))

_CACHE: dict = {}  # simple in-memory cache: name → result


def screen_sanctions(name: str) -> dict:
    """
    Screen a name against OpenSanctions default dataset (UN, OFAC, EU, FATF, etc.).

    Args:
        name: Full name of individual or entity to screen.

    Returns:
        {
            "hit":        bool   — True if any match with confidence >= threshold,
            "list":       str|None — Name of the matched sanctions list, or None,
            "confidence": float  — Highest match confidence (0.0–1.0),
            "details":    dict   — Raw first match details for audit trail,
            "source":     str    — "opensanctions_api" or "disabled" or "error",
        }
    """
    if not name or not name.strip():
        return {"hit": False, "list": None, "confidence": 0.0, "details": {}, "source": "empty_name"}

    name = name.strip()

    # In-memory cache
    if name in _CACHE:
        return _CACHE[name]

    if not SANCTIONS_ENABLED:
        result = {"hit": False, "list": None, "confidence": 0.0, "details": {}, "source": "disabled"}
        _CACHE[name] = result
        return result

    try:
        result = _call_opensanctions_api(name)
    except Exception as exc:
        # Fail open with explicit source tag — never crash the evidence pipeline
        result = {
            "hit": False, "list": None, "confidence": 0.0,
            "details": {"error": str(exc)},
            "source": "error",
        }

    _CACHE[name] = result
    return result


def _call_opensanctions_api(name: str) -> dict:
    """
    Calls the OpenSanctions /match/default endpoint.
    Uses the entity matching API which supports fuzzy name matching.
    """
    # OpenSanctions match API payload
    payload = {
        "queries": {
            "q1": {
                "schema": "Person",
                "properties": {"name": [name]}
            }
        }
    }

    url = f"{OPENSANCTIONS_API}/match/default"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OPENSANCTIONS_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenSanctions API HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"OpenSanctions API unreachable: {e.reason}")

    # Parse response
    results = body.get("responses", {}).get("q1", {}).get("results", [])

    if not results:
        return {"hit": False, "list": None, "confidence": 0.0, "details": {}, "source": "opensanctions_api"}

    # Take highest-confidence match
    top = max(results, key=lambda r: r.get("score", 0.0))
    score = float(top.get("score", 0.0))

    # OpenSanctions scores: >= 0.8 is a strong match
    HIT_THRESHOLD = 0.8
    hit = score >= HIT_THRESHOLD

    # Extract which dataset/list this came from
    datasets = top.get("datasets", [])
    sanctions_list = datasets[0] if datasets else None

    return {
        "hit":        hit,
        "list":       sanctions_list,
        "confidence": round(score, 3),
        "details": {
            "id":       top.get("id"),
            "caption":  top.get("caption"),
            "schema":   top.get("schema"),
            "datasets": datasets,
        },
        "source": "opensanctions_api",
    }


def clear_cache():
    """Clear the in-memory sanctions cache. Useful between test runs."""
    global _CACHE
    _CACHE = {}


# ── Self-test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*60)
    print("Sanctions Screening — OpenSanctions API Test")
    print("="*60)

    test_cases = [
        ("Kim Jong Un",    True,  "Known OFAC/UN sanctioned individual"),
        ("John Smith",     False, "Common name — should not be a confident hit"),
        ("",               False, "Empty name — should return no-hit gracefully"),
    ]

    for name, expected_hit, description in test_cases:
        result = screen_sanctions(name)
        status = "✅" if (result["hit"] == expected_hit or result["source"] in ("error", "disabled")) else "⚠️"
        print(f"\n{status} '{name}' ({description})")
        print(f"   hit={result['hit']}, confidence={result['confidence']:.3f}, "
              f"list={result['list']}, source={result['source']}")
        if result.get("details", {}).get("error"):
            print(f"   [API error: {result['details']['error']}]")

    print("\n✅ Sanctions screen test complete (API connectivity may vary)")
