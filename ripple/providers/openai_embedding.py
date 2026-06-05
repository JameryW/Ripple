"""OpenAI-compatible Embedding Provider — calls /embeddings endpoint via ModelRouter."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

import httpx

logger = logging.getLogger(__name__)


class OpenAIEmbeddingProvider:
    """Embedding provider that calls an OpenAI-compatible /embeddings endpoint.

    Shares connection config (url, api_key) with ModelRouter by reading
    the resolved ``ModelEndpointConfig`` for a designated role.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = self._ensure_embeddings_path(url)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    @staticmethod
    def _ensure_embeddings_path(url: str) -> str:
        """Ensure URL ends with /embeddings."""
        url = url.rstrip("/")
        if not url.endswith("/embeddings"):
            url = url + "/embeddings"
        return url

    @property
    def name(self) -> str:
        return f"openai-embedding({self._model})"

    def is_available(self) -> bool:
        return bool(self._url and self._api_key)

    async def health_check(self) -> bool:
        try:
            result = await self.embed("health-check")
            return result is not None and len(result) > 0
        except Exception:
            return False

    async def embed(self, text: str) -> List[float] | None:
        if not text.strip():
            return []
        results = await self.embed_batch([text])
        return results[0] if results else None

    async def embed_batch(self, texts: List[str]) -> List[List[float] | None]:
        if not texts:
            return []

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        body = {
            "model": self._model,
            "input": texts,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(self._url, headers=headers, json=body)
                    response.raise_for_status()
                    data = response.json()

                embeddings: List[List[float] | None] = [None] * len(texts)
                for item in data.get("data", []):
                    idx = item.get("index", -1)
                    vec = item.get("embedding")
                    if 0 <= idx < len(texts) and isinstance(vec, list):
                        embeddings[idx] = vec

                return embeddings

            except Exception as e:
                last_error = e
                logger.warning(
                    "Embedding API call failed (attempt %d/%d): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

        logger.error("Embedding API failed after %d attempts: %s", self._max_retries + 1, last_error)
        return [None] * len(texts)

    @classmethod
    def from_endpoint_config(cls, config: Any, **overrides: Any) -> OpenAIEmbeddingProvider:
        """Create from a ModelEndpointConfig (shares url/api_key with LLM router)."""
        if not config.url:
            raise ValueError("EmbeddingProvider requires url in endpoint config")
        if not config.api_key:
            raise ValueError("EmbeddingProvider requires api_key in endpoint config")

        return cls(
            url=config.url,
            api_key=config.api_key,
            model=overrides.get("model", "text-embedding-3-small"),
            timeout=overrides.get("timeout", config.timeout or 30.0),
            max_retries=overrides.get("max_retries", 2),
        )
