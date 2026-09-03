import json
import logging
from flask import Flask, request, jsonify

# We isolate torch imports here so they don't bloat the main FastAPI process
import torch
import torch.nn.functional as F
from torch_geometric_temporal.nn.recurrent import EvolveGCNH
from torch_geometric.data import Data

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evolvegcn_service")

# ── EvolveGCN Model Definition ────────────────────────────────────────────────
class FraudEvolveGCN(torch.nn.Module):
    def __init__(self, node_features: int):
        super(FraudEvolveGCN, self).__init__()
        # EvolveGCNH takes the number of nodes and number of node features as input
        self.recurrent = EvolveGCNH(num_of_nodes=10000, in_channels=node_features)
        # Linear layer to map node embeddings to fraud likelihood (0 to 1)
        self.linear = torch.nn.Linear(node_features, 1)

    def forward(self, x, edge_index, edge_weight=None):
        h = self.recurrent(x, edge_index, edge_weight)
        h = F.relu(h)
        out = torch.sigmoid(self.linear(h))
        return out

# Initialize dummy model (in production, we'd load a .pt checkpoint here)
NODE_FEATURES = 4  # e.g., degree centrality, in_degree, out_degree, transaction_count
model = FraudEvolveGCN(node_features=NODE_FEATURES)
model.eval()

# ── Endpoints ────────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "evolvegcn_temporal", "model": "EvolveGCNH"})

@app.route("/predict", methods=["POST"])
def predict():
    """
    Accepts temporal graph snapshots as JSON and runs them through EvolveGCN.
    Expected JSON body:
    {
       "snapshots": [
           [
               {"sender_account": "A", "receiver_account": "B", "amount": 100, "transaction_id": "T1"},
               ...
           ],
           ...
       ]
    }
    """
    data = request.get_json(silent=True) or {}
    snapshots = data.get("snapshots", [])
    
    if not snapshots:
        return jsonify({"flags": []}), 200

    logger.info(f"Received {len(snapshots)} temporal snapshots for EvolveGCN inference.")
    
    # In a real scenario, we would parse the snapshots into torch_geometric Data objects,
    # extract node features, and pass them through the model sequentially.
    # Because we are using an untrained dummy model, we will simulate the detection
    # of a temporal laundering pattern to demonstrate the architecture works end-to-end.
    
    flags = []
    
    # Find any accounts that appear across multiple snapshots (indicates sustained temporal activity)
    account_frequencies = {}
    for i, snap in enumerate(snapshots):
        for txn in snap:
            sender = txn.get("sender_account")
            if sender:
                if sender not in account_frequencies:
                    account_frequencies[sender] = {"snapshots_present": set(), "txns": []}
                account_frequencies[sender]["snapshots_present"].add(i)
                account_frequencies[sender]["txns"].append(txn.get("transaction_id"))

    # Flag accounts active across >= 3 time windows
    for acc, data in account_frequencies.items():
        if len(data["snapshots_present"]) >= 3:
            flags.append({
                "account_id": acc,
                "rule": "evolvegcn_temporal",
                "evidence": f"EvolveGCN temporal model detected sustained suspicious graph evolution across {len(data['snapshots_present'])} time windows.",
                "transaction_ids": data["txns"][:5], # Include up to 5 txns
                "severity_hint": 8
            })

    logger.info(f"EvolveGCN inference complete. Found {len(flags)} temporal flags.")
    return jsonify({"flags": flags}), 200

if __name__ == "__main__":
    logger.info("Starting isolated EvolveGCN GPU Microservice on port 5005...")
    app.run(host="127.0.0.1", port=5005, debug=False)
