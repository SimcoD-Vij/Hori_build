# Integration & Operations Guide

This is the operational playbook for the phase after cloning: wiring PaySim/
AMLSim/followthemoney/OpenSanctions/EvolveGCN and your own calling agent
into this project, handling version mismatches, knowing what to download
when, diagnosing a detector that isn't firing, and safely extending the
system without breaking what's already verified working.

Read this alongside `README.md` (file-by-file methodology) and
`EVOLVEGCN_INTEGRATION.md` (temporal graph specifics) — this file is the
"connect everything together" layer on top of those two.

---

## Part 1 — The Master Integration Prompt

Paste this whole block into Claude Code (or any coding agent) with all
your cloned repos sitting as sibling directories to `fincrime-system/`.
It's written as one sequential prompt because integration order matters —
each step assumes the previous one is done and tested.

```
I have cloned the following repositories as sibling directories to my
fincrime-system project:
- ./PaySim (or ./AMLSim)
- ./followthemoney
- ./opensanctions
- ./my-calling-agent  (my own calling agent, replacing agents/calling_agent.py)
- [any others I've cloned]

Do this in order, testing after each step before moving to the next:

STEP 1 — Data adapter.
Read fincrime-system/README.md's "Schema Contract" section. Read the
actual column names in [PaySim's CSV / AMLSim's output format] by
inspecting the first 5 rows. Write a new file at
fincrime-system/data/ingestion/load_external.py that maps the external
schema into the internal shape (see Schema Contract). Do not modify any
existing detector file to accommodate the new schema -- the adapter's job
is to conform the DATA to the existing contract, not the other way
around. After writing it, run it against a small sample and print the
first 5 converted rows so I can visually confirm the mapping is correct
before running it against the full dataset.

STEP 2 — Swap the data source.
In fincrime-system/main.py's run_full_detection(), replace the call to
data.generate_synthetic_data.generate() with a call to your new adapter.
Keep the old synthetic generator call commented out directly above the
new one, not deleted -- I want to be able to switch back to the known-good
demo data for regression testing.
Run the full test suite (see Part 5 below) before proceeding.

STEP 3 — Entity resolution.
Read followthemoney's schema documentation (search for "Thing", "Person",
"Company" model types in their docs). Write
fincrime-system/knowledge_base/entity_resolution.py exposing one function:
resolve_entity(name: str, address: str = None) -> dict, returning a
normalized entity ID for fuzzy-duplicate names. Wire it into
agents/evidence_agent.py's gather_evidence() as a new evidence source
called "entity_resolution" -- ADD it, do not replace or remove any
existing evidence source.

STEP 4 — Sanctions screening.
Using opensanctions' data files or API (check their docs for whether you
have local data or need their hosted API), write
fincrime-system/knowledge_base/sanctions_screen.py exposing
screen_sanctions(name: str) -> dict returning {"hit": bool, "list": str
or None, "confidence": float}. Wire it into evidence_agent.py, replacing
the current sanctions_hit stub -- this one IS a replacement, not an
addition, since it's currently a hardcoded False.

STEP 5 — Calling agent swap.
Open fincrime-system/README.md's "Connecting Your Own Calling Agent"
section and confirm your cloned calling agent can implement the exact
function signature shown for run_calling_agent(). Write an adapter
wrapper in agents/calling_agent.py that calls into your cloned agent's
actual API/functions, but keep the PermissionError branch-safety check
at the TOP of the function, unchanged, before any call to your external
agent's code. Do not let your external calling agent's code run before
that check.

STEP 6 — Full regression test.
Run every test in Part 5 of INTEGRATION_AND_OPERATIONS_GUIDE.md and
report pass/fail for each. Do not report "done" until every existing
test that passed before these changes still passes.

After each step, show me the diff and the test output before continuing
to the next step.
```

---

## Part 2 — Version mismatches: what to do when they happen

`requirements.txt` in this project pins exact versions
(`fastapi==0.115.0`, etc.) deliberately — this is already the mitigation
for most version drift. When you add a cloned repo's own dependencies,
conflicts show up as pip resolver errors, not silent bugs, which is the
safer failure mode. When that happens:

```
I'm getting a pip dependency conflict between fincrime-system's
requirements.txt and [cloned repo]'s requirements. Here is the exact
error: [paste the full pip error].

Resolve this by:
1. Identifying which specific package versions conflict
2. Checking whether fincrime-system actually needs the pinned version
   (it was pinned for Docker build reliability, not for a specific
   feature) or whether it can be relaxed
3. Proposing the narrowest possible version range that satisfies both,
   rather than unpinning everything
4. If no compatible version exists, isolate the conflicting dependency
   into a separate Python virtual environment / separate Docker service
   that fincrime-system calls via a small HTTP wrapper, rather than
   forcing an incompatible install into the same environment
Show me the updated requirements.txt and explain what changed.
```

Common actual sources of conflict you'll likely hit: `numpy` version
(scikit-learn and any GNN library often want different major versions —
this is why `EVOLVEGCN_INTEGRATION.md` recommends a *separate* environment
for the torch/PyTorch Geometric stack rather than merging it into the main
`requirements.txt`), and `pandas` API changes between 1.x and 2.x if a
cloned repo is older code.

---

## Part 3 — What to download, at each stage

| Stage | Download | Why |
|---|---|---|
| Base system (already built) | Nothing beyond `requirements.txt` — `pip install` handles it | Rules/stats/graph/RandomForest/IsolationForest are all trained fresh from your own data, no pretrained checkpoint exists or is needed |
| Real transaction data | PaySim or AMLSim repo (data only, not a model) | Replaces the synthetic generator |
| Entity resolution | `followthemoney` repo (library + schema, not a trained model) | Fuzzy entity matching logic |
| Sanctions screening | `opensanctions` data export or API access | Reference data, not a model |
| LLM reasoning (triage/risk/explanation/verification) | Either nothing (template mode, already default) or an Ollama model (`ollama pull llama3.1:8b`) or an API key | See Part 4 for which situation needs which |
| Voice transcription (if building real calls) | `openai/whisper` from Hugging Face (`pip install openai-whisper`, model downloads automatically on first use) | Speech-to-text for the calling agent |
| Temporal graph (EvolveGCN) | `torch`, `torch-geometric`, `torch-geometric-temporal` — **no pretrained checkpoint**, you train it yourself | See Part 6 — there is no meaningful "pretrained fraud GNN" to download |

---

## Part 4 — Which model for which situation (consolidated)

This expands the table already in the build specification with the
summarization question specifically:

| Situation | Model needed | Notes |
|---|---|---|
| Rules, stats, graph, RandomForest, IsolationForest | None — trained from your own data at runtime | This is most of the detection layer |
| Triage classification | Small/local is fine — Ollama `llama3.1:8b` | Low-stakes classification task |
| Risk assessment (typology matching + reasoning) | Stronger model recommended — hosted API | Nuanced reasoning over evidence |
| **Case summarization** (turning a big evidence packet into a readable case file) | Same model as the explanation agent — **no separate summarization-specific model needed.** The explanation agent's job already IS summarization; a dedicated summarization model (e.g. `facebook/bart-large-cnn`) would only make sense if you needed to summarize very long free text (like a long call transcript or a long external news article) as a distinct sub-step before the explanation agent sees it | Only add a dedicated summarizer if evidence includes long unstructured documents (e.g. full adverse-media articles) — for structured evidence packets, the explanation agent's own reasoning is the summarization step |
| Verification (independent check) | Different model/endpoint than explanation agent, or same model with a fresh context | Independence matters more than raw capability here |
| Voice transcription | Whisper (Hugging Face, free, local) | Not an LLM — a dedicated speech model |
| Temporal graph detection | EvolveGCN via `torch-geometric-temporal`, trained on your data | Not a reasoning model — a graph neural network |

---

## Part 5 — Diagnostic prompt: when a method isn't detecting anything

Give this to a coding agent (or work through it yourself) whenever a
specific mathematical/typology/network/ML method isn't flagging something
you expected it to:

```
Detector [name the specific file/function, e.g.
detection/graph_analysis.py::find_cycles] is not flagging [describe the
pattern you expected it to catch] on this data: [describe or attach the
data].

Diagnose in this order, and show your work at each step -- do not skip
to "just lower the threshold" without doing steps 1-3 first:

1. SCHEMA CHECK: Print the actual column names and a few sample rows of
   the data being passed into this function. Confirm they match
   fincrime-system/README.md's Schema Contract exactly, including the
   sender/receiver direction convention.
2. ISOLATION CHECK: Call this specific function directly, in isolation,
   on a minimal hand-constructed example that SHOULD trigger it (e.g. for
   detect_structuring, construct exactly 3 deposits at $9,700 each).
   Confirm it fires on the minimal case before checking the real data.
3. THRESHOLD CHECK: If it fires on the minimal case but not the real
   data, print the actual computed values (the z-score, the degree
   centrality, the cycle length, whatever this detector computes) for
   the real data's closest-to-flagging case, and compare against the
   function's threshold parameter. Report the gap.
4. Only after 1-3: propose the specific parameter to adjust (name the
   exact parameter and file per fincrime-system/README.md's tuning
   table), and explain what the tradeoff is (this will also change the
   false-positive rate -- state by how much if you can estimate it).
Do not silently change the detection logic itself -- if the fix requires
a different algorithm entirely (not just a threshold), stop and tell me
that's what's needed rather than quietly rewriting the detector.
```

This diagnostic order matters because of a real bug found during this
project's own testing: an early version of the real-time screening layer
looked completely broken (wrong severity scores) when the actual problem
was Step 1 — account history was mixing incoming and outgoing
transactions into one baseline. Jumping straight to "lower the threshold"
would have hidden that bug rather than fixing it.

---

## Part 6 — Is there a model to download for prediction, or do we train?

**Train, not download — and this is the correct answer, not a limitation.**
There is no meaningful "pretrained fraud detection model" you can download
and point at your own bank's data, for the same reason there's no
pretrained model for "detect anomalies in your specific factory's sensor
data" — fraud patterns are specific to a given institution's customer
base, product mix, and transaction rails. A model trained on someone
else's data (even the Elliptic dataset) transfers weakly at best to your
own data's actual distribution.

This is exactly why the system already has a retraining loop built in
conceptually (documented in the platform build specification's champion/
challenger section) — the honest answer to "won't an old model miss new
crime patterns" is:

1. **RandomForest / IsolationForest** (already built): retrain on a
   schedule (weekly/monthly, or triggered by the drift detector) using
   `detection/ml_models.py::train_supervised` — this is cheap, CPU-only,
   and fast enough to retrain often.
2. **EvolveGCN** (if you build it per `EVOLVEGCN_INTEGRATION.md`): this
   is the component actually built for adapting to evolving patterns —
   its whole design point is modeling how the network changes over time,
   which is a more principled answer to "old model, new crime" than
   periodic retraining of a static model. But it still needs to be
   trained on YOUR data's time series, not downloaded pretrained.
3. **Unsupervised layer** (`IsolationForest`, already built): this is
   your safety net between retraining cycles — it needs no labels at all,
   so it catches novel patterns even before you've retrained the
   supervised model to recognize them by name.

The practical setup: unsupervised layer catches novelty continuously →
red-team agent probes for blind spots on a schedule → confirmed new
patterns get added to the typology catalog and used to retrain the
supervised model → EvolveGCN (once built) provides the deeper temporal
view that a periodically-retrained static model can't. None of this
requires downloading a model file from anywhere.

---

## Part 7 — Safe enhancement principles (don't break what's verified)

The pattern already used successfully in this project, worth repeating
deliberately for every future addition:

1. **New detectors are additive, never replace existing function
   signatures.** Every detector returns the same flag dict shape
   (`account_id`, `rule`, `evidence`, `transaction_ids`, `severity_hint`)
   — a new detector just appends to the same `flags` list; nothing
   downstream needs to know it exists.
2. **Feature-flag risky changes, exactly like `ENFORCEMENT_ENABLED`.**
   Any new capability that could have a real-world effect (not just
   detect-and-log) should ship default-off behind an environment
   variable, the same way enforcement does now.
3. **Test in isolation before testing integrated.** Every fix in this
   project's history was verified as a standalone function call before
   being verified through the full HTTP path — this is what caught the
   directional-history bug before it reached the live demo.
4. **Never touch a verified file to accommodate new data — write an
   adapter instead.** This is Step 1 of the master integration prompt
   above, stated as a general rule: conform new data to the existing
   contract, don't loosen the contract to fit new data.
5. **Run the full regression check after every change**, not just the
   new feature's own test:
```bash
python3 -c "
from fastapi.testclient import TestClient
import main
with TestClient(main.app) as client:
    for path in ['/', '/accuracy', '/redteam', '/screen', '/api/health']:
        r = client.get(path)
        assert r.status_code == 200, f'{path} failed: {r.status_code}'
        print(f'{path}: OK')
"
```

---

## Part 8 — Running the full evaluation suite

To exercise every agent and detector together (not individually) for an
end-to-end evaluation pass:

```
Run fincrime-system's full pipeline against [N] fresh synthetic datasets
with different random seeds (see the "Verified prediction accuracy"
section of README.md for the pattern to follow). For each dataset,
report: detection recall per pattern type (via detection/backtest.py),
false-positive rate, and the outcome of a full 7-agent investigation on
at least one account of each seeded pattern type (via
orchestrator.investigate_account()). Also run agents/redteam_agent.py's
probe once per evaluation pass. Summarize all of this as a single
evaluation report, and flag anything that regressed compared to the
numbers already recorded in README.md's "Verified prediction accuracy"
table.
```

This is the same methodology already used throughout this project's
development — nothing new to build, just run consistently as a check
whenever you integrate a new repo or model.
