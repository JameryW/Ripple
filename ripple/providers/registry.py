"""Provider registry — YAML defaults + runtime overrides with priority resolution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

from .ambient import AmbientProvider, StubAmbientProvider
from .base import DataSourceProvider
from .embedding import EmbeddingProvider, StubEmbeddingProvider
from .historical import HistoricalProvider, StubHistoricalProvider
from .openai_embedding import OpenAIEmbeddingProvider
from .topology import TopologyProvider, StubTopologyProvider

# Lazy imports for optional-dependency providers (networkx required)
_PROVIDER_LAZY_IMPORTS: Dict[str, Dict[str, tuple]] = {
    "topology": {
        "file": ("ripple.providers.topology_loaders", "FileTopologyProvider"),
        "synthetic": ("ripple.providers.topology_loaders", "SyntheticTopologyProvider"),
    },
}

logger = logging.getLogger(__name__)

_PROVIDER_MAP: Dict[str, type] = {
    "topology": StubTopologyProvider,
    "historical": StubHistoricalProvider,
    "embedding": StubEmbeddingProvider,
    "ambient": StubAmbientProvider,
}

# Forward-reference: will be populated when concrete providers are implemented
_PROVIDER_IMPLEMENTATIONS: Dict[str, Dict[str, type]] = {
    "topology": {},  # Populated lazily from _PROVIDER_LAZY_IMPORTS
    "historical": {},
    "embedding": {"openai": OpenAIEmbeddingProvider},
    "ambient": {},
}


def _ensure_lazy_imports(category: str) -> None:
    """Eagerly import lazy-registered provider classes for *category*."""
    lazy = _PROVIDER_LAZY_IMPORTS.get(category, {})
    if not lazy:
        return
    for impl_name, (module_path, class_name) in lazy.items():
        if impl_name in _PROVIDER_IMPLEMENTATIONS.get(category, {}):
            continue
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            _PROVIDER_IMPLEMENTATIONS[category][impl_name] = cls
        except Exception:
            logger.debug("Lazy import failed for %s.%s: skipped", module_path, class_name)
    # Mark as resolved (clear lazy entries)
    _PROVIDER_LAZY_IMPORTS.pop(category, None)


def register_provider(category: str, impl_name: str, cls: type) -> None:
    """Register a concrete provider implementation for a category."""
    if category not in _PROVIDER_IMPLEMENTATIONS:
        raise ValueError(f"Unknown provider category: {category!r}")
    _PROVIDER_IMPLEMENTATIONS[category][impl_name] = cls


class ProviderRegistry:
    """Manages provider instances with three-tier priority:

    1. Runtime params (simulate(providers=...)) — highest
    2. YAML config defaults (from llm_config.yaml _providers section)
    3. Stub (always returns None → LLM fallback) — lowest
    """

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        yaml_providers_cfg: Dict[str, Any] | None = None,
        runtime_overrides: Dict[str, DataSourceProvider] | None = None,
    ) -> None:
        self._yaml_providers: Dict[str, DataSourceProvider] = {}
        self._runtime_overrides: Dict[str, DataSourceProvider] = runtime_overrides or {}
        self._stubs: Dict[str, DataSourceProvider] = {
            "topology": StubTopologyProvider(),
            "historical": StubHistoricalProvider(),
            "embedding": StubEmbeddingProvider(),
            "ambient": StubAmbientProvider(),
        }

        if yaml_path is not None:
            self._load_yaml(yaml_path)
        elif yaml_providers_cfg is not None:
            self._load_yaml_cfg(yaml_providers_cfg)

    def _load_yaml(self, path: str | Path) -> None:
        path = Path(path)
        if not path.exists():
            logger.warning("Provider config not found: %s — using stubs", path)
            return

        with open(path) as f:
            cfg = yaml.safe_load(f) or {}

        providers_cfg = cfg.get("providers", {})
        self._load_yaml_cfg(providers_cfg)

    def _load_yaml_cfg(self, providers_cfg: Dict[str, Any]) -> None:
        """Instantiate providers from a YAML config dict (e.g. llm_config.yaml _providers section)."""
        for category, spec in providers_cfg.items():
            if category not in _PROVIDER_MAP:
                logger.warning("Unknown provider category in YAML: %s", category)
                continue
            if not isinstance(spec, dict):
                logger.warning("Provider config for %s must be a dict, got %s", category, type(spec).__name__)
                continue

            impl_name = spec.get("impl") or spec.get("class")
            if not impl_name:
                logger.warning("No 'impl' key for provider %s — skipping", category)
                continue

            # Ensure lazy imports are resolved for this category
            _ensure_lazy_imports(category)

            impls = _PROVIDER_IMPLEMENTATIONS.get(category, {})
            cls = impls.get(impl_name)
            if cls is None:
                logger.warning("Unknown impl %r for provider %s — using stub", impl_name, category)
                continue

            init_kwargs = {k: v for k, v in spec.items() if k not in ("impl", "class")}
            try:
                self._yaml_providers[category] = cls(**init_kwargs)
                logger.info("Loaded provider %s=%s from YAML", category, impl_name)
            except Exception:
                logger.exception("Failed to instantiate provider %s=%s", category, impl_name)

    # --- accessors with priority resolution ---

    def get(self, category: str) -> DataSourceProvider:
        """Return the highest-priority provider for *category*."""
        if category in self._runtime_overrides:
            return self._runtime_overrides[category]
        if category in self._yaml_providers:
            return self._yaml_providers[category]
        return self._stubs[category]

    @property
    def topology(self) -> TopologyProvider:
        return self.get("topology")  # type: ignore[return-value]

    @property
    def historical(self) -> HistoricalProvider:
        return self.get("historical")  # type: ignore[return-value]

    @property
    def embedding(self) -> EmbeddingProvider:
        return self.get("embedding")  # type: ignore[return-value]

    @property
    def ambient(self) -> AmbientProvider:
        return self.get("ambient")  # type: ignore[return-value]

    def merge(self, overrides: Dict[str, DataSourceProvider]) -> ProviderRegistry:
        """Return a new registry with *overrides* layered on top of this one."""
        merged = ProviderRegistry()
        merged._yaml_providers = {**self._yaml_providers}
        merged._runtime_overrides = {**self._runtime_overrides, **overrides}
        merged._stubs = {**self._stubs}
        return merged

    async def health_check_all(self) -> Dict[str, bool]:
        """Run health checks on all non-stub providers."""
        results: Dict[str, bool] = {}
        for cat in ("topology", "historical", "embedding", "ambient"):
            p = self.get(cat)
            stub_cls = _PROVIDER_MAP.get(cat)
            # Only health-check non-stub providers
            if stub_cls is not None and not isinstance(p, stub_cls):
                try:
                    results[cat] = await p.health_check()
                except Exception:
                    logger.exception("Health check failed for provider %s", cat)
                    results[cat] = False
        return results
