"""Tests for HistoricalProvider concrete implementations and validator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from ripple.providers.historical import StubHistoricalProvider
from ripple.providers.historical_loaders import (
    FileHistoricalProvider,
    RedditArchiveProvider,
    WikiPageviewProvider,
    _filter_records,
)
from ripple.providers.historical_validator import (
    HistoricalValidationReport,
    HistoricalValidator,
    MetricDeviation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def json_historical_file(tmp_path: Path) -> Path:
    records = [
        {"platform": "twitter", "event_type": "viral", "views": 10000, "shares": 500},
        {"platform": "twitter", "event_type": "normal", "views": 500, "shares": 20},
        {"platform": "reddit", "event_type": "viral", "views": 5000, "shares": 200},
    ]
    p = tmp_path / "history.json"
    p.write_text(json.dumps(records))
    return p


@pytest.fixture
def csv_historical_file(tmp_path: Path) -> Path:
    p = tmp_path / "history.csv"
    p.write_text("platform,event_type,views,shares\ntwitter,viral,10000,500\nreddit,normal,500,20\n")
    return p


@pytest.fixture
def json_with_records_key(tmp_path: Path) -> Path:
    p = tmp_path / "history_wrapped.json"
    p.write_text(json.dumps({"records": [
        {"platform": "twitter", "views": 1000},
    ]}))
    return p


# ---------------------------------------------------------------------------
# FileHistoricalProvider
# ---------------------------------------------------------------------------


class TestFileHistoricalProvider:
    @pytest.mark.asyncio
    async def test_load_json(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file, format="json")
        assert provider.is_available()
        result = await provider.get_historical()
        assert result is not None
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_load_csv(self, csv_historical_file: Path):
        provider = FileHistoricalProvider(csv_historical_file, format="csv")
        result = await provider.get_historical()
        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_load_json_with_records_key(self, json_with_records_key: Path):
        provider = FileHistoricalProvider(json_with_records_key, format="json")
        result = await provider.get_historical()
        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_auto_format(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file)
        result = await provider.get_historical()
        assert result is not None

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        provider = FileHistoricalProvider("/nonexistent/file.json")
        assert not provider.is_available()
        result = await provider.get_historical()
        assert result is None

    @pytest.mark.asyncio
    async def test_filter_by_platform(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file, format="json")
        result = await provider.get_historical(platform="twitter")
        assert result is not None
        assert all(r["platform"] == "twitter" for r in result)

    @pytest.mark.asyncio
    async def test_filter_by_event_type(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file, format="json")
        result = await provider.get_historical(event_type="viral")
        assert result is not None
        assert all(r["event_type"] == "viral" for r in result)

    @pytest.mark.asyncio
    async def test_limit(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file, format="json")
        result = await provider.get_historical(limit=1)
        assert result is not None
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_caching(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file, format="json")
        r1 = await provider.get_historical()
        r2 = await provider.get_historical()
        assert r1 is r2

    def test_name(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file)
        assert "file-historical" in provider.name

    @pytest.mark.asyncio
    async def test_health_check(self, json_historical_file: Path):
        provider = FileHistoricalProvider(json_historical_file)
        assert await provider.health_check()


# ---------------------------------------------------------------------------
# WikiPageviewProvider
# ---------------------------------------------------------------------------


class TestWikiPageviewProvider:
    def test_name(self):
        provider = WikiPageviewProvider(article="Python_(programming_language)")
        assert "wiki-pageview" in provider.name

    def test_is_available(self):
        provider = WikiPageviewProvider(article="Test")
        assert provider.is_available()

    @pytest.mark.asyncio
    async def test_get_historical_success(self):
        import httpx

        mock_body = {
            "items": [
                {"timestamp": "2025010100", "views": 1000, "article": "Test", "granularity": "daily"},
                {"timestamp": "2025010200", "views": 1200, "article": "Test", "granularity": "daily"},
            ]
        }

        async def handler(request):
            return httpx.Response(200, json=mock_body)

        with patch(
            "ripple.providers.historical_loaders.httpx.AsyncClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        ):
            provider = WikiPageviewProvider(article="Test")
            result = await provider.get_historical()
            assert result is not None
            assert len(result) == 2
            assert result[0]["platform"] == "wikipedia"
            assert result[0]["views"] == 1000

    @pytest.mark.asyncio
    async def test_get_historical_failure(self):
        import httpx

        async def handler(request):
            return httpx.Response(500, text="Server Error")

        with patch(
            "ripple.providers.historical_loaders.httpx.AsyncClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        ):
            provider = WikiPageviewProvider(article="Test")
            result = await provider.get_historical()
            assert result is None

    @pytest.mark.asyncio
    async def test_caching(self):
        import httpx

        call_count = 0
        mock_body = {"items": [{"timestamp": "2025010100", "views": 100}]}

        async def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=mock_body)

        with patch(
            "ripple.providers.historical_loaders.httpx.AsyncClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        ):
            provider = WikiPageviewProvider(article="Test")
            r1 = await provider.get_historical()
            r2 = await provider.get_historical()
            assert r1 is r2
            assert call_count == 1


# ---------------------------------------------------------------------------
# RedditArchiveProvider
# ---------------------------------------------------------------------------


class TestRedditArchiveProvider:
    def test_name(self):
        provider = RedditArchiveProvider(subreddit="technology")
        assert "reddit-archive" in provider.name

    def test_is_available(self):
        provider = RedditArchiveProvider(subreddit="technology")
        assert provider.is_available()

    @pytest.mark.asyncio
    async def test_get_historical_success(self):
        import httpx

        mock_body = {
            "data": [
                {"title": "Post 1", "score": 500, "num_comments": 100, "upvote_ratio": 0.95, "created_utc": 1700000000, "subreddit": "technology"},
                {"title": "Post 2", "score": 200, "num_comments": 50, "upvote_ratio": 0.88, "created_utc": 1700001000, "subreddit": "technology"},
            ]
        }

        async def handler(request):
            return httpx.Response(200, json=mock_body)

        with patch(
            "ripple.providers.historical_loaders.httpx.AsyncClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        ):
            provider = RedditArchiveProvider(subreddit="technology")
            result = await provider.get_historical()
            assert result is not None
            assert len(result) == 2
            assert result[0]["platform"] == "reddit"
            assert result[0]["score"] == 500

    @pytest.mark.asyncio
    async def test_get_historical_failure(self):
        import httpx

        async def handler(request):
            return httpx.Response(500, text="Server Error")

        with patch(
            "ripple.providers.historical_loaders.httpx.AsyncClient",
            lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
        ):
            provider = RedditArchiveProvider(subreddit="technology")
            result = await provider.get_historical()
            assert result is None

    def test_size_capped_at_100(self):
        provider = RedditArchiveProvider(subreddit="test", size=200)
        assert provider._size == 100


# ---------------------------------------------------------------------------
# _filter_records helper
# ---------------------------------------------------------------------------


class TestFilterRecords:
    def test_no_filter(self):
        records = [{"platform": "a"}, {"platform": "b"}]
        result = _filter_records(records, limit=10)
        assert len(result) == 2

    def test_platform_filter(self):
        records = [{"platform": "twitter"}, {"platform": "reddit"}]
        result = _filter_records(records, platform="twitter", limit=10)
        assert len(result) == 1

    def test_event_type_filter(self):
        records = [{"event_type": "viral"}, {"type": "normal"}]
        result = _filter_records(records, event_type="viral", limit=10)
        assert len(result) == 1

    def test_limit(self):
        records = [{"i": i} for i in range(10)]
        result = _filter_records(records, limit=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# HistoricalValidator
# ---------------------------------------------------------------------------


class TestHistoricalValidator:
    def test_validate_identical(self):
        prediction = {"views": 1000, "shares": 50}
        historical = [{"views": 1000, "shares": 50}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        assert report.is_acceptable
        assert len(report.metric_deviations) == 2

    def test_validate_deviation(self):
        prediction = {"views": 5000}
        historical = [{"views": 1000}, {"views": 1200}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        # avg=1100, predicted=5000, deviation=(5000-1100)/1100*100 ≈ 354%
        assert not report.is_acceptable  # >100% threshold

    def test_validate_no_historical(self):
        prediction = {"views": 1000}
        validator = HistoricalValidator()
        report = validator.validate(prediction, [])
        assert len(report.warnings) > 0

    def test_validate_no_matching_metric(self):
        prediction = {"views": 1000}
        historical = [{"shares": 50}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        assert len(report.metric_deviations) == 0

    def test_validate_zero_historical_avg(self):
        prediction = {"views": 0}
        historical = [{"views": 0}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        assert report.is_acceptable

    def test_validate_infinite_deviation(self):
        prediction = {"views": 100}
        historical = [{"views": 0}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        assert report.metric_deviations[0].deviation_pct == float("inf")

    def test_validate_skip_non_numeric(self):
        prediction = {"views": 100, "step": 3, "agent_id": "a"}
        historical = [{"views": 100, "step": 1}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        # Only "views" should be checked; "step" and "agent_id" skipped
        metrics = {d.metric for d in report.metric_deviations}
        assert "views" in metrics
        assert "step" not in metrics

    def test_report_log(self):
        prediction = {"views": 1000}
        historical = [{"views": 1000}]
        validator = HistoricalValidator()
        report = validator.validate(prediction, historical)
        report.log()  # Should not raise

    def test_custom_threshold(self):
        prediction = {"views": 2000}
        historical = [{"views": 1000}]
        validator = HistoricalValidator(threshold=200.0)
        report = validator.validate(prediction, historical)
        assert report.is_acceptable  # 100% deviation, within 200% threshold


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestHistoricalRegistryIntegration:
    def test_lazy_import_resolves(self):
        from ripple.providers.registry import (
            _PROVIDER_IMPLEMENTATIONS,
            _ensure_lazy_imports,
        )

        _ensure_lazy_imports("historical")
        impls = _PROVIDER_IMPLEMENTATIONS["historical"]
        assert "file" in impls
        assert "wikipedia" in impls
        assert "reddit" in impls

    def test_yaml_config_file_provider(self, json_historical_file: Path):
        from ripple.providers.registry import ProviderRegistry

        cfg = {
            "historical": {
                "impl": "file",
                "path": str(json_historical_file),
                "format": "json",
            }
        }
        registry = ProviderRegistry(yaml_providers_cfg=cfg)
        hist = registry.historical
        assert not isinstance(hist, StubHistoricalProvider)

    @pytest.mark.asyncio
    async def test_yaml_file_provider_get_historical(self, json_historical_file: Path):
        from ripple.providers.registry import ProviderRegistry

        cfg = {
            "historical": {
                "impl": "file",
                "path": str(json_historical_file),
                "format": "json",
            }
        }
        registry = ProviderRegistry(yaml_providers_cfg=cfg)
        result = await registry.historical.get_historical()
        assert result is not None
        assert len(result) == 3
