# Integrating EvolveGCN (Temporal Graph Layer)

The current graph layer (`detection/graph_analysis.py`) treats the transaction
network as a single static snapshot. EvolveGCN's actual value is modeling how
the network *changes* block-by-block or day-by-day -- catching a ring that
looks clean today but shows a laundering-shaped evolution over a week. This
is a genuine upgrade over the NetworkX proxy, not a replacement for it --
keep the lightweight rules/graph layer for real-time flagging, and add
EvolveGCN as a periodic (e.g. nightly) deeper pass over recent history.

## Which repo to clone

Two real options, pick based on what you need:

**Option A — official paper reproduction (research fidelity):**
```
git clone https://github.com/IBM/EvolveGCN
```
This is the original AAAI 2020 authors' code, benchmarked directly on the
Elliptic dataset. Use this if you want to reproduce the paper's published
results as a baseline, or if your write-up needs to cite the exact original
implementation.

**Option B — PyTorch Geometric Temporal (practical integration, recommended for this project):**
```
pip install torch torch-geometric torch-geometric-temporal
```
This packages both EvolveGCN-H and EvolveGCN-O as importable PyTorch Geometric
layers (`from torch_geometric_temporal.nn.recurrent import EvolveGCNH, EvolveGCNO`)
rather than a standalone research codebase — far less integration work to wire
into `detection/graph_analysis.py`, and it's what the "implementations of
EvolveGCN integrated with PyTorch Geometric" reference points to. Use this
one for the actual build.

## Data connection

```
# Elliptic dataset (Bitcoin transactions, licit/illicit labels)
git clone https://github.com/Rufaim/EvolveGCN elliptic-evolvegcn-example
# or download the raw CSVs directly from Kaggle: search "elliptic data set"
```

The Elliptic dataset comes pre-split into 49 discrete time steps -- this
structure is exactly what EvolveGCN needs (a sequence of graph snapshots),
and exactly what our own transaction data lacks by default. To adapt your
own pipeline's data to this shape:

```python
# detection/temporal_graph.py (new file to add)
import pandas as pd

def build_temporal_snapshots(transactions: pd.DataFrame, window="1D"):
    """Split transactions into a sequence of graph snapshots, one per time window --
    the input shape EvolveGCN expects, instead of the single static graph
    detection/graph_analysis.py builds."""
    transactions = transactions.copy()
    transactions["timestamp"] = pd.to_datetime(transactions["timestamp"])
    snapshots = []
    for _, group in transactions.groupby(pd.Grouper(key="timestamp", freq=window)):
        if len(group) > 0:
            snapshots.append(group)
    return snapshots  # feed this sequence into the EvolveGCN training loop
```

## Model prerequisites

- `torch` (CPU build is fine to start; GPU strongly recommended for real training runs -- this is the one component in the whole system where CPU-only will genuinely struggle at scale, unlike the NetworkX proxy layer)
- `torch-geometric` and `torch-geometric-temporal`
- No API key needed -- this is a trained-from-scratch model, not an LLM call

## Where it plugs into the orchestrator

Add it as a new detection source alongside the existing ones in `main.py`'s
`run_full_detection()`:

```python
from detection.temporal_graph import build_temporal_snapshots
# from your_evolvegcn_module import run_evolvegcn_inference  # after you build/train it

snapshots = build_temporal_snapshots(txn_df)
# temporal_flags = run_evolvegcn_inference(snapshots)
# flags += temporal_flags
```

Its output should follow the same flag dict shape every other detector uses
(`account_id`, `rule`, `evidence`, `transaction_ids`, `severity_hint`) so it
drops into the existing evidence/risk-assessment/explanation pipeline without
any agent code changing -- this is exactly why every detector in this project
was built against that shared shape.

## Honest scoping note

Training a GNN from scratch is a real time investment -- expect this to be
a distinct, later phase of the roadmap, not a first-week addition. Get the
rest of the pipeline (rules through the 7 agents) fully working on the
lightweight graph layer first; add EvolveGCN once you have real historical
data with enough time steps to make a temporal model worth the added
complexity. On the small synthetic dataset this project ships with, there
usually isn't enough time-series depth for EvolveGCN to show a real
advantage over the static graph layer -- it earns its place on larger,
longer-horizon data.
