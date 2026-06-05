# Research: Topology Data Sources for Social Network Simulation

- **Query**: Public datasets and data sources for social network topology / graph data to feed Ripple simulation engine
- **Scope**: External (public datasets, file formats, Python libraries, APIs)
- **Date**: 2026-06-05

---

## Executive Summary

This research identifies viable data sources for loading social network topology into Ripple's `TopologyProvider`. The key findings are:

1. **SNAP (Stanford)** is the primary source for real social network datasets - edge list format, free, no auth required
2. **NetworkX** is the recommended Python library - BSD-3 license, `node_link_data()` produces Ripple-compatible format
3. **Bluesky/Mastodon APIs** are the best free options for real-time topology extraction
4. **Twitter/X API** is effectively unusable due to cost and rate limits (Academic Research track discontinued)

---

## 1. Public Graph Datasets

### 1.1 SNAP (Stanford Large Network Dataset Collection)

**URL**: https://snap.stanford.edu/data/

**Status**: FREE, no authentication, direct download

**Format**: Edge list (text file, one edge per line, gzipped)

| Dataset | Description | Size | Type |
|---------|-------------|------|------|
| `soc-Epinions1` | Epinions social network | 75,879 nodes, 405,740 edges | Directed |
| `soc-Slashdot0811` | Slashdot social network | 77,360 nodes, 469,180 edges | Directed |
| `soc-Pokec` | Pokec (Slovakia) | 1.6M nodes, 22.2M edges | Directed |
| `soc-LiveJournal1` | LiveJournal | 4.8M nodes, 42.8M edges | Directed |
| `ego-Twitter` | Twitter ego networks | 81,306 nodes, 1.3M edges | Directed |
| `ego-Facebook` | Facebook ego networks | 4,039 nodes, 88,234 edges | Undirected |
| `wiki-Vote` | Wikipedia vote network | 7,115 nodes, 100,762 edges | Directed |
| `wiki-Talk` | Wikipedia talk network | 2.4M nodes, 4.7M edges | Directed |
| `email-Eu-core` | Email network | 1,005 nodes, 25,571 edges | Directed |

**Download Pattern**: `https://snap.stanford.edu/data/{name}.txt.gz`

**File Format Example**:
```
# Directed graph (each unordered pair of nodes is saved once)
# soc-Epinions1
# Nodes: 75879 Edges: 405740
# FromNodeId    ToNodeId
0 1
0 2
...
```

**Pros**:
- Free, no registration
- Well-documented with size/type metadata
- Standard edge list format
- Includes both directed and undirected graphs
- Some datasets have temporal columns (unix timestamp)

**Cons**:
- Node IDs are integers (need mapping to agent IDs)
- No node metadata (follower counts, etc.)
- Some datasets are dated (2008-2012 era)

**Ripple Relevance**: HIGH - Primary source for realistic social graph topology

---

### 1.2 Network Repository

**URL**: http://networkrepository.com/

**Status**: FREE, no authentication, direct download

**Format**: MTX (Matrix Market), Edge List, GraphML

**Categories**:
- Social Networks (50+ networks)
- Facebook Networks (100+ ego/institutional)
- Twitter Networks (retweet/follower/mention)
- Reddit Networks (thread/user interaction)
- Collaboration Networks (DBLP, ACM)
- Trust Networks (Advogato, PGP)

**Pros**:
- Interactive explorer with visualization
- Rich metadata (degree distribution, clustering coefficient)
- Multiple format options per dataset
- Size range from 10s to millions of nodes

**Cons**:
- Website can be slow
- MTX format is 1-indexed (confusion risk)
- Less standardized than SNAP

**Ripple Relevance**: HIGH - Complements SNAP with different networks and formats

---

### 1.3 KONECT (Koblenz Network Collection)

**URL**: http://konect.cc/

**Status**: FREE, no authentication

**Format**:
- `out.{name}`: Edge list (tab-separated, 1-indexed)
- `out.{name}.mtx`: Matrix Market format
- `meta.{name}`: Metadata (node count, edge count, type)

**Notable Datasets**: Facebook friends, Twitter follows, Slashdot, Epinions

**Pros**:
- Comprehensive multi-category collection
- Metadata files with graph statistics
- Standardized file naming

**Cons**:
- 1-indexed node IDs
- Website occasionally down

**Ripple Relevance**: MEDIUM-HIGH - Good backup source for SNAP

---

### 1.4 Other Curated Sources

| Source | URL | Format | Relevance |
|--------|-----|--------|-----------|
| ICON | https://icon.colorado.edu/ | Links to original sources | MEDIUM - Index of 4000+ networks |
| OGB | https://ogb.stanford.edu/ | Custom (ogb package) | MEDIUM - ML benchmark datasets |
| Netzschleuder | https://networks.skewed.de/ | CSV, GraphML, GML | MEDIUM - 2000+ networks |
| ASU Social Computing | http://socialcomputing.asu.edu/ | Edge list + features | MEDIUM - BlogCatalog, Flickr, YouTube |
| Hugging Face | https://huggingface.co/datasets | Arrow, JSON, CSV | MEDIUM - Growing collection |
| Gephi Wiki | https://github.com/gephi/gephi/wiki/Datasets | GEXF, Pajek | LOW - Small sample datasets |

---

## 2. File Formats

### 2.1 Format Comparison

| Format | Extension | Pros | Cons | Ripple Compatibility |
|--------|-----------|------|------|---------------------|
| **Edge List** | `.txt`, `.edgelist` | Simplest, universal, compact | No metadata | HIGH - Easy to parse |
| **Weighted Edge List** | `.txt` | Adds weight | No rich metadata | HIGH - Matches TopologyEdge |
| **CSV Edge List** | `.csv`, `.tsv` | Self-documenting, Pandas-friendly | Slightly larger | HIGH - Easy with pandas |
| **JSON (node-link)** | `.json` | Web-friendly, matches TopologyData | No standard schema | **NATIVE** - Direct match |
| **GraphML** | `.graphml` | Rich attributes, tool interoperability | Verbose XML | MEDIUM - Need conversion |
| **GML** | `.gml` | Human-readable, attributes | Parsing inconsistencies | MEDIUM - Need conversion |
| **Matrix Market** | `.mtx` | Sparse matrix standard | 1-indexed, no graph semantics | LOW - Overkill |
| **Pajek** | `.net` | Rich SNA metadata | Tool-specific | LOW - Niche |

### 2.2 Ripple TopologyData Format

```json
{
  "nodes": [
    {"id": "agent_1", "type": "star", "follower_count": 1000},
    {"id": "agent_2", "type": "sea", "follower_count": 50}
  ],
  "edges": [
    {"source": "agent_1", "target": "agent_2", "weight": 0.8}
  ]
}
```

**Required Keys**:
- `nodes`: List of `TopologyNode` dicts with required `id` and `type`
- `edges`: List of `TopologyEdge` dicts with required `source`, `target`, `weight`

**Source**: `/home/admin/Ripple/ripple/providers/topology.py:25-29`

---

## 3. Python Libraries

### 3.1 NetworkX (RECOMMENDED)

**Install**: `pip install networkx`

**License**: BSD-3 (permissive, commercial-friendly)

**Version**: 3.x (latest stable)

**Key Features for Ripple**:

1. **Format Support**: edgelist, GraphML, GML, GEXF, Pajek, JSON (node-link), adjacency
2. **Graph Generators**: Barabasi-Albert, Watts-Strogatz, Erdos-Renyi, SBM, etc.
3. **JSON Serialization**: `node_link_data()` / `node_link_graph()` - **direct TopologyData match**
4. **Pandas Integration**: `from_pandas_edgelist()` for CSV loading
5. **Pure Python**: No C dependencies, easy install

**NetworkX to Ripple Conversion**:
```python
import networkx as nx

# Load from any format
G = nx.read_edgelist("snap_dataset.txt", create_using=nx.DiGraph)
# Or: G = nx.read_graphml("graph.graphml")
# Or: G = nx.from_pandas_edgelist(df, source="src", target="dst")

# Convert to Ripple TopologyData
data = nx.node_link_data(G)
topology_data = {
    "nodes": data["nodes"],      # Direct match
    "edges": data["links"],      # Key rename: links -> edges
}
```

**Graph Generators for Synthetic Topologies**:

| Generator | Description | Use Case |
|-----------|-------------|----------|
| `barabasi_albert_graph(n, m)` | Scale-free (preferential attachment) | Models social network growth |
| `watts_strogatz_graph(n, k, p)` | Small-world with clustering | Local communities + shortcuts |
| `powerlaw_cluster_graph(n, m, p)` | Scale-free + high clustering | Realistic social structure |
| `stochastic_block_model(sizes, p)` | Community-structured | Explicit community control |
| `erdos_renyi_graph(n, p)` | Random baseline | Control experiments |

**Pros**:
- Pure Python - no compilation
- Extensive algorithm library
- Excellent documentation
- Large community
- BSD-3 license (commercial OK)
- `node_link_data()` produces Ripple-compatible format

**Cons**:
- Slow for large graphs (>1M nodes)
- High memory usage
- Not suitable for production-scale graph DB

**Ripple Relevance**: HIGH - Best choice for topology loading and synthetic generation

---

### 3.2 python-igraph

**Install**: `pip install python-igraph`

**License**: GPL-2+ (may be restrictive for commercial use)

**Key Features**:
- C core - 10-100x faster than NetworkX for large graphs
- Fast community detection (Louvain, Walktrap)
- Format support: GraphML, GML, NCOL, Pajek, Edge list

**Pros**:
- Fast C core
- Efficient memory
- Good community detection

**Cons**:
- GPL license may be restrictive
- C compilation needed
- API less Pythonic
- Smaller Python community

**Ripple Relevance**: MEDIUM - Good for loading large SNAP datasets, but license is a concern

---

### 3.3 graph-tool

**Install**: `pip install graph-tool` (or conda)

**License**: GPL-3

**Key Features**:
- C++ core, very fast
- Stochastic Block Model (SBM) for realistic graph generation
- Statistical inference for community detection

**Pros**:
- Very fast
- Advanced statistical methods
- SBM can generate realistic social graphs

**Cons**:
- GPL-3 license
- Hard to install (C++ compilation)
- Complex API
- Small community

**Ripple Relevance**: LOW - Overkill for topology loading, license/install barriers

---

### 3.4 Library Recommendation

| Library | License | Speed | Ease of Use | Ripple Fit |
|---------|---------|-------|-------------|------------|
| **NetworkX** | BSD-3 | Medium | HIGH | HIGH (recommended) |
| python-igraph | GPL-2+ | Fast | Medium | MEDIUM |
| graph-tool | GPL-3 | Very Fast | Low | LOW |
| cuGraph | Apache-2.0 | Very Fast (GPU) | Low | LOW |

**Recommendation**: Use NetworkX as the primary library for topology loading. It's BSD-3 licensed, pure Python (easy install), and `node_link_data()` produces the exact format Ripple expects.

---

## 4. API-Based Topology Sources

### 4.1 Twitter/X API (v2)

**Status**: PAID - Free tier extremely limited (1,500 tweets/month read)

**URL**: https://developer.twitter.com/en/docs/twitter-api

**Relevant Endpoints**:
- `GET /2/users/:id/followers` - Get followers
- `GET /2/users/:id/following` - Get following
- `GET /2/tweets/:id/retweets` - Get retweets

**Rate Limits**:
- Free: 1,500 tweets/month
- Basic: 10,000 tweets/month ($100/mo)
- Pro: 1M tweets/month ($5,000/mo)

**Auth**: OAuth 2.0 Bearer Token

**Critical Note**: Academic Research track was **discontinued in 2023**. Previously, researchers could access full Twitter graph for free. This is no longer available.

**Pros**:
- Real social graph data
- Rich metadata (verified, follower counts)
- Streaming API for real-time

**Cons**:
- Very expensive for meaningful graph extraction
- Rate limits severely restrict graph traversal
- Graph construction requires iterative BFS/DFS calls
- Terms of Service restrict data sharing
- Academic Research track discontinued

**Ripple Relevance**: LOW - Cost and rate limits make large-scale topology extraction impractical. Only viable for small ego-network samples.

---

### 4.2 Mastodon API

**Status**: FREE - Public APIs on each instance

**URL**: https://docs.joinmastodon.org/methods/

**Relevant Endpoints**:
- `GET /api/v1/accounts/:id/followers` - Get followers
- `GET /api/v1/accounts/:id/following` - Get following
- `GET /api/v1/timelines/public` - Public timeline

**Rate Limits**: Instance-specific, typically 300 requests/5min

**Auth**: None for public endpoints; OAuth for user endpoints

**Pros**:
- Free and open
- Federated - multiple instances
- Public data by default
- No approval process needed
- Active developer community

**Cons**:
- Smaller user base than Twitter
- Federation means incomplete graph view
- Rate limits per instance
- Must query each instance separately
- No global search across instances

**Ripple Relevance**: MEDIUM - Best free option for real social graph data. Federation complicates full topology extraction but individual instance graphs are accessible. Good for proof-of-concept.

---

### 4.3 Bluesky API (AT Protocol)

**Status**: FREE - Open protocol

**URL**: https://atproto.com/

**Relevant Endpoints**:
- `app.bsky.actor.getProfile` - Get user profile
- `app.bsky.graph.getFollowers` - Get followers
- `app.bsky.graph.getFollows` - Get follows
- `com.atproto.sync.getRepo` - Get full user data repo

**Rate Limits**: Relatively generous; varies by method

**Auth**: None for public data; JWT for authenticated requests

**Pros**:
- Open protocol and data model
- Federated design (like email)
- Public data by default
- Growing user base
- Active developer tools
- No approval process
- Graph endpoints explicitly available

**Cons**:
- Smaller user base
- Federation complicates complete graph
- API still evolving
- Less historical data

**Ripple Relevance**: MEDIUM-HIGH - Best emerging free option. AT Protocol is designed for data portability. Graph endpoints are explicitly available. Growing community and tooling.

---

### 4.4 Reddit API

**Status**: FREE with rate limits (OAuth required)

**URL**: https://www.reddit.com/dev/api/

**Rate Limits**: 60 requests/min (OAuth)

**Note**: Reddit provides **interaction graphs** (who-replies-to-whom) rather than explicit follower graphs. Useful for information propagation simulation but requires graph construction from interactions.

**Ripple Relevance**: LOW-MEDIUM - Interaction graphs rather than social graphs. Requires post-processing to construct topology.

---

### 4.5 API Comparison

| API | Cost | Rate Limits | Graph Type | Ripple Relevance |
|-----|------|-------------|------------|------------------|
| Twitter/X | PAID ($5K/mo for 1M) | Severe | Follower graph | LOW |
| **Mastodon** | FREE | 300/5min per instance | Follower graph | MEDIUM |
| **Bluesky** | FREE | Generous | Follower graph | MEDIUM-HIGH |
| Reddit | FREE | 60/min | Interaction graph | LOW-MEDIUM |
| GitHub | FREE | 5000/hr (auth) | Follower graph | LOW |

**Recommendation**: For real-time topology extraction, Bluesky is the best emerging option (free, open protocol, explicit graph endpoints). Mastodon is a good backup. Twitter/X is effectively unusable due to cost.

---

## 5. Implementation Patterns

### 5.1 Loading SNAP Edge List to Ripple TopologyData

```python
import networkx as nx
from typing import Dict, Any

def load_snap_to_topology(filepath: str) -> Dict[str, Any]:
    """Load SNAP edge list file to Ripple TopologyData format."""
    G = nx.read_edgelist(
        filepath,
        create_using=nx.DiGraph,
        nodetype=str,  # Keep node IDs as strings for agent_id compatibility
    )

    # Convert to node-link format
    data = nx.node_link_data(G)

    # Add default node type (required by TopologyNode)
    for node in data["nodes"]:
        node["type"] = "sea"  # Default to "sea" (regular user)

    # Add default edge weight (required by TopologyEdge)
    for edge in data["links"]:
        edge["weight"] = 1.0  # Default weight

    return {
        "nodes": data["nodes"],
        "edges": data["links"],  # Key rename
    }
```

### 5.2 Generating Synthetic Topology with NetworkX

```python
import networkx as nx

def generate_scale_free_topology(n_agents: int, m_edges_per_node: int = 3) -> Dict[str, Any]:
    """Generate Barabasi-Albert scale-free topology."""
    G = nx.barabasi_albert_graph(n_agents, m_edges_per_node)

    # Convert to directed (social networks are typically directed)
    G = G.to_directed()

    data = nx.node_link_data(G)

    # Assign node types based on degree (star = high degree, sea = low)
    degrees = dict(G.degree())
    max_degree = max(degrees.values())

    for node in data["nodes"]:
        node_id = node["id"]
        deg = degrees[int(node_id)]
        # Top 10% by degree are "star" nodes
        node["type"] = "star" if deg > max_degree * 0.9 else "sea"

    # Normalize edge weights by target degree
    for edge in data["links"]:
        target_degree = degrees[int(edge["target"])]
        edge["weight"] = 1.0 / max(target_degree, 1)

    return {
        "nodes": data["nodes"],
        "edges": data["links"],
    }
```

### 5.3 Provider Registration Pattern

Based on `/home/admin/Ripple/ripple/providers/registry.py`:

```python
# In ripple/providers/topology_loaders.py (new file)

from .topology import TopologyProvider, TopologyData
from .registry import register_provider

class SnapTopologyProvider:
    """Load topology from SNAP dataset file."""

    def __init__(self, dataset_path: str, default_type: str = "sea"):
        self.dataset_path = dataset_path
        self.default_type = default_type

    @property
    def name(self) -> str:
        return "snap-topology"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    async def get_topology(self, **kwargs) -> TopologyData | None:
        # Load and convert using NetworkX
        ...

# Register in registry
register_provider("topology", "snap", SnapTopologyProvider)
```

---

## 6. Related Specs

| Spec File | Description |
|-----------|-------------|
| `.trellis/spec/backend/provider-architecture.md` | DataSource Provider architecture, TopologyData format |
| `.trellis/tasks/archive/2026-06/06-05-datasource-provider/prd.md` | Original provider architecture PRD |

---

## 7. Caveats / Not Found

### Not Researched
- Chinese social media APIs (Weibo, WeChat) - likely require business registration
- Instagram/Facebook Graph API - requires app review, limited access
- LinkedIn API - restricted to approved partners

### Limitations
- Twitter Academic Research track is discontinued (critical for research use)
- Most real-time APIs have rate limits that make large-scale graph extraction slow
- Public datasets may not reflect current social network structures (most are 2008-2015 era)

### Open Questions for Implementation
1. Should topology loading be async or sync? (NetworkX is sync, but provider interface is async)
2. How to handle node ID mapping (integer SNAP IDs to string agent IDs)?
3. Should we cache loaded topologies in memory or re-read each simulation?
4. How to handle weighted vs unweighted edge lists?

---

## 8. Summary Recommendations

| Use Case | Recommended Source | Library |
|----------|-------------------|---------|
| Realistic social graph | SNAP datasets | NetworkX |
| Small test topologies | NetworkX generators | NetworkX |
| Real-time extraction | Bluesky API | httpx + custom |
| Large-scale loading | SNAP + igraph | python-igraph (if GPL OK) |
| Community-structured synthetic | SBM generator | NetworkX |

**Primary Recommendation**: Add `networkx` as an optional dependency and implement a `SnapTopologyProvider` that loads edge list files and converts to Ripple TopologyData format using `node_link_data()`.
