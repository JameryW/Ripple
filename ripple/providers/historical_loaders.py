"""Concrete HistoricalProvider implementations — file loading and API sources.

Provides historical event propagation records for SYNTHESIZE-phase anchoring.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .historical import HistoricalProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_format(path: Path) -> str:
    ext = path.suffix.lower()
    return "csv" if ext in (".csv", ".tsv") else "json"


def _load_json(path: Path) -> List[Dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    return [data]


def _load_csv(path: Path) -> List[Dict[str, Any]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with open(path, newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return [row for row in reader if any(v for v in row.values())]


# ---------------------------------------------------------------------------
# FileHistoricalProvider
# ---------------------------------------------------------------------------


class FileHistoricalProvider:
    """Load historical records from a JSON or CSV file.

    Parameters
    ----------
    path : str or Path
        Path to the historical data file.
    format : str
        ``"json"``, ``"csv"``, or ``"auto"`` (infer from extension).
    """

    def __init__(
        self,
        path: str | Path,
        format: str = "auto",
    ) -> None:
        self._path = Path(path)
        self._format = format
        self._cache: List[Dict[str, Any]] | None = None

    @property
    def name(self) -> str:
        return f"file-historical({self._path.name})"

    def is_available(self) -> bool:
        return self._path.exists()

    async def health_check(self) -> bool:
        return self.is_available()

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        if self._cache is not None:
            records = self._cache
        else:
            fmt = self._format if self._format != "auto" else _detect_format(self._path)
            try:
                if fmt == "csv":
                    records = await asyncio.to_thread(_load_csv, self._path)
                else:
                    records = await asyncio.to_thread(_load_json, self._path)
                self._cache = records
            except Exception as exc:
                logger.warning("FileHistoricalProvider failed to load %s: %s", self._path, exc)
                return None

        return _filter_records(records, platform=platform, event_type=event_type, limit=limit)


def _filter_records(
    records: List[Dict[str, Any]],
    *,
    platform: str | None = None,
    event_type: str | None = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Filter and limit historical records."""
    result = records
    if platform:
        result = [r for r in result if r.get("platform", "").lower() == platform.lower()]
    if event_type:
        result = [r for r in result if r.get("event_type", r.get("type", "")).lower() == event_type.lower()]
    return result[:limit]


# ---------------------------------------------------------------------------
# WikiPageviewProvider
# ---------------------------------------------------------------------------


class WikiPageviewProvider:
    """Fetch historical pageview data from the Wikimedia Pageview API.

    Parameters
    ----------
    article : str
        Wikipedia article title (e.g. ``"Python_(programming_language)"``).
    project : str
        Project identifier (default ``"en.wikipedia"``).
    start : str
        Start date YYYYMMDD.
    end : str
        End date YYYYMMDD.
    granularity : str
        ``"daily"`` or ``"monthly"``.
    access : str
        Access type filter (default ``"all-access"``).
    agent : str
        Agent filter (default ``"user"``).
    """

    _BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

    def __init__(
        self,
        article: str,
        project: str = "en.wikipedia",
        start: str = "20250101",
        end: str = "20250601",
        granularity: str = "daily",
        access: str = "all-access",
        agent: str = "user",
    ) -> None:
        self._article = article
        self._project = project
        self._start = start
        self._end = end
        self._granularity = granularity
        self._access = access
        self._agent = agent
        self._cache: List[Dict[str, Any]] | None = None

    @property
    def name(self) -> str:
        return f"wiki-pageview({self._article})"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            url = (
                f"{self._BASE_URL}/{self._project}/{self._access}/{self._agent}/"
                f"{self._article}/{self._granularity}/{self._start}/{self._end}"
            )
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers={"User-Agent": "Ripple/0.1"})
                return resp.status_code == 200
        except Exception:
            return False

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        if self._cache is not None:
            return _filter_records(self._cache, platform=platform, event_type=event_type, limit=limit)

        url = (
            f"{self._BASE_URL}/{self._project}/{self._access}/{self._agent}/"
            f"{self._article}/{self._granularity}/{self._start}/{self._end}"
        )
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, headers={"User-Agent": "Ripple/0.1"})
                resp.raise_for_status()
                body = resp.json()

            records = []
            for item in body.get("items", []):
                records.append({
                    "platform": "wikipedia",
                    "event_type": "pageview",
                    "timestamp": item.get("timestamp", ""),
                    "article": item.get("article", self._article),
                    "views": item.get("views", 0),
                    "granularity": item.get("granularity", self._granularity),
                })
            self._cache = records
        except Exception as exc:
            logger.warning("WikiPageviewProvider failed: %s", exc)
            return None

        return _filter_records(records, platform=platform, event_type=event_type, limit=limit)


# ---------------------------------------------------------------------------
# RedditArchiveProvider
# ---------------------------------------------------------------------------


class RedditArchiveProvider:
    """Fetch historical Reddit post data via Pushshift API.

    Parameters
    ----------
    subreddit : str
        Target subreddit (e.g. ``"technology"``).
    size : int
        Number of results per request (max 100, default 25).
    sort_type : str
        Sort field (``"score"``, ``"num_comments"``, ``"created_utc"``).
    """

    _BASE_URL = "https://api.pushshift.io/reddit/search/submission"

    def __init__(
        self,
        subreddit: str,
        size: int = 25,
        sort_type: str = "score",
    ) -> None:
        self._subreddit = subreddit
        self._size = min(size, 100)
        self._sort_type = sort_type
        self._cache: List[Dict[str, Any]] | None = None

    @property
    def name(self) -> str:
        return f"reddit-archive(r/{self._subreddit})"

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            params = {"subreddit": self._subreddit, "size": 1}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self._BASE_URL, params=params, headers={"User-Agent": "Ripple/0.1"})
                return resp.status_code == 200
        except Exception:
            return False

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        if self._cache is not None:
            return _filter_records(self._cache, platform=platform, event_type=event_type, limit=limit)

        params = {
            "subreddit": self._subreddit,
            "size": self._size,
            "sort_type": self._sort_type,
            "sort": "desc",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(self._BASE_URL, params=params, headers={"User-Agent": "Ripple/0.1"})
                resp.raise_for_status()
                body = resp.json()

            records = []
            for post in body.get("data", []):
                records.append({
                    "platform": "reddit",
                    "event_type": "submission",
                    "timestamp": post.get("created_utc", 0),
                    "title": post.get("title", ""),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0.0),
                    "subreddit": post.get("subreddit", self._subreddit),
                })
            self._cache = records
        except Exception as exc:
            logger.warning("RedditArchiveProvider failed: %s", exc)
            return None

        return _filter_records(records, platform=platform, event_type=event_type, limit=limit)
