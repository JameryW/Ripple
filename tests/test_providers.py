"""Tests for DataSource Provider architecture."""

import asyncio
import pytest
from pathlib import Path

from ripple.providers import (
    DataSourceProvider,
    ProviderRegistry,
    StubTopologyProvider,
    StubHistoricalProvider,
    StubEmbeddingProvider,
    StubAmbientProvider,
    OpenAIEmbeddingProvider,
)
from ripple.skills.manager import LoadedSkill


# ---------------------------------------------------------------------------
# Stub providers
# ---------------------------------------------------------------------------


class TestStubProviders:
    def test_topology_stub(self):
        p = StubTopologyProvider()
        assert p.name == "stub-topology"
        assert p.is_available() is False
        assert asyncio.run(p.health_check()) is False
        assert asyncio.run(p.get_topology()) is None

    def test_historical_stub(self):
        p = StubHistoricalProvider()
        assert p.name == "stub-historical"
        assert p.is_available() is False
        assert asyncio.run(p.get_historical()) is None

    def test_embedding_stub(self):
        p = StubEmbeddingProvider()
        assert p.name == "stub-embedding"
        assert p.is_available() is False
        assert asyncio.run(p.embed("hello")) is None
        assert asyncio.run(p.embed_batch(["a", "b"])) == [None, None]

    def test_ambient_stub(self):
        p = StubAmbientProvider()
        assert p.name == "stub-ambient"
        assert p.is_available() is False
        assert asyncio.run(p.get_ambient()) is None
        assert asyncio.run(p.get_trending_memes()) is None


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------


class TestProviderRegistry:
    def test_default_stubs(self):
        r = ProviderRegistry()
        assert isinstance(r.topology, StubTopologyProvider)
        assert isinstance(r.embedding, StubEmbeddingProvider)

    def test_runtime_override(self):
        custom = StubEmbeddingProvider()
        r = ProviderRegistry(runtime_overrides={"embedding": custom})
        assert r.get("embedding") is custom

    def test_runtime_overrides_yaml(self):
        """Runtime overrides take priority over YAML providers."""
        custom = StubEmbeddingProvider()
        r = ProviderRegistry(runtime_overrides={"embedding": custom})
        assert r.get("embedding") is custom

    def test_yaml_providers_cfg(self):
        """ProviderRegistry can load providers from a YAML config dict."""
        cfg = {
            "embedding": {
                "impl": "openai",
                "url": "https://api.example.com/v1",
                "api_key": "sk-test",
            },
        }
        r = ProviderRegistry(yaml_providers_cfg=cfg)
        emb = r.get("embedding")
        assert isinstance(emb, OpenAIEmbeddingProvider)
        assert emb.is_available() is True

    def test_yaml_providers_cfg_runtime_overrides_priority(self):
        """Runtime overrides take priority over YAML-declared providers."""
        cfg = {
            "embedding": {
                "impl": "openai",
                "url": "https://api.example.com/v1",
                "api_key": "sk-yaml",
            },
        }
        custom = StubEmbeddingProvider()
        r = ProviderRegistry(yaml_providers_cfg=cfg, runtime_overrides={"embedding": custom})
        # Runtime override wins
        assert r.get("embedding") is custom

    def test_yaml_providers_cfg_unknown_impl(self):
        """Unknown impl in YAML falls back to stub."""
        cfg = {
            "embedding": {
                "impl": "nonexistent",
                "url": "https://api.example.com/v1",
                "api_key": "sk-test",
            },
        }
        r = ProviderRegistry(yaml_providers_cfg=cfg)
        assert isinstance(r.get("embedding"), StubEmbeddingProvider)

    def test_merge(self):
        r1 = ProviderRegistry()
        custom = StubEmbeddingProvider()
        r2 = r1.merge({"embedding": custom})
        assert r2.get("embedding") is custom
        # Original registry unchanged
        assert isinstance(r1.get("embedding"), StubEmbeddingProvider)

    def test_yaml_not_found(self):
        """Missing YAML file falls back to stubs without error."""
        r = ProviderRegistry(yaml_path="/nonexistent/providers.yaml")
        assert isinstance(r.topology, StubTopologyProvider)

    def test_get_unknown_category(self):
        r = ProviderRegistry()
        with pytest.raises(KeyError):
            r.get("nonexistent")

    def test_health_check_all_stubs(self):
        r = ProviderRegistry()
        result = asyncio.run(r.health_check_all())
        # All stubs should report unavailable
        assert all(v is False for v in result.values())

    def test_health_check_all_with_yaml_provider(self):
        """health_check_all only checks non-stub providers."""
        cfg = {
            "embedding": {
                "impl": "openai",
                "url": "https://api.example.com/v1",
                "api_key": "sk-test",
            },
        }
        r = ProviderRegistry(yaml_providers_cfg=cfg)
        result = asyncio.run(r.health_check_all())
        # Only embedding is non-stub, so only it gets health-checked
        assert "embedding" in result


# ---------------------------------------------------------------------------
# OpenAIEmbeddingProvider (unit-level, no real API calls)
# ---------------------------------------------------------------------------


class TestOpenAIEmbeddingProvider:
    def test_ensure_embeddings_path(self):
        assert OpenAIEmbeddingProvider._ensure_embeddings_path(
            "https://api.openai.com/v1"
        ) == "https://api.openai.com/v1/embeddings"

        assert OpenAIEmbeddingProvider._ensure_embeddings_path(
            "https://api.openai.com/v1/embeddings"
        ) == "https://api.openai.com/v1/embeddings"

    def test_is_available(self):
        p = OpenAIEmbeddingProvider(url="https://api.example.com/v1", api_key="sk-test")
        assert p.is_available() is True

    def test_not_available_without_key(self):
        p = OpenAIEmbeddingProvider(url="https://api.example.com/v1", api_key="")
        assert p.is_available() is False

    def test_embed_empty_text(self):
        p = OpenAIEmbeddingProvider(url="https://api.example.com/v1", api_key="sk-test")
        result = asyncio.run(p.embed(""))
        assert result == []


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_stub_topology_is_provider(self):
        assert isinstance(StubTopologyProvider(), DataSourceProvider)

    def test_stub_embedding_is_provider(self):
        assert isinstance(StubEmbeddingProvider(), DataSourceProvider)

    def test_openai_embedding_is_provider(self):
        p = OpenAIEmbeddingProvider(url="https://api.example.com/v1", api_key="sk-test")
        assert isinstance(p, DataSourceProvider)


# ---------------------------------------------------------------------------
# LoadedSkill required_providers
# ---------------------------------------------------------------------------


class TestLoadedSkillRequiredProviders:
    def test_default_empty(self):
        skill = LoadedSkill(
            name="test",
            version="1.0",
            description="test skill",
            path=Path("."),
            prompts={},
            prompt_hashes={},
        )
        assert skill.required_providers == []

    def test_from_frontmatter(self):
        skill = LoadedSkill(
            name="test",
            version="1.0",
            description="test skill",
            path=Path("."),
            prompts={},
            prompt_hashes={},
            required_providers=["embedding", "topology"],
        )
        assert skill.required_providers == ["embedding", "topology"]
