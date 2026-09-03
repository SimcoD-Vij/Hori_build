# Question Structure — Case-Specific Call Scripts
# Fincrime Investigation Voice Engine

This document defines the **exact question set for each Dograh workflow call**,
mapped one-to-one with the detection rules from `detection/rules.py` and the
triage branches from `agents/triage_agent.py`.

---

## Gate Rule — Read Before Anything Else

```
FRAUD_BRANCH  → call ALLOWED  → questions below apply
AML_BRANCH    → call BLOCKED  → no questions, no contact, ever
```

The triage agent decides the branch. Calling on an `AML_BRANCH` case is
illegal tipping-off. This is enforced in code. There is no override.

**Rules that always route `AML_BRANCH` (no call, no questions):**
`structuring` · `graph_fan_hub` · `graph_round_trip` ·
`synthetic_identity_ring` · `peer_group_deviation` ·
`unsupervised_novel_pattern` · `bust_out`

**Rules that can route `FRAUD_BRANCH` (call allowed):**
`zscore_self_history` · `supervised_ml` · `velocity`

---

## Every Call — Non-Negotiable Opening (3 questions, always first)

These run on every call regardless of typology. They are the identity
verification layer. Without passing this, no case-specific questions follow.

| # | Question | Purpose |
|---|---|---|
| V1 | "Can you confirm the full name on the account?" | Identity check |
| V2 | "Can you confirm the last four digits of your card or account number?" | Partial ID — never full number |
| V3 | "Can you confirm the date of birth registered on this account?" | Second factor |

If the customer fails identity verification → **end call, log as `IDENTITY_FAILED`, route to human review**.
Do not proceed to case questions.

---

## Every Call — Core Transaction Questions (4 questions, always second)

After identity is confirmed, these questions run on every FRAUD_BRANCH call
before any typology-specific questions. They satisfy FATF R.10 (CDD — identify
purpose and nature of transaction).

| # | Question | Template Variables |
|---|---|---|
| T1 | "Did you personally authorize a transaction of {amount} on {date}?" | `amount`, `date` |
| T2 | "What was the purpose of this transaction?" | — |
| T3 | "What is your relationship to {beneficiary}?" | `beneficiary` |
| T4 | "Is this a one-time transaction or part of a regular arrangement?" | — |

---

## Workflow 1 — Card-Not-Present Fraud (CNP)

**Triggered by detection rule:** `zscore_self_history` (CNP path) or
`supervised_ml` where the flagged transaction method is `card_not_present`

**Dograh Workflow ID:** `WORKFLOW_CNP`
*(Set this in the Dograh dashboard → name it "CNP Fraud Investigation")*

**Context variables the orchestrator must send:**
`amount`, `date`, `merchant`, `account_holder_name`, `account_id`, `case_id`

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions]

CNP-1: "Do you recognize a charge of {amount} at {merchant} on {date}?"
CNP-2: "Have you shopped with this merchant before, even under a
         different name or website?"
CNP-3: "Is your physical card in your possession right now?"
CNP-4: "Has anyone else had access to your card number or CVV recently —
         family, friends, or anyone you may have shared it with?"
```

### Satisfactory response signals
- "Yes I made that purchase / that was me / I recognize it"
- Names a specific product or reason for the purchase
- Explicitly says card is in possession and no one else has access

### Unsatisfactory / red-flag signals
- "I didn't make that / I don't recognize it"
- "Someone else might have used it"
- Inability to name the merchant or product
- Discloses card was shared with a third party

---

## Workflow 2 — Account Takeover (ATO)

**Triggered by detection rule:** `zscore_self_history` or `supervised_ml`
where the flag evidence references a new device, location, or login anomaly

**Dograh Workflow ID:** `WORKFLOW_ATO`
*(Set this in Dograh → name it "Account Takeover Investigation")*

**Context variables the orchestrator must send:**
`device`, `date`, `amount`, `account_holder_name`, `account_id`, `case_id`

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions]

ATO-1: "Did you recently log in from a new device or an unusual location?"
ATO-2: "Do you recognize this device: {device}?"
ATO-3: "Have you received any password-reset emails or SMS codes you did
         not request in the last 7 days?"
ATO-4: "Did you change your online banking password or security details recently?"
ATO-5: "Have you clicked any links in emails or SMS messages claiming to be
         from your bank recently?"
```

### Satisfactory response signals
- "Yes I logged in from a new phone / I was travelling"
- Correctly identifies the device
- Has not received unrequested security codes

### Unsatisfactory / red-flag signals
- Does not recognize the device or login location
- Reports receiving unrequested OTPs or reset emails
- Says they clicked a link or gave details to someone online

---

## Workflow 3 — High-Velocity Transaction Burst

**Triggered by detection rule:** `velocity`
(8+ outgoing transactions within a 24-hour window)

**Dograh Workflow ID:** `WORKFLOW_VELOCITY`
*(Set this in Dograh → name it "High Velocity Investigation")*

**Context variables the orchestrator must send:**
`amount`, `date`, `account_holder_name`, `account_id`, `case_id`,
`transaction_count` (number of flagged transactions)

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions — adapt T1 to the count, not single amount]

VEL-1: "We noticed {transaction_count} transactions leaving your account
         on {date}. Were all of these made by you personally?"
VEL-2: "Were any of these transfers made in response to an urgent request
         from someone you know — or someone you recently met online?"
VEL-3: "Were you told to keep any of these transfers confidential, or
         to not contact the bank about them?"
VEL-4: "Has anyone gained access to your mobile banking app recently —
         through a screen-share, a downloaded app, or any other means?"
```

### Satisfactory response signals
- Can account for each transfer with specific named recipients and purposes
- No third-party involvement, no urgency, no secrecy request

### Unsatisfactory / red-flag signals
- Cannot explain multiple transfers
- Mentions an urgent or secretive request
- Discloses remote access (screen share, remote desktop, downloaded "bank helper" app)
- Reports being coached by a third party during the call itself

---

## Workflow 4 — Wire Fraud / Business Email Compromise (BEC)

**Triggered by detection rule:** `supervised_ml` or `zscore_self_history`
where the transaction method is `wire` and the beneficiary is a new or
unrecognized account

**Dograh Workflow ID:** `WORKFLOW_WIRE_BEC`
*(Set this in Dograh → name it "Wire Fraud / BEC Investigation")*

**Context variables the orchestrator must send:**
`amount`, `date`, `beneficiary`, `account_holder_name`, `account_id`, `case_id`

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions]

BEC-1: "Can you confirm you personally instructed this wire transfer of
         {amount} to {beneficiary} on {date}?"
BEC-2: "Did you receive any request to change payment details — a different
         account number, routing number, or bank — by email or text recently?"
BEC-3: "Was this transfer communicated with any urgency — were you told
         it had to be done immediately or that you couldn't verify it first?"
BEC-4: "Did you independently verify this payment request through a
         phone number you already had on file — not the number in the email?"
BEC-5: "Have you recently been in contact with {beneficiary} through channels
         other than email, such as by phone or in person?"
```

### Satisfactory response signals
- "Yes I sent it — it's for [specific known purpose], I called them to confirm"
- Has an existing documented relationship with the beneficiary
- Wire was not preceded by a payment-detail change request

### Unsatisfactory / red-flag signals
- "Someone emailed me updated banking details"
- "It was urgent, they said not to call"
- Cannot confirm independent verbal verification
- Beneficiary is unknown or only contacted via email

---

## Workflow 5 — Romance / Advance-Fee Pattern

**Triggered by detection rule:** `supervised_ml` where repeated transfers
to the same new external beneficiary match a romance/advance-fee typology
pattern (low prior contact, emotional framing evidence in account history)

**Dograh Workflow ID:** `WORKFLOW_ROMANCE`
*(Set this in Dograh → name it "Romance Fraud Investigation")*

**Context variables the orchestrator must send:**
`amount`, `date`, `beneficiary`, `account_holder_name`, `account_id`, `case_id`

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions]

ROM-1: "Have you ever met {beneficiary} in person?"
ROM-2: "How did you first make contact with {beneficiary}?"
ROM-3: "Were you asked to keep this transfer confidential — from family,
         friends, or from the bank?"
ROM-4: "Has {beneficiary} asked you for money more than once?"
ROM-5: "Has {beneficiary} ever asked you to receive money into your account
         and then forward it somewhere else?"
```

### Satisfactory response signals
- Has met the person in person, can describe them in detail
- Transfer has a clear documented non-romantic purpose (business, family)
- No secrecy requested, no prior money requests

### Unsatisfactory / red-flag signals
- "We met online / on a dating site / social media"
- "They told me not to tell anyone"
- "They've asked for money before"
- "They said they'd pay me back / they're in trouble"
- Any mention of money passing through their account onward to a third party

---

## Workflow 6 — Default / Mixed Signal

**Triggered by:** `FRAUD_BRANCH` triage with no specific typology match
(e.g., `supervised_ml` flag without a dominant pattern)

**Dograh Workflow ID:** `WORKFLOW_DEFAULT`
*(Set this in Dograh → name it "General Fraud Investigation")*

**Context variables the orchestrator must send:**
`amount`, `date`, `account_holder_name`, `account_id`, `case_id`

### Question Script

```
[V1–V3: Identity verification]
[T1–T4: Core transaction questions]

DEF-1: "Can you walk me through what happened with this transaction of
         {amount} on {date} in your own words?"
DEF-2: "Was this something you personally initiated, or did someone
         else set it up on your behalf?"
DEF-3: "Is there anything else about this transaction you think we
         should know?"
```

---

## Classification Decision Table

| Customer says | Classification | Action |
|---|---|---|
| Specific, credible, verifiable confirmation | `satisfactory` | Eligible for auto-close |
| Denial, confusion, third-party access disclosed | `unsatisfactory` | Route to human review |
| No answer, voicemail, hang-up | `no_response` | Schedule callback, route to human |
| Identity verification failed | `identity_failed` | Immediate human escalation |
| Mentions being coached / third party present | `coerced_flag` | Immediate human escalation + welfare check |

**Ambiguous responses always default to `unsatisfactory`.
Auto-close is only available on explicit `satisfactory`.**

---

## Never Ask (Applies to All Workflows)

- Full SSN, full card number, PIN, password, or full CVV
- The internal rule name, ML score, or typology label that triggered the call
- Race, religion, national origin, immigration status
- Any question implying AML / money-laundering suspicion
  *(If AML is suspected, this call never happens at all — triage blocks it)*
- Leading questions: "Why did you make this fraudulent transfer?"

---

## Dograh Workflow → Fincrime Rule Mapping (Summary)

| Dograh Workflow ID | Typology | Detection Rules That Trigger It |
|---|---|---|
| `WORKFLOW_CNP` | Card-not-present fraud | `zscore_self_history` (CNP method) |
| `WORKFLOW_ATO` | Account takeover | `zscore_self_history` (device/login), `supervised_ml` |
| `WORKFLOW_VELOCITY` | High-velocity burst | `velocity` |
| `WORKFLOW_WIRE_BEC` | Wire fraud / BEC | `supervised_ml` (wire + new beneficiary) |
| `WORKFLOW_ROMANCE` | Romance / advance-fee | `supervised_ml` (repeat transfer, social pattern) |
| `WORKFLOW_DEFAULT` | General / mixed signal | `FRAUD_BRANCH` with no dominant typology |
| *(none)* | All AML typologies | `structuring`, `graph_*`, `bust_out`, `peer_group_deviation`, `synthetic_identity_ring` |

---

## How the Code Uses This

In `calling_agent.py`, the `select_questions()` function picks the question
bank using the `matched_typology` field from the risk assessment. The
`QUESTION_BANKS` dict keys must match the typology strings exactly.

In `server.py`, when `/trigger-call` fires, it sets `DOGRAH_WORKFLOW_ID`
from an environment variable — which means **one workflow ID per deployment**.

To support multiple workflow IDs (one per typology), set a mapping in `.env`:

```env
DOGRAH_WORKFLOW_ID_CNP=wf_abc123
DOGRAH_WORKFLOW_ID_ATO=wf_def456
DOGRAH_WORKFLOW_ID_VELOCITY=wf_ghi789
DOGRAH_WORKFLOW_ID_WIRE_BEC=wf_jkl012
DOGRAH_WORKFLOW_ID_ROMANCE=wf_mno345
DOGRAH_WORKFLOW_ID_DEFAULT=wf_pqr678
```

And `server.py` selects the right one based on `fraud_type` in the payload.
