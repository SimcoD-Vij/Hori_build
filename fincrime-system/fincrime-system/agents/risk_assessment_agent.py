"""Risk assessment agent: matches evidence against the typology catalog, only citing red flags actually present."""
import json
import os

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "detection", "typology_catalog.json")
with open(CATALOG_PATH) as f:
    TYPOLOGY_CATALOG = json.load(f)


def assess_risk(evidence: dict) -> dict:
    present_rules = set(evidence["sources"])
    best_match, best_score = None, 0.0
    for typ in TYPOLOGY_CATALOG:
        red_flags = set(typ["red_flags"])
        overlap = present_rules & red_flags
        if not red_flags:
            continue
        score = len(overlap) / len(red_flags)
        if score > best_score:
            best_score, best_match = score, typ

    if best_match is None or best_score < 0.4:
        matched_typology = "NOVEL_PATTERN"
        confidence = 0.0
        branch_hint = "AML_BRANCH"
        matched_flags = []
    else:
        matched_typology = best_match["typology"]
        confidence = round(best_score, 2)
        branch_hint = best_match["branch"]
        matched_flags = sorted(present_rules & set(best_match["red_flags"]))

    severity = min(10, max((f.get("severity_hint", 0) for f in evidence["flags"]), default=0))

    return {
        "account_id": evidence["account_id"],
        "matched_typology": matched_typology,
        "confidence": confidence,
        "matched_red_flags": matched_flags,
        "severity": severity,
        "branch_hint": branch_hint,
        "reasoning": (
            f"Evidence shows {len(evidence['flags'])} flag(s) from source(s) {sorted(present_rules)}. "
            f"{'No confident typology match -- routed as a novel pattern for fresh human review.' if matched_typology == 'NOVEL_PATTERN' else f'Best match: {matched_typology} (confidence {confidence}), supported by {matched_flags}.'}"
        ),
    }
