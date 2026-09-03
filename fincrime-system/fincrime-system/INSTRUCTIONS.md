# INSTRUCTIONS: Coverage Analysis & Tool Integration Decisions

This document does three things: checks whether the system already
handles the challenges/use-cases from your source material, evaluates
each open-source tool it mentions for whether merging it in is actually
safe, and tells you exactly what to do next for each one.

**One thing worth naming directly**: your source material is explicitly
Reddit-derived opinion, and it says so itself. I've treated the practical
recommendations (tiered risk hierarchy, human-in-the-loop architecture)
as generally sound and consistent with everything else this project has
been built against — but I have not treated any specific regulatory claim
in it as verified, and neither should you before citing one.

---

## Part 1 — Does the system already handle this? (Tier-by-tier check)

| Source's tier | Use case | This system | Status |
|---|---|---|---|
| 🟢 Tier 1 | Case QA (review completed investigations) | **`agents/qa_agent.py` — just built and tested this turn** | ✅ Now covered |
| 🟢 Tier 1 | SAR/STR drafting | `agents/explanation_agent.py` | ✅ Covered |
| 🟢 Tier 1 | Case summarization | Explanation agent's report generation | ✅ Covered |
| 🟢 Tier 1 | Evidence organization | `agents/evidence_agent.py` | ✅ Covered |
| 🟢 Tier 1 | Investigator research assistant | Partial — evidence gathering exists; OSINT/external research does not (see Part 2) | ⚠️ Partial |
| 🟡 Tier 2 | Alert prioritization | Triage agent's priority score | ✅ Covered |
| 🟡 Tier 2 | Entity resolution | Stub only (documented gap) | ⚠️ Stub |
| 🟡 Tier 2 | Relationship/graph analysis | `detection/graph_analysis.py` | ✅ Covered |
| 🟡 Tier 2 | Anomaly detection | Statistical + unsupervised layers | ✅ Covered |
| 🟡 Tier 2 | Predictive risk modeling | `realtime/pretransaction_screening.py` | ✅ Covered — and this is actually ahead of what the source describes, which frames prediction as "surfacing relationships for an investigator," not as a live allow/hold/block decision |
| 🟠 Tier 3 | Auto-closing low-risk alerts | Calling agent's `auto_close_eligible` path | ✅ Covered, and already built with the source's stated precondition — strong controls + auditability — via the hard branch-safety check and full audit logging |
| 🔴 Tier 4 | Autonomous SAR filing / account closure / accusation | **Deliberately not built** | ✅ Correctly absent — the system escalates to human review or leaves enforcement disabled by default (`ENFORCEMENT_ENABLED=false`), consistent with the source's strongest recurring argument |

**Overall: the architecture already matches the source's recommended
shape** — human-in-the-loop, tiered risk, explainable reasoning, audit
trail, no autonomous consequential action — and the one clean gap (Case
QA) is now closed. The genuinely open gap is Tier 1's "research
assistant" / OSINT capability, which is where the tool-by-tool analysis
below matters.

---

## Part 2 — Tool-by-tool: safe to merge, or not?

| Tool | What it is | Verdict | Why |
|---|---|---|---|
| **SEC EDGAR** | Public company filings API | ✅ **Added and tested this turn** — `knowledge_base/sec_edgar_lookup.py` | Simple public JSON API, no auth beyond a User-Agent string, low integration risk. **Honest caveat: live connectivity was NOT tested from this sandbox** (`data.sec.gov` isn't reachable from here — confirmed directly). Parsing and error-handling logic were tested with mocked responses matching the real documented format. **Test the live call yourself after cloning** — your Docker container will have normal internet access. |
| **Bellingcat Toolkit** | Curated directory of OSINT tools (maps, geolocation, image verification, etc.) | ❌ Not merged — and shouldn't be | It's a directory of *other* tools, not a library or API itself. There's nothing to "integrate" — it's a reference. **Action: add its URL as a documented research resource for a human investigator to consult during evidence gathering, not as code.** |
| **DFIRe** | Full self-hosted case-management platform (evidence, timelines, chain-of-custody) | ❌ Not merged | This is a complete, separate application with its own database and UI — merging it into this codebase would mean running two case-management systems that both think they own the case record, which is a data-integrity risk, not a feature. **Action: if you want DFIRe's capabilities, run it as a separate service and export this system's audit log / case reports to it, rather than merging code.** |
| **Refloow Geo Forensics** | Image/video metadata + geolocation forensics tool | ❌ Not merged | Real, verifiable GitHub project, but it addresses a different problem (verifying physical evidence like photos) than this system currently handles (transaction data). No media evidence exists anywhere in this pipeline to run it against yet. **Action: only worth integrating if you add a document/media-evidence intake path to the evidence agent first — premature otherwise.** |
| **Varda** | AI agent for OSINT/identity investigation | ❌ Not merged, and not recommended even as a companion | Your source material itself says it couldn't independently verify a reliable public page for this tool. I can't verify it either. **Action: don't integrate an unverifiable tool into a system whose whole design point is auditability — this is exactly the "don't trust vendor claims" lesson your source material itself makes in the same document.** |
| **Verafin / Quantifind / FactSet** | Commercial/proprietary platforms | ❌ Not applicable | Not open source, nothing to clone. **Action: useful only as competitive/comparative reference points for what a commercial system looks like — not integration targets.** |
| **CAMS / FCIS certifications** | Professional AML certifications | ❌ Not applicable to code | These are training credentials, not software. **Action: genuinely useful for you personally if you want domain credibility to go with the project — not a code task.** |

---

## Part 3 — What was actually added and tested this turn

1. **`agents/qa_agent.py`** — post-hoc QA review of closed cases. Tested
   against 4 deliberately broken scenarios (all correctly flagged, right
   reasons each time) and against 5 *real* investigated cases from the
   live system (all correctly passed — confirms the existing pipeline was
   already producing well-documented cases). Wired into `main.py` at the
   new `/qa` route.
2. **`knowledge_base/sec_edgar_lookup.py`** — public company research
   tool. Parsing/error-handling tested via mocked responses (success,
   network failure, malformed response — all three handled correctly).
   **Not yet wired into `agents/evidence_agent.py`** — deliberately left
   as a standalone, tested function rather than auto-wiring it into the
   live evidence pipeline, since its live connectivity is unverified from
   this environment. Wire it in yourself once you've confirmed the live
   call works on your machine (see Part 4).

Full regression suite re-run after both additions — all 6 routes
(`/`, `/accuracy`, `/redteam`, `/screen`, `/qa`, `/api/health`) still
return 200, nothing existing broke.

---

## Part 4 — Your next steps, in order

1. **Test the SEC EDGAR call live.** After `docker compose up --build`,
   exec into the container or run locally:
   ```python
   from knowledge_base.sec_edgar_lookup import lookup_company_by_cik
   print(lookup_company_by_cik("320193"))  # Apple's CIK, a safe known-good test
   ```
   Replace the placeholder `USER_AGENT` string with your real contact
   info first — SEC EDGAR rejects requests without one.

2. **Wire it into the evidence agent, once confirmed working**, by
   adding a call to `lookup_company_by_cik()` inside
   `agents/evidence_agent.py::gather_evidence()` as a new evidence
   source — follow the same additive pattern used for every other
   detector in this project (append to the evidence sources list, don't
   replace anything).

3. **Add Bellingcat's toolkit URL to your evidence agent's documentation**
   or a research-resources file — not as code, as a reference link for
   whoever's doing manual OSINT alongside the automated pipeline.

4. **If you want DFIRe's case-management depth**, run it separately and
   treat this system's `/audit/{case_id}` endpoint as an export source
   feeding it — don't attempt to merge the codebases.

5. **Run `/qa` periodically** (same cadence as the red-team agent — this
   project's pattern is weekly/scheduled, not per-case) and route
   flagged cases to whoever does compliance QA at your organization or,
   for a student project, review them yourself as a sanity check before
   presenting results.

6. **Skip Varda entirely** unless you can independently verify what it
   actually is — this isn't overcaution, it's the exact discipline your
   own source material argues for.
