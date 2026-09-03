"""
Graph layer: the network-level 'string board' patterns, formalized.
Lightweight NetworkX proxy (not a full GNN) -- matches the published
precedent discussed for CPU-only prototypes; upgrade to PyTorch Geometric
+ a real graph database later per the build spec's production section.
"""
import networkx as nx
import pandas as pd
try:
    import community as community_louvain
    HAVE_LOUVAIN = True
except ImportError:
    HAVE_LOUVAIN = False


def build_transaction_graph(transactions: pd.DataFrame) -> nx.DiGraph:
    G = nx.DiGraph()
    for _, row in transactions.iterrows():
        if row["sender_account"] in ("CASH",) or row["receiver_account"] in ("EXTERNAL",):
            continue
        G.add_edge(row["sender_account"], row["receiver_account"], amount=row["amount"], txn_id=row["transaction_id"])
    return G


def build_identity_graph(accounts: pd.DataFrame) -> nx.Graph:
    """Link accounts sharing device, phone, or address -- catches synthetic-identity infrastructure."""
    G = nx.Graph()
    for _, acc in accounts.iterrows():
        G.add_node(acc["account_id"])
    for key in ("device_id", "phone", "address"):
        groups = accounts.groupby(key)["account_id"].apply(list)
        for _, members in groups.items():
            if len(members) > 1:
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        G.add_edge(members[i], members[j], shared=key)
    return G


def find_hub_accounts(G: nx.DiGraph, min_in_degree=5, min_out_degree=5, min_amount_per_edge=20000):
    """
    Fan-in/fan-out mule hub detection. Filtered to material-value edges first --
    otherwise routine small P2P activity in a normal population creates noise.
    """
    material_G = nx.DiGraph()
    for u, v, data in G.edges(data=True):
        if data.get("amount", 0) >= min_amount_per_edge:
            material_G.add_edge(u, v, **data)

    flags = []
    for node in material_G.nodes:
        in_deg, out_deg = material_G.in_degree(node), material_G.out_degree(node)
        if in_deg >= min_in_degree and out_deg >= min_out_degree:
            flags.append({
                "account_id": node, "rule": "graph_fan_hub",
                "evidence": f"Receives material-value funds from {in_deg} and sends to {out_deg} distinct "
                            f"accounts (edges >= {min_amount_per_edge})",
                "transaction_ids": [], "severity_hint": 7,
            })
    return flags


def find_cycles(G: nx.DiGraph, max_len=5, min_amount_per_edge=20000):
    """
    Round-tripping detection. Restricted to material-value edges and short cycles --
    a dense transaction graph of routine payments produces thousands of spurious
    small cycles otherwise, which would drown out the real signal.
    """
    material_G = nx.DiGraph()
    for u, v, data in G.edges(data=True):
        if data.get("amount", 0) >= min_amount_per_edge:
            material_G.add_edge(u, v, **data)

    flags = []
    seen_cycles = set()
    try:
        cycles = list(nx.simple_cycles(material_G, length_bound=max_len))
    except TypeError:
        cycles = [c for c in nx.simple_cycles(material_G) if len(c) <= max_len]
    for cyc in cycles:
        key = tuple(sorted(cyc))
        if key in seen_cycles:
            continue
        seen_cycles.add(key)
        for node in cyc:
            flags.append({
                "account_id": node, "rule": "graph_round_trip",
                "evidence": f"Part of a {len(cyc)}-account circular fund flow (material-value edges only): "
                            f"{' -> '.join(cyc)} -> {cyc[0]}",
                "transaction_ids": [], "severity_hint": 8,
            })
    return flags


def find_synthetic_identity_rings(identity_G: nx.Graph, accounts: pd.DataFrame, min_size=3, days_window=14):
    """Community detection + coordinated-opening-date check."""
    flags = []
    if identity_G.number_of_edges() == 0:
        return flags
    opened = accounts.set_index("account_id")["opened_date"].to_dict()
    for component in nx.connected_components(identity_G):
        if len(component) < min_size:
            continue
        dates = pd.to_datetime([opened[a] for a in component if a in opened])
        if len(dates) == 0:
            continue
        spread_days = (dates.max() - dates.min()).days
        if spread_days <= days_window:
            for acct in component:
                flags.append({
                    "account_id": acct, "rule": "synthetic_identity_ring",
                    "evidence": f"One of {len(component)} accounts sharing device/phone/address, all opened "
                                f"within a {spread_days}-day window -- coordinated-creation signature",
                    "transaction_ids": [], "severity_hint": 8,
                })
    return flags


def run_graph_detection(accounts: pd.DataFrame, transactions: pd.DataFrame):
    txn_G = build_transaction_graph(transactions)
    id_G = build_identity_graph(accounts)
    flags = []
    flags += find_hub_accounts(txn_G)
    flags += find_cycles(txn_G)
    flags += find_synthetic_identity_rings(id_G, accounts)
    return flags
