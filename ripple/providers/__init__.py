"""DataSource Provider abstraction layer for Ripple."""

from .ambient import AmbientProvider, StubAmbientProvider
from .base import DataSourceProvider
from .embedding import EmbeddingProvider, StubEmbeddingProvider
from .historical import HistoricalProvider, StubHistoricalProvider
from .openai_embedding import OpenAIEmbeddingProvider
from .registry import ProviderRegistry, register_provider
from .topology import TopologyData, TopologyEdge, TopologyNode, TopologyProvider, StubTopologyProvider

# Optional-dependency providers
try:
    from .topology_loaders import FileTopologyProvider, SyntheticTopologyProvider
    from .topology_validator import TopologyValidator, ValidationReport
except ImportError:
    pass

try:
    from .historical_loaders import FileHistoricalProvider, WikiPageviewProvider, RedditArchiveProvider
    from .historical_validator import HistoricalValidator, HistoricalValidationReport
except ImportError:
    pass

__all__ = [
    "AmbientProvider",
    "DataSourceProvider",
    "EmbeddingProvider",
    "FileHistoricalProvider",
    "FileTopologyProvider",
    "HistoricalProvider",
    "HistoricalValidator",
    "HistoricalValidationReport",
    "OpenAIEmbeddingProvider",
    "ProviderRegistry",
    "RedditArchiveProvider",
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
    "WikiPageviewProvider",
    "register_provider",
]
