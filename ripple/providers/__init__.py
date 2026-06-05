"""DataSource Provider abstraction layer for Ripple."""

from .ambient import AmbientProvider, StubAmbientProvider
from .base import DataSourceProvider
from .embedding import EmbeddingProvider, StubEmbeddingProvider
from .historical import HistoricalProvider, StubHistoricalProvider
from .openai_embedding import OpenAIEmbeddingProvider
from .registry import ProviderRegistry, register_provider
from .topology import TopologyData, TopologyEdge, TopologyNode, TopologyProvider, StubTopologyProvider

# Optional-dependency providers (require networkx)
try:
    from .topology_loaders import FileTopologyProvider, SyntheticTopologyProvider
    from .topology_validator import TopologyValidator, ValidationReport
except ImportError:
    pass

__all__ = [
    "AmbientProvider",
    "DataSourceProvider",
    "EmbeddingProvider",
    "FileTopologyProvider",
    "HistoricalProvider",
    "OpenAIEmbeddingProvider",
    "ProviderRegistry",
    "StubAmbientProvider",
    "StubEmbeddingProvider",
    "StubHistoricalProvider",
    "StubTopologyProvider",
    "SyntheticTopologyProvider",
    "TopologyData",
    "TopologyEdge",
    "TopologyNode",
    "TopologyProvider",
    "TopologyValidator",
    "ValidationReport",
    "register_provider",
]
