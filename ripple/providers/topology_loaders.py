"""Concrete TopologyProvider implementations — file loading and synthetic generation.

Requires ``networkx`` (optional dependency).  If NetworkX is not installed,
``is_available()`` returns ``False`` and ``get_topology()`` returns ``None``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict

from .topology import TopologyData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NetworkX optional import
# ---------------------------------------------------------------------------

try:
    import networkx as nx

    _HAS_NETWORKX = True
except ImportError:
    nx = None  # type: ignore[assignment]
    _HAS_NETWORKX = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nx_to_topology_data(G: Any, *, default_type: str = "sea") -> TopologyData:
    """Convert a NetworkX graph to Ripple ``TopologyData`` format.

    - ``node_link_data()`` produces ``{"nodes": [...], "edges"|"links": [...]}``
    - Ripple expects ``"edges"`` key
    - Ensures every node has ``type`` and every edge has ``weight``
    - Converts integer node IDs to ``agent_N`` strings
    """
    data = nx.node_link_data(G)

    # NetworkX <3.4 used "links"; >=3.4 uses "edges"
    edge_list = data.get("edges", data.get("links", []))

    id_map: Dict[Any, str] = {}
    for node in data["nodes"]:
        raw_id = node["id"]
        mapped = str(raw_id) if isinstance(raw_id, str) else f"agent_{raw_id}"
        id_map[raw_id] = mapped
        node["id"] = mapped
        node.setdefault("type", default_type)

    for edge in edge_list:
        edge["source"] = id_map.get(edge["source"], str(edge["source"]))
        edge["target"] = id_map.get(edge["target"], str(edge["target"]))
        edge.setdefault("weight", 1.0)

    return TopologyData({"nodes": data["nodes"], "edges": edge_list})


def _detect_format(path: Path) -> str:
    """Infer file format from extension."""
    ext = path.suffix.lower()
    mapping = {
        ".txt": "snap",
        ".edgelist": "snap",
        ".json": "json",
        ".graphml": "graphml",
        ".graphmlz": "graphml",
        ".csv": "csv",
        ".tsv": "csv",
        ".gml": "gml",
    }
    return mapping.get(ext, "snap")


def _load_graph(path: Path, fmt: str, **kwargs: Any) -> Any:
    """Load a NetworkX graph from file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Topology file not found: {path}")

    if fmt == "snap":
        G = nx.read_edgelist(
            str(path),
            create_using=nx.DiGraph,
            nodetype=int,
            data=False,
        )
    elif fmt == "json":
        with open(path) as f:
            raw = json.load(f)
        # NetworkX node_link_graph expects either "edges" (>=3.4) or "links" (<3.4).
        # Ripple JSON uses "edges"; if only "links" present, rename for compatibility.
        if "edges" not in raw and "links" in raw:
            raw["edges"] = raw.pop("links")
        G = nx.node_link_graph(raw, directed=True)
    elif fmt == "graphml":
        G = nx.read_graphml(str(path))
        if not G.is_directed():
            G = G.to_directed()
    elif fmt == "csv":
        import csv

        G = nx.DiGraph()
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with open(path, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            for row in reader:
                if not row or row[0].startswith("#"):
                    continue
                # Skip header row if it contains non-numeric source
                if len(row) >= 2:
                    src, tgt = row[0].strip(), row[1].strip()
                    try:
                        weight = float(row[2]) if len(row) >= 3 else 1.0
                    except (ValueError, IndexError):
                        # Header row like "source,target,weight" — skip
                        continue
                    G.add_edge(src, tgt, weight=weight)
    elif fmt == "gml":
        G = nx.read_gml(str(path))
        if not G.is_directed():
            G = G.to_directed()
    else:
        raise ValueError(f"Unknown topology format: {fmt!r}")

    return G


# ---------------------------------------------------------------------------
# FileTopologyProvider
# ---------------------------------------------------------------------------


class FileTopologyProvider:
    """Load topology from a file (SNAP / JSON / GraphML / CSV / GML).

    Parameters
    ----------
    path : str or Path
        Path to the topology file.
    format : str
        File format: ``"snap"``, ``"json"``, ``"graphml"``, ``"csv"``, ``"gml"``,
        or ``"auto"`` (infer from extension).
    default_type : str
        Default ``type`` for nodes that lack one (``"star"`` or ``"sea"``).
    node_type_map : dict, optional
        Mapping ``{node_id: "star"|"sea"}`` to override default type assignment.
    """

    def __init__(
        self,
        path: str | Path,
        format: str = "auto",
        default_type: str = "sea",
        node_type_map: Dict[str, str] | None = None,
    ) -> None:
        self._path = Path(path)
        self._format = format
        self._default_type = default_type
        self._node_type_map = node_type_map or {}
        self._cache: TopologyData | None = None

    @property
    def name(self) -> str:
        return f"file-topology({self._path.name})"

    def is_available(self) -> bool:
        return _HAS_NETWORKX and self._path.exists()

    async def health_check(self) -> bool:
        return self.is_available()

    async def get_topology(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        constraints: Dict[str, Any] | None = None,
    ) -> TopologyData | None:
        if not _HAS_NETWORKX:
            logger.warning("NetworkX not installed — FileTopologyProvider unavailable")
            return None

        if self._cache is not None:
            return self._cache

        fmt = self._format
        if fmt == "auto":
            fmt = _detect_format(self._path)

        try:
            G = await asyncio.to_thread(_load_graph, self._path, fmt)
            data = _nx_to_topology_data(G, default_type=self._default_type)
        except Exception as exc:
            logger.warning("FileTopologyProvider failed to load %s: %s", self._path, exc)
            return None

        # Apply node_type_map overrides
        if self._node_type_map:
            for node in data["nodes"]:
                if node["id"] in self._node_type_map:
                    node["type"] = self._node_type_map[node["id"]]

        self._cache = data
        return data


# ---------------------------------------------------------------------------
# SyntheticTopologyProvider
# ---------------------------------------------------------------------------

_SYNTHETIC_MODELS = {"ba", "ws", "sbm", "er"}


class SyntheticTopologyProvider:
    """Generate synthetic topology using NetworkX graph models.

    Parameters
    ----------
    model : str
        Graph model: ``"ba"`` (Barabasi-Albert), ``"ws"`` (Watts-Strogatz),
        ``"sbm"`` (Stochastic Block Model), ``"er"`` (Erdos-Renyi).
    n : int
        Number of nodes.
    seed : int, optional
        Random seed for reproducibility.
    **model_kwargs
        Model-specific parameters:

        - BA: ``m=2`` (edges per new node)
        - WS: ``k=4``, ``p=0.3`` (neighbors, rewiring prob)
        - SBM: ``sizes=[25, 25]``, ``p=[[0.3,0.02],[0.02,0.3]]``
        - ER: ``p=0.1`` (edge probability)
    """

    def __init__(
        self,
        model: str = "ba",
        n: int = 50,
        seed: int | None = None,
        **model_kwargs: Any,
    ) -> None:
        if model not in _SYNTHETIC_MODELS:
            raise ValueError(f"Unknown model {model!r}; choose from {sorted(_SYNTHETIC_MODELS)}")
        self._model = model
        self._n = n
        self._seed = seed
        self._model_kwargs = model_kwargs
        self._cache: TopologyData | None = None

    @property
    def name(self) -> str:
        return f"synthetic-topology({self._model}, n={self._n})"

    def is_available(self) -> bool:
        return _HAS_NETWORKX

    async def health_check(self) -> bool:
        return _HAS_NETWORKX

    async def get_topology(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        constraints: Dict[str, Any] | None = None,
    ) -> TopologyData | None:
        if not _HAS_NETWORKX:
            logger.warning("NetworkX not installed — SyntheticTopologyProvider unavailable")
            return None

        if self._cache is not None:
            return self._cache

        try:
            G = await asyncio.to_thread(self._generate)
            data = _nx_to_topology_data(G)
            self._cache = data
            return data
        except Exception as exc:
            logger.warning("SyntheticTopologyProvider failed: %s", exc)
            return None

    def _generate(self) -> Any:
        """Generate a NetworkX graph (sync — called via to_thread)."""
        n = self._n
        seed = self._seed
        kw = self._model_kwargs

        if self._model == "ba":
            m = kw.get("m", 2)
            G = nx.barabasi_albert_graph(n, m, seed=seed)
        elif self._model == "ws":
            k = kw.get("k", 4)
            p = kw.get("p", 0.3)
            G = nx.watts_strogatz_graph(n, k, p, seed=seed)
        elif self._model == "sbm":
            sizes = kw.get("sizes", [n // 2, n - n // 2])
            p = kw.get("p", [[0.3, 0.02], [0.02, 0.3]])
            G = nx.stochastic_block_model(sizes, p, seed=seed)
        elif self._model == "er":
            p = kw.get("p", 0.1)
            G = nx.erdos_renyi_graph(n, p, seed=seed)
        else:
            raise ValueError(f"Unknown model: {self._model!r}")

        # Ensure directed (social networks are typically directed)
        if not G.is_directed():
            G = G.to_directed()

        # Assign node types based on degree distribution
        degrees = dict(G.degree())
        if degrees:
            threshold = sorted(degrees.values())[int(len(degrees) * 0.9)]
            for node_id, deg in degrees.items():
                node_type = "star" if deg >= threshold else "sea"
                G.nodes[node_id]["type"] = node_type

        # Normalize edge weights
        for u, v in G.edges():
            G.edges[u, v].setdefault("weight", 1.0)

        return G
