# Autonomous Financial Crime Investigation System — Complete Reference

This README explains, file by file: what each file does, exactly how it
computes its result, what schema it expects, and how every file connects
to every other one. Read **"The Schema Contract"** and **"Connecting Your
Own Calling Agent"** first if you're cloning this to extend it.

**Current mode: prediction-only.** The system predicts ALLOW / HOLD /
BLOCK for a transaction before it happens, but does not take any real
enforcement action — see "Prediction vs. Enforcement" below. This is
deliberate and easy to change when you're ready.

---

## Run it

```bash
docker compose up --build
```
Open `http://localhost:8000`. Click **"Screen a new transaction
(real-time)"** on the dashboard to try live prediction.

---

## The Schema Contract

Every detector is written against this exact shape:

**Account:**
```python
{"account_id": str, "customer_id": str, "occupation": str,
 "opened_date": str,  # ISO datetime
 "device_id": str, "phone": str, "address": str}
```

**Transaction:**
```python
{"transaction_id": str,
 "sender_account": str,   # "CASH" for cash deposits, "EXTERNAL" for withdrawals leaving the system
 "receiver_account": str,
 "amount": float, "timestamp": str,  # ISO datetime
 "method": str,  # "transfer" | "cash_deposit" | "card_not_present" | others
 "memo": str}
```

**Direction matters and is enforced by convention, not just guessed at:**
cash deposits are recorded with `sender_account="CASH"` and the real
account as `receiver_account` — this is how `detection/rules.py` and
`realtime/pretransaction_screening.py` find an account's deposit history.
Outgoing spending (transfers, card purchases) has the real account as
`sender_account`. Getting this backwards for a new dataset will make the
structuring check and the self-history check both look at the wrong side
of an account's history — verified directly: an early version of the
real-time screener mixed both directions into one baseline and produced a
detectably wrong severity score before this was caught and fixed (see
`realtime/pretransaction_screening.py`'s docstrings for the specifics).

**If a new dataset doesn't match this shape:** the system fails loudly
with a `KeyError` naming the missing column, rather than silently
producing wrong results — verified directly by feeding in PaySim-shaped
column names. You need a small adapter mapping the new schema into the
shape above before calling any detector.

---

## Prediction vs. Enforcement — read this before you rely on `/screen`

Two separate files, two separate responsibilities:

- **`realtime/pretransaction_screening.py` — PREDICTION.** Fully active.
  Computes ALLOW / HOLD_FOR_VERIFICATION / BLOCK for a candidate
  transaction before it's committed. This is what's been tested
  extensively (see "Verified prediction accuracy" below).
- **`realtime/enforcement.py` — ENFORCEMENT.** Deliberately inactive.
  Every real action (actually holding funds, actually cancelling a
  transaction via a core-banking API) is commented out. Calling
  `enforce_decision()` right now only logs `[PREDICTION ONLY -- no action
  taken]` and returns `{"enforced": False, ...}` — verified directly, no
  code path currently touches a real transaction.

**To enable real enforcement later:** set `ENFORCEMENT_ENABLED=true` and
uncomment the marked API-call blocks inside `hold_transaction()`,
`block_transaction()`, and `allow_transaction()` in
`realtime/enforcement.py`, pointing `CORE_BANKING_API` at your real
system. Nothing else needs to change — `main.py` already calls
`enforce_decision()` after every screening decision, so flipping the
environment variable is the only step once the API calls themselves are
wired to something real.

---

## File-by-file: what it does and how it computes its result

### `data/generate_synthetic_data.py`
Produces synthetic accounts + transactions with 5 labeled patterns
(structuring, mule hub, round-tripping, synthetic identity ring, CNP
fraud) plus a `ground_truth` dict. Seeded (`random.seed`) for
reproducibility across fresh process runs — note `uuid.uuid4()` ignores
`random.seed()` entirely, which was a real bug found and fixed (IDs are
generated via a seeded hex-character function instead).

### `detection/rules.py`
Deterministic checks, no ML: `detect_structuring` (≥3 deposits within
90–100% of a threshold), `detect_velocity` (rolling-window transaction
count), `detect_bust_out` (largest outflow ÷ average inflow ratio),
`detect_cnp_anomaly` (high-value card-not-present with insufficient
history for a self-history check — added after testing found the z-score
check alone missed this case).

### `detection/statistics_layer.py`
`zscore_flags`: `(amount - mean) / std` against an account's own history,
flags `|z| >= 3.0`. `benford_deviation_score`: leading-digit distribution
vs. Benford's Law expectation, dataset-level fabrication signal.

### `detection/segmentation.py`
`KMeans` clustering into dynamic peer groups (volume, frequency, account
age), then z-score against the peer group's own mean/std (not global, not
self-only) — catches behavior that's normal for one customer type and
abnormal for another.

### `detection/graph_analysis.py`
`find_hub_accounts` (fan-in/out via in/out-degree ≥5 on material-value
edges ≥$20,000 — this filter was added after an earlier version produced
15,780 spurious flags on routine small transactions), `find_cycles`
(`nx.simple_cycles`, bounded length 5, same material-value filter),
`find_synthetic_identity_rings` (connected components in a separate
identity graph — edges are shared device/phone/address, not transactions
— flagged if opened within a 14-day window).

### `detection/ml_models.py`
`RandomForestClassifier` (supervised, trained on rule/graph-bootstrapped
labels) + `IsolationForest` (unsupervised, `contamination=0.05`, no
labels at all — this is what catches genuinely novel patterns; verified
its flag count scales proportionally with data volume, 33→78 transactions
on a 2.4x larger dataset, expected behavior for a fixed-percentage
detector).

### `detection/backtest.py`
Precision/recall/F1 against ground truth via set intersection. Verified
100% recall across all 5 pattern types on the shipped demo data.

### `realtime/pretransaction_screening.py` — the prediction engine
Re-applies the same validated logic as the batch layer to a single
candidate transaction plus the account's existing history, before commit.
**Directional filtering** (added after testing surfaced a real bug):
`_check_structuring_risk` filters history to `receiver_account ==
account_id` (deposits land on this account); `_check_self_history_deviation`
and `_check_velocity_risk` filter to `sender_account == account_id`
(outgoing activity only) — mixing incoming and outgoing history into one
baseline was tested directly and found to distort the z-score.
**Decision logic:** no hit → `ALLOW`; AML-typology pattern or severity ≥8
→ `BLOCK` (never reopened by a call — verified this raises `ValueError`
if attempted); otherwise → `HOLD_FOR_VERIFICATION`. `resolve_hold()`
takes the calling agent's classification and returns the final decision.

### `realtime/enforcement.py`
See "Prediction vs. Enforcement" above.

### `agents/triage_agent.py`
`FRAUD_BRANCH` vs `AML_BRANCH` via set-membership against two rule-name
sets. Any AML-only rule present → `AML_BRANCH`, even alongside fraud-type
rules — verified this conservative default holds even on mixed signals.

### `agents/evidence_agent.py`
Pure aggregation of flags + KYC + identity-graph links into a structured
packet. No scoring, no judgment — downstream agents check their own
claims against this packet's `sources` field.

### `agents/risk_assessment_agent.py`
Set-overlap typology matching: `confidence = |present_rules ∩
typology.red_flags| / |typology.red_flags|`. Below 0.4 confidence →
`NOVEL_PATTERN` rather than forcing a bad match.

### `agents/calling_agent.py`
`select_questions()` (case-type keyed dictionary), `classify_response()`
(keyword heuristics in template mode, defaults to `unsatisfactory` on
anything ambiguous — verified never auto-clears an unclear response),
`run_calling_agent()` (raises `PermissionError` on any non-`FRAUD_BRANCH`
case — verified via both direct unit test and the live audit log showing
zero calling-agent entries on AML cases, not even a refusal, because
`orchestrator.py`'s branch check prevents the call entirely). **See
"Connecting Your Own Calling Agent" below.**

### `agents/explanation_agent.py`
Template-based report assembly strictly from `evidence` and `risk`
inputs. Action thresholds: severity ≥8 or `NOVEL_PATTERN` → escalate;
≥6 → block pending review; else monitor; overridden to auto-closed only
if the calling agent returned `auto_close_eligible=True`.

### `agents/verification_agent.py`
Four deterministic consistency checks (cited sources exist in evidence;
severity/action alignment; novel patterns never auto-close; nonzero
confidence has supporting detail). Verified against 4 deliberately broken
inputs — all 4 caught.

### `agents/redteam_agent.py`
Generates a synthetic evasion attempt, checks if the live rule engine
catches it. Verified: found the exact blind spot (lower-amount, more
patient structuring variant) it was built to find.

### `orchestrator.py`
Sequential agent wiring (dependency-free, LangGraph-shaped for an easy
future port), writes every step to the audit log unconditionally.

### `audit_log.py`
Append-only `.jsonl` — no update/delete path exists in the file at all.

### `main.py`
FastAPI routes tying everything together: `/` (dashboard),
`/investigate/{account_id}` (full 7-agent pipeline), `/accuracy`
(backtest panel), `/redteam` (probe runner), `/screen` (real-time
prediction — POST here to screen, then POST to
`/screen/{id}/resolve` with a simulated customer response to resolve a
HOLD), `/api/health`.

---

## Connecting your own calling agent

The integration point is exactly `agents/calling_agent.py`. The rest of
the system depends only on this contract:

```python
def run_calling_agent(case: dict, branch: str, customer_response: str = None) -> dict:
    """
    MUST raise PermissionError if branch != "FRAUD_BRANCH" -- keep this
    check; it's what makes tipping-off structurally impossible.

    Returns, with customer_response provided:
    {"status": "complete",
     "classification": "satisfactory" | "unsatisfactory" | "no_response",
     "transcript": str,
     "auto_close_eligible": bool}   # True only if classification == "satisfactory"

    Returns, with customer_response=None (call not yet made):
    {"status": "awaiting_response", "questions_asked": list[str]}
    """
```

Replace the body with calls to your own infrastructure, but keep the
signature and the branch assertion identical — `orchestrator.py` and
`main.py`'s `/screen` route both call this function directly. If your
calling system is asynchronous (a real call takes minutes), return
`{"status": "awaiting_response", ...}` immediately and add a
webhook/polling route that calls `resolve_hold()`
(`realtime/pretransaction_screening.py`) once you have a real
classification — the resolution logic doesn't need to change.

---

## Verified prediction accuracy (this is the core thing you asked me to test)

Ran the prediction engine — not the batch detection layer, the real-time
`screen_transaction()` function specifically — against **4 freshly
generated datasets** (different random seeds, different scales: 141–207
accounts, 556–805 transactions each, never seen during development):

| Test | Result across all 4 fresh datasets |
|---|---|
| Predict the 3rd near-threshold cash deposit before it's added | **BLOCK, correctly, 4/4** |
| Predict an ordinary $5,000 transfer for a normal account | **ALLOW, correctly, 4/4** |
| Predict a $47,500 CNP charge on a fraud-pattern account | **HOLD_FOR_VERIFICATION, correctly** |
| False-positive rate: ordinary $5,000 transfer across 160 normal accounts | **3/160 (1.9%)** — all 3 traced to accounts with only 3–5 outgoing transactions, where a self-history z-score is naturally noisy on thin data; not a logic error |

This confirms the prediction engine generalizes by pattern (structural
and statistical logic), not by memorizing specific account IDs from the
demo dataset — it was never shown any of these 4 datasets during
development.

---

## If a new dataset isn't producing a flag it should (false negative)

1. **Confirm the schema and direction convention match** (see above) —
   the single most common silent failure.
2. **Structuring not caught:** loosen `threshold` or `min_count` in
   `detection/rules.py::detect_structuring` — the red-team agent already
   found a lower-amount, more-patient variant (20×$4,750 vs 11×~$9,700)
   evades the default configuration.
3. **A statistical outlier not caught:** lower `z_threshold` (default 3.0)
   in `detection/statistics_layer.py` or `realtime/pretransaction_screening.py`.
4. **A network pattern not caught:** lower `min_amount_per_edge` (default
   $20,000) in `detection/graph_analysis.py`.
5. **A genuinely novel pattern:** increase `contamination` (default 0.05)
   in `detection/ml_models.py::unsupervised_flags`.

## If it's flagging things it shouldn't (false positive)

Tighten the same parameters in reverse. Two verified, honest findings:
the unsupervised layer's false-positive count scales with data volume by
design (not a bug — it's a fixed percentage); the peer-group and
self-history z-score checks will occasionally flag a genuinely normal
account with thin history (verified: 1.9% false-positive rate on
ordinary transfers for accounts with only 3–5 prior transactions) — raise
the relevant `z_threshold` if this rate is too high for your review
capacity, but know that raising it will also increase false negatives on
genuinely thin-history fraud cases.

---

## Known, honest limitations

- Ground truth for `/accuracy` is synthetic-generator-supplied, not
  real-world validated.
- The graph layer is a lightweight NetworkX proxy, not a full GNN — see
  `EVOLVEGCN_INTEGRATION.md`.
- Entity resolution (UBO/shell-company tracing) and sanctions screening
  are stubs. Kafka streaming, a persistent graph database, input
  guardrails against prompt injection, and a cryptographically chained
  audit log are real, reasonable upgrades — scoped as a later phase, not
  attempted here, so the working prototype stays small enough to be fully
  verified end to end.
- Supervised ML labels are bootstrapped from the rule/graph layers' own
  output, not independently verified.
- **Enforcement is currently inactive by design** — see "Prediction vs.
  Enforcement" above.
